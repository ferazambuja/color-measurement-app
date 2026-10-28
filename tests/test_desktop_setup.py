"""Deliberate setup through real InstrumentBuild policy over inert/recording clients."""

import json
import platform
from pathlib import Path
from uuid import uuid4

import pytest
from conftest import write_stub_receipt
from test_desktop_session import completed_command, create, measure, send, settled, wait
from test_discovery import CATALOG, _build, _candidates, _report, current_cr_identity

from color_measurement import argyll_description
from color_measurement.argyll_capability import baseline_mode_words, mode_word
from color_measurement.demo import demo_frontends
from color_measurement.desktop_session import DesktopSession
from color_measurement.discovery import InstrumentBuild
from color_measurement.instrument import Instrument
from color_measurement.native_receipt import native_worker_name


@pytest.fixture(autouse=True)
def no_transport(monkeypatch):
    monkeypatch.setattr(
        Instrument, "_open_session", lambda *a, **k: pytest.fail("Native transport"),
    )
    monkeypatch.setattr(
        argyll_description, "HelperSession",
        lambda *a, **k: pytest.fail("Native description worker"),
    )


def setup(session):
    return session.request("equipment_setup", {})


def refresh(session, *, command_id=None):
    return session.request("refresh_devices", dict(
        command_id=command_id or str(uuid4()), setup_generation=setup(session)["generation"],
    ))


def connect_params(session, **changes):
    state = setup(session)
    return dict(
        command_id=str(uuid4()), setup_generation=state["generation"],
        listing_generation=state["listing_generation"],
        candidate_id=state["candidates"][0]["candidate_id"], expect=None, expected_serial=None,
        allow_serial_identification=False, accept_disclosed_initialization=False,
        accept_unassessed_initialization=False, timeout=300,
    ) | changes


def recorded_setup(tmp_path, monkeypatch, candidates=None, *, setup_executor=None, **kwargs):
    build, client = _build(candidates or _candidates(("usb:/recording-only", 21, 0x02, None)),
                           **kwargs)
    client._describe["provenance"] = "simulated"
    monkeypatch.setattr(InstrumentBuild, "from_report", classmethod(lambda cls, path: build))
    session = DesktopSession([], setup_executor=setup_executor)
    path = tmp_path / "synthetic-report.json"
    path.write_text('{"software_only": true}', encoding="utf-8")
    session.select_build_report(path, expected_setup_generation=0)
    return session, client, path


def connected_recording(tmp_path, monkeypatch):
    session, client, path = recorded_setup(tmp_path, monkeypatch)
    refresh(session)
    connected = session.request("connect_device", connect_params(session))
    assert connected["state"] == "completed"
    return session, client, path, connected["result"]


def binding_params(session, binding):
    return dict(command_id=str(uuid4()), setup_generation=setup(session)["generation"],
                binding_id=binding["binding_id"], binding_generation=binding["generation"])


def reopen_params(session, binding):
    chosen = connect_params(session)
    return binding_params(session, binding) | {key: value for key, value in chosen.items()
                                              if key not in {"command_id", "expect",
                                                             "expected_serial"}}


def test_close_and_selected_reopen_preserve_reported_identity_across_paths(tmp_path, monkeypatch):
    session, client, _, connected = connected_recording(tmp_path, monkeypatch)
    original = setup(session)["bindings"][0]
    command = binding_params(session, original)
    before = session.snapshot()
    closed = session.request("close_device", command)
    assert closed["state"] == "completed" and closed["result"]["software_selection_retired"]
    assert closed["result"]["instrument_state"] == "unknown"
    assert session.request("close_device", command) == closed and len(client.describes) == 1
    assert session.settings(None)["selections"] == [] and setup(session)["candidates"] == []
    assert session.snapshot() == before
    client._candidates = _candidates(("usb:/different-port", 21, 0x02, None))
    refresh(session)
    retired = setup(session)["bindings"][0]
    request = reopen_params(session, retired)
    reopened = session.request("reopen_device", request)
    assert reopened["state"] == "completed" and len(client.describes) == 2
    assert client.describes[-1][1]["expected_serial"] == original["serial"]
    current = reopened["result"]
    assert current["descriptor"]["device_id"] == connected["descriptor"]["device_id"]
    assert current["connection"]["path"] == "usb:/different-port"
    assert current["binding_id"] == original["binding_id"] and current["binding_generation"] == 3
    assert current["selection_id"] != connected["selection_id"]
    assert session.request("reopen_device", request) == reopened and len(client.describes) == 2
    stale = session.request("close_device", dict(command, command_id=str(uuid4())))
    assert stale["state"] == "failed" and "binding changed" in stale["reason"]
    assert len(session.settings(None)["selections"]) == 1


@pytest.mark.parametrize("failure", ["serial", "bind", "register"])
def test_failed_selected_replacement_retains_previous_binding_without_retry(
    tmp_path, monkeypatch, failure,
):
    session, client, _, connected = connected_recording(tmp_path, monkeypatch)
    original = setup(session)["bindings"][0]
    old_settings = session.settings(connected["selection_id"])
    before = session.snapshot()
    client._candidates = _candidates(("usb:/replacement-port", 21, 0x02, None))
    refresh(session)
    request = reopen_params(session, original)
    if failure == "serial":
        client._describe["serial"] = "Other unit"
    else:
        def refuse(*args, **kwargs):
            raise ValueError("Deliberate replacement refusal")

        owner = session._equipment_setup._build if failure == "bind" else session
        monkeypatch.setattr(owner, "bind" if failure == "bind" else "register_frontend", refuse)
    failed = session.request("reopen_device", request)
    assert failed["state"] == "failed" and len(client.describes) == 2
    assert session.request("reopen_device", request) == failed and len(client.describes) == 2
    assert session.settings(connected["selection_id"]) == old_settings
    assert session.snapshot() == before and setup(session)["bindings"] == [original]
    repeated = session.request("reopen_device", dict(request, command_id=str(uuid4())))
    assert repeated["state"] == "failed" and "consumed" in repeated["reason"]
    assert len(client.describes) == 2


