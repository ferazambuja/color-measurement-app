"""Shared polling and finalization on the current Demo/InstrumentFrontend path.

No native process, transport discovery or physical instrument is permitted.
"""

import json
import sys
import threading
import time
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from color_measurement import demo, frontend, instrument
from color_measurement.exports import from_json, to_json
from color_measurement.frontend import MAX_EVENT_BYTES, MAX_REQUEST_BYTES
from color_measurement.instrument_frontend import InstrumentFrontend, InstrumentOperation
from color_measurement.operation_records import PatchResult, result_from_json
from color_measurement.operations import OperationBusyError


@pytest.fixture(autouse=True)
def no_native_transport(monkeypatch):
    monkeypatch.setattr(instrument, "HelperSession",
                        lambda *a, **kw: pytest.fail("Demo attempted native I/O"))


def request(front, *, workflow="strip", ids=None):
    snapshot = json.loads(front.describe_json())
    return {
        "schema_version": 1, "device_id": snapshot["device_id"],
        "capability_id": snapshot["capability_id"],
        "mode": "reflective_strip" if workflow == "strip" else "reflective_spot",
        "workflow": workflow,
        "patch_ids": (["A1", "A2", "A3"] if ids is None else ids),
        "options": {}, "layout": {}, "event_contract": "patch_v1",
    }


@pytest.fixture
def begin():
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "i1pro3")
    operations = []

    def create(data=None, **kwargs):
        operation = front.prepare(json.dumps(request(front) if data is None else data), **kwargs)
        operations.append(operation)
        operation.dispatch()
        return operation

    create.front = front
    yield create
    for operation in operations:
        operation.close()
        if operation._thread is not None and operation._thread.ident is not None:
            operation._thread.join(3)
            assert not operation._thread.is_alive()
        assert operation.done


def event_until(operation, stop=None, *, response="accept", respond_instructions=True):
    events = []
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        text = operation.poll_event_json()
        if not text:
            time.sleep(0.001)
            continue
        assert len(text.encode()) <= MAX_EVENT_BYTES
        event = json.loads(text)
        events.append(event)
        assert event["operation_id"] == operation.operation_id
        if stop is not None and stop(event):
            return events
        if event["kind"] == "instruction_required" and respond_instructions:
            operation.respond(operation.operation_id, event["payload"]["request_id"], response)
        if event["kind"] == "terminal":
            result_from_json(operation.result_json())
            return events
    pytest.fail("Demo operation failed to produce the requested event")


def test_client_contract_level_is_readable_without_loading_the_backend():
    """A client checks the level before it uses anything else, so reading it must
    not import a submodule, launch a worker or touch a device. The failure this
    catches is a convenience import added to ``__init__``, which would make the
    check load the backend it is supposed to vet."""
    import subprocess
    import sys

    probe = ("import sys, color_measurement as c; "
             "print(c.CLIENT_CONTRACT, type(c.CLIENT_CONTRACT).__name__); "
             "print(sorted(m for m in sys.modules if m.startswith('color_measurement')))")
    lines = subprocess.run([sys.executable, "-c", probe], check=True, capture_output=True,
                           text=True).stdout.splitlines()
    level, kind = lines[0].split()
    assert kind == "int" and int(level) >= 1  # bool is a subclass of int; name it exactly
    assert lines[1] == "['color_measurement']"


def test_fixture_capture_waits_for_terminal_when_done_polling_lock_is_busy(begin, monkeypatch):
    import importlib
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))
    generator = importlib.import_module("generate_frontend_fixtures")
    operation = begin.front.prepare(json.dumps(request(begin.front)))
    monkeypatch.setattr(begin.front, "prepare", lambda text: operation)
    monkeypatch.setattr(operation, "_ask", lambda *args, **kwargs: "accept")
    locked, release = threading.Event(), threading.Event()
    complete = operation._release_and_complete
    dispatch, poll, close = operation.dispatch, operation.poll_event_json, operation.close
    blocked_polls = []

    def hold_completed_lock():
        complete()
        locked.set()
        assert release.wait(3), "Fixture reader did not release the controlled completion"

    def dispatch_completed():
        dispatch()
        assert locked.wait(3), "Demo did not reach finalization"

    def poll_with_contention():
        if not release.is_set():
            assert operation.done
            condition_free = operation._condition.acquire(blocking=False)
            if condition_free:
                operation._condition.release()
            assert not condition_free, "Controlled producer must still hold finalization lock"
            blocked_polls.append(True)
            if len(blocked_polls) < 3:
                # Keep the reader's empty-poll schedule explicit. A shared
                # frontend may drain its stable final queue without this lock.
                return ""
            release.set()
            operation._thread.join(3)
            assert not operation._thread.is_alive()
        return poll()

    def release_and_close():
        release.set()
        close()
        operation._thread.join(3)
        assert not operation._thread.is_alive()

    monkeypatch.setattr(operation, "_release_and_complete", hold_completed_lock)
    monkeypatch.setattr(operation, "dispatch", dispatch_completed)
    monkeypatch.setattr(operation, "poll_event_json", poll_with_contention)
    monkeypatch.setattr(operation, "close", release_and_close)
    expected_descriptor = json.loads(begin.front.describe_json())
    result, events, descriptor = generator._drive(begin.front, request(begin.front), seconds=1)
    assert descriptor == expected_descriptor
    assert result["status"] == "completed" and len(blocked_polls) == 3
    assert [event["kind"] for event in events].count("terminal") == 1
    assert sum(event["kind"] == "patch" for event in events) == len(result["patches"])


