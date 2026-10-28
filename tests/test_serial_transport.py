"""Direct serial framing against isolated bytes and clocks; no OS ports or device I/O."""

import threading

import pytest

from color_measurement.serial_transport import (
    SerialCancelled,
    SerialChannel,
    SerialProtocolError,
    open_serial_port,
)


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Port:
    def __init__(self, clock, fragments=()):
        self.clock = clock
        self.fragments = list(fragments)
        self.buffer = bytearray()
        self.writes = []
        self.is_open = True
        self.write_timeout = None

    @property
    def in_waiting(self):
        if self.writes and not self.buffer and self.fragments:
            arrived, data = self.fragments[0]
            if self.clock.now >= arrived:
                self.buffer.extend(data)
                self.fragments.pop(0)
        return len(self.buffer)

    def read(self, count):
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        return data

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def close(self):
        self.is_open = False


@pytest.mark.parametrize("ending", [b"\r", b"\n", b"\r\n"])
def test_fragmented_reply_uses_complete_lines_and_one_deadline(ending):
    clock = Clock()
    port = Port(clock, [(0.1, b"RM XYZ" + ending), (0.3, b"OK:0:"),
                        (0.9, b"RM XYZ:1,2,3" + ending)])
    channel = SerialChannel(port, clock=clock)
    channel.begin("RM XYZ", timeout=2)
    assert not channel.synchronized
    # Framing returns every line, including an echo; the CR/CS reply policies refuse it.
    assert channel.line(1) == "RM XYZ"
    assert channel.line(1) == "OK:0:RM XYZ:1,2,3"
    channel.complete()
    assert channel.synchronized and port.writes == [b"RM XYZ\r\n"]
    assert bytes.fromhex(channel.facts()["received_hex"]) == (
        b"RM XYZ" + ending + b"OK:0:RM XYZ:1,2,3" + ending
    )


@pytest.mark.parametrize("count", [0, 1, -1, True, 99, None])
def test_incomplete_or_invalid_write_forbids_reply_completion_and_cleanup_commands(count):
    clock = Clock()
    port = Port(clock)
    channel = SerialChannel(port, clock=clock)

    def incomplete(data):
        port.writes.append(data)
        return count

    port.write = incomplete
    with pytest.raises(SerialProtocolError):
        channel.begin("MEAS,1", timeout=5)
    assert not channel.synchronized and not channel.facts()["write_complete"]
    for call in (lambda: channel.line(5), channel.complete,
                 lambda: channel.begin("RMTS,0", timeout=5)):
        with pytest.raises(SerialProtocolError):
            call()
    assert len(port.writes) == 1
    assert channel.close() and not port.is_open


def test_pending_input_refuses_a_command_without_flushing_the_original():
    clock = Clock()
    port = Port(clock)
    port.buffer.extend(b"late previous response\r\n")
    channel = SerialChannel(port, clock=clock)
    with pytest.raises(SerialProtocolError, match="Unread"):
        channel.begin("M", timeout=5)
    assert not port.writes
    assert bytes.fromhex(channel.facts()["received_hex"]) == b"late previous response\r\n"
    assert not channel.synchronized


@pytest.mark.parametrize("reply", [b"1,2\r\nextra", b"\xff\r\n", b"a" * 4097,
                                   b"a" * 4097 + b"\r\n"])
def test_surplus_nonascii_and_oversized_lines_quarantine_exact_received_input(reply):
    clock = Clock()
    port = Port(clock, [(0, reply)])
    channel = SerialChannel(port, clock=clock)
    channel.begin("RM XYZ", timeout=5)
    with pytest.raises(SerialProtocolError):
        channel.line(5)
        channel.complete()
    assert bytes.fromhex(channel.facts()["received_hex"]) == reply
    assert not channel.synchronized
    with pytest.raises(SerialProtocolError):
        channel.complete()


def test_response_cap_retains_only_its_exact_prefix_and_never_allows_another_command():
    clock = Clock()
    port = Port(clock, [(0, b"12345678\r\n")])
    channel = SerialChannel(port, clock=clock, limit_bytes=8)
    channel.begin("M", timeout=5)
    with pytest.raises(SerialProtocolError, match="cap"):
        channel.line(5)
    assert bytes.fromhex(channel.facts()["received_hex"]) == b"12345678"
    with pytest.raises(SerialProtocolError):
        channel.begin("RMTS,0", timeout=5)
    assert port.writes == [b"M\r\n"]


