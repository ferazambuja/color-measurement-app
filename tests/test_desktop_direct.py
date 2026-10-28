"""Native setup composition over scripted ports only; no OS serial enumeration or opening."""

from uuid import uuid4

import pytest
from test_serial_instruments import (
    ScriptedPort,
    direct_request,
    drain,
    exchange,
    settings_script,
    shutdown,
    startup,
)
from test_serial_transport import Clock

from color_measurement import serial_instruments
from color_measurement.desktop_session import DesktopSession


@pytest.fixture(autouse=True)
def no_os_ports(monkeypatch):
    monkeypatch.setattr(serial_instruments, "open_serial_port",
                        lambda *a, **k: pytest.fail("OS serial opening is forbidden"))


def state(session):
    return session.request("equipment_setup", {})


def connect(session, port, *, path="scripted-port", **changes):
    params = dict(command_id=str(uuid4()), setup_generation=state(session)["generation"],
                  model="CR-100", expected_serial="CR-SOFTWARE",
                  accept_disclosed_initialization=True, response_timeout=5, measurement_timeout=300)
    params.update(changes)
    return session.connect_direct_serial(path, transport=port, clock=Clock(), **params), params


def ready_port(model="CR-100"):
    return ScriptedPort(startup(model) + settings_script(model) + shutdown(model))


def close_params(session, binding):
    return dict(command_id=str(uuid4()), setup_generation=state(session)["generation"],
                binding_id=binding["binding_id"], binding_generation=binding["generation"])


@pytest.mark.parametrize("model", serial_instruments.MODELS)
def test_direct_native_setup_to_batch_retention_formats_finish_and_close(tmp_path, model):
    from test_application import _assert_current_automatic_companion
    from test_desktop_session import measure, send, wait
    from test_serial_instruments import acquisition_script

    from color_measurement.application import review_saved
    from color_measurement.operation_records import result_from_json

    port = ScriptedPort(startup(model) + settings_script(model)
                        + settings_script(model)
                        + acquisition_script(model) + shutdown(model))
    session = DesktopSession([])
    connected, _ = connect(session, port, model=model, expected_serial=None)
    assert connected["state"] == "completed", connected
    selection = connected["result"]["selection_id"]
    folder = tmp_path / "direct-batch"
    session.create(folder, "Native direct source", "", selection_id=selection,
                   mode="emissive_spot", options={"automatic": False})
    measure(session)
    saved = wait(session, lambda state: not state["companions"]["saving"])
    assert session.batch.command_outcome(saved["operations"][0]["command_id"])["state"] == \
        "completed"
    assert saved["sample_count"] == saved["accepted_count"] == saved["saved_count"] == 1
    assert saved["descriptor"]["contract"] == "direct_serial_instrument_v1"
    original = result_from_json((folder / saved["operations"][0]["path"]).read_text())
    row, = session.dataset()["readings"].values()
    assert row["observation"]["observation_id"] == original.patches[0].observation.observation_id
    assert original.device.model == model and original.provenance == "simulated"
    assert original.patches[0].observation.calibration.status == "unknown"
    finish_id = str(uuid4())
    assert send(session, "finish", identity=finish_id)["admission"] == "admitted"
    final = wait(session, lambda state:
                 session.batch.command_outcome(finish_id)["state"] != "pending")
    assert session.batch.command_outcome(finish_id)["state"] == "completed"
    _assert_current_automatic_companion(final, "mat", "measurements.mat")
    _assert_current_automatic_companion(final, "csv", "measurements.csv")
    review = review_saved(folder)
    assert review["sample_count"] == 1 and review["operations"][0]["review_state"] == "accepted"
    assert review["manifest"]["descriptor"] == final["descriptor"]
    # Unknown native quantity units remain explicit TI3 component refusals.
    assert final["ti3"] is not None
    assert session.request("close_device", close_params(session, state(session)["bindings"][0]))[
        "state"] == "completed"
    assert not port.is_open and not port.script
    assert session.batch.command(
        str(uuid4()), final["generation"], "close", {"discard_token": None},
        batch_id=final["batch_id"],
    )["admission"] == "admitted"