def test_fixture_capture_refuses_completed_result_without_terminal_at_deadline(begin, monkeypatch):
    import importlib
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))
    generator = importlib.import_module("generate_frontend_fixtures")
    operation = SimpleNamespace(done=True, dispatch=lambda: None,
                                poll_event_json=lambda: "", result_json=lambda: "{}",
                                close=lambda: closed.append(True))
    closed = []
    clock = iter((0.0, 2.0))
    monkeypatch.setattr(generator, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    monkeypatch.setattr(begin.front, "prepare", lambda text: operation)
    with pytest.raises(TimeoutError, match="terminal event"):
        generator._drive(begin.front, request(begin.front), seconds=1)
    assert closed == [True]


def test_preparation_returns_identity_before_any_worker_or_ownership(begin, monkeypatch):
    monkeypatch.setattr(frontend._PolledOperation, "_start_worker",
                        lambda *a: pytest.fail("Preparation attempted dispatch"))
    operation = begin.front.prepare(json.dumps(request(begin.front)))
    assert operation.operation_id and operation._thread is None
    assert not operation.done and not operation._ownership.locked()
    operation.close()
    result = result_from_json(operation.result_json())
    assert result.status == "cancelled" and result.software_stopped and not result.patches
    assert dict(result.configuration)["stop_requested_by"] == "client"
    assert "dispatch_failure_stage" not in dict(result.configuration)
    with pytest.raises(OperationBusyError):
        operation.dispatch()


def test_interrupt_at_admitted_state_transition_resolves_no_dispatch(begin):
    import sys
    operation = begin.front.prepare(json.dumps(request(begin.front)))
    raised = []
    previous = sys.gettrace()

    def interrupt(frame, event, argument):
        if (event == "line" and frame.f_code is operation.dispatch.__func__.__code__
                and frame.f_locals.get("self") is operation
                and operation._dispatch_state == "starting" and not raised):
            raised.append(frame.f_lineno)
            raise KeyboardInterrupt("Injected admitted transition interruption")
        return interrupt

    try:
        sys.settrace(interrupt)
        with pytest.raises(KeyboardInterrupt, match="transition interruption"):
            operation.dispatch()
    finally:
        sys.settrace(previous)
    assert len(raised) == 1 and operation._thread is None
    assert operation.done and not operation._ownership.locked()
    result = result_from_json(operation.result_json())
    assert result.status == "failed" and result.software_stopped and not result.patches
    assert dict(result.configuration)["dispatch_failure_stage"] == "admission"
    assert dict(result.configuration)["stop_requested_by"] == "host_failure"
    assert result.error_stage is None and result.error_native_code is None


@pytest.mark.parametrize("failure", ["before_spawn", "after_spawn", "interrupted_after_spawn"])
def test_failed_spawn_never_crosses_gate_and_held_handle_resolves(begin, monkeypatch, failure):
    operation = begin.front.prepare(json.dumps(request(begin.front)))
    identity = operation.operation_id
    calls = []
    original = threading.Thread.start

    def starter(thread):
        if failure != "before_spawn":
            original(thread)
        if failure == "interrupted_after_spawn":
            raise KeyboardInterrupt("Injected interrupted spawn")
        raise RuntimeError("Injected spawn failure")

    with monkeypatch.context() as guard:
        guard.setattr(threading.Thread, "start", starter)
        guard.setattr(InstrumentOperation, "_run", lambda self: calls.append(self.operation_id))
        if failure == "interrupted_after_spawn":
            with pytest.raises(KeyboardInterrupt):
                operation.dispatch()
        else:
            assert operation.dispatch() is None
        if operation._thread.ident is not None:
            operation._thread.join(2)
            assert not operation._thread.is_alive()
    assert calls == [] and operation.operation_id == identity
    result = result_from_json(operation.result_json())
    assert result.status == "failed" and result.software_stopped and not result.patches
    assert "Operation dispatch failed" in result.error
    assert dict(result.configuration)["dispatch_failure_stage"] == "worker_start"
    assert dict(result.configuration)["stop_requested_by"] == "host_failure"
    assert result.error_stage is None and result.error_native_code is None
    assert not operation._ownership.locked()
    assert [json.loads(text)["kind"] for text in iter(operation.poll_event_json, "")] == [
        "started", "terminal"]
    event_until(begin())


@pytest.mark.parametrize("notification_failure", [False, True])
def test_busy_dispatch_has_a_host_stage_and_does_not_release_the_other_owner(
    begin, monkeypatch, notification_failure
):
    held = begin.front.prepare(json.dumps(request(begin.front)))
    owner = begin()
    event_until(owner, stop=lambda event: event["kind"] == "instruction_required")
    if notification_failure:
        def failure():
            raise RuntimeError("Secondary startup notification failure")

        monkeypatch.setattr(held, "_emit_started", failure)
    try:
        held.dispatch()
        result = result_from_json(held.result_json())
        assert result.status == "failed" and result.software_stopped and not result.patches
        assert dict(result.configuration)["dispatch_failure_stage"] == "instance_busy"
        assert dict(result.configuration)["stop_requested_by"] == "host_failure"
        assert result.error_stage is None and result.error_native_code is None
        assert begin.front._ownership.locked() and not owner.done
    finally:
        owner.close()
        event_until(owner)


@pytest.mark.parametrize("boundary,stage", [
    ("snapshot", "snapshot_validation"), ("construct", "worker_start"),
    ("spawn", "worker_start")])
def test_host_failure_classification_uses_the_boundary_not_the_sentence(
    begin, monkeypatch, boundary, stage
):
    operation = begin.front.prepare(json.dumps(request(begin.front)))

    def failure(*args, **kwargs):
        raise ValueError("Stale or different capability snapshot")

    if boundary == "snapshot":
        monkeypatch.setattr(begin.front, "describe_json", failure)
    elif boundary == "construct":
        monkeypatch.setattr(threading, "Thread", failure)
    else:
        monkeypatch.setattr(threading.Thread, "start", failure)
    operation.dispatch()
    result = result_from_json(operation.result_json())
    assert result.status == "failed" and result.software_stopped and not result.patches
    assert dict(result.configuration)["dispatch_failure_stage"] == stage
    assert result.error_stage is None and result.error_native_code is None


def test_interruption_before_commit_has_a_host_stage_without_acquisition(begin):
    operation = begin.front.prepare(json.dumps(request(begin.front)))
    interrupted = []
    previous = sys.gettrace()

    def interrupt(frame, event, argument):
        if (event == "line" and frame.f_code is operation.dispatch.__func__.__code__
                and frame.f_locals.get("self") is operation
                and operation._dispatch_stage == "commit"
                and operation._dispatch_state == "starting" and not interrupted):
            interrupted.append(None)
            raise KeyboardInterrupt("Injected commit entry interruption")
        return interrupt

    try:
        sys.settrace(interrupt)
        with pytest.raises(KeyboardInterrupt, match="commit entry"):
            operation.dispatch()
    finally:
        sys.settrace(previous)
    operation._thread.join(2)
    assert not operation._thread.is_alive() and len(interrupted) == 1
    result = result_from_json(operation.result_json())
    assert result.status == "failed" and result.software_stopped and not result.patches
    assert dict(result.configuration)["dispatch_failure_stage"] == "commit"
    assert dict(result.configuration)["stop_requested_by"] == "host_failure"
    assert result.error_stage is None and result.error_native_code is None


@pytest.mark.parametrize("boundary", ["started", "notification"])
def test_cancelled_prepared_handle_retains_client_stop_when_publication_fails(
    begin, monkeypatch, boundary
):
    operation = begin.front.prepare(json.dumps(request(begin.front)))
    if boundary == "started":
        def failure():
            raise RuntimeError("Injected startup notification failure")

        monkeypatch.setattr(operation, "_emit_started", failure)
    else:
        notify = operation._condition.notify_all
        calls = []

        def fail_once():
            calls.append(None)
            if len(calls) == 1:
                raise RuntimeError("Injected aborted bootstrap notification failure")
            return notify()

        monkeypatch.setattr(operation._condition, "notify_all", fail_once)
    operation.cancel(operation.operation_id)
    result = result_from_json(operation.result_json())
    assert result.status == "failed" and result.software_stopped and not result.patches
    assert dict(result.configuration)["dispatch_failure_stage"] == "notification"
    assert dict(result.configuration)["stop_requested_by"] == "client"
    assert result.error_stage is None and result.error_native_code is None


@pytest.mark.parametrize("profile", ["patch_v1", "retained_patch_v1"])
def test_no_dispatch_facts_survive_result_finalization_failure(begin, monkeypatch, profile):
    prepared_request = request(begin.front)
    prepared_request["event_contract"] = profile
    operation = begin.front.prepare(json.dumps(prepared_request))

    def failure(*args, **kwargs):
        raise RuntimeError("Injected failure")

    monkeypatch.setattr(threading.Thread, "start", failure)
    monkeypatch.setattr(frontend, "asdict", failure)
    operation.dispatch()
    result = result_from_json(operation.result_json())
    assert result.status == "failed" and result.software_stopped and not result.patches
    assert dict(result.configuration)["dispatch_failure_stage"] == "worker_start"
    assert dict(result.configuration)["stop_requested_by"] == "host_failure"
    assert dict(result.configuration)["event_contract"] == profile
    assert len(result.configuration) == 3
    assert result.error_stage is None and result.error_native_code is None


@pytest.mark.parametrize("interrupted", [False, True])
@pytest.mark.parametrize("stopped", [False, True])
@pytest.mark.parametrize("boundary", ["notification", "condition_exit"])
def test_commit_wakeup_failure_keeps_handle_and_lease_until_confirmed_cleanup(
    begin, monkeypatch, interrupted, stopped, boundary
):
    captured = begin()
    event_until(captured)
    observation = result_from_json(captured.result_json()).patches[0].observation
    operation = begin.front.prepare(json.dumps(request(begin.front)))
    entered, finish, waiting = threading.Event(), threading.Event(), threading.Event()
    calls = []
    failed = []
    original_wait = operation._condition.wait
    original_notify = operation._condition.notify_all
    original_start = threading.Thread.start
    original_trace = sys.gettrace()

    def wait(timeout=None):
        waiting.set()
        return original_wait(timeout)

    def starter(thread):
        original_start(thread)
        assert waiting.wait(1), "Bootstrap did not wait before commit"

    def notify():
        if (boundary == "notification" and operation._dispatch_state == "committed"
                and not failed):
            failed.append(True)
            if interrupted:
                raise KeyboardInterrupt("Injected lost bootstrap notification")
            raise RuntimeError("Injected lost bootstrap notification")
        return original_notify()

    def trace(frame, event, argument):
        if (boundary == "condition_exit" and not failed and event == "return"
                and frame.f_code is threading.Condition.__exit__.__code__
                and frame.f_locals.get("self") is operation._condition
                and operation._dispatch_state == "committed"):
            failed.append(True)
            sys.settrace(original_trace)
            if interrupted:
                raise KeyboardInterrupt("Injected lost bootstrap notification")
            raise RuntimeError("Injected lost bootstrap notification")
        return trace

    def neutral(self):
        calls.append(self.operation_id)
        self._emit_started()
        entered.set()
        finish.wait(2)
        self._publish_patch(PatchResult(
            0, "A1", replace(observation, operation_id=self.operation_id)))
        self._retain_stop(cancellation="client", acknowledged_by="none",
                          worker_stop="simulated" if stopped else "unknown",
                          device_completion="completed")
        self._software_stopped = stopped
        self._finish("cancelled", "Neutral worker confirmed stopped")

    with monkeypatch.context() as guard:
        guard.setattr(InstrumentOperation, "_run", neutral)
        guard.setattr(operation._condition, "wait", wait)
        guard.setattr(operation._condition, "notify_all", notify)
        guard.setattr(threading.Thread, "start", starter)
        try:
            if boundary == "condition_exit":
                sys.settrace(trace)
            if interrupted:
                with pytest.raises(KeyboardInterrupt):
                    operation.dispatch()
            else:
                with pytest.raises(RuntimeError, match="lost bootstrap notification"):
                    operation.dispatch()
            assert entered.wait(1)
            assert failed == [True]
            assert calls == [operation.operation_id] and not operation.done
            assert operation._ownership.locked() and operation._cancel.is_set()
            with pytest.raises(OperationBusyError):
                operation.dispatch()
            operation.close()
            assert operation._ownership.locked()  # Cancellation is not cleanup proof.
        finally:
            sys.settrace(original_trace)
            finish.set()
            operation._thread.join(2)
            assert not operation._thread.is_alive()
    assert operation.done and operation._ownership.locked() is not stopped
    result = result_from_json(operation.result_json())
    assert result.software_stopped is stopped and result.status == "failed"
    assert "Operation dispatch failed" in result.error
    assert "Injected lost bootstrap notification" in result.error
    assert len(result.patches) == 1
    assert result.patches[0].observation == replace(
        observation, operation_id=operation.operation_id)
    assert dict(result.configuration)["stop_requested_by"] == "host_failure"
    assert "dispatch_failure_stage" not in dict(result.configuration)
    if stopped:
        event_until(begin())
    else:
        with pytest.raises(OperationBusyError):
            begin.front.prepare(json.dumps(request(begin.front)))
        # This is only a neutral unit-test lock: no helper or acquisition was
        # created. Release the fixture, not an actual unconfirmed device lease.
        operation._ownership.release()
        operation._owns_instance = False


def test_committed_dispatch_failure_keeps_cause_through_real_demo_cleanup(begin, monkeypatch):
    operation = begin.front.prepare(json.dumps(request(begin.front)))
    original_notify = operation._condition.notify_all
    failed = []

    def notify():
        if operation._dispatch_state == "committed" and not failed:
            failed.append(True)
            raise RuntimeError("Injected host startup failure")
        original_notify()

    with monkeypatch.context() as guard:
        guard.setattr(operation._condition, "notify_all", notify)
        try:
            with pytest.raises(RuntimeError, match="host startup failure"):
                operation.dispatch()
        finally:
            operation._thread.join(2)
            assert not operation._thread.is_alive()
    events = event_until(operation)
    result = result_from_json(operation.result_json())
    assert result.status == "failed" and result.software_stopped and not result.patches
    assert "Injected host startup failure" in result.error
    assert "Cancelled by the client" not in result.error
    assert events[-1]["payload"]["error"] == result.error
    assert not operation._ownership.locked()
    event_until(begin())


@pytest.mark.parametrize("publication", ["serializing", "already_complete"])
def test_post_commit_interruption_preserves_pending_cause_and_completed_result(
    begin, monkeypatch, publication
):
    data = request(begin.front, workflow="mode_snapshot", ids=[])
    data["event_contract"] = "retained_patch_v1"
    operation = begin.front.prepare(json.dumps(data))
    serializing, release = threading.Event(), threading.Event()
    original_serialize, original_trace = frontend.result_to_json, sys.gettrace()
    interrupted = []

    def neutral(self):
        self._emit_started()
        self._software_stopped = True
        self._finish("completed", None)

    def serialize(result):
        if publication == "serializing":
            serializing.set()
            assert release.wait(3)
        return original_serialize(result)

    def trace(frame, event, argument):
        if (not interrupted and event == "return"
                and frame.f_code is threading.Condition.__exit__.__code__
                and frame.f_locals.get("self") is operation._condition
                and operation._dispatch_state == "committed"):
            interrupted.append(True)
            sys.settrace(original_trace)
            if publication == "serializing":
                assert serializing.wait(2), "Worker did not begin finalization"
            else:
                assert operation._done.wait(2), "Worker did not finish before interruption"
            raise KeyboardInterrupt("Injected post-context startup interruption")
        return trace

    with monkeypatch.context() as guard:
        guard.setattr(InstrumentOperation, "_run", neutral)
        guard.setattr(frontend, "result_to_json", serialize)
        try:
            sys.settrace(trace)
            with pytest.raises(KeyboardInterrupt, match="post-context startup interruption"):
                operation.dispatch()
        finally:
            sys.settrace(original_trace)
            release.set()
            operation._thread.join(3)
            assert not operation._thread.is_alive()
    result = result_from_json(operation.result_json())
    assert interrupted == [True] and result.software_stopped and not result.patches
    assert "dispatch_failure_stage" not in dict(result.configuration)
    assert not operation._ownership.locked()
    if publication == "serializing":
        assert result.status == "failed" and "post-context startup interruption" in result.error
    else:
        assert result.status == "completed" and result.error is None
        assert operation._dispatch_error is None and not operation._cancel.is_set()
    events = [json.loads(text) for text in iter(operation.poll_event_json, "")]
    assert [event["kind"] for event in events] == ["started", "terminal"]
    assert events[-1]["payload"]["status"] == result.status
    assert events[-1]["payload"]["error"] == result.error
    event_until(begin())


def test_lost_dispatch_return_keeps_prepared_identity_cancelable_and_drainable(begin, monkeypatch):
    held = SimpleNamespace(operation=begin.front.prepare(json.dumps(request(begin.front))))
    held.operation_id = held.operation.operation_id
    entered, finish = threading.Event(), threading.Event()

    def neutral(operation):
        operation._emit_started()
        entered.set()
        finish.wait(2)
        operation._software_stopped = True
        operation._finish("cancelled", "Neutral worker confirmed stopped")

    def lost_return():
        held.operation.dispatch()
        assert entered.wait(1)
        raise RuntimeError("Injected lost dispatch response")

    with monkeypatch.context() as guard:
        guard.setattr(InstrumentOperation, "_run", neutral)
        try:
            with pytest.raises(RuntimeError, match="lost dispatch response"):
                lost_return()
            assert held.operation._ownership.locked() and not held.operation.done
            held.operation.cancel(held.operation_id)
            assert held.operation._ownership.locked()
        finally:
            finish.set()
            held.operation._thread.join(2)
            assert not held.operation._thread.is_alive()
    result = result_from_json(held.operation.result_json())
    assert result.operation_id == held.operation_id and result.software_stopped
    event_until(begin())


@pytest.mark.parametrize("sample", [None, {"cell_index": 1, "attempt": 1}])
def test_prepared_demo_uses_current_descriptor_owner_after_completed_query(begin, sample):
    front = begin.front
    data = request(front, workflow="spot", ids=["A1"])
    if sample is not None:
        data["options"]["demo_chart_sample"] = sample
    operation = front.prepare(json.dumps(data))
    assert operation._frontend is front
    assert operation._meter is not front._meter
    query = request(front, workflow="mode_snapshot", ids=[])
    query["mode"] = "emissive_spot"
    query["event_contract"] = "retained_patch_v1"
    refresh = front.prepare(json.dumps(query))
    try:
        refresh.dispatch()
        event_until(refresh)
        assert result_from_json(refresh.result_json()).status == "completed"
    finally:
        refresh.close()
        refresh._thread.join(2)
        assert not refresh._thread.is_alive()
    assert json.loads(front.describe_json())["capability_id"] != data["capability_id"]
    operation.dispatch()
    result = result_from_json(operation.result_json())
    assert result.status == "failed" and result.software_stopped and not result.patches
    assert "Stale or different capability snapshot" in result.error
    assert operation._thread is None and not front._ownership.locked()



def test_ordered_readings_roundtrip_and_completed_result_is_immutable(begin):
    operation = begin()
    events = event_until(operation)
    assert [e["sequence"] for e in events] == list(range(len(events)))
    result = result_from_json(operation.result_json())
    assert result.status == "completed" and result.provenance == "simulated"
    assert [p.patch_id for p in result.patches] == ["A1", "A2", "A3"]
    assert [p.index for p in result.patches] == [0, 1, 2]
    for patch in result.patches:
        assert from_json(to_json(patch.observation)) == patch.observation
    before = operation.result_json()
    assert operation.poll_event_json() == ""
    operation.cancel(operation.operation_id)
    operation.close()
    assert operation.result_json() == before


def test_calibration_is_independent_zero_row_operation(begin):
    operation = begin(request(begin.front, workflow="calibrate", ids=[]))
    events = event_until(operation)
    result = result_from_json(operation.result_json())
    assert result.status == "completed" and not result.patches and not result.expected_patch_ids
    assert result.calibration.status == "valid"
    outcome = json.loads(dict(result.configuration)["calibration_outcome_json"])
    assert outcome["performed"] is True
    assert not any(e["kind"] in ("patch", "patch_reference", "measuring") for e in events)


def test_instruction_identity_one_shot_and_trigger_are_independent():
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "dtp51")
    data = request(front, ids=[f"sample-{n}" for n in range(7)])
    data["layout"] = json.loads(front.describe_json())["preview"]["example_layouts"][
        "reflective_strip"]
    operation = front.prepare(json.dumps(data))
    operation.dispatch()
    try:
        first = event_until(operation, lambda e: e["kind"] == "instruction_required")[-1]["payload"]
        for args in [
            ("wrong-operation", first["request_id"], "accept"),
            (operation.operation_id, "stale-request", "accept"),
            (operation.operation_id, first["request_id"], "skip"),
            (operation.operation_id, first["request_id"], True),
        ]:
            with pytest.raises(ValueError):
                operation.respond(*args)
        operation.respond(operation.operation_id, first["request_id"], "accept")
        with pytest.raises(ValueError):
            operation.respond(operation.operation_id, first["request_id"], "accept")
        # Real routes may ask positioning between calibration and the button. Keep
        # those independent prompts instead of assuming the toy route's two phases.
        for _ in range(8):
            trigger = event_until(operation, lambda e: e["kind"] == "instruction_required")[-1][
                "payload"]
            if trigger["kind"] == "device_trigger":
                break
            operation.respond(operation.operation_id, trigger["request_id"], "accept")
        else:
            pytest.fail("The current Demo route did not ask for its device trigger")
        assert trigger["request_id"] != first["request_id"]
        assert trigger["allowed_responses"] == ["accept", "cancel"]
        with pytest.raises(ValueError):
            operation.respond(operation.operation_id, first["request_id"], "accept")
        with pytest.raises(ValueError):
            operation.respond(operation.operation_id, trigger["request_id"], "trigger")
        assert not operation.done
        operation.respond(operation.operation_id, trigger["request_id"], "accept")
        event_until(operation)
        assert result_from_json(operation.result_json()).status == "completed"
    finally:
        operation.close()
        operation._thread.join(3)
        assert not operation._thread.is_alive()


