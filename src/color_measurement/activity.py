"""Read-only activity composition over the selected source's existing facts."""

import base64
import hashlib
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from .application import _check_recovery, _diagnostic_bytes, _diagnostic_review, _manifest
from .operation_records import OperationResult
from .reading_dataset import reading_key


def _digest(value):
    from .dataset_binding import content_digest

    return content_digest(value)


def _batch_source(kind, manifest, digest=None, source_id=None):
    return dict(kind=kind, batch_id=manifest["batch_id"],
                context_id=manifest["context_id"], name=manifest["batch_name"],
                mode=manifest["mode"], descriptor=deepcopy(manifest["descriptor"]),
                source_manifest_sha256=digest, source_id=source_id)


def _operation(source, entry, result, recovery, *, saved, read_only, review_state=None,
               review_reason=None, retained=True, events=(), diagnostics=None):
    reference = {key: source[key] for key in
                 ("kind", "batch_id", "context_id", "source_manifest_sha256", "source_id")}
    qualified = source["kind"] == "inherited"
    typed = isinstance(result, OperationResult)
    # A record without a patch list has an unknown reading count, never zero.
    patches = result.patches if typed else (result or {}).get("patches")
    references = [dict(operation_id=entry["operation_id"],
                       observation_id=patch.observation.observation_id if typed else
                       patch["observation"]["observation_id"], **(
                           dict(segment_id=source["batch_id"],
                                source_manifest_sha256=source["source_manifest_sha256"])
                           if qualified else {}))
                  for patch in patches or []] if retained else []
    summary = dict(
        key=_digest([reference, "operation", entry["command_id"], entry["operation_id"]]),
        kind="operation", source_ref=reference, command_id=entry["command_id"],
        operation_id=entry["operation_id"], workflow=entry["request"]["workflow"],
        status=result.status if typed else result["status"] if result else "unknown",
        state=entry["state"],
        reason=entry["reason"] or (result.error if typed else (result or {}).get("error")),
        review_state=review_state, review_reason=review_reason,
        record_retained=retained and result is not None, saved=saved, read_only=read_only,
        reading_count=len(patches) if patches is not None else None, reading_refs=references,
        name=entry["draft"]["name"], notes_preview=entry["draft"]["notes"][:256],
        provenance=result.provenance if typed else (result or {}).get("provenance"),
    )
    return dict(summary=summary, source=source, entry=dict(entry), result=result,
                recovery_result=recovery, events=list(events), diagnostics=diagnostics)


def _inherited(binding, chart):
    """Use only previously admitted/captured content-addressed original sources."""
    if not chart:
        return []
    records = []
    for selected in chart["sources"]:
        digest = selected["sha256"]
        blob = (binding.owner._source_blobs[digest] if binding.owner else
                binding._payloads[f"source-{digest}.json"])
        cache = binding.__dict__.setdefault("_activity_source_manifests", {})
        captured = cache.get(digest)
        if captured is None or captured[0] is not blob:
            if hashlib.sha256(blob).hexdigest() != digest:
                raise ValueError("Inherited activity manifest differs from its captured identity")
            captured = cache[digest] = (blob, _manifest(blob))
        manifest = captured[1]
        if manifest["batch_id"] != selected["segment_id"]:
            raise ValueError("Inherited activity source identifies another segment")
        source = _batch_source("inherited", manifest, digest, selected["source_id"])
        for entry in manifest["operations"]:
            result, recovery = None, None
            if entry["state"] == "accepted":
                if binding.owner:
                    result = binding.owner._source_results[
                        manifest["batch_id"], entry["operation_id"]]
                else:
                    result = next(item["result"] for item in binding._review["inherited_operations"]
                                  if item["segment_id"] == manifest["batch_id"]
                                  and item["source_manifest_sha256"] == digest
                                  and item["operation_id"] == entry["operation_id"])
            metadata = entry["native_diagnostics"]
            payload = None
            if metadata and metadata["sha256"] is not None:
                payload = (binding.owner._source_blobs[metadata["sha256"]] if binding.owner else
                           binding._payloads[f"source-{metadata['sha256']}.json"])
            record = _operation(
                source, entry, result, recovery, saved=True, read_only=True,
                review_state=entry["state"], retained=result is not None,
                diagnostics=dict(metadata=metadata, payload=payload, retention_reason=None),
            )
            original = entry["recovery"]
            if original is not None:
                record["recovery_payload"] = (
                    binding.owner._source_blobs[original["sha256"]] if binding.owner else
                    binding._payloads[f"source-{original['sha256']}.json"])
            records.append(record)
    return records


