"""Connected owner/bridge proof without macOS, a renderer or physical equipment."""

import hashlib
import json
import math
import threading
import time
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

import pytest

from color_measurement import presentation, science
from color_measurement.demo import demo_frontends
from color_measurement.desktop_session import DesktopSession

# A command outcome ends in one of these. Acquisition passes through "starting"
# before its worker marks it "active", so waiting while "pending" or "active"
# returned early on a loaded host; waiting for a terminal state cannot.
TERMINAL_COMMAND_STATES = frozenset({"completed", "failed", "refused", "unknown"})


def create(session, folder, name, notes, *, device_id="demo:i1pro3",
           mode="emissive_spot", options=None):
    settings = session.request("settings", {"selection_id": None})
    selection_id = next(row["selection_id"] for row in settings["selections"]
                        if row["device_id"] == device_id)
    return session.create(folder, name, notes, selection_id=selection_id, mode=mode,
                          options={} if options is None else options)


def query(session, method, params=None, *, binding=None):
    source = binding or session.snapshot()["dataset_binding"]
    return session.request(method, dict(binding_id=source["id"], generation=source["generation"])
                           | (params or {}))


def source_key(data, pair):
    return next(key for key, row in data["readings"].items()
                if all(row["observation"][field] == pair[field]
                       for field in ("operation_id", "observation_id")))


def test_missing_mat_capability_uses_operator_language(monkeypatch):
    from color_measurement import operation_exports

    def missing():
        raise ImportError("MAT export/import requires color-measurement[mat]")

    monkeypatch.setattr(operation_exports, "_mat_dependencies", missing)
    session = DesktopSession(demo_frontends())
    formats = session.format_capabilities()
    for kind in ("operation", "analysis", "batch"):
        assert formats[kind]["mat"] == {
            "available": False, "reason": "MAT files are unavailable in this app build.",
        }


def send(session, action, payload=None, *, identity=None, snapshot=None):
    state = snapshot or session.snapshot()["batch"]
    return session.request(
        "command",
        dict(
            command_id=identity or str(uuid4()),
            generation=state["generation"],
            batch_id=state["batch_id"],
            action=action,
            payload=payload or {},
        ),
    )


def wait(session, predicate, *, respond=False):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        state = session.snapshot()["batch"]
        if predicate(state):
            return state
        if respond and state["instructions"]:
            pending = state["instructions"]
            assert (
                send(
                    session,
                    "respond",
                    {k: pending[k] for k in ("operation_id", "request_id")}
                    | {"response": "accept"},
                )["admission"]
                == "admitted"
            )
        time.sleep(0.005)
    pytest.fail(f"Desktop owner did not settle: {session.snapshot()}")


def completed_command(session, action, payload=None, *, identity=None):
    """Wait only when a case requires this exact admitted command's completion."""
    owner = session.batch
    command_id = identity or str(uuid4())
    outcome = send(session, action, payload, identity=command_id)
    assert outcome["admission"] == "admitted", outcome
    wait(session, lambda state: owner.command_outcome(command_id)["state"]
         in TERMINAL_COMMAND_STATES)
    outcome = owner.command_outcome(command_id)
    assert outcome["state"] == "completed", outcome
    return outcome


def settled(session):
    """Capture exact-state assertions after canonical and optional I/O finishes."""
    return wait(session, lambda state: state["phase"] == "idle"
                and not state["companions"]["saving"])


def measure(session):
    state = session.snapshot()["batch"]
    draft = state["draft"]
    outcome = send(
        session,
        "measure",
        dict(context_id=state["context_id"], draft_id=draft["id"], draft_version=draft["version"]),
    )
    assert outcome["admission"] == "admitted"
    wait(session, lambda state: state["phase"] == "idle", respond=True)
    return outcome


