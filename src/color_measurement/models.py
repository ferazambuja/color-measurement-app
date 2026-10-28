"""Immutable records: native observations and calculated values stay separate."""

import math
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime
from types import UnionType
from typing import Literal, Union, get_args, get_origin, get_type_hints

Triple = tuple[float, float, float]
SerialEncoding = Literal["utf8", "bytes"]


def _serial_pair(serial: str | None, encoding: SerialEncoding | None) -> None:
    """Validate a native serial without conflating UTF-8 text and escaped bytes."""
    if serial is None and encoding is None:
        return
    if (not isinstance(serial, str) or not serial or "\x00" in serial
            or encoding not in ("utf8", "bytes")):
        raise ValueError("Device serial and encoding must be paired nonempty text or None")
    try:
        serial.encode("utf-8" if encoding == "utf8" else "latin-1")
    except UnicodeError:
        raise ValueError("Device serial cannot be represented by its encoding") from None


def _finite(values: tuple[float, ...]) -> None:
    try:
        valid = all(type(v) in (int, float) and math.isfinite(v) for v in values)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("Scientific values must be finite real numbers, not booleans")


def _text(*values: str) -> None:
    if not all(isinstance(v, str) and v.strip() for v in values):
        raise ValueError("Required text fields must be nonempty strings")


@dataclass(frozen=True)
class Capabilities:
    device_id: str
    modes: tuple[str, ...]
    supports_xyz: bool
    supports_spectrum: bool
    calibration_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.device_id)
        for values in (self.modes, self.calibration_actions):
            if not isinstance(values, tuple) or any(not isinstance(v, str) for v in values):
                raise ValueError("Capability names must be immutable tuples of text")
        if type(self.supports_xyz) is not bool or type(self.supports_spectrum) is not bool:
            raise ValueError("Quantity capabilities must be booleans")


@dataclass(frozen=True)
class DeviceIdentity:
    device_id: str
    model: str
    backend: str
    backend_version: str
    serial: str | None = None
    serial_encoding: SerialEncoding | None = None

    def __post_init__(self) -> None:
        _text(self.device_id, self.model, self.backend, self.backend_version)
        _serial_pair(self.serial, self.serial_encoding)


def identity_agrees(reported, bound) -> bool:
    """Whether a reading's device is the one this operation bound.

    Everything must match except in one direction: an operation that bound no
    serial may receive a reading from an instrument that reports one. The serial
    and encoding form the only pair a reading may add. Requiring equality instead
    would refuse a patch when an instrument reports a serial that was not pinned.

    A serial that WAS pinned is never overridden here: the helper is given it to
    verify against, so a disagreement at this point is a mismatch, not a refinement.
    """
    if reported == bound:
        return True
    return (bound.serial is None
            and replace(reported, serial=None, serial_encoding=None) == bound)


@dataclass(frozen=True)
class CalibrationContext:
    status: Literal["not_applicable", "unknown", "valid", "required", "failed"]
    detail: str

    def __post_init__(self) -> None:
        if self.status not in ("not_applicable", "unknown", "valid", "required", "failed"):
            raise ValueError("Unknown calibration status")
        if not isinstance(self.detail, str):
            raise ValueError("Calibration detail must be text")


@dataclass(frozen=True)
class XYZ:
    """Native XYZ with explicit scaling and colorimetric reference conditions."""

    values: Triple
    unit: str
    scale: str
    observer: str
    illuminant: str

    def __post_init__(self) -> None:
        if not isinstance(self.values, tuple) or len(self.values) != 3:
            raise ValueError("XYZ requires an immutable three-component tuple")
        _finite(self.values)
        _text(self.unit, self.scale, self.observer, self.illuminant)


@dataclass(frozen=True)
class Spectrum:
    """Native samples; signed values and irregular ascending grids are retained."""

    wavelengths_nm: tuple[float, ...]
    values: tuple[float, ...]
    quantity: str
    unit: str

    def __post_init__(self) -> None:
        if not isinstance(self.wavelengths_nm, tuple) or not isinstance(self.values, tuple):
            raise ValueError("Spectrum arrays must be immutable tuples")
        if not self.values or len(self.values) != len(self.wavelengths_nm):
            raise ValueError("Spectrum needs equal, nonempty wavelength and sample arrays")
        _finite(self.wavelengths_nm)
        _finite(self.values)
        if self.wavelengths_nm[0] <= 0 or any(
            b <= a for a, b in zip(self.wavelengths_nm, self.wavelengths_nm[1:])
        ):
            raise ValueError("Wavelengths must be positive and strictly increasing")
        _text(self.quantity, self.unit)


