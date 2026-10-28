"""Connected Demo batch ownership and crash-boundary persistence proof."""

import hashlib
import inspect
import json
import os
import subprocess
import sys
import threading
import time
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from color_measurement import application, exports, instrument, operation_exports, ti3_exports
from color_measurement.application import BatchApplication, review_saved
from color_measurement.demo import demo_frontends
from color_measurement.exports import _folder_lock, _read_regular


def _native_diagnostic_seam(owner, monkeypatch, original_bytes, *, written_bytes=None,
                            invalid_result=False, sink_stopped=True):
    """Record the native-only held-handle API while using actual Demo dispatch."""
    frontend = owner._frontend
    descriptor = json.loads(frontend.describe_json())
    descriptor["limits"]["native_diagnostics"] = dict(available=True, unavailable_reason=None)
    descriptor["capability_id"] = hashlib.sha256(json.dumps(
        {key: value for key, value in descriptor.items() if key != "capability_id"},
        sort_keys=True, allow_nan=False,
    ).encode("utf-8")).hexdigest()
    owner._data["descriptor"] = descriptor
    monkeypatch.setattr(frontend, "describe_json", lambda *args: application._json(descriptor))
    prepare = frontend.prepare
    calls = []

    def prepared(request_json, **kwargs):
        calls.append(dict(request=request_json, kwargs=kwargs))
        operation = prepare(request_json)
        if not kwargs:
            return operation
        target = kwargs["native_diagnostic_target"]
        cap = kwargs["native_diagnostic_limit_bytes"]
        retained = original_bytes if len(original_bytes) <= cap else (
            original_bytes[:cap // 2] + original_bytes[-(cap - cap // 2):]
        )
        segments = [dict(offset=0, length=len(original_bytes))] if original_bytes else []
        if len(original_bytes) > cap:
            head, tail = cap // 2, cap - cap // 2
            segments = ([dict(offset=0, length=head)] if head else []) + [
                dict(offset=len(original_bytes) - tail, length=tail),
            ]
        metadata = dict(
            schema_version=2, kind="native_stderr", state="prepared", path=target.name,
            debug_level=kwargs["native_debug_level"], limit_bytes=cap,
            received_bytes=0, retained_bytes=0, written_bytes=0, sha256=None,
            file_sha256=None, truncated=False, drain_complete=False, file_complete=False,
            worker_started=False, error=None,
            sink_stopped=True,
            retention="head_tail", segments=[],
        )
        dispatch = operation.dispatch

        def dispatched():
            result = dispatch()
            count = len(retained) if written_bytes is None else min(written_bytes, len(retained))
            target.write_bytes(retained[:count])
            complete = sink_stopped and count == len(retained)
            metadata.update(
                state="complete" if complete else "incomplete", received_bytes=len(original_bytes),
                retained_bytes=len(retained), written_bytes=count,
                sha256=hashlib.sha256(retained).hexdigest(),
                file_sha256=hashlib.sha256(retained[:count]).hexdigest() if sink_stopped else None,
                truncated=len(original_bytes) > len(retained), drain_complete=True,
                file_complete=complete, worker_started=True,
                error=None if complete else "Recording seam initial sink failed",
                sink_stopped=sink_stopped,
                segments=segments,
            )
            return result

        operation.dispatch = dispatched
        operation.native_diagnostics = lambda: deepcopy(metadata)
        operation.native_diagnostic_bytes = lambda: retained if metadata["sha256"] else None
        if invalid_result:
            operation.result_json = lambda: "original invalid terminal bytes"
        return operation

    monkeypatch.setattr(frontend, "prepare", prepared)
    return calls


@pytest.mark.parametrize("state", [
    "prepared", "not_started", "capturing", "complete", "incomplete", "truncated",
])
def test_real_diagnostic_capture_snapshots_satisfy_application_validator(tmp_path, state):
    from color_measurement.native_protocol import NativeDiagnosticCapture

    identity = str(uuid4())
    capture = NativeDiagnosticCapture(
        tmp_path / f"diagnostic-{identity}.log", debug_level=2, limit_bytes=8,
    )
    try:
        if state == "not_started":
            capture.not_started("Controlled unstarted capture")
        elif state != "prepared":
            capture.open()
            capture.record(b"head and tail original" if state == "truncated" else b"raw")
            if state != "capturing":
                capture.finish(drain_complete=state != "incomplete")
        metadata = capture.snapshot()
        assert application._diagnostic_metadata(metadata, identity) == metadata
        if metadata["sha256"] is not None:
            assert application._diagnostic_bytes(capture.original_bytes(), metadata) == (
                capture.original_bytes()
            )
    finally:
        capture.finish(drain_complete=True)


@pytest.mark.parametrize("version, old_shape", [
    (4, False), (4, True), ("5", False), (5.0, False), (True, False),
])
def test_batch_manifest_refuses_old_or_noninteger_schema_before_loading(app, version, old_shape):
    manifest = json.loads(app._manifest_bytes)
    manifest["schema_version"] = version
    if old_shape:
        del manifest["native_diagnostics"]
    with pytest.raises(ValueError, match="Unsupported batch manifest schema.*expected 5"):
        application._manifest(application._json(manifest).encode("ascii"))


def test_native_diagnostics_off_and_typed_authority_before_prepare(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app._frontend, "prepare", lambda *args, **kwargs: calls.append(args))
    assert app.snapshot()["native_diagnostics"] == dict(debug_level=0, limit_bytes=65536)
    for settings in (dict(debug_level=True, limit_bytes=16),
                     dict(debug_level=1, limit_bytes=True), dict(debug_level=1, limit_bytes=16)):
        assert _command(app, "configure_native_diagnostics", settings)["admission"] == "refused"
    assert calls == [] and not list(app._folder.glob("diagnostic-*.log"))


@pytest.mark.parametrize("written_bytes", [None, 3])
def test_native_diagnostics_exact_prefix_save_and_relocation(app, monkeypatch, tmp_path,
                                                            written_bytes):
    original = b"original\x00native\xffstderr\nremaining"
    calls = _native_diagnostic_seam(app, monkeypatch, original, written_bytes=written_bytes)
    assert _command(app, "configure_native_diagnostics", dict(
        debug_level=2, limit_bytes=16,
    ))["admission"] == "admitted"
    assert _acquire(app)["admission"] == "admitted"
    _wait(app, lambda state: state["phase"] == "idle", respond=True)
    entry = app._data["operations"][0]
    capture = entry["native_diagnostics"]
    expected = original[:8] + original[-8:]
    assert capture["segments"] == [dict(offset=0, length=8),
                                   dict(offset=len(original) - 8, length=8)]
    assert calls[0]["kwargs"]["native_diagnostic_target"].name == (
        f"diagnostic-{entry['command_id']}.log"
    )
    assert "native_debug_level" not in json.loads(calls[0]["request"])["options"]
    assert capture["truncated"] and capture["worker_started"]
    assert capture["state"] == ("complete" if written_bytes is None else "incomplete")
    assert (app._folder / capture["path"]).read_bytes() == expected
    review = review_saved(app._folder)
    assert review["operations"][0]["review_state"] == "accepted"
    assert review["operations"][0]["native_diagnostic_review"]["state"] == "authenticated"
    assert review["operations"][0]["native_diagnostics"] == capture
    _automatic_companion_state(app)
    (app._folder / capture["path"]).write_bytes(b"tampered source")
    damaged = review_saved(app._folder)
    assert damaged["operations"][0]["review_state"] == "accepted"
    assert damaged["operations"][0]["native_diagnostic_review"]["state"] == "unavailable"
    assert _command(app, "retry_save")["admission"] == "admitted"
    assert app.snapshot()["save_reason"] is None and app.snapshot()["saved_count"] == 1
    assert app.snapshot()["native_diagnostic_failures"]["publication"]
    assert (app._folder / capture["path"]).read_bytes() == b"tampered source"
    outcome = _command(app, "another_folder", dict(folder=str(tmp_path / "rescue")))
    assert outcome["admission"] == "admitted", outcome
    assert (app._folder / capture["path"]).read_bytes() == expected
    assert review_saved(app._folder)["operations"][0]["native_diagnostics"] == capture


def test_native_diagnostics_survive_unknown_terminal_without_reading(app, monkeypatch):
    original = b"original failure bytes\x00\xff"
    _native_diagnostic_seam(app, monkeypatch, original, invalid_result=True)
    assert _command(app, "configure_native_diagnostics", dict(
        debug_level=1, limit_bytes=64,
    ))["admission"] == "admitted"
    assert _acquire(app)["admission"] == "admitted"
    _wait(app, lambda state: state["phase"] == "idle", respond=True)
    review = review_saved(app._folder)
    assert review["sample_count"] == 0
    entry = review["operations"][0]
    assert entry["review_state"] == "terminal_unknown"
    assert entry["recovery_result"]["result_json"] == "original invalid terminal bytes"
    assert entry["native_diagnostic_review"]["state"] == "authenticated"
    assert (app._folder / entry["native_diagnostics"]["path"]).read_bytes() == original


@pytest.mark.parametrize("action, invalid_result, failed_api", [
    ("retry_save", False, "native_diagnostics"),
    ("retry_save", True, "native_diagnostic_bytes"),
    ("another_folder", False, "native_diagnostic_bytes"),
    ("another_folder", True, "native_diagnostics"),
])
def test_native_diagnostic_retry_recaptures_same_terminal_original(
    app, monkeypatch, tmp_path, action, invalid_result, failed_api,
):
    original = b"retained original\x00\xff"
    calls = _native_diagnostic_seam(app, monkeypatch, original, invalid_result=invalid_result)
    prepare = app._frontend.prepare
    held = []
    remaining = [1]

    def prepared(request, **kwargs):
        operation = prepare(request, **kwargs)
        if kwargs:
            held.append(operation)
            capture = getattr(operation, failed_api)

            def transient_failure():
                if operation.done and remaining[0]:
                    remaining[0] -= 1
                    raise OSError("Controlled transient diagnostic retention failure")
                return capture()

            setattr(operation, failed_api, transient_failure)
        return operation

    monkeypatch.setattr(app._frontend, "prepare", prepared)
    assert _command(app, "configure_native_diagnostics", dict(
        debug_level=1, limit_bytes=64,
    ))["admission"] == "admitted"
    assert _acquire(app)["admission"] == "admitted"
    _wait(app, lambda state: state["phase"] == "idle", respond=True)
    _automatic_companion_state(app)
    entry = app._data["operations"][0]
    identity = entry["command_id"]
    assert app._operation is None and held[0].done
    assert identity in app._diagnostic_reasons and app.snapshot()["can_measure"]
    source = app._folder
    reading = {path.name: path.read_bytes() for path in source.glob("operation-*.json")}
    preparations = len(calls)
    payload = (dict(folder=str(tmp_path / "rescued-diagnostics"))
               if action == "another_folder" else {})
    if action == "another_folder":
        publish = application._publish_owned_file

        def failed_destination(path, *args):
            if path == Path(payload["folder"]) / "batch.json":
                raise OSError("Controlled rescue manifest publication failure")
            return publish(path, *args)

        with monkeypatch.context() as scoped:
            scoped.setattr(application, "_publish_owned_file", failed_destination)
            failed = _command(app, action, payload)
        assert failed["admission"] == "refused" and app._folder == source
        assert str(Path(payload["folder"])) in app._rescue_attempts
    outcome = _command(app, action, payload)
    assert outcome["admission"] == "admitted", outcome
    assert len(calls) == preparations and held == [held[0]]
    assert not app._diagnostic_reasons and app.snapshot()["save_reason"] is None
    assert (app._folder / entry["native_diagnostics"]["path"]).read_bytes() == original
    review = review_saved(app._folder)
    assert review["operations"][0]["native_diagnostic_review"]["state"] == "authenticated"
    assert review["operations"][0]["review_state"] == (
        "terminal_unknown" if invalid_result else "accepted"
    )
    assert {path.name: path.read_bytes() for path in source.glob("operation-*.json")} == reading


def test_native_diagnostic_retry_preserves_permanent_failure_while_rescuing_readings(
    app, monkeypatch, tmp_path,
):
    calls = _native_diagnostic_seam(app, monkeypatch, b"exact permanent-failure original")
    prepare = app._frontend.prepare

    def prepared(request, **kwargs):
        operation = prepare(request, **kwargs)
        if kwargs:
            capture = operation.native_diagnostic_bytes

            def unavailable():
                if operation.done:
                    raise OSError("Controlled permanent diagnostic retention failure")
                return capture()

            operation.native_diagnostic_bytes = unavailable
        return operation

    monkeypatch.setattr(app._frontend, "prepare", prepared)
    publish = application._publish_owned_file
    blocked = [True]
    source = app._folder

    def canonical_failure(path, payload, previous):
        if (blocked[0] and path == source / "batch.json" and any(
            item["state"] == "accepted" for item in json.loads(payload)["operations"]
        )):
            raise OSError("Controlled canonical manifest failure")
        return publish(path, payload, previous)

    monkeypatch.setattr(application, "_publish_owned_file", canonical_failure)
    assert _command(app, "configure_native_diagnostics", dict(
        debug_level=1, limit_bytes=64,
    ))["admission"] == "admitted"
    assert _acquire(app)["admission"] == "admitted"
    _wait(app, lambda state: state["phase"] == "idle", respond=True)
    _automatic_companion_state(app)
    assert app.snapshot()["sample_count"] == 1 and app.snapshot()["saved_count"] == 0
    blocked[0] = False
    preparations = len(calls)
    target = tmp_path / "rescued-without-diagnostic"
    for action, payload in (("retry_save", {}), ("another_folder", dict(folder=str(target)))):
        outcome = _command(app, action, payload)
        assert outcome["admission"] == "admitted", outcome
        assert app._diagnostic_reasons and app.snapshot()["save_reason"] is None
        assert app.snapshot()["saved_count"] == 1 and app.snapshot()["can_measure"]
        assert len(calls) == preparations and review_saved(app._folder)["sample_count"] == 1
        identity = app._data["operations"][0]["command_id"]
        assert "permanent diagnostic" in app.operation_details(identity)[
            "native_diagnostic_retention_reason"
        ]
    assert app._folder == target and app._diagnostic_operations
    assert _command(app, "close", dict(discard_token=None))["admission"] == "refused"


def test_native_diagnostics_changes_do_not_split_chart_context(app, monkeypatch):
    from color_measurement import chart_state

    _native_diagnostic_seam(app, monkeypatch, b"native stderr")
    assert _configure_chart(app, chart_state.definition(
        "custom", name="Diagnostic provenance only", rows=1, columns=1,
    ))["admission"] == "admitted"
    chart = deepcopy(app._data["chart"])
    context = app.snapshot()["context_id"]
    assert _command(app, "configure_native_diagnostics", dict(
        debug_level=2, limit_bytes=32,
    ))["admission"] == "admitted"
    assert app._data["chart"] == chart and app.snapshot()["context_id"] == context


@pytest.mark.parametrize("original", [b"native original\x00\xff", b""])
def test_native_diagnostics_chart_continuation_retains_exact_original(app, monkeypatch, tmp_path,
                                                                     original):
    from color_measurement import chart_state

    _native_diagnostic_seam(app, monkeypatch, original)
    assert _command(app, "configure_native_diagnostics", dict(
        debug_level=1, limit_bytes=64,
    ))["admission"] == "admitted"
    _wait(app, lambda current: current["phase"] == "idle")
    assert _configure_chart(app, chart_state.definition(
        "custom", name="Diagnostic continuation", rows=1, columns=1,
    ))["admission"] == "admitted"
    state, _ = _wait(app, lambda current: current["phase"] == "idle")
    admitted = _command(app, "measure_chart", dict(
        context_id=state["context_id"], draft_id=state["draft"]["id"],
        draft_version=state["draft"]["version"], chart_revision=state["chart"]["revision"],
    ))
    assert admitted["admission"] == "admitted"
    _wait(app, lambda current: current["phase"] == "idle"
          and not current["companions"]["saving"], respond=True)
    source = review_saved(app._folder)
    assert source["chart"]["available"]
    capture = source["operations"][0]["native_diagnostics"]
    owner = _demo_batch(tmp_path / "continued")
    owner.bind()
    try:
        assert _command(owner, "continue_chart", dict(
            folder=str(app._folder), source_sha256=source["manifest_sha256"], action="new-pass",
            source_pass_id=source["manifest"]["chart"]["active_pass"], acknowledged_material=[],
        ))["admission"] == "admitted"
        _wait(owner, lambda current: current["phase"] == "idle"
              and not current["companions"]["saving"])
        inherited_path = owner._folder / f"source-{capture['sha256']}.json"
        assert inherited_path.read_bytes() == original
        (app._folder / capture["path"]).unlink()
        continued = review_saved(owner._folder)
        assert continued["chart"]["available"]
        assert dict(path=inherited_path.name, sha256=capture["sha256"]) in (
            continued["chart"]["source_files"]
        )
    finally:
        _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


def test_native_diagnostic_publication_failure_retains_reading_and_close_confirmation(
        app, monkeypatch, tmp_path):
    _native_diagnostic_seam(app, monkeypatch, b"retained failed publication")
    assert _command(app, "configure_native_diagnostics", dict(
        debug_level=1, limit_bytes=64,
    ))["admission"] == "admitted"
    publish = application._publish_owned_file

    def failed(path, *args):
        if path.suffix == ".log" and path.parent == tmp_path / "batch":
            raise OSError("Diagnostic publication failure")
        return publish(path, *args)

    monkeypatch.setattr(application, "_publish_owned_file", failed)
    assert _acquire(app)["admission"] == "admitted"
    _wait(app, lambda state: state["phase"] == "idle"
          and not state["companions"]["saving"], respond=True)
    assert app.snapshot()["accepted_count"] == 1 and app.snapshot()["saved_count"] == 1
    assert app.snapshot()["save_reason"] is None
    assert app.snapshot()["native_diagnostic_failures"]["publication"]
    assert app.discard_summary()["native_diagnostics"]["captures"]
    assert _command(app, "close", dict(discard_token=None))["admission"] == "refused"
    rescued = _command(app, "another_folder", dict(folder=str(tmp_path / "rescue")))
    assert rescued["admission"] == "admitted", rescued
    assert review_saved(app._folder)["sample_count"] == 1
    assert not app.snapshot()["native_diagnostic_failures"]["publication"]


def test_native_diagnostic_unfinished_sink_preserves_original_path_and_rescues_closed_prefix(
        app, monkeypatch, tmp_path):
    original = b"original memory prefix survives unfinished sink"
    _native_diagnostic_seam(app, monkeypatch, original, written_bytes=4, sink_stopped=False)
    assert _command(app, "configure_native_diagnostics", dict(
        debug_level=1, limit_bytes=64,
    ))["admission"] == "admitted"
    assert _acquire(app)["admission"] == "admitted"
    _wait(app, lambda state: state["phase"] == "idle"
          and not state["companions"]["saving"], respond=True)
    capture = app._data["operations"][0]["native_diagnostics"]
    old_path = app._folder / capture["path"]
    assert app.snapshot()["software_stopped"] and app.snapshot()["accepted_count"] == 1
    assert not capture["sink_stopped"] and not capture["file_complete"]
    assert capture["file_sha256"] is None and old_path.read_bytes() == original[:4]
    assert app.snapshot()["saved_count"] == 1 and app.snapshot()["save_reason"] is None
    assert any("sink stop is unconfirmed" in reason for reason in
               app.snapshot()["native_diagnostic_failures"]["publication"].values())
    rescued = _command(app, "another_folder", dict(folder=str(tmp_path / "rescued-sink")))
    assert rescued["admission"] == "admitted", rescued
    assert old_path.read_bytes() == original[:4]
    assert (app._folder / capture["path"]).read_bytes() == original
    reviewed = review_saved(app._folder)
    assert reviewed["operations"][0]["native_diagnostics"] == capture
    assert reviewed["operations"][0]["native_diagnostic_review"]["state"] == "authenticated"


def _demo_frontend(persona="i1pro3"):
    return next(front for front in demo_frontends() if front._meter.persona == persona)


def _demo_batch(folder, **metadata):
    return BatchApplication(
        folder, frontend=_demo_frontend(), mode="emissive_spot", options={}, **metadata,
    )


def _demo_new_batch(owner, folder, **metadata):
    return owner.new_batch(
        folder, frontend=owner._frontend, mode=owner.snapshot()["mode"],
        options=owner.snapshot()["draft"]["options"], **metadata,
    )


def _snapshot(app):
    state = app.snapshot()
    state["annotations"] = [
        app.annotation_details(item["operation_id"], item["observation_id"])["applied"]
        for item in state["annotations"]
    ]
    state["operations"] = [
        dict(
            item,
            **{
                key: value
                for key, value in app.operation_details(item["command_id"]).items()
                if key not in item
            },
        )
        for item in state["operations"]
    ]
    return state


def _annotate(app, item, *, name, notes):
    key = {field: item[field] for field in ("operation_id", "observation_id", "version")}
    existing = next(
        (
            draft
            for draft in app.snapshot()["annotation_drafts"]
            if draft["operation_id"] == item["operation_id"]
            and draft["observation_id"] == item["observation_id"]
        ),
        None,
    )
    version = existing["draft_version"] if existing else 0
    staged = _command(
        app, "edit_annotation_draft", dict(key, draft_version=version, name=name, notes=notes)
    )
    assert staged["admission"] == "admitted", staged
    return _command(app, "annotate", dict(key, draft_version=version + 1))


def _command(app, action, payload=None, *, identity=None, generation=None, batch_id=None):
    snapshot = _snapshot(app)
    command_id = identity or str(uuid4())
    outcome = app.command(
        command_id,
        snapshot["generation"] if generation is None else generation,
        action,
        payload or {},
        batch_id=batch_id or snapshot["batch_id"],
    )
    if outcome.get("state") in {"pending", "saving"}:
        _wait(app, lambda state: app.command_outcome(command_id)["state"]
              not in {"pending", "saving"})
        return app.command_outcome(command_id)
    return outcome


def _configure_chart(app, definition):
    snapshot = app.snapshot()
    staged = app.stage_chart_definition(definition, **{
        key: snapshot[key] for key in ("batch_id", "generation", "context_id")
    })
    return _command(app, "configure_chart", {
        key: staged[key] for key in ("definition_token", "mapping_confirmation")
    })


def _acquire(app, action="measure", *, identity=None):
    snapshot = _snapshot(app)
    draft = snapshot["draft"]
    return _command(
        app,
        action,
        dict(
            context_id=snapshot["context_id"], draft_id=draft["id"], draft_version=draft["version"]
        ),
        identity=identity,
    )


def _wait(app, predicate, *, respond=False, timeout=5, details=True):
    deadline = time.monotonic() + timeout
    prompts = []
    while time.monotonic() < deadline:
        snapshot = _snapshot(app) if details else app.snapshot()
        if predicate(snapshot):
            return snapshot, prompts
        if respond and snapshot["instructions"]:
            pending = snapshot["instructions"]
            prompts.append(pending["kind"])
            outcome = _command(
                app,
                "respond",
                dict(
                    operation_id=pending["operation_id"],
                    request_id=pending["request_id"],
                    response="accept",
                ),
            )
            assert outcome["admission"] == "admitted", outcome
        time.sleep(0.005)
    pytest.fail(f"Owner did not reach expected state: "
                f"{_snapshot(app) if details else app.snapshot()}")


def _complete(app, action="measure", *, export=True):
    outcome = _acquire(app, action)
    assert outcome["admission"] == "admitted", outcome
    result = _wait(app, lambda s: s["phase"] == "idle", respond=True)
    if export and result[0]["save_reason"] is None:
        _wait(app, lambda state: not state["companions"]["saving"])
        return _snapshot(app), result[1]
    return result


@pytest.fixture(autouse=True)
def no_native_transport(monkeypatch):
    attempts = []

    def forbidden(*args, **kwargs):
        # Acquisition runs on the owner's drainer thread, where a raised failure
        # only becomes a warning; record it and fail the test at teardown.
        attempts.append((args, kwargs))
        raise RuntimeError("Demo application attempted native transport")

    # _DemoInstrument overrides _open_session, so a patch on the base method
    # never fires for it; the helper transport that method constructs does.
    monkeypatch.setattr(instrument, "HelperSession", forbidden)
    monkeypatch.setattr(application, "_UNCERTAIN_OWNERS", [])
    import color_measurement.exports as publication

    monkeypatch.setattr(publication, "_RETIRED_PUBLICATION_LOCKS", {})
    yield
    assert not attempts, "Demo application attempted native transport"


@pytest.fixture
def app(tmp_path):
    owner = _demo_batch(tmp_path / "batch")
    owner.bind()
    yield owner
    if _snapshot(owner)["status"] == "abandoned":
        return
    _wait(owner, lambda state: not state["companions"]["saving"])
    if _snapshot(owner)["phase"] == "cleanup_uncertain":
        _command(
            owner,
            "abandon",
            dict(discard_token=owner.discard_summary()["token"], reason="Test owner abandonment"),
        )
        return
    if _snapshot(owner)["phase"] != "idle":
        _command(owner, "cancel", dict(command_id=owner._active))
        _wait(owner, lambda s: s["phase"] == "idle")
    if _snapshot(owner)["status"] != "closed":
        _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


def test_complete_public_demo_batch_route(app, tmp_path):
    draft = _snapshot(app)["draft"]
    assert (
        _command(
            app,
            "edit_draft",
            dict(
                version=draft["version"],
                name="White α",
                notes="First line\nSecond line",
                options={},
                draft_id=draft["id"],
                number_samples=False,
            ),
        )["admission"]
        == "admitted"
    )
    first, prompts = _complete(app)
    assert prompts == ["calibration_setup", "sample_position"]
    assert first["sample_count"] == first["accepted_count"] == first["saved_count"] == 1
    original = _read_regular(Path(first["folder"]) / first["operations"][0]["path"])
    annotation = first["annotations"][0]
    assert (
        _command(
            app, "select", {key: annotation[key] for key in ("operation_id", "observation_id")}
        )["admission"]
        == "admitted"
    )
    assert _annotate(app, annotation, name="Renamed", notes="Saved note")["admission"] == "admitted"
    assert _read_regular(Path(first["folder"]) / first["operations"][0]["path"]) == original
    assert first["draft"]["id"] != draft["id"] and first["draft"]["notes"] == ""
    second, prompts = _complete(app)
    assert prompts == []
    assert second["sample_count"] == 2
    third, prompts = _complete(app)
    assert prompts == [] and third["sample_count"] == 3
    current = third["ti3"]
    assert current["status"] == "current"
    # One batch writer group aggregates both accepted spots, with saved overlays.
    xyz = [
        item
        for item in current["files"]
        if item["scope"] == "ordinary" and item["state"] == "current" and item["row_count"] == 3
    ]
    assert xyz
    assert any(
        "Renamed" in Path(item["path"]).read_text()
        and "Saved note" in Path(item["path"]).read_text()
        for item in current["files"]
    )
    assert len({item["group_id"] for item in xyz}) >= 1
    calibrated, prompts = _complete(app, "calibrate")
    assert prompts == []
    assert calibrated["sample_count"] == 3 and calibrated["saved_count"] == 4
    assert calibrated["operations"][-1]["result"]["patches"] == []
    assert _command(app, "finish")["admission"] == "admitted"
    with _folder_lock(Path(first["folder"]), ".batch.lock"):
        pass
    review = review_saved(first["folder"])
    assert review["read_only"] and review["manifest"]["status"] == "finished"
    assert review["sample_count"] == 3 and len(review["operations"]) == 4
    assert review["export_state"] == "unverified"
    new = _demo_new_batch(app, tmp_path / "next")
    try:
        assert _snapshot(new)["batch_id"] != first["batch_id"]
        assert _command(new, "finish", batch_id=first["batch_id"])["admission"] == "refused"
    finally:
        _command(new, "close", dict(discard_token=None))


def test_fresh_explicit_calibration_and_retained_state(app):
    snapshot, prompts = _complete(app, "calibrate")
    assert prompts == ["calibration_setup"]
    assert snapshot["accepted_count"] == snapshot["saved_count"] == 1
    assert snapshot["sample_count"] == 0 and snapshot["operations"][0]["result"]["patches"] == []
    next_snapshot, prompts = _complete(app)
    assert prompts == [] and next_snapshot["sample_count"] == 1
    assert next_snapshot["operations"][0]["result"]["calibration"]


def test_cancelled_fresh_calibration_retains_non_sample_outcome(app):
    identity = str(uuid4())
    assert _acquire(app, "calibrate", identity=identity)["admission"] == "admitted"
    snapshot, _ = _wait(app, lambda s: s["instructions"] is not None)
    pending = snapshot["instructions"]
    assert pending["kind"] == "calibration_setup"
    assert (
        _command(
            app,
            "respond",
            dict(operation_id=pending["operation_id"], request_id="wrong", response="accept"),
        )["admission"]
        == "refused"
    )
    assert _command(app, "cancel", dict(command_id=identity))["effect"] == "cancellation_requested"
    snapshot, _ = _wait(app, lambda s: s["phase"] == "idle")
    result = snapshot["operations"][0]["result"]
    assert result["status"] == "cancelled" and result["patches"] == []
    assert result["software_stopped"] and result["instrument_state"] == "not_applicable"
    assert review_saved(snapshot["folder"])["sample_count"] == 0


def test_duplicate_completed_command_survives_rebind_and_changed_content_refused(app):
    identity = str(uuid4())
    draft = _snapshot(app)["draft"]
    payload = dict(
        context_id=_snapshot(app)["context_id"],
        draft_id=draft["id"],
        draft_version=draft["version"],
    )
    assert _command(app, "measure", payload, identity=identity)["admission"] == "admitted"
    snapshot, _ = _wait(app, lambda s: s["phase"] == "idle", respond=True)
    app.bind()
    assert _command(app, "measure", payload, identity=identity)["state"] == "completed"
    assert _snapshot(app)["accepted_count"] == 1
    assert _command(app, "calibrate", payload, identity=identity)["admission"] == "refused"
    assert snapshot["operations"][0]["command_id"] == identity


def test_stale_generation_refuses_unadmitted_call_but_retains_admitted_work(app):
    old = _snapshot(app)["generation"]
    identity = str(uuid4())
    assert _acquire(app, identity=identity)["admission"] == "admitted"
    rebound = app.bind()
    assert rebound["generation"] == old + 1
    assert _command(app, "finish", generation=old)["admission"] == "refused"
    snapshot, _ = _wait(app, lambda s: s["phase"] == "idle", respond=True)
    assert app.command_outcome(identity)["state"] == "completed"
    assert snapshot["sample_count"] == 1


def test_starting_cancel_resolves_held_operation_before_dispatch(app, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    prepare = app._frontend.prepare

    def blocked(request):
        operation = prepare(request)
        dispatch = operation.dispatch

        def held_dispatch():
            entered.set()
            assert release.wait(5)
            # The real frontend refuses a resolved handle; do not pre-empt it.
            dispatch()

        operation.dispatch = held_dispatch
        return operation

    monkeypatch.setattr(app._frontend, "prepare", blocked)
    persisted, persist = [], app._persist

    def observed_persist():
        persisted.append([entry["state"] for entry in app._data["operations"]])
        return persist()

    monkeypatch.setattr(app, "_persist", observed_persist)
    identity = str(uuid4())
    admitted = _acquire(app, identity=identity)
    assert admitted["state"] == "starting" and admitted["operation_id"]
    assert entered.wait(2)
    assert _command(app, "finish")["admission"] == "refused"
    assert _command(app, "cancel", dict(command_id=identity))["admission"] == "admitted"
    release.set()
    snapshot, _ = _wait(app, lambda s: s["phase"] == "idle")
    assert snapshot["operations"][0]["result"]["status"] == "cancelled"
    assert snapshot["sample_count"] == 0
    # Nothing was dispatched: no started intent was published and no dispatch
    # failure was recorded for an ordinary cancel during Starting.
    assert all("started" not in states for states in persisted)
    assert all(event["kind"] != "application_dispatch_failed" for event in snapshot["events"])
    assert app.command_outcome(identity)["state"] == "completed"
    assert snapshot["operations"][0]["state"] == "accepted"


def test_failed_intent_retains_and_drains_without_dispatch(app, monkeypatch):
    calls, held = [], []
    prepare = app._frontend.prepare

    def retain(request):
        operation = prepare(request)
        held.append(operation)
        operation.dispatch = lambda: calls.append(operation.operation_id)
        return operation

    monkeypatch.setattr(app._frontend, "prepare", retain)

    def fail(path, payload, previous):
        if path.name == "batch.json":
            entry = json.loads(payload)["operations"][0]
            if entry["state"] == "reserved":
                assert app._operation is held[0]
            assert entry["operation_id"] == held[0].operation_id
            assert entry["request"] == json.loads(held[0].request_json)
        raise OSError("intent destination denied")

    monkeypatch.setattr(application, "_publish_owned_file", fail)
    outcome = _acquire(app)
    assert outcome["admission"] == "refused" and "intent destination denied" in outcome["reason"]
    assert outcome["operation_id"] == held[0].operation_id
    assert calls == [] and _snapshot(app)["phase"] == "idle"
    result = json.loads(held[0].result_json())
    assert held[0].done and result["software_stopped"] and result["status"] == "cancelled"
    assert not result["patches"] and not application._UNCERTAIN_OWNERS


def test_record_published_manifest_failed_retry_never_republishes_or_reacquires(app, monkeypatch):
    publication = application._publish_owned_file

    def fail_acceptance(path, payload, previous):
        if path.name == "batch.json" and any(
            item["state"] == "accepted" for item in json.loads(payload)["operations"]
        ):
            raise OSError("manifest acceptance denied")
        return publication(path, payload, previous)

    monkeypatch.setattr(application, "_publish_owned_file", fail_acceptance)
    snapshot, _ = _complete(app)
    assert snapshot["sample_count"] == snapshot["accepted_count"] == 1
    assert snapshot["saved_count"] == 0 and "manifest acceptance denied" in snapshot["save_reason"]
    path = Path(snapshot["folder"]) / snapshot["operations"][0]["path"]
    raw = _read_regular(path)
    review = review_saved(snapshot["folder"])
    assert review["operations"][0]["review_state"] == "found_unaccepted"
    assert review["sample_count"] == 0
    assert _acquire(app)["admission"] == "refused"
    assert _acquire(app, "calibrate")["admission"] == "refused"
    assert _command(app, "finish")["admission"] == "refused"
    monkeypatch.setattr(application, "_publish_owned_file", publication)

    def forbidden(*args, **kwargs):
        pytest.fail("Retry attempted acquisition or record publication")

    monkeypatch.setattr(app._frontend, "prepare", forbidden)
    monkeypatch.setattr(operation_exports, "publish", forbidden)
    assert _command(app, "retry_save")["admission"] == "admitted"
    assert _read_regular(path) == raw
    assert _snapshot(app)["saved_count"] == 1
    assert review_saved(snapshot["folder"])["operations"][0]["review_state"] == "accepted"


def test_record_failure_and_another_folder_preserve_exact_retained_work(app, monkeypatch, tmp_path):
    publish = operation_exports.publish

    def fail(*args, **kwargs):
        raise OSError("record destination denied")

    monkeypatch.setattr(operation_exports, "publish", fail)
    snapshot, _ = _complete(app)
    assert snapshot["saved_count"] == 0 and snapshot["accepted_count"] == 1
    old_manifest = _read_regular(Path(snapshot["folder"]) / "batch.json")
    expected = application.result_to_json(next(iter(app._retained.values()))).encode()
    monkeypatch.setattr(operation_exports, "publish", publish)
    monkeypatch.setattr(app._frontend, "prepare", lambda text: pytest.fail("move reacquired"))
    new_folder = tmp_path / "replacement"
    assert _command(app, "another_folder", dict(folder=str(new_folder)))["admission"] == "admitted"
    moved = _snapshot(app)
    assert moved["batch_id"] == snapshot["batch_id"] and moved["saved_count"] == 1
    assert _read_regular(new_folder / moved["operations"][0]["path"]) == expected
    assert _read_regular(Path(snapshot["folder"]) / "batch.json") == old_manifest
    assert moved["ti3"]["source_id"] == str(new_folder / "batch.json")


def test_failed_folder_handover_keeps_old_lock_and_destination(app, monkeypatch, tmp_path):
    before = _snapshot(app)
    publication = application._publish_owned_file
    new_folder = tmp_path / "bad-new"

    def fail_new(path, payload, previous):
        if path.parent == new_folder:
            raise OSError("new destination denied")
        return publication(path, payload, previous)

    monkeypatch.setattr(application, "_publish_owned_file", fail_new)
    assert _command(app, "another_folder", dict(folder=str(new_folder)))["admission"] == "refused"
    assert _snapshot(app)["folder"] == before["folder"]
    with pytest.raises(OSError):
        with _folder_lock(Path(before["folder"]), ".batch.lock"):
            pass
    with _folder_lock(new_folder, ".batch.lock"):
        pass


def test_reserved_file_conflict_is_not_overwritten(app, monkeypatch):
    publish = operation_exports.publish

    def conflicting(result, path, **kwargs):
        path.write_bytes(b"foreign bytes")
        return publish(result, path, **kwargs)

    monkeypatch.setattr(operation_exports, "publish", conflicting)
    snapshot, _ = _complete(app)
    assert snapshot["saved_count"] == 0
    path = Path(snapshot["folder"]) / snapshot["operations"][0]["path"]
    assert path.read_bytes() == b"foreign bytes"
    monkeypatch.setattr(operation_exports, "publish", publish)
    assert _command(app, "retry_save")["admission"] == "refused"
    assert path.read_bytes() == b"foreign bytes" and _snapshot(app)["accepted_count"] == 1


def test_optional_receipt_failure_preserves_canonical_work_and_full_result(app, monkeypatch):
    publication = application._publish_owned_file

    def fail_receipt(path, payload, previous):
        if path.name == "ti3-result.json":
            raise OSError("receipt destination denied")
        return publication(path, payload, previous)

    monkeypatch.setattr(application, "_publish_owned_file", fail_receipt)
    snapshot, _ = _complete(app)
    assert snapshot["saved_count"] == 1 and snapshot["save_reason"] is None
    assert snapshot["ti3"]["files"] and snapshot["export_state"] == "unverified"
    assert "receipt destination denied" in snapshot["export_reason"]
    assert _command(app, "finish")["admission"] == "admitted"
    monkeypatch.setattr(application, "_publish_owned_file", publication)
    monkeypatch.setattr(app._frontend, "prepare", lambda text: pytest.fail("export reacquired"))
    assert _command(app, "retry_save")["admission"] == "admitted"
    assert _snapshot(app)["export_state"] == "current"
    assert (
        json.loads((Path(snapshot["folder"]) / "ti3-result.json").read_text())
        == _snapshot(app)["ti3"]
    )


def test_export_failure_is_optional_and_retry_reuses_complete_receipt(app, monkeypatch):
    _complete(app)
    previous = _snapshot(app)["ti3"]
    calls = []
    update = ti3_exports.update_json

    def fail(*args):
        calls.append(json.loads(args[2]))
        raise OSError("optional writer failed")

    monkeypatch.setattr(ti3_exports, "update_json", fail)
    assert _command(app, "retry_save")["admission"] == "admitted"
    assert calls == [previous] and _snapshot(app)["saved_count"] == 1
    assert _snapshot(app)["export_state"] == "unverified"
    monkeypatch.setattr(ti3_exports, "update_json", update)
    assert _command(app, "retry_save")["admission"] == "admitted"
    assert _snapshot(app)["export_state"] == "current"


def test_finished_export_retry_authenticates_manifest_records_and_lock(app):
    _complete(app)
    assert _command(app, "finish")["admission"] == "admitted"
    folder = Path(_snapshot(app)["folder"])
    with _folder_lock(folder, ".batch.lock"):
        assert _command(app, "retry_save")["admission"] == "refused"
    entry = _snapshot(app)["operations"][0]
    (folder / entry["path"]).write_bytes(b"changed record")
    assert _command(app, "retry_save")["admission"] == "refused"
    assert _snapshot(app)["saved_count"] == 1  # Historical publication, not current authentication.
    assert review_saved(folder)["operations"][0]["review_state"] == "unknown"


def test_discard_summary_versions_stale_confirmation(app, monkeypatch):
    def fail(*args):
        raise OSError("annotation save denied")

    monkeypatch.setattr(application, "_publish_owned_file", fail)
    draft = _snapshot(app)["draft"]
    _command(
        app,
        "edit_draft",
        dict(
            version=draft["version"],
            name="First",
            notes="",
            options={},
            draft_id=draft["id"],
            number_samples=False,
        ),
    )
    old = app.discard_summary()
    draft = _snapshot(app)["draft"]
    _command(
        app,
        "edit_draft",
        dict(
            version=draft["version"],
            name="Second",
            notes="",
            options={},
            draft_id=draft["id"],
            number_samples=False,
        ),
    )
    new = app.discard_summary()
    assert old["token"] != new["token"] and new["drafts"]["name"] == "Second"
    assert _command(app, "close", dict(discard_token=old["token"]))["admission"] == "refused"
    assert _command(app, "close", dict(discard_token=new["token"]))["admission"] == "admitted"


def test_new_batch_refuses_unfinished_busy_and_unsaved(app, tmp_path):
    with pytest.raises(ValueError, match="durable Finish"):
        _demo_new_batch(app, tmp_path / "new")
    _acquire(app)
    with pytest.raises(ValueError, match="durable Finish"):
        _demo_new_batch(app, tmp_path / "new")
    assert _command(app, "close", dict(discard_token=None))["admission"] == "refused"
    _command(app, "cancel", dict(command_id=app._active))
    _wait(app, lambda s: s["phase"] == "idle")


@pytest.mark.parametrize("boundary", ["reserved", "started", "record", "finished"])
def test_crash_review_classifies_only_reserved_references(app, monkeypatch, boundary):
    entered, release = threading.Event(), threading.Event()
    prepare = app._frontend.prepare
    if boundary == "reserved":

        def blocked(text):
            operation = prepare(text)
            dispatch = operation.dispatch

            def held_dispatch():
                entered.set()
                assert release.wait(5)
                # The real frontend refuses a resolved handle; do not pre-empt it.
                dispatch()

            operation.dispatch = held_dispatch
            return operation

        monkeypatch.setattr(app._frontend, "prepare", blocked)
        _acquire(app)
        assert entered.wait(2)
        review = review_saved(_snapshot(app)["folder"])
        assert review["operations"][0]["state"] == "reserved"
        assert review["operations"][0]["review_state"] == "unknown"
        release.set()
        _command(app, "cancel", dict(command_id=app._active))
        _wait(app, lambda s: s["phase"] == "idle")
    elif boundary == "started":
        _acquire(app)
        _wait(app, lambda s: s["instructions"] is not None)
        review = review_saved(_snapshot(app)["folder"])
        assert review["operations"][0]["state"] == "started"
        assert review["operations"][0]["review_state"] == "unknown"
        _command(app, "cancel", dict(command_id=app._active))
        _wait(app, lambda s: s["phase"] == "idle")
    else:
        if boundary == "record":
            publication = application._publish_owned_file

            def fail(path, payload, previous):
                if path.name == "batch.json" and any(
                    item["state"] == "accepted" for item in json.loads(payload)["operations"]
                ):
                    raise OSError("simulated crash before acceptance")
                return publication(path, payload, previous)

            monkeypatch.setattr(application, "_publish_owned_file", fail)
        _complete(app)
        if boundary == "finished":
            _command(app, "finish")
        review = review_saved(_snapshot(app)["folder"])
        assert review["operations"][0]["review_state"] == (
            "found_unaccepted" if boundary == "record" else "accepted"
        )
    folder = Path(_snapshot(app)["folder"])
    (folder / "unreferenced.json").write_text("invalid unrelated text")
    assert len(review_saved(folder)["operations"]) == 1


@pytest.mark.parametrize("tamper", ["digest", "request", "identity", "path", "symlink"])
def test_readback_rejects_tampered_binding(app, tamper):
    _complete(app)
    folder = Path(_snapshot(app)["folder"])
    manifest_path = folder / "batch.json"
    manifest = json.loads(manifest_path.read_text())
    entry = manifest["operations"][0]
    record = folder / entry["path"]
    if tamper == "digest":
        entry["sha256"] = "0" * 64
    elif tamper == "request":
        entry["request"]["options"] = {"different": True}
        entry["draft"]["options"] = {"different": True}
    elif tamper == "identity":
        entry["operation_id"] = str(uuid4())
        manifest["annotations"] = []
    elif tamper == "path":
        entry["path"] = "../escape.json"
    else:
        payload = record.read_bytes()
        record.unlink()
        (folder / "foreign.json").write_bytes(payload)
        record.symlink_to(folder / "foreign.json")
    manifest_path.write_text(json.dumps(manifest))
    if tamper == "path":
        with pytest.raises(ValueError, match="reserved command UUID"):
            review_saved(folder)
    else:
        review = review_saved(folder)
        assert review["operations"][0]["review_state"] == "unknown"
        assert review["sample_count"] == 0


def test_batch_lock_excludes_another_process_and_ti3_lock_stays_independent(app):
    folder = Path(_snapshot(app)["folder"])
    with _folder_lock(folder, ".ti3-export.lock"):
        pass
    script = """import sys
from pathlib import Path
from color_measurement.exports import _folder_lock
try:
    with _folder_lock(Path(sys.argv[1]), '.batch.lock'):
        sys.exit(2)
except OSError:
    sys.exit(0)
"""
    environment = dict(os.environ, PYTHONPATH=str(Path(application.__file__).parents[1]))
    outcome = subprocess.run(
        [sys.executable, "-c", script, str(folder)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert outcome.returncode == 0, outcome.stderr
    assert (folder / ".batch.lock").exists()


def test_incompatible_calibration_context_stays_distinct_groups(app):
    _complete(app)
    before = _snapshot(app)["ti3"]
    # Frontend owns calibration facts; changing its state through its operation
    # creates a genuine second canonical context, without changing grouping logic.
    _complete(app, "calibrate")
    _complete(app)
    snapshot = _snapshot(app)
    assert snapshot["saved_count"] == 3
    assert snapshot["ti3"]["owner_id"] == before["owner_id"]
    assert (
        snapshot["ti3"]["source_sha256"]
        == hashlib.sha256((Path(snapshot["folder"]) / "batch.json").read_bytes()).hexdigest()
    )
    # Existing TI3 owner alone decides whether reported contexts are compatible.
    assert (
        sum(
            item["row_count"]
            for item in snapshot["ti3"]["files"]
            if item["state"] == "current" and item["scope"] == "ordinary"
        )
        >= 2
    )


def test_empty_batch_finish_and_non_sample_only_export(app):
    assert _command(app, "retry_save")["admission"] == "admitted"
    assert _snapshot(app)["ti3"]["files"] == []
    assert _command(app, "finish")["admission"] == "admitted"
    review = review_saved(_snapshot(app)["folder"])
    assert review["sample_count"] == 0 and review["operations"] == []


@pytest.mark.parametrize("invalid", [float("nan"), None, [1], "text"])
def test_invalid_command_data_refused_before_mutation(app, invalid):
    with pytest.raises(ValueError):
        app.command(
            str(uuid4()),
            _snapshot(app)["generation"],
            "edit_draft",
            invalid,
            batch_id=_snapshot(app)["batch_id"],
        )
    assert _snapshot(app)["accepted_count"] == 0


def test_retired_receipt_readback_passes_full_writer_result(app, monkeypatch):
    _complete(app)
    snapshot = _snapshot(app)
    empty = ti3_exports.retained_snapshot(
        [],
        snapshot["batch_id"],
        hashlib.sha256((Path(snapshot["folder"]) / "batch.json").read_bytes()).hexdigest(),
        source_id=str(Path(snapshot["folder"]) / "batch.json"),
    )
    retired = json.loads(
        ti3_exports.update_json(json.dumps(empty), snapshot["folder"], json.dumps(snapshot["ti3"]))
    )
    assert retired["files"] and all(item["state"] == "retired" for item in retired["files"])
    receipt = json.dumps(retired, ensure_ascii=True, indent=2).encode() + b"\n"
    (Path(snapshot["folder"]) / "ti3-result.json").write_bytes(receipt)
    app._receipt_digest = hashlib.sha256(receipt).hexdigest()
    app._ti3 = None  # Exercise disk receipt readback, not the retained shortcut.
    update = ti3_exports.update_json
    previous = []

    def capture(source, folder, prior):
        previous.append(json.loads(prior))
        return update(source, folder, prior)

    monkeypatch.setattr(ti3_exports, "update_json", capture)
    assert _command(app, "retry_save")["admission"] == "admitted"
    assert previous == [retired]
    assert _snapshot(app)["export_state"] == "current"


def test_cross_operation_row_collision_is_optional_export_conflict(app, monkeypatch):
    from dataclasses import replace

    parse = application.result_from_json

    def shared_identity(text):
        result = parse(text)
        return replace(
            result,
            patches=tuple(
                replace(
                    patch, observation=replace(patch.observation, observation_id="same-observation")
                )
                for patch in result.patches
            ),
        )

    monkeypatch.setattr(application, "result_from_json", shared_identity)
    _complete(app)
    snapshot, _ = _complete(app)
    assert snapshot["saved_count"] == 2 and snapshot["sample_count"] == 2
    assert snapshot["export_state"] == "unverified"
    assert "unique observation IDs" in snapshot["export_reason"]
    assert len(snapshot["annotations"]) == 2
    assert snapshot["annotations"][0]["operation_id"] != snapshot["annotations"][1]["operation_id"]


def test_same_command_concurrent_callbacks_admit_once(app):
    identity = str(uuid4())
    barrier = threading.Barrier(3)
    outcomes = []

    def acquire():
        barrier.wait()
        outcomes.append(_acquire(app, identity=identity))

    threads = [threading.Thread(target=acquire) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=3)
    assert len(outcomes) == 2 and all(item["admission"] == "admitted" for item in outcomes)
    snapshot, _ = _wait(app, lambda s: s["phase"] == "idle", respond=True)
    assert snapshot["accepted_count"] == 1 and len(snapshot["operations"]) == 1


def test_setup_invalid_options_and_stale_draft_refused_before_start(app, monkeypatch):
    draft = _snapshot(app)["draft"]
    before = _snapshot(app)
    assert (
        _command(
            app,
            "edit_draft",
            dict(
                version=draft["version"],
                name="Invalid",
                notes="",
                options={"unknown_option": True},
                draft_id=draft["id"],
                number_samples=False,
            ),
        )["admission"]
        == "refused"
    )
    assert _snapshot(app)["draft"] == before["draft"]
    payload = dict(context_id=before["context_id"], draft_id=draft["id"], draft_version=-1)
    assert _command(app, "measure", payload)["admission"] == "refused"
    assert _snapshot(app)["operations"] == []


def test_final_poll_race_drains_terminal_and_cleanup_uncertainty_blocks_actions(app, monkeypatch):
    from dataclasses import replace

    # Use an actual Demo result as the immutable fake worker's terminal payload.
    _complete(app)
    exemplar = next(iter(app._retained.values()))

    class Operation:
        operation_id = str(uuid4())
        done = False
        emitted = False

        def poll_event_json(self):
            if not self.done:
                self.done = True  # Empty poll races final publication/done.
                return ""
            if not self.emitted:
                self.emitted = True
                return json.dumps(
                    dict(
                        schema_version=1,
                        operation_id=self.operation_id,
                        sequence=0,
                        kind="terminal",
                        payload={"status": "completed"},
                    )
                )
            return ""

        def result_json(self):
            request = self.request
            observation = replace(
                exemplar.patches[0].observation,
                operation_id=self.operation_id,
                sample_id=request["patch_ids"][0],
                observation_id=str(uuid4()),
            )
            patch = replace(
                exemplar.patches[0], patch_id=request["patch_ids"][0], observation=observation
            )
            return application.result_to_json(
                replace(
                    exemplar,
                    operation_id=self.operation_id,
                    patches=(patch,),
                    expected_patch_ids=tuple(request["patch_ids"]),
                    request_json=json.dumps(request),
                    software_stopped=False,
                )
            )

    operation = Operation()

    def prepare(text):
        operation.request = json.loads(text)
        operation.request_json = text
        operation.dispatch = lambda: None
        return operation

    monkeypatch.setattr(app._frontend, "prepare", prepare)
    _acquire(app)
    snapshot, _ = _wait(app, lambda s: s["phase"] == "cleanup_uncertain")
    assert snapshot["saved_count"] == 2 and snapshot["save_reason"] is None
    assert snapshot["cleanup_reason"]
    with pytest.raises(ValueError, match="Unconfirmed acquisition cleanup"):
        _demo_batch(Path(snapshot["folder"]).parent / "blocked-before-abandon")
    assert any(
        event["kind"] == "terminal" and event["operation_id"] == operation.operation_id
        for event in snapshot["events"]
    )
    for action, payload in [
        ("close", dict(discard_token=None)),
        ("finish", {}),
        ("cancel", dict(command_id=app._active)),
    ]:
        assert _command(app, action, payload)["admission"] == "refused"
    assert (
        _acquire(app)["admission"] == "refused"
        and _acquire(app, "calibrate")["admission"] == "refused"
    )
    _wait(app, lambda state: not state["companions"]["saving"])
    abandoned = _command(
        app,
        "abandon",
        dict(
            discard_token=app.discard_summary()["token"],
            reason="Explicit test abandonment after unconfirmed cleanup",
        ),
    )
    assert abandoned["admission"] == "admitted" and abandoned["process_shutdown_required"]
    assert not abandoned["cleanup_confirmed"] and app.snapshot()["status"] == "abandoned"
    with pytest.raises(ValueError, match="Unconfirmed acquisition cleanup"):
        _demo_batch(Path(snapshot["folder"]).parent / "unsafe-next-owner")


def test_payload_is_frozen_before_waiting_for_owner_mutex(app, monkeypatch):
    # Capture the first signature serialization to deterministically locate the
    # caller between input capture and the owner mutex, without scheduling sleeps.
    captured = threading.Event()
    serialize = application._json
    payload = dict(
        version=_snapshot(app)["draft"]["version"],
        name="Frozen",
        notes="",
        options={},
        draft_id=_snapshot(app)["draft"]["id"],
        number_samples=False,
    )

    def signal(value):
        text = serialize(value)
        if isinstance(value, dict) and value.get("action") == "edit_draft":
            captured.set()
        return text

    monkeypatch.setattr(application, "_json", signal)
    outcomes = []
    with app._mutex:
        worker = threading.Thread(
            target=lambda: outcomes.append(
                app.command(
                    str(uuid4()),
                    app._generation,
                    "edit_draft",
                    payload,
                    batch_id=app._data["batch_id"],
                )
            )
        )
        worker.start()
        assert captured.wait(2)
        payload["name"] = "Changed caller buffer"
    worker.join(timeout=3)
    assert outcomes[0]["admission"] == "admitted"
    assert _snapshot(app)["draft"]["name"] == "Frozen"


def test_saved_source_uses_canonical_folder_spelling(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    app = _demo_batch(parent / ".." / "batch")
    try:
        app.bind()
        _complete(app)
        folder = Path(_snapshot(app)["folder"])
        assert folder == folder.resolve() and ".." not in folder.parts
        review = review_saved(parent / ".." / "batch")
        assert review["ti3"]["source_id"] == str(folder / "batch.json")
        assert "another batch" not in review["export_reason"]
    finally:
        _command(app, "close", dict(discard_token=None))


@pytest.mark.parametrize("device", [None, {}, {"device_id": "demo:i1pro3"}])
def test_malformed_frozen_descriptor_rejected_at_manifest_boundary(app, device):
    path = Path(_snapshot(app)["folder"]) / "batch.json"
    data = json.loads(path.read_text())
    if device is None:
        del data["descriptor"]["device"]
    else:
        data["descriptor"]["device"] = device
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="valid frozen DeviceIdentity"):
        review_saved(path.parent)


@pytest.mark.skipif(os.name == "nt", reason="Windows batch lock prevents renaming its open parent")
def test_removed_destination_rescue_uses_retained_digest_and_preserves_old_path(app, tmp_path):
    snapshot, _ = _complete(app)
    old = Path(snapshot["folder"])
    moved = tmp_path / "moved-old"
    old.rename(moved)
    new = tmp_path / "rescued"
    assert _command(app, "another_folder", dict(folder=str(new)))["admission"] == "admitted"
    state = _snapshot(app)
    assert state["saved_count"] == 1 and state["batch_id"] == snapshot["batch_id"]
    entry = state["operations"][0]
    assert (new / entry["path"]).read_bytes() == (moved / entry["path"]).read_bytes()
    assert str(old) in state["destination_issues"]
    review = review_saved(new)
    assert str(old) in review["manifest"]["destination_issues"]
    assert review["operations"][0]["review_state"] == "accepted"
    with _folder_lock(moved, ".batch.lock"):
        pass


def test_owned_folder_replacement_is_refused_before_record_write(app, tmp_path):
    before = _snapshot(app)
    old = Path(before["folder"])
    if os.name == "nt":
        with pytest.raises(PermissionError):
            old.rename(tmp_path / "old-folder")
        # Windows prevents the namespace attack at its held lock. The original
        # owned folder remains usable and no replacement can receive a record.
        after, _ = _complete(app)
        assert after["saved_count"] == 1 and Path(after["folder"]) == old
        assert not (tmp_path / "old-folder").exists()
        return
    old.rename(tmp_path / "old-folder")
    old.mkdir()
    assert _acquire(app)["admission"] == "refused"
    assert list(old.iterdir()) == []
    assert "replaced" in _snapshot(app)["save_reason"]


def test_duplicate_command_payload_key_order_is_semantically_equal(app):
    before = _snapshot(app)
    d = before["draft"]
    identity = str(uuid4())
    payload = dict(context_id=before["context_id"], draft_id=d["id"], draft_version=d["version"])
    assert _command(app, "measure", payload, identity=identity)["admission"] == "admitted"
    _wait(app, lambda s: s["phase"] == "idle", respond=True)
    reversed_payload = dict(reversed(list(payload.items())))
    assert _command(app, "measure", reversed_payload, identity=identity)["state"] == "completed"
    assert _snapshot(app)["accepted_count"] == 1


@pytest.mark.parametrize("field", ["status", "workflow", "state", "annotation_operation_id"])
@pytest.mark.parametrize("invalid", [[], {}])
def test_malformed_json_consumed_identity_and_enum_refused_with_value_error(app, field, invalid):
    _complete(app)
    path = Path(_snapshot(app)["folder"]) / "batch.json"
    manifest = json.loads(path.read_text())
    if field == "status":
        manifest["status"] = invalid
    elif field == "annotation_operation_id":
        manifest["annotations"][0]["operation_id"] = invalid
    elif field == "workflow":
        manifest["operations"][0]["request"]["workflow"] = invalid
    else:
        manifest["operations"][0]["state"] = invalid
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        review_saved(path.parent)


def test_rescue_preserves_authenticated_originals_and_new_work_after_old_file_edit(app, tmp_path):
    first, _ = _complete(app, export=False)
    old = Path(first["folder"])
    entry = first["operations"][0]
    original = (old / entry["path"]).read_bytes()
    changed = original + b" "
    (old / entry["path"]).write_bytes(changed)
    second, _ = _complete(app, export=False)
    assert second["saved_count"] == 2 and second["accepted_count"] == 2
    _automatic_companion_state(app)
    target = tmp_path / "rescue-conflict"
    assert _command(app, "another_folder", dict(folder=str(target)))["admission"] == "admitted"
    assert (old / entry["path"]).read_bytes() == changed
    assert (target / entry["path"]).read_bytes() == original
    review = review_saved(target)
    assert review["sample_count"] == 2
    assert "differs from accepted bytes" in review["manifest"]["destination_issues"][str(old)]


def test_same_rescue_folder_retries_authenticated_partial_publication(app, tmp_path, monkeypatch):
    _complete(app, export=False)
    _automatic_companion_state(app)
    target = tmp_path / "retry-rescue"
    publish = application._publish_owned_file

    def fail_manifest(path, payload, previous):
        if path.parent == target and path.name == "batch.json":
            raise OSError("manifest denied once")
        return publish(path, payload, previous)

    monkeypatch.setattr(application, "_publish_owned_file", fail_manifest)
    original = app.snapshot()["folder"]
    assert _command(app, "another_folder", dict(folder=str(target)))["admission"] == "refused"
    lock_inode = (target / ".batch.lock").stat().st_ino
    assert app.snapshot()["folder"] == original
    (target / "unrelated.txt").write_text("preserve this")
    monkeypatch.setattr(application, "_publish_owned_file", publish)
    assert _command(app, "another_folder", dict(folder=str(target)))["admission"] == "admitted"
    assert (target / ".batch.lock").stat().st_ino == lock_inode
    assert (target / "unrelated.txt").read_text() == "preserve this"
    assert review_saved(target)["sample_count"] == 1


def test_python_annotation_draft_survives_bind_revert_and_no_change_apply(app):
    state, _ = _complete(app, export=False)
    item = state["annotations"][0]
    key = {field: item[field] for field in ("operation_id", "observation_id", "version")}
    assert (
        _command(
            app, "edit_annotation_draft", dict(key, draft_version=0, name="Edited", notes="\n測定")
        )["admission"]
        == "admitted"
    )
    rebound = app.bind()
    draft = rebound["annotation_drafts"][0]
    assert draft["name"] == "Edited" and draft["notes_preview"] == "\n測定" and draft["dirty"]
    assert "notes" not in draft and draft["notes_length"] == 3
    exact = app.annotation_details(item["operation_id"], item["observation_id"])
    assert exact["draft"]["notes"] == "\n測定"
    assert not rebound["can_finish"]
    assert _command(app, "finish")["admission"] == "refused"
    assert (
        _command(
            app,
            "edit_annotation_draft",
            dict(key, draft_version=draft["draft_version"], name=item["name"], notes=item["notes"]),
        )["admission"]
        == "admitted"
    )
    reverted = app.snapshot()["annotation_drafts"][0]
    assert not reverted["dirty"] and app.snapshot()["can_finish"]
    assert (
        _command(app, "annotate", dict(key, draft_version=reverted["draft_version"]))["admission"]
        == "no_change"
    )
    assert app.snapshot()["annotations"][0]["version"] == item["version"]


def test_annotation_saving_is_visible_and_publication_failure_retains_owner_draft(app, monkeypatch):
    state, _ = _complete(app, export=False)
    item = state["annotations"][0]
    key = {field: item[field] for field in ("operation_id", "observation_id", "version")}
    _command(
        app,
        "edit_annotation_draft",
        dict(key, draft_version=0, name="New name", notes="first\nsecond"),
    )
    entered, release = threading.Event(), threading.Event()
    publish = application._publish_owned_file

    def block(path, payload, previous):
        if path.name == "batch.json":
            entered.set()
            assert release.wait(3)
            raise OSError("metadata disk fault")
        return publish(path, payload, previous)

    monkeypatch.setattr(application, "_publish_owned_file", block)
    identity = str(uuid4())
    before = app.snapshot()
    result = app.command(
        identity,
        before["generation"],
        "annotate",
        dict(key, draft_version=1),
        batch_id=before["batch_id"],
    )
    assert result["admission"] == "admitted" and result["state"] == "pending"
    assert entered.wait(2)
    current = app.bind()
    assert current["annotation_drafts"][0]["state"] == "saving"
    assert (
        current["annotations"][0]["name"] == "New name" and not current["annotations"][0]["saved"]
    )
    assert not current["can_measure"] and not current["can_finish"]
    release.set()
    _wait(app, lambda state: state["phase"] == "idle")
    assert app.command_outcome(identity)["state"] == "failed"
    assert app.snapshot()["annotation_drafts"][0]["state"] == "unsaved"
    monkeypatch.setattr(application, "_publish_owned_file", publish)
    assert _command(app, "retry_save")["admission"] == "admitted"
    assert app.snapshot()["annotation_drafts"][0]["state"] == "saved"
    assert (
        review_saved(app.snapshot()["folder"])["manifest"]["annotations"][0]["notes"]
        == "first\nsecond"
    )


def test_annotation_refusal_and_blank_name_have_truthful_status(app):
    state, _ = _complete(app, export=False)
    item = state["annotations"][0]
    key = {field: item[field] for field in ("operation_id", "observation_id", "version")}
    _command(app, "edit_annotation_draft", dict(key, draft_version=0, name="  ", notes="keep"))
    refusal = _command(app, "annotate", dict(key, draft_version=1))
    assert refusal["admission"] == "refused" and "non-whitespace" in refusal["reason"]
    assert app.snapshot()["annotation_drafts"][0]["state"] == "refused"
    assert app.snapshot()["annotations"][0]["version"] == 0
    draft = app.snapshot()["draft"]
    assert (
        _command(
            app,
            "edit_draft",
            dict(
                version=draft["version"],
                name="",
                notes="",
                options={},
                draft_id=draft["id"],
                number_samples=False,
            ),
        )["admission"]
        == "refused"
    )


@pytest.mark.parametrize("missing_result", [False, True])
def test_invalid_terminal_with_confirmed_stop_preserves_good_readings_and_can_finish(
    app, monkeypatch, tmp_path, missing_result
):
    previous, _ = _complete(app, export=False)
    prepare = app._frontend.prepare

    class InvalidResult:
        def __init__(self, operation):
            self.operation = operation

        def __getattr__(self, name):
            return getattr(self.operation, name)

        def result_json(self):
            if missing_result:
                raise RuntimeError("Terminal result was not published")
            result = json.loads(self.operation.result_json())
            result["device"]["serial"] = "wrong serial"
            return json.dumps(result)

    monkeypatch.setattr(app._frontend, "prepare", lambda text: InvalidResult(prepare(text)))
    _acquire(app)
    state, _ = _wait(app, lambda state: state["phase"] == "idle", respond=True)
    unknown = app.operation_details(state["operations"][-1]["command_id"])
    recovery = unknown["recovery_result"]
    assert state["sample_count"] == previous["sample_count"] == 1
    assert state["accepted_count"] == state["saved_count"] == 1
    assert recovery["software_stopped"] is True
    assert not state["cleanup_reason"] and not application._UNCERTAIN_OWNERS
    assert state["can_finish"] and state["can_measure"]
    assert _command(app, "retry_save")["admission"] == "admitted"
    assert _command(app, "finish")["admission"] == "admitted"
    review = review_saved(state["folder"])
    assert review["manifest"]["status"] == "finished" and review["sample_count"] == 1
    assert review["operations"][-1]["review_state"] == "terminal_unknown"
    raw = review["operations"][-1]["recovery_result"]["result_json"]
    if missing_result:
        assert raw == "" and "RuntimeError: Terminal result was not published" in recovery["reason"]
    else:
        assert json.loads(raw)["device"]["serial"] == "wrong serial"
    next_owner = _demo_batch(tmp_path / "after-confirmed-stop")
    _command(next_owner, "close", dict(discard_token=None))


def test_metadata_reuses_retained_records_and_selection_does_not_regenerate_ti3(app, monkeypatch):
    _complete(app, export=False)
    state = _automatic_companion_state(app)
    item = state["annotations"][0]
    read = application._read_regular
    attempts, refreshes = [], []
    update_ti3 = ti3_exports.update_json

    def guarded(path):
        if path.name.startswith("operation-"):
            attempts.append("metadata reread immutable record")
            raise RuntimeError(attempts[-1])
        return read(path)

    def observe_ti3(*args):
        refreshes.append(args)
        return update_ti3(*args)

    monkeypatch.setattr(application, "_read_regular", guarded)
    monkeypatch.setattr(ti3_exports, "update_json", observe_ti3)
    _annotate(app, item, name="Changed metadata", notes="safe")
    d = app.snapshot()["draft"]
    assert (
        _command(
            app,
            "edit_draft",
            dict(
                version=d["version"],
                name="Next sample",
                notes="notes",
                options={},
                draft_id=d["id"],
                number_samples=False,
            ),
        )["admission"]
        == "admitted"
    )
    current = _automatic_companion_state(app)
    assert refreshes
    assert current["ti3"]["source_sha256"] == current["companions"]["source_manifest_sha256"]
    row, = _automatic_companion_rows(current)
    assert row["name"] == "Changed metadata" and row["notes"] == "safe"
    refreshed = len(refreshes)
    before = len(app._commands)
    for _ in range(20):
        _command(
            app, "select", {field: item[field] for field in ("operation_id", "observation_id")}
        )
    assert len(app._commands) == before
    snapshot = app.snapshot()
    assert "commands" not in snapshot and all(
        "result" not in item for item in snapshot["operations"]
    )
    _automatic_companion_state(app)
    assert not attempts and len(refreshes) == refreshed


def test_200_actual_demo_readings_metadata_and_snapshot_cost(app, monkeypatch):
    operation_reads = []
    read = application._read_regular
    snapshot_calls = 0
    snapshot = app.snapshot

    def counted_snapshot():
        nonlocal snapshot_calls
        snapshot_calls += 1
        return snapshot()

    monkeypatch.setattr(app, "snapshot", counted_snapshot)

    def count(path):
        if path.name.startswith("operation-"):
            operation_reads.append(path)
        return read(path)

    monkeypatch.setattr(application, "_read_regular", count)
    begun = time.perf_counter()
    for index in range(200):
        state = app.snapshot()
        draft = state["draft"]
        outcome = app.command(
            str(uuid4()),
            state["generation"],
            "measure",
            dict(
                context_id=state["context_id"], draft_id=draft["id"], draft_version=draft["version"]
            ),
            batch_id=state["batch_id"],
        )
        assert outcome["admission"] == "admitted", outcome
        deadline = time.monotonic() + 5
        while True:
            state = app.snapshot()
            if state["instructions"]:
                p = state["instructions"]
                app.command(
                    str(uuid4()),
                    state["generation"],
                    "respond",
                    dict(
                        operation_id=p["operation_id"],
                        request_id=p["request_id"],
                        response="accept",
                    ),
                    batch_id=state["batch_id"],
                )
            if state["phase"] == "idle":
                break
            assert time.monotonic() < deadline, (
                "Demo reading did not become idle within 5 seconds: "
                f"attempt={index + 1}, saved={state['saved_count']}, "
                f"retained={state['sample_count']}, phase={state['phase']}, "
                f"snapshot_calls={snapshot_calls}, companions_saving="
                f"{state['companions']['saving']}"
            )
            time.sleep(0.001)
    generation_elapsed = time.perf_counter() - begun
    generation_snapshot_calls = snapshot_calls
    assert state["saved_count"] == 200 and state["sample_count"] == 200
    assert len(operation_reads) == 400  # Two checks for each new path, never prior files.
    operation_reads.clear()
    snapshot_begun = time.perf_counter()
    compact = app.snapshot()
    snapshot_elapsed = time.perf_counter() - snapshot_begun
    assert all("result" not in item for item in compact["operations"]) and "commands" not in compact
    encoded_bytes = len(json.dumps(compact).encode())
    annotation = compact["annotations"][0]
    edit_begun = time.perf_counter()
    _annotate(app, annotation, name="First of200", notes="\nMultiline retained metadata\n測定")
    annotation_elapsed = time.perf_counter() - edit_begun
    d = app.snapshot()["draft"]
    draft_begun = time.perf_counter()
    _command(
        app,
        "edit_draft",
        dict(
            version=d["version"],
            name="Next of200",
            notes="next",
            options={},
            draft_id=d["id"],
            number_samples=False,
        ),
    )
    draft_elapsed = time.perf_counter() - draft_begun
    select_begun = time.perf_counter()
    _command(
        app, "select", {field: annotation[field] for field in ("operation_id", "observation_id")}
    )
    select_elapsed = time.perf_counter() - select_begun
    assert operation_reads == []
    assert (
        len(
            {
                app.operation_details(item["command_id"])["result"]["operation_id"]
                for item in compact["operations"]
            }
        )
        == 200
    )
    # Two final metadata publications can queue another whole-batch TI3 refresh.
    # Own that settlement here rather than leaving it to the single-reading
    # fixture's five-second cleanup. Keep the measured interaction costs above
    # separate, and poll compact state without expanding all 200 native records.
    settlement_begun = time.perf_counter()
    settled, _ = _wait(app, lambda state: not state["companions"]["saving"],
                       timeout=30, details=False)
    assert settled["phase"] == "idle" and settled["save_reason"] is None
    assert settled["saved_count"] == settled["sample_count"] == 200
    assert settled["export_state"] == "current", settled["export_reason"]
    for format, output in settled["companions"]["formats"].items():
        if output["state"] == "unavailable":
            assert format in {"mat", "chart_selected_csv"} and output["reason"]
        else:
            if not output["current"]:
                pytest.fail(f"{format} companion is {output['state']}; "
                            f"reason: {output['reason']}")
            assert output["source_manifest_sha256"] == (
                settled["companions"]["source_manifest_sha256"])
    print(
        "BATCH_200_TIMING "
        + json.dumps(
            dict(
                readings=200,
                generation_seconds=generation_elapsed,
                generation_snapshot_calls=generation_snapshot_calls,
                annotation_seconds=annotation_elapsed,
                draft_seconds=draft_elapsed,
                selection_seconds=select_elapsed,
                compact_snapshot_seconds=snapshot_elapsed,
                compact_snapshot_bytes=encoded_bytes,
                metadata_operation_file_reads=len(operation_reads),
                automatic_companion_saving=compact["companions"]["saving"],
                final_companion_settlement_seconds=time.perf_counter() - settlement_begun,
            )
        )
    )


def test_manifest_sync_failure_adopts_known_bytes_and_retries_in_same_folder(app, monkeypatch):
    import color_measurement.exports as publisher

    state, _ = _complete(app, export=False)
    item = state["annotations"][0]
    sync = publisher._sync_directory
    folder = Path(state["folder"])
    prior = (folder / "batch.json").read_bytes()

    def denied(target):
        assert target == folder
        assert (folder / "batch.json").read_bytes() != prior
        raise OSError("directory synchronization denied")

    monkeypatch.setattr(publisher, "_sync_directory", denied)
    result = _annotate(app, item, name="Published before sync failure", notes="retained note")
    assert result["state"] == "failed"
    failed = _snapshot(app)
    assert "bytes were published" in failed["save_reason"]
    assert "synchronization is unconfirmed" in failed["save_reason"]
    assert app._manifest_bytes == (folder / "batch.json").read_bytes()
    assert not failed["annotations"][0]["saved"]
    assert failed["annotation_drafts"][0]["state"] == "unsaved"
    monkeypatch.setattr(publisher, "_sync_directory", sync)
    draft = failed["draft"]
    edit = _command(
        app,
        "edit_draft",
        dict(
            version=draft["version"],
            name="Next draft after failed sync",
            notes="",
            options={},
            draft_id=draft["id"],
            number_samples=False,
        ),
    )
    assert edit["admission"] == "admitted"
    assert _command(app, "retry_save")["admission"] == "admitted"
    recovered = _snapshot(app)
    assert recovered["folder"] == str(folder) and not recovered["save_reason"]
    assert recovered["annotations"][0]["saved"]
    assert recovered["annotation_drafts"][0]["state"] == "saved"
    assert review_saved(folder)["manifest"]["draft"]["name"] == "Next draft after failed sync"


@pytest.mark.parametrize(
    "boundary",
    [
        "prepare",
        "before_dispatch",
        "after_dispatch",
        "drainer_construct",
        "drainer_start",
        "drainer_start_after_launch",
    ],
)
def test_startup_failures_keep_truthful_no_dispatch_and_cleanup_ownership(
    app, monkeypatch, boundary
):
    prepare = app._frontend.prepare
    held = []

    def changed(text):
        if boundary == "prepare":
            raise RuntimeError("Preparation refused without dispatch")
        operation = prepare(text)
        held.append(operation)
        dispatch = operation.dispatch
        if boundary in {"before_dispatch", "after_dispatch"}:

            def failed_dispatch():
                if boundary == "after_dispatch":
                    dispatch()
                raise RuntimeError(f"fault {boundary}")

            operation.dispatch = failed_dispatch
        return operation

    monkeypatch.setattr(app._frontend, "prepare", changed)
    if boundary == "drainer_construct":
        monkeypatch.setattr(
            threading,
            "Thread",
            lambda **_: (_ for _ in ()).throw(
                RuntimeError("Application drainer construction refused")
            ),
        )
    elif boundary in {"drainer_start", "drainer_start_after_launch"}:
        start = threading.Thread.start

        def refused(thread):
            if not thread.name.startswith("batch-"):
                return start(thread)
            if boundary == "drainer_start_after_launch":
                start(thread)
            raise RuntimeError(f"Application drainer fault {boundary}")

        monkeypatch.setattr(threading.Thread, "start", refused)
    admitted = _acquire(app)
    state, _ = _wait(app, lambda state: state["phase"] == "idle", respond=True)
    assert not application._UNCERTAIN_OWNERS and not state["cleanup_reason"]
    assert not state["sample_count"]
    if boundary == "prepare":
        assert admitted["admission"] == "refused" and not held
        assert state["operations"] == [] and state["can_measure"]
    else:
        assert held[0].done
        result = app.operation_details(state["operations"][-1]["command_id"])["result"]
        assert result["software_stopped"]
        assert result["status"] in {"cancelled", "failed"}
        assert result["operation_id"] == admitted["operation_id"]
        if boundary not in {"after_dispatch", "drainer_start_after_launch"}:
            assert held[0]._thread is None
    assert state["can_finish"]


@pytest.mark.parametrize("confirmed_stop", [False, True])
def test_missing_terminal_runtime_error_preserves_stop_facts_and_raw_evidence(
    app, monkeypatch, confirmed_stop
):
    prepare = app._frontend.prepare

    class MissingTerminal:
        def __init__(self, operation):
            self.operation = operation

        def __getattr__(self, name):
            return getattr(self.operation, name)

        def poll_event_json(self):
            text = self.operation.poll_event_json()
            if text:
                event = json.loads(text)
                if event["kind"] == "terminal":
                    event["payload"]["software_stopped"] = confirmed_stop
                    return json.dumps(event)
            return text

        def result_json(self):
            raise RuntimeError("Terminal result was not published")

    monkeypatch.setattr(app._frontend, "prepare", lambda text: MissingTerminal(prepare(text)))
    _acquire(app)
    phase = "idle" if confirmed_stop else "cleanup_uncertain"
    state, _ = _wait(app, lambda state: state["phase"] == phase, respond=True)
    details = app.operation_details(state["operations"][-1]["command_id"])
    evidence = details["recovery_result"]
    assert evidence["result_json"] == "" and evidence["software_stopped"] is confirmed_stop
    assert "RuntimeError: Terminal result was not published" in evidence["reason"]
    assert state["can_finish"] is confirmed_stop
    assert bool(application._UNCERTAIN_OWNERS) is not confirmed_stop
    assert state["cleanup_reason"] == (
        None if confirmed_stop else "Software stop is unconfirmed; retain operation ownership"
    )
    assert _command(app, "retry_save")["admission"] == "admitted"
    review = review_saved(state["folder"])
    assert review["operations"][-1]["recovery_result"] == evidence


def test_transient_intent_failure_has_no_false_cleanup_or_save_block_after_cancel_is_saved(
    app, monkeypatch
):
    publish = application._publish_owned_file
    attempts = []

    def transient(path, payload, previous):
        if path.name == "batch.json":
            attempts.append(payload)
            if len(attempts) == 1:
                raise OSError("first intent publication refused")
        return publish(path, payload, previous)

    monkeypatch.setattr(application, "_publish_owned_file", transient)
    result = _acquire(app)
    assert result["admission"] == "refused"
    assert "first intent publication refused" in result["reason"]
    state = _snapshot(app)
    assert state["phase"] == "idle" and state["can_measure"] and state["can_finish"]
    assert not state["save_reason"] and not application._UNCERTAIN_OWNERS
    assert state["operations"][0]["result"]["status"] == "cancelled"
    assert state["operations"][0]["result"]["software_stopped"]
    assert review_saved(state["folder"])["sample_count"] == 0


def test_annotation_details_keep_exact_long_text_and_bounded_rebind_snapshots(app, monkeypatch):
    state, _ = _complete(app, export=False)
    item = state["annotations"][0]
    notes = "\n\n" + ("測定 long Unicode note\n" * 1000)
    assert app.annotation_details(item["operation_id"], item["observation_id"])["draft"] is None
    native = app.reading_details(item["operation_id"], item["observation_id"])
    _annotate(app, item, name="Applied name", notes=notes)
    _automatic_companion_state(app)
    applied = app.annotation_details(item["operation_id"], item["observation_id"])["applied"]
    draft_notes = notes + "Editing suffix"
    key = {field: applied[field] for field in ("operation_id", "observation_id", "version")}
    _command(
        app,
        "edit_annotation_draft",
        dict(
            key,
            draft_version=1,
            name="Editing name",
            notes=draft_notes,
        ),
    )
    rebound = app.bind()
    annotation, draft = rebound["annotations"][0], rebound["annotation_drafts"][0]
    assert annotation["notes_preview"] == notes[:256] and annotation["notes_length"] == len(notes)
    assert draft["notes_preview"] == draft_notes[:256] and draft["notes_length"] == len(draft_notes)
    assert "notes" not in annotation and "notes" not in draft
    assert draft["draft_version"] == 2 and draft["version"] == applied["version"]
    assert draft["dirty"] and draft["state"] == "editing"
    before = app.snapshot()

    def forbidden(*args, **kwargs):
        pytest.fail("Metadata detail query attempted I/O, acquisition or publication")

    with monkeypatch.context() as guard:
        guard.setattr(application, "_read_regular", forbidden)
        guard.setattr(application, "_publish_owned_file", forbidden)
        guard.setattr(app._frontend, "prepare", forbidden)
        details = app.annotation_details(item["operation_id"], item["observation_id"])
        assert details["applied"]["notes"] == notes and details["draft"]["notes"] == draft_notes
        assert details["draft"]["state"] == "editing"
        details["applied"]["notes"] = "caller changed copy"
        details["draft"]["name"] = "caller changed draft copy"
        exact = app.annotation_details(item["operation_id"], item["observation_id"])
        assert exact["applied"]["notes"] == notes and exact["draft"]["name"] == "Editing name"
        for operation_id, observation_id in [
            ("not-a-uuid", item["observation_id"]),
            (str(uuid4()), item["observation_id"]),
            (item["operation_id"], "missing"),
            (None, None),
        ]:
            with pytest.raises(ValueError):
                app.annotation_details(operation_id, observation_id)
        assert app.snapshot() == before
    assert app.reading_details(item["operation_id"], item["observation_id"]) == native
    assert review_saved(state["folder"])["manifest"]["annotations"][0]["notes"] == notes
    discard = app.discard_summary()
    assert discard["annotation_drafts"][0]["notes"] == draft_notes
    discard["annotation_drafts"][0]["notes"] = "caller changed discard copy"
    assert app.discard_summary()["annotation_drafts"][0]["notes"] == draft_notes
    abandoned = _command(
        app,
        "abandon",
        dict(
            discard_token=app.discard_summary()["token"],
            reason="Reviewed synthetic editing draft",
        ),
    )
    assert abandoned["admission"] == "admitted" and abandoned["shutdown_saved"]
    discarded_preview = app.snapshot()["shutdown"]["discarded"]["annotation_drafts"][0]
    assert "notes" not in discarded_preview
    assert discarded_preview["notes_length"] == len(draft_notes)
    shutdown = review_saved(state["folder"])["manifest"]["shutdown"]
    assert shutdown["discarded"]["annotation_drafts"][0]["notes"] == draft_notes


class _HeldStartupOperation:
    """Prepared real envelope, synthetic possible-dispatch/stop boundaries only."""

    def __init__(self, operation):
        self.operation = operation
        self.dispatched = threading.Event()
        self.release_terminal = threading.Event()
        self.cancel_requested = False

    def __getattr__(self, name):
        return getattr(self.operation, name)

    def dispatch(self):
        self.dispatched.set()  # No frontend worker or native dispatch.

    def cancel(self, operation_id):
        self.cancel_requested = True
        if not self.dispatched.is_set():
            self.operation.cancel(operation_id)

    @property
    def done(self):
        if self.dispatched.is_set():
            if not self.release_terminal.is_set():
                return False
            self.operation.cancel(self.operation_id)
        return self.operation.done

    def poll_event_json(self):
        if self.dispatched.is_set() and not self.release_terminal.is_set():
            return ""
        if self.dispatched.is_set():
            self.operation.cancel(self.operation_id)
        return self.operation.poll_event_json()


class _StartupFault:
    """Actual application thread with controlled bootstrap/start-return faults."""

    def __init__(self, monkeypatch, app, kind, boundary, signal, entered, release):
        self.threads = []
        self.bootstrap = threading.Event()
        self.release_bootstrap = threading.Event()
        real_thread = threading.Thread
        fault = self

        def factory(**options):
            if not options.get("name", "").startswith(f"{kind}-"):
                return real_thread(**options)
            if boundary == "construct":
                raise signal("startup construction interrupt")
            target = options["target"]
            if boundary == "launched_unclaimed":

                def delayed():
                    fault.bootstrap.set()
                    assert fault.release_bootstrap.wait(5)
                    target()

                options["target"] = delayed
            thread = real_thread(**options)
            fault.threads.append(thread)
            start = thread.start

            def interrupted_start():
                if boundary == "before_launch":
                    raise signal("startup before-launch interrupt")
                start()
                if boundary == "launched_unclaimed":
                    assert fault.bootstrap.wait(5)
                elif boundary == "claimed":
                    assert entered.wait(5)
                elif boundary == "finished":
                    # Let the claimed callback finish before start raises. The
                    # command normally retains this mutex across startup.
                    app._mutex.release()
                    try:
                        release.set()
                        thread.join(5)
                        assert not thread.is_alive()
                    finally:
                        app._mutex.acquire()
                raise signal("startup after-launch interrupt")

            thread.start = interrupted_start
            return thread

        monkeypatch.setattr(threading, "Thread", factory)

    def finish(self):
        self.release_bootstrap.set()
        for thread in self.threads:
            if thread.ident is not None:
                thread.join(5)
                assert not thread.is_alive()


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("after_publication", [False, True])
def test_intent_control_signal_drains_same_handle_and_remembers_published_bytes(
    app, monkeypatch, signal, after_publication
):
    publication = application._publish_owned_file
    prepare = app._frontend.prepare
    held, injected = [], []

    def retain(text):
        operation = prepare(text)
        operation.dispatch = lambda: pytest.fail("Interrupted intent dispatched acquisition")
        held.append(operation)
        return operation

    def interrupted(path, payload, previous):
        if path.name == "batch.json" and not injected:
            injected.append(payload)
            if after_publication:
                publication(path, payload, previous)
            raise signal("intent publication interrupt")
        return publication(path, payload, previous)

    monkeypatch.setattr(app._frontend, "prepare", retain)
    monkeypatch.setattr(application, "_publish_owned_file", interrupted)
    command_id = str(uuid4())
    with pytest.raises(signal, match="intent publication interrupt"):
        _acquire(app, identity=command_id)
    state = _snapshot(app)
    assert state["phase"] == "idle" and state["can_finish"]
    assert held[0].done and state["operations"][0]["operation_id"] == held[0].operation_id
    assert state["operations"][0]["result"]["software_stopped"]
    assert state["sample_count"] == 0 and not application._UNCERTAIN_OWNERS
    outcome = app.command_outcome(command_id)
    assert outcome["state"] == "refused" and signal.__name__ in outcome["reason"]
    assert app._manifest_bytes == (Path(state["folder"]) / "batch.json").read_bytes()
    assert _command(app, "retry_save")["admission"] == "admitted"
    assert _command(app, "finish")["admission"] == "admitted"
    assert review_saved(state["folder"])["sample_count"] == 0


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit, RuntimeError])
@pytest.mark.parametrize(
    "boundary",
    [
        "construct",
        "before_launch",
        "launched_unclaimed",
        "claimed",
        "finished",
    ],
)
def test_drainer_startup_control_fault_keeps_one_callback_and_truthful_stop(
    app, monkeypatch, signal, boundary
):
    prepare, drain = app._frontend.prepare, app._run
    held, drains = [], []

    def retain(text):
        operation = _HeldStartupOperation(prepare(text))
        held.append(operation)
        return operation

    def counted(*args, **kwargs):
        drains.append(threading.current_thread().ident)
        return drain(*args, **kwargs)

    # The synthetic dispatch event proves the existing application callback
    # entered its possible-dispatch phase, without invoking any frontend worker.
    dispatched = threading.Event()
    release = threading.Event()

    def prepared(text):
        operation = retain(text)
        operation.dispatched, operation.release_terminal = dispatched, release
        return operation

    monkeypatch.setattr(app._frontend, "prepare", prepared)
    monkeypatch.setattr(app, "_run", counted)
    fault = _StartupFault(monkeypatch, app, "batch", boundary, signal, dispatched, release)
    command_id = str(uuid4())
    try:
        if issubclass(signal, Exception):
            _acquire(app, identity=command_id)
        else:
            with pytest.raises(signal):
                _acquire(app, identity=command_id)
        if boundary == "claimed":
            state, _ = _wait(app, lambda state: state["phase"] == "active")
            assert not state["can_finish"] and not held[0].done
            assert app._operation is held[0] and held[0].cancel_requested
            assert drains == [fault.threads[0].ident]
            assert len(drains) == 1
        release.set()
        fault.finish()
        state, _ = _wait(app, lambda state: state["phase"] == "idle")
        assert len(drains) == 1
        result = app.operation_details(command_id)["result"]
        assert result["operation_id"] == held[0].operation_id and result["software_stopped"]
        assert not result["patches"] and state["can_finish"]
        assert not application._UNCERTAIN_OWNERS
        assert signal.__name__ in app.command_outcome(command_id)["reason"]
        if boundary in {"construct", "before_launch", "launched_unclaimed"}:
            assert not dispatched.is_set()
    finally:
        release.set()
        fault.finish()


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit, RuntimeError])
@pytest.mark.parametrize(
    "boundary",
    [
        "construct",
        "before_launch",
        "launched_unclaimed",
        "claimed",
        "finished",
    ],
)
def test_metadata_startup_control_fault_keeps_single_publication_owner(
    app, monkeypatch, signal, boundary
):
    entered, release = threading.Event(), threading.Event()
    callbacks, publications = [], []
    writer, publish = app._metadata_worker, application._publish_owned_file

    def counted(*args):
        callbacks.append(threading.current_thread().ident)
        entered.set()
        assert release.wait(5)
        return writer(*args)

    def publication(path, payload, previous):
        if path.name == "batch.json":
            publications.append(payload)
        return publish(path, payload, previous)

    monkeypatch.setattr(app, "_metadata_worker", counted)
    monkeypatch.setattr(application, "_publish_owned_file", publication)
    fault = _StartupFault(monkeypatch, app, "metadata", boundary, signal, entered, release)
    command_id = str(uuid4())
    draft = app.snapshot()["draft"]
    payload = dict(
        version=draft["version"],
        name="Retained interrupted draft",
        notes="\n\nExact retained metadata",
        options={},
        draft_id=draft["id"],
        number_samples=False,
    )
    scope = app.snapshot()

    def send():
        return app.command(
            command_id, scope["generation"], "edit_draft", payload, batch_id=scope["batch_id"]
        )

    try:
        if issubclass(signal, Exception):
            send()
        else:
            with pytest.raises(signal):
                send()
        if boundary == "claimed":
            assert app.snapshot()["phase"] == "saving" and app._metadata_saving
            assert callbacks == [fault.threads[0].ident]
            assert not publications and len(callbacks) == 1
            assert _command(app, "retry_save")["admission"] == "refused"
        release.set()
        fault.finish()
        _wait(app, lambda state: state["phase"] == "idle")
        if boundary in {"construct", "before_launch", "launched_unclaimed"}:
            assert not callbacks and not publications
            assert app.snapshot()["save_reason"]
            assert app.command_outcome(command_id)["state"] == "failed"
            assert _command(app, "retry_save")["admission"] == "admitted"
        else:
            assert len(callbacks) == len(publications) == 1
            assert not app.snapshot()["save_reason"]
            assert app.command_outcome(command_id)["state"] == "completed"
        assert signal.__name__ in app.command_outcome(command_id)["reason"]
        saved = review_saved(app.snapshot()["folder"])["manifest"]["draft"]
        assert saved["name"] == payload["name"] and saved["notes"] == payload["notes"]
    finally:
        release.set()
        fault.finish()


