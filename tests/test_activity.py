"""Selected-source activity, exact retained details and refusal without equipment."""

import base64
import hashlib
from copy import deepcopy
from dataclasses import replace

import pytest
from test_application import (
    _acquire,
    _automatic_companion_state,
    _command,
    _complete,
    _native_diagnostic_seam,
    _wait,
)
from test_application import app as _app_fixture
from test_batch_archive import continued_chart as _continued_chart_fixture
from test_chart_sources import (
    collect,
    continued,
    diagnostic_source,
    payload,
    recovery_source,
)
from test_chart_sources import (
    original as _original_fixture,
)
from test_operation_exports import result as _result_fixture

from color_measurement import activity, application, exports, instrument
from color_measurement.dataset_binding import DatasetBinding
from color_measurement.operation_records import result_to_json

app = _app_fixture
continued_chart = _continued_chart_fixture
original = _original_fixture
result = _result_fixture


@pytest.fixture(autouse=True)
def no_native(monkeypatch):
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append(args)
        raise RuntimeError("Activity attempted native transport")

    monkeypatch.setattr(instrument, "HelperSession", forbidden)
    monkeypatch.setattr(application, "_UNCERTAIN_OWNERS", [])
    yield
    assert not attempts


def stored(tmp_path, manifest, files):
    folder = tmp_path / "saved"
    folder.mkdir()
    (folder / "batch.json").write_bytes(payload(manifest))
    for name, raw in files.items():
        (folder / name).write_bytes(raw)
    return DatasetBinding(folder=folder), folder


def inspect(binding, roster=None, index=0):
    roster = roster or binding.activity()
    return binding.activity_details(roster["entries"][index]["key"], roster["activity_revision"])


def test_empty_binding_has_no_invented_activity():
    binding = DatasetBinding()
    roster = binding.activity()
    assert roster["source"] is None and roster["entries"] == [] and roster["reason"] is None
    with pytest.raises(ValueError, match="Unknown activity"):
        binding.activity_details("unknown", roster["activity_revision"])


@pytest.mark.parametrize("status,patches", [("failed", 0), ("cancelled", 0), ("failed", 1)])
def test_standalone_operations_retain_native_outcomes_without_commands(tmp_path, result,
                                                                    status, patches):
    retained = replace(result, status=status, error="Original native refusal",
                       patches=result.patches[:patches], error_stage="read_sample",
                       error_native_code=0x110001)
    path = tmp_path / "failed.json"
    path.write_text(result_to_json(retained))
    binding = DatasetBinding(source_file=path, format="operation_json")
    roster = binding.activity()
    entry, = roster["entries"]
    assert entry["command_id"] is None and entry["status"] == status
    assert entry["reading_count"] == patches and entry["record_retained"]
    assert not {"batch_id", "context_id", "descriptor"} & roster["source"].keys()
    detail = inspect(binding, roster)
    assert detail["entry"] is None and detail["result"]["error_native_code"] == 0x110001
    assert detail["result"]["error_stage"] == "read_sample"
    assert len(detail["readings"]) == patches and not detail["events"]
    if patches:
        assert detail["readings"][0]["annotation"]["applied"]["name"] == retained.patches[
            0].observation.sample_id
    detail["result"]["status"] = "completed"
    assert inspect(binding)["result"]["status"] == status


def test_observation_only_source_preserves_null_operation_facts_and_quantity_reasons(
    tmp_path, result,
):
    observation = result.patches[1].observation  # Spectrum only, including signed native values.
    path = tmp_path / "observation.json"
    path.write_text(exports.to_json(observation))
    binding = DatasetBinding(source_file=path, format="observation_json")
    entry, = binding.activity()["entries"]
    assert entry["kind"] == "observation" and entry["command_id"] is None
    assert entry["status"] is None and entry["workflow"] is None
    assert entry["reading_count"] == 1
    detail = inspect(binding)
    assert detail["entry"] is None and detail["result"] is None
    reading, = detail["readings"]
    assert reading["observation"]["backend_reported"]["xyz"] is None
    assert reading["coordinate_reasons"]["xyy"] == "Native XYZ is unavailable"
    assert reading["observation"]["raw_backend_output"] == observation.raw_backend_output


