"""Typed, bounded IO shared by native helper software checks and operations.

No scientific conversion is performed; upstream ipatch fields retain their meaning.
"""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .decimal_numbers import decimal_number
from .display_types import LIMIT as DISPLAY_TYPE_LIMIT
from .json_values import unique_object
from .native_controls import CONTROL_FIELDS

# Native CR/PR tracing owns actual-size buffers under an aggregate byte ceiling.
# Their support adapters emit 1024-byte chunks, including an empty exchange.
# Settings followed by acquisition can publish two independently counted traces.
NATIVE_RAW_MAX_EXCHANGES = 256
NATIVE_RAW_MAX_BYTES = 1024 * 1024
NATIVE_RAW_MAX_TOTAL_BYTES = 4 * 1024 * 1024
NATIVE_RAW_CHUNK_BYTES = 1024
NATIVE_CONTROL_MAX_ROWS = 1024
NATIVE_CR_CONTROL_MAX_ROWS = 256
NATIVE_CR_CONTROL_MAX_REPLIES = 128
NATIVE_TEXT_MAX_BYTES = 4096
# CR RM Spectrum and PR M5 bins arrive in chunks of at most this many values.
# CR's engine parses at most NATIVE_CR_SPECTRUM_MAX_SAMPLES; PR's count is fixed
# per model (argyll_description.PR_GRID_CONTRACTS).
NATIVE_SPECTRUM_CHUNK_VALUES = 100
NATIVE_CR_SPECTRUM_MAX_SAMPLES = 10000
NATIVE_MESSAGE_MAX_BYTES = 65536
NATIVE_RAW_MAX_EVENTS = (NATIVE_RAW_MAX_TOTAL_BYTES // NATIVE_RAW_CHUNK_BYTES
                         + NATIVE_RAW_MAX_EXCHANGES)
# A CR report can carry an inventory for each control and 128 parsed replies.
# Bound both settings and capture traces without rejecting a permitted report.
NATIVE_CONTROL_MAX_EVENTS = max(
    2 * NATIVE_CONTROL_MAX_ROWS,
    len(CONTROL_FIELDS["cr250"]) * (1 + NATIVE_CR_CONTROL_MAX_ROWS)
    + NATIVE_CR_CONTROL_MAX_REPLIES,
)
MAX_OPERATION_EVENTS = (64 + DISPLAY_TYPE_LIMIT
                        + 2 * (NATIVE_RAW_MAX_EVENTS + NATIVE_CONTROL_MAX_EVENTS))
# Cancellation is cooperative and two-phase, mirroring the uicallback poll
# points in Argyll 3.5.0 spectro/inst.h:1120-1142. Acknowledging a cancel and
# cleaning the instrument up are separate costs: cleanup runs the driver's
# del(), which for a SpectroScan moves the scanning table. One budget cannot
# serve both, and neither belongs to a per-device module.
CANCEL_ACK_TIMEOUT = 0.5
CANCEL_CLEANUP_TIMEOUT = 2.0
# A cancellation the instrument outran is a fact about the reading, not only a message.
# Upstream discards the keystroke outright when the read succeeded
# (spotread.c:2293-2294, `else ch = '0'`), which is sufficient there because the
# operator, the controller and the record writer are one process and the operator is
# watching. Ours is interactive too -- the terminal event carries the same fact live --
# but it is ALSO written to a record another client reads later, so the fact is carried
# as a structured key beside its human-readable warning, the way calibration warnings
# carry a condition code beside their message. Prose alone is a weak pin for clients.
LATE_CANCELLATION_KEY = "cancellation_requested_after_delivery"


def with_late_cancellation(configuration):
    """Return `configuration` with the late-cancellation fact recorded."""
    return tuple(sorted((dict(configuration) | {LATE_CANCELLATION_KEY: "true"}).items()))


class ProtocolError(ValueError):
    """The helper violated the versioned protocol contract."""


def _closed_pipe(error: OSError) -> bool:
    # CPython subprocess._stdin_write documents Windows EINVAL for an exited
    # child or a closed stdin pipe (bpo-19612, bpo-30418).
    return isinstance(error, BrokenPipeError) or (
        sys.platform == "win32" and error.errno == errno.EINVAL
    )


