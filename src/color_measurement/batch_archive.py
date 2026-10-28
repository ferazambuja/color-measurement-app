"""Lossless current-batch MAT archive, reopened through the ordinary read-only validator."""

import base64
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from .application import _manifest, review_saved
from .dataset_binding import (
    assert_captured,
    assert_review_files,
    chart_source_files,
    referenced_paths,
)
from .document_mat import from_mat, to_mat
from .exports import _safe_cell
from .operation_exports import MAT_UNAVAILABLE_REASON


def _name(name):
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or PurePosixPath(name).is_absolute()
            or any(part in {"", ".", ".."} for part in name.split("/"))):
        raise ValueError("Batch archive contains an unsafe source path")
    return name


def prepare(binding):
    """Capture canonical bytes, including available excluded files, without relabeling them."""
    review, identity, files = binding.capture()
    folder = Path(review["folder"])
    if review["chart"] is not None and not review["chart"]["available"]:
        raise ValueError("Cannot archive unauthenticated chart sources: "
                         + review["chart"]["reason"])
    if review["capture"]["uncopyable_files"]:
        raise ValueError("Cannot archive unavailable declared sources: "
                         + ", ".join(sorted(review["capture"]["uncopyable_files"])))
    missing = sorted(referenced_paths(review["manifest"], chart_source_files(review))
                     - files.keys())
    assert_captured(folder, identity, files, missing=missing)
    payload, source = encode(review, files, missing_files=missing)
    assert_captured(folder, identity, files, missing=missing)
    return payload, source


def encode(review, files, *, missing_files=()):
    """Serialize an authenticated frozen capture without filesystem I/O or publication."""
    if review["chart"] is not None and not review["chart"]["available"]:
        raise ValueError("Cannot archive unauthenticated chart sources: "
                         + review["chart"]["reason"])
    assert_review_files(review, files)
    if _manifest(files["batch.json"]) != review["manifest"]:
        raise ValueError("Captured manifest differs from its authenticated review")
    missing = list(missing_files)
    if (len(set(missing)) != len(missing) or set(missing) & files.keys()
            or files.keys() | set(missing) != referenced_paths(
                review["manifest"], chart_source_files(review),
            )):
        raise ValueError("Archive inventory differs from its declared canonical inputs")
    document = dict(
        schema_version=1, archive_kind="current-batch", missing_files=missing,
        source=dict(batch_id=review["manifest"]["batch_id"],
                    context_id=review["manifest"]["context_id"],
                    manifest_sha256=review["manifest_sha256"]),
        files={name: dict(sha256=hashlib.sha256(raw).hexdigest(),
                         bytes_base64=base64.b64encode(raw).decode("ascii"))
               for name, raw in files.items()},
    )
    payload = to_mat(document, kind="batch")
    return payload, document["source"]


def native_csv(review, *, selected_passes=None):
    """Inspect authenticated native readings and annotations, including inherited sources.

    CSV is a reading table, not a replacement for the complete MAT archive. Empty
    batches retain the header; unavailable color values stay empty while complete
    typed observations and annotations remain in JSON cells. No science runs.
    """
    if review["chart"] is not None and not review["chart"]["available"]:
        raise ValueError("Cannot export unauthenticated chart sources: "
                         + review["chart"]["reason"])
    manifest = review["manifest"]
    selected = None
    by_reference = {}
    if selected_passes is not None:
        chart = manifest["chart"]
        passes = {item["id"]: item for item in chart["passes"]} if chart else {}
        if (len(set(selected_passes)) != len(selected_passes)
                or not set(selected_passes) <= passes.keys()):
            raise ValueError("Unknown or repeated authenticated chart pass")
        selected = {}
        for pass_id in selected_passes:
            chosen = passes[pass_id]
            attempts = {item["id"]: item for item in chosen["attempts"]}
            for identity in chosen["selected_attempts"]:
                if identity is not None:
                    attempt = attempts[identity]
                    for reference in attempt["readings"]:
                        key = tuple(reference[field] for field in (
                            "segment_id", "operation_id", "observation_id", "sample_id",
                        ))
                        selected[(pass_id, key)] = (
                            attempt["cell"], reference,
                            chart["definition"]["cells"][attempt["cell"] - 1],
                        )
                        by_reference.setdefault(key, []).append((pass_id, key))
    sources = [(manifest["batch_id"], review["manifest_sha256"], item["result"],
                manifest["annotations"])
               for item in review["operations"] if item["review_state"] == "accepted"]
    sources.extend((item["segment_id"], item["source_manifest_sha256"], item["result"],
                    item["annotations"]) for item in review["inherited_operations"])
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "batch_id", "source_manifest_sha256", "segment_id", "operation_id", "observation_id",
        "sample_id", "provenance", "record_kind", "operation_status", "patch_index",
        "name", "notes", "native_X", "native_Y", "native_Z", "native_xyz_unit",
        "native_xyz_scale", "observation_json", "annotation_json", "cell_index", "reading_ref_json",
        "target_sample_id", "target_sample_location", "pass_id",
    ])
    rows, selected_rows = [], {}
    for segment, digest, result, annotations in sources:
        overlays = {(item["operation_id"], item["observation_id"]): item for item in annotations}
        for patch in result["patches"]:
            observation = patch["observation"]
            pair = observation["operation_id"], observation["observation_id"]
            key = segment, *pair, observation["sample_id"]
            annotation = overlays[pair]
            kind = observation.get("record_kind", "color")
            xyz = observation["backend_reported"]["xyz"] if kind == "color" else None
            values = xyz["values"] if xyz is not None else (None, None, None)
            cells = [manifest["batch_id"], digest, segment, *pair, observation["sample_id"],
                     observation["provenance"], kind, result["status"], patch["index"],
                     annotation["name"], annotation["notes"], *values,
                     xyz["unit"] if xyz else None, xyz["scale"] if xyz else None,
                     json.dumps(observation, ensure_ascii=False, allow_nan=False, sort_keys=True),
                     json.dumps(annotation, ensure_ascii=False, allow_nan=False, sort_keys=True)]
            row = [_safe_cell(value) if isinstance(value, str) else
                   "" if value is None else value for value in cells]
            if selected is None:
                rows.append(row + ["", "", "", "", ""])
            else:
                for selection in by_reference.get(key, []):
                    cell, reference, target = selected[selection]
                    suffix = [cell, json.dumps(reference, allow_nan=False, sort_keys=True),
                              target["source_id"], target["source_location"], selection[0]]
                    selected_rows[selection] = row + [
                        _safe_cell(value) if isinstance(value, str) else
                        "" if value is None else value for value in suffix
                    ]
    if selected is not None and selected_rows.keys() != selected.keys():
        raise ValueError("Selected chart readings cannot be authenticated")
    writer.writerows(rows if selected is None else [selected_rows[key] for key in selected])
    return output.getvalue().encode("utf-8")