def test_timeout_never_treats_a_temporary_empty_buffer_as_completion():
    clock = Clock()
    port = Port(clock, [(0.2, b"partial"), (2, b"late\r\n")])
    channel = SerialChannel(port, clock=clock)
    channel.begin("M", timeout=5)
    with pytest.raises(SerialProtocolError, match="Timed out"):
        channel.line(1)
    assert clock.now == pytest.approx(1)
    assert bytes.fromhex(channel.facts()["received_hex"]) == b"partial"
    with pytest.raises(SerialProtocolError):
        channel.line(5)


def test_cancellation_after_completed_write_has_no_reply_or_instrument_abort_claim():
    clock = Clock()
    port = Port(clock)
    cancelled = threading.Event()
    channel = SerialChannel(port, clock=clock, cancel=cancelled)

    def cancel_after_write(data):
        port.writes.append(data)
        cancelled.set()
        return len(data)

    port.write = cancel_after_write
    channel.begin("RMTS,1", timeout=5)
    with pytest.raises(SerialCancelled, match="instrument state is unknown"):
        channel.line(5)
    assert port.writes == [b"RMTS,1\r\n"]
    assert channel.facts()["sent_hex"] == b"RMTS,1\r\n".hex()
    assert channel.facts()["write_complete"]
    assert not channel.synchronized and channel.close()


@pytest.mark.parametrize("witness", [True, None, 0])
def test_failed_close_keeps_owned_port_and_retry_can_confirm_release(witness):
    clock = Clock()
    port = Port(clock, [(0, b"OK00\r\n")])
    channel = SerialChannel(port, clock=clock)
    channel.begin("RMTS,1", timeout=5)
    assert channel.line(5) == "OK00"
    channel.complete()
    assert channel.synchronized
    port.close = lambda: setattr(port, "is_open", witness)
    with pytest.raises(SerialProtocolError, match="release"):
        channel.close()
    assert not channel.closed and channel.transport is port
    for call in (lambda: channel.begin("MEAS,1", timeout=5), lambda: channel.line(5),
                 channel.complete):
        with pytest.raises(SerialProtocolError):
            call()
    assert port.writes == [b"RMTS,1\r\n"]
    port.close = lambda: setattr(port, "is_open", False)
    assert channel.close()


def test_blank_cr_frame_is_preserved_for_model_policy_refusal():
    clock = Clock()
    port = Port(clock, [(0, b"1\r\r2\r")])
    channel = SerialChannel(port, clock=clock)
    channel.begin("RM Spectrum", timeout=5)
    assert channel.line(5) == "1"
    assert channel.line(5) == ""  # The second CR is an empty frame, not a paired LF.
    channel.quarantine()
    with pytest.raises(SerialProtocolError):
        channel.complete()
    assert bytes.fromhex(channel.facts()["received_hex"]) == b"1\r\r2\r"


@pytest.mark.parametrize("recovers", [False, True])
def test_real_pyserial_failed_close_retains_ownership_and_retry_is_only_an_attempt(
    monkeypatch, recovers,
):
    # Constructing Serial with no port is inert. No OS descriptor is opened or closed.
    serialposix = pytest.importorskip("serial.serialposix")
    port = serialposix.Serial(port=None)
    port.is_open, port.fd = True, -101
    (port.pipe_abort_read_w, port.pipe_abort_read_r,
     port.pipe_abort_write_w, port.pipe_abort_write_r) = (-102, -103, -104, -105)
    channel = SerialChannel(port)
    closed = []

    def close(fd):
        closed.append(fd)
        if fd == -101 and (not recovers or closed.count(fd) == 1):
            raise OSError("Original pySerial host close failure")

    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(serialposix.os, "close", close)
            with pytest.raises(OSError, match="Original pySerial"):
                channel.close()
            assert port.is_open and port.fd == -101 and not channel.closed
            assert not channel.synchronized and channel.transport is port
            if recovers:
                assert channel.close() and port.is_open is False
                assert closed == [-101, -101, -102, -103, -104, -105]
            else:
                with pytest.raises(OSError, match="Original pySerial"):
                    channel.close()
                assert port.is_open and not channel.closed and closed == [-101, -101]
    finally:
        # Prevent pySerial's destructor from treating these test operands as OS handles.
        port.fd, port.is_open = None, False