@pytest.mark.parametrize("fault", ["bytes", "inode", "during_detail"])
def test_source_change_refuses_detail_and_disables_roster_jumps(tmp_path, result, monkeypatch,
                                                               fault):
    path = tmp_path / "operation.json"
    path.write_text(result_to_json(result))
    binding = DatasetBinding(source_file=path, format="operation_json")
    roster = binding.activity()
    if fault == "during_detail":
        original = binding.annotation_reference

        def changed(reference):
            detail = original(reference)
            path.write_bytes(path.read_bytes() + b" ")
            return detail

        monkeypatch.setattr(binding, "annotation_reference", changed)
    elif fault == "inode":
        raw = path.read_bytes()
        path.rename(tmp_path / "previous")
        path.write_bytes(raw)
    else:
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="changed"):
        inspect(binding, roster)
    current = binding.activity()
    assert current["reason"] and not current["entries"][0]["reading_refs"]


@pytest.mark.parametrize("state", [
    "accepted", "excluded", "terminal_unknown", "missing", "unparsed", "reserved", "started",
])
def test_saved_operations_retain_exact_notes_exclusions_and_raw_recovery(tmp_path, original,
                                                                       state):
    manifest, files = deepcopy(original)
    manifest["chart"] = None
    if state == "terminal_unknown":
        manifest, files = recovery_source(original)
        manifest["chart"] = None
    else:
        manifest["annotations"][0]["notes"] = "\nExact multiline notes 🟦\n" * 30
        if state == "excluded":
            name = manifest["operations"][0]["path"]
            files[name] += b" "
        elif state in {"missing", "reserved", "started"}:
            files.pop(manifest["operations"][0]["path"])
            if state in {"reserved", "started"}:
                manifest["operations"][0].update(state=state, sha256=None)
                manifest["annotations"] = []
        elif state == "unparsed":
            files[manifest["operations"][0]["path"]] = b'{"unparsed":'
    binding, folder = stored(tmp_path, manifest, files)
    before = {item.name: item.read_bytes() for item in folder.iterdir()}
    roster = binding.activity()
    entry, = roster["entries"]
    detail = inspect(binding, roster)
    assert detail["events_scope"] == "native-records-only; application events not persisted"
    assert not detail["events"]
    if state == "accepted":
        assert entry["reading_count"] == 1 and entry["saved"] and entry["record_retained"]
        assert detail["readings"][0]["annotation"]["applied"]["notes"] == manifest[
            "annotations"][0]["notes"]
    elif state == "excluded":
        assert detail["result"] is not None and entry["review_state"] == "unknown"
        assert not entry["record_retained"] and not entry["reading_refs"]
        assert entry["reading_count"] == 1
        assert not detail["readings"] and "cannot be authenticated" in entry["review_reason"]
    elif state == "terminal_unknown":
        assert entry["state"] == "terminal_unknown" and not entry["reading_refs"]
        assert entry["reading_count"] is None
        assert detail["result"] is None and detail["recovery_result"]["result_json"] == (
            '{"original":"broken')
        assert detail["recovery_result"]["software_stopped"] is True
    else:
        assert entry["reading_count"] is None and entry["status"] == "unknown"
        assert not entry["reading_refs"] and not detail["readings"]
        assert detail["result"] is None and not entry["record_retained"]
        if state in {"reserved", "started"}:
            assert entry["state"] == state
    assert {item.name: item.read_bytes() for item in folder.iterdir()} == before


