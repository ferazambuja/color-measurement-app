"""Emit generic-frontend fixtures from the substituted instruments.

Produced by running the SUBSTITUTED workers only -- no hardware, no real helper,
nothing named *-never-run. Python demo and regression clients pin these original
observations rather than hand-written measurement JSON, so contract changes remain explicit in the
retained descriptors and captures.

Regenerate with:
    uv run python tools/generate_frontend_fixtures.py --build <native-check-output>
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import re
import shlex
import sys
import tempfile
import time
from dataclasses import replace
from functools import cache
from pathlib import Path

from test_all import authenticate_stub

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from color_measurement.argyll_capability import quantity_contract, workflow_kind  # noqa: E402
from color_measurement.argyll_description import NativeDescriptionClient  # noqa: E402
from color_measurement.discovery import InstrumentBuild  # noqa: E402
from color_measurement.instrument import (  # noqa: E402
    Instrument,
    InstrumentError,
    _cr_spectrum_wavelengths,
)
from color_measurement.instrument_frontend import (  # noqa: E402
    InstrumentFrontend,
    capability_identity,
)
from color_measurement.json_values import unique_object  # noqa: E402
from color_measurement.native_controls import NativeControls  # noqa: E402
from color_measurement.native_protocol import (  # noqa: E402
    NATIVE_CR_SPECTRUM_MAX_SAMPLES,
    finite,
    invalid_constant,
)
from color_measurement.native_receipt import native_worker_name  # noqa: E402

OUT = ROOT / "tests/fixtures/generic_frontend"
DISCOVERY_OUT = ROOT / "tests/fixtures/discovery"
_WRITTEN_CAPTURES: set[Path] = set()

# What a "Refresh devices" action returns, recorded from the substituted
# description worker. The default scenario enumerates TWO units of the SAME model
# at different paths, which is the case a model name cannot identify.
ENUMERATIONS = (
    ("two_of_one_model", "success",
     "Two units, same model, different paths: what a device picker must tell apart"),
    ("same_path_twice", "duplicate_path",
     "Two candidates at one path, which is the enumeration being wrong rather than "
     "two instruments"),
    ("nothing_attached", "no_candidates",
     "An empty list is an answer, not a failure: the helper ran and found nothing"),
    ("enumeration_failed", "enumeration_failure",
     "The enumeration itself failed; no list is produced and none is invented"),
    ("discloses_led_flash", "i1d3_flash",
     "Listed, identified, and NOT attachable: opening it flashes the LED (i1d3.c:3079)"),
    ("discloses_led_flash_colorhug", "colorhug_flash",
     "As above for colorhug.c:653-666"),
    ("discloses_led_flash_huey", "huey_flash",
     "As above for huey.c:1145-1168, an eight-step sequence with 50 ms sleeps"),
    ("refuses_firmware_download", "spyder1",
     "Listed, identified, and never opened: its init_inst downloads firmware "
     "(spyd2.c:2998-3002), which no acknowledgement authorizes"),
)

# Each persona is a substituted instrument the gate already drives. The probe mode
# is only used to read back the three capability words; nothing else depends on it.
# The i1Pro family, ColorMunki and SpectroScan record their routes from a fresh
# start (chart_stub.c `startup`): their drivers calibrate before the first
# reading, so the published routes -- and the demo -- do too.
# The default i1_display_3 substitute represents the non-munkdisp variant in
# i1d3.c:3922-3926. Argyll's combined instrument name does not make this a
# refresh-capable model of the ColorMunki Display variant.
PERSONAS = (
    ("i1_display_3", "generic-session-stub-worker", "X-Rite i1 DisplayPro, ColorMunki Display",
     "CM-COLORIMETER-0001", {"CM_COLORIMETER_FIXTURE": "success"}, "emissive_spot", False),
    ("dtp41", "generic-chart-stub-worker", "X-Rite DTP41", "CM-CHART-0001",
     {"CM_CHART_PERSONA": "dtp41", "CM_CHART_FIXTURE": "success"}, "reflective_spot", True),
    ("spectroscan", "generic-chart-stub-worker", "GretagMacbeth SpectroScan", "CM-CHART-0001",
     {"CM_CHART_PERSONA": "spectroscan", "CM_CHART_FIXTURE": "startup",
      # The vectors `navigation_vectors` derives from the three points this stub
      # sights, so read_xy aborts on a wrong derivation rather than accepting one.
      "CM_CHART_XY_VECTORS": "10,20,2,4,0,0,0.5,1"},
     "reflective_spot", True),
    ("dtp20", "generic-chart-stub-worker", "X-Rite DTP20", "CM-CHART-0001",
     {"CM_CHART_PERSONA": "dtp20", "CM_CHART_FIXTURE": "success"}, "reflective_spot", True),
    ("dtp51", "generic-chart-stub-worker", "X-Rite DTP51", "CM-CHART-0001",
     {"CM_CHART_PERSONA": "dtp51", "CM_CHART_FIXTURE": "success"}, "reflective_spot", True),
    ("colormunki", "generic-chart-stub-worker", "X-Rite ColorMunki", "CM-CHART-0001",
     {"CM_CHART_PERSONA": "munki", "CM_CHART_FIXTURE": "startup"}, "reflective_spot", True),
    # The instrument the PER-DEVICE helper was written for, driven by the generic
    # ladder. Published so a client can pin the same contract for it as for every
    # other instrument, rather than a second one for the hard-coded path.
    ("i1pro3", "generic-chart-stub-worker", "X-Rite i1 Pro 3", "CM-CHART-0001",
     {"CM_CHART_PERSONA": "i1pro3", "CM_CHART_FIXTURE": "startup"}, "reflective_spot", True),
    ("i1pro2", "generic-chart-stub-worker", "X-Rite i1 Pro 2", "CM-CHART-0001",
     {"CM_CHART_PERSONA": "i1pro2", "CM_CHART_FIXTURE": "startup"}, "reflective_spot", True),
    # The REAL cr250.c over a scripted link (cr250_persona.c), in a build that
    # composed the combined CR/PR series. Its observations carry native_facts_json.
    ("cr250", "generic-session-stub-worker", "Colorimetry Research CR-250", "SYNTHETIC-250",
     {"CM_COLORIMETER_FIXTURE": "cr250"}, "emissive_spot", False),
    # The CR-100 row of the same driver: an XYZ colorimeter.
    ("cr100", "generic-session-stub-worker", "Colorimetry Research CR-100", "SYNTHETIC-100",
     {"CM_COLORIMETER_FIXTURE": "cr100"}, "emissive_spot", False),
    ("cr300", "generic-session-stub-worker", "Colorimetry Research CR-300", "SYNTHETIC-300",
     {"CM_COLORIMETER_FIXTURE": "cr300"}, "emissive_spot", False),
    ("pr655", "generic-session-stub-worker", "Photo Research PR-655", "SYNTHETIC-P7",
     {"CM_COLORIMETER_FIXTURE": "pr655"}, "emissive_spot", True),
    ("pr670", "generic-session-stub-worker", "Photo Research PR-670", "SYNTHETIC-P7",
     {"CM_COLORIMETER_FIXTURE": "pr670"}, "emissive_spot", True),
    # The SpectroScan's transmission model, whose X-Y bits go once the mode is
    # transmission (ss.c:414-421).
    ("spectroscant", "generic-chart-stub-worker", "GretagMacbeth SpectroScanT",
     "CM-CHART-0001", {"CM_CHART_PERSONA": "spectroscant", "CM_CHART_FIXTURE": "startup",
                       "CM_CHART_XY_VECTORS": "10,20,2,4,0,0,0.5,1"},
     "reflective_spot", True),
)

# Every other constructible Argyll type, one row each of driver_personas.c's table:
# (persona, the name inst_enum matches, the serial its driver reports -- "" for
# none -- a mode it binds, whether it returns a spectrum). The serial is the table's; a row that
# disagreed would be refused as serial_mismatch. Spyder1 and Spyder2 have none:
# their initialization downloads firmware, so they appear only in discovery.
TABLE_PERSONAS = (
    ("i1_display_1", "GretagMacbeth i1 Display 1", "", "emissive_spot", False),
    ("i1_display_2", "GretagMacbeth i1 Display 2", "", "emissive_spot", False),
    ("colormunki_smile", "ColorMunki Smile", "", "emissive_spot", False),
    ("huey", "GretagMacbeth Huey", "1000001", "emissive_spot", False),
    ("colorhug", "Hughski ColorHug", "1000002", "emissive_spot", False),
    ("colorhug2", "Hughski ColorHug2", "1000003", "emissive_spot", False),
    ("hcfr", "Colorimtre HCFR", "", "emissive_spot", False),
    ("dtp92", "X-Rite DTP92", "", "emissive_spot", False),
    ("dtp94", "X-Rite DTP94", "", "emissive_spot", False),
    ("spyder3", "Datacolor Spyder3", "00100004", "emissive_spot", False),
    ("spyder4", "Datacolor Spyder4", "00100005", "emissive_spot", False),
    ("spyder5", "Datacolor Spyder5", "00100006", "emissive_spot", False),
    ("spyderx", "Datacolor SpyderX", "", "emissive_spot", False),
    ("spyderx2", "Datacolor SpyderX2", "", "emissive_spot", False),
    ("spyder2024", "GretagMacbeth Spyder 2024", "", "emissive_spot", False),
    ("klein_k10", "Klein K-10", "K10000007", "emissive_spot", False),
    ("specbos_1201", "JETI specbos 1201", "1000008", "emissive_tele", True),
    ("specbos", "JETI specbos", "1000009", "emissive_tele", True),
    ("specbos_2501", "JETI specbos 2501", "1000010", "emissive_tele", True),
    ("spectraval", "JETI spectraval", "1000011", "emissive_tele", True),
    ("ex1", "Image Engineering EX1", "1000012", "emissive_tele", True),
    ("smcube", "SwatchMate Cube", "", "reflective_spot", False),
    ("dtp22", "X-Rite DTP22", "", "reflective_spot", True),
    ("i1pro", "GretagMacbeth i1 Pro", "1000013d", "reflective_spot", True),
    ("i1monitor", "GretagMacbeth i1 Monitor", "1000014d", "emissive_spot", True),
    ("spectrolino", "GretagMacbeth Spectrolino", "1000015", "reflective_spot", True),
)
PERSONAS += tuple(
    # A driver reporting no serial is bound without a pin (session.c's
    # cm_expected_serial), as a real one must be.
    (persona, "generic-chart-stub-worker", instrument, serial or None,
     {"CM_CHART_PERSONA": persona}, probe, spectral)
    for persona, instrument, serial, probe, spectral in TABLE_PERSONAS)

# Failed CR-250 reads a client must render: the driver's own text in
# error_native_text and the attempt's facts in native_facts_json. A failed read
# may retain parsed originals as unverified observations without becoming success.
# (fixture, capture name, timeout): the budget refusal needs a timeout above the
# published floor that still cannot cover this unit's longer exposure ceiling.
#: Retained outcomes a client must handle, recorded here rather than as one-off
#: captures so they move with the contract (MATLAB replays each). Written as
#: <persona>/scenario.<name>.json beside that persona's describe.json, which is
#: their descriptor. (persona, scenario, environment, mode, workflow,
#: patches, layout, options, then the status, error stage and retained patch count the
#: consumer asserts -- the generator refuses a run that differs.)
SCENARIOS = (
    # A read that discovers a calibration requirement: recovered, failed, and a
    # repeated requirement that exhausts the single retry.
    ("dtp41", "late_calibration", {"CM_CHART_FIXTURE": "late_calibration"},
     "reflective_spot", "spot", 1, {}, {}, "completed", None, 1),
    ("dtp41", "late_calibration_failure", {"CM_CHART_FIXTURE": "late_calibration_failure"},
     "reflective_spot", "spot", 1, {}, {}, "failed", "calibrate", 0),
    ("dtp41", "repeated_calibration", {"CM_CHART_FIXTURE": "repeated_calibration"},
     "reflective_spot", "spot", 1, {}, {}, "failed", "read_sample", 0),
    # The repaired dtp92.c clears the read-reported offset need only after
    # successful calibration; the recovered read retains the updated assessment.
    ("dtp92", "late_calibration", {"CM_CHART_FIXTURE": "read_needs_calibration"},
     "emissive_spot", "spot", 1, {}, {}, "completed", None, 1),
    # Operation failures that retain what was read before them.
    ("dtp20", "nothing_stored", {"CM_CHART_FIXTURE": "nothing_stored"},
     "saved_spot", "spot", 1, {}, {}, "failed", "no_saved_readings", 0),
    ("spectroscan", "xy_release_failure", {"CM_CHART_FIXTURE": "xy_release_failure"},
     "reflective_xy", "xy", 6, {"passes": 2, "steps": 3}, {}, "failed", "xy_release", 6),
    ("spectroscan", "xy_restore_failure", {"CM_CHART_FIXTURE": "xy_restore_failure"},
     "reflective_spot", "spot", 1, {}, {}, "failed", "xy_restore", 1),
    # The sensor, dial or adapter where the step cannot work (chart_stub.c and
    # colorimeter_stub.c CM_SENSOR_POSITION): the driver names the position, the
    # operator moves it once and the step completes; left there, the read fails.
    ("colormunki", "wrong_dial", {"CM_SENSOR_POSITION": "read"},
     "emissive_tele", "spot", 1, {}, {}, "completed", None, 1),
    ("colormunki", "wrong_dial_calibration", {"CM_SENSOR_POSITION": "calibrate"},
     "transmission_spot", "spot", 1, {}, {}, "completed", None, 1),
    ("colormunki", "dial_left_wrong", {"CM_SENSOR_POSITION": "stuck"},
     "emissive_tele", "spot", 1, {}, {}, "failed", "read_sample", 0),
    ("i1_display_3", "diffuser_arm", {"CM_SENSOR_POSITION": "read"},
     "ambient_spot", "spot", 1, {}, {}, "completed", None, 1),
    ("i1pro3", "ambient_head", {"CM_SENSOR_POSITION": "read"},
     "ambient_spot", "spot", 1, {}, {}, "completed", None, 1),
    ("i1pro3", "off_tile_calibration", {"CM_SENSOR_POSITION": "calibrate"},
     "reflective_spot", "spot", 1, {}, {}, "completed", None, 1),
    # Generic vtable control: repositioning can be the only operator setup.
    # This does not predict a particular physical driver's calibration sequence.
    ("i1pro3", "calibration_position_only",
     {"CM_CHART_FIXTURE": "calibration_position_only"},
     "reflective_spot", "spot", 1, {}, {}, "completed", None, 1),
    # A pure driver message does not change placement or the sample stimulus.
    ("i1pro2", "calibration_message_only", {"CM_CHART_FIXTURE": "message_calibration"},
     "reflective_spot", "spot", 1, {}, {}, "completed", None, 1),
    ("i1pro3", "calibration_position_refused",
     {"CM_SENSOR_POSITION": "calibrate_stuck", "CM_CHART_FIXTURE": "startup"},
     "reflective_spot", "spot", 1, {}, {}, "failed", "calibrate", 0),
    # Off the tile while calibrating AND without the ambient head to read: each
    # step gets its own retry.
    ("i1pro3", "tile_then_ambient_head", {"CM_SENSOR_POSITION": "both"},
     "ambient_spot", "spot", 1, {}, {}, "completed", None, 1),
    # A reading whose driver restored a calibration still within its limits: the
    # at-open request waived (session.c cm_bind), nothing to calibrate, and the
    # record says the reading stands on what the driver retained.
    ("i1pro3", "retained_calibration", {"CM_CHART_FIXTURE": "retained_calibration"},
     "reflective_spot", "spot", 1, {}, {}, "completed", None, 1),
)
SCENARIOS += tuple(
    (persona, name, {"CM_COLORIMETER_FIXTURE": persona + fixture},
     "emissive_spot", workflow, count, layout, options, status, stage, retained)
    for persona in ("pr655", "pr670")
    for name, fixture, workflow, count, layout, options, status, stage, retained in (
        ("controls_read", "_controls_read", "native_controls", 0,
         {"action": "read"}, {}, "completed", None, 0),
        ("controls_apply", "_controls_apply", "native_controls", 0,
         {"action": "apply"}, {"native_controls": {"cycles": 3}}, "completed", None, 0),
        ("controls_apply_spot", "_control_capture", "spot", 1,
         {}, {"native_controls": {"cycles": 3}}, "completed", None, 1),
        ("geometry_refused", "_geometry_changed", "spot", 1,
         {}, {}, "failed", "read_sample", 1),
        ("late_d2_retained", "_d2_failure", "spot", 1,
         {}, {}, "failed", "read_sample", 1),
        ("xyz_only", "_xyz_only", "spot", 1,
         {}, {"xyz_only": True}, "completed", None, 1),
    )
)

# These consecutive operations share the same frontend's recovery owner while
# each dispatch starts a fresh actual-driver worker, as the application does.
PR_CONTROL_RECOVERY = (
    ("control_recovery_failed_apply", "_control_failed", "native_controls", 0,
     {"action": "apply"}, {"native_controls": {"cycles": 3}}, "failed", "native_controls", 0),
    ("control_recovery_read", "_controls_read", "native_controls", 0,
     {"action": "read"}, {}, "completed", None, 0),
    ("control_recovery_apply", "_controls_apply", "native_controls", 0,
     {"action": "apply"}, {"native_controls": {"cycles": 3}}, "completed", None, 0),
    ("control_recovery_spot", "", "spot", 1,
     {}, {}, "completed", None, 1),
)

CR250_FAILURES = (
    ("cr250_warning", "capture_warning", None),
    ("cr250_long_exposure", "budget_refused_before_m", 200.0),
    # A setting the driver wrote that the unit did not take: refused before M,
    # with the unconfirmed write in native_facts_json.
    ("cr250_setup", "setting_not_taken", None),
    # A spectrum its own instrument's totals contradict: refused after M, with the
    # unit check's numbers.
    ("cr250_spectrum_mismatch", "spectrum_mismatch", None),
)


# Representative calibration conditions for each operator action, raised by the
# persona whose driver owns them. These supplement the ordinary startup captures
# with conditions not every instrument needs at startup. DTP20's acknowledged
# remote white and DTP22's instrument-triggered white must remain distinct.
CALIBRATIONS = (
    ("i1pro3", "present_reference", "generic-chart-stub-worker", "X-Rite i1 Pro 3",
     "CM-CHART-0001", {"CM_CHART_PERSONA": "i1pro3",
                       "CM_CHART_FIXTURE": "white_calibration"}, "reflective_spot", True),
    # dtp20.c:1174-1187 requests acknowledgement, then issues the remote CR command.
    ("dtp20", "present_reference", "generic-chart-stub-worker", "X-Rite DTP20",
     "CM-CHART-0001", {"CM_CHART_PERSONA": "dtp20",
                       "CM_CHART_FIXTURE": "white_calibration"}, "reflective_spot", True),
    # dtp22.c:714-738 instead waits for its read switch; the table persona models it.
    ("dtp22", "click_reference", "generic-chart-stub-worker", "X-Rite DTP22",
     None, {"CM_CHART_PERSONA": "dtp22", "CM_CHART_FIXTURE": "success"},
     "reflective_spot", True),
    ("spectroscan", "change_filter", "generic-chart-stub-worker",
     "GretagMacbeth SpectroScan", "CM-CHART-0001",
     {"CM_CHART_PERSONA": "spectroscan",
      "CM_CHART_FIXTURE": "filter_after_calibration"}, "reflective_spot_m2", True),
    ("colormunki", "set_sensor_position", "generic-chart-stub-worker",
     "X-Rite ColorMunki", "CM-CHART-0001",
     {"CM_CHART_PERSONA": "munki",
      "CM_CHART_FIXTURE": "sensor_calibration"}, "reflective_spot", True),
    ("i1pro2", "message", "generic-chart-stub-worker", "X-Rite i1 Pro 2",
     "CM-CHART-0001", {"CM_CHART_PERSONA": "i1pro2",
                       "CM_CHART_FIXTURE": "message_calibration"}, "reflective_spot", True),
    ("dtp41", "operate_instrument", "generic-chart-stub-worker", "X-Rite DTP41",
     "CM-CHART-0001", {"CM_CHART_PERSONA": "dtp41",
                       "CM_CHART_FIXTURE": "uop_calibration"}, "reflective_spot", True),
    ("i1_display_3", "display_patch", "generic-session-stub-worker",
     "X-Rite i1 DisplayPro, ColorMunki Display", "CM-COLORIMETER-0001",
     {"CM_COLORIMETER_FIXTURE": "grey_ladder_calibration"}, "emissive_spot", False),
)


# Synthetic target operands. Each published request retains only the subset
# its native driver policy consumes; DTP51 ignores widths, while DTP20/41 use them.
_LAYOUTS = {
    "strip": {"strip_name": "STRIP", "pass_name": "B", "guide": 15,
              "patch_width_mm": 6.0, "gap_width_mm": 1.5, "trailer_width_mm": 3.0},
    # The chart the substituted DTP20 models (dtp20.c:440, mirrored in
    # chart_stub.c): twelve patches over four passes of three steps, as two
    # sheets, chart 7. A chart read is the one workflow where the geometry must
    # match the chart that is physically in the instrument -- sending a different
    # one is not a smaller chart, it is a chart the instrument is not holding.
    "chart": {"chart_id": 7, "passes": 4, "steps": 3, "sheets": [2, 2]},
    # The sheet the substituted SpectroScan models (ss.c, mirrored in
    # chart_stub.c): two passes of three steps, six patches, sighted from the
    # three fiducials `sheet_layout` calls for.
    "xy": {"passes": 2, "steps": 3},
}


def _write(path: Path, payload) -> None:
    _write_capture(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_capture(path: Path, text: str) -> None:
    _WRITTEN_CAPTURES.add(path)
    # Preserve original clocks and operation IDs, but publish a fresh capture
    # when its actual worker/report identity changes. Reproduction across
    # toolchains ignores those digests; current binding admission cannot.
    if path.is_file() and _per_run_blank(
        path.read_text(), preserve_build_identity=True
    ) == _per_run_blank(text, preserve_build_identity=True):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# Any binding reports the three capability words, so the probe only has to
# succeed -- not to be the right mode for the device. Trying a few in turn keeps
# this generator free of the per-device table the whole exercise removes.
_PROBES = ("reflective_spot", "emissive_spot", "reflective_strip", "transmission_spot",
           "ambient_spot", "saved_spot", "reflective_chart")


def _acquire(meter, mode, spectral, patches=4):
    """Drive whichever workflow this mode belongs to."""
    kind = workflow_kind(mode)
    if kind == "strip":
        return meter.measure_strip(mode, patch_count=patches, spectral=spectral,
                                   **_LAYOUTS["strip"])
    if kind == "chart":
        return meter.measure_chart(mode, patch_count=patches, spectral=spectral,
                                   passes=2, steps=2, sheets=(2,), chart_id=0)
    if kind == "xy":
        raise ValueError("X-Y is not probed")
    return meter.measure(mode, spectral=spectral)


def _catalog_row(build: Path, instrument: str) -> dict:
    """The build's own catalog row for ``instrument``: its floors and whether its
    driver publishes native facts, as bind() would read them."""
    from color_measurement.argyll_capability import CONTRIBUTED_INSTRUMENTS, INSTRUMENTS

    enum = next((symbol for table in (INSTRUMENTS, CONTRIBUTED_INSTRUMENTS)
                 for symbol, names in table.items() if instrument in names), None)
    rows = json.loads((build / "report.json").read_text())["driver_manifest"]["instrument_types"]
    return next((row for row in rows if row["enum"] == enum), {})


def _frontend(build: Path, worker, instrument, serial, environment, probe, spectral,
              timeout=None):
    # A driver with a floor gets a timeout it can actually accept -- twice the floor,
    # which bind() and prepare() would otherwise refuse -- as a real client must bind it.
    row = _catalog_row(build, instrument)
    floor = row.get("read_allowance_floor_s")
    if timeout is None:
        timeout = min(600.0, 2 * floor) if floor else 30.0
    if row.get("native_controls") is True:
        # These description fixtures construct the same production driver over
        # its existing scripted transport. Identity and control availability
        # therefore come through the ordinary authenticated describe/bind owner,
        # before any exposure, rather than being inferred from a model label.
        model = environment["CM_COLORIMETER_FIXTURE"][:5]
        owner = InstrumentBuild.from_report(
            build / "report.json", simulated=True, source_root=ROOT,
            scenario=f"{model}_native", timeout=timeout,
        )
        found, = owner.attached()
        description = owner.describe(
            found, expect=instrument, expected_serial=serial,
            accept_disclosed_initialization=True,
        )
        front = owner.bind(description, device_id=instrument, timeout=timeout)
        # The binding's immutable pins, receipt and identity stay intact. Only
        # the substituted session's declared scenario environment is selected.
        front._meter = replace(front._meter, environment=dict(os.environ, **environment))
        return front._meter, front
    meter = Instrument(helper=build / native_worker_name(worker), instrument=instrument,
                       expected_serial=serial,
                       timeout=timeout, environment=dict(os.environ, **environment),
                expected_helper_sha256=hashlib.sha256(
                    Path(build / native_worker_name(worker)).read_bytes()).hexdigest())
    connected = None
    for candidate in (probe, *_PROBES):
        for wants_spectrum in (spectral, not spectral):
            try:
                measurement = _acquire(meter, candidate, wants_spectrum)
            except InstrumentError as error:
                # A persona whose READ fails by design (a capture warning, a budget
                # refusal) still bound: its capability words are on `connected`.
                connected = next((e for e in error.events if e["event"] == "connected"), None)
                if connected is None:
                    continue
                break
            except Exception:  # noqa: BLE001 - probing for any mode that binds
                continue
            connected = next(e for e in measurement.events if e["event"] == "connected")
            break
        if connected is not None:
            break
    if connected is None:
        raise SystemExit(f"No mode bound on {instrument}; cannot read its capabilities")
    return meter, _bound_frontend(meter, connected, row, serial)


def _bound_frontend(meter, connected, row, serial, *, current_binding_identity=None,
                    binding_policy=None):
    """One constructor for freshly observed or retained native connection facts."""
    return InstrumentFrontend(
        meter,
        mode_capabilities=connected["mode_capabilities"],
        trigger_capabilities=connected["trigger_capabilities"],
        filter_capabilities=connected["filter_capabilities"],
        baseline_mode_checks=connected["baseline_mode_checks"],
        display_types=connected["display_types"],
        strip_policies=connected["strip_policies"],
        # The native policy the live path passes too (discovery.py): whether the
        # driver applies a correction to an ambient reading.
        ambient_ccmx=connected.get("ambient_ccmx", False),
        ambient_matrix_threshold=connected.get("ambient_matrix_threshold"),
        provenance="simulated", serial=serial,
        read_allowance_floor=row.get("read_allowance_floor_s"),
        read_allowance_floors={int(word): value for word, value
                               in row.get("read_allowance_floors_s", {}).items()} or None,
        native_facts=row.get("native_facts", False),
        native_controls_available=row.get("native_controls", False),
        current_binding_identity=current_binding_identity,
        binding_policy=binding_policy,
    )


def _validate_descriptor_identity(descriptor):
    content = {key: value for key, value in descriptor.items() if key != "capability_id"}
    if capability_identity(content) != descriptor.get("capability_id"):
        raise ValueError("Retained descriptor content identity differs")


def _require_driver_series(build):
    """Refuse fixture projection or capture without the published CR/PR contribution."""
    report = json.loads((build / "report.json").read_bytes())
    contribution = report.get("contribution")
    added = contribution.get("instrument_types_added") if isinstance(contribution, dict) else None
    if (not isinstance(added, list) or not all(isinstance(name, str) for name in added)
            or not {"instCR250", "instCR100", "instCR300", "instPR655", "instPR670"} <= set(added)):
        raise SystemExit("Regenerate from a build composed with the CR/PR series "
                         "(argyll_native_check.py --contribution ...)")
    return set(added)


def _refresh_descriptors(build, *, check=False):
    """Project current descriptor DTOs without starting any worker or changing captures."""
    build = build.resolve()
    _require_driver_series(build)
    for worker in ("generic-session-stub-worker", "generic-chart-stub-worker"):
        authenticate_stub(build / native_worker_name(worker))
    index = json.loads((OUT / "index.json").read_bytes())
    helper_hashes = {
        worker: hashlib.sha256((build / native_worker_name(worker)).read_bytes()).hexdigest()
        for worker in {row[1] for row in PERSONAS}}
    retained = {}
    refresh = []
    for name, worker, instrument, *_ in PERSONAS:
        path = OUT / name / "describe.json"
        if not path.is_file() or name not in index["personas"]:
            refresh.append((name, "missing persona descriptor or index entry"))
            continue
        original = json.loads(path.read_bytes())
        _validate_descriptor_identity(original)
        row = _catalog_row(build, instrument)
        identity = original.get("current_binding_identity")
        if row.get("native_facts") is True or identity is not None:
            receipt = identity.get("provenance") if isinstance(identity, dict) else None
            if (not isinstance(identity, dict)
                    or not isinstance(identity.get("native_identity"), dict)
                    or not isinstance(receipt, dict)
                    or not isinstance(receipt.get("description_helper_sha256"), str)
                    or len(receipt["description_helper_sha256"]) != 64
                    or receipt.get("session_helper_sha256") != helper_hashes[worker]):
                refresh.append((name, "missing native receipt or changed acquisition helper"))
                continue
        retained[name] = original
    if refresh:
        command = shlex.join([
            "uv", "run", "--locked", "--extra", "mat", "--extra", "app", "python",
            "tools/generate_frontend_fixtures.py", "--build", str(build),
            "--refresh-current-captures", "--personas", *(name for name, _ in refresh),
        ])
        details = "; ".join(f"{name}: {reason}" for name, reason in refresh)
        raise SystemExit("Descriptor projection cannot establish a current binding from "
                         f"retained facts ({details}). Run from the repository root:\n{command}")
    native_paths = sorted(path for path in OUT.rglob("*.json")
                          if path.name not in {"index.json", "describe.json"})
    native_hashes = {str(path.relative_to(OUT)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in native_paths}
    replacements = {}
    for name, worker, instrument, serial, environment, _, _ in PERSONAS:
        path = OUT / name / "describe.json"
        original = retained[name]
        if original["capability_id"] != index["personas"][name]["capability_id"]:
            raise ValueError(f"{name}: retained index and descriptor identities differ")
        connected = None
        native_fields = ("mode_capabilities", "trigger_capabilities", "filter_capabilities",
                         "baseline_mode_checks", "display_types")
        for capture_path in sorted((OUT / name).glob("*.json")):
            if capture_path.name in {"describe.json", "refusals.json", "calibration_retained.json"}:
                continue
            capture = json.loads(capture_path.read_bytes())
            for patch in capture.get("result", {}).get("patches", []):
                native = json.loads(patch["observation"]["raw_backend_output"])
                candidate = next((event for event in native["events"]
                                  if event["event"] == "connected"), None)
                if candidate is not None and all(candidate[field] == original[field]
                                                 for field in native_fields):
                    connected = candidate
                    break
            if connected is not None:
                break
        if connected is None:
            raise ValueError(f"{name}: no retained native connection facts; no worker will run")
        for field in native_fields:
            if connected[field] != original[field]:
                raise ValueError(f"{name}: retained native {field} differs from descriptor")
        row = _catalog_row(build, instrument)
        meter = Instrument(
            helper=build / native_worker_name(worker), instrument=instrument,
            expected_serial=serial,
            timeout=original["limits"]["binding_timeout_seconds"],
            environment=dict(os.environ, **environment),
            expected_helper_sha256=helper_hashes[worker],
        )
        current = json.loads(_bound_frontend(
            meter, connected, row, serial,
            current_binding_identity=original.get("current_binding_identity"),
            binding_policy=original["limits"]["binding_policy"],
        ).describe_json())
        replacements[path] = current
        index["personas"][name]["capability_id"] = current["capability_id"]
    replacements[OUT / "index.json"] = index
    differing = [str(path.relative_to(OUT)) for path, value in replacements.items()
                 if json.loads(path.read_bytes()) != value]
    if check and differing:
        raise ValueError("Retained descriptor projections differ: " + ", ".join(differing))
    if not check:
        for path, value in replacements.items():
            _write(path, value)
    if native_hashes != {str(path.relative_to(OUT)): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in native_paths}:
        raise ValueError("Native captures changed during descriptor projection")
    print(f"Current descriptor projections: {len(replacements) - 1} personas; "
          f"{len(native_hashes)} native capture files unchanged; no worker executed", flush=True)
    return 0


def _unit(found):
    """One row as a device picker would show it, derived values included."""
    return {
        "index": found.index, "name": found.name, "symbol": found.symbol,
        "native_type": found.native_type, "path": found.path,
        "transport": found.transport, "transports": list(found.transports),
        "identified": found.identified,
        "initialization_assessed": found.initialization_assessed,
        "initialization_refusal": found.initialization_refusal,
        "attachable": found.attachable, "opening_requires": found.opening_requires,
    }


def _capture_enumerations(build_dir: Path, catalog_json: str) -> list[dict]:
    """Record what a Refresh returns for each substituted enumeration.

    Nothing here opens an instrument except the one explicit `describe`, and the
    worker it runs is wholly substituted -- it links no upstream runtime.
    """
    worker = build_dir / native_worker_name("description-stub-worker")
    if not worker.is_file():
        raise SystemExit("Missing substituted worker: description-stub-worker")
    index = []
    session = build_dir / native_worker_name("generic-session-stub-worker")
    for name, scenario, note in ENUMERATIONS:
        client = NativeDescriptionClient.for_offline_tests(worker, scenario)
        build = InstrumentBuild(client, session,
                    expected_helper_sha256=hashlib.sha256(session.read_bytes()).hexdigest())
        capture = {"scenario": scenario, "note": note}
        try:
            found = build.attached(catalog_json=catalog_json)
        except Exception as error:  # noqa: BLE001 - a refusal IS the capture
            capture["refused"] = {"error": type(error).__name__, "message": str(error)}
            capture["units"] = None
        else:
            capture["units"] = [_unit(unit) for unit in found]
            capture["refused"] = None
            # Whether a caller may open each unit as it stands, and why not.
            capture["opening"] = [
                {"index": unit.index,
                 "permitted_without_acknowledgement": unit.attachable,
                 "requires": unit.opening_requires}
                for unit in found
            ]
        _write(DISCOVERY_OUT / f"{name}.json", capture)
        index.append({"capture": name, "scenario": scenario, "note": note,
                      "units": 0 if capture["units"] is None else len(capture["units"]),
                      "refused": capture["refused"] is not None})
        print(f"  discovery: {name} "
              f"({'refused' if capture['refused'] else str(len(capture['units'])) + ' unit(s)'})",
              flush=True)
    return index


def _capture_native_messages(build_dir: Path, catalog_json: str, *, selected=None) -> dict:
    """Retain the actual client envelopes, including nulls, arrays and events.

    Clients replay these bytes, rather than rebuilding the native protocol from
    the normalized picker captures. Variants originate in the substituted C
    transport; neither this generator nor MATLAB edits an envelope's identity.
    """
    rows = json.loads(catalog_json)["instrument_types"]
    cr250 = next(row["native_type"] for row in rows if row["enum"] == "instCR250")
    specifications = []
    for name, scenario, _ in ENUMERATIONS:
        specifications.append((name, scenario, None))
        if scenario in {"success", "i1d3_flash", "colorhug_flash", "huey_flash"}:
            specifications.extend((f"{name}_{variant}", f"{scenario}__{variant}", None)
                                  for variant in ("reordered", "shared_serial", "no_serial"))
    specifications.append(("typed_cr250", "cr250_typed__single", cr250))
    if selected is not None and not set(selected) <= {row[0] for row in specifications}:
        raise ValueError("Unknown native discovery case")
    worker = build_dir / native_worker_name("description-stub-worker")
    authenticate_stub(worker)
    if selected is None:
        for stale in (DISCOVERY_OUT / "native").rglob("*.json"):
            stale.unlink()
    cases = {}

    def capture(name, scenario, expected_type=None):
        if selected is not None and name not in selected:
            return
        client = NativeDescriptionClient.for_offline_tests(worker, scenario)
        folder = DISCOVERY_OUT / "native" / name
        folder.mkdir(parents=True, exist_ok=True)
        written = set()

        def retain(filename, text):
            path = folder / filename
            _write_capture(path, text + "\n")
            written.add(path)
            return path.relative_to(DISCOVERY_OUT).as_posix()

        listing = client.list_candidates_json()
        entry = {"scenario": scenario, "listing": retain("list.json", listing),
                 "descriptions": []}
        message = json.loads(listing)
        if message["status"] == "completed":
            for index, candidate in enumerate(message["data"]["candidates"], start=1):
                selection = json.dumps(candidate)
                options = {"expected_initialized_dtype": expected_type}
                item = {"path": candidate["path"]}
                reason = candidate["initialization_refusal"]
                if reason and reason != "initialization_downloads_pld":
                    item["without_acknowledgement"] = retain(
                        f"describe-{index}-unacknowledged.json",
                        client.describe_json(selection, **options))
                    options["accepted_initialization_reason"] = reason
                item["response"] = retain(f"describe-{index}.json",
                                          client.describe_json(selection, **options))
                entry["descriptions"].append(item)
        if expected_type is not None:
            entry["expected_initialized_dtype"] = expected_type
        cases[name] = entry
        for stale in folder.glob("*.json"):
            if stale not in written:
                stale.unlink()

    # Only those variants open distinct paths. Duplicate paths and firmware
    # refusals retain their real failures, never a fabricated description.
    for name, scenario, expected_type in specifications:
        capture(name, scenario, expected_type)
    if selected is not None and set(cases) != set(selected):
        raise ValueError("Unknown native discovery case")
    return cases


def _native_message_selection(selected):
    """Validate a requested scope before any mixed frontend generation writes."""
    index = json.loads((DISCOVERY_OUT / "index.json").read_bytes())
    selected = set(selected)
    if not selected or not selected <= set(index["native_messages"]):
        raise ValueError("Choose existing native discovery cases")
    return selected


def _refresh_native_messages(build: Path, selected):
    """Refresh selected genuine worker envelopes; retain every unrelated case."""
    build = build.resolve()
    _require_driver_series(build)
    selected = _native_message_selection(selected)
    index_path = DISCOVERY_OUT / "index.json"
    index = json.loads(index_path.read_bytes())
    catalog = (DISCOVERY_OUT / "catalog.json").read_text()
    index["native_messages"].update(_capture_native_messages(build, catalog, selected=selected))
    _write(index_path, index)
    print(f"Current native discovery: {len(selected)} cases; unrelated captures retained",
          flush=True)


def _calibration_retained(build, name, worker, instrument, serial, environment, probe,
                          spectral, snapshot, calibrated):
    """Each route that calibrated from a fresh start, read again from a start whose
    driver restored a calibration still within its limits. Written beside the
    persona's captures; returns the modes, or None when there is nothing to keep."""
    if not calibrated:
        return None
    if worker != "generic-chart-stub-worker":
        raise SystemExit(f"{name}: no retained start is modelled for {worker}")
    _, front = _frontend(build, worker, instrument, serial,
                         {**environment, "CM_CHART_FIXTURE": "retained_calibration"},
                         probe, spectral)
    if json.loads(front.describe_json())["capability_id"] != snapshot["capability_id"]:
        raise SystemExit(f"{name}: a retained start changed the descriptor")
    routes = {}
    for request in calibrated:
        result, _, _ = _drive(front, request)
        if result["status"] != "completed":
            raise SystemExit(f"{name}/{request['mode']}: a retained start did not complete: "
                             f"{result.get('error')}")
        routes[request["mode"]] = json.loads(
            result["patches"][0]["observation"]["raw_backend_output"])["events"]
    _write(OUT / name / "calibration_retained.json",
           {"fixture": "retained_calibration", "routes": routes})
    print(f"  {name}: calibration_retained, {len(routes)} route(s)", flush=True)
    return sorted(routes)


