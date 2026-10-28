"""Pure authentication of portable guided-chart source manifests and outcomes.

The application supplies exact retained bytes and its canonical validators.
This module neither opens paths nor owns publication, acquisition, or science.
Source labels are provenance text; only content hashes resolve portable blobs.
"""

from __future__ import annotations

import hashlib
import json
from collections import ChainMap
from collections.abc import Mapping
from uuid import UUID

from .operation_records import result_from_json

MAX_SOURCE_SEGMENTS = 64
MAX_BLOB_BYTES = 32 * 1024 * 1024


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True).encode("ascii")
    except (ValueError, TypeError, RecursionError) as error:
        raise ValueError("Chart source must contain finite JSON values") from error


def _digest(payload):
    return hashlib.sha256(payload).hexdigest()


def _sha(value):
    if not isinstance(value, str) or len(value) != 64 or any(
        letter not in "0123456789abcdef" for letter in value
    ):
        raise ValueError("Chart source needs a lowercase SHA-256")
    return value


def _segment(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError("Chart source needs a canonical segment UUID")
    except (ValueError, AttributeError) as error:
        raise ValueError("Chart source needs a canonical segment UUID") from error
    return value


def _source(source):
    if not isinstance(source, dict) or set(source) != {"source_id", "segment_id", "sha256"}:
        raise ValueError("Chart sources contain metadata only")
    label = source["source_id"]
    if not isinstance(label, str) or not label or len(label) > 4096 or "\0" in label:
        raise ValueError("Chart source label must be bounded nonempty text")
    _segment(source["segment_id"])
    _sha(source["sha256"])
    return source


def _blob(blobs, digest, retained, *, allow_empty=False):
    _sha(digest)
    try:
        payload = blobs[digest]
    except KeyError as error:
        raise ValueError("Missing chart source blob") from error
    if (not isinstance(payload, bytes) or not payload and not allow_empty
            or len(payload) > MAX_BLOB_BYTES):
        raise ValueError("Missing chart source blob or bytes exceed 32 MiB")
    if _digest(payload) != digest:
        raise ValueError("Chart source blob digest does not match exact bytes")
    retained[digest] = payload
    return payload


def _chart(manifest):
    chart = manifest.get("chart")
    if not isinstance(chart, dict) or chart.get("segment_id") != manifest.get("batch_id"):
        raise ValueError("Chart source segment does not match its manifest")
    _segment(chart["segment_id"])
    if not isinstance(chart.get("sources"), list):
        raise ValueError("Chart source metadata must be a list")
    seen = set()
    for source in chart["sources"]:
        _source(source)
        if source["segment_id"] in seen:
            raise ValueError("Duplicate chart source segment metadata")
        seen.add(source["segment_id"])
    return chart


def _attempts(chart):
    return [
        (item["id"], attempt)
        for item in chart["passes"]
        for attempt in item["attempts"]
    ]


def _diagnostic(entry, blobs, retained, *, qualify):
    capture = entry["native_diagnostics"]
    if capture is None:
        return
    if qualify and capture["state"] != "not_started" and (
            capture["state"] in {"prepared", "capturing"} or capture["sha256"] is None):
        raise ValueError("A chart source has unsaved native diagnostic capture")
    if capture["sha256"] is None:
        return
    payload = _blob(blobs, capture["sha256"], retained,
                    allow_empty=capture["retained_bytes"] == 0)
    if len(payload) != capture["retained_bytes"]:
        raise ValueError("Native diagnostic bytes do not match the retained capture length")
    if (capture["file_sha256"] is not None
            and _digest(payload[:capture["written_bytes"]]) != capture["file_sha256"]):
        raise ValueError("Native diagnostic file digest does not match its original written prefix")


def _outcomes(manifest, blobs, retained, operations, recoveries, *, qualify, check_result,
              check_recovery):
    segment = manifest["batch_id"]
    annotations = {
        (item["operation_id"], item["observation_id"]) for item in manifest["annotations"]
    }
    expected_annotations = set()
    for entry in manifest["operations"]:
        _diagnostic(entry, blobs, retained, qualify=qualify)
        key = (segment, entry["operation_id"])
        if entry["state"] in {"reserved", "started"}:
            if qualify:
                raise ValueError("A chart source has unresolved prepared or started work")
            continue
        if entry["state"] == "terminal_unknown":
            if entry["recovery"] is None:
                if qualify:
                    raise ValueError("A chart source needs its saved terminal recovery")
                continue
            payload = _blob(blobs, entry["recovery"]["sha256"], retained)
            failure = check_recovery(payload, entry)
            if qualify and failure["software_stopped"] is not True:
                raise ValueError("Chart source recovery does not confirm software stop")
            recoveries[key] = failure
            continue
        payload = _blob(blobs, entry["sha256"], retained)
        result = result_from_json(payload.decode("utf-8"))
        check_result(result, entry, manifest["descriptor"])
        if qualify and result.software_stopped is not True:
            raise ValueError("Chart source operation does not confirm software stop")
        operations[key] = result
        expected_annotations.update(
            (result.operation_id, patch.observation.observation_id) for patch in result.patches
        )
    if annotations != expected_annotations:
        raise ValueError("Chart source annotations do not match canonical reading identities")
    for entry in manifest["operations"]:
        repeat = entry["repeat_of"]
        if repeat is None:
            continue
        original = operations.get((segment, repeat["operation_id"]))
        if (original is None or original.status != "completed" or not any(
                patch.observation.observation_id == repeat["observation_id"]
                for patch in original.patches)):
            raise ValueError("Chart source Repeat reading cannot be authenticated")


def _local_attempts(manifest, operations, recoveries, *, qualify):
    segment = manifest["batch_id"]
    entries = {item["command_id"]: item for item in manifest["operations"]}
    for _, attempt in _attempts(_chart(manifest)):
        if attempt["segment_id"] != segment:
            continue
        entry = entries.get(attempt["command_id"])
        if (entry is None or entry["operation_id"] != attempt["operation_id"]
                or _json(entry["request"]) != _json(attempt["request"])):
            raise ValueError("Chart attempt does not match its original reserved intent")
        if attempt["status"] == "started":
            if qualify or entry["state"] not in {"reserved", "started"}:
                raise ValueError("Chart source attempt has no retained terminal outcome")
            continue
        key = (segment, attempt["operation_id"])
        result = operations.get(key)
        if result is None:
            failure = recoveries.get(key)
            if (failure is None or attempt["status"] != "unresolved" or attempt["readings"]
                    or attempt["result_status"] != "unresolved"):
                raise ValueError("Chart attempt has no matching canonical outcome or recovery")
            if attempt["result_reason"] != failure["reason"][:8192]:
                raise ValueError(
                    "Chart recovery reason does not match its original terminal failure"
                )
            continue
        if (attempt["result_status"] != result.status
                or attempt["status"] not in {result.status, "unresolved"}):
            raise ValueError("Chart attempt status does not match its native terminal outcome")
        if attempt["result_reason"] != (result.error or "")[:8192]:
            raise ValueError("Chart result reason does not match its original native outcome")
        readings = [
            dict(segment_id=segment, operation_id=result.operation_id,
                 observation_id=patch.observation.observation_id,
                 sample_id=patch.observation.sample_id)
            for patch in result.patches
        ]
        if _json(attempt["readings"]) != _json(readings):
            raise ValueError("Chart reading references do not match the canonical operation")


def _authenticate(manifest, blobs, *, validate_manifest, check_result, check_recovery,
                  qualify_root=False, root_payload=None):
    if not isinstance(blobs, Mapping):
        raise ValueError("Chart source blobs must be keyed by exact SHA-256")
    chart = _chart(manifest)
    root_segment = chart["segment_id"]
    segments, source_hashes, operations, recoveries, retained = {}, {}, {}, {}, {}
    visited, active = set(), {root_segment}
    if qualify_root:
        segments[root_segment] = manifest
        digest = _digest(root_payload)
        source_hashes[root_segment] = digest
        _blob(blobs, digest, retained)

    def visit(source):
        _source(source)
        segment, digest = source["segment_id"], source["sha256"]
        if segment in active:
            raise ValueError("Chart source graph has a cycle")
        if segment in source_hashes and source_hashes[segment] != digest:
            raise ValueError("One chart segment names different original manifests")
        if segment in visited:
            return
        if len(segments) >= MAX_SOURCE_SEGMENTS:
            raise ValueError("Chart continuation exceeds 64 source segments")
        payload = _blob(blobs, digest, retained)
        original = validate_manifest(payload)
        original_chart = _chart(original)
        if original_chart["segment_id"] != segment:
            raise ValueError("Chart source metadata names another original segment")
        if (original_chart["run_id"] != chart["run_id"]
                or _json(original_chart["definition"]) != _json(chart["definition"])):
            raise ValueError("Chart source belongs to another chart run or definition")
        segments[segment], source_hashes[segment] = original, digest
        active.add(segment)
        for ancestor in original_chart["sources"]:
            visit(ancestor)
        _outcomes(original, blobs, retained, operations, recoveries, qualify=True,
                  check_result=check_result, check_recovery=check_recovery)
        _local_attempts(original, operations, recoveries, qualify=True)
        active.remove(segment)
        visited.add(segment)

    for source in chart["sources"]:
        visit(source)
    _outcomes(manifest, blobs, retained, operations, recoveries, qualify=qualify_root,
              check_result=check_result, check_recovery=check_recovery)
    _local_attempts(manifest, operations, recoveries, qualify=qualify_root)
    for current in [manifest, *segments.values()]:
        current_chart = _chart(current)
        current_attempts = _attempts(current_chart)
        current_keys = {(item["segment_id"], item["id"]) for _, item in current_attempts}
        for source in current_chart["sources"]:
            for _, original_attempt in _attempts(_chart(segments[source["segment_id"]])):
                if (original_attempt["segment_id"] == source["segment_id"]
                        and (source["segment_id"], original_attempt["id"]) not in current_keys):
                    raise ValueError("Inherited chart history omits an original retained attempt")
        for pass_id, attempt in current_attempts:
            segment = attempt["segment_id"]
            if segment == current["batch_id"]:
                continue
            original = segments.get(segment)
            if original is None:
                raise ValueError("Inherited chart attempt has no exact original segment")
            matches = [
                (original_pass, original_attempt)
                for original_pass, original_attempt in _attempts(_chart(original))
                if original_attempt["id"] == attempt["id"]
                and original_attempt["segment_id"] == segment
            ]
            if (len(matches) != 1 or matches[0][0] != pass_id
                    or _json(matches[0][1]) != _json(attempt)):
                raise ValueError("Inherited chart attempt differs from its exact original")
    return dict(segments=segments, operations=operations, recoveries=recoveries, blobs=retained)


def authenticate(manifest, blobs_by_sha, *, validate_manifest, check_result, check_recovery):
    """Authenticate a current manifest and all reachable, stopped original sources.

    ``validate_manifest(bytes)`` returns the application's validated manifest.
    ``check_result(result, entry, descriptor)`` validates its canonical context.
    ``check_recovery(bytes, entry)`` returns validated raw terminal evidence.
    The caller owns chart/scientific validation at those canonical seams.
    """
    validated = validate_manifest(_json(manifest))
    return _authenticate(validated, blobs_by_sha, validate_manifest=validate_manifest,
                         check_result=check_result, check_recovery=check_recovery)


def collect_source(review, manifest_bytes, operation_bytes_by_path, inherited_blobs_by_sha, *,
                   validate_manifest, check_result, check_recovery):
    """Collect an authenticated original and its portable closure without file I/O.

    Exact ordinary operation/recovery paths select bytes supplied by the caller;
    the returned source graph resolves hashes only. Missing or unconfirmed work
    refuses continuation without dropping failed/cancelled or raw outcomes.
    """
    if (not isinstance(manifest_bytes, bytes) or not manifest_bytes
            or len(manifest_bytes) > MAX_BLOB_BYTES):
        raise ValueError("Chart source manifest bytes exceed 32 MiB or are missing")
    manifest = validate_manifest(manifest_bytes)
    digest = _digest(manifest_bytes)
    if (not isinstance(review, dict) or review.get("manifest_sha256") != digest
            or _json(review.get("manifest")) != _json(manifest)):
        raise ValueError("Chart source review does not match the exact retained manifest")
    folder = review.get("folder")
    if not isinstance(folder, str) or not folder:
        raise ValueError("Chart source review needs its original provenance label")
    if (not isinstance(operation_bytes_by_path, Mapping)
            or not isinstance(inherited_blobs_by_sha, Mapping)):
        raise ValueError("Chart source byte inputs must be mappings")
    owned = {}
    for entry in manifest["operations"]:
        reference = entry if entry["state"] == "accepted" else entry["recovery"]
        if reference is not None and reference["sha256"] is not None:
            payload = operation_bytes_by_path.get(reference["path"])
            owned[reference["sha256"]] = payload
        capture = entry["native_diagnostics"]
        if capture is not None and capture["sha256"] is not None:
            owned[capture["sha256"]] = operation_bytes_by_path.get(capture["path"])
    owned[digest] = manifest_bytes
    blobs = ChainMap(owned, inherited_blobs_by_sha)
    result = _authenticate(manifest, blobs, validate_manifest=validate_manifest,
                           check_result=check_result, check_recovery=check_recovery,
                           qualify_root=True, root_payload=manifest_bytes)
    result["source"] = dict(source_id=f"{folder.rstrip('/')}/batch.json",
                            segment_id=manifest["batch_id"], sha256=digest)
    _source(result["source"])
    return result
