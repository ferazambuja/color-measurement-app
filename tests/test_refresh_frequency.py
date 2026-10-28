"""Fresh Hz through the shared session, scalar retention and common persistence.

Connected cases use authenticated substituted workers with no device transport.
They prove orchestration and record semantics, not physical refresh accuracy.
"""

import hashlib
import json
import os
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import supplied_stub

from color_measurement.argyll_capability import CAPABILITY2, mode_word
from color_measurement.demo import demo_frontends
from color_measurement.instrument import Configuration, Instrument, InstrumentError
from color_measurement.instrument_frontend import InstrumentFrontend
from color_measurement.models import (
    CalibrationContext,
    DeviceIdentity,
    ExternalStimulus,
    MeasuredScalar,
    ScalarObservation,
)
from color_measurement.operation_exports import OperationSaveError, from_mat, load, save, to_mat
from color_measurement.operation_records import (
    OperationResult,
    PatchResult,
    result_from_json,
    result_to_json,
)

LAYOUT = {"stimulus_id": "WHITE-60", "stimulus_description": "Externally presented steady white"}


def frontend(device=None, *, facilities=CAPABILITY2["emis_refr_meas"], spectral=False):
    return InstrumentFrontend(device or Instrument("/must-not-open", "instI1Disp3"),
        mode_capabilities=mode_word("emissive_spot", spectral=spectral),
        trigger_capabilities=facilities, filter_capabilities=0, provenance="simulated")


def request(front, **changes):
    descriptor = json.loads(front.describe_json())
    return json.dumps({
        "schema_version": 1, "device_id": descriptor["device_id"],
        "capability_id": descriptor["capability_id"], "mode": "emissive_spot",
        "workflow": "refresh_frequency", "patch_ids": ["RATE-1"],
        "options": {}, "layout": LAYOUT, **changes,
    })


def drive(front, *, cancel=False):
    operation = front.prepare(request(front), timeout=3, response_timeout=3)
    events = []
    try:
        operation.dispatch()
        deadline = time.monotonic() + 8
        while True:
            while raw := operation.poll_event_json():
                event = json.loads(raw)
                events.append(event)
                if event["kind"] == "instruction_required":
                    operation.respond(operation.operation_id, event["payload"]["request_id"],
                                      "cancel" if cancel else "accept")
            if operation.done:
                break
            assert time.monotonic() < deadline, "refresh operation did not stop"
            time.sleep(0.002)
        return result_from_json(operation.result_json()), events
    finally:
        operation.close()