def test_nonblocking_poll_and_cancel_during_operator_wait(begin):
    operation = begin()
    event_until(operation, lambda e: e["kind"] == "instruction_required")
    with pytest.raises(RuntimeError, match="Operation has not completed"):
        operation.result_json()
    start = time.monotonic()
    for _ in range(100):
        assert operation.poll_event_json() == ""
    assert time.monotonic() - start < .5
    operation.cancel(operation.operation_id)
    event_until(operation)
    result = result_from_json(operation.result_json())
    assert result.status == "cancelled" and not result.patches
    assert result.software_stopped and result.instrument_state == "not_applicable"
    event_until(begin())



def test_response_timeout_is_terminal_error_and_releases_instance(begin):
    operation = begin(response_timeout=.02)
    event_until(operation, respond_instructions=False)
    result = result_from_json(operation.result_json())
    assert result.status == "failed" and "timed out" in result.error
    assert result.software_stopped and not operation._ownership.locked()
    event_until(begin())


@pytest.mark.parametrize("budget", [120.0, 0.25, 17.5])
def test_instruction_reports_actual_owned_response_budget(begin, budget):
    operation = begin(response_timeout=budget)
    event = event_until(operation, lambda e: e["kind"] == "instruction_required")[-1]
    assert event["payload"]["response_timeout_seconds"] == budget == operation._response_timeout
    assert event["payload"]["request_id"] and event["payload"]["allowed_responses"]


