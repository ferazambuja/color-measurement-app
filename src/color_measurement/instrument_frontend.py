"""One polled client contract over the shared Argyll acquisition path.

Modes and controls are derived from initialized capability words plus bounded
native driver facts. Acquisition confirms each request through the shared vtable.
The frontend owns supervision and records; clients render its instructions and
never reproduce calibration or per-instrument acquisition logic.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
from uuid import uuid4

from .argyll_capability import (
    CALIBRATION_CONDITIONS,
    CALIBRATION_DEFERRABLE_MASK,
    CALIBRATION_TYPES,
    CAPABILITY2,
    CONDITION_ACTIONS,
    MEASUREMENT_TYPES,
    XCALIBRATION_STANDARDS,
    available_conditions,
    calibration_condition,
    calibration_reference,
    condition_detail,
    decode_calibration_types,
    decode_facilities,
    decode_filters,
    decode_mode_flags,
    decode_modes,
    expected_measurement_type,
    filter_for,
    illumination_of,
    instrument_configuration,
    instrument_display_name,
    mode_word,
    parse_mode_name,
    quantity_contract,
    rank_routes,
    requires_positioning,
    supported_modes,
    workflow_kind,
)
from .argyll_description import validate_native_identity
from .colorimeter_correction import CorrectionMatrix
from .display_types import validate_catalogue
from .frontend import (
    MAX_EVENT_BYTES,
    MAX_REQUEST_BYTES,
    _failure_detail,
    _json,
    _load,
    _load_request,
    _PolledOperation,
)
from .instrument import (
    MAX_PATCHES,
    MAX_SHEETS,
    Configuration,
    Instrument,
    InstrumentError,
    StoredRefreshRate,
    observation_from_native_capture,
    observation_from_patch,
    observation_from_refresh,
    observation_from_timing,
    sheet_layout,
    validate_chart_layout,
    validate_nonphotometric_configuration,
)
from .mode_acceptance import baseline_native_mode, validate_baseline_mode_checks
from .models import (
    DISPLAY_CONTEXT_DESCRIPTION_MAX_BYTES,
    DISPLAY_CONTEXT_ID_MAX_BYTES,
    DeviceIdentity,
    ExternalStimulus,
    TimingStimulus,
)
from .native_controls import (
    CONTROL_FIELDS,
    NativeControlState,
    ack_only_mask,
    control_request_fields,
    native_control_driver,
)
from .operations import OperationBusyError
from .operator_setup import (
    calibration_setup,
    calibration_setups,
    mode_calibration_setups,
    mode_setup,
    physical_chart_setup,
)
from .strip_layout import _validate_value, validate_policies, workflow_fields
from .strip_layout import validate_layout as validate_strip_layout

__all__ = ["InstrumentFrontend", "InstrumentOperation"]

#: Patch counts a workflow accepts. Spot, refresh_frequency and display_timing are
#: one reading; calibrate, mode_snapshot and stored_refresh are none; strip, X-Y and
#: chart are bounded only by the ladder's own CM_MAX_PATCHES.
_PATCH_BOUNDS = {
    "spot": (1, 1),
    "calibrate": (0, 0),
    "mode_snapshot": (0, 0),
    "native_controls": (0, 0),
    "stored_refresh": (0, 0),
    "refresh_frequency": (1, 1),
    "display_timing": (1, 1),
    "strip": (1, MAX_PATCHES),
    "xy": (1, MAX_PATCHES),
    "chart": (1, MAX_PATCHES),
}


#: Layout keys each workflow takes. This is CHART geometry, not instrument
#: capability: `session.h` says so of every field -- "upstream derives all of them
#: in chartread.c from the .ti2 layout and none of them from the instrument".
#: Drivers consume different subsets; strip fields are narrowed by native policy.
def _layout_field(key, label, kind, help, *, required=False, default=None, **bounds):
    return dict(key=key, label=label, type=kind, help=help,
                required=required, default=default, **bounds)


def _integer_field(key, label, help, *, required=True, maximum=MAX_PATCHES, minimum=1,
                   default=None, unit=""):
    return _layout_field(key, label, "integer", help, required=required, default=default,
                         minimum=minimum, maximum=maximum, exclusive_minimum=False, unit=unit)


def _context_field(key, label, help, *, maximum=DISPLAY_CONTEXT_ID_MAX_BYTES):
    return _layout_field(key, label, "text", help, required=True,
                         max_bytes=maximum, encoding="utf8")


_GRID_FIELDS = [
    _integer_field("passes", "Passes", "Pass count from the target layout.", unit="passes"),
    _integer_field("steps", "Steps per pass", "Patch slots per pass from the target layout.",
                   unit="patch slots"),
]
_STIMULUS_FIELDS = [
    _context_field("stimulus_id", "Stimulus identifier", "Identify the declared display stimulus."),
    _context_field("stimulus_description", "Stimulus description",
                   "Describe the display stimulus and keep its declared conditions unchanged.",
                   maximum=DISPLAY_CONTEXT_DESCRIPTION_MAX_BYTES),
]
_LAYOUT_FIELDS = {
    "spot": [], "calibrate": [], "mode_snapshot": [], "strip": [],
    "native_controls": [_layout_field(
        "action", "Settings action", "choice",
        "Read instrument settings or apply explicit changes. This action takes no measurement.",
        default="read", choices=["read", "apply"])],
    "stored_refresh": [_layout_field(
        "calibrate", "Calibrate before querying stored state", "boolean",
        "Run the ordinary calibration workflow before reading stored device status.",
        default=False)],
    "refresh_frequency": _STIMULUS_FIELDS,
    "display_timing": [*_STIMULUS_FIELDS, _context_field(
        "display_id", "Presentation display identifier",
        "Identify the controlled display surface.")],
    "chart": [*_GRID_FIELDS, _layout_field(
        "sheets", "Pass counts per sheet", "integer_array",
        "Ordered pass counts for each sheet; their sum must equal the declared passes.",
        required=True, minimum=1, maximum=MAX_PATCHES, exclusive_minimum=False, unit="passes",
        min_items=1, max_items=MAX_SHEETS), _integer_field(
        "chart_id", "Native chart identifier",
        "Native stored-chart identifier; omission uses zero.",
        required=False, minimum=0, maximum=2147483647, default=0)],
    # `hexagonal` is the CHART's layout, like every other key here: chartread.c
    # derives it from the .ti2, and it changes the navigation vectors, so a client
    # reading a hexagonal chart that cannot say so navigates it as a grid.
    "xy": [*_GRID_FIELDS, _layout_field(
        "pass_name", "Starting pass name", "text",
        "Argyll inst.h: starting pass name from ' A' to 'ZZ'; omission keeps ' A'.",
        max_bytes=2, encoding="ascii_printable"), _layout_field(
        "step_name", "Starting step name", "text",
        "Argyll inst.h: starting step name from ' 1' to '99'; omission keeps ' 1'.",
        max_bytes=2, encoding="ascii_printable"), _layout_field(
        "hexagonal", "Hexagonal target layout", "boolean",
        "Use the target's declared arrangement when deriving navigation vectors.", default=False)],
}
_LAYOUT_KEYS = {key: frozenset(field["key"] for field in fields)
                for key, fields in _LAYOUT_FIELDS.items()}
_LAYOUT_KEYS["strip"] = frozenset({"strip_name", "pass_name", "guide", "patch_width_mm",
                                 "gap_width_mm", "trailer_width_mm", "scan_tolerance", "direction"})
#: Layout keys a workflow cannot proceed without.
_REQUIRED_LAYOUT = {key: frozenset(field["key"] for field in fields if field["required"])
                    for key, fields in _LAYOUT_FIELDS.items()}

_PRESENTATION_FIELDS = [
    _layout_field("phase", "Presentation phase", "choice", "Echo the pending presentation phase.",
                  required=True, choices=["black", "white"]),
    _context_field("stimulus_id", "Stimulus identifier", "Echo the pending stimulus identity."),
    _context_field("display_id", "Display identifier", "Echo the pending display identity."),
    _context_field("clock_id", "Client clock identifier", "Use one clock for both draw receipts."),
    _layout_field("presented_at_seconds", "Draw-completion timestamp", "number",
                  "Record completed drawing in the declared client clock, not optical onset.",
                  required=True, minimum=0, maximum=None, exclusive_minimum=False, unit="s"),
    _layout_field("source", "Timestamp source", "choice",
                  "Identify a client draw-completion receipt.",
                  default="client_draw_completed", choices=["client_draw_completed"]),
]


def _validate_layout(workflow: str, layout, patch_count: int) -> None:
    if not isinstance(layout, dict):
        raise ValueError("Layout must be an object")
    if (workflow == "stored_refresh" and "calibrate" in layout
            and type(layout["calibrate"]) is not bool):
        raise ValueError("Stored-refresh calibration is an explicit boolean")
    unknown = set(layout) - _LAYOUT_KEYS[workflow]
    if unknown:
        raise ValueError(f"Layout keys not used by a {workflow} workflow: {sorted(unknown)}")
    required = _REQUIRED_LAYOUT.get(workflow, frozenset())
    if required - set(layout):
        raise ValueError(f"A {workflow} layout needs {sorted(required)}")
    for field in _LAYOUT_FIELDS[workflow]:
        if field["key"] in layout:
            _validate_value(field, layout[field["key"]])
    if workflow == "display_timing":
        TimingStimulus(layout["stimulus_id"], layout["stimulus_description"], layout["display_id"])
    if workflow == "refresh_frequency":
        ExternalStimulus(layout["stimulus_id"], layout["stimulus_description"])
    if workflow == "strip" and layout.get("direction", "forward") not in {"forward", "reverse"}:
        raise ValueError("Strip direction must be forward or reverse")
    if workflow == "xy":
        sheet_layout(layout["passes"], layout["steps"], patch_count)
        if "hexagonal" in layout and not isinstance(layout["hexagonal"], bool):
            raise ValueError("`hexagonal` must be true or false")
        if layout["passes"] * layout["steps"] < patch_count:
            raise ValueError("A sheet holds `passes` x `steps` patches at most")
    if workflow == "chart":
        validate_chart_layout(patch_count, layout["passes"], layout["steps"],
                              layout["sheets"], layout.get("chart_id", 0))


#: Caption fragments, so one vocabulary describes a mode everywhere instead of
#: each client inventing its own from the underscore-separated name.
_ILLUMINATION_LABELS = {
    "emissive": "Emissive", "ambient": "Ambient", "reflective": "Reflective",
    "transmission": "Transmission", "saved": "Saved",
}
_ACTION_LABELS = {
    "spot": "spot", "strip": "strip", "tele": "telephoto", "chart": "chart",
    "xy": "X-Y chart", "flash": "flash",
    # inst.h:278-279: an alternate 90/diffuse spot geometry that reuses the
    # ambient action bit, which is why it is not simply "spot".
    "ambient": "ambient (90/diffuse)",
}
#: "acquisition", not "integration": the nonadaptive flag (inst.h:232) selects
#: a fixed integration time in the i1Pro family (i1pro3.c:658), the frequency
#: measurement mode in the i1Display 3 (i1d3.c:2315) and reduced low-light
#: averaging in the Klein K-10 (kleink10.c:1347-1349), so the shared caption
#: names what is common to them: acquisition that does not adapt to the light.
_MODIFIER_LABELS = {
    "adaptive": "adaptive acquisition", "nonadaptive": "nonadaptive acquisition",
    "m0": "M0 (legacy illumination; UV unspecified)",
    "m1": "M1 (D50; controlled UV)", "m2": "M2 (UV excluded)",
    "m3": "M3 (polarized; UV excluded)", "d65": "D65",
}

#: The helper's operator-instruction words, each with a STABLE kind. The word is
#: the ladder's own (`session.c`); the kind is this contract's, so a client can
#: choose a screen without matching on a spelling upstream may change.
#: `xy_navigate` is absent: the ladder answers it itself from sighted fiducials
#: and it never reaches a client. Each detail is shared by every instrument, so it
#: names no accessory: a colorimeter has no calibration reference to remove.
_INSTRUCTIONS = (
    {"instruction": "refresh_stimulus", "kind": "present_refresh_stimulus", "answered": True,
     "carries_calibration": False,
     "detail": "Present the declared external display stimulus and keep it unchanged "
               "during the frequency measurement"},
    {"instruction": "strip_position", "kind": "present_strip", "answered": True,
     "carries_calibration": False,
     "detail": "Prepare the requested strip, then continue"},
    {"instruction": "sample_position", "kind": "present_sample", "answered": True,
     "carries_calibration": False,
     "detail": "Position the sample or measurement optics, then continue"},
    # The `calibration` block names WHICH condition and which of the seven actions
    # it is; a client keys its screen on that, not on this word (generic-frontend.md).
    {"instruction": "calibration_setup", "kind": "present_calibration_reference",
     "answered": True, "carries_calibration": True,
     "detail": "Set up what the instrument asks for to calibrate, then continue"},
    # Not a question. The instrument's own action is the trigger, so nothing is
    # waiting for a reply -- see `acknowledged` on the condition.
    {"instruction": "calibration_condition", "kind": "notify_calibration_condition",
     "answered": False, "carries_calibration": True,
     "detail": "Do what the instrument asks for to calibrate; it continues by itself"},
    {"instruction": "xy_sheet", "kind": "place_sheet", "answered": True,
     "carries_calibration": False,
     "detail": "Place the chart on the table"},
    {"instruction": "xy_locate", "kind": "sight_fiducial", "answered": True,
     "carries_calibration": False,
     "detail": "Sight a named fiducial so the table can locate the sheet"},
    # session.c `reposition`: the driver's own reading or calibration step found
    # its sensor, dial, diffuser or adapter where that step cannot work. The
    # `position` block names where the driver needs it.
    {"instruction": "sensor_position", "kind": "reposition_sensor", "answered": True,
     "carries_calibration": False,
     "detail": "The sensor, dial, diffuser or adapter is not where this step needs "
               "it. Move it, then continue; the step is tried once more"},
)


def _check_matrix_rule(model: str, entry: dict, correction) -> None:
    """Refuse before an instrument opens what session.c refuses after it has: a
    correction that would type the reading as another route's, or none where the
    route needs one. The rule is the descriptor's, so the two cannot disagree."""
    rule, mode = entry["correction_matrix_diagonal_mean"], entry["mode"]
    if rule is None:
        return
    if not isinstance(correction, CorrectionMatrix):
        if entry["correction_required"]:
            raise ValueError(
                f"The {model} labels each reading by its correction matrix, so {mode} "
                "needs a .ccmx scaled for illuminance: one whose diagonal averages more "
                f"than {rule['above']:g}")
        return
    matrix = correction.matrix
    # The driver's own arithmetic and order (kleink10.c:1423).
    mean = (matrix[0][0] + matrix[1][1] + matrix[2][2]) / 3.0
    if "above" in rule and not mean > rule["above"]:
        raise ValueError(
            f"This correction's diagonal averages {mean:g}, so the {model} would label the "
            f"reading emission, not ambient. {mode} needs a .ccmx scaled for illuminance: "
            f"one whose diagonal averages more than {rule['above']:g}")
    if "at_most" in rule and mean > rule["at_most"]:
        raise ValueError(
            f"This correction's diagonal averages {mean:g}, so the {model} would label the "
            f"reading ambient. {mode} needs one whose diagonal averages at most "
            f"{rule['at_most']:g}")


