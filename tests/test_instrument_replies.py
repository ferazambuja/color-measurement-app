"""Reply transport failures preserve caller identity and queued native endings.

Only the transport and clock are scripted. The public spot, strip and sheet operations
still validate the native events and compute their own operator replies.
"""

import errno
from contextlib import ExitStack
from types import SimpleNamespace

import pytest
from conftest import native_display_unsupported

from color_measurement.argyll_capability import mode_word
from color_measurement.instrument import Instrument, InstrumentError
from color_measurement.native_protocol import ProtocolError


@pytest.mark.parametrize("action,ending", [
    (action, ending)
    for ending in ("native_error", "completed", "host_timeout")
    for action in ("acknowledge", "skip", "cancel", "navigate", "read_allowance")
    if not (action == "cancel" and ending == "host_timeout")
])
@pytest.mark.parametrize("error_kind", ["broken_pipe", "windows_einval", "other_oserror"])
def test_reply_failure_retains_transport_or_native_ending(
        action, error_kind, ending, monkeypatch):
    """Every reply boundary drains a closed pipe's already-queued ending.

    An unrelated I/O error is raised immediately. A completed worker cannot
    replace a failed write with a success, and a host timeout retains the native
    diagnostic and the actual failed write as separate facts.
    """
    error = _error(error_kind, monkeypatch)
    session, meter, invoke = _session(action, error, ending, monkeypatch)
    with pytest.raises(Exception) as failed:
        invoke(meter)
    value = failed.value
    if error_kind == "other_oserror" or ending == "completed":
        assert value is error
        assert value.__cause__ is None
    else:
        assert isinstance(value, InstrumentError)
        assert value.stage == ("timeout" if ending == "host_timeout" else "read_sample")
        assert value.native_code == 458752
        assert value.native_text == "queued driver ending"
        assert value.native_text_truncated is True
        assert value.__cause__ is error
        assert value.worker_stop == "exited"
        assert [e["event"] for e in value.events][-2:] == ["error", "terminal"]
        assert value.stop_requested_by == (
            "host_deadline" if ending == "host_timeout" else
            "client" if action == "cancel" else None)
    assert session.closed
    assert session.finished == (error_kind != "other_oserror")
    assert action in [command[0] for command in session.sent]


@pytest.mark.parametrize("callback_kind", ["runtime", "broken_pipe", "windows_einval"])
@pytest.mark.parametrize("cancel_error_kind", ["broken_pipe", "windows_einval", "other_oserror"])
def test_failed_callback_keeps_identity_when_cancel_write_also_fails(
        callback_kind, cancel_error_kind, monkeypatch):
    """Identical exception types do not make a callback into a transport error."""
    callback = (RuntimeError("operator UI failed") if callback_kind == "runtime"
                else _error(callback_kind, monkeypatch))
    transport = _error(cancel_error_kind, monkeypatch)
    session, meter, invoke = _session("cancel", transport, "native_error", monkeypatch)

    def acknowledge(event):
        raise callback

    with pytest.raises(type(callback)) as failed:
        invoke(meter, acknowledge=acknowledge)
    assert failed.value is callback
    assert failed.value.__cause__ is transport
    assert session.closed
    assert session.finished == (cancel_error_kind != "other_oserror")
    if session.finished:
        assert [e["event"] for e in callback.events][-2:] == ["error", "terminal"]


@pytest.mark.parametrize("callback_kind", ["runtime", "broken_pipe", "windows_einval"])
@pytest.mark.parametrize("close_failure", [False, True])
def test_unanswered_calibration_condition_preserves_callback_identity(
        callback_kind, close_failure, monkeypatch):
    callback = (RuntimeError("operator UI failed") if callback_kind == "runtime"
                else _error(callback_kind, monkeypatch))
    session, meter, invoke = _session("calibration_condition", None, "native_error", monkeypatch)
    close_error = RuntimeError("session close failed")
    if close_failure:
        def close():
            session.closed = True
            raise close_error
        session.close = close

    def acknowledge(event):
        raise callback

    with pytest.raises(Exception) as failed:
        invoke(meter, acknowledge=acknowledge)
    assert failed.value is callback
    assert callback.__cause__ is (close_error if close_failure else None)
    assert session.closed and not session.finished
    assert not session.sent  # A condition that takes no answer must never get one.