def test_instruction_metadata_cannot_replace_owned_response_budget(begin):
    operation = begin.front.prepare(json.dumps(request(begin.front, workflow="spot", ids=["S"])))
    try:
        with pytest.raises(ValueError, match="Instruction metadata"):
            operation._ask(
                "sample_position",
                "Current sample",
                ("accept", "cancel"),
                metadata={"response_timeout_seconds": 1.0},
            )
        assert operation._pending is None and operation._pending_deadline is None
    finally:
        operation.cancel(operation.operation_id)


def reply_to(operation, event, *, operation_id=None, request_id=None, invalid=False):
    payload = event["payload"]
    operation_id = operation.operation_id if operation_id is None else operation_id
    request_id = payload["request_id"] if request_id is None else request_id
    if event["kind"] == "instruction_required":
        operation.respond(operation_id, request_id,
                          "invalid-choice" if invalid else payload["allowed_responses"][0])
    else:
        phase = payload["phase"]
        operation.presented(operation_id, request_id, json.dumps({
            "phase": ("white" if phase == "black" else "black") if invalid else phase,
            "stimulus_id": payload["stimulus_id"], "display_id": payload["display_id"],
            "clock_id": "test-client", "presented_at_seconds": time.monotonic(),
            "source": "client_draw_completed",
        }))


@pytest.fixture(params=["operator", "presentation"])
def pending_reply(begin, monkeypatch, request):
    # Advance the real frontend's deadline clock, not the native/demo transport
    # or test scheduler. Wake its existing condition instead of sleeping past it.
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(frontend, "time", SimpleNamespace(monotonic=lambda: clock.now))
    kind = "instruction_required"
    if request.param == "presentation":
        kind = "presentation_required"
        front, snapshot, mode = next(
            (f, d, m["mode"]) for f in demo.demo_frontends()
            for d in [json.loads(f.describe_json())] for m in d["modes"]
            if m["canonical"] and m["available"] and any(
                w["workflow"] == "display_timing" and w["available"] for w in m["workflows"]))
        data = {
            "schema_version": 1, "device_id": snapshot["device_id"],
            "capability_id": snapshot["capability_id"], "mode": mode,
            "workflow": "display_timing", "patch_ids": ["TIMING-1"], "options": {},
            "event_contract": "retained_patch_v1", "layout": {
                "stimulus_id": "test-transition", "stimulus_description": "Synthetic timing",
                "display_id": "test-display",
            },
        }
        operation = front.prepare(json.dumps(data))
        operation.dispatch()
    else:
        operation = begin()
    try:
        event = event_until(operation, lambda e: e["kind"] == kind)[-1]
        assert event["kind"] == kind
        yield operation, event, clock
    finally:
        operation.close()
        operation._thread.join(3)
        assert operation.done and not operation._thread.is_alive()