def test_retirement_and_replacement_require_finish_and_preserve_retained_batch(
    tmp_path, monkeypatch,
):
    session, client, _ = recorded_setup(tmp_path, monkeypatch)
    primary = mode_word("emissive_spot", spectral=False)
    client._describe["capabilities"]["primary"] = primary
    client._describe["baseline_mode_checks"] = dict(
        scope="initialized_before_mode_configuration", mode_capabilities=primary,
        checks=[dict(mode_word=word, native_code=0) for word in baseline_mode_words(primary)],
    )
    refresh(session)
    connected = session.request("connect_device", connect_params(session))["result"]
    mode = next(row["mode"] for row in connected["descriptor"]["modes"]
                if row["available"] and "spot" in row["mode"])
    session.create(tmp_path / "retained", "Retained context", "Original notes",
                   selection_id=connected["selection_id"], mode=mode, options={})
    original = setup(session)["bindings"][0]
    before, data = session.snapshot(), session.dataset()
    closed = session.request("close_device", binding_params(session, original))
    assert closed["state"] == "failed" and "Finish" in closed["reason"]
    client._candidates = _candidates(("usb:/later-port", 21, 0x02, None))
    refresh(session)
    refused = session.request("reopen_device", reopen_params(session, original))
    assert refused["state"] == "failed" and "Finish" in refused["reason"]
    assert len(client.describes) == 1 and session.snapshot() == before and session.dataset() == data
    completed_command(session, "finish")
    saved, data = session.snapshot(), session.dataset()
    reopened = session.request("reopen_device", reopen_params(session, original))
    assert reopened["state"] == "completed" and len(client.describes) == 2
    assert session.snapshot() == saved and session.dataset() == data
    with pytest.raises(ValueError, match="stale frontend selection"):
        session.settings(connected["selection_id"])


def test_missing_reported_serial_cannot_claim_reopened_continuity(tmp_path, monkeypatch):
    session, client, _ = recorded_setup(tmp_path, monkeypatch)
    client._describe["serial"] = None
    refresh(session)
    connected = session.request("connect_device", connect_params(session))
    assert connected["state"] == "completed"
    original = setup(session)["bindings"][0]
    refresh(session)
    refused = session.request("reopen_device", reopen_params(session, original))
    assert refused["state"] == "failed" and "original reported serial" in refused["reason"]
    assert len(client.describes) == 1 and setup(session)["bindings"] == [original]


@pytest.mark.parametrize("action", ["connect", "reopen", "close"])
@pytest.mark.parametrize("failure", [ValueError, KeyboardInterrupt, SystemExit])
def test_post_registry_mutation_failure_rolls_back_coherently_and_never_reinitializes(
    tmp_path, monkeypatch, action, failure,
):
    if action == "connect":
        session, client, _ = recorded_setup(tmp_path, monkeypatch)
        refresh(session)
        params = connect_params(session)
    else:
        session, client, _, _ = connected_recording(tmp_path, monkeypatch)
        original = setup(session)["bindings"][0]
        refresh(session)
        params = (binding_params(session, original) if action == "close"
                  else reopen_params(session, original))
    demo = next(front for front in demo_frontends()
                if json.loads(front.describe_json())["device_id"] == "demo:i1pro3")
    session.register_frontend(demo)
    create(session, tmp_path / "finished-demo", "Retained readings", "Original notes")
    measure(session)
    completed_command(session, "finish")
    before, data, bindings = session.snapshot(), session.dataset(), setup(session)["bindings"]
    settings = session.settings(None)
    attribute = "_retire_frontend" if action == "close" else "register_frontend"
    real = getattr(session, attribute)
    signal = failure("Original post-mutation failure")
    calls = []

    def mutate_then_fail(*args, **kwargs):
        calls.append(real(*args, **kwargs))
        raise signal

    monkeypatch.setattr(session, attribute, mutate_then_fail)
    method = {"connect": "connect_device", "reopen": "reopen_device",
              "close": "close_device"}[action]
    initializations = len(client.describes)
    if failure is ValueError:
        result = session.request(method, params)
        assert result["state"] == "failed" and result["reason"] == str(signal)
    else:
        with pytest.raises(failure) as caught:
            session.request(method, params)
        assert caught.value is signal
        result = session.request(method, params)
        assert result["state"] == "interrupted" and "unconfirmed" in result["reason"]
    assert len(calls) == 1
    assert len(client.describes) == initializations + (action != "close")
    assert session.settings(None) == settings and setup(session)["bindings"] == bindings
    assert session.snapshot() == before and session.dataset() == data
    assert session.request(method, params) == result and len(calls) == 1
    assert len(client.describes) == initializations + (action != "close")


def test_real_report_authentication_and_cached_snapshot_start_no_worker(tmp_path):
    path = _report(tmp_path, override={"driver_manifest": json.loads(CATALOG)})
    session = DesktopSession([])
    assert setup(session)["state"] == "unselected"
    selected = session.select_build_report(path, expected_setup_generation=0)
    assert selected["generation"] == 1 and selected["state"] == "ready"
    assert selected["catalog"]["instrument_types"] and selected["candidates"] == []
    selected["catalog"]["instrument_types"].clear()
    assert setup(session)["catalog"]["instrument_types"]
    path.with_name(native_worker_name("generic-worker-never-run")).write_bytes(b"Tampered worker")
    with pytest.raises(ValueError, match="does not match"):
        session.select_build_report(path, expected_setup_generation=1)
    assert setup(session)["generation"] == 1
    with pytest.raises(ValueError, match="generation"):
        session.select_build_report(path, expected_setup_generation=0)
    with pytest.raises(ValueError):
        session.request("select_build_report", {"path": str(path)})


