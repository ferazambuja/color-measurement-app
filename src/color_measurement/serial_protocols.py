"""Source-backed CR/CS reply policies over the common byte/number owners.

Adapted from the attributed MATLAB and PR Python owners identified in
docs/direct-serial.md. Parsers never send commands, compute colorimetry or
replace unknown quantity or setting fields with invented values.
"""

from __future__ import annotations

import re

from .decimal_numbers import decimal_integer, decimal_number
from .models import Spectrum
from .serial_transport import SerialProtocolError


class SerialInstrumentError(SerialProtocolError):
    """A complete framed device error; its code does not prove physical idle state."""

    def __init__(self, command, code, raw):
        self.command, self.code, self.raw = command, code, raw
        super().__init__(f"{command} returned instrument error {code}: {raw}")


def _text(payload):
    if (not isinstance(payload, str) or len(payload) > 4096
            or any(ord(char) < 32 or ord(char) > 126 for char in payload)):
        raise SerialProtocolError("Protocol payload requires at most 4096 printable ASCII bytes")
    return payload


def numbers(payload, count=None, *, cs2000=False):
    """Parse complete decimal fields; CS's calculation-error sentinel is unavailable."""
    payload = _text(payload)
    if count is not None and (type(count) is not int or count < 1):
        raise ValueError("A protocol field count must be a positive integer")
    if type(cs2000) is not bool:
        raise ValueError("CS sentinel policy must be explicit boolean")
    fields = payload.split(",")
    if count is not None and len(fields) != count:
        raise SerialProtocolError("Protocol reply has an unexpected number of decimal fields")
    try:
        result = tuple(decimal_number(field.strip()) for field in fields)
    except ValueError as error:
        raise SerialProtocolError(str(error)) from error
    if cs2000 and -9.9999e9 in result:
        raise SerialProtocolError("CS instrument returned its calculation-error sentinel")
    return result


def cr_reply(line, command, *, expected_label=None):
    """Remote 1.36: command-matched success and sign-consistent OK/ER status."""
    match = re.fullmatch(r"(OK|ER):([+-]?[0-9]+):([^:]+):(.*)", _text(line))
    if match is None:
        raise SerialProtocolError("Malformed CR response; check that ECHO is off")
    prefix, number, label, payload = match.groups()
    code = int(number)
    if (prefix == "OK" and code < 0) or (prefix == "ER" and code >= 0):
        raise SerialProtocolError("CR response has inconsistent status framing")
    if code >= 0 and label != (command if expected_label is None else expected_label):
        raise SerialProtocolError("CR response does not match its command")
    if code < 0:
        if command == "M" and code > -500 and label != "M":
            raise SerialProtocolError("CR measurement error does not match M")
        raise SerialInstrumentError(command, code, line)
    return payload, code


def cr_grid(payload):
    """Remote 1.36 §4.3.1.37: returned spacing owns the grid, not optical bandwidth."""
    first, last, step, count = numbers(payload, 4)
    try:
        count = decimal_integer(payload.split(",")[3].strip())
    except ValueError as error:
        raise SerialProtocolError("CR spectrum count must be an exact integer") from error
    if (first <= 0 or last <= first or step <= 0 or not 2 <= count <= 10000
            or count != int(count)
            or abs(first + (count - 1) * step - last) > 1e-8 * max(1, last)):
        raise SerialProtocolError("CR spectrum header has inconsistent start/end/spacing/count")
    return tuple(first + index * step for index in range(int(count)))


def cr_spectrum(header, lines, radiometric):
    """Keep measured geometry; the radiometric total does not verify a per-nm unit."""
    grid = cr_grid(header)
    if not isinstance(lines, (list, tuple)) or len(lines) != len(grid):
        raise SerialProtocolError("CR spectrum does not match its declared sample count")
    kind, integrated, photon = numbers(radiometric, 3)
    try:
        kind = decimal_integer(radiometric.split(",")[0].strip())
    except ValueError as error:
        raise SerialProtocolError("CR radiometric type must be an exact integer") from error
    if kind not in (0, 1, 2, 3):
        raise SerialProtocolError("CR radiometric quantity type must be an integer from 0 to 3")
    quantity = ("radiance", "irradiance", "radiant_intensity", "radiant_flux")[int(kind)]
    unit = ("W/(m^2 sr)", "W/m^2", "W/sr", "W")[int(kind)]
    values = tuple(numbers(line, 1)[0] for line in lines)
    spectrum = Spectrum(grid, values, quantity, "unverified native spectral density units")
    return spectrum, dict(radiometric_type=int(kind), radiometric_quantity=quantity,
                         radiometric_unit=unit, instrument_integrated_reading=integrated,
                         instrument_photon_reading=photon, spectral_density_units_confirmed=False)


