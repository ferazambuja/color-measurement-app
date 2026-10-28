"""Bounded direct-instrument byte transport; no discovery or acquisition policy.

Framing and uncertain-stream rules follow the attributed PR Python port and the
current MATLAB PR/CR/CS drivers. See docs/direct-serial.md for source attribution.
Construction is inert. Only open_serial_port opens the explicitly selected path.
"""

from __future__ import annotations

import math
import os
import time


class SerialProtocolError(RuntimeError):
    """A failed host exchange; reconnect when stream synchronization is unknown."""


class SerialCancelled(SerialProtocolError):
    """The host stopped waiting; this does not establish a device abort."""


def _finite_seconds(value):
    if type(value) not in (int, float):
        raise ValueError("Serial time values require finite seconds")
    try:
        value = float(value)
    except OverflowError:
        raise ValueError("Serial time values exceed finite seconds") from None
    if not math.isfinite(value):
        raise ValueError("Serial time values require finite seconds")
    return value


def positive_seconds(value):
    value = _finite_seconds(value)
    if value <= 0:
        raise ValueError("Serial deadlines require finite positive seconds")
    return value


def open_serial_port(port, *, baudrate, write_timeout):
    """Open one explicit 8N1 port exclusively where pySerial supports it.

    No listing, probing, fallback port, flow control or automatic write retry.
    The caller must have authorized physical operation before invoking this.
    """
    if not isinstance(port, str) or not port.strip() or "\x00" in port:
        raise ValueError("Choose one explicit serial port")
    if type(baudrate) is not int or baudrate not in (
            600, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200):
        raise ValueError("Choose a documented serial baud rate")
    write_timeout = positive_seconds(write_timeout)
    try:
        import serial
    except ImportError as error:
        raise RuntimeError("Direct serial instruments require pySerial") from error
    return serial.Serial(
        port=port, baudrate=baudrate, bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
        timeout=0, write_timeout=write_timeout, xonxoff=False, rtscts=False, dsrdtr=False,
        **({"exclusive": True} if os.name == "posix" else {}),
    )


def close_owned_transport(transport):
    """Release one already-owned host port, including before a channel can be initialized."""
    transport.close()
    if transport.is_open is not False:
        raise SerialProtocolError("Serial close did not confirm host port release")
    return True


