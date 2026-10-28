"""Shared bounded JSON, polling and finalization for instrument frontends.

The generic instrument frontend owns descriptors and requests. Its explicit Demo
uses the same operation lifetime without a native process or physical device.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import asdict
from typing import Literal
from uuid import uuid4

from .json_values import unique_object
from .models import CalibrationContext, PresentationStamp, _decode
from .models import identity_agrees as _identity_agrees
from .operation_records import OperationResult, PatchResult, result_from_json, result_to_json
from .operations import OperationBusyError

MAX_EVENT_BYTES = 16384
MAX_REQUEST_BYTES = 16384

#: Instruction metadata fields owned here; native_* keys pass through unchanged.
_INSTRUCTION_METADATA = frozenset({"calibration", "position"})


def _validate_instruction_payload(payload):
    """Validate the current shared envelope; native metadata keeps its namespace."""
    fields = {"request_id", "kind", "message", "allowed_responses", "response_timeout_seconds"}
    if (
        not isinstance(payload, dict)
        or fields - payload.keys()
        or any(
            key not in fields | _INSTRUCTION_METADATA and not key.startswith("native_")
            for key in payload
        )
    ):
        raise ValueError("Instruction requires the current closed envelope")
    if not isinstance(payload["request_id"], str) or not payload["request_id"].strip():
        raise ValueError("Instruction request ID must be nonempty text")
    if (
        not isinstance(payload["kind"], str)
        or not payload["kind"].strip()
        or not isinstance(payload["message"], str)
    ):
        raise ValueError("Instruction kind and message must be text")
    responses = payload["allowed_responses"]
    if (
        not isinstance(responses, list)
        or not responses
        or any(not isinstance(response, str) or not response for response in responses)
        or len(set(responses)) != len(responses)
    ):
        raise ValueError("Instruction responses must be distinct nonempty text")
    budget = payload["response_timeout_seconds"]
    if type(budget) not in (int, float) or not 0 < budget <= 600:
        raise ValueError("Instruction response budget must be finite seconds in (0, 600]")


def _validate_presentation_payload(payload):
    """Validate a controlled drawing request without inventing a completion stamp."""
    fields = {"request_id", "phase", "rgb", "minimum_delay_ms", "deadline_ms",
              "stimulus_id", "stimulus_description", "display_id", "timestamp_source"}
    # The producer retains additional native protocol facts unchanged. Only
    # these drawing/identity fields control the client presentation exchange.
    if not isinstance(payload, dict) or fields - payload.keys():
        raise ValueError("Controlled presentation requires its declared envelope")
    for name in ("request_id", "stimulus_id", "display_id", "stimulus_description"):
        if not isinstance(payload[name], str) or not payload[name].strip():
            raise ValueError("Controlled presentation identities must be nonempty text")
    if payload["phase"] not in {"black", "white"}:
        raise ValueError("Controlled presentation phase must be black or white")
    rgb = payload["rgb"]
    expected = [0.0, 0.0, 0.0] if payload["phase"] == "black" else [1.0, 1.0, 1.0]
    if (not isinstance(rgb, list) or any(type(value) not in (int, float) for value in rgb)
            or rgb != expected or payload["timestamp_source"] != "client_draw_completed"):
        raise ValueError("Controlled presentation must draw its exact declared stimulus")
    if (type(payload["deadline_ms"]) is not int or not 0 < payload["deadline_ms"] <= 600000
            or type(payload["minimum_delay_ms"]) is not int
            or not 0 <= payload["minimum_delay_ms"] < payload["deadline_ms"]):
        raise ValueError("Controlled presentation requires a bounded drawing deadline")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _failure_detail(failure: BaseException) -> str:
    """Bound member/cause diagnostics without losing a short secondary failure."""
    def describe(error, limit):
        try:
            message = str(error).strip()
        except Exception:
            message = "exception message unavailable"
        return (f"{type(error).__name__}: {message}" if message
                else type(error).__name__)[:limit]

    if not isinstance(failure, BaseExceptionGroup) and failure.__cause__ is None:
        return describe(failure, 700)
    # Iterate rather than recurse: nesting and explicit cause cycles are bounded
    # independently of the final text. Iterators avoid copying a wide group.
    pending, members, seen = [iter((failure,))], [], set()
    visits = 0
    while pending and len(members) < 8 and visits < 32:
        error = next(pending[-1], None)
        if error is None:
            pending.pop()
            continue
        visits += 1
        if id(error) in seen:
            continue
        seen.add(id(error))
        if error.__cause__ is not None:
            pending.append(iter((error.__cause__,)))
        if isinstance(error, BaseExceptionGroup):
            pending.append(iter(error.exceptions))
        else:
            members.append(error)
    suffix = " [diagnostic limit]" if pending else ""
    title = describe(failure, 90) if isinstance(failure, BaseExceptionGroup) else ""
    budget = (700 - len(title) - len(suffix) - 2 * len(members)) // max(1, len(members))
    return "; ".join(([title] if title else []) +
                     [describe(error, budget) for error in members]) + suffix


def _finalization_error(previous: str | None, stage: str, failure: Exception) -> str:
    detail = f"{stage} failed: {_failure_detail(failure)}"
    if previous:
        detail += f"; previous outcome: {previous}"
    return detail[:1000]


def _fallback_json(value) -> str:
    """Encode already validated primitives without the normal serializer hook."""
    return json.JSONEncoder(ensure_ascii=True, allow_nan=False, indent=2).encode(value) + "\n"


def _load(text: str):
    def invalid(value):
        raise ValueError(f"Nonfinite JSON value: {value}")

    if not isinstance(text, str):
        raise ValueError("Expected canonical JSON text")
    try:
        return json.loads(text, object_pairs_hook=unique_object, parse_constant=invalid)
    except RecursionError as error:
        raise ValueError("Frontend JSON nesting exceeds the supported depth") from error


def _load_request(text: str):
    """Check the original request's UTF-8 byte bound before strict JSON decoding.

    Adapters must use this before rewriting a request, including removing
    Demo-only options. Other JSON documents keep their own size boundaries.
    """
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise ValueError("Request must be JSON text within frontend size limits")
    return _load(text)


class _PolledOperation:
    """Shared polling and terminal publication; producers own acquisition/state.

    Producers validate requests before initialization, publish readings through
    ``_publish_patch`` and call ``_finish`` in their worker's finally block.
    Finalization handles ordinary construction, serialization and event-hook
    failures. It cannot promise recovery from memory exhaustion or process loss.
    """

    def _initialize_operation(
        self, request, *, identity, provenance, ownership, instrument_state="unknown"
    ):
        self._request = request
        self._event_schema_version = request["schema_version"]
        self._operation_id = str(uuid4())
        self._identity_record = identity
        self._provenance = provenance
        self._response_timeout = 120.0
        self._condition = threading.Condition()
        self._cancel = threading.Event()
        self._done = threading.Event()
        # The instrument frontend validates the requested patch count before initialization.
        self._events = queue.SimpleQueue()
        self._pending = None
        self._pending_deadline = None
        self._expired_request = None
        self._decision = None
        self._presentation_pending = False
        self._sequence = 0
        self._result = None
        self._patches = []
        self._patch_data = []
        self._calibration = CalibrationContext("unknown", "Calibration has not been reported")
        self._configuration_data = ()
        self._software_stopped = False
        self._instrument_state = instrument_state
        self._ownership = ownership
        self._owns_instance = False
        self._dispatch_state = "prepared"
        self._dispatch_stage = "admission"
        self._dispatch_error = None
        self._thread = None
        self._initial_result = {
            "schema_version": 1,
            "operation_id": self.operation_id,
            "device_id": identity.device_id,
            "device": asdict(identity),
            "provenance": provenance,
            "mode": request["mode"],
            "workflow": request["workflow"],
            "status": "failed",
            "expected_patch_ids": list(request["patch_ids"]),
            "patches": [],
            "request_json": _json(request),
            # Producers already validated this declaration. Keep it available
            # even when ordinary finalization cannot decode live configuration.
            "configuration": [("event_contract", request["event_contract"])],
            "calibration": asdict(self._calibration),
            "error": "Operation did not complete",
            "software_stopped": False,
            "instrument_state": instrument_state,
            "error_stage": None,
            "error_native_code": None,
        }
        # Validate the recovery template before acquiring ownership. The normal
        # result constructor/serializer are deliberately not used for recovery.
        result_from_json(_fallback_json(self._initial_result))

    def _start_worker(self):
        self._dispatch_stage = "worker_start"
        self._thread = threading.Thread(target=self._run_after_dispatch, daemon=True)
        if not self._ownership.acquire(blocking=False):
            self._dispatch_stage = "instance_busy"
            raise OperationBusyError("An operation already owns this instance")
        self._owns_instance = True
        self._thread.start()

    def _run_after_dispatch(self):
        with self._condition:
            while self._dispatch_state == "starting":
                # Notifications can themselves be interrupted. Rechecking the
                # owned state bounds bootstrap cleanup even after a lost wakeup.
                self._condition.wait(0.1)
            committed = self._dispatch_state == "committed"
        if committed:
            self._run()

    def _emit_started(self):
        self._emit("started", {
            "device_id": self._request["device_id"],
            "provenance": self._provenance,
            "capability_id": self._request["capability_id"],
            # Echoed here as well as in the result's `configuration`, so a
            # client confirms what it opted into at the FIRST event rather
            # than after the operation it affects has finished.
            "event_contract": self._request.get("event_contract", "retained_patch_v1"),
        })

    def _retain_no_dispatch_facts(self, failure_stage, requested_by):
        # These are observed host facts, not native driver error codes. Seed the
        # recovery template too, so an unrelated finalizer hook cannot erase them.
        facts = {"stop_requested_by": requested_by}
        if failure_stage is not None:
            facts["dispatch_failure_stage"] = failure_stage
        self._configuration_data = tuple(sorted((dict(self._configuration_data) | facts).items()))
        self._initial_result["configuration"] = tuple(sorted(
            (dict(self._initial_result["configuration"]) | facts).items()
        ))

    def _finish_without_dispatch(self, status, error, *, failure_stage=None):
        # A late bootstrap can only leave the permanently aborted gate. It can
        # never invoke acquisition, so the I/O lease may be released safely.
        self._dispatch_state = "aborted"
        self._software_stopped = True
        self._initial_result["software_stopped"] = True
        requested_by = "client" if status == "cancelled" else "host_failure"
        self._retain_no_dispatch_facts(failure_stage, requested_by)
        try:
            self._condition.notify_all()
            self._emit_started()
        except Exception as failure:
            status = "failed"
            error = _finalization_error(error, "Startup notification", failure)
            if failure_stage is None:
                self._retain_no_dispatch_facts("notification", requested_by)
        finally:
            self._finish(status, error)

    def _record_dispatch_failure(self, failure):
        # The caller holds the condition. A published result is immutable;
        # a pending result retains the first startup cause and requests cleanup.
        if not self.done:
            if self._dispatch_error is None:
                self._dispatch_error = f"Operation dispatch failed: {_failure_detail(failure)}"
            self._cancel.set()

    @staticmethod
    def _dispatch_outcome(status, error, dispatch_error):
        if dispatch_error is None:
            return status, error
        detail = dispatch_error + (f"; worker outcome: {error}" if error else "")
        if len(detail) > 1000:
            detail = detail[:975] + " [diagnostic truncated]"
        return "failed", detail

    def dispatch(self) -> None:
        """Dispatch this held operation once; callers retain the handle first.

        Ordinary startup failures before the gate opens become drainable failed
        terminal results without device I/O. Committed failures and interruptions
        are re-raised after resolving a proved no-dispatch outcome or cancelling a
        committed worker. A possibly active worker retains its I/O lease until
        its normal cleanup confirms termination; an exception is not that proof.
        """
        admitted = False
        try:
            with self._condition:
                if self._dispatch_state != "prepared":
                    raise OperationBusyError("This operation was already dispatched or resolved")
                # Own this attempt before its state transition, so an interrupt
                # at the following line cannot strand a starting handle.
                admitted = True
                self._dispatch_state = "starting"
            self._start_worker()
            self._dispatch_stage = "commit"
            with self._condition:
                try:
                    if self._cancel.is_set():
                        self._finish_without_dispatch("cancelled", "Cancelled before dispatch")
                    else:
                        # Mark uncertainty before the fallible wakeup. A committed
                        # state is not proof that the waiter received notification.
                        self._dispatch_state = "committed"
                        self._condition.notify_all()
                except BaseException as failure:
                    if self._dispatch_state == "committed":
                        # Record the host failure while the bootstrap is excluded.
                        # Its cancellation is not an operator's decision.
                        self._record_dispatch_failure(failure)
                    raise
        except BaseException as failure:
            if not admitted:
                raise
            with self._condition:
                if self._dispatch_state == "committed":
                    # The condition's exit can also be interrupted after commit.
                    self._record_dispatch_failure(failure)
                    if not self.done:
                        # Always notify again: an interruption may have occurred
                        # after committing state but before waking the worker.
                        self._condition.notify_all()
                elif not self.done:
                    self._cancel.set()
                    self._finish_without_dispatch(
                        "failed", f"Operation dispatch failed: {_failure_detail(failure)}",
                        failure_stage=self._dispatch_stage,
                    )
            if self._dispatch_state == "committed" or not isinstance(failure, Exception):
                raise

    @property
    def operation_id(self) -> str:
        return self._operation_id

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def poll_event_json(self) -> str:
        """Return immediately with one event or empty text; does not advance IO."""
        # Before completion, the condition hides temporary terminal staging and
        # keeps its result/done publication coherent. Completion follows staging
        # rollback or commit, so the stable final queue needs no condition lock.
        locked = not self.done
        if locked and not self._condition.acquire(blocking=False):
            return ""
        try:
            return self._events.get_nowait()
        except queue.Empty:
            return ""
        finally:
            if locked:
                self._condition.release()

    def respond(self, operation_id: str, request_id: str, response: str) -> None:
        """Consume one exact current instruction; stale/wrong responses raise."""
        with self._condition:
            match = self._matching_reply(operation_id, request_id, presentation=False)
            if match is None or not isinstance(response, str) or response not in match[0]:
                raise ValueError("Response does not match the pending operation/instruction")
            if match[1]:
                raise ValueError("Operator response arrived after its deadline")
            self._decision = response
            self._condition.notify_all()

    def _matching_reply(self, operation_id, request_id, *, presentation):
        """Match under the condition, keeping only the last request that timed out.

        The sender may win this lock after the deadline but before the waiter
        wakes. Use the same deadline here so that scheduling cannot admit it.
        """
        if (operation_id != self.operation_id or self._decision is not None
                or self._cancel.is_set()):
            return None
        if self._pending is not None:
            if self._presentation_pending != presentation:
                return None
            pending = self._pending
            expired = time.monotonic() >= self._pending_deadline
        elif self._expired_request is not None:
            kind, pending = self._expired_request
            if kind != presentation:
                return None
            expired = True
        else:
            return None
        if request_id != pending[0]:
            return None
        return pending[1], expired

    def presented(self, operation_id: str, request_id: str, stamp_json: str) -> None:
        """Consume one typed draw-completion response, separate from operator acceptance."""
        if not isinstance(stamp_json, str) or len(stamp_json.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise ValueError("Presentation stamp exceeds its text bound")
        stamp = _decode(PresentationStamp, _load(stamp_json))
        with self._condition:
            match = self._matching_reply(operation_id, request_id, presentation=True)
            if match is None:
                raise ValueError("Presentation does not match the pending operation/request")
            phase, stimulus_id, display_id = match[0]
            if (stamp.phase, stamp.stimulus_id, stamp.display_id) != (
                    phase, stimulus_id, display_id):
                if self._pending is None:
                    raise ValueError("Presentation does not match the pending operation/request")
                raise ValueError("Presentation phase or stimulus/display identity changed")
            if match[1]:
                raise ValueError("Presentation reply arrived after its deadline")
            self._decision = stamp
            self._condition.notify_all()

    def _request_presentation(self, payload):
        """Bounded client rendering exchange on the existing operation condition."""
        _validate_presentation_payload(payload)
        with self._condition:
            if self._cancel.is_set():
                raise InterruptedError("Display presentation cancelled")
            self._pending = (payload["request_id"],
                (payload["phase"], payload["stimulus_id"], payload["display_id"]))
            self._decision, self._presentation_pending = None, True
            self._pending_deadline = time.monotonic() + payload["deadline_ms"]/1000
            try:
                self._emit("presentation_required", payload)
                while self._decision is None and not self._cancel.is_set():
                    remaining = self._pending_deadline - time.monotonic()
                    if remaining <= 0:
                        self._expired_request = (True, self._pending)
                        raise TimeoutError("Controlled presentation timed out")
                    self._condition.wait(remaining)
                if self._cancel.is_set():
                    raise InterruptedError("Display presentation cancelled")
                return self._decision
            finally:
                self._pending, self._decision, self._presentation_pending = None, None, False
                self._pending_deadline = None

    def cancel(self, operation_id: str) -> None:
        """Request cancellation without waiting; completed results stay completed."""
        if operation_id != self.operation_id:
            raise ValueError("Wrong operation ID")
        with self._condition:
            if not self.done:
                self._cancel.set()
                if self._dispatch_state == "prepared":
                    self._finish_without_dispatch("cancelled", "Cancelled before dispatch")
                self._condition.notify_all()

    def close(self) -> None:
        """Nonblocking cancellation request. Poll terminal to confirm completion."""
        self.cancel(self.operation_id)

    def result_json(self) -> str:
        """Return retained scalar result envelope only after terminal completion."""
        if not self.done:
            raise RuntimeError("Operation has not completed")
        if self._result is None:
            raise RuntimeError("Terminal result was not published")
        return self._result

    def _emit(self, kind, payload):
        event = {
            "schema_version": getattr(self, "_event_schema_version", 1),
            "operation_id": self.operation_id,
            "sequence": self._sequence,
            "kind": kind,
            "payload": payload,
        }
        encoded = _json(event)
        if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
            # The wire limit is UTF-8 bytes. Escaping Unicode as ASCII can
            # unnecessarily exceed it; preserve the same JSON values compactly.
            encoded = json.dumps(event, ensure_ascii=False, allow_nan=False,
                                 separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
            raise ValueError("Event exceeds the bounded frame size; a reading this large "
                             "is notified by reference under retained_patch_v1")
        self._events.put(encoded)
        self._sequence += 1

    def _ask(self, kind, message, responses, *, request_id=None, metadata=None):
        with self._condition:
            if self._cancel.is_set():
                return "cancel"
            request_id = str(uuid4()) if request_id is None else request_id
            self._pending, self._decision = (request_id, responses), None
            self._pending_deadline = time.monotonic() + self._response_timeout
            try:
                payload = {
                    "request_id": request_id,
                    "kind": kind,
                    "message": message,
                    "allowed_responses": list(responses),
                    "response_timeout_seconds": self._response_timeout,
                }
                if metadata is not None:
                    # The guard exists so a passthrough cannot collide with the
                    # envelope above; it is not a rule that everything extra is
                    # native. `calibration` is DECODED -- condition names, the
                    # operator action, upstream's wording, the display level --
                    # so namespacing it `native_` would say it came off the wire
                    # unread, which is the opposite of what it is.
                    if any(
                        key not in _INSTRUCTION_METADATA and not key.startswith("native_")
                        for key in metadata
                    ):
                        raise ValueError(
                            "Instruction metadata must be native passthrough or "
                            f"one of {sorted(_INSTRUCTION_METADATA)}"
                        )
                    payload.update(metadata)
                _validate_instruction_payload(payload)
                self._emit("instruction_required", payload)
                while self._decision is None and not self._cancel.is_set():
                    remaining = self._pending_deadline - time.monotonic()
                    if remaining <= 0:
                        self._expired_request = (False, self._pending)
                        raise TimeoutError("Operator response timed out")
                    self._condition.wait(remaining)
                response = "cancel" if self._cancel.is_set() else self._decision
                if response == "cancel":
                    self._cancel.set()
                return response
            finally:
                self._pending, self._decision = None, None
                self._pending_deadline = None

    def _publish_patch(self, patch):
        """Retain a validated reading before attempting its notification."""
        data = asdict(patch)
        _decode(PatchResult, data)
        expected = self._initial_result["expected_patch_ids"]
        observation = patch.observation
        if (
            patch.index >= len(expected)
            or (self._patches and patch.index <= self._patches[-1].index)
            or patch.patch_id != expected[patch.index]
            or observation.operation_id != self.operation_id
            or not _identity_agrees(observation.device,
                self._patches[-1].observation.device if self._patches else self._identity_record)
            or observation.provenance != self._provenance
            or observation.requested_mode != self._initial_result["mode"]
            or observation.actual_mode != self._initial_result["mode"]
            or any(
                p.observation.observation_id == observation.observation_id for p in self._patches
            )
        ):
            raise ValueError("Patch identity or order disagrees with the operation")
        self._patches.append(patch)
        self._patch_data.append(data)
        self._notify_patch(data)

    def _notify_patch(self, data):
        """Notify only after retention; opt-in references never replace canonical data."""
        if self._request.get("event_contract") == "retained_patch_v1":
            self._emit("patch_reference", {
                "index": data["index"],
                "patch_id": data["patch_id"],
                "observation_id": data["observation"]["observation_id"],
            })
        else:
            self._emit("patch", data)

    def _publish_complete_batch(self, observations):
        """Retain the entire validated batch before notifying about any row."""
        ids = self._request["patch_ids"]
        if (self._patches or len(observations) != len(ids)
            or any(item.sample_id != patch_id for item, patch_id in zip(observations, ids))):
            raise ValueError("Native batch count disagrees with the request")
        patches = [PatchResult(index, patch_id, observation)
                   for index, (patch_id, observation) in enumerate(zip(ids, observations))]
        data = [asdict(patch) for patch in patches]
        # Reuse the envelope validator for identity, mode, unique observations
        # and canonical request order before retaining any member of the batch.
        result_from_json(_json({
            **self._initial_result, "status": "completed", "error": None, "patches": data,
        }))
        self._patches.extend(patches)
        self._patch_data.extend(data)
        for row in data:
            self._notify_patch(row)

    def _stage_event(self, kind, payload):
        """Try the ordinary emitter without exposing partial publication.

        The caller holds the condition, so poll cannot consume this temporary
        queue. Even an emitter which enqueues then raises is rolled back here.
        """
        events, sequence = self._events, self._sequence
        staged = queue.SimpleQueue()
        self._events = staged
        try:
            self._emit(kind, payload)
            encoded = staged.get_nowait()
            if not staged.empty():
                raise ValueError("Emitter produced more than one event")
            return encoded
        finally:
            self._events, self._sequence = events, sequence

    def _release_and_complete(self):
        self._pending, self._decision = None, None
        try:
            if self._owns_instance and self._software_stopped is True:
                self._ownership.release()
                self._owns_instance = False
        finally:
            self._done.set()

    def _finish(self, status, error, *, native_failure=None):
        """Publish one terminal plus retained result despite ordinary hook failure.

        ``native_failure`` is the ``(stage, inst_code)`` pair a helper reported, so
        the result carries the failure in a form a client can branch on instead of
        only as a sentence.
        """
        dispatch_error = self._dispatch_error
        status, error = self._dispatch_outcome(status, error, dispatch_error)
        fields = dict(self._initial_result)
        fields["patches"] = list(self._patch_data)
        fields["status"], fields["error"] = status, error
        if native_failure is not None and status != "completed":
            fields["error_stage"], fields["error_native_code"] = native_failure
        try:
            try:
                if status not in ("completed", "failed", "cancelled"):
                    raise ValueError("Invalid operation outcome")
                if status != "completed" and (not isinstance(error, str) or not error.strip()):
                    fields["error"] = f"Operation {status} without a diagnostic"
                if isinstance(fields["error"], str) and len(fields["error"]) > 1000:
                    fields["error"] = fields["error"][:975] + " [diagnostic truncated]"
                if status == "completed" and len(self._patch_data) != len(
                    fields["expected_patch_ids"]
                ):
                    fields["status"] = "failed"
                    fields["error"] = "Operation ended before all requested patches were acquired"
                calibration = _decode(CalibrationContext, asdict(self._calibration))
                fields["calibration"] = asdict(calibration)
                configuration = _decode(tuple[tuple[str, str], ...], self._configuration_data)
                if len({key for key, _ in configuration}) != len(configuration):
                    raise ValueError("Duplicate configuration keys")
                fields["configuration"] = configuration
                fields["software_stopped"] = _decode(bool, self._software_stopped)
                fields["instrument_state"] = _decode(
                    Literal["not_applicable", "unknown", "confirmed_ready"], self._instrument_state
                )
                result = OperationResult(
                    **{
                        **fields,
                        "device": self._identity_record,
                        "expected_patch_ids": tuple(fields["expected_patch_ids"]),
                        "patches": tuple(self._patches),
                        "calibration": calibration,
                        "configuration": configuration,
                    }
                )
                encoded_result = result_to_json(result)
            except Exception as failure:
                fields["status"] = "failed"
                fields["error"] = _finalization_error(
                    fields["error"], "Result finalization", failure
                )
                encoded_result = _fallback_json(fields)

            with self._condition:
                if self._dispatch_error != dispatch_error:
                    # Serialization runs outside the condition. A startup cause
                    # arriving during it must reach the same terminal/result,
                    # without holding the polling lock over a large encoding.
                    fields["status"], fields["error"] = self._dispatch_outcome(
                        fields["status"], fields["error"], self._dispatch_error)
                    encoded_result = _fallback_json(fields)
                payload = self._terminal_payload(fields)
                try:
                    encoded_terminal = self._stage_event("terminal", payload)
                except Exception as failure:
                    fields["status"] = "failed"
                    fields["error"] = _finalization_error(
                        fields["error"], "Terminal publication", failure
                    )
                    encoded_result = _fallback_json(fields)
                    encoded_terminal = _fallback_json(
                        {
                            "schema_version": self._event_schema_version,
                            "operation_id": self.operation_id,
                            "sequence": self._sequence,
                            "kind": "terminal",
                            "payload": self._terminal_payload(fields),
                        }
                    )
                self._result = encoded_result
                self._events.put(encoded_terminal)
                self._sequence += 1
                self._release_and_complete()
        finally:
            # Complete even after a catastrophic failure in primitive recovery.
            # Ownership remains held if the helper's stop was not confirmed;
            # publication itself cannot then be promised.
            with self._condition:
                if not self.done:
                    self._release_and_complete()

    def _terminal_payload(self, fields):
        return {
            "status": fields["status"],
            "completed_patches": len(fields["patches"]),
            "expected_patches": len(fields["expected_patch_ids"]),
            "error": fields["error"],
            # The same failure a client can branch on, on the EVENT stream too: a
            # client watching events and never fetching the result would otherwise
            # have only the prose.
            "error_stage": fields.get("error_stage"),
            "error_native_code": fields.get("error_native_code"),
            "software_stopped": fields["software_stopped"],
            "instrument_state": fields["instrument_state"],
        }