@dataclass(frozen=True)
class ReportedValues:
    xyz: XYZ | None = None
    spectrum: Spectrum | None = None

    def __post_init__(self) -> None:
        if self.xyz is not None and not isinstance(self.xyz, XYZ):
            raise ValueError("Native XYZ must be an XYZ record or None")
        if self.spectrum is not None and not isinstance(self.spectrum, Spectrum):
            raise ValueError("Native spectrum must be a Spectrum record or None")
        if self.xyz is None and self.spectrum is None:
            raise ValueError("An observation must contain at least one native quantity")


@dataclass(frozen=True)
class ComputedValue:
    """A result or an explicit unavailable result; None never means zero."""

    status: Literal["ok", "unavailable"]
    values: Triple | None
    reason: str | None
    algorithm: str
    algorithm_version: str
    observer: str
    illuminant: str
    scale: str
    reference_white: Triple | None = None

    def __post_init__(self) -> None:
        if self.status not in ("ok", "unavailable"):
            raise ValueError("Unknown computation status")
        if self.status == "ok":
            if (
                not isinstance(self.values, tuple)
                or len(self.values) != 3
                or self.reason is not None
            ):
                raise ValueError("Successful computation needs three values and no failure reason")
            _finite(self.values)
        elif self.values is not None or not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("Unavailable computation needs null values and an explicit reason")
        if self.reference_white is not None:
            if not isinstance(self.reference_white, tuple) or len(self.reference_white) != 3:
                raise ValueError("Reference white must contain three values")
            _finite(self.reference_white)
            if any(v <= 0 for v in self.reference_white):
                raise ValueError("Reference white components must be positive")
        _text(self.algorithm, self.algorithm_version, self.observer, self.illuminant, self.scale)


@dataclass(frozen=True)
class ComputedValues:
    xyy: ComputedValue | None = None
    lab: ComputedValue | None = None

    def __post_init__(self) -> None:
        if any(v is not None and not isinstance(v, ComputedValue) for v in (self.xyy, self.lab)):
            raise ValueError("Computed quantities must be ComputedValue records or None")
        if self.xyy is not None and (
            self.xyy.algorithm != "xyz_to_xyy" or self.xyy.reference_white is not None
        ):
            raise ValueError("xyY requires its named algorithm and no reference white")
        if self.lab is not None and (
            self.lab.algorithm != "xyz_to_lab" or self.lab.reference_white is None
        ):
            raise ValueError("Lab requires its named algorithm and an explicit reference white")


@dataclass(frozen=True)
class Observation:
    schema_version: Literal[1]
    observation_id: str
    operation_id: str
    sample_id: str
    acquired_at: str
    provenance: Literal["simulated", "replay", "physical"]
    device: DeviceIdentity
    requested_mode: str
    actual_mode: str
    configuration: tuple[tuple[str, str], ...]
    calibration: CalibrationContext
    backend_reported: ReportedValues
    project_computed: ComputedValues
    raw_backend_output: str
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_observation_context(self)
        if not isinstance(self.backend_reported, ReportedValues):
            raise ValueError("Observation requires a ReportedValues record")
        if not isinstance(self.project_computed, ComputedValues):
            raise ValueError("Observation requires a ComputedValues record")


DISPLAY_CONTEXT_ID_MAX_BYTES = 128
DISPLAY_CONTEXT_DESCRIPTION_MAX_BYTES = 2048


@dataclass(frozen=True)
class ExternalStimulus:
    """Caller-declared display stimulus; neither presented nor verified by this backend."""

    stimulus_id: str
    description: str
    source: Literal["external"] = "external"

    def __post_init__(self) -> None:
        _text(self.stimulus_id, self.description)
        if self.source != "external":
            raise ValueError("Refresh measurements require a declared external stimulus")
        if (len(self.stimulus_id.encode("utf-8")) > DISPLAY_CONTEXT_ID_MAX_BYTES
                or len(self.description.encode("utf-8")) > DISPLAY_CONTEXT_DESCRIPTION_MAX_BYTES):
            raise ValueError("Stimulus identity and description exceed their text bounds")


@dataclass(frozen=True)
class MeasuredScalar:
    """Successful native refresh measurement, distinct from stored or configured rates."""

    value: float
    quantity: Literal["refresh_frequency"] = "refresh_frequency"
    unit: Literal["Hz"] = "Hz"
    native_method: Literal["read_refrate"] = "read_refrate"

    def __post_init__(self) -> None:
        _finite((self.value,))
        if self.value <= 0:
            raise ValueError("A measured refresh frequency must be positive")
        if (self.quantity, self.unit, self.native_method) != (
                "refresh_frequency", "Hz", "read_refrate"):
            raise ValueError("Unknown scalar quantity, unit or measurement method")