def _no_empty_scope(options: list[dict]) -> list[dict]:
    """`applies_to` is null (every mode) or a non-empty list of modes, never empty.

    An empty list and null decode alike in a client's JSON reader (MATLAB's
    jsondecode gives both as empty), so "no mode" would read as "every mode". An
    option no mode of this instrument can take -- a filter with no reflective mode
    -- is therefore published unavailable everywhere, with its reason.
    """
    for option in options:
        if option["applies_to"] == []:
            option.update(applies_to=None, available=False,
                          unavailable_reason=option["unavailable_reason"]
                          or "No mode of this instrument takes this option")
    return options


def _position_detail(prompt: dict) -> dict | None:
    """Name the position a wrong-position prompt asks for, or None for any other.

    The helper publishes the driver's ``config_enum`` answer as an integer and
    the driver's own words for its code; this names the position, so a client
    never learns inst.h's enumeration from whichever instrument it met first.
    """
    if prompt.get("instruction") != "sensor_position":
        return None
    return {
        "needed": instrument_configuration(int(prompt["configuration"])),
        "stage": str(prompt["stage"]),
        "driver_text": prompt.get("native_text"),
    }


def _calibration_detail(prompt: dict) -> dict | None:
    """Name what a calibration prompt is asking for.

    The helper publishes three ArgyllCMS enumerations as integers -- the condition,
    the identifier type and the calibration-type bitfields -- because that is what
    the driver returns. Naming them here rather than in each client is the whole
    point: the i1Pro family raises 7 of the 19 conditions, so a client that learned
    the vocabulary from an i1Pro would meet an unrenderable integer the first time
    it drove a DTP41, a ColorMunki or a SpectroScan.

    Returns None for a prompt that is not a calibration condition (a sheet
    placement, a fiducial sighting), which carry no condition word.
    """
    if "calibration_condition" not in prompt:
        return None
    condition = calibration_condition(int(prompt["calibration_condition"]))
    detail = condition_detail(condition)
    reference = calibration_reference(int(prompt.get("reference_type", 0)))
    return {
        "condition": condition,
        "action": detail["action"],
        "instruction": detail["instruction"],
        # Present only where the condition carries one; says what `reference_id`
        # HOLDS, so a reference identifier, a filter name and a message are not all
        # rendered as the same field.
        "reference_id_holds": detail.get("reference_id"),
        "reference_id": prompt.get("reference_id"),
        "reference": reference["reference"],
        "warnings": list(reference["warning_detail"]),
        # A fraction of full white for the patch the client must display, or None
        # when the condition is not a display one. The ladder runs the ladder
        # upstream runs in dispsup.c; a client must not invent a grey.
        "display_level": prompt.get("display_level"),
        "skippable": bool(prompt.get("skippable")),
        "acknowledged": prompt.get("acknowledged", True),
        "needed": list(decode_calibration_types(int(prompt.get("needed_calibrations", 0)))),
        "remaining": list(
            decode_calibration_types(int(prompt.get("remaining_calibrations", 0)))),
    }


def _mode_axes(name: str) -> dict:
    """The name's own axes, parsed rather than re-split by every client."""
    route, flags, condition = parse_mode_name(name)
    parts = name.split("_")
    modifiers = [part for part in parts[2:] if part != condition]
    return {
        "illumination": parts[0],
        "action": parts[1],
        "modifiers": modifiers,
        "condition": condition,
        "route": route,
        "mode_flags": list(flags),
    }


def _mode_label(name: str) -> str:
    axes = _mode_axes(name)
    head = (f"{_ILLUMINATION_LABELS.get(axes['illumination'], axes['illumination'])} "
            f"{_ACTION_LABELS.get(axes['action'], axes['action'])}")
    tail = [_MODIFIER_LABELS.get(part, part)
            for part in (*axes["modifiers"], *( [axes["condition"]] if axes["condition"] else []))]
    return f"{head}, {', '.join(tail)}" if tail else head


#: How a retained patch is NOTIFIED. Retention is identical either way -- the
#: result always carries the whole record -- and this changes only what the event
#: stream carries.
#:
#: `retained_patch_v1` exists because a spectral patch can approach the event cap
#: on its own: 601 bands (XSPECT_MAX_BANDS) of JSON float is most of
#: MAX_EVENT_BYTES before any envelope, and an event that will not fit is a
#: failed operation rather than a truncated notification. A client that takes
#: references reads the patch from the result instead.
_EVENT_CONTRACTS = ("patch_v1", "retained_patch_v1")