def simulation_report(folder):
    """Original section shapes with inert bytes, never executable substitutes."""
    path = _report(folder, override={"driver_manifest": json.loads(CATALOG)})
    report = json.loads(path.read_bytes())
    report.update(description_workflow=dict(status="passed"), generic_session=dict(status="passed"))
    path.write_text(json.dumps(report), encoding="utf-8")
    description = folder / native_worker_name("description-stub-worker")
    session = folder / native_worker_name("generic-session-stub-worker")
    description.write_bytes(b"Inert substituted description")
    session.write_bytes(b"Inert substituted acquisition")
    write_stub_receipt(description, session)
    return path


@pytest.mark.parametrize("system", ["Darwin", "Linux", "Windows"])
def test_explicit_simulation_report_uses_original_receipt_fields_without_starting_workers(
    tmp_path, monkeypatch, system,
):
    import hashlib

    monkeypatch.setattr(platform, "system", lambda: system)
    path = simulation_report(tmp_path)
    original = path.read_bytes()
    source = Path(__file__).resolve().parents[1]
    session = DesktopSession([])
    selected = session.select_build_report(path, expected_setup_generation=0, simulated=True,
                                          source_root=source, scenario="unknown_serial")
    context = selected["report"]["context"]
    assert context["kind"] == "authenticated-substituted-workers"
    assert context["provenance"] == "simulated"
    assert context["description_fixture"] == "unknown_serial"
    assert context["source_root"] == str(source) and len(context["limitations"]) == 3
    build = session._equipment_setup._build
    suffix = ".exe" if system == "Windows" else ""
    assert build._helper.name == "generic-session-stub-worker" + suffix
    assert build._client._helper.name == "description-stub-worker" + suffix
    assert build._client._report_sha256 == hashlib.sha256(original).hexdigest()
    assert json.loads(build.catalog_json())["instrument_types"] == json.loads(CATALOG)[
        "instrument_types"]
    assert selected["candidates"] == [] and path.read_bytes() == original
    physical = InstrumentBuild.from_report(path)
    assert physical._helper.name == native_worker_name("generic-worker-never-run")
    assert physical.binding_context() == dict(kind="production-workers", provenance="physical")
    assert build._client._build_report["generic_worker_build_link"]["executed"] is False


@pytest.mark.parametrize("simulated", [False, True])
@pytest.mark.parametrize("system", [None, "other", True, [], {}, "foreign"])
def test_wrong_build_system_refuses_before_execution_and_preserves_setup(
    tmp_path, simulated, system,
):
    path = simulation_report(tmp_path)
    session = DesktopSession([])
    session.select_build_report(path, expected_setup_generation=0)
    before = setup(session)
    report = json.loads(path.read_bytes())
    if system is None:
        del report["build_system"]
    else:
        report["build_system"] = (
            "Linux" if platform.system() != "Linux" else "Windows"
        ) if system == "foreign" else system
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="build_system"):
        session.select_build_report(
            path, expected_setup_generation=1, simulated=simulated,
            **(dict(source_root=Path(__file__).resolve().parents[1]) if simulated else {}),
        )
    assert setup(session) == before


@pytest.mark.parametrize("system", ["Darwin", "Linux", "Windows"])
@pytest.mark.parametrize("fault", [None, "missing", "bytes", "digest", "source", "unused_generic"])
def test_chart_simulation_uses_its_original_authenticated_worker(
    tmp_path, monkeypatch, system, fault,
):
    monkeypatch.setattr(platform, "system", lambda: system)
    path = simulation_report(tmp_path)
    chart = tmp_path / native_worker_name("generic-chart-stub-worker")
    chart.write_bytes(b"Inert chart substitute")
    write_stub_receipt(chart)
    session = DesktopSession([])
    session.select_build_report(path, expected_setup_generation=0)
    original = setup(session)
    if fault == "missing":
        chart.unlink()
    elif fault == "bytes":
        chart.write_bytes(b"Changed chart substitute")
    elif fault == "digest":
        report = json.loads(path.read_bytes())
        report["generic_session"]["chart_executable_sha256"] = report[
            "generic_session"]["executable_sha256"]
        path.write_text(json.dumps(report))
    elif fault == "source":
        report = json.loads(path.read_bytes())
        report["original_check_source_sha256"]["tools/native_argyll/chart_stub.c"] = "0" * 64
        path.write_text(json.dumps(report))
    elif fault == "unused_generic":
        path.with_name(native_worker_name("generic-session-stub-worker")).write_bytes(
            b"Unselected inert substitute")
    kwargs = dict(expected_setup_generation=1, simulated=True,
                  source_root=Path(__file__).resolve().parents[1], simulated_session="chart")
    if fault not in {None, "unused_generic"}:
        with pytest.raises((ValueError, RuntimeError)):
            session.select_build_report(path, **kwargs)
        assert setup(session) == original
        return
    selected = session.select_build_report(path, **kwargs)
    assert selected["report"]["context"]["session_worker"] == chart.name
    assert session._equipment_setup._build._helper == chart
    assert selected["candidates"] == [] and selected["bindings"] == []