@pytest.mark.parametrize("ending", ["eof", "grace", "invalid_event", "decode", "finish", "close"])
@pytest.mark.parametrize("callback_kind", [None, "runtime", "broken_pipe", "windows_einval"])
@pytest.mark.parametrize("write_kind", ["broken_pipe", "windows_einval"])
def test_incomplete_ending_keeps_callback_and_all_cleanup_diagnostics(
        ending, callback_kind, write_kind, monkeypatch):
    """A failed drain is not permission to replace an earlier callback error.

    Without a callback, keep the protocol/deadline/cleanup refusal and its failed
    write. With a callback, keep that exact object and both ending diagnostics.
    Resource cleanup still runs when closing the session itself fails.
    """
    import color_measurement.instrument as module

    callback = (None if callback_kind is None else RuntimeError("operator UI failed")
                if callback_kind == "runtime" else _error(callback_kind, monkeypatch))
    transport = _error(write_kind, monkeypatch)
    session, meter, invoke = _session("acknowledge" if callback is None else "cancel",
                                    transport, "native_error", monkeypatch)
    drain = (RuntimeError("session close failed") if ending == "close" else
             ProtocolError("Helper exited before the expected event"))
    decode = ValueError("malformed JSON")
    if ending == "decode":
        drain.__cause__ = decode
    if ending in {"eof", "grace", "decode"}:
        session.events[-2:] = []
        receive = session.receive_message

        def receive_message(**kwargs):
            if session.events:
                return receive(**kwargs)
            if ending == "grace":
                session.clock.now += .5
                raise TimeoutError("waiting for terminal")
            raise drain

        session.receive_message = receive_message
    elif ending == "invalid_event":
        session.events[-2]["event"] = "not_a_native_event"
    elif ending == "finish":
        finish = session.finish_messages

        def finish_messages(**kwargs):
            finish(**kwargs)
            raise drain

        session.finish_messages = finish_messages
    else:
        def close():
            session.closed = True
            raise drain
        session.close = close
    resources_closed = []
    resources = ExitStack()
    resources.callback(lambda: resources_closed.append(True))
    monkeypatch.setattr(module, "ExitStack", lambda: resources)

    def acknowledge(event):
        if callback is not None:
            raise callback
        return "acknowledge"

    with pytest.raises(Exception) as failed:
        invoke(meter, acknowledge=acknowledge)
    value = failed.value
    if callback is not None:
        assert value is callback
        assert isinstance(value.__cause__, ExceptionGroup)
        assert value.__cause__.exceptions[0] is transport
        refused = value.__cause__.exceptions[1]
    else:
        refused = value
        if ending == "decode":
            assert isinstance(value.__cause__, ExceptionGroup)
            assert value.__cause__.exceptions == (decode, transport)
        else:
            assert value.__cause__ is transport
    if ending == "grace":
        assert isinstance(refused, InstrumentError)
        assert refused.stage == ("cancelled" if callback is not None else "timeout")
        assert refused.raised_by == "supervisor" and refused.worker_stop == "exited"
    elif ending == "invalid_event":
        assert isinstance(refused, ProtocolError)
        assert "Unknown helper event" in str(refused)
    else:
        assert refused is drain
        if ending == "decode" and callback is not None:
            assert refused.__cause__ is decode
    assert session.closed and resources_closed == [True]
    assert session.finished == (ending in {"finish", "close"})
    assert value.events  # The failed operation's partial trace remains available.


