"""Shared Argyll acquisition through its instrument vtable.

Selection, calibration, spot/strip/sheet/chart acquisition and result validation
use one session helper for all linked drivers, including i1Pro. Capabilities are
advisory; the initialized driver's check_mode/set_mode confirm each request.
Source-backed driver differences belong in small native policies or patches, not
separate family supervisors. This module does not enumerate or authorize hardware.

Chart navigation follows the pinned chartread geometry; explicit strip direction
maps returned positions to caller order. Automatic target/row recognition and
DTP51 padding/alignment correlation are not implemented here.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

from .argyll_capability import (
    CALIBRATION_DEFERRABLE_MASK,
    CAPABILITY2,
    FILTERS,
    MEASUREMENT_CONDITIONS,
    MODE_FLAGS,
    XCALIBRATION_STANDARDS,
    decode_modes,
    expected_measurement_type,
    filter_for,
    illumination_of,
    instrument_binding_name,
    mode_word,
    parse_mode_name,
    quantity_contract,
    workflow_kind,
)
from .argyll_description import PR_GRID_CONTRACTS, validate_native_identity
from .colorimeter_correction import (
    CalibrationSpectralSet,
    CorrectionError,
    CorrectionMatrix,
    correction_material,
    correction_material_json,
    parse_correction,
    read_correction,
)
from .display_types import DisplayTypeSelection, catalogue, validate_row
from .mode_acceptance import validate_baseline_mode_checks
from .models import (
    XYZ,
    CalibrationContext,
    ComputedValues,
    DeviceIdentity,
    ExternalStimulus,
    MeasuredScalar,
    MeasuredTiming,
    Observation,
    PresentationStamp,
    ReportedValues,
    ScalarObservation,
    SerialEncoding,
    Spectrum,
    TimingContext,
    TimingObservation,
    TimingStimulus,
    _decode,
    _serial_pair,
)
from .native_controls import (
    CONTROL_FIELDS,
    READBACK_DECIMAL_FIELDS,
    NativeControls,
    ack_only_mask,
    native_control_driver,
)
from .native_protocol import (
    MAX_OPERATION_EVENTS,
    NATIVE_CONTROL_MAX_ROWS,
    NATIVE_CR_CONTROL_MAX_REPLIES,
    NATIVE_CR_CONTROL_MAX_ROWS,
    NATIVE_CR_SPECTRUM_MAX_SAMPLES,
    NATIVE_RAW_CHUNK_BYTES,
    NATIVE_RAW_MAX_BYTES,
    NATIVE_RAW_MAX_EXCHANGES,
    NATIVE_RAW_MAX_TOTAL_BYTES,
    NATIVE_SPECTRUM_CHUNK_VALUES,
    NATIVE_TEXT_MAX_BYTES,
    HelperSession,
    NativeDiagnosticCapture,
    NativeSpectrum,
    ProtocolError,
    _closed_pipe,
    finite,
)

#: Events the generic ladder publishes (``tools/native_argyll/session.c``).
EVENTS = frozenset({
    "display_type", "connected", "instruction_required", "calibration_condition", "xy_location",
    "calibration_ready", "calibration_retry", "refresh_frequency", "presentation_required",
    "display_timing", "result",
    "strip_patch", "xy_patch", "chart_patch",
    "saved_readings", "stored_refresh_rate", "instrument_event", "error", "terminal",
    # A driver support adapter's retained facts (native_facts.h): one per attempt,
    # then each native reply in order. Only drivers with an adapter send them.
    "native_facts", "native_reply", "native_setting", "native_write", "native_spectrum",
    "native_raw", "native_record",
    "native_control", "native_accessory", "native_aperture", "native_control_row",
    "native_control_choice", "native_control_reply",
    "read_allowance_required",
})

#: Facilities each workflow genuinely cannot proceed without.
#:
#: NO workflow lists a trigger. The helper negotiates one from the capability word
#: the way every upstream application does (``chartread.c:1412-1432``), because
#: ``dtp51.c:809`` advertises ``inst2_switch_trig`` and nothing else -- requiring
#: ``prog_trig`` here made the DTP51 unattachable, which is a code change per
#: instrument and therefore the whole thing this module exists to avoid.
#:
#: ``xy_holdrel`` is likewise absent: upstream guards every use of it
#: (``chartread.c:1071``, ``:1142``, ``:1344``) rather than requiring it, and so
#: does the ladder. Only the two the sheet cannot be located without are listed.
WORKFLOW_FACILITIES = {
    "refresh_frequency": ("emis_refr_meas",),
    "display_timing": ("meas_disp_update",),
    "spot": (),
    "strip": (),
    "xy": ("xy_locate", "xy_position"),
    # A stored chart is read without a trigger at all (inst.h:877-878).
    "chart": (),
}

MAX_PATCHES = 4096   # matches CM_MAX_PATCHES in session.c
MAX_SHEETS = 64      # matches CM_MAX_SHEETS in session.h

#: A helper that never terminates must not grow this process without limit. One
#: sheet or pass is at most MAX_PATCHES patches plus its prompts and status lines;
#: this bounds the whole session generously and still refuses a runaway.
MAX_SESSION_EVENTS = MAX_PATCHES + MAX_OPERATION_EVENTS


@dataclass(frozen=True)
class Configuration:
    """What is asked of the instrument beyond the mode, applied before the trigger.

    Every field is capability-gated by the helper, and a request the instrument
    cannot honour is refused rather than quietly dropped -- a reading taken under
    conditions other than the ones asked for is not the reading that was asked for.

    ``filter`` defaults to whatever the mode name declares: ``reflective_spot_m2``
    already says "U.V. cut filter", so it need not be repeated. Set it only to
    request a condition no mode name spells, such as D65.
    """

    highres: bool = False
    #: XYZ alone from an instrument that could also return a spectrum: the helper
    #: binds without ``inst_mode_spectral``. A driver whose spectral read costs time
    #: and checks of its own (the CR-250's) then skips them.
    xyz_only: bool = False
    averages: int = 0                     # readings averaged into one; 0 = the driver's own
    xcalstd: str = "none"                 # a key of XCALIBRATION_STANDARDS
    display_type: DisplayTypeSelection | None = None
    filter: str | None = None             # a key of FILTERS; None = whatever the mode says
    #: A colorimeter correction, read with ``colorimeter_correction.read_correction``.
    #: Without one, the driver's factory/display-type calibration remains in use.
    #: A suitable display-specific correction can reduce spectral mismatch; its
    #: absence alone does not establish that the reported XYZ is invalid.
    #:
    #: The file's own ``instrument`` field is NOT checked against the instrument
    #: being driven, and upstream does not check it either (``spotread.c:1680``
    #: applies whatever it was given). It cannot be checked reliably: the field is
    #: free text that may carry an observer prefix -- the shipped
    #: ``DTP-94_1964_10_Dell_2408W.ccmx`` says ``"1964_10 Xrite DTP94"`` -- and is
    #: not one of the spellings ``inst_enum()`` matches. Compare it yourself when
    #: it matters; it is preserved verbatim on the parsed object.
    correction: CorrectionMatrix | CalibrationSpectralSet | None = None
    refresh_rate: float | None = None  # 0 invalidates; otherwise a requested 5..150 Hz override
    native_controls: NativeControls | None = None

    def __post_init__(self) -> None:
        if (self.native_controls is not None
                and not isinstance(self.native_controls, NativeControls)):
            raise ValueError("Native control changes require an immutable NativeControls request")
        if self.native_controls is not None and (
            self.native_controls.driver == "cr250"
            and any(name in {"matrixId", "matchId", "userCalibrationMode"}
                    for name, _ in self.native_controls.fields)
            and (self.display_type is not None or self.correction is not None)
        ):
            raise ValueError("Native calibration controls and generic calibration selections "
                             "are alternative requests")
        # TYPE before range or membership. The frontend descriptor gives every
        # option a type -- boolean, integer, choice -- and a value of the wrong
        # one used to get past here on its way to a Python error much later:
        # `highres="yes"` survived validation and raised `invalid literal for
        # int()` inside the operation, after `started`, as a failed result
        # carrying an interpreter message. The others were refused, but by
        # accident and in the wrong words: `averages="5"` reported "Unknown option
        # for this instrument: '<=' not supported between instances of 'int' and
        # 'str'", which names the wrong fault.
        #
        # `type(...) is` rather than isinstance, because bool IS an int in Python
        # and `averages=True` otherwise means "average one reading" -- a client
        # that wired a checkbox to the wrong control would be obeyed rather than
        # told.
        if self.refresh_rate is not None and (type(self.refresh_rate) not in (int, float)
                or (self.refresh_rate != 0 and not 5 <= self.refresh_rate <= 150)
                or not math.isfinite(self.refresh_rate)):
            raise ValueError("refresh_rate is null, 0 to invalidate, or finite Hz in [5, 150]")
        if type(self.highres) is not bool:
            raise ValueError("highres is a boolean")
        if type(self.xyz_only) is not bool:
            raise ValueError("xyz_only is a boolean")
        if type(self.averages) is not int:
            raise ValueError("averages is an integer number of readings")
        if not isinstance(self.xcalstd, str):
            raise ValueError("xcalstd is one of the calibration standard names")
        if (self.display_type is not None
                and not isinstance(self.display_type, DisplayTypeSelection)):
            raise ValueError("display_type is a named DisplayTypeSelection, or null")
        if self.filter is not None and not isinstance(self.filter, str):
            raise ValueError("filter is one of the filter names, or null")
        if self.xcalstd not in XCALIBRATION_STANDARDS:
            raise ValueError(f"Not an X-Rite calibration standard: {self.xcalstd!r}")
        if self.xyz_only and self.xcalstd not in {"none", "native"}:
            raise ValueError("Calibration-standard conversion requires spectral output; "
                             "turn off XYZ only")
        if self.filter is not None and self.filter not in FILTERS:
            raise ValueError(f"Not an ArgyllCMS filter: {self.filter!r}")
        if not 0 <= self.averages <= 1000:
            raise ValueError("Averaging is bounded")
        if self.correction is not None and not isinstance(
            self.correction, CorrectionMatrix | CalibrationSpectralSet
        ):
            raise ValueError(
                "A correction is a CorrectionMatrix or a CalibrationSpectralSet, "
                "from colorimeter_correction.read_correction"
            )

    @classmethod
    def from_options(cls, options: dict, *, display_types=None,
                     controls_driver=None) -> Configuration:
        """Build one of these from a request's ``options`` object.

        Every field is a JSON scalar except ``correction``, which is a parsed
        object this class will not accept as a dict. It arrives as::

            {"correction": {"path": "/.../DTP94_Dell_2408W.ccmx"}}
            {"correction": {"text": "CCMX\n..."}}

        and the KIND is not stated: the dispatch is on the file's own CGATS
        identifier, exactly as ``spotread -X file`` does it, so a client that
        picked a file does not have to know whether it got a matrix or a spectral
        set. ``text`` exists because a client on the other end of a request
        channel has bytes rather than a path this process can open.

        Without a supplied correction, the driver's factory/display-type
        calibration remains in use. A suitable correction can reduce mismatch
        for the actual display spectrum.
        """
        if not isinstance(options, dict):
            raise ValueError("Options must be an object")
        supplied = dict(options)
        controls = supplied.pop("native_controls", None)
        if controls is not None:
            supplied["native_controls"] = NativeControls.from_changes(controls_driver, controls)
        if supplied.get("display_type") is not None:
            supplied["display_type"] = DisplayTypeSelection.from_catalogue(
                display_types, supplied["display_type"])
        correction = supplied.pop("correction", None)
        if correction is not None and supplied.get("display_type") is not None:
            selected = supplied["display_type"]
            row = next(row for row in display_types["entries"] if row["id"] == selected.choice_id)
            if row["kind"] in {"ccmx", "ccss"}:
                raise ValueError("An installed display correction and an explicit correction "
                                 "are alternative selections")
        if correction is not None:
            supplied["correction"] = cls._correction(correction)
        try:
            return cls(**supplied)
        except TypeError as error:
            raise ValueError(f"Unknown option for this instrument: {error}") from error

    @staticmethod
    def _correction(request) -> CorrectionMatrix | CalibrationSpectralSet:
        if not isinstance(request, dict) or set(request) not in (
                {"path"}, {"text"}, {"text", "source"}):
            raise ValueError(
                'A correction is {"path": ...} or {"text": ...} naming a .ccmx or .ccss'
            )
        try:
            if "text" in request:
                if not isinstance(request["text"], str):
                    raise ValueError("A correction's text must be a string")
                if "source" in request:
                    source = request["source"]
                    if (not isinstance(source, dict)
                            or set(source) != {"path", "sha256", "material_sha256"}
                            or not isinstance(source["path"], str) or not source["path"].strip()
                            or source["sha256"] != hashlib.sha256(
                                request["text"].encode("utf-8")).hexdigest()
                            or source["material_sha256"] != hashlib.sha256(
                                correction_material_json(request["text"]).encode("utf-8")
                            ).hexdigest()):
                        raise ValueError(
                            "Correction source provenance differs from captured material",
                        )
                return parse_correction(request["text"])
            if not isinstance(request["path"], str):
                raise ValueError("A correction's path must be a string")
            return read_correction(request["path"])
        except CorrectionError as error:
            raise ValueError(f"Not a usable colorimeter correction: {error}") from error
        except OSError as error:
            raise ValueError(f"The correction file could not be read: {error}") from error

    def flags(self, mode: str) -> dict:
        """The ladder flags this configuration becomes, for ``mode``."""
        if (self.xcalstd != "none"
                and illumination_of(mode) not in {"reflection", "s_reflection"}):
            raise ValueError("X-Rite calibration standards apply only to reflective measurements")
        declared = filter_for(mode)
        chosen = declared if self.filter is None else FILTERS[self.filter]
        if declared != FILTERS["unknown"] and chosen != declared:
            raise ValueError("The filter conflicts with the condition in the requested mode")
        if (chosen != FILTERS["unknown"]
                and illumination_of(mode) not in {"reflection", "s_reflection"}):
            raise ValueError("Reflective filters do not apply to this measurement mode")
        flags = {
            "highres": int(self.highres),
            "filter": chosen,
            "xcalstd": XCALIBRATION_STANDARDS[self.xcalstd],
        }
        if self.refresh_rate is not None:
            flags["refresh_rate"] = float(self.refresh_rate)
        if self.xyz_only:
            flags["xyz_only"] = 1
        if self.averages:
            flags["averages"] = self.averages
        if self.display_type is not None:
            flags["display_type"] = self.display_type.index
            flags["display_catalogue_id"] = self.display_type.catalogue_id
            flags["display_choice_id"] = self.display_type.choice_id
        if self.native_controls is not None:
            flags.update(self.native_controls.flags())
        return flags


@dataclass(frozen=True)
class Location:
    """A point on an X-Y table, in the instrument's own millimetre coordinates."""

    x: float
    y: float


@dataclass(frozen=True)
class Patch:
    """One reading exactly as the driver reported it; nothing is computed here."""

    mtype: int
    mcond: int
    xyz: tuple[float, float, float] | None
    spectrum: NativeSpectrum | None
    duration_seconds: float
    index: int | None = None


@dataclass(frozen=True)
class StoredRefreshRate:
    """An explicit get_refr_rate state query, never a measured Hz observation.

    ``native_code`` is None, with status ``not_advertised``, when the configured
    word lacks inst2_get_refresh_rate: the getter was not called, so no code exists.
    """

    status: str
    native_code: int | None
    value: float | None
    unit: str = "Hz"
    native_method: str = "get_refr_rate"

    def __post_init__(self):
        if (self.native_code is not None and (type(self.native_code) is not int
                or not 0 <= self.native_code <= 0xffffffff)
                or self.unit != "Hz" or self.native_method != "get_refr_rate"):
            raise ValueError("Invalid stored refresh method, unit or native code")
        if self.native_code is None:
            expected = "not_advertised"
        elif self.native_code == 0:
            expected = "available" if self.value is not None else "invalid"
        else:
            expected = {0x050000: "unsupported", 0x110000: "needs_calibration",
                        0x0e0000: "undetermined"}.get(self.native_code & 0xff0000, "failed")
        if self.status != expected:
            raise ValueError("Stored refresh status contradicts its native code")
        if self.status == "available":
            if (type(self.value) not in (int, float) or self.value <= 0
                    or not self.value <= float.fromhex("0x1.fffffffffffffp+1023")):
                raise ValueError("Stored refresh value must be finite positive Hz")
        elif self.value is not None:
            raise ValueError("Unavailable stored refresh has no numeric value")