def _collect(binding):
    """Copy one coherent roster and its retained detail witnesses without publication."""
    with binding.owner._mutex if binding.owner else nullcontext():
        if binding.kind == "empty":
            return None, [], None
        if binding.kind == "standalone":
            record = binding._standalone.record
            source = binding._standalone.metadata()
            reference = {key: source[key] for key in ("kind", "format", "source_sha256")}
            operation = isinstance(record, OperationResult)
            references = [dict(operation_id=item.operation_id, observation_id=item.observation_id)
                          for item in binding._standalone.observations]
            summary = dict(
                key=_digest([reference, record.operation_id,
                             None if operation else record.observation_id]),
                kind="operation" if operation else "observation", source_ref=reference,
                command_id=None, operation_id=record.operation_id,
                workflow=record.workflow if operation else None,
                status=record.status if operation else None, state="retained",
                reason=record.error if operation else None, review_state=None, review_reason=None,
                record_retained=True, saved=True, read_only=True,
                reading_count=len(references), reading_refs=references,
                name=binding._standalone.path.name if operation else record.sample_id,
                notes_preview="", provenance=record.provenance,
            )
            return source, [dict(summary=summary, source=source, entry=None,
                                 result=record if operation else None,
                                 recovery_result=None, events=[], diagnostics=None)], None
        if binding.owner:
            state = binding.owner.snapshot()
            source = _batch_source("live", state)
            source["folder"] = state["folder"]
            saved = {item["command_id"]: item["saved"] for item in state["operations"]}
            records = _inherited(binding, binding.owner._data["chart"])
            for entry in binding.owner._data["operations"]:
                identity = entry["command_id"]
                result = binding.owner._record_views.get(identity)
                records.append(_operation(
                    source, entry, result, binding.owner._unknown.get(identity),
                    saved=saved[identity], read_only=binding.owner._closed
                    or state["status"] != "open", retained=result is not None,
                    events=[event for event in state["events"]
                            if event.get("operation_id") == entry["operation_id"]],
                    diagnostics=dict(metadata=deepcopy(entry["native_diagnostics"]),
                                     payload=binding.owner._diagnostic_payloads.get(identity),
                                     retention_reason=binding.owner._diagnostic_reasons.get(identity)),
                ))
            # Owner revisions cover exact applied/draft metadata, terminal/save
            # state and events. Immutable originals retain their declared hashes.
            return source, records, dict(revision=state["revision"], events=state["events"])
        manifest = binding._review["manifest"]
        source = _batch_source("saved", manifest, binding._review["manifest_sha256"])
        source["folder"] = None if binding._archive_path else str(binding._folder)
        source["inherited_reason"] = (binding._review["chart"] or {}).get("reason")
        if binding._archive_path:
            source.update(path=str(binding._archive_path), format="mat",
                          archive_sha256=hashlib.sha256(binding._archive_bytes).hexdigest())
        chart = manifest["chart"] if (binding._review["chart"] or {}).get("available") else None
        records = _inherited(binding, chart)
        for entry in binding._review["operations"]:
            metadata = entry["native_diagnostics"]
            records.append(_operation(
                source, entry, entry["result"], entry.get("recovery_result"),
                saved=entry["state"] == "accepted" and entry["review_state"] == "accepted"
                or entry["review_state"] == "terminal_unknown",
                read_only=True, review_state=entry["review_state"],
                review_reason=entry["review_reason"], retained=entry["review_state"] == "accepted",
                diagnostics=dict(metadata=metadata,
                                 payload=binding._payloads.get(metadata["path"])
                                 if metadata else None,
                                 retention_reason=None, review=entry["native_diagnostic_review"]),
            ))
        return source, records, None


def _revision(source, records, witness):
    facts = []
    for record in records:
        diagnostic = record["diagnostics"]
        entry = record["entry"] or {}
        facts.append(dict(
            summary=record["summary"], operation_sha256=entry.get("sha256"),
            recovery=entry.get("recovery"),
            diagnostics=dict(metadata=diagnostic["metadata"],
                             original_available=diagnostic["payload"] is not None,
                             retention_reason=diagnostic["retention_reason"])
            if diagnostic else None,
        ))
    return _digest([source, facts, witness])