@pytest.mark.parametrize("direction", ["forward", "reverse"])
@pytest.mark.parametrize("ending", [
    "completed", "native", "timeout", "cleanup", "native_cleanup", "control_group", "short",
])
def test_strip_order_survives_retained_failure(direction, ending, monkeypatch):
    """A complete strip has one declared order, including when its ending fails."""
    native_ending = ending in {"native", "native_cleanup", "short"}
    session, meter, _ = _session(
        "acknowledge", None, "native_error" if native_ending else "completed", monkeypatch)
    mode = "reflective_strip"
    connected = session.events[0]
    connected["mode"] = mode_word(mode, spectral=False)
    connected["baseline_mode_checks"] = {
        "scope": "initialized_before_mode_configuration",
        "mode_capabilities": connected["mode"],
        "checks": [{"mode_word": connected["mode"], "native_code": 0}],
    }
    # Reuse the scripted transport, replacing its spot prompt with native strip rows.
    session.events[1:2] = [
        {"protocol_version": 1, "provenance": "simulated", "event": "strip_patch",
         "index": index, "patch_count": 2, "backend_reported": {
             "mtype": 5, "mcond": 0, "XYZ_v": 1, "XYZ": [index + 1, 2, 3],
             "spectrum": None, "duration": 0}}
        for index in range(1 if ending == "short" else 2)
    ]
    native_events = tuple(session.events)
    if ending == "timeout":
        receive = session.receive_message

        def receive_message(**kwargs):
            event = receive(**kwargs)
            if event.get("index") == 0:
                session.clock.now = 4.0
            return event

        session.receive_message = receive_message
    cleanup = None
    if ending in {"cleanup", "native_cleanup", "control_group"}:
        cleanup = (BaseExceptionGroup("cleanup interrupted", [
            KeyboardInterrupt("stop"), RuntimeError("cleanup failed")])
            if ending == "control_group" else RuntimeError("cleanup failed"))

        def close():
            session.closed = True
            raise cleanup

        session.close = close

    if ending == "completed":
        measured = meter.measure_strip(mode, patch_count=2, spectral=False, direction=direction)
    else:
        with pytest.raises(BaseException) as failed:
            meter.measure_strip(mode, patch_count=2, spectral=False, direction=direction)
        failure = failed.value
        if cleanup is not None:
            assert failure is cleanup
        else:
            assert isinstance(failure, InstrumentError)
            assert failure.stage == ("timeout" if ending == "timeout" else "read_sample")
        if ending == "short":
            assert failure.measurement is None
            assert session.closed and session.finished
            return
        measured = failure.measurement
        if ending == "native_cleanup":
            assert failure.instrument_error.measurement is measured
            assert failure.instrument_error.stage == "read_sample"
        assert failure.worker_stop == "exited"
    assert measured.strip_direction == direction
    expected = [1, 0] if direction == "reverse" else [0, 1]
    assert [patch.index for patch in measured.patches] == expected
    assert [patch.xyz[0] for patch in measured.patches] == [index + 1 for index in expected]
    assert measured.events == native_events
    assert [event["index"] for event in measured.events
            if event["event"] == "strip_patch"] == [0, 1]
    assert session.closed and session.finished


def _error(kind, monkeypatch):
    if kind == "windows_einval":
        from color_measurement import native_protocol
        monkeypatch.setattr(native_protocol, "sys", SimpleNamespace(platform="win32"))
        return OSError(errno.EINVAL, "closed Windows stdin")
    if kind == "broken_pipe":
        return BrokenPipeError(errno.EPIPE, "closed stdin")
    return OSError(errno.EACCES, "unrelated write refusal")


def _session(action, error, ending, monkeypatch):
    import color_measurement.instrument as module

    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    envelope = {"protocol_version": 1, "provenance": "simulated"}
    mode = "reflective_xy" if action == "navigate" else "emissive_spot"
    events = [dict(envelope, event="connected", serial="TEST", serial_encoding="utf8",
                   spectral=False, display_types=native_display_unsupported(),
                   baseline_mode_checks={"scope": "initialized_before_mode_configuration",
                       "mode_capabilities": mode_word(mode, spectral=False),
                       "checks": [{"mode_word": mode_word(mode, spectral=False),
                                   "native_code": 0}]},
                   mode=mode_word(mode, spectral=False))]
    if action == "navigate":
        events.extend(dict(envelope, event="xy_location", x=x, y=y)
                      for x, y in ((0, 0), (0, 10), (20, 0)))
    if action == "read_allowance":
        events.append(dict(envelope, event="read_allowance_required"))
    else:
        events.append(dict(envelope, event=("calibration_condition" if
                           action == "calibration_condition" else "instruction_required"),
                           request_id="reply",
                           instruction="xy_navigate" if action == "navigate" else "read_sample",
                           skippable=action == "skip"))
    if ending != "completed":
        events.append(dict(envelope, event="error", stage="read_sample", native_code=458752,
                           native_text="queued driver ending", native_text_truncated=True))
    events.append(dict(envelope, event="terminal",
                       status="completed" if ending == "completed" else "failed"))

    class Session:
        stopped = "exited"
        finished = False
        closed = False

        def __init__(self):
            self.sent = []

        def receive_message(self, **kwargs):
            return events.pop(0)

        def send(self, command, argument=""):
            self.sent.append((command, argument))
            if command == action:
                if ending == "host_timeout":
                    clock.now = 4.0
                raise error

        def finish_messages(self, *, expected_returncode):
            assert not events
            assert expected_returncode == (0 if ending == "completed" else 1)
            self.finished = True

        def close(self):
            self.closed = True

    session = Session()
    session.events = events
    session.clock = clock
    monkeypatch.setattr(Instrument, "_open_session", lambda *args: session)
    meter = Instrument("/never-open", "instSpectroScan" if action == "navigate" else
                       "instI1Disp3", timeout=3)

    def invoke(meter, acknowledge=None):
        acknowledge = acknowledge or (lambda event: action)
        if action == "navigate":
            return meter.measure_sheet(mode, passes=3, steps=5, spectral=False,
                                       acknowledge=acknowledge)
        return meter.measure(mode, spectral=False, acknowledge=acknowledge)

    return session, meter, invoke