def expire_reply(operation, clock):
    with operation._condition:
        clock.now = 1000.0
        operation._condition.notify_all()
    operation._thread.join(3)
    assert operation.done and not operation._thread.is_alive()


@pytest.mark.parametrize("waiter_finished", [False, True])
def test_late_reply_names_deadline_and_preserves_timeout_cleanup(pending_reply, waiter_finished):
    operation, event, clock = pending_reply
    if waiter_finished:
        expire_reply(operation, clock)
    else:
        # Hold the condition across expiry and dispatch: the reply wins the lock
        # before the waiter can process its timeout, but must still be refused.
        with operation._condition:
            clock.now = 1000.0
            with pytest.raises(ValueError, match="arrived after its deadline"):
                reply_to(operation, event)
            assert operation._decision is None
            operation._condition.notify_all()
        operation._thread.join(3)
        assert operation.done and not operation._thread.is_alive()
    with pytest.raises(ValueError, match="arrived after its deadline"):
        reply_to(operation, event)
    result = result_from_json(operation.result_json())
    expected = ("Operator response timed out" if event["kind"] == "instruction_required"
                else "Controlled presentation timed out")
    assert result.status == "failed" and f"TimeoutError: {expected}" in result.error
    assert not result.patches and result.software_stopped
    assert result.error_stage is None and result.error_native_code is None
    assert not operation._ownership.locked()


@pytest.mark.parametrize("fault", ["wrong_operation", "wrong_request", "invalid_reply"])
def test_expired_reply_does_not_mislabel_invalid_identity_or_value(pending_reply, fault):
    operation, event, clock = pending_reply
    expire_reply(operation, clock)
    options = {"operation_id": "other"} if fault == "wrong_operation" else (
        {"request_id": "other"} if fault == "wrong_request" else {"invalid": True})
    with pytest.raises(ValueError, match="does not match the pending"):
        reply_to(operation, event, **options)


def test_expired_reply_through_the_other_method_keeps_generic_refusal(pending_reply):
    """A wrong reply method cannot claim the identity of an expired request."""
    operation, event, clock = pending_reply
    expire_reply(operation, clock)
    payload = event["payload"]
    with pytest.raises(ValueError, match="does not match the pending"):
        if event["kind"] == "presentation_required":
            operation.respond(operation.operation_id, payload["request_id"], payload["phase"])
        else:
            operation.presented(operation.operation_id, payload["request_id"], json.dumps({
                "phase": "black", "stimulus_id": "s", "display_id": "d",
                "clock_id": "test-client", "presented_at_seconds": time.monotonic(),
                "source": "client_draw_completed",
            }))


def test_prompt_reply_completes_and_duplicate_keeps_generic_refusal(pending_reply):
    operation, event, _ = pending_reply
    first = event
    with operation._condition:
        reply_to(operation, event)
        with pytest.raises(ValueError, match="does not match the pending"):
            reply_to(operation, event)
    while event["kind"] != "terminal":
        event = event_until(operation, lambda e: e["kind"] in {
            "presentation_required", "terminal"})[-1]
        if event["kind"] == "presentation_required":
            reply_to(operation, event)
    result = result_from_json(operation.result_json())
    assert result.status == "completed" and result.patches and result.software_stopped
    with pytest.raises(ValueError, match="does not match the pending"):
        reply_to(operation, first)


def test_cancelled_reply_is_not_mislabelled_as_deadline_expiry(pending_reply):
    operation, event, clock = pending_reply
    with operation._condition:
        operation.cancel(operation.operation_id)
        clock.now = 1000.0
        with pytest.raises(ValueError, match="does not match the pending"):
            reply_to(operation, event)
    operation._thread.join(3)
    result = result_from_json(operation.result_json())
    assert result.status == "cancelled" and not result.patches and result.software_stopped


def test_same_instance_is_owned_and_another_instance_progresses(begin):
    first = begin()
    with pytest.raises(OperationBusyError):
        begin()
    other_front = next(f for f in demo.demo_frontends() if f._meter.persona == "i1pro3")
    other = other_front.prepare(json.dumps(request(other_front)))
    other.dispatch()
    try:
        assert first._ownership is not other._ownership
        event_until(other)
        event_until(first)
    finally:
        other.close()
        other._thread.join(3)
        assert not other._thread.is_alive()


@pytest.mark.parametrize("key,value", [
    ("schema_version", True), ("schema_version", 2), ("capability_id", "stale"),
    ("device_id", "unknown"), ("mode", "unknown"), ("workflow", "unknown"),
    ("patch_ids", ["same", "same"]), ("patch_ids", [None]), ("patch_ids", []),
    ("patch_ids", ["x" * 129]), ("options", []), ("options", {"unknown": 2}),
    ("layout", {"direction": "unknown"}), ("layout", {"unknown": 2}),
    ("layout", {"scan_tolerance": 10**400}),
])
def test_invalid_requests_fail_before_owning_an_instance(begin, key, value):
    data = request(begin.front)
    data[key] = value
    with pytest.raises(ValueError):
        begin(data)
    assert not begin.front._ownership.locked()


def test_request_missing_extra_duplicate_and_oversized_fields_refused(begin):
    data = request(begin.front)
    for text, refusal in [
        (json.dumps({**data, "extra": 1}), "exactly the generic frontend fields"),
        (json.dumps({k: v for k, v in data.items() if k != "layout"}),
         "exactly the generic frontend fields"),
        ('{"schema_version":1,' + json.dumps(data)[1:], "Duplicate JSON field"),
        # One oversized ID also breaks the per-ID bound checked after parsing;
        # the match keeps this case on the byte bound applied before parsing.
        (json.dumps({**data, "patch_ids": ["x" * 20000]}), "within frontend size limits"),
    ]:
        with pytest.raises(ValueError, match=refusal):
            begin.front.prepare(text)
        assert not begin.front._ownership.locked()