def activity(binding):
    """Return compact source-bound outcomes, including operations with no readings."""
    reason = None
    try:
        binding.assert_current()
    except (OSError, ValueError, UnicodeError) as error:
        reason = str(error)
    source, records, witness = _collect(binding)
    revision = _revision(source, records, witness)
    entries = [deepcopy(record["summary"]) for record in records]
    if reason:
        for entry in entries:
            entry["reading_refs"] = []
    return dict(activity_revision=revision, source=deepcopy(source), entries=entries, reason=reason)


def _diagnostic(record, binding):
    diagnostic = record["diagnostics"]
    if diagnostic is None:
        return dict(metadata=None, review=None, retention_reason=None, segments=[], original=None,
                    reason="Standalone records carry no separate application diagnostic transcript")
    metadata, payload = diagnostic["metadata"], diagnostic["payload"]
    review = diagnostic.get("review")
    if review is None:
        if binding.owner and record["source"]["kind"] == "live":
            detail = binding.owner.operation_details(record["summary"]["command_id"])
            review = detail["native_diagnostic_review"]
        else:
            def read(_name):
                if payload is None:
                    raise FileNotFoundError("Native diagnostic original is unavailable")
                return payload

            review = _diagnostic_review(Path("."), metadata, read)
    original, reason, segments = None, None, []
    if metadata is None:
        reason = "Native diagnostic capture was not enabled for this operation"
    elif payload is None:
        reason = review["reason"] or "Native diagnostic capture has no retained closed original"
    else:
        try:
            _diagnostic_bytes(payload, metadata)
            original = dict(encoding="base64", data=base64.b64encode(payload).decode("ascii"),
                            sha256=metadata["sha256"])
            cursor = 0
            for segment in metadata["segments"]:
                length = segment["length"]
                segments.append(dict(source_offset=segment["offset"], length=length,
                                     base64=base64.b64encode(
                                         payload[cursor:cursor + length]).decode("ascii")))
                cursor += length
        except ValueError as error:
            reason = str(error)
    return deepcopy(dict(metadata=metadata, review=review,
                         retention_reason=diagnostic["retention_reason"],
                         segments=segments,
                         original=original, reason=reason))


def activity_details(binding, key, activity_revision):
    """Inspect exactly one current roster entry without inventing missing envelopes."""
    binding.assert_current()
    source, records, witness = _collect(binding)
    revision = _revision(source, records, witness)
    if not isinstance(activity_revision, str) or activity_revision != revision:
        raise ValueError("Activity changed; refresh the current roster")
    record = next((item for item in records if item["summary"]["key"] == key), None)
    if record is None:
        raise ValueError("Unknown activity entry in this selected source")
    if binding.owner and record["source"]["kind"] == "live":
        retained = binding.owner.operation_details(record["summary"]["command_id"])
        record["entry"] = {name: retained[name] for name in record["entry"]}
        record["result"] = retained["result"]
        record["recovery_result"] = retained["recovery_result"]
        record["diagnostics"]["review"] = retained["native_diagnostic_review"]
    if record.get("recovery_payload") is not None:
        original = record["entry"]["recovery"]
        raw = record["recovery_payload"]
        if hashlib.sha256(raw).hexdigest() != original["sha256"]:
            raise ValueError("Inherited activity recovery differs from its captured identity")
        record["recovery_result"] = _check_recovery(raw, record["entry"])
    if isinstance(record["result"], OperationResult):
        record["result"] = asdict(record["result"])
    readings, data = [], binding.data()
    for reference in record["summary"]["reading_refs"]:
        identity = reading_key(reference)
        if (identity not in data["readings"]
                or data["readings"][identity]["reading_ref"] != reference):
            raise ValueError("Activity reading is unavailable in this selected source")
        row = data["readings"][identity]
        readings.append(dict(key=identity, reading_ref=deepcopy(reference),
                             annotation=binding.annotation_reference(reference),
                             observation=deepcopy(row["observation"]),
                             coordinate_reasons=deepcopy(row["coordinate_reasons"])))
    diagnostics = _diagnostic(record, binding)
    binding.assert_current()
    after = activity(binding)
    if after["reason"] or after["activity_revision"] != revision:
        raise ValueError("Activity changed during inspection; refresh the current roster")
    events_scope = ("recent-32-live-batch-events; scoped-by-operation" if
                    record["source"]["kind"] == "live"
                    else "native-records-only; application events not persisted")
    return deepcopy(dict(
        activity_revision=revision, key=key, summary=record["summary"], source=record["source"],
        entry=record["entry"], result=record["result"], recovery_result=record["recovery_result"],
        events=record["events"], events_scope=events_scope,
        readings=readings, native_diagnostics=diagnostics,
    ))