@dataclass(frozen=True)
class Measurement:
    instrument: str
    serial: str | None
    mode: str
    workflow: str
    provenance: str
    spectral: bool
    confirmed_routes: tuple[str, ...]
    patches: tuple[Patch, ...] = ()
    locations: tuple[Location, ...] = ()
    calibration: dict | None = None
    stored: int | None = None       # inst_stat_savdrd bits, when asked for
    #: Which colorimeter correction the DRIVER ACCEPTED: "none", "matrix" or
    #: "spectral". Reported by the helper rather than inferred from what was
    #: requested, because `set_disptype` "clears col_cor_mat() and
    #: col_cal_spec_set()" (inst.h:843-845) without saying so -- passing a .ccmx
    #: is not evidence the reading was corrected.
    correction: str = "none"
    events: tuple[dict, ...] = field(default=(), repr=False)
    strip_direction: str | None = None
    #: What the driver itself retained about this attempt, from its support
    #: adapter, or None when the driver has none. Carried as the driver sent it;
    #: see ``native_facts``.
    native_facts: dict | None = None
    native_controls: dict | None = None
    refresh_frequency: MeasuredScalar | None = None
    stored_refresh: StoredRefreshRate | None = None
    mode_snapshot: dict | None = None
    display_timing: MeasuredTiming | None = None
    timing_context: TimingContext | None = None
    #: How the helper process was observed to stop (HelperSession.stopped).
    worker_stop: str | None = None
    serial_encoding: SerialEncoding | None = None

    def __post_init__(self):
        _serial_pair(self.serial, self.serial_encoding)

    @property
    def has_readings(self) -> bool:
        """Whether the driver delivered colour, scalar or timing readings."""
        return (bool(self.patches) or self.refresh_frequency is not None
                or self.display_timing is not None)


class InstrumentError(RuntimeError):
    """A stage the helper named, kept as the helper named it."""

    def __init__(self, stage: str, native_code: int, *, events=(),
                 native_text: str | None = None, native_text_truncated: bool = False):
        super().__init__(f"{stage} (native code {native_code})")
        self.stage = stage
        self.native_code = native_code
        self.events = tuple(events)
        #: The driver's own words for a failure the DRIVER returned, as upstream
        #: prints them (``interp_error``, spotread.c:397). Bounded by the helper,
        #: which says when it cut them. Never parsed: facts travel in native_facts.
        self.native_text = native_text
        self.native_text_truncated = native_text_truncated
        #: The driver's retained facts for the failed attempt, when it has an adapter.
        self.native_facts: dict | None = None
        self.native_controls: dict | None = None
        #: Who ended the attempt: "helper" when the helper reported this stage
        #: itself, "supervisor" when this client stopped waiting and interrupted
        #: it. A cancellation the helper did not acknowledge is the latter.
        self.raised_by = "helper"
        #: How the helper process was observed to stop (HelperSession.stopped), or
        #: "unknown" when a started helper's end was not confirmed; None when no
        #: helper was started.
        self.worker_stop: str | None = None
        #: A stop request and the driver's failure are independent facts. A
        #: read_sample error may arrive after cancellation without acknowledging it.
        self.stop_requested_by: str | None = None
        # Present only after a complete validated read and a consistent terminal
        # exchange. A subsequent cleanup failure must not discard those data.
        self.measurement: Measurement | None = None


#: The lists a support adapter sends one item per event after its ``native_facts``
#: head: event name, the head field giving its count (in ``capture``, or at the top
#: level for replies' historical place), the index/count fields, and each item's
#: fields with their accepted types.
_NATIVE_LISTS = (
    ("native_reply", "replies", "reply", {
        "status": ("OK", "ER"), "code": int, "context": str, "value": str}),
    ("native_setting", "settings", "setting", {
        "key": str, "value": (str, type(None)),
        "presence": ("present", "not_applicable", "unknown"), "driver_set": bool}),
    ("native_write", "writes", "write", {
        "key": str, "before": str, "target": str, "after": (str, type(None)),
        "confirmed": bool}),
    # A refused read's spectrum, as the instrument sent it, in bounded chunks.
    ("native_spectrum", "spectrum_chunks", "spectrum_chunk", {
        "offset": int, "values": list}),
)


#: Most bins each spectrum producer emits; its chunk-count ceiling follows from it.
_NATIVE_SPECTRUM_MAX_SAMPLES = MappingProxyType({
    "cr250": NATIVE_CR_SPECTRUM_MAX_SAMPLES,
    "pr655": max(count for count, _, _ in PR_GRID_CONTRACTS.values()),
})


def _native_item(event: dict, fields: dict) -> dict | None:
    item = {}
    for key, accepted in fields.items():
        value = event.get(key)
        if isinstance(accepted, tuple) and all(isinstance(a, str) for a in accepted):
            if value not in accepted:
                return None
        elif accepted is int or accepted is bool:
            if type(value) is not accepted:
                return None
        elif not isinstance(value, accepted):
            return None
        item[key] = value
    return item


_CONTROL_DETAILS = frozenset({
    "native_accessory", "native_aperture", "native_control_row", "native_control_choice",
    "native_control_reply",
})
_CAPTURE_DETAILS = frozenset({name for name, *_ in _NATIVE_LISTS} | {"native_record"})


def _native_envelopes(events):
    """Refuse a counted detail that its own report head did not precede.

    The adapters emit settings rows, choices and replies after ``native_control``,
    capture lists and records after ``native_facts``, and raw exchanges after
    either. Each joiner reads only its last head's tail, so a detail elsewhere
    would otherwise be skipped rather than refused.
    """
    current = None
    for event in events:
        name = event.get("event")
        if name in {"native_control", "native_facts"}:
            current = name
        elif (name == "native_raw" and current is None
              or name in _CONTROL_DETAILS and current != "native_control"
              or name in _CAPTURE_DETAILS and current != "native_facts"):
            raise ProtocolError(f"Native {name} arrived outside its counted report")


def _cr_text_fields(item):
    """CR parsed strings use its printable, 4096-byte line contract."""
    return all(not isinstance(value, str) or (len(value) <= NATIVE_TEXT_MAX_BYTES
               and all(32 <= ord(char) <= 126 for char in value)) for value in item.values())


def _native_raw(events, *, driver, count):
    """Join exact original CR/PR bytes without interpreting malformed wire replies.

    A trace admits 256 exchanges and 4 MiB total, with at most 1 MiB per reply.
    Both support adapters emit 1024-byte contiguous hex chunks.
    An empty exchange still has one event. Status, completion and original bytes
    are independent retained facts, not evidence of a successful setting.
    """
    if type(count) is not int or not 0 <= count <= NATIVE_RAW_MAX_EXCHANGES:
        raise ProtocolError("Invalid native raw exchange count")
    fields = dict(command=str, complete=bool, error=int, byte_total=int)
    replies = []
    current = None
    offset = 0
    chunks = []
    retained_bytes = 0
    for event in events:
        if event.get("event") != "native_raw":
            continue
        item = _native_item(event, fields)
        index = event.get("exchange_index")
        total = event.get("exchange_count")
        position = event.get("byte_offset")
        raw = event.get("raw_hex")
        if (item is None or event.get("driver") != driver
                or type(index) is not int or type(total) is not int or total != count
                or type(position) is not int or position != offset
                or index != len(replies) or index >= count
                or not 0 <= item["byte_total"] <= NATIVE_RAW_MAX_BYTES
                or not -(2**31) <= item["error"] < 2**31
                or not item["command"] or "\0" in item["command"]
                or any(not 32 <= ord(char) <= 126 for char in item["command"])
                or len(item["command"]) > (64 if driver == "cr250" else 31)
                or not isinstance(raw, str) or not re.fullmatch(r"[0-9a-f]*", raw)
                or len(raw) != 2 * min(NATIVE_RAW_CHUNK_BYTES, item["byte_total"] - offset)):
            raise ProtocolError("Native raw chunks are malformed or out of order")
        if current is None:
            retained_bytes += item["byte_total"]
            if retained_bytes > NATIVE_RAW_MAX_TOTAL_BYTES:
                raise ProtocolError("Native raw trace exceeds its aggregate byte ceiling")
            current = item
        elif item != current:
            raise ProtocolError("Native raw exchange attribution changed between chunks")
        chunks.append(raw)
        offset += len(raw) // 2
        if offset == current["byte_total"]:
            replies.append(dict(current, raw_hex="".join(chunks)))
            current, offset, chunks = None, 0, []
    if current is not None or len(replies) != count:
        raise ProtocolError("Native raw exchanges are incomplete")
    return replies


def _cr_record_size(value):
    """Minimum wire bytes for id,name[,type], excluding its synthetic key.

    The parser normalizes exact decimal/exponent IDs. A shorter exponent token
    can fit when normalized decimal digits would not; raw trace keeps the actual
    spelling. Bound the shortest exact token, rather than guess its original size.
    """
    digits = str(value["id"])
    significand = digits.rstrip("0")
    size = len(digits)
    if significand and len(significand) < size:
        size = min(size, len(significand) + 1 + len(str(size - len(significand))))
    return size + 1 + len(value["name"]) + (
        0 if value["type"] is None else 1 + len(value["type"])
    )


def _cr_record(value, *, key=None):
    """Validate one original counted CR record, preserving exact IDs and text."""
    if (not isinstance(value, dict) or set(value) != {"key", "id", "name", "type"}
            or type(value["id"]) is not int or not 0 <= value["id"] <= 2**53 - 1
            or not isinstance(value["key"], str) or not value["key"]
            or key is not None and value["key"] != key
            or not isinstance(value["name"], str) or not value["name"]
            or value["type"] is not None and (
                not isinstance(value["type"], str) or not value["type"])):
        raise ProtocolError("CR native record is malformed")
    if (not _cr_text_fields(value) or _cr_record_size(value) > NATIVE_TEXT_MAX_BYTES
            or "," in value["name"] or value["type"] is not None and "," in value["type"]):
        raise ProtocolError("CR native record text exceeds its wire envelope")
    return dict(value)


def _cr_capture_records(capture, settings, events):
    """Reconstruct saved record semantics from bounded, counted wire events."""
    count = capture.get("record_count")
    if (type(count) is not int or not 0 <= count <= 17
            or "matched_records" in capture or "range_record" in capture):
        raise ProtocolError("Invalid CR native capture record count or inline records")
    matched, range_record = [], None
    last_slot = -1
    received = 0
    for event in events:
        if event.get("event") != "native_record":
            continue
        slot, role = event.get("slot"), event.get("role")
        if (event.get("driver") != "cr250"
                or type(event.get("record_index")) is not int
                or event["record_index"] != received
                or type(event.get("record_count")) is not int or event["record_count"] != count
                or received >= count or type(slot) is not int):
            raise ProtocolError("CR native records are malformed or out of order")
        if role == "matched":
            if not last_slot < slot < len(settings) or slot >= 16 or range_record is not None:
                raise ProtocolError("CR native matched record slot is invalid or repeated")
            record = _cr_record(event.get("record"), key=settings[slot]["key"])
            if any(previous["key"] == record["key"] for previous in matched):
                raise ProtocolError("CR native matched record key is repeated")
            matched.append(record)
            last_slot = slot
        elif role == "range":
            if slot != 0 or range_record is not None:
                raise ProtocolError("CR native range record slot is invalid or repeated")
            range_record = _cr_record(event.get("record"), key="Range")
        else:
            raise ProtocolError("Unknown CR native capture record role")
        received += 1
    if received != count:
        raise ProtocolError("CR native capture records are incomplete")
    return dict(capture, matched_records=matched, range_record=range_record)


def _control_values(value, *, driver, requested=False):
    fields = CONTROL_FIELDS[driver]
    if (not isinstance(value, dict) or set(value) != {"present", *fields}
            or type(value["present"]) is not int
            or not 0 <= value["present"] < 1 << len(fields)):
        raise ProtocolError("Malformed native control value/presence structure")
    changes = {}
    for index, name in enumerate(fields):
        item = value[name]
        if not value["present"] & (1 << index):
            if item is not None:
                raise ProtocolError("Absent native control value must stay null")
        else:
            number = finite(item)
            if name not in READBACK_DECIMAL_FIELDS[driver] and (
                number != item or number != int(number) or abs(number) > 2**53 - 1
            ):
                raise ProtocolError("Native control integer is not exactly representable")
            changes[name] = item
    if requested:
        try:
            request = NativeControls.from_changes(driver, changes)
        except ValueError as error:
            raise ProtocolError("Malformed requested native controls") from error
        if request.present != value["present"]:
            raise ProtocolError("Requested native controls lost explicit presence")
    return value


def _pr_control_setup(value, valid, *, model=None):
    """Admit one PR D601/D602 setup only within the C parser's own bounds.

    pr655_imp.c parse_setup reads optical IDs exactly within
    +/-(2**53-1) (optical_integer): primary and aperture are
    nonnegative and an add-on is -1 (none) or an ID. Other codes are C ints
    (integer): units 0/1, cycles 1..99, observer 2/10, Smart Dark 0/1, sync
    0/1/3, and the sync value is nonnegative. Exposure is 0 (Adaptive) or
    3..6000 ms on PR-655 and 6..30000 ms on PR-670. A control report names no
    model, so it receives the union 3..30000. setup_reports adds the
    nonnegative D602 Hertz and the Adaptive/Extended label rules; a label that
    was not recognised leaves its flag false.
    """
    if type(valid) is not bool or (value is None) != (not valid):
        raise ProtocolError("Native PR setup availability disagrees with its original report")
    if not valid:
        return value
    integers = {"primary", "aperture", "units", "exposure_mode", "exposure_ms", "gain",
                "cycles", "observer", "dark_mode", "sync_mode", "capture_mode"}
    booleans = {"adaptive_known", "adaptive", "sensitivity_known", "extended"}
    if (not isinstance(value, dict) or set(value) != integers | booleans | {
        "addons", "sync_value", "frequency_hz", "descriptions",
    } or any(type(value[key]) is not int for key in integers)
            or any(type(value[key]) is not bool for key in booleans)
            or not isinstance(value["addons"], list) or len(value["addons"]) != 3
            or any(type(item) is not int for item in value["addons"])
            or not isinstance(value["descriptions"], list) or len(value["descriptions"]) != 15
            or any(not isinstance(item, str) or not item.isascii()
                   or any(ord(char) < 32 or ord(char) > 126 for char in item)
                   or len(item) > NATIVE_TEXT_MAX_BYTES for item in value["descriptions"])
            or sum(map(len, value["descriptions"])) > NATIVE_TEXT_MAX_BYTES):
        raise ProtocolError("Malformed native PR setup; opaque Gain/descriptions are retained")
    optical, exposure = 2**53 - 1, value["exposure_ms"]
    low, high = {"PR-655": (3, 6000), "PR-670": (6, 30000)}.get(model, (3, 30000))
    if (not 0 <= value["primary"] <= optical or not 0 <= value["aperture"] <= optical
            or any(not -1 <= item <= optical for item in value["addons"])
            or any(not -(2**31) <= value[key] < 2**31
                   for key in integers - {"primary", "aperture"})
            or value["units"] not in (0, 1) or not 1 <= value["cycles"] <= 99
            or value["observer"] not in (2, 10) or value["dark_mode"] not in (0, 1)
            or value["sync_mode"] not in (0, 1, 3)
            or exposure != 0 and not low <= exposure <= high
            or finite(value["sync_value"]) < 0 or finite(value["frequency_hz"]) < 0
            or value["adaptive"] and not value["adaptive_known"]
            or value["extended"] and not value["sensitivity_known"]
            or value["adaptive_known"] and value["adaptive"] != (exposure == 0)
            or value["sensitivity_known"] and not value["extended"] and exposure > 6000
            or model == "PR-655" and value["extended"]):
        raise ProtocolError("Native PR setup is outside its parsed D601/D602 bounds")
    return value


def _control_list(events, *, driver, name, prefix, count, fields):
    if type(count) is not int or not 0 <= count <= NATIVE_CONTROL_MAX_ROWS:
        raise ProtocolError("Invalid native control inventory count")
    items = []
    for event in events:
        if event.get("event") != name:
            continue
        item = _native_item(event, fields)
        if (item is None or event.get("driver") != driver
                or type(event.get(f"{prefix}_index")) is not int
                or event[f"{prefix}_index"] != len(items)
                or type(event.get(f"{prefix}_count")) is not int
                or event[f"{prefix}_count"] != count
                or type(item["id"]) is not int or not 0 <= item["id"] <= 2**53 - 1
                or any(previous["id"] == item["id"] for previous in items)):
            raise ProtocolError("Native control inventory is malformed or out of order")
        if "bandwidth" in item and finite(item["bandwidth"]) < 0:
            raise ProtocolError("Native aperture bandwidth is negative")
        strings = [item[key] for key, kind in fields.items() if kind is str]
        if (any(not value or not value.isascii()
                or any(ord(char) < 32 or ord(char) > 126 for char in value)
                for value in strings)
                or sum(map(len, strings)) > NATIVE_TEXT_MAX_BYTES):
            raise ProtocolError("Native inventory text exceeds its wire envelope")
        items.append(item)
    if len(items) != count:
        raise ProtocolError("Native control inventory is incomplete")
    if sum(len(item[key]) for item in items for key, kind in fields.items()
           if kind is str) > NATIVE_RAW_MAX_BYTES:
        raise ProtocolError("Native control inventory exceeds its exchange byte ceiling")
    return items


