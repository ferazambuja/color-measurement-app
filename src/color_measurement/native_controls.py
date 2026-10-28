"""Typed requests and retained settings for the existing native instrument lifetime.

Field order/types come from cr250_control.h and pr655_control.h. The initialized
C adapter owns model rules, complete-target validation and fresh inventories.
This module performs no acquisition, serial I/O or scientific calculation.
"""

import math
import re
from dataclasses import dataclass, field
from threading import RLock
from types import MappingProxyType

from .argyll_capability import instrument_binding_name

CONTROL_FIELDS = MappingProxyType({
    "pr655": (
        "exposureMilliseconds", "cycles", "syncMode", "syncFrequencyHz",
        "primaryAccessoryId", "addon1Id", "addon2Id", "addon3Id", "apertureId",
        "sensitivityMode", "smartDark", "photometricUnits", "instrumentObserver",
        "shutterMode", "speedModeCode",
    ),
    "cr250": (
        "mode", "exposureMode", "exposureMs", "rangeMode", "range",
        "exposureMultiplier", "syncMode", "syncFrequencyHz", "speed", "accessory",
        "filter1", "filter2", "filter3", "userCalibrationMode", "matrixId", "matchId",
        "maxAutoExposureMs",
    ),
})
# Requested decimal members. PR SK takes whole Hz only (pr655_imp.c:722-725).
_DECIMAL_FIELDS = MappingProxyType({
    "cr250": frozenset({"exposureMs", "syncFrequencyHz", "maxAutoExposureMs"}),
    "pr655": frozenset(),
})
# Reported before/current values. D602 reports PR frequency as decimal Hertz
# (pr655_imp.c:582), so its readback stays decimal while its request is whole Hz.
READBACK_DECIMAL_FIELDS = MappingProxyType({
    "cr250": _DECIMAL_FIELDS["cr250"],
    "pr655": frozenset({"syncFrequencyHz"}),
})
# Setters that are acknowledged but have no documented current-setting query:
# CR MaxAutoExposure (cr250_control.h:76), PR SZ shutter and SG speed
# (pr655_control.h:37-45). They never gain a current or confirmed bit.
ACK_ONLY_FIELDS = MappingProxyType({
    "cr250": frozenset({"maxAutoExposureMs"}),
    "pr655": frozenset({"shutterMode", "speedModeCode"}),
})
CONTROL_ARGUMENT_LIMIT = 4096
_PR_OPTICAL_FIELDS = frozenset({
    "primaryAccessoryId", "addon1Id", "addon2Id", "addon3Id", "apertureId",
})


def field_mask(driver, names):
    """Presence bits of named fields in the native header's declared order."""
    return sum(1 << CONTROL_FIELDS[driver].index(name) for name in names)


def ack_only_mask(driver):
    return field_mask(driver, ACK_ONLY_FIELDS[driver])


def _integer_bounds(driver, name):
    if driver == "pr655" and name == "syncFrequencyHz":
        return 20, 400  # Manual SK, printed p. 110; pr655_imp.c:722-725.
    return 0, (2**31 - 1 if driver == "pr655" and name not in _PR_OPTICAL_FIELDS
               else 2**53 - 1)


@dataclass
class NativeControlState:
    """Session-owned recovery and operation ownership for identified native units.

    Frontend replacement keeps this state. The lock serializes debt updates and
    descriptor admission; per-unit ownership prevents overlapping connections
    to the same model/serial through different frontend instances.
    """

    debt: dict = field(default_factory=dict)
    ownership: dict = field(default_factory=dict)
    lock: object = field(default_factory=RLock)


def native_control_driver(instrument):
    """Use the existing native name authority; compiled availability is a separate fact."""
    binding = instrument_binding_name(instrument)
    for driver, symbols in {
        "pr655": ("instPR655", "instPR670"),
        "cr250": ("instCR100", "instCR250", "instCR300"),
    }.items():
        if binding in {instrument_binding_name(symbol) for symbol in symbols}:
            return driver
    raise ValueError("This binding has no declared native control adapter")