def test_finish_published_before_directory_sync_failure_stays_finished(app, monkeypatch):
    from color_measurement import exports

    sync = exports._sync_directory
    failed = []

    def fail_after_finished(folder):
        manifest = json.loads((folder / "batch.json").read_text())
        if manifest["status"] == "finished" and not failed:
            failed.append(True)
            raise OSError("Finish directory synchronization failed")
        return sync(folder)

    monkeypatch.setattr(exports, "_sync_directory", fail_after_finished)
    outcome = _command(app, "finish")
    state = app.snapshot()
    assert outcome["state"] == "failed"
    assert state["status"] == "finished" and not state["can_measure"]
    assert "synchronization is unconfirmed" in state["save_reason"]
    before = (Path(state["folder"]) / "batch.json").read_bytes()
    assert review_saved(state["folder"])["manifest"]["status"] == "finished"
    assert _command(app, "retry_save")["state"] == "completed"
    assert (Path(state["folder"]) / "batch.json").read_bytes() == before
    assert app.snapshot()["status"] == "finished" and not app.snapshot()["save_reason"]


def test_control_signal_between_manifest_publication_and_acceptance_keeps_owned_bytes(
    app, monkeypatch
):
    accept = app._accept_manifest
    injected = []

    def interrupted(payload):
        if not injected:
            injected.append(True)
            raise KeyboardInterrupt("After publication, before acceptance")
        return accept(payload)

    monkeypatch.setattr(app, "_accept_manifest", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _acquire(app)
    state = app.snapshot()
    assert state["phase"] == "idle" and not state["cleanup_reason"]
    assert app._manifest_bytes == (Path(state["folder"]) / "batch.json").read_bytes()
    assert _command(app, "retry_save")["admission"] == "admitted"
    assert app.snapshot()["folder"] == state["folder"] and not app.snapshot()["save_reason"]


def test_unexpected_terminal_type_error_retains_failure_and_confirmed_stop(app, monkeypatch):
    prepare = app._frontend.prepare

    class BrokenResult:
        def __init__(self, operation):
            self.operation = operation

        def __getattr__(self, name):
            return getattr(self.operation, name)

        def result_json(self):
            raise TypeError("Unexpected terminal type")

    monkeypatch.setattr(app._frontend, "prepare", lambda text: BrokenResult(prepare(text)))
    command_id = str(uuid4())
    _acquire(app, identity=command_id)
    state, _ = _wait(app, lambda state: state["phase"] == "idle", respond=True)
    assert not state["cleanup_reason"] and state["can_finish"]
    details = app.operation_details(command_id)
    assert "TypeError: Unexpected terminal type" in details["reason"]
    assert details["state"] == "terminal_unknown" and not application._UNCERTAIN_OWNERS


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
def test_finish_control_signal_keeps_published_status_and_sync_recovery(app, monkeypatch, signal):
    from color_measurement import exports

    sync = exports._sync_directory
    injected = []
    identity = str(uuid4())

    def interrupted(folder):
        if json.loads((folder / "batch.json").read_text())["status"] == "finished" and not injected:
            injected.append(True)
            raise signal("Finish synchronization interrupted")
        return sync(folder)

    monkeypatch.setattr(exports, "_sync_directory", interrupted)
    with pytest.raises(signal):
        _command(app, "finish", identity=identity)
    state = app.snapshot()
    assert state["status"] == "finished" and state["save_reason"]
    assert app.command_outcome(identity)["admission"] == "admitted"
    assert app.command_outcome(identity)["state"] == "failed"
    with pytest.raises(ValueError):
        _demo_new_batch(app, Path(state["folder"]).with_name("new-batch"))
    before = (Path(state["folder"]) / "batch.json").read_bytes()
    assert _command(app, "retry_save")["state"] == "completed"
    assert (Path(state["folder"]) / "batch.json").read_bytes() == before
    assert not app.snapshot()["save_reason"]


def test_failed_annotation_publisher_start_retains_unsaved_applied_edit(app, monkeypatch):
    _complete(app, export=False)
    item = _snapshot(app)["annotations"][0]
    thread = threading.Thread

    def fail(**options):
        if options.get("name", "").startswith("metadata-"):
            raise RuntimeError("Metadata construction refused")
        return thread(**options)

    monkeypatch.setattr(threading, "Thread", fail)
    outcome = _annotate(app, item, name="Applied retained name", notes="Retained exact notes")
    state = _snapshot(app)
    assert outcome["admission"] == "admitted" and outcome["state"] == "failed"
    assert state["annotations"][0]["name"] == "Applied retained name"
    assert state["annotation_drafts"][0]["state"] == "unsaved"
    assert state["annotation_drafts"][0]["reason"]


@pytest.mark.parametrize("state", ["reserved", "terminal_unknown"])
def test_every_retained_intent_requires_prepared_operation_identity(app, state):
    # Tamper an actual current envelope instead of maintaining an obsolete fixture.
    _complete(app, export=False)
    data = json.loads(app._manifest_bytes)
    entry = data["operations"][0]
    entry.update(
        state=state,
        operation_id=None,
        sha256=None,
        reason="Unknown terminal" if state == "terminal_unknown" else None,
    )
    data["annotations"] = []
    with pytest.raises(ValueError, match="UUID"):
        application._manifest(json.dumps(data).encode())


def test_ordinary_sync_limitations_are_visible_and_bound_to_current_bytes(app, monkeypatch):
    import errno
    import types

    try:
        import fcntl
    except ImportError:
        # Exercise the macOS full-sync fallback on Windows without acquiring
        # POSIX locks; publication locking keeps using the actual platform.
        fcntl = types.SimpleNamespace(fcntl=lambda *_: None)
        monkeypatch.setitem(sys.modules, "fcntl", fcntl)
    original = fcntl.fcntl
    full_sync = getattr(fcntl, "F_FULLFSYNC", 51)
    monkeypatch.setattr(fcntl, "F_FULLFSYNC", full_sync, raising=False)

    def unsupported(descriptor, command, *arguments):
        if command == full_sync:
            raise OSError(errno.EOPNOTSUPP, "Full sync unsupported")
        return original(descriptor, command, *arguments)

    monkeypatch.setattr(fcntl, "fcntl", unsupported)
    monkeypatch.setattr(exports.sys, "platform", "darwin")
    draft = app.snapshot()["draft"]
    assert (
        _command(
            app,
            "edit_draft",
            dict(
                version=draft["version"],
                name="Changed synchronized bytes",
                notes="",
                options={},
                draft_id=draft["id"],
                number_samples=False,
            ),
        )["admission"]
        == "admitted"
    )
    state, _ = _wait(app, lambda item: item["phase"] == "idle")
    assert not state["save_reason"] and state["durability"]["status"] == "limited"
    manifest = next(
        item for item in state["durability"]["files"] if item["path"].endswith("batch.json")
    )
    assert manifest["file_sync"] == "fsync" and manifest["limitations"]
    assert manifest["sha256"] == hashlib.sha256(app._manifest_bytes).hexdigest()


@pytest.mark.parametrize("failure", [TypeError, KeyboardInterrupt])
def test_retention_view_failure_preserves_prior_readings_and_actual_stop(
    tmp_path, monkeypatch, failure
):
    owner = _demo_batch(tmp_path / "retention-fault")
    owner.bind()
    try:
        before, _ = _complete(owner, export=False)
        original = before["operations"][0]
        original_path = Path(before["folder"]) / original["path"]
        original_bytes = original_path.read_bytes()
        load, drain = application._load, owner._drain
        injected, drainers = [], []
        thread_errors, reported = [], threading.Event()

        def report_thread_error(arguments):
            thread_errors.append(arguments.exc_value)
            reported.set()

        def broken_view(payload):
            if isinstance(payload, bytes) and not injected:
                value = json.loads(payload)
                if "patches" in value and threading.current_thread().name.startswith("batch-"):
                    injected.append(True)
                    raise failure("Retained view construction failed")
            return load(payload)

        def counted_drain(*args, **kwargs):
            drainers.append(threading.current_thread().ident)
            return drain(*args, **kwargs)

        monkeypatch.setattr(application, "_load", broken_view)
        monkeypatch.setattr(owner, "_drain", counted_drain)
        monkeypatch.setattr(threading, "excepthook", report_thread_error)
        identity = str(uuid4())
        assert _acquire(owner, identity=identity)["admission"] == "admitted"
        state, _ = _wait(
            owner,
            lambda item: injected and (item["save_reason"] or item["phase"] == "idle"),
            respond=True,
        )
        assert state["phase"] == "idle" and state["can_finish"]
        assert not state["cleanup_reason"] and not application._UNCERTAIN_OWNERS
        assert len(drainers) == 1
        assert state["sample_count"] == state["accepted_count"] == state["saved_count"] == 1
        assert state["annotations"] == before["annotations"]
        assert original_path.read_bytes() == original_bytes
        details = owner.operation_details(identity)
        assert details["state"] == "terminal_unknown" and details["result"] is None
        assert failure.__name__ in details["reason"]
        recovery = details["recovery_result"]
        assert recovery["software_stopped"] is True
        raw = json.loads(recovery["result_json"])
        assert raw["operation_id"] == details["operation_id"] and len(raw["patches"]) == 1
        if not issubclass(failure, Exception):
            assert reported.wait(2)
            assert len(thread_errors) == 1 and isinstance(thread_errors[0], failure)
        else:
            assert thread_errors == []
        assert _command(owner, "retry_save")["state"] == "completed"
        reviewed = review_saved(state["folder"])
        assert reviewed["operations"][0]["review_state"] == "accepted"
        assert original_path.read_bytes() == original_bytes
    finally:
        state = owner.snapshot()
        if state["phase"] != "idle":
            _command(
                owner,
                "abandon",
                dict(
                    discard_token=owner.discard_summary()["token"],
                    reason="Retention-fault test owner cleanup",
                ),
            )
        else:
            _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


def test_already_terminal_prepared_handle_never_claims_dispatch_or_active(app, monkeypatch):
    prepare, persist = app._frontend.prepare, app._persist
    held, publications = [], []

    def cancelled(request):
        operation = prepare(request)
        operation.cancel(operation.operation_id)
        operation.dispatch = lambda: pytest.fail("Already-terminal handle was dispatched")
        held.append(operation)
        return operation

    def observed_persist():
        entry = app._data["operations"][-1]
        publications.append((entry["state"], app._commands[entry["command_id"]]["state"]))
        return persist()

    monkeypatch.setattr(app._frontend, "prepare", cancelled)
    monkeypatch.setattr(app, "_persist", observed_persist)
    identity = str(uuid4())
    assert _acquire(app, identity=identity)["admission"] == "admitted"
    state, _ = _wait(app, lambda item: item["phase"] == "idle")
    assert held[0].done and state["can_finish"] and not state["cleanup_reason"]
    assert all(entry != "started" and command != "active" for entry, command in publications)
    result = app.operation_details(identity)["result"]
    assert result["operation_id"] == held[0].operation_id
    assert result["status"] == "cancelled" and result["software_stopped"]
    assert result["patches"] == []


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
def test_synchronous_no_dispatch_retention_interrupt_propagates_once(app, monkeypatch, signal):
    parse, prepare = application.result_from_json, app._frontend.prepare
    real_thread = threading.Thread
    held, classified = [], []
    failed = app._drainer_failed

    def prepared(request):
        operation = prepare(request)
        held.append(operation)
        return operation

    def interrupted_parse(text):
        if held and json.loads(text).get("operation_id") == held[-1].operation_id:
            raise signal("Synchronous terminal retention interrupted")
        return parse(text)

    def refused_worker(**options):
        if options.get("name", "").startswith("batch-"):
            raise RuntimeError("Application drainer could not start")
        return real_thread(**options)

    def counted_failure(*args):
        classified.append(args[-1])
        return failed(*args)

    monkeypatch.setattr(app._frontend, "prepare", prepared)
    monkeypatch.setattr(application, "result_from_json", interrupted_parse)
    monkeypatch.setattr(threading, "Thread", refused_worker)
    monkeypatch.setattr(app, "_drainer_failed", counted_failure)
    identity = str(uuid4())
    with pytest.raises(signal, match="Synchronous terminal retention interrupted"):
        _acquire(app, identity=identity)
    state = app.snapshot()
    assert held[0].done and held[0]._thread is None
    assert state["phase"] == "idle" and state["can_finish"]
    assert not state["cleanup_reason"] and not application._UNCERTAIN_OWNERS
    assert classified == []
    details = app.operation_details(identity)
    assert details["state"] == "terminal_unknown"
    assert details["recovery_result"]["software_stopped"] is True
    assert details["recovery_result"]["result_json"] == held[0].result_json()
    assert signal.__name__ in app.command_outcome(identity)["reason"]


def test_application_callback_does_not_repeat_failed_classification(app, monkeypatch):
    handler = app._drainer_failed
    classified, thread_errors = [], []
    reported = threading.Event()

    def drain_failure(*args, **kwargs):
        raise TypeError("Drain callback failure")

    def handler_failure(*args):
        classified.append(args[-1])
        handler(*args)
        raise RuntimeError("Failure-report callback boundary")

    def report_thread_error(arguments):
        thread_errors.append(arguments.exc_value)
        reported.set()

    monkeypatch.setattr(app, "_drain", drain_failure)
    monkeypatch.setattr(app, "_drainer_failed", handler_failure)
    monkeypatch.setattr(threading, "excepthook", report_thread_error)
    identity = str(uuid4())
    assert _acquire(app, identity=identity)["admission"] == "admitted"
    assert reported.wait(2)
    state = app.snapshot()
    assert len(classified) == len(thread_errors) == 1
    assert isinstance(classified[0], TypeError) and isinstance(thread_errors[0], RuntimeError)
    assert state["phase"] == "cleanup_uncertain" and not state["software_stopped"]
    assert app.operation_details(identity)["state"] == "terminal_unknown"


def test_unsupported_directory_sync_does_not_resync_accepted_operations(app, monkeypatch):
    from color_measurement import exports

    sync_file = exports._sync_file
    operation_syncs = []

    def observed_sync(descriptor, path):
        if path.name.startswith("operation-"):
            operation_syncs.append(path)
        return sync_file(descriptor, path)

    monkeypatch.setattr(exports, "_sync_file", observed_sync)
    monkeypatch.setattr(exports, "_sync_directory", lambda folder: False)
    _complete(app, export=False)
    before = list(operation_syncs)
    assert len(before) == 1
    for _ in range(2):
        assert _command(app, "retry_save")["admission"] == "admitted"
    state = app.snapshot()
    assert operation_syncs == before
    assert state["saved_count"] == 1 and state["durability"]["status"] == "limited"
    assert not state["save_reason"]


def test_metadata_publication_blocks_close_and_abandon_with_current_token(app, monkeypatch):
    state, _ = _complete(app, export=False)
    item = state["annotations"][0]
    key = {field: item[field] for field in ("operation_id", "observation_id", "version")}
    assert _command(app, "edit_annotation_draft", dict(
        key, draft_version=0, name="Pending publication", notes="Retained while writing",
    ))["admission"] == "admitted"
    entered, release = threading.Event(), threading.Event()
    publish, lock = application._publish_owned_file, app._lock

    def blocked_publish(path, payload, previous):
        if path.name == "batch.json":
            entered.set()
            assert release.wait(5)
        return publish(path, payload, previous)

    monkeypatch.setattr(application, "_publish_owned_file", blocked_publish)
    scope = app.snapshot()
    identity = str(uuid4())
    try:
        outcome = app.command(identity, scope["generation"], "annotate",
            dict(key, draft_version=1), batch_id=scope["batch_id"])
        assert outcome["admission"] == "admitted" and entered.wait(2)
        for action in ("close", "abandon"):
            payload = dict(discard_token=app.discard_summary()["token"])
            if action == "abandon":
                payload["reason"] = "Attempted while metadata writer owns publication"
            refusal = _command(app, action, payload)
            assert refusal["admission"] == "refused"
            assert refusal["reason"] == "Metadata publication is pending"
        assert app._lock is lock and not app._closed and app._metadata_saving
        with pytest.raises(OSError):
            with _folder_lock(Path(scope["folder"]), ".batch.lock"):
                pass
    finally:
        release.set()
        _wait(app, lambda item: item["phase"] == "idle")
    assert app.command_outcome(identity)["state"] == "completed"


@pytest.mark.parametrize("boundary, retention_failure, publication_failure, after_publication", [
    ("parse", TypeError, KeyboardInterrupt, False),
    ("view", TypeError, SystemExit, True),
    ("view", TypeError, TypeError, True),
    ("parse", KeyboardInterrupt, SystemExit, True),
])
def test_terminal_recovery_publication_failure_keeps_raw_result_and_same_folder_retry(
    app, monkeypatch, boundary, retention_failure, publication_failure, after_publication
):
    before, _ = _complete(app, export=False)
    original_path = Path(before["folder"]) / before["operations"][0]["path"]
    original_bytes = original_path.read_bytes()
    prepare, parse, load = app._frontend.prepare, application.result_from_json, application._load
    publish, drain, handler = application._publish_owned_file, app._drain, app._drainer_failed
    identity = str(uuid4())
    recovery_path = Path(before["folder"]) / f"recovery-{identity}.json"
    retention_error = retention_failure("Terminal construction fault")
    publication_error = publication_failure("Recovery publication fault")
    held, injected, publications, published = [], [], [], []
    drains, classified, thread_errors = [], [], []
    reported = threading.Event()

    def prepared(request):
        operation = prepare(request)
        held.append(operation)
        return operation

    def matches(payload):
        return held and json.loads(payload).get("operation_id") == held[-1].operation_id

    def broken_parse(payload):
        if boundary == "parse" and not injected and matches(payload):
            injected.append(True)
            raise retention_error
        return parse(payload)

    def broken_view(payload):
        if boundary == "view" and isinstance(payload, bytes) and not injected and matches(payload):
            injected.append(True)
            raise retention_error
        return load(payload)

    def interrupted_publication(path, payload, previous):
        if path == recovery_path and not publications:
            publications.append(payload)
            if after_publication:
                result = publish(path, payload, previous)
                published.append(result)
                publication_error.publication = result
            raise publication_error
        return publish(path, payload, previous)

    def counted_drain(*args, **kwargs):
        drains.append(threading.current_thread().ident)
        return drain(*args, **kwargs)

    def counted_classification(*args):
        classified.append(args[-1])
        return handler(*args)

    def report_thread_error(arguments):
        thread_errors.append(arguments.exc_value)
        reported.set()

    monkeypatch.setattr(app._frontend, "prepare", prepared)
    monkeypatch.setattr(application, "result_from_json", broken_parse)
    monkeypatch.setattr(application, "_load", broken_view)
    monkeypatch.setattr(application, "_publish_owned_file", interrupted_publication)
    monkeypatch.setattr(app, "_drain", counted_drain)
    monkeypatch.setattr(app, "_drainer_failed", counted_classification)
    monkeypatch.setattr(threading, "excepthook", report_thread_error)
    assert _acquire(app, identity=identity)["admission"] == "admitted"
    state, _ = _wait(
        app, lambda item: item["phase"] == "idle" and item["save_reason"] is not None,
        respond=True,
    )
    assert state["phase"] == "idle" and not state["can_finish"] and state["software_stopped"]
    assert not state["cleanup_reason"] and not application._UNCERTAIN_OWNERS
    assert len(drains) == len(injected) == len(publications) == 1 and classified == []
    assert state["sample_count"] == state["accepted_count"] == state["saved_count"] == 1
    assert state["annotations"] == before["annotations"]
    assert original_path.read_bytes() == original_bytes
    details = app.operation_details(identity)
    evidence = details["recovery_result"]
    assert details["state"] == "terminal_unknown" and details["result"] is None
    assert evidence["result_json"] == held[0].result_json()
    assert evidence["reason"] == details["reason"] == app.command_outcome(identity)["reason"]
    assert evidence["reason"] == (
        f"Terminal retention failed: {type(retention_error).__name__}: {retention_error}"
    )
    assert evidence["software_stopped"] is True
    assert json.loads(publications[0]) == evidence
    assert publication_failure.__name__ in state["save_reason"]
    expected_control = (
        retention_error if not isinstance(retention_error, Exception)
        else publication_error if not isinstance(publication_error, Exception) else None
    )
    if expected_control is not None:
        assert reported.wait(2) and thread_errors == [expected_control]
    else:
        assert thread_errors == []
    if after_publication:
        assert recovery_path.read_bytes() == publications[0]
        receipt = app._publications[str(recovery_path)]
        assert receipt["sha256"] == hashlib.sha256(publications[0]).hexdigest()
        assert receipt["file_sync"] == published[0].file_sync
        assert receipt["directory_synced"] == published[0].directory_synced
    else:
        assert not recovery_path.exists()
    assert _command(app, "retry_save")["state"] == "completed"
    assert app.snapshot()["folder"] == before["folder"] and not app.snapshot()["save_reason"]
    assert app.snapshot()["can_finish"]
    assert app.operation_details(identity)["recovery_result"] == evidence
    assert recovery_path.read_bytes() == publications[0] and classified == []
    reviewed = review_saved(before["folder"])
    assert reviewed["operations"][0]["review_state"] == "accepted"
    assert reviewed["operations"][-1]["review_state"] == "terminal_unknown"
    assert reviewed["operations"][-1]["recovery_result"] == evidence
    assert original_path.read_bytes() == original_bytes


@pytest.mark.parametrize("drain_failure, cancel_failure, save_failure, first_control", [
    (TypeError, KeyboardInterrupt, SystemExit, "cancel"),
    (TypeError, SystemExit, KeyboardInterrupt, "cancel"),
    (KeyboardInterrupt, SystemExit, TypeError, "drain"),
    (SystemExit, KeyboardInterrupt, SystemExit, "drain"),
    (TypeError, RuntimeError, SystemExit, "save"),
])
def test_drainer_recovery_propagates_first_control_after_cancel_and_save_bookkeeping(
    app, monkeypatch, drain_failure, cancel_failure, save_failure, first_control
):
    prepare, publish = app._frontend.prepare, application._publish_owned_file
    handler = app._drainer_failed
    identity = str(uuid4())
    recovery_path = Path(app.snapshot()["folder"]) / f"recovery-{identity}.json"
    errors = dict(
        drain=drain_failure("Unexpected drainer fault"),
        cancel=cancel_failure("Cancellation control fault"),
        save=save_failure("Recovery publication control fault"),
    )
    held, drains, cancellations, classifications = [], [], [], []
    publications, published, thread_errors = [], [], []
    reported = threading.Event()

    def prepared(request):
        operation = prepare(request)
        cancel = operation.cancel

        def interrupted_cancel(operation_id):
            cancellations.append(operation_id)
            cancel(operation_id)
            raise errors["cancel"]

        operation.cancel = interrupted_cancel
        held.append(operation)
        return operation

    def broken_drain(*args, **kwargs):
        drains.append(threading.current_thread().ident)
        raise errors["drain"]

    def counted_classification(*args):
        classifications.append(args[-1])
        return handler(*args)

    def interrupted_publication(path, payload, previous):
        if path == recovery_path and not publications:
            publications.append(payload)
            result = publish(path, payload, previous)
            published.append(result)
            errors["save"].publication = result
            raise errors["save"]
        return publish(path, payload, previous)

    def report_thread_error(arguments):
        thread_errors.append(arguments.exc_value)
        reported.set()

    monkeypatch.setattr(app._frontend, "prepare", prepared)
    monkeypatch.setattr(app, "_drain", broken_drain)
    monkeypatch.setattr(app, "_drainer_failed", counted_classification)
    monkeypatch.setattr(application, "_publish_owned_file", interrupted_publication)
    monkeypatch.setattr(threading, "excepthook", report_thread_error)
    assert _acquire(app, identity=identity)["admission"] == "admitted"
    assert reported.wait(2) and thread_errors == [errors[first_control]]
    state = app.snapshot()
    assert state["phase"] == "cleanup_uncertain" and not state["software_stopped"]
    assert app in application._UNCERTAIN_OWNERS and app._operation is held[0]
    assert held[0].done and len(drains) == len(cancellations) == len(publications) == 1
    assert classifications == [errors["drain"]]
    evidence = app.operation_details(identity)["recovery_result"]
    assert evidence["result_json"] == "" and evidence["software_stopped"] is None
    assert evidence["reason"] == (
        f"Application draining failed: {type(errors['drain']).__name__}: {errors['drain']}; "
        f"cancellation failed: {type(errors['cancel']).__name__}: {errors['cancel']}"
    )
    assert json.loads(publications[0]) == evidence and recovery_path.read_bytes() == publications[0]
    assert save_failure.__name__ in state["save_reason"]
    receipt = app._publications[str(recovery_path)]
    assert receipt["sha256"] == hashlib.sha256(publications[0]).hexdigest()
    assert receipt["file_sync"] == published[0].file_sync
    assert receipt["directory_synced"] == published[0].directory_synced
    assert _command(app, "retry_save")["state"] == "completed"
    assert recovery_path.read_bytes() == publications[0]
    assert app.operation_details(identity)["recovery_result"] == evidence
    assert classifications == [errors["drain"]] and thread_errors == [errors[first_control]]


@pytest.mark.parametrize("failure", [KeyboardInterrupt, SystemExit, TypeError])
def test_retained_result_publication_interrupt_keeps_sync_receipt_and_retry_bytes(
    app, monkeypatch, failure
):
    from color_measurement import exports

    identity = str(uuid4())
    folder = Path(app.snapshot()["folder"])
    path = folder / f"operation-{identity}.json"
    sync, handler = exports._sync_directory, app._drainer_failed
    error = failure("Canonical directory synchronization interrupted")
    injected, classified, thread_errors = [], [], []
    reported = threading.Event()

    def interrupted_sync(directory):
        if path.exists() and not injected:
            injected.append(True)
            raise error
        return sync(directory)

    def counted_classification(*args):
        classified.append(args[-1])
        return handler(*args)

    def report_thread_error(arguments):
        thread_errors.append(arguments.exc_value)
        reported.set()

    monkeypatch.setattr(exports, "_sync_directory", interrupted_sync)
    monkeypatch.setattr(app, "_drainer_failed", counted_classification)
    monkeypatch.setattr(threading, "excepthook", report_thread_error)
    assert _acquire(app, identity=identity)["admission"] == "admitted"
    state, _ = _wait(
        app, lambda item: item["phase"] == "idle" and item["save_reason"] is not None,
        respond=True,
    )
    assert len(injected) == 1 and classified == []
    assert state["phase"] == "idle" and state["software_stopped"]
    assert not state["can_finish"] and not state["cleanup_reason"]
    assert state["sample_count"] == state["accepted_count"] == 1 and state["saved_count"] == 0
    details = app.operation_details(identity)
    assert details["result"] is not None and details["recovery_result"] is None
    canonical = path.read_bytes()
    assert json.loads(canonical) == details["result"]
    receipt = next(item for item in state["durability"]["files"] if item["path"] == str(path))
    assert receipt["sha256"] == hashlib.sha256(canonical).hexdigest()
    assert receipt["file_sync"] == error.publication.file_sync
    assert receipt["directory_synced"] is False and state["durability"]["status"] == "limited"
    assert failure.__name__ in state["save_reason"]
    if not isinstance(error, Exception):
        assert reported.wait(2) and thread_errors == [error]
    else:
        assert thread_errors == []
    assert _command(app, "retry_save")["state"] == "completed"
    after = app.snapshot()
    assert not after["save_reason"] and after["can_finish"] and after["saved_count"] == 1
    assert path.read_bytes() == canonical and classified == []
    assert app.operation_details(identity)["result"] == details["result"]
    assert review_saved(folder)["operations"][0]["review_state"] == "accepted"


@pytest.mark.parametrize("signal, cancel_failure", [
    (KeyboardInterrupt, RuntimeError), (KeyboardInterrupt, SystemExit),
    (SystemExit, KeyboardInterrupt),
])
def test_dispatch_first_control_survives_cancellation_and_retained_publication(
    app, monkeypatch, signal, cancel_failure
):
    from color_measurement import exports

    prepare, sync = app._frontend.prepare, exports._sync_directory
    identity = str(uuid4())
    path = Path(app.snapshot()["folder"]) / f"operation-{identity}.json"
    first = signal("First dispatch interruption")
    later = SystemExit("Later publication interruption")
    held, cancellations, publications, thread_errors = [], [], [], []
    reported = threading.Event()

    def prepared(request):
        operation = prepare(request)
        cancel = operation.cancel

        def interrupted_dispatch():
            cancel(operation.operation_id)
            raise first

        def interrupted_cancel(operation_id):
            cancellations.append(operation_id)
            cancel(operation_id)
            raise cancel_failure("Later cancellation failure")

        operation.dispatch, operation.cancel = interrupted_dispatch, interrupted_cancel
        held.append(operation)
        return operation

    def interrupted_sync(directory):
        if path.exists() and not publications:
            publications.append(path.read_bytes())
            raise later
        return sync(directory)

    def report_thread_error(arguments):
        thread_errors.append(arguments.exc_value)
        reported.set()

    monkeypatch.setattr(app._frontend, "prepare", prepared)
    monkeypatch.setattr(exports, "_sync_directory", interrupted_sync)
    monkeypatch.setattr(threading, "excepthook", report_thread_error)
    assert _acquire(app, identity=identity)["admission"] == "admitted"
    assert reported.wait(2) and thread_errors == [first]
    state = app.snapshot()
    details = app.operation_details(identity)
    assert held[0].done and held[0]._thread is None
    assert len(cancellations) == len(publications) == 1
    assert state["phase"] == "idle" and state["software_stopped"]
    assert not state["cleanup_reason"] and not application._UNCERTAIN_OWNERS
    assert details["result"]["status"] == "cancelled" and details["recovery_result"] is None
    diagnostic = next(event for event in state["events"]
                      if event["kind"] == "application_dispatch_failed")
    assert signal.__name__ in diagnostic["payload"]["reason"]
    assert cancel_failure.__name__ in diagnostic["payload"]["reason"]
    receipt = next(item for item in state["durability"]["files"] if item["path"] == str(path))
    assert receipt["sha256"] == hashlib.sha256(publications[0]).hexdigest()
    assert receipt["directory_synced"] is False
    assert _command(app, "retry_save")["state"] == "completed"
    assert path.read_bytes() == publications[0] and thread_errors == [first]


@pytest.mark.parametrize("boundary, original_failure, cancel_failure", [
    ("reservation", KeyboardInterrupt, SystemExit),
    ("startup", SystemExit, RuntimeError),
    ("reservation", RuntimeError, KeyboardInterrupt),
    ("startup", RuntimeError, SystemExit),
])
def test_startup_first_control_survives_cancel_and_terminal_recovery(
    app, monkeypatch, boundary, original_failure, cancel_failure
):
    prepare, persist, parse = app._frontend.prepare, app._persist, application.result_from_json
    publish = application._publish_owned_file
    identity = str(uuid4())
    folder = Path(app.snapshot()["folder"])
    recovery_path = folder / f"recovery-{identity}.json"
    original = original_failure("First startup failure")
    cancellation = cancel_failure("Later cancellation failure")
    terminal = KeyboardInterrupt("Later terminal retention interruption")
    publication = SystemExit("Later recovery publication interruption")
    expected = original if not isinstance(original, Exception) else cancellation
    held, cancelled, startup_faults, parse_faults, published = [], [], [], [], []

    def prepared(request):
        operation = prepare(request)
        cancel = operation.cancel

        def interrupted_cancel(operation_id):
            cancelled.append(operation_id)
            cancel(operation_id)
            raise cancellation

        operation.cancel = interrupted_cancel
        operation.dispatch = lambda: pytest.fail("Startup failure dispatched its prepared handle")
        held.append(operation)
        return operation

    def failed_reservation():
        if not startup_faults:
            startup_faults.append(True)
            raise original
        return persist()

    start = application._ApplicationWorker.start

    def failed_start(self):
        if not self._name.startswith("batch-"):
            return start(self)
        startup_faults.append(True)
        raise original

    def interrupted_parse(payload):
        if (held and json.loads(payload).get("operation_id") == held[0].operation_id
                and not parse_faults):
            parse_faults.append(True)
            raise terminal
        return parse(payload)

    def interrupted_publication(path, payload, previous):
        if path == recovery_path and not published:
            published.append(payload)
            publication.publication = publish(path, payload, previous)
            raise publication
        return publish(path, payload, previous)

    monkeypatch.setattr(app._frontend, "prepare", prepared)
    monkeypatch.setattr(application, "result_from_json", interrupted_parse)
    monkeypatch.setattr(application, "_publish_owned_file", interrupted_publication)
    if boundary == "reservation":
        monkeypatch.setattr(app, "_persist", failed_reservation)
    else:
        monkeypatch.setattr(application._ApplicationWorker, "start", failed_start)
    with pytest.raises(type(expected)) as raised:
        _acquire(app, identity=identity)
    assert raised.value is expected
    assert len(startup_faults) == len(cancelled) == len(parse_faults) == len(published) == 1
    assert held[0].done and held[0]._thread is None
    state = app.snapshot()
    assert state["phase"] == "idle" and state["software_stopped"]
    assert not state["cleanup_reason"] and not application._UNCERTAIN_OWNERS
    evidence = app.operation_details(identity)["recovery_result"]
    assert evidence["result_json"] == held[0].result_json() and evidence["software_stopped"] is True
    assert str(terminal) in evidence["reason"]
    assert str(original) in app.command_outcome(identity)["reason"]
    assert str(cancellation) in app.command_outcome(identity)["reason"]
    assert recovery_path.read_bytes() == published[0]
    assert _command(app, "retry_save")["state"] == "completed"
    assert recovery_path.read_bytes() == published[0]
    assert app.operation_details(identity)["recovery_result"] == evidence


@pytest.mark.parametrize("boundary", ["reservation", "startup", "dispatch"])
def test_first_control_before_cancel_effect_quarantines_same_unfinished_handle(
    app, monkeypatch, boundary
):
    prepare, persist = app._frontend.prepare, app._persist
    first, later = KeyboardInterrupt("First failure"), SystemExit("Cancel did not take effect")
    held, cancellations, thread_errors = [], [], []
    reported = threading.Event()

    def prepared(request):
        operation = prepare(request)
        held.append((operation, operation.cancel))

        def interrupted_cancel(operation_id):
            cancellations.append(operation_id)
            raise later

        def interrupted_dispatch():
            raise first

        operation.cancel, operation.dispatch = interrupted_cancel, interrupted_dispatch
        return operation

    def interrupted_start(self):
        raise first

    def interrupted_reservation():
        raise first

    def report_thread_error(arguments):
        thread_errors.append(arguments.exc_value)
        reported.set()

    monkeypatch.setattr(app._frontend, "prepare", prepared)
    monkeypatch.setattr(threading, "excepthook", report_thread_error)
    identity = str(uuid4())
    try:
        if boundary == "reservation":
            monkeypatch.setattr(app, "_persist", interrupted_reservation)
        elif boundary == "startup":
            monkeypatch.setattr(application._ApplicationWorker, "start", interrupted_start)
        if boundary == "dispatch":
            assert _acquire(app, identity=identity)["admission"] == "admitted"
            assert reported.wait(2) and thread_errors == [first]
        else:
            with pytest.raises(KeyboardInterrupt) as raised:
                _acquire(app, identity=identity)
            assert raised.value is first
        operation = held[0][0]
        state = app.snapshot()
        assert cancellations == [operation.operation_id]
        assert app._operation is operation and not operation.done and operation._thread is None
        assert state["phase"] == "cleanup_uncertain" and not state["software_stopped"]
        assert app in application._UNCERTAIN_OWNERS and not state["can_measure"]
        evidence = app.operation_details(identity)["recovery_result"]
        assert evidence["result_json"] == "" and evidence["software_stopped"] is None
        assert str(first) in evidence["reason"] and str(later) in evidence["reason"]
    finally:
        # Only the test restores its injected cancel failure. Production retains
        # the unfinished handle and blocks reuse until its real owner resolves it.
        if held:
            operation, cancel = held[0]
            operation.cancel = cancel
            cancel(operation.operation_id)
        if boundary == "reservation":
            monkeypatch.setattr(app, "_persist", persist)


@pytest.mark.parametrize("boundary, first_signal, later_failure", [
    ("poll", KeyboardInterrupt, TypeError), ("poll", SystemExit, KeyboardInterrupt),
    ("record", KeyboardInterrupt, TypeError), ("record", SystemExit, KeyboardInterrupt),
    ("active_intent", KeyboardInterrupt, TypeError),
    ("active_intent", SystemExit, KeyboardInterrupt),
])
def test_dispatch_first_control_survives_later_poll_record_and_intent_failure(
    app, monkeypatch, boundary, first_signal, later_failure
):
    prepare, persist = app._frontend.prepare, app._persist
    record, handler = app._record_event, app._drainer_failed
    identity = str(uuid4())
    first = first_signal("Original dispatch control")
    later = later_failure(f"Later {boundary} failure")
    held, injected, classifications, thread_errors = [], [], [], []
    reported = threading.Event()

    def prepared(request):
        operation = prepare(request)
        cancel, poll = operation.cancel, operation.poll_event_json

        def interrupted_dispatch():
            cancel(operation.operation_id)
            raise first

        def broken_poll():
            if boundary == "poll" and not injected:
                injected.append(True)
                raise later
            return poll()

        operation.dispatch, operation.poll_event_json = interrupted_dispatch, broken_poll
        held.append(operation)
        return operation

    def broken_record(event, operation):
        if boundary == "record" and not injected:
            injected.append(True)
            raise later
        return record(event, operation)

    def broken_intent():
        if (boundary == "active_intent" and app._data["operations"]
                and app._data["operations"][-1]["state"] == "started" and not injected):
            injected.append(True)
            raise later
        return persist()

    def counted_classification(*args, **kwargs):
        classifications.append(args[-1])
        return handler(*args, **kwargs)

    def report_thread_error(arguments):
        thread_errors.append(arguments.exc_value)
        reported.set()

    monkeypatch.setattr(app._frontend, "prepare", prepared)
    monkeypatch.setattr(app, "_record_event", broken_record)
    monkeypatch.setattr(app, "_persist", broken_intent)
    monkeypatch.setattr(app, "_drainer_failed", counted_classification)
    monkeypatch.setattr(threading, "excepthook", report_thread_error)
    assert _acquire(app, identity=identity)["admission"] == "admitted"
    assert reported.wait(2) and thread_errors == [first]
    assert first.__cause__ is later and classifications == [first] and injected == [True]
    assert held[0].done and held[0]._thread is None
    state = app.snapshot()
    assert app._operation is held[0] and state["phase"] == "cleanup_uncertain"
    assert not state["software_stopped"] and app in application._UNCERTAIN_OWNERS
    evidence = app.operation_details(identity)["recovery_result"]
    assert evidence["result_json"] == "" and evidence["software_stopped"] is None
    assert str(first) in evidence["reason"] and str(later) in evidence["reason"]
    assert first_signal.__name__ in evidence["reason"]
    assert later_failure.__name__ in evidence["reason"]
    assert app.command_outcome(identity)["reason"] == evidence["reason"]
    reviewed = review_saved(state["folder"])
    assert reviewed["operations"][0]["review_state"] == "terminal_unknown"
    assert reviewed["operations"][0]["recovery_result"] == evidence


@pytest.mark.parametrize("boundary", ["reservation", "metadata"])
@pytest.mark.parametrize(
    "first_signal, authentication_signal",
    [
        (KeyboardInterrupt, SystemExit),
        (SystemExit, KeyboardInterrupt),
        (RuntimeError, SystemExit),
    ],
)
def test_manifest_authentication_control_preserves_publication_control_and_receipt(
    app, monkeypatch, tmp_path, boundary, first_signal, authentication_signal
):
    from color_measurement import exports

    sync, read = exports._sync_directory, application._read_regular
    first = first_signal("Original manifest publication control")
    later = authentication_signal("Later manifest authentication control")
    expected = first if not isinstance(first, Exception) else later
    path = Path(app.snapshot()["folder"]) / "batch.json"
    installed, authentication_failures, thread_errors = [], [], []
    reported = threading.Event()

    def interrupted_sync(directory):
        if directory == path.parent and not installed:
            installed.append(path.read_bytes())
            raise first
        return sync(directory)

    def interrupted_authentication(target):
        if target == path and installed and not authentication_failures:
            authentication_failures.append(target)
            raise later
        return read(target)

    def report_thread_error(arguments):
        thread_errors.append(arguments.exc_value)
        reported.set()

    monkeypatch.setattr(exports, "_sync_directory", interrupted_sync)
    monkeypatch.setattr(application, "_read_regular", interrupted_authentication)
    monkeypatch.setattr(threading, "excepthook", report_thread_error)
    identity = str(uuid4())
    if boundary == "reservation":
        prepare = app._frontend.prepare
        held = []

        def prepared(request):
            operation = prepare(request)
            operation.dispatch = lambda: pytest.fail("Interrupted reservation dispatched")
            held.append(operation)
            return operation

        monkeypatch.setattr(app._frontend, "prepare", prepared)
        with pytest.raises(type(expected)) as raised:
            _acquire(app, identity=identity)
        assert raised.value is expected
        assert held[0].done and held[0]._thread is None
        assert app.operation_details(identity)["result"]["status"] == "cancelled"
    else:
        draft = app.snapshot()["draft"]
        outcome = _command(
            app,
            "edit_draft",
            dict(
                version=draft["version"],
                name="Retained draft",
                notes="Exact metadata",
                options={},
                draft_id=draft["id"],
                number_samples=False,
            ),
            identity=identity,
        )
        assert outcome["state"] == "failed"
        assert reported.wait(2) and thread_errors == [expected]
        assert app.snapshot()["draft"]["notes"] == "Exact metadata"
        assert not app._metadata_saving
    state = app.snapshot()
    assert state["phase"] == "idle" and state["software_stopped"]
    assert not state["cleanup_reason"] and not application._UNCERTAIN_OWNERS
    assert len(installed) == len(authentication_failures) == 1
    assert path.read_bytes() == installed[0]
    receipt = next(item for item in state["durability"]["files"] if item["path"] == str(path))
    assert receipt["sha256"] == hashlib.sha256(installed[0]).hexdigest()
    assert receipt["file_sync"] == first.publication.file_sync
    assert receipt["directory_synced"] is False
    reason = app.command_outcome(identity)["reason"]
    assert str(first) in reason and str(later) in reason
    recovery = _command(app, "another_folder", dict(folder=str(tmp_path / "recovery")))
    assert recovery["state"] == "completed"
    assert path.read_bytes() == installed[0]
    assert not app.snapshot()["save_reason"] and app.snapshot()["can_finish"]
    saved = review_saved(app.snapshot()["folder"])
    if boundary == "metadata":
        assert saved["manifest"]["draft"]["notes"] == "Exact metadata"
    else:
        assert saved["operations"][0]["result"]["status"] == "cancelled"


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
def test_rescue_authentication_control_restores_old_owner_and_releases_new_lock(
    app, monkeypatch, tmp_path, signal
):
    from color_measurement import exports

    before, data = app.snapshot(), application.deepcopy(app._data)
    old_lock, old_bytes = app._lock, app._manifest_bytes
    target = tmp_path / "interrupted-rescue"
    sync, read = exports._sync_directory, application._read_regular
    first = signal("Original rescue publication control")
    later = SystemExit("Later rescue authentication control")
    installed, authentication_failures = [], []

    def interrupted_sync(directory):
        if directory == target and not installed:
            installed.append((target / "batch.json").read_bytes())
            raise first
        return sync(directory)

    def interrupted_authentication(path):
        if path == target / "batch.json" and installed:
            authentication_failures.append(path)
            raise later
        return read(path)

    monkeypatch.setattr(exports, "_sync_directory", interrupted_sync)
    monkeypatch.setattr(application, "_read_regular", interrupted_authentication)
    with pytest.raises(signal) as caught:
        _command(app, "another_folder", dict(folder=str(target)))
    assert caught.value is first and len(installed) == 1
    assert len(authentication_failures) == 2
    assert app._lock is old_lock and app._manifest_bytes == old_bytes and app._data == data
    assert app.snapshot()["folder"] == before["folder"] and app.snapshot()["phase"] == "idle"
    with _folder_lock(target, ".batch.lock"):
        assert (target / "batch.json").read_bytes() == installed[0]
    with pytest.raises(OSError):
        with _folder_lock(Path(before["folder"]), ".batch.lock"):
            pytest.fail("The original batch lock was released")
    notes = "; ".join(first.__notes__)
    assert str(later) in notes and "Rescue manifest authentication failed" in notes
    recovery = _command(app, "another_folder", dict(folder=str(tmp_path / "next-rescue")))
    assert recovery["state"] == "completed"
    assert (target / "batch.json").read_bytes() == installed[0]


@pytest.mark.parametrize("control", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("published", [False, True])
def test_interrupted_abandon_restores_unpublished_state_or_retires_published_owner(
    app, monkeypatch, control, published
):
    from color_measurement import exports

    before, data = app.snapshot(), application.deepcopy(app._data)
    path = Path(before["folder"]) / "batch.json"
    old_bytes, old_lock = path.read_bytes(), app._lock
    publish, sync = application._publish_owned_file, exports._sync_directory
    interruption = control("Abandon publication interrupted")
    injected = []

    def refuse_before_replace(target, payload, previous):
        if target == path and json.loads(payload)["status"] == "abandoned" and not injected:
            injected.append(True)
            raise interruption
        return publish(target, payload, previous)

    def refuse_after_replace(folder):
        if (folder == path.parent and json.loads(path.read_bytes())["status"] == "abandoned"
                and not injected):
            injected.append(True)
            raise interruption
        return sync(folder)

    monkeypatch.setattr(application, "_publish_owned_file", refuse_before_replace if not published
                        else publish)
    monkeypatch.setattr(exports, "_sync_directory", refuse_after_replace if published else sync)
    identity = str(uuid4())
    with pytest.raises(control) as caught:
        _command(app, "abandon", dict(discard_token=app.discard_summary()["token"],
                                      reason="Explicit interrupted abandonment"), identity=identity)
    assert caught.value is interruption and injected == [True]
    state = app.snapshot()
    assert state["save_reason"] and str(interruption) in state["save_reason"]
    assert app.command_outcome(identity)["admission"] == "admitted"
    assert app.command_outcome(identity)["state"] == "failed"
    if published:
        saved = json.loads(path.read_bytes())
        assert saved["status"] == state["status"] == "abandoned"
        assert saved["shutdown"]["reason"] == "Explicit interrupted abandonment"
        assert app._manifest_bytes == path.read_bytes() and app._closed and app._lock is None
        receipt = next(item for item in state["durability"]["files"] if item["path"] == str(path))
        assert receipt["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert receipt["directory_synced"] is False
        with _folder_lock(path.parent, ".batch.lock"):
            assert path.read_bytes() == app._manifest_bytes
    else:
        assert state["status"] == data["status"] and app._data["shutdown"] == data["shutdown"]
        assert path.read_bytes() == old_bytes and app._manifest_bytes == old_bytes
        assert not app._closed and app._lock is old_lock
        refused = _command(app, "close", dict(discard_token="not the confirmed contents"))
        assert refused["admission"] == "refused"
        assert app._lock is old_lock
        monkeypatch.setattr(application, "_publish_owned_file", publish)
        assert _command(app, "retry_save")["state"] == "completed"
        assert json.loads(path.read_bytes())["status"] == data["status"]


def test_failed_rescue_durability_preserves_target_receipt_but_uses_current_folder(
    app, tmp_path, monkeypatch
):
    from color_measurement import exports

    old = Path(app.snapshot()["folder"])
    path = old / "batch.json"
    # Controlled synchronization facts exercise aggregation without claiming
    # that this filesystem implements full synchronization on every platform.
    app._note_publication(application.PublicationResult(path, path.read_bytes(), "fullsync", True))
    before = app.snapshot()
    assert before["durability"]["status"] == "synchronized"
    target = tmp_path / "failed-rescue-sync"
    sync = exports._sync_directory

    def refuse_target(folder):
        if folder == target:
            raise OSError("Rescue directory synchronization refused")
        return sync(folder)

    monkeypatch.setattr(exports, "_sync_directory", refuse_target)
    assert _command(app, "another_folder", dict(folder=str(target)))["admission"] == "refused"
    state = app.snapshot()
    assert state["folder"] == str(old) and state["durability"]["status"] == "synchronized"
    receipt = next(item for item in state["durability"]["files"]
                   if item["path"] == str(target / "batch.json"))
    assert receipt["directory_synced"] is False
    assert receipt["sha256"] == hashlib.sha256((target / "batch.json").read_bytes()).hexdigest()
    assert all(app._publications[receipt["path"]][key] == value for key, value in receipt.items())
    with _folder_lock(target, ".batch.lock"):
        assert (target / "batch.json").exists()


def test_successful_rescue_durability_uses_new_folder_and_retains_old_limitations(app, tmp_path):
    old = Path(app.snapshot()["folder"]) / "batch.json"
    app._note_publication(application.PublicationResult(old, old.read_bytes(), "fsync", False))
    target = tmp_path / "new-active-destination"
    assert _command(app, "another_folder", dict(folder=str(target)))["state"] == "completed"
    path = target / "batch.json"
    app._note_publication(application.PublicationResult(path, path.read_bytes(), "fullsync", True))
    state = app.snapshot()
    assert state["folder"] == str(target) and state["durability"]["status"] == "synchronized"
    receipt = next(item for item in state["durability"]["files"] if item["path"] == str(old))
    assert receipt["directory_synced"] is False and receipt["file_sync"] == "fsync"


@pytest.mark.parametrize("control", [KeyboardInterrupt, SystemExit])
def test_interrupted_abandon_preserves_real_publication_through_authentication_failure(
    app, monkeypatch, control
):
    from color_measurement import exports

    path = Path(app.snapshot()["folder"]) / "batch.json"
    sync, read = exports._sync_directory, application._read_regular
    first = control("Published abandonment control")
    later = SystemExit("Later authentication control")
    published = []

    def interrupt_sync(folder):
        if folder == path.parent and json.loads(path.read_bytes())["status"] == "abandoned":
            published.append(path.read_bytes())
            raise first
        return sync(folder)

    def interrupt_authentication(target):
        if target == path and published:
            raise later
        return read(target)

    monkeypatch.setattr(exports, "_sync_directory", interrupt_sync)
    monkeypatch.setattr(application, "_read_regular", interrupt_authentication)
    with pytest.raises(control) as caught:
        _command(app, "abandon", dict(discard_token=app.discard_summary()["token"],
                                      reason="Explicit published abandonment"))
    assert caught.value is first and len(published) == 1
    state = app.snapshot()
    assert state["status"] == "abandoned" and app._closed and app._lock is None
    assert app._manifest_bytes == path.read_bytes() == published[0]
    assert first.publication.payload == published[0] and not first.publication.directory_synced
    assert str(later) in "; ".join(first.__notes__)


@pytest.mark.parametrize("receipt_kind", ["other_path", "previous_payload"])
def test_interrupted_abandon_refuses_receipts_for_other_path_or_payload(
    app, monkeypatch, tmp_path, receipt_kind
):
    path = Path(app.snapshot()["folder"]) / "batch.json"
    before, old_bytes, old_lock = application.deepcopy(app._data), path.read_bytes(), app._lock
    publish = application._publish_owned_file
    first = KeyboardInterrupt("Unpublished abandonment")

    def refuse(target, payload, previous):
        if target == path and json.loads(payload)["status"] == "abandoned":
            if receipt_kind == "other_path":
                other = tmp_path / "other-publication.json"
                first.publication = publish(other, payload, None)
            else:
                first.publication = publish(path, old_bytes, hashlib.sha256(old_bytes).hexdigest())
            raise first
        return publish(target, payload, previous)

    monkeypatch.setattr(application, "_publish_owned_file", refuse)
    with pytest.raises(KeyboardInterrupt) as caught:
        _command(app, "abandon", dict(discard_token=app.discard_summary()["token"],
                                      reason="Must not be inferred from another receipt"))
    assert caught.value is first and path.read_bytes() == old_bytes
    assert app._data["status"] == before["status"] and app._data["shutdown"] == before["shutdown"]
    assert not app._closed and app._lock is old_lock and app.snapshot()["save_reason"]


@pytest.mark.skipif(os.name == "nt", reason="Windows batch lock prevents renaming its open parent")
def test_interrupted_abandon_refuses_receipt_after_owned_directory_replacement(
    app, monkeypatch, tmp_path
):
    from color_measurement import exports

    folder = Path(app.snapshot()["folder"])
    path, moved = folder / "batch.json", tmp_path / "moved-owned-folder"
    data, old_lock = application.deepcopy(app._data), app._lock
    sync = exports._sync_directory
    first = KeyboardInterrupt("Published into earlier owned inode")
    installed = []

    def replace_folder(target):
        if target == folder and json.loads(path.read_bytes())["status"] == "abandoned":
            installed.append(path.read_bytes())
            folder.rename(moved)
            folder.mkdir()
            raise first
        return sync(target)

    monkeypatch.setattr(exports, "_sync_directory", replace_folder)
    with pytest.raises(KeyboardInterrupt) as caught:
        _command(app, "abandon", dict(discard_token=app.discard_summary()["token"],
                                      reason="Retain exact old-directory publication"))
    assert caught.value is first and len(installed) == 1
    assert first.publication.payload == installed[0]
    assert (moved / "batch.json").read_bytes() == installed[0] and not path.exists()
    assert app._data["status"] == data["status"] and app._data["shutdown"] == data["shutdown"]
    assert not app._closed and app._lock is old_lock
    assert "Owned batch directory was replaced" in app.snapshot()["save_reason"]
    with pytest.raises(OSError):
        with _folder_lock(moved, ".batch.lock"):
            pytest.fail("Owned moved directory lock was released")


@pytest.mark.parametrize("control", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("release_error", [OSError, SystemExit])
@pytest.mark.parametrize("action", ["abandon", "finish"])
def test_interrupted_published_abandon_keeps_first_control_when_lock_release_fails(
    app, monkeypatch, control, release_error, tmp_path, action
):
    from color_measurement import exports

    folder = Path(app.snapshot()["folder"])
    first, later = control("Original published control"), release_error("Lock release failed")
    lock, sync, close = app._lock, exports._sync_directory, exports.os.close
    descriptor = lock.descriptor
    before = app.snapshot()
    calls = []
    status = "abandoned" if action == "abandon" else "finished"

    def failed_close(value):
        if value == descriptor:
            calls.append(True)
            raise later
        return close(value)

    def interrupted(directory):
        if (
            directory == folder
            and json.loads((folder / "batch.json").read_bytes())["status"] == status
        ):
            raise first
        return sync(directory)

    monkeypatch.setattr(exports.os, "close", failed_close)
    monkeypatch.setattr(exports, "_sync_directory", interrupted)
    try:
        with pytest.raises(control) as caught:
            payload = (
                dict(
                    discard_token=app.discard_summary()["token"],
                    reason="Explicit published abandonment",
                )
                if action == "abandon"
                else {}
            )
            _command(app, action, payload)
        assert caught.value is first and calls == [True]
        assert app._lock is lock and lock.release_failure is later and not app._closed
        assert exports._RETIRED_PUBLICATION_LOCKS == {lock.path.absolute(): lock}
        assert app.snapshot()["status"] == status
        installed = (folder / "batch.json").read_bytes()
        assert app._manifest_bytes == first.publication.payload == installed
        assert str(later) in "; ".join(first.__notes__)
        assert app.snapshot()["software_stopped"] == before["software_stopped"]
        assert app.snapshot()["instrument_state"] == before["instrument_state"]
        with pytest.raises(OSError):
            with _folder_lock(folder, ".batch.lock"):
                pytest.fail("Publication lock was lost")
        outcome = _command(app, "close", dict(discard_token=app.discard_summary()["token"]))
        assert outcome["state"] == "refused" and not app._closed and calls == [True]
        # The retired destination refuses further publication; the retained
        # batch is still rescued, with its published status, to another folder.
        assert _command(app, "retry_save")["state"] == "refused"
        target = tmp_path / "rescued-after-retirement"
        assert _command(app, "another_folder", dict(folder=str(target)))["state"] == "completed"
        assert review_saved(target)["manifest"]["status"] == status
        assert app._lock is not lock and lock.release_failure is later
        assert exports._RETIRED_PUBLICATION_LOCKS == {lock.path.absolute(): lock}
    finally:
        # The injected close never touched this exact owned descriptor. Real
        # ambiguous production failures do not authorize a descriptor retry.
        monkeypatch.setattr(exports.os, "close", close)
        if lock.release_failure is not None:
            close(descriptor)
            exports._RETIRED_PUBLICATION_LOCKS.clear()
            lock.descriptor = None
        else:
            app._release_lock()
        app._lock = None
        app._closed = True


@pytest.mark.parametrize("boundary", ["constructor", "rescue_rollback"])
def test_constructor_and_rescue_keep_control_and_owned_resource_on_close_failure(
    tmp_path, monkeypatch, boundary
):
    from color_measurement import exports

    owner = None
    if boundary == "rescue_rollback":
        owner = _demo_batch(tmp_path / "original")
        owner.bind()
        before, old_lock = application.deepcopy(owner._data), owner._lock
    target = tmp_path / "failed-lock-destination"
    factory, sync, close = application._folder_lock, exports._sync_directory, exports.os.close
    locks, calls = [], []
    first, later = KeyboardInterrupt("Original publication control"), OSError("Owned close refused")

    def capture(folder, name):
        lock = factory(folder, name)
        if folder == target:
            locks.append(lock)
        return lock

    def interrupt_sync(folder):
        if folder == target:
            raise first
        return sync(folder)

    def interrupt_close(value):
        if locks and value == locks[0].descriptor:
            calls.append(value)
            raise later
        return close(value)

    monkeypatch.setattr(application, "_folder_lock", capture)
    monkeypatch.setattr(exports, "_sync_directory", interrupt_sync)
    monkeypatch.setattr(exports.os, "close", interrupt_close)
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            if owner is None:
                _demo_batch(target)
            else:
                _command(owner, "another_folder", dict(folder=str(target)))
        assert caught.value is first and calls == [locks[0].descriptor]
        assert locks[0].release_failure is later
        assert exports._RETIRED_PUBLICATION_LOCKS == {locks[0].path.absolute(): locks[0]}
        assert first.publication.path == target / "batch.json"
        assert first.publication.payload == (target / "batch.json").read_bytes()
        assert str(later) in "; ".join(first.__notes__)
        if owner is not None:
            assert owner._data == before and owner._lock is old_lock and not owner._closed
            assert str(first) in owner.snapshot()["save_reason"]
        # Only the destination whose lock close is unconfirmed is retired.
        with pytest.raises(OSError, match="is retired in this process"):
            with _folder_lock(target, ".batch.lock"):
                pytest.fail("Retired lock path was re-entered")
        fresh = _demo_batch(tmp_path / "unaffected")
        try:
            assert fresh.snapshot()["can_measure"]
        finally:
            fresh._release_lock()
            fresh._closed = True
    finally:
        monkeypatch.setattr(exports.os, "close", close)
        if locks and locks[0].release_failure is not None:
            close(locks[0].descriptor)
            locks[0].descriptor = None
        exports._RETIRED_PUBLICATION_LOCKS.clear()
        if owner is not None:
            owner._release_lock()
            owner._closed = True


def test_ordinary_abandon_release_failure_retains_reachable_owner_and_failure(app, monkeypatch):
    from color_measurement import exports

    lock, calls, close = app._lock, [], exports.os.close
    descriptor = lock.descriptor

    def failed_close(value):
        if value == descriptor:
            calls.append(True)
            raise OSError("Ordinary publication lock cleanup failed")
        return close(value)

    monkeypatch.setattr(exports.os, "close", failed_close)
    try:
        outcome = _command(
            app,
            "abandon",
            dict(
                discard_token=app.discard_summary()["token"], reason="Ordinary explicit abandonment"
            ),
        )
        assert outcome["admission"] == "admitted" and outcome["state"] == "failed"
        assert not app._closed and app._lock is lock and calls == [True]
        assert app.snapshot()["status"] == "abandoned" and app.snapshot()["save_reason"]
        saved = json.loads((Path(app.snapshot()["folder"]) / "batch.json").read_bytes())
        assert saved["status"] == "abandoned"
    finally:
        monkeypatch.setattr(exports.os, "close", close)
        if lock.release_failure is not None:
            close(descriptor)
            exports._RETIRED_PUBLICATION_LOCKS.clear()
            lock.descriptor = None
        else:
            app._release_lock()
        app._lock = None
        app._closed = True


@pytest.mark.parametrize("cleanup", ["idle", "active"])
def test_retired_destination_blocks_only_itself_and_other_owners_keep_saving(
    tmp_path, monkeypatch, cleanup
):
    from color_measurement import exports

    issuer, other = _demo_batch(tmp_path / "issuer"), _demo_batch(tmp_path / "other")
    issuer.bind()
    other.bind()
    _complete(issuer, export=False)
    if cleanup == "idle":
        _complete(other, export=False)
    else:
        assert _acquire(other)["admission"] == "admitted"
        _wait(other, lambda state: state["instructions"] is not None)
    # Close refuses while the issuer's automatic files are still being written.
    _automatic_companion_state(issuer)
    lock = issuer._lock
    descriptor, close = lock.descriptor, exports.os.close

    def failed_close(value):
        if value == descriptor:
            raise OSError("Issuer lock cleanup failed")
        return close(value)

    monkeypatch.setattr(exports.os, "close", failed_close)
    try:
        outcome = _command(issuer, "close", dict(discard_token=issuer.discard_summary()["token"]))
        assert outcome["admission"] == "admitted" and outcome["state"] == "failed"
        assert exports._RETIRED_PUBLICATION_LOCKS == {lock.path.absolute(): lock}
        # The other owner shares nothing with the retired destination: a reading
        # retained while the issuer failed is saved, and it keeps measuring.
        if cleanup == "active":
            saved, _ = _wait(other, lambda state: state["phase"] == "idle", respond=True)
        else:
            saved = other.snapshot()
        assert saved["save_reason"] is None and saved["saved_count"] == 1
        assert saved["can_measure"] and saved["can_finish"]
        _complete(other, export=False)
        assert other.snapshot()["saved_count"] == 2
        # The issuer's own destination is retired: no publication and no re-lock.
        issued = issuer.snapshot()
        assert "is retired in this process" in issued["save_reason"]
        assert not issued["can_measure"] and not issued["can_finish"]
        assert _command(issuer, "retry_save")["state"] == "refused"
        assert _acquire(issuer)["state"] == "refused"
        with pytest.raises(OSError, match="is retired in this process"):
            with _folder_lock(Path(issued["folder"]), ".batch.lock"):
                pytest.fail("Retired lock path was re-entered")
        # Its retained work is rescued to another destination instead of being
        # lost to a process restart.
        target = tmp_path / "issuer-rescue"
        assert _command(issuer, "another_folder", dict(folder=str(target)))["state"] == "completed"
        rescued = issuer.snapshot()
        assert rescued["folder"] == str(target) and rescued["save_reason"] is None
        assert rescued["saved_count"] == 1 and rescued["can_measure"]
        assert review_saved(target)["sample_count"] == 1
        assert issuer._lock is not lock and lock.release_failure is not None
    finally:
        monkeypatch.setattr(exports.os, "close", close)
        if lock.release_failure is not None:
            close(descriptor)
            lock.descriptor = None
        exports._RETIRED_PUBLICATION_LOCKS.clear()
        for owner in (issuer, other):
            if owner._lock is not None and owner._lock.release_failure is None:
                owner._release_lock()
            owner._lock = None
            owner._closed = True


@pytest.mark.parametrize("closing", [OSError, KeyboardInterrupt])
def test_successful_rescue_keeps_new_destination_when_old_lock_close_fails(
    app, tmp_path, monkeypatch, closing
):
    from color_measurement import exports

    before, _ = _complete(app)
    target, old_lock = tmp_path / "rescued", app._lock
    descriptor, close = old_lock.descriptor, exports.os.close

    def failed_close(value):
        if value == descriptor:
            raise closing("Earlier destination lock cleanup failed")
        return close(value)

    monkeypatch.setattr(exports.os, "close", failed_close)
    try:
        identity = str(uuid4())
        if issubclass(closing, Exception):
            outcome = _command(app, "another_folder", dict(folder=str(target)), identity=identity)
        else:
            with pytest.raises(closing):
                _command(app, "another_folder", dict(folder=str(target)), identity=identity)
            outcome = app.command_outcome(identity)
        state = app.snapshot()
        assert outcome["admission"] == "admitted" and outcome["state"] == "failed"
        assert state["folder"] == str(target) and app._lock is not old_lock
        assert state["saved_count"] == before["saved_count"] == 1
        assert state["save_reason"] and not state["can_measure"]
        assert app._manifest_bytes == (target / "batch.json").read_bytes()
        path = state["operations"][0]["path"]
        assert (target / path).read_bytes() == (Path(before["folder"]) / path).read_bytes()
        assert exports._RETIRED_PUBLICATION_LOCKS == {old_lock.path.absolute(): old_lock}
        assert old_lock.descriptor == descriptor
        # The new destination shares nothing with the retired one: an ordinary
        # retry clears the release failure and the owner keeps working there.
        assert _command(app, "retry_save")["state"] == "completed"
        recovered = app.snapshot()
        assert recovered["save_reason"] is None and recovered["can_measure"]
    finally:
        monkeypatch.setattr(exports.os, "close", close)
        if old_lock.release_failure is not None:
            close(descriptor)
            old_lock.descriptor = None
        exports._RETIRED_PUBLICATION_LOCKS.clear()


def test_constructor_draft_initialization_control_releases_acquired_lock(tmp_path, monkeypatch):
    from color_measurement import exports

    first = KeyboardInterrupt("Draft initialization interrupted after lock acquisition")

    def interrupt():
        raise first

    monkeypatch.setattr(BatchApplication, "_new_draft", staticmethod(interrupt))
    target = tmp_path / "interrupted-initialization"
    with pytest.raises(KeyboardInterrupt) as caught:
        _demo_batch(target)
    assert caught.value is first and not (target / "batch.json").exists()
    with exports._folder_lock(target, ".batch.lock"):
        assert not exports._RETIRED_PUBLICATION_LOCKS


@pytest.mark.parametrize("action", ["close", "finish"])
def test_admitted_close_control_keeps_failed_outcome_and_owned_lock(app, monkeypatch, action):
    from color_measurement import exports

    first = KeyboardInterrupt("Admitted lock close interrupted")
    lock, close = app._lock, exports.os.close
    descriptor = lock.descriptor

    def interrupt(value):
        if value == descriptor:
            raise first
        return close(value)

    monkeypatch.setattr(exports.os, "close", interrupt)
    identity = str(uuid4())
    controls = []
    run = application._ApplicationWorker._run

    def record_companion_control(worker):
        try:
            run(worker)
        except BaseException as error:
            controls.append(error)

    if action == "finish":
        monkeypatch.setattr(application._ApplicationWorker, "_run", record_companion_control)
    try:
        payload = dict(discard_token=app.discard_summary()["token"]) if action == "close" else {}
        if action == "close":
            with pytest.raises(KeyboardInterrupt) as caught:
                _command(app, action, payload, identity=identity)
            assert caught.value is first
        else:
            assert _command(app, action, payload, identity=identity)["state"] == "failed"
            assert controls == [first]
        outcome = app.command_outcome(identity)
        assert outcome["admission"] == "admitted"
        assert outcome["state"] == "failed" and str(first) in outcome["reason"]
        assert app._lock is lock and not app._closed and lock.release_failure is first
        assert exports._RETIRED_PUBLICATION_LOCKS == {lock.path.absolute(): lock}
    finally:
        monkeypatch.setattr(exports.os, "close", close)
        if lock.release_failure is not None:
            close(descriptor)
            lock.descriptor = None
        exports._RETIRED_PUBLICATION_LOCKS.clear()


@pytest.mark.parametrize(
    "boundary", ["manifest", "saved_ids", "unsaved_ids", "annotations", "saved_comparison"]
)
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_rescue_rollback_copies_fail_before_any_new_lock_or_directory(
    app, tmp_path, monkeypatch, boundary, failure
):
    target = tmp_path / "must-not-acquire"
    first = failure("Rollback snapshot copy failed")
    original_data, old_lock = application.deepcopy(app._data), app._lock
    deepcopy, factory = application.deepcopy, application._folder_lock
    caches = app._saved_ids, app._unsaved_ids, app._saved_annotation_versions
    locks, copied = [], []

    class FailedSet(set):
        def copy(self):
            copied.append(True)
            raise first

    class FailedDict(dict):
        def copy(self):
            copied.append(True)
            raise first

    def interrupted_copy(value):
        if (boundary == "manifest" and value is app._data) or (
            boundary == "saved_comparison" and value is app._saved_comparison
        ):
            copied.append(True)
            raise first
        return deepcopy(value)

    def capture(folder, name):
        lock = factory(folder, name)
        if folder == target:
            locks.append(lock)
        return lock

    monkeypatch.setattr(application, "_folder_lock", capture)
    if boundary in ("manifest", "saved_comparison"):
        monkeypatch.setattr(application, "deepcopy", interrupted_copy)
    elif boundary == "saved_ids":
        app._saved_ids = FailedSet(app._saved_ids)
    elif boundary == "unsaved_ids":
        app._unsaved_ids = FailedSet(app._unsaved_ids)
    else:
        app._saved_annotation_versions = FailedDict(app._saved_annotation_versions)
    try:
        if issubclass(failure, Exception):
            outcome = _command(app, "another_folder", dict(folder=str(target)))
            assert outcome["state"] == "refused" and str(first) in outcome["reason"]
        else:
            with pytest.raises(failure) as caught:
                _command(app, "another_folder", dict(folder=str(target)))
            assert caught.value is first
        assert copied == [True] and not locks and not target.exists()
        assert app._data == original_data and app._lock is old_lock
    finally:
        monkeypatch.setattr(application, "deepcopy", deepcopy)
        app._saved_ids, app._unsaved_ids, app._saved_annotation_versions = caches
        # Before the repair, this test exposes and then releases its captured
        # real lock. No descriptor-close failure is injected in this proof.
        for lock in locks:
            lock.__exit__(None, None, None)


def _edit_target(app, name, *, number_samples=True, notes="", options=None):
    draft = app.snapshot()["draft"]
    return _command(
        app,
        "edit_draft",
        dict(
            draft_id=draft["id"],
            version=draft["version"],
            name=name,
            notes=notes,
            options={} if options is None else options,
            number_samples=number_samples,
        ),
    )


def _preview_target(app, name, *, number_samples=True):
    state = app.snapshot()
    draft = state["draft"]
    return app.preview_draft(
        batch_id=state["batch_id"], generation=state["generation"],
        draft_id=draft["id"], version=draft["version"], name=name,
        number_samples=number_samples,
    )["resolved_name"]


def test_explicit_numbering_backfills_only_unused_ordinals(app):
    assert _edit_target(app, "White 03")["state"] == "completed"
    _complete(app, export=False)
    assert _preview_target(app, "White 01") == "White 01"
    assert _edit_target(app, "White 01")["state"] == "completed"
    _complete(app, export=False)
    assert _preview_target(app, "White 01") == "White 02"
    assert _preview_target(app, "White 03") == "White 04"
    assert _preview_target(app, "White") == "White 04"
    assert app.snapshot()["draft"]["name"] == "White 02"
    _complete(app, export=False)
    assert app.snapshot()["draft"]["name"] == "White 04"
    _complete(app, export=False)
    assert [item["name"] for item in app.snapshot()["annotations"]] == [
        "White 03", "White 01", "White 02", "White 04",
    ]


@pytest.mark.parametrize("first,bare,following", [
    ("Sample #01", "Sample #", "Sample #02"),
    ("White #009", "White #", "White #010"),
])
def test_deleted_digits_resume_the_spaced_hash_series(app, first, bare, following):
    assert _edit_target(app, first)["state"] == "completed"
    _complete(app, export=False)
    before = application.deepcopy(app._data)
    assert _preview_target(app, bare) == following and app._data == before
    assert _edit_target(app, bare)["admission"] == "no_change"
    assert app.snapshot()["draft"]["name"] == following


@pytest.mark.parametrize("control", ["\n", "\t", "\x01", "\x7f", "\u2028"])
@pytest.mark.parametrize("number_samples", [True, False])
def test_sample_label_controls_refuse_before_preview_or_draft_mutation(
    app, monkeypatch, control, number_samples,
):
    name = "White" + control + "Blue"
    before, manifest = application.deepcopy(app._data), app._manifest_bytes
    monkeypatch.setattr(app._frontend, "prepare", lambda *args: pytest.fail(
        "Invalid label reached frontend validation"
    ))
    with pytest.raises(ValueError, match="control|single-line"):
        _preview_target(app, name, number_samples=number_samples)
    outcome = _edit_target(app, name, number_samples=number_samples)
    assert outcome["state"] == "refused" and "label" in outcome["reason"]
    assert app._data == before and app._manifest_bytes == manifest
    assert not app._metadata_saving and app.snapshot()["save_reason"] is None


def test_annotation_label_controls_refuse_before_mutation_and_notes_stay_multiline(app):
    state, _ = _complete(app, export=False)
    item = state["annotations"][0]
    key = {name: item[name] for name in ("operation_id", "observation_id")}
    before, manifest = application.deepcopy(app._data), app._manifest_bytes
    for control in ("\n", "\t", "\x01", "\x7f", "\u0085", "\u2028", "\u2029"):
        outcome = _command(app, "edit_annotation_draft", dict(
            key, version=item["version"], draft_version=0,
            name="White" + control + "Blue", notes="\nkept\tverbatim",
        ))
        assert outcome["state"] == "refused" and "label" in outcome["reason"]
        assert not app._annotation_drafts and app._data == before
        assert app._manifest_bytes == manifest
    notes = "\nFirst line\nSecond\tline"
    assert _annotate(app, item, name="White", notes=notes)["state"] == "completed"
    assert app.annotation_details(**key)["applied"]["notes"] == notes


def test_next_label_overflow_refuses_preview_and_edit_before_frontend_validation(
    app, monkeypatch,
):
    name = "x" * 1020 + " 999"
    assert len(name) == 1024
    before, manifest = application.deepcopy(app._data), app._manifest_bytes
    monkeypatch.setattr(app._frontend, "prepare", lambda *args: pytest.fail(
        "Unrepresentable next label reached frontend validation"
    ))
    with pytest.raises(ValueError, match="Next sample label.*1,024"):
        _preview_target(app, name)
    outcome = _edit_target(app, name)
    assert outcome["state"] == "refused" and "Next sample label" in outcome["reason"]
    assert app._data == before and app._manifest_bytes == manifest
    assert _preview_target(app, name, number_samples=False) == name


@pytest.mark.parametrize(
    "name,number_samples,expected",
    [
        ("", True, "Sample #01"),
        ("White", True, "White 01"),
        ("White 03", True, "White 03"),
        ("White 003", True, "White 003"),
        ("White #09", True, "White #09"),
        ("D65", True, "D65 01"),
        ("Panel2026", True, "Panel2026 01"),
        ("D65", False, "D65"),
        ("White 01", False, "White 01"),
        ("  Centre  ", False, "  Centre  "),
    ],
)
def test_owner_preview_and_edit_use_same_visible_label_without_consuming(
    app, name, number_samples, expected
):
    state = app.snapshot()
    params = dict(
        batch_id=state["batch_id"],
        generation=state["generation"],
        draft_id=state["draft"]["id"],
        version=state["draft"]["version"],
        name=name,
        number_samples=number_samples,
    )
    before = application.deepcopy(app._data)
    preview = app.preview_draft(**params)
    assert preview == dict(params, resolved_name=expected) and app._data == before
    result = _edit_target(app, name, number_samples=number_samples)
    assert result["admission"] in {"admitted", "no_change"}
    assert app.snapshot()["draft"]["name"] == expected and not app._retained


def test_automatic_default_and_independent_series_resume_from_successful_frozen_intents(app):
    initial = app.snapshot()["draft"]
    assert initial["name"] == "Sample #01" and initial["number_samples"]
    first, _ = _complete(app, export=False)
    assert first["draft"]["name"] == "Sample #02" and first["draft"]["id"] != initial["id"]
    assert _edit_target(app, "White 03", notes="First white only")["state"] == "completed"
    white, _ = _complete(app, export=False)
    assert white["annotations"][-1]["name"] == "White 03"
    assert white["annotations"][-1]["notes"] == "First white only"
    assert white["draft"]["name"] == "White 04" and white["draft"]["notes"] == ""
    assert _edit_target(app, "Yellow")["state"] == "completed"
    yellow, _ = _complete(app, export=False)
    assert (
        yellow["annotations"][-1]["name"] == "Yellow 01" and yellow["draft"]["name"] == "Yellow 02"
    )
    assert _edit_target(app, "White")["state"] == "completed"
    assert app.snapshot()["draft"]["name"] == "White 04"
    _complete(app, export=False)
    assert _edit_target(app, "White 02")["state"] == "completed"
    assert app.snapshot()["draft"]["name"] == "White 02"
    item = _snapshot(app)["annotations"][1]
    assert _annotate(app, item, name="White 99", notes="An overlay")["state"] == "completed"
    assert _edit_target(app, "White")["state"] == "completed"
    assert app.snapshot()["draft"]["name"] == "White 05"
    assert _edit_target(app, "White 99", number_samples=False)["state"] == "completed"
    _complete(app, export=False)
    assert _edit_target(app, "White")["state"] == "completed"
    assert app.snapshot()["draft"]["name"] == "White 05"


def test_repeat_authenticates_pair_and_keeps_prepared_next_and_frozen_metadata(app):
    assert _edit_target(app, "White 03", notes="Original notes")["state"] == "completed"
    source, _ = _complete(app, export=False)
    reading = source["annotations"][0]
    assert _annotate(app, reading, name="Later renamed", notes="Overlay")["state"] == "completed"
    assert _edit_target(app, "Yellow", notes="Prepared next note")["state"] == "completed"
    before = app.snapshot()
    assert before["repeat_source"]["name"] == "White 03"
    payload = dict(
        context_id=before["context_id"],
        draft_id=before["draft"]["id"],
        draft_version=before["draft"]["version"],
        operation_id=reading["operation_id"],
        observation_id=reading["observation_id"],
    )
    assert (
        _command(app, "repeat_sample", dict(payload, observation_id="foreign-reading"))["state"]
        == "refused"
    )
    assert (
        _command(app, "repeat_sample", dict(payload, draft_id=str(uuid4())))["state"] == "refused"
    )
    identity = str(uuid4())
    result = _command(app, "repeat_sample", payload, identity=identity)
    assert result["admission"] == "admitted" and result["operation_id"] != reading["operation_id"]
    _wait(app, lambda state: state["phase"] == "idle", respond=True)
    assert (
        _command(app, "repeat_sample", payload, identity=identity)["operation_id"]
        == result["operation_id"]
    )
    after = _snapshot(app)
    assert after["draft"] == before["draft"] and after["sample_count"] == 2
    repeated = after["annotations"][-1]
    assert repeated["name"] == "White 03" and repeated["notes"] == ""
    assert repeated["observation_id"] != reading["observation_id"]
    first, second = after["operations"]
    assert second["request"] == first["request"]
    assert second["repeat_of"] == {key: reading[key] for key in ("operation_id", "observation_id")}
    assert _edit_target(app, "White")["state"] == "completed"
    assert app.snapshot()["draft"]["name"] == "White 04"
    review = review_saved(app.snapshot()["folder"])
    assert review["manifest"]["schema_version"] == 5 and all(
        row["review_state"] == "accepted" for row in review["operations"]
    )


def test_cancel_calibration_and_save_failure_preserve_exact_advancement_boundary(app, monkeypatch):
    initial = app.snapshot()["draft"]
    _acquire(app)
    _wait(app, lambda state: state["instructions"] is not None)
    assert _command(app, "cancel", dict(command_id=app._active))["state"] == "completed"
    cancelled, _ = _wait(app, lambda state: state["phase"] == "idle")
    assert cancelled["draft"] == initial and cancelled["repeat_source"] is None
    _complete(app, "calibrate", export=False)
    assert app.snapshot()["draft"] == initial
    publish = operation_exports.publish

    def fail_record(*args):
        raise OSError("Retained spot save failed")

    monkeypatch.setattr(operation_exports, "publish", fail_record)
    retained, _ = _complete(app, export=False)
    assert retained["draft"]["name"] == "Sample #02" and retained["save_reason"]
    assert retained["sample_count"] == 1 and not retained["can_measure"]
    next_draft = retained["draft"]
    assert _acquire(app)["state"] == "refused"
    monkeypatch.setattr(operation_exports, "publish", publish)
    assert _command(app, "retry_save")["state"] == "completed"
    assert app.snapshot()["draft"] == next_draft


def test_draft_identity_fence_prevents_version_zero_collision_after_automatic_next(app):
    first = app.snapshot()
    _complete(app, export=False)
    assert app.snapshot()["draft"]["version"] == first["draft"]["version"] == 0
    payload = dict(
        draft_id=first["draft"]["id"],
        version=0,
        name="Stale",
        notes="",
        options={},
        number_samples=False,
    )
    assert _command(app, "edit_draft", payload)["state"] == "refused"
    with pytest.raises(ValueError, match="Stale draft"):
        app.preview_draft(
            batch_id=first["batch_id"],
            generation=first["generation"],
            draft_id=first["draft"]["id"],
            version=0,
            name="White",
            number_samples=True,
        )
    del payload["draft_id"]
    assert _command(app, "edit_draft", payload)["state"] == "refused"
    assert _command(app, "new_draft")["state"] == "refused"


def test_failed_terminal_with_retained_reading_does_not_consume_target_or_offer_repeat(
    app, monkeypatch
):
    prepare = app._frontend.prepare
    initial = app.snapshot()["draft"]

    def failed_terminal(request):
        operation = prepare(request)
        result_json = operation.result_json

        def substitute():
            result = json.loads(result_json())
            result.update(
                status="failed", error="Substituted terminal failure with retained reading"
            )
            return json.dumps(result)

        operation.result_json = substitute
        return operation

    monkeypatch.setattr(app._frontend, "prepare", failed_terminal)
    failed, _ = _complete(app, export=False)
    assert failed["sample_count"] == 1 and failed["draft"] == initial
    assert failed["operations"][0]["status"] == "failed" and failed["repeat_source"] is None
    pair = failed["annotations"][0]
    payload = dict(
        context_id=failed["context_id"],
        draft_id=initial["id"],
        draft_version=0,
        operation_id=pair["operation_id"],
        observation_id=pair["observation_id"],
    )
    assert _command(app, "repeat_sample", payload)["state"] == "refused"
    monkeypatch.setattr(app._frontend, "prepare", prepare)
    succeeded, _ = _complete(app, export=False)
    assert succeeded["sample_count"] == 2 and succeeded["draft"]["name"] == "Sample #02"


def _set_comparison(app, reference=None, white=None):
    return _command(
        app,
        "set_comparison_context",
        dict(
            version=app.snapshot()["comparison"]["version"],
            reference=reference,
            white=white,
        ),
    )


def test_comparison_context_persists_exact_pairs_and_validated_white_across_annotations_rescue(
    app, tmp_path
):
    first, _ = _complete(app, export=False)
    second, _ = _complete(app, export=False)
    reference = {key: first["annotations"][0][key] for key in ("operation_id", "observation_id")}
    source = {key: second["annotations"][-1][key] for key in ("operation_id", "observation_id")}
    white = dict(source, kind="retained-reading")
    assert _set_comparison(app, reference, white)["state"] == "completed"
    state = _snapshot(app)
    context = state["comparison"]
    assert context["saved"] and context["reference"] == reference and context["white"] == white
    native = app.reading_details(**source)["backend_reported"]["xyz"]
    assert context["white_descriptor"]["xyz"] == list(native["values"])
    assert context["white_descriptor"]["source_id"] == "/".join(source.values())
    assert (
        app.comparison_context(context["version"])["white_descriptor"]
        == context["white_descriptor"]
    )
    item = state["annotations"][-1]
    assert (
        _annotate(app, item, name="Different annotation", notes="Exact text")["state"]
        == "completed"
    )
    assert app.snapshot()["comparison"] == context
    _automatic_companion_state(app)
    target = tmp_path / "comparison-rescue"
    assert _command(app, "another_folder", dict(folder=str(target)))["state"] == "completed"
    assert app.snapshot()["comparison"] == context
    reviewed = review_saved(target)
    assert reviewed["comparison"] == dict(context, available=True, reason=None)
    assert _command(app, "finish")["state"] == "completed"
    assert review_saved(target)["comparison"]["reference"] == reference
    created = _demo_new_batch(app, tmp_path / "fresh-comparison")
    try:
        assert created.snapshot()["comparison"] == dict(
            version=0, reference=None, white=None, white_descriptor=None, saved=True
        )
        assert created.snapshot()["draft"]["name"] == "Sample #01"
    finally:
        created._release_lock()
        created._closed = True


def test_comparison_save_failure_retains_choice_blocks_quantities_and_recovers_same_revision(
    app, monkeypatch
):
    state, _ = _complete(app, export=False)
    reference = {key: state["annotations"][0][key] for key in ("operation_id", "observation_id")}
    publish = application._publish_owned_file

    def fail_manifest(*args):
        raise OSError("Comparison metadata publication refused")

    monkeypatch.setattr(application, "_publish_owned_file", fail_manifest)
    result = _set_comparison(app, reference)
    comparison = app.snapshot()["comparison"]
    assert result["admission"] == "admitted" and result["state"] == "failed"
    assert (
        comparison["reference"] == reference
        and comparison["version"] == 1
        and not comparison["saved"]
    )
    assert app.discard_summary()["comparison"]["reference"] == reference
    with pytest.raises(ValueError, match="Save comparison context"):
        app.comparison_context(1)
    assert _set_comparison(app)["state"] == "refused"
    monkeypatch.setattr(application, "_publish_owned_file", publish)
    assert _command(app, "retry_save")["state"] == "completed"
    assert app.snapshot()["comparison"] == dict(comparison, saved=True)
    assert app.comparison_context(1)["reference"] == reference


@pytest.mark.parametrize("tamper", ["manifest", "record", "white_descriptor"])
def test_comparison_refuses_changed_source_or_fabricated_white_assumptions(app, tamper):
    state, _ = _complete(app, export=False)
    pair = {key: state["annotations"][0][key] for key in ("operation_id", "observation_id")}
    assert _set_comparison(app, pair, dict(pair, kind="retained-reading"))["state"] == "completed"
    folder = Path(state["folder"])
    target = folder / (state["operations"][0]["path"] if tamper == "record" else "batch.json")
    original = target.read_bytes()
    try:
        if tamper == "white_descriptor":
            changed = json.loads(original)
            changed["comparison"]["white_descriptor"]["xyz"][0] += 1
            target.write_text(json.dumps(changed))
        else:
            target.write_bytes(original + b"\n")
        with pytest.raises(ValueError, match="cannot be authenticated"):
            app.comparison_context(1)
        reviewed = review_saved(folder)
        if tamper in {"record", "white_descriptor"}:
            assert not reviewed["comparison"]["available"] and reviewed["comparison"]["reason"]
    finally:
        target.write_bytes(original)


def test_comparison_rejects_stale_version_invalid_identity_and_modified_theoretical_descriptor(app):
    assert (
        _set_comparison(app, dict(operation_id=str(uuid4()), observation_id="foreign"))["state"]
        == "refused"
    )
    white = dict(
        kind="theoretical-lab-reference",
        name="D65",
        observer="CIE1931_2",
        reference_y=100,
        unit="cd/m^2",
        scale="absolute; Y is luminance",
    )
    assert _set_comparison(app, white=white)["state"] == "completed"
    context = app.comparison_context(1)
    assert context["white"] == white and context["reference"] is None
    assert (
        _command(app, "set_comparison_context", dict(version=0, reference=None, white=None))[
            "state"
        ]
        == "refused"
    )
    changed = json.loads(app._manifest_bytes)
    changed["comparison"]["white_descriptor"]["xyz"][0] += 1
    with pytest.raises(ValueError, match="assumptions differ"):
        application._manifest(json.dumps(changed))


@pytest.mark.parametrize("fault", ["missing_budget", "boolean_budget", "extra_field"])
def test_owner_rejects_invalid_instruction_envelope_without_replacing_current_request(app, fault):
    _acquire(app)
    state, _ = _wait(app, lambda current: current["instructions"] is not None)
    pending = state["instructions"]
    event = application.deepcopy(
        next(item for item in app._events if item["kind"] == "instruction_required")
    )
    if fault == "missing_budget":
        del event["payload"]["response_timeout_seconds"]
    elif fault == "boolean_budget":
        event["payload"]["response_timeout_seconds"] = True
    else:
        event["payload"]["timeout_alias"] = 120
    app._record_event(json.dumps(event), app._operation)
    assert app.snapshot()["instructions"] == pending
    assert app._events[-1]["kind"] == "application_event_rejected"
    assert _command(app, "cancel", dict(command_id=app._active))["state"] == "completed"
    _wait(app, lambda current: current["phase"] == "idle")


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
def test_control_signal_between_prepare_and_intent_write_drains_the_held_handle(
    app, monkeypatch, signal
):
    held, fired = [], []
    prepare, touch = app._frontend.prepare, app._touch

    def prepared(request):
        operation = prepare(request)
        held.append(operation)
        return operation

    def interrupted_touch():
        if held and not fired:
            fired.append(True)
            raise signal("Interrupted before the intent was reserved")
        return touch()

    monkeypatch.setattr(app._frontend, "prepare", prepared)
    monkeypatch.setattr(app, "_touch", interrupted_touch)
    identity = str(uuid4())
    with pytest.raises(signal, match="before the intent"):
        _acquire(app, identity=identity)
    assert held[0].done
    state = _snapshot(app)
    assert state["phase"] == "idle" and state["operation_id"] is None
    assert state["software_stopped"] and state["can_measure"]
    outcome = app.command_outcome(identity)
    assert outcome["admission"] == "refused" and signal.__name__ in outcome["reason"]
    assert state["operations"][0]["command_id"] == identity
    assert state["operations"][0]["result"]["status"] == "cancelled"
    assert state["operations"][0]["state"] == "accepted"
    assert _acquire(app)["admission"] == "admitted"
    _wait(app, lambda current: current["phase"] == "idle", respond=True)


def test_recovery_evidence_is_synchronized_once_across_save_retries(app, monkeypatch):
    from color_measurement import exports

    sync_file, parse = exports._sync_file, application.result_from_json
    recovery_syncs = []

    def observed_sync(descriptor, path):
        if path.name.startswith("recovery-"):
            recovery_syncs.append(path)
        return sync_file(descriptor, path)

    def rejected_parse(text):
        raise ValueError("Terminal result rejected for this test")

    monkeypatch.setattr(exports, "_sync_file", observed_sync)
    monkeypatch.setattr(application, "result_from_json", rejected_parse)
    assert _acquire(app)["admission"] == "admitted"
    state, _ = _wait(app, lambda current: current["phase"] == "idle", respond=True)
    monkeypatch.setattr(application, "result_from_json", parse)
    assert state["operations"][0]["state"] == "terminal_unknown"
    assert len(recovery_syncs) == 1
    for _ in range(2):
        assert _command(app, "retry_save")["admission"] == "admitted"
    assert len(recovery_syncs) == 1
    assert review_saved(state["folder"])["operations"][0]["review_state"] == "terminal_unknown"


def test_comparison_context_stays_available_during_an_unrelated_metadata_save(app, monkeypatch):
    state, _ = _complete(app, export=False)
    reference = {key: state["annotations"][0][key] for key in ("operation_id", "observation_id")}
    assert _set_comparison(app, reference)["state"] == "completed"
    publish = application._publish_owned_file
    entered, release = threading.Event(), threading.Event()

    def held_publish(target, payload, previous):
        result = publish(target, payload, previous)
        if target.name == "batch.json" and json.loads(payload)["draft"]["name"] == "Held draft":
            entered.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(application, "_publish_owned_file", held_publish)
    draft, identity = app.snapshot()["draft"], str(uuid4())
    outcome = app.command(
        identity,
        app.snapshot()["generation"],
        "edit_draft",
        dict(
            draft_id=draft["id"], version=draft["version"], name="Held draft", notes="",
            options={}, number_samples=False,
        ),
        batch_id=state["batch_id"],
    )
    assert outcome["state"] == "pending" and entered.wait(2)
    try:
        # The file already holds the in-flight manifest; the saved comparison is
        # unchanged, so projections and analysis keep their authenticated context.
        assert app.snapshot()["phase"] == "saving"
        assert app.comparison_context(1)["reference"] == reference
    finally:
        release.set()
    _wait(app, lambda current: current["phase"] == "idle")
    assert app.command_outcome(identity)["state"] == "completed"
    assert app.comparison_context(1)["reference"] == reference


def test_control_signal_before_admission_leaves_a_reasoned_refusal_for_that_identity(
    app, monkeypatch
):
    state, _ = _complete(app, export=False)
    key = {field: state["annotations"][0][field] for field in ("operation_id", "observation_id")}
    original = app._annotation

    def interrupted(*args):
        raise KeyboardInterrupt("Interrupted while applying")

    monkeypatch.setattr(app, "_annotation", interrupted)
    identity, payload = str(uuid4()), dict(key, draft_version=0)
    send = lambda: app.command(  # noqa: E731
        identity, state["generation"], "discard_annotation_draft", payload,
        batch_id=state["batch_id"],
    )
    with pytest.raises(KeyboardInterrupt):
        send()
    monkeypatch.setattr(app, "_annotation", original)
    outcome = app.command_outcome(identity)
    assert outcome["admission"] == "refused" and outcome["state"] == "refused"
    assert outcome["reason"] == "Interrupted before admission: KeyboardInterrupt"
    assert send() == outcome


@pytest.mark.parametrize("boundary", ["before_admission", "after_admission"])
@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
def test_post_intent_control_recovers_before_drainer_startup(app, monkeypatch, boundary, signal):
    prepare, held = app._frontend.prepare, []
    first = signal("Interrupted after durable intent, before drainer startup")

    def prepared(request):
        operation = prepare(request)
        operation.dispatch = lambda: pytest.fail("Interrupted prepared handle was dispatched")
        held.append(operation)
        return operation

    lines, start = inspect.getsourcelines(BatchApplication._admit)
    admission_line = start + next(
        index for index, line in enumerate(lines)
        if 'outcome.update(admission="admitted", state="starting"' in line
    )
    armed, fired = False, []
    previous_trace = sys.gettrace()

    def interrupt(frame, event, argument):
        nonlocal armed
        if frame.f_code is BatchApplication._admit.__code__ and event == "line":
            if armed or (frame.f_lineno == admission_line and boundary == "before_admission"):
                fired.append(True)
                sys.settrace(previous_trace)
                raise first
            if frame.f_lineno == admission_line:
                armed = True
        return interrupt

    monkeypatch.setattr(app._frontend, "prepare", prepared)
    identity = str(uuid4())
    try:
        sys.settrace(interrupt)
        with pytest.raises(signal) as caught:
            _acquire(app, identity=identity)
        sys.settrace(previous_trace)
        assert caught.value is first and fired == [True] and len(held) == 1
        state = _snapshot(app)
        assert state["phase"] == "idle" and state["operation_id"] is None
        assert held[0].done and state["software_stopped"] and state["can_measure"]
        assert state["operations"][0]["state"] == "accepted"
        assert state["operations"][0]["result"]["status"] == "cancelled"
        assert state["operations"][0]["result"]["patches"] == []
        outcome = app.command_outcome(identity)
        assert signal.__name__ in outcome["reason"] and str(first) in outcome["reason"]
        assert _acquire(app, identity=identity) == outcome and len(held) == 1
    finally:
        sys.settrace(previous_trace)
        if app._active == identity and app._operation is not None:
            # Red-run cleanup occurs after the failed assertions and is not
            # evidence that the interrupted owner recovered on its own.
            entry = next(item for item in app._data["operations"] if item["command_id"] == identity)
            app._operation.cancel(app._operation.operation_id)
            app._run(entry, app._operation, dispatch=False)


def _unknown_demo_result(app, monkeypatch):
    with monkeypatch.context() as scoped:
        def reject_result(text):
            raise ValueError("Retain the original terminal result as recovery evidence")

        scoped.setattr(application, "result_from_json", reject_result)
        state, _ = _complete(app, export=False)
    assert state["operations"][0]["state"] == "terminal_unknown"
    return state


@pytest.mark.parametrize("change", ["changed", "missing"])
def test_recovery_publication_authenticates_known_bytes_before_retry(app, monkeypatch, change):
    state = _unknown_demo_result(app, monkeypatch)
    recovery = state["operations"][0]["recovery"]
    path = Path(state["folder"]) / recovery["path"]
    original = path.read_bytes()
    retained = application.deepcopy(app._unknown)
    manifest = app._manifest_bytes
    if change == "changed":
        path.write_bytes(original + b"\n")
    else:
        path.unlink()
    monkeypatch.setattr(app._frontend, "prepare", lambda *args: pytest.fail("Retry reacquired"))
    outcome = _command(app, "retry_save")
    if change == "changed":
        assert outcome["state"] == "refused" and outcome["reason"]
        assert path.read_bytes() == original + b"\n"
        assert app._manifest_bytes == manifest
    else:
        assert outcome["state"] == "completed"
        assert path.read_bytes() == original
        assert review_saved(state["folder"])["operations"][0]["review_state"] == "terminal_unknown"
    assert app._unknown == retained and app.snapshot()["sample_count"] == 0


@pytest.mark.parametrize("record", ["canonical", "recovery"])
def test_unsupported_directory_sync_does_not_repeat_completed_file_sync(app, monkeypatch, record):
    from color_measurement import exports

    sync_file, publish = exports._sync_file, application._publish_owned_file
    synchronized, refused = [], []

    def observe_sync(descriptor, path):
        if path.name.startswith("operation-" if record == "canonical" else "recovery-"):
            synchronized.append(path)
        return sync_file(descriptor, path)

    def refuse_manifest_once(target, payload, previous):
        if target.name == "batch.json" and app._retained and not refused:
            refused.append(True)
            raise OSError("Keep the published canonical record pending manifest acceptance")
        return publish(target, payload, previous)

    monkeypatch.setattr(exports, "_sync_file", observe_sync)
    monkeypatch.setattr(exports, "_sync_directory", lambda folder: False)
    if record == "canonical":
        monkeypatch.setattr(application, "_publish_owned_file", refuse_manifest_once)
        state, _ = _complete(app, export=False)
        assert refused == [True] and state["saved_count"] == 0
        monkeypatch.setattr(application, "_publish_owned_file", publish)
    else:
        state = _unknown_demo_result(app, monkeypatch)
    assert len(synchronized) == 1
    path = synchronized[0]
    original = path.read_bytes()
    retained = dict(app._payloads), application.deepcopy(app._unknown)
    monkeypatch.setattr(app._frontend, "prepare", lambda *args: pytest.fail("Retry reacquired"))
    for _ in range(2):
        assert _command(app, "retry_save")["state"] == "completed"
    assert len(synchronized) == 1 and path.read_bytes() == original
    assert (app._payloads, app._unknown) == retained
    receipt = next(
        item for item in app.snapshot()["durability"]["files"] if item["path"] == str(path)
    )
    assert receipt["directory_synced"] is False and receipt["limitations"]
    assert app.snapshot()["durability"]["status"] == "limited"


def test_saved_comparison_requires_the_accepted_object_at_its_version(app):
    state, _ = _complete(app, export=False)
    pair = {key: state["annotations"][0][key] for key in ("operation_id", "observation_id")}
    assert _set_comparison(app, pair)["state"] == "completed"
    comparison = application.deepcopy(app._data["comparison"])
    manifest, retained = app._manifest_bytes, dict(app._payloads)
    try:
        # An unchanged version alone cannot authenticate different choices.
        app._data["comparison"]["reference"] = None
        assert not app.snapshot()["comparison"]["saved"]
        with pytest.raises(ValueError, match="Save comparison context"):
            app.comparison_context(comparison["version"])
    finally:
        app._data["comparison"] = comparison
    assert app.comparison_context(comparison["version"])["reference"] == pair
    assert app._manifest_bytes == manifest and app._payloads == retained


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_rescue_after_manifest_acceptance_restores_saved_comparison_atomically(
    app, tmp_path, monkeypatch, failure,
):
    state, _ = _complete(app, export=False)
    pair = {key: state["annotations"][0][key] for key in ("operation_id", "observation_id")}
    _automatic_companion_state(app)
    publish = application._publish_owned_file

    def refuse_comparison(*args):
        raise OSError("Keep the changed comparison unsaved")

    monkeypatch.setattr(application, "_publish_owned_file", refuse_comparison)
    assert _set_comparison(app, pair)["state"] == "failed"
    monkeypatch.setattr(application, "_publish_owned_file", publish)
    old_folder, old_bytes = app._folder, app._manifest_bytes
    old_data = application.deepcopy(app._data)
    old_saved = application.deepcopy(app._saved_comparison)
    old_version, old_lock = app._saved_comparison_version, app._lock
    retained = dict(app._payloads)
    assert old_saved != old_data["comparison"] and old_version == 0
    target = tmp_path / "accepted-before-failure"
    first = failure("Rescue canonical acceptance interrupted after destination acceptance")
    accepted = []

    accept_manifest = app._accept_manifest

    def interrupt_acceptance(payload):
        accept_manifest(payload)
        if app._folder != target:
            return
        assert app._folder == target
        assert app._saved_comparison == old_data["comparison"]
        assert app._saved_comparison_version == old_data["comparison"]["version"]
        accepted.append(app._manifest_bytes)
        assert (target / "batch.json").read_bytes() == app._manifest_bytes
        raise first

    monkeypatch.setattr(app, "_accept_manifest", interrupt_acceptance)
    monkeypatch.setattr(app._frontend, "prepare", lambda *args: pytest.fail("Rescue reacquired"))
    if issubclass(failure, Exception):
        outcome = _command(app, "another_folder", dict(folder=str(target)))
        assert outcome["state"] == "refused" and str(first) in outcome["reason"]
    else:
        with pytest.raises(failure) as caught:
            _command(app, "another_folder", dict(folder=str(target)))
        assert caught.value is first
    assert len(accepted) == 1 and accepted[0] != old_bytes
    assert app._folder == old_folder and app._lock is old_lock
    assert app._manifest_bytes == old_bytes
    assert (old_folder / "batch.json").read_bytes() == old_bytes
    assert app._data == old_data and app._saved_comparison_version == old_version
    assert app._saved_comparison == old_saved and app._payloads == retained
    assert not app.snapshot()["comparison"]["saved"]
    with pytest.raises(ValueError, match="Save comparison context"):
        app.comparison_context(old_data["comparison"]["version"])
    with _folder_lock(target, ".batch.lock"):
        assert (target / "batch.json").read_bytes() == accepted[0]


def _retire_exit_lock(app, monkeypatch, name):
    from color_measurement import exports

    _automatic_companion_state(app)
    lock = app._lock if name == ".batch.lock" else _folder_lock(app._folder, name).__enter__()
    descriptor, close = lock.descriptor, exports.os.close
    first = OSError("Controlled own publication-lock close failure")

    def refuse_close(value):
        if value == descriptor:
            raise first
        return close(value)

    monkeypatch.setattr(exports.os, "close", refuse_close)
    try:
        if name == ".batch.lock":
            outcome = _command(app, "close", dict(discard_token=app.discard_summary()["token"]))
            assert outcome["admission"] == "admitted" and outcome["state"] == "failed"
        else:
            with pytest.raises(OSError) as caught:
                lock.__exit__(None, None, None)
            assert caught.value is first
    finally:
        monkeypatch.setattr(exports.os, "close", close)
    assert lock.release_failure is first
    assert lock.directory_identity == app._folder_identity
    assert any(retired is lock for retired in exports._RETIRED_PUBLICATION_LOCKS.values())
    return lock, descriptor, close


def _retain_actual_unconfirmed_demo_operation(app, monkeypatch):
    prepare = app._frontend.prepare

    def prepared(request, **kwargs):
        operation = prepare(request, **kwargs)
        result = operation.result_json

        def unconfirmed():
            value = json.loads(result())
            value.update(software_stopped=False, instrument_state="unknown")
            return json.dumps(value)

        operation.result_json = unconfirmed
        return operation

    monkeypatch.setattr(app._frontend, "prepare", prepared)
    assert _acquire(app)["admission"] == "admitted"
    state, _ = _wait(app, lambda current: current["phase"] == "cleanup_uncertain", respond=True)
    _wait(app, lambda current: not current["companions"]["saving"])
    assert state["sample_count"] == state["saved_count"] == 1
    assert app._active is not None and app._operation is not None and app._operation.done


def test_abandoned_unconfirmed_owner_can_exit_after_batch_lock_release_failure(app, monkeypatch):
    from color_measurement import exports

    _retain_actual_unconfirmed_demo_operation(app, monkeypatch)
    lock, close = app._lock, exports.os.close
    descriptor = lock.descriptor

    def refuse_close(value):
        if value == descriptor:
            raise OSError("Controlled abandoned batch-lock close failure")
        return close(value)

    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(exports.os, "close", refuse_close)
            failed = _command(app, "abandon", dict(
                discard_token=app.discard_summary()["token"],
                reason="Explicit abandonment of terminal unconfirmed cleanup",
            ))
        assert failed["admission"] == "admitted" and failed["state"] == "failed"
        state = app.snapshot()
        assert state["status"] == "abandoned" and not app._closed
        assert not state["software_stopped"] and app in application._UNCERTAIN_OWNERS
        original = (app._folder / "batch.json").read_bytes()
        saved = json.loads(original)
        assert saved["status"] == "abandoned" and not saved["shutdown"]["cleanup_confirmed"]
        assert state["process_exit_ready"]

        # Another uncertain acquisition is not covered by this owner's consent.
        foreign = object()
        application._UNCERTAIN_OWNERS.append(foreign)
        assert not app.snapshot()["process_exit_ready"]
        refused = _command(app, "confirm_process_exit", dict(
            discard_token=app.discard_summary()["token"],
        ))
        assert refused["admission"] == "refused"
        application._UNCERTAIN_OWNERS.remove(foreign)

        token = app.discard_summary()["token"]
        assert _command(app, "confirm_process_exit", dict(discard_token="stale"))[
            "admission"
        ] == "refused"

        def forbidden(*args, **kwargs):
            pytest.fail("Retired process-exit acknowledgment attempted filesystem cleanup")

        with monkeypatch.context() as scoped:
            scoped.setattr(application, "_require_publication_cleanup", forbidden)
            scoped.setattr(app, "_release_lock", forbidden)
            accepted = _command(app, "confirm_process_exit", dict(discard_token=token))
        assert (accepted["admission"], accepted["state"], accepted["effect"]) == (
            "admitted", "completed", "process_exit_acknowledged",
        )
        assert not app._closed and app._lock is lock and lock.descriptor == descriptor
        assert not app.snapshot()["software_stopped"]
        assert app in application._UNCERTAIN_OWNERS
        assert (app._folder / "batch.json").read_bytes() == original
        with pytest.raises(ValueError, match="Unconfirmed acquisition cleanup"):
            _demo_batch(app._folder.parent / "unsafe-new-owner")
    finally:
        _clean_retired_exit_lock(app, lock, descriptor, close)


def _clean_retired_exit_lock(app, lock, descriptor, close):
    from color_measurement import exports

    # The controlled failure never called the real close. This test owns that
    # known-open descriptor; production never retries an ambiguous descriptor.
    close(descriptor)
    lock.descriptor = None
    exports._RETIRED_PUBLICATION_LOCKS.clear()
    if app._lock is lock:
        app._lock = None


@pytest.mark.parametrize("name", [".batch.lock", ".ti3-export.lock"])
@pytest.mark.parametrize("rename", [False, True])
def test_own_retirement_acknowledges_process_exit_without_io(
    app, tmp_path, monkeypatch, name, rename,
):
    from color_measurement import exports

    if rename and os.name == "nt":
        pytest.skip("Windows refuses renaming a folder whose ambiguous batch lock remains open")
    _complete(app, export=False)
    publish = application._publish_owned_file

    def refuse_metadata(*args):
        raise OSError("Retain an unsaved next target for explicit discard")

    monkeypatch.setattr(application, "_publish_owned_file", refuse_metadata)
    draft = app.snapshot()["draft"]
    assert _command(app, "edit_draft", dict(
        draft_id=draft["id"], version=draft["version"], name="Unsaved next target",
        notes="Retain this until explicit discard", options={}, number_samples=False,
    ))["state"] == "failed"
    monkeypatch.setattr(application, "_publish_owned_file", publish)
    lock, descriptor, close = _retire_exit_lock(app, monkeypatch, name)
    try:
        token = app.discard_summary()["token"]
        assert app.discard_summary()["drafts"] is not None
        assert _command(app, "close", dict(discard_token=token))["state"] == "refused"
        assert _command(app, "abandon", dict(
            discard_token=token, reason="Operator requested process exit",
        ))["state"] == "refused"
        if rename:
            app._folder.rename(tmp_path / "renamed-owned-folder")
        state = app.snapshot()
        assert state["process_exit_ready"] and not state["process_exit_acknowledged"]
        token = app.discard_summary()["token"]
        retained = dict(app._payloads), application.deepcopy(app._unknown)
        data, manifest, owned_lock = application.deepcopy(app._data), app._manifest_bytes, app._lock
        retired = tuple(exports._RETIRED_PUBLICATION_LOCKS.items())

        def forbidden(*args, **kwargs):
            pytest.fail("Process-exit acknowledgment performed I/O or publication cleanup")

        with monkeypatch.context() as scoped:
            for member in ("_read_regular", "_publish_owned_file", "_folder_lock",
                           "_require_publication_cleanup"):
                scoped.setattr(application, member, forbidden)
            for member in ("_save", "_persist", "_assert_folder", "_release_lock"):
                scoped.setattr(app, member, forbidden)
            for member in ("close", "open", "stat", "lstat", "read", "write"):
                scoped.setattr(exports.os, member, forbidden)
            identity = str(uuid4())
            outcome = app.command(identity, state["generation"], "confirm_process_exit",
                                  dict(discard_token=token), batch_id=state["batch_id"])
            assert outcome["admission"] == "admitted" and outcome["state"] == "completed"
            assert outcome["effect"] == "process_exit_acknowledged"
            assert app.command(identity, state["generation"], "confirm_process_exit",
                               dict(discard_token=token), batch_id=state["batch_id"]) == outcome
            changed = app.command(identity, state["generation"], "confirm_process_exit",
                                  dict(discard_token="stale"), batch_id=state["batch_id"])
            assert changed["admission"] == "refused" and "different content" in changed["reason"]
            assert not app._closed and app._lock is owned_lock and lock.descriptor == descriptor
            assert (app._payloads, app._unknown) == retained
            assert app._data == data and app._manifest_bytes == manifest
            assert tuple(exports._RETIRED_PUBLICATION_LOCKS.items()) == retired
        assert app.snapshot()["process_exit_acknowledged"]
    finally:
        _clean_retired_exit_lock(app, lock, descriptor, close)


@pytest.mark.parametrize("block", [
    "starting", "active", "metadata", "unconfirmed_stop", "uncertain_acquisition",
    "nonterminal_operation", "removed_retirement", "changed_retired_object", "changed_contents",
])
def test_process_exit_acknowledgment_fences_current_retirement_and_stop(app, monkeypatch, block):
    from copy import copy
    from types import SimpleNamespace

    from color_measurement import exports

    lock, descriptor, close = _retire_exit_lock(app, monkeypatch, ".batch.lock")
    token = app.discard_summary()["token"]
    state = app.snapshot()
    try:
        with monkeypatch.context() as scoped:
            if block == "starting":
                scoped.setattr(app, "_starting", True)
            elif block == "active":
                scoped.setattr(app, "_active", str(uuid4()))
            elif block == "metadata":
                scoped.setattr(app, "_metadata_saving", True)
            elif block == "unconfirmed_stop":
                scoped.setattr(app, "_stop_confirmed", False)
            elif block == "uncertain_acquisition":
                scoped.setattr(application, "_UNCERTAIN_OWNERS", [object()])
            elif block == "nonterminal_operation":
                scoped.setattr(app, "_operation", SimpleNamespace(done=False))
            elif block == "removed_retirement":
                scoped.setattr(exports, "_RETIRED_PUBLICATION_LOCKS", {})
            elif block == "changed_retired_object":
                scoped.setattr(exports, "_RETIRED_PUBLICATION_LOCKS", {lock.path: copy(lock)})
            else:
                app._data["draft"]["notes"] = "Changed after confirmation was shown"
            assert app.discard_summary()["token"] != token

            def forbidden(*args, **kwargs):
                pytest.fail("Refused process-exit acknowledgment attempted cleanup")

            scoped.setattr(application, "_require_publication_cleanup", forbidden)
            scoped.setattr(app, "_release_lock", forbidden)
            outcome = app.command(str(uuid4()), state["generation"], "confirm_process_exit",
                                  dict(discard_token=token), batch_id=state["batch_id"])
            assert outcome["state"] == "refused" and not app._process_exit_acknowledged
            assert not app._closed and lock.descriptor == descriptor
    finally:
        _clean_retired_exit_lock(app, lock, descriptor, close)


def test_other_retired_folders_do_not_authorize_or_stale_own_process_exit(
    app, tmp_path, monkeypatch,
):
    from color_measurement import exports

    other = _demo_batch(tmp_path / "other-owner")
    other.bind()
    lock, descriptor, close = _retire_exit_lock(other, monkeypatch, ".batch.lock")
    try:
        assert not app.snapshot()["process_exit_ready"]
        assert _command(app, "confirm_process_exit", dict(
            discard_token=app.discard_summary()["token"],
        ))["state"] == "refused"
        own, own_descriptor, own_close = _retire_exit_lock(app, monkeypatch, ".batch.lock")
        try:
            token = app.discard_summary()["token"]
            with monkeypatch.context() as scoped:
                scoped.setattr(exports, "_RETIRED_PUBLICATION_LOCKS", {own.path: own})
                assert app.discard_summary()["token"] == token
            assert _command(app, "confirm_process_exit", dict(discard_token=token))["effect"] == \
                "process_exit_acknowledged"
            assert not app._closed and not other._closed
        finally:
            _clean_retired_exit_lock(app, own, own_descriptor, own_close)
    finally:
        _clean_retired_exit_lock(other, lock, descriptor, close)


@pytest.mark.parametrize("publication_phase", ["before_replace", "after_replace"])
@pytest.mark.parametrize("tamper", ["manifest", "native_record", "comparison"])
def test_unrelated_metadata_comparison_window_keeps_exact_authentication(
    app, monkeypatch, publication_phase, tamper,
):
    state, _ = _complete(app, export=False)
    pair = {key: state["annotations"][0][key] for key in ("operation_id", "observation_id")}
    _set_comparison(app, pair, dict(pair, kind="retained-reading"))
    _automatic_companion_state(app)
    publish = application._publish_owned_file
    held, release = threading.Event(), threading.Event()

    def pause_publication(*args):
        if Path(args[0]).name != "batch.json":
            return publish(*args)
        result = publish(*args) if publication_phase == "after_replace" else None
        held.set()
        assert release.wait(3)
        return result if result is not None else publish(*args)

    monkeypatch.setattr(application, "_publish_owned_file", pause_publication)
    state = app.snapshot()
    draft = state["draft"]
    outcome = app.command(str(uuid4()), state["generation"], "edit_draft", dict(
        draft_id=draft["id"], version=draft["version"], name="Another next sample",
        notes=draft["notes"], options=draft["options"], number_samples=draft["number_samples"],
    ), batch_id=state["batch_id"])
    original = target = comparison = None
    try:
        assert outcome["admission"] == "admitted" and held.wait(3)
        assert app.comparison_context(1)["reference"] == pair
        if tamper == "comparison":
            comparison = application.deepcopy(app._data["comparison"])
            app._data["comparison"]["reference"] = None
            assert not app.snapshot()["comparison"]["saved"]
        else:
            target = Path(state["folder"]) / (
                state["operations"][0]["path"] if tamper == "native_record" else "batch.json"
            )
            original = target.read_bytes()
            target.write_bytes(original + b"\n")
        with pytest.raises(ValueError, match="authenticated|Save comparison context"):
            app.comparison_context(1)
    finally:
        if target is not None:
            target.write_bytes(original)
        if comparison is not None:
            app._data["comparison"] = comparison
        release.set()
        _automatic_companion_state(app)
    assert app.comparison_context(1)["reference"] == pair


def test_inflight_comparison_authenticates_frozen_mode_without_operations(app):
    original = app._manifest_bytes
    changed = json.loads(original)
    changed["mode"] = "reflective_spot"
    pending = application._json(changed).encode("ascii")
    assert application._manifest(pending)["mode"] == "reflective_spot"
    path = app._folder / "batch.json"
    before = application.deepcopy(app._data)
    try:
        app._metadata_saving, app._publishing_manifest = True, pending
        path.write_bytes(pending)
        with pytest.raises(ValueError, match="frozen context cannot be authenticated"):
            app.comparison_context(0)
    finally:
        path.write_bytes(original)
        app._metadata_saving, app._publishing_manifest = False, None
    assert app._data == before and app._manifest_bytes == original
    assert app.comparison_context(0)["reference"] is None


@pytest.mark.parametrize("mode,options", [
    ("unknown_spot", {}), ("unknown_strip", {}), (None, {}),
    ("emissive_spot", {"unknown_option": True}),
    ("emissive_spot", {"xyz_only": "yes"}), ("emissive_spot", []),
])
def test_generic_binding_rejects_configuration_before_files_or_acquisition(
    tmp_path, monkeypatch, mode, options,
):
    front = _demo_frontend()
    target = tmp_path / "rejected"

    def forbidden(*args, **kwargs):
        pytest.fail("Constructor attempted acquisition")

    monkeypatch.setattr(type(front._meter), "_open_session", forbidden)
    with pytest.raises((TypeError, ValueError)):
        BatchApplication(target, frontend=front, mode=mode, options=options)
    assert not target.exists() and not front._ownership.locked()
    with pytest.raises(TypeError, match="already-bound"):
        BatchApplication(target, frontend=object(), mode="emissive_spot", options={})
    assert not target.exists()


@pytest.mark.parametrize("quantity", ["xyz-only", "xyz-and-spectrum", "spectrum-only"])
def test_generic_spot_preserves_native_quantities_context_repeat_and_review(
    tmp_path, monkeypatch, quantity,
):
    from dataclasses import replace

    if quantity == "xyz-only":
        front = next(front for front in demo_frontends() if not json.loads(
            front.describe_json()
        )["spectral"] and any(
            mode["mode"] == "emissive_spot" and mode["available"]
            for mode in json.loads(front.describe_json())["modes"]
        ))
        mode, options = "emissive_spot", {}
    else:
        front, mode, options = _demo_frontend("spectrolino"), "reflective_spot", {}
    if quantity == "spectrum-only":
        measure = type(front._meter).measure

        def without_xyz(meter, *args, **kwargs):
            measurement = measure(meter, *args, **kwargs)
            return replace(measurement, patches=tuple(
                replace(patch, xyz=None) for patch in measurement.patches
            ))

        monkeypatch.setattr(type(front._meter), "measure", without_xyz)
    owner = BatchApplication(tmp_path / quantity, frontend=front, mode=mode, options=options)
    try:
        state = owner.bind()
        descriptor = json.loads(front.describe_json())
        assert state["mode"] == mode and state["descriptor"] == descriptor
        assert state["can_calibrate"] == any(
            item["workflow"] == "calibrate" and item["available"]
            for entry in descriptor["modes"] if entry["mode"] == mode
            for item in entry["workflows"]
        )
        state, _ = _complete(owner)
        result = state["operations"][0]["result"]
        native = result["patches"][0]["observation"]["backend_reported"]
        assert (native["xyz"] is None) == (quantity == "spectrum-only")
        assert (native["spectrum"] is None) == (quantity == "xyz-only")
        assert result["device_id"] == descriptor["device_id"]
        assert result["device"] == descriptor["device"] and result["mode"] == mode
        assert result["provenance"] == "simulated" and result["software_stopped"]
        draft = state["draft"]
        repeat = _command(owner, "repeat_sample", dict(
            context_id=state["context_id"], draft_id=draft["id"], draft_version=draft["version"],
            operation_id=result["operation_id"],
            observation_id=result["patches"][0]["observation"]["observation_id"],
        ))
        assert repeat["admission"] == "admitted"
        after, _ = _wait(owner, lambda state: state["phase"] == "idle", respond=True)
        assert after["draft"] == draft and after["sample_count"] == 2
        if after["can_calibrate"]:
            calibrated, _ = _complete(owner, "calibrate")
            assert calibrated["sample_count"] == 2
            assert calibrated["operations"][-1]["result"]["patches"] == []
        assert _command(owner, "finish")["state"] == "completed"
        reviewed = review_saved(state["folder"])
        assert reviewed["manifest"]["schema_version"] == 5
        assert reviewed["manifest"]["mode"] == mode
        assert reviewed["sample_count"] == 2
        assert all(item["review_state"] == "accepted" for item in reviewed["operations"])
        assert reviewed["operations"][0]["result"]["patches"][0]["observation"][
            "backend_reported"
        ] == native
    finally:
        _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


def test_finished_snapshot_eligibility_does_not_reparse_the_saved_batch(app, monkeypatch):
    assert _command(app, "finish")["state"] == "completed"
    assert app.snapshot()["can_start_new_batch"] is True
    load = application._load

    def no_manifest_parse(payload):
        assert payload is not app._manifest_bytes, "Eligibility reparsed the accepted batch"
        return load(payload)

    # Polling a finished batch must not reconstruct a discard report or parse
    # its immutable accepted manifest again. Diagnostics still block New batch.
    with monkeypatch.context() as guard:
        guard.setattr(application, "_load", no_manifest_parse)
        for _ in range(3):
            assert app.snapshot()["can_start_new_batch"] is True
        app._data["native_diagnostics"]["debug_level"] = 1
        assert app.snapshot()["can_start_new_batch"] is False
    summary = app.discard_summary()["native_diagnostics"]
    assert summary["settings"]["debug_level"] == 1
    # Accepting different bytes invalidates the retained settings automatically.
    app._persist()
    assert app.discard_summary()["native_diagnostics"] is None
    assert _command(app, "retry_save")["state"] == "completed"
    assert app.snapshot()["can_start_new_batch"] is True


def test_generic_new_batch_selects_fresh_context_only_after_durable_finish(tmp_path):
    first = _demo_batch(tmp_path / "first")
    second_front = _demo_frontend("spectrolino")
    target = tmp_path / "second"
    options = {"xyz_only": True}
    try:
        assert first.snapshot()["can_start_new_batch"] is False
        with pytest.raises(ValueError, match="durable Finish"):
            first.new_batch(target, frontend=second_front, mode="reflective_spot", options=options)
        assert not target.exists()
        assert _command(first, "finish")["state"] == "completed"
        # The published flag is the rule new_batch enforces, in both directions.
        assert first.snapshot()["can_start_new_batch"] is True
        first._save_reason = "Synthetic Finish publication failure"
        assert first.snapshot()["can_start_new_batch"] is False
        with pytest.raises(ValueError, match="durable Finish"):
            first.new_batch(target, frontend=second_front, mode="reflective_spot", options=options)
        first._save_reason = None
        assert first.snapshot()["can_start_new_batch"] is True
        second = first.new_batch(
            target, frontend=second_front, mode="reflective_spot", options=options,
        )
        try:
            options["xyz_only"] = False
            state = second.bind()
            assert second._frontend is second_front
            assert state["context_id"] != first.snapshot()["context_id"]
            assert state["descriptor"]["device_id"] == "demo:spectrolino"
            assert state["mode"] == "reflective_spot"
            assert state["draft"]["options"] == {"xyz_only": True}
            state, _ = _complete(second)
            assert state["operations"][0]["result"]["patches"][0]["observation"][
                "backend_reported"
            ]["spectrum"] is None
        finally:
            _command(second, "close", dict(discard_token=second.discard_summary()["token"]))
    finally:
        _command(first, "close", dict(discard_token=first.discard_summary()["token"]))


@pytest.mark.parametrize("change", ["mode", "device", "provenance", "capability", "workflow"])
def test_generic_saved_review_refuses_foreign_frozen_context(app, change):
    state, _ = _complete(app)
    path = Path(state["folder"]) / "batch.json"
    manifest = json.loads(path.read_text())
    if change == "mode":
        manifest["mode"] = "reflective_spot"
    elif change == "workflow":
        manifest["operations"][0]["request"]["workflow"] = "refresh_frequency"
    elif change == "capability":
        manifest["descriptor"]["capability_id"] = "0" * 64
    else:
        descriptor = manifest["descriptor"]
        if change == "device":
            descriptor["device"]["model"] = "Another model"
        else:
            descriptor["provenance"] = "replay"
        descriptor["capability_id"] = hashlib.sha256(json.dumps(
            {key: value for key, value in descriptor.items() if key != "capability_id"},
            sort_keys=True, allow_nan=False,
        ).encode("utf-8")).hexdigest()
        manifest["operations"][0]["request"]["capability_id"] = descriptor["capability_id"]
    path.write_text(json.dumps(manifest))
    if change in {"mode", "capability", "workflow"}:
        with pytest.raises(ValueError):
            review_saved(path.parent)
    else:
        reviewed = review_saved(path.parent)
        assert reviewed["operations"][0]["review_state"] != "accepted"
        assert reviewed["sample_count"] == 0


def test_generic_calibration_admission_follows_selected_descriptor(tmp_path, monkeypatch):
    front = _demo_frontend()
    descriptor = front._descriptor

    def without_calibration():
        value = descriptor()
        for entry in value["modes"]:
            entry["workflows"] = [item for item in entry["workflows"]
                                  if item["workflow"] != "calibrate"]
        return value

    monkeypatch.setattr(front, "_descriptor", without_calibration)
    owner = BatchApplication(tmp_path / "no-calibrate", frontend=front,
                             mode="emissive_spot", options={})
    try:
        state = owner.bind()
        assert state["can_measure"] and not state["can_calibrate"]
        outcome = _acquire(owner, "calibrate")
        assert outcome["admission"] == "refused" and "workflow" in outcome["reason"].lower()
        assert owner.snapshot()["operations"] == [] and not front._ownership.locked()
        state, _ = _complete(owner)
        assert state["sample_count"] == 1
    finally:
        _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


@pytest.mark.parametrize("field", ["provenance", "model", "serial", "mode", "capability_id"])
def test_generic_retained_result_cannot_replace_frozen_source(app, field):
    from dataclasses import replace

    from color_measurement.operation_records import result_from_json

    state, _ = _complete(app)
    entry = app._data["operations"][0]
    result = result_from_json(json.dumps(state["operations"][0]["result"]))
    if field == "provenance":
        changed = replace(result, provenance="replay", patches=tuple(
            replace(patch, observation=replace(patch.observation, provenance="replay"))
            for patch in result.patches
        ))
    elif field in {"model", "serial"}:
        device = replace(result.device, **(
            {"model": "Another model"} if field == "model" else
            {"serial": "Another serial", "serial_encoding": "utf8"}
        ))
        changed = replace(result, device=device, patches=tuple(
            replace(patch, observation=replace(patch.observation, device=device))
            for patch in result.patches
        ))
    elif field == "mode":
        request = json.loads(result.request_json)
        request["mode"] = "emissive_tele"
        changed = replace(
            result, mode=request["mode"], request_json=json.dumps(request), patches=tuple(
                replace(patch, observation=replace(
                    patch.observation, requested_mode=request["mode"], actual_mode=request["mode"],
                )) for patch in result.patches
            ),
        )
    else:
        request = json.loads(result.request_json)
        request["capability_id"] = "0" * 64
        changed = replace(result, request_json=json.dumps(request))
    with pytest.raises(ValueError, match="reserved identity/context"):
        application._check_result(changed, entry, app._data["descriptor"])


@pytest.mark.parametrize("entry_point", ["constructor", "new_batch"])
@pytest.mark.parametrize("boundary", ["batch_binding", "frontend_preparation"])
def test_shown_descriptor_changes_refuse_before_folder_or_owner_mutation(
    tmp_path, monkeypatch, entry_point, boundary,
):
    front = _demo_frontend()
    initial = json.loads(front.describe_json())
    changed = application.deepcopy(initial)
    changed["device"]["model"] = "Changed after operator selection"
    changed["capability_id"] = hashlib.sha256(json.dumps(
        {key: value for key, value in changed.items() if key != "capability_id"},
        sort_keys=True, allow_nan=False,
    ).encode("utf-8")).hexdigest()
    stable_reads = 3 if boundary == "batch_binding" else 4
    described, prepared = [], []
    prepare = front.prepare

    def describe(device_id=None):
        assert device_id is None or device_id == initial["device_id"]
        value = initial if len(described) < stable_reads else changed
        described.append(value["capability_id"])
        return json.dumps(value)

    def prepare_once(request, **kwargs):
        prepared.append(json.loads(request))
        return prepare(request, **kwargs)

    def forbidden_acquisition(*args, **kwargs):
        pytest.fail("Descriptor validation attempted instrument acquisition")

    monkeypatch.setattr(front, "describe_json", describe)
    monkeypatch.setattr(front, "prepare", prepare_once)
    monkeypatch.setattr(type(front._meter), "_open_session", forbidden_acquisition)
    # Registration, displayed settings and the session precheck all see A.
    shown = json.loads(front.describe_json())
    assert json.loads(front.describe_json()) == shown
    assert json.loads(front.describe_json()) == shown
    source = _demo_batch(tmp_path / "source") if entry_point == "new_batch" else None
    created = None
    target = tmp_path / "must-not-create"
    if source is not None:
        assert _command(source, "finish")["state"] == "completed"
        original = source.snapshot(), application.deepcopy(source._data), source._manifest_bytes
        old_lock = source._lock
    try:
        reason = "descriptor changed" if boundary == "batch_binding" else "capability snapshot"
        with pytest.raises(ValueError, match=reason):
            configuration = dict(
                frontend=front, mode="emissive_spot", options={}, expected_descriptor=shown,
            )
            created = (
                BatchApplication(target, **configuration) if source is None
                else source.new_batch(target, **configuration)
            )
        assert not target.exists() and not front._ownership.locked()
        assert len(described) == stable_reads + 1
        assert len(prepared) == (0 if boundary == "batch_binding" else 1)
        if source is not None:
            assert (source.snapshot(), source._data, source._manifest_bytes) == original
            assert source._lock is old_lock
            assert (source._folder / "batch.json").read_bytes() == original[2]
    finally:
        for owner in (created, source):
            if owner is not None:
                _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


@pytest.mark.parametrize("entry_point", ["constructor", "new_batch"])
@pytest.mark.parametrize("shown_selection", [True, False])
def test_batch_binding_uses_shown_semantic_content_or_current_headless_facts(
    tmp_path, monkeypatch, entry_point, shown_selection,
):
    front = _demo_frontend()
    shown = json.loads(front.describe_json())
    current = application.deepcopy(shown)
    if not shown_selection:
        current["device"]["model"] = "Current headless selection"
        current["capability_id"] = hashlib.sha256(json.dumps(
            {key: value for key, value in current.items() if key != "capability_id"},
            sort_keys=True, allow_nan=False,
        ).encode("utf-8")).hexdigest()

    def reverse_members(value):
        if isinstance(value, dict):
            return {key: reverse_members(value[key]) for key in reversed(value)}
        if isinstance(value, list):
            return [reverse_members(item) for item in value]
        return value

    prepare, prepared = front.prepare, []

    def prepare_once(request, **kwargs):
        prepared.append(json.loads(request))
        return prepare(request, **kwargs)

    def forbidden_acquisition(*args, **kwargs):
        pytest.fail("Batch construction attempted instrument acquisition")

    monkeypatch.setattr(front, "describe_json", lambda device_id=None: json.dumps(
        reverse_members(current), indent=1,
    ))
    monkeypatch.setattr(front, "prepare", prepare_once)
    monkeypatch.setattr(type(front._meter), "_open_session", forbidden_acquisition)
    source = _demo_batch(tmp_path / "source") if entry_point == "new_batch" else None
    created = None
    if source is not None:
        assert _command(source, "finish")["state"] == "completed"
        original = source.snapshot(), application.deepcopy(source._data), source._manifest_bytes
        old_lock = source._lock
    try:
        configuration = dict(frontend=front, mode="emissive_spot", options={})
        if shown_selection:
            configuration["expected_descriptor"] = shown
        target = tmp_path / "selected"
        created = (
            BatchApplication(target, **configuration) if source is None
            else source.new_batch(target, **configuration)
        )
        assert target.is_dir() and created.snapshot()["descriptor"] == current
        assert len(prepared) == 1 and not front._ownership.locked()
        assert prepared[0]["capability_id"] == current["capability_id"]
        shown["device"]["model"] = "Later caller edit"
        assert created.snapshot()["descriptor"] == current
        if source is not None:
            assert (source.snapshot(), source._data, source._manifest_bytes) == original
            assert source._lock is old_lock
            assert (source._folder / "batch.json").read_bytes() == original[2]
    finally:
        for owner in (created, source):
            if owner is not None:
                _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


@pytest.mark.parametrize("persona,mode,workflow,ids,layout", [
    ("colormunki", "emissive_strip", "strip", ["A1", "A2"], {}),
    ("spectroscan", "reflective_xy", "xy", ["A1", "A2"], {"passes": 1, "steps": 2}),
    ("dtp20", "saved_chart", "chart", ["A1", "A2"], {"passes": 1, "steps": 2, "sheets": [1]}),
    ("colormunki_smile", "emissive_spot", "stored_refresh", [], {}),
    ("colorhug", "emissive_spot", "mode_snapshot", [], {}),
    ("colormunki_smile", "emissive_spot", "refresh_frequency", ["refresh"],
     {"stimulus_id": "external-demo", "stimulus_description": "Synthetic external stimulus"}),
    ("i1_display_3", "emissive_spot", "display_timing", ["timing"],
     {"stimulus_id": "draw-demo", "stimulus_description": "Synthetic controlled stimulus",
      "display_id": "test-surface"}),
])
def test_generic_workflow_uses_durable_owner_and_typed_retention(
    tmp_path, persona, mode, workflow, ids, layout,
):
    owner = BatchApplication(tmp_path / workflow, frontend=_demo_frontend(persona),
                             mode=mode, options={})
    try:
        state = owner.bind()
        draft = state["draft"]
        payload = dict(context_id=state["context_id"], draft_id=draft["id"],
                       draft_version=draft["version"], workflow=workflow, patch_ids=ids,
                       layout=layout)
        identity = str(uuid4())
        outcome = _command(owner, "run_workflow", payload, identity=identity)
        assert outcome["admission"] == "admitted", outcome
        deadline = time.monotonic() + 5
        presentations = []
        while time.monotonic() < deadline:
            state = owner.snapshot()
            if state["instructions"]:
                prompt = state["instructions"]
                assert _command(owner, "respond", dict(
                    operation_id=prompt["operation_id"], request_id=prompt["request_id"],
                    response=prompt["allowed_responses"][0],
                ))["admission"] == "admitted"
            elif state["presentation"]:
                prompt = state["presentation"]
                # This is labelled software proof, not a physical draw-completion claim.
                stamp = dict(phase=prompt["phase"], stimulus_id=prompt["stimulus_id"],
                             display_id=prompt["display_id"], clock_id="synthetic-test-client",
                             presented_at_seconds=time.monotonic(), source="client_draw_completed")
                wrong = _command(owner, "presented", dict(
                    operation_id=prompt["operation_id"], request_id="stale", stamp=stamp,
                ))
                assert wrong["admission"] == "refused" and owner.snapshot()["presentation"]
                assert _command(owner, "presented", dict(
                    operation_id=prompt["operation_id"], request_id=prompt["request_id"],
                    stamp=stamp,
                ))["admission"] == "admitted"
                presentations.append(prompt["phase"])
            if state["phase"] == "idle":
                break
            time.sleep(.005)
        assert state["phase"] == "idle", state
        assert state["save_reason"] is None, state["save_reason"]
        assert len(state["operations"]) == 1
        details = owner.operation_details(identity)
        result = details["result"]
        assert result["status"] == "completed", result["error"]
        assert result["workflow"] == workflow
        assert result["request_json"] == json.dumps(json.loads(result["request_json"]),
                                                     ensure_ascii=True, allow_nan=False,
                                                     separators=(",", ":"))
        assert json.loads(result["request_json"])["layout"] == layout
        assert _command(owner, "run_workflow", payload, identity=identity)["operation_id"] == \
            outcome["operation_id"]
        review = review_saved(state["folder"])
        assert review["operations"][0]["review_state"] == "accepted", review
        assert review["sample_count"] == len(ids)
        if workflow in {"strip", "xy", "chart"}:
            assert [item["name"] for item in review["manifest"]["annotations"]] == ids
        if workflow == "display_timing":
            assert presentations == ["black", "white"]
            assert state["presentation"] is None
    finally:
        _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


@pytest.mark.parametrize("workflow,ids,layout", [
    ("spot", ["A1"], {}), ("unoffered", ["A1"], {}),
    ("refresh_frequency", ["A1"], {}), ("mode_snapshot", ["A1"], {}),
    ("strip", ["A1", "A1"], {}),
    (None, [], {}), ([], [], {}), ({"workflow": "mode_snapshot"}, [], {}),
])
def test_generic_workflow_invalid_request_has_no_intent_or_dispatch(
    tmp_path, workflow, ids, layout,
):
    front = _demo_frontend()
    owner = BatchApplication(tmp_path / "refused", frontend=front, mode="emissive_spot", options={})
    try:
        state = owner.bind()
        draft = state["draft"]
        result = _command(owner, "run_workflow", dict(
            context_id=state["context_id"], draft_id=draft["id"], draft_version=draft["version"],
            workflow=workflow, patch_ids=ids, layout=layout,
        ))
        assert result["admission"] == "refused", result
        assert owner.snapshot()["operations"] == [] and not front._ownership.locked()
        assert list(Path(state["folder"]).glob("operation-*.json")) == []
    finally:
        _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


@pytest.mark.parametrize("fault", ["terminal_decode", "drainer_poll"])
def test_generic_workflow_terminal_failure_clears_controlled_presentation(
    tmp_path, monkeypatch, fault,
):
    owner = BatchApplication(tmp_path / fault, frontend=_demo_frontend("i1_display_3"),
                             mode="emissive_spot", options={})
    try:
        state = owner.bind()
        draft = state["draft"]
        identity = str(uuid4())
        outcome = _command(owner, "run_workflow", dict(
            context_id=state["context_id"], draft_id=draft["id"], draft_version=draft["version"],
            workflow="display_timing", patch_ids=["timing"],
            layout=dict(stimulus_id="draw-failure", stimulus_description="Synthetic fault proof",
                        display_id="synthetic-surface"),
        ), identity=identity)
        assert outcome["admission"] == "admitted", outcome
        _wait(owner, lambda state: state["presentation"] is not None, respond=True)

        def failed():
            raise ValueError("Injected terminal reader failure")

        if fault == "terminal_decode":
            monkeypatch.setattr(owner._operation, "result_json", failed)
            assert _command(owner, "cancel", {"command_id": identity})["admission"] == "admitted"
        else:
            monkeypatch.setattr(owner._operation, "poll_event_json", failed)
        _wait(owner, lambda state: bool(owner._unknown))
        assert owner.operation_details(identity)["state"] == "terminal_unknown"
        assert owner.snapshot()["presentation"] is None
        refused = _command(owner, "presented", dict(
            operation_id=outcome["operation_id"], request_id="timing-black", stamp={},
        ))
        assert refused["admission"] == "refused" and "No pending" in refused["reason"]
    finally:
        _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


def test_mode_snapshot_requires_new_context_without_losing_saved_data_or_draft(tmp_path):
    owner = BatchApplication(tmp_path / "query-context", frontend=_demo_frontend("colorhug"),
                             mode="emissive_spot", options={})
    try:
        before, _ = _complete(owner)
        state = owner.snapshot()
        draft = state["draft"]
        identity = str(uuid4())
        assert _command(owner, "run_workflow", dict(
            context_id=state["context_id"], draft_id=draft["id"], draft_version=draft["version"],
            workflow="mode_snapshot", patch_ids=[], layout={},
        ), identity=identity)["admission"] == "admitted"
        state, _ = _wait(owner, lambda s: s["phase"] == "idle", respond=True)
        assert json.loads(owner._frontend.describe_json())["capability_id"] != \
            state["descriptor"]["capability_id"]
        assert not state["can_measure"]
        assert not state["context_current"]
        assert "new batch" in state["context_reason"].lower()
        count = len(state["operations"])
        assert _acquire(owner)["admission"] == "refused"
        assert len(owner.snapshot()["operations"]) == count
        # Save current draft text under the original descriptor without asking
        # a changed frontend to validate an unchanged acquisition configuration.
        draft = state["draft"]
        assert _command(owner, "edit_draft", dict(
            draft_id=draft["id"], version=draft["version"], name=draft["name"],
            notes="Text retained after a configuration query", options=draft["options"],
            number_samples=draft["number_samples"],
        ))["admission"] == "admitted"
        assert owner.snapshot()["draft"]["notes"] == "Text retained after a configuration query"
        assert _command(owner, "finish")["admission"] == "admitted"
        review = review_saved(state["folder"])
        assert review["sample_count"] == before["sample_count"] == 1
        assert len(review["operations"]) == count
        assert all(item["review_state"] == "accepted" for item in review["operations"])
    finally:
        _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


@pytest.mark.parametrize("driver,changes", [
    ("pr655", {"cycles": 3}), ("cr250", {"exposureMs": 20}),
])
def test_native_controls_repeated_reads_and_full_recovery_keep_actual_batch_context(
    tmp_path, monkeypatch, driver, changes,
):
    from test_native_controls import native_frontend, native_readback_seam

    front = native_frontend(driver)
    native = native_readback_seam(monkeypatch, front)
    owner = BatchApplication(tmp_path / driver, frontend=front, mode="emissive_spot",
                             options={"native_controls": changes})
    try:
        original = owner.bind()
        for _ in range(2):
            state, _ = _complete(owner)
            assert state["context_current"] and state["context_id"] == original["context_id"]
        assert state["sample_count"] == 2
        assert json.loads(front.describe_json())["capability_id"] == original["descriptor"][
            "capability_id"]
        native["fail_next"] = True
        native["current"].update({key: value + 1 for key, value in changes.items()})
        failed, _ = _complete(owner)
        assert failed["operations"][-1]["result"]["status"] == "failed"
        assert front._control_pending() and failed["context_current"]
        assert _edit_target(owner, "No recovery", options={})["admission"] == "refused"
        restored, _ = _complete(owner)
        assert restored["operations"][-1]["result"]["status"] == "completed"
        assert front._control_pending() == 0
        assert restored["context_current"] and restored["sample_count"] == 3
        assert restored["context_id"] == original["context_id"]
        _command(owner, "finish")
        review = review_saved(owner._folder)
        assert review["sample_count"] == 3
        assert [row["result"]["status"] for row in review["operations"]] == [
            "completed", "completed", "failed", "completed"]
    finally:
        _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))

def test_snapshot_reports_no_retirement_for_an_unidentifiable_folder(app, tmp_path, monkeypatch):
    # Catches: snapshot(), bind() and dataset() raising NotADirectoryError from
    # the retirement identity lookup once any other destination is retired.
    other = tmp_path / "other"
    other.mkdir()
    lock = exports._folder_lock(other, ".batch.lock")
    lock.__enter__()
    close_descriptor = exports.os.close

    def refuse_close(number):
        close_descriptor(number)
        if number == lock.descriptor:
            raise OSError("Lock close is unconfirmed")

    monkeypatch.setattr(exports.os, "close", refuse_close)
    with pytest.raises(OSError, match="unconfirmed"):
        lock.__exit__(None, None, None)
    monkeypatch.setattr(exports.os, "close", close_descriptor)
    assert exports._RETIRED_PUBLICATION_LOCKS
    plain = tmp_path / "plain.txt"
    plain.write_text("not a directory\n")
    folder = app._folder
    app._folder = plain / "inside"
    try:
        state = app.snapshot()
        assert "retired" not in (state["save_reason"] or "")
    finally:
        app._folder = folder
    assert "retired" not in (app.snapshot()["save_reason"] or "")


@pytest.mark.parametrize("ordinal, representable", [("998", True), ("999", False)])
def test_manifest_refuses_a_numbered_draft_whose_successor_is_unrepresentable(
    app, ordinal, representable,
):
    # Catches: the validator accepting a saved draft whose next label the owner
    # could never form, while the document claims that rule for the manifest.
    data = application._load(app._manifest_bytes)
    name = "S" * (1024 - len(ordinal) - 1) + " " + ordinal
    assert len(name) == 1024
    data["draft"].update(name=name, number_samples=True)
    payload = application._json(data).encode("ascii")
    if representable:
        application._manifest(payload)
    else:
        with pytest.raises(ValueError, match="Next sample label exceeds 1,024 characters"):
            application._manifest(payload)


def test_control_signal_inside_intent_reservation_is_reserved_by_the_recovery(app, monkeypatch):
    # Catches: the recovery's re-reservation branch replaced by ``pass``; the
    # cancelled handle would then be drained under no intent at all.
    original = app._intent_entry
    calls = []

    def interrupted_once(*args):
        calls.append(args)
        if len(calls) == 1:
            raise KeyboardInterrupt("Interrupted while reserving the intent")
        return original(*args)

    monkeypatch.setattr(app, "_intent_entry", interrupted_once)
    state = _snapshot(app)
    identity = str(uuid4())
    with pytest.raises(KeyboardInterrupt):
        app.command(identity, state["generation"], "measure", dict(
            context_id=state["context_id"], draft_id=state["draft"]["id"],
            draft_version=state["draft"]["version"],
        ), batch_id=state["batch_id"])
    assert len(calls) == 2
    after = _snapshot(app)
    # The intent reserved by the recovery names the handle, and the cancelled
    # outcome was drained and retained under it.
    entry = next(item for item in after["operations"] if item["command_id"] == identity)
    assert entry["operation_id"] is not None
    assert app.operation_details(identity)["result"]["status"] == "cancelled"
    outcome = app.command_outcome(identity)
    assert outcome["admission"] == "refused" and outcome["reason"].startswith("KeyboardInterrupt")
    assert after["phase"] == "idle" and after["can_measure"]
    _complete(app, export=False)


@pytest.mark.parametrize("second_site", ["_intent_entry", "_touch"])
def test_second_control_signal_inside_the_recovery_releases_the_held_name(
    app, monkeypatch, second_site,
):
    # Catches: a second signal inside the recovery's own re-reservation leaving
    # ``_active`` set with no drainer, so the owner could never measure again;
    # and, when it lands after the append, a reserved entry left in memory that
    # the next publication would persist as an unknown operation forever.
    original_entry, original_touch = app._intent_entry, app._touch
    calls = []

    def interrupted_entry(*args):
        calls.append("entry")
        if second_site == "_intent_entry" or len(calls) == 1:
            raise KeyboardInterrupt("Interrupted while reserving the intent")
        return original_entry(*args)

    def interrupted_touch():
        if second_site == "_touch" and "entry" in calls and len(calls) > 1:
            raise KeyboardInterrupt("Interrupted while touching the reservation")
        original_touch()

    monkeypatch.setattr(app, "_intent_entry", interrupted_entry)
    monkeypatch.setattr(app, "_touch", interrupted_touch)
    state = _snapshot(app)
    identity = str(uuid4())
    with pytest.raises(KeyboardInterrupt):
        app.command(identity, state["generation"], "measure", dict(
            context_id=state["context_id"], draft_id=state["draft"]["id"],
            draft_version=state["draft"]["version"],
        ), batch_id=state["batch_id"])
    monkeypatch.undo()
    after = _snapshot(app)
    assert after["phase"] == "idle" and after["can_measure"]
    assert after["software_stopped"] is state["software_stopped"]
    assert not any(item["command_id"] == identity for item in after["operations"])
    outcome = app.command_outcome(identity)
    assert outcome["admission"] == "refused"
    assert "reservation interrupted: KeyboardInterrupt" in outcome["reason"]
    final, _ = _complete(app, export=False)
    assert final["save_reason"] is None
    review = review_saved(Path(final["folder"]))
    assert [item["review_state"] for item in review["operations"]] == ["accepted"]


def test_comparison_context_answers_while_the_drainer_publishes_a_retained_result(
    app, monkeypatch,
):
    # Catches: the in-flight manifest accepted as this owner's own but then
    # compared with the last accepted one, whose operations differ by the newly
    # retained entry, so every projection in the window was refused.
    first, _ = _complete(app, export=False)
    pair = {key: first["annotations"][0][key] for key in ("operation_id", "observation_id")}
    assert _command(app, "set_comparison_context", dict(
        version=0, reference=pair, white=None,
    ))["admission"] == "admitted"
    saved, _ = _wait(app, lambda s: s["phase"] == "idle" and s["comparison"]["saved"])
    version = saved["comparison"]["version"]
    assert app.comparison_context(version)["reference"] == pair
    started, release = threading.Event(), threading.Event()
    publish = application._publish_owned_file

    def published_then_held(path, payload, previous):
        result = publish(path, payload, previous)
        if (
            threading.current_thread().name.startswith("batch-")
            and path.name == "batch.json"
            and app._metadata_saving
        ):
            started.set()
            assert release.wait(5), "the test did not release the publication"
        return result

    monkeypatch.setattr(application, "_publish_owned_file", published_then_held)
    assert _acquire(app)["admission"] == "admitted"
    deadline = time.monotonic() + 5
    while not started.is_set():
        assert time.monotonic() < deadline, _snapshot(app)
        pending = _snapshot(app)["instructions"]
        if pending:
            _command(app, "respond", dict(
                operation_id=pending["operation_id"], request_id=pending["request_id"],
                response="accept",
            ))
        time.sleep(0.005)
    answers = []
    reader = threading.Thread(
        target=lambda: answers.append(app.comparison_context(version)), daemon=True,
    )
    reader.start()
    reader.join(2)
    try:
        assert answers and answers[0]["reference"] == pair, answers
        assert _snapshot(app)["phase"] == "saving"
    finally:
        release.set()
    final, _ = _wait(app, lambda s: s["phase"] == "idle")
    assert final["save_reason"] is None and final["saved_count"] == 2


@pytest.mark.skipif(os.name == "nt", reason="Windows batch lock prevents renaming its open parent")
def test_a_folder_replaced_during_the_drainer_publication_is_refused_before_the_manifest(
    app, monkeypatch, tmp_path,
):
    # Catches: the manifest written by path after the operation-file syncs with
    # no owned-directory check in between (the synchronous route checks it
    # immediately before the write); a renamed folder would then fail with a
    # path error, or publish into whatever the path names, instead of the
    # replaced-directory refusal.
    started, release = threading.Event(), threading.Event()
    read = application._read_regular
    folder = Path(_snapshot(app)["folder"])
    moved = tmp_path / "moved-away"

    def held_after_readback(path):
        # The last read of the published operation file before the manifest.
        data = read(path)
        if (
            threading.current_thread().name.startswith("batch-")
            and path.name.startswith("operation-")
            and app._metadata_saving
        ):
            started.set()
            assert release.wait(5), "the test did not release the publication"
        return data

    monkeypatch.setattr(application, "_read_regular", held_after_readback)
    assert _acquire(app)["admission"] == "admitted"
    deadline = time.monotonic() + 5
    while not started.is_set():
        assert time.monotonic() < deadline, _snapshot(app)
        pending = _snapshot(app)["instructions"]
        if pending:
            _command(app, "respond", dict(
                operation_id=pending["operation_id"], request_id=pending["request_id"],
                response="accept",
            ))
        time.sleep(0.005)
    # Another directory now answers to the owned path.
    folder.rename(moved)
    folder.mkdir()
    release.set()
    try:
        state, _ = _wait(app, lambda s: s["phase"] == "idle" and s["save_reason"] is not None)
        assert "Owned batch directory was replaced" in state["save_reason"]
        # The impostor received nothing; the real manifest is still the old one.
        assert list(folder.iterdir()) == [] and (moved / "batch.json").exists()
        assert state["saved_count"] == 0
    finally:
        folder.rmdir()
        moved.rename(folder)
    assert _command(app, "retry_save")["state"] == "completed"
    assert _snapshot(app)["saved_count"] == 1


def test_phase_reports_saving_ahead_of_cleanup_uncertain_and_active(app):
    # Catches: the phase precedence placing cleanup_uncertain or active ahead
    # of an open publication, which the renderer then reads as a failed save.
    try:
        app._metadata_saving = True
        app._cleanup_reason = "Software stop is unconfirmed; retain operation ownership"
        assert app.snapshot()["phase"] == "saving"
        app._active = "held-command"
        assert app.snapshot()["phase"] == "saving"
        app._metadata_saving = False
        assert app.snapshot()["phase"] == "cleanup_uncertain"
        app._cleanup_reason = None
        assert app.snapshot()["phase"] == "active"
    finally:
        app._metadata_saving, app._cleanup_reason, app._active = False, None, None
    assert app.snapshot()["phase"] == "idle"


def test_unexpected_exception_before_admission_is_recorded_as_a_failure(app, monkeypatch):
    # Catches: a programming error before admission relabelled as an
    # interruption, which an identical re-send would then report as fact.
    state, _ = _complete(app, export=False)
    key = {field: state["annotations"][0][field] for field in ("operation_id", "observation_id")}

    def broken(*args):
        raise TypeError("handler defect")

    monkeypatch.setattr(app, "_annotation", broken)
    identity, payload = str(uuid4()), dict(key, draft_version=0)
    send = lambda: app.command(  # noqa: E731
        identity, state["generation"], "discard_annotation_draft", payload,
        batch_id=state["batch_id"],
    )
    with pytest.raises(TypeError):
        send()
    monkeypatch.undo()
    outcome = app.command_outcome(identity)
    assert outcome["admission"] == "refused" and outcome["state"] == "refused"
    assert outcome["reason"] == "Failed before admission: TypeError: handler defect"
    assert send() == outcome


def test_snapshot_answers_while_the_drainer_publishes_a_retained_result(app, monkeypatch):
    # Catches: the drainer holding the owner mutex across the retained-result
    # and manifest publication, so every snapshot waited for the syncs.
    started, release = threading.Event(), threading.Event()
    publish = application._publish_owned_file

    def held_publication(path, payload, previous):
        # The drainer's staged manifest publication (its event-boundary manifest
        # writes during the operation stay under the mutex and are not held).
        if (
            threading.current_thread().name.startswith("batch-")
            and path.name == "batch.json"
            and app._metadata_saving
        ):
            started.set()
            assert release.wait(5), "the test did not release the publication"
        return publish(path, payload, previous)

    monkeypatch.setattr(application, "_publish_owned_file", held_publication)
    outcome = _acquire(app)
    assert outcome["admission"] == "admitted", outcome
    deadline = time.monotonic() + 5
    while not started.is_set():
        assert time.monotonic() < deadline, _snapshot(app)
        pending = _snapshot(app)["instructions"]
        if pending:
            _command(app, "respond", dict(
                operation_id=pending["operation_id"], request_id=pending["request_id"],
                response="accept",
            ))
        time.sleep(0.005)
    answers = []
    reader = threading.Thread(target=lambda: answers.append(app.snapshot()), daemon=True)
    reader.start()
    reader.join(2)
    try:
        assert answers, "snapshot() blocked behind the drainer's publication"
        state = answers[0]
        # The result is retained and the handle released; only its publication is open.
        assert state["phase"] == "saving" and state["operation_id"] is None
        assert state["sample_count"] == 1 and state["saved_count"] == 0
        assert state["save_reason"] is None and not state["can_measure"]
        assert state["operations"][-1]["state"] != "accepted"
        refused = _command(app, "measure", dict(
            context_id=state["context_id"], draft_id=state["draft"]["id"],
            draft_version=state["draft"]["version"],
        ))
        assert refused["admission"] == "refused" and "pending" in refused["reason"]
    finally:
        release.set()
    final, _ = _wait(app, lambda s: s["phase"] == "idle")
    assert final["save_reason"] is None
    assert final["operations"][-1]["state"] == "accepted"


@pytest.mark.parametrize("action", ["retry_save", "export_ti3", "retry_companions"])
def test_finished_retry_waits_for_owned_companion_publication(app, monkeypatch, action):
    _complete(app)
    original = dict(app._payloads)
    publish = app._publish_companion
    entered, release = threading.Event(), threading.Event()

    def held(*args):
        entered.set()
        assert release.wait(8)
        return publish(*args)

    monkeypatch.setattr(app, "_publish_companion", held)
    try:
        state = app.snapshot()
        finish = app.command(str(uuid4()), state["generation"], "finish", {},
                             batch_id=state["batch_id"])
        assert finish["state"] == "pending" and entered.wait(8)
        assert app._lock is not None and app.snapshot()["companions"]["saving"]
        refused = _command(app, action)
        assert refused["admission"] == "refused"
        assert refused["reason"] == "Wait for automatic companion publication before this action"
    finally:
        release.set()
        _automatic_companion_state(app)
    assert _command(app, action)["admission"] == "admitted"
    _automatic_companion_state(app)
    assert app._payloads == original and app._lock is None


def _automatic_companion_state(owner):
    state, _ = _wait(owner, lambda packet: packet["phase"] == "idle"
                     and not packet["companions"]["saving"])
    return state


def _automatic_companion_path(state, format):
    return Path(state["folder"]) / state["companions"]["formats"][format]["path"]


def _automatic_companion_rows(state, format="csv"):
    import csv
    import io

    return list(csv.DictReader(io.StringIO(
        _automatic_companion_path(state, format).read_text(), newline="",
    )))


def _assert_current_automatic_companion(state, format, filename):
    item = state["companions"]["formats"][format]
    path = _automatic_companion_path(state, format)
    assert path.name == filename
    assert item["state"] == "current" and item["current"] is True, item
    assert item["source_manifest_sha256"] == state["companions"]["source_manifest_sha256"]
    assert all(Path(receipt["path"]).name != filename
               for receipt in state["durability"]["files"])
    assert item["payload_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert item["reason"] is None and item["retryable"] is False
    assert item["publication"] is not None
    return path.read_bytes()


@pytest.mark.parametrize("boundary,failed_csv", [
    ("starting", False), ("active", False), ("saving", False), ("active", True),
    ("cancelling", False),
])
def test_stale_companions_report_owned_refresh_without_hiding_failure(
    app, monkeypatch, mat, boundary, failed_csv,
):
    from color_measurement import demo

    publish = application._publish_owned_file

    def publication(path, payload, previous):
        if failed_csv and path.name == "measurements.csv":
            raise OSError("Existing controlled CSV publication failure")
        return publish(path, payload, previous)

    monkeypatch.setattr(application, "_publish_owned_file", publication)
    _complete(app, export=False)
    before = _automatic_companion_state(app)
    prior = before["companions"]["formats"]
    assert prior["mat"]["current"]
    assert prior["csv"]["state"] == ("failed" if failed_csv else "current")
    retained = deepcopy(app._companion_pending.get("csv"))
    entered, release = threading.Event(), threading.Event()
    prepare, receive = app._frontend.prepare, demo._DemoSession.receive_message

    def prepared(request):
        operation = prepare(request)
        dispatch = operation.dispatch

        def held_dispatch():
            if boundary in {"starting", "cancelling"}:
                entered.set()
                assert release.wait(5), "Release the owned Starting boundary"
            return dispatch()

        operation.dispatch = held_dispatch
        return operation

    def held_receive(session, **kwargs):
        if boundary == "active" and not release.is_set():
            entered.set()
            assert release.wait(5), "Release the actual Demo event parser"
        return receive(session, **kwargs)

    def held_publication(path, payload, previous):
        if boundary == "saving" and path.name == "batch.json" and app._metadata_saving:
            entered.set()
            assert release.wait(5), "Release the canonical terminal publication"
        return publication(path, payload, previous)

    monkeypatch.setattr(app._frontend, "prepare", prepared)
    monkeypatch.setattr(demo._DemoSession, "receive_message", held_receive)
    monkeypatch.setattr(application, "_publish_owned_file", held_publication)
    try:
        identity = str(uuid4())
        admitted = _acquire(app, identity=identity)
        assert admitted["admission"] == "admitted"
        assert entered.wait(2)
        if boundary == "cancelling":
            assert _command(app, "cancel", dict(command_id=identity))[
                "admission"
            ] == "admitted"
            assert app._operation.done
        phase = "starting" if boundary == "cancelling" else boundary
        state, _ = _wait(app, lambda state: state["phase"] == phase)
        companions = state["companions"]
        assert not companions["saving"]
        for format in ("mat", "csv"):
            item = companions["formats"][format]
            if format == "csv" and failed_csv:
                assert item["state"] == "failed" and item["retryable"]
                assert not item["refresh_pending"]
                assert "Existing controlled CSV" in item["reason"]
                assert app._companion_pending[format] == retained
            else:
                assert item["state"] == "stale" and not item["current"]
                assert item["source_manifest_sha256"] != companions["source_manifest_sha256"]
                assert item["refresh_pending"] and item["retryable"]
        unavailable = companions["formats"]["chart_selected_csv"]
        assert unavailable["state"] == "unavailable" and not unavailable["refresh_pending"]
        summary = app.discard_summary()
        assert {item["format"] for item in summary["companions"]} >= {"mat", "csv"}
        if boundary != "saving":
            refused = _command(app, "close", dict(discard_token=summary["token"]))
            assert refused["admission"] == "refused"
        with app._mutex, monkeypatch.context() as scoped:
            scoped.setitem(app._data, "status", "abandoned")
            assert not app._companions_snapshot()["formats"]["mat"]["refresh_pending"]
        with app._mutex, monkeypatch.context() as scoped:
            scoped.setattr(app, "_cleanup_reason", "Unconfirmed cleanup")
            assert not app._companions_snapshot()["formats"]["mat"]["refresh_pending"]
        with app._mutex, monkeypatch.context() as scoped:
            scoped.setattr(app, "_closed", True)
            assert not app._companions_snapshot()["formats"]["mat"]["refresh_pending"]
    finally:
        release.set()
    _wait(app, lambda state: state["phase"] == "idle", respond=True)
    final = _automatic_companion_state(app)
    assert final["saved_count"] == 2
    assert final["sample_count"] == (1 if boundary == "cancelling" else 2)
    _assert_current_automatic_companion(final, "mat", "measurements.mat")
    assert not any(item["refresh_pending"] for item in final["companions"]["formats"].values())
    if failed_csv:
        assert final["companions"]["formats"]["csv"]["state"] == "failed"
        assert app._companion_pending["csv"] == retained
    else:
        _assert_current_automatic_companion(final, "csv", "measurements.csv")


def test_automatic_companions_follow_terminal_and_finish_after_canonical_publication(
    app, monkeypatch, mat,
):
    from color_measurement.document_mat import from_mat

    publish = application._publish_owned_file
    observed = []

    def canonical_first(path, payload, previous):
        if path.name in {"measurements.mat", "measurements.csv"}:
            review = review_saved(path.parent)
            assert review["sample_count"] == 1
            assert all(item["review_state"] == "accepted" for item in review["operations"])
            observed.append((path.name, review["manifest_sha256"]))
        return publish(path, payload, previous)

    monkeypatch.setattr(application, "_publish_owned_file", canonical_first)
    _complete(app, export=False)
    state = _automatic_companion_state(app)
    source = review_saved(state["folder"])
    assert source["manifest_sha256"] == state["companions"]["source_manifest_sha256"]
    payload = _assert_current_automatic_companion(state, "mat", "measurements.mat")
    _assert_current_automatic_companion(state, "csv", "measurements.csv")
    document = from_mat(payload, kind="batch")
    assert document["source"]["manifest_sha256"] == source["manifest_sha256"]
    row, = _automatic_companion_rows(state)
    original = source["operations"][0]["result"]["patches"][0]["observation"]
    assert json.loads(row["observation_json"]) == original
    assert [float(row[key]) for key in ("native_X", "native_Y", "native_Z")] == (
        original["backend_reported"]["xyz"]["values"]
    )
    assert state["companions"]["formats"]["chart_selected_csv"]["state"] == "unavailable"
    assert state["export_state"] == "current" and state["ti3"]["files"]
    assert {name for name, digest in observed if digest == source["manifest_sha256"]} == {
        "measurements.mat", "measurements.csv",
    }
    before = len(state["operations"])
    assert _command(app, "finish")["admission"] == "admitted"
    finished = _automatic_companion_state(app)
    assert finished["status"] == "finished" and len(finished["operations"]) == before
    final = from_mat(_assert_current_automatic_companion(
        finished, "mat", "measurements.mat",
    ), kind="batch")
    assert final["source"]["manifest_sha256"] != document["source"]["manifest_sha256"]
    assert _assert_current_automatic_companion(finished, "csv", "measurements.csv")
    assert review_saved(finished["folder"])["manifest"]["status"] == "finished"


def test_empty_finish_automatically_writes_mat_and_header_only_csv(app, mat):
    from color_measurement.document_mat import from_mat

    assert _command(app, "finish")["admission"] == "admitted"
    state = _automatic_companion_state(app)
    document = from_mat(_assert_current_automatic_companion(
        state, "mat", "measurements.mat",
    ), kind="batch")
    assert document["files"].keys() == {"batch.json"}
    payload = _assert_current_automatic_companion(state, "csv", "measurements.csv")
    assert b"observation_json" in payload and _automatic_companion_rows(state) == []
    assert state["sample_count"] == state["saved_count"] == 0


def test_applied_annotation_refreshes_automatic_formats_without_recursive_archive_inputs(
    app, monkeypatch, mat,
):
    from color_measurement import batch_archive
    from color_measurement.document_mat import from_mat

    capture = batch_archive.prepare_from_capture
    inventories = []

    def captured(review, files, **kwargs):
        inventories.append(set(files))
        return capture(review, files, **kwargs)

    monkeypatch.setattr(batch_archive, "prepare_from_capture", captured)
    _complete(app, export=False)
    before = _automatic_companion_state(app)
    operation = before["operations"][0]
    original = (Path(before["folder"]) / operation["path"]).read_bytes()
    assert _annotate(app, before["annotations"][0], name="Updated native label",
                     notes="Exact multiline\nannotation retained")["admission"] == "admitted"
    after = _automatic_companion_state(app)
    assert before["companions"]["source_manifest_sha256"] != (
        after["companions"]["source_manifest_sha256"]
    )
    payload = _assert_current_automatic_companion(after, "mat", "measurements.mat")
    _assert_current_automatic_companion(after, "csv", "measurements.csv")
    row, = _automatic_companion_rows(after)
    annotation = app.annotation_details(row["operation_id"], row["observation_id"])["applied"]
    assert row["name"] == annotation["name"] == "Updated native label"
    assert row["notes"] == annotation["notes"] == "Exact multiline\nannotation retained"
    assert annotation["saved"] is True
    assert json.loads(row["annotation_json"]) == {
        key: value for key, value in annotation.items() if key != "saved"
    }
    document = from_mat(payload, kind="batch")
    assert set(document["files"]) == inventories[-1] == {"batch.json", operation["path"]}
    assert all(not any(name.endswith((".mat", ".csv", ".ti3", ".ti2"))
                       or name == "ti3-result.json" for name in names) for names in inventories)
    assert (Path(after["folder"]) / operation["path"]).read_bytes() == original
    assert after["sample_count"] == after["saved_count"] == 1


def test_unavailable_automatic_mat_does_not_suppress_csv_ti3_or_canonical_reading(
    app, monkeypatch,
):
    from color_measurement import batch_archive

    def unavailable(*args, **kwargs):
        # The actual missing-runtime text (operation_exports._mat_dependencies),
        # which names a package extra meant for command-line installations.
        raise ImportError("MAT export/import requires color-measurement[mat]")

    monkeypatch.setattr(batch_archive, "to_mat", unavailable)
    _complete(app, export=False)
    state = _automatic_companion_state(app)
    item = state["companions"]["formats"]["mat"]
    assert item["state"] == "unavailable" and item["current"] is False
    # The workspace shows the operator reason, never the install hint.
    assert item["reason"] == "MAT files are unavailable in this app build."
    assert item["payload_sha256"] is None and item["retryable"] is False
    assert not _automatic_companion_path(state, "mat").exists()
    _assert_current_automatic_companion(state, "csv", "measurements.csv")
    assert len(_automatic_companion_rows(state)) == 1 and state["export_state"] == "current"
    assert state["save_reason"] is None and state["saved_count"] == 1
    assert review_saved(state["folder"])["operations"][0]["review_state"] == "accepted"


def test_failed_automatic_mat_retry_uses_exact_prepared_bytes_without_reacquisition(
    app, monkeypatch, mat,
):
    from color_measurement import batch_archive

    publish = application._publish_owned_file
    retained = []

    def denied(path, payload, previous):
        if path.name == "measurements.mat":
            retained.append(payload)
            raise OSError("Automatic MAT destination denied")
        return publish(path, payload, previous)

    monkeypatch.setattr(application, "_publish_owned_file", denied)
    _complete(app, export=False)
    failed = _automatic_companion_state(app)
    original = retained[0]
    item = failed["companions"]["formats"]["mat"]
    assert item["state"] == "failed" and item["retryable"] is True
    assert item["payload_sha256"] == hashlib.sha256(original).hexdigest()
    assert "destination denied" in item["reason"]
    _assert_current_automatic_companion(failed, "csv", "measurements.csv")
    assert failed["save_reason"] is None and failed["saved_count"] == 1
    prepare, contacts = app._frontend.prepare, []

    def tracked(request, **kwargs):
        contacts.append(request)
        return prepare(request, **kwargs)

    def cannot_reserialize(*args, **kwargs):
        raise ImportError("Retry must retain the already prepared MAT header")

    monkeypatch.setattr(app._frontend, "prepare", tracked)
    monkeypatch.setattr(batch_archive, "to_mat", cannot_reserialize)
    monkeypatch.setattr(application, "_publish_owned_file", publish)
    assert _command(app, "retry_companions")["admission"] == "admitted"
    retried = _automatic_companion_state(app)
    assert _assert_current_automatic_companion(retried, "mat", "measurements.mat") == original
    assert contacts == [] and retried["sample_count"] == retried["saved_count"] == 1
    assert app.discard_summary()["companions"] == []


def test_automatic_retry_publishes_retained_old_mat_then_refreshes_new_annotation_source(
    app, monkeypatch, mat,
):
    from color_measurement.document_mat import from_mat

    publish = application._publish_owned_file
    rejected = []

    def denied(path, payload, previous):
        if path.name == "measurements.mat":
            rejected.append(payload)
            raise OSError("Hold prepared MAT while the canonical source advances")
        return publish(path, payload, previous)

    monkeypatch.setattr(application, "_publish_owned_file", denied)
    _complete(app, export=False)
    first = _automatic_companion_state(app)
    original = rejected[0]
    old_source = from_mat(original, kind="batch")["source"]["manifest_sha256"]
    assert _annotate(app, first["annotations"][0], name="Advanced annotation",
                     notes="New accepted source")["admission"] == "admitted"
    advanced = _automatic_companion_state(app)
    assert advanced["companions"]["source_manifest_sha256"] != old_source
    _assert_current_automatic_companion(advanced, "csv", "measurements.csv")
    assert _automatic_companion_rows(advanced)[0]["name"] == "Advanced annotation"
    successful = []

    def accepted(path, payload, previous):
        result = publish(path, payload, previous)
        if path.name == "measurements.mat":
            successful.append(payload)
        return result

    monkeypatch.setattr(application, "_publish_owned_file", accepted)
    assert _command(app, "retry_companions")["admission"] == "admitted"
    final = _automatic_companion_state(app)
    assert successful[0] == original and len(successful) == 2
    latest = _assert_current_automatic_companion(final, "mat", "measurements.mat")
    assert successful[-1] == latest
    assert from_mat(latest, kind="batch")["source"]["manifest_sha256"] == (
        advanced["companions"]["source_manifest_sha256"]
    )
    assert final["sample_count"] == 1 and app.discard_summary()["companions"] == []


def test_another_folder_rescues_exact_failed_companions_without_new_measurement(
    app, monkeypatch, tmp_path, mat,
):
    from color_measurement.document_mat import from_mat

    publish = application._publish_owned_file
    original_folder = app._folder
    target = tmp_path / "automatic-companion-rescue"
    failed_bytes = []
    rescued_payloads = []

    def old_destination_denied(path, payload, previous):
        if path.parent == original_folder and path.name == "measurements.mat":
            failed_bytes.append(payload)
            raise OSError("Only original MAT destination is denied")
        result = publish(path, payload, previous)
        if path.parent == target and path.name == "measurements.mat":
            rescued_payloads.append(payload)
        return result

    monkeypatch.setattr(application, "_publish_owned_file", old_destination_denied)
    _complete(app, export=False)
    before = _automatic_companion_state(app)
    original_manifest = (original_folder / "batch.json").read_bytes()
    original_records = {item["path"]: (original_folder / item["path"]).read_bytes()
                        for item in before["operations"]}
    prepare, contacts = app._frontend.prepare, []

    def tracked(request, **kwargs):
        contacts.append(request)
        return prepare(request, **kwargs)

    monkeypatch.setattr(app._frontend, "prepare", tracked)
    assert _command(app, "another_folder", dict(folder=str(target)))["admission"] == "admitted"
    rescued = _automatic_companion_state(app)
    assert Path(rescued["folder"]) == target
    assert rescued["companions"]["source_manifest_sha256"] != (
        before["companions"]["source_manifest_sha256"]
    )
    assert len(rescued_payloads) == 2 and rescued_payloads[0] == failed_bytes[0]
    latest = _assert_current_automatic_companion(rescued, "mat", "measurements.mat")
    assert rescued_payloads[-1] == latest
    assert from_mat(rescued_payloads[0], kind="batch")["source"]["manifest_sha256"] == (
        before["companions"]["source_manifest_sha256"]
    )
    document = from_mat(latest, kind="batch")
    assert document["source"]["manifest_sha256"] == (
        rescued["companions"]["source_manifest_sha256"]
    )
    _assert_current_automatic_companion(rescued, "csv", "measurements.csv")
    assert (target / "batch.json").read_bytes() != original_manifest
    assert (original_folder / "batch.json").read_bytes() == original_manifest
    assert all((target / name).read_bytes() == payload
               for name, payload in original_records.items())
    assert rescued["annotations"] == before["annotations"]
    assert review_saved(target)["sample_count"] == 1 and contacts == []
    assert app.discard_summary()["companions"] == []


def test_failed_unprepared_files_and_ti3_are_disclosed_and_retry_completion_is_truthful(
    app, monkeypatch,
):
    from color_measurement import batch_archive

    prepare = batch_archive.prepare_from_capture
    entered, release = threading.Event(), threading.Event()

    def preparation_failed(*args, **kwargs):
        entered.set()
        assert release.wait(5), "The test did not release initial companion preparation"
        raise OSError("Controlled automatic file preparation failure")

    monkeypatch.setattr(batch_archive, "prepare_from_capture", preparation_failed)
    try:
        _complete(app, export=False)
        assert entered.wait(2)
        pending = next(item for item in app.discard_summary()["companions"]
                       if item["format"] == "ti3")
        assert pending["state"] == "pending" and "refresh is pending" in pending["reason"]
        assert app.snapshot()["saved_count"] == 1
    finally:
        release.set()
    _automatic_companion_state(app)
    summary = app.discard_summary()
    assert {item["format"] for item in summary["companions"]} == {"mat", "csv", "ti3"}
    assert all(item["payload_sha256"] is None and item["payload_bytes"] is None
               for item in summary["companions"])
    assert _command(app, "close", dict(discard_token=None))["admission"] == "refused"
    failed = _command(app, "retry_companions")
    assert failed["admission"] == "admitted" and failed["state"] == "failed", failed
    token = app.discard_summary()["token"]
    monkeypatch.setattr(batch_archive, "prepare_from_capture", prepare)
    assert _command(app, "retry_companions")["state"] == "completed"
    assert app.discard_summary()["companions"] == []
    assert app.discard_summary()["token"] != token


def test_ti3_published_receipt_sync_failure_retains_new_owner_digest_for_retry(app, monkeypatch):
    from dataclasses import replace

    publish = application._publish_owned_file
    failed = []

    def sync_failure(path, payload, previous):
        result = publish(path, payload, previous)
        if path.name == "ti3-result.json" and not failed:
            failed.append(payload)
            error = OSError("Controlled TI3 receipt directory-sync failure")
            error.publication = replace(result, directory_synced=False)
            raise error
        return result

    monkeypatch.setattr(application, "_publish_owned_file", sync_failure)
    _complete(app, export=False)
    _automatic_companion_state(app)
    assert app._receipt_digest == application._digest(failed[0])
    assert any(item["format"] == "ti3" for item in app.discard_summary()["companions"])
    assert _command(app, "retry_companions")["state"] == "completed"
    assert app.snapshot()["export_reason"] is None and app.discard_summary()["companions"] == []


@pytest.mark.parametrize("character", ["\u202e", "\u200b", "\u2066"])
def test_sample_labels_refuse_invisible_format_controls_without_mutating_notes(app, character):
    before = app.snapshot()["draft"]
    payload = dict(draft_id=before["id"], version=before["version"], name="sample" + character,
                   notes=before["notes"], options=before["options"],
                   number_samples=before["number_samples"])
    assert _command(app, "edit_draft", payload)["admission"] == "refused"
    assert app.snapshot()["draft"] == before
    payload.update(name=before["name"], notes="Retained note" + character)
    assert _command(app, "edit_draft", payload)["admission"] == "admitted"


def test_close_requires_exact_discard_token_for_failed_prepared_companion_bytes(
    app, monkeypatch, mat,
):
    publish = application._publish_owned_file
    retained = []

    def denied(path, payload, previous):
        if path.name == "measurements.mat":
            retained.append(payload)
            raise OSError("Retain exact MAT for Close disclosure")
        return publish(path, payload, previous)

    monkeypatch.setattr(application, "_publish_owned_file", denied)
    _complete(app, export=False)
    state = _automatic_companion_state(app)
    summary = app.discard_summary()
    pending, = summary["companions"]
    assert pending["format"] == "mat" and Path(pending["path"]).name == "measurements.mat"
    assert pending["payload_sha256"] == hashlib.sha256(retained[0]).hexdigest()
    assert pending["payload_bytes"] == len(retained[0])
    assert pending["source_manifest_sha256"] == state["companions"]["source_manifest_sha256"]
    assert summary["operations"] == [] and summary["annotations"] == []
    assert _command(app, "close", dict(discard_token=None))["admission"] == "refused"
    assert app.discard_summary()["token"] == summary["token"]
    assert _command(app, "close", dict(discard_token=summary["token"]))["state"] == "completed"
    assert review_saved(state["folder"])["sample_count"] == 1


@pytest.mark.parametrize("boundary", ["serializer", "publisher"])
def test_automatic_companion_io_allows_acquisition_and_coalesces_latest_source(
    app, monkeypatch, mat, boundary,
):
    from color_measurement import batch_archive

    _complete(app, export=False)
    before = _automatic_companion_state(app)
    entered, release = threading.Event(), threading.Event()
    prepare, publish = batch_archive.prepare_from_capture, application._publish_owned_file
    captures, projections = [], []
    update = ti3_exports.update_json

    def held_prepare(review, files, **kwargs):
        captures.append(review["manifest_sha256"])
        if boundary == "serializer" and not release.is_set():
            entered.set()
            assert release.wait(5), "Test must release companion serialization"
        return prepare(review, files, **kwargs)

    def held_publish(path, payload, previous):
        if boundary == "publisher" and path.name == "measurements.mat" and not release.is_set():
            entered.set()
            assert release.wait(5), "Test must release companion publication"
        return publish(path, payload, previous)

    def inspect_projection(snapshot, *args):
        projections.append(json.loads(snapshot))
        return update(snapshot, *args)

    monkeypatch.setattr(batch_archive, "prepare_from_capture", held_prepare)
    monkeypatch.setattr(application, "_publish_owned_file", held_publish)
    monkeypatch.setattr(ti3_exports, "update_json", inspect_projection)
    annotation = before["annotations"][0]
    key = {field: annotation[field] for field in ("operation_id", "observation_id", "version")}
    assert _command(app, "edit_annotation_draft", dict(
        key, draft_version=0, name="Concurrent query", notes="Accepted metadata source",
    ))["admission"] == "admitted"
    identity = str(uuid4())
    assert _command(app, "annotate", dict(key, draft_version=1),
                    identity=identity)["state"] == "completed"
    answers = []
    try:
        assert entered.wait(2)
        frozen = app.snapshot()["companions"]["source_manifest_sha256"]

        def queries():
            answers.append((app.snapshot(), app.operation_details(before["operations"][0][
                "command_id"
            ]), app.reading_details(key["operation_id"], key["observation_id"]),
                app.annotation_details(key["operation_id"], key["observation_id"])))

        reader = threading.Thread(target=queries, daemon=True)
        reader.start()
        reader.join(1)
        assert answers and not reader.is_alive(), "Read-only queries blocked behind companion I/O"
        shown, operation, reading, details = answers[0]
        assert shown["phase"] == "idle" and shown["companions"]["saving"]
        assert shown["can_measure"] and shown["saved_count"] == 1
        assert operation["state"] == "accepted"
        assert reading["observation_id"] == key["observation_id"]
        assert details["applied"]["name"] == "Concurrent query"
        # Canonical acquisition and applied annotations continue while optional
        # serialization/publication is deliberately held, without a larger wait.
        _complete(app, export=False)
        current = app.snapshot()
        assert current["saved_count"] == 2 and current["phase"] == "idle"
        assert current["companions"]["saving"]
        applied = app.annotation_details(key["operation_id"], key["observation_id"])["applied"]
        _annotate(app, applied, name="Latest annotation", notes="Latest source")
        latest = app.snapshot()["companions"]["source_manifest_sha256"]
        assert latest != frozen
        for action, payload in (("close", dict(discard_token=None)),
                                ("another_folder", dict(folder=str(app._folder.parent / "held")))):
            refused = _command(app, action, payload)
            assert refused["admission"] == "refused" and "companion" in refused["reason"]
    finally:
        release.set()
    final = _automatic_companion_state(app)
    _assert_current_automatic_companion(final, "mat", "measurements.mat")
    _assert_current_automatic_companion(final, "csv", "measurements.csv")
    assert final["sample_count"] == 2 and app.command_outcome(identity)["state"] == "completed"
    assert captures == [frozen, latest], "Queued unprepared updates must coalesce"
    assert [item["source_sha256"] for item in projections] == [frozen, latest]
    assert len(projections[0]["records"]) == 1 and len(projections[1]["records"]) == 2
    assert projections[0]["records"][0]["name"] == "Concurrent query"
    assert projections[1]["records"][0]["name"] == "Latest annotation"
    assert _automatic_companion_rows(final)[0]["name"] == "Latest annotation"


@pytest.mark.parametrize("mode", ["reflective_spot_m1", "emissive_spot"])
def test_automatic_guided_chart_formats_retain_selected_ti2_and_inherited_native_closure(
    tmp_path, mat, mode,
):
    import base64
    from uuid import UUID

    from test_application_chart import _measure

    from color_measurement import batch_archive, chart_state
    from color_measurement.chart_targets import import_ti2_json
    from color_measurement.document_mat import from_mat

    fixture = Path(__file__).parent / "fixtures/targets/asymmetric-six-patch.ti2"
    target = import_ti2_json(str(fixture))
    definition = chart_state.definition("ti2", target_json=target, strips_as="rows",
                                        mapping_confirmed=True)
    parent_owner = BatchApplication(tmp_path / "automatic-chart-parent", frontend=_demo_frontend(),
                                    mode=mode, options={})
    parent_owner.bind()
    child = None
    try:
        assert _configure_chart(parent_owner, definition)["admission"] == "admitted"
        _measure(parent_owner)
        _automatic_companion_state(parent_owner)
        parent = review_saved(parent_owner._folder)
        child = BatchApplication(
            tmp_path / "automatic-chart-child", frontend=parent_owner._frontend,
            mode=mode, options={},
        )
        child.bind()
        assert _command(child, "continue_chart", dict(
            folder=str(parent_owner._folder), source_sha256=parent["manifest_sha256"],
            action="new-pass", source_pass_id=parent["manifest"]["chart"]["active_pass"],
            acknowledged_material=[],
        ))["admission"] == "admitted"
        _automatic_companion_state(child)
        _measure(child)
        current = _automatic_companion_state(child)
        assert _command(child, "finish_chart", dict(
            chart_revision=current["chart"]["revision"], allow_incomplete=True,
        ))["admission"] == "admitted"
        state = _automatic_companion_state(child)
        payload = _assert_current_automatic_companion(state, "mat", "measurements.mat")
        _assert_current_automatic_companion(state, "csv", "measurements.csv")
        _assert_current_automatic_companion(state, "chart_selected_csv", "chart-selected.csv")
        rows = _automatic_companion_rows(state)
        assert len(rows) == 2 and {row["segment_id"] for row in rows} == {
            parent["manifest"]["batch_id"], state["batch_id"],
        }
        selected_rows = _automatic_companion_rows(state, "chart_selected_csv")
        review = review_saved(child._folder)
        assert [row["pass_id"] for row in selected_rows] == [
            item["id"] for item in review["manifest"]["chart"]["passes"]
        ]
        assert len(selected_rows) == 2
        selected = selected_rows[-1]
        reading, = child.chart_readings()
        assert json.loads(selected["reading_ref_json"]) == reading["reading_ref"]
        assert int(selected["cell_index"]) == reading["cell_index"] == 1
        assert selected["sample_id"] == reading["reading_ref"]["sample_id"]
        assert str(UUID(selected["sample_id"])) == selected["sample_id"]
        assert selected["target_sample_id"] == "007"
        assert selected["target_sample_location"] == "01A"
        document = from_mat(payload, kind="batch")
        embedded = json.loads(base64.b64decode(document["files"]["batch.json"]["bytes_base64"]))
        original_target = json.loads(embedded["chart"]["definition"]["target_json"])
        assert base64.b64decode(original_target["source_base64"]) == fixture.read_bytes()
        assert all(document["files"][item["path"]]["sha256"] == item["sha256"]
                   for item in review["chart"]["source_files"])
        assert not any(name.endswith((".mat", ".csv", ".ti3", ".ti2"))
                       or name == "ti3-result.json" for name in document["files"])
        target_files = [item for item in state["ti3"]["files"] if item["scope"] == "chart_target"]
        assert len(target_files) == 2 and all(item["row_count"] == 1 for item in target_files)
        if mode == "reflective_spot_m1":
            assert all(item["state"] == "current" and item["reason"] == ""
                       for item in target_files)
            assert all(Path(item["path"]).is_file() for item in target_files)
            assert all(Path(item["target_path"]).read_bytes() == fixture.read_bytes()
                       for item in target_files)
            assert all(item["target_sha256"] == hashlib.sha256(fixture.read_bytes()).hexdigest()
                       for item in target_files)
        else:
            refusal = (
                "Every chart selection requires qualified reflective XYZ; ordinary TI3 is separate"
            )
            assert all(item["state"] == "unavailable" and item["reason"] == refusal
                       for item in target_files)
            assert all(not Path(item["path"]).exists() and not Path(item["target_path"]).exists()
                       for item in target_files)
        # Only retained review is needed from the parent now. Release its live
        # lock through normal Close before making its source location unavailable.
        _automatic_companion_state(parent_owner)
        assert _command(parent_owner, "close", dict(discard_token=None))["state"] == "completed"
        offline = tmp_path / "automatic-chart-parent-offline"
        parent_owner._folder.rename(offline)
        temporary = None
        try:
            temporary = batch_archive.unpack(payload)
            restored = review_saved(temporary.name)
            assert restored["chart"] == review["chart"]
            assert restored["inherited_operations"] == review["inherited_operations"]
            assert restored["operations"] == review["operations"]
        finally:
            if temporary is not None:
                temporary.cleanup()
            offline.rename(parent_owner._folder)
    finally:
        for owner in (child, parent_owner):
            if owner is not None:
                _command(owner, "close", dict(discard_token=owner.discard_summary()["token"]))


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_optional_worker_start_failure_preserves_saved_reading_and_original_control(
    app, monkeypatch, failure,
):
    first = failure("Optional worker startup interrupted")
    start, run = application._ApplicationWorker.start, application._ApplicationWorker._run
    controls = []

    def fail_companion_start(worker):
        if worker._name.startswith("companions-"):
            raise first
        return start(worker)

    def record_control(worker):
        try:
            run(worker)
        except BaseException as error:
            controls.append(error)

    monkeypatch.setattr(application._ApplicationWorker, "start", fail_companion_start)
    monkeypatch.setattr(application._ApplicationWorker, "_run", record_control)
    state, _ = _complete(app, export=False)
    assert state["saved_count"] == state["accepted_count"] == state["sample_count"] == 1
    assert state["save_reason"] is None and state["software_stopped"]
    assert state["phase"] == "idle" and not state["companions"]["saving"]
    assert not application._UNCERTAIN_OWNERS and app._unknown == {}
    assert review_saved(app._folder)["operations"][0]["review_state"] == "accepted"
    assert all(item["state"] == "failed" and str(first) in item["reason"]
               for item in state["companions"]["formats"].values())
    if isinstance(first, Exception):
        assert controls == []
    else:
        _wait(app, lambda state: bool(controls))
        assert controls == [first]
    monkeypatch.setattr(application._ApplicationWorker, "start", start)
    monkeypatch.setattr(app._frontend, "prepare", lambda *args: pytest.fail("Retry reacquired"))
    assert _command(app, "retry_companions")["state"] == "completed"
    final = _automatic_companion_state(app)
    _assert_current_automatic_companion(final, "csv", "measurements.csv")
    assert final["saved_count"] == 1 and final["save_reason"] is None


@pytest.mark.parametrize("boundary", [
    "bytes", "opened_inode", "publishing_inode", "external_inode",
])
def test_companion_manifest_check_distinguishes_owner_progress_from_external_replacement(
    app, monkeypatch, boundary,
):
    _automatic_companion_state(app)
    read = application._read_regular
    path = app._folder / "batch.json"
    crossings = []
    if boundary == "bytes":
        def crossing_read(current):
            if current == path and not crossings:
                crossings.append(True)
                with app._mutex:
                    app._data["batch_name"] = "First owned revision"
                    app._touch()
                    app._persist()
                intermediate = read(current)
                with app._mutex:
                    app._data["batch_name"] = "Second owned revision"
                    app._touch()
                    app._persist()
                return intermediate
            return read(current)

        monkeypatch.setattr(application, "_read_regular", crossing_read)
        app._assert_companion_source()
    else:
        opened = exports._open_regular_file
        reader_thread = threading.get_ident()
        publishing = boundary == "publishing_inode"
        original_bytes = path.read_bytes()
        ready, replace, replaced, accept = (threading.Event() for _ in range(4))
        if publishing:
            publish = application._publish_owned_file

            def held_publish(current, *args, **kwargs):
                if current == path:
                    ready.set()
                    assert replace.wait(5), "Test must allow the owned replacement"
                    result = publish(current, *args, **kwargs)
                    replaced.set()
                    assert accept.wait(5), "Test must release manifest acceptance"
                    return result
                return publish(current, *args, **kwargs)

            monkeypatch.setattr(application, "_publish_owned_file", held_publish)
            state = app.snapshot()
            draft = state["draft"]
            command_id = str(uuid4())
            outcome = app.command(command_id, state["generation"], "edit_draft", dict(
                draft_id=draft["id"], version=draft["version"], name="In-flight owned revision",
                notes="", options={}, number_samples=False,
            ), batch_id=state["batch_id"])
            assert outcome["admission"] == "admitted" and ready.wait(2)
            previous = app._manifest_bytes

        def crossing_open(current, *args, **kwargs):
            descriptor = opened(current, *args, **kwargs)
            if (Path(current) == path and threading.get_ident() == reader_thread
                    and not crossings):
                crossings.append(True)
                try:
                    if publishing:
                        replace.set()
                        assert replaced.wait(5), "Owned publication must replace the opened inode"
                    elif boundary == "external_inode":
                        foreign = path.with_name("external-manifest")
                        foreign.write_bytes(original_bytes + b" ")
                        # Windows handle-based replacement needs DELETE access
                        # on the actual foreign source, just like owned publication.
                        with exports._regular_descriptor(foreign, delete=True) as source_descriptor:
                            exports._replace_file(foreign, path, source_descriptor)
                    else:
                        with app._mutex:
                            app._data["batch_name"] = "Owned replacement during safe open"
                            app._touch()
                            app._persist()
                except BaseException:
                    exports.os.close(descriptor)
                    raise
            return descriptor

        monkeypatch.setattr(exports, "_open_regular_file", crossing_open)
        try:
            if boundary == "external_inode":
                with pytest.raises(exports._OwnedFileReplacedDuringOpen):
                    app._assert_companion_source()
            else:
                app._assert_companion_source()
            if publishing:
                assert app._manifest_bytes == previous
                assert path.read_bytes() == app._publishing_manifest
        finally:
            replace.set()
            accept.set()
            if boundary == "external_inode":
                path.write_bytes(original_bytes)
            if publishing:
                _wait(app, lambda _: app.command_outcome(command_id)["state"]
                      not in {"pending", "saving"})
            monkeypatch.setattr(exports, "_open_regular_file", opened)
        if publishing:
            assert app.command_outcome(command_id)["state"] == "completed"
            _automatic_companion_state(app)
    assert crossings == [True]
    monkeypatch.setattr(application, "_read_regular", read)
    accepted = path.read_bytes()
    path.write_bytes(accepted + b" ")
    try:
        with pytest.raises(ValueError, match="changed outside this owner"):
            app._assert_companion_source()
    finally:
        path.write_bytes(accepted)

    # Even an in-flight owned-byte witness cannot excuse an unsafe destination.
    for kind in ("symlink", "directory", "hardlink", "missing"):
        original = path.with_name("preserved-original-manifest")
        path.rename(original)
        if kind == "symlink":
            path.symlink_to(original.name)
        elif kind == "directory":
            path.mkdir()
        elif kind == "hardlink":
            exports.os.link(original, path)
        with app._mutex:
            app._publishing_manifest = accepted
        try:
            with pytest.raises(OSError):
                app._assert_companion_source()
        finally:
            with app._mutex:
                app._publishing_manifest = None
            if path.is_dir() and not path.is_symlink():
                path.rmdir()
            elif path.exists() or path.is_symlink():
                path.unlink()
            original.rename(path)

    def denied(current):
        raise PermissionError(f"Permission denied: {current}")

    with monkeypatch.context() as denied_read:
        denied_read.setattr(application, "_read_regular", denied)
        with pytest.raises(PermissionError, match="Permission denied"):
            app._assert_companion_source()

    if os.name == "nt":
        # The persistent Windows batch lock refuses renaming its open parent.
        # File replacement and every unsafe-file refusal above still run here.
        with pytest.raises(PermissionError):
            app._folder.rename(app._folder.parent / "preserved-owned-source")
        return
    moved = app._folder.parent / "preserved-owned-source"
    replaced = []

    def replacement_read(current):
        payload = read(current)
        if current.name == "batch.json" and not replaced:
            replaced.append(True)
            app._folder.rename(moved)
            app._folder.mkdir()
            (app._folder / "batch.json").write_bytes(payload)
        return payload

    monkeypatch.setattr(application, "_read_regular", replacement_read)
    prepared = dict(payload=b"Exact retained CSV publication fixture", source=application._digest(
        app._manifest_bytes,
    ))
    try:
        assert not app._publish_companion(dict(folder=app._folder, previous=dict(csv=None)),
                                          "csv", prepared)
        assert not (app._folder / "measurements.csv").exists()
        assert (moved / "batch.json").read_bytes() == accepted
        assert app._companion_pending["csv"] == prepared
        assert "directory was replaced" in app._companions["csv"]["reason"]
    finally:
        monkeypatch.setattr(application, "_read_regular", read)
        (app._folder / "batch.json").unlink()
        app._folder.rmdir()
        moved.rename(app._folder)