@pytest.mark.parametrize("system", ["Darwin", "Linux", "Windows"])
@pytest.mark.parametrize("worker,simulated,session_kind", [
    ("argyll-description-worker-never-run", False, "generic"),
    ("generic-worker-never-run", False, "generic"),
    ("description-stub-worker", True, "generic"),
    ("generic-session-stub-worker", True, "generic"),
    ("generic-chart-stub-worker", True, "chart"),
])
def test_worker_basename_has_no_cross_platform_fallback(
    tmp_path, monkeypatch, system, worker, simulated, session_kind,
):
    monkeypatch.setattr(platform, "system", lambda: system)
    path = simulation_report(tmp_path)
    chart = tmp_path / native_worker_name("generic-chart-stub-worker")
    chart.write_bytes(b"Inert chart substitute")
    write_stub_receipt(chart)
    session = DesktopSession([])
    session.select_build_report(path, expected_setup_generation=0)
    before = setup(session)
    expected = tmp_path / (worker + (".exe" if system == "Windows" else ""))
    wrong = tmp_path / (worker + ("" if system == "Windows" else ".exe"))
    expected.rename(wrong)
    with pytest.raises((OSError, ValueError, RuntimeError)):
        session.select_build_report(
            path, expected_setup_generation=1, simulated=simulated,
            simulated_session=session_kind,
            **(dict(source_root=Path(__file__).resolve().parents[1]) if simulated else {}),
        )
    assert setup(session) == before


@pytest.mark.parametrize("fault,match", [
    ("description", "digest"), ("session", "digest"), ("missing_session", "authenticate"),
    ("failed_section", "passed generic worker"), ("missing_inputs", "native source"),
    ("catalog", "catalog"), ("production_fields_only", "passed generic worker"),
])
def test_simulation_authentication_failure_preserves_previous_setup(tmp_path, fault, match):
    path = simulation_report(tmp_path)
    source = Path(__file__).resolve().parents[1]
    session = DesktopSession([])
    session.select_build_report(path, expected_setup_generation=0)
    before = setup(session)
    report = json.loads(path.read_bytes())
    if fault in {"description", "session"}:
        filename = ("description-stub-worker" if fault == "description"
                    else "generic-session-stub-worker")
        (tmp_path / native_worker_name(filename)).write_bytes(b"Changed inert bytes")
    elif fault == "missing_session":
        (tmp_path / native_worker_name("generic-session-stub-worker")).unlink()
    elif fault == "failed_section":
        report["generic_session"]["status"] = "failed"
    elif fault == "missing_inputs":
        del report["original_check_source_sha256"]["tools/native_argyll/session.c"]
    elif fault == "catalog":
        report["driver_manifest"]["archive_sha256"] = "0" * 64
    else:
        del report["generic_session"]
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError), match=match):
        session.select_build_report(path, expected_setup_generation=1, simulated=True,
                                    source_root=source)
    assert setup(session) == before


@pytest.mark.parametrize("changes", [dict(simulated=True), dict(simulated=1),
                                     dict(source_root="/not/a/checkout"),
                                     dict(scenario="unknown_serial"),
                                     dict(simulated_session="chart"),
                                     dict(simulated_session="other"),
                                     dict(simulated_session=[]),
                                     dict(simulated_session=True)])
def test_simulation_is_explicit_and_requires_source_checkout(tmp_path, changes):
    path = simulation_report(tmp_path)
    session = DesktopSession([])
    with pytest.raises(ValueError):
        session.select_build_report(path, expected_setup_generation=0, **changes)
    assert setup(session)["state"] == "unselected"


def test_scoped_native_receipt_never_becomes_a_production_binding(tmp_path):
    path = simulation_report(tmp_path)
    report = json.loads(path.read_bytes())
    report["check_scope"] = "substituted_binding"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="explicit simulation"):
        InstrumentBuild.from_report(path)
    build = InstrumentBuild.from_report(path, simulated=True,
                                        source_root=Path(__file__).resolve().parents[1])
    assert build.binding_context()["provenance"] == "simulated"


def test_deliberate_listing_selected_binding_and_reconciliation_do_not_open_twice(
    tmp_path, monkeypatch,
):
    session, client, _ = recorded_setup(tmp_path, monkeypatch)
    assert client.enumerations == 0 and client.describes == []
    identity = str(uuid4())
    listed = refresh(session, command_id=identity)
    assert listed["state"] == "completed" and client.enumerations == 1
    assert refresh(session, command_id=identity) == listed and client.enumerations == 1
    params = connect_params(session)
    before = session.snapshot()
    result = session.request("connect_device", params)
    assert result["state"] == "completed" and len(client.describes) == 1
    token = result["result"]["selection_id"]
    settings = session.settings(token)
    assert settings["descriptor"] == result["result"]["descriptor"]
    assert settings["descriptor"]["provenance"] == "simulated"
    assert session.snapshot() == before and session.batch is None
    assert session.request("connect_device", params) == result and len(client.describes) == 1
    with pytest.raises(ValueError, match="different content"):
        session.request("connect_device", dict(params, timeout=400))
    refused = session.request("connect_device", dict(params, command_id=str(uuid4())))
    assert "consumed" in refused["reason"] and len(client.describes) == 1
    refresh(session)
    refused = session.request("connect_device", connect_params(session))
    assert "already" in refused["reason"] and len(client.describes) == 1