class _Cancelled(Exception):
    """Cancellation stopped the operation; its owner retains the actual cause."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _validate_descriptor_request(request, snapshot):
    """Validate advertised workflow, patch identities and layout without device access.

    Preparation and saved-intent review share the exact descriptor contract.
    Configuration and acquisition remain at their existing frontend owners.
    """
    contract = request.get("event_contract", "retained_patch_v1")
    if type(request.get("schema_version")) is not int or request["schema_version"] != 1:
        raise ValueError("Unsupported frontend request version")
    if request["capability_id"] != snapshot["capability_id"]:
        raise ValueError("Stale or different capability snapshot")

    if not isinstance(request["workflow"], str):
        raise ValueError("Workflow must name an advertised operation")
    offered = {entry["mode"]: entry for entry in snapshot["modes"]}
    if request["mode"] not in offered:
        raise ValueError("Mode is not advertised by this instrument")
    entry = offered[request["mode"]]
    if (not entry["available"]
            and request["workflow"] not in {"mode_snapshot", "stored_refresh"}):
        raise ValueError(entry["unavailable_reason"])
    # The bounds come from the WORKFLOW entry the descriptor published, so a
    # client reading them and this check cannot disagree about what is allowed.
    workflows = {item["workflow"]: item for item in entry["workflows"]}
    if request["workflow"] not in workflows:
        raise ValueError("Workflow is not offered for this mode")
    chosen = workflows[request["workflow"]]
    if not chosen["available"]:
        raise ValueError(chosen["unavailable_reason"])
    if (request["workflow"] in {"mode_snapshot", "stored_refresh", "native_controls"}
            and contract != "retained_patch_v1"):
        raise ValueError("Configuration queries require retained_patch_v1")
    low, high = chosen["min_patches"], chosen["max_patches"]
    ids = request["patch_ids"]
    if (
        not isinstance(ids, list)
        or not low <= len(ids) <= high
        or any(not isinstance(value, str) or not value.strip()
               or len(value.encode("utf-8")) > 128 for value in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError(f"This workflow takes {low} to {high} unique patch IDs")
    if request["workflow"] == "strip":
        validate_strip_layout(request["layout"], chosen)
    else:
        _validate_layout(request["workflow"], request["layout"], len(ids))
    return entry


def descriptor_context(content: Mapping) -> dict:
    """Retain descriptor fences while excluding native report and recovery telemetry.

    Source receipts, initialized identity, capabilities and option schemas stay
    unchanged. Callers can still publish the complete current descriptor.
    """
    logical = dict(content)
    controls = logical.get("native_controls")
    if isinstance(controls, dict):
        logical["native_controls"] = {
            key: value for key, value in controls.items() if key not in {"current", "pending"}
        }
    return logical


def capability_identity(content: Mapping) -> str:
    """The published ``capability_id``: a digest of a descriptor's logical content.

    Logical object content identifies a snapshot. Retained JSON and native
    events can carry equal objects with different member order, so the digest
    covers the sorted canonical form. Recomputing this over a frozen descriptor's
    content without the ``capability_id`` member checks its consistency, except for
    build-receipt provenance and the native settings' current report/recovery debt.
    Those are operational observations, not changed capabilities. Actual settings
    remain in original acquired context; debt is enforced at prepare and dispatch.
    This identity is not a signature.
    """
    logical = descriptor_context(content)
    packet = logical.get("current_binding_identity")
    if isinstance(packet, dict):
        # Build receipts prove origin, not a changed instrument/capability.
        logical["current_binding_identity"] = {
            key: value for key, value in packet.items() if key != "provenance"
        }
    return hashlib.sha256(
        json.dumps(logical, sort_keys=True, allow_nan=False).encode("utf-8")
    ).hexdigest()


class InstrumentFrontend:
    """Offer what one attached instrument says it can do, and drive it.

    ``mode_capabilities``, ``trigger_capabilities`` and ``filter_capabilities``
    are the instrument's own three words, as the ladder publishes them. Obtain
    them from a prior binding or from the description helper; this class does not
    enumerate or attach on its own, because learning capabilities means opening an
    instrument and that is the caller's decision to make.
    """

    def __init__(
        self,
        meter: Instrument,
        *,
        mode_capabilities: int,
        trigger_capabilities: int,
        filter_capabilities: int,
        baseline_mode_checks: dict | None = None,
        strip_policies: dict | None = None,
        ambient_ccmx: bool = False,
        ambient_matrix_threshold: float | None = None,
        provenance: str = "physical",
        serial: str | None = None,
        device_id: str | None = None,
        read_allowance_floor: float | None = None,
        read_allowance_floors: Mapping[int, float] | None = None,
        binding_policy: dict | None = None,
        native_facts: bool = False,
        native_controls_available: bool = False,
        native_control_state: NativeControlState | None = None,
        display_types: dict | None = None,
        current_binding_identity: dict | None = None,
    ) -> None:
        self._display_types = (None if display_types is None else validate_catalogue(display_types))
        if provenance not in ("physical", "simulated", "replay"):
            raise ValueError("Unknown provenance")
        if read_allowance_floor is not None and (
                type(read_allowance_floor) not in (int, float)
                or not math.isfinite(read_allowance_floor) or read_allowance_floor <= 0):
            raise ValueError("A read-allowance floor is finite positive seconds")
        #: The smallest allowance this instrument's driver accepts for one read, from
        #: the build's catalog (InstrumentBuild.bind), or None when it declares none.
        self._read_allowance_floor = (None if read_allowance_floor is None
                                      else float(read_allowance_floor))
        self._binding_policy = deepcopy(binding_policy)
        #: The same, per exact inst_mode word the worker binds, where the driver
        #: gives one (the CR family's cr250_required_floor_s). A started read is held
        #: to its own word's floor; a word without an entry to the type's least.
        floors = {} if read_allowance_floors is None else dict(read_allowance_floors)
        if any(type(word) is not int or type(value) not in (int, float)
               or not math.isfinite(value) or value <= 0 for word, value in floors.items()):
            raise ValueError("Read-allowance floors map mode words to finite positive seconds")
        if floors and (self._read_allowance_floor is None
                       or min(floors.values()) < self._read_allowance_floor):
            raise ValueError("A per-mode floor below the type's floor")
        self._read_allowance_floors = {word: float(value) for word, value in floors.items()}
        if type(native_facts) is not bool:
            raise ValueError("native_facts is a boolean")
        #: The driver retains native facts (native_facts.h), which every reading
        #: carries in its observation. No inline patch event can hold that.
        self._native_facts = native_facts
        if device_id is not None and (not device_id.strip() or len(device_id) > 200):
            raise ValueError("A device_id is a non-empty label of at most 200 characters")
        self._meter = meter
        self._caps = (mode_capabilities, trigger_capabilities, filter_capabilities)
        self._mode_snapshots = {}
        self._native_control_state = (NativeControlState() if native_control_state is None
                                      else native_control_state)
        if not isinstance(self._native_control_state, NativeControlState):
            raise ValueError("Native control recovery requires the session-owned state")
        self._description_lock = self._native_control_state.lock
        self._strip_policies = validate_policies(strip_policies)
        self._baseline_mode_checks = (None if baseline_mode_checks is None else
                                     validate_baseline_mode_checks(baseline_mode_checks))
        if type(ambient_ccmx) is not bool:
            raise ValueError("ambient_ccmx must be a native policy boolean")
        self._ambient_ccmx = ambient_ccmx
        threshold = ambient_matrix_threshold
        if threshold is not None and (type(threshold) not in (int, float)
                                      or not math.isfinite(threshold) or threshold <= 0):
            raise ValueError("ambient_matrix_threshold must be a native policy threshold or None")
        #: driver_policy.h: the diagonal mean above which the driver types a reading
        #: ambient by its correction matrix, or None for a driver that types by route.
        self._ambient_matrix_threshold = None if threshold is None else float(threshold)
        self._provenance = provenance
        self._ownership = threading.Lock()
        self._identity = DeviceIdentity(
            # The MODEL by default, which is unambiguous for one attached
            # instrument and ambiguous for two of the same kind. A caller holding
            # a `discovery.DiscoveredInstrument` per unit passes its own label so
            # the saved records tell those two units apart.
            device_id=device_id or meter.instrument,
            model=instrument_display_name(meter.instrument),
            backend="argyll-native",
            backend_version="3.5.0",
            serial=serial if serial is not None else meter.expected_serial,
            serial_encoding=("utf8" if serial is not None or meter.expected_serial is not None
                             else None),
        )
        self._current_binding_identity = deepcopy(current_binding_identity)
        if current_binding_identity is not None:
            packet = current_binding_identity
            if (not isinstance(packet, dict) or set(packet) != {
                "device_id", "initialized_type", "native_identity", "provenance",
            } or packet["device_id"] != self._identity.device_id
                    or type(packet["initialized_type"]) is not int
                    or packet["initialized_type"] <= 0):
                raise ValueError("Current binding identity disagrees with the bound device")
            validate_native_identity(packet["native_identity"], serial=self._identity.serial)
            receipt = packet["provenance"]
            if (not isinstance(receipt, dict) or set(receipt) != {
                "backend_version", "description_helper_sha256", "session_helper_sha256",
                "build_report_sha256",
            } or receipt["backend_version"] != self._identity.backend_version
                    or receipt["session_helper_sha256"] != meter.expected_helper_sha256
                    or any(value is not None and (
                        not isinstance(value, str) or len(value) != 64
                        or any(char not in "0123456789abcdef" for char in value)
                    ) for key, value in receipt.items() if key != "backend_version")
                    or receipt["description_helper_sha256"] is None
                    or receipt["session_helper_sha256"] is None):
                raise ValueError(
                    "Current binding identity has no matching authenticated worker receipt",
                )
        if type(native_controls_available) is not bool:
            raise ValueError("Native control availability must come from compiled metadata")
        self._native_controls_available = native_controls_available
        self._native_control_report = None
        self._native_control_debt = self._native_control_state.debt
        self._control_key = ((self._identity.model, self._identity.serial)
                             if self._identity.serial is not None else None)
        with self._description_lock:
            self._validate_control_debt()
            if native_controls_available and self._control_key is None:
                raise ValueError("Native controls require a verified reported serial")
            # Recovery debt is keyed by this serial. Only a worker pinned to it is
            # refused when it reaches another unit (Instrument._run's binding check).
            if native_controls_available and meter.expected_serial != self._identity.serial:
                raise ValueError("Native controls require a worker pinned to the bound serial")
            if self._control_key is not None:
                self._ownership = self._native_control_state.ownership.setdefault(
                    self._control_key, threading.Lock(),
                )
        if native_controls_available:
            native = (current_binding_identity or {}).get("native_identity")
            if (not isinstance(native, dict) or native.get("native_controls") is not True
                    or native["driver"] != native_control_driver(self._identity.model)):
                raise ValueError("Compiled control support has no matching current native identity")

    def _validate_control_debt(self):
        if type(self._native_control_debt) is not dict:
            raise ValueError("Malformed shared native control recovery debt")
        for key, value in self._native_control_debt.copy().items():
            if (type(key) is not tuple or len(key) != 2
                    or any(type(item) is not str or not item or "\0" in item for item in key)
                    or type(value) is not tuple or len(value) != 2
                    or type(value[0]) is not str or value[0] not in CONTROL_FIELDS
                    or type(value[1]) is not int
                    or not 0 <= value[1] < 1 << len(CONTROL_FIELDS[value[0]])
                    or native_control_driver(key[0]) != value[0]):
                raise ValueError("Malformed shared native control recovery debt")

    def _control_pending(self):
        with self._description_lock:
            self._validate_control_debt()
            return self._native_control_debt.get(self._control_key, (None, 0))[1]

    def _admit_native_controls(self, workflow, configuration):
        pending = self._control_pending()
        changes = configuration.native_controls
        if changes is not None and not self._native_controls_available:
            raise ValueError("This bound worker does not advertise native controls")
        if changes is not None and workflow not in {"spot", "native_controls"}:
            raise ValueError("Native settings apply only in a settings operation "
                             "or ordinary spot read")
        if workflow == "native_controls" and changes is None:
            return
        if pending and (changes is None or changes.present & pending != pending):
            raise ValueError("Native control recovery requires an explicit apply covering "
                             "every unresolved field before measurement")

    def _retain_native_controls(self, report, changes, *, uncertain=False):
        """Merge one validated report, then keep uncertain dispatched changes pending.

        native_control_facts admits a failed report only when its block covers
        every attempted field, so the block carries that report's debt.
        """
        with self._description_lock:
            pending = self._control_pending()
            if report is not None:
                self._native_control_report = {key: deepcopy(value) for key, value in report.items()
                                               if key not in {"raw_replies", "replies"}}
                pending |= report["blocked"]
                if (not report["failed"] and changes is not None
                        and changes.present & pending == pending):
                    covered = report["confirmed"] | (
                        report["acknowledged"] & ack_only_mask(report["driver"]))
                    if covered & pending == pending:
                        pending = 0
            if uncertain and changes is not None:
                pending |= changes.present
            if self._control_key is not None:
                if pending:
                    driver = native_control_driver(self._identity.model)
                    self._native_control_debt[self._control_key] = (driver, pending)
                else:
                    self._native_control_debt.pop(self._control_key, None)

    # -- description ------------------------------------------------------------

    def modes(self) -> tuple[str, ...]:
        """Caller-facing modes this instrument advertises, in upstream's own order.

        `decode_modes` answers in Argyll's route keys and one route carries
        several caller-facing names, so the names come from `supported_modes`
        and the ORDER from `rank_routes` over the routes behind them.
        """
        routes = decode_modes(self._caps[0])
        # Saved and live routes are ranked SEPARATELY, and rank_routes refuses to
        # rank them together on purpose: upstream chooses the branch from
        # inst_stat_saved_readings -- a runtime status, not a capability
        # (chartread.c:900-931). A DTP20 advertises both, so a descriptor that
        # merged them would be claiming a preference the instrument has not
        # expressed. `stored` says which family a mode belongs to; a client asks
        # `Instrument.stored_readings()` to find out what is actually held.
        live = rank_routes([route for route in routes if not route.startswith("s_")])
        stored = rank_routes([route for route in routes if route.startswith("s_")])
        order = {route: index for index, route in enumerate((*live, *stored))}
        names = supported_modes(self._caps[0], self._caps[2])
        return tuple(sorted(names, key=lambda name: (order[parse_mode_name(name)[0]], name)))

    def _matrix_rule(self, name: str) -> dict | None:
        """The diagonal mean a correction matrix for ``name`` must have, where the
        driver types each reading by its matrix rather than by the mode asked for.

        kleink10.c:1422-1425: above the threshold a reading is ambient, at or below
        it emission. session.c refuses a route the matrix disagrees with at bind,
        after the instrument has been opened; the descriptor states the rule
        first and ``_validate_request`` applies it before anything is opened.
        """
        threshold = self._ambient_matrix_threshold
        if threshold is None:
            return None
        return {"above": threshold} if name.startswith("ambient_") else {"at_most": threshold}

    def _event_contracts(self) -> list[str]:
        """What a request may ask for in ``event_contract``; the first is the default."""
        return ["retained_patch_v1"] if self._native_facts else list(_EVENT_CONTRACTS)

    def _floors_by_mode(self) -> dict | None:
        if not self._read_allowance_floors:
            return None
        spectral = "spectral" in decode_mode_flags(self._caps[0])
        floors = {}
        for name in self.modes():
            with_spectrum = self._read_allowance_floors.get(
                mode_word(name, spectral=spectral))
            xyz_only = (self._read_allowance_floors.get(mode_word(name, spectral=False))
                        if spectral else None)
            floors[name] = {"default": with_spectrum, "xyz_only": xyz_only}
        return floors

    def _read_floor_for(self, request: dict, snapshot: dict, configuration) -> float | None:
        """The floor a started read is held to: its exact word's, else the type's."""
        if request["workflow"] in {"mode_snapshot", "stored_refresh", "native_controls"}:
            return None
        spectral = bool(snapshot["spectral"]) and not configuration.xyz_only
        word = mode_word(request["mode"], spectral=spectral)
        return self._read_allowance_floors.get(word, self._read_allowance_floor)

    def _options(self, primary: int, tertiary: int, facilities, reflective) -> list[dict]:
        """Every option this instrument will accept, and why the others are absent.

        Each entry is gated on the SAME capability the helper checks before it
        calls the driver, and the refusal each gate would produce is named, so a
        client can grey an option out and say why instead of offering it and
        collecting a failure. `session.c` is the authority for every gate here:
        highres at :419, display type at :432, averaging at :449, the correction
        facilities at :492 and :498.
        """
        filters = decode_filters(tertiary)
        flags = decode_mode_flags(primary)
        # Pinned kleink10.c applies CCMX in ambient; other linked ambient
        # colorimeters bypass correction there. This policy is not in cap2.
        correctable = [name for name in self.modes() if not name.startswith("ambient_")
                       or self._ambient_ccmx]
        rows = self._display_types["entries"] if self._display_types else []
        usable = [row for row in rows if row["available"]]
        def name(row):
            return row["label"] if row["label"] is not None else "Unavailable display calibration"
        counts = Counter(name(row) for row in rows)
        def qualified(row):
            return f"{name(row)} [{row['kind']}, #{row['index'] + 1}]"
        details = [row | {"presentation_label": qualified(row)
                         if counts[name(row)] > 1 or not row["available"] else name(row)}
                   for row in rows]
        # A native name can itself equal another entry's qualified label.
        # Qualifying the entire list then guarantees distinct index suffixes.
        if len({row["presentation_label"] for row in details}) != len(details):
            details = [row | {"presentation_label": qualified(row)} for row in rows]
        return _no_empty_scope([
            *([{
                "id": "native_controls", "type": "object", "default": None,
                "label": "Instrument settings",
                "available": self._native_controls_available,
                "members": control_request_fields(native_control_driver(self._identity.model)),
                "validation_owner": "initialized_native_adapter",
                "unavailable_reason": None if self._native_controls_available else
                    "The authenticated worker does not advertise a native control adapter",
                "refusal_stage": "native_controls_unavailable", "applies_to": None,
            }] if self._native_controls_available else []),
            {
                "id": "refresh_rate", "type": "number", "default": None, "unit": "Hz",
                "minimum": 0, "maximum": 150, "allowed_ranges": [[0, 0], [5, 150]],
                "label": "Stored refresh override (0 invalidates the stored rate)",
                "available": "set_refresh_rate" in facilities,
                "unavailable_reason": None if "set_refresh_rate" in facilities else
                    "The configured capability snapshot does not advertise a stored-rate setter",
                "refusal_stage": "refresh_rate_unavailable", "applies_to": None,
            },
            {
                "id": "highres", "type": "boolean", "default": False,
                "label": "High resolution spectral mode",
                "available": "highres" in flags,
                "unavailable_reason": None if "highres" in flags else
                "The instrument does not advertise high-resolution spectral measurements",
                "refusal_stage": "highres_unavailable",
                "applies_to": None,
            },
            {
                "id": "xyz_only", "type": "boolean", "default": False,
                "label": "XYZ only, without the spectrum",
                "available": "spectral" in flags,
                "unavailable_reason": None if "spectral" in flags else
                "The instrument returns no spectrum to leave out",
                "refusal_stage": "xyz_only_unavailable",
                "applies_to": None,
            },
            {
                "id": "averages", "type": "integer", "default": 0,
                "minimum": 0, "maximum": 1000,
                "label": "Readings averaged into one (0 = the driver's own)",
                "available": "average" in filters,
                "unavailable_reason": None if "average" in filters else
                "The instrument does not advertise measurement averaging",
                "refusal_stage": "averaging_unavailable",
                "applies_to": None,
            },
            {
                "id": "xcalstd", "type": "choice", "default": "none",
                "choices": sorted(XCALIBRATION_STANDARDS),
                "label": "Reflective calibration standard",
                # No capability word gates this one; the driver refuses at
                # set_xcalstd if it cannot honour it (session.c, stage `set_xcalstd`).
                "available": bool(reflective),
                "unavailable_reason": None if reflective else
                "Calibration standards apply only to reflective measurements",
                "refusal_stage": "set_xcalstd",
                "applies_to": reflective,
            },
            {
                "id": "display_type", "type": "choice", "default": None,
                "choices": [row["id"] for row in usable],
                "choice_details": details,
                "catalogue_id": (self._display_types["catalogue_id"]
                                 if self._display_types else None),
                "label": "Display calibration",
                "available": bool(usable and "disptype" in facilities),
                "unavailable_reason": None if usable and "disptype" in facilities else
                    "No usable observed display-calibration choices" if rows else
                    "No observed display-calibration choices",
                "refusal_stage": "display_type_unavailable",
                "applies_to": None,
            },
            {
                "id": "filter", "type": "choice", "default": None,
                # Every explicit filter needs its own tertiary capability bit.
                # Only "unknown", meaning leave the filter alone, is unconditional.
                "choices": sorted(available_conditions(tertiary)),
                "label": "Measurement condition",
                "available": bool(reflective), "unavailable_reason": None if reflective else
                "This instrument advertises no reflective mode",
                "refusal_stage": "set_filter",
                # A measurement condition is a property of a reflective
                # measurement; setting one on an emissive read would ask for a
                # condition that read has no notion of.
                "applies_to": reflective,
            },
            {
                "id": "correction", "type": "object", "default": None,
                "label": "Colorimeter correction (.ccmx or .ccss)",
                "shape": {"path": "string"},
                "alternative_shape": {"text": "string"},
                "available": bool(facilities & {"ccmx", "ccss"}),
                "unavailable_reason": None if facilities & {"ccmx", "ccss"} else
                "The instrument does not advertise colorimeter correction support",
                "refusal_stage": "correction_matrix_unavailable",
                "applies_to": correctable,
                "kinds": sorted(facilities & {"ccmx", "ccss"}),
                "note": "Without one the driver's factory/display-type calibration "
                        "remains in use. A suitable correction can reduce display "
                        "spectral mismatch. The file's CGATS identifier determines "
                        "the kind. Ambient CCMX availability comes from the backend.",
            },
        ])

    def _descriptor(self) -> dict:
        primary, secondary, tertiary = self._caps
        facilities = set(decode_facilities(secondary))
        flags = decode_mode_flags(primary)
        modes = []
        names = self.modes()
        # Two names can compose the SAME instrument word: `emissive_spot` and
        # `emissive_spot_adaptive` both parse to (emis_spot, (), None) because
        # adaptive acquisition IS the default and the suffix only says so
        # explicitly. A client showing both as separate choices is offering one
        # measurement twice, so the first name for each effect is marked canonical
        # and the others point at it.
        canonical: dict[tuple, str] = {}
        for name in names:
            route, mode_flags, _ = parse_mode_name(name)
            canonical.setdefault((route, mode_flags, filter_for(name)), name)
        for name in names:
            kind = workflow_kind(name)
            route, mode_flags, _ = parse_mode_name(name)
            effect = canonical[(route, mode_flags, filter_for(name))]
            # A snapshot is of the composed word, which every equivalent name binds.
            configured = self._mode_snapshots.get(effect)
            mode_secondary = (configured["trigger_capabilities"] if configured else secondary)
            mtype = expected_measurement_type(name)
            quantities = quantity_contract(mtype)
            low, high = _PATCH_BOUNDS[kind]
            strip_fields = (workflow_fields(self._strip_policies, name, secondary)
                            if kind == "strip" else None)
            if strip_fields is not None:
                high = strip_fields["max_patches"]
            # X-Y is offered like any other workflow. The operator-sighted
            # fiducial exchange it needs is the `xy_sheet` / `xy_locate`
            # instruction pair, which is the same acknowledgement contract every
            # other operator condition uses -- the instrument reports where its
            # own sight ended up, so a client sights and accepts, and never sends
            # a coordinate. It additionally needs the two positioning methods,
            # which is a capability and reported as one.
            unavailable = None if kind != "xy" or requires_positioning(secondary) else (
                "The instrument does not advertise chart positioning and location "
                "support, so a sheet cannot be located or measured")
            if kind == "strip" and strip_fields is None:
                unavailable = "The native driver has no assessed strip operand contract."
            workflows = [kind, "mode_snapshot"]
            if mode_secondary & CAPABILITY2["get_refresh_rate"]:
                workflows.append("stored_refresh")
            # `calibrate` runs through the spot path (`Instrument.measure`), which
            # refuses a non-spot mode. That is the ONLY gate.
            #
            # It used to also require inst_mode_calibration, and that was wrong
            # twice. Only munki.c sets that bit in the whole pinned tree, so the
            # workflow was offered for 1 instrument of 38 -- and the bit does not
            # mean "can calibrate". inst.h:251-252 defines it as "Configured for
            # calibration": a SENSOR POSITION, reported through meas_config()
            # (inst.h:812, munki.c:213-217) because the ColorMunki's dial has a
            # calibration detent. Nothing else has one.
            #
            # The authority is get_n_a_cals(), including the upstream default, and
            # which the ladder already calls first; `calibrate` is a command-line
            # flag and never enters the mode word. An instrument needing nothing
            # completes immediately with `performed: false`, which is a truthful
            # outcome rather than a refusal.
            if kind == "spot":
                workflows.append("calibrate")
            if self._native_controls_available:
                workflows.append("native_controls")
            if (route in {"emis_spot", "emis_tele"}
                    and mode_secondary & CAPABILITY2["emis_refr_meas"]):
                workflows.append("refresh_frequency")
            if (route in {"emis_spot", "emis_tele"}
                    and mode_secondary & CAPABILITY2["meas_disp_update"]):
                workflows.append("display_timing")
            rule = self._matrix_rule(name)
            modes.append({
                "mode": name,
                "label": _mode_label(name),
                "axes": _mode_axes(name),
                # False for a name that composes the same instrument word as an
                # earlier one; `equivalent_to` names that one.
                "canonical": effect == name,
                "capability_snapshot": deepcopy(configured),
                "equivalent_to": effect,
                # What this mode is EXPECTED to return, so a UI can label an axis
                # before anything is measured. The driver stamps each reading with
                # its own inst_meas_type and the observation's units come from
                # THAT -- this is a forecast, not the record.
                "expected_measurement_type": mtype,
                "expected_measurement_type_name": MEASUREMENT_TYPES[mtype],
                "expected_quantities": quantities,
                # What the operator sets up for this mode -- accessory, holder, dial
                # position -- as the manufacturer's manual states it, with the
                # document, page and a verbatim quote; null where it states none.
                "operator_setup": mode_setup(self._identity.model, route),
                # Source-backed target scope, independent of tele/spectral bits.
                "physical_chart": physical_chart_setup(self._identity.model, route),
                # Possible references for this mode, not current readiness and
                # not the instrument-wide live-prompt manual catalogue below.
                "calibration_setup": mode_calibration_setups(
                    self._identity.model, name, mode_secondary),
                "baseline_native_mode": baseline_native_mode(
                    self._baseline_mode_checks, name, spectral="spectral" in flags),
                "workflow": kind,
                "workflows": [
                    {
                        "workflow": entry,
                        "available": (entry in {"mode_snapshot", "stored_refresh"}
                                      or unavailable is None),
                        "unavailable_reason": (None if entry in {"mode_snapshot", "stored_refresh"}
                                               else unavailable),
                        "min_patches": (_PATCH_BOUNDS[entry][0]
                            if entry in {"calibrate", "mode_snapshot", "stored_refresh",
                                         "native_controls", "refresh_frequency", "display_timing"}
                            else low),
                        "max_patches": (_PATCH_BOUNDS[entry][1]
                            if entry in {"calibrate", "mode_snapshot", "stored_refresh",
                                         "native_controls", "refresh_frequency", "display_timing"}
                            else high),
                        # Chart geometry the REQUEST must carry. `session.h` says of
                        # every one that "upstream derives all of them in
                        # chartread.c from the .ti2 layout and none of them from
                        # the instrument", so the client owns them.
                        "layout_keys": sorted(_LAYOUT_KEYS[entry]),
                        "layout_fields": deepcopy(_LAYOUT_FIELDS[entry]),
                        # One source with `_validate_layout`, so the descriptor
                        # cannot promise a key the validator does not demand.
                        "required_layout_keys": sorted(
                            _REQUIRED_LAYOUT.get(entry, frozenset())),
                        **({"result_kind": "configuration", "expected_quantities": {},
                            "option_ids": ["refresh_rate", "display_type",
                                "highres", "xyz_only", "averages", "xcalstd",
                                "filter", "correction"],
                            "non_acquiring": entry == "mode_snapshot", "passive": False}
                           if entry in {"mode_snapshot", "stored_refresh"} else {}),
                        **({"result_kind": "native_controls", "expected_quantities": {},
                            "non_acquiring": True, "passive": False}
                           if entry == "native_controls" else {}),
                        **({"result_kind": "scalar", "expected_quantities": {
                            "quantity": "refresh_frequency", "unit": "Hz",
                            "native_method": "read_refrate"},
                            "option_ids": (["display_type"]
                                if secondary & CAPABILITY2["disptype"] else []),
                            "instruction": "Present the declared external display stimulus and "
                                           "keep it unchanged during the frequency measurement."}
                           if entry == "refresh_frequency" else {}),
                        **({"result_kind": "timing", "expected_quantities": {
                            "display_update_delay": "ms", "instrument_reaction": "ms",
                            "native_method": "meas_delay", "time_reference": "white_change_call"},
                            "option_ids": (["display_type"]
                                if secondary & CAPABILITY2["disptype"] else []),
                            "presentation_contract": "black_white_draw_completion_v1",
                            "presentation_wait_owner": "backend",
                            "presentation_reply_fields": deepcopy(_PRESENTATION_FIELDS),
                            "presentation_reply_owner": "client_draw_completion",
                            "presentation_constraints": [
                                "phase, stimulus_id and display_id must match the pending request",
                                "black and white receipts must use the same clock and source",
                                "white timestamp must be strictly greater than black",
                                "the reply must arrive before the pending presentation deadline"],
                            "instruction": "Present controlled black and white on the declared "
                                "display. Results use Argyll's software white marker as reference."}
                           if entry == "display_timing" else {}),
                        **((strip_fields or {"layout_keys": [], "required_layout_keys": [],
                                            "layout_fields": []}) if entry == "strip" else {}),
                    }
                    for entry in workflows
                ],
                # An X-Y sheet cannot be located without the positioning group,
                # and the ladder refuses the workflow when it is absent.
                "requires_positioning": kind == "xy" and requires_positioning(secondary),
                # Where the driver types a reading by its correction matrix
                # (driver_policy.h), the diagonal mean this mode's matrix needs --
                # {"above": t} or {"at_most": t} -- and whether it needs one at
                # all, as an ambient reading does. Null and false elsewhere.
                "correction_required": rule is not None and "above" in rule,
                "correction_matrix_diagonal_mean": rule,
                # A stored route reads what the instrument already holds; whether
                # anything IS held is a runtime status, not a capability.
                "stored": name.startswith("saved_"),
                "available": unavailable is None,
                "unavailable_reason": unavailable,
            })
        reflective = [entry["mode"] for entry in modes
                      if entry["axes"]["illumination"] in ("reflective", "saved")]
        options = self._descriptor_options(primary, tertiary, facilities, reflective)
        content = {
            "schema_version": 1,
            # Both native acquisition and the Demo use this descriptor contract.
            "contract": "argyll_generic_instrument_v1",
            "device_id": self._identity.device_id,
            "device": asdict(self._identity),
            # The instrument's own calibration setup from its manual -- which tile,
            # plate or dial position -- for a client's calibration prompt; null
            # where the manual states none. The prompt's `calibration` block still
            # says which condition the driver asks for.
            "calibration_setup": calibration_setups(self._identity.model),
            "provenance": self._provenance,
            # `simulated` values are invented and must never be reported as
            # measurements. `replay` values were MEASURED, on an earlier occasion,
            # so they are not synthetic and not live either -- a client showing a
            # "live" indicator wants `live`, one labelling data trustworthiness
            # wants `synthetic`.
            "synthetic": self._provenance == "simulated",
            "live": self._provenance == "physical",
            # The raw words, so a client can decode anything this project has not
            # named yet rather than waiting for a release.
            "mode_capabilities": primary,
            "trigger_capabilities": secondary,
            "filter_capabilities": tertiary,
            "baseline_mode_checks": deepcopy(self._baseline_mode_checks),
            "facilities": sorted(facilities),
            # Modifiers live in the PRIMARY word, not among the cap2 facilities:
            # `spectral` and `colorimeter` are inst_mode_* bits (inst.h:240-252),
            # and whether a reading carries a spectrum follows from them.
            "mode_flags": list(flags),
            "spectral": "spectral" in flags,
            "display_types": self._display_types,
            "accepts_correction": next(row["available"] for row in options
                                       if row["id"] == "correction"),
            "filters": list(decode_filters(tertiary)),
            "modes": modes,
            "options": options,
            "instructions": [dict(entry) for entry in _INSTRUCTIONS],
            # The calibration vocabulary, published so a client can build its
            # screens from the contract instead of learning condition numbers from
            # whichever instrument it met first. `actions` is the set of genuinely
            # different screens -- seven, for nineteen conditions.
            "calibration": {
                "actions": list(CONDITION_ACTIONS),
                "conditions": [
                    {"condition": name, "value": value, **{
                        key: condition_detail(name).get(key)
                        for key in ("action", "instruction", "acknowledged")
                    }, "reference_id_holds": condition_detail(name).get("reference_id")}
                    for name, value in CALIBRATION_CONDITIONS.items()
                    if name not in ("none", "unknown")
                ],
                # Every condition a client may be asked for is listed above, but
                # an instrument reports WHICH calibrations it needs as a separate
                # bitfield; these are its names.
                "types": list(CALIBRATION_TYPES),
                "deferrable_types": [
                    name for name, bit in CALIBRATION_TYPES.items()
                    if bit & CALIBRATION_DEFERRABLE_MASK
                ],
                "display_level": "A fraction of full white for the patch the client "
                                 "must display; present only for `display_patch` "
                                 "conditions, and never to be invented by the client",
            },
            # What a request may ask for in `event_contract`; omitting the field
            # means the first.
            "event_contracts": self._event_contracts(),
            "default_event_contract": "retained_patch_v1",
            "limits": {
                "binding_timeout_seconds": self._meter.timeout,
                "binding_policy": deepcopy(self._binding_policy),
                "max_patches": MAX_PATCHES,
                "max_event_bytes": MAX_EVENT_BYTES,
                "max_request_bytes": MAX_REQUEST_BYTES,
                "max_patch_id_bytes": 128,
                "max_response_timeout_seconds": 600,
                "native_diagnostics": {
                    "available": self._meter.expected_helper_sha256 is not None,
                    "default_debug_level": 0, "debug_level_range": [0, 8],
                    "default_limit_bytes": 65536, "max_limit_bytes": 1048576,
                    "unavailable_reason": (None if self._meter.expected_helper_sha256 is not None
                                           else "No authenticated native acquisition worker"),
                    "scope": "Auxiliary stderr provenance; no scientific setting changes",
                },
                # The driver's own smallest allowance for one read, or null. An
                # operation timeout at or below it is refused at prepare(): the
                # driver would refuse the read. Exceeding it is not a promise.
                "read_allowance_floor_seconds": self._read_allowance_floor,
                # Per offered mode, the floor of the word a request binds: with the
                # spectrum when the instrument returns one, and with xyz_only.
                "read_allowance_floor_seconds_by_mode": self._floors_by_mode(),
            },
            "capability_source": "instrument capabilities(), decoded; baseline_native_mode "
                                 "reports exact initialized-state checks when observed; "
                                 "acquisition confirms the requested configuration at bind time",
        }
        if self._current_binding_identity is not None:
            content["current_binding_identity"] = deepcopy(self._current_binding_identity)
        if self._native_controls_available or self._control_pending():
            content["native_controls"] = dict(
                available=self._native_controls_available,
                driver=native_control_driver(self._identity.model),
                fields=list(CONTROL_FIELDS[native_control_driver(self._identity.model)]),
                pending=self._control_pending(), current=deepcopy(self._native_control_report),
            )
        return content

    def _descriptor_options(self, primary, tertiary, facilities, reflective):
        # A configured word can expose or withdraw modifiers and filters.
        # Preserve each word's facts instead of ORing them into a global promise.
        options = self._options(primary, tertiary, facilities, reflective)
        by_mode = {}
        for name in self.modes():
            facts = self._mode_snapshots.get(self._canonical_mode(name), {})
            rows = self._options(
                facts.get("mode_capabilities", primary),
                facts.get("filter_capabilities", tertiary),
                set(decode_facilities(facts.get("trigger_capabilities", self._caps[1]))),
                reflective,
            )
            by_mode[name] = {}
            for row in rows:
                applies = row["applies_to"]
                available = row["available"] and (applies is None or name in applies)
                value = dict(available=available, unavailable_reason=None if available else
                             row["unavailable_reason"] or "Option does not apply to this mode")
                value["capability_source"] = facts.get(
                    "source", "initialized_before_mode_configuration",
                )
                for field in ("choices", "kinds"):
                    if field in row:
                        value[field] = deepcopy(row[field])
                by_mode[name][row["id"]] = value
        for row in options:
            states = {name: values[row["id"]] for name, values in by_mode.items()}
            names = [name for name, value in states.items() if value["available"]]
            row["by_mode"] = states
            row["available"] = bool(names)
            every_mode = names and len(names) == len(by_mode) and row["applies_to"] is None
            row["applies_to"] = None if every_mode else names or None
            row["unavailable_reason"] = (None if names else row["unavailable_reason"]
                                         or "No configured mode advertises this option")
            for field in ("choices", "kinds"):
                if field in row:
                    choices = []
                    for state in states.values():
                        if state["available"]:
                            for choice in state[field]:
                                if choice not in choices:
                                    choices.append(choice)
                    if names or field == "kinds":
                        row[field] = choices
        return options

    def _canonical_mode(self, mode):
        """The first offered name composing the same word, as `equivalent_to` names it."""
        route, mode_flags, _ = parse_mode_name(mode)
        key = (route, mode_flags, filter_for(mode))
        return next(name for name in self.modes()
                    if (*parse_mode_name(name)[:2], filter_for(name)) == key)

    def _retain_mode_snapshot(self, mode, options, measurement):
        facts = deepcopy(measurement.mode_snapshot)
        if not isinstance(facts, dict):
            raise ValueError("Mode query completed without configured native facts")
        facts.update(source="mode_snapshot", requested_options=deepcopy(options),
            stored_refresh=(asdict(measurement.stored_refresh)
                            if measurement.stored_refresh else None))
        catalogue = validate_catalogue(facts["display_types"])
        with self._description_lock:
            # Each operation starts at the driver default, so a selection this
            # query applied is a fact of the query, not of the instrument. A
            # correction also changes readback without applying a list choice.
            # Neither provides a new observation of the driver's default.
            if (catalogue["applied_choice_id"] is not None
                    or options.get("correction") is not None):
                if self._display_types is None:
                    catalogue = None
                else:
                    catalogue = {**catalogue, "applied_choice_id": None,
                                 "readback": deepcopy(self._display_types["readback"])}
            self._display_types = catalogue
            self._mode_snapshots = {**self._mode_snapshots, self._canonical_mode(mode): facts}
            descriptor = _load(self.describe_json())
            entry = next(item for item in descriptor["modes"] if item["mode"] == mode)
            return {**facts, "workflows": deepcopy(entry["workflows"]),
                    "capability_id": descriptor["capability_id"]}

    def describe_json(self, device_id: str | None = None) -> str:
        """The descriptor, with a ``capability_id`` over its own content."""
        if device_id is not None and device_id != self._identity.device_id:
            raise ValueError("Unknown bound device")
        with self._description_lock:
            snapshot = self._descriptor()
            snapshot["capability_id"] = capability_identity(snapshot)
            return _json(snapshot)

    # -- operations -------------------------------------------------------------

    def _validate_request(self, text: str):
        request = _load_request(text)
        if not isinstance(request, dict):
            raise ValueError("Request must be a JSON object")
        fields = {"schema_version", "device_id", "capability_id", "mode", "workflow",
                  "patch_ids", "options", "layout"}
        # References keep complete spectra and session context outside bounded
        # notifications. Clients retrieve the canonical retained result.
        if request.keys() - {"event_contract"} != fields:
            raise ValueError("Request must contain exactly the generic frontend fields")
        contract = request.get("event_contract", "retained_patch_v1")
        if contract not in _EVENT_CONTRACTS:
            raise ValueError(f"Unknown event contract: {contract!r}")
        if contract not in self._event_contracts():
            # Refused before any worker runs: the reading would be taken and
            # retained, then fail to notify, leaving a failed operation that holds
            # a valid reading.
            raise ValueError(
                f"{contract} cannot carry this instrument's readings, which include its "
                "driver's native facts; use retained_patch_v1")
        request["event_contract"] = contract
        snapshot = _load(self.describe_json(request["device_id"]))
        entry = _validate_descriptor_request(request, snapshot)
        # Options are checked by constructing the Configuration the ladder will be
        # given: one validator, and it is the same one acquisition uses. A
        # correction is parsed here too, so a request naming an unreadable or
        # non-correction file is refused before an instrument is opened.
        configuration = Configuration.from_options(
            request["options"], display_types=snapshot["display_types"],
            controls_driver=native_control_driver(self._identity.model)
            if self._native_controls_available else None)
        if request["workflow"] == "native_controls":
            action = request["layout"].get("action", "read")
            if (action == "read") != (configuration.native_controls is None):
                raise ValueError("Native controls read takes no changes; "
                                 "apply requires a changes object")
        self._admit_native_controls(request["workflow"], configuration)
        for option in snapshot["options"]:
            if option["id"] not in request["options"]:
                continue
            value = request["options"][option["id"]]
            # Neutral defaults do not ask a driver to use an unavailable feature.
            if value == option["default"]:
                continue
            state = option["by_mode"][request["mode"]]
            if not state["available"]:
                raise ValueError(state["unavailable_reason"])
            if (option["id"] in {"filter", "xcalstd"}
                    and value not in state["choices"]):
                raise ValueError("Option choice is not advertised for this configured mode")
        if configuration.display_type is not None and "disptype" not in decode_facilities(
                (entry.get("capability_snapshot") or {}).get(
                    "trigger_capabilities", self._caps[1])):
            raise ValueError("The instrument does not advertise display-type selection")
        if configuration.display_type is not None:
            selected = snapshot["display_types"]["entries"][configuration.display_type.index]
            if (selected["kind"] == "ccmx" and request["mode"].startswith("ambient_")
                    and not self._ambient_ccmx):
                raise ValueError("This driver does not apply installed CCMX in ambient mode")
        if request["workflow"] in {"refresh_frequency", "display_timing"}:
            validate_nonphotometric_configuration(configuration)
            if contract != "retained_patch_v1":
                raise ValueError("Scalar results require retained_patch_v1")
        configuration.flags(request["mode"])
        if (configuration.filter is not None
                and configuration.filter not in available_conditions(
                    (entry.get("capability_snapshot") or {}).get(
                        "filter_capabilities", snapshot["filter_capabilities"]))):
            raise ValueError("The requested reflective filter is not advertised by this instrument")
        if configuration.correction is not None and not snapshot["accepts_correction"]:
            raise ValueError(
                "This instrument does not advertise colorimeter correction support, so a "
                "colorimeter correction cannot be applied to its readings"
            )
        if configuration.correction is not None:
            kind = "ccmx" if isinstance(configuration.correction, CorrectionMatrix) else "ccss"
            offer = next(option for option in snapshot["options"] if option["id"] == "correction")
            if kind not in offer["by_mode"][request["mode"]]["kinds"]:
                raise ValueError(f"This instrument does not advertise {kind.upper()} support")
        _check_matrix_rule(self._identity.model, entry, configuration.correction)
        return request, snapshot, configuration

    def prepare(self, request_json: str, *, response_timeout: float = 120.0,
                timeout: float | None = None, native_diagnostic_target=None,
                native_debug_level=0, native_diagnostic_limit_bytes=65536) -> InstrumentOperation:
        """Return a validated operation/identity without a worker or device I/O.

        Retain this handle before dispatch(). ``timeout`` defaults to the bound
        instrument's own -- the one bind()
        checked against the driver's floor. A fixed default here once replaced it
        with 30 s, so every CR-250 operation reached its driver already refused."""
        return InstrumentOperation(
            self, request_json, response_timeout=response_timeout,
            timeout=self._meter.timeout if timeout is None else timeout,
            native_diagnostic_target=native_diagnostic_target,
            native_debug_level=native_debug_level,
            native_diagnostic_limit_bytes=native_diagnostic_limit_bytes,
        )


class InstrumentOperation(_PolledOperation):
    """One prepared generic operation, explicitly dispatched and drained."""

    @property
    def request_json(self) -> str:
        """Return this prepared operation's frozen, normalized request text."""
        return self._initial_result["request_json"]

    def _start_worker(self):
        # A query completing between this request's validation and its ownership
        # changes the descriptor; take ownership against the identity it was
        # validated on, under the lock that commit holds.
        self._dispatch_stage = "snapshot_validation"
        with self._frontend._description_lock:
            self._frontend._admit_native_controls(
                self._request["workflow"], self._configuration,
            )
            if _load(self._frontend.describe_json())["capability_id"] != (
                    self._snapshot["capability_id"]):
                self._dispatch_stage = "stale_snapshot"
                raise ValueError("Stale or different capability snapshot")
            super()._start_worker()

    def __init__(self, frontend: InstrumentFrontend, request_json: str, *,
                 response_timeout: float, timeout: float, native_diagnostic_target=None,
                 native_debug_level=0, native_diagnostic_limit_bytes=65536) -> None:
        from .native_protocol import NativeDiagnosticCapture

        if type(native_debug_level) is not int or not 0 <= native_debug_level <= 8:
            raise ValueError("Native debug level must be an integer in [0, 8]")
        if (type(native_diagnostic_limit_bytes) is not int
                or not 1 <= native_diagnostic_limit_bytes <= NativeDiagnosticCapture.MAX_BYTES):
            raise ValueError("Native diagnostic cap must be integer bytes in [1, 1048576]")
        self._native_diagnostic_capture = None
        if native_debug_level:
            if native_diagnostic_target is None or frontend._meter.expected_helper_sha256 is None:
                raise ValueError(
                    "Native diagnostics require an owned target and authenticated worker"
                )
            self._native_diagnostic_capture = NativeDiagnosticCapture(
                native_diagnostic_target, debug_level=native_debug_level,
                limit_bytes=native_diagnostic_limit_bytes,
            )
        self._frontend = frontend
        request, self._snapshot, self._configuration = frontend._validate_request(request_json)
        for value in (response_timeout, timeout):
            if type(value) not in (int, float) or not 0 < value <= 600:
                raise ValueError("Timeouts must be finite seconds in (0, 600]")
        floor = frontend._read_floor_for(request, self._snapshot, self._configuration)
        if floor is not None and float(timeout) <= floor:
            raise ValueError(
                f"This instrument's driver needs more than {floor:g} s for one read; "
                f"this operation allows {float(timeout):g} s")
        self._layout = dict(request["layout"])
        self._initialize_operation(
            request,
            identity=frontend._identity,
            provenance=frontend._provenance,
            ownership=frontend._ownership,
            instrument_state="not_applicable" if frontend._provenance == "simulated"
            else "unknown",
        )
        self._response_timeout = float(response_timeout)
        self._meter = replace(frontend._meter, timeout=float(timeout), cancel=self._cancel,
                              native_diagnostics=self._native_diagnostic_capture)
        self._configuration_data = tuple(sorted({
            "capability_id": self._snapshot["capability_id"],
            "mode_capabilities": str(self._snapshot["mode_capabilities"]),
            "trigger_capabilities": str(self._snapshot["trigger_capabilities"]),
            "filter_capabilities": str(self._snapshot["filter_capabilities"]),
            "requested_options_json": _json(request["options"]),
            "event_contract": request.get("event_contract", "retained_patch_v1"),
            "requested_layout_json": _json(request["layout"]),
            "capability_source": self._snapshot["capability_source"],
        }.items()))
        if frontend._ownership.locked():
            raise OperationBusyError("An operation already owns this instance")

    def native_diagnostics(self):
        """Return auxiliary capture facts even when no valid terminal result exists."""
        return (None if self._native_diagnostic_capture is None
                else self._native_diagnostic_capture.snapshot())

    def native_diagnostic_bytes(self):
        """Native owner only: closed bounded originals, including initial sink failures."""
        return (None if self._native_diagnostic_capture is None
                else self._native_diagnostic_capture.original_bytes())

    def _finish(self, status, error, *, native_failure=None):
        if self._native_diagnostic_capture is not None:
            self._native_diagnostic_capture.not_started(
                error or "Operation ended without starting a native helper",
            )
        return super()._finish(status, error, native_failure=native_failure)

    def _finish_without_dispatch(self, status, error, *, failure_stage=None):
        if self._native_diagnostic_capture is not None:
            self._native_diagnostic_capture.not_started(error)
        return super()._finish_without_dispatch(status, error, failure_stage=failure_stage)

    def _acknowledge(self, prompt: dict) -> str:
        """Surface one instrument condition and block for the client's answer.

        Cancellation travels to the native callback so the driver can unwind.
        """
        if self._cancel.is_set():
            return "cancel"
        # A synthetic device-button action may carry the preceding calibration
        # notification as context. Its condition remains unacknowledged; the
        # outer action alone asks for a response, so the reference stays visible.
        condition = _calibration_detail(prompt.get("calibration_context", prompt))
        if condition is not None:
            # The manual's setup for THIS condition only: an instrument asks for more
            # than one, and a note written for one is wrong on another.
            condition["setup"] = calibration_setup(self._frontend._identity.model,
                                                   condition["condition"])
        # A condition the instrument itself triggers takes no answer: the ladder
        # does not read one and will call `calibrate()` straight back. The client
        # is TOLD -- it has to show the instruction -- so this is emitted rather
        # than asked, and the return value is discarded by the caller.
        if prompt.get("acknowledged") is False:
            self._emit("instruction_notified", {
                "instruction": str(prompt.get("instruction", "calibration_condition")),
                "calibration": condition,
                "native_event": prompt,
            })
            return "acknowledge"
        instruction = str(prompt.get("instruction"))
        detail = instruction
        if instruction == "refresh_stimulus":
            detail = (f"Present external stimulus {self._layout['stimulus_id']}: "
                      f"{self._layout['stimulus_description']}. "
                      "Keep it unchanged during the frequency measurement.")
        if instruction == "strip_position":
            if (prompt.get("effective_trigger") not in {
                    "programmatic", "instrument_switch", "user_switch"}
                    or not isinstance(prompt.get("trigger_guidance"), str)
                    or not prompt["trigger_guidance"]):
                raise ValueError("The strip prompt lacks its negotiated trigger guidance")
            detail = (f"Present the strip in {self._layout.get('direction', 'forward')} order. "
                      "Patch IDs follow that declared order; no target recognition is performed. "
                      + prompt["trigger_guidance"])
        response = self._ask(
            instruction,
            detail,
            ("accept", "skip", "cancel") if prompt.get("skippable")
            else ("accept", "cancel"),
            request_id=str(prompt.get("request_id")),
            metadata={"native_event": prompt, "calibration": condition,
                      "position": _position_detail(prompt)},
        )
        if response == "cancel":
            return "cancel"
        # The ladder takes only these two words; nothing is coerced.
        return "skip" if response == "skip" else "acknowledge"

    def _run(self) -> None:
        status, error = "failed", "Operation did not complete"
        native_failure = None
        software_stopped = True
        control = None
        try:
            self._emit_started()
            if self._cancel.is_set():
                raise _Cancelled
            mode, workflow = self._request["mode"], self._request["workflow"]
            ids = list(self._request["patch_ids"])
            # The instrument's own claim, from its primary word. A caller that
            # demanded a spectrum from a colorimeter would be refused at bind
            # time; deriving it here means the request never has to state it.
            spectral = bool(self._snapshot["spectral"]) and not self._configuration.xyz_only
            live = illumination_of(mode) != "s_reflection"
            common = dict(spectral=spectral, acknowledge=self._acknowledge,
                          configuration=self._configuration)
            if workflow == "native_controls":
                changes = self._configuration.native_controls
                measurement = self._meter.native_controls(
                    mode, None if changes is None else dict(changes.fields),
                    configuration=replace(self._configuration, native_controls=None),
                )
            elif workflow == "mode_snapshot":
                measurement = self._meter.mode_snapshot(mode, configuration=self._configuration)
            elif workflow == "stored_refresh":
                measurement = self._meter.stored_refresh_rate(mode,
                    calibrate=self._layout.get("calibrate", False),
                    acknowledge=self._acknowledge, configuration=self._configuration)
            elif workflow == "display_timing":
                measurement = self._meter.measure_display_timing(mode, calibrate=True,
                    presenter=self._present, acknowledge=self._acknowledge,
                    configuration=self._configuration)
            elif workflow == "refresh_frequency":
                measurement = self._meter.measure_refresh(mode, calibrate=True,
                    acknowledge=self._acknowledge, configuration=self._configuration)
            elif workflow == "calibrate":
                # Calibration ON ITS OWN -- no reading. `measure(calibrate=True)`
                # takes one, which the workflow's own bounds (0 patches) then
                # refuse, so every calibrate operation failed here.
                measurement = self._meter.calibrate(mode, **common)
            elif workflow == "strip":
                measurement = self._meter.measure_strip(
                    mode, patch_count=len(ids), calibrate=live, **self._layout, **common)
            elif workflow == "chart":
                measurement = self._meter.measure_chart(
                    mode, patch_count=len(ids),
                    **{**self._layout, "sheets": tuple(self._layout["sheets"])}, **common)
            elif workflow == "xy":
                # The operator-sighted exchange, which `measure_sheet` has always
                # implemented: the ladder asks for each fiducial in turn
                # (`xy_locate`), the INSTRUMENT reports where its sight ended up,
                # and `navigation_vectors` derives the sheet from those points.
                # Nothing was missing here but the call.
                layout = dict(self._layout)
                measurement = self._meter.measure_sheet(
                    mode, patch_count=len(ids),
                    passes=layout.pop("passes"), steps=layout.pop("steps"),
                    calibrate=live, **layout, **common)
            else:
                measurement = self._meter.measure(mode, calibrate=live, positioned=None, **common)

            if workflow in {"mode_snapshot", "stored_refresh"}:
                self._complete_configuration_query(measurement)
                return
            self._retain_measurement(measurement)
            # Preserve a completed read even when cancellation lost the native
            # race. The operation still records the client's cancellation.
            if self._cancel.is_set():
                # The read finished before the cancellation could reach it. The
                # reading is kept; the record says the cancellation changed nothing.
                self._retain_stop(cancellation="client", acknowledged_by="none",
                                  worker_stop=measurement.worker_stop,
                                  device_completion="completed")
                raise _Cancelled
            self._software_stopped = True
            status, error = "completed", None
        except _Cancelled:
            status, error = "cancelled", (
                "Stopped after dispatch failure" if self._dispatch_error is not None
                else "Cancelled by the client")
            self._software_stopped = True
        except BaseException as failure:
            native = getattr(failure, "instrument_error",
                             failure if isinstance(failure, InstrumentError) else None)
            # A native ending's own report is merged by _retain_native_failure.
            # Instrument._run attaches a report it validated only to a failure it
            # raised itself (a refused exchange or cleanup) when no native ending
            # owns that report. That outcome was not accepted, so dispatched changes
            # stay uncertain, as they do when no report survived a bound worker.
            owner = native or failure
            checked = None if failure is native else getattr(failure, "native_controls", None)
            if ((checked is not None or getattr(owner, "native_controls", None) is None)
                    and any(event.get("event") == "connected"
                            for event in getattr(owner, "events", ()))):
                self._frontend._retain_native_controls(
                    checked, self._configuration.native_controls, uncertain=True,
                )
                retained = {} if checked is None else {"native_control_json": _json(checked)}
                if self._configuration.native_controls is not None:
                    retained["native_control_exchange_json"] = _json(list(owner.events))
                self._configuration_data = tuple(sorted(
                    (dict(self._configuration_data) | retained).items()))
            if not isinstance(failure, Exception):
                control = failure
            software_stopped = getattr(failure, "worker_stop", None) != "unknown"
            if not software_stopped:
                self._configuration_data = tuple(sorted((dict(self._configuration_data) | {
                    "worker_stop": "unknown", "device_completion": "unknown"}).items()))
            self._retain_context(getattr(failure, "events", ()))
            status, error = "failed", _failure_detail(failure)
            retention_notes = []
            measurement = getattr(failure, "measurement", None)
            if isinstance(native, InstrumentError):
                if failure is not native:
                    error = _failure_detail(BaseExceptionGroup(
                        "Instrument operation and session ending failed", [native, failure]))
                try:
                    self._retain_native_failure(native)
                except Exception as retention_error:
                    retention_notes.append(
                        f"; native original retention refused: {retention_error}"
                    )
                if native.stop_requested_by is not None or native.stage in ("cancelled", "timeout"):
                    # Four facts kept apart, because they are different claims: that a
                    # stop was asked for, who (if anyone) answered it, how the helper was
                    # SEEN to end, and that nothing here knows what the device did. A
                    # helper killed mid-exposure answered nothing, and its instrument
                    # may have finished the measurement or not.
                    self._retain_stop(
                        cancellation=(native.stop_requested_by or
                                      ("client" if native.stage == "cancelled"
                                       else "host_deadline")),
                        acknowledged_by=("helper" if native.raised_by == "helper"
                                         and native.stage in ("cancelled", "timeout") else "none"),
                        worker_stop=native.worker_stop,
                        device_completion=("completed" if native.measurement is not None
                                           and native.measurement.has_readings else "unknown"))
                # The stage the helper named, kept as it named it: a client that shows
                # this to an operator must see the ladder's own word, not a paraphrase.
                # Published twice on purpose -- as the sentence, and as the pair a
                # client branches on without matching English.
                if failure is native:
                    error = f"{native.stage} (native code {native.native_code})"
                native_failure = (native.stage, native.native_code)
                measurement = native.measurement
            if measurement is not None and measurement.has_readings:
                try:
                    self._retain_measurement(measurement)
                except Exception as retention_error:
                    retention_notes.append(f"; result publication failed: {retention_error}")
            if failure is native and native.stage == "cancelled":
                status, error = "cancelled", (
                    "Stopped after dispatch failure" if self._dispatch_error is not None
                    else "Cancelled by the client")
            error += "".join(retention_notes)
        finally:
            # A terminal result does not confirm that an unobserved helper
            # stopped. Clients use this fact to decide whether to allow Start.
            self._software_stopped = software_stopped
            if not self.done:
                self._finish(status, error, native_failure=native_failure)
        if control is not None:
            raise control

    def _complete_configuration_query(self, measurement):
        """Publish query completion and its descriptor transition atomically.

        A query has no reading to preserve against a late cancellation. Keep
        observed native context, but change client capabilities only when its
        completed result is actually published. Cancellation after publication
        sees an already completed operation through the ordinary condition lock.
        """
        with self._condition, self._frontend._description_lock:
            if self._cancel.is_set():
                self._retain_context(measurement.events)
                self._retain_stop(cancellation="client", acknowledged_by="none",
                                  worker_stop=measurement.worker_stop,
                                  device_completion="completed")
                raise _Cancelled
            previous = self._frontend._display_types, self._frontend._mode_snapshots
            committed = False
            try:
                self._retain_measurement(measurement)
                self._software_stopped = True
                self._finish("completed", None)
                committed = _load(self.result_json())["status"] == "completed"
            finally:
                if not committed:
                    (self._frontend._display_types,
                     self._frontend._mode_snapshots) = previous

    def _present(self, message):
        """Request client rendering without accepting an ordinary operator answer."""
        payload = {**message,
            "stimulus_id": self._layout["stimulus_id"],
            "stimulus_description": self._layout["stimulus_description"],
            "display_id": self._layout["display_id"],
            "timestamp_source": "client_draw_completed",
            "rgb": [0.0, 0.0, 0.0] if message["phase"] == "black" else [1.0, 1.0, 1.0]}
        return self._request_presentation(payload)

    def _retain_measurement(self, measurement) -> None:
        """Retain a complete native batch before emitting bounded notifications."""
        ids = self._request["patch_ids"]
        self._retain_context(measurement.events)
        if measurement.native_controls is not None:
            self._frontend._retain_native_controls(
                measurement.native_controls, self._configuration.native_controls,
            )
            self._configuration_data = tuple(sorted((dict(self._configuration_data) | {
                "native_control_json": _json(measurement.native_controls),
            }).items()))
        if measurement.workflow == "native_controls":
            return

        if measurement.stored_refresh is not None:
            self._configuration_data = tuple(sorted((dict(self._configuration_data) | {
                "stored_refresh_json": _json(asdict(measurement.stored_refresh))}).items()))
        if measurement.workflow == "mode_snapshot":
            facts = self._frontend._retain_mode_snapshot(
                self._request["mode"], self._request["options"], measurement)
            self._configuration_data = tuple(sorted((dict(self._configuration_data) | {
                "mode_snapshot_json": _json(facts)}).items()))
            return

        if measurement.workflow == "display_timing":
            observation = observation_from_timing(measurement, device=self._identity_record,
                operation_id=self.operation_id, sample_id=ids[0], acquired_at=_now(),
                stimulus=TimingStimulus(self._layout["stimulus_id"],
                    self._layout["stimulus_description"], self._layout["display_id"]))
            self._publish_complete_batch([observation])
            return

        if measurement.workflow == "refresh_frequency":
            observation = observation_from_refresh(measurement, device=self._identity_record,
                operation_id=self.operation_id, sample_id=ids[0], acquired_at=_now(),
                stimulus=ExternalStimulus(self._layout["stimulus_id"],
                                          self._layout["stimulus_description"]))
            self._publish_complete_batch([observation])
            return

        if measurement.stored is not None:
            # inst_stat_saved_readings is a RUNTIME status, not a capability
            # (inst.h:467-468), so no descriptor field can carry it and a
            # client cannot ask for it without opening the instrument. It
            # travels on the result of the operation that already asked.
            self._configuration_data = tuple(sorted(
                (dict(self._configuration_data)
                 | {"stored_readings": str(measurement.stored)}).items()
            ))
        acquired = _now()
        observations = []
        for index, patch in enumerate(measurement.patches):
            if index >= len(ids):
                raise ValueError("The instrument returned more patches than were requested")
            observations.append(observation_from_patch(
                patch, measurement, device=self._identity_record,
                operation_id=self.operation_id, sample_id=ids[index], acquired_at=acquired,
                raw_backend_output=_json({"events": [event for event in measurement.events
                    if event["event"] not in {"result", "strip_patch", "xy_patch", "chart_patch"}
                    or event.get("index", 0) == (patch.index if patch.index is not None else 0)]}),
                observation_id=str(uuid4()),
            ))
        self._publish_complete_batch(observations)

    def _retain_stop(self, *, cancellation, acknowledged_by, worker_stop,
                     device_completion) -> None:
        """How an interrupted operation actually ended, in the operation's
        configuration beside the other native context (not new schema-1 fields).

        ``worker_stop`` is what HelperSession.close() observed -- "exited",
        "terminated" or "killed" -- or "unknown" when a started helper's end was
        not confirmed, or "not_started" when no helper existed. Only
        an observed end is recorded; nothing here claims a driver acknowledgment
        or a confirmed device abort that did not happen.
        """
        if self._dispatch_error is not None and cancellation == "client":
            cancellation = "host_failure"
        self._configuration_data = tuple(sorted((dict(self._configuration_data) | {
            "stop_requested_by": cancellation,
            "stop_acknowledged_by": acknowledged_by,
            "worker_stop": worker_stop or "not_started",
            "device_completion": device_completion,
        }).items()))

    def _retain_native_failure(self, failure) -> None:
        """Keep what the DRIVER said about a failed attempt on the result.

        Beside ``calibration_events_json``, in the operation's configuration, not as
        a new schema-1 field: the record decoder requires its exact field set, so a
        new field would stop every previously saved result from loading.

        ``error_native_text`` is the driver's own sentence (``interp_error``),
        bounded by the helper, which says when it cut it; never parsed here.
        ``native_facts_json`` is what the driver retained about the attempt, from
        its support adapter: present for a warning-bearing or refused read that
        published no measurement, because those replies are the evidence.
        """
        extra = {}
        if failure.native_controls is not None:
            self._frontend._retain_native_controls(
                failure.native_controls, self._configuration.native_controls,
            )
            extra["native_control_json"] = _json(failure.native_controls)
        if failure.native_text is not None:
            extra["error_native_text"] = failure.native_text
            extra["error_native_text_truncated"] = (
                "true" if failure.native_text_truncated else "false")
        if failure.native_facts is not None and failure.measurement is None:
            extra["native_facts_json"] = _json(failure.native_facts)
        if extra:
            self._configuration_data = tuple(sorted(
                (dict(self._configuration_data) | extra).items()))
        if failure.native_facts is not None and failure.measurement is None:
            if self._request["workflow"] != "spot" or self._patches:
                return
            observation = observation_from_native_capture(
                failure.native_facts, device=self._identity_record,
                operation_id=self.operation_id, sample_id=self._request["patch_ids"][0],
                acquired_at=_now(), mode=self._request["mode"],
                provenance=self._frontend._provenance,
                raw_backend_output=_json({"events": list(failure.events)}),
            )
            if observation is not None:
                self._publish_complete_batch([observation])
                self._configuration_data = tuple(sorted((dict(self._configuration_data) | {
                    "native_observation_retained": "true",
                    "argyll_publication_status": "refused",
                }).items()))

    def _retain_context(self, events) -> None:
        from .instrument import calibration_context
        from .models import CalibrationContext

        binding = next((event for event in events if event["event"] == "connected"), None)
        if binding is not None and "display_types" in binding:
            self._configuration_data = tuple(sorted((dict(self._configuration_data) | {
                "display_types_json": _json(binding["display_types"])}).items()))
        # Both facts are kept only as validated: a failure path also carries the
        # frame that was refused, and a malformed one must not replace the error.
        override = None if binding is None else binding.get("refresh_rate_override_hz")
        if type(override) in (int, float) and override == self._configuration.refresh_rate:
            self._configuration_data = tuple(sorted((dict(self._configuration_data) | {
                "refresh_rate_override_hz": str(override)}).items()))
        refresh = next((event for event in events if event["event"] == "stored_refresh_rate"), None)
        try:
            state = None if refresh is None else StoredRefreshRate(**{
                key: refresh[key] for key in
                ("status", "native_code", "value", "unit", "native_method")})
        except (KeyError, TypeError, ValueError):
            state = None
        if state is not None:
            self._configuration_data = tuple(sorted((dict(self._configuration_data) | {
                "stored_refresh_json": _json(asdict(state))}).items()))
        stored = next((event["stored"] for event in reversed(events)
                       if event["event"] == "saved_readings"
                       and type(event.get("stored")) is int and 0 <= event["stored"] <= 15), None)
        if stored is not None:
            self._configuration_data = tuple(sorted((dict(self._configuration_data) |
                {"stored_readings": str(stored)}).items()))
        context = [event for event in events if event["event"] in
                   {"calibration_ready", "calibration_condition", "calibration_retry"} or
                   (event["event"] == "error" and
                    type(event.get("native_code")) is int and
                    event.get("native_code", 0) & 0xff0000 == 0x110000) or
                   event.get("instruction") == "calibration_setup" or
                   (event.get("instruction") == "sensor_position"
                    and event.get("stage") == "calibrate")]
        latest = next((event for event in reversed(context) if event["event"] in
                       {"calibration_ready", "calibration_retry", "error"}), None)
        outcome = latest if latest and latest["event"] == "calibration_ready" else None
        if context:
            self._calibration = (CalibrationContext("required",
                "The read reported that calibration is needed; recovery has not completed")
                if latest and outcome is None else calibration_context(outcome))
            self._configuration_data = tuple(sorted((dict(self._configuration_data) | {
                "calibration_events_json": _json(context),
                "calibration_outcome_json": _json(outcome),
            }).items()))