def prepare_from_capture(review, files, *, missing_files=()):
    """Prepare each automatic companion independently from one frozen native capture.

    The application owns publication, status, retry and source advancement. This
    helper only serializes once; callers retain prepared bytes rather than
    recomputing MAT's dated header after a publication failure.
    """
    assert_review_files(review, files)
    prepared = dict(source_manifest_sha256=review["manifest_sha256"])
    chart = review["manifest"]["chart"]
    actions = dict(
        mat=lambda: encode(review, files, missing_files=missing_files)[0],
        csv=lambda: native_csv(review),
        chart_selected_csv=lambda: native_csv(
            review, selected_passes=[item["id"] for item in chart["passes"]],
        )
        if chart is not None else None,
    )
    for format, action in actions.items():
        try:
            payload = action()
            reason = None if payload is not None else "This batch has no guided chart"
            retryable = False
        except ImportError as error:
            # Automatic files are shown in the workspace: MAT unavailability
            # uses the operator reason, not the package-extra install hint.
            payload, retryable = None, False
            reason = MAT_UNAVAILABLE_REASON if format == "mat" else str(error)
        except (OSError, ValueError, UnicodeError) as error:
            payload, reason = None, str(error)
            retryable = True
        prepared[format] = dict(payload=payload, reason=reason, retryable=retryable)
    return prepared


def unpack(payload):
    """Materialize one temporary source; never instantiate a writable batch owner."""
    document = from_mat(payload, kind="batch")
    if (set(document) != {"schema_version", "archive_kind", "missing_files", "source", "files"}
            or type(document["schema_version"]) is not int or document["schema_version"] != 1
            or document["archive_kind"] != "current-batch"
            or not isinstance(document["files"], dict) or "batch.json" not in document["files"]
            or not isinstance(document["missing_files"], list)
            or not isinstance(document["source"], dict)
            or set(document["source"]) != {"batch_id", "context_id", "manifest_sha256"}):
        raise ValueError("Invalid current-batch archive")
    files = {}
    for name, item in document["files"].items():
        _name(name)
        if not isinstance(item, dict) or set(item) != {"sha256", "bytes_base64"}:
            raise ValueError("Invalid archive file identity")
        try:
            raw = base64.b64decode(item["bytes_base64"], validate=True)
        except (ValueError, TypeError) as error:
            raise ValueError("Invalid archive source bytes") from error
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("Archive source digest differs")
        files[name] = raw
    missing = [_name(name) for name in document["missing_files"]]
    if len(set(missing)) != len(missing) or set(missing) & files.keys():
        raise ValueError("Archive missing-file facts conflict")
    if hashlib.sha256(files["batch.json"]).hexdigest() != document["source"]["manifest_sha256"]:
        raise ValueError("Archive manifest digest differs")
    manifest = _manifest(files["batch.json"])
    if any(document["source"][field] != manifest[field] for field in ("batch_id", "context_id")):
        raise ValueError("Archive source identity differs from its original manifest")
    temporary = TemporaryDirectory(prefix="cm-readonly-mat-source-")
    root = Path(temporary.name)
    try:
        for name, raw in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as output:
                output.write(raw)
        review = review_saved(root)
        if review["chart"] is not None and not review["chart"]["available"]:
            raise ValueError("Archive chart source graph cannot be authenticated: "
                             + review["chart"]["reason"])
        if files.keys() | set(missing) != referenced_paths(manifest, chart_source_files(review)):
            raise ValueError("Archive inventory differs from its declared canonical inputs")
    except BaseException:
        temporary.cleanup()
        raise
    return temporary
