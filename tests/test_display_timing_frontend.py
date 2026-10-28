"""Controlled timing exchange and retention, using substituted workers only."""

import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import supplied_stub

from color_measurement.argyll_capability import CAPABILITY2, mode_word
from color_measurement.instrument import Instrument, InstrumentError
from color_measurement.instrument_frontend import InstrumentFrontend
from color_measurement.models import (
    CalibrationContext,
    DeviceIdentity,
    MeasuredTiming,
    PresentationStamp,
    TimingContext,
    TimingObservation,
    TimingStimulus,
)
from color_measurement.operation_exports import OperationSaveError, from_mat, load, save, to_mat
from color_measurement.operation_records import (
    OperationResult,
    PatchResult,
    result_from_json,
    result_to_json,
)

LAYOUT = {"stimulus_id": "black-white-1", "stimulus_description": "Controlled transition",
          "display_id": "display-1"}


def stamp(phase, seconds):
    return PresentationStamp(phase, LAYOUT["stimulus_id"], LAYOUT["display_id"],
                             "client-clock", seconds)


def frontend(meter=None, capability=CAPABILITY2["meas_disp_update"]):
    return InstrumentFrontend(meter or Instrument("/never-open", "instI1Disp3"),
        mode_capabilities=mode_word("emissive_spot", spectral=False),
        trigger_capabilities=capability, filter_capabilities=0, provenance="simulated")


def request(front, **changes):
    data = json.loads(front.describe_json())
    return json.dumps({"schema_version": 1, "device_id": data["device_id"],
        "capability_id": data["capability_id"], "mode": "emissive_spot",
        "workflow": "display_timing", "patch_ids": ["TIMING-1"], "options": {},
        "layout": LAYOUT, **changes})


def timing_result():
    front = frontend()
    device = DeviceIdentity("timing-device", "fixture", "test", "1")
    calibration = CalibrationContext("not_applicable", "Software-only timing")
    reading = TimingObservation(1, "timing-1", "operation-1", "TIMING-1",
        "2026-09-30T12:00:00+00:00", "simulated", device, "emissive_spot", "emissive_spot",
        (), calibration, MeasuredTiming(0, -230),
        TimingStimulus(LAYOUT["stimulus_id"], LAYOUT["stimulus_description"], LAYOUT["display_id"]),
        TimingContext((stamp("black", 50.0), stamp("white", 51.0)),
                      "worker_monotonic", 10.0, 10.2, 10.21, 10.5), "native trace")
    raw = json.loads(request(front))
    raw["device_id"] = device.device_id
    return OperationResult(1, "operation-1", device.device_id, device, "simulated",
        "emissive_spot", "display_timing", "completed", ("TIMING-1",),
        (PatchResult(0, "TIMING-1", reading),), json.dumps(raw), (), calibration, None, True,
        "not_applicable")


def test_zero_delay_signed_reaction_and_disjoint_clock_epochs_roundtrip():
    original = timing_result()
    text = result_to_json(original)
    assert result_from_json(text) == original
    record = json.loads(text)["patches"][0]["observation"]
    assert record["record_kind"] == "timing"
    assert record["backend_reported"]["display_update_delay_ms"] == 0
    assert record["backend_reported"]["instrument_reaction_ms"] == -230
    assert not set(record) & {"project_computed", "XYZ", "spectrum", "observer", "illuminant"}
    assert record["timing_context"]["presentations"][0]["presented_at_seconds"] == 50.0


def test_zero_delay_timing_result_roundtrips_through_mat(mat):
    original = timing_result()
    assert from_mat(to_mat(original)) == original


@pytest.mark.parametrize("format", ["json", "mat"])
def test_timing_save_failure_retains_exact_result_for_retry(format, tmp_path, monkeypatch,
                                                            request):
    if format == "mat":
        request.getfixturevalue("mat")
    original = timing_result()
    path = tmp_path / f"timing.{format}"
    publish = os.link
    def blocked(*args):
        raise OSError("publication refused")
    monkeypatch.setattr(os, "link", blocked)
    with pytest.raises(OperationSaveError) as failed:
        save(original, path, format=format)
    assert failed.value.result == original
    assert not path.exists()
    monkeypatch.setattr(os, "link", publish)
    save(failed.value.result, path, format=format)
    assert load(path, format=format) == original


