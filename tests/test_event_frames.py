"""UTF-8 event limits preserve valid Unicode values without increasing the cap."""

import json
import time
from queue import SimpleQueue
from types import SimpleNamespace

import pytest

from color_measurement import demo, instrument
from color_measurement.frontend import MAX_EVENT_BYTES, _PolledOperation
from color_measurement.operation_records import result_from_json


@pytest.mark.parametrize(
    "text", ["x" * MAX_EVENT_BYTES, "💡" * (MAX_EVENT_BYTES // 4)], ids=["ascii", "utf8"]
)
def test_event_limit_still_refuses_actual_utf8_oversize(text):
    sink = SimpleNamespace(operation_id="bounded-event", _sequence=0, _events=SimpleQueue())
    with pytest.raises(ValueError, match="bounded frame size"):
        _PolledOperation._emit(sink, "error", {"message": text})
    assert sink._sequence == 0 and sink._events.empty()


def test_compact_utf8_fallback_preserves_a_frame_that_ascii_escaping_oversizes():
    sink = SimpleNamespace(operation_id="bounded-event", _sequence=0, _events=SimpleQueue())
    payload = {"message": "💡" * 3000}
    expected = {"schema_version": 1, "operation_id": sink.operation_id,
                "sequence": 0, "kind": "error", "payload": payload}
    ascii_frame = json.dumps(expected, allow_nan=False, separators=(",", ":"))
    utf8_frame = json.dumps(expected, ensure_ascii=False, allow_nan=False,
                            separators=(",", ":"))
    assert len(ascii_frame.encode("utf-8")) > MAX_EVENT_BYTES
    assert len(utf8_frame.encode("utf-8")) <= MAX_EVENT_BYTES
    _PolledOperation._emit(sink, "error", payload)
    emitted = sink._events.get_nowait()
    assert emitted == utf8_frame and json.loads(emitted) == expected
    assert sink._sequence == 1 and sink._events.empty()


def test_a_maximum_length_patch_id_still_notifies_through_a_real_operation(monkeypatch):
    """Maximum-length ASCII patch identifier traverses the current Demo frontend.

    UTF-8 fallback is tested separately.
    """
    monkeypatch.setattr(instrument, "HelperSession",
                        lambda *a, **kw: pytest.fail("Demo attempted native I/O"))
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "i1pro3")
    snapshot = json.loads(front.describe_json())
    device = snapshot["device_id"]
    request = {
        "schema_version": 1, "device_id": device,
        "capability_id": snapshot["capability_id"], "event_contract": "patch_v1",
        "mode": "reflective_spot", "workflow": "spot",
        # Exercise the longest patch identifier the contract permits.
        "patch_ids": ["x" * 128], "options": {}, "layout": {},
    }
    operation = front.prepare(json.dumps(request))
    operation.dispatch()
    events = []
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            text = operation.poll_event_json()
            if not text:
                time.sleep(.001)
                continue
            assert len(text.encode("utf-8")) <= MAX_EVENT_BYTES
            event = json.loads(text)
            assert event["sequence"] == len(events)
            events.append(event)
            if event["kind"] == "instruction_required":
                operation.respond(operation.operation_id, event["payload"]["request_id"], "accept")
            if event["kind"] == "terminal":
                break
        assert operation.done
        result = result_from_json(operation.result_json())
        assert result.status == "completed", result.error
        patches = [event for event in events if event["kind"] == "patch"]
        assert len(patches) == len(result.patches) == 1
        assert result.patches[0].patch_id == "x" * 128
    finally:
        operation.close()
