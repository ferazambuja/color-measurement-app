"""Synthetic equipment previews through the production generic acquisition path.

Only instrument I/O is substituted. These are workflow models, not optical,
timing, transport or calibration-accuracy models of physical equipment.
"""

from __future__ import annotations

import copy
import json
import math
import random
import time
from dataclasses import dataclass, field, replace
from functools import cache
from importlib.resources import files
from pathlib import Path

from .argyll_capability import (
    FILTERS,
    MEASUREMENT_CONDITIONS,
    MODE_FLAGS,
    calibration_condition,
    decode_facilities,
    filter_for,
    illumination_of,
    mode_word,
    parse_mode_name,
)
from .frontend import _load_request
from .instrument import Instrument
from .instrument_frontend import InstrumentFrontend
from .native_protocol import ProtocolError

__all__ = ["demo_catalog_json", "demo_frontends", "is_demo_frontend"]

_UNMODELED = [
    "Optical accuracy, actual instrument noise, physical calibration validity and actual timing",
    "Calibration expiry: a kept calibration lasts for the rest of the demo, whatever "
    "the driver's own time and temperature limits",
    "USB/serial faults, real device buttons, motors and accessory sensing",
    "Numerical effects of resolution, averaging, XRGA and display-type selection",
    "Display-type-dependent calibration: prompts replay the recorded default, "
    "regardless of the selected display type",
    "Correction transforms (CCMX/CCSS controls explicitly unavailable in this demo)",
    "Real stored-memory contents; the requested chart is synthesized",
]
#: The demo's own control: replay a run recorded with the sensor, dial, diffuser or
#: adapter in the wrong place, for a route where one was recorded.
_MISPLACED = "demo_sensor_misplaced"
_CHART_SAMPLE = "demo_chart_sample"
_MAX_DEMO_ATTEMPT = 2**53 - 1  # Exactly representable in the MATLAB request client.


@cache
def _data():
    return json.loads(files("color_measurement").joinpath("data/demo.json").read_text())


def _event(event_name, **fields):
    return {"protocol_version": 1, "provenance": "simulated", "event": event_name, **fields}


@cache
def _palette():
    return json.loads(files("color_measurement").joinpath("data/demo_palette.json").read_text())


@dataclass
class _DemoSamples:
    """Free-sample sequence, or an explicitly selected guided synthetic target."""

    index: int = 0

    def apply(self, patch, mode, chart_sample=None):
        reported = patch["backend_reported"]
        spectrum = reported.get("spectrum")
        if spectrum:
            grid = [spectrum[k] for k in (
                "spec_n", "wavelength_start_nm", "wavelength_end_nm", "norm")]
        else:
            grid = [36, 380, 730, 1 if illumination_of(mode) == "emission" else 100]
        palette = _palette()
        index = self.index if chart_sample is None else chart_sample["cell_index"] - 1
        seed = self.index + 1 if chart_sample is None else (
            140 * (chart_sample["attempt"] - 1) + chart_sample["cell_index"])
        colour = palette["palettes"][":".join(str(int(v)) for v in grid)][index % 12]
        # A uniform gain keeps native XYZ and spectrum consistent. This is only
        # illustrative brightness jitter, not sensor noise or setting effects.
        gain = 1.0 + random.Random(seed).uniform(-.005, .005)
        reported["XYZ"] = [v * gain for v in colour["XYZ"]]
        if spectrum:
            spectrum["samples"] = [v * gain for v in colour["samples"]]
        patch["demo_sample"] = {"profile": palette["names"][index % 12], "gain": gain,
                                "seed": seed}
        if chart_sample is None:
            self.index += 1
        else:
            patch["demo_sample"]["chart_sample"] = dict(chart_sample)


@dataclass(frozen=True)
class _DemoInstrument(Instrument):
    persona: str = ""
    misplaced: bool = False
    #: Modes calibrated in this demo, which its driver may keep for later readings.
    #: One set per equipment preview, shared with its wrong-position twin.
    calibrated: set = field(default_factory=set, compare=False)
    samples: _DemoSamples = field(default_factory=_DemoSamples, compare=False)
    chart_sample: dict | None = field(default=None, compare=False)

    def _open_session(self, mode, spectral, facilities, flags):
        persona = _data()["personas"][self.persona]
        return _DemoSession(persona, mode, spectral, facilities, flags, self.expected_serial,
                            misplaced=self.misplaced, calibrated=self.calibrated,
                            name=self.persona, samples=self.samples, chart_sample=self.chart_sample)


