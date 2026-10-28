"""Source-bound JSON/CSV analysis snapshots over the shared publication owner."""

import csv
import hashlib
import io
import json
from copy import deepcopy
from pathlib import Path

from . import presentation, processing, reading_dataset, science
from .application import _manifest
from .dataset_binding import content_digest
from .document_mat import to_mat
from .exports import _publish_new_file, _safe_cell
from .reading_dataset import _color_input, _record_kind, reading_identity, resolve_reading


class AnalysisExportError(OSError):
    """Retain prepared bytes and any publication receipt after an I/O failure."""

    def __init__(self, target, payload, format, cause):
        super().__init__(f"Could not save analysis to {target}: {cause}")
        self.path, self.payload, self.format = target, payload, format
        self.publication = getattr(cause, "publication", None)


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def prepare_analysis(binding, *, dataset_revision, comparison_version, scope, selected, scale,
                     derivation=None):
    """Freeze exact accepted selected/reference/white inputs and shared calculations."""
    if scope not in {"selected", "all"} or not isinstance(selected, list):
        raise ValueError("Choose selected or all readings with an explicit selection array")
    if scope == "all" and selected:
        raise ValueError("All-reading export requires an empty selection array")
    header, identity, payloads = binding.capture()
    binding.assert_capture(header, identity, payloads)
    data = binding.data()
    if dataset_revision != data["dataset_revision"]:
        raise ValueError("Reading revision changed")
    keys = list(data["readings"]) if scope == "all" else selected
    if not keys or any(not isinstance(key, str) or key not in data["readings"] for key in keys) \
            or len(set(keys)) != len(keys):
        raise ValueError("Select unique authenticated readings")
    params = dict(dataset_revision=dataset_revision, comparison_version=comparison_version,
                  scale=scale)
    analysis = binding.analyze("analysis", dict(params, selected=keys))
    projection = binding.analyze("projection", params)
    context = binding.context(comparison_version)
    derived_science = binding.derive(dataset_revision=dataset_revision, selected=keys,
                                    derivation=derivation) if derivation is not None else None
    if header["kind"] not in {"standalone", "batch"}:
        raise ValueError("Unsupported analysis source kind")
    standalone = header["kind"] == "standalone"
    accepted = {}
    if standalone:
        source = deepcopy(header["source"])
        original_file = deepcopy(header["original_file"])
        annotations = {(item["operation_id"], item["observation_id"]): item
                       for item in header["annotations"]}
        enclosing_operation = source["record_kind"] == "operation"
        originals = ([patch["observation"] for patch in header["record"]["patches"]]
                     if enclosing_operation else [header["record"]])
        for observation in originals:
            pair = observation["operation_id"], observation["observation_id"]
            accepted[pair] = None, observation
    else:
        manifest = header["manifest"]
        source = dict(kind="batch", batch_id=manifest["batch_id"],
                      context_id=manifest["context_id"],
                      manifest_sha256=header["manifest_sha256"],
                      descriptor=manifest["descriptor"], mode=manifest["mode"])
        original_file = None
        annotations = {(item["operation_id"], item["observation_id"]): item
                       for item in manifest["annotations"]}
        for item in header["operations"]:
            if item["review_state"] != "accepted":
                continue
            for patch in item["result"]["patches"]:
                observation = patch["observation"]
                pair = observation["operation_id"], observation["observation_id"]
                accepted[pair] = item, observation
        for inherited in header["inherited_operations"]:
            digest = inherited["source_manifest_sha256"]
            original_manifest = _manifest(payloads[f"source-{digest}.json"])
            original_entry = next(item for item in original_manifest["operations"]
                                  if item["operation_id"] == inherited["operation_id"])
            item = dict(original_entry, path=f"source-{original_entry['sha256']}.json",
                        result=inherited["result"])
            for patch in item["result"]["patches"]:
                observation = patch["observation"]
                pair = observation["operation_id"], observation["observation_id"]
                qualified_identity = inherited["segment_id"], digest, *pair
                accepted[qualified_identity] = item, observation
                annotations[qualified_identity] = next(
                    annotation for annotation in inherited["annotations"]
                    if (annotation["operation_id"], annotation["observation_id"]) == pair)

    documents = {}

    def input_record(key):
        row = data["readings"][key]
        observation = row["observation"]
        reference = row["reading_ref"]
        identity = reading_identity(reference)
        pair = observation["operation_id"], observation["observation_id"]
        if not row["saved"] or identity not in accepted:
            raise ValueError("Save each exported input before analysis export")
        item, original = accepted[identity]
        if content_digest(original) != content_digest(observation):
            raise ValueError("Exported observation differs from authenticated source")
        annotation = annotations[identity]
        if (row["version"] != annotation["version"] or row["name"] != annotation["name"]
                or binding.annotation_reference(reference)["applied"]["notes"]
                != annotation["notes"]):
            raise ValueError("Exported annotation differs from authenticated source")
        if standalone:
            native = header["native_input"]
            document_sha256 = native["canonical_json_sha256"]
            if document_sha256 not in documents:
                canonical = native["canonical_json"]
                if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != document_sha256:
                    raise ValueError("Native input document digest changed")
                documents[document_sha256] = dict(kind=native["kind"], canonical_json=canonical)
            operation = dict(
                operation_sha256=source["source_sha256"] if enclosing_operation else None,
                operation_status=header["record"]["status"] if enclosing_operation else None,
                repeat_of=None,
                operation_document=document_sha256 if enclosing_operation else None,
                native_input=dict(kind=native["kind"], format=native["format"],
                                  source_sha256=native["source_sha256"],
                                  document_sha256=document_sha256),
            )
        else:
            if item["sha256"] not in documents:
                canonical = payloads[item["path"]].decode("utf-8")
                if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != item["sha256"]:
                    raise ValueError("Operation document digest changed")
                documents[item["sha256"]] = dict(kind="operation", canonical_json=canonical)
            operation = dict(operation_sha256=item["sha256"],
                             operation_status=item["result"]["status"], repeat_of=item["repeat_of"],
                             operation_document=item["sha256"], native_input=None)
        return dict(
            reading_id=key, operation_id=pair[0], observation_id=pair[1],
            reading_ref=reference,
            record_kind=_record_kind(observation),
            name=annotation["name"], notes=annotation["notes"],
            annotation_version=annotation["version"],
            observation=observation, observation_sha256=content_digest(observation),
            **operation,
        )

    roles = {}
    for role, pair in (("reference", context["reference"]), ("white", context["white"])):
        if pair is not None and "operation_id" in pair:
            key = resolve_reading(data["readings"], {name: value for name, value in pair.items()
                                                  if name != "kind"})
            roles[role] = dict(input_record(key), selected=key in keys)
    if derived_science is not None and derivation["method"] == "adapt_xyz":
        for role in ("source_white", "destination_white"):
            declaration = derivation["context"][role]
            if isinstance(declaration, dict) and declaration.get("kind") == "retained-reading":
                key = resolve_reading(data["readings"], {
                    name: value for name, value in declaration.items() if name != "kind"})
                roles["adaptation_" + role] = dict(input_record(key), selected=key in keys)
    if derived_science is not None:
        ratio_context = None
        if derivation["method"] == "characterized_reflectance":
            ratio_context = derivation["context"]
        elif (derivation["method"] == "relight_reflectance"
              and derivation["context"]["reflectance"]["kind"] == "characterized-reference"):
            ratio_context = derivation["context"]["reflectance"]["context"]
        if ratio_context is not None:
            key = resolve_reading(data["readings"], ratio_context["reference"])
            roles["reflectance_reference"] = dict(input_record(key), selected=key in keys)
    records = []
    for key in keys:
        row = data["readings"][key]
        lab = projection["lab_by_source"].get(key)
        delta = analysis["delta_e_by_source"].get(key)
        derived = dict(
            xyy=row["xyy"], uvy=row["uvy"], coordinate_reasons=row["coordinate_reasons"],
            lab=lab, lab_reason=projection["color_reasons"].get(key) if lab is None else None,
            delta_e_2000=delta,
            delta_e_reason=analysis["color_comparison_by_source"][key]["delta_e2000_reason"],
            xyz_comparison=analysis["xyz_comparison_by_source"][key],
            color_comparison=analysis["color_comparison_by_source"][key],
            spectral_comparison=analysis["comparison_by_source"][key],
            display_previews=projection["display_previews"][key],
        )
        if lab is None and derived["lab_reason"] is None:
            derived["lab_reason"] = "Choose a declared Lab white"
        records.append(dict(input_record(key), derived=derived))
    result = dict(
        schema_version=1, export_kind="color-measurement-analysis", scope=scope,
        binding_id=binding.id, binding_generation=binding.generation,
        source=source, source_documents=documents, original_file=original_file,
        dataset_revision=dataset_revision, comparison_version=comparison_version,
        selection=keys, scale=scale, comparison=context, role_inputs=roles,
        records=records,
        summary=analysis["summary"],
        derived_science=derived_science,
        methods=dict(
            source_sha256={
                module.__name__: hashlib.sha256(
                    Path(module.__file__).with_suffix(".py").read_bytes()
                ).hexdigest()
                for module in (reading_dataset, presentation, processing, science)
            },
            coordinates="science.xyz_to_xyy / science.xyz_to_uvy",
            lab="science.xyz_to_lab", delta_e="science.delta_e_2000",
            xyz_comparison="processing.compare_xyz", spectra="processing.compare_spectra",
            color_comparison="processing.compare_color",
            rgb="common-scale display projection; signed channels plus explicit display clipping",
        ),
        limitations=[
            "Retained source and numerical calculation do not establish physical accuracy",
            "Exact-grid spectral comparison; no resampling or missing-band invention",
            "Source checks observe bounded read-only snapshots, not a filesystem transaction",
        ],
    )
    binding.assert_current()
    if binding.data()["dataset_revision"] != dataset_revision:
        raise ValueError("Dataset changed during export preparation")
    if binding.context(comparison_version) != context:
        raise ValueError("Analysis context changed during export preparation")
    binding.assert_capture(header, identity, payloads)
    return result, header, identity, payloads