def test_direct_setup_catalog_and_selected_connect_have_one_reconciled_binding():
    session, port = DesktopSession([]), ready_port()
    catalogue = state(session)["direct_serial"]
    assert len(catalogue["models"]) == 5 and not port.commands
    cs = next(row for row in catalogue["models"] if row["model_enum"] == "CS-2000")
    assert cs["binding_policy"]["measurement_floor_seconds"] == 287
    assert cs["binding_policy"]["response_timeout_default_seconds"] == 10
    assert "Disable instrument measurement switch" in cs["initialization_effects"]
    result, params = connect(session, port)
    assert result["state"] == "completed", result["reason"]
    before = list(port.commands)
    original = state(session)["bindings"][0]
    assert original["state"] == "active" and original["backend"] == "direct_serial"
    assert result["result"]["native_receipt"]["exchanges"]
    assert session.request("equipment_outcome", dict(command_id=params["command_id"])) == result
    assert session.connect_direct_serial("scripted-port", transport=port, clock=Clock(),
                                         **params) == result
    assert port.commands == before
    duplicate, _ = connect(session, port)
    assert duplicate["state"] == "failed" and "already" in duplicate["reason"]
    assert port.commands == before
    closed = session.request("close_device", close_params(session, original))
    assert closed["state"] == "completed" and not port.is_open
    assert closed["result"]["native_cleanup"]["port_closed"] is True
    assert closed["result"]["instrument_state"] == "unknown"
    assert not session.settings(None)["selections"]


@pytest.mark.parametrize("changes", [
    dict(accept_disclosed_initialization=False), dict(accept_disclosed_initialization=1),
    dict(model="CS-2000", measurement_timeout=281), dict(response_timeout=True),
])
def test_direct_setup_rejects_missing_acknowledgement_and_timeout_before_initialization(changes):
    session, port = DesktopSession([]), ready_port()
    failed, _ = connect(session, port, **changes)
    assert failed["state"] == "failed" and not port.commands
    assert not state(session)["bindings"] and not session.settings(None)["selections"]


def test_direct_close_failure_retains_cleanup_binding_until_explicit_retry(monkeypatch):
    session, port = DesktopSession([]), ready_port()
    connected, _ = connect(session, port)
    assert connected["state"] == "completed"
    binding = state(session)["bindings"][0]
    calls = []
    monkeypatch.setattr(port, "close", lambda: calls.append("close"))
    params = close_params(session, binding)
    failed = session.request("close_device", params)
    assert failed["state"] == "failed" and calls == ["close"]
    assert state(session)["bindings"][0]["state"] == "cleanup_required"
    assert len(session.settings(None)["selections"]) == 1
    assert session.request("equipment_outcome", dict(command_id=params["command_id"])) == failed
    assert session.request("close_device", params) == failed and calls == ["close"]
    refused, _ = connect(session, ready_port())
    assert refused["state"] == "failed" and "already" in refused["reason"]
    monkeypatch.setattr(port, "close", lambda: setattr(port, "is_open", False))
    closed = session.request("close_device", close_params(session, binding))
    assert closed["state"] == "completed" and not session.settings(None)["selections"]


def test_direct_reopen_asserts_retained_identity_and_preserves_other_sources():
    session, port = DesktopSession([]), ready_port()
    result, _ = connect(session, port)
    original = state(session)["bindings"][0]
    session.request("close_device", close_params(session, original))
    binding = state(session)["bindings"][0]
    params = close_params(session, binding) | dict(
        accept_disclosed_initialization=True, response_timeout=5, measurement_timeout=300,
    )
    wrong = ScriptedPort(startup("CR-100", serial="OTHER"))
    failed = session.reopen_direct_serial(
        "different-port", transport=wrong, clock=Clock(), **params,
    )
    assert failed["state"] == "failed" and not wrong.is_open
    assert state(session)["bindings"] == [binding]
    assert session.request("equipment_outcome", dict(command_id=params["command_id"])) == failed
    new_port = ready_port()
    params["command_id"] = str(uuid4())
    reopened = session.reopen_direct_serial("different-port", transport=new_port, clock=Clock(),
                                            **params)
    assert reopened["state"] == "completed", reopened["reason"]
    assert reopened["result"]["descriptor"]["device_id"] == (
        result["result"]["descriptor"]["device_id"]
    )
    assert reopened["result"]["binding_generation"] == 3
    assert session.reopen_direct_serial("different-port", transport=new_port, clock=Clock(),
                                       **params) == reopened
    session.request("close_device", close_params(session, state(session)["bindings"][0]))
    assert not new_port.script