@dataclass(frozen=True)
class ScalarObservation:
    """A non-photometric observation with identity and operational context.

    Observer, illuminant, M-condition, XYZ and spectrum are absent. Calibration
    and requested sensor mode remain operational context, not quantity labels.
    """

    schema_version: Literal[1]
    observation_id: str
    operation_id: str
    sample_id: str
    acquired_at: str
    provenance: Literal["simulated", "replay", "physical"]
    device: DeviceIdentity
    requested_mode: str
    actual_mode: str
    configuration: tuple[tuple[str, str], ...]
    calibration: CalibrationContext
    backend_reported: MeasuredScalar
    stimulus: ExternalStimulus
    raw_backend_output: str
    warnings: tuple[str, ...] = ()
    record_kind: Literal["scalar"] = "scalar"

    def __post_init__(self) -> None:
        _validate_observation_context(self)
        if (self.record_kind != "scalar" or not isinstance(self.backend_reported, MeasuredScalar)
                or not isinstance(self.stimulus, ExternalStimulus)):
            raise ValueError("A scalar observation requires measured scalar and stimulus records")


@dataclass(frozen=True)
class TimingStimulus:
    """Declared client-controlled black-to-white display transition."""

    stimulus_id: str
    description: str
    display_id: str
    source: Literal["client_controlled"] = "client_controlled"
    transition: Literal["black_to_white"] = "black_to_white"

    def __post_init__(self) -> None:
        _text(self.stimulus_id, self.description, self.display_id)
        if (len(self.stimulus_id.encode("utf-8")) > DISPLAY_CONTEXT_ID_MAX_BYTES
                or len(self.display_id.encode("utf-8")) > DISPLAY_CONTEXT_ID_MAX_BYTES
                or len(self.description.encode("utf-8")) > DISPLAY_CONTEXT_DESCRIPTION_MAX_BYTES
                or self.source != "client_controlled" or self.transition != "black_to_white"):
            raise ValueError("Invalid controlled timing stimulus")


@dataclass(frozen=True)
class PresentationStamp:
    """Client draw-completion time, never an independently measured optical onset."""

    phase: Literal["black", "white"]
    stimulus_id: str
    display_id: str
    clock_id: str
    presented_at_seconds: float
    source: Literal["client_draw_completed"] = "client_draw_completed"

    def __post_init__(self) -> None:
        _text(self.stimulus_id, self.display_id, self.clock_id)
        _finite((self.presented_at_seconds,))
        if (self.phase not in {"black", "white"} or self.source != "client_draw_completed"
                or self.presented_at_seconds < 0
                or any(len(v.encode("utf-8")) > DISPLAY_CONTEXT_ID_MAX_BYTES
                       for v in (self.stimulus_id, self.display_id, self.clock_id))):
            raise ValueError("Invalid client presentation stamp")


@dataclass(frozen=True)
class MeasuredTiming:
    """Argyll's native marker-relative delay and signed instrument reaction."""

    display_update_delay_ms: int
    instrument_reaction_ms: int
    unit: Literal["ms"] = "ms"
    native_method: Literal["meas_delay"] = "meas_delay"
    time_reference: Literal["white_change_call"] = "white_change_call"

    def __post_init__(self) -> None:
        if (type(self.display_update_delay_ms) is not int
                or not 0 <= self.display_update_delay_ms <= 2147483647
                or type(self.instrument_reaction_ms) is not int
                or not -2147483647 <= self.instrument_reaction_ms <= 2147483647
                or (self.unit, self.native_method, self.time_reference)
                   != ("ms", "meas_delay", "white_change_call")):
            raise ValueError("Invalid native timing result; INT_MIN is the unwritten sentinel")


@dataclass(frozen=True)
class TimingContext:
    """Separate client stamps and worker bounds; their clock epochs are not correlated."""

    presentations: tuple[PresentationStamp, PresentationStamp]
    native_clock_id: Literal["worker_monotonic"]
    method_window_started_seconds: float
    marker_call_started_seconds: float
    marker_call_finished_seconds: float
    method_window_finished_seconds: float

    def __post_init__(self) -> None:
        values = (self.method_window_started_seconds, self.marker_call_started_seconds,
                  self.marker_call_finished_seconds, self.method_window_finished_seconds)
        _finite(values)
        if (self.native_clock_id != "worker_monotonic" or values[0] < 0
                or tuple(sorted(values)) != values
                or values[1] < values[0] + 0.2
                or values[2] > values[0] + 2.0
                or not isinstance(self.presentations, tuple) or len(self.presentations) != 2
                or any(not isinstance(p, PresentationStamp) for p in self.presentations)):
            raise ValueError("Invalid native timing window or presentation context")
        black, white = self.presentations
        if ((black.phase, white.phase) != ("black", "white")
                or black.clock_id != white.clock_id
                or black.presented_at_seconds >= white.presented_at_seconds
                or black.stimulus_id != white.stimulus_id or black.display_id != white.display_id):
            raise ValueError("Timing requires ordered presentations on one declared client clock")