class _DemoSession:
    """In-process instrument I/O, consumed by Instrument's unchanged event parser."""

    def __init__(self, persona, mode, spectral, facilities, flags, serial, *, misplaced,
                 calibrated, samples, chart_sample, name=""):
        self.operation_id = ""
        self._name = name
        self._samples = samples
        self._chart_sample = chart_sample
        self._cancelled = False
        self._closed = False
        self._pending = None
        self._terminal = None
        self._timing_marker = None
        #: HelperSession.stopped records how a helper PROCESS was seen to end. There
        #: is no process here, so the value says so rather than borrowing "exited".
        self.stopped = "simulated"
        self._events = iter(self._produce(persona, mode, spectral, facilities, flags, serial,
                                          misplaced, calibrated))

    def _produce(self, persona, mode, spectral, facilities, flags, serial, misplaced,
                 calibrated):
        route = (persona["misplaced"] if misplaced else persona["routes"]).get(mode)
        available = decode_facilities(persona["capabilities"]["trigger_capabilities"])
        if route is None or not set(facilities) <= set(available):
            yield _event("error", stage="demo_route_unmodeled", native_code=0)
            yield _event("terminal", status="failed")
            return
        if any(key.startswith("correction") for key in flags):
            raise ValueError("Correction transforms are not modeled by the demo")
        if "scan_tolerance" in flags and "has_scan_toll" not in available:
            raise ValueError("This persona does not advertise scan-tolerance control")
        connected = copy.deepcopy(route["connected"])
        configured_facilities = decode_facilities(connected["trigger_capabilities"])
        if "refresh_rate" in flags and "set_refresh_rate" not in configured_facilities:
            yield _event("error", stage="refresh_rate_unavailable", native_code=0)
            yield _event("terminal", status="failed")
            return
        if spectral and not connected["spectral"]:
            raise ValueError("This demo instrument supplies no spectrum")
        # XYZ alone: bound as the helper would bind it, without the spectral bit,
        # and the recorded spectrum left out of every reading below.
        if flags.get("xyz_only"):
            if not connected["spectral"]:
                raise ValueError("This demo instrument supplies no spectrum to leave out")
            connected["spectral"] = False
        connected.update(serial=serial, serial_encoding="utf8",
                         mode=mode_word(mode, spectral=connected["spectral"]))
        if any(flags.get(key) for key in ("read_refresh", "read_timing",
                                         "mode_snapshot", "stored_refresh")):
            connected["trigger"] = None
        if flags.get("highres"):
            connected["mode"] |= MODE_FLAGS["highres"]
        connected["refresh_rate_override_hz"] = flags.get("refresh_rate")
        connected["demo_configuration"] = dict(flags)
        connected["demo_numerical_effects_modeled"] = False
        types = connected["display_types"]
        for row in types.pop("entries"):
            yield _event("display_type", **row)
        types["applied_choice_id"] = flags.get("display_choice_id")
        yield connected
        if (flags.get("mode_snapshot")
                or (flags.get("stored_refresh") and not flags.get("calibrate"))):
            if flags.get("stored_refresh"):
                yield self._stored_refresh(connected, flags)
            yield _event("terminal", status="completed")
            return

        count, kind = next(((flags[key], event) for key, event in
                           (("read", "result"), ("read_strip", "strip_patch"),
                            ("read_xy", "xy_patch"), ("read_chart", "chart_patch"))
                           if flags.get(key)), (0, None))
        # Once this demo has calibrated the mode, a reading replays the route read
        # from a start whose driver kept that calibration (calibration_retained.json):
        # no prompt for what it keeps, and whatever it still asks for. A recorded
        # wrong position is replayed as recorded.
        kept = None if misplaced or mode not in calibrated else persona["retained"].get(mode)
        setup = copy.deepcopy(route["setup"] if kept is None else kept)
        # A wrong position met while calibrating belongs to the calibration, in the
        # order recorded: before its calibration_ready, as the helper asks it.
        calibration = [e for e in setup if e["event"] in
                       {"calibration_ready", "calibration_condition"}
                       or e.get("instruction") == "calibration_setup"
                       or (e.get("instruction") == "sensor_position"
                           and e.get("stage") == "calibrate")]
        # A reading replays its own route's recorded calibration: a route recorded
        # from a fresh start calibrated before its first reading, as its driver
        # does with nothing valid restored, and one whose driver needs nothing
        # reports just that. The frontend sets `calibrate` on every live reading,
        # so the flag alone says nothing about what the instrument needs.
        # Calibration on its own (no reading) is the operator's explicit request,
        # and only there do the recorded examples below stand in.
        # Asked for the recorded wrong position, calibration on its own replays the
        # run's own calibration where the position was met while calibrating; a
        # run that met it only while reading calibrates as any other.
        recorded_position = misplaced and any(
            e.get("instruction") == "sensor_position" for e in calibration)
        if (flags.get("calibrate") and not count
                and not (flags.get("read_refresh") or flags.get("read_timing"))
                and not recorded_position):
            # Recorded calibration scenarios are examples, not predictions of
            # the calibration a real instrument currently needs. Reuse white
            # reference examples for reflective reads; display examples only
            # for the same route. A physical-filter prompt stays condition-specific.
            for calibration_mode, sample in persona["calibrations"].items():
                is_filter = any(calibration_condition(e["calibration_condition"])
                                == "change_filter" for e in sample if "calibration_condition" in e)
                same_route = parse_mode_name(calibration_mode)[0] == parse_mode_name(mode)[0]
                reflective = (illumination_of(calibration_mode)
                              == illumination_of(mode) == "reflection")
                filter_match = same_route and flags.get("filter") == filter_for(calibration_mode)
                if (mode == calibration_mode or (is_filter and filter_match)
                        or (not is_filter and (same_route or reflective))):
                    calibration = copy.deepcopy(sample)
                    break
        if flags.get("calibrate"):
            for event in calibration:
                yield event
                if event["event"] == "calibration_condition":
                    yield _event("instruction_required", instruction="device_trigger",
                                 request_id="demo-calibration-trigger",
                                 calibration_context=copy.deepcopy(event))
        # Match the common native ladder: a calibration outcome alone does not
        # mean the operator moved anything. A setup may have changed placement
        # even when it was later skipped; a pure driver message cannot do so.
        calibration_setup_requested = flags.get("calibrate") and any(
            (event.get("instruction") == "sensor_position"
             or ("calibration_condition" in event
                 and calibration_condition(event["calibration_condition"]) != "message"))
            for event in calibration)
        for event in setup:
            if event["event"] == "saved_readings":
                yield event
        if flags.get("calibrate") and any(event["event"] == "calibration_ready"
                                          and event["performed"] for event in calibration):
            calibrated.add(mode)
        if flags.get("stored_refresh"):
            yield self._stored_refresh(connected, flags)
        if flags.get("read_refresh"):
            yield _event("instruction_required", instruction="refresh_stimulus",
                         request_id="refresh-ready")
            # A declared synthetic frequency exercises scalar retention; this is
            # not a prediction of this persona's display or numerical settings.
            yield _event("refresh_frequency", quantity="refresh_frequency", unit="Hz",
                         native_method="read_refrate", native_code=0, value=59.94)
            yield _event("terminal", status="completed")
            return
        if flags.get("read_timing"):
            yield _event("presentation_required", phase="black", request_id="timing-black",
                         minimum_delay_ms=0, deadline_ms=2000,
                         native_clock_id="worker_monotonic", method_window_started_seconds=0.0)
            time.sleep(.6)
            started = time.monotonic()
            yield _event("presentation_required", phase="white", request_id="timing-white",
                         minimum_delay_ms=200, deadline_ms=2000,
                         native_clock_id="worker_monotonic",
                         method_window_started_seconds=started)
            marker = self._timing_marker
            if marker is None or not started + .2 <= marker <= started + 2.0:
                yield _event("error", stage="timing_transition", native_code=0)
                yield _event("terminal", status="failed")
                return
            # These declared values exercise the record shape, never a sensor,
            # clock correlation or optical model of the selected persona.
            yield _event("display_timing", native_code=0, unit="ms", native_method="meas_delay",
                         time_reference="white_change_call", display_update_delay_ms=37,
                         instrument_reaction_ms=-230, native_clock_id="worker_monotonic",
                         method_window_started_seconds=started,
                         marker_call_started_seconds=marker, marker_call_finished_seconds=marker,
                         method_window_finished_seconds=time.monotonic())
            yield _event("terminal", status="completed")
            return
        if not count:
            yield _event("terminal", status="completed")
            return

        if flags.get("read_xy"):
            yield _event("instruction_required", instruction="xy_sheet", request_id="sheet")
            # The shared Instrument._reply computes navigation from these
            # synthetic coordinates. Layout and patch count remain caller-owned.
            points = [(10.0, 20.0), (10.0 + flags["xy_steps"] - 1, 20.0),
                      (10.0, 20.0 + 5 * (flags["xy_passes"] - 1))]
            for index in range(flags.get("xy_locate", 0)):
                yield _event("instruction_required", instruction="xy_locate",
                             request_id=f"locate-{index}", location_index=index,
                             location_count=flags["xy_locate"])
                yield _event("xy_location", index=index, x=points[index][0], y=points[index][1])
            if flags.get("xy_locate"):
                yield _event("instruction_required", instruction="xy_navigate",
                             request_id="navigate")
        else:
            for event in setup:
                if (event["event"] in {"instruction_required", "xy_location"}
                        and event not in calibration):
                    if (event.get("request_id") == "sample-ready"
                            and not calibration_setup_requested):
                        continue
                    yield event

        if ("prog_trig" not in available
                and {"switch_trig", "user_switch_trig"} & set(available)
                and kind != "chart_patch"):
            # No actual switch is attached: an explicit demo action supplies it.
            yield _event("instruction_required", instruction="device_trigger",
                         request_id="demo-measurement-trigger")
        for index in range(count):
            patch = copy.deepcopy(route["patches"][index % len(route["patches"])])
            self._samples.apply(patch, mode, self._chart_sample)
            patch["event"] = kind
            if kind == "result":
                patch.pop("index", None)
                patch.pop("patch_count", None)
            else:
                patch.update(index=index, patch_count=count)
            if flags.get("xyz_only"):
                patch["backend_reported"]["spectrum"] = None
            chosen = flags.get("filter", FILTERS["unknown"])
            if chosen in MEASUREMENT_CONDITIONS:
                patch["backend_reported"]["mcond"] = MEASUREMENT_CONDITIONS[chosen]
            yield patch
        yield _event("terminal", status="completed")

    def _stored_refresh(self, connected, flags):
        # Only explicit overrides are modeled. Calibration replay does not
        # manufacture a calibrated rate that the frozen captures never contain.
        has_getter = "get_refresh_rate" in decode_facilities(connected["trigger_capabilities"])
        rate = flags.get("refresh_rate")
        # With nothing set, i1disp.c:2131, i1d3.c:3664, spyd2.c:3338 and
        # specbos.c:1938 answer inst_needs_cal. DTP92 asks the device, and its
        # no-data reply is inst_misread (dtp92.c:897-908); device memory is not
        # modeled, so that is its answer here.
        unset = (("undetermined", 0x0e0000) if self._name == "dtp92"
                 else ("needs_calibration", 0x110000))
        status, code = (("not_advertised", None) if not has_getter else
                        ("available", 0) if rate else
                        ("undetermined", 0x0e0000) if rate == 0 else unset)
        return _event("stored_refresh_rate", native_method="get_refr_rate", unit="Hz",
                      native_code=code, status=status,
                      value=rate if status == "available" else None)

    def receive_message(self, *, timeout):
        if self._closed:
            raise ProtocolError("Demo session is closed")
        if self._cancelled:
            self._events = iter((_event("error", stage="cancelled", native_code=0),
                                 _event("terminal", status="failed")))
            self._cancelled = False
        if self._pending is not None:
            raise ProtocolError("Demo instruction was not answered")
        # Synthetic data is ready immediately. Per-event pacing would spend the
        # acquisition deadline on invented exposure time for larger batches.
        message = next(self._events)
        if message["event"] in {"instruction_required", "presentation_required"}:
            self._pending = message
        if message["event"] == "terminal":
            self._terminal = message["status"]
        return message

    def send(self, command, value=""):
        if command == "cancel":
            self._cancelled = True
            self._pending = None
            return
        if self._pending is None:
            raise ProtocolError("Demo response has no pending instruction")
        if self._pending["event"] == "presentation_required":
            if command != "presented" or value != self._pending["request_id"]:
                raise ProtocolError("Demo presentation expects its exact typed request")
            if self._pending["phase"] == "white":
                self._timing_marker = time.monotonic()
        elif self._pending.get("instruction") == "xy_navigate":
            values = value.split(",")
            if command != "navigate" or len(values) != 8 or not all(
                    math.isfinite(float(item)) for item in values):
                raise ProtocolError("Demo sheet needs eight finite navigation values")
        elif command != "acknowledge":
            raise ProtocolError("Demo instruction expects acknowledge or cancel")
        self._pending = None

    def finish_messages(self, *, expected_returncode):
        expected = "completed" if expected_returncode == 0 else "failed"
        if self._terminal != expected:
            raise ProtocolError("Demo terminal status disagrees with the parser")

    def close(self):
        self._closed = True