def control_request_fields(driver):
    """Present typed request members; current dynamic limits stay native-owned.

    These bounds describe numeric representation, not a model's supported
    choices. PR SK frequency is the exception: its only setter form is whole
    Hz from 20 to 400. Read-only/unknown settings and fresh inventories are
    retained in the native report and must never become guessed selectable defaults.
    """
    units = {
        "exposureMilliseconds": "ms", "exposureMs": "ms", "maxAutoExposureMs": "ms",
        "syncFrequencyHz": "Hz",
        "cycles": "cycles", "exposureMultiplier": "multiplier",
    }
    help_by_field = {
        "exposureMilliseconds": "0 requests Adaptive exposure. Fixed durations and compatible "
                                "modes depend on the identified model and sensitivity.",
        "exposureMs": "Fixed exposure duration. Use the supported exposure mode "
                      "and reported limits.",
        "syncMode": "Use a synchronization code from the instrument report and manual. "
                    "Frequency and supported combinations depend on that mode.",
        "syncFrequencyHz": "Synchronization frequency in Hz. The selected mode must support it.",
        "shutterMode": "Use a documented shutter code. Acknowledgment does not provide "
                       "current-setting readback.",
        "speedModeCode": "Use a documented speed code. Acknowledgment does not establish "
                         "a current code or a mapping to reported Gain.",
        "maxAutoExposureMs": "Maximum exposure limit. Its setter can be acknowledged, "
                             "but no documented current-setting query confirms it.",
    }
    if driver == "pr655":
        help_by_field.update(
            photometricUnits="0 selects English units; 1 selects Metric units. Measurement units "
                             "are verified separately from this request.",
            instrumentObserver="2 selects the 2° observer; 10 selects the 10° observer. "
                               "Measurement publication requires 2; a 10° setup is retained "
                               "and refused without an automatic reset.",
            syncFrequencyHz="Whole Hz from 20 to 400 for User Frequency synchronization "
                            "(syncMode 3).",
        )
    fields = []
    for name in CONTROL_FIELDS[driver]:
        decimal = name in _DECIMAL_FIELDS[driver]
        minimum, maximum = (0, None) if decimal else _integer_bounds(driver, name)
        label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
        row = dict(key=name, type="number" if decimal else "integer", minimum=minimum,
                   label=label[0].upper() + label[1:],
                   unit=units.get(name, "native code"), default=None, required=False,
                   help="Leave blank to omit a change. "
                        + ("Zero is explicit. " if minimum == 0 else "")
                        + help_by_field.get(name, "The instrument checks supported values "
                                           "and combinations."),
                   exclusive_minimum=False, maximum=maximum)
        fields.append(row)
    return fields


@dataclass(frozen=True)
class NativeControls:
    """Immutable, complete proposed changes; zero remains an explicitly present field.

    A request contains only documented typed field names and native numeric codes.
    Enum labels, arbitrary commands and guessed current values are absent. For
    example, PR speedModeCode=0 requests SG0; it supplies no Gain/speed mapping.
    Model-specific values and dynamic constraints are checked by the C adapter
    before any setter. An empty apply is distinct from a read-only refresh and
    cannot establish recovery from a failed earlier application.
    """

    driver: str
    fields: tuple[tuple[str, int | float], ...]

    def __post_init__(self):
        if (type(self.driver) is not str or self.driver not in CONTROL_FIELDS
                or type(self.fields) is not tuple):
            raise ValueError("Native controls require a supported driver and immutable fields")
        names = CONTROL_FIELDS[self.driver]
        supplied = {}
        for pair in self.fields:
            if (type(pair) is not tuple or len(pair) != 2 or type(pair[0]) is not str
                    or pair[0] not in names
                    or pair[0] in supplied):
                raise ValueError("Native control fields must be known, unique name/value pairs")
            key, value = pair
            if key in _DECIMAL_FIELDS[self.driver]:
                if type(value) not in (int, float):
                    raise ValueError(f"{key} requires a finite native number")
                try:
                    valid = math.isfinite(value) and float(value) == value and value >= 0
                except (OverflowError, ValueError):
                    valid = False
            else:
                low, high = _integer_bounds(self.driver, key)
                valid = type(value) is int and low <= value <= high
            if not valid:
                raise ValueError(f"{key} is outside its exact native numeric contract")
            supplied[key] = value
        if tuple(supplied) != tuple(key for key in names if key in supplied):
            raise ValueError("Native control fields must follow the declared protocol order")
        if len(self.argument().encode("ascii")) > CONTROL_ARGUMENT_LIMIT:
            raise ValueError("Native controls exceed the bounded command argument")

    @classmethod
    def from_changes(cls, driver, changes):
        if (type(changes) is not dict or type(driver) is not str
                or driver not in CONTROL_FIELDS):
            raise ValueError("Native controls require a field object and supported driver")
        changes = changes.copy()
        names = CONTROL_FIELDS[driver]
        if changes.keys() - set(names):
            raise ValueError("Unknown native control field")
        return cls(driver, tuple((name, changes[name]) for name in names if name in changes))

    @property
    def present(self):
        return field_mask(self.driver, (name for name, _ in self.fields))

    def argument(self):
        return ",".join(f"{name}={value!r}" for name, value in self.fields)

    def flags(self):
        return dict(controls_action="apply", controls_fields=self.argument())