@pytest.mark.parametrize("arrival,accepted", [(1, True), (1.001, False)])
def test_reply_at_exact_deadline_is_checked_once_and_late_reply_stays_refused(arrival, accepted):
    clock = Clock()
    port = Port(clock, [(arrival, b"OK00\r\n")])
    channel = SerialChannel(port, clock=clock)
    channel.begin("M", timeout=5)
    if accepted:
        assert channel.line(1) == "OK00"
        channel.complete()
    else:
        with pytest.raises(SerialProtocolError, match="Timed out"):
            channel.line(1)
    assert clock.now == pytest.approx(1)


@pytest.mark.parametrize("interruption", ["deadline", "cancel"])
def test_reply_read_cannot_hide_deadline_expiry_or_cancellation(interruption):
    clock = Clock()
    port = Port(clock, [(0, b"OK00\r\n")])
    cancelled = threading.Event()
    channel = SerialChannel(port, clock=clock, cancel=cancelled)
    read = port.read

    def interrupted_read(count):
        result = read(count)
        if interruption == "deadline":
            clock.now = 1.01
        else:
            cancelled.set()
        return result

    port.read = interrupted_read
    channel.begin("M", timeout=5)
    with pytest.raises(SerialProtocolError):
        channel.line(1)
    assert bytes.fromhex(channel.facts()["received_hex"]) == b"OK00\r\n"
    assert not channel.synchronized
    with pytest.raises(SerialProtocolError):
        channel.complete()


@pytest.mark.parametrize("field", ["timeout", "deadline", "open-timeout"])
def test_huge_integer_time_inputs_refuse_with_value_error_before_device_io(field):
    clock = Clock()
    port = Port(clock)
    channel = SerialChannel(port, clock=clock)
    enormous = 1 << 20000
    with pytest.raises(ValueError):
        if field == "deadline":
            channel.line(enormous)
        elif field == "open-timeout":
            open_serial_port("COM1", baudrate=115200, write_timeout=enormous)
        else:
            channel.begin("M", timeout=enormous)
    assert not port.writes


@pytest.mark.parametrize("arguments", [dict(port="", baudrate=115200, write_timeout=5),
                                      dict(port="COM1", baudrate=True, write_timeout=5),
                                      dict(port="COM1", baudrate=115200,
                                           write_timeout=float("nan"))])
def test_invalid_open_arguments_refuse_before_importing_or_opening_pyserial(arguments):
    with pytest.raises(ValueError):
        open_serial_port(**arguments)


@pytest.mark.parametrize("stage", ["before_idle", "after_idle", "write_attempt"])
def test_cancel_distinguishes_no_write_from_an_unconfirmed_write_attempt(stage):
    clock, cancelled = Clock(), threading.Event()

    class CancelPort(Port):
        @property
        def write_timeout(self):
            return None

        @write_timeout.setter
        def write_timeout(self, value):
            if stage == "after_idle" and value is not None:
                cancelled.set()

        def write(self, data):
            self.writes.append(data)
            raise SerialCancelled("Unconfirmed transport write")

    port = CancelPort(clock)
    channel = SerialChannel(port, clock=clock, cancel=cancelled)
    if stage == "before_idle":
        cancelled.set()
    with pytest.raises(SerialCancelled):
        channel.begin("RMTS,1", timeout=5)
    assert channel.facts()["sent_hex"] == ""
    assert channel.synchronized is (stage != "write_attempt")
    assert port.writes == ([b"RMTS,1\r\n"] if stage == "write_attempt" else [])
    cancelled.clear()
    channel.cancel = None
    if stage == "write_attempt":
        with pytest.raises(SerialProtocolError, match="reconnect"):
            channel.begin("RMTS,0", timeout=5)
    else:
        port.write = lambda data: len(data)
        channel.begin("RMTS,0", timeout=5)
        port.buffer.extend(b"OK00\r\n")
        assert channel.line(5) == "OK00"
        channel.complete()
    assert channel.close()