@dataclass(frozen=True)
class TimingObservation:
    """Non-photometric timing record, with native and presentation facts kept separate."""

    schema_version: Literal[1]
    observation_id: str
    operation_id: str
    sample_id: str
    acquired_at: str
    provenance: Literal["simulated", "replay", "physical"]
    device: DeviceIdentity
    requested_mode: str
    actual_mode: str
    configuration: tuple[tuple[str, str], ...]
    calibration: CalibrationContext
    backend_reported: MeasuredTiming
    stimulus: TimingStimulus
    timing_context: TimingContext
    raw_backend_output: str
    warnings: tuple[str, ...] = ()
    record_kind: Literal["timing"] = "timing"

    def __post_init__(self) -> None:
        _validate_observation_context(self)
        if (self.record_kind != "timing" or not isinstance(self.backend_reported, MeasuredTiming)
                or not isinstance(self.stimulus, TimingStimulus)
                or not isinstance(self.timing_context, TimingContext)):
            raise ValueError("A timing observation requires native timing, stimulus and context")
        if any(p.stimulus_id != self.stimulus.stimulus_id
               or p.display_id != self.stimulus.display_id
               for p in self.timing_context.presentations):
            raise ValueError("Timing presentations differ from the declared stimulus")


def _validate_observation_context(self) -> None:
    """One identity/context invariant for colour, scalar and timing observations."""
    if type(self.schema_version) is not int or self.schema_version != 1:
        raise ValueError("Unsupported observation schema version")
    _text(
        self.observation_id,
        self.operation_id,
        self.sample_id,
        self.acquired_at,
        self.requested_mode,
        self.actual_mode,
        self.raw_backend_output,
    )
    if self.provenance not in ("simulated", "replay", "physical"):
        raise ValueError("Unknown observation provenance")
    for value, kind in (
        (self.device, DeviceIdentity),
        (self.calibration, CalibrationContext),
    ):
        if not isinstance(value, kind):
            raise ValueError(f"Observation requires a {kind.__name__} record")
    if datetime.fromisoformat(self.acquired_at).utcoffset() is None:
        raise ValueError("Acquisition timestamp must include its UTC offset")
    if self.requested_mode != self.actual_mode:
        raise ValueError("Unapproved mode fallback")
    if not isinstance(self.configuration, tuple) or any(
        not isinstance(p, tuple)
        or len(p) != 2
        or any(not isinstance(v, str) for v in p)
        for p in self.configuration
    ):
        raise ValueError("Configuration must be immutable text key/value pairs")
    if len({key for key, _ in self.configuration}) != len(self.configuration):
        raise ValueError("Duplicate configuration keys")
    if not isinstance(self.warnings, tuple) or any(
        not isinstance(v, str) for v in self.warnings
    ):
        raise ValueError("Warnings must be an immutable tuple of text")


def _decode(kind: object, value: object) -> object:
    """Strict schema decoder shared by persistence and constructor validation."""
    origin, args = get_origin(kind), get_args(kind)
    if origin in (Union, UnionType):
        reasons = []
        for option in args:
            try:
                return _decode(option, value)
            except (TypeError, ValueError) as error:
                # The None option's own refusal says nothing a caller can act on;
                # the record option's refusal is the reason the value was rejected.
                if option is not type(None):
                    reasons.append(str(error))
        raise ValueError("Value does not match the declared optional record type: "
                         + "; ".join(reasons))
    if origin is Literal:
        if not any(type(value) is type(a) and value == a for a in args):
            raise ValueError(f"Unexpected literal value: {value!r}")
        return value
    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            raise ValueError("Expected an array")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], v) for v in value)
        if len(value) != len(args):
            raise ValueError("Incorrect array length")
        return tuple(_decode(t, v) for t, v in zip(args, value))
    if isinstance(kind, type) and is_dataclass(kind):
        if not isinstance(value, dict):
            raise ValueError("Expected a record object")
        expected = {f.name for f in fields(kind)}
        if value.keys() != expected:
            raise ValueError(f"{kind.__name__} fields differ: {sorted(value.keys() ^ expected)}")
        hints = get_type_hints(kind)
        return kind(**{key: _decode(hints[key], v) for key, v in value.items()})
    if kind is float:
        _finite((value,))
        return value
    if type(value) is not kind:
        raise ValueError(f"Expected {getattr(kind, '__name__', kind)}")
    return value


def observation_from_dict(data: dict) -> Observation:
    """Load schema 1 strictly, refusing unknown fields and malformed values."""
    return _decode(Observation, data)