@pytest.mark.parametrize("entry", ["instrument", "demo", "demo_prepare"])
@pytest.mark.parametrize("case", [
    "nontext", "utf8_bytes", "before_demo_strip", "duplicate", "nan", "infinity",
    "deep_array", "deep_object",
])
def test_original_request_boundary_precedes_adaptation_and_ownership(begin, entry, case):
    data = request(begin.front, workflow="spot", ids=["A1"])
    refusal = "within frontend size limits"
    if case == "nontext":
        text = json.dumps(data).encode()
    elif case == "utf8_bytes":
        data["patch_ids"] = ["é" * (MAX_REQUEST_BYTES // 2)]
        text = json.dumps(data, ensure_ascii=False)
        assert len(text) < MAX_REQUEST_BYTES < len(text.encode("utf-8"))
    elif case == "before_demo_strip":
        data["options"] = {"demo_chart_sample": {"cell_index": 1, "attempt": 1}}
        text = json.dumps(data) + " " * MAX_REQUEST_BYTES
        # Removing Demo-only options reserializes this valid JSON below the
        # bound. The original request must still be refused before that rewrite.
        data["options"] = {}
        assert len(json.dumps(data).encode("utf-8")) < MAX_REQUEST_BYTES
    elif case == "duplicate":
        text = json.dumps(data).replace('"options": {}', '"options": {"x": 1, "x": 2}')
        refusal = "Duplicate JSON field"
    elif case in {"deep_array", "deep_object"}:
        text = ("[" * 5000 + "0" + "]" * 5000 if case == "deep_array"
                else '{"x":' * 1500 + "0" + "}" * 1500)
        assert len(text.encode("utf-8")) < MAX_REQUEST_BYTES
        # Parser recursion limits vary by Python runtime. Either normalized
        # parser overflow or rejection of the decoded request is valid.
        refusal = "Frontend JSON nesting|Request must be a JSON object|Request must contain"
    else:
        data["options"] = {"demo_chart_sample": {
            "cell_index": 1, "attempt": float("nan") if case == "nan" else float("inf")}}
        text = json.dumps(data)
        refusal = "Nonfinite JSON value"
    if entry == "instrument":
        def validate(text):
            return InstrumentFrontend._validate_request(begin.front, text)
    else:
        validate = begin.front.prepare if entry == "demo_prepare" else begin.front._validate_request
    with pytest.raises(ValueError, match=refusal):
        validate(text)
    assert not begin.front._ownership.locked()


def test_deep_request_never_initializes_an_operation_and_allows_a_valid_next_request(
    begin, monkeypatch
):
    text = "[" * 5000 + "0" + "]" * 5000
    descriptor = begin.front.describe_json()
    with monkeypatch.context() as guard:
        guard.setattr(
            frontend._PolledOperation, "_initialize_operation",
            lambda *args, **kwargs: pytest.fail("Invalid request initialized an operation"),
        )
        with pytest.raises(ValueError, match="Frontend JSON nesting|Request must"):
            begin.front.prepare(text)
    assert not begin.front._ownership.locked()
    assert begin.front.describe_json() == descriptor
    operation = begin()
    event_until(operation)
    assert result_from_json(operation.result_json()).status == "completed"
    assert not begin.front._ownership.locked()


@pytest.mark.parametrize("pending_reply", ["presentation"], indirect=True)
@pytest.mark.parametrize("shape", ["array", "object"])
def test_deep_presentation_refuses_without_consuming_the_pending_reply(pending_reply, shape):
    operation, event, _ = pending_reply
    text = ("[" * 5000 + "0" + "]" * 5000 if shape == "array"
            else '{"x":' * 1500 + "0" + "}" * 1500)
    assert len(text.encode("utf-8")) < MAX_REQUEST_BYTES
    with operation._condition:
        before = (operation._pending, operation._pending_deadline, operation._decision,
                  operation._sequence, tuple(operation._patches))
        with pytest.raises(ValueError, match="does not match the pending operation/request"):
            reply_to(operation, event, operation_id="another-operation")
        with pytest.raises(ValueError, match=(
            "Frontend JSON nesting|Expected a record object|PresentationStamp fields differ"
        )):
            operation.presented(operation.operation_id, event["payload"]["request_id"], text)
        assert (operation._pending, operation._pending_deadline, operation._decision,
                operation._sequence, tuple(operation._patches)) == before
        reply_to(operation, event)
        assert operation._decision is not None


def test_parser_recursion_refuses_request_before_initialization(begin, monkeypatch):
    overflow = RecursionError("Controlled parser recursion overflow")

    def overflowed_parser(*args, **kwargs):
        raise overflow

    with monkeypatch.context() as guard:
        guard.setattr(frontend.json, "loads", overflowed_parser)
        guard.setattr(
            frontend._PolledOperation, "_initialize_operation",
            lambda *args, **kwargs: pytest.fail("Parser overflow initialized an operation"),
        )
        with pytest.raises(ValueError, match="Frontend JSON nesting") as refusal:
            begin.front.prepare("{}")
        assert refusal.value.__cause__ is overflow
    assert not begin.front._ownership.locked()
    operation = begin()
    event_until(operation)
    assert result_from_json(operation.result_json()).status == "completed"


@pytest.mark.parametrize("pending_reply", ["presentation"], indirect=True)
def test_parser_recursion_preserves_the_pending_presentation(pending_reply, monkeypatch):
    operation, event, _ = pending_reply
    overflow = RecursionError("Controlled presentation parser overflow")

    def overflowed_parser(*args, **kwargs):
        raise overflow

    with operation._condition:
        before = (operation._pending, operation._pending_deadline, operation._decision,
                  operation._sequence, tuple(operation._patches))
        with monkeypatch.context() as guard:
            guard.setattr(frontend.json, "loads", overflowed_parser)
            with pytest.raises(ValueError, match="Frontend JSON nesting") as refusal:
                operation.presented(operation.operation_id, event["payload"]["request_id"], "{}")
            assert refusal.value.__cause__ is overflow
        assert (operation._pending, operation._pending_deadline, operation._decision,
                operation._sequence, tuple(operation._patches)) == before
        reply_to(operation, event)
        assert operation._decision is not None


@pytest.mark.parametrize("entry", ["instrument", "demo"])
def test_request_utf8_byte_limit_is_inclusive(begin, entry):
    data = request(begin.front, workflow="spot", ids=["é"])
    text = json.dumps(data, ensure_ascii=False)
    text += " " * (MAX_REQUEST_BYTES - len(text.encode("utf-8")))
    assert len(text.encode("utf-8")) == MAX_REQUEST_BYTES
    if entry == "instrument":
        def validate(text):
            return InstrumentFrontend._validate_request(begin.front, text)
    else:
        validate = begin.front._validate_request
    assert validate(text)[0]["patch_ids"] == ["é"]
    with pytest.raises(ValueError, match="within frontend size limits"):
        validate(text + " ")
    assert not begin.front._ownership.locked()


@pytest.mark.parametrize("persona", ["i1_display_3", "spyderx2", "i1pro3"])
def test_capability_identity_ignores_member_order_but_keeps_values_and_list_order(
    monkeypatch, persona,
):
    front = next(f for f in demo.demo_frontends() if f._meter.persona == persona)
    snapshot = front._descriptor()
    first_text = front.describe_json()
    first = json.loads(first_text)

    def reverse_members(value):
        if isinstance(value, dict):
            return {key: reverse_members(item) for key, item in reversed(value.items())}
        if isinstance(value, list):
            return [reverse_members(item) for item in value]
        return value

    reordered = reverse_members(snapshot)
    assert reordered == snapshot
    monkeypatch.setattr(front, "_descriptor", lambda: deepcopy(reordered))
    reordered_text = front.describe_json()
    assert reordered_text != first_text  # Protocol serialization keeps its order.
    assert json.loads(reordered_text) == first

    changed = deepcopy(snapshot)
    changed["options"][0]["label"] += " changed"
    monkeypatch.setattr(front, "_descriptor", lambda: deepcopy(changed))
    assert json.loads(front.describe_json())["capability_id"] != first["capability_id"]

    changed = deepcopy(snapshot)
    assert len(changed["modes"]) > 1
    changed["modes"].reverse()
    monkeypatch.setattr(front, "_descriptor", lambda: deepcopy(changed))
    assert json.loads(front.describe_json())["capability_id"] != first["capability_id"]


def test_calibration_dummy_rows_and_multireading_spot_are_refused(begin):
    for data in [request(begin.front, workflow="calibrate", ids=["dummy"]),
                 request(begin.front, workflow="spot", ids=["one", "two"])]:
        with pytest.raises(ValueError):
            begin(data)
        assert not begin.front._ownership.locked()


@pytest.mark.parametrize("field", ["response_timeout", "timeout"])
@pytest.mark.parametrize("value", [True, 0, -1, float("nan"), float("inf"), 10**400])
def test_timeout_bounds_raise_validation_error_before_ownership(begin, field, value):
    with pytest.raises(ValueError, match="Timeouts"):
        begin(**{field: value})
    assert not begin.front._ownership.locked()


def test_terminal_publication_is_atomic_without_blocking_poll(begin, monkeypatch):
    reached, resume = threading.Event(), threading.Event()
    original = InstrumentOperation._emit

    def pause_terminal(self, kind, payload):
        if kind == "terminal":
            reached.set()
            assert resume.wait(3)
        original(self, kind, payload)

    monkeypatch.setattr(InstrumentOperation, "_emit", pause_terminal)
    operation = begin(request(begin.front, workflow="calibrate", ids=[]))
    instruction = event_until(operation, lambda e: e["kind"] == "instruction_required")[-1]
    operation.respond(operation.operation_id, instruction["payload"]["request_id"], "accept")
    try:
        assert reached.wait(2)
        assert not operation.done
        with pytest.raises(RuntimeError, match="Operation has not completed"):
            operation.result_json()
        start = time.monotonic()
        assert operation.poll_event_json() == ""
        assert time.monotonic() - start < 0.2
    finally:
        resume.set()
    assert event_until(operation)[-1]["kind"] == "terminal"
    assert operation.done


@pytest.mark.parametrize("completed", [False, True])
def test_terminal_poll_matches_completion_while_producer_holds_condition(
    begin, monkeypatch, completed
):
    operation = begin.front.prepare(json.dumps(request(begin.front)))
    complete = operation._release_and_complete
    reached, resume = threading.Event(), threading.Event()
    failures = []

    def held_completion():
        if completed:
            complete()
        reached.set()
        assert resume.wait(3)
        if not completed:
            complete()

    def cancel_prepared():
        try:
            operation.cancel(operation.operation_id)
        except BaseException as failure:
            failures.append(failure)

    monkeypatch.setattr(operation, "_release_and_complete", held_completion)
    producer = threading.Thread(target=cancel_prepared)
    producer.start()
    try:
        assert reached.wait(2) and operation.done is completed
        assert operation._thread is None and not operation._ownership.locked()
        acquired = operation._condition.acquire(blocking=False)
        if acquired:
            operation._condition.release()
        assert not acquired
        start = time.monotonic()
        if completed:
            first = operation.poll_event_json()
            assert first, "Queued events were hidden by the producer condition"
            terminal = operation.poll_event_json()
            assert terminal, "Queued terminal was hidden despite done"
            assert operation.poll_event_json() == ""
        else:
            assert operation.poll_event_json() == ""
            with pytest.raises(RuntimeError, match="Operation has not completed"):
                operation.result_json()
        assert time.monotonic() - start < 0.2
        if completed:
            result = result_from_json(operation.result_json())
            assert result.status == "cancelled" and result.software_stopped and not result.patches
    finally:
        resume.set()
        producer.join(2)
        assert not producer.is_alive()
    assert failures == []
    assert operation.done
    events = ([json.loads(first), json.loads(terminal)] if completed else
              [json.loads(text) for text in iter(operation.poll_event_json, "")])
    assert [event["kind"] for event in events] == ["started", "terminal"]
    assert [event["sequence"] for event in events] == [0, 1]
    assert all(event["operation_id"] == operation.operation_id for event in events)
    assert events[-1]["payload"]["software_stopped"] is True


@pytest.mark.parametrize("hook", ["OperationResult", "result_to_json"])
def test_result_hook_failure_retains_patches_and_releases_instance(begin, monkeypatch, hook):
    def fail(*args, **kwargs):
        raise RuntimeError(f"injected {hook} failure")

    with monkeypatch.context() as patcher:
        patcher.setattr(frontend, hook, fail)
        operation = begin()
        events = event_until(operation)
        result = result_from_json(operation.result_json())
        assert result.status == "failed" and len(result.patches) == 3
        assert result.expected_patch_ids == ("A1", "A2", "A3")
        assert hook in result.error
        assert result.software_stopped and result.instrument_state == "not_applicable"
        assert [e["sequence"] for e in events] == list(range(len(events)))
        for patch, event in zip(result.patches, (e for e in events if e["kind"] == "patch")):
            assert to_json(patch.observation) == to_json(
                from_json(json.dumps(event["payload"]["observation"]))
            )
        assert operation.done
        assert not operation._ownership.locked()
    replacement = begin()
    event_until(replacement)
    assert result_from_json(replacement.result_json()).status == "completed"


@pytest.mark.parametrize("kind", ["patch", "terminal"])
@pytest.mark.parametrize("after_enqueue", [False, True])
def test_event_failure_keeps_acquired_patches_and_exactly_one_terminal(
    begin, monkeypatch, kind, after_enqueue
):
    original = InstrumentOperation._emit
    def fail(self, current, payload):
        if current == kind:
            if after_enqueue:
                original(self, current, payload)
            raise RuntimeError(f"injected {kind} publication failure")
        original(self, current, payload)

    with monkeypatch.context() as patcher:
        patcher.setattr(InstrumentOperation, "_emit", fail)
        operation = begin(request(begin.front, workflow="spot", ids=["A1"]))
        events = event_until(operation)
        result = result_from_json(operation.result_json())
        assert result.status == "failed" and len(result.patches) == 1
        assert result.patches[0].patch_id == "A1"
        assert kind in result.error
        assert [e["sequence"] for e in events] == list(range(len(events)))
        assert sum(e["kind"] == "terminal" for e in events) == 1
        assert events[-1]["payload"]["status"] == result.status
        assert events[-1]["payload"]["error"] == result.error
        assert operation.poll_event_json() == ""
        assert operation.done and not operation._ownership.locked()
    replacement = begin()
    event_until(replacement)


def test_fallback_does_not_reuse_broken_constructor_serializer_or_emitter(begin, monkeypatch):
    calls = []
    original = InstrumentOperation._emit

    def fail_constructor(*args, **kwargs):
        calls.append("constructor")
        raise RuntimeError("broken constructor")

    def fail_serializer(*args, **kwargs):
        calls.append("serializer")
        raise RuntimeError("broken serializer")

    def fail_terminal(self, kind, payload):
        if kind == "terminal":
            calls.append("terminal")
            original(self, kind, payload)
            raise RuntimeError("broken terminal emitter after enqueue")
        original(self, kind, payload)

    with monkeypatch.context() as patcher:
        patcher.setattr(frontend, "OperationResult", fail_constructor)
        patcher.setattr(frontend, "result_to_json", fail_serializer)
        patcher.setattr(InstrumentOperation, "_emit", fail_terminal)
        operation = begin()
        events = event_until(operation)
        result = result_from_json(operation.result_json())
        assert calls == ["constructor", "terminal"]
        assert result.status == "failed" and len(result.patches) == 3
        assert "broken constructor" in result.error and "broken terminal" in result.error
        assert sum(e["kind"] == "terminal" for e in events) == 1
        assert operation.done and not operation._ownership.locked()
    event_until(begin())


@pytest.mark.parametrize(
    "message,workflow",
    [("", "strip"), (" ", "strip"), ("broken_str", "strip"), ("large", "strip"),
     ("", "calibrate")],
)
def test_unusable_exception_messages_still_produce_failed_terminal(
    begin, monkeypatch, message, workflow
):
    # The failure precedes workflow branching; calibration adds a zero-row request.
    class UnprintableError(Exception):
        def __str__(self):
            raise RuntimeError("cannot format this exception")

    def fail(*args, **kwargs):
        if message == "broken_str":
            raise UnprintableError()
        raise RuntimeError("\U0001f3a8" * 20000 if message == "large" else message)

    with monkeypatch.context() as patcher:
        patcher.setattr(InstrumentOperation, "_ask", fail)
        operation = begin(request(begin.front, workflow=workflow,
                                  ids=[] if workflow == "calibrate" else None))
        events = event_until(operation)
        result = result_from_json(operation.result_json())
        assert result.status == "failed" and not result.patches
        assert result.software_stopped and result.instrument_state == "not_applicable"
        assert result.error.strip()
        if message == "broken_str":
            assert "UnprintableError" in result.error
        else:
            assert "RuntimeError" in result.error
        assert events[-1]["payload"]["status"] == "failed"
        assert operation.done and not operation._ownership.locked()
    event_until(begin())


@pytest.mark.parametrize("shape", ["nested", "long", "wide", "deep", "cycle", "broken_str"])
def test_group_and_cause_diagnostics_are_bounded_in_terminal_and_persistence(
        begin, monkeypatch, tmp_path, shape):
    from color_measurement.operation_exports import load, save

    class UnprintableError(Exception):
        def __str__(self):
            raise RuntimeError("cannot format this exception")

    first = RuntimeError("first diagnostic")
    last = OSError("last cleanup diagnostic")
    if shape == "nested":
        failure = ExceptionGroup("nested ending", [
            first, ExceptionGroup("cleanup", [last])])
    elif shape == "long":
        failure = ExceptionGroup("long ending", [RuntimeError("x" * 20000), last])
    elif shape == "wide":
        failure = ExceptionGroup("wide ending", [RuntimeError(f"diagnostic {i}")
                                                 for i in range(40)])
    elif shape == "deep":
        failure = last
        for _ in range(2000):
            failure = ExceptionGroup("deep ending", [failure])
    elif shape == "cycle":
        first.__cause__ = last
        last.__cause__ = first
        failure = first
    else:
        failure = ExceptionGroup("unprintable ending", [UnprintableError(), last])

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(InstrumentOperation, "_ask", fail)
    operation = begin()
    events = event_until(operation)
    result = result_from_json(operation.result_json())
    assert result.status == "failed" and result.software_stopped
    assert not result.patches and not operation._ownership.locked()
    assert 0 < len(result.error) <= 700
    assert events[-1]["payload"]["error"] == result.error
    assert sum(event["kind"] == "terminal" for event in events) == 1
    if shape in {"nested", "long", "cycle", "broken_str"}:
        assert "last cleanup diagnostic" in result.error
    if shape == "broken_str":
        assert "UnprintableError: exception message unavailable" in result.error
    if shape in {"wide", "deep"}:
        assert "[diagnostic limit]" in result.error
    assert load(save(result, tmp_path / "failure.json").path) == result


def test_non_cancel_early_end_is_failure_without_inventing_missing_patches(begin, monkeypatch):
    # The completed native batch is normally retained together. Deliberately
    # shorten that retention without cancellation to challenge the finalizer.
    def early_end(self, observations):
        self._publish_patch(PatchResult(0, self._request["patch_ids"][0], observations[0]))

    with monkeypatch.context() as patcher:
        patcher.setattr(InstrumentOperation, "_publish_complete_batch", early_end)
        operation = begin()
        event_until(operation)
        result = result_from_json(operation.result_json())
        assert not operation._cancel.is_set()
        assert result.status == "failed" and len(result.patches) == 1
        assert "before all requested patches" in result.error
        assert result.expected_patch_ids == ("A1", "A2", "A3")
    event_until(begin())


@pytest.mark.parametrize("last_pair", [
    ("A", "utf8"), ("B", "utf8"), (None, None), ("A", "bytes"),
])
def test_patch_publication_keeps_serial_pair_learned_after_first_patch(begin, last_pair):
    captured = begin()
    event_until(captured)
    observations = [p.observation for p in result_from_json(captured.result_json()).patches]
    identity = replace(observations[0].device, serial=None, serial_encoding=None)
    operation = frontend._PolledOperation()
    operation._initialize_operation(request(begin.front), identity=identity,
                                    provenance="simulated", ownership=threading.Lock())
    pairs = [(None, None), ("A", "utf8"), last_pair]
    for index, (observation, pair) in enumerate(zip(observations, pairs)):
        reading = replace(observation, operation_id=operation.operation_id,
                          device=replace(identity, serial=pair[0], serial_encoding=pair[1]))
        patch = PatchResult(index, reading.sample_id, reading)
        if index == 2 and pair != ("A", "utf8"):
            with pytest.raises(ValueError, match="Patch identity or order"):
                operation._publish_patch(patch)
        else:
            operation._publish_patch(patch)
    count = 3 if last_pair == ("A", "utf8") else 2
    assert len(operation._patches) == len(operation._patch_data) == count
    published = []
    while text := operation.poll_event_json():
        event = json.loads(text)
        if event["kind"] == "patch":
            published.append(event["payload"])
    assert len(published) == count
    assert published[1]["observation"]["device"]["serial"] == "A"
    assert published[1]["observation"]["device"]["serial_encoding"] == "utf8"


def test_fallback_terminal_publication_is_atomic_even_after_partial_emit(begin, monkeypatch):
    reached, resume = threading.Event(), threading.Event()
    original = InstrumentOperation._emit

    def fail_after_enqueue(self, kind, payload):
        original(self, kind, payload)
        if kind == "terminal":
            reached.set()
            assert resume.wait(3)
            raise RuntimeError("terminal hook failed after enqueue")

    with monkeypatch.context() as patcher:
        patcher.setattr(InstrumentOperation, "_emit", fail_after_enqueue)
        operation = begin(request(begin.front, workflow="calibrate", ids=[]))
        instruction = event_until(operation, lambda e: e["kind"] == "instruction_required")[-1]
        operation.respond(operation.operation_id, instruction["payload"]["request_id"], "accept")
        try:
            assert reached.wait(2)
            assert not operation.done
            with pytest.raises(RuntimeError, match="Operation has not completed"):
                operation.result_json()
            start = time.monotonic()
            assert operation.poll_event_json() == ""
            assert time.monotonic() - start < 0.2
        finally:
            resume.set()
        events = event_until(operation)
        result = result_from_json(operation.result_json())
        assert result.status == "failed" and not result.patches
        assert result.calibration.status == "valid"
        assert sum(e["kind"] == "terminal" for e in events) == 1
        assert operation.done and not operation._ownership.locked()
    event_until(begin())


@pytest.mark.parametrize("primary_failure", ["result_serializer", "terminal_publication"])
def test_missing_terminal_result_raises_after_recovery_failure(begin, monkeypatch, primary_failure):
    failures = []
    original_run = InstrumentOperation._run

    def record_worker_failure(self):
        try:
            original_run(self)
        except RuntimeError as failure:
            failures.append(failure)

    def fail_primary(value):
        raise RuntimeError("injected primary serializer failure")

    def fail_recovery(value):
        raise RuntimeError("injected primitive recovery failure")

    def fail_terminal(self, kind, payload):
        assert kind == "terminal"
        raise RuntimeError("injected terminal publication failure")

    with monkeypatch.context() as patcher:
        patcher.setattr(InstrumentOperation, "_run", record_worker_failure)
        operation = begin()
        instruction = event_until(operation, lambda e: e["kind"] == "instruction_required")[-1]
        assert operation._ownership.locked() and not operation.done
        # The recovery template has already been validated and ownership taken.
        # Inject failure only now, so this exercises late finalization recovery.
        if primary_failure == "result_serializer":
            patcher.setattr(frontend, "result_to_json", fail_primary)
        else:
            patcher.setattr(InstrumentOperation, "_stage_event", fail_terminal)
        patcher.setattr(frontend, "_fallback_json", fail_recovery)
        operation.respond(operation.operation_id, instruction["payload"]["request_id"], "accept")
        # The native-shaped Demo may need placement and button responses before
        # finalization. Answer those too; do not mistake an operator wait for a
        # recovery failure or waive the no-terminal guarantee.
        deadline = time.monotonic() + 3
        before_done = []
        while not operation.done and time.monotonic() < deadline:
            text = operation.poll_event_json()
            if not text:
                time.sleep(.001)
                continue
            event = json.loads(text)
            before_done.append(event)
            if event["kind"] == "instruction_required":
                operation.respond(operation.operation_id, event["payload"]["request_id"], "accept")
        operation._thread.join(2)
        assert not operation._thread.is_alive() and operation.done
        assert not operation._ownership.locked() and operation._result is None
        assert [str(failure) for failure in failures] == ["injected primitive recovery failure"]
        assert [patch.patch_id for patch in operation._patches] == ["A1", "A2", "A3"]
        events = before_done
        while text := operation.poll_event_json():
            events.append(json.loads(text))
        assert not any(event["kind"] == "terminal" for event in events)
        with pytest.raises(RuntimeError, match="Terminal result was not published"):
            operation.result_json()
    replacement = begin()
    event_until(replacement)
    assert result_from_json(replacement.result_json()).status == "completed"