def _drive(front, request, seconds=20.0, *, cancel_instruction=False):
    descriptor = json.loads(front.describe_json())
    operation = front.prepare(json.dumps(request))
    events = []
    terminal_received = False

    def drain():
        nonlocal terminal_received
        while True:
            raw = operation.poll_event_json()
            if not raw:
                return
            event = json.loads(raw)
            events.append(event)
            if event["kind"] == "terminal":
                terminal_received = True
            # A calibration prompt must be answered or the capture is a timeout.
            # `instruction_notified` is NOT answered: the instrument triggers it.
            if event["kind"] == "instruction_required":
                operation.respond(operation.operation_id,
                                  event["payload"]["request_id"],
                                  "cancel" if cancel_instruction else "accept")
            elif event["kind"] == "presentation_required":
                payload = event["payload"]
                time.sleep(payload["minimum_delay_ms"] / 1000.0 + .03)
                operation.presented(operation.operation_id, payload["request_id"], json.dumps({
                    "phase": payload["phase"], "stimulus_id": payload["stimulus_id"],
                    "display_id": payload["display_id"], "clock_id": "fixture-client",
                    "presented_at_seconds": time.monotonic(), "source": "client_draw_completed",
                }))

    try:
        operation.dispatch()
        deadline = time.monotonic() + seconds
        while True:
            drain()
            # Before completion, a nonblocking poll can miss a busy staging lock.
            # After completion, the stable queue is read without that condition.
            # `done` proves publication, not delivery to this reader; require the
            # terminal event itself before retaining the capture.
            if operation.done and terminal_received:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Fixture capture did not receive its terminal event "
                                   "before the capture deadline")
            time.sleep(0.01)
        return json.loads(operation.result_json()), events, descriptor
    finally:
        operation.close()