def test_live_details_reuse_exact_annotation_events_and_refuse_changed_revision(app, monkeypatch):
    _complete(app)
    binding = DatasetBinding(owner=app)
    roster = binding.activity()
    detail = inspect(binding, roster)
    assert detail["events"] and detail["events_scope"] == (
        "recent-32-live-batch-events; scoped-by-operation")
    row, = detail["readings"]
    applied = row["annotation"]["applied"]
    notes = "\nCurrent editing text " + "x" * 300 + " different full suffix"
    assert _command(app, "edit_annotation_draft", dict(
        row["reading_ref"], version=applied["version"], draft_version=0,
        name="Changed name", notes=notes,
    ))["admission"] == "admitted"
    with pytest.raises(ValueError, match="Activity changed"):
        inspect(binding, roster)
    current = binding.activity()
    assert inspect(binding, current)["readings"][0]["annotation"]["draft"]["notes"] == notes
    original = activity._diagnostic

    def changed(record, selected):
        value = original(record, selected)
        with app._mutex:
            app._events.append(dict(kind="application_test_notice",
                                    operation_id=record["summary"]["operation_id"],
                                    payload=dict(reason="Scripted retained event")))
            app._revision += 1
        return value

    monkeypatch.setattr(activity, "_diagnostic", changed)
    with pytest.raises(ValueError, match="changed during inspection"):
        inspect(binding, current)
    assert _command(app, "discard_annotation_draft", dict(row["reading_ref"], draft_version=1))[
        "admission"] == "admitted"
    monkeypatch.setattr(activity, "_diagnostic", original)
    with app._mutex:
        for index in range(40):
            app._events.append(dict(kind="application_test_notice",
                                    operation_id=row["reading_ref"]["operation_id"],
                                    payload=dict(index=index)))
            app._revision += 1
    bounded = inspect(binding)
    assert len(bounded["events"]) == 32 and bounded["events"][0]["payload"]["index"] == 8


def test_live_unknown_terminal_keeps_memory_diagnostics_when_original_file_is_missing(
    app, monkeypatch,
):
    original = b"head\x00\xff gap omitted tail\n"
    _native_diagnostic_seam(app, monkeypatch, original, invalid_result=True, written_bytes=2)
    assert _command(app, "configure_native_diagnostics", dict(debug_level=2, limit_bytes=8))[
        "admission"] == "admitted"
    assert _acquire(app)["admission"] == "admitted"
    _wait(app, lambda state: state["phase"] == "idle", respond=True)
    _automatic_companion_state(app)
    entry = app._data["operations"][0]
    (app._folder / entry["native_diagnostics"]["path"]).unlink()
    detail = inspect(DatasetBinding(owner=app))
    assert detail["summary"]["reading_count"] is None
    assert detail["recovery_result"]["result_json"] == "original invalid terminal bytes"
    assert not detail["readings"]
    capture = detail["native_diagnostics"]
    assert capture["metadata"]["state"] == "incomplete"
    assert capture["review"]["state"] == "unavailable"
    assert base64.b64decode(capture["original"]["data"]) == original[:4] + original[-4:]
    assert capture["segments"] == [dict(source_offset=0, length=4,
                                        base64=base64.b64encode(original[:4]).decode()),
                                   dict(source_offset=len(original) - 4, length=4,
                                        base64=base64.b64encode(original[-4:]).decode())]