class _DemoFrontend(InstrumentFrontend):
    def _descriptor(self):
        descriptor = super()._descriptor()
        persona = _data()["personas"][self._meter.persona]
        for entry in descriptor["modes"]:
            reason = ("Needs a colorimeter correction, and corrections are not modeled "
                      "in this demo" if entry["correction_required"]
                      else "Not modeled in this demo" if entry["mode"] not in persona["routes"]
                      else None)
            if reason is not None:
                entry.update(available=False, unavailable_reason=reason)
                for workflow in entry["workflows"]:
                    workflow.update(available=False, unavailable_reason=entry["unavailable_reason"])
            elif entry["stored"]:
                for workflow in entry["workflows"]:
                    if workflow["workflow"] == "calibrate":
                        workflow.update(available=False,
                                        unavailable_reason="Stored-data calibration is not modeled")
        for option in descriptor["options"]:
            option["preview_effect"] = "Configuration only; synthetic values/grid are unchanged"
            if option["id"] == "display_type":
                option["preview_effect"] = (
                    "Configuration only; recorded default calibration prompts and "
                    "synthetic values/grid are unchanged")
            if option["id"] == "correction" and option["available"]:
                option.update(available=False, unavailable_reason="CCMX/CCSS correction transforms "
                              "are not modeled in this demo; no corrected result is claimed",
                              refusal_stage="demo_correction_unmodeled")
                for state in option["by_mode"].values():
                    state.update(available=False, unavailable_reason=option["unavailable_reason"])
        recorded = sorted(persona["misplaced"])
        descriptor["options"].append({
            "id": _MISPLACED, "type": "boolean", "default": False,
            "label": "Simulate the sensor, dial, diffuser or adapter left in the wrong position",
            "available": bool(recorded),
            "unavailable_reason": None if recorded else
            "No run with this instrument's sensor in the wrong position was recorded",
            "refusal_stage": "demo_misplaced_unmodeled",
            "applies_to": recorded or None,
            "preview_effect": "Replays a run recorded with the substituted instrument's "
                              "sensor in the wrong place: the wrong-position prompt, then "
                              "the reading once it is moved",
        })
        spot_modes = [entry["mode"] for entry in descriptor["modes"]
                      if any(workflow["workflow"] == "spot" and workflow["available"]
                             for workflow in entry["workflows"])]
        descriptor["options"].append({
            "id": _CHART_SAMPLE, "type": "object", "default": None,
            "label": "Synthetic guided sample context", "available": bool(spot_modes),
            "unavailable_reason": None if spot_modes else "No modeled spot workflow",
            "refusal_stage": "demo_chart_sample_unmodeled", "applies_to": spot_modes or None,
            "preview_effect": "Per-reading context supplied by the guided workflow: "
                              "cell_index (1–140) and attempt (1–2^53−1) select a repeatable "
                              "illustrative profile and bounded brightness gain; "
                              "not chart reference data or an instrument noise model",
        })
        for option in descriptor["options"][-2:]:
            option["by_mode"] = {}
            for entry in descriptor["modes"]:
                available = entry["available"] and entry["mode"] in (option["applies_to"] or [])
                option["by_mode"][entry["mode"]] = dict(
                    available=available,
                    unavailable_reason=None if available else option["unavailable_reason"]
                    or "This simulation option is not modeled in the selected mode",
                    capability_source="Packaged synthetic persona policy", choices=[],
                )
        descriptor["accepts_correction"] = False
        descriptor["capability_source"] = (
            "Packaged synthetic personas from pinned Argyll 3.5.0 source checks; "
            "preview routes are bounded to modeled workflows, not hardware support")
        descriptor["instructions"].append({
            "instruction": "device_trigger", "kind": "simulate_device_button", "answered": True,
            "carries_calibration": False, "detail": "Simulate pressing the instrument button"})
        descriptor["preview"] = {
            "persona": self._meter.persona, "label": f"{self._identity.model} (simulated)",
            "modeled": ["Source-backed capability differences", "Sampled calibration conditions",
                        "Placement and simulated device-button prompts",
                        "A calibration kept for later readings of its mode, where its "
                        "driver keeps one; what the driver still asks is asked again",
                        "A recorded wrong-position prompt, where one was recorded",
                        "Variable patch IDs/counts",
                        "Advertised setting forms, validation and synthetic configuration",
                        "Reflective condition labels", "Strip order and synthetic chart geometry",
                        "Repeated operations, cancellation and retained results",
                        "Explicit guided spot cell/attempt with a repeatable illustrative target",
                        "Synthetic refresh-frequency scalar on advertised measuring routes",
                        "Controlled timing exchange with declared synthetic delay/reaction"],
            "unmodeled": list(_UNMODELED),
            "data": "Synthetic pattern values and wavelength grids do not predict physical "
                    "measurements or numerical setting effects. "
                    "Twelve shared demo shapes cycle across free readings; explicit guided "
                    "cell/attempt context selects a repeatable shape and gain. "
                    "Brightness jitter is bounded +/-0.5%, not an instrument noise model "
                    "or chart reference data.",
            "example_layouts": {name: route["example_layout"]
                                for name, route in persona["routes"].items()},
        }
        return descriptor

    def _validate_request(self, text):
        # The demo's own option never reaches Configuration, which knows only the
        # ladder's: it is taken out, checked here, and kept in the request.
        demo_options, text = _take_demo_options(text)
        request, snapshot, configuration = super()._validate_request(text)
        misplaced = demo_options.get(_MISPLACED)
        if misplaced is not None:
            recorded = _data()["personas"][self._meter.persona]["misplaced"]
            # applies_to names modes, so the option holds for each of their
            # workflows: the reading, and calibration on its own.
            if misplaced and (request["mode"] not in recorded
                              or request["workflow"] not in {"spot", "calibrate"}):
                raise ValueError("No run with the sensor in the wrong position was recorded "
                                 "for this mode")
        if demo_options.get(_CHART_SAMPLE) is not None and (
                request["workflow"] != "spot" or len(request["patch_ids"]) != 1):
            raise ValueError("demo_chart_sample requires a one-reading spot request")
        request["options"].update(demo_options)
        for option in snapshot["options"]:
            value = request["options"].get(option["id"], option["default"])
            if value != option["default"] and not option["available"]:
                raise ValueError(option["unavailable_reason"])
            if option["id"] == "filter" and value is not None and value not in option["choices"]:
                raise ValueError("This reflective filter is not modeled by the persona")
        return request, snapshot, configuration


    def prepare(self, request_json, **kwargs):
        """Freeze per-request Demo options on the non-acquiring operation meter."""
        demo_options, _ = _take_demo_options(request_json)
        operation = InstrumentFrontend.prepare(self, request_json, **kwargs)
        if (demo_options.get(_MISPLACED) is True
                or demo_options.get(_CHART_SAMPLE) is not None):
            operation._meter = replace(operation._meter,
                                       misplaced=demo_options.get(_MISPLACED, False),
                                       chart_sample=demo_options.get(_CHART_SAMPLE))
        return operation


