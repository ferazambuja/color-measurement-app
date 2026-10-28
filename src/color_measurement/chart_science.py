"""Pure chart consumers over original observations and explicit comparison policy.

The application authenticates source bytes, requests, selected attempts and
operation records. Canonical cell binding remains separate from native sample
identity. This module neither acquires data nor verifies physical placement.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict

from .chart_context import source_identity_keys
from .chart_state import validate_context, validate_definition
from .chart_targets import MAX_PATCHES, expected_reference_json
from .models import Observation, observation_from_dict
from .presentation import corresponding_color, qualify_reflective_xyz
from .science import ColorDomainError, xyz_to_lab

_NATIVE_FACTS = {"xyz_unit", "xyz_scale", "observer", "illuminant", "actual_mode"}
_NON_COLOR_FACTS = {
    "route",
    "origin",
    "model",
    "serial",
    "serial_encoding",
    "backend",
    "backend_version",
    "mode",
    "workflow",
    "spectral_quantity",
    "spectral_unit",
    "spectral_grid_nm",
    "spectrum_norm",
}


def _json(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    )


def _plain(value):
    try:
        return json.loads(_json(value))
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("Chart science requires finite JSON values") from error


def _digest(value):
    return hashlib.sha256(_json(value).encode("ascii")).hexdigest()


def _known(fact):
    return (
        isinstance(fact, dict)
        and fact["certainty"] not in {"unknown", "requested"}
        and fact["value"] is not None
        and not (
            isinstance(fact["value"], str)
            and fact["value"].strip().lower() in {"", "unknown", "unspecified", "not reported"}
        )
    )


def _material_compatible(before_source, after_source):
    before, after = (source["accepted_context"] for source in (before_source, after_source))
    identities = [
        source_identity_keys(source["observation"], source["accepted_context"])
        for source in (before_source, after_source)
    ]
    keys = _NATIVE_FACTS | (
        (set(before["required_material"]) | set(after["required_material"])) - _NON_COLOR_FACTS
    )
    keys = {
        key
        for key in keys
        if not (
            (key in {"device_id", "driver"} or key.startswith("native_identity:"))
            and all(
                key not in context["material"] or key in bound
                for context, bound in zip((before, after), identities)
            )
        )
    }
    for key in keys:
        old, new = before["material"].get(key), after["material"].get(key)
        if not _known(old) or not _known(new) or _json(old["value"]) != _json(new["value"]):
            return False
    # Acquisition provenance must agree; device identity has no colorimetric law.
    return before["provenance"].get("observation_provenance") == after["provenance"].get(
        "observation_provenance"
    )


def qualify_chart_reading(observation, *, reading_ref, accepted_context, observer_csv):
    """Retain native XYZ only when original identity and reflective facts agree.

    Units must be one, scale perfect-diffuser Y=100, observer CIE1931_2 and
    illuminant D50/D65/A. Require finite nonnegative XYZ from an Argyll native
    or explicitly simulated Demo reflective mode. No radiance relabelling,
    zero replacement, computed-XYZ substitution or sample normalization occurs.
    The theoretical common white reuses illumination.illuminant_white: CIE
    original 1 nm SPD/CMF sum on 360..830 nm, at Y=100. See its primary sources.

    Example: native XYZ=(0,0,0) is available with Lab=(0,0,0); missing native
    XYZ is unavailable even if project-computed Lab exists. Accepted facts
    must match the actual observation; requested/unknown facts cannot qualify.
    Application caller authenticates bytes, source ownership and selected
    attempt beforehand. Neither those checks nor this function prove physical
    specimen, illumination, calibration, geometry or placement.
    """
    if not isinstance(observation, Observation):
        raise ValueError("Chart source requires an original Observation")
    context = validate_context(accepted_context)
    if (
        not isinstance(reading_ref, dict)
        or set(reading_ref) != {"segment_id", "operation_id", "observation_id", "sample_id"}
        or any(
            not isinstance(value, str) or not value or "\0" in value
            for value in reading_ref.values()
        )
    ):
        raise ValueError("Chart source requires the exact four reading identities")
    result = dict(available=False, reason="", xyz=None, context=None, lab=None)
    reason = None
    if any(
        reading_ref[key] != getattr(observation, key)
        for key in ("operation_id", "observation_id", "sample_id")
    ):
        reason = "Original observation identity differs from the selected reading"
    elif context["phase"] != "accepted":
        reason = "Chart colors require accepted acquisition facts"
    native = observation.backend_reported.xyz
    if not reason and native is None:
        reason = "Native XYZ is unavailable; no replacement is inferred"
    if not reason:
        facts = dict(
            xyz_unit=native.unit,
            xyz_scale=native.scale,
            observer=native.observer,
            illuminant=native.illuminant,
            actual_mode=observation.actual_mode,
        )
        for key, expected in facts.items():
            fact = context["material"].get(key)
            if not _known(fact) or _json(fact["value"]) != _json(expected):
                reason = f"Accepted {key} is missing, unestablished or differs from native data"
                break
        if context["provenance"].get("observation_provenance") != observation.provenance:
            reason = "Accepted provenance differs from the original observation"
    if not reason:
        qualified = qualify_reflective_xyz(observation, observer_csv=observer_csv)
        reason = qualified["reason"] or None
    if not reason:
        try:
            lab = xyz_to_lab(native.values, qualified["context"]["white"]["xyz"])
            result.update(
                available=True,
                xyz=list(native.values),
                lab=list(lab),
                context=qualified["context"],
            )
        except (ColorDomainError, ValueError, OverflowError, TypeError) as error:
            reason = f"Relative chart calculation unavailable: {error}"
    result["reason"] = reason or ""
    return _plain(result)


def _source(wrapper, count):
    if not isinstance(wrapper, dict) or set(wrapper) != {
        "cell_index",
        "observation",
        "reading_ref",
        "accepted_context",
    }:
        raise ValueError(
            "Chart source requires cell_index, observation, reading_ref, accepted_context"
        )
    if type(wrapper["cell_index"]) is not int or not 1 <= wrapper["cell_index"] <= count:
        raise ValueError("Chart source cell_index is outside the canonical grid")
    if not isinstance(wrapper["observation"], Observation):
        raise ValueError("Chart source requires an original Observation")
    return _plain(dict(wrapper, observation=asdict(wrapper["observation"])))


def chart_reading_colors(wrapper, *, observer_csv, color_space="srgb"):
    """Return a qualified native chart swatch under the shared corresponding model.

    Calls qualify_chart_reading then presentation.corresponding_color, retaining
    original native XYZ/Lab/white, signed linear RGB and display clipping separately.
    All cells use common white Y=100, never each sample's own luminance.
    Example: black produces RGB=(0,0,0), white approximately (1,1,1).
    Output is an ideal-display illustration, not a measured display appearance.
    Existing Bradford and RGB scientific owners retain equations and citations.
    """
    source = _source(wrapper, MAX_PATCHES)
    values = qualify_chart_reading(
        wrapper["observation"],
        reading_ref=source["reading_ref"],
        accepted_context=source["accepted_context"],
        observer_csv=observer_csv,
    )
    return dict(
        values, cell_index=source["cell_index"], color=_color(values, color_space), source=source
    )


def _color(values, color_space):
    if not values["available"]:
        return dict(available=False, reason=values["reason"], encoding=None)
    try:
        encoding = corresponding_color(
            values["xyz"],
            reference_white=values["context"]["white"]["xyz"],
            observer=values["context"]["observer"],
            color_space=color_space,
        )
        return _plain(dict(available=True, reason="", encoding=asdict(encoding)))
    except (ColorDomainError, ValueError, OverflowError) as error:
        return dict(available=False, reason=str(error), encoding=None)


def chart_expected_colors(definition, context_json, *, color_space="srgb"):
    """Compose authenticated TI2 expected values with exact canonical mapping.

    Reuses chart_targets.expected_reference_json, its strict retained-source
    validation and established Lab inverse. Source row/ID/location must match
    the canonical cell, independently of a native reading's sample ID.
    CIE1931_2deg producer spelling denotes exactly Python's CIE1931_2, the same
    fixed two-degree definition; no other observer is mapped. An explicit common
    white is retained, including declared E/custom expected contexts. Negative
    expected XYZ stays original but is unavailable for nonnegative chart display.
    Example: expected Lab=(100,0,0) illustrates the declared white as white.
    No expected row becomes a measurement or qualifies a position warning.
    The existing inverse/Bradford/RGB owners retain equations and primary sources.
    """
    definition = validate_definition(definition)
    if definition["kind"] != "ti2":
        return dict(
            available=False,
            reason="No authenticated TI2 expected values",
            definition_id=definition["id"],
            context=None,
            cells=[],
        )
    expected = json.loads(expected_reference_json(definition["target_json"], context_json))
    rows = {row["source_row"]: row for row in expected["cells"]}
    cells = []
    for cell in definition["cells"]:
        row = rows[cell["source_row"]]
        if (row["sample_id"], row["sample_loc"]) != (cell["source_id"], cell["source_location"]):
            raise ValueError("Expected row identity differs from canonical source mapping")
        context = expected["context"]
        reason = row["reason"]
        if row["available"] and any(value < 0 for value in row["XYZ"]):
            reason = "Expected XYZ is retained but negative chart display is unavailable"
        values = dict(
            available=not reason,
            reason=reason,
            xyz=row["XYZ"],
            context=(
                dict(observer="CIE1931_2", white=dict(xyz=context["whiteXYZ"]))
                if row["available"]
                else None
            ),
        )
        cells.append(dict(cell, expected=row, color=_color(values, color_space)))
    return dict(
        available=any(cell["color"]["available"] for cell in cells),
        reason="",
        definition_id=definition["id"],
        source_sha256=expected["source_sha256"],
        context=expected["context"],
        cells=cells,
    )


def _policy(qualification, criteria, count):
    qualification, criteria = _plain(qualification), _plain(criteria)
    labels = {"specimen_id", "edition", "geometry", "backing", "lighting", "declarer"}
    if (
        not isinstance(qualification, dict)
        or set(qualification) != labels | {"matched_conditions_confirmed"}
        or qualification["matched_conditions_confirmed"] is not True
    ):
        raise ValueError(
            "Explicit specimen, edition, geometry, backing, lighting and match are required"
        )
    for key in labels:
        _label(qualification[key], key)
    numeric = ("maximum_delta_e", "reference_bound", "measurement_bound")
    if not isinstance(criteria, dict) or set(criteria) != set(numeric) | {"basis", "coverage"}:
        raise ValueError("Explicit allowance, bounds, basis and coverage are required")
    for key in ("basis", "coverage"):
        _label(criteria[key], key)
    for key in numeric:
        values = criteria[key] if isinstance(criteria[key], list) else [criteria[key]]
        try:
            valid = all(
                type(value) in (int, float) and math.isfinite(value) and value >= 0
                for value in values
            )
        except OverflowError:
            valid = False
        if len(values) not in {1, count} or not valid:
            raise ValueError(f"{key} requires a finite nonnegative scalar or one value per cell")
        criteria[key] = values * count if len(values) == 1 else values
    if any(not math.isfinite(sum(criteria[key][i] for key in numeric)) for i in range(count)):
        raise ValueError("Combined chart comparison budget exceeds the finite range")
    return qualification, criteria


def _label(value, name):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 2048
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(f"{name} requires bounded nonblank declaration text")


def prepare_position_reference(definition, selected, *, qualification, criteria, observer_csv):
    """Freeze explicit policy and selected original measured sources, one per cell.

    CIE S 017:2020 17-23-076/077 define Lab and Euclidean DeltaE*ab:
    https://cie.co.at/eilvterm/17-23-076 and https://cie.co.at/eilvterm/17-23-077.
    Criteria are nonnegative scalar or N-cell Lab-distance declarations, not
    sensor uncertainty estimates. This factory preserves holes and unavailable
    originals; any eligible coverage qualifies only those cells. The SHA256
    digest binds exact canonical mapping, sources, computed values and policy.
    Caller retains separately authenticated source-byte/run/pass ownership.

    Example: allowance=2, reference_bound=1, measurement_bound=1 yields budget4.
    Qualification explicitly names specimen, edition, geometry, backing, lighting,
    declarer and matched conditions. Those declarations are not optical proof.
    No illustration-only target, editable XYZ or computed Lab replaces native data.
    No acquisition, user-source file access, inferred position or default limit occurs.
    """
    definition = validate_definition(definition)
    count = len(definition["cells"])
    qualification, criteria = _policy(qualification, criteria, count)
    if not isinstance(selected, (list, tuple)) or len(selected) > count:
        raise ValueError("Select at most one original measured reading per canonical cell")
    cells = [
        dict(
            mapping=cell,
            source=None,
            values=dict(
                available=False,
                reason="No selected measured reference",
                xyz=None,
                context=None,
                lab=None,
            ),
        )
        for cell in definition["cells"]
    ]
    seen, readings = set(), set()
    for wrapper in selected:
        if wrapper is None:
            continue
        source = _source(wrapper, count)
        index = source["cell_index"]
        if index in seen:
            raise ValueError("Duplicate selected reference cell")
        reading_identity = tuple(
            source["reading_ref"][key] for key in ("operation_id", "observation_id")
        )
        if reading_identity in readings:
            raise ValueError("One original reading cannot be selected for multiple reference cells")
        seen.add(index)
        readings.add(reading_identity)
        values = qualify_chart_reading(
            wrapper["observation"],
            reading_ref=source["reading_ref"],
            accepted_context=source["accepted_context"],
            observer_csv=observer_csv,
        )
        if values["available"]:
            context = source["accepted_context"]
            reason = None
            if not _material_compatible(source, source):
                reason = "Required measured-reference material conditions are unestablished"
            for key in ("specimen_id", "edition", "geometry", "backing", "lighting"):
                fact = context["material"].get(key)
                if _known(fact) and _json(fact["value"]) != _json(qualification[key]):
                    reason = f"Measured reference {key} differs from the matched qualification"
                    break
            if reason:
                values = dict(available=False, reason=reason, xyz=None, context=None, lab=None)
        cells[index - 1].update(source=source, values=values)
    coverage = sum(cell["values"]["available"] for cell in cells)
    packet = dict(
        format="chart-position-reference",
        version=1,
        definition_id=definition["id"],
        qualified=coverage > 0,
        coverage=coverage,
        total=count,
        cells=cells,
        qualification=qualification,
        criteria=criteria,
        algorithm="CIE76-declared-Lab-envelope",
        algorithm_version="1",
    )
    return dict(packet, digest=_digest(packet))


def validate_position_reference(reference, definition, *, observer_csv):
    """Rebuild a frozen scientific reference from its retained originals and policy."""
    reference = _plain(reference)
    if not isinstance(reference, dict) or not isinstance(reference.get("cells"), list):
        raise ValueError("Invalid measured chart reference")
    sources = []
    try:
        for cell in reference["cells"]:
            if cell["source"] is not None:
                source = cell["source"]
                sources.append(
                    dict(source, observation=observation_from_dict(source["observation"]))
                )
        rebuilt = prepare_position_reference(
            definition,
            sources,
            qualification=reference["qualification"],
            criteria=reference["criteria"],
            observer_csv=observer_csv,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Measured chart reference cannot be reconstructed: {error}") from error
    if _json(reference) != _json(rebuilt):
        raise ValueError("Measured chart reference source, mapping, policy or projection changed")
    return rebuilt


def _distance(a, b):
    distance = math.hypot(*(x - y for x, y in zip(a, b)))
    if not math.isfinite(distance):
        raise ColorDomainError("Lab distance exceeds the finite range")
    return distance


def chart_position_check(reference, definition, wrapper, *, observer_csv):
    """Compare original Lab to a frozen measured cell using declared CIE76 limits.

    d=hypot(deltaL*,deltaa*,deltab*), T=allowance+reference_bound+measurement_bound.
    Warn only if d>T; equality does not warn. The triangle inequality supports
    addition of declared same-space Lab-vector envelopes, without establishing
    their coverage or measurement uncertainty. CIE S 017:2020 17-23-077:
    https://cie.co.at/eilvterm/17-23-077. Compute before RGB or clipping.

    Example: Lab50 to Lab54 gives d4; a declared total4 does not warn, total3 does.
    Require established matching material and native white/quantity context.
    Device model/serial/backend identity is retained, not a colorimetric equality
    law. Compatible reference regions overlap when separation<=Ti+Tj; preserve
    nearest overlapping peer and numerical refusal. Even separated regions do
    not identify an alternative cell or verify physical placement. Missing,
    unqualified or mismatched sources remain unavailable with original data intact.
    """
    definition = validate_definition(definition)
    source = _source(wrapper, len(definition["cells"]))
    result = dict(
        available=False,
        warning=False,
        reference_digest=None,
        cell_index=source["cell_index"],
        reading_ref=source["reading_ref"],
        reason="No qualified measured reference",
        distance=None,
        limit=None,
        nearest_reference_separation=None,
        overlapping_cell=None,
        alternative_reason="Physical placement is not verified",
    )
    if reference is None:
        return result
    reference = validate_position_reference(reference, definition, observer_csv=observer_csv)
    result["reference_digest"] = reference["digest"]
    index = source["cell_index"] - 1
    cell = reference["cells"][index]
    values = qualify_chart_reading(
        wrapper["observation"],
        reading_ref=source["reading_ref"],
        accepted_context=source["accepted_context"],
        observer_csv=observer_csv,
    )
    if not cell["values"]["available"] or not values["available"]:
        result["reason"] = (
            cell["values"]["reason"] if not cell["values"]["available"] else values["reason"]
        )
        return result
    if cell["values"]["context"] != values["context"] or not _material_compatible(
        cell["source"], source
    ):
        result["reason"] = "Reference and reading conditions are mismatched or unestablished"
        return result
    budgets = [
        sum(
            reference["criteria"][key][i]
            for key in ("maximum_delta_e", "reference_bound", "measurement_bound")
        )
        for i in range(reference["total"])
    ]
    try:
        distance = _distance(values["lab"], cell["values"]["lab"])
        nearest, overlap, overlap_distance, unavailable, compatible = None, None, None, False, 0
        for i, peer in enumerate(reference["cells"]):
            if (
                i == index
                or not peer["values"]["available"]
                or peer["values"]["context"] != cell["values"]["context"]
                or not _material_compatible(peer["source"], cell["source"])
            ):
                continue
            compatible += 1
            try:
                separation = _distance(peer["values"]["lab"], cell["values"]["lab"])
            except ColorDomainError:
                unavailable = True
                continue
            nearest = separation if nearest is None else min(nearest, separation)
            if separation <= budgets[index] + budgets[i]:
                if overlap_distance is None or separation < overlap_distance:
                    overlap, overlap_distance = i + 1, separation
        if overlap is not None:
            alternative = "Declared reference regions overlap; alternative positions are ambiguous"
        elif unavailable:
            alternative = "Reference separation exceeds finite range; separation is unestablished"
        elif compatible == 0:
            alternative = "No other reference has compatible established conditions"
        else:
            alternative = (
                "Declared reference regions are separated; physical position is unverified"
            )
        warning = distance > budgets[index]
        result.update(
            available=True,
            warning=warning,
            distance=distance,
            limit=budgets[index],
            nearest_reference_separation=nearest,
            overlapping_cell=overlap,
            alternative_reason=alternative,
            reason=(
                "Reading exceeds the declared total; check placement"
                if warning
                else "Within the declared total; physical position is not verified"
            ),
        )
    except ColorDomainError as error:
        result["reason"] = f"Position check unavailable: {error}"
    return result
