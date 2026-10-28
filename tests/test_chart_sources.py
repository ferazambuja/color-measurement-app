"""Portable chart source graphs through the current canonical schema-5 callbacks."""

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from uuid import UUID

import pytest

from color_measurement import application, chart_sources, chart_state, instrument
from color_measurement.demo import demo_frontends
from color_measurement.models import (
    XYZ,
    CalibrationContext,
    ComputedValues,
    DeviceIdentity,
    Observation,
    ReportedValues,
)
from color_measurement.operation_records import OperationResult, PatchResult, result_to_json


def uid(number):
    return str(UUID(int=number))


def payload(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2, sort_keys=True).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def context(phase="accepted"):
    return dict(phase=phase, material={}, provenance={"scope": "simulated fixture"},
                required_material=[])


def validate_manifest(raw):
    return application._manifest(raw)


def check_recovery(raw, entry):
    return application._check_recovery(raw, entry)


CALLBACKS = dict(validate_manifest=validate_manifest, check_result=application._check_result,
                 check_recovery=check_recovery)


@pytest.fixture(autouse=True)
def no_native_transport(monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("Source authentication attempted native transport")

    monkeypatch.setattr(instrument, "HelperSession", forbidden)
    yield
    assert calls == []


@pytest.fixture
def original(tmp_path):
    front = next(item for item in demo_frontends() if item._meter.persona == "i1pro3")
    descriptor = json.loads(front.describe_json())
    segment, operation, command, sample = uid(1), uid(2), uid(3), uid(4)
    draft = dict(id=sample, version=0, name="Synthetic patch", notes="", options={},
                 number_samples=False)
    request = application._batch_request(descriptor, "emissive_spot", draft)
    device = DeviceIdentity(**descriptor["device"])
    calibration = CalibrationContext("not_applicable", "Synthetic software fixture")
    observation = Observation(
        schema_version=1, observation_id="retained-reading-1", operation_id=operation,
        sample_id=sample, acquired_at="2026-10-13T12:00:00+00:00", provenance="simulated",
        device=device, requested_mode="emissive_spot", actual_mode="emissive_spot",
        configuration=(), calibration=calibration,
        backend_reported=ReportedValues(xyz=XYZ(
            (20, 25, 10), "cd/m2", "absolute", "unspecified", "unspecified",
        )),
        project_computed=ComputedValues(), raw_backend_output="original simulated native text",
        warnings=("SIMULATED: no instrument operated",),
    )
    result = OperationResult(
        schema_version=1, operation_id=operation, device_id=device.device_id, device=device,
        provenance="simulated", mode="emissive_spot", workflow="spot", status="completed",
        expected_patch_ids=(sample,), patches=(PatchResult(0, sample, observation),),
        request_json=json.dumps(request), configuration=(), calibration=calibration,
        error=None, software_stopped=True, instrument_state="not_applicable",
    )
    raw = result_to_json(result).encode()
    entry = dict(command_id=command, path=f"operation-{command}.json", context_id=uid(5),
                 request=request, draft=deepcopy(draft), operation_id=operation,
                 state="accepted", sha256=sha(raw), reason=None, recovery=None, repeat_of=None,
                 native_diagnostics=None)
    chart = chart_state.new_state(
        chart_state.definition("custom", name="Synthetic chart", rows=1, columns=1),
        context(), run_id=uid(6), segment_id=segment, pass_id=uid(7),
    )
    chart = chart_state.begin_attempt(chart, attempt_id=uid(8), command_id=command,
                                     operation_id=operation, request=request,
                                     intended_context=context("intended"))
    chart = chart_state.complete_attempt(
        chart, uid(8), status="completed", accepted_context=context(),
        reading_refs=[dict(segment_id=segment, operation_id=operation,
                           observation_id=observation.observation_id, sample_id=sample)],
    )
    attempt = chart["passes"][0]["attempts"][0]
    attempt.update(result_status="completed", result_reason="")
    manifest = dict(
        schema_version=5, batch_id=segment, batch_name="Synthetic chart", batch_notes="",
        revision=1, status="open", context_id=uid(5), descriptor=descriptor,
        mode="emissive_spot", draft=draft, operations=[entry],
        annotations=[dict(operation_id=operation, observation_id=observation.observation_id,
                          name="Synthetic patch", notes="retained native reading", version=0)],
        destinations=[str(tmp_path / "original-location")], destination_issues={}, shutdown=None,
        comparison=dict(version=0, reference=None, white=None, white_descriptor=None), chart=chart,
        native_diagnostics=dict(debug_level=0, limit_bytes=65536),
    )
    return manifest, {entry["path"]: raw}


def collect(manifest, records, ancestors=None):
    raw = payload(manifest)
    review = dict(folder=manifest["destinations"][0], manifest=deepcopy(manifest),
                  manifest_sha256=sha(raw))
    return chart_sources.collect_source(review, raw, records,
                                        {} if ancestors is None else ancestors, **CALLBACKS)


def continued(original, collected, segment=uid(20)):
    current = deepcopy(original)
    current.update(batch_id=segment, operations=[], annotations=[], status="open")
    current["chart"] = chart_state.continue_state(
        original["chart"], context(), segment_id=segment,
        source_id=collected["source"]["source_id"],
        source_sha256=collected["source"]["sha256"],
        source_bytes=collected["blobs"][collected["source"]["sha256"]],
    )
    return current


class DeclaredOnly(Mapping):
    """Lazy caller can serve declared keys without directory or mapping scans."""

    def __init__(self, values):
        self.values, self.reads = values, []

    def __getitem__(self, key):
        self.reads.append(key)
        return self.values[key]

    def __iter__(self):
        raise AssertionError("Source authentication scanned undeclared blobs")

    def __len__(self):
        raise AssertionError("Source authentication enumerated undeclared blobs")


def test_portable_continuation_preserves_exact_bytes_and_native_identity(original):
    manifest, records = original
    first = collect(manifest, records)
    current = continued(manifest, first)
    lazy = DeclaredOnly(dict(first["blobs"], unused=b"unrelated private bytes"))
    checked = chart_sources.authenticate(current, lazy, **CALLBACKS)
    key = (manifest["batch_id"], manifest["operations"][0]["operation_id"])
    result = checked["operations"][key]
    assert result.patches[0].observation.raw_backend_output == "original simulated native text"
    assert checked["blobs"] == first["blobs"]
    assert set(lazy.reads) == set(first["blobs"])
    second = collect(current, {}, lazy)
    assert len(second["blobs"]) == 3
    assert all("source_base64" not in item for item in current["chart"]["sources"])
    assert second["segments"][manifest["batch_id"]] == manifest


@pytest.mark.parametrize("destination", [
    "/Users/source/Measurements/batch",
    r"C:\Measurements\batch",
    r"\\server\measurements\batch",
])
def test_historical_destinations_are_portable_source_provenance(original, tmp_path, destination):
    manifest, records = original
    manifest["destinations"] = [destination]
    manifest["destination_issues"] = {destination: "Original destination is unavailable"}
    raw = payload(manifest)
    first = collect(manifest, records)
    assert first["blobs"][sha(raw)] == raw
    assert first["source"]["source_id"] == destination.rstrip("/") + "/batch.json"
    current = continued(manifest, first)
    current["destinations"] = [str(tmp_path / "current-destination")]
    current["destination_issues"] = {}
    checked = chart_sources.authenticate(current, first["blobs"], **CALLBACKS)
    assert checked["segments"][manifest["batch_id"]] == manifest
    assert checked["blobs"] == first["blobs"]
    assert current["chart"]["sources"][0] == first["source"]
    assert next(iter(checked["operations"].values())).patches[0].observation.raw_backend_output == \
        "original simulated native text"


@pytest.mark.parametrize("destination", [
    "relative/batch", r"C:batch", r"\batch", "", None,
])
def test_historical_destinations_still_require_absolute_syntax(original, destination):
    manifest, _ = original
    manifest["destinations"] = [destination]
    with pytest.raises(ValueError, match="Destinations must contain explicit absolute paths"):
        validate_manifest(payload(manifest))


@pytest.mark.parametrize("mutate", [
    lambda item: item.update(cell=2),
    lambda item: item["intended_context"]["provenance"].update(scope="forged"),
    lambda item: item["accepted_context"]["provenance"].update(scope="forged"),
    lambda item: item["request"].update(options={"sampling": "high"}),
    lambda item: item["readings"][0].update(observation_id="borrowed-reading"),
    lambda item: item.update(terminal_sha256="a" * 64),
    lambda item: item.update(result_status="failed"),
])
def test_inherited_attempt_must_equal_its_original(original, mutate):
    manifest, records = original
    first = collect(manifest, records)
    current = continued(manifest, first)
    mutate(current["chart"]["passes"][0]["attempts"][0])
    with pytest.raises(ValueError):
        chart_sources.authenticate(current, first["blobs"], **CALLBACKS)


@pytest.mark.parametrize("missing", ["manifest", "operation"])
def test_missing_originals_refuse_without_path_fallback(original, missing):
    manifest, records = original
    first = collect(manifest, records)
    current = continued(manifest, first)
    digest = (first["source"]["sha256"] if missing == "manifest"
              else sha(next(iter(records.values()))))
    del first["blobs"][digest]
    with pytest.raises(ValueError):
        chart_sources.authenticate(current, first["blobs"], **CALLBACKS)


def test_inherited_attempt_cannot_be_omitted_from_retained_history(original):
    manifest, records = original
    first = collect(manifest, records)
    current = continued(manifest, first)
    current["chart"]["passes"][0]["attempts"] = []
    with pytest.raises(ValueError):
        chart_sources.authenticate(current, first["blobs"], **CALLBACKS)


def test_changed_exact_bytes_refuse_even_if_json_value_is_unchanged(original):
    manifest, records = original
    first = collect(manifest, records)
    current = continued(manifest, first)
    first["blobs"][first["source"]["sha256"]] += b"\n"
    with pytest.raises(ValueError):
        chart_sources.authenticate(current, first["blobs"], **CALLBACKS)


@pytest.mark.parametrize("kind", ["run", "definition", "metadata-segment", "pass"])
def test_forged_chart_source_association_refuses(original, kind):
    manifest, records = original
    first = collect(manifest, records)
    current = continued(manifest, first)
    if kind == "run":
        current["chart"]["run_id"] = uid(40)
    elif kind == "definition":
        current["chart"]["definition"]["name"] = "Another chart"
    elif kind == "metadata-segment":
        current["chart"]["sources"][0]["segment_id"] = uid(41)
    else:
        current["chart"]["passes"][0]["id"] = uid(42)
    with pytest.raises(ValueError):
        chart_sources.authenticate(current, first["blobs"], **CALLBACKS)


@pytest.mark.parametrize("state", ["reserved", "started", "terminal_unknown"])
def test_unresolved_original_operation_cannot_be_collected(original, state):
    manifest, records = original
    entry = manifest["operations"][0]
    entry.update(state=state, sha256=None,
                 reason="Original raw result missing" if state == "terminal_unknown" else None)
    manifest["annotations"] = []
    with pytest.raises(ValueError):
        collect(manifest, records)


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_stopped_failed_and_cancelled_results_remain_exact(original, status):
    manifest, records = original
    from color_measurement.operation_records import result_from_json

    result = result_from_json(next(iter(records.values())).decode())
    changed = replace(result, status=status, error="Original native failure", patches=())
    raw = result_to_json(changed).encode()
    entry = manifest["operations"][0]
    entry["sha256"] = sha(raw)
    manifest["annotations"] = []
    attempt = manifest["chart"]["passes"][0]["attempts"][0]
    attempt.update(status=status, result_status=status, result_reason="Original native failure",
                   reason="Original native failure", readings=[])
    attempt["terminal_sha256"] = chart_state._digest(chart_state._terminal_facts(attempt))
    manifest["chart"]["passes"][0]["selected_attempts"] = [None]
    found = collect(manifest, {entry["path"]: raw})
    assert found["blobs"][sha(raw)] == raw
    assert found["operations"][(manifest["batch_id"], result.operation_id)].status == status


def test_unconfirmed_native_software_stop_cannot_be_collected(original):
    manifest, records = original
    from color_measurement.operation_records import result_from_json

    result = result_from_json(next(iter(records.values())).decode())
    raw = result_to_json(replace(result, software_stopped=False)).encode()
    manifest["operations"][0]["sha256"] = sha(raw)
    with pytest.raises(ValueError):
        collect(manifest, {manifest["operations"][0]["path"]: raw})


def recovery_source(original, stopped=True):
    manifest, _ = deepcopy(original)
    entry = manifest["operations"][0]
    entry.update(state="terminal_unknown", sha256=None, reason="Original parse failure")
    raw = payload(dict(schema_version=1, command_id=entry["command_id"],
                       operation_id=entry["operation_id"], request=entry["request"],
                       result_json='{"original":"broken', reason="Original parse failure",
                       software_stopped=stopped, instrument_state="unknown"))
    entry["recovery"] = dict(path=f"recovery-{entry['command_id']}.json", sha256=sha(raw))
    manifest["annotations"] = []
    attempt = manifest["chart"]["passes"][0]["attempts"][0]
    attempt.update(status="unresolved", result_status="unresolved", readings=[],
                   result_reason="Original parse failure", reason="Original parse failure",
                   accepted_context=None)
    rehash_terminal(attempt)
    manifest["chart"]["passes"][0]["selected_attempts"] = [None]
    return manifest, {entry["recovery"]["path"]: raw}


def test_raw_stopped_failure_remains_exact_and_does_not_create_a_reading(original):
    manifest, records = recovery_source(original)
    checked = collect(manifest, records)
    key = (manifest["batch_id"], manifest["operations"][0]["operation_id"])
    assert checked["operations"] == {}
    assert checked["recoveries"][key]["result_json"] == '{"original":"broken'
    assert next(iter(records.values())) in checked["blobs"].values()


def test_unconfirmed_raw_failure_stop_cannot_be_collected(original):
    manifest, records = recovery_source(original, stopped=None)
    with pytest.raises(ValueError):
        collect(manifest, records)


@pytest.mark.parametrize("changed", ["review", "annotations", "request", "readings"])
def test_source_local_canonical_mismatch_refuses(original, changed):
    manifest, records = original
    if changed == "review":
        raw = payload(manifest)
        with pytest.raises(ValueError):
            chart_sources.collect_source(dict(manifest=manifest, manifest_sha256="a" * 64),
                                         raw, records, {}, **CALLBACKS)
        return
    if changed == "annotations":
        manifest["annotations"][0]["observation_id"] = "forged"
    elif changed == "request":
        manifest["chart"]["passes"][0]["attempts"][0]["request"]["layout"] = {"forged": True}
    else:
        manifest["chart"]["passes"][0]["attempts"][0]["readings"][0]["sample_id"] = "forged"
    with pytest.raises(ValueError):
        collect(manifest, records)


def test_64_segment_portable_chain_is_flat_and_65_refuses(original):
    manifest, records = original
    first = collect(manifest, records)
    current, checked = manifest, first
    for number in range(2, 65):
        current = continued(current, checked, segment=uid(100 + number))
        checked = collect(current, {}, checked["blobs"])
        assert len(checked["segments"]) == number
        assert len(checked["blobs"]) == number + 1
    assert len(checked["blobs"]) == 65
    last = continued(current, checked, segment=uid(200))
    with pytest.raises(ValueError):
        collect(last, {}, checked["blobs"])


def test_same_segment_different_original_manifests_refuses(original):
    manifest, records = original
    first = collect(manifest, records)
    other = deepcopy(manifest)
    other["batch_notes"] = "A different manifest revision"
    other_raw = payload(other)
    current = continued(manifest, first)
    second = collect(current, {}, first["blobs"])
    intermediary = continued(current, second, segment=uid(21))
    intermediary["chart"]["sources"][0]["sha256"] = sha(other_raw)
    intermediary_raw = payload(intermediary)
    current["chart"]["sources"].append(dict(source_id="another original",
                                           segment_id=uid(21), sha256=sha(intermediary_raw)))
    blobs = dict(second["blobs"], **{sha(other_raw): other_raw,
                                   sha(intermediary_raw): intermediary_raw})
    with pytest.raises(ValueError):
        chart_sources.authenticate(current, blobs, **CALLBACKS)


def test_cycle_and_duplicate_source_metadata_refuse(original):
    manifest, records = original
    first = collect(manifest, records)
    current = continued(manifest, first)
    current["chart"]["sources"].append(deepcopy(first["source"]))
    with pytest.raises(ValueError):
        chart_sources.authenticate(current, first["blobs"], **CALLBACKS)
    current["chart"]["sources"] = [dict(first["source"], segment_id=current["batch_id"])]
    with pytest.raises(ValueError):
        chart_sources.authenticate(current, first["blobs"], **CALLBACKS)


def test_oversized_declared_blob_and_nested_source_bytes_refuse(original):
    manifest, records = original
    first = collect(manifest, records)
    current = continued(manifest, first)
    oversized = b"x" * (chart_sources.MAX_BLOB_BYTES + 1)
    current["chart"]["sources"][0]["sha256"] = sha(oversized)
    with pytest.raises(ValueError):
        chart_sources.authenticate(current, {sha(oversized): oversized}, **CALLBACKS)
    current["chart"]["sources"][0]["source_base64"] = "forbidden recursive source"
    with pytest.raises(ValueError):
        chart_sources.authenticate(current, first["blobs"], **CALLBACKS)


def rehash_terminal(attempt):
    # This is the chart owner's current seven-field retained-outcome contract.
    fields = ("result_status", "result_reason", "status", "reason", "readings",
              "accepted_context", "position_check")
    canonical = json.dumps({key: attempt[key] for key in fields}, sort_keys=True,
                           ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    attempt["terminal_sha256"] = sha(canonical.encode("ascii"))


@pytest.mark.parametrize("kind", ["result", "recovery"])
def test_rehashed_terminal_reason_tamper_cannot_replace_original_native_text(original, kind):
    manifest, records = original if kind == "result" else recovery_source(original)
    original_raw = next(iter(records.values()))
    attempt = manifest["chart"]["passes"][0]["attempts"][0]
    original_digest = attempt["terminal_sha256"]
    attempt.update(result_reason="Forged native reason", reason="Forged native reason")
    rehash_terminal(attempt)
    assert attempt["terminal_sha256"] != original_digest
    with pytest.raises(ValueError):
        collect(manifest, records)
    assert next(iter(records.values())) == original_raw


@pytest.mark.parametrize("kind", ["result", "recovery"])
def test_bounded_reason_preview_keeps_full_original_stopped_failure(original, kind):
    manifest, records = original if kind == "result" else recovery_source(original)
    reason = "Original failure " + "x" * 8192 + "\nExact original suffix"
    entry = manifest["operations"][0]
    if kind == "result":
        from color_measurement.operation_records import result_from_json

        result = result_from_json(next(iter(records.values())).decode())
        raw = result_to_json(replace(result, status="failed", error=reason, patches=())).encode()
        entry["sha256"] = sha(raw)
        path, status = entry["path"], "failed"
    else:
        failure = application._load(next(iter(records.values())))
        failure["reason"] = reason
        entry["reason"] = reason
        raw = payload(failure)
        entry["recovery"]["sha256"] = sha(raw)
        path, status = entry["recovery"]["path"], "unresolved"
    manifest["annotations"] = []
    attempt = manifest["chart"]["passes"][0]["attempts"][0]
    attempt.update(status=status, result_status=status, readings=[], result_reason=reason[:8192],
                   reason=reason[:8192])
    rehash_terminal(attempt)
    manifest["chart"]["passes"][0]["selected_attempts"] = [None]
    checked = collect(manifest, {path: raw})
    key = (manifest["batch_id"], entry["operation_id"])
    retained_reason = (checked["operations"][key].error if kind == "result"
                       else checked["recoveries"][key]["reason"])
    assert retained_reason == reason and checked["blobs"][sha(raw)] == raw
    assert len(attempt["result_reason"]) == 8192


def diagnostic_source(original, data=b"original stderr\n", *, state="complete", recovery=False):
    manifest, records = (recovery_source(original) if recovery else deepcopy(original))
    entry = manifest["operations"][0]
    terminal = state in {"complete", "incomplete"}
    written = len(data) if state == "complete" else len(data) // 2
    capture = dict(
        schema_version=2, kind="native_stderr", state=state,
        path=f"diagnostic-{entry['command_id']}.log", debug_level=2, limit_bytes=1024,
        received_bytes=len(data), retained_bytes=len(data), written_bytes=written,
        truncated=False, sha256=sha(data) if terminal else None,
        file_sha256=sha(data[:written]) if terminal else None,
        drain_complete=state == "complete", file_complete=state == "complete",
        worker_started=state not in {"prepared", "not_started"},
        sink_stopped=True,
        retention="head_tail", segments=[dict(offset=0, length=len(data))] if data else [],
        error="Original capture sink failure" if state == "incomplete" else None,
    )
    entry["native_diagnostics"] = capture
    if terminal:
        records[capture["path"]] = data
    return manifest, records


def test_diagnostic_closure_follows_only_declared_paths_and_hashes(original):
    manifest, records = diagnostic_source(original)
    lazy_paths = DeclaredOnly(dict(records, unrelated=b"never inspected"))
    collected = collect(manifest, lazy_paths)
    capture = manifest["operations"][0]["native_diagnostics"]
    assert set(lazy_paths.reads) == set(records)
    assert collected["blobs"][capture["sha256"]] == records[capture["path"]]
    inherited = DeclaredOnly(dict(collected["blobs"], unrelated=b"never inspected"))
    checked = chart_sources.authenticate(continued(manifest, collected), inherited, **CALLBACKS)
    assert set(inherited.reads) == set(collected["blobs"])
    saved_entry = checked["segments"][manifest["batch_id"]]["operations"][0]
    assert saved_entry["native_diagnostics"] == capture


@pytest.mark.parametrize("change", ["missing", "tamper", "length", "file-prefix"])
def test_diagnostic_original_bytes_and_written_prefix_must_authenticate(original, change):
    manifest, records = diagnostic_source(
        original, state="incomplete" if change in {"length", "file-prefix"} else "complete",
    )
    capture = manifest["operations"][0]["native_diagnostics"]
    if change == "missing":
        del records[capture["path"]]
    elif change == "tamper":
        records[capture["path"]] += b"forged"
    elif change == "length":
        capture.update(received_bytes=2, retained_bytes=2, written_bytes=2)
        capture["segments"] = [dict(offset=0, length=2)]
        capture["file_sha256"] = sha(records[capture["path"]][:2])
    else:
        capture["file_sha256"] = sha(b"forged initial sink")
    with pytest.raises(ValueError):
        collect(manifest, records)


@pytest.mark.parametrize("state", ["complete", "incomplete"])
def test_exact_empty_terminal_diagnostic_is_preserved_without_worker_inference(original, state):
    manifest, records = diagnostic_source(original, b"", state=state)
    capture = manifest["operations"][0]["native_diagnostics"]
    checked = collect(manifest, records)
    assert checked["blobs"][sha(b"")] == b""
    saved_entry = checked["segments"][manifest["batch_id"]]["operations"][0]
    assert saved_entry["native_diagnostics"] == capture


@pytest.mark.parametrize("fault", ["bool-offset", "wrong-offset", "wrong-retention", "old-schema"])
def test_diagnostic_source_segment_declarations_are_strict(original, fault):
    manifest, records = diagnostic_source(original)
    capture = manifest["operations"][0]["native_diagnostics"]
    if fault == "bool-offset":
        capture["segments"][0]["offset"] = False
    elif fault == "wrong-offset":
        capture["segments"][0]["offset"] = 1
    elif fault == "wrong-retention":
        capture["retention"] = "prefix"
    else:
        capture["schema_version"] = 1
    with pytest.raises(ValueError):
        collect(manifest, records)
    assert capture["worker_started"] is True
    entry = manifest["operations"][0]
    entry["sha256"], records[entry["path"]] = sha(b""), b""
    with pytest.raises(ValueError):
        collect(manifest, records)


def test_incomplete_diagnostic_keeps_full_original_and_initial_write_prefix_facts(original):
    manifest, records = diagnostic_source(original, state="incomplete", recovery=True)
    capture = deepcopy(manifest["operations"][0]["native_diagnostics"])
    checked = collect(manifest, records)
    entry = manifest["operations"][0]
    assert checked["operations"] == {}
    assert checked["recoveries"][(manifest["batch_id"], entry["operation_id"])]["software_stopped"]
    assert checked["blobs"][capture["sha256"]] == records[capture["path"]]
    assert capture["file_sha256"] != capture["sha256"]
    assert capture["file_sha256"] not in checked["blobs"]
    saved_entry = checked["segments"][manifest["batch_id"]]["operations"][0]
    assert saved_entry["native_diagnostics"] == capture
    assert capture["state"] == "incomplete" and capture["file_complete"] is False


@pytest.mark.parametrize("state", ["prepared", "capturing", "complete", "incomplete"])
def test_unsaved_diagnostic_capture_cannot_qualify_continuation(original, state):
    manifest, records = diagnostic_source(original, b"", state=state)
    capture = manifest["operations"][0]["native_diagnostics"]
    capture.update(sha256=None, file_sha256=None, file_complete=False, written_bytes=0)
    with pytest.raises(ValueError):
        collect(manifest, records)


def test_explicit_not_started_diagnostic_absence_preserves_worker_provenance(original):
    manifest, records = diagnostic_source(original, b"", state="not_started")
    checked = collect(manifest, records)
    capture = manifest["operations"][0]["native_diagnostics"]
    assert capture["sha256"] is None and capture["file_sha256"] is None
    assert capture["worker_started"] is False and capture["file_complete"] is False
    assert len(checked["blobs"]) == 2
    saved_entry = checked["segments"][manifest["batch_id"]]["operations"][0]
    assert saved_entry["native_diagnostics"] == capture


def test_unclosed_nonempty_diagnostic_metadata_is_factual_but_cannot_qualify_source(original):
    manifest, records = diagnostic_source(original, state="capturing")
    capture = manifest["operations"][0]["native_diagnostics"]
    capture.update(segments=[], written_bytes=0, file_sha256=None,
                   sink_stopped=False, file_complete=False)
    validated = application._manifest(payload(manifest))
    assert validated["operations"][0]["native_diagnostics"] == capture
    with pytest.raises(ValueError, match="unsaved native diagnostic"):
        collect(manifest, records)