def _capture_scenario(front, snapshot, persona, name, mode, workflow, count, layout,
                      options, status, stage, retained, scenarios):
    """Retain one shared frontend request, outcome and genuine worker events."""
    request = {"schema_version": 1, "device_id": snapshot["device_id"],
               "capability_id": snapshot["capability_id"], "mode": mode,
               "workflow": workflow, "patch_ids": [f"P{i}" for i in range(1, count + 1)],
               "options": options, "layout": layout,
               "event_contract": "patch_v1" if "patch_v1" in snapshot["event_contracts"]
                                 else "retained_patch_v1"}
    result, events, descriptor = _drive(front, request)
    if (result["status"], result.get("error_stage"), len(result["patches"])) != (
            status, stage, retained):
        raise SystemExit(f"{persona}/scenario.{name}: {result['status']} "
                         f"{result.get('error_stage')} with {len(result['patches'])} "
                         f"patches, not {status} {stage} with {retained}")
    _write(OUT / persona / f"scenario.{name}.json",
           {"descriptor": descriptor, "request": request,
            "result": result, "events": events})
    scenarios.append({"capture": f"{persona}/scenario.{name}", "status": status,
                      "error_stage": stage, "patches": retained})
    print(f"  {persona}: scenario.{name} {status}", flush=True)