def _take_demo_options(text):
    """Validate Demo-only context before removing it from native configuration."""
    request = _load_request(text)
    if not (isinstance(request, dict) and isinstance(request.get("options"), dict)):
        return {}, text
    options = {key: request["options"].pop(key) for key in (_MISPLACED, _CHART_SAMPLE)
               if key in request["options"]}
    if _MISPLACED in options and type(options[_MISPLACED]) is not bool:
        raise ValueError(f"{_MISPLACED} is true or false")
    sample = options.get(_CHART_SAMPLE)
    if sample is not None:
        if (not isinstance(sample, dict) or sample.keys() != {"cell_index", "attempt"}
                or any(type(value) is not int or value < 1 for value in sample.values())
                or sample["cell_index"] > 140 or sample["attempt"] > _MAX_DEMO_ATTEMPT):
            raise ValueError("demo_chart_sample needs integer cell_index (1–140) "
                             "and integer attempt (1–2^53−1)")
    return options, json.dumps(request, allow_nan=False) if options else text


def is_demo_frontend(frontend) -> bool:
    """Identify packaged Demo I/O, rather than trusting a provenance label."""
    return type(frontend) is _DemoFrontend and type(frontend._meter) is _DemoInstrument


def demo_frontends() -> list[InstrumentFrontend]:
    """Isolated, reusable equipment previews; creates no process or connection."""
    result = []
    for name, persona in _data()["personas"].items():
        meter = _DemoInstrument(helper=Path("unused-demo-transport"), instrument=persona["model"],
                                expected_serial=f"DEMO-{name}", persona=name)
        front = _DemoFrontend(meter, **persona["capabilities"], provenance="simulated",
                              baseline_mode_checks=persona["baseline_mode_checks"],
                              strip_policies=persona["strip_policies"],
                              display_types=persona["display_types"],
                              ambient_matrix_threshold=persona["ambient_matrix_threshold"],
                              device_id=f"demo:{name}")
        front._identity = replace(front._identity, backend="argyll-demo")
        result.append(front)
    return result