def analysis_csv(snapshot):
    """Write native/derived rows with shared original documents only in the first row."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "reading_id", "operation_id", "observation_id", "name", "notes",
        "native_X", "native_Y", "native_Z", "native_xyz_unit", "native_xyz_scale",
        "xyY_json", "uvY_json", "Lab_json", "delta_e_2000", "Y_change_percent",
        "uv_distance", "spectral_rms", "derived_json", "record_json", "export_context_json",
        "record_kind", "native_quantity_json", "stimulus_json", "timing_context_json",
        "delta_XYZ_json", "delta_xy_json", "delta_uv_json", "delta_Lab_json",
        "delta_chroma", "delta_hue",
        "source_documents_json",
    ])
    common = {key: snapshot[key] for key in (
        "source_documents", "original_file", "role_inputs", "derived_science",
    )}
    context = {key: value for key, value in snapshot.items()
               if key not in {"records", "selection", *common}}
    context["role_inputs"] = {
        role: {key: record[key] for key in (
            "reading_id", "reading_ref", "selected", "observation_sha256", "operation_sha256",
            "operation_document", "native_input",
        )} for role, record in snapshot["role_inputs"].items()
    }
    if snapshot["derived_science"] is not None:
        context["derived_science"] = {key: snapshot["derived_science"][key] for key in (
            "method", "context_sha256", "implementation_sha256", "limitations",
        )}
    for index, record in enumerate(snapshot["records"]):
        observation = record["observation"]
        native = _color_input(observation, "xyz")
        derived = record["derived"]
        color = derived["color_comparison"]
        xyz = native["values"] if native is not None else (None, None, None)
        cells = [
            record["reading_id"], record["operation_id"], record["observation_id"],
            _safe_cell(record["name"]), _safe_cell(record["notes"]), *xyz,
            _safe_cell(native["unit"]) if native else None,
            _safe_cell(native["scale"]) if native else None,
            _json(derived["xyy"]), _json(derived["uvy"]), _json(derived["lab"]),
            derived["delta_e_2000"], derived["xyz_comparison"]["y_percent"],
            derived["xyz_comparison"]["uv_distance"], derived["spectral_comparison"]["rms"],
            _json(derived), _json(record), _json(context),
            _record_kind(observation), _json(observation["backend_reported"]),
            _json(observation.get("stimulus")), _json(observation.get("timing_context")),
            _json(color["delta_xyz"]), _json(color["delta_xy"]), _json(color["delta_uv"]),
            _json(color["delta_lab"]), color["delta_chroma"], color["delta_hue"],
            _json(common) if index == 0 else "",
        ]
        writer.writerow(["" if value is None else value for value in cells])
    return output.getvalue().encode("utf-8")


def publish_analysis(binding, path, *, format, **params):
    """Publish one new artifact; failed I/O retains its exact prepared bytes."""
    if format not in {"json", "csv", "mat"}:
        raise ValueError("Analysis export supports JSON, CSV or MAT")
    snapshot, header, identity, inputs = prepare_analysis(binding, **params)
    payload = to_mat(snapshot, kind="analysis") if format == "mat" else (
        (_json(snapshot) + "\n").encode("utf-8") if format == "json" else analysis_csv(snapshot)
    )
    binding.assert_capture(header, identity, inputs)
    context = dict(
        source=snapshot["source"],
        binding_id=snapshot["binding_id"], dataset_revision=snapshot["dataset_revision"],
        comparison_version=snapshot["comparison_version"], selection=snapshot["selection"],
    )
    return publish_prepared(path, payload=payload, format=format, context=context)


def publish_prepared(path, *, payload, format, context):
    """Publish the already authenticated immutable snapshot without recomputing it."""
    target = Path(path)
    try:
        publication = _publish_new_file(
            target, payload, lambda cause: AnalysisExportError(target, payload, format, cause),
        )
    except OSError as error:
        if isinstance(error, AnalysisExportError):
            error.context = context
            raise
        failure = AnalysisExportError(target, payload, format, error)
        failure.context = context
        raise failure from error
    return dict(
        path=str(target), format=format, sha256=hashlib.sha256(payload).hexdigest(),
        **context,
        publication=dict(path=str(publication.path), file_sync=publication.file_sync,
                         directory_synced=publication.directory_synced,
                         payload_sha256=hashlib.sha256(publication.payload).hexdigest(),
                         limitations=publication.limitations),
    )