def _capture_pr_control_recovery(build, persona, specification, scenarios):
    """Failed writes stay blocked across workers until an explicit complete apply."""
    worker, instrument, serial, environment, probe, spectral = specification
    _, front = _frontend(build, worker, instrument, serial, environment, probe, spectral)
    expected_pending = NativeControls.from_changes("pr655", {"cycles": 3}).present
    for (name, fixture, workflow, count, layout, options,
         status, stage, retained) in PR_CONTROL_RECOVERY:
        front._meter = replace(front._meter, environment={
            **os.environ, **environment, "CM_COLORIMETER_FIXTURE": persona + fixture})
        snapshot = json.loads(front.describe_json())
        _capture_scenario(front, snapshot, persona, name, "emissive_spot", workflow,
                          count, layout, options, status, stage, retained, scenarios)
        current = json.loads(front.describe_json())
        pending = current["native_controls"]["pending"]
        wanted = expected_pending if fixture in {"_control_failed", "_controls_read"} else 0
        if pending != wanted:
            raise SystemExit(f"{persona}/{name}: pending controls {pending}, not {wanted}")
        if fixture == "_controls_read":
            # Both requests are refused before dispatch, so the scripted worker
            # must not be opened to recover by a read or an empty application.
            for blocked_workflow, blocked_options, blocked_layout, ids in (
                ("spot", {}, {}, ["P1"]),
                ("native_controls", {"native_controls": {}}, {"action": "apply"}, []),
            ):
                request = dict(schema_version=1, device_id=current["device_id"],
                    capability_id=current["capability_id"], mode="emissive_spot",
                    workflow=blocked_workflow, patch_ids=ids, options=blocked_options,
                    layout=blocked_layout, event_contract="retained_patch_v1")
                try:
                    operation = front.prepare(json.dumps(request))
                except ValueError as error:
                    if "recovery requires an explicit apply" not in str(error):
                        raise SystemExit(
                            f"{persona}/{name}: wrong recovery refusal: {error}") from error
                else:
                    operation.close()
                    raise SystemExit(f"{persona}/{name}: unresolved controls admitted a request")


def _required_correction(snapshot, entry) -> dict:
    """A correction for a mode that cannot be read without one. SYNTHETIC, for a
    substituted instrument, and its DESCRIPTOR says so: twice the published
    threshold on the diagonal, which the driver types as the route asked for."""
    scale = 2 * entry["correction_matrix_diagonal_mean"]["above"]
    rows = "".join(" ".join(repr(scale if column == row else 0.0) for column in range(3))
                   + "\n" for row in range(3))
    return {"text": (
        'CCMX\n\nDESCRIPTOR "Synthetic illuminance-scaled matrix for a substituted '
        'instrument; not a measured correction"\nORIGINATOR "generate_frontend_fixtures.py"\n'
        f'INSTRUMENT "{snapshot["device"]["model"]}"\nDISPLAY "none"\n'
        'KEYWORD "COLOR_REP"\nCOLOR_REP "XYZ"\nNUMBER_OF_FIELDS 3\nBEGIN_DATA_FORMAT\n'
        "XYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\nNUMBER_OF_SETS 3\nBEGIN_DATA\n"
        f"{rows}END_DATA\n")}