def _pr_inventory_metadata(value):
    """Keep absent inventory reports distinct from a documented empty list.

    Error numbers are original device responses. Zero can mean no standalone
    native error, including a host framing failure; validity never follows from
    the error number alone. Raw exchanges retain every attempted response.
    """
    for key in ("inventory_counts_valid", "accessory_list_valid"):
        if type(value.get(key)) is not bool:
            raise ProtocolError("Invalid native PR inventory availability")
    for key in ("reported_accessory_count", "reported_aperture_count"):
        if type(value.get(key)) is not int or not 0 <= value[key] <= NATIVE_CONTROL_MAX_ROWS:
            raise ProtocolError("Invalid native PR reported inventory count")
    for key in ("inventory_counts_error", "accessory_list_error"):
        if type(value.get(key)) is not int or not -4095 <= value[key] <= 0:
            raise ProtocolError("Invalid native PR original inventory status")
    if (not value["inventory_counts_valid"] and (
        value["reported_accessory_count"] or value["reported_aperture_count"]
        or value["accessory_list_valid"]
    ) or value["inventory_counts_valid"] and value["inventory_counts_error"]
            or value["accessory_list_valid"] and value["accessory_list_error"]):
        raise ProtocolError("Native PR inventory availability contradicts its report")


def _cr_control_details(head, events):
    fields = CONTROL_FIELDS["cr250"]
    if head.get("control_count") != len(fields) or type(head.get("control_count")) is not int:
        raise ProtocolError("CR native controls must retain every declared row")
    rows = []
    index = 0
    while index < len(events):
        event = events[index]
        index += 1
        if event.get("event") != "native_control_row":
            if event.get("event") == "native_control_choice":
                raise ProtocolError("CR choice arrived without its counted row")
            continue
        row = _native_item(event, dict(
            id=str, kind=int, available=bool, read_only=bool, limits_known=bool,
            choice_count=int, minimum=(int, float, type(None)),
            maximum=(int, float, type(None)), current_native=(str, type(None)),
            problem=(str, type(None)),
        ))
        if (row is None or not _cr_text_fields(row) or len(rows) >= len(fields)
                or event.get("driver") != "cr250"
                or type(event.get("control_index")) is not int
                or event["control_index"] != len(rows)
                or type(event.get("control_count")) is not int
                or event["control_count"] != len(fields) or row["id"] != fields[len(rows)]
                or row["kind"] not in range(5)
                or not 0 <= row["choice_count"] <= NATIVE_CR_CONTROL_MAX_ROWS):
            raise ProtocolError("CR native control row is malformed or out of order")
        if row["limits_known"]:
            low, high = finite(row["minimum"]), finite(row["maximum"])
            if not 0 <= low <= high:
                raise ProtocolError("CR native limits are not positive ordered bounds")
        elif row["minimum"] is not None or row["maximum"] is not None:
            raise ProtocolError("Unknown CR limits cannot become numeric defaults")
        choices = []
        for choice_index in range(row["choice_count"]):
            if index >= len(events):
                raise ProtocolError("CR native choices are incomplete")
            selected = events[index]
            index += 1
            choice = selected.get("choice")
            if (selected.get("event") != "native_control_choice"
                    or selected.get("driver") != "cr250"
                    or type(selected.get("control_index")) is not int
                    or selected["control_index"] != len(rows)
                    or type(selected.get("choice_index")) is not int
                    or selected["choice_index"] != choice_index
                    or type(selected.get("choice_count")) is not int
                    or selected["choice_count"] != row["choice_count"]):
                raise ProtocolError("CR native choice is malformed or out of order")
            choice = _cr_record(choice, key=row["id"])
            if any(old["id"] == choice["id"] for old in choices):
                raise ProtocolError("CR native choice ID is repeated")
            choices.append(choice)
        # Both LF and CRLF are valid; original token and terminator bytes are in
        # raw_replies. This semantic lower bound must not guess either spelling.
        if sum(_cr_record_size(choice) + 1 for choice in choices) > NATIVE_RAW_MAX_BYTES:
            raise ProtocolError("CR native inventory exceeds its exchange byte ceiling")
        rows.append(dict(row, choices=choices))
    if len(rows) != len(fields):
        raise ProtocolError("CR native control rows are incomplete")
    count = head.get("reply_count")
    if type(count) is not int or not 0 <= count <= NATIVE_CR_CONTROL_MAX_REPLIES:
        raise ProtocolError("Invalid CR native control reply count")
    replies = []
    for event in events:
        if event.get("event") != "native_control_reply":
            continue
        reply = _native_item(event, dict(command=str, phase=int, error=int, complete=bool))
        if (reply is None or not _cr_text_fields(reply) or event.get("driver") != "cr250"
                or type(event.get("reply_index")) is not int
                or event["reply_index"] != len(replies)
                or type(event.get("reply_count")) is not int or event["reply_count"] != count
                or reply["phase"] not in range(3)
                or replies and reply["phase"] < replies[-1]["phase"]):
            raise ProtocolError("CR native control replies are malformed or out of order")
        if reply["complete"]:
            parsed = _native_item(event, dict(
                status=("OK", "ER"), code=int, context=str, value=str,
            ))
            if parsed is None or not _cr_text_fields(parsed):
                raise ProtocolError("Complete CR native control reply lacks original status")
            reply.update(parsed)
        elif any(name in event for name in ("status", "code", "context", "value")):
            raise ProtocolError("Incomplete CR native reply cannot invent parsed status")
        replies.append(reply)
    if len(replies) != count:
        raise ProtocolError("CR native control replies are incomplete")
    return dict(controls=rows, replies=replies)


def native_control_facts(events):
    """Join the last counted settings receipt without manufacturing readback or bytes.

    A C report's masks remain original. The logical frontend separately retains
    unresolved control debt across its ephemeral worker lifetimes. Read-only
    refresh and empty/partial application cannot establish recovery. Settings
    records contain no invented color observation or physical readiness claim.
    """
    _native_envelopes(events)
    starts = [index for index, event in enumerate(events)
              if event.get("event") == "native_control"]
    if not starts:
        return None
    head = events[starts[-1]]
    driver = head.get("driver")
    if type(driver) is not str or driver not in CONTROL_FIELDS:
        raise ProtocolError("Unknown native control adapter")
    tail = []
    for event in events[starts[-1] + 1:]:
        if event.get("event") in {"native_control", "native_facts"}:
            break
        tail.append(event)
    maximum = (1 << len(CONTROL_FIELDS[driver])) - 1
    masks = ("changed", "attempted", "acknowledged", "confirmed", "blocked")
    if (any(type(head.get(name)) is not int or not 0 <= head[name] <= maximum for name in masks)
            or type(head.get("failed")) is not bool
            or type(head.get("status_code")) is not int or not 0 <= head["status_code"] < 2**32
            or not head["failed"] and head["status_code"] != 0):
        raise ProtocolError("Malformed native control outcome/masks")
    for name in ("before", "values", "requested"):
        _control_values(head.get(name), driver=driver, requested=name == "requested")
    requested = head["requested"]["present"]
    if (head["changed"] & ~requested or head["attempted"] & ~head["changed"]
            or head["acknowledged"] & ~head["attempted"] or head["confirmed"] & ~requested):
        raise ProtocolError("Native planned, attempted, ACK and confirmed masks disagree")
    # Both adapters add every attempted field of a failed application to their
    # lifetime block before reporting it (pr655_control.c:378-383;
    # cr250_control.c:384,540). A failed report outside that rule lost its debt.
    if head["failed"] and head["attempted"] & ~head["blocked"]:
        raise ProtocolError("Failed native controls do not block every attempted field")
    ack_only = ack_only_mask(driver)
    if (head["confirmed"] | head["before"]["present"] | head["values"]["present"]) & ack_only:
        raise ProtocolError("Acknowledged-only native controls cannot invent current or "
                            "confirmed readback")
    for index, name in enumerate(CONTROL_FIELDS[driver]):
        bit = 1 << index
        if head["confirmed"] & bit and (
            not head["values"]["present"] & bit
            or head["values"][name] != head["requested"][name]
        ):
            raise ProtocolError("Confirmed native control differs from the requested readback")
    if not head["failed"] and requested & ~(
        head["confirmed"] | (head["acknowledged"] & ack_only)
    ):
        raise ProtocolError("Successful native controls lack confirmation of the complete request")
    result = {key: value for key, value in head.items()
              if key not in {"event", "protocol_version", "provenance"}}
    if driver == "pr655":
        _pr_control_setup(head.get("setup_before"), head.get("setup_before_valid"))
        _pr_control_setup(head.get("setup"), head.get("setup_valid"))
        _pr_inventory_metadata(head)
        if (head.get("accessory_count") != (head["reported_accessory_count"]
            if head["accessory_list_valid"] else 0)):
            raise ProtocolError("Native PR accessory count contradicts list availability")
        if type(head.get("aperture_list_valid")) is not bool:
            raise ProtocolError("Invalid native PR aperture-list availability")
        if (type(head.get("aperture_list_error")) is not int
                or not -4095 <= head["aperture_list_error"] <= 0
                or head["aperture_list_valid"] and (
                    not head["inventory_counts_valid"] or head["aperture_list_error"])
                or head.get("aperture_count") != (head["reported_aperture_count"]
                    if head["aperture_list_valid"] else 0)):
            raise ProtocolError("Unavailable PR aperture list cannot have invented rows")
        result["accessories"] = _control_list(
            tail, driver=driver, name="native_accessory", prefix="accessory",
            count=head.get("accessory_count"),
            fields=dict(id=int, name=str, type=str, photometry=str, radiometry=str),
        )
        result["apertures"] = _control_list(
            tail, driver=driver, name="native_aperture", prefix="aperture",
            count=head.get("aperture_count"), fields=dict(id=int, name=str, bandwidth=(int, float)),
        )
        result["raw_replies"] = _native_raw(
            tail, driver=driver, count=head.get("raw_exchange_count"),
        )
    else:
        result.update(_cr_control_details(head, tail))
        if type(head.get("trace_overflow")) is not bool:
            raise ProtocolError("Invalid CR native trace overflow fact")
        if head["trace_overflow"] and not head["failed"]:
            # cr250_control.c finish(): retention overflow fails a successful report.
            raise ProtocolError("CR native trace overflow cannot accompany successful controls")
        result["raw_replies"] = _native_raw(
            tail, driver=driver, count=head.get("raw_exchange_count"),
        )
    return result


def _cr_spectrum_wavelengths(grid):
    """Map the declared CR grid under the current parser's structural tolerance.

    Spectrum owns strict increase of the generated binary64 axis. Complete
    native declarations remain facts even when this mapping is refused.
    """
    start, end, step = (finite(grid.get(key)) for key in ("start_nm", "end_nm", "step_nm"))
    arithmetic_end = start + (grid["count"] - 1) * step
    if (grid["count"] < 2 or start <= 0 or end <= start or step <= 0
            or not math.isfinite(arithmetic_end)
            or abs(arithmetic_end - end) > 1e-8 * max(1, end)):
        raise ProtocolError("CR native spectral grid is inconsistent")
    return tuple(start + index * step for index in range(grid["count"]))


def _cr_spectral_basis(facts):
    """Validate the CR adapter's raw versus model-inferred publication contract.

    Remote Communication 1.36 defines the native grid and radiometric total, but
    not RM Spectrum's density unit. The driver's same-capture consistency model
    may qualify its Argyll publication; native bins retain an unknown unit.
    """
    if facts is None:
        return None
    if not isinstance(facts, dict):
        raise ProtocolError("Malformed native facts")
    if facts.get("driver") != "cr250":
        return None
    capture = facts.get("capture")
    if not isinstance(capture, dict):
        raise ProtocolError("Malformed CR capture")
    grid = capture.get("spectrum")
    if grid is None:
        return None
    check = capture.get("unit_check")
    if (not isinstance(grid, dict) or not isinstance(check, dict)
            or check.get("verdict") not in {"not_checked", "consistent", "inconclusive", "mismatch"}
            or type(capture.get("valid")) is not bool):
        raise ProtocolError("Malformed CR spectral qualification")
    consistent = check["verdict"] == "consistent"
    published = grid.get("published_unit") is not None or grid.get("published_scale") is not None
    expected = dict(
        unit=None, inferred_unit="W/(sr.m^2.nm)" if consistent else None,
        published_unit="mW/(m^2.sr.nm)" if published else None,
        published_scale=1000 if published else None,
        unit_basis="same_capture_consistency_under_driver_model" if consistent else "unverified",
    )
    if (any(key not in grid or grid[key] != value for key, value in expected.items())
            or published and (not consistent or not capture["valid"])):
        raise ProtocolError("CR native density and inferred publication disagree")
    count = grid.get("count")
    values = grid.get("values")
    if (type(count) is not int or not 1 <= count <= NATIVE_CR_SPECTRUM_MAX_SAMPLES
            or not isinstance(values, list) or len(values) != count):
        raise ProtocolError("CR raw spectral values are incomplete")
    start, end, step = (finite(grid.get(key)) for key in ("start_nm", "end_nm", "step_nm"))
    for value in values:
        finite(value)
    if (consistent or published) and not (
        4 <= count <= 601 and 200 <= start < end <= 1100 and step >= 0.1
    ):
        raise ProtocolError("CR spectral qualification exceeds the driver's publication domain")
    if consistent or published:
        _cr_spectrum_wavelengths(grid)
    if capture["valid"]:
        try:
            Spectrum(
                _cr_spectrum_wavelengths(grid), tuple(values),
                "unverified native spectral density", "unverified native spectral density units",
            )
        except ValueError as error:
            raise ProtocolError("A valid CR capture cannot carry an unmappable spectrum") from error
    return grid


def _pr_spectral_basis(facts):
    """Validate retained PR M5 originals separately from Argyll publication.

    pr655_support.c retains a parsed complete spectrum even when D2, geometry,
    restoration or publication fails. A combined native_complete=false is not
    a missing spectrum. Raw density units stay unknown; the same-capture driver
    consistency verdict, its basis and actual publication remain separate.
    PR's manual M5 integral and D116 geometry can support dim publication even
    when a consistency ratio is inconclusive; unlike CR, the denominator is
    declared by that model. No inconclusive verdict becomes inferred native units.
    No spectrum, XYZ, unit conversion or sampled value is inferred here.
    """
    if facts is None or facts.get("driver") != "pr655":
        return None
    capture = facts.get("capture")
    if not isinstance(capture, dict):
        raise ProtocolError("Malformed PR capture")
    grid = capture.get("spectrum")
    if grid is None:
        if capture.get("spectrum_parsed") is True:
            raise ProtocolError("Parsed PR spectrum is missing from its retained capture")
        return None
    identity = facts.get("identity")
    model = identity.get("model") if isinstance(identity, dict) else None
    flags = ("valid", "native_complete", "spectrum_complete", "spectrum_parsed",
             "geometry_valid", "xyz_complete", "xyz_parsed")
    if (not isinstance(grid, dict) or not isinstance(identity, dict)
            or identity.get("verified") is not True or type(model) is not str
            or model not in PR_GRID_CONTRACTS
            or any(type(capture.get(key)) is not bool for key in flags)
            or not capture["spectrum_complete"] or not capture["spectrum_parsed"]):
        raise ProtocolError("Malformed independently retained PR spectrum")
    count, step, _ = PR_GRID_CONTRACTS[model]
    if (any(type(grid.get(key)) is not int for key in
            ("count", "start_nm", "end_nm", "step_nm"))
            or (grid["count"], grid["start_nm"], grid["end_nm"], grid["step_nm"])
            != (count, 380, 780, step)
            or not isinstance(grid.get("values"), list) or len(grid["values"]) != count):
        raise ProtocolError("PR retained spectrum differs from its exact native grid")
    for value in grid["values"]:
        finite(value)
    check = facts.get("unit_check")
    if (not isinstance(check, dict) or type(check.get("verdict")) is not int
            or check["verdict"] not in range(4)):
        raise ProtocolError("Malformed PR density qualification")
    consistent = check["verdict"] == 1  # PR655_UNIT_CONSISTENT; other verdicts do not qualify.
    published = grid.get("published_unit") is not None or grid.get("published_scale") is not None
    # D116 geometry and the native same-capture check own the denominator.
    # The actual ipatch type is checked separately at publication below.
    density_units = (("W/(sr.m^2.nm)", "mW/(m^2.sr.nm)"),
                     ("W/(m^2.nm)", "mW/(m^2.nm)"))
    inferred = grid.get("inferred_unit", "missing")
    publication_unit = grid.get("published_unit", "missing")
    if (grid.get("unit", "missing") is not None
            or "unit_basis" not in grid
            or (inferred not in tuple(row[0] for row in density_units)
                if consistent else inferred is not None)
            or grid.get("unit_basis") is not None and (
                not isinstance(grid["unit_basis"], str) or not grid["unit_basis"])
            or consistent and not capture["geometry_valid"]
            or published and (check["verdict"] not in {1, 2} or not capture["valid"]
                              or not capture["native_complete"] or not capture["geometry_valid"]
                              or not grid.get("unit_basis"))
            or (publication_unit not in tuple(row[1] for row in density_units)
                if published else publication_unit is not None)
            or consistent and published and (inferred, publication_unit) not in density_units
            or grid.get("published_scale", "missing") != (1000 if published else None)
            or capture["valid"] and not published):
        raise ProtocolError("PR raw density, inferred basis and publication disagree")
    return grid