def test_native_timing_delivered_before_late_timeout_remains_retained(monkeypatch):
    import color_measurement.instrument as module

    capture = json.loads((Path(__file__).parent /
        "fixtures/generic_frontend/i1_display_3/timing_success.json").read_text())
    raw = capture["result"]["patches"][0]["observation"]["raw_backend_output"]
    events = json.loads(raw)["events"]
    next(e for e in events if e["event"] == "connected")["display_types"].pop("entries")
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.now,
        sleep=lambda seconds: setattr(clock, "now", clock.now+seconds)))

    class Session:
        stopped = "exited"
        def receive_message(self, **kwargs):
            event = events.pop(0)
            if event["event"] == "display_timing":
                clock.now = 4.0
            return event
        def send(self, *args):
            pass
        def finish_messages(self, *, expected_returncode):
            assert expected_returncode == 0
        def close(self):
            pass

    monkeypatch.setattr(Instrument, "_open_session", lambda *args: Session())
    meter = Instrument("/never-open", "instI1Disp3", timeout=3)
    with pytest.raises(InstrumentError) as failed:
        meter.measure_display_timing(presenter=lambda e: stamp(e["phase"],
            50 if e["phase"] == "black" else 51))
    assert failed.value.stage == "timeout"
    retained = failed.value.measurement
    assert retained.display_timing == MeasuredTiming(37, -230)
    assert retained.worker_stop == "exited"


@pytest.mark.parametrize("origin,error_kind,worker_ended,host_timeout,cancel_closed", [
    ("write", "broken_pipe", "with_its_own_error", False, False),
    ("write", "broken_pipe", "completed", False, False),
    ("write", "windows_einval", "with_its_own_error", False, False),
    ("write", "windows_einval", "completed", False, False),
    ("presenter", "broken_pipe", "with_its_own_error", False, False),
    ("presenter", "windows_einval", "with_its_own_error", False, False),
    ("acknowledge", "broken_pipe", "with_its_own_error", False, False),
    ("acknowledge", "windows_einval", "with_its_own_error", False, False),
    ("write", "broken_pipe", "with_its_own_error", True, False),
    ("write", "windows_einval", "with_its_own_error", True, False),
    ("presenter", "broken_pipe", "with_its_own_error", False, True),
    ("presenter", "windows_einval", "with_its_own_error", False, True),
], ids=["write-native", "write-completed", "windows-write-native", "windows-write-completed",
        "presenter-broken-pipe", "presenter-windows-einval", "acknowledge-broken-pipe",
        "acknowledge-windows-einval", "write-native-timeout", "windows-write-native-timeout",
        "presenter-cancel-broken-pipe", "presenter-cancel-windows-einval"])