class SerialChannel:
    """One owned, nonblocking serial stream with finite writes and reply bounds.

    Model policies own complete-response validation. A policy calls complete()
    only after consuming its exact expected reply; an error leaves the channel
    quarantined. pySerial's open flushes the host input buffer once; after that,
    received bytes are never silently discarded before commands.
    Injected transports and clocks exercise the same framing in software tests.
    """

    def __init__(self, transport, *, clock=time, cancel=None, limit_bytes=1048576):
        if type(limit_bytes) is not int or not 1 <= limit_bytes <= 1048576:
            raise ValueError("Serial response cap must be integer bytes in [1, 1048576]")
        self.transport = transport
        self.clock, self.cancel = clock, cancel
        self.limit_bytes = limit_bytes
        self.synchronized = True
        self.closed = False
        self._buffer = bytearray()
        self._received = bytearray()
        self._sent = bytearray()
        self._command = None
        self._write_complete = False
        self._failed = False
        self._optional_lf = False

    def facts(self):
        """Retain exact bounded bytes and host state, including incomplete exchanges."""
        return dict(command=self._command, sent_hex=self._sent.hex(),
                    received_hex=self._received.hex(),
                    write_complete=self._write_complete,
                    synchronized=self.synchronized, port_closed=self.closed)

    def quarantine(self):
        """A malformed or incomplete exchange cannot become ready through another reply."""
        self._failed = True
        self.synchronized = False

    def _check_cancel(self):
        if self.cancel is not None and self.cancel.is_set():
            raise SerialCancelled("Host serial wait cancelled; instrument state is unknown")

    def _collect(self):
        count = self.transport.in_waiting
        if type(count) is not int or count < 0:
            raise SerialProtocolError("Serial transport returned an invalid pending-byte count")
        remaining = self.limit_bytes - len(self._received)
        if count:
            data = self.transport.read(min(count, remaining + 1))
            if not isinstance(data, bytes) or len(data) > min(count, remaining + 1):
                raise SerialProtocolError("Serial transport returned invalid response bytes")
            # Preserve the exact bounded prefix even when the next byte exceeds
            # the cap. An over-limit response can never authorize another command.
            self._received.extend(data[:remaining])
            self._buffer.extend(data[:remaining])
            if len(data) > remaining:
                raise SerialProtocolError("Serial reply exceeded its declared byte cap")
        if self._optional_lf and self._buffer:
            if self._buffer[0] == 10:
                del self._buffer[0]
            self._optional_lf = False

    def _idle(self):
        if self.closed or not self.synchronized:
            raise SerialProtocolError("Serial stream requires an explicitly selected reconnect")
        self._check_cancel()
        try:
            self._collect()
            if self._buffer:
                raise SerialProtocolError("Unread serial input; no command sent or bytes flushed")
        except BaseException:
            self.quarantine()
            raise

    def begin(self, command, *, timeout):
        """Send once; partial or interrupted writes permanently quarantine this stream."""
        timeout = positive_seconds(timeout)
        if (not isinstance(command, str) or not command or len(command) > 128
                or any(ord(char) < 32 or ord(char) > 126 for char in command)):
            raise ValueError("Serial commands require at most 128 printable ASCII characters")
        # A validated attempt owns its own facts, including refusal before a
        # write. The caller has already retained the preceding exchange. Late
        # input belongs to this idle check; prior sent/reply bytes must not be
        # duplicated or labelled as a newly dispatched command.
        self._command = command
        self._sent.clear()
        self._received.clear()
        self._write_complete = False
        self._idle()
        self.synchronized = False
        write_attempted = False
        try:
            self.transport.write_timeout = timeout
            encoded = command.encode("ascii") + b"\r\n"
            self._check_cancel()
            write_attempted = True
            count = self.transport.write(encoded)
            if type(count) is not int or not 0 <= count <= len(encoded):
                raise SerialProtocolError("Serial write returned an invalid byte count")
            self._sent.extend(encoded[:count])
            if count != len(encoded):
                raise SerialProtocolError("Serial write was incomplete; no automatic retry")
            self._write_complete = True
        except SerialCancelled:
            if not write_attempted:
                self.synchronized = True
            else:
                self.quarantine()
            raise
        except BaseException:
            self.quarantine()
            raise

    def line(self, deadline):
        """Read CR/LF/CRLF fragments on one caller-owned monotonic deadline."""
        deadline = _finite_seconds(deadline)
        if self.closed or self._failed or not self._write_complete:
            raise SerialProtocolError("A closed or failed exchange cannot accept another reply")
        try:
            while True:
                self._check_cancel()
                if self.clock.monotonic() > deadline:
                    raise SerialProtocolError("Timed out waiting for a complete serial reply")
                self._collect()
                self._check_cancel()
                observed = self.clock.monotonic()
                if observed > deadline:
                    raise SerialProtocolError("Timed out waiting for a complete serial reply")
                end = next((index for index, byte in enumerate(self._buffer)
                            if byte in (10, 13)), None)
                if end is not None:
                    if end > 4096:
                        raise SerialProtocolError("Serial reply line exceeded 4096 bytes")
                    data = bytes(self._buffer[:end])
                    self._optional_lf = self._buffer[end] == 13
                    del self._buffer[:end + 1]
                    try:
                        value = data.decode("ascii")
                    except UnicodeDecodeError as error:
                        raise SerialProtocolError("Numeric serial reply is not ASCII") from error
                    return value
                if len(self._buffer) > 4096:
                    raise SerialProtocolError("Unterminated serial reply exceeded 4096 bytes")
                if observed == deadline:
                    raise SerialProtocolError("Timed out waiting for a complete serial reply")
                self.clock.sleep(min(0.01, deadline - observed))
        except BaseException:
            self.quarantine()
            raise

    def complete(self):
        """Declare a validated complete reply; refuse already available surplus bytes."""
        if self.closed or self._failed or not self._write_complete:
            raise SerialProtocolError("A closed or incomplete command cannot complete")
        try:
            self._collect()
            if self._buffer:
                raise SerialProtocolError("Serial reply contains unexpected surplus bytes")
        except BaseException:
            self.quarantine()
            raise
        self.synchronized = True

    def close(self):
        """Close only this host port; retain ownership when close does not confirm it."""
        if not self.closed:
            self.quarantine()
            self.closed = close_owned_transport(self.transport)
        return self.closed