def _bound_native_family(facts, device, *, capture_identity=False):
    """Bind originals to the selected model/serial, using a capture snapshot on refusal.

    A quarantined CR link loses its current identity. The capture's separately
    retained verified snapshot can still attribute its earlier originals. It
    does not qualify current publication or reopen the link.
    """
    reported = facts.get("driver") if isinstance(facts, dict) else None
    known = reported in {"cr250", "pr655"}
    if device.backend == "argyll-demo":
        # The explicit Demo owner generates palette observations without a native
        # driver. A model label cannot turn that backend into a CR acquisition.
        if known:
            label = "CR" if reported == "cr250" else "PR"
            raise ProtocolError(f"Demo preview cannot claim native {label} capture facts")
        return None
    try:
        bound = instrument_binding_name(device.model)
    except KeyError as error:
        if known:
            raise ProtocolError(
                "Retained native capture belongs to a different model binding",
            ) from error
        return None
    try:
        driver = native_control_driver(bound)
    except ValueError:
        if known:
            raise ProtocolError("Retained native capture belongs to a different model binding")
        return None
    if not isinstance(facts, dict):
        raise ProtocolError("Bound native CR/PR reading has no current native identity/basis")
    identity = facts.get("identity")
    if capture_identity and driver == "cr250" and "identity" in facts.get("capture", {}):
        identity = facts["capture"]["identity"]
    if reported != driver or not isinstance(identity, dict):
        raise ProtocolError("Bound native CR/PR reading has no current native identity/basis")
    if identity.get("verified") is not True:
        raise ProtocolError("Retained native capture identity was not verified")
    try:
        reported = instrument_binding_name(
            ("Colorimetry Research " if driver == "cr250" else "Photo Research ")
            + identity["model"])
    except (KeyError, TypeError) as error:
        raise ProtocolError(
            "Retained native capture belongs to a different model binding",
        ) from error
    if reported != bound:
        raise ProtocolError("Retained native capture belongs to a different model binding")
    if not isinstance(identity.get("serial"), str) or not identity["serial"]:
        raise ProtocolError("Retained native capture has no reported serial")
    if device.serial is not None and identity["serial"] != device.serial:
        raise ProtocolError("Retained native capture belongs to a different reported serial")
    if capture_identity and driver == "cr250" and "identity" in facts.get("capture", {}):
        validate_native_identity(dict(driver=driver, identity=identity), serial=identity["serial"])
    return driver