def _refusals(front, snapshot):
    """One example per refusal class, as the frontend actually words them."""
    base = {"schema_version": 1, "device_id": snapshot["device_id"],
            "capability_id": snapshot["capability_id"], "mode": snapshot["modes"][0]["mode"],
            "workflow": snapshot["modes"][0]["workflow"], "patch_ids": ["A1"], "options": {},
            "layout": {}}
    cases = {
        "mode_not_offered": {**base, "mode": "reflective_xy_m3"},
        "workflow_not_offered": {**base, "workflow": "xy"},
        "too_many_patch_ids": {**base, "patch_ids": ["A1", "A2"]}
        if base["workflow"] == "spot" else {**base, "patch_ids": []},
        "unknown_option": {**base, "options": {"not_an_option": 1}},
        "stale_capability_id": {**base, "capability_id": "0" * 64},
        "unknown_field": {**base, "not_a_field": 1},
        # The key has to be one this workflow really does not take: `guide` IS a
        # strip key, so filing it here recorded refused=false for every strip
        # persona and mislabelled an ACCEPTED request as a refusal example.
        "layout_key_not_used_by_this_workflow": {
            **base, "layout": {"guide": 3} if base["workflow"] != "strip"
            else {"passes": 2}},
    }
    # Refused at start, before the instrument opens, in the words a client shows.
    needs = next((entry for entry in snapshot["modes"] if entry["correction_required"]), None)
    if needs is not None:
        cases["correction_required"] = {**base, "mode": needs["mode"],
                                        "workflow": needs["workflow"]}
    out = {}
    for name, request in cases.items():
        try:
            front.prepare(json.dumps(request)).close()
            out[name] = {"request": request, "refused": False}
        except ValueError as error:
            out[name] = {"request": request, "refused": True, "message": str(error)}
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path,
                        help="A native-check output directory holding the substituted workers")
    parser.add_argument("--check", action="store_true",
                        help="Regenerate into a temporary directory and require the committed "
                             "fixtures to match it, per-run fields aside; write nothing")
    parser.add_argument("--refresh-descriptors", action="store_true",
                        help="Reproject only descriptor DTOs/index from retained native facts; "
                             "start no workers and preserve original captures")
    parser.add_argument("--refresh-current-captures", action="store_true",
                        help="Regenerate current frontend requests/records with cached substituted "
                             "workers; preserve unchanged discovery and native calibration inputs")
    parser.add_argument("--personas", nargs="+", choices=sorted(row[0] for row in PERSONAS),
                        help="With --refresh-current-captures, regenerate only these personas")
    parser.add_argument("--native-discovery", nargs="+",
                        help="Refresh only these existing native discovery cases")
    args = parser.parse_args(argv)
    if args.build is None:
        parser.error("--build is required for descriptor projection or worker capture")
    if args.refresh_descriptors and args.refresh_current_captures:
        parser.error("Choose descriptor projection or current capture regeneration")
    if args.personas and not args.refresh_current_captures:
        parser.error("--personas requires --refresh-current-captures")
    if args.native_discovery and (args.check or args.refresh_descriptors):
        parser.error("Native discovery selection cannot use --check or descriptor projection")
    if args.native_discovery:
        _native_message_selection(args.native_discovery)
    if args.refresh_current_captures:
        if args.check:
            parser.error("Use the normal full fixture --check after current capture regeneration")
        result = _generate(args.build, refresh_current_captures=True, selected=args.personas)
        if args.native_discovery:
            _refresh_native_messages(args.build, args.native_discovery)
        return result
    if args.native_discovery:
        _refresh_native_messages(args.build, args.native_discovery)
        return 0
    if args.refresh_descriptors:
        return _refresh_descriptors(args.build, check=args.check)
    if args.check:
        return _check(args.build)
    return _generate(args.build)


#: What differs between two runs of the same workers and nothing else: the
#: identifiers each operation and observation mints, the clock, and the CR host
#: allowance counted on that clock -- including where the refusal prints it.
#: Each is blanked only in its own shape -- a UUID, an ISO timestamp, a JSON
#: number -- so a value that turns null, loses its ID or changes type still
#: differs. The value's quotes must match the key's (the backslash run a nesting
#: adds), so the same fields inside an embedded JSON string (raw_backend_output,
#: native_facts_json) are covered at any depth. A capability_id is never blanked:
#: it is the contract.
#:
#: The discovery envelopes also carry the worker's own digest (helper_sha256).
#: It varies between two builds of the same authenticated source on different
#: toolchains -- an Xcode update would fail the gate with nothing changed -- and
#: the source a worker came from is already proved by authenticate_stub. Chart
#: provenance repeats both worker digests and the build report's, whose absolute
#: build paths differ for every build, so no fresh build could reproduce them.
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_TIME = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
_NUMBER = r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w.])"
_PER_RUN = re.compile(
    r'(\\*")(?:(operation_id|observation_id)\1:\s*\1' + _UUID + r"\1"
    r"|(acquired_at)\1:\s*\1" + _TIME + r"\1"
    r"|(host_allowance_s|remaining_s|deadline_ms|presented_at_seconds|method_window_started_seconds"
    r"|marker_call_started_seconds|marker_call_finished_seconds|method_window_finished_seconds)"
    r"\1:\s*" + _NUMBER +
    r"|(helper_sha256|description_helper_sha256|session_helper_sha256|build_report_sha256)"
    r"\1:\s*\1[0-9a-f]{64}\1)")
_REMAINING = re.compile(r"Read allowance remaining \d+(?:\.\d+)? s")

# cr250.c/pr655.c own these admission thresholds; the reproduction regression
# checks their actual #defines. This tool does not change native admission.
_UNIT_RATIO_LOW, _UNIT_RATIO_HIGH = 0.9, 1.1
_CR_UNIT_CHROMA, _CR_UNIT_MIN_Y = 0.05, 0.2
_CR_UNIT_AXIS_NM = 5.0
_PR_CHECK_Y = 2  # pr655.h: PR655_CHECK_Y


def _per_run_blank(text: str, *, preserve_build_identity: bool = False) -> str:
    def blank(match):
        name = match.group(2) or match.group(3) or match.group(4) or match.group(5)
        if preserve_build_identity and match.group(5):
            return match.group(0)
        return f"{match.group(1)}{name}{match.group(1)}:X"
    return _REMAINING.sub("Read allowance remaining X s", _PER_RUN.sub(blank, text))


def _float_roundoff_matches(before, after) -> bool:
    return (type(before) is float and type(after) is float
            and math.isfinite(before) and math.isfinite(after)
            and before != 0 and after != 0
            and math.copysign(1, before) == math.copysign(1, after)
            and abs(before - after) <= 4 * max(math.ulp(before), math.ulp(after)))


@cache
def _synthetic_profiles() -> tuple:
    """Read the one authoritative illustrative Gaussian table, never execute C."""
    source = (ROOT / "tools/native_argyll/chart_stub.c").read_text()
    declaration = re.search(r"static const double shapes\[12\]\[3\]\[3\] = (\{.*?\n\});",
                            source, re.S)
    if (declaration is None or "exp(-0.5 * pow((nm - shapes[k][c][0]) / shapes[k][c][1], 2))"
            not in source or "emitted ? 1000.0 * s :" not in source):
        return ()
    try:
        literal = re.sub(r"/\*.*?\*/", "", declaration[1], flags=re.S)
        profiles = ast.literal_eval(literal.replace("{", "[").replace("}", "]"))
        result = tuple(tuple(tuple(finite(value) for value in component)
                             for component in profile) for profile in profiles)
        if len(result) != 12 or any(not 1 <= len(profile) <= 3 for profile in result):
            return ()
        if any(len(component) != 3 or component[1] <= 0 or component[2] <= 0
               for profile in result for component in profile):
            return ()
        return result
    except (ValueError, TypeError, SyntaxError):
        return ()


def _four_ulp_interval(value: float) -> tuple[float, float]:
    lower = upper = value
    for _ in range(4):
        lower, upper = math.nextafter(lower, -math.inf), math.nextafter(upper, math.inf)
    return lower, upper


def _synthetic_emitted_interval(profile, nm: float) -> tuple[float, float]:
    """Propagate this synthetic reproduction policy through chart_stub's exp.

    Four argument ULPs and four exp-result ULPs are an explicit software budget,
    not a universal libm guarantee or instrument uncertainty. Each positive
    component, source-order sum and final W-to-mW scaling rounds outward once.
    chart_stub.c emits 1000*sum(A*exp(z)); observation_from_patch retains those
    samples verbatim, attaching quantity_contract's mtype-1 or mtype-2 units.
    """
    lower = upper = 0.0
    for centre, width, amplitude in profile:
        lo, hi = _four_ulp_interval(-0.5 * math.pow((nm - centre) / width, 2))
        lo = max(0.0, _four_ulp_interval(math.exp(lo))[0])
        hi = _four_ulp_interval(math.exp(hi))[1]
        lo = max(0.0, math.nextafter(amplitude * lo, -math.inf))
        hi = math.nextafter(amplitude * hi, math.inf)
        lower = max(0.0, math.nextafter(lower + lo, -math.inf))
        upper = math.nextafter(upper + hi, math.inf)
    return (max(0.0, math.nextafter(1000.0 * lower, -math.inf)),
            math.nextafter(1000.0 * upper, math.inf))


def _synthetic_emitted_matches(before: dict, after: dict) -> bool:
    """Both complete emitted vectors must fit the same source profile and grid."""
    try:
        spectra = before.get("spectrum"), after.get("spectrum")
        if not all(isinstance(spectrum, dict) for spectrum in spectra):
            return False
        vectors, grids = [], []
        native = "mtype" in before or "mtype" in after
        for packet, spectrum in zip((before, after), spectra):
            if native:
                if (type(packet.get("mtype")) is not int or packet["mtype"] not in {1, 2}
                        or packet["mtype"] != before.get("mtype")
                        or finite(spectrum.get("norm")) != 1):
                    return False
                values, count = spectrum.get("samples"), spectrum.get("spec_n")
                if type(count) is not int or not 2 <= count <= 601:
                    return False
                start, end = (finite(spectrum.get(key)) for key in (
                    "wavelength_start_nm", "wavelength_end_nm"))
                step = (end - start) / (count - 1)
                grid = tuple(start + index * step for index in range(count))
            else:
                if not any((spectrum.get("quantity"), spectrum.get("unit")) == (
                        quantity_contract(mtype)["quantity"],
                        quantity_contract(mtype)["spectrum_unit"]) for mtype in (1, 2)):
                    return False
                values = spectrum.get("values")
                grid = tuple(finite(value) for value in spectrum.get("wavelengths_nm", ()))
            if not isinstance(values, list) or not 2 <= len(values) == len(grid) <= 601:
                return False
            # Integer-nm uniform grids keep chart_stub's internal grid arithmetic
            # exact. This exception does not assign a grid-coordinate tolerance.
            step = grid[1] - grid[0]
            if (step <= 0 or any(not value.is_integer() or not 0 < value <= 2**53
                                 or value != grid[0] + index * step
                                 for index, value in enumerate(grid))):
                return False
            vectors.append(values)
            grids.append(grid)
        if grids[0] != grids[1]:
            return False
        needs_interval = False
        for left, right in zip(*vectors):
            if type(left) is not type(right) or type(left) not in (int, float):
                return False
            a, b = finite(left), finite(right)
            if a < 0 or b < 0 or (a == 0 or b == 0) and (
                    a != b or math.copysign(1, a) != math.copysign(1, b)):
                return False
            if left != right:
                if type(left) is int:
                    return False
                needs_interval |= not _float_roundoff_matches(left, right)
        if not needs_interval:
            return False
        for profile in _synthetic_profiles():
            for nm, left, right in zip(grids[0], *vectors):
                lower, upper = _synthetic_emitted_interval(profile, nm)
                if (not math.isfinite(lower) or not math.isfinite(upper)
                        or not lower <= left <= upper or not lower <= right <= upper):
                    break
            else:
                return True
        return False
    except (ValueError, TypeError, OverflowError):
        return False


def _has_luminance_check(unit: dict) -> bool:
    discriminating = unit.get("discriminating")
    if isinstance(discriminating, dict):
        return (type(unit.get("verdict")) is str and unit["verdict"] in {"consistent", "mismatch"}
                and discriminating.get("luminance") is True)
    return (type(unit.get("verdict")) is int and unit["verdict"] in {1, 3}
            and type(discriminating) is int and bool(discriminating & _PR_CHECK_Y)
            and type(unit.get("computed")) is int and bool(unit["computed"] & _PR_CHECK_Y))


def _cr_chroma_reproduces(capture: dict) -> bool:
    """Validate the diagnostic against its own inputs, in cr250.c's order.

    cr250.c:603-611 quarters each term, left-associates the native total, then
    divides max(|derived X/4 - native X/4|, |derived Z/4 - native Z/4|) by it.
    Cancellation makes ULPs of two different captures' near-zero results a poor
    error bound. Four result ULPs here compare each result to its own calculation;
    the ordinary paired XYZ comparison still bounds changes to its inputs.
    """
    unit = capture.get("unit_check")
    if (not isinstance(unit, dict) or not _has_luminance_check(unit)
            or not isinstance(unit.get("discriminating"), dict)
            or unit["discriminating"].get("chromaticity") is not True):
        return False
    vectors = capture.get("xyz"), unit.get("xyz")
    if any(not isinstance(vector, list) or len(vector) != 3 for vector in vectors):
        return False
    try:
        native, derived = (tuple(finite(value) for value in vector) for vector in vectors)
    except ValueError:
        return False
    if native[1] < _CR_UNIT_MIN_Y:
        return False
    total = native[0] / 4 + native[1] / 4 + native[2] / 4
    if not math.isfinite(total) or total <= 0:
        return False
    expected = max(abs(derived[0] / 4 - native[0] / 4),
                   abs(derived[2] / 4 - native[2] / 4)) / total
    return _float_roundoff_matches(unit.get("chroma_error"), expected)