@pytest.mark.parametrize("action", ["refresh_devices", "connect_device", "reopen_device"])
@pytest.mark.parametrize("cancel", [False, True])
def test_shared_setup_keeps_status_cancel_and_source_fences_responsive_until_native_ending(
    tmp_path, monkeypatch, action, cancel,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    entered, release = Event(), Event()
    terminal = dict(event="terminal", software_stopped=True, instrument_state="unknown",
                    initialization_performed=action != "refresh_devices",
                    cleanup="destructor_returned" if action != "refresh_devices"
                    else "not_constructed")
    with ThreadPoolExecutor(max_workers=1) as equipment, ThreadPoolExecutor(max_workers=1) as owner:
        session, client, path = recorded_setup(tmp_path, monkeypatch, setup_executor=equipment)
        if action != "refresh_devices":
            refresh(session)
            assert session._equipment_setup._future.result(timeout=2)["state"] == "completed"
        if action == "reopen_device":
            session.request("connect_device", connect_params(session))
            assert session._equipment_setup._future.result(timeout=2)["state"] == "completed"
            refresh(session)
            assert session._equipment_setup._future.result(timeout=2)["state"] == "completed"
        before = session.settings(None)
        method = "list_candidates_json" if action == "refresh_devices" else "describe_json"
        original = getattr(client, method)
        calls = []

        def blocked(*args, **kwargs):
            calls.append((args, kwargs))
            client.last_events = ()
            entered.set()
            assert release.wait(4)
            reply = original(*args, **kwargs)
            client.last_events = (deepcopy(terminal),)
            return reply

        from copy import deepcopy

        monkeypatch.setattr(client, method, blocked)
        params = (dict(command_id=str(uuid4()), setup_generation=setup(session)["generation"])
                  if action == "refresh_devices" else
                  reopen_params(session, setup(session)["bindings"][0])
                  if action == "reopen_device" else connect_params(session))
        try:
            pending = owner.submit(session.request, action, params).result(timeout=1)
            assert pending["state"] == "pending" and entered.wait(1)
            future = session._equipment_setup._future
            owner.submit(session.request, "snapshot", {}).result(timeout=1)
            current = owner.submit(setup, session).result(timeout=1)
            active = current["active_setup"]
            assert active["command_id"] == params["command_id"] and active["backend"] == "argyll"
            assert active["action"] == action
            assert active["cancellation_mode"] == "after_initialization"
            replay = owner.submit(session.request, action, params).result(timeout=1)
            assert replay["state"] == "pending" and len(calls) == 1
            with pytest.raises(ValueError, match="setup is running"):
                owner.submit(session.select_build_report, path,
                             expected_setup_generation=current["generation"]).result(timeout=1)
            with pytest.raises(ValueError, match="setup is running"):
                owner.submit(session.close_direct_sources).result(timeout=1)
            if cancel:
                stopped = owner.submit(session.request, "cancel_setup", dict(
                    command_id=params["command_id"],
                )).result(timeout=1)
                assert stopped["state"] == "pending" and stopped["cancel_requested"] is True
                assert setup(session)["active_setup"]["cancel_requested"] and not future.done()
                assert session.settings(None) == before
        finally:
            release.set()
        result = future.result(timeout=2)
        assert result["state"] == ("failed" if cancel else "completed")
        assert result["native_events"] == [terminal] and len(calls) == 1
        assert setup(session)["active_setup"] is None
        if cancel:
            assert "unconfirmed" in result["reason"] and session.settings(None) == before
            if action == "refresh_devices":
                assert setup(session)["candidates"] == []
        elif action != "refresh_devices":
            assert len(setup(session)["bindings"]) == 1
        else:
            assert result["result"]["active_setup"] is None
        assert session.request("equipment_outcome", dict(command_id=params["command_id"])) == result


def test_shared_setup_cancellation_before_worker_start_does_not_initialize(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    occupied, release = Event(), Event()
    session, client, _ = recorded_setup(tmp_path, monkeypatch)
    refresh(session)
    params = connect_params(session)

    def occupy():
        occupied.set()
        assert release.wait(3)

    with ThreadPoolExecutor(max_workers=1) as equipment:
        blocker = equipment.submit(occupy)
        assert occupied.wait(1)
        session._equipment_setup._executor = equipment
        try:
            assert session.request("connect_device", params)["state"] == "pending"
            result = session.request("cancel_setup", dict(command_id=params["command_id"]))
            assert result["state"] == "failed" and "no instrument was opened" in result["reason"]
            assert setup(session)["active_setup"] is None and client.describes == []
            assert session.settings(None)["selections"] == []
        finally:
            release.set()
            blocker.result(timeout=2)


@pytest.mark.parametrize("backend", ["argyll", "direct_serial"])
def test_setup_cancellation_after_registration_commit_cannot_claim_publication_was_prevented(
    tmp_path, monkeypatch, backend,
):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event

    from test_desktop_direct import ready_port
    from test_serial_transport import Clock

    committed, release = Event(), Event()
    with ThreadPoolExecutor(max_workers=1) as worker:
        if backend == "argyll":
            session, _, _ = recorded_setup(tmp_path, monkeypatch, setup_executor=worker)
            refresh(session)
            session._equipment_setup._future.result(timeout=2)
            params = connect_params(session)
        else:
            session = DesktopSession([])
            params = dict(command_id=str(uuid4()), setup_generation=0, model="CR-100",
                          expected_serial=None, accept_disclosed_initialization=True,
                          response_timeout=2, measurement_timeout=300)
        transaction = session._equipment_registration_transaction

        @contextmanager
        def held_commit():
            with transaction():
                yield
            committed.set()
            assert release.wait(4)

        monkeypatch.setattr(session, "_equipment_registration_transaction", held_commit)
        try:
            pending = (session.request("connect_device", params) if backend == "argyll" else
                       session.connect_direct_serial(
                           "scripted-port", transport=ready_port(), clock=Clock(),
                           executor=worker, **params))
            assert pending["state"] == "pending" and committed.wait(2)
            future = session._equipment_setup._future
            assert len(session.settings(None)["selections"]) == 1
            with pytest.raises(ValueError, match="registration has completed.*too late"):
                session.request("cancel_setup", dict(command_id=params["command_id"]))
            assert setup(session)["active_setup"]["cancel_requested"] is False
        finally:
            release.set()
        result = future.result(timeout=2)
        assert result["state"] == "completed" and not result.get("cancel_requested", False)
        assert len(session.settings(None)["selections"]) == 1


def test_listing_cancellation_after_publication_is_refused_as_too_late(tmp_path, monkeypatch):
    """Accepting it would end `completed` with `cancel_requested` and the listing shown."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread

    published, release = Event(), Event()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="equipment") as worker:
        session, _, _ = recorded_setup(tmp_path, monkeypatch, setup_executor=worker)
        owner = session._equipment_setup
        snapshot = owner.snapshot

        def held_after_publication():
            # The runner's own snapshot follows publication, before its lease retires.
            if current_thread().name.startswith("equipment"):
                published.set()
                assert release.wait(4)
            return snapshot()

        monkeypatch.setattr(owner, "snapshot", held_after_publication)
        params = dict(command_id=str(uuid4()), setup_generation=setup(session)["generation"])
        try:
            assert session.request("refresh_devices", params)["state"] == "pending"
            assert published.wait(2) and len(setup(session)["candidates"]) == 1
            with pytest.raises(ValueError, match="listing has been published.*too late"):
                session.request("cancel_setup", dict(command_id=params["command_id"]))
            assert setup(session)["active_setup"]["cancel_requested"] is False
        finally:
            release.set()
        result = owner._future.result(timeout=2)
    assert result["state"] == "completed" and not result.get("cancel_requested", False)
    assert len(setup(session)["candidates"]) == 1


def test_cancellation_during_binding_is_caught_inside_registration(tmp_path, monkeypatch):
    """After the post-description check, only the registration check stops the source."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    binding, release = Event(), Event()
    with ThreadPoolExecutor(max_workers=1) as worker:
        session, _, _ = recorded_setup(tmp_path, monkeypatch, setup_executor=worker)
        refresh(session)
        session._equipment_setup._future.result(timeout=2)
        build = session._equipment_setup._build
        bind = build.bind

        def held_bind(*args, **kwargs):
            binding.set()
            assert release.wait(4)
            return bind(*args, **kwargs)

        monkeypatch.setattr(build, "bind", held_bind)
        params = connect_params(session)
        try:
            assert session.request("connect_device", params)["state"] == "pending"
            assert binding.wait(2) and setup(session)["active_setup"]["stage"] == "binding"
            cancelled = session.request("cancel_setup", dict(command_id=params["command_id"]))
            assert cancelled["state"] == "pending" and cancelled["cancel_requested"] is True
        finally:
            release.set()
        result = session._equipment_setup._future.result(timeout=2)
    assert result["state"] == "failed" and "no source was registered" in result["reason"]
    assert session.settings(None)["selections"] == [] and setup(session)["bindings"] == []


@pytest.mark.parametrize("changes", [dict(setup_generation=0), dict(listing_generation=0),
                                     dict(candidate_id="foreign"), dict(timeout=True),
                                     dict(accept_unassessed_initialization=1)])
def test_stale_or_malformed_setup_never_describes(tmp_path, monkeypatch, changes):
    session, client, _ = recorded_setup(tmp_path, monkeypatch)
    refresh(session)
    params = connect_params(session, **changes)
    if "setup_generation" in changes:
        with pytest.raises(ValueError, match="generation"):
            session.request("connect_device", params)
    else:
        assert session.request("connect_device", params)["state"] == "failed"
    assert client.describes == [] and session.settings(None)["selections"] == []


def test_refresh_failure_retires_previous_candidates_and_keeps_reason(tmp_path, monkeypatch):
    session, client, _ = recorded_setup(tmp_path, monkeypatch)
    refresh(session)
    old = connect_params(session)
    client._enumeration_error = {"code": "synthetic-refusal", "message": "Recording failure"}
    assert refresh(session)["state"] == "failed"
    assert setup(session)["candidates"] == [] and setup(session)["reason"]
    assert session.request("connect_device", old)["state"] == "failed"
    assert client.describes == []


def test_report_reselection_invalidates_listing_and_preserves_live_demo_work(tmp_path, monkeypatch):
    session, client, path = recorded_setup(tmp_path, monkeypatch)
    session.register_frontend(demo_frontends()[0])
    refresh(session)
    params = connect_params(session)
    before = session.snapshot()
    session.select_build_report(path, expected_setup_generation=1)
    assert session.snapshot() == before
    with pytest.raises(ValueError, match="generation"):
        session.request("connect_device", params)
    assert client.describes == []
    # Ordinary retained Demo work is separate from setup report/candidate state.
    source = DesktopSession(demo_frontends())
    create(source, tmp_path / "demo-batch", "Retained Demo", "")
    measure(source)
    settled(source)
    owner, before, data = source.batch, source.snapshot(), source.dataset()
    source.select_build_report(path, expected_setup_generation=0)
    refresh(source)
    assert source.batch is owner and source.snapshot() == before and source.dataset() == data
    completed_command(source, "finish")


@pytest.mark.parametrize("native_type, accepted, succeeds", [(1, False, False), (1, True, True),
                                                           (26, True, False)])
def test_existing_disclosed_and_forbidden_initialization_policy_survives_facade(
    tmp_path, monkeypatch, native_type, accepted, succeeds,
):
    catalog = json.loads((Path(__file__).parent / "fixtures/discovery/catalog.json").read_text())
    row = next(row for row in catalog["instrument_types"] if row["native_type"] == native_type)
    session, client, _ = recorded_setup(
        tmp_path, monkeypatch,
        _candidates(("usb:/policy", native_type, 0x02, row["initialization_refusal"])),
        catalog=json.dumps(catalog),
        describe={"initialized_dtype": native_type, "serial": "SYNTHETIC",
                  "capabilities": {"primary": 0x21, "secondary": 0x30001, "tertiary": 0}},
    )
    refresh(session)
    result = session.request("connect_device", connect_params(
        session, accept_disclosed_initialization=accepted,
    ))
    assert (result["state"] == "completed") is succeeds
    assert len(client.describes) == int(succeeds)


def test_unidentified_serial_requires_expected_model_and_specific_identification(
    tmp_path, monkeypatch,
):
    session, client, _ = recorded_setup(
        tmp_path, monkeypatch, _candidates(("serial:/recording", 0, 0x01, None)),
        describe={"initialized_dtype": 5, "serial": "SERIAL-1",
                  "capabilities": {"primary": 0x21, "secondary": 0x30001, "tertiary": 0}},
    )
    refresh(session)
    failed = session.request("connect_device", connect_params(
        session, expect="GretagMacbeth SpectroScan", accept_disclosed_initialization=True,
    ))
    assert failed["state"] == "failed" and client.describes == []
    refresh(session)
    result = session.request("connect_device", connect_params(
        session, expect="GretagMacbeth SpectroScan", allow_serial_identification=True,
        accept_disclosed_initialization=True,
    ))
    assert result["state"] == "completed"
    assert client.describes[0][1]["allow_serial_identification"] is True


def test_unassessed_unit_remains_visible_and_needs_its_own_acknowledgement(tmp_path, monkeypatch):
    catalog = json.loads(CATALOG)
    row = next(row for row in catalog["instrument_types"] if row["native_type"] == 21)
    row["initialization_assessed"] = False
    candidates = json.loads(_candidates(("usb:/unassessed", 21, 0x02, None)))
    candidates["candidates"][0]["initialization_assessed"] = False
    session, client, _ = recorded_setup(tmp_path, monkeypatch, json.dumps(candidates),
                                       catalog=json.dumps(catalog))
    refresh(session)
    candidate, = setup(session)["candidates"]
    assert candidate["opening_requires"] == "accept_unassessed_initialization"
    assert candidate["attachable"] is False
    assert session.request("connect_device", connect_params(session))["state"] == "failed"
    assert client.describes == []
    refresh(session)
    result = session.request("connect_device", connect_params(
        session, accept_unassessed_initialization=True,
    ))
    assert result["state"] == "completed"
    assert client.describes[0][1]["accept_unassessed_initialization"] is True


def test_driver_allowance_floor_refuses_before_initializing(tmp_path, monkeypatch):
    catalog = json.loads(CATALOG)
    row = next(row for row in catalog["instrument_types"] if row["native_type"] == 21)
    row["read_allowance_floor_s"] = 174  # Explicit synthetic policy input.
    session, client, _ = recorded_setup(tmp_path, monkeypatch, catalog=json.dumps(catalog))
    refresh(session)
    result = session.request("connect_device", connect_params(session, timeout=174))
    assert result["state"] == "failed" and "more than 174" in result["reason"]
    assert client.describes == [] and setup(session)["candidates"][0]["consumed"] is False


@pytest.mark.parametrize("mistake", ["expect", "identify", "serial", "disclosure"])
def test_form_error_keeps_candidate_for_corrected_explicit_selection(
    tmp_path, monkeypatch, mistake,
):
    if mistake in {"expect", "identify"}:
        session, client, _ = recorded_setup(
            tmp_path, monkeypatch, _candidates(("serial:/recording", 0, 0x01, None)),
            describe={"initialized_dtype": 5, "serial": "SERIAL-1",
                      "capabilities": {"primary": 0x21, "secondary": 0x30001, "tertiary": 0}},
        )
        valid = dict(expect="GretagMacbeth SpectroScan", allow_serial_identification=True,
                     accept_disclosed_initialization=True)
        invalid = valid | ({"expect": None} if mistake == "expect"
                           else {"allow_serial_identification": False})
    elif mistake == "disclosure":
        session, client, _ = recorded_setup(
            tmp_path, monkeypatch, _candidates(("serial:/disclosed", 1, 0x01,
                                               "initialization_resets_device")),
            describe={"initialized_dtype": 1, "serial": "DTP22-1",
                      "capabilities": {"primary": 0x21, "secondary": 0x30001, "tertiary": 0}},
        )
        valid, invalid = dict(accept_disclosed_initialization=True), {}
    else:
        session, client, _ = recorded_setup(tmp_path, monkeypatch)
        valid, invalid = {}, dict(expected_serial=True)
    refresh(session)
    candidate = setup(session)["candidates"][0]["candidate_id"]
    failed = session.request("connect_device", connect_params(session, **invalid))
    assert failed["state"] == "failed" and client.describes == []
    assert setup(session)["candidates"][0]["candidate_id"] == candidate
    assert setup(session)["candidates"][0]["consumed"] is False
    corrected = session.request("connect_device", connect_params(session, **valid))
    assert corrected["state"] == "completed" and len(client.describes) == 1
    assert setup(session)["candidates"][0]["consumed"] is True


def test_active_operation_blocks_setup_and_keeps_existing_candidate(tmp_path, monkeypatch):
    session, client, path = recorded_setup(tmp_path, monkeypatch)
    for frontend in demo_frontends():
        session.register_frontend(frontend)
    refresh(session)
    candidates = setup(session)["candidates"]
    create(session, tmp_path / "live", "Active Demo", "")
    state = session.snapshot()["batch"]
    draft = state["draft"]
    identity = str(uuid4())
    assert send(session, "measure", dict(
        context_id=state["context_id"], draft_id=draft["id"], draft_version=draft["version"],
    ), identity=identity)["admission"] == "admitted"
    wait(session, lambda state: bool(state["instructions"]))
    try:
        with pytest.raises(ValueError, match="Stop current acquisition"):
            refresh(session)
        with pytest.raises(ValueError, match="Stop current acquisition"):
            session.select_build_report(path, expected_setup_generation=1)
        assert setup(session)["candidates"] == candidates
        assert client.enumerations == 1 and client.describes == []
    finally:
        assert send(session, "cancel", dict(command_id=identity))["admission"] == "admitted"
        wait(session, lambda state: state["phase"] == "idle")
        completed_command(session, "finish")


@pytest.mark.parametrize("symbol, floor", [("instCR250", 174.0), ("instCR100", 165.0)])
def test_cr_catalog_floor_and_typed_serial_policy_reach_the_registered_source(
    tmp_path, monkeypatch, symbol, floor,
):
    catalog = json.loads((Path(__file__).parent / "fixtures/discovery/catalog.json").read_text())
    row = next(row for row in catalog["instrument_types"] if row["enum"] == symbol)
    assert row["read_allowance_floor_s"] == floor and row["typed_serial_selection"] is True
    # Real retained catalog policy, synthetic capability reply. This tests the
    # facade composition, not acquisition or a physically observed CR device.
    native_identity = current_cr_identity()
    native_identity["native_controls"] = True
    native_identity["identity"].update(
        model="CR-250" if symbol == "instCR250" else "CR-100", serial="CR-SOFTWARE",
        instrument_type=2 if symbol == "instCR250" else 1,
    )
    session, client, _ = recorded_setup(
        tmp_path, monkeypatch, _candidates(("serial:/CR-software", 0, 0x01, None)),
        catalog=json.dumps(catalog),
        describe={"initialized_dtype": row["native_type"], "serial": "CR-SOFTWARE",
                  "native_identity": native_identity,
                  "capabilities": {"primary": 0x21, "secondary": 0x30001, "tertiary": 0}},
    )
    shown = next(row for row in setup(session)["catalog"]["instrument_types"]
                 if row["enum"] == symbol)
    assert shown["read_allowance_floor_s"] == floor
    maximum = max(row["read_allowance_floors_s"].values())
    assert shown["binding_policy"]["all_modes_read_allowance_floor_seconds"] == maximum
    assert shown["binding_policy"]["default_binding_timeout_seconds"] == maximum + 120
    assert shown["model_enum"] == symbol
    assert shown["display_label"] == ("Colorimetry Research CR-250" if symbol == "instCR250"
                                      else "Colorimetry Research CR-100")
    assert shown["opening_requires"] is None and shown["initialization_assessed"] is True
    refresh(session)
    for budget in (30, floor, maximum):
        result = session.request("connect_device", connect_params(
            session, expect=symbol, timeout=budget,
        ))
        assert result["state"] == "failed" and "more than" in result["reason"]
        assert client.describes == []
    result = session.request("connect_device", connect_params(session, expect=symbol, timeout=300))
    assert result["state"] == "completed"
    assert client.describes[0][1]["expected_initialized_dtype"] == row["native_type"]
    assert client.describes[0][1]["allow_serial_identification"] is False
    descriptor = session.settings(result["result"]["selection_id"])["descriptor"]
    assert descriptor["current_binding_identity"]["native_identity"] == native_identity
    assert descriptor["limits"]["read_allowance_floor_seconds"] == floor
    assert descriptor["limits"]["binding_timeout_seconds"] == 300
    assert descriptor["limits"]["binding_policy"] == shown["binding_policy"]
    binding = setup(session)["bindings"][0]
    for key in ("model_enum", "native_type", "display_label", "binding_policy",
                "initialization_assessed", "initialization_refusal", "opening_requires"):
        assert binding[key] == shown[key]
    assert descriptor["device_id"].startswith("instrument:")
    assert result["result"]["connection"]["path"] == "serial:/CR-software"


@pytest.mark.parametrize("control", [KeyboardInterrupt, SystemExit])
def test_interrupted_description_retains_stage_and_unconfirmed_state_without_retry(
    tmp_path, monkeypatch, control,
):
    session, client, _ = recorded_setup(tmp_path, monkeypatch)
    for frontend in demo_frontends():
        session.register_frontend(frontend)
    create(session, tmp_path / "live", "Retained live Demo", "")
    measure(session)
    settled(session)
    owner, before, data = session.batch, session.snapshot(), session.dataset()
    refresh(session)
    params = connect_params(session)
    calls = []
    original_control = control("Synthetic control interruption")

    def interrupted(*args, **kwargs):
        calls.append((args, kwargs))
        raise original_control

    monkeypatch.setattr(client, "describe_json", interrupted)
    try:
        with pytest.raises(control) as caught:
            session.request("connect_device", params)
        assert caught.value is original_control
        assert len(calls) == 1 and setup(session)["candidates"][0]["consumed"] is True
        assert setup(session)["state"] == "interrupted"
        replay = session.request("connect_device", params)
        assert replay["state"] == "interrupted" and replay["stage"] == "description"
        assert "unconfirmed" in replay["reason"] and len(calls) == 1
        assert session.batch is owner and session.snapshot() == before and session.dataset() == data
    finally:
        completed_command(session, "finish")


def test_reported_identity_survives_path_change_and_duplicate_serial_is_not_registered(
    tmp_path, monkeypatch,
):
    first_path = tmp_path / "first"
    first_path.mkdir()
    first, client, _ = recorded_setup(
        first_path, monkeypatch, _candidates(("usb:/first-path", 21, 0x02, None)),
    )
    refresh(first)
    bound = first.request("connect_device", connect_params(first))
    original = bound["result"]["descriptor"]["device_id"]
    second_path = tmp_path / "second"
    second_path.mkdir()
    second, _, _ = recorded_setup(
        second_path, monkeypatch, _candidates(("usb:/changed-path", 21, 0x02, None)),
    )
    refresh(second)
    reopened = second.request("connect_device", connect_params(second))
    assert reopened["result"]["descriptor"]["device_id"] == original
    assert reopened["result"]["connection"]["path"] != bound["result"]["connection"]["path"]
    client._candidates = _candidates(("usb:/duplicate-path", 21, 0x02, None))
    refresh(first)
    duplicate = first.request("connect_device", connect_params(first))
    assert duplicate["state"] == "failed" and "model and serial" in duplicate["reason"]
    assert len(first.settings(None)["selections"]) == 1 and len(client.describes) == 2