def cr_capture_value(payload, kind):
    """Remote 1.36 last-capture scalars; NA is unavailable, not zero or empty text."""
    payload = _text(payload).strip()
    if payload == "NA":
        return None
    if not payload:
        raise SerialProtocolError("CR capture metadata requires nonempty text or NA")
    modes = {
        "syncMode": {"None", "Auto", "Manual", "NTSC", "PAL", "CINEMA"},
        "calibrationMode": {"None", "Matrix", "Match"},
        "legacyMatrixMode": {"Disabled", "Enabled"},
    }
    if kind == "text":
        return payload
    if kind in modes:
        if payload not in modes[kind]:
            raise SerialProtocolError("CR capture mode is outside the documented vocabulary")
        return payload
    if kind in {"milliseconds", "hertz"}:
        suffix = "msec" if kind == "milliseconds" else "Hz"
        match = re.fullmatch(r"(.+)\s+" + suffix, payload)
        if match is None:
            raise SerialProtocolError("CR capture metadata lacks its documented unit suffix")
        value = numbers(match[1].strip(), 1)[0]
    elif kind in {"integer", "positiveInteger"}:
        try:
            value = decimal_integer(payload)
        except ValueError as error:
            raise SerialProtocolError(str(error)) from error
    else:
        raise ValueError("Unknown CR capture scalar kind")
    if value < 0 or (kind == "positiveInteger" and value == 0):
        raise SerialProtocolError("CR capture metadata is outside its native scalar range")
    return value


def cs_reply(line, command, *, acknowledgement=False):
    """CS Rev.7: exact OK00/ERnn framing; errors never carry a payload."""
    if type(acknowledgement) is not bool:
        raise ValueError("Acknowledgement policy must be explicit boolean")
    match = re.fullmatch(r"(OK00|ER[0-9]{2})(?:,(.*))?", _text(line))
    if match is None:
        raise SerialProtocolError("Malformed CS response")
    code, payload = match.groups()
    if code != "OK00":
        if payload is not None:
            raise SerialProtocolError("CS error replies must not contain a payload")
        raise SerialInstrumentError(command, code, line)
    if acknowledgement and line != "OK00":
        raise SerialProtocolError("CS command requires an acknowledgement without data")
    return "" if payload is None else payload


def cs_identity(payload, expected_model):
    """CS Rev.7 p.14: explicitly selected standard model, variation and seven-digit serial."""
    if not isinstance(expected_model, str) or expected_model not in {"CS-2000", "CS-2000A"}:
        raise ValueError("Select CS-2000 or CS-2000A explicitly")
    fields = tuple(field.strip() for field in _text(payload).split(","))
    if (len(fields) != 3 or fields[:2] != (
            expected_model, "1" if expected_model == "CS-2000" else "2")
            or re.fullmatch(r"[0-9]{7}", fields[2]) is None):
        raise SerialProtocolError("CS identity does not match the selected standard model")
    return dict(model=fields[0], variation=int(fields[1]), serial=fields[2])


def cs_duration(payload):
    """MEAS,1 announces 002..242 integer seconds, separately from the completion reply."""
    if not isinstance(payload, str) or re.fullmatch(r"[0-9]{3}", payload) is None:
        raise SerialProtocolError("CS measurement duration requires three decimal digits")
    duration = int(payload)
    if not 2 <= duration <= 242:
        raise SerialProtocolError("CS measurement duration is outside [2, 242] seconds")
    return duration


def cs_conditions(payload):
    """Rev.7 p.44: preserve eight native fields, including integration time in microseconds."""
    numbers(payload, 8, cs2000=True)
    try:
        values = tuple(decimal_integer(field.strip()) for field in payload.split(","))
    except ValueError as error:
        raise SerialProtocolError("CS condition fields require exact integer tokens") from error
    bounds = ((0, 4), (0, 2), (1, 999999999), (0, 1), (0, 1), (0, 2), (0, 2), (0, 10))
    if any(value != int(value) or not low <= value <= high
           for value, (low, high) in zip(values, bounds)):
        raise SerialProtocolError("CS measurement conditions contain undocumented values")
    names = ("speed_mode", "sync_mode", "integration_time_microseconds", "internal_nd_filter",
             "closeup_lens", "external_nd_filter", "measurement_angle", "user_calibration_channel")
    return dict(zip(names, (int(value) for value in values)))


def cs_spectrum(blocks):
    """Four complete MEDR blocks retain 401 signed spectral-radiance samples."""
    if not isinstance(blocks, (list, tuple)) or len(blocks) != 4:
        raise SerialProtocolError("CS spectrum requires four complete MEDR blocks")
    values = tuple(value for block, count in zip(blocks, (100, 100, 100, 101))
                   for value in numbers(block, count, cs2000=True))
    return Spectrum(tuple(float(value) for value in range(380, 781)), values,
                    "spectral_radiance", "W/(m^2 sr nm)")