def joined_native_session(*, scenario, simulated_session="generic", **selection):
    """Use the original authenticated worker pair through deliberate setup."""
    from conftest import supplied_stub
    from test_desktop_setup import connect_params, refresh, setup

    worker = Path(supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB"))
    report = worker.parent / "report.json"
    session = DesktopSession([])
    selected = session.select_build_report(
        report, expected_setup_generation=0, simulated=True,
        source_root=Path(__file__).resolve().parents[1], scenario=scenario,
        simulated_session=simulated_session,
    )
    assert selected["candidates"] == selected["bindings"] == []
    assert session.settings(None)["selections"] == []
    assert refresh(session)["state"] == "completed"
    params = connect_params(session, **selection)
    connected = session.request("connect_device", params)
    assert connected["state"] == "completed", connected
    assert session.request("connect_device", params) == connected
    assert len(setup(session)["bindings"]) == 1
    result = connected["result"]
    descriptor = session.settings(result["selection_id"])["descriptor"]
    assert descriptor == result["descriptor"] and descriptor["provenance"] == "simulated"
    receipt = json.loads(report.read_bytes())
    identity = descriptor["current_binding_identity"]["provenance"]
    assert identity["build_report_sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()
    assert identity["description_helper_sha256"] == receipt[
        "description_workflow"]["executable_sha256"]
    key = "chart_executable_sha256" if simulated_session == "chart" else "executable_sha256"
    assert identity["session_helper_sha256"] == receipt["generic_session"][key]
    return session, result["selection_id"], descriptor


def native_saved_roundtrip(session, folder, result):
    """Reopen canonical and whole-batch MAT bytes without upgrading failed outcomes."""
    from color_measurement.operation_records import result_from_json, result_to_json

    completed_command(session, "finish")
    if result.patches:
        from test_application import _assert_current_automatic_companion

        completed_command(session, "export_ti3")
        state = settled(session)
        _assert_current_automatic_companion(state, "csv", "measurements.csv")
        _assert_current_automatic_companion(state, "mat", "measurements.mat")
        ti3 = state["ti3"]
        assert ti3["source_sha256"] == hashlib.sha256(
            (folder / "batch.json").read_bytes()).hexdigest()
        current = [item for item in ti3["files"] if item["state"] == "current"]
        for item in current:
            assert hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() == item[
                "payload_sha256"]
        assert current or ti3["unavailable"]
    binding = session.snapshot()["dataset_binding"]
    archive = folder.with_suffix(".mat")
    session.export_batch_mat(archive, binding_id=binding["id"], generation=binding["generation"])
    expected = json.loads(result_to_json(result))
    for source in (folder, archive):
        binding = session.snapshot()["dataset_binding"]
        session.open_saved(source, expected_binding_id=binding["id"],
                           expected_generation=binding["generation"])
        assert session.snapshot()["dataset_binding"]["kind"] == "saved"
        readings = session.dataset()["readings"].values()
        assert [json.loads(json.dumps(row["observation"])) for row in readings] == [
            patch["observation"] for patch in expected["patches"]]
    review = session.review(folder)
    operation, = review["operations"]
    assert operation["review_state"] == "accepted"
    assert result_from_json(json.dumps(operation["result"])) == result


def close_native_session(session):
    binding = session.snapshot()["dataset_binding"]
    session.activate_live(expected_binding_id=binding["id"],
                          expected_generation=binding["generation"])
    state = session.batch.snapshot()
    if state["phase"] != "idle":
        active = next(item for item in state["operations"]
                      if item["operation_id"] == state["operation_id"])
        send(session, "cancel", {"command_id": active["command_id"]})
        state = settled(session)
    outcome = session.batch.command(
        str(uuid4()), state["generation"], "close",
        {"discard_token": session.batch.discard_summary()["token"]}, batch_id=state["batch_id"])
    assert outcome["admission"] == "admitted" and outcome["state"] == "completed", outcome


@pytest.mark.parametrize("mode,options", [
    ("emissive_spot", {}),
    ("emissive_spot_adaptive", {"highres": True}),
    ("emissive_spot_nonadaptive", {"xyz_only": True}),
    ("reflective_spot", {}),
    ("reflective_spot", {"filter": "m0", "xcalstd": "xrga"}),
    ("reflective_spot", {"filter": "m1"}),
    ("reflective_spot", {"filter": "m2"}),
    ("transmission_spot", {}),
    ("transmission_ambient", {}),
    ("ambient_spot", {}),
    ("ambient_flash", {}),
    ("emissive_tele", {}),
    ("emissive_tele_adaptive", {}),
    ("emissive_tele_nonadaptive", {}),
])
def test_i1pro3_authenticated_setup_modes_save_and_reopen(
    tmp_path, monkeypatch, mat, mode, options,
):
    """Join existing substituted description/acquisition owners, not a physical driver."""
    from color_measurement.argyll_capability import FILTERS, MEASUREMENT_CONDITIONS
    from color_measurement.operation_records import result_from_json

    monkeypatch.setenv("CM_CHART_PERSONA", "i1pro3")
    monkeypatch.setenv("CM_CHART_FIXTURE", "startup")
    session, selection, descriptor = joined_native_session(
        scenario="i1pro3", simulated_session="chart", expect="instI1Pro3",
        expected_serial="CM-CHART-0001")
    assert descriptor["device"]["model"] == "X-Rite i1 Pro 3"
    assert descriptor["device"]["serial"] == "CM-CHART-0001"
    offered = next(entry for entry in descriptor["modes"] if entry["mode"] == mode)
    assert offered["available"] and any(
        item["workflow"] == "spot" and item["available"] for item in offered["workflows"])
    folder = tmp_path / "joined-i1pro3"
    if options.get("highres") or "filter" in options:
        # Initialized capabilities are not configured-mode facts. Use the real
        # explicit query and fresh-context workflow before requesting modifiers.
        session.create(tmp_path / "configuration", "Configured-mode query", "",
                       selection_id=selection, mode=mode, options={})
        state = session.snapshot()["batch"]
        completed_command(session, "run_workflow", dict(
            context_id=state["context_id"], draft_id=state["draft"]["id"],
            draft_version=state["draft"]["version"], workflow="mode_snapshot",
            patch_ids=[], layout={}))
        assert not settled(session)["context_current"]
        completed_command(session, "finish")
        refreshed = session.settings(selection)
        selection, descriptor = refreshed["selection_id"], refreshed["descriptor"]
        if options.get("filter") == "m2":
            with pytest.raises(ValueError, match="high-resolution"):
                session.create(tmp_path / "refused-highres", "Unsupported reflective modifier", "",
                               selection_id=selection, mode=mode,
                               options={**options, "highres": True})
            assert not (tmp_path / "refused-highres").exists()
    session.create(folder, "Substituted i1Pro3 join", "", selection_id=selection,
                   mode=mode, options=options)
    try:
        command = measure(session)
        state = settled(session)
        assert state["saved_count"] == state["accepted_count"] == 1
        entry, = state["operations"]
        result = result_from_json((folder / entry["path"]).read_text())
        assert result.operation_id == command["operation_id"]
        if mode == "transmission_ambient":
            # This advertised composite route has an explicit native rejection,
            # already present in the source-bound low-level fixture.
            baseline = offered["baseline_native_mode"]["default"]
            assert baseline["status"] == "rejected"
            assert result.status == "failed" and result.error_stage == "check_mode"
            assert result.error_native_code == baseline["native_code"]
            assert not result.patches and state["sample_count"] == 0
            native_saved_roundtrip(session, folder, result)
            return
        assert result.status == "completed", result.error
        assert state["sample_count"] == 1
        assert result.mode == mode and result.device == result.patches[0].observation.device
        request = json.loads(result.request_json)
        assert request["options"] == options
        observation = result.patches[0].observation
        assert observation.actual_mode == mode and observation.provenance == "simulated"
        if options.get("xyz_only"):
            assert observation.backend_reported.spectrum is None
        else:
            assert observation.backend_reported.spectrum is not None
        if "filter" in options:
            assert dict(observation.configuration)["native_mcond"] == str(
                MEASUREMENT_CONDITIONS[FILTERS[options["filter"]]])
        assert state["ti3"] is not None
        native_saved_roundtrip(session, folder, result)
    finally:
        close_native_session(session)


@pytest.mark.parametrize("response", ["accept", "cancel"])
def test_i1pro3_joined_calibration_prompt_continuation_and_cancellation(
    tmp_path, monkeypatch, mat, response,
):
    from color_measurement.operation_records import result_from_json

    monkeypatch.setenv("CM_CHART_PERSONA", "i1pro3")
    monkeypatch.setenv("CM_CHART_FIXTURE", "startup")
    session, selection, _ = joined_native_session(
        scenario="i1pro3", simulated_session="chart", expect="instI1Pro3",
        expected_serial="CM-CHART-0001")
    folder = tmp_path / "joined-calibration"
    session.create(folder, "Substituted operator calibration", "", selection_id=selection,
                   mode="reflective_spot", options={})
    try:
        state = session.snapshot()["batch"]
        command = send(session, "measure", dict(context_id=state["context_id"],
                       draft_id=state["draft"]["id"], draft_version=state["draft"]["version"]))
        assert command["admission"] == "admitted"
        pending = wait(session, lambda state: state["instructions"])["instructions"]
        assert pending["kind"] == "calibration_setup"
        assert pending["allowed_responses"] == ["accept", "cancel"]
        assert send(session, "respond", dict(operation_id=pending["operation_id"],
                    request_id="stale", response="accept"))["admission"] == "refused"
        assert session.snapshot()["batch"]["instructions"] == pending
        assert send(session, "respond", dict(operation_id=pending["operation_id"],
                    request_id=pending["request_id"], response=response))["admission"] == "admitted"
        wait(session, lambda state: state["phase"] == "idle", respond=True)
        state = settled(session)
        assert state["sample_count"] == int(response == "accept")
        entry, = state["operations"]
        result = result_from_json((folder / entry["path"]).read_text())
        assert result.status == ("completed" if response == "accept" else "cancelled")
        assert result.software_stopped and result.instrument_state == "not_applicable"
        assert state["saved_count"] == 1
        native_saved_roundtrip(session, folder, result)
    finally:
        close_native_session(session)


@pytest.mark.parametrize("scenario,fixture,model,serial", [
    ("cr250_native", "cr250", "instCR250", "SYNTHETIC-250"),
    ("cr100_native", "cr100", "instCR100", "SYNTHETIC-100"),
    ("pr655_native", "pr655_cancel", "instPR655", "SYNTHETIC-P7"),
])
def test_real_c_driver_cancel_joins_session_retention_and_saved_review(
    tmp_path, monkeypatch, mat, scenario, fixture, model, serial,
):
    """The real driver's armed callback consumes a real owner cancellation request."""
    from io import BytesIO

    from color_measurement.native_protocol import HelperSession
    from color_measurement.operation_records import result_from_json

    monkeypatch.setenv("CM_COLORIMETER_FIXTURE", fixture)
    session, selection, _ = joined_native_session(
        scenario=scenario, expect=model, expected_serial=serial,
        accept_disclosed_initialization=model == "instPR655", timeout=500)
    reached, release = threading.Event(), threading.Event()
    original_send = HelperSession.send
    sent = []
    pending_grant = {}

    def grant_then_cancel(native, action, argument=""):
        if action != "read_allowance" and not (action == "cancel" and pending_grant):
            return original_send(native, action, argument)
        # Buffer only commands actually produced by the unchanged owner. The
        # loop must observe its real cancel Event and send the second command.
        stream = native.process.stdin
        buffered = BytesIO()
        native.process.stdin = buffered
        try:
            original_send(native, action, argument)
        finally:
            native.process.stdin = stream
        if action == "read_allowance":
            pending_grant["bytes"] = buffered.getvalue()
            reached.set()
            assert release.wait(8), "The owner did not release its cancelled allowance grant"
            return
        sent.append(pending_grant.pop("bytes") + buffered.getvalue())
        stream.write(sent[-1])
        stream.flush()

    monkeypatch.setattr(HelperSession, "send", grant_then_cancel)
    folder = tmp_path / "joined-c-cancel"
    session.create(folder, "Real C driver over a scripted transport", "", selection_id=selection,
                   mode="emissive_spot", options={})
    try:
        state = session.snapshot()["batch"]
        command_id = str(uuid4())
        outcome = send(session, "measure", dict(context_id=state["context_id"],
                       draft_id=state["draft"]["id"], draft_version=state["draft"]["version"]),
                       identity=command_id)
        assert outcome["admission"] == "admitted"
        assert reached.wait(8), "The actual C driver never reached its allowance exchange"
        assert session.snapshot()["batch"]["instructions"] is None
        assert send(session, "cancel", {"command_id": command_id})["admission"] == "admitted"
        release.set()
        state = settled(session)
        assert state["sample_count"] == 0 and state["saved_count"] == 1
        entry, = state["operations"]
        result = result_from_json((folder / entry["path"]).read_text())
        assert result.status == "cancelled" and result.error_stage == "cancelled"
        assert not result.patches and result.software_stopped
        configuration = dict(result.configuration)
        assert configuration["stop_requested_by"] == "client"
        assert configuration["stop_acknowledged_by"] == "helper"
        assert configuration["worker_stop"] == "exited"
        assert configuration["device_completion"] == "unknown"
        facts = json.loads(configuration["native_facts_json"])
        assert facts["capture"]["valid"] is False
        assert not any(reply["command"] in {"M", "M5"} for reply in facts["raw_replies"])
        assert len(sent) == 1 and sent[0].count(b"\n") == 2
        native_saved_roundtrip(session, folder, result)
    finally:
        release.set()
        close_native_session(session)


def test_settings_catalog_is_compact_explicit_and_cannot_mutate_the_cached_descriptor():
    session = DesktopSession(demo_frontends())
    catalog = session.request("settings", {"selection_id": None})
    assert catalog["selection_id"] is None and catalog["descriptor"] is None
    assert len(catalog["selections"]) > 1
    assert all("modes" not in row and "options" not in row for row in catalog["selections"])
    selected = next(row for row in catalog["selections"] if row["device_id"] == "demo:i1pro3")
    token = selected["selection_id"]
    descriptor = session.request("settings", {"selection_id": token})["descriptor"]
    selected["device"]["model"] = "Altered reply"
    shown = session.request("settings", {"selection_id": token})
    shown["descriptor"]["modes"].clear()
    assert session.request("settings", {"selection_id": token})["descriptor"] == descriptor


@pytest.mark.parametrize("frontends", [None, False, {}, [object()]])
def test_session_requires_explicit_frontend_objects(frontends):
    with pytest.raises(ValueError, match="explicit collection"):
        DesktopSession(frontends)


def test_empty_source_collection_does_not_invent_demo_or_a_default_selection(tmp_path):
    session = DesktopSession([])
    assert session.request("settings", {"selection_id": None})["selections"] == []
    assert session.dataset()["roster_revision"] == session.dataset()["dataset_revision"] == "empty"
    with pytest.raises(ValueError, match="Unknown or stale"):
        session.create(tmp_path / "none", "No source", "", selection_id=str(uuid4()),
                       mode="emissive_spot", options={})
    assert not (tmp_path / "none").exists()


def test_duplicate_source_identity_is_refused():
    front = demo_frontends()[0]
    with pytest.raises(ValueError, match="distinct device identities"):
        DesktopSession([front, front])


def test_native_settings_read_apply_and_repeated_measurement_keep_desktop_selection(
    tmp_path, monkeypatch,
):
    from test_native_controls import native_frontend, native_readback_seam

    from color_measurement.application import review_saved

    front = native_frontend()
    native_readback_seam(monkeypatch, front)
    session = DesktopSession([front])
    settings = session.request("settings", {"selection_id": None})
    selection = settings["selections"][0]["selection_id"]
    offered = session.request("settings", {"selection_id": selection})
    scope = dict(selection_id=selection, mode="emissive_spot",
                 capability_id=offered["descriptor"]["capability_id"],
                 settings_generation=offered["settings_generation"])
    original_scope = session._correction_scope(**scope)
    session.create(tmp_path / "native-settings", "Settings", "", selection_id=selection,
                   mode="emissive_spot", options={})
    try:
        before = session.snapshot()["batch"]
        draft = before["draft"]
        assert send(session, "run_workflow", dict(
            context_id=before["context_id"], draft_id=draft["id"], draft_version=draft["version"],
            workflow="native_controls", patch_ids=[], layout=dict(action="read"),
        ))["admission"] == "admitted"
        settled(session)
        assert session.request("settings", {"selection_id": selection})["selection_id"] == selection
        state = session.snapshot()["batch"]
        draft = state["draft"]
        command_id = str(uuid4())
        assert send(session, "edit_draft", dict(
            draft_id=draft["id"], version=draft["version"], name="Settings", notes="",
            number_samples=True, options=dict(native_controls=dict(cycles=3)),
        ), identity=command_id)["admission"] == "admitted"
        wait(session, lambda state: session.batch.command_outcome(command_id)["state"]
             not in {"pending", "saving"})
        assert session.batch.command_outcome(command_id)["state"] == "completed"
        state = settled(session)
        draft = state["draft"]
        assert send(session, "run_workflow", dict(
            context_id=state["context_id"], draft_id=draft["id"], draft_version=draft["version"],
            workflow="native_controls", patch_ids=[], layout=dict(action="apply"),
        ))["admission"] == "admitted"
        settled(session)
        for _ in range(2):
            measure(session)
            state = settled(session)
            assert state["context_current"] and state["context_id"] == before["context_id"]
        assert state["sample_count"] == 2
        review = review_saved(session.batch._folder)
        assert [row["result"]["workflow"] for row in review["operations"]] == [
            "native_controls", "native_controls", "spot", "spot"]
        assert session.request("settings", {"selection_id": selection})["selection_id"] == selection
        refreshed_scope = session._correction_scope(**scope)
        assert refreshed_scope[0] is original_scope[0]
        assert refreshed_scope[2] == original_scope[2]
        front._current_binding_identity["provenance"]["build_report_sha256"] = "f" * 64
        with pytest.raises(ValueError, match="descriptor|selection|settings"):
            session._correction_scope(**scope)
        completed_command(session, "finish")
    finally:
        from test_application import _command

        _command(session.batch, "close",
                 dict(discard_token=session.batch.discard_summary()["token"]))


def test_native_registration_offers_a_later_binding_without_changing_current_batch(
    tmp_path, monkeypatch,
):
    frontends = {json.loads(front.describe_json())["device_id"]: front
                 for front in demo_frontends()}
    first, second = frontends["demo:i1pro3"], frontends["demo:i1pro"]
    session = DesktopSession([first])
    create(session, tmp_path / "registered-first", "First", "")
    try:
        measure(session)
        settled(session)
        original, data = session.snapshot(), session.dataset()
        before = session.request("settings", {"selection_id": None})

        def no_prepare(*args, **kwargs):
            pytest.fail("Registering cached metadata prepared an operation")

        with monkeypatch.context() as patch:
            patch.setattr(second, "prepare", no_prepare)
            token = session.register_frontend(second)
            with pytest.raises(ValueError, match="distinct device identities"):
                session.register_frontend(second)
            with pytest.raises(ValueError, match="already-bound"):
                session.register_frontend(object())
        assert session.snapshot() == original and session.dataset() == data
        settings = session.request("settings", {"selection_id": token})
        assert settings["selections"][0] == before["selections"][0]
        assert settings["descriptor"]["device_id"] == "demo:i1pro"
        assert len(settings["selections"]) == 2
        with pytest.raises(ValueError, match="Unknown desktop method"):
            session.request("register_frontend", {"selection_id": token})
        completed_command(session, "finish")
        session.create(tmp_path / "registered-second", "Second", "",
                       selection_id=token, mode="emissive_spot", options={})
        assert session.snapshot()["batch"]["descriptor"]["device_id"] == "demo:i1pro"
        measure(session)
        observation = next(iter(session.dataset()["readings"].values()))["observation"]
        assert observation["device"]["device_id"] == "demo:i1pro"
        completed_command(session, "finish")
    finally:
        session.batch._release_lock()
        session.batch._closed = True


def test_changed_frontend_descriptor_requires_explicit_refresh_before_folder_mutation(
    tmp_path, monkeypatch
):
    front = next(f for f in demo_frontends()
                 if json.loads(f.describe_json())["device_id"] == "demo:i1pro3")
    session = DesktopSession([front])
    token = session.request("settings", {"selection_id": None})["selections"][0]["selection_id"]
    descriptor = json.loads(front.describe_json())
    descriptor["capability_id"] = hashlib.sha256(b"Substituted new capability context").hexdigest()
    monkeypatch.setattr(front, "describe_json", lambda: json.dumps(descriptor))
    with pytest.raises(ValueError, match="Frontend context changed"):
        session.create(tmp_path / "stale", "Stale", "", selection_id=token,
                       mode="emissive_spot", options={})
    assert not (tmp_path / "stale").exists() and session.batch is None
    refreshed = session.request("settings", {"selection_id": token})
    assert refreshed["selection_id"] != token and refreshed["descriptor"] == descriptor
    with pytest.raises(ValueError, match="Unknown or stale"):
        session.create(tmp_path / "old-token", "Old", "", selection_id=token,
                       mode="emissive_spot", options={})
    assert not (tmp_path / "old-token").exists()


def test_descriptor_change_after_session_precheck_is_refused_before_owner_mutation(
    tmp_path, monkeypatch,
):
    front = next(f for f in demo_frontends()
                 if json.loads(f.describe_json())["device_id"] == "demo:i1pro3")
    shown = json.loads(front.describe_json())
    changed = deepcopy(shown)
    changed["device"]["backend_version"] += "-changed-cached-metadata"
    changed["capability_id"] = hashlib.sha256(json.dumps(
        {key: value for key, value in changed.items() if key != "capability_id"},
        sort_keys=True, allow_nan=False,
    ).encode("utf-8")).hexdigest()
    descriptions, preparations = [], []
    prepare = front.prepare

    def describe(*args):
        # Registration, catalogue, selected settings and preliminary check see A.
        # Final constructor admission and any later preparation see B.
        descriptor = shown if len(descriptions) < 4 else changed
        descriptions.append(deepcopy(descriptor))
        return json.dumps(descriptor)

    def prepare_spy(*args, **kwargs):
        preparations.append(args)
        return prepare(*args, **kwargs)

    monkeypatch.setattr(front, "describe_json", describe)
    monkeypatch.setattr(front, "prepare", prepare_spy)
    session = DesktopSession([front])
    token = session.request("settings", {"selection_id": None})["selections"][0]["selection_id"]
    assert session.request("settings", {"selection_id": token})["descriptor"] == shown
    before = session.snapshot()
    try:
        with pytest.raises(
            ValueError, match="Selected frontend descriptor changed; select it again",
        ):
            session.create(tmp_path / "raced", "Unshown source", "", selection_id=token,
                           mode="emissive_spot", options={})
        assert descriptions == [shown] * 4 + [changed]
        assert preparations == []
        assert not front._ownership.locked()
        assert not (tmp_path / "raced").exists() and session.batch is None
        assert session.snapshot() == before
    finally:
        if session.batch is not None:
            session.batch._release_lock()
            session.batch._closed = True


@pytest.mark.parametrize("mode,options", [("not-advertised", {}),
                                        ("emissive_spot", {"unknown_option": True})])
def test_create_validates_advertised_mode_and_options_before_folder_mutation(
    tmp_path, mode, options,
):
    session = DesktopSession(demo_frontends())
    with pytest.raises(ValueError):
        create(session, tmp_path / "refused", "Refused", "", mode=mode, options=options)
    assert not (tmp_path / "refused").exists() and session.batch is None


def test_new_batch_can_select_another_bound_frontend_without_reusing_old_context(tmp_path):
    session = DesktopSession(demo_frontends())
    first = create(session, tmp_path / "first-source", "First", "")
    measure(session)
    finished = completed_command(session, "finish")
    assert finished["admission"] == "admitted" and finished["state"] == "completed"
    assert session.snapshot()["batch"]["status"] == "finished"
    second = create(session, tmp_path / "second-source", "Second", "", device_id="demo:i1pro")
    assert second["batch"]["descriptor"]["device_id"] == "demo:i1pro"
    assert second["batch"]["context_id"] != first["batch"]["context_id"]
    measure(session)
    row = next(iter(session.dataset()["readings"].values()))
    assert row["observation"]["device"]["device_id"] == "demo:i1pro"
    completed_command(session, "finish")


def test_renderer_correction_paths_are_refused_before_create_or_draft_mutation(
    tmp_path, monkeypatch,
):
    from color_measurement import instrument
    from color_measurement.instrument_frontend import InstrumentFrontend

    preparations, reads = [], []
    prepare = InstrumentFrontend.prepare

    def prepare_spy(frontend, *args, **kwargs):
        preparations.append(args)
        return prepare(frontend, *args, **kwargs)

    def read_spy(path):
        reads.append(path)
        raise AssertionError("Renderer correction path reached the file reader")

    monkeypatch.setattr(InstrumentFrontend, "prepare", prepare_spy)
    monkeypatch.setattr(instrument, "read_correction", read_spy)
    session = DesktopSession(demo_frontends())
    with pytest.raises(ValueError, match="native file dialog"):
        create(session, tmp_path / "raw-path", "Refused path", "",
               options={"correction": {"path": str(tmp_path / "unselected.ccmx")}})
    assert not (tmp_path / "raw-path").exists()
    assert preparations == reads == []
    create(session, tmp_path / "no-correction", "Bound source", "")
    preparations.clear()
    before = session.snapshot()
    draft = before["batch"]["draft"]
    with pytest.raises(ValueError, match="native file dialog"):
        send(session, "edit_draft", dict(
            draft_id=draft["id"], version=draft["version"], name=draft["name"], notes="",
            number_samples=draft["number_samples"],
            options={"correction": {"path": str(tmp_path / "unselected.ccmx")}},
        ))
    assert session.snapshot() == before
    assert preparations == reads == []
    assert send(session, "finish")["admission"] == "admitted"


@pytest.mark.parametrize("selection", ["unselected.ccmx", {"path": "unselected.ccss"}])
def test_renderer_display_type_paths_are_refused_without_reads_or_draft_mutation(
    tmp_path, monkeypatch, selection,
):
    from color_measurement import instrument

    reads = []

    def read_spy(path):
        reads.append(path)
        raise AssertionError("Display-type path reached the correction file reader")

    monkeypatch.setattr(instrument, "read_correction", read_spy)
    session = DesktopSession(demo_frontends())
    with pytest.raises(ValueError, match="named choice identity"):
        create(session, tmp_path / "raw-display-type", "Refused", "",
               options={"display_type": selection})
    assert not (tmp_path / "raw-display-type").exists() and session.batch is None
    create(session, tmp_path / "display-type-owner", "Bound source", "")
    try:
        before = session.snapshot()
        manifest = Path(before["batch"]["folder"]) / "batch.json"
        manifest_bytes = manifest.read_bytes()
        draft = before["batch"]["draft"]
        outcome = send(session, "edit_draft", dict(
            draft_id=draft["id"], version=draft["version"], name=draft["name"], notes="",
            number_samples=draft["number_samples"], options={"display_type": selection},
        ))
        assert outcome["admission"] == "refused" and "named choice identity" in outcome["reason"]
        after = session.snapshot()
        # The refused command is recorded, but acquired/setup/draft state and
        # the durable manifest must remain unchanged.
        for field in ("draft", "descriptor", "mode", "context_id", "operations",
                      "annotations", "annotation_drafts"):
            assert after["batch"][field] == before["batch"][field]
        assert manifest.read_bytes() == manifest_bytes
        assert reads == []
        assert send(session, "finish")["admission"] == "admitted"
    finally:
        session.batch._release_lock()
        session.batch._closed = True


def test_xyz_only_native_readings_keep_color_metrics_when_spectrum_is_absent(tmp_path):
    session = DesktopSession(demo_frontends())
    try:
        create(session, tmp_path / "xyz-only", "XYZ-only Demo", "", options={"xyz_only": True})
        measure(session)
        measure(session)
        data = session.dataset()
        assert all(row["observation"]["backend_reported"]["spectrum"] is None
                   for row in data["readings"].values())
        reference = next(iter(data["readings"].values()))["observation"]
        pair = {key: reference[key] for key in ("operation_id", "observation_id")}
        assert send(session, "set_comparison_context", dict(
            version=0, reference=pair, white=dict(pair, kind="retained-reading"),
        ))["admission"] == "admitted"
        wait(session, lambda state: state["phase"] == "idle")
        result = xyz_analysis(session)
        key = result["selected"][0]
        assert result["delta_e_by_source"][key] is not None
        assert result["xyz_comparison_by_source"][key]["y_percent"] is not None
        assert result["comparison_by_source"][key]["rms"] is None
        assert result["comparison_by_source"][key]["reason"]
        assert result["summary"]["spectral_unavailable"] == 1
    finally:
        session.batch._release_lock()
        session.batch._closed = True


def test_spectrum_only_dataset_retains_row_and_native_values_without_xyz_fabrication(
    xyz_comparison_session, monkeypatch
):
    session = xyz_comparison_session
    before = session.dataset()
    reference = source_key(before, session.snapshot()["batch"]["comparison"]["reference"])
    key = next(key for key in before["readings"] if key != reference)
    details = session.batch.reading_details

    def spectrum_only(operation_id, observation_id):
        record = details(operation_id, observation_id)
        expected = before["readings"][key]["observation"]
        if (operation_id, observation_id) == (expected["operation_id"], expected["observation_id"]):
            record["backend_reported"]["xyz"] = None
            record["project_computed"] = None
        return record

    monkeypatch.setattr(session.batch, "reading_details", spectrum_only)
    session._binding._observations.clear()
    session._binding._dataset_key = None
    data = session.dataset()
    row = data["readings"][key]
    assert row["observation"]["backend_reported"]["xyz"] is None
    assert row["observation"]["backend_reported"]["spectrum"] == before["readings"][key][
        "observation"
    ]["backend_reported"]["spectrum"]
    assert row["xyy"] is row["uvy"] is None
    assert all(row["coordinate_reasons"].values())
    assert key not in data["eligible_white_sources"]
    assert all(value["display_rgb"] is None for value in row["display_previews"].values())
    result = xyz_analysis(session)
    assert result["comparison_by_source"][key]["rms"] is not None
    assert result["xyz_comparison_by_source"][key]["y_percent"] is None
    assert result["xyz_comparison_by_source"][key]["uv_distance"] is None


def test_draft_and_annotation_metadata_do_not_change_scientific_input_identity(
    xyz_comparison_session,
):
    session = xyz_comparison_session
    before = session.dataset()
    metrics = xyz_analysis(session)["xyz_comparison_by_source"]
    state = session.snapshot()["batch"]
    assert before["roster_revision"] == f"{state['batch_id']}/{state['revision']}"
    draft = state["draft"]
    assert send(session, "edit_draft", dict(
        draft_id=draft["id"], version=draft["version"], name="Pending sample text",
        notes="Typing before the next measurement", number_samples=False, options=draft["options"],
    ))["admission"] == "admitted"
    settled(session)
    assert session.dataset()["dataset_revision"] == before["dataset_revision"]
    after_draft = session.dataset()
    current = session.snapshot()["batch"]
    assert after_draft["roster_revision"] == f"{current['batch_id']}/{current['revision']}"
    assert after_draft["roster_revision"] != before["roster_revision"]
    identity = next(iter(before["readings"]))
    row = before["readings"][identity]
    pair = {field: row["observation"][field] for field in ("operation_id", "observation_id")}
    assert send(session, "edit_annotation_draft", dict(
        pair, version=row["version"], draft_version=0, name="Updated annotation", notes="New notes",
    ))["admission"] == "admitted"
    assert send(session, "annotate", dict(
        pair, version=row["version"], draft_version=1,
    ))["admission"] == "admitted"
    settled(session)
    after = session.dataset()
    assert after["dataset_revision"] == before["dataset_revision"]
    current = session.snapshot()["batch"]
    assert after["roster_revision"] == f"{current['batch_id']}/{current['revision']}"
    assert after["roster_revision"] != after_draft["roster_revision"]
    assert after["readings"][identity]["name"] == "Updated annotation"
    assert xyz_analysis(session)["xyz_comparison_by_source"] == metrics
    measure(session)
    assert session.dataset()["dataset_revision"] != before["dataset_revision"]
    with pytest.raises(ValueError, match="Reading revision changed"):
        query(session, "analysis", dict(
            selected=list(before["readings"]), scale=100,
            dataset_revision=before["dataset_revision"],
            comparison_version=session.snapshot()["batch"]["comparison"]["version"],
        ))


def test_equal_native_observation_ids_in_retained_operations_keep_distinct_dataset_keys(
    tmp_path, monkeypatch,
):
    from color_measurement import instrument_frontend

    # This seam assigns only observation IDs during actual Demo retention.
    # Prepared operation IDs and both authenticated canonical records stay real.
    native_id = "repeated-native-observation"
    monkeypatch.setattr(instrument_frontend, "uuid4", lambda: native_id)
    session = DesktopSession(demo_frontends())
    folder = tmp_path / "paired-identities"
    create(session, folder, "Duplicate native ID", "")
    try:
        measure(session)
        first_revision = session.dataset()["dataset_revision"]
        measure(session)
        data = session.dataset()
        keys = list(data["readings"])
        assert len(keys) == 2 and native_id not in keys
        assert all(len(key) == 64 and set(key) <= set("0123456789abcdef") for key in keys)
        pairs = [{field: data["readings"][key]["observation"][field]
                  for field in ("operation_id", "observation_id")} for key in keys]
        assert pairs[0]["operation_id"] != pairs[1]["operation_id"]
        assert all(pair["observation_id"] == native_id for pair in pairs)
        assert data["dataset_revision"] != first_revision
        reviewed = session.review(folder)
        assert reviewed["sample_count"] == 2
        assert all(operation["review_state"] == "accepted" for operation in reviewed["operations"])
        canonical = {folder / operation["path"]: (folder / operation["path"]).read_bytes()
                     for operation in reviewed["operations"]}
        for key, pair in zip(keys, pairs, strict=True):
            assert session.batch.reading_details(**pair) == data["readings"][key]["observation"]
            assert send(session, "select", pair)["admission"] == "admitted"
            assert session.snapshot()["batch"]["selected"] == pair
        assert send(session, "set_comparison_context", dict(
            version=0, reference=pairs[0], white=dict(pairs[1], kind="retained-reading"),
        ))["admission"] == "admitted"
        wait(session, lambda state: state["phase"] == "idle")
        result = xyz_analysis(session)
        assert result["reference"] == keys[0] and result["selected"] == [keys[1]]
        assert result["white"] == dict(pairs[1], kind="retained-reading")
        assert set(result["xyz_comparison_by_source"]) == {keys[1]}
        assert result["xyz_comparison_by_source"][keys[1]]["y_percent"] is not None
        assert result["delta_e_by_source"][keys[1]] is not None
        view = dict(
            plotSlots=["space", "chromaticity"],
            plotted=[keys[1]], previousPlotted=[keys[0]], displayScale=100, follow=True,
            tool="compare", inspector=True, controls={}, expanded=[], zoom=1,
        )
        source = session.snapshot()["dataset_binding"]
        view_request = dict(binding_id=source["id"], generation=source["generation"], view=view)
        assert session.request("set_view", view_request)["view"] == view
        for slots in (["space", "space"], ["space"], ["space", "unknown"], [None, "space"]):
            with pytest.raises(ValueError, match="plot positions"):
                session.request("set_view", dict(view_request, view=dict(view, plotSlots=slots)))
        with pytest.raises(ValueError, match="retained readings"):
            session.request("set_view", dict(view_request, view=dict(view, plotted=[native_id])))
        assert session.bind()["view"] == view
        for index, pair in enumerate(pairs):
            assert send(session, "edit_annotation_draft", dict(
                pair, version=0, draft_version=0, name=f"Operation {index}",
                notes=f"Distinct notes {index}",
            ))["admission"] == "admitted"
        drafts = session.dataset()["drafts"]
        assert set(drafts) == set(keys)
        for index, (key, pair) in enumerate(zip(keys, pairs, strict=True)):
            assert drafts[key]["notes"] == f"Distinct notes {index}"
            assert query(session, "annotation", {"reading_ref": pair})["draft"] == drafts[key]
            assert send(session, "annotate", dict(
                pair, version=0, draft_version=1,
            ))["admission"] == "admitted"
            wait(session, lambda state: state["phase"] == "idle")
        after = session.dataset()
        assert after["dataset_revision"] == data["dataset_revision"]
        assert after["drafts"] == {}
        assert [after["readings"][key]["name"] for key in keys] == ["Operation 0", "Operation 1"]
        assert (xyz_analysis(session)["xyz_comparison_by_source"]
                == result["xyz_comparison_by_source"])
        assert all(path.read_bytes() == payload for path, payload in canonical.items())
        reviewed = session.review(folder)
        assert reviewed["sample_count"] == 2 and reviewed["comparison"]["available"]
        assert all(operation["review_state"] == "accepted" for operation in reviewed["operations"])
        assert send(session, "finish")["admission"] == "admitted"
    finally:
        session.batch._release_lock()
        session.batch._closed = True


@pytest.mark.parametrize("pause", ["before-publication", "after-publication"])
def test_unrelated_pending_draft_publication_preserves_authenticated_scientific_queries(
    xyz_comparison_session, monkeypatch, pause,
):
    from color_measurement import application

    session = xyz_comparison_session
    before = session.dataset()
    entered, release = threading.Event(), threading.Event()
    publish = application._publish_owned_file

    def paused(*args, **kwargs):
        if pause == "before-publication":
            entered.set()
            assert release.wait(5), "Synthetic metadata publisher was not released"
        result = publish(*args, **kwargs)
        if pause == "after-publication":
            entered.set()
            assert release.wait(5), "Synthetic metadata publisher was not released"
        return result

    with monkeypatch.context() as patch:
        patch.setattr(application, "_publish_owned_file", paused)
        state = session.snapshot()["batch"]
        draft = state["draft"]
        assert send(session, "edit_draft", dict(
            draft_id=draft["id"], version=draft["version"], name="Autosave while inspecting",
            notes="Unrelated draft", options=draft["options"], number_samples=False,
        ))["admission"] == "admitted"
        try:
            assert entered.wait(3), "Synthetic metadata publisher did not enter"
            assert session.dataset()["dataset_revision"] == before["dataset_revision"]
            result = xyz_analysis(session)
            assert result["xyz_comparison_by_source"]
            assert result["summary"]["y_unavailable"] == 0
        finally:
            release.set()
            wait(session, lambda state: state["phase"] == "idle")


def test_connected_route_lost_return_rebind_annotation_finish_review_and_new_batch(tmp_path):
    session = DesktopSession(demo_frontends())
    original = create(session, tmp_path / "first", "Study", "Batch\nnotes")["batch"]
    draft = original["draft"]
    assert (
        send(
            session,
            "edit_draft",
            dict(
                version=draft["version"],
                name="Sample A",
                notes="Synthetic\nrecord",
                options={},
                draft_id=draft["id"],
                number_samples=False,
            ),
        )["admission"]
        == "admitted"
    )
    wait(session, lambda state: state["phase"] == "idle")
    state = session.snapshot()["batch"]
    params = dict(
        context_id=state["context_id"],
        draft_id=state["draft"]["id"],
        draft_version=state["draft"]["version"],
    )
    identity = str(uuid4())
    # Discard the first return; repeat its exact canonical content after a bind.
    send(session, "measure", params, identity=identity, snapshot=state)
    rebound = session.bind()["batch"]
    assert rebound["generation"] > state["generation"]
    send(session, "measure", params, identity=identity, snapshot=state)
    assert send(session, "calibrate", params, snapshot=state)["admission"] == "refused"
    done = wait(session, lambda state: state["phase"] == "idle", respond=True)
    assert done["sample_count"] == 1
    rows = query(session, "dataset", {})
    row = next(iter(rows["readings"].values()))
    observation = row["observation"]
    pair = dict(
        operation_id=observation["operation_id"], observation_id=observation["observation_id"]
    )
    assert (
        send(
            session,
            "edit_annotation_draft",
            dict(
                pair, version=row["version"], draft_version=0, name="Renamed", notes="\nExact notes"
            ),
        )["admission"]
        == "admitted"
    )
    wait(session, lambda state: not state["companions"]["saving"])
    summary = session.batch.discard_summary()
    session.bind()
    detail = query(session, "annotation", {"reading_ref": pair})
    assert detail["draft"]["notes"] == "\nExact notes"
    assert session.batch.discard_summary()["token"] == summary["token"]
    assert (
        send(
            session,
            "annotate",
            dict(
                pair,
                version=detail["applied"]["version"],
                draft_version=detail["draft"]["draft_version"],
            ),
        )["admission"]
        == "admitted"
    )
    wait(session, lambda state: state["phase"] == "idle")
    finish_id = str(uuid4())
    assert send(session, "finish", identity=finish_id)["admission"] == "admitted"
    wait(session, lambda state: session.batch.command_outcome(finish_id)["state"] != "pending")
    assert session.batch.command_outcome(finish_id)["state"] == "completed"
    reviewed = session.review(tmp_path / "first")
    assert reviewed["read_only"] and reviewed["sample_count"] == 1
    assert reviewed["manifest"]["batch_name"] == "Study"
    assert reviewed["manifest"]["batch_notes"] == "Batch\nnotes"
    assert reviewed["manifest"]["annotations"][0]["notes"] == "\nExact notes"
    second = create(session, tmp_path / "second", "Second", "")["batch"]
    assert second["batch_id"] != original["batch_id"]
    assert session.dataset()["readings"] == {}


def test_analysis_uses_current_reading_and_revision_without_mutating_it(tmp_path):
    session = DesktopSession(demo_frontends())
    create(session, tmp_path / "analysis", "Study", "")
    measure(session)
    measure(session)
    data = session.dataset()
    selected = list(data["readings"])
    observation = data["readings"][selected[0]]["observation"]
    pair = {key: observation[key] for key in ("operation_id", "observation_id")}
    assert (
        send(session, "set_comparison_context", dict(version=0, reference=pair, white=None))[
            "admission"
        ]
        == "admitted"
    )
    state = settled(session)
    data = session.dataset()
    params = dict(
        comparison_version=state["comparison"]["version"],
        scale=100,
        dataset_revision=data["dataset_revision"],
    )
    projected = query(session, "projection", params)
    assert set(projected["display_previews"]) == set(selected)
    result = query(session, "analysis", dict(params, selected=selected))
    residual = result["comparison_by_source"][selected[1]]["residual"]
    assert residual["source_quantity"] == "spectral_radiance"
    assert session.dataset() == data
    with pytest.raises(ValueError, match="revision changed"):
        query(session, "projection", dict(params, dataset_revision="stale"))
    completed_command(session, "finish")


def test_analysis_has_no_implicit_reference_and_fences_persisted_context(tmp_path):
    session = DesktopSession(demo_frontends())
    create(session, tmp_path / "comparison", "Study", "")
    measure(session)
    data = session.dataset()
    selected = list(data["readings"])
    params = dict(scale=100, dataset_revision=data["dataset_revision"], comparison_version=0)
    reply = query(session, "analysis", dict(params, selected=selected))
    assert reply["reference"] is None and reply["white"] is None and not reply["delta_e_by_source"]
    assert reply["comparison_version"] == 0
    assert all(
        row["y_percent"] is row["uv_distance"] is None and row["y_reason"] and row["uv_reason"]
        for row in reply["xyz_comparison_by_source"].values()
    )
    assert all(
        "Choose a saved reference" in row["reason"]
        for row in reply["comparison_by_source"].values()
    )
    observation = data["readings"][selected[0]]["observation"]
    pair = {key: observation[key] for key in ("operation_id", "observation_id")}
    assert (
        send(session, "set_comparison_context", dict(version=0, reference=pair, white=None))[
            "admission"
        ]
        == "admitted"
    )
    settled(session)
    params["dataset_revision"] = session.dataset()["dataset_revision"]
    with pytest.raises(ValueError, match="Comparison version changed"):
        query(session, "analysis", dict(params, selected=selected))
    params["comparison_version"] = 1
    reply = query(session, "analysis", dict(params, selected=selected))
    assert reply["reference"] == selected[0] and reply["comparison_version"] == 1
    with pytest.raises(ValueError, match="Expected fields"):
        query(session, "analysis", dict(
            params, selected=selected, reference=selected[0], white=None,
        )
        )
    state = session.snapshot()["batch"]
    preview = dict(
        batch_id=state["batch_id"],
        generation=state["generation"],
        draft_id=state["draft"]["id"],
        version=state["draft"]["version"],
        name="White",
        number_samples=True,
    )
    assert session.request("preview_draft", preview) == dict(preview, resolved_name="White 01")
    completed_command(session, "finish")


@pytest.fixture
def xyz_comparison_session(tmp_path):
    session = DesktopSession(demo_frontends())
    create(session, tmp_path / "native-xyz-comparison", "Synthetic comparison", "")
    try:
        measure(session)
        measure(session)
        data = session.dataset()
        observation = next(iter(data["readings"].values()))["observation"]
        pair = {key: observation[key] for key in ("operation_id", "observation_id")}
        assert send(session, "set_comparison_context", dict(version=0, reference=pair, white=None))[
            "admission"
        ] == "admitted"
        settled(session)
        yield session
    finally:
        settled(session)
        session.batch._release_lock()
        session.batch._closed = True


def xyz_analysis(session, *, scale=100):
    state, data = session.snapshot()["batch"], session.dataset()
    reference = source_key(data, state["comparison"]["reference"])
    return query(session, "analysis", dict(
        selected=[key for key in data["readings"] if key != reference],
        scale=scale, dataset_revision=data["dataset_revision"],
        comparison_version=state["comparison"]["version"],
    ))


def test_native_xyz_analysis_uses_unplotted_saved_reference_without_lab_white(
    xyz_comparison_session,
):
    session = xyz_comparison_session
    before = session.dataset()
    result = xyz_analysis(session)
    reference = before["readings"][result["reference"]]["observation"]["backend_reported"]["xyz"]
    key = result["selected"][0]
    sample = before["readings"][key]["observation"]["backend_reported"]["xyz"]
    ry, sy = Fraction(reference["values"][1]), Fraction(sample["values"][1])
    expected_y = float(100 * (sy - ry) / ry)

    def exact_uv(values):
        x, y, z = map(Fraction, values)
        denominator = x + 15 * y + 3 * z
        return 4 * x / denominator, 9 * y / denominator

    a, b = exact_uv(reference["values"]), exact_uv(sample["values"])
    expected_uv = math.sqrt(float((b[0] - a[0])**2 + (b[1] - a[1])**2))
    metric = result["xyz_comparison_by_source"][key]
    assert metric["y_percent"] == pytest.approx(expected_y, rel=2e-12, abs=1e-12)
    assert metric["uv_distance"] == pytest.approx(expected_uv, rel=2e-12, abs=1e-15)
    assert metric["y_reason"] is metric["uv_reason"] is None
    assert result["white"] is None and result["delta_e_by_source"] == {}
    assert result["reference"] not in result["selected"]
    assert result["summary"]["y_percent_range"]["count"] == 1
    assert result["summary"]["uv_distance_range"]["count"] == 1
    assert result["summary"]["y_unavailable"] == result["summary"]["uv_unavailable"] == 0
    assert session.dataset() == before
    assert xyz_analysis(session, scale=1)["xyz_comparison_by_source"] == result[
        "xyz_comparison_by_source"
    ]
    state = session.snapshot()["batch"]
    pair = state["comparison"]["reference"]
    assert send(session, "set_comparison_context", dict(
        version=state["comparison"]["version"], reference=pair,
        white=dict(pair, kind="retained-reading"),
    ))["admission"] == "admitted"
    wait(session, lambda state: state["phase"] == "idle")
    with_white = xyz_analysis(session, scale=200)
    assert with_white["delta_e_by_source"] and with_white["white"]
    assert with_white["xyz_comparison_by_source"] == result["xyz_comparison_by_source"]


@pytest.mark.parametrize("kind", ["black", "mismatch", "missing"])
def test_xyz_analysis_serializes_independent_unavailability(
    xyz_comparison_session, monkeypatch, kind
):
    session = xyz_comparison_session
    original, data = session.dataset(), deepcopy(session.dataset())
    reference = source_key(data, session.snapshot()["batch"]["comparison"]["reference"])
    key = next(key for key in data["readings"] if key != reference)
    reported = data["readings"][key]["observation"]["backend_reported"]
    # Substitute only this analysis input DTO; canonical observations stay unchanged.
    if kind == "black":
        reported["xyz"]["values"] = (0, 0, 0)
    elif kind == "mismatch":
        reported["xyz"]["observer"] = "CIE1964_10"
    else:
        reported["xyz"] = None
    with monkeypatch.context() as patch:
        patch.setattr(session._binding, "data", lambda: deepcopy(data))
        result = xyz_analysis(session)
    metric = result["xyz_comparison_by_source"][key]
    assert metric["uv_distance"] is None and metric["uv_reason"]
    assert result["summary"]["uv_distance_range"] is None
    assert result["summary"]["uv_unavailable"] == 1
    if kind == "black":
        assert metric["y_percent"] == -100 and metric["y_reason"] is None
        assert result["summary"]["y_percent_range"] == dict(min=-100, max=-100, count=1, unit="%")
        assert result["summary"]["y_unavailable"] == 0
    else:
        assert metric["y_percent"] is None and metric["y_reason"]
        assert result["summary"]["y_percent_range"] is None
        assert result["summary"]["y_unavailable"] == 1
    assert session.dataset() == original


@pytest.mark.parametrize("target", ["manifest", "reference-record"])
def test_xyz_analysis_refuses_external_context_changes(xyz_comparison_session, target):
    session = xyz_comparison_session
    state = session.snapshot()["batch"]
    path = Path(state["folder"]) / (
        "batch.json" if target == "manifest" else state["operations"][0]["path"]
    )
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\n")
        with pytest.raises(ValueError, match="cannot be authenticated"):
            xyz_analysis(session)
    finally:
        path.write_bytes(original)


def test_xyz_analysis_refuses_unsaved_context_before_metrics(xyz_comparison_session, monkeypatch):
    from color_measurement import application

    session = xyz_comparison_session
    state = session.snapshot()["batch"]

    def refuse_publication(*args):
        raise OSError("Synthetic comparison publication refusal")

    with monkeypatch.context() as patch:
        patch.setattr(application, "_publish_owned_file", refuse_publication)
        assert send(session, "set_comparison_context", dict(
            version=state["comparison"]["version"], reference=state["comparison"]["reference"],
            white=dict(state["comparison"]["reference"], kind="retained-reading"),
        ))["admission"] == "admitted"
        wait(session, lambda state: not state["comparison"]["saved"] and state["phase"] != "saving")
        with pytest.raises(ValueError, match="Save comparison context"):
            xyz_analysis(session)


@pytest.mark.parametrize(
    "method,params,reason",
    [
        ("bind", lambda data: {"extra": 1}, "Expected fields"),
        ("snapshot", lambda data: [], "Expected fields"),
        ("arbitrary", lambda data: {}, "Unknown desktop method"),
        ("projection", lambda data: {"scale": float("nan")}, "bounded JSON values"),
        ("annotation", lambda data: {"operation_id": "x" * 131073, "observation_id": ""},
         "131072 UTF-8 bytes"),
        *(
            ("projection", lambda data, scale=scale: dict(
                scale=scale, dataset_revision=data["dataset_revision"], comparison_version=0,
            ), "finite and positive")
            for scale in (0, -1.5, True)
        ),
    ],
)
def test_bridge_refuses_noncanonical_request_without_mutation(
    tmp_path, method, params, reason,
):
    session = DesktopSession(demo_frontends())
    create(session, tmp_path / "bounded", "Study", "")
    before, data = session.snapshot(), session.dataset()
    with pytest.raises(ValueError, match=reason):
        values = params(data)
        if method in {"projection", "analysis", "annotation"} and isinstance(values, dict):
            source = session.snapshot()["dataset_binding"]
            values = dict(binding_id=source["id"], generation=source["generation"]) | values
        session.request(method, values)
    assert session.snapshot() == before
    assert send(session, "finish")["admission"] == "admitted"


@pytest.mark.parametrize("admission", ["admitted", "refused"])
def test_native_exit_forwards_captured_confirmation_without_refresh_or_retry(admission):
    calls = []
    outcome = dict(admission=admission, state="completed" if admission == "admitted" else "refused")
    if admission == "admitted":
        outcome["effect"] = "process_exit_acknowledged"

    class CapturedOwner:
        def command(self, command_id, generation, action, payload, *, batch_id):
            calls.append((command_id, generation, action, payload, batch_id))
            return outcome

    session = DesktopSession([])
    session.batch = CapturedOwner()
    assert session.confirm_process_exit(
        batch_id="captured batch", generation=7, discard_token="confirmed contents",
    ) is outcome
    assert len(calls) == 1
    command_id, generation, action, payload, batch_id = calls[0]
    from uuid import UUID

    assert str(UUID(command_id)) == command_id
    assert (generation, action, payload, batch_id) == (
        7, "confirm_process_exit", {"discard_token": "confirmed contents"}, "captured batch",
    )


def test_native_exit_requires_a_batch_and_is_not_a_renderer_method():
    session = DesktopSession([])
    with pytest.raises(ValueError, match="Create a batch first"):
        session.confirm_process_exit(batch_id="none", generation=0, discard_token="none")
    session.batch = object()
    with pytest.raises(ValueError, match="Unknown desktop method"):
        session.request("confirm_process_exit", {})


@pytest.mark.parametrize("count", [40, 160])
def test_native_demo_preload_saves_many_real_owner_readings(tmp_path, monkeypatch, count):
    from test_application import _wait

    from color_measurement.instrument import Instrument

    monkeypatch.setattr(
        Instrument, "_open_session", lambda *args, **kwargs: pytest.fail("Native transport"),
    )
    session = DesktopSession(demo_frontends())
    state = create(session, tmp_path / f"dense-{count}", "Demo UI data", "")["batch"]
    existing = {}
    if count == 40:
        measure(session)
        existing = {item["path"]: (Path(state["folder"]) / item["path"]).read_bytes()
                    for item in session.snapshot()["batch"]["operations"]}
    result = session.preload_demo(batch_id=state["batch_id"], generation=state["generation"],
                                  count=count)
    assert result == dict(batch_id=state["batch_id"], requested=count, completed=count,
                         retained=count, state="completed", reason=None)
    data = session.dataset()
    assert len(data["readings"]) == count + len(existing)
    assert all(row["saved"] and row["observation"]["provenance"] == "simulated"
               and row["observation"]["backend_reported"]["xyz"] is not None
               and row["observation"]["backend_reported"]["spectrum"] is not None
               for row in data["readings"].values())
    # Finish publishes a final manifest and queues another whole-batch TI3
    # refresh. Use the existing bulk-test Close budget and compact public owner
    # snapshots here; ordinary command waits retain their eight-second bound.
    finish_id = str(uuid4())
    outcome = send(session, "finish", identity=finish_id)
    assert outcome["admission"] == "admitted", outcome

    def finish_settled(state):
        # The command can settle after this snapshot was captured. Return only
        # a snapshot whose own terminal and optional-I/O facts are settled too.
        outcome = session.batch.command_outcome(finish_id)
        return outcome["state"] in TERMINAL_COMMAND_STATES and (
            outcome["state"] != "completed" or state["status"] == "finished"
            and state["phase"] == "idle" and not state["companions"]["saving"]
        )

    final, _ = _wait(session.batch, finish_settled, timeout=30, details=False)
    outcome = session.batch.command_outcome(finish_id)
    assert outcome["state"] == "completed", outcome
    assert final["status"] == "finished" and final["phase"] == "idle"
    assert final["saved_count"] == final["sample_count"] == count + len(existing)
    assert final["save_reason"] is None and not final["companions"]["saving"]
    assert final["export_state"] == "current", final["export_reason"]
    for format, output in final["companions"]["formats"].items():
        if output["state"] == "unavailable":
            assert format in {"mat", "chart_selected_csv"} and output["reason"]
        else:
            assert output["current"], (format, output["state"], output["reason"])
            assert output["source_manifest_sha256"] == (
                final["companions"]["source_manifest_sha256"])
    reviewed = session.review(state["folder"])
    assert reviewed["sample_count"] == count + len(existing)
    assert all(item["review_state"] == "accepted" for item in reviewed["operations"])
    assert {path: (Path(state["folder"]) / path).read_bytes() for path in existing} == existing


@pytest.mark.parametrize("stop_kind", ["event", "rebind"])
def test_native_demo_preload_stop_cancels_its_admitted_operation(tmp_path, monkeypatch, stop_kind):
    from threading import Event

    session = DesktopSession(demo_frontends())
    state = create(session, tmp_path / "dense-stop", "Demo UI data", "")["batch"]
    stop = Event()
    command = session.batch.command
    measures = []

    def stop_after_admission(command_id, generation, action, payload, *, batch_id):
        outcome = command(command_id, generation, action, payload, batch_id=batch_id)
        if action == "measure":
            measures.append(command_id)
            if stop_kind == "event":
                stop.set()
            else:
                session.bind()
        return outcome

    monkeypatch.setattr(session.batch, "command", stop_after_admission)
    result = session.preload_demo(batch_id=state["batch_id"], generation=state["generation"],
                                  count=40, stop_event=stop)
    assert result["state"] == "stopped" and result["completed"] == 0
    assert result["reason"] == (
        "Demo loading stopped" if stop_kind == "event" else "Demo preload binding changed"
    )
    assert len(measures) == 1
    assert session.batch.command_outcome(measures[0])["result_status"] == "cancelled"
    assert session.snapshot()["batch"]["software_stopped"] is True
    assert session.snapshot()["batch"]["phase"] == "idle"
    completed_command(session, "finish")


def test_native_demo_preload_save_failure_keeps_reading_and_stops_without_retry(
    tmp_path, monkeypatch,
):
    from color_measurement import operation_exports

    session = DesktopSession(demo_frontends())
    state = create(session, tmp_path / "dense-save-failure", "Demo UI data", "")["batch"]
    def refuse_reading(*args, **kwargs):
        raise OSError("Controlled Demo reading save failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(operation_exports, "publish", refuse_reading)
        result = session.preload_demo(batch_id=state["batch_id"], generation=state["generation"],
                                      count=40)
    assert result["state"] == "failed" and result["completed"] == 0 and result["retained"] == 1
    assert "Controlled Demo reading save failure" in result["reason"]
    row, = session.dataset()["readings"].values()
    assert not row["saved"] and row["observation"]["provenance"] == "simulated"
    assert len(session.snapshot()["batch"]["operations"]) == 1
    completed_command(session, "retry_save")
    assert session.snapshot()["batch"]["sample_count"] == 1
    completed_command(session, "finish")


def test_native_demo_preload_fences_source_binding_count_and_renderer(tmp_path, monkeypatch):
    from color_measurement.demo import is_demo_frontend

    session = DesktopSession(demo_frontends())
    state = create(session, tmp_path / "dense-fences", "Demo UI data", "")["batch"]
    inputs = dict(batch_id=state["batch_id"], generation=state["generation"], count=40)
    before = session.snapshot()
    for altered, message in ((dict(count=True), "40 or 160"), (dict(count=41), "40 or 160"),
                             (dict(generation=0), "binding changed"),
                             (dict(generation=True), "binding changed"),
                             (dict(batch_id=str(uuid4())), "binding changed"),
                             (dict(stop_event=lambda: False), "threading.Event")):
        with pytest.raises(ValueError, match=message):
            session.preload_demo(**(inputs | altered))
    assert is_demo_frontend(session.batch._frontend)
    assert not is_demo_frontend(object())
    with monkeypatch.context() as scoped:
        # A simulated label alone cannot authorize automated native-worker I/O.
        scoped.setattr(session.batch, "_frontend", object())
        with pytest.raises(ValueError, match="packaged Demo"):
            session.preload_demo(**inputs)
    with pytest.raises(ValueError, match="Unknown desktop method"):
        session.request("preload_demo", inputs)
    assert session.snapshot() == before
    completed_command(session, "finish")


def test_native_exit_current_owner_refuses_stale_dialog_and_acknowledges_without_io(
    tmp_path, monkeypatch,
):
    from color_measurement import application, exports

    monkeypatch.setattr(exports, "_RETIRED_PUBLICATION_LOCKS", {})
    session = DesktopSession(demo_frontends())
    create(session, tmp_path / "native-exit", "Study", "")
    measure(session)
    settled(session)
    old = session.snapshot()["batch"]
    old_token = session.request("discard_summary", {})["token"]
    session.bind()
    lock = exports._folder_lock(Path(old["folder"]), ".ti3-export.lock")
    close = exports.os.close
    descriptor = None
    try:
        with pytest.raises(OSError, match="Controlled unclosed test descriptor"):
            with monkeypatch.context() as scoped:
                with lock:
                    descriptor = lock.descriptor

                    def refuse_close(value):
                        if value == descriptor:
                            raise OSError("Controlled unclosed test descriptor")
                        return close(value)

                    scoped.setattr(exports.os, "close", refuse_close)
        state = session.snapshot()["batch"]
        assert state["process_exit_ready"] and not state["process_exit_acknowledged"]
        token = session.request("discard_summary", {})["token"]
        assert token != old_token
        manifest = session.batch._manifest_bytes
        retained = tuple(exports._RETIRED_PUBLICATION_LOCKS.items())

        def forbidden(*args, **kwargs):
            pytest.fail("Native exit performed I/O, refreshed state or released a lock")

        with monkeypatch.context() as scoped:
            for member in ("_read_regular", "_publish_owned_file", "_folder_lock",
                           "_require_publication_cleanup"):
                scoped.setattr(application, member, forbidden)
            for member in ("snapshot", "_assert_folder", "_release_lock"):
                scoped.setattr(session.batch, member, forbidden)
            for member in ("close", "open", "stat", "lstat", "read", "write"):
                scoped.setattr(exports.os, member, forbidden)
            current = dict(batch_id=state["batch_id"], generation=state["generation"],
                           discard_token=token)
            for stale in (dict(current, batch_id=str(uuid4())),
                          dict(current, generation=old["generation"]),
                          dict(current, discard_token=old_token)):
                outcome = session.confirm_process_exit(**stale)
                assert outcome["admission"] == "refused"
                assert not session.batch._process_exit_acknowledged
            outcome = session.confirm_process_exit(**current)
            assert (outcome["admission"], outcome["state"], outcome["effect"]) == (
                "admitted", "completed", "process_exit_acknowledged",
            )
            assert session.batch._manifest_bytes == manifest
            assert tuple(exports._RETIRED_PUBLICATION_LOCKS.items()) == retained
            assert lock.descriptor == descriptor and not session.batch._closed
    finally:
        # The substituted failure never called close. This test owns the known
        # open descriptor; production must never retry an uncertain descriptor.
        if descriptor is not None:
            close(descriptor)
            lock.descriptor = None
            exports._RETIRED_PUBLICATION_LOCKS.pop(lock.path, None)
        session.batch._release_lock()
        session.batch._closed = True


def test_bridge_cannot_bypass_native_path_or_shutdown_dialog(tmp_path):
    session = DesktopSession(demo_frontends())
    create(session, tmp_path / "bounded", "Study", "")
    for action in ("another_folder", "continue_chart", "close", "abandon", "confirm_process_exit"):
        with pytest.raises(ValueError, match="native"):
            send(session, action)
    assert send(session, "finish")["admission"] == "admitted"


def test_ti3_export_keeps_authenticated_repeat_pair_in_row_provenance(tmp_path, monkeypatch):
    from color_measurement.cgats import read_cgats
    from color_measurement.instrument import Instrument

    monkeypatch.setattr(
        Instrument, "_open_session", lambda *args, **kwargs: pytest.fail("Native transport"),
    )
    session = DesktopSession(demo_frontends())
    create(session, tmp_path / "repeat-lineage", "Study", "")
    try:
        measure(session)
        first = next(iter(session.dataset()["readings"].values()))["observation"]
        source_pair = {key: first[key] for key in ("operation_id", "observation_id")}
        state = session.snapshot()["batch"]
        draft = state["draft"]
        assert send(session, "repeat_sample", dict(
            context_id=state["context_id"], draft_id=draft["id"], draft_version=draft["version"],
            **source_pair,
        ))["admission"] == "admitted"
        wait(session, lambda current: current["phase"] == "idle", respond=True)
        measure(session)
        before = session.snapshot()["batch"]
        folder = Path(before["folder"])
        native_bytes = {item["path"]: (folder / item["path"]).read_bytes()
                        for item in before["operations"]}
        completed_command(session, "export_ti3")
        state = session.snapshot()["batch"]
        current = [file for file in state["ti3"]["files"]
                   if file["state"] == "current" and file["scope"] == "ordinary"]
        assert len(current) == 1  # Repeat lineage does not change material grouping.
        file, = current
        parsed, = read_cgats(Path(file["path"]).read_text())
        contexts = [json.loads(row[parsed.index("CM_CONTEXT_JSON")])["provenance"]
                    for row in parsed.rows]
        manifest = json.loads((folder / "batch.json").read_bytes())
        expected = [(item["operation_id"], item["observation_id"])
                    for item in state["annotations"]]
        assert [(item["operation_id"], item["observation_id"]) for item in contexts] == expected
        assert [item["repeat_of"] for item in contexts] == [None, source_pair, None]
        assert manifest["operations"][1]["repeat_of"] == source_pair
        assert state["ti3"]["source_sha256"] == hashlib.sha256(
            (folder / "batch.json").read_bytes()
        ).hexdigest()
        assert {path: (folder / path).read_bytes() for path in native_bytes} == native_bytes
    finally:
        completed_command(session, "finish")


def test_bundled_illustrations_and_notices_bind_actual_source():
    assets = Path(presentation.__file__).with_name("desktop_assets")
    text = (assets / "reference-data.js").read_text()
    data = json.loads(text[len("const FIXTURES = ") : -2])
    for module in (science, presentation):
        assert (
            data["source_hashes"][module.__name__]
            == hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
        )
    assert data["chromaticity_field"]["relative_y_cap"] == 0.25
    assert data["readings"] == {}
    assert "CC BY-SA" in (assets / "resources/NOTICE.md").read_text()
    assert "MIT License" in (assets / "vendor/LICENSE").read_text()


def test_full_annotation_query_and_view_choices_survive_rebind(tmp_path):
    session = DesktopSession(demo_frontends())
    create(session, tmp_path / "view", "Study", "")
    measure(session)
    data = session.dataset()
    identity, row = next(iter(data["readings"].items()))
    pair = {field: row["observation"][field] for field in ("operation_id", "observation_id")}
    notes = "\n" + "Full Unicode note 🟦\n" * 80
    assert (
        send(
            session,
            "edit_annotation_draft",
            dict(pair, version=row["version"], draft_version=0, name="Long note", notes=notes),
        )["admission"]
        == "admitted"
    )
    assert (
        send(session, "annotate", dict(pair, version=row["version"], draft_version=1))["admission"]
        == "admitted"
    )
    wait(session, lambda state: state["phase"] == "idle")
    assert session.dataset()["readings"][identity]["notes_preview"] == notes[:256]
    assert "notes" not in session.dataset()["readings"][identity]
    assert query(session, "annotation", {"reading_ref": pair})["applied"]["notes"] == notes
    state = session.snapshot()["batch"]
    view = dict(
        plotSlots=["spectrum", "chromaticity"],
        plotted=[identity],
        previousPlotted=None,
        displayScale=123.456,
        follow=False,
        tool="data",
        inspector=True,
        controls={"coordinates": "xyy"},
        expanded=["chromaticityPanel"],
        zoom=1.5,
    )
    session.request(
        "set_view", dict(binding_id=session.snapshot()["dataset_binding"]["id"],
                         generation=state["generation"], view=view)
    )
    assert session.bind()["view"] == view
    with pytest.raises(ValueError, match="binding changed"):
        session.request(
            "set_view", dict(binding_id=session.snapshot()["dataset_binding"]["id"],
                         generation=state["generation"], view=view)
        )
    assert send(session, "finish")["admission"] == "admitted"


@pytest.mark.parametrize("missing", ["reference", "compared"])
def test_spectral_comparison_names_the_reading_that_lacks_a_spectrum(
    xyz_comparison_session, monkeypatch, missing,
):
    # Catches: a neutral "two Spectrum records" refusal that says nothing about
    # which side is absent, which the earlier test could not tell apart.
    session = xyz_comparison_session
    before = session.dataset()
    reference = source_key(before, session.snapshot()["batch"]["comparison"]["reference"])
    compared = next(key for key in before["readings"] if key != reference)
    target = before["readings"][reference if missing == "reference" else compared]["observation"]
    details = session.batch.reading_details

    def without_spectrum(operation_id, observation_id):
        record = details(operation_id, observation_id)
        if (operation_id, observation_id) == (target["operation_id"], target["observation_id"]):
            record["backend_reported"]["spectrum"] = None
        return record

    monkeypatch.setattr(session.batch, "reading_details", without_spectrum)
    session._binding._observations.clear()
    session._binding._dataset_key = None
    result = xyz_analysis(session)
    comparison = result["comparison_by_source"][compared]
    assert comparison["rms"] is None
    assert comparison["reason"] == f"The {missing} reading reported no native spectrum"
    assert result["summary"]["spectral_unavailable"] == 1


def test_dataset_names_the_preview_scale_it_encoded(xyz_comparison_session):
    # Catches: the renderer restating 100 while the dataset encodes previews at
    # a scale it never names.
    session = xyz_comparison_session
    empty = DesktopSession(demo_frontends()).dataset()
    assert empty["preview_scale"] == presentation.DEFAULT_PREVIEW_SCALE == 100
    data = session.dataset()
    assert data["preview_scale"] == presentation.DEFAULT_PREVIEW_SCALE
    row = next(iter(data["readings"].values()))
    assert all(
        preview["reference_luminance"] == data["preview_scale"]
        for preview in row["display_previews"].values()
        if preview["display_rgb"] is not None
    )


@pytest.mark.parametrize("recipe", [dict(kind="classic"), dict(kind="custom", name="Grid", rows=2,
                                        columns=3)])
def test_native_chart_recipe_uses_bounded_token_and_exact_command_replay(tmp_path, recipe):
    session = DesktopSession(demo_frontends())
    create(session, tmp_path / "chart-recipe", "Chart recipe", "")
    state = session.snapshot()["batch"]
    fence = {key: state[key] for key in ("batch_id", "generation", "context_id")}
    packet = session.request("stage_chart", dict(fence, recipe=recipe))
    assert packet["mapping_confirmation"] is None
    assert "target_json" not in packet["preview"] and "definition" not in packet
    command = str(uuid4())
    params = dict(command_id=command, generation=fence["generation"], batch_id=fence["batch_id"],
                  action="configure_chart", payload=dict(
                      definition_token=packet["definition_token"], mapping_confirmation=None))
    first = session.request("command", params)
    assert first["admission"] == "admitted"
    wait(session, lambda current: current["phase"] == "idle")
    assert session.request("command", params)["effect"] == "configure_chart"
    expected = 24 if recipe["kind"] == "classic" else 6
    assert session.snapshot()["batch"]["chart"]["total"] == expected
    with pytest.raises(ValueError, match="native TI2 chooser"):
        session.request("stage_chart", dict(fence, recipe=dict(kind="ti2")))


def test_native_ti2_staging_keeps_full_bytes_local_and_requires_exact_mapping(tmp_path):
    session = DesktopSession(demo_frontends())
    create(session, tmp_path / "native-ti2", "TI2 import", "")
    fixture = Path(__file__).parent / "fixtures/targets/asymmetric-six-patch.ti2"
    original = fixture.read_bytes()
    path = tmp_path / "target.ti2"
    path.write_bytes(original)
    state = session.snapshot()["batch"]
    fence = {key: state[key] for key in ("batch_id", "generation", "context_id")}
    packet = session.stage_chart_target(path, strips_as="columns", **fence)
    assert packet["source"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert packet["source"]["byte_count"] == len(original)
    assert (packet["preview"]["rows"], packet["preview"]["columns"]) == (3, 2)
    assert "source_base64" not in json.dumps(packet) and "target_json" not in json.dumps(packet)
    bad = send(session, "configure_chart", dict(
        definition_token=packet["definition_token"], mapping_confirmation=True))
    assert bad["admission"] == "refused" and session.batch.snapshot()["chart"] is None
    good = send(session, "configure_chart", dict(
        definition_token=packet["definition_token"],
        mapping_confirmation=packet["mapping_confirmation"]))
    assert good["admission"] == "admitted"
    wait(session, lambda current: current["phase"] == "idle")
    assert session.batch.snapshot()["chart"]["total"] == 6