def _cr_spectrum_mean_matches(captures) -> bool:
    """Bound moment/sum roundoff while preserving cr250.c's axis decision.

    cr250.c:530-568 sums samples and their wavelength-weighted moment. The final
    quotient can differ by one ULP with contracted multiply/add. Raw capture
    events retain the computed context and grid, but their sample chunks are
    separate events. Admit only four result ULPs here, without rejoining them.
    """
    values, decisions = [], []
    for capture in captures:
        unit, spectrum = capture.get("unit_check"), capture.get("spectrum")
        if not isinstance(unit, dict) or not isinstance(spectrum, dict):
            return False
        verdict, discriminating = unit.get("verdict"), unit.get("discriminating")
        if (type(verdict) is not str or verdict not in {"consistent", "inconclusive", "mismatch"}
                or not isinstance(discriminating, dict)
                or set(discriminating) != {"power", "axis", "luminance", "chromaticity"}
                or any(type(value) is not bool for value in discriminating.values())
                or type(spectrum.get("count")) is not int
                or not 2 <= spectrum["count"] <= NATIVE_CR_SPECTRUM_MAX_SAMPLES
                or not isinstance(unit.get("xyz"), list) or len(unit["xyz"]) != 3):
            return False
        try:
            grid = _cr_spectrum_wavelengths(spectrum)
            if (any(not math.isfinite(value) for value in grid)
                    or any(right <= left for left, right in zip(grid, grid[1:]))
                    or finite(unit.get("spectrum_w")) <= 0):
                return False
            for value in unit["xyz"]:
                finite(value)
            value = unit.get("spectrum_mean_nm")
            reference = finite(unit.get("mean_nm")) if discriminating["axis"] else None
            if reference is not None and reference <= 0:
                return False
        except ValueError:
            return False
        values.append(value)
        decisions.append(abs(value - reference) <= _CR_UNIT_AXIS_NM
                         if reference is not None and type(value) is float else None)
    return _float_roundoff_matches(*values) and decisions[0] == decisions[1]


def _fixture_difference(before, after, field: str = "", path: str = "$", *,
                        allow_roundoff: bool = False, capture_pair=None,
                        synthetic_context: bool = False,
                        emitted_values_match: bool = False) -> tuple | None:
    """Return the first differing field without changing captured observations.

    Native synthetic spectra use libm exp/pow and Argyll's XYZ summation. Allow
    at most four double ULPs at the larger magnitude for finite, nonzero floats
    of the same sign in XYZ, spectral sample and computed quantity arrays only.
    Outside existing per-run fields, grids, domain metadata, other numbers, zero,
    numeric types and all structural values stay exact.
    Embedded JSON observations use the same comparison; ordinary strings do not.
    Unit-check y_ratio additionally preserves both threshold decisions. Changed
    CR chroma_error must reproduce each side's own retained inputs and preserve
    its threshold decision; it has no blanket absolute or pairwise ULP allowance.
    The CR spectral mean also preserves its axis decision. Authenticated synthetic
    emitted spectra can instead fit one complete source profile under the
    exp-argument interval budget; this does not alter observations or metadata.
    """
    if isinstance(before, dict):
        if not isinstance(after, dict):
            return path, before, after
        if before.keys() != after.keys():
            return f"{path}.keys", sorted(before), sorted(after)
        if "provenance" in before:
            synthetic_context = before["provenance"] == after["provenance"] == "simulated"
        for key, value in before.items():
            other = after[key]
            if field == "spectrum" and key in {"values", "samples"} and emitted_values_match:
                continue
            if (field == "unit_check" and key == "y_ratio"
                    and _has_luminance_check(before) and _has_luminance_check(after)
                    and _float_roundoff_matches(value, other)
                    and (value >= _UNIT_RATIO_LOW, value <= _UNIT_RATIO_HIGH)
                    == (other >= _UNIT_RATIO_LOW, other <= _UNIT_RATIO_HIGH)):
                continue
            if (field == "unit_check" and key == "chroma_error" and capture_pair is not None
                    and all(_cr_chroma_reproduces(capture) for capture in capture_pair)
                    and (value <= _CR_UNIT_CHROMA) == (other <= _CR_UNIT_CHROMA)):
                continue
            if (field == "unit_check" and key == "spectrum_mean_nm" and capture_pair is not None
                    and _cr_spectrum_mean_matches(capture_pair)):
                continue
            quantity_array = isinstance(value, list) and (key in {"XYZ", "xyz"}
                              or (key == "samples" and field == "spectrum")
                              or (key == "values" and (
                                  field in {"xyz", "spectrum", "xyy", "lab"}
                                  or before.get("event") == "native_spectrum")))
            difference = _fixture_difference(value, other, key, f"{path}.{key}",
                                             allow_roundoff=quantity_array,
                                             capture_pair=(before, after)
                                             if key == "unit_check" else None,
                                             synthetic_context=synthetic_context,
                                             emitted_values_match=(synthetic_context
                                                 and field == "backend_reported"
                                                 and key == "spectrum"
                                                 and _synthetic_emitted_matches(before, after)))
            if difference is not None:
                return difference
        return None
    if isinstance(before, list):
        if not isinstance(after, list):
            return path, before, after
        if len(before) != len(after):
            return f"{path}.length", len(before), len(after)
        configuration_pair = (field == "configuration" and len(before) == 2
                              and isinstance(before[0], str))
        for index, (left, right) in enumerate(zip(before, after)):
            name = "configuration" if field == "configuration" else ""
            if configuration_pair and index == 1:
                name = before[0]
            difference = _fixture_difference(left, right, name, f"{path}[{index}]",
                                             allow_roundoff=allow_roundoff,
                                             synthetic_context=synthetic_context)
            if difference is not None:
                return difference
        return None
    if isinstance(after, (dict, list)):
        return path, before, after
    if (isinstance(before, str) and (field == "raw_backend_output" or field.endswith("_json"))
            and before.lstrip().startswith(("{", "["))):
        try:
            left = json.loads(before, object_pairs_hook=unique_object,
                              parse_constant=invalid_constant)
            right = json.loads(after, object_pairs_hook=unique_object,
                               parse_constant=invalid_constant)
        except (ValueError, TypeError):
            return path, before, after
        return _fixture_difference(left, right, path=f"{path} (JSON)",
                                   synthetic_context=synthetic_context)
    if any(isinstance(value, float) and not math.isfinite(value) for value in (before, after)):
        return path, before, after
    # Retain the existing shape guards for clocks, IDs and authenticated build
    # hashes, including their copies in native JSON. Invalid shapes cannot match
    # a normalized valid value; scientific numbers do not match this expression.
    if _per_run_blank(json.dumps({field: before})) == _per_run_blank(
            json.dumps({field: after})):
        return None
    if type(before) is not type(after):
        return path, before, after
    if allow_roundoff and _float_roundoff_matches(before, after):
        return None
    return path, before, after


def _check(build: Path) -> int:
    """Compare committed fixtures under the declared reproduction rules.

    Changed contracts, out-of-policy edits, and missing or extra files fail.
    Shape-guarded per-run exclusions, bounded quantity/derived-metric roundoff,
    CR chroma validation against each capture's own inputs, and conditioned
    reproduction of one complete synthetic source profile are intentional rules.
    Regeneration uses temporary output and names each differing
    file and its first differing field. Publication remains separate and retains
    unrounded observations.
    """
    global OUT, DISCOVERY_OUT
    committed = (OUT, DISCOVERY_OUT)
    with tempfile.TemporaryDirectory(prefix="fixture-check-") as directory:
        OUT = Path(directory) / "generic_frontend"
        DISCOVERY_OUT = Path(directory) / "discovery"
        try:
            _generate(build)
            fresh = (OUT, DISCOVERY_OUT)
        finally:
            OUT, DISCOVERY_OUT = committed
        differing = []
        for published, regenerated in zip(committed, fresh):
            names = ({p.relative_to(published) for p in published.rglob("*.json")}
                     | {p.relative_to(regenerated) for p in regenerated.rglob("*.json")})
            for name in sorted(names):
                old, new = published / name, regenerated / name
                fixture_name = f"{published.name}/{name.as_posix()}"
                if not (old.is_file() and new.is_file()):
                    state = "missing from the tree" if new.is_file() else "extra in the tree"
                    differing.append(f"{fixture_name}: {state}")
                    continue
                try:
                    before = json.loads(old.read_text(), object_pairs_hook=unique_object,
                                        parse_constant=invalid_constant)
                    after = json.loads(new.read_text(), object_pairs_hook=unique_object,
                                       parse_constant=invalid_constant)
                except ValueError as error:
                    differing.append(f"{fixture_name}: invalid JSON ({str(error)[:240]})")
                    continue
                difference = _fixture_difference(before, after)
                if difference is not None:
                    location, before, after = difference
                    # Report the actual field, including inside native JSON strings,
                    # instead of an opaque capture line thousands of bytes long.
                    differing.append(f"{fixture_name}\n      at {location}\n"
                                     f"      -{json.dumps(before)[:240]}\n"
                                     f"      +{json.dumps(after)[:240]}")
    if differing:
        raise SystemExit("Published fixtures differ from these workers; regenerate them "
                         "and tell the client:\n  " + "\n  ".join(differing))
    print("Published fixtures match these workers, per-run fields and native float roundoff aside",
          flush=True)
    return 0


