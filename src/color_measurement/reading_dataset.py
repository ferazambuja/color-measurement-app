"""One dataset projection and numerical composition for live and saved inputs."""

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import asdict

from .application import _comparison_descriptor, _fields, _uuid
from .models import XYZ, Spectrum
from .presentation import (
    DEFAULT_PREVIEW_SCALE,
    TARGETS,
    display_projections,
    lab_projections,
    reference_projections,
    resolve_white,
)
from .processing import compare_color, compare_spectra, compare_xyz
from .science import standard_reference_white, xyz_to_uvy, xyz_to_xyy


def _reading_key(operation_id, observation_id):
    """Opaque dataset identity; native observation IDs remain operation-scoped."""
    return hashlib.sha256(json.dumps(
        [operation_id, observation_id], separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def reading_identity(reference):
    """Keep local pairs distinct from immutable source-qualified native pairs."""
    if not isinstance(reference, dict):
        raise ValueError("Reading reference must be an object")
    fields = {"operation_id", "observation_id"}
    if "segment_id" in reference or "source_manifest_sha256" in reference:
        fields |= {"segment_id", "source_manifest_sha256"}
    _fields(reference, fields)
    if any(not isinstance(reference[key], str) or not reference[key].strip()
           for key in ("operation_id", "observation_id")):
        raise ValueError("Reading reference requires its native operation and observation IDs")
    pair = reference["operation_id"], reference["observation_id"]
    if "segment_id" not in reference:
        return pair
    _uuid(reference["segment_id"])
    digest = reference["source_manifest_sha256"]
    if (not isinstance(digest, str) or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)):
        raise ValueError("Inherited reading requires its original manifest SHA-256")
    return reference["segment_id"], digest, *pair


def reading_key(reference):
    """One opaque DTO key; qualifiers never replace IDs inside native observations."""
    identity = reading_identity(reference)
    return (_reading_key(*identity) if len(identity) == 2 else
            hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest())


def resolve_reading(readings, reference):
    key = reading_key(reference)
    if key not in readings:
        raise ValueError("Comparison source is unavailable in this dataset")
    row = readings[key]
    actual = row.get("reading_ref", {name: row["observation"][name]
                                   for name in ("operation_id", "observation_id")})
    if actual != reference:
        raise ValueError("Comparison source differs from its authenticated reading reference")
    return key


def dataset_white_descriptor(readings, declaration):
    """Reuse the existing explicit-white owner with an authenticated DTO source key."""
    if isinstance(declaration, dict) and declaration.get("kind") == "retained-reading":
        reference = {key: value for key, value in declaration.items() if key != "kind"}
        key = resolve_reading(readings, reference)
        if not readings[key]["saved"] or _color_input(readings[key]["observation"], "xyz") is None:
            raise ValueError("Adopted-white inputs must be saved authenticated native XYZ readings")
        return json.loads(json.dumps(resolve_white(
            readings, dict(kind="retained-reading", source_id=key),
        ), allow_nan=False))
    return _comparison_descriptor({}, declaration)


def _record_kind(observation):
    # The current colour schema has no discriminator; scalar/timing schemas do.
    return observation.get("record_kind", "color")


def _color_input(observation, quantity):
    return (
        observation["backend_reported"][quantity] if _record_kind(observation) == "color" else None
    )


def _color_rows(readings):
    return {key: row for key, row in readings.items()
            if _record_kind(row["observation"]) == "color"}


def _color_reason(observation):
    return f"Color metrics are unavailable for a {_record_kind(observation)} observation"


def _display_previews(readings, scale):
    previews = display_projections(_color_rows(readings), scale)
    for key, row in readings.items():
        if _record_kind(row["observation"]) != "color":
            previews[key] = {
                target: dict(display_rgb=None, gamut_status=None,
                             reason=_color_reason(row["observation"])) for target in TARGETS
            }
    return previews


def project_dataset(annotations, observations, observation_hashes, drafts, key, *, source_identity,
                    operation_labels=None):
    """Project original readings under an explicit batch or standalone source identity.

    ``operation_labels`` maps an operation ID to its ``repeat_of`` reading reference
    and ``chart_cell`` coordinate/name. They are presentation metadata and never
    enter the scientific revision.
    """
    if not isinstance(source_identity, dict):
        raise ValueError("Dataset source identity must be an object")
    if source_identity.get("kind") == "batch":
        _fields(source_identity, {"kind", "batch_id", "context_id"})
        _uuid(source_identity["batch_id"])
        _uuid(source_identity["context_id"])
    elif source_identity.get("kind") == "standalone":
        _fields(source_identity, {"kind", "format", "source_sha256"})
        if not isinstance(source_identity["format"], str) or not source_identity["format"].strip():
            raise ValueError("Standalone source identity requires its declared format")
        digest = source_identity["source_sha256"]
        if (not isinstance(digest, str) or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)):
            raise ValueError("Standalone source identity requires its original file SHA-256")
    else:
        raise ValueError("Choose a batch or standalone dataset source")
    rows = {}
    for item in annotations:
        reference = item.get("reading_ref", {name: item[name]
                                            for name in ("operation_id", "observation_id")})
        pair = reading_identity(reference)
        identity = reading_key(reference)
        observation = observations[pair]
        kind = _record_kind(observation)
        native = _color_input(observation, "xyz")
        coordinates, coordinate_reasons = {}, {}
        for coordinate, transform in (("xyy", xyz_to_xyy), ("uvy", xyz_to_uvy)):
            try:
                if native is None:
                    raise ValueError(_color_reason(observation) if kind != "color"
                                     else "Native XYZ is unavailable")
                coordinates[coordinate] = transform(native["values"])
            except (TypeError, ValueError, OverflowError) as error:
                coordinates[coordinate] = None
                coordinate_reasons[coordinate] = str(error)
        rows[identity] = dict(
            observation=observation,
            reading_ref=reference,
            inherited=len(pair) != 2,
            **{label: deepcopy((operation_labels or {}).get(reference["operation_id"], {})
                               .get(label)) for label in ("repeat_of", "chart_cell")},
            record_kind=kind,
            name=item["name"],
            notes_preview=item["notes_preview"],
            notes_length=item["notes_length"],
            saved=item["saved"],
            version=item["version"],
            **coordinates,
            coordinate_reasons=coordinate_reasons,
        )
    previews = _display_previews(rows, DEFAULT_PREVIEW_SCALE)
    for identity, row in rows.items():
        row["display_previews"] = previews[identity]
    eligible = [
        identity
        for identity, row in rows.items()
        if _color_input(row["observation"], "xyz") is not None
        and min(_color_input(row["observation"], "xyz")["values"]) > 0
    ]
    scientific_identity = [source_identity, [
        (*reading_identity(row["reading_ref"]),
         observation_hashes[reading_identity(row["reading_ref"])])
        for row in rows.values()
    ]]
    # Annotations and sample/UI autosaves refresh metadata without
    # invalidating science. Comparison version is fenced separately.
    scientific_revision = hashlib.sha256(json.dumps(
        scientific_identity, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return dict(
        readings=rows, drafts=drafts, eligible_white_sources=eligible,
        dataset_revision=scientific_revision, roster_revision=key,
        preview_scale=DEFAULT_PREVIEW_SCALE,
    )


def validate_analysis(method, params, dataset):
    fields = {"scale", "dataset_revision", "comparison_version"}
    if method == "analysis":
        fields |= {"selected"}
    _fields(params, fields)
    data = dataset()
    if params["dataset_revision"] != data["dataset_revision"]:
        raise ValueError("Reading revision changed; refresh the current snapshot")
    scale = params["scale"]
    if (
        isinstance(scale, bool)
        or not isinstance(scale, (int, float))
        or not math.isfinite(scale)
        or scale <= 0
    ):
        raise ValueError("Preview scale must be finite and positive")
    return data


def _comparison_white(descriptor):
    """Reconstruct the typed explicit white through its established owner."""
    if descriptor is None:
        return None
    if descriptor["kind"] == "retained-reading":
        return XYZ(tuple(descriptor["xyz"]), **{
            field: descriptor[field] for field in ("unit", "scale", "observer", "illuminant")
        })
    return standard_reference_white(**{
        field: descriptor[field] for field in ("name", "observer", "reference_y", "unit", "scale")
    })


def analyze_dataset(method, params, data, context):
    readings = data["readings"]
    scale = params["scale"]
    white, descriptor = context["white"], context["white_descriptor"]
    reference = None
    for pair in (context["reference"], white):
        if pair is None or "operation_id" not in pair:
            continue
        resolve_reading(readings, {key: value for key, value in pair.items() if key != "kind"})
    if context["reference"] is not None:
        reference = resolve_reading(readings, context["reference"])
    colors = _color_rows(readings)
    lab, reasons = lab_projections(colors, descriptor)
    reasons.update({key: _color_reason(row["observation"]) for key, row in readings.items()
                    if key not in colors})
    reply = dict(
        white=white,
        white_descriptor=descriptor,
        scale=scale,
        source_commit="installed-science",
        dataset_revision=data["dataset_revision"],
        comparison_version=context["version"],
        reference=reference,
    )
    if method == "projection":
        return dict(
            reply,
            lab_by_source=lab,
            color_reasons=reasons,
            display_previews=_display_previews(readings, scale),
            reference_gamuts=reference_projections(scale, descriptor, readings=colors),
        )
    selected = params["selected"]
    if (
        not isinstance(selected, list)
        or any(not isinstance(key, str) for key in selected)
        or len(set(selected)) != len(selected)
        or any(key not in readings for key in selected)
    ):
        raise ValueError("Choose retained readings")
    comparisons, deltas, xyz_comparisons, color_comparisons = {}, {}, {}, {}
    comparison_white = _comparison_white(descriptor)
    for key in selected:
        comparison = dict(residual=None, rms=None, reason=None)
        try:
            if reference is None:
                raise ValueError("Choose a saved reference reading")
            spectra = []
            for role, identity in (("reference", reference), ("compared", key)):
                observation = readings[identity]["observation"]
                if _record_kind(observation) != "color":
                    raise ValueError(_color_reason(observation))
                native = _color_input(observation, "spectrum")
                if native is None:
                    raise ValueError(f"The {role} reading reported no native spectrum")
                spectra.append(
                    Spectrum(
                        **{
                            k: tuple(v) if k in {"wavelengths_nm", "values"} else v
                            for k, v in native.items()
                        }
                    )
                )
            result = compare_spectra(*spectra)
            residual = result.residual
            comparison.update(
                rms=result.rms,
                residual=dict(
                    wavelengths_nm=residual.wavelengths_nm,
                    values=residual.values,
                    unit=residual.unit,
                    source_quantity=residual.source_quantity,
                    algorithm=residual.algorithm,
                    algorithm_version=residual.algorithm_version,
                ),
            )
        except (TypeError, ValueError, KeyError, OverflowError) as error:
            comparison["reason"] = str(error)
        comparisons[key] = comparison
        xyz = []
        for identity in (reference, key):
            native = (
                _color_input(readings[identity]["observation"], "xyz")
                if identity is not None else None
            )
            xyz.append(
                XYZ(values=tuple(native["values"]), **{
                    field: native[field]
                    for field in ("unit", "scale", "observer", "illuminant")
                }) if native is not None else None
            )
        xyz_comparisons[key] = asdict(compare_xyz(*xyz))
        color_comparisons[key] = asdict(compare_color(*xyz, reference_white=comparison_white))
        if color_comparisons[key]["delta_e2000"] is not None:
            deltas[key] = color_comparisons[key]["delta_e2000"]
    others = [key for key in selected if key != reference]
    valid = [comparisons[key] for key in others if comparisons[key]["rms"] is not None]

    def numeric_range(values, unit=None):
        return (
            dict(min=min(values), max=max(values), count=len(values), unit=unit)
            if values
            else None
        )

    signed_summary = {}
    xyz_unit = next((packet["xyz_unit"] for packet in color_comparisons.values()
                     if packet["xyz_unit"] is not None), None)
    for metric, dimensions, unit in (
        ("delta_xyz", 3, xyz_unit), ("delta_xy", 2, "1"), ("delta_uv", 2, "1"),
        ("delta_lab", 3, "1"), ("delta_chroma", None, "1"), ("delta_hue", None, "1"),
    ):
        values = [color_comparisons[key][metric] for key in others
                  if color_comparisons[key][metric] is not None]
        signed_summary[metric + "_range"] = (
            [numeric_range([value[index] for value in values], unit)
             for index in range(dimensions)] if dimensions is not None and values
            else numeric_range(values, unit) if dimensions is None else None
        )
        signed_summary[metric + "_unavailable"] = len(others) - len(values)

    return dict(
        reply,
        selected=selected,
        comparison_by_source=comparisons,
        xyz_comparison_by_source=xyz_comparisons,
        color_comparison_by_source=color_comparisons,
        delta_e_by_source=deltas,
        color_reasons=reasons,
        summary=dict(
            signed_summary,
            rms_range=numeric_range(
                [item["rms"] for item in valid], valid[0]["residual"]["unit"] if valid else None
            ),
            delta_range=numeric_range([deltas[key] for key in others if key in deltas]),
            y_percent_range=numeric_range(
                [xyz_comparisons[key]["y_percent"] for key in others
                 if xyz_comparisons[key]["y_percent"] is not None], "%"
            ),
            uv_distance_range=numeric_range(
                [xyz_comparisons[key]["uv_distance"] for key in others
                 if xyz_comparisons[key]["uv_distance"] is not None], "1"
            ),
            spectral_unavailable=len(others) - len(valid),
            color_unavailable=sum(key not in deltas for key in others),
            y_unavailable=sum(xyz_comparisons[key]["y_percent"] is None for key in others),
            uv_unavailable=sum(xyz_comparisons[key]["uv_distance"] is None for key in others),
        ),
    )