def test_cs_session_recovery_survives_retirement_and_repeated_reopen_failures():
    import json

    model, session = "CS-2000", DesktopSession([])
    first = ScriptedPort(startup(model) + settings_script(model) * 2
                         + [exchange("MEAS,1", "ER02")])
    connected, _ = connect(session, first, model=model, expected_serial="1234567")
    assert connected["state"] == "completed", connected
    frontend = session._selections[connected["result"]["selection_id"]]["frontend"]
    operation = frontend.prepare(json.dumps(
        direct_request(frontend, options=dict(automatic=False)),
    ))
    operation.dispatch()
    result, _ = drain(operation)
    assert result.status == "failed" and not first.is_open
    assert not any(sent.startswith(b"BALS,1,1") for sent in first.commands)
    binding = state(session)["bindings"][0]
    assert session.request("close_device", close_params(session, binding))["state"] == "completed"
    original = operation.result_json()
    ledger = session._equipment_setup._unrestored_backlight
    assert ledger == {(model, "1234567"): (1, 1)}

    # A different reported unit cannot consume this unit's retained restoration.
    other = ScriptedPort(startup(model, serial="7654321")
                         + settings_script(model) + shutdown(model))
    different, _ = connect(session, other, path="other-port", model=model,
                           expected_serial="7654321")
    assert different["state"] == "completed", different
    other_binding = next(view for view in state(session)["bindings"]
                         if view["serial"] == "7654321")
    assert session.request("close_device", close_params(session, other_binding))[
        "state"] == "completed"
    assert ledger == {(model, "1234567"): (1, 1)}

    binding = next(view for view in state(session)["bindings"] if view["serial"] == "1234567")
    script = startup(model)
    script[2] = exchange("BALR", "OK00,0,0")
    del script[3]
    for _ in range(2):
        failed_port = ScriptedPort(script[:-1] + [exchange("MSWE,0", "unframed")])
        params = close_params(session, binding) | dict(
            accept_disclosed_initialization=True, response_timeout=5, measurement_timeout=300,
        )
        failed = session.reopen_direct_serial("reselected-port", transport=failed_port,
                                              clock=Clock(), **params)
        assert failed["state"] == "failed" and not failed_port.is_open
        assert not failed_port.script and ledger[(model, "1234567")][1] == 1
        assert session.request("equipment_outcome", dict(command_id=params["command_id"])) == failed

    cleanup = [exchange("BALS,0,1", "OK00"), exchange("RMTS,0", "OK00")]
    recovered = ScriptedPort(script + settings_script(model) + cleanup)
    params = close_params(session, binding) | dict(
        accept_disclosed_initialization=True, response_timeout=5, measurement_timeout=300,
    )
    reopened = session.reopen_direct_serial("reselected-port", transport=recovered,
                                            clock=Clock(), **params)
    assert reopened["state"] == "completed", reopened
    binding = next(view for view in state(session)["bindings"] if view["serial"] == "1234567")
    assert session.request("close_device", close_params(session, binding))["state"] == "completed"
    assert not recovered.script and not recovered.is_open and ledger == {}
    assert operation.result_json() == original


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("phase", ["initialization", "settings_readback"])
def test_direct_initialization_keeps_snapshot_replay_and_exact_cancel_responsive(
    tmp_path, cancel, phase,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from color_measurement.demo import demo_frontends

    session, port = DesktopSession(demo_frontends()), ready_port()
    entered, release = Event(), Event()
    read = port.read
    before = session.settings(None)

    def held_read(size):
        command_count = 1 if phase == "initialization" else len(startup("CR-100")) + 1
        if len(port.commands) == command_count:
            entered.set()
            assert release.wait(5), "The test did not release its scripted port"
        return read(size)

    port.read = held_read
    with ThreadPoolExecutor(max_workers=1) as worker:
        try:
            pending, params = connect(session, port, executor=worker)
            assert pending["state"] == "pending" and entered.wait(2)
            future = session._equipment_setup._future
            # These queries run on the calling thread while actual serial I/O is held.
            assert session.snapshot()["batch"] is None
            assert session.settings(None) == before
            active = state(session)["active_setup"]
            assert active == dict(command_id=params["command_id"], stage=phase,
                                  action="connect_direct_serial",
                                  cancel_requested=False, backend="direct_serial",
                                  cancellation_mode="cooperative")
            assert session.connect_direct_serial("scripted-port", transport=port, clock=Clock(),
                                                 **params) == pending | dict(
                                                     stage=phase,
                                                 )
            assert session._equipment_setup._future is future
            with pytest.raises(ValueError, match="different content"):
                session.connect_direct_serial("changed-port", transport=port, clock=Clock(),
                                              **params)
            with pytest.raises(ValueError, match="setup is running"):
                connect(session, ScriptedPort([]), path="another-port", executor=worker)
            with pytest.raises(ValueError, match="setup is running"):
                session.close_direct_sources()
            with pytest.raises(ValueError, match="setup is running"):
                session.create(tmp_path / "refused-during-setup", "Not created", "",
                               selection_id=before["selections"][0]["selection_id"],
                               mode="emissive_spot", options={})
            assert not (tmp_path / "refused-during-setup").exists()
            with pytest.raises(ValueError, match="no longer active"):
                session.request("cancel_setup", dict(command_id=str(uuid4())))
            if cancel:
                outcome = session.request("cancel_setup", dict(
                    command_id=params["command_id"],
                ))
                assert outcome["state"] == "pending"
                assert state(session)["active_setup"]["cancel_requested"]
        finally:
            release.set()
        result = future.result(timeout=5)
    assert state(session)["active_setup"] is None
    assert session.request("equipment_outcome", dict(command_id=params["command_id"])) == result
    assert result["state"] == ("failed" if cancel else "completed"), result
    if cancel:
        assert not port.is_open and session.settings(None) == before
        assert result["result"]["native_receipt"]["cleanup"]["port_closed"] is True
    else:
        binding = state(session)["bindings"][0]
        assert session.request("close_device", close_params(session, binding))[
            "state"] == "completed"
        assert not port.is_open and not port.script


@pytest.mark.parametrize("failure_type", [OSError, KeyboardInterrupt])
def test_direct_initialization_submission_failure_revokes_queued_effect_and_keeps_original_control(
    monkeypatch, failure_type,
):
    from concurrent.futures import ThreadPoolExecutor

    session, port = DesktopSession([]), ScriptedPort([])
    queued = []
    failure = failure_type("Controlled exception after worker submission")
    params = dict(command_id=str(uuid4()), setup_generation=0, model="CR-100",
                  expected_serial="CR-SOFTWARE", accept_disclosed_initialization=True,
                  response_timeout=5, measurement_timeout=300)
    with ThreadPoolExecutor(max_workers=1) as worker:
        submit = worker.submit

        def submitted_then_failed(callback):
            queued.append(submit(callback))
            raise failure

        monkeypatch.setattr(worker, "submit", submitted_then_failed)
        if failure_type is OSError:
            result = session.connect_direct_serial("scripted-port", transport=port,
                                                   clock=Clock(), executor=worker, **params)
        else:
            with pytest.raises(failure_type) as raised:
                session.connect_direct_serial("scripted-port", transport=port,
                                              clock=Clock(), executor=worker, **params)
            assert raised.value is failure
            result = session.request("equipment_outcome", dict(command_id=params["command_id"]))
        assert queued[0].result(timeout=5) == result
        assert state(session)["active_setup"] is None and not state(session)["bindings"]
        assert not port.commands and port.is_open
        assert session.connect_direct_serial("scripted-port", transport=port, clock=Clock(),
                                             executor=worker, **params) == result
        assert len(queued) == 1 and result["state"] == (
            "failed" if failure_type is OSError else "interrupted"
        )
    port.close()  # This never-owned simulated fixture was not acquired or closed by setup.


@pytest.mark.parametrize("failure_type", [ValueError, KeyboardInterrupt, SystemExit])
def test_direct_registration_rollback_closes_candidate_and_keeps_original_control(
    monkeypatch, failure_type,
):
    session, first = DesktopSession([]), ready_port()
    connected, _ = connect(session, first)
    assert connected["state"] == "completed"
    original = state(session)["bindings"][0]
    selections = session.settings(None)
    register = session.register_frontend
    failure = failure_type("Registration interrupted after selection mutation")

    def interrupted(frontend):
        register(frontend)
        raise failure

    monkeypatch.setattr(session, "register_frontend", interrupted)
    candidate = ScriptedPort(startup("CR-100", serial="SECOND") + settings_script("CR-100"))
    if failure_type is ValueError:
        failed, params = connect(session, candidate, path="second-port", expected_serial="SECOND")
        assert failed["state"] == "failed"
    else:
        with pytest.raises(failure_type) as raised:
            connect(session, candidate, path="second-port", expected_serial="SECOND")
        assert raised.value is failure
    assert not candidate.is_open and state(session)["bindings"] == [original]
    assert session.settings(None) == selections
    # Rollback keeps the first binding's actual persistent instrument, including its host port.
    session.request("close_device", close_params(session, original))
    assert not first.is_open


def test_direct_failed_identification_with_unconfirmed_close_keeps_candidate_for_cleanup(
    monkeypatch,
):
    session = DesktopSession([])
    port = ScriptedPort([exchange("RC Model", "OK:0:RC Model:CR-250")])
    calls = []
    monkeypatch.setattr(port, "close", lambda: calls.append("close"))
    failed, params = connect(session, port)
    assert failed["state"] == "failed" and calls == ["close"]
    binding = failed["result"]["binding"]
    assert binding["state"] == "cleanup_required" and not session.settings(None)["selections"]
    assert failed["result"]["native_receipt"]["exchanges"]
    assert session.request("equipment_outcome", dict(command_id=params["command_id"])) == failed
    refused, _ = connect(session, ready_port())
    assert refused["state"] == "failed" and calls == ["close"]
    monkeypatch.setattr(port, "close", lambda: setattr(port, "is_open", False))
    closed = session.request("close_device", close_params(session, binding))
    assert closed["state"] == "completed" and not port.is_open


def test_renderer_cannot_supply_direct_port_or_reopen_paths():
    session = DesktopSession([])
    for method in ("connect_direct_serial", "reopen_direct_serial"):
        with pytest.raises(ValueError):
            session.request(method, dict(port="forbidden-renderer-path"))
    with pytest.raises(ValueError):
        session.request("equipment_outcome", dict(command_id=str(uuid4()), port="forbidden"))


def test_direct_channel_initialization_failed_close_retains_registry_owner_until_explicit_retry(
    monkeypatch,
):
    session, port = DesktopSession([]), ScriptedPort([])
    monkeypatch.setattr(port, "close", lambda: None)

    def fail(*args, **kwargs):
        raise ValueError("Owned transport has no channel")

    monkeypatch.setattr(serial_instruments, "SerialChannel", fail)
    failed, _ = connect(session, port)
    assert failed["state"] == "failed"
    binding = failed["result"]["binding"]
    assert binding["state"] == "cleanup_required" and not binding["cleanup"]["port_closed"]
    refused, _ = connect(session, ScriptedPort([]))
    assert refused["state"] == "failed" and "already" in refused["reason"]
    monkeypatch.setattr(port, "close", lambda: setattr(port, "is_open", False))
    closed = session.request("close_device", close_params(session, binding))
    assert closed["state"] == "completed" and not port.is_open


def test_direct_completed_worker_failed_host_close_preserves_source_until_public_retirement(
    monkeypatch,
):
    import json

    session = DesktopSession([])
    port = ScriptedPort(startup("CR-100") + settings_script("CR-100") * 2
                        + [exchange("M", "malformed")])
    connected, _ = connect(session, port)
    assert connected["state"] == "completed"
    binding = state(session)["bindings"][0]
    frontend = session._selections[connected["result"]["selection_id"]]["frontend"]
    monkeypatch.setattr(port, "close", lambda: None)
    operation = frontend.prepare(json.dumps(
        direct_request(frontend, options=dict(automatic=False)),
    ))
    operation.dispatch()
    result, _ = drain(operation)
    assert result.status == "failed" and result.software_stopped
    assert state(session)["bindings"][0]["state"] == "cleanup_required"
    original = operation.result_json()
    refused, _ = connect(session, ScriptedPort([]))
    assert refused["state"] == "failed" and "already" in refused["reason"]
    failed = session.request("close_device", close_params(session, binding))
    assert failed["state"] == "failed" and len(session.settings(None)["selections"]) == 1
    monkeypatch.setattr(port, "close", lambda: setattr(port, "is_open", False))
    closed = session.request("close_device", close_params(session, binding))
    assert closed["state"] == "completed" and not session.settings(None)["selections"]
    assert operation.result_json() == original


def test_direct_batch_failed_port_close_can_finish_and_retry_without_rewriting_failure(
    tmp_path, monkeypatch,
):
    from test_desktop_session import measure, send, wait

    from color_measurement.operation_records import result_from_json

    session = DesktopSession([])
    port = ScriptedPort(startup("CR-100") + settings_script("CR-100") * 2
                        + [exchange("M", "malformed")])
    connected, _ = connect(session, port)
    assert connected["state"] == "completed"
    binding = state(session)["bindings"][0]
    folder = tmp_path / "failed-direct"
    session.create(folder, "Retained failure", "", selection_id=connected["result"]["selection_id"],
                   mode="emissive_spot", options=dict(automatic=False))
    monkeypatch.setattr(port, "close", lambda: None)
    measure(session)
    saved = wait(session, lambda current: current["phase"] == "idle")
    assert session.batch.command_outcome(saved["operations"][0]["command_id"])["state"] == (
        "completed"
    )
    assert saved["software_stopped"] and not saved["sample_count"]
    original = (folder / saved["operations"][0]["path"]).read_bytes()
    assert result_from_json(original.decode()).status == "failed"
    finish_id = str(uuid4())
    finish = send(session, "finish", identity=finish_id)
    assert finish["admission"] == "admitted"
    wait(session, lambda current: session.batch.command_outcome(finish_id)["state"] != "pending")
    assert session.batch.command_outcome(finish_id)["state"] == "completed"
    failed = session.request("close_device", close_params(session, binding))
    assert failed["state"] == "failed"
    assert state(session)["bindings"][0]["state"] == "cleanup_required"
    monkeypatch.setattr(port, "close", lambda: setattr(port, "is_open", False))
    closed = session.request("close_device", close_params(session, binding))
    assert closed["state"] == "completed" and not port.is_open
    assert (folder / saved["operations"][0]["path"]).read_bytes() == original


@pytest.mark.parametrize("model", serial_instruments.MODELS)
def test_native_application_exit_closes_each_owned_direct_source_without_a_batch(model):
    port, session = ready_port(model), DesktopSession([])
    connected, _ = connect(session, port, model=model, expected_serial=None)
    assert connected["state"] == "completed"
    report = session.close_direct_sources()
    assert not report["failures"] and len(report["outcomes"]) == 1
    assert not port.is_open and not port.script
    binding, = state(session)["bindings"]
    assert binding["state"] == "closed" and binding["connection_available"] is False
    assert not session.settings(None)["selections"]


def test_application_exit_preserves_failed_close_ownership_then_retries_only_the_owned_port():
    session, port = DesktopSession([]), ready_port()
    connected, _ = connect(session, port)
    original = session.settings(None)
    close = port.close
    calls = []
    def refused_once():
        calls.append(True)
        if len(calls) == 1:
            raise OSError("Controlled host-port cleanup failure")
        close()
    port.close = refused_once
    first = session.close_direct_sources()
    assert first["failures"] and port.is_open
    assert state(session)["bindings"][0]["state"] == "cleanup_required"
    assert {item["device_id"] for item in session.settings(None)["selections"]} == {
        item["device_id"] for item in original["selections"]
    }
    second = session.close_direct_sources()
    assert not second["failures"] and not port.is_open and len(calls) == 2
    assert not session.settings(None)["selections"]


def test_application_exit_requires_batch_close_and_reports_automatically_closed_source(tmp_path):
    from test_desktop_session import wait

    from color_measurement.desktop import _close_owner

    session, port = DesktopSession([]), ready_port()
    connected, _ = connect(session, port)
    session.create(tmp_path / "close-lifetime", "Retained source", "",
                   selection_id=connected["result"]["selection_id"],
                   mode="emissive_spot", options={})
    wait(session, lambda current: not current["companions"]["saving"])
    with pytest.raises(ValueError, match="Close the retained batch"):
        session.close_direct_sources()
    assert port.is_open
    # Model the existing owner's terminal automatic host release without changing registry identity.
    session._equipment_setup._bindings[connected["result"]["binding_id"]]["instrument"].close()
    binding, = state(session)["bindings"]
    assert binding["state"] == "active" and binding["connection_available"] is False
    current = session.batch.snapshot()
    closed = _close_owner(session, "close", batch_id=current["batch_id"],
                          generation=current["generation"],
                          discard_token=session.batch.discard_summary()["token"])
    assert closed["state"] == "completed"
    assert session.batch._closed and session.snapshot()["dataset_binding"]["closed"]
    assert session.snapshot()["dataset_binding"]["read_only"]
    assert not session.close_direct_sources()["failures"]