def invalid_constant(value: str) -> None:
    raise ProtocolError(f"Non-JSON numeric constant: {value}")


def finite(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError("Expected a finite number")
    try:
        result = float(value)
    except OverflowError as error:
        raise ProtocolError("Number exceeds binary64 range") from error
    if not math.isfinite(result):
        raise ProtocolError("Expected a finite number")
    return result


@dataclass(frozen=True)
class NativeSpectrum:
    start_nm: float
    end_nm: float
    norm: float
    samples: tuple[float, ...]

    @property
    def wavelengths_nm(self) -> tuple[float, ...]:
        step = (self.end_nm - self.start_nm) / (len(self.samples) - 1)
        return tuple(self.start_nm + i * step for i in range(len(self.samples)))


@dataclass(frozen=True)
class NativeReading:
    mtype: int
    mcond: int
    xyz_valid: bool
    xyz: tuple[float, float, float] | None
    spectrum: NativeSpectrum | None
    duration_seconds: float

    @classmethod
    def parse(
        cls,
        message: dict,
        *,
        provenance: str = "synthetic",
        mode: str = "synthetic_reflective",
        calibration_context: str = "synthetic_not_calibrated",
        expected_mtype: int = 5,
    ) -> NativeReading:
        if (
            message.get("provenance") != provenance
            or message.get("argyll_version") != "3.5.0"
            or message.get("project_computed") != {}
            or message.get("calibration_context") != calibration_context
            or message.get("requested_mode") != mode
            or message.get("confirmed_mode") != mode
        ):
            raise ProtocolError("Unexpected provenance, mode, version or computed values")
        try:
            native = message["backend_reported"]
            mtype, mcond, valid = (native[k] for k in ("mtype", "mcond", "XYZ_v"))
            if (
                type(mtype) is not int
                or mtype != expected_mtype
                or type(mcond) is not int
                or mcond not in range(6)
                or type(valid) is not int
                or valid not in (0, 1)
            ):
                raise ProtocolError("Unsupported type/condition or invalid XYZ validity")
            xyz = None
            if valid:
                if not isinstance(native["XYZ"], list) or len(native["XYZ"]) != 3:
                    raise ProtocolError("XYZ needs three coordinates")
                xyz = tuple(finite(v) for v in native["XYZ"])
            elif native["XYZ"] is not None:
                raise ProtocolError("Invalid XYZ storage must be omitted, not converted to zeros")
            spectrum = None
            raw = native["spectrum"]
            if raw is not None:
                count = raw["spec_n"]
                if (
                    type(count) is not int
                    or not 2 <= count <= 601
                    or not isinstance(raw["samples"], list)
                    or len(raw["samples"]) != count
                ):
                    raise ProtocolError("Invalid active spectral count")
                start, end, norm = (
                    finite(raw[k]) for k in ("wavelength_start_nm", "wavelength_end_nm", "norm")
                )
                if start <= 0 or end <= start or norm <= 0:
                    raise ProtocolError("Invalid spectral grid or normalization")
                spectrum = NativeSpectrum(
                    start, end, norm, tuple(finite(v) for v in raw["samples"])
                )
            duration = finite(native["duration"])
            if duration < 0 or (xyz is None and spectrum is None):
                raise ProtocolError("Invalid duration or missing quantities")
            return cls(mtype, mcond, bool(valid), xyz, spectrum, duration)
        except (KeyError, TypeError) as error:
            raise ProtocolError("Incomplete native result") from error


@dataclass(frozen=True)
class NativeEvent:
    operation_id: str
    sequence: int
    event: str
    reading: NativeReading | None = None
    error_code: str | None = None


class NativeDiagnosticCapture:
    """One opt-in bounded binary stderr original, independent of scientific settings.

    Construction is inert. The shared supervisor opens the exclusive destination,
    drains stderr even after the cap, and closes this capture during cleanup.
    Metadata remains accessible when an operation publishes no valid result.
    """

    MAX_BYTES = 1048576
    SINK_CLEANUP_SECONDS = 0.25

    def __init__(self, target, *, debug_level, limit_bytes=65536):
        if type(debug_level) is not int or not 1 <= debug_level <= 8:
            raise ValueError("Native debug level must be an integer in [1, 8]")
        if type(limit_bytes) is not int or not 1 <= limit_bytes <= self.MAX_BYTES:
            raise ValueError("Native diagnostic cap must be integer bytes in [1, 1048576]")
        self.target = Path(target).absolute()
        self.debug_level = debug_level
        self.limit_bytes = limit_bytes
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._writer = None
        self._writer_done = threading.Event()
        self._recording_finished = False
        self._sink_timeout_reported = False
        self._control = self._later_control = None
        self._control_delivered = False
        self._stream = None
        self._file_hash = hashlib.sha256()
        self._head = bytearray()
        self._tail = bytearray()
        self._closed_original = None
        self._written = 0
        self._received = self._retained = 0
        self._state = "prepared"
        self._error = None
        self._drain_complete = self._file_complete = False
        self.worker_started = False

    def fail(self, error):
        with self._lock:
            detail = f"{type(error).__name__}: {error}"
            self._error = detail if self._error is None else f"{self._error}; {detail}"

    def not_started(self, reason=None):
        with self._lock:
            closed_empty = (
                self._state == "complete" and not self.worker_started
                and self._received == self._retained == self._written == 0
                and self._writer_done.is_set() and self._file_complete
            )
            if self._state == "prepared" or closed_empty:
                self._state, self._error = "not_started", reason
                self._drain_complete = self._file_complete = False

    def open(self):
        with self._lock:
            if self._state != "prepared":
                raise ValueError("Native diagnostic capture was already consumed")
            self._state = "capturing"
        try:
            stream = self.target.open("xb", buffering=0)
        except BaseException as error:
            with self._lock:
                self._state, self._error = "not_started", f"{type(error).__name__}: {error}"
            raise
        with self._lock:
            self._stream = stream

    def start_writer(self):
        with self._lock:
            if self._writer is not None:
                return
            self._writer = threading.Thread(target=self._write_original, daemon=True,
                                            name="native-diagnostic-sink")
            self._writer.start()

    def record(self, chunk):
        # The stderr reader never waits on filesystem I/O. This one bounded
        # buffer remains authoritative even when the optional sink blocks.
        with self._condition:
            if self._state != "capturing" or self._recording_finished:
                return
            self._received += len(chunk)
            head_limit = self.limit_bytes // 2
            count = min(len(chunk), max(0, head_limit - len(self._head)))
            self._head.extend(chunk[:count])
            self._tail.extend(chunk[count:])
            tail_limit = self.limit_bytes - head_limit
            del self._tail[:-tail_limit]
            self._retained = len(self._head) + len(self._tail)
            self._condition.notify_all()

    def _write_original(self):
        first_control = None
        later_control = None
        try:
            while True:
                with self._condition:
                    while not self._recording_finished:
                        self._condition.wait()
                    if self._written == self._retained:
                        break
                    data = self._closed_original[self._written:]
                count = self._stream.write(data)
                with self._lock:
                    self._file_hash.update(data[:count])
                    self._written += count
                if count != len(data):
                    raise OSError("Incomplete native diagnostic write")
            self._stream.flush()
            os.fsync(self._stream.fileno())
        except BaseException as error:
            self.fail(error)
            if not isinstance(error, Exception):
                first_control = error
        finally:
            try:
                self._stream.close()
                if self._stream.closed is not True:
                    raise OSError("Native diagnostic file closure was not confirmed")
            except BaseException as error:
                self.fail(error)
                if not isinstance(error, Exception):
                    later_control = error
            with self._lock:
                self._file_complete = self._written == self._retained and self._error is None
                self._control = first_control or later_control
                self._later_control = later_control if first_control is not None else None
                self._writer_done.set()

    def finish(self, *, drain_complete):
        with self._condition:
            if self._state not in {"capturing", "incomplete"} or self._stream is None:
                return
            self._drain_complete = drain_complete
            self._recording_finished = True
            self._closed_original = bytes(self._head + self._tail)
            self._state = "incomplete"
            self._condition.notify_all()
        try:
            self.start_writer()
            self._writer.join(timeout=self.SINK_CLEANUP_SECONDS)
        except BaseException as error:
            self.fail(error)
            raise
        with self._lock:
            if not self._writer_done.is_set():
                if not self._sink_timeout_reported:
                    self.fail(TimeoutError(
                        "Native diagnostic sink did not stop within bounded cleanup"
                    ))
                    self._sink_timeout_reported = True
                self._file_complete = False
            self._state = ("complete" if self._drain_complete and self._file_complete
                           and self._error is None else "incomplete")
            control = self._control if not self._control_delivered else None
            if control is not None:
                self._control_delivered = True
                if self._later_control is not None:
                    raise control from self._later_control
                raise control

    def _segments(self):
        if not self._recording_finished or not self._retained:
            return []
        if self._received <= self.limit_bytes:
            return [dict(offset=0, length=self._retained)]
        segments = [dict(offset=0, length=len(self._head))] if self._head else []
        if self._tail:
            segments.append(dict(offset=self._received-len(self._tail), length=len(self._tail)))
        return segments

    def snapshot(self):
        with self._lock:
            captured = self._state in {"complete", "incomplete"}
            return dict(
                schema_version=2, kind="native_stderr", state=self._state,
                retention="head_tail", segments=self._segments(),
                path=self.target.name, debug_level=self.debug_level, limit_bytes=self.limit_bytes,
                received_bytes=self._received, retained_bytes=self._retained,
                written_bytes=self._written,
                truncated=self._received > self._retained,
                sha256=(hashlib.sha256(self._closed_original).hexdigest()
                        if captured else None),
                file_sha256=(self._file_hash.hexdigest()
                             if captured and self._writer_done.is_set() else None),
                sink_stopped=self._stream is None or self._writer_done.is_set(),
                drain_complete=self._drain_complete, file_complete=self._file_complete,
                worker_started=self.worker_started,
                error=self._error,
            )

    def original_bytes(self):
        with self._lock:
            return (self._closed_original if self._state in {"complete", "incomplete"} else None)


class HelperSession:
    """Own exactly one helper; bound IO, waits and cleanup."""

    def __init__(self, command: list[str], *, timeout: float = 2.0, env: dict | None = None,
                 diagnostics: NativeDiagnosticCapture | None = None):
        self.timeout = timeout
        if diagnostics is not None and not isinstance(diagnostics, NativeDiagnosticCapture):
            raise ValueError("Use the shared native diagnostic capture")
        self.diagnostics = diagnostics
        if diagnostics is not None:
            diagnostics.open()
        try:
            self.process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env,
            )
        except BaseException as original:
            if diagnostics is not None:
                try:
                    diagnostics.finish(drain_complete=True)
                    diagnostics.not_started(f"{type(original).__name__}: {original}")
                except BaseException as cleanup:
                    raise original from cleanup
            raise
        if diagnostics is not None:
            diagnostics.worker_started = True
        # A caller may pause between yielded statuses while the helper finishes.
        # A full startup catalogue and its statuses must fit even while the
        # caller pauses. Acquisition clients separately bound whole sessions.
        self.messages: queue.Queue = queue.Queue(maxsize=MAX_OPERATION_EVENTS + 1)
        self.reader_error: str | None = None
        self.stderr_error: str | None = None
        self._stderr_eof = False
        self.stderr = bytearray()
        self.operation_id = ""
        self.sequence = 0
        self.state = "idle"
        self.cancel_sent = False
        self.cancel_lost_race = False
        #: How the helper was OBSERVED to stop, recorded by close(): "exited" when
        #: it had already ended, "terminated" or "killed" when this session had to
        #: end it. None until close() runs. Only an observed end is recorded.
        self.stopped: str | None = None
        self.last_raw = ""
        self.threads = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for thread in self.threads:
            thread.start()
        if self.diagnostics is not None:
            self.diagnostics.start_writer()

    def _read_stdout(self) -> None:
        try:
            while True:
                line = self.process.stdout.readline(NATIVE_MESSAGE_MAX_BYTES + 1)
                if len(line) > NATIVE_MESSAGE_MAX_BYTES or (line and not line.endswith(b"\n")):
                    raise ProtocolError("Oversized or truncated helper line")
                self.messages.put(line or None, timeout=0.2)
                if not line:
                    return
        except (OSError, ValueError, queue.Full) as error:
            self.reader_error = str(error) or "Helper output queue overflow"

    def _read_stderr(self) -> None:
        try:
            while chunk := self.process.stderr.read(1024):
                self.stderr.extend(chunk)
                del self.stderr[:-4096]
                if self.diagnostics is not None:
                    self.diagnostics.record(chunk)
            self._stderr_eof = True
        except (OSError, ValueError) as error:
            self.stderr_error = f"{type(error).__name__}: {error}"
            if self.diagnostics is not None:
                self.diagnostics.fail(error)

    def send(self, action: str, argument: str = "") -> None:
        # "begin" and "complete" belong to the synthetic helper only
        # (tools/native_argyll/synthetic_helper.c, driven by
        # tools/native_argyll/protocol.py), which exercises transport framing without
        # an instrument. "cancel" is shared by synthetic and acquisition sessions.
        # "acknowledge", "skip" and "navigate" are the generic ladder's operator
        # replies (tools/native_argyll/session.c): a calibration condition met or
        # declined, and the eight sheet navigation vectors derived from the
        # fiducials the operator sighted. "read_allowance" is the client
        # deadline reply, not an operator action.
        if action not in {"begin", "complete", "cancel",
                          "acknowledge", "skip", "navigate", "read_allowance", "presented"}:
            raise ProtocolError("Unsupported action")
        # Eight decimal numbers, comma separated. Exponent form is accepted because
        # repr(float) emits it -- repr(1e-5) is "1e-05" -- and strtod on the other
        # side reads it; rejecting it here would fail on a legitimate small vector.
        number = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
        pattern = (f"{number}(?:,{number}){{7}}" if action == "navigate" else
                   number if action == "read_allowance" else r"[a-z0-9_-]{1,32}")
        if argument and not re.fullmatch(pattern, argument):
            raise ProtocolError(f"Invalid {action} argument")
        if action == "navigate" and not argument:
            raise ProtocolError("Navigation needs its eight vectors")
        if action == "read_allowance" and (
                not argument or len(argument) > 255 or not 0 < float(argument) < float("inf")):
            raise ProtocolError("Read allowance needs finite positive seconds")
        try:
            self.process.stdin.write(
                f"v1 {self.operation_id} {action}{' ' + argument if argument else ''}\n".encode()
            )
            self.process.stdin.flush()
        except OSError as error:
            if action != "begin" or not _closed_pipe(error):
                raise
            # An argv refusal can beat the first command as well as the first
            # read. Keep the OSError contract, with the same startup diagnostic.
            raise type(error)(
                error.errno, "Helper refused the initial command" + self._stderr_detail()
            ) from error
        if action == "cancel":
            self.cancel_sent = True

    def _stderr_detail(self) -> str:
        self.threads[1].join(timeout=min(self.timeout, 0.2))
        diagnostic = self.stderr.decode("utf-8", errors="replace").strip()
        if self.stderr_error:
            diagnostic += f" [stderr preview incomplete: {self.stderr_error}]"
        return f": {diagnostic}" if diagnostic else ""

    def begin(self, operation_id: str, scenario: str) -> None:
        """Start a synthetic-helper scenario; acquisition binds through its argv."""
        if self.state != "idle" or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", operation_id):
            raise ProtocolError("Invalid operation start")
        self.operation_id = operation_id
        self.send("begin", scenario)

    def receive_message(self, *, timeout: float | None = None) -> dict:
        """Read one bounded, unambiguous JSON object; higher layers own its lifecycle."""
        if self.reader_error:
            raise ProtocolError(self.reader_error)
        try:
            line = self.messages.get(timeout=self.timeout if timeout is None else timeout)
        except queue.Empty as error:
            raise TimeoutError("Native helper response timed out") from error
        if line is None:
            # A rejected command can exit before emitting protocol JSON. Preserve
            # its bounded diagnostic (for example, an unsupported stub fixture)
            # instead of turning every startup refusal into an opaque EOF.
            raise ProtocolError("Helper exited before the expected event" + self._stderr_detail())
        try:
            text = line.decode("utf-8")
            message = json.loads(
                text, object_pairs_hook=unique_object, parse_constant=invalid_constant,
                parse_float=decimal_number,
                # %.17g emits native double negative zero as the integer token
                # -0. Preserve its sign without changing ordinary integer headers.
                parse_int=lambda value: decimal_number(value) if value == "-0" else int(value),
            )
        except (ValueError, UnicodeError, RecursionError) as error:
            raise ProtocolError("Malformed JSON event") from error
        if not isinstance(message, dict):
            raise ProtocolError("Event must be an object")
        self.last_raw = text
        return message

    def receive(self) -> NativeEvent:
        message = self.receive_message()
        if (
            type(message.get("protocol_version")) is not int
            or message["protocol_version"] != 1
            or message.get("provenance") != "synthetic"
            or message.get("operation_id") != self.operation_id
            or type(message.get("sequence")) is not int
            or message["sequence"] != self.sequence
        ):
            raise ProtocolError("Wrong event version, provenance, operation or sequence")
        name = message.get("event")
        allowed = {
            "idle": {"started"},
            "started": {"ready", "calibration_required"},
            "ready": {"result", "error", "cancelled"},
            "calibration_required": {"calibration_complete", "error", "cancelled"},
        }
        if name not in allowed.get(self.state, set()):
            raise ProtocolError("Unexpected or late event after terminal state")
        if self.cancel_sent and name in {"result", "calibration_complete"}:
            # Upstream specifies a window in which an abort is ignored: the
            # uicallback's inst_triggered purpose discards its return value
            # (Argyll 3.5.0 spectro/inst.h:1136). A completed reading after a
            # cancel request therefore means the request lost that race, not
            # that the helper misbehaved. Argyll's own callers branch on the
            # call's return code and never discard data a driver returned, so
            # the helper's terminal event stands. A genuinely late event after
            # a terminal one is still refused by the state table above.
            self.cancel_lost_race = True
        reading = NativeReading.parse(message) if name == "result" else None
        code = message.get("code") if name == "error" else None
        if name == "error" and (not isinstance(code, str) or not code):
            raise ProtocolError("Error event needs a code")
        event = NativeEvent(self.operation_id, self.sequence, name, reading, code)
        self.sequence += 1
        self.state = name
        return event

    def finish(self) -> None:
        """A terminal event must be followed by a clean exit, not a late result."""
        if self.state not in {"result", "error", "cancelled", "calibration_complete"}:
            raise ProtocolError("Cannot finish before a terminal event")
        self.finish_messages()

    def finish_messages(self, *, expected_returncode: int = 0) -> None:
        """Verify EOF and exit after a caller has validated its terminal message."""
        try:
            self.process.stdin.close()
        except OSError as error:
            # A cancel that lost its race to the helper's exit was tolerated when
            # it was sent (instrument.py stop()), but its bytes stayed buffered,
            # and closing flushes them again. The exit code below is the verdict.
            if not _closed_pipe(error):
                raise
        if self.process.wait(timeout=self.timeout) != expected_returncode:
            raise ProtocolError("Helper exit code disagrees with its terminal event")
        for thread in self.threads:
            thread.join(timeout=self.timeout)
        if self.reader_error or any(t.is_alive() for t in self.threads):
            raise ProtocolError(self.reader_error or "Reader did not stop")
        if self.messages.get(timeout=self.timeout) is not None or not self.messages.empty():
            raise ProtocolError("Late output after terminal event")

    def close(self) -> None:
        try:
            self._close_owned_process()
        except BaseException as original:
            if self.diagnostics is not None:
                try:
                    self.diagnostics.finish(drain_complete=self._stderr_eof)
                except BaseException as cleanup:
                    raise original from cleanup
            raise
        if self.diagnostics is not None:
            self.diagnostics.finish(drain_complete=self._stderr_eof)

    def _close_owned_process(self) -> None:
        stopped = "exited"
        if self.process.poll() is None:
            self.process.terminate()
            stopped = "terminated"
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1)
                stopped = "killed"
        if self.stopped is None:
            self.stopped = stopped
        for thread in self.threads:
            thread.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except OSError as error:
                # The owned child has exited. Retrying a buffered stdin flush
                # must not mask its original failure or prevent closing output.
                if stream is not self.process.stdin or not _closed_pipe(error):
                    raise

    def __enter__(self) -> HelperSession:
        return self

    def __exit__(self, *_args) -> None:
        try:
            self.close()
        except BaseException as cleanup:
            if len(_args) > 1 and _args[1] is not None:
                raise _args[1] from cleanup
            raise