def test_inherited_live_and_saved_activity_keeps_original_qualified_refs(continued_chart,
                                                                      monkeypatch):
    parent, owner = continued_chart
    for binding in (DatasetBinding(owner=owner), DatasetBinding(folder=owner._folder)):
        roster = binding.activity()
        assert len(roster["entries"]) == 2
        inherited, local = roster["entries"]
        assert inherited["source_ref"]["kind"] == "inherited" and inherited["read_only"]
        assert inherited["source_ref"]["batch_id"] == parent.snapshot()["batch_id"]
        assert inherited["key"] != local["key"]
        detail = inspect(binding, roster)
        reference, = inherited["reading_refs"]
        assert reference["segment_id"] == parent.snapshot()["batch_id"]
        assert reference["source_manifest_sha256"] == inherited["source_ref"][
            "source_manifest_sha256"]
        assert detail["readings"][0]["annotation"]["read_only"]
    # A scripted retained local outcome deliberately shares both native IDs
    # with the admitted original. This exercises actual roster/detail resolution,
    # without rewriting the inherited original or operating a frontend.
    inherited_result = next(iter(owner._source_results.values()))
    identity, local_result = next(iter(owner._retained.items()))
    collision = replace(local_result, operation_id=inherited_result.operation_id, patches=tuple(
        replace(patch, observation=replace(
            patch.observation, operation_id=inherited_result.operation_id,
            observation_id=inherited_result.patches[0].observation.observation_id))
        for patch in local_result.patches))
    with monkeypatch.context() as scoped:
        current = deepcopy(owner._data)
        current["operations"][0].update(operation_id=collision.operation_id, sha256=None)
        for annotation in current["annotations"]:
            annotation.update(operation_id=collision.operation_id,
                              observation_id=collision.patches[0].observation.observation_id)
        scoped.setattr(owner, "_data", current)
        scoped.setattr(owner, "_retained", {identity: collision})
        scoped.setattr(owner, "_record_views", {
            identity: application._load(result_to_json(collision)),
        })
        scoped.setattr(owner, "_saved_ids", set())
        binding = DatasetBinding(owner=owner)
        roster = binding.activity()
        inherited, local = roster["entries"]
        original_ref, = inherited["reading_refs"]
        local_ref, = local["reading_refs"]
        assert (original_ref["operation_id"], original_ref["observation_id"]) == (
            local_ref["operation_id"], local_ref["observation_id"])
        assert inherited["key"] != local["key"]
        original_detail, local_detail = inspect(binding, roster), inspect(binding, roster, 1)
        assert original_detail["readings"][0]["key"] != local_detail["readings"][0]["key"]
        assert original_detail["readings"][0]["observation"]["sample_id"] == (
            inherited_result.patches[0].observation.sample_id)
        assert local_detail["readings"][0]["observation"]["sample_id"] == (
            local_result.patches[0].observation.sample_id)


def test_inherited_terminal_recovery_and_binary_diagnostic_original_remain_inspectable(
    tmp_path, original,
):
    manifest, files = diagnostic_source(original, b"native\x00\xff bytes\n", recovery=True,
                                       state="incomplete")
    collected = collect(manifest, files)
    current = continued(manifest, collected)
    originals = {f"source-{digest}.json": raw for digest, raw in collected["blobs"].items()}
    binding, _ = stored(tmp_path, current, originals)
    assert binding._review["chart"]["available"]
    entry, = binding.activity()["entries"]
    assert entry["source_ref"]["kind"] == "inherited" and entry["state"] == "terminal_unknown"
    assert entry["reading_count"] is None
    detail = inspect(binding)
    assert detail["recovery_result"]["result_json"] == '{"original":"broken'
    diagnostics = detail["native_diagnostics"]
    assert diagnostics["metadata"]["state"] == "incomplete"
    assert diagnostics["review"]["state"] == "authenticated"
    assert base64.b64decode(diagnostics["original"]["data"]) == b"native\x00\xff bytes\n"
    assert diagnostics["original"]["sha256"] == (
        hashlib.sha256(b"native\x00\xff bytes\n").hexdigest())
    assert not detail["readings"]


def test_operation_summary_reports_an_unknown_reading_count_for_a_record_without_patches():
    from color_measurement import activity

    source = dict(kind="current", batch_id="b", context_id="c", source_manifest_sha256="0" * 64,
                  source_id="s")
    entry = dict(operation_id="op", command_id="cmd", state="accepted", reason=None,
                 request=dict(workflow="spot"), draft=dict(name="n", notes=""))
    summary = activity._operation(source, entry, {"status": "completed"}, None,
                                  saved=True, read_only=False)["summary"]
    assert summary["reading_count"] is None and summary["reading_refs"] == []
    explicit = activity._operation(source, entry, {"status": "completed", "patches": []}, None,
                                   saved=True, read_only=False)["summary"]
    assert explicit["reading_count"] == 0