def demo_catalog_json() -> str:
    """Equipment names, picker grouping and explicit synthetic preview scope."""
    catalog = []
    for front in demo_frontends():
        model = front._identity.model
        brand, display_model = _picker_names(model)
        catalog.append({"persona": front._meter.persona,
                        "device_id": front._identity.device_id,
                        "model": model, "brand": brand, "display_model": display_model,
                        "label": f"{model} (simulated)",
                        "synthetic": True, "live": False,
                        "preview": json.loads(front.describe_json())["preview"]})
    return json.dumps(catalog)


def _picker_names(model: str) -> tuple[str, str]:
    """Split the canonical Argyll name without changing its binding identity."""
    # The canonical name omits the manufacturer; retain the full product name
    # for model search and group it with X-Rite's other ColorMunki instruments.
    if model == "ColorMunki Smile":
        return "X-Rite", model
    # insttypes.c names HCFR by instrument type first; hcfr.c identifies the
    # project. Keep the canonical spelling in model and use a readable picker name.
    if model == "Colorimtre HCFR":
        return "HCFR", "Colorimeter"
    for brand in ("Colorimetry Research", "Photo Research", "Image Engineering"):
        if model.startswith(brand + " "):
            return brand, model[len(brand) + 1:]
    brand, _, display_model = model.partition(" ")
    # Published manufacturer associations make both the historical and later
    # names searchable. The canonical model keeps its original spelling.
    brand = {"GretagMacbeth": "X-Rite (GretagMacbeth)",
             "SwatchMate": "Palette (SwatchMate)"}.get(brand, brand)
    return brand, display_model