def _generate(build: Path, *, refresh_current_captures=False, selected=None) -> int:
    build = build.resolve()
    # All five contributed models are part of the published fixture contract.
    # A stock or CR-only build must not silently drop those captures.
    contributed = _require_driver_series(build)
    for worker in ("generic-session-stub-worker", "generic-chart-stub-worker",
                   "description-stub-worker"):
        if not (build / native_worker_name(worker)).is_file():
            raise SystemExit(f"Missing substituted worker: {worker}")
        authenticate_stub(build / native_worker_name(worker))

    retained_index = (json.loads((OUT / "index.json").read_bytes())
                      if refresh_current_captures else {})
    if selected is not None and (not refresh_current_captures or not set(selected)
                                or not set(selected) <= {row[0] for row in PERSONAS}):
        raise ValueError("Choose existing personas for current capture refresh")
    selected = set(selected) if selected is not None else {row[0] for row in PERSONAS}
    _WRITTEN_CAPTURES.clear()
    index = json.loads(json.dumps(retained_index)) if refresh_current_captures else {
        "origin": "tools/generate_frontend_fixtures.py", "personas": {},
        "provenance": "simulated; substituted instruments only, no hardware"}
    for name, worker, instrument, serial, environment, probe, spectral in PERSONAS:
        if name not in selected:
            continue
        meter, front = _frontend(build, worker, instrument, serial, environment, probe, spectral)
        snapshot = json.loads(front.describe_json())
        # The persona's directory is this generator's output alone: clear it, so a
        # capture that is no longer produced cannot survive as a stale file.
        if not refresh_current_captures:
            for stale in (OUT / name).glob("*.json") if (OUT / name).is_dir() else ():
                stale.unlink()
        _write(OUT / name / "describe.json", snapshot)
        offered = []
        calibrated = []
        for entry in snapshot["modes"]:
            # Known unavailable routes remain visible in the descriptor/index.
            # Do not manufacture an operation or native event for a refusal that
            # occurs before a worker is started.
            if not entry["available"]:
                continue
            if name in {"pr655", "pr670"}:
                # The default actor reports Adaptive exposure. A nonadaptive
                # request must refuse during mode admission, before capture.
                # Ambient has its own reported Illuminance/Irradiance optics;
                # the emission actor correctly refuses that different geometry.
                fixture = (name + "_ambient" if entry["mode"] == "ambient_spot" else
                           name + "_nonadaptive_refused"
                           if entry["mode"].endswith("_nonadaptive") else name)
                front._meter = replace(meter, environment={
                    **os.environ, **environment, "CM_COLORIMETER_FIXTURE": fixture})
            workflow = entry["workflow"]
            layout = _LAYOUTS.get(workflow, {})
            if workflow == "strip":
                keys = next(w["layout_keys"] for w in entry["workflows"]
                            if w["workflow"] == workflow)
                layout = {k: v for k, v in layout.items() if k in keys}
            count = (layout["passes"] * layout["steps"]) if workflow in ("chart", "xy") else 4
            patches = ["A1"] if workflow == "spot" else [f"P{i + 1}" for i in range(count)]
            request = {"schema_version": 1, "device_id": snapshot["device_id"],
                       "capability_id": snapshot["capability_id"], "mode": entry["mode"],
                       "workflow": workflow, "patch_ids": patches,
                       "options": ({"correction": _required_correction(snapshot, entry)}
                                   if entry["correction_required"] else {}),
                       "layout": layout,
                       # Inline where the instrument offers it; a driver whose
                       # readings carry native facts offers retained_patch_v1 only.
                       "event_contract": ("patch_v1" if "patch_v1" in snapshot["event_contracts"]
                                          else "retained_patch_v1")}
            result, events, descriptor = _drive(front, request)
            if (request["event_contract"] == "patch_v1" and result["status"] == "failed"
                    and "bounded frame size" in (result.get("error") or "")):
                # A reading too large for an inline event -- a JETI's 401 bands --
                # is recorded the way a client must take it: by reference.
                request["event_contract"] = "retained_patch_v1"
                result, events, descriptor = _drive(front, request)
            if name in {"pr655", "pr670"}:
                front._meter = meter
            if result["status"] == "failed" and result.get("error_stage") is None:
                # Every recorded failure is the instrument's or the worker's own
                # refusal, with its stage. One without is this generator's defect.
                raise SystemExit(f"{name}/{entry['mode']}: failed outside the instrument: "
                                 f"{result.get('error')}")
            _write(OUT / name / f"{entry['mode']}.{workflow}.json",
                   {"descriptor": descriptor, "request": request,
                    "result": result, "events": events})
            outcome = json.loads(dict(result["configuration"]).get(
                "calibration_outcome_json", "null"))
            if result["status"] == "completed" and outcome and outcome["performed"]:
                calibrated.append(request)
            # A capture records its OUTCOME. A substituted instrument refuses
            # routes its persona does not serve -- check_mode is the authority and
            # the descriptor is advisory (inst.h:305-306) -- so a recorded failure
            # is a real and useful artefact. Listing it under a bare "captured"
            # left a client unable to tell a recorded refusal from a recorded
            # reading without opening all 61 files.
            offered.append({
                "capture": f"{entry['mode']}.{workflow}",
                "mode": entry["mode"], "workflow": workflow,
                "event_contract": request["event_contract"],
                "status": result["status"],
                "patches": len(result["patches"]),
                "error_stage": result.get("error_stage"),
                "error_native_code": result.get("error_native_code"),
            })
        # One retained_patch_v1 capture per persona that reads, so the `started`
        # echo and the `patch_reference` shape are pinned from a recording rather
        # than from a client's own double. Spot is enough: the contract changes
        # the patch EVENT, not the workflow.
        retained = None
        spot = next((item for item in offered
                     if item["workflow"] == "spot" and item["status"] == "completed"), None)
        if spot is not None:
            request = {"schema_version": 1, "device_id": snapshot["device_id"],
                       "capability_id": snapshot["capability_id"], "mode": spot["mode"],
                       "workflow": "spot", "patch_ids": ["A1"], "options": {},
                       "layout": {}, "event_contract": "retained_patch_v1"}
            result, events, descriptor = _drive(front, request)
            _write(OUT / name / f"{spot['mode']}.retained.json",
                   {"descriptor": descriptor, "request": request,
                    "result": result, "events": events})
            retained = {
                "capture": f"{spot['mode']}.retained", "mode": spot["mode"],
                "status": result["status"],
                "event_kinds": sorted({event["kind"] for event in events}),
                "started_event_contract": next(
                    (event["payload"].get("event_contract") for event in events
                     if event["kind"] == "started"), None),
            }
            print(f"  {name}: {spot['mode']}.retained {result['status']}", flush=True)
        kept = (retained_index["personas"].get(name, {}).get("calibration_retained")
                if refresh_current_captures else
                _calibration_retained(build, name, worker, instrument, serial, environment,
                                      probe, spectral, snapshot, calibrated))
        _write(OUT / name / "refusals.json", _refusals(front, snapshot))
        completed = [item for item in offered if item["status"] == "completed"]
        index["personas"][name] = {
            "instrument": instrument, "modes": [e["mode"] for e in snapshot["modes"]],
            "unavailable_modes": {e["mode"]: e["unavailable_reason"]
                                  for e in snapshot["modes"] if not e["available"]},
            "captured": offered,
            "completed": [item["capture"] for item in completed],
            "not_completed": [item["capture"] for item in offered
                              if item["status"] != "completed"],
            "capability_id": snapshot["capability_id"],
            "retained_patch_v1": retained,
            "calibration_retained": kept,
        }
        print(f"  {name}: {len(completed)}/{len(offered)} completed", flush=True)
    # At least one representative per operator action, so a client pins the
    # recorded payload rather than the documented shape.
    calibrations = [item for item in index.get("calibrations", [])
                    if item["persona"] not in selected]
    for name, action, worker, instrument, serial, environment, probe, spectral in CALIBRATIONS:
        if name not in selected:
            continue
        _, front = _frontend(build, worker, instrument, serial, environment, probe, spectral)
        snapshot = json.loads(front.describe_json())
        # Calibration conditions depend on the selected mode. In particular a
        # reflective filter change must not be captured under the first emitted
        # spot route, which may be emissive. Use this case's declared mode.
        mode = probe
        request = {"schema_version": 1, "device_id": snapshot["device_id"],
                   "capability_id": snapshot["capability_id"], "mode": mode,
                   "workflow": "calibrate", "patch_ids": [], "options": {}, "layout": {}}
        result, events, descriptor = _drive(front, request)
        _write(OUT / name / f"calibration.{action}.json",
               {"descriptor": descriptor, "request": request,
                    "result": result, "events": events})
        prompts = [event["payload"] for event in events
                   if event["kind"] in ("instruction_required", "instruction_notified")]
        calibrations.append({
            "capture": f"{name}/calibration.{action}",
            "persona": name, "action": action, "status": result["status"],
            "prompts": len(prompts),
            "conditions": [prompt["calibration"]["condition"] for prompt in prompts],
            "answered": [prompt["calibration"]["acknowledged"] for prompt in prompts],
        })
        print(f"  {name}: calibration.{action} {result['status']} "
              f"({len(prompts)} prompt(s))", flush=True)
    calibration_order = {f"{name}/calibration.{action}": position
                         for position, (name, action, *_) in enumerate(CALIBRATIONS)}
    index["calibrations"] = sorted(calibrations,
                                   key=lambda item: calibration_order[item["capture"]])
    index["calibration_capture_shape"] = (
        "At least one representative capture per operator action. `instruction_required` "
        "carries a prompt to answer; `instruction_notified` carries one that takes NO answer, "
        "because the instrument itself triggers it -- its `calibration.acknowledged` "
        "is false. Both carry the same decoded `calibration` block. The i1Pro "
        "family raises 7 of ArgyllCMS's 19 conditions, so most of these come from "
        "instruments whose conditions no i1Pro can raise."
    )
    index["captured_entry_shape"] = (
        "Each `captured` entry records its own outcome: status, patch count, and "
        "the native stage/code when the substituted instrument refused the route. "
        "A `failed` capture is a real recorded refusal, not a gap in the fixtures: "
        "every non-completed capture here carries a stage, so none of them failed "
        "outside the instrument. `check_mode` means the persona does not serve "
        "that route; `set_filter` means it does not advertise that measurement "
        "condition. A stage-less failure would mean the capture itself broke and "
        "should be reported rather than pinned."
    )
    index["calibration_retained_shape"] = (
        "<persona>/calibration_retained.json, for each persona whose fresh start "
        "calibrated: every such route read again from a start whose driver restored "
        "a calibration still within its own limits (the stubs' retained_calibration). "
        "`routes` maps the mode to the helper's native events for that reading. A "
        "reading asks the driver to keep what it retained (initial_calibration_"
        "suppressed in calibration_ready); what is still asked is the driver's own "
        "need, or a transmission white. A driver that is not asked -- the "
        "SpectroScan family, whose option would mask the need -- calibrates again. "
        "The demo replays these once a mode has been calibrated."
    )
    index["not_yet_available"] = {}

    # Current captures can add native modes or change their time floors. Refresh
    # this build metadata even when unchanged discovery envelopes are retained.
    manifest = build / "driver-manifest.json"
    if not manifest.is_file():
        raise SystemExit("Missing driver-manifest.json beside the substituted workers")
    catalog_json = json.dumps(
        {"instrument_types": json.loads(manifest.read_text())["instrument_types"]})
    _write(DISCOVERY_OUT / "catalog.json", json.loads(catalog_json))
    _write(DISCOVERY_OUT / "catalog-without-contribution.json", {
        "instrument_types": [row for row in json.loads(catalog_json)["instrument_types"]
                             if row["enum"] not in contributed],
    })
    if not refresh_current_captures:
        enumerations = _capture_enumerations(build, catalog_json)
        native_messages = _capture_native_messages(build, catalog_json)
        _write(DISCOVERY_OUT / "index.json", {
            "origin": "tools/generate_frontend_fixtures.py",
            "provenance": "simulated; substituted description worker only, no hardware",
            "what_this_is": (
                "One capture per enumeration scenario -- the result of a 'Refresh devices' "
                "action. Enumerating opens nothing: on macOS every transport is read out "
                "of the IORegistry (usbio_ox.c:34-98, hidio.c:288-297, icoms_ux.c). Each "
                "unit carries what a picker must show, including units it must NOT open "
                "silently. Each unit's `opening_requires` is the argument describe needs "
                "first: null when it needs none, `expect` for an unidentified path, or "
                "not_permitted when nothing opens it. `opening` repeats it per index."
            ),
            "catalog": ("catalog.json: native_type -> instType symbol, from the build's own "
                        "driver manifest. An ordinal is a position upstream may reorder, so "
                        "this is a build artifact and never a table in client code."),
            "enumerations": enumerations,
            "native_messages": native_messages,
            "catalog_without_contribution": "catalog-without-contribution.json",
        })
        print(f"Wrote {DISCOVERY_OUT}", flush=True)

    failures = [item for item in index.get("native_failures", [])
                if item["capture"].split("/", 1)[0] not in selected]
    for fixture, capture, timeout in CR250_FAILURES if "cr250" in selected else ():
        _, front = _frontend(build, "generic-session-stub-worker", "Colorimetry Research CR-250",
                             "SYNTHETIC-250", {"CM_COLORIMETER_FIXTURE": fixture},
                             "emissive_spot", False, timeout=timeout)
        snapshot = json.loads(front.describe_json())
        request = {"schema_version": 1, "device_id": snapshot["device_id"],
                   "capability_id": snapshot["capability_id"], "mode": "emissive_spot",
                   "workflow": "spot", "patch_ids": ["A1"], "options": {}, "layout": {},
                   "event_contract": "retained_patch_v1"}
        result, events, descriptor = _drive(front, request)
        configuration = dict(result["configuration"])
        if (result["status"] != "failed"
                or "error_native_text" not in configuration
                or "native_facts_json" not in configuration):
            raise SystemExit(f"cr250/{capture}: not a failed read with its native evidence")
        _write(OUT / "cr250" / f"failure.{capture}.json",
               {"descriptor": descriptor, "request": request,
                    "result": result, "events": events})
        failures.append({"capture": f"cr250/failure.{capture}", "status": result["status"],
                         "patches": len(result["patches"]),
                         "error_stage": result["error_stage"],
                         "error_native_code": result["error_native_code"]})
        print(f"  cr250: failure.{capture} {result['status']}", flush=True)
    failure_order = {f"cr250/failure.{capture}": position
                     for position, (_, capture, _) in enumerate(CR250_FAILURES)}
    index["native_failures"] = sorted(failures,
                                      key=lambda item: failure_order[item["capture"]])

    scenarios = [item for item in index.get("scenarios", [])
                 if item["capture"].split("/", 1)[0] not in selected]
    personas = {row[0]: row[1:] for row in PERSONAS}
    for (persona, name, environment, mode, workflow, count, layout, options,
         status, stage, retained) in SCENARIOS:
        if persona not in selected:
            continue
        # The persona's own worker and settings, with the scenario's on top.
        worker, instrument, serial, base, probe, spectral = personas[persona]
        _, front = _frontend(build, worker, instrument, serial, {**base, **environment},
                             probe, spectral)
        snapshot = json.loads(front.describe_json())
        published = json.loads((OUT / persona / "describe.json").read_text())
        if snapshot["capability_id"] != published["capability_id"]:
            raise SystemExit(f"{persona}/scenario.{name}: not the published descriptor")
        if workflow == "strip":
            entry = next(e for e in snapshot["modes"] if e["mode"] == mode)
            keys = next(w["layout_keys"] for w in entry["workflows"]
                            if w["workflow"] == workflow)
            layout = {k: v for k, v in layout.items() if k in keys}
        _capture_scenario(front, snapshot, persona, name, mode, workflow, count, layout,
                          options, status, stage, retained, scenarios)
    for persona in ("pr655", "pr670"):
        if persona in selected:
            _capture_pr_control_recovery(build, persona, personas[persona], scenarios)
    scenario_order = {f"{persona}/scenario.{name}": position
                      for position, (persona, name, *_) in enumerate(SCENARIOS)}
    for persona in ("pr655", "pr670"):
        for name, *_ in PR_CONTROL_RECOVERY:
            scenario_order[f"{persona}/scenario.{name}"] = len(scenario_order)
    index["scenarios"] = sorted(scenarios, key=lambda item: scenario_order[item["capture"]])
    if "i1_display_3" in selected:
        _generate_display_variants(build, index)
    for name in selected:
        for stale in (OUT / name).glob("*.json"):
            if stale not in _WRITTEN_CAPTURES and not (
                refresh_current_captures and stale.name == "calibration_retained.json"
            ):
                stale.unlink()
    _write(OUT / "index.json", index)
    print(f"Wrote {OUT}", flush=True)
    return 0