def test_a_reply_into_a_closed_pipe_reports_the_workers_own_ending(
        monkeypatch, origin, error_kind, worker_ended, host_timeout, cancel_closed):
    """The worker closes its pipe the moment it fails a phase (display_timing.h
    gives the white reply a 0.2–2.0 s window); a client reply that arrives after
    that raises BrokenPipeError on write. The worker's error event is still in the
    stream and names the stage and native code. That stage is the outcome; the
    pipe error is how the ending looked from the client and stays attached as the
    cause. When the worker completed instead, there is no worker reason to prefer
    and the pipe error is still the error. An identically typed callback failure
    must retain the callback's exact exception object; only an actual pipe write
    can prefer the native ending. A host timeout retains that ending's facts and
    pipe cause. Scripted white_change is not an identified cause of the historical
    MATLAB timing_success BrokenPipe.
    """
    import color_measurement.instrument as module

    capture = json.loads((Path(__file__).parent /
        "fixtures/generic_frontend/i1_display_3/timing_success.json").read_text())
    raw = capture["result"]["patches"][0]["observation"]["raw_backend_output"]
    events = json.loads(raw)["events"]
    next(e for e in events if e["event"] == "connected")["display_types"].pop("entries")
    white = next(i for i, e in enumerate(events)
                 if e["event"] == "presentation_required" and e["phase"] == "white")
    envelope = {"protocol_version": 1, "provenance": "simulated"}
    if worker_ended == "with_its_own_error":
        events[white + 1:] = [
            dict(envelope, event="error", stage="white_change", native_code=458752,
                 native_text=None, native_text_truncated=False),
            dict(envelope, event="terminal", status="failed", instrument_state="unknown",
                 software_stopped=True),
        ]
    if origin == "acknowledge":
        events[white:] = [dict(envelope, event="instruction_required",
            instruction="read_sample", request_id="read-ack"), *events[white + 1:]]
    error = (BrokenPipeError(32, "Broken pipe") if error_kind == "broken_pipe"
             else OSError(22, "Invalid argument"))
    cancel_error = BrokenPipeError(32, "closed during callback cleanup")
    if error_kind == "windows_einval":
        from color_measurement import native_protocol
        # Exercise the real Windows closed-pipe classifier, without changing
        # process-wide sys.platform or opening a real helper.
        monkeypatch.setattr(native_protocol, "sys", SimpleNamespace(platform="win32"))
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.now,
        sleep=lambda seconds: setattr(clock, "now", clock.now + seconds)))
    sent = []
    closed = []

    class Session:
        stopped = "exited"

        def receive_message(self, **kwargs):
            return events.pop(0)

        def send(self, action, argument=""):
            sent.append((action, argument))
            if cancel_closed and action == "cancel":
                raise cancel_error
            if origin == "write" and action == "presented" and argument == "timing-white":
                if host_timeout:
                    clock.now = 4.0  # Expire the host deadline after the actual failed write.
                raise error

        def finish_messages(self, *, expected_returncode):
            assert expected_returncode == (1 if worker_ended == "with_its_own_error" else 0)

        def close(self):
            closed.append(True)

    monkeypatch.setattr(Instrument, "_open_session", lambda *args: Session())
    meter = Instrument("/never-open", "instI1Disp3", timeout=3)

    def presenter(event):
        if origin == "presenter" and event["phase"] == "white":
            raise error
        return stamp(event["phase"], 50 if event["phase"] == "black" else 51)

    def acknowledge(event):
        assert event["request_id"] == "read-ack"
        raise error

    if origin == "write" and worker_ended == "with_its_own_error":
        with pytest.raises(InstrumentError) as failed:
            meter.measure_display_timing(presenter=presenter, acknowledge=acknowledge)
        stage = "timeout" if host_timeout else "white_change"
        assert (failed.value.stage, failed.value.native_code) == (stage, 458752)
        assert failed.value.__cause__ is error
        assert failed.value.worker_stop == "exited"
        if host_timeout:
            assert failed.value.stop_requested_by == "host_deadline"
            assert failed.value.raised_by == "supervisor"
        assert [e["event"] for e in failed.value.events][-2:] == ["error", "terminal"]
    else:
        with pytest.raises(type(error)) as failed:
            meter.measure_display_timing(presenter=presenter, acknowledge=acknowledge)
        assert failed.value is error
        assert failed.value.__cause__ is (cancel_error if cancel_closed else None)
    # Callbacks and transport failures still request cancellation and close the session.
    assert sent[-1] == ("cancel", "")
    assert closed


@pytest.mark.parametrize("fault,refusal", [
    ("phase", "one declared client clock"), ("clock", "one declared client clock"),
    ("order", "one declared client clock"), ("equal", "one declared client clock"),
    ("identity", "one declared client clock"), ("stimulus", "one declared client clock"),
    ("early", "Invalid native timing window"), ("late", "Invalid native timing window"),
    ("late_ordered", "Invalid native timing window"),
])
def test_timing_context_refuses_wrong_phase_clock_identity_and_native_window(fault, refusal):
    ctx = timing_result().patches[0].observation.timing_context
    if fault == "phase":
        changes = {"presentations": (stamp("white", 50), stamp("white", 51))}
    elif fault == "clock":
        changes = {"presentations": (stamp("black", 50),
            replace(stamp("white", 51), clock_id="different"))}
    elif fault == "order":
        changes = {"presentations": (stamp("black", 51), stamp("white", 50))}
    elif fault == "equal":
        changes = {"presentations": (stamp("black", 50), stamp("white", 50))}
    elif fault == "stimulus":
        changes = {"presentations": (stamp("black", 50),
            replace(stamp("white", 51), stimulus_id="different"))}
    elif fault == "identity":
        changes = {"presentations": (stamp("black", 50),
            replace(stamp("white", 51), display_id="different"))}
    elif fault == "early":
        changes = {"marker_call_started_seconds": 10.19}
    elif fault == "late_ordered":
        changes = {"marker_call_finished_seconds": 12.01,
                   "method_window_finished_seconds": 12.02}
    else:
        changes = {"method_window_finished_seconds": 10.20}
    with pytest.raises(ValueError, match=refusal):
        replace(ctx, **changes)