def native_facts(events) -> dict | None:
    """The driver's retained facts for the LAST attempt, joined and checked.

    A support adapter (``native_facts.h``) publishes one ``native_facts`` event per
    attempt, then every retained reply, checked setting and setting write as its own
    event so no line passes the protocol's size cap. They are joined here. A gap, a
    reordering or a count that disagrees is a protocol error, never a shorter list:
    an item that did not arrive is a fact dropped, not a fact absent.
    """
    _native_envelopes(events)
    starts = [i for i, event in enumerate(events) if event.get("event") == "native_facts"]
    if not starts:
        return None
    head = events[starts[-1]]
    capture, identity = head.get("capture"), head.get("identity")
    if (not isinstance(head.get("driver"), str) or not isinstance(capture, dict)
            or not isinstance(identity, dict)):
        raise ProtocolError("Malformed native facts")
    facts = {key: value for key, value in head.items()
             if key not in ("protocol_version", "provenance", "event")}
    tail = events[starts[-1] + 1:]
    for name, joined, prefix, fields in _NATIVE_LISTS:
        if head["driver"] == "pr655" and name == "native_reply":
            if any(event.get("event") == name for event in tail):
                raise ProtocolError("PR raw exchanges cannot claim CR parsed reply status")
            continue
        # Replies predate the other lists and are always counted; a driver whose
        # head carries no count for a list sends none of it.
        count = capture.get(f"{prefix}_count", None if prefix != "reply" else -1)
        if count is None:
            if any(event.get("event") == name for event in tail):
                raise ProtocolError(f"Uncounted {name} events")
            continue
        maximum = NATIVE_RAW_MAX_EXCHANGES
        if prefix == "spectrum_chunk" and head["driver"] in _NATIVE_SPECTRUM_MAX_SAMPLES:
            maximum = -(-_NATIVE_SPECTRUM_MAX_SAMPLES[head["driver"]]
                        // NATIVE_SPECTRUM_CHUNK_VALUES)
        if type(count) is not int or not 0 <= count <= maximum:
            raise ProtocolError("Malformed native facts")
        items = []
        for event in tail:
            if event.get("event") != name:
                continue
            item = _native_item(event, fields)
            if (item is None or head["driver"] == "cr250" and not _cr_text_fields(item)
                    or event.get("driver") != head["driver"]
                    or type(event.get(f"{prefix}_index")) is not int
                    or type(event.get(f"{prefix}_count")) is not int
                    or event.get(f"{prefix}_index") != len(items)
                    or event.get(f"{prefix}_count") != count):
                raise ProtocolError(f"Native {joined} are malformed or out of order")
            items.append(item)
        if len(items) != count:
            raise ProtocolError(f"Native {joined} are incomplete")
        facts[joined] = items
    if head["driver"] in {"cr250", "pr655"}:
        if type(head.get("trace_overflow")) is not bool:
            raise ProtocolError("Invalid native trace overflow fact")
        if head["trace_overflow"] and capture.get("valid") is not False:
            # Neither adapter marks a capture valid after retention failed: a failed
            # CR snapshot returns before cr250.c sets capture.valid, and
            # pr655_control.c ends the capture with CM_PR655_TRACE_LIMIT.
            raise ProtocolError("Native trace overflow cannot accompany a valid capture")
    if head["driver"] == "pr655":
        if any(event.get("event") == "native_record" for event in tail):
            raise ProtocolError("CR native records belong to a different capture driver")
        _pr_inventory_metadata(capture)
        # Same-capture setups feed chart material; admit them like settings setups.
        model = identity.get("model") if identity.get("model") in PR_GRID_CONTRACTS else None
        for phase in ("setup_before", "setup_after"):
            setup = capture.get(phase)
            _pr_control_setup(setup, setup is not None, model=model)
        facts["raw_replies"] = _native_raw(tail, driver="pr655", count=capture.get("reply_count"))
    elif head["driver"] == "cr250":
        facts["raw_replies"] = _native_raw(
            tail, driver="cr250", count=capture.get("raw_exchange_count"),
        )
        capture = _cr_capture_records(capture, facts.get("settings", []), tail)
        facts["capture"] = capture
    elif any(event.get("event") in {"native_raw", "native_record"} for event in tail):
        raise ProtocolError("Native raw exchanges belong to a different capture driver")
    chunks = facts.pop("spectrum_chunks", None)
    if chunks:
        # Contiguous, finite, and exactly the grid's count: a chunk dropped or
        # duplicated is a fact lost, not a shorter spectrum.
        values: list[float] = []
        for chunk in chunks:
            if (chunk["offset"] != len(values) or not chunk["values"]
                    or len(chunk["values"]) > NATIVE_SPECTRUM_CHUNK_VALUES
                    or not all(type(v) in (int, float) and math.isfinite(v)
                               for v in chunk["values"])):
                raise ProtocolError("Native spectrum chunks are malformed or out of order")
            values.extend(float(v) for v in chunk["values"])
        grid = capture.get("spectrum")
        if not isinstance(grid, dict) or grid.get("count") != len(values):
            raise ProtocolError("Native spectrum is incomplete")
        facts["capture"] = {**capture, "spectrum": {**grid, "values": values}}
    _cr_spectral_basis(facts)
    _pr_spectral_basis(facts)
    return facts


# --- chart geometry ------------------------------------------------------------


def sheet_layout(passes: int, steps: int, patch_count: int) -> tuple[int, int]:
    """``(fiducials, steps_in_first_pass)`` for one sheet.

    ``chartread.c:1090-1107``. A full sheet holds ``passes * steps`` patches. When
    the chart ends mid-sheet the shortfall comes off the first pass ONLY on a
    single-pass sheet (``:1093-1100``: upstream guards that reduction with
    ``paist == 1``); a multi-pass sheet keeps its full first pass and simply reads
    fewer patches. A sheet of one pass also needs fewer sighted points, because the
    pass direction is then the only direction there is:

    * three points -- origin, end of the first pass, start of the last pass
    * two, when there is one pass (no second direction to measure)
    * one, when that single pass holds a single patch (nothing to measure at all)
    """
    if (any(type(value) is not int or not 1 <= value <= MAX_PATCHES
            for value in (passes, steps, patch_count)) or patch_count > passes * steps):
        raise ValueError("A sheet needs at least one patch and no more than passes x steps")
    first_pass_steps = steps
    if passes == 1:
        first_pass_steps = patch_count
    fiducials = 3
    if passes == 1:
        fiducials = 2 if first_pass_steps > 1 else 1
    return fiducials, first_pass_steps


def validate_chart_layout(patch_count: int, passes: int, steps: int, sheets,
                          chart_id: int = 0) -> None:
    """Bound the geometry before opening an instrument or entering its driver."""
    sheet_layout(passes, steps, patch_count)
    if passes * steps > MAX_PATCHES:
        raise ValueError("Chart grid must contain at most 4096 patch slots")
    if (not isinstance(sheets, (tuple, list)) or not 1 <= len(sheets) <= MAX_SHEETS
            or any(type(value) is not int or not 1 <= value <= MAX_PATCHES for value in sheets)
            or sum(sheets) != passes):
        raise ValueError("Chart sheets must account for exactly the passes within 64 sheets")
    if type(chart_id) is not int or not 0 <= chart_id <= 2147483647:
        raise ValueError("Chart ID must be a nonnegative signed 32-bit integer")


def navigation_vectors(
    points: tuple[Location, ...], *, passes: int, first_pass_steps: int, hexagonal: bool = False,
) -> tuple[float, ...]:
    """Sighted points to the eight vectors ``read_xy`` navigates by.

    ``chartread.c:1254-1300``, returning ``(ox, oy, ax, ay, aax, aay, px, py)`` in
    that call's own order (``:1305-1306``). Units are the instrument's table
    coordinates -- millimetres for the SpectroScan -- and every vector is a
    difference of sighted points, so the units are whatever ``xy_get_location``
    reported.

    * ``o``   origin: the first patch (``:1255-1256``)
    * ``a``   pass increment: one step from pass to pass (``:1292-1294``)
    * ``aa``  pass offset applied to odd steps, zero for a rectangular chart
      (``:1299``) and half a pass for a hexagonal one (``:1266-1267``)
    * ``p``   step increment: one step along a pass (``:1296-1298``)

    On a hexagonal chart the odd steps sit half a pass over, and ``aa`` is that
    displacement. With three sighted points it is literally half the pass
    increment (``:1266-1267``, ``:1278-1279``). With two, the pass direction was
    never sighted, so it is recovered from the step vector instead: ``aa`` is the
    step vector rotated 90 degrees and scaled by ``kk = sqrt(1/3)``
    (``aax, aay = kk*py, -kk*px``; upstream ``:1271-1272``, ``:1285-1286``) -- so
    ``kk`` is a fraction of the STEP increment, not of the row pitch. The
    non-aligned two-point case additionally divides by ``kk*kk + steps*steps``
    (``:1283-1284``), which is the least-squares solution for a step vector whose
    endpoint carries that rotated offset.

    Limitation: with one sighted point only the origin is known, so the remaining
    vectors keep chartread's own defaults (``:1086-1088``) -- a unit pass increment
    in x and a unit step increment in y. That is a single-patch sheet, where they
    are never applied.
    """
    if not 1 <= len(points) <= 3:
        raise ValueError("A sheet is located from one, two or three sighted points")
    if passes < 1 or first_pass_steps < 1:
        raise ValueError("A sheet has at least one pass of at least one step")
    if len(points) > 1 and first_pass_steps < 2:
        raise ValueError("Two sighted points need at least two steps between them")
    if len(points) > 2 and passes < 2:
        raise ValueError("Three sighted points need at least two passes between them")
    for point in points:
        if not math.isfinite(point.x) or not math.isfinite(point.y):
            raise ValueError("A sighted point must be finite")
    ox, oy = points[0].x, points[0].y
    ax, ay, aax, aay, px, py = 1.0, 0.0, 0.0, 0.0, 0.0, 1.0
    steps = first_pass_steps - 1          # nn: step intervals in the first pass
    if hexagonal:
        kk = math.sqrt(1.0 / 3.0)
        aligned = steps % 2 == 0          # are points[0] and points[1] on the same row?
        if len(points) == 3:
            ax = (points[2].x - ox) / (passes - 1)
            ay = (points[2].y - oy) / (passes - 1)
            aax, aay = 0.5 * ax, 0.5 * ay
            if aligned:
                px, py = (points[1].x - ox) / steps, (points[1].y - oy) / steps
            else:
                px = (points[1].x - ox - aax) / steps
                py = (points[1].y - oy - aay) / steps
        elif len(points) == 2:
            dx, dy = points[1].x - ox, points[1].y - oy
            if aligned:
                px, py = dx / steps, dy / steps
            else:
                px = (steps * dx - kk * dy) / (kk * kk + steps * steps)
                py = (steps * dy + kk * dx) / (kk * kk + steps * steps)
            aax, aay = kk * py, -kk * px
    else:
        if len(points) == 3 and passes > 1:
            ax = (points[2].x - ox) / (passes - 1)
            ay = (points[2].y - oy) / (passes - 1)
        if len(points) >= 2 and first_pass_steps > 1:
            px = (points[1].x - ox) / steps
            py = (points[1].y - oy) / steps
        aax = aay = 0.0
    return (ox, oy, ax, ay, aax, aay, px, py)


# --- the instrument ------------------------------------------------------------


@dataclass(frozen=True)
class Instrument:
    """One selected instrument. Adding another is another value of this type."""

    helper: Path
    instrument: str
    #: The serial to pin the selection to, or ``None`` to accept whichever
    #: instrument the chosen path presents. ``inst.c:99-101`` defines the default
    #: ``get_serial_no`` as returning ``""`` and ``inst.c:755-756`` installs it for
    #: every driver that does not override it -- the DTP22/41/51/92/94, HCFR,
    #: SMCube, SpyderX and SpyderX2 all inherit it. Requiring a pin would make
    #: those nine unattachable.
    expected_serial: str | None = None
    path_index: int = 1
    #: Permit OPENING the selected serial port to ask the device what it is.
    #:
    #: A serial path carries no identity: ``icompaths_add_serial`` never writes
    #: ``dtype`` (``icoms.c:269-299``) and this build suppresses the automatic
    #: probe, so twelve instruments -- the SpectroScan among them -- present a path
    #: that can never match the requested type. Talking to the device is the only
    #: way to learn what it is, so it is off unless asked for.
    #:
    #: What it does NOT do: widen enumeration. Ports are still listed passively;
    #: only the one path at ``path_index`` is opened, and a device that turns out
    #: to be another family is refused before its constructor runs.
    allow_serial_identification: bool = False
    timeout: float = 30.0
    environment: dict | None = None
    cancel: object = field(default=None, repr=False, compare=False)
    expected_path: str | None = None
    #: Required before opening a process; report-bound builds supply this digest.
    expected_helper_sha256: str | None = None
    #: Auxiliary stderr provenance, never a mode, option or scientific setting.
    native_diagnostics: NativeDiagnosticCapture | None = field(
        default=None, repr=False, compare=False,
    )

    def __post_init__(self) -> None:
        if self.native_diagnostics is not None and not isinstance(
                self.native_diagnostics, NativeDiagnosticCapture):
            raise ValueError("Use the shared native diagnostic capture")
        if type(self.timeout) not in (int, float) or not 0 < self.timeout <= 600:
            raise ValueError("timeout must be finite seconds in (0, 600]")
        if self.expected_helper_sha256 is not None and (
                not isinstance(self.expected_helper_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", self.expected_helper_sha256) is None):
            raise ValueError("The acquisition helper SHA-256 must be 64 lowercase hex digits")
        # Fail on a name Argyll would answer instUnknown for, here rather than as
        # a failed attach in front of an operator holding the instrument.
        object.__setattr__(self, "helper", Path(self.helper))
        object.__setattr__(self, "instrument", instrument_binding_name(self.instrument))
        if self.expected_path is not None and (not isinstance(self.expected_path, str)
                or not self.expected_path or len(self.expected_path.encode()) > 4096
                or "\x00" in self.expected_path):
            raise ValueError("The selected OS path must be bounded nonempty text")
        if self.expected_serial is not None:
            try:
                _serial_pair(self.expected_serial, "utf8")
                bounded = len(self.expected_serial.encode("utf-8")) <= NATIVE_TEXT_MAX_BYTES
            except ValueError:
                bounded = False
            if not bounded:
                raise ValueError("A serial pin is a non-empty string, or None for no pin; "
                                 f"UTF-8 pins are limited to {NATIVE_TEXT_MAX_BYTES} bytes")
        if not 1 <= self.path_index <= 64:
            raise ValueError("The path index is chosen from an enumeration, not searched for")

    # -- workflows --------------------------------------------------------------

    def measure(self, mode: str, *, spectral: bool = True, positioned: bool | None = False,
                calibrate: bool = False, acknowledge=None,
                configuration: Configuration | None = None) -> Measurement:
        """One reading. ``positioned`` drives a motorised table to a sighted point."""
        if workflow_kind(mode) != "spot":
            raise ValueError(f"{mode} is a {workflow_kind(mode)} workflow, not a spot read")
        facilities = list(WORKFLOW_FACILITIES["spot"])
        if positioned is not None and type(positioned) is not bool:
            raise ValueError("positioned is boolean, or None for post-mode capability selection")
        if positioned:
            facilities += ["xy_locate", "xy_position"]
        return self._run(
            mode, spectral, facilities, acknowledge, configuration=configuration,
            read=1, position=2 if positioned is None else int(positioned), calibrate=int(calibrate),
        )

    def mode_snapshot(self, mode: str, *,
                      configuration: Configuration | None = None) -> Measurement:
        """Explicitly open/configure/query/release, with no calibration or measurement.

        This is not passive: initialization and requested setters can affect the
        instrument. Stored-rate retrieval may issue a device query (DTP92).
        """
        return self._run(mode, False, [], None, configuration=configuration,
                         mode_snapshot=1, stored_refresh=1)

    def native_controls(self, mode: str, changes=None, *,
                        configuration: Configuration | None = None) -> Measurement:
        """Read or apply typed settings in the ordinary owned native worker lifetime.

        None reads; an exact changes dict applies, including an explicitly empty
        application. Settings produce no color observation. Initial/current and
        requested facts, attempted writes, ACK and confirmation remain separate.
        The frontend/binding owner retains logical recovery debt across workers.
        """
        configuration = configuration or Configuration()
        if configuration.native_controls is not None:
            raise ValueError("Supply native control changes once through the changes argument")
        driver = native_control_driver(self.instrument)
        requested = None if changes is None else NativeControls.from_changes(driver, changes)
        configuration = replace(configuration, native_controls=requested)
        return self._run(
            mode, False, [], None, configuration=configuration,
            controls_action="read" if requested is None else "apply",
            controls_fields="" if requested is None else requested.argument(),
        )

    def stored_refresh_rate(self, mode: str = "emissive_spot", *, calibrate: bool = False,
                            acknowledge=None,
                            configuration: Configuration | None = None) -> Measurement:
        """Retrieve the current helper/device state; optional calibration is explicit."""
        return self._run(mode, False, [], acknowledge, configuration=configuration,
                         stored_refresh=1, calibrate=int(calibrate))

    def measure_refresh(self, mode: str = "emissive_spot", *, calibrate: bool = True,
                        acknowledge=None,
                        configuration: Configuration | None = None) -> Measurement:
        """Measure fresh display frequency through the common read_refrate method.

        No stored-rate query, setter or automatic repeated method call is performed.
        Calibration uses the same needed-driven ladder as colour acquisition.
        """
        if parse_mode_name(mode)[0] not in {"emis_spot", "emis_tele"}:
            raise ValueError("Refresh measurement requires an emissive spot or telephoto mode")
        configuration = configuration or Configuration()
        validate_nonphotometric_configuration(configuration)
        return self._run(mode, False, list(WORKFLOW_FACILITIES["refresh_frequency"]), acknowledge,
                         configuration=configuration, read_refresh=1, calibrate=int(calibrate))

    def measure_display_timing(self, mode: str = "emissive_spot", *, presenter,
                               calibrate: bool = True, acknowledge=None,
                               configuration: Configuration | None = None) -> Measurement:
        """Measure marker-relative timing with an explicit controlled presenter.

        The presenter returns a PresentationStamp for each requested phase.
        Stored refresh, optical onset and cross-clock latency correction are absent.
        """
        if parse_mode_name(mode)[0] not in {"emis_spot", "emis_tele"}:
            raise ValueError("Display timing requires an emissive spot or telephoto mode")
        if not callable(presenter):
            raise ValueError("Display timing requires a controlled presenter")
        configuration = configuration or Configuration()
        validate_nonphotometric_configuration(configuration)
        return self._run(mode, False, list(WORKFLOW_FACILITIES["display_timing"]), acknowledge,
            presenter=presenter, configuration=configuration,
            read_timing=1, calibrate=int(calibrate))

    def calibrate(self, mode: str, *, spectral: bool = True, acknowledge=None,
                  configuration: Configuration | None = None) -> Measurement:
        """Run the instrument's outstanding calibrations and read nothing.

        `measure(calibrate=True)` calibrates and then takes a reading; this is the
        calibration on its own, which is what upstream's own standalone
        `inst_handle_calibrate` does and what the `calibrate` workflow means. The
        helper has always accepted `--read 0` (session.c, the `--read` flag); nothing sent it,
        so the workflow asked for zero patches and received one, and every
        `calibrate` operation failed with "returned more patches than requested".

        The returned `Measurement` carries no patches. Whether anything was
        actually performed is in `calibration`: `get_n_a_cals()` is the authority,
        and an instrument needing nothing reports `performed: false` rather than
        refusing.
        """
        if workflow_kind(mode) != "spot":
            raise ValueError(f"{mode} is a {workflow_kind(mode)} workflow, not a spot read")
        return self._run(
            mode, spectral, list(WORKFLOW_FACILITIES["spot"]), acknowledge,
            configuration=configuration, read=0, position=0, calibrate=1,
        )

    def measure_strip(self, mode: str, *, patch_count: int, pass_name: str = "1",
                      strip_name: str = "STRIP", guide: int | None = None,
                      patch_width_mm: float | None = None, gap_width_mm: float | None = None,
                      trailer_width_mm: float | None = None, scan_tolerance: float | None = None,
                      spectral: bool = True, calibrate: bool = False, acknowledge=None,
                      configuration: Configuration | None = None,
                      direction: str = "forward") -> Measurement:
        """One strip in declared patch order; direction is operator-supplied.

        Forward means the driver's returned order; reverse reverses that order
        and requires bidirectional scan support. Native Patch.index is preserved.
        No target recognition or DTP51 padding/offset correlation is performed.
        """
        if workflow_kind(mode) != "strip":
            raise ValueError(f"{mode} is a {workflow_kind(mode)} workflow, not a strip pass")
        if direction not in {"forward", "reverse"}:
            raise ValueError("Strip direction must be forward or reverse")
        if type(patch_count) is not int or not 1 <= patch_count <= MAX_PATCHES:
            raise ValueError("A pass holds at least one patch")
        flags = {
            "read_strip": patch_count,
            "strip_name": strip_name,
            "strip_pass": pass_name,
            "strip_guide": -1 if guide is None else guide,
            "strip_widths": ",".join(str(-1 if value is None else value)
                                    for value in (patch_width_mm, gap_width_mm, trailer_width_mm)),
            "calibrate": int(calibrate),
        }
        if scan_tolerance is not None:
            if (type(scan_tolerance) not in (int, float)
                    or not math.isfinite(scan_tolerance) or scan_tolerance < 0):
                raise ValueError("A scan tolerance is finite and nonnegative")
            flags["scan_tolerance"] = scan_tolerance
        facilities = list(WORKFLOW_FACILITIES["strip"])
        if direction == "reverse":
            facilities.append("bidi_scan")
        return self._run(mode, spectral, facilities, acknowledge,
                         configuration=configuration, strip_direction=direction, **flags)

    def measure_sheet(self, mode: str, *, passes: int, steps: int, patch_count: int | None = None,
                      pass_name: str = " A", step_name: str = " 1", hexagonal: bool = False,
                      geometry: tuple[float, ...] | None = None, spectral: bool = True,
                      calibrate: bool = False, acknowledge=None,
                      configuration: Configuration | None = None) -> Measurement:
        """One X-Y sheet.

        With ``geometry`` the sheet is read straight away from vectors the caller
        already knows. Without it the operator sights the fiducials
        ``sheet_layout`` calls for and the vectors are derived here.
        """
        if workflow_kind(mode) != "xy":
            raise ValueError(f"{mode} is a {workflow_kind(mode)} workflow, not a sheet")
        count = passes * steps if patch_count is None else patch_count
        fiducials, first_pass_steps = sheet_layout(passes, steps, count)
        if count > MAX_PATCHES:
            raise ValueError("A sheet is bounded; split a larger chart into sheets")
        if geometry is None:
            # Prove the sighted points CAN yield navigation vectors before anything
            # is spawned. Otherwise a degenerate sheet -- three passes of one step,
            # where sheet_layout correctly asks for three fiducials but there is no
            # step interval to divide by -- fails inside _reply, after the operator
            # has already sighted every point on a real table.
            navigation_vectors(
                tuple(Location(0.0, 0.0) for _ in range(fiducials)),
                passes=passes, first_pass_steps=first_pass_steps, hexagonal=hexagonal,
            )
        flags = {
            "read_xy": count, "xy_passes": passes, "xy_steps": steps,
            "xy_pass_name": pass_name, "xy_step_name": step_name,
            "calibrate": int(calibrate),
        }
        if geometry is None:
            flags["xy_locate"] = fiducials
        else:
            if len(geometry) != 8 or not all(math.isfinite(v) for v in geometry):
                raise ValueError("A sheet navigates by eight finite vectors")
            flags["xy_geometry"] = ",".join(repr(float(v)) for v in geometry)
        return self._run(
            mode, spectral, WORKFLOW_FACILITIES["xy"], acknowledge,
            configuration=configuration,
            sheet=(passes, first_pass_steps, hexagonal, 0 if geometry else fiducials), **flags,
        )

    def measure_chart(self, mode: str, *, patch_count: int, passes: int, steps: int,
                      sheets: tuple[int, ...], chart_id: int = 0, spectral: bool = True,
                      acknowledge=None, configuration: Configuration | None = None,
                      ) -> Measurement:
        """A whole chart the instrument already holds, read in one call.

        ``sheets`` is how many passes are on each physical sheet, and must account
        for exactly ``passes`` -- the driver walks that list, so a short one walks
        off the end of the array the helper allocated for it.

        Whether such a chart exists is a runtime status and not a capability
        (``inst.h:467-468``), so it is queried and published alongside the reading
        rather than inferred: see ``Measurement.stored``.
        """
        if workflow_kind(mode) != "chart":
            raise ValueError(f"{mode} is a {workflow_kind(mode)} workflow, not a chart")
        validate_chart_layout(patch_count, passes, steps, sheets, chart_id)
        return self._run(
            mode, spectral, WORKFLOW_FACILITIES["chart"], acknowledge,
            configuration=configuration, read_chart=patch_count, chart_passes=passes,
            chart_steps=steps, chart_sheets=",".join(str(sheet) for sheet in sheets),
            chart_id=chart_id, saved_readings=int(illumination_of(mode) == "s_reflection"),
        )

    def stored_readings(self, mode: str, *, spectral: bool = True) -> int:
        """What the instrument is holding right now, as ``inst_stat_savdrd`` bits.

        Zero means nothing is stored. This is the input chartread.c:900-931 uses to
        prefer a saved route over a live one, and no capability word answers it.
        """
        result = self._run(mode, spectral, (), None, saved_readings=1)
        if result.stored is None:
            # The helper was asked and did not answer. Reporting 0 here would say
            # "nothing is stored", which is a different fact from "nobody knows".
            raise ProtocolError("The helper published no saved-readings status")
        return result.stored

    # -- session ----------------------------------------------------------------

    def _open_session(self, mode: str, spectral: bool, facilities, flags: dict):
        """Open the authenticated native transport; demos replace only this seam."""
        if self.expected_helper_sha256 is None:
            raise ProtocolError("Acquisition requires an authenticated helper SHA-256")
        if hashlib.sha256(self.helper.read_bytes()).hexdigest() != self.expected_helper_sha256:
            raise ProtocolError("Acquisition helper changed since build binding")
        return HelperSession(
            self._argv(mode, spectral, facilities, flags),
            timeout=self.timeout, env={key: value for key, value in
                (os.environ if self.environment is None else self.environment).items()
                if not key.upper().startswith("ARGYLL_")},
            **({"diagnostics": self.native_diagnostics} if self.native_diagnostics else {}),
        )

    def _argv(self, mode: str, spectral: bool, facilities, flags: dict) -> list[str]:
        need2 = 0
        for facility in facilities:
            need2 |= CAPABILITY2[facility]
        argv = [
            str(self.helper),
            # The helper still calls it --usb-index, but the enumeration it
            # indexes is `icomt_instrument | icomt_portattr_all`
            # (session_attach.c:117) -- upstream's own mask, which covers USB,
            # serial AND HID. It is emphatically NOT `icomt_usb | icomt_serial`:
            # session_attach.c:108-116 rejects that mask by name, because HID
            # paths come only from hid_get_paths, which icoms.c:489-493 runs only
            # when the mask carries icomt_hid (0x04, icoms.h:91), so every display
            # colorimeter would have no index and look like an absent instrument.
            #
            # Consequences for this index: HID paths are added FIRST (icoms.c:489-497),
            # so they shift every USB index down. Take this index from a fresh
            # enumeration with the same mask; acquisition also pins its OS path.
            "--usb-index", str(self.path_index),
            "--expected-serial", self.expected_serial or "",
            "--instrument", self.instrument,
            # Positional: cm_parse_selection reads this optional fourth pair
            # immediately after the three mandatory ones, before the ladder's own
            # flags. Always sent so the accepted command line is one shape.
            "--allow-serial-identification", str(int(self.allow_serial_identification)),
            "--mode-word", str(mode_word(mode, spectral=spectral)),
            "--facilities", str(need2),
            # Whether a missing spectral bit is a fault is the caller's claim about
            # this instrument class, not something a capability word states: a
            # colorimeter has none and is well, a spectrophotometer that has lost
            # one is faulty.
            "--spectral-required", str(int(spectral)),
            # Upper bound for the worker's fresh pre-read allowance request.
            # Only this client knows its actual deadline and operator pauses.
            "--read-allowance-s", repr(float(self.timeout)),
        ]
        if self.expected_path is not None:
            position = argv.index("--mode-word")
            argv[position:position] = ["--expected-path", self.expected_path]
        for flag, value in flags.items():
            argv += [f"--{flag.replace('_', '-')}", str(value)]
        if self.native_diagnostics is not None:
            argv += ["--native-debug-level", str(self.native_diagnostics.debug_level)]
        return argv

    def _run(self, mode: str, spectral: bool, facilities, acknowledge, *,
             sheet=None, presenter=None, strip_direction=None,
             configuration: Configuration | None = None, **flags) -> Measurement:
        display_rows = []
        configuration = configuration or Configuration()
        if (configuration.native_controls is not None
                and configuration.native_controls.driver != native_control_driver(self.instrument)):
            raise ValueError("Native control changes belong to a different initialized driver")
        if configuration.native_controls is not None and not (
            flags.get("read") == 1 or flags.get("controls_action") == "apply"
        ):
            raise ValueError("Native settings apply only in a settings operation "
                             "or ordinary spot read")
        if configuration.xyz_only and spectral:
            raise ValueError("xyz_only asks for no spectrum; pass spectral=False with it")
        # The correction's own resources outlive argument construction: a spectral
        # set is a file the helper opens at startup, so it is released only once
        # the session is over, next to session.close().
        resources = ExitStack()
        try:
            if self.cancel is not None and self.cancel.is_set():
                early = InstrumentError("cancelled", 0)
                early.raised_by = "supervisor"   # before any helper existed
                raise early
            flags = {
                **configuration.flags(mode),
                **resources.enter_context(_correction_arguments(configuration.correction)),
                **flags,
            }
            session = self._open_session(mode, spectral, facilities, flags)
        except BaseException as original:
            try:
                resources.close()
            except BaseException as cleanup:
                raise original from cleanup
            raise
        session.operation_id = "cm-session"
        connected: dict = {}
        frequency: MeasuredScalar | None = None
        stored_refresh: StoredRefreshRate | None = None
        timing: MeasuredTiming | None = None
        timing_context: TimingContext | None = None
        presentations: list[PresentationStamp] = []
        timing_window_start: float | None = None
        patches: list[Patch] = []
        locations: list[Location] = []
        calibration: dict | None = None
        calibration_retries = 0
        positioned_steps: set[str] = set()
        stored: int | None = None
        seen: list[dict] = []
        failure: InstrumentError | None = None
        # A settings report complete() validated and matched to the dispatched request.
        checked_controls: dict | None = None
        interrupted = None
        interaction_failure = None
        reply_failure: OSError | None = None
        deadline = time.monotonic() + self.timeout

        def send_reply(action: str, argument: str = "", *, stopping: bool = False) -> bool:
            nonlocal reply_failure
            try:
                session.send(action, argument)
            except OSError as error:
                if not _closed_pipe(error):
                    # Other I/O failures are not evidence of exit: do not wait
                    # for an ending that the worker may never send.
                    raise
                # A closed pipe still has queued events to drain. Remember an
                # actual failed reply, or a cancel sent after a callback failed;
                # a routine stop of an already-ended helper is not a new failure.
                if reply_failure is None and (not stopping or interaction_failure is not None):
                    reply_failure = error
                return False
            return True

        def stop() -> None:
            # The helper may already be gone: it finished before the cancel, or a
            # request it had queued took an earlier cancel as its reply (session.c
            # `answer`) and it exited. Either way its events are still to drain.
            send_reply("cancel", stopping=True)

        ending_failures: list[Exception] = []
        session_finished = False
        try:
            while True:
                if len(seen) >= MAX_SESSION_EVENTS:
                    raise ProtocolError("The helper exceeded this session's event budget")
                now = time.monotonic()
                if interrupted is None and (self.cancel is not None and self.cancel.is_set()
                                             or now >= deadline):
                    interrupted = "cancelled" if now < deadline else "timeout"
                    stop()
                    deadline = now + min(self.timeout, 2.0)
                if interrupted is not None and now >= deadline:
                    interruption = InstrumentError(interrupted, 0, events=seen)
                    interruption.raised_by = "supervisor"
                    raise interruption
                try:
                    message = session.receive_message(timeout=min(0.05, max(0.001, deadline-now)))
                except TimeoutError:
                    continue
                name = message.get("event")
                if (type(message.get("protocol_version")) is not int
                        or message["protocol_version"] != 1):
                    raise ProtocolError(
                        f"Helper spoke protocol version {message.get('protocol_version')!r}"
                    )
                if name not in EVENTS:
                    raise ProtocolError(f"Unknown helper event: {name!r}")
                if connected and message.get("provenance") != connected["provenance"]:
                    raise ProtocolError("Native provenance changed during the operation")
                seen.append(message)
                if name == "terminal":
                    # The helper always follows an error with a terminal event and
                    # then exits. Reading to the end rather than raising here lets
                    # it close its own instrument and report its own exit status;
                    # raising early would terminate it mid-cleanup instead.
                    if message.get("status") not in {"completed", "failed"} or \
                            (message["status"] == "completed") == (failure is not None):
                        raise ProtocolError(
                            f"Terminal status {message.get('status')!r} contradicts the events"
                        )
                    break
                if name == "error":
                    if failure is None:
                        stage, code = message.get("stage"), message.get("native_code")
                        if not isinstance(stage, str) or type(code) is not int:
                            raise ProtocolError("An error event needs a stage and a code")
                        text = message.get("native_text")
                        cut = message.get("native_text_truncated", False)
                        if ((text is not None and not isinstance(text, str))
                                or type(cut) is not bool):
                            raise ProtocolError("An error event's native text is malformed")
                        failure = InstrumentError(stage, code, native_text=text,
                                                  native_text_truncated=cut)
                    continue
                if name == "display_type":
                    if connected or failure:
                        raise ProtocolError("Late native display-type entry")
                    try:
                        display_rows.append(validate_row(
                            {key: value for key, value in message.items() if key not in
                             {"event", "protocol_version", "schema_version", "sequence",
                              "operation_id",
                              "provenance", "backend_version"}}, len(display_rows)))
                    except ValueError as error:
                        raise ProtocolError(str(error)) from error
                    continue
                if name == "connected":
                    try:
                        message["display_types"] = catalogue(
                            message.get("display_types"), display_rows)
                    except ValueError as error:
                        raise ProtocolError(str(error)) from error
                    selection = configuration.display_type
                    applied = message["display_types"]["applied_choice_id"]
                    if applied != (None if selection is None else selection.choice_id):
                        raise ProtocolError("Confirmed display selection differs from request")
                    if (selection and message["display_types"]["catalogue_id"]
                            != selection.catalogue_id):
                        raise ProtocolError("Confirmed display catalogue differs from request")
                    if connected or patches or calibration is not None:
                        raise ProtocolError("Duplicate or late instrument binding")
                    if (type(message.get("spectral")) is not bool
                            or spectral and not message["spectral"]
                            or configuration.xyz_only and message["spectral"]):
                        raise ProtocolError("Confirmed spectral state differs from the request")
                    # spectral=False means not required, not suppress an available spectrum.
                    expected_mode = mode_word(mode, spectral=message["spectral"])
                    if configuration.highres:
                        expected_mode |= MODE_FLAGS["highres"]
                    if type(message.get("mode")) is not int or message["mode"] != expected_mode:
                        raise ProtocolError("Confirmed mode differs from the requested mode")
                    if message.get("provenance") not in {"physical", "simulated"}:
                        raise ProtocolError("Unknown instrument provenance")
                    if "serial" not in message or "serial_encoding" not in message:
                        raise ProtocolError("Connected event lacks the native serial pair")
                    try:
                        _serial_pair(message["serial"], message["serial_encoding"])
                    except ValueError as error:
                        raise ProtocolError(str(error)) from error
                    if (self.expected_serial is not None
                            and (message["serial"] != self.expected_serial
                                 or message["serial_encoding"] != "utf8")):
                        raise ProtocolError("Confirmed serial differs from the selected unit")
                    try:
                        validate_baseline_mode_checks(message.get("baseline_mode_checks"))
                    except ValueError as error:
                        raise ProtocolError(str(error)) from error
                    accepted_rate = message.get("refresh_rate_override_hz")
                    if ((accepted_rate is not None and type(accepted_rate) not in (int, float))
                            or accepted_rate != configuration.refresh_rate):
                        raise ProtocolError("Accepted refresh override differs from request")
                    if flags.get("mode_snapshot"):
                        for key in ("mode_capabilities", "trigger_capabilities",
                                    "filter_capabilities"):
                            if (type(message.get(key)) is not int
                                    or not 0 <= message[key] <= 0xffffffff):
                                raise ProtocolError("Configured capability word is invalid")
                    connected = message
                elif name == "presentation_required":
                    if (not connected or not flags.get("read_timing") or presenter is None
                            or timing is not None or failure
                            or (flags.get("calibrate") and calibration is None)):
                        raise ProtocolError("Unexpected controlled presentation request")
                    phase = "black" if not presentations else "white"
                    if (len(presentations) >= 2 or message.get("phase") != phase
                            or message.get("request_id") != f"timing-{phase}"
                            or type(message.get("minimum_delay_ms")) is not int
                            or message["minimum_delay_ms"] != (0 if phase == "black" else 200)
                            or type(message.get("deadline_ms")) is not int
                            or message["deadline_ms"] != 2000
                            or message.get("native_clock_id") != "worker_monotonic"):
                        raise ProtocolError("Invalid controlled presentation sequence")
                    window_start = finite(message.get("method_window_started_seconds"))
                    if window_start < 0 or (phase == "black" and window_start != 0):
                        raise ProtocolError("Invalid presentation method-window bound")
                    if phase == "white":
                        timing_window_start = window_start
                    if interrupted is not None:
                        stop()
                        continue
                    try:
                        began = time.monotonic()
                        if phase == "white":
                            if self.cancel is None:
                                time.sleep(.2)
                            elif self.cancel.wait(.2):
                                stop()
                                continue
                        remaining = min(deadline, began+2.0) - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("Controlled presentation deadline expired")
                        # A conservative wait after receipt needs no clock
                        # correlation. The presenter draws now, within the
                        # remaining phase budget, without duplicating the wait.
                        stamp = presenter({**message, "minimum_delay_ms": 0,
                            "native_minimum_delay_ms": message["minimum_delay_ms"],
                            "deadline_ms": max(1, int(remaining*1000))})
                        if isinstance(stamp, dict):
                            stamp = _decode(PresentationStamp, stamp)
                        if not isinstance(stamp, PresentationStamp) or stamp.phase != phase:
                            raise ProtocolError("Presenter returned an invalid phase stamp")
                        if time.monotonic() > min(deadline, began+2.0):
                            raise TimeoutError("Controlled presentation deadline expired")
                        if presentations and (stamp.clock_id != presentations[0].clock_id
                                or stamp.stimulus_id != presentations[0].stimulus_id
                                or stamp.display_id != presentations[0].display_id
                                or stamp.presented_at_seconds
                                   <= presentations[0].presented_at_seconds):
                            raise ProtocolError("Presentation identity, clock or order changed")
                        if self.cancel is not None and self.cancel.is_set():
                            stop()
                            continue
                        presentations.append(stamp)
                    except Exception as error:
                        if self.cancel is None or not self.cancel.is_set():
                            interaction_failure = error
                        stop()
                    else:
                        if not send_reply("presented", message["request_id"]):
                            stop()
                elif name == "display_timing":
                    if (not connected or not flags.get("read_timing") or timing is not None
                            or len(presentations) != 2 or patches or failure
                            or (flags.get("calibrate") and calibration is None)
                            or type(message.get("native_code")) is not int
                            or message["native_code"] != 0):
                        raise ProtocolError("Unexpected display-timing measurement")
                    try:
                        timing = MeasuredTiming(**{k: message[k] for k in (
                            "display_update_delay_ms", "instrument_reaction_ms", "unit",
                            "native_method", "time_reference")})
                        timing_context = TimingContext(presentations=tuple(presentations),
                            **{k: message[k] for k in ("native_clock_id",
                                "method_window_started_seconds", "marker_call_started_seconds",
                                "marker_call_finished_seconds", "method_window_finished_seconds")})
                        if timing_context.method_window_started_seconds != timing_window_start:
                            raise ValueError("Native timing window changed after "
                                             "presentation request")
                    except (KeyError, ValueError) as error:
                        raise ProtocolError("Invalid native timing result/context") from error
                elif name == "stored_refresh_rate":
                    if (not connected or not flags.get("stored_refresh")
                            or stored_refresh is not None
                            or patches or failure
                            or (flags.get("calibrate") and calibration is None)):
                        raise ProtocolError("Unexpected stored-refresh state query")
                    try:
                        stored_refresh = StoredRefreshRate(**{key: message[key] for key in
                            ("status", "native_code", "value", "unit", "native_method")})
                    except (KeyError, ValueError) as error:
                        raise ProtocolError("Invalid stored-refresh state") from error
                elif name == "refresh_frequency":
                    if (not connected or not flags.get("read_refresh") or frequency is not None
                            or patches or failure
                            or (flags.get("calibrate") and calibration is None)
                            or message.get("native_code") != 0
                            or type(message.get("native_code")) is not int):
                        raise ProtocolError("Unexpected refresh-frequency measurement")
                    try:
                        frequency = MeasuredScalar(value=finite(message["value"]),
                            quantity=message["quantity"], unit=message["unit"],
                            native_method=message["native_method"])
                    except (KeyError, ValueError) as error:
                        raise ProtocolError("Invalid native refresh-frequency result") from error
                elif name == "xy_location":
                    locations.append(Location(finite(message["x"]), finite(message["y"])))
                elif name == "calibration_ready":
                    if not connected or calibration is not None or patches or frequency is not None:
                        raise ProtocolError("Unexpected calibration outcome")
                    if type(message.get("initial_calibration_suppressed")) is not bool:
                        raise ProtocolError("A calibration outcome must say whether the "
                                            "driver's at-open calibration was waived")
                    calibration = message
                elif name == "calibration_retry":
                    code = message.get("native_code")
                    expected_stage = ("read_sample" if flags.get("read") else
                                      "read_strip" if flags.get("read_strip") else None)
                    if (not connected or not flags.get("calibrate") or calibration is None
                            or patches or failure or calibration_retries or expected_stage is None
                            or message.get("stage") != expected_stage
                            or type(message.get("retry")) is not int or message["retry"] != 1
                            or type(code) is not int or not 0 <= code <= 0xffffffff
                            or code & 0xff0000 != 0x110000):
                        raise ProtocolError("Unexpected calibration retry")
                    calibration_retries += 1
                    calibration = None  # The read invalidated the preceding assessment.
                elif name == "saved_readings":
                    value = message.get("stored")
                    if (not connected or stored is not None or patches
                            or type(value) is not int or not 0 <= value <= 15):
                        raise ProtocolError("Invalid or late saved-readings status")
                    stored = value
                elif name in {"result", "strip_patch", "xy_patch", "chart_patch"}:
                    expected_event, count = next(((event, flags[flag]) for flag, event in
                        (("read", "result"), ("read_strip", "strip_patch"),
                         ("read_xy", "xy_patch"), ("read_chart", "chart_patch"))
                        if flags.get(flag)), (None, 0))
                    if (not connected or name != expected_event or len(patches) >= count
                            or failure or frequency is not None):
                        raise ProtocolError("Unexpected measurement result")
                    if name != "result" and (type(message.get("index")) is not int or
                        message["index"] != len(patches) or message.get("patch_count") != count):
                        raise ProtocolError("Patch order or count differs from the requested batch")
                    patch = _patch(message, connected["spectral"])
                    _validate_reading(patch, mode, configuration.flags(mode)["filter"])
                    patches.append(patch)
                elif name == "read_allowance_required":
                    if (not connected or not (flags.get("read") or flags.get("controls_action"))
                            or patches or failure):
                        raise ProtocolError("Unexpected read allowance request")
                    remaining = deadline - time.monotonic()
                    if interrupted is not None or remaining <= 0 or (
                            self.cancel is not None and self.cancel.is_set()):
                        stop()
                    else:
                        if not send_reply("read_allowance", repr(min(self.timeout, remaining))):
                            stop()
                elif name == "instruction_required":
                    if flags.get("mode_snapshot"):
                        # The query neither calibrates nor reads, so nothing in it
                        # may ask an operator; acknowledging would hide the step.
                        raise ProtocolError("A configured-mode query asked for an "
                                            "operator instruction")
                    if interrupted is not None:
                        # A prompt may already be queued when the stop is sent.
                        # Do not ask for more work, extend the grace period, or
                        # turn an expired deadline into an operator cancellation.
                        stop()
                        continue
                    if message.get("instruction") == "sensor_position":
                        # session.c's `reposition`: the driver's own read or
                        # calibrate() returned inst_wrong_config (0x13xxxx), once
                        # while calibrating and once while reading, at a stage
                        # this request actually runs.
                        code, stage = message.get("native_code"), message.get("stage")
                        read_stage = ("read_sample" if flags.get("read") else
                                      "read_strip" if flags.get("read_strip") else None)
                        at_read = (stage is not None and stage == read_stage and not patches
                                   and (not flags.get("calibrate") or calibration is not None))
                        at_calibration = (stage == "calibrate" and flags.get("calibrate")
                                          and calibration is None)
                        step = "calibrate" if at_calibration else "read"
                        if (not connected or failure or step in positioned_steps
                                or not (at_read or at_calibration)
                                or message.get("request_id") != "position-retry"
                                or type(code) is not int or not 0 <= code <= 0xffffffff
                                or code & 0xff0000 != 0x130000
                                or type(message.get("configuration")) is not int
                                or not 0 <= message["configuration"] <= 0xffffffff
                                or not isinstance(message.get("native_text"), (str, type(None)))
                                or type(message.get("native_text_truncated")) is not bool):
                            raise ProtocolError("Unexpected sensor position request")
                        positioned_steps.add(step)
                    began = time.monotonic()
                    try:
                        reply = self._reply(message, locations, sheet, acknowledge)
                    except Exception as error:
                        interaction_failure = error
                        reply = ("cancel", "")
                    if reply[0] != "cancel":
                        # Only operator wait is outside the acquisition deadline;
                        # time spent writing the reply remains within it.
                        deadline += time.monotonic() - began
                    if not send_reply(*reply):
                        stop()
                    if reply[0] == "cancel":
                        interrupted = "cancelled"
                        deadline = time.monotonic() + min(self.timeout, 2.0)
                elif name == "calibration_condition":
                    # An operator condition that takes NO answer, and the ladder
                    # does not read one -- replying would desynchronise the pipe,
                    # since the next `answer()` would consume it. The caller is
                    # still told, because it has to show the instruction.
                    if acknowledge is not None:
                        try:
                            acknowledge(message)
                        except Exception as error:
                            interaction_failure = error
                            raise
            session.finish_messages(expected_returncode=1 if failure else 0)
            session_finished = True
        except BaseException as error:
            error.events = tuple(seen)
            if error is not interaction_failure:
                ending_failures.append(error)
        finally:
            # Finish both owned cleanup steps before choosing the error. EOF,
            # grace expiry or failed cleanup must not replace a pending callback.
            try:
                session.close()
            except BaseException as error:
                ending_failures.append(error)
            finally:
                try:
                    resources.close()
                except BaseException as error:
                    ending_failures.append(error)
        observed_stop = session.stopped or "unknown"

        def requested_stop() -> str | None:
            return ("client" if interrupted == "cancelled" else
                    "host_deadline" if interrupted == "timeout" else None)

        def complete() -> Measurement:
            """Validate the drained exchange once, including any retained late reading."""
            nonlocal failure, checked_controls
            facts = native_facts(seen)
            controls = native_control_facts(seen)
            if flags.get("controls_action") is not None:
                if controls is None and failure is None:
                    raise ProtocolError("Native controls have no matching complete retained report")
                if controls is not None:
                    request = configuration.native_controls
                    expected = dict(request.fields) if request is not None else {}
                    actual = {name: controls["requested"][name]
                              for index, name in enumerate(CONTROL_FIELDS[controls["driver"]])
                              if controls["requested"]["present"] & (1 << index)}
                    if (controls["driver"] != native_control_driver(self.instrument)
                            or actual != expected):
                        raise ProtocolError("Native control report differs from dispatched changes")
                    checked_controls = controls
                    if controls["failed"] and failure is None:
                        raise ProtocolError("The helper completed after failed native controls")
            elif controls is not None:
                raise ProtocolError("Native control report arrived without a settings request")
            expected_count = sum(flags.get(key, 0) for key in
                                 ("read", "read_strip", "read_xy", "read_chart",
                                  "read_refresh", "read_timing"))
            received_count = len(patches) + int(frequency is not None) + int(timing is not None)
            complete_read = (expected_count > 0 and received_count == expected_count
                             and (not flags.get("calibrate") or calibration is not None))
            if interrupted == "timeout":
                # Expiry and the native outcome are separate facts. Keep a driver's
                # diagnostic even when the host deadline wins; an unrelated native
                # failure is not an acknowledgment of the cancel we sent. A completed
                # late reading is retained below, with the timeout still recorded.
                native_failure = failure
                failure = InstrumentError(
                    "timeout", native_failure.native_code if native_failure else 0,
                    native_text=native_failure.native_text if native_failure else None,
                    native_text_truncated=(native_failure.native_text_truncated
                                           if native_failure else False))
                failure.raised_by = ("helper" if native_failure is not None
                                     and native_failure.stage in {"cancelled", "timeout"}
                                     else "supervisor")
                if native_failure is not None:
                    failure.__cause__ = native_failure.__cause__
            if failure is not None:
                failure.stop_requested_by = requested_stop()
                failure.events = tuple(seen)
                failure.worker_stop = observed_stop
                failure.native_facts = facts
                failure.native_controls = controls
                if not complete_read:
                    raise failure
            if stored_refresh is not None and stored_refresh.status in {"invalid", "failed"}:
                # session.c fails the operation after either; a completion is a lie.
                raise ProtocolError("The helper completed after an unusable stored refresh rate")
            if not connected:
                raise ProtocolError("The helper never reported a binding")
            if type(connected.get("mode")) is not int:
                raise ProtocolError("The binding reported no confirmed mode")
            if (received_count != expected_count or (flags.get("calibrate") and calibration is None)
                    or (flags.get("stored_refresh") and stored_refresh is None)):
                raise ProtocolError("The helper completed without the requested results")
            applied = connected.get("correction", "none")
            if applied not in {"none", "matrix", "spectral"}:
                raise ProtocolError(f"The binding reported an unknown correction: {applied!r}")
            if configuration.correction is not None and applied == "none":
                # The request and the outcome disagree, and this is the one place both
                # are known. Publishing the reading anyway would hand the caller
                # numbers that look corrected and are not -- which is what a silent
                # set_disptype clear does, and the reason the helper reports at all.
                raise ProtocolError(
                    "A colorimeter correction was requested and the driver applied none"
                )
            measurement = Measurement(
                instrument=self.instrument,
                serial=connected.get("serial"),
                serial_encoding=connected["serial_encoding"],
                mode=mode,
                workflow=("refresh_frequency" if flags.get("read_refresh") else
                          "display_timing" if flags.get("read_timing") else
                          "mode_snapshot" if flags.get("mode_snapshot") else
                          "native_controls" if flags.get("controls_action")
                          and not flags.get("read") else
                          "stored_refresh" if flags.get("stored_refresh") and not flags.get("read")
                          else workflow_kind(mode)),
                provenance=str(connected.get("provenance")),
                spectral=bool(connected.get("spectral")),
                confirmed_routes=decode_modes(int(connected["mode"])),
                correction=applied,
                # Apply association before the completed reading can be returned
                # or retained on a native, timeout or cleanup failure. Raw events
                # retain their order, and native patch index values stay unchanged.
                patches=tuple(reversed(patches) if strip_direction == "reverse" else patches),
                strip_direction=strip_direction,
                locations=tuple(locations),
                calibration=calibration,
                stored=stored,
                events=tuple(seen),
                native_facts=facts,
                native_controls=controls,
                refresh_frequency=frequency,
                stored_refresh=stored_refresh,
                mode_snapshot=({key: connected[key] for key in ("mode", "mode_capabilities",
                    "trigger_capabilities", "filter_capabilities", "display_types", "correction")}
                    if flags.get("mode_snapshot") else None),
                display_timing=timing,
                timing_context=timing_context,
                worker_stop=observed_stop,
            )
            if failure is not None:
                failure.measurement = measurement
                raise failure
            return measurement

        primary_ending = ending_failures[0] if ending_failures else None
        if isinstance(primary_ending, InstrumentError):
            primary_ending.worker_stop = observed_stop
            primary_ending.stop_requested_by = requested_stop()
            try:
                primary_ending.native_facts = native_facts(seen)
                primary_ending.native_controls = native_control_facts(seen)
            except ProtocolError as error:
                # Keep incomplete/refused facts out of the record while retaining
                # their validation failure beside the original ending.
                ending_failures.append(error)
        authoritative_failure = (primary_ending
                                 if isinstance(primary_ending, InstrumentError) else None)
        retained_measurement = None
        if ending_failures and session_finished and interaction_failure is None:
            # Cleanup must not erase a validated native ending or its completed
            # reading. Reuse the normal validation path without changing the
            # cleanup exception's identity or its existing transport cause.
            if reply_failure is not None and failure is not None:
                failure.__cause__ = reply_failure
            try:
                retained_measurement = complete()
            except InstrumentError as error:
                authoritative_failure = error
                retained_measurement = error.measurement
            except Exception as error:
                ending_failures.append(error)
        first_control = next((error for error in ending_failures
                              if not isinstance(error, Exception)), None)
        ending_failure = (first_control if first_control is not None else
                          ending_failures[0] if len(ending_failures) == 1 else
                          ExceptionGroup("Session ending and cleanup failed", ending_failures)
                          if ending_failures else None)
        if first_control is not None:
            related = [error for error in ending_failures if error is not first_control]
            if first_control.__cause__ is not None:
                related.insert(0, first_control.__cause__)
            if related:
                first_control.__cause__ = BaseExceptionGroup(
                    "Native ending and cleanup facts", related,
                )
        if ending_failure is not None:
            ending_failure.events = tuple(seen)
            # Producer-owned metadata preserves the frame even when a single
            # cleanup exception remains principal. Arbitrary group members are
            # never treated as this operation's native ending.
            ending_failure.instrument_error = authoritative_failure
            ending_failure.measurement = retained_measurement
            ending_failure.worker_stop = observed_stop
            if authoritative_failure is None:
                # No native ending owns a report validated before cleanup failed.
                ending_failure.native_controls = checked_controls
        if interaction_failure is not None:
            interaction_failure.events = tuple(seen)
            interaction_failure.instrument_error = None
            interaction_failure.measurement = None
            interaction_failure.worker_stop = observed_stop
            if first_control is not None:
                raise first_control from BaseExceptionGroup(
                    "Callback and native cleanup facts", [interaction_failure, *(
                        [first_control.__cause__] if first_control.__cause__ is not None else [])],
                )
            if ending_failure is not None:
                cause = (ExceptionGroup("Reply and session ending failed",
                                       [reply_failure, ending_failure])
                         if reply_failure is not None else ending_failure)
                raise interaction_failure from cause
            if reply_failure is not None:
                raise interaction_failure from reply_failure
            raise interaction_failure
        if ending_failure is not None:
            if reply_failure is not None:
                if ending_failure.__cause__ is not None:
                    raise ending_failure from BaseExceptionGroup(
                        "Session ending and reply diagnostics",
                        [ending_failure.__cause__, reply_failure],
                    )
                raise ending_failure from reply_failure
            raise ending_failure
        if reply_failure is not None:
            if failure is not None:
                # Prefer the helper's retained stage and code only for an actual
                # closed-pipe write. Callback exceptions keep their own identity.
                failure.__cause__ = reply_failure
            else:
                reply_failure.events = tuple(seen)
                raise reply_failure
        try:
            return complete()
        except InstrumentError:
            raise
        except Exception as refusal:
            # complete() refused the drained exchange itself. Keep its frame, the
            # native ending it may contradict and a report already matched to the
            # dispatched request, as the ending path above does for cleanup.
            if failure is not None:
                failure.stop_requested_by = requested_stop()
                failure.events = tuple(seen)
                failure.worker_stop = observed_stop
            refusal.events = tuple(seen)
            refusal.instrument_error = failure
            refusal.measurement = None
            refusal.worker_stop = observed_stop
            refusal.native_controls = (checked_controls if failure is None
                                       or failure.native_controls is None else None)
            raise

    def _reply(self, prompt: dict, locations, sheet, acknowledge) -> tuple[str, str]:
        """Answer one operator instruction. Only navigation is computed here."""
        instruction = prompt.get("instruction")
        if instruction == "xy_navigate":
            if sheet is None:
                raise ProtocolError("A navigation prompt outside a sheet workflow")
            passes, first_pass_steps, hexagonal, fiducials = sheet
            # Upstream cannot diverge here: chartread.c:1192 loops exactly nloc
            # times. Deriving the branch from however many points happened to
            # arrive would navigate a sheet by a geometry nobody established.
            if len(locations) != fiducials:
                raise ProtocolError(
                    f"{len(locations)} points sighted where {fiducials} were requested"
                )
            vectors = navigation_vectors(
                tuple(locations), passes=passes, first_pass_steps=first_pass_steps,
                hexagonal=hexagonal,
            )
            return "navigate", ",".join(repr(float(v)) for v in vectors)
        # Everything else is an operator condition: place a sheet, sight a patch,
        # present a calibration reference. The caller decides; the default is to
        # proceed, and only a condition the driver marked optional may be skipped.
        answer = "acknowledge" if acknowledge is None else acknowledge(prompt)
        if answer not in {"acknowledge", "skip", "cancel"}:
            raise ProtocolError(f"Not an answer to an instrument condition: {answer!r}")
        if answer == "skip" and not prompt.get("skippable"):
            raise ProtocolError("The driver did not offer to skip this condition")
        return answer, ""


#: ``inst_mrt_none``: the driver left the reading's type unset. Upstream reads it
#: as "not stated", compatible with every type (spotread.c:1923-1935,
#: spec2cie.c:260-264), and labels such a reading by the mode it asked for
#: (spotread.c:3023-3027 prints lux from its own `ambient` flag). Two drivers
#: return it on real readings: huey.c:1246 for every emissive one and ss.c:829
#: for every X-Y one.
_UNSET_MEASUREMENT_TYPE = 0


def _labelled_type(patch: Patch, mode: str) -> int:
    """The type a reading's units come from: its own, or its route's when unset."""
    if patch.mtype == _UNSET_MEASUREMENT_TYPE:
        return expected_measurement_type(mode)
    return patch.mtype


def _validate_reading(patch: Patch, mode: str, selected_filter: int | None = None) -> None:
    """Require native quantities to match the requested route; never relabel them.

    An unset type is not a mismatch: it states nothing to relabel."""
    if type(patch.mtype) is not int or patch.mtype not in (
            _UNSET_MEASUREMENT_TYPE, expected_measurement_type(mode)):
        raise ProtocolError("Native measurement type differs from the requested mode")
    if type(patch.mcond) is not int or patch.mcond not in range(6):
        raise ProtocolError("Unknown native measurement condition")
    selected_filter = filter_for(mode) if selected_filter is None else selected_filter
    if (selected_filter != FILTERS["unknown"]
            and patch.mcond != MEASUREMENT_CONDITIONS[selected_filter]):
        raise ProtocolError("Native measurement condition differs from the requested filter")


def calibration_context(outcome: dict | None) -> CalibrationContext:
    if outcome is None:
        return CalibrationContext("unknown", "Calibration status was not queried in this session")
    if outcome.get("status_source") == "upstream_default":
        return CalibrationContext("unknown",
            "The driver exposes no calibration assessment; upstream returned its default")
    remaining = outcome.get("remaining_calibrations", outcome.get("needed_calibrations"))
    if type(remaining) is not int or remaining < 0:
        raise ProtocolError("Invalid calibration outcome")
    required = remaining & ~CALIBRATION_DEFERRABLE_MASK
    # An outcome arrives only once the negotiation has completed (session.c), so
    # required bits are the driver's assessment AFTER it. The label stays conservative;
    # a successful negotiation must not overrule that assessment. The detail says
    # whose word it is, and both facts stay in the record.
    if required:
        return CalibrationContext("required",
            "Calibration negotiation completed; the driver's subsequent assessment still "
            "reports required calibration. Both native results are retained")
    # A reading waives the driver's at-open request (session.c cm_bind); when that
    # left nothing to do, whatever calibration the reading uses is one the driver
    # kept. It claims no more: a mode may need none at all (spydX.c:922, ambient).
    if outcome.get("initial_calibration_suppressed") and not outcome.get("performed"):
        return CalibrationContext("valid",
            "No calibration was performed in this operation. The driver was asked to "
            "keep the calibration it retained instead of calibrating at open, and "
            "reports none outstanding: any calibration the reading uses is one the "
            "driver kept and judges valid. This is not physical validation")
    return CalibrationContext("valid",
        "Driver reports no outstanding non-deferrable calibration; optional work may remain. "
        "This is not physical validation")


@contextmanager
def _correction_arguments(correction):
    """The ladder flags for a correction, for as long as the session needs them.

    A matrix is nine numbers and goes on the command line. A spectral set does
    not: one ``.ccss`` may hold 601 bands (``XSPECT_MAX_BANDS``) times N samples,
    and Linux refuses a single ``argv`` element over 128 KiB (``MAX_ARG_STRLEN``),
    so it is written to a file the helper reads at startup. The file lives until
    the session ends rather than until the process is spawned, because nothing
    here observes when the helper has finished parsing its arguments.

    ``repr`` is used for every number because it round-trips a Python float
    exactly and ``strtod`` on the other side reads what it writes.
    """
    if correction is None:
        yield {}
        return
    material = correction_material(correction)
    if material["kind"] == "matrix":
        yield {
            "correction_matrix": ",".join(
                repr(value) for row in material["matrix"] for value in row
            ),
            "correction_dtech": material["display_technology"],
            "correction_cbid": material["calibration_base_id"],
        }
        return
    with tempfile.TemporaryDirectory(prefix="cm-ccss-") as directory:
        path = Path(directory) / "calibration_spectra.txt"
        header = (
            f"{material['bands']} {material['start_nm']!r} "
            f"{material['end_nm']!r} {material['norm']!r} "
            f"{len(material['samples'])}"
        )
        rows = [" ".join(repr(value) for value in sample)
                for sample in material["samples"]]
        path.write_text("\n".join([header, *rows]) + "\n", encoding="ascii")
        yield {
            "calibration_spectra": str(path),
            "correction_dtech": material["display_technology"],
        }


def _patch(message: dict, spectral: bool) -> Patch:
    """One published patch, with the absence of a spectrum preserved as absence."""
    try:
        native = message["backend_reported"]
        valid = native["XYZ_v"]
        if type(valid) is not int or valid not in (0, 1):
            raise ProtocolError("Invalid XYZ validity")
        xyz = None
        if valid:
            if not isinstance(native["XYZ"], list) or len(native["XYZ"]) != 3:
                raise ProtocolError("XYZ needs three coordinates")
            xyz = tuple(finite(value) for value in native["XYZ"])
        elif native["XYZ"] is not None:
            raise ProtocolError("Invalid XYZ must be omitted, never converted to zeros")
        spectrum = None
        raw = native["spectrum"]
        if raw is None:
            if spectral:
                raise ProtocolError("A spectral instrument published no spectrum")
        else:
            if not spectral:
                raise ProtocolError("A spectrum from an instrument advertising none")
            count = raw["spec_n"]
            if type(count) is not int or not 2 <= count <= 601 \
                    or len(raw["samples"]) != count:
                raise ProtocolError("Invalid spectral count")
            start, end, norm = (
                finite(raw[key]) for key in ("wavelength_start_nm", "wavelength_end_nm", "norm")
            )
            if start <= 0 or end <= start or norm <= 0:
                raise ProtocolError("Invalid spectral grid or normalization")
            spectrum = NativeSpectrum(start, end, norm,
                                      tuple(finite(value) for value in raw["samples"]))
        if xyz is None and spectrum is None:
            raise ProtocolError("A patch with no quantities is not a reading")
        duration = finite(native["duration"])
        if duration < 0:
            raise ProtocolError("A reading does not take negative time")
        index = message.get("index")
        return Patch(native["mtype"], native["mcond"], xyz, spectrum, duration, index)
    except (KeyError, TypeError) as error:
        raise ProtocolError("Incomplete published patch") from error


def observation_from_patch(
    patch: Patch,
    measurement: Measurement,
    *,
    device: DeviceIdentity,
    operation_id: str,
    sample_id: str,
    acquired_at: str,
    raw_backend_output: str,
    observation_id: str | None = None,
) -> Observation:
    """One generic reading as an ``Observation``, so it can be saved and exported.

    Nothing is converted and nothing is computed. The numbers are exactly what the
    driver reported; this attaches the labels that say what they mean, and those
    come from the reading's OWN ``inst_meas_type`` (``quantity_contract``), not
    from the requested mode and not from the instrument. The one exception is a
    driver that left the type unset (``inst_mrt_none``): upstream labels that
    reading by the mode it asked for, and so does this, recording
    ``native_mtype`` 0 and a ``units_source`` saying so.

    ``device`` is supplied by the caller rather than rebuilt here, because which
    unit is being driven is a property of the BINDING and not of one patch. Two
    identical instruments on one bus differ only by the ``device_id`` the caller
    assigned them, and a reading that named itself after its model would make them
    indistinguishable in the saved record. This function may add only the serial
    and encoding pair the instrument volunteered when none was pinned.

    Two absences are preserved as absences rather than filled:

    * A colorimeter reports no spectrum, so ``spectrum`` stays ``None``. A zero
      array would be a fabricated measurement.
    * ``XYZ_v == 0`` means the driver did not produce a valid XYZ, so ``xyz``
      stays ``None`` -- the ladder already refuses a zeroed substitute.

    ``project_computed`` is empty by construction. xyY and Lab are derivations the
    science layer owns, and computing them here would put a derived value in the
    record that holds native observations.

    Raises ``ProtocolError`` for a reading whose measurement type is not
    colorimetric (``inst_mrt_sensitivity``, ``inst_mrt_frequency``) or is another
    route's. A reading nobody can label must not be published with another type's
    units.
    """
    _validate_reading(patch, measurement.mode)
    if device.backend == "argyll-demo" and measurement.provenance != "simulated":
        raise ProtocolError("Demo observations require explicit simulated provenance")
    mtype = _labelled_type(patch, measurement.mode)
    quantities = quantity_contract(mtype)
    if device.serial is None and measurement.serial is not None:
        # The binding did not pin a serial and the instrument volunteered one. That
        # is measured fact about which unit produced this reading, so it is kept.
        # A pinned serial is NOT overwritten: the helper was given it to verify
        # against, and a disagreement there is the helper's to refuse.
        device = replace(device, serial=measurement.serial,
                         serial_encoding=measurement.serial_encoding)
    native_driver = _bound_native_family(measurement.native_facts, device)
    if native_driver == "pr655" and patch.mtype not in (1, 2):
        raise ProtocolError("PR actual quantities have no qualified C-owned publication basis "
                            "without their native measurement type")
    cr_grid = (_cr_spectral_basis(measurement.native_facts) if native_driver == "cr250" else
               _pr_spectral_basis(measurement.native_facts) if native_driver == "pr655" else None)
    published_cr = cr_grid is not None and cr_grid["published_scale"] is not None
    label = "PR" if native_driver == "pr655" else "CR"
    if native_driver and (patch.spectrum is not None) != published_cr:
        raise ProtocolError(f"{label} actual spectral output disagrees with its publication claim")
    if published_cr:
        if (native_driver == "pr655" and cr_grid["published_unit"]
                != ("mW/(m^2.nm)" if patch.mtype == 2 else "mW/(m^2.sr.nm)")):
            raise ProtocolError("PR spectral publication differs from its native measurement type")
        if (patch.spectrum.norm != 1
                or patch.spectrum.start_nm != cr_grid["start_nm"]
                or patch.spectrum.end_nm != cr_grid["end_nm"]
                or patch.spectrum.samples != tuple(
                    value * cr_grid["published_scale"] for value in cr_grid["values"])):
            raise ProtocolError(
                f"{label} published spectrum disagrees with its retained native source",
            )
    if native_driver == "pr655":
        capture = measurement.native_facts["capture"]
        publication = capture.get("xyz_publication")
        if (not isinstance(publication, dict) or set(publication) != {
            "xyz", "unit", "observer", "native_to_published_scale",
        } or publication["unit"] != ("lux" if patch.mtype == 2 else "cd/m2")
                or type(publication["observer"]) is not int
                or publication["observer"] != 2 or capture.get("geometry_valid") is not True
                or capture.get("valid") is not True or capture.get("xyz_parsed") is not True
                or capture.get("xyz_complete") is not True
                or capture.get("native_complete") is not True
                or not isinstance(capture.get("xyz"), list) or len(capture["xyz"]) != 3
                or not isinstance(publication["xyz"], list) or len(publication["xyz"]) != 3):
            raise ProtocolError("PR actual XYZ has no qualified C-owned publication basis")
        scale = finite(publication["native_to_published_scale"])
        if (scale <= 0 or tuple(finite(value) for value in publication["xyz"]) != patch.xyz
                or tuple(finite(value) * scale for value in capture["xyz"]) != patch.xyz):
            raise ProtocolError("PR published XYZ disagrees with its retained native source")
    xyz = None
    if patch.xyz is not None:
        xyz = XYZ(
            values=patch.xyz,
            unit=quantities["xyz_unit"],
            scale=quantities["scale"],
            # inst.h:92-94: XYZ is computed for the CIE 1931 2 degree observer, and
            # for the relative types under an assumed D50 rather than a measured one.
            observer="CIE1931_2",
            illuminant=quantities["illuminant"],
        )
    spectrum = None
    if patch.spectrum is not None:
        spectrum = Spectrum(
            wavelengths_nm=patch.spectrum.wavelengths_nm,
            values=patch.spectrum.samples,
            quantity=quantities["quantity"],
            unit=quantities["spectrum_unit"],
        )
    if xyz is None and spectrum is None:
        # models.ReportedValues enforces this too, but opaquely. The ladder already
        # refuses an empty reading ("empty_reading"), so reaching here means a
        # patch with no measured quantity got past it -- worth naming, not padding.
        raise ProtocolError(
            "A reading with neither a valid XYZ nor a spectrum is not an observation"
        )
    calibration = calibration_context(measurement.calibration)
    configuration = {
        # The condition that WAS present, as distinct from the D50 the XYZ is
        # computed under (inst_meas_cond, inst.h:100-107).
        #
        # These are `native_mcond`/`native_mtype` rather than longer spellings
        # because that is the vocabulary the rest of this project already reads:
        # `processing.reflectance_fraction` and `spectral_exports` both gate on
        # these exact keys plus `spectrum_norm`, and the deleted per-device
        # adapters wrote them too. A second spelling here did not merely look
        # inconsistent --
        # it meant a generic reflective reading could not be converted to
        # reflectance or exported as SPECT at all.
        "native_mcond": str(patch.mcond),
        "native_mtype": str(patch.mtype),
        # Which correction the DRIVER ACCEPTED, never which one was requested.
        "correction_applied": measurement.correction,
        "confirmed_routes": ",".join(measurement.confirmed_routes),
        "acquired_at_source": "client receipt; the ladder publishes no instrument clock",
        "native_duration_seconds": repr(float(patch.duration_seconds)),
    }
    if patch.mtype == _UNSET_MEASUREMENT_TYPE:
        configuration["units_source"] = (
            "requested mode; the driver left inst_meas_type unset (inst_mrt_none)")
    if measurement.strip_direction is not None:
        configuration["strip_direction"] = measurement.strip_direction
        configuration["strip_direction_source"] = "operator-declared; no target recognition"
    if patch.index is not None:
        configuration["native_patch_index"] = str(patch.index)
    if measurement.native_facts is not None:
        # What the driver retained about the attempt that produced this reading --
        # verified identity, every native reply, the settings it checked -- as the
        # support adapter sent it. Nothing here is re-queried or re-derived.
        configuration["native_facts_json"] = json.dumps(
            measurement.native_facts, sort_keys=True, separators=(",", ":"))
    if spectrum is not None:
        # The divisor the values are expressed against (xspect.c:4380-4382). Without
        # it a percent spectrum cannot be turned into a fraction, because the norm
        # is what says which percent scale it is on.
        configuration["spectrum_norm"] = repr(float(patch.spectrum.norm))
    warnings: tuple[str, ...] = ()
    if spectrum is not None and cr_grid is not None:
        configuration["units_source"] = (
            "PR spectral density uses the manual M5 integral/D116 geometry model; "
            "native unit, consistency inference and publication remain separate"
            if native_driver == "pr655" else
            "CR spectral density inferred by same-capture consistency under the driver model; "
            "not manufacturer-confirmed RM Spectrum units"
        )
        warnings += (configuration["units_source"],)
    if measurement.provenance != "physical":
        # Never silently: a record whose numbers were not measured must say so
        # wherever it is read, not only where it was produced.
        warnings += (
            f"{measurement.provenance.upper()}: these values did not come from a "
            "physical measurement of a physical sample.",
        )
    configuration["native_clamping"] = (
        "instNoClamp" if measurement.workflow == "spot" else "driver-defined")
    if mtype in (3, 4):
        # A flash reading is the one place duration is a measured quantity rather
        # than an incidental field, and it is the one place a caller is likely to
        # misuse it. `inst.h:126-127` calls it the "apparent total duration ...
        # typically limited to sampling rate of instrument": it is what the
        # instrument could resolve, not the pulse width.
        #
        # And the values it accompanies are ALREADY integrated over it --
        # `quantity_contract` gives emission_flash time-integrated luminance in
        # cd.s/m^2 and ambient_flash luminous exposure in lux.s
        # (xicc/xspect.h:40-50). Multiplying by duration would integrate twice.
        integrated = "time-integrated luminance" if mtype == 3 else "time-integrated exposure"
        warnings += (
            "Native flash duration is the instrument's apparent total duration, limited "
            "by its sampling rate (inst.h:126-127); it is not the pulse width, the "
            "capture span or host elapsed time. The reported values are already a "
            f"{integrated} ({quantities['xyz_unit']}) and must not be "
            "multiplied by it.",
        )
        if not patch.duration_seconds:
            # Zero is the field's initialised state (i1pro3_imp.c:1787, :4825), so a
            # driver that did not measure one and a genuine zero are indistinguishable
            # here. Reporting it as zero exposure would invent a measurement.
            warnings += (
                "Native flash duration is zero, which means unreported rather than a "
                "zero-length exposure.",
            )
    elif patch.duration_seconds:
        # Outside a flash, `duration` is whatever the driver happened to put in a
        # field inst.h documents only for flash (inst.h:126). It is reported
        # because it was measured, and labelled because it is not an exposure.
        warnings += (
            "Native duration outside a flash measurement is not an exposure or "
            "integration time; inst.h:126 documents the field for flash only.",
        )
    if measurement.calibration and measurement.calibration.get("warning"):
        # The driver's own words, passed through rather than summarised.
        warnings += (f"Native calibration warning: {measurement.calibration['warning']}",)
    return Observation(
        schema_version=1,
        observation_id=observation_id or str(uuid4()),
        operation_id=operation_id,
        sample_id=sample_id,
        acquired_at=acquired_at,
        provenance=measurement.provenance,
        device=device,
        requested_mode=measurement.mode,
        actual_mode=measurement.mode,
        configuration=tuple(sorted(configuration.items())),
        calibration=calibration,
        backend_reported=ReportedValues(xyz=xyz, spectrum=spectrum),
        project_computed=ComputedValues(),
        raw_backend_output=raw_backend_output,
        warnings=warnings,
    )


def observation_from_native_capture(
    facts, *, device, operation_id, sample_id, acquired_at, mode, provenance, raw_backend_output,
):
    """Retain parsed CR/PR originals when an Argyll publication was refused.

    This creates no ipatch, unit conversion or successful measurement outcome.
    Native XYZ and spectral bins retain unverified units/scale; the complete
    driver facts retain radiometric totals, identity and qualification reasons.
    """
    if facts is None or facts.get("driver") not in {"cr250", "pr655"}:
        return None
    driver = facts["driver"]
    capture = facts["capture"]
    xyz = None
    if capture.get("xyz") is not None:
        values = capture["xyz"]
        if (not isinstance(values, list) or len(values) != 3
                or driver == "pr655" and (
                    capture.get("xyz_parsed") is not True
                    or capture.get("xyz_complete") is not True)):
            raise ProtocolError(f"{'CR' if driver == 'cr250' else 'PR'} native XYZ is malformed")
        xyz = XYZ(tuple(finite(value) for value in values), "unverified native XYZ units",
                  "unverified native XYZ scale", "unverified native observer", "unverified")
    grid = _cr_spectral_basis(facts) if driver == "cr250" else _pr_spectral_basis(facts)
    spectrum, mapping_refusal = None, None
    if grid is not None:
        try:
            wavelengths = (_cr_spectrum_wavelengths(grid) if driver == "cr250" else tuple(
                grid["start_nm"] + index * grid["step_nm"] for index in range(grid["count"])))
            spectrum = Spectrum(wavelengths, tuple(grid["values"]),
                                "unverified native spectral density",
                                "unverified native spectral density units")
        except ValueError as error:
            if driver != "cr250":
                raise
            mapping_refusal = f"Native spectrum mapping refused: {error}"
    if xyz is None and spectrum is None:
        return None
    # Only quantities retained in an observation need device attribution. A
    # failure before any capture leaves its raw facts in the operation record.
    _bound_native_family(facts, device, capture_identity=True)
    return Observation(
        schema_version=1, observation_id=str(uuid4()), operation_id=operation_id,
        sample_id=sample_id, acquired_at=acquired_at, provenance=provenance, device=device,
        requested_mode=mode, actual_mode=mode,
        configuration=tuple(sorted(dict(
            native_facts_json=json.dumps(facts, sort_keys=True, separators=(",", ":")),
            units_source="Parsed native originals; requested Argyll publication was refused",
            acquired_at_source="client receipt; the ladder publishes no instrument clock",
            **({"native_spectrum_mapping_refusal": mapping_refusal} if mapping_refusal else {}),
        ).items())),
        calibration=CalibrationContext("unknown", "No calibration qualification is inferred"),
        backend_reported=ReportedValues(xyz=xyz, spectrum=spectrum),
        project_computed=ComputedValues(), raw_backend_output=raw_backend_output,
        warnings=("Requested Argyll publication was refused; native units/scale remain unverified",)
        + ((mapping_refusal,) if mapping_refusal else ())
        + (() if provenance == "physical" else
           (f"{provenance.upper()}: these values did not come from physical measurement.",)),
    )


def validate_nonphotometric_configuration(configuration: Configuration) -> None:
    """Only named display calibration applies to fresh frequency or timing reads."""
    if (configuration.highres or configuration.xyz_only or configuration.averages
            or configuration.xcalstd != "none" or configuration.filter is not None
            or configuration.correction is not None or configuration.refresh_rate is not None
            or configuration.native_controls is not None):
        raise ValueError("Non-photometric measurement accepts only named "
                         "display-type configuration")


def _nonphotometric_record_context(measurement, device, operation_id, sample_id, acquired_at,
                                  native_method, warning):
    connected = next(event for event in measurement.events if event["event"] == "connected")
    configuration = {"native_mode_word": str(connected["mode"]),
                     "native_method": native_method, "automatic_read_retries": "0",
                     "display_types_json": json.dumps(connected["display_types"],
                                                       sort_keys=True, separators=(",", ":"))}
    if measurement.native_facts is not None:
        configuration["native_facts_json"] = json.dumps(measurement.native_facts,
                                                        sort_keys=True, separators=(",", ":"))
    return dict(schema_version=1, observation_id=str(uuid4()),
        operation_id=operation_id, sample_id=sample_id, acquired_at=acquired_at,
        provenance=measurement.provenance,
        device=replace(device, serial=measurement.serial,
                       serial_encoding=measurement.serial_encoding),
        requested_mode=measurement.mode, actual_mode=measurement.mode,
        configuration=tuple(sorted(configuration.items())),
        calibration=calibration_context(measurement.calibration),
        raw_backend_output=json.dumps({"events": measurement.events}, allow_nan=False),
        warnings=(() if measurement.provenance == "physical" else (warning,)))


def observation_from_refresh(measurement: Measurement, *, device: DeviceIdentity,
                             operation_id: str, sample_id: str, acquired_at: str,
                             stimulus: ExternalStimulus) -> ScalarObservation:
    """Retain successful Hz without fabricating colour quantities or conditions."""
    if measurement.workflow != "refresh_frequency" or measurement.refresh_frequency is None:
        raise ValueError("A fresh refresh-frequency measurement is required")
    return ScalarObservation(**_nonphotometric_record_context(measurement, device, operation_id,
        sample_id, acquired_at, "read_refrate",
        "SIMULATED: no physical refresh frequency was measured."),
        backend_reported=measurement.refresh_frequency, stimulus=stimulus)


def observation_from_timing(measurement: Measurement, *, device: DeviceIdentity,
                            operation_id: str, sample_id: str, acquired_at: str,
                            stimulus: TimingStimulus) -> TimingObservation:
    """Retain native marker-relative timing and separately declared client stamps."""
    if (measurement.workflow != "display_timing" or measurement.display_timing is None
            or measurement.timing_context is None):
        raise ValueError("A complete native timing measurement/context is required")
    return TimingObservation(**_nonphotometric_record_context(measurement, device, operation_id,
        sample_id, acquired_at, "meas_delay",
        "SIMULATED: no physical display timing was measured."),
        backend_reported=measurement.display_timing, stimulus=stimulus,
        timing_context=measurement.timing_context)


__all__ = [
    "EVENTS", "Configuration", "Instrument", "InstrumentError", "Location", "Measurement",
    "Patch", "StoredRefreshRate", "calibration_context", "illumination_of", "native_facts",
    "navigation_vectors", "observation_from_native_capture", "observation_from_patch",
    "observation_from_refresh",
    "observation_from_timing", "sheet_layout", "validate_chart_layout",
    "validate_nonphotometric_configuration", "workflow_kind",
]