def device(fixture):
    return Instrument(Path(supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")), "instI1Disp3",
                      environment={**os.environ, "CM_COLORIMETER_FIXTURE": fixture},
               expected_helper_sha256=hashlib.sha256(
                   Path(supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")).read_bytes()).hexdigest())


def outcome():
    identity = DeviceIdentity("test-device", "synthetic", "fixture", "1")
    calibration = CalibrationContext("not_applicable", "Synthetic scalar, no hardware")
    observation = ScalarObservation(
        1, "reading-1", "operation-1", "RATE-1", "2026-09-30T12:00:00+00:00", "simulated",
        identity, "emissive_spot", "emissive_spot", (), calibration, MeasuredScalar(59.94),
        ExternalStimulus(LAYOUT["stimulus_id"], LAYOUT["stimulus_description"]),
        '{"event":"refresh_frequency","value":59.94}', ("SIMULATED",),
    )
    retained = json.dumps({"device_id": identity.device_id, "mode": "emissive_spot",
                          "workflow": "refresh_frequency", "patch_ids": ["RATE-1"],
                          "layout": LAYOUT})
    return OperationResult(
        1, "operation-1", identity.device_id, identity, "simulated", "emissive_spot",
        "refresh_frequency", "completed", ("RATE-1",), (PatchResult(0, "RATE-1", observation),),
        retained, (), calibration, None, True, "not_applicable",
    )


@pytest.mark.parametrize("value", [True, 0, -60, float("nan"), float("inf"), 10**400])
def test_no_invalid_measurement_or_missing_result_becomes_zero_hz(value):
    with pytest.raises(ValueError):
        MeasuredScalar(value)


def test_scalar_contract_omits_photometric_fields_and_binds_declared_stimulus():
    result = outcome()
    assert result_from_json(result_to_json(result)) == result
    reading = json.loads(result_to_json(result))["patches"][0]["observation"]
    assert reading["record_kind"] == "scalar"
    assert reading["backend_reported"] == {
        "value": 59.94, "quantity": "refresh_frequency", "unit": "Hz",
        "native_method": "read_refrate",
    }
    assert not {"xyz", "spectrum", "observer", "illuminant", "mcond", "project_computed"} & (
        reading.keys() | reading["backend_reported"].keys())
    for key in ("stimulus_id", "stimulus_description"):
        altered = json.loads(result.request_json)
        altered["layout"][key] = "different"
        with pytest.raises(ValueError, match="stimulus"):
            replace(result, request_json=json.dumps(altered))
    with pytest.raises(ValueError, match="colour workflow"):
        replace(result, workflow="spot", request_json=result.request_json.replace(
            '"refresh_frequency"', '"spot"'))


@pytest.mark.parametrize("format", ["json", "mat"])
def test_scalar_uses_common_save_roundtrip_and_retry(format, tmp_path, monkeypatch):
    if format == "mat":
        pytest.importorskip("scipy")
    result = outcome()
    path = tmp_path / f"scalar.{format}"
    original = os.link
    def blocked(*args):
        raise OSError("publication refused")
    monkeypatch.setattr(os, "link", blocked)
    with pytest.raises(OperationSaveError) as failed:
        save(result, path, format=format)
    assert failed.value.result == result
    assert not path.exists()
    monkeypatch.setattr(os, "link", original)
    save(failed.value.result, path, format=format)
    assert load(path, format=format) == result


def test_mat_scalar_view_and_canonical_record_agree():
    io = pytest.importorskip("scipy.io")
    import io as streams

    result = outcome()
    payload = to_mat(result)
    assert from_mat(payload) == result
    view = io.loadmat(streams.BytesIO(payload), simplify_cells=True)["operation"]
    scalar = view["patches"]["backend_reported"]
    assert scalar["value"] == 59.94 and scalar["unit"] == "Hz"
    assert not {"xyz", "spectrum", "observer", "illuminant", "mcond"} & scalar.keys()


@pytest.mark.parametrize("spectral", [False, True])
def test_offer_uses_measurement_bit_not_identity_or_stored_or_settable_bits(spectral):
    for facilities, expected in [
        (CAPABILITY2["emis_refr_meas"], True),
        (CAPABILITY2["get_refresh_rate"], False),
        (CAPABILITY2["set_refresh_rate"], False),
    ]:
        descriptor = json.loads(frontend(facilities=facilities, spectral=spectral).describe_json())
        workflows = [w for m in descriptor["modes"] for w in m["workflows"]
                     if w["workflow"] == "refresh_frequency"]
        assert bool(workflows) is expected
        if workflows:
            assert workflows[0]["expected_quantities"]["unit"] == "Hz"
            assert workflows[0]["result_kind"] == "scalar"
            assert workflows[0]["option_ids"] == []
            assert workflows[0]["min_patches"] == workflows[0]["max_patches"] == 1


@pytest.mark.parametrize("changes", [
    {"layout": {}}, {"layout": {**LAYOUT, "stimulus_id": ""}},
    {"layout": {**LAYOUT, "stimulus_description": "é" * 1025}},
    {"patch_ids": ["A", "B"]}, {"event_contract": "patch_v1"},
    {"options": {"highres": True}}, {"options": {"averages": 2}},
])
def test_invalid_scalar_request_is_refused_before_start(changes):
    front = frontend()
    with pytest.raises(ValueError):
        front.prepare(request(front, **changes))


def test_canonical_descriptor_is_the_scalar_capture_authority():
    fixtures = Path(__file__).parent / "fixtures/generic_frontend/i1_display_3"
    canonical = json.loads((fixtures / "describe.json").read_text())
    for name in ("refresh_success", "refresh_needs_cal", "refresh_nan"):
        capture = json.loads((fixtures / f"{name}.json").read_text())
        assert capture["descriptor"] == canonical
        assert capture["request"]["capability_id"] == canonical["capability_id"]


def test_default_native_persona_admits_the_same_refresh_measurement():
    measured = device("success").measure_refresh(
        acknowledge=lambda prompt: "acknowledge")
    assert measured.refresh_frequency.value == 59.94005994005994


def test_i1display_demo_uses_the_canonical_scalar_offer():
    front = next(f for f in demo_frontends() if f._meter.persona == "i1_display_3")
    result, _ = drive(front)
    assert result.status == "completed"
    assert result.patches[0].observation.backend_reported.value == 59.94
    assert result.patches[0].observation.record_kind == "scalar"


def test_fresh_frequency_native_lifecycle_without_colour_trigger():
    measured = device("refresh_no_trigger").measure_refresh(
        acknowledge=lambda prompt: "acknowledge")
    assert measured.patches == () and measured.workflow == "refresh_frequency"
    assert measured.refresh_frequency.value == 59.94005994005994
    connected = next(e for e in measured.events if e["event"] == "connected")
    assert connected["trigger"] is None
    assert not connected["trigger_capabilities"] & CAPABILITY2["prog_trig"]
    assert [e["instruction"] for e in measured.events if e["event"] == "instruction_required"] == [
        "refresh_stimulus"]


def test_mode_dependent_refresh_capability_is_checked_after_mode_selection():
    measured = device("refresh_after_mode").measure_refresh(
        acknowledge=lambda prompt: "acknowledge")
    assert measured.refresh_frequency.value == 59.94005994005994
    connected = next(e for e in measured.events if e["event"] == "connected")
    assert connected["trigger_capabilities"] & CAPABILITY2["emis_refr_meas"]


@pytest.mark.parametrize("fixture,stage,code", [
    ("refresh_error", "read_refrate", 0x70007),
    ("refresh_needs_cal", "read_refrate", 0x110008),
    ("refresh_zero", "invalid_refresh_frequency", 0),
    ("refresh_negative", "invalid_refresh_frequency", 0),
    ("refresh_nan", "invalid_refresh_frequency", 0),
    ("refresh_inf", "invalid_refresh_frequency", 0),
    ("refresh_unwritten", "invalid_refresh_frequency", 0),
])
def test_failed_native_measurement_retains_status_without_exposure_retry(fixture, stage, code):
    result, _ = drive(frontend(device(fixture)))
    assert result.status == "failed" and result.patches == ()
    assert (result.error_stage, result.error_native_code) == (stage, code)
    if stage == "invalid_refresh_frequency":
        assert "invalid frequency" in dict(result.configuration)["error_native_text"]
    if fixture == "refresh_needs_cal":
        assert result.calibration.status == "required"
    # The native fixture asserts exactly one read and no trigger setter.


@pytest.mark.parametrize("fixture", ["refresh_stored_only", "refresh_withdrawn"])
def test_configured_measurement_capability_is_required(fixture):
    with pytest.raises(InstrumentError) as failed:
        device(fixture).measure_refresh(acknowledge=lambda prompt: "acknowledge")
    assert failed.value.stage == "facilities_changed"
    assert not any(e["event"] == "refresh_frequency" for e in failed.value.events)


def test_refresh_record_refinement_retains_native_byte_serial_encoding():
    result, _ = drive(frontend(device("invalid_serial_bytes")))
    assert result.status == "completed" and len(result.patches) == 1
    identity = result.patches[0].observation.device
    assert (identity.serial, identity.serial_encoding) == ("í\u00a0\u0080", "bytes")
    assert result_from_json(result_to_json(result)) == result
    assert from_mat(to_mat(result)) == result


def test_scalar_passes_public_polled_boundary_and_can_cancel_stimulus():
    result, events = drive(frontend(device("refresh_no_trigger")))
    assert result.status == "completed"
    assert result.patches[0].observation.backend_reported.value == 59.94005994005994
    prompts = [e["payload"] for e in events if e["kind"] == "instruction_required"]
    assert prompts[-1]["kind"] == "refresh_stimulus"
    assert LAYOUT["stimulus_id"] in prompts[-1]["message"]
    assert len([e for e in events if e["kind"] == "patch_reference"]) == 1
    assert result_from_json(result_to_json(result)) == result
    cancelled, _ = drive(frontend(device("refresh_no_trigger")), cancel=True)
    assert cancelled.status == "cancelled" and cancelled.patches == ()


def test_colour_configuration_does_not_leak_into_scalar_measurement():
    for configuration in (Configuration(highres=True), Configuration(averages=2)):
        with pytest.raises(ValueError, match="display-type"):
            Instrument("/must-not-open", "instI1Disp3").measure_refresh(
                configuration=configuration)


def test_scalar_offer_and_native_request_agree_on_emissive_spot_geometry():
    from color_measurement.argyll_capability import MODE_ROUTES

    caps = (MODE_ROUTES["emis_spot"] | MODE_ROUTES["emis_tele"]
            | MODE_ROUTES["emis_ambient"] | MODE_ROUTES["emis_ambient_flash"])
    front = InstrumentFrontend(Instrument("/must-not-open", "instI1Disp3"),
        mode_capabilities=caps, trigger_capabilities=CAPABILITY2["emis_refr_meas"],
        filter_capabilities=0, provenance="simulated")
    for mode in json.loads(front.describe_json())["modes"]:
        offered = any(w["workflow"] == "refresh_frequency" for w in mode["workflows"])
        assert offered is mode["mode"].startswith(("emissive_spot", "emissive_tele"))
    for mode in ("ambient_spot", "ambient_flash", "emissive_strip"):
        with pytest.raises(ValueError, match="emissive spot"):
            Instrument("/must-not-open", "instI1Disp3").measure_refresh(mode)


@pytest.mark.parametrize("interrupt", ["cancelled", "timeout"])
def test_blocked_frequency_retains_real_stop_outcome(interrupt):
    stop = threading.Event()
    meter = replace(device("refresh_block"), cancel=stop,
                    timeout=0.15 if interrupt == "timeout" else 3)
    timer = None
    def acknowledge(prompt):
        nonlocal timer
        if interrupt == "cancelled" and prompt["instruction"] == "refresh_stimulus":
            timer = threading.Timer(0.1, stop.set)
            timer.start()
        return "acknowledge"
    try:
        with pytest.raises(InstrumentError) as failed:
            meter.measure_refresh(acknowledge=acknowledge)
        assert failed.value.stage == interrupt
        assert failed.value.worker_stop == "exited"
        assert not any(e["event"] == "refresh_frequency" for e in failed.value.events)
    finally:
        if timer is not None:
            timer.cancel()


def test_demo_scalar_is_explicitly_simulated_and_spectrum_free():
    front = next(f for f in demo_frontends() if any(
        w["workflow"] == "refresh_frequency" and w["available"]
        for m in json.loads(f.describe_json())["modes"] if m["mode"] == "emissive_spot"
        for w in m["workflows"]))
    result, _ = drive(front)
    assert result.status == "completed", result.error
    observation = result.patches[0].observation
    assert observation.provenance == "simulated"
    assert observation.backend_reported.value == 59.94
    assert observation.stimulus.stimulus_id == LAYOUT["stimulus_id"]
    assert not hasattr(observation, "project_computed")


def test_late_scalar_survives_a_host_timeout_in_common_operation(monkeypatch):
    import color_measurement.instrument as module

    capture = json.loads((Path(__file__).parent /
        "fixtures/generic_frontend/i1_display_3/refresh_success.json").read_text())
    raw = capture["result"]["patches"][0]["observation"]["raw_backend_output"]
    events = json.loads(raw)["events"]
    # Instrument folds preceding display rows into connected for retention;
    # restore the native frame shape before passing it through the real parser.
    next(e for e in events if e["event"] == "connected")["display_types"].pop("entries")
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.now))

    class Session:
        stopped = "exited"
        def receive_message(self, **kwargs):
            event = events.pop(0)
            if event["event"] == "calibration_ready":
                clock.now = 4.0  # drive() gives the common operation three seconds.
            return event
        def send(self, *args):
            pass
        def finish_messages(self, *, expected_returncode):
            assert expected_returncode == 0  # Device completed; the host expired.
        def close(self):
            pass

    monkeypatch.setattr(Instrument, "_open_session", lambda *args: Session())
    result, _ = drive(frontend())
    assert result.status == "failed" and result.error_stage == "timeout"
    assert len(result.patches) == 1
    assert result.patches[0].observation.backend_reported.value == 59.94005994005994
    stopped = dict(result.configuration)
    assert stopped["device_completion"] == "completed"
    assert stopped["stop_acknowledged_by"] == "none"
    assert stopped["worker_stop"] == "exited"