def test_timing_request_and_result_cannot_be_relabelled_as_refresh_or_colour():
    result = timing_result()
    for workflow, refusal in (("refresh_frequency", "requires one requested scalar record"),
                              ("spot", "non-photometric observation cannot enter")):
        raw = json.loads(result.request_json)
        raw["workflow"] = workflow
        with pytest.raises(ValueError, match=refusal):
            replace(result, workflow=workflow, request_json=json.dumps(raw))
    raw = json.loads(result.request_json)
    raw["layout"]["display_id"] = "different"
    with pytest.raises(ValueError, match="stimulus"):
        replace(result, request_json=json.dumps(raw))


@pytest.mark.parametrize("capability,offered", [
    (CAPABILITY2["meas_disp_update"], True),
    (CAPABILITY2["emis_refr_meas"], False),
    (CAPABILITY2["get_refresh_rate"], False),
    (CAPABILITY2["set_refresh_rate"], False),
])
def test_timing_advertisement_uses_its_own_capability(capability, offered):
    modes = json.loads(frontend(capability=capability).describe_json())["modes"]
    mode = next(m for m in modes if m["mode"] == "emissive_spot")
    assert any(w["workflow"] == "display_timing" for w in mode["workflows"]) == offered


def device(fixture):
    return Instrument(Path(supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")), "instI1Disp3",
        environment={**os.environ, "CM_COLORIMETER_FIXTURE": fixture},
               expected_helper_sha256=hashlib.sha256(
                   Path(supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")).read_bytes()).hexdigest())


@pytest.mark.parametrize("fixture,delay,reaction", [
    ("timing_zero", 0, -230), ("timing_success", 37, -230), ("timing_positive", 37, 12),
    ("timing_no_trigger", 37, -230),
])
def test_common_native_method_preserves_timing_and_context(fixture, delay, reaction):
    def present(event):
        if event["phase"] == "white":
            time.sleep(.22)
        return stamp(event["phase"], time.monotonic())

    result = device(fixture).measure_display_timing(presenter=present)
    assert result.patches == () and result.workflow == "display_timing"
    assert result.display_timing == MeasuredTiming(delay, reaction)
    assert [e["phase"] for e in result.events if e["event"] == "presentation_required"] == [
        "black", "white"]


@pytest.mark.parametrize("persona,instrument", [
    (None, "instI1Disp3"), ("i1pro3", "instI1Pro3"),
])
def test_synthetic_capture_stays_open_for_valid_late_white(persona, instrument):
    """Both fake meters must cover the native method's two-second white window."""
    if persona is None:
        meter = device("timing_success")
    else:
        worker = Path(supplied_stub("COLOR_MEASUREMENT_CHART_STUB"))
        meter = Instrument(worker, instrument,
            environment={**os.environ, "CM_CHART_PERSONA": persona,
                         "CM_CHART_FIXTURE": "success"},
            expected_helper_sha256=hashlib.sha256(worker.read_bytes()).hexdigest())

    def present(event):
        if event["phase"] == "white":
            time.sleep(1.5)
        return stamp(event["phase"], time.monotonic())

    result = meter.measure_display_timing(presenter=present, calibrate=False)
    assert result.display_timing == MeasuredTiming(37, -230)
    assert result.worker_stop == "exited"
    assert result.timing_context is not None
    window = result.timing_context
    assert 1.5 < (window.marker_call_started_seconds
                  - window.method_window_started_seconds) < 2.0
    assert (window.method_window_finished_seconds
            - window.method_window_started_seconds) >= 2.0


@pytest.mark.parametrize("fixture,stage", [
    ("timing_withdrawn", "facilities_changed"),
    ("timing_init_error", "white_change_init"), ("timing_marker_error", "white_change"),
    ("timing_error", "meas_delay"), ("timing_unwritten", "invalid_display_timing"),
    ("timing_negative", "invalid_display_timing"),
    ("timing_reaction_unwritten", "invalid_display_timing"),
])
def test_native_failures_never_publish_default_timing(fixture, stage):
    def present(event):
        if event["phase"] == "white":
            time.sleep(.22)
        return stamp(event["phase"], time.monotonic())

    with pytest.raises(InstrumentError) as failed:
        device(fixture).measure_display_timing(presenter=present)
    assert failed.value.stage == stage
    assert not any(e["event"] == "display_timing" for e in failed.value.events)


def test_polled_presented_is_typed_bounded_and_stale_response_is_refused():
    front = frontend(device("timing_success"))
    operation = front.prepare(request(front), timeout=4)
    events = []
    try:
        operation.dispatch()
        end = time.monotonic()+8
        while not operation.done:
            while raw := operation.poll_event_json():
                event = json.loads(raw)
                events.append(event)
                if event["kind"] == "instruction_required":
                    operation.respond(operation.operation_id, event["payload"]["request_id"],
                                      "accept")
                if event["kind"] == "presentation_required":
                    payload = event["payload"]
                    # The phase is in the pending identity tuple, so only the pending
                    # presentation itself can refuse this operator response.
                    with pytest.raises(ValueError, match="does not match the pending"):
                        operation.respond(operation.operation_id, payload["request_id"],
                                          payload["phase"])
                    assert payload["minimum_delay_ms"] == 0
                    assert payload["native_minimum_delay_ms"] == (
                        200 if payload["phase"] == "white" else 0)
                    body = json.dumps(asdict(stamp(payload["phase"], time.monotonic())))
                    operation.presented(operation.operation_id, payload["request_id"], body)
                    with pytest.raises(ValueError, match="does not match the pending"):
                        operation.presented(operation.operation_id, payload["request_id"], body)
            assert time.monotonic() < end
            time.sleep(.002)
        result = result_from_json(operation.result_json())
        assert result.status == "completed"
        assert result.patches[0].observation.record_kind == "timing"
    finally:
        operation.close()


def test_cancel_during_presentation_uses_common_stop_without_a_measurement():
    cancel = threading.Event()
    def present(event):
        cancel.set()
        raise InterruptedError("Surface closed")

    with pytest.raises(InstrumentError) as failed:
        replace(device("timing_success"), cancel=cancel).measure_display_timing(presenter=present)
    assert failed.value.stage == "cancelled"
    assert not any(e["event"] == "display_timing" for e in failed.value.events)


@pytest.mark.parametrize("phase,reply,stage", [
    ("black", "wrong", "timing_protocol"),
    ("black", "missing", "timing_presentation_timeout"),
    ("white", "wrong", "timing_protocol"),
    ("white", "early", "timing_protocol"),
    ("white", "duplicate", "timing_protocol"),
    ("white", "missing", "timing_presentation_timeout"),
    ("white", "cancel", "cancelled"),
])
def test_native_exchange_rejects_missing_early_duplicate_wrong_and_cancel(phase, reply, stage):
    meter = device("timing_wait")
    seen = []
    with meter._open_session("emissive_spot", False, ["meas_disp_update"],
                             {"read_timing": 1}) as session:
        session.operation_id = "timing-probe"
        while True:
            event = session.receive_message(timeout=4)
            seen.append(event)
            if event["event"] == "terminal":
                session.finish_messages(expected_returncode=1)
                break
            if event["event"] != "presentation_required":
                continue
            current = event["phase"]
            if current != phase:
                session.send("presented", event["request_id"])
            elif reply != "missing":
                if current == "white" and reply != "early":
                    time.sleep(.22)
                if reply == "cancel":
                    session.send("cancel")
                else:
                    session.send("presented", "wrong" if reply == "wrong"
                                 else event["request_id"])
                    if reply == "duplicate":
                        session.send("presented", event["request_id"])
    assert next(e["stage"] for e in seen if e["event"] == "error") == stage
    assert not any(e["event"] == "display_timing" for e in seen)
    assert session.stopped == "exited"


@pytest.mark.parametrize("persona,instrument", [
    ("i1pro2", "instI1Pro2"), ("i1pro3", "instI1Pro3"), ("munki", "instColorMunki"),
    ("klein_k10", "instKleinK10"),
])
def test_other_source_families_use_same_ladder_and_post_mode_capability(persona, instrument):
    meter = Instrument(Path(supplied_stub("COLOR_MEASUREMENT_CHART_STUB")), instrument,
        environment={**os.environ, "CM_CHART_PERSONA": persona, "CM_CHART_FIXTURE": "success"},
                expected_helper_sha256=hashlib.sha256(
                    Path(supplied_stub("COLOR_MEASUREMENT_CHART_STUB")).read_bytes()).hexdigest())
    def present(event):
        if event["phase"] == "white":
            time.sleep(.22)
        return stamp(event["phase"], time.monotonic())

    result = meter.measure_display_timing(presenter=present, calibrate=False)
    assert result.display_timing == MeasuredTiming(37, -230)
    assert result.patches == ()
    assert result.worker_stop == "exited"