def _generate_display_variants(build, index):
    """The existing i1 Display-specific capture cases, when that persona is selected."""
    personas = {row[0]: row[1:] for row in PERSONAS}
    # One current request/result pair pins the named choice wire shape for
    # consumers. The driver still owns its index and confirms the identity.
    worker, instrument, serial, base, probe, spectral = personas["i1_display_3"]
    _, front = _frontend(build, worker, instrument, serial, base, probe, spectral)
    snapshot = json.loads(front.describe_json())
    choice = snapshot["display_types"]["entries"][1]["id"]
    request = {"schema_version": 1, "device_id": snapshot["device_id"],
               "capability_id": snapshot["capability_id"], "mode": "emissive_spot",
               "workflow": "spot", "patch_ids": ["A1"], "layout": {},
               "options": {"display_type": choice}, "event_contract": "retained_patch_v1"}
    result, events, descriptor = _drive(front, request)
    types = json.loads(dict(result["configuration"])["display_types_json"])
    if result["status"] != "completed" or types["applied_choice_id"] != choice:
        raise SystemExit("Named display-type fixture did not confirm its selected identity")
    _write(OUT / "i1_display_3" / "named_display_type.json",
           {"descriptor": descriptor, "request": request,
                    "result": result, "events": events})
    index["named_display_type"] = "i1_display_3/named_display_type.json"
    isolated = []
    for fixture in ("display_invalid_label", "display_invalid_path",
                    "display_invalid_both", "display_invalid_all"):
        _, front = _frontend(build, "generic-session-stub-worker", "instI1Disp3",
            "CM-COLORIMETER-0001", {"CM_COLORIMETER_FIXTURE": fixture},
            "emissive_spot", False)
        snapshot = json.loads(front.describe_json())
        rows = snapshot["display_types"]["entries"]
        available = [row for row in rows if row["available"]]
        choice = available[-1]["id"] if available else None
        request = {"schema_version": 1, "device_id": snapshot["device_id"],
            "capability_id": snapshot["capability_id"], "mode": "emissive_spot",
            "workflow": "spot", "patch_ids": ["A1"], "layout": {},
            "options": {} if choice is None else {"display_type": choice},
            "event_contract": "retained_patch_v1"}
        result, events, descriptor = _drive(front, request)
        retained = json.loads(dict(result["configuration"])["display_types_json"])
        if result["status"] != "completed" or retained["applied_choice_id"] != choice:
            raise SystemExit(f"{fixture}: valid selection/default was not retained")
        disabled = next(row for row in rows if not row["available"])
        refused = request | {"options": {"display_type": disabled["id"]}}
        try:
            front.prepare(json.dumps(refused))
        except ValueError as error:
            refusal = {"request": refused, "error_type": "ValueError", "message": str(error)}
        else:
            raise SystemExit(f"{fixture}: unavailable choice started an operation")
        capture = f"i1_display_3/{fixture}.json"
        _write(OUT / capture, {"descriptor": descriptor, "request": request,
                              "result": result, "events": events, "refusal": refusal})
        isolated.append(capture)
    index["display_type_isolation"] = isolated
    # Scalar records are a separate quantity shape, so retain successful and
    # refused calls for clients instead of adapting a colour observation fixture.
    refresh = []
    for fixture, expected_status, expected_stage in (
        ("refresh_success", "completed", None),
        ("refresh_needs_cal", "failed", "read_refrate"),
        ("refresh_nan", "failed", "invalid_refresh_frequency"),
    ):
        _, front = _frontend(build, "generic-session-stub-worker", "instI1Disp3",
            "CM-COLORIMETER-0001", {"CM_COLORIMETER_FIXTURE": fixture},
            "emissive_spot", False)
        snapshot = json.loads(front.describe_json())
        canonical = json.loads((OUT / "i1_display_3" / "describe.json").read_text())
        if snapshot != canonical:
            raise SystemExit("Refresh capture differs from its canonical persona descriptor")
        request = {"schema_version": 1, "device_id": snapshot["device_id"],
            "capability_id": snapshot["capability_id"], "mode": "emissive_spot",
            "workflow": "refresh_frequency", "patch_ids": ["RATE-1"], "options": {},
            "layout": {"stimulus_id": "SIMULATED-WHITE",
                       "stimulus_description": "Externally declared steady white, simulated"},
            "event_contract": "retained_patch_v1"}
        result, events, descriptor = _drive(front, request)
        if (result["status"] != expected_status or result["error_stage"] != expected_stage
                or len(result["patches"]) != int(expected_status == "completed")):
            raise SystemExit(f"{fixture}: scalar outcome differs from its native fixture")
        capture = f"i1_display_3/{fixture}.json"
        _write(OUT / capture, {"descriptor": descriptor, "request": request,
                              "result": result, "events": events})
        refresh.append(capture)
    index["refresh_frequency"] = refresh
    queries = []
    for fixture, workflow, options, expected_status, expected_stage in (
        ("success", "mode_snapshot", {}, "completed", None),
        ("stored_unsupported", "mode_snapshot", {}, "completed", None),
        ("stored_zero", "mode_snapshot", {}, "failed", "invalid_stored_refresh_rate"),
        ("success", "stored_refresh", {}, "completed", None),
        ("success", "stored_refresh", {"refresh_rate": 60}, "completed", None),
        ("success", "stored_refresh", {"refresh_rate": 0}, "completed", None),
        ("stored_misread", "stored_refresh", {}, "completed", None),
        ("stored_zero", "stored_refresh", {}, "failed", "invalid_stored_refresh_rate"),
        ("stored_error", "stored_refresh", {}, "failed", "get_refresh_rate"),
        ("stored_set_error", "stored_refresh", {"refresh_rate": 60}, "failed", "set_refresh_rate"),
    ):
        _, front = _frontend(build, "generic-session-stub-worker", "instI1Disp3",
            "CM-COLORIMETER-0001", {"CM_COLORIMETER_FIXTURE": fixture},
            "emissive_spot", False)
        snapshot = json.loads(front.describe_json())
        request = {"schema_version": 1, "device_id": snapshot["device_id"],
            "capability_id": snapshot["capability_id"], "mode": "emissive_spot",
            "workflow": workflow, "patch_ids": [], "options": options,
            "layout": {}, "event_contract": "retained_patch_v1"}
        result, events, descriptor = _drive(front, request)
        if (result["status"] != expected_status or result["error_stage"] != expected_stage
                or result["patches"]):
            raise SystemExit(f"{fixture}: configuration query outcome differs")
        stale = None
        if workflow == "mode_snapshot" and expected_status == "completed":
            try:
                front.prepare(json.dumps(request))
            except ValueError as error:
                stale = {"type": type(error).__name__, "message": str(error)}
            else:
                raise SystemExit("Previous capability ID remained usable after a query")
        suffix = str(options["refresh_rate"]) if options else "default"
        capture = f"i1_display_3/query.{workflow}.{fixture}.{suffix}.json"
        _write(OUT / capture, {"descriptor": descriptor, "request": request,
            "result": result, "events": events,
            "after_descriptor": json.loads(front.describe_json()), "stale_request_refusal": stale})
        queries.append(capture)
    for cancelled in (False, True):
        _, front = _frontend(build, "generic-session-stub-worker", "instI1Disp3",
            "CM-COLORIMETER-0001", {"CM_COLORIMETER_FIXTURE": "skippable_calibration"},
            "emissive_spot", False)
        snapshot = json.loads(front.describe_json())
        request = {"schema_version": 1, "device_id": snapshot["device_id"],
            "capability_id": snapshot["capability_id"], "mode": "emissive_spot",
            "workflow": "stored_refresh", "patch_ids": [], "options": {},
            "layout": {"calibrate": True}, "event_contract": "retained_patch_v1"}
        result, events, descriptor = _drive(front, request, cancel_instruction=cancelled)
        if result["status"] != ("cancelled" if cancelled else "completed") or result["patches"]:
            raise SystemExit("Stored query calibration/cancellation outcome differs")
        capture = f"i1_display_3/query.stored_refresh.calibration.{str(cancelled).lower()}.json"
        _write(OUT / capture, {"descriptor": descriptor, "request": request,
            "result": result, "events": events,
            "after_descriptor": json.loads(front.describe_json())})
        queries.append(capture)
    index["configuration_queries"] = queries
    timing = []
    for fixture, expected_status, expected_stage in (
        ("timing_success", "completed", None), ("timing_zero", "completed", None),
        ("timing_marker_error", "failed", "white_change"),
        ("timing_error", "failed", "meas_delay"),
    ):
        _, front = _frontend(build, "generic-session-stub-worker", "instI1Disp3",
            "CM-COLORIMETER-0001", {"CM_COLORIMETER_FIXTURE": fixture},
            "emissive_spot", False)
        snapshot = json.loads(front.describe_json())
        canonical = json.loads((OUT / "i1_display_3" / "describe.json").read_text())
        if snapshot != canonical:
            raise SystemExit("Timing capture differs from its canonical persona descriptor")
        request = {"schema_version": 1, "device_id": snapshot["device_id"],
            "capability_id": snapshot["capability_id"], "mode": "emissive_spot",
            "workflow": "display_timing", "patch_ids": ["TIMING-1"], "options": {},
            "layout": {"stimulus_id": "SIMULATED-BLACK-WHITE",
                       "stimulus_description": "Controlled simulated black-to-white transition",
                       "display_id": "SIMULATED-DISPLAY"},
            "event_contract": "retained_patch_v1"}
        result, events, descriptor = _drive(front, request)
        if (result["status"] != expected_status or result["error_stage"] != expected_stage
                or len(result["patches"]) != int(expected_status == "completed")):
            raise SystemExit(f"{fixture}: timing outcome differs from its native fixture")
        capture = f"i1_display_3/{fixture}.json"
        _write(OUT / capture, {"descriptor": descriptor, "request": request,
                              "result": result, "events": events})
        timing.append(capture)
    index["display_timing"] = timing


if __name__ == "__main__":
    raise SystemExit(main())
