"""Guide a physical bench session through the desktop web UI and record it.

The operator drives each instrument in the web application; this tool says
what to do next, checks what the application saved and records the outcome,
so no step is forgotten or retyped. It never opens a device, enumerates ports
or starts a native worker. It authenticates the selected app's bundled report with
``InstrumentBuild.from_report`` (which hashes files and runs nothing) and reads
every saved batch with the application's read-only ``review_saved``.

    uv run --locked python tools/bench_session.py start --name NAME --operator WHO \\
        --app '/path/to/Color Measurement.app'
    uv run --locked python tools/bench_session.py run --session DIR --device cr250
    uv run --locked python tools/bench_session.py report --session DIR

Records are append-only: ``steps.jsonl`` chains each record to the previous
one by SHA-256, and the saved files each step checked are copied under
``evidence/``. Rerunning ``run`` resumes at the first unrecorded step.
``--rehearsal`` admits only simulated Demo batches and marks the session as a
rehearsal, not evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import plistlib
import shlex
import shutil
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCH = "uv run --locked --extra app color-measurement-app"
EQUIPMENT = "Connect instrument... > List connected instruments"
NEW_BATCH = ("New batch: choose Instrument = this device, Measurement mode = '{label}', a "
             "batch name, then Create batch and choose a NEW folder. Paste or drag that "
             "folder here when asked.")
SAFETY = (
    "Never unplug an instrument or close its port during acquisition.",
    "After any cancelled or failed operation, confirm the instrument is idle "
    "before continuing (PR: remote mode and backlight).",
    "Stop and record any unexpected state; do not change settings or code to make "
    "a check pass.",
    "One operator; no other program (CRI App, spotread, another app) may hold the "
    "instrument. Do not change firmware, drivers or calibration data.",
    "Exploratory comparisons have no pass or fail unless a limit was declared first.",
)


@dataclass(frozen=True)
class Device:
    key: str
    model: str
    symbol: str
    native: str | None          # native control driver, or None
    spectrum: bool              # whether a full read publishes a spectrum
    bands: int | None = None    # fixed band count, when the protocol fixes it


DEVICES = {
    "i1pro3": Device("i1pro3", "X-Rite i1 Pro 3", "instI1Pro3", None, True),
    "cr250": Device("cr250", "Colorimetry Research CR-250", "instCR250", "cr250", True),
    "cr100": Device("cr100", "Colorimetry Research CR-100", "instCR100", "cr250", False),
    "pr655": Device("pr655", "Photo Research PR-655", "instPR655", "pr655", True, 101),
}


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    lines: tuple[str, ...]
    kind: str = "confirm"       # confirm, batch, readings, calibrate, settings, cancel, finish
    mode: str | None = None     # batch steps: the mode to create
    count: int = 1              # readings steps: accepted spot readings expected
    xyz_only: bool = False      # readings: the batch was set to XYZ only
    action: str | None = None   # settings steps: read, apply or restore
    fields: tuple[tuple[str, str], ...] = ()   # typed answers: (key, prompt)
    optional: bool = False      # may be skipped when the descriptor lacks the mode
    target: str | None = None   # readings: shared target name for comparisons
    gate: bool = False          # a failure here offers to stop this device


def _common_head(device: Device) -> list[Step]:
    clients = ("Close CRI App and any other program that can open the CR. "
               if device.native == "cr250" else "")
    return [
        Step("preflight", "Preflight", (
            f"{clients}Connect the {device.model}; note the cable, adapter and port.",
            "Do not update firmware. Have the white tile, display and stable lamp ready.",
            "Open the application recorded in this session's OPERATOR.md.",
        ), fields=(("connection", "Cable/adapter/port"),)),
        Step("initialize", "Initialize the device", (
            EQUIPMENT,
            *((f"Select the serial-port row the {device.model} is connected to, and set "
               f"Instrument model = {device.model}. Leave 'Identify the selected serial "
               "connection' unticked: this model is selected by type, without probing.",)
              if device.native else
              (f"Select the USB row identified as {device.model}.",)),
            "Connect selected instrument: expected serial if known, and an Operation "
            "timeout above the 'Instrument allowance' it shows (floor printed below).",
            *(("Read the disclosed effect (remote mode; the touch screen is disabled "
               "until a clean close), then tick 'I accept the initialization effect "
               "described above'.",) if device.key == "pr655" else ()),
            "Expect 'Device initialized. It is ready for a new batch.'",
        ), fields=(("listed_row", "Listed device row text"),
                   ("serial", "Serial number shown"),
                   ("firmware", "Firmware shown (or 'not shown')")), gate=True),
    ]


def _new_batch(mode: str) -> str:
    # The setup list shows each offer's label, which the frontend derives from
    # the mode name with this function.
    from color_measurement.instrument_frontend import _mode_label

    return NEW_BATCH.format(label=_mode_label(mode))


def _control(driver: str, key: str) -> str:
    """The settings field's caption, from the same rows the application presents."""
    from color_measurement.native_controls import control_request_fields

    label, = (row["label"] for row in control_request_fields(driver) if row["key"] == key)
    return f"'{label}'"


def _repeat(mode: str, target: str, *, xyz_only=False, label="") -> list[Step]:
    suffix = f"-{label}" if label else ""
    return [
        Step(f"batch-{mode}{suffix}", f"New batch, {mode}{' (' + label + ')' if label else ''}", (
            _new_batch(mode),
            *(("In the setup options tick 'XYZ only' before Create batch.",) if xyz_only else ()),
        ), kind="batch", mode=mode, gate=True),
        Step(f"repeat-{mode}{suffix}", f"Five readings of {target}", (
            f"Aim at {target}. Without moving anything, press Measure five times, "
            "waiting for each reading to finish.",
        ), kind="readings", count=5, xyz_only=xyz_only, target=target),
    ]


def _cancel_and_finish() -> list[Step]:
    return [
        Step("cancel", "Cancel once, then confirm idle", (
            "Press Measure, then Cancel operation while it is still measuring.",
            "Confirm the instrument is idle, then press Measure once more and let it "
            "finish.",
        ), kind="cancel"),
        Step("finish", "Finish, reopen and check the saved batch", (
            "Batch > Finish batch. Then Batch > Open saved batch... on this folder and "
            "check the readings shown match; Return to live. Then Batch > Check saved "
            "files.",
        ), kind="finish", fields=(("review_matches", "Reopened readings match (yes/no)"),)),
    ]


def _settings(action: str, change: str, step_id: str, title: str) -> Step:
    lines = ["Task 'Read or apply instrument settings'."]
    if action == "read":
        lines.append("Settings action 'read', then press the main button.")
    else:
        lines += [
            f"Open 'Advanced instrument settings', set {change} (blank = unchanged), "
            "'Apply to next reading', then Settings action 'apply' and press the main "
            "button.",
        ]
    return Step(step_id, title, tuple(lines), kind="settings", action=action)


def steps_for(device: Device) -> list[Step]:
    steps = _common_head(device)
    if device.key == "i1pro3":
        steps += [
            Step("batch-reflective_spot", "New batch, reflective_spot", (
                _new_batch("reflective_spot"),
            ), kind="batch", mode="reflective_spot", gate=True),
            Step("calibrate-reflective", "Reflective calibration on the white tile", (
                "Check that the tile serial matches the instrument and the slider is "
                "fully open. Batch > Calibrate... and follow each prompt.",
            ), kind="calibrate", fields=(("tile_serial", "Tile serial"),)),
            Step("repeat-reflective_spot", "Five readings of the white tile", (
                "Place the instrument on the tile. Press Measure five times without "
                "moving it.",
            ), kind="readings", count=5, target="white tile"),
            *_cancel_and_finish(),
            # The default reflective reading does not cover the explicit
            # measurement conditions the descriptor also offers.
            *(step for condition in ("m0", "m1", "m2") for step in (
                Step(f"batch-reflective_spot_{condition}",
                     f"New batch, reflective_spot_{condition} (if offered)", (
                         _new_batch(f"reflective_spot_{condition}"),
                     ), kind="batch", mode=f"reflective_spot_{condition}", optional=True),
                Step(f"repeat-reflective_spot_{condition}",
                     f"Three {condition.upper()} readings of the white tile", (
                         "If the app asks for a calibration, follow its prompt first. "
                         "Place the instrument on the tile and press Measure three times "
                         "without moving it.",
                     ), kind="readings", count=3, optional=True, target="white tile"),
                Step(f"finish-reflective_spot_{condition}",
                     f"Finish the {condition.upper()} batch", (
                         "Batch > Finish batch.",), kind="finish", optional=True),
            )),
            *_repeat("emissive_spot", "the display white patch"),
            Step("finish-emissive_spot", "Finish the emissive batch", (
                "Batch > Finish batch.",), kind="finish"),
            Step("batch-ambient_spot", "New batch, ambient_spot (if offered)", (
                "Calibrate on the base first, then fit the ambient head as prompted. "
                + _new_batch("ambient_spot"),
            ), kind="batch", mode="ambient_spot", optional=True),
            Step("repeat-ambient_spot", "Five ambient readings", (
                "Aim at the stable lamp. Press Measure five times.",
            ), kind="readings", count=5, optional=True, target="stable lamp"),
            Step("finish-ambient_spot", "Finish the ambient batch", (
                "Batch > Finish batch.",), kind="finish", optional=True),
        ]
    elif device.native == "cr250":
        cr250 = device.key == "cr250"
        steps += [
            Step("batch-emissive_spot", "New batch, emissive_spot", (
                _new_batch("emissive_spot"),
            ), kind="batch", mode="emissive_spot", gate=True),
            _settings("read", "", "settings-read", "Read current settings"),
            Step("repeat-emissive_spot", "Five readings of the display white", (
                "Aim at the display white patch. Press Measure five times without "
                "moving anything.",
            ), kind="readings", count=5, target="the display white patch"),
            _settings("apply", f"{_control('cr250', 'exposureMultiplier')} = 2",
                      "settings-apply", "Apply a reversible change"),
            Step("reading-after-apply", "One reading with the change", (
                "Press Measure once.",), kind="readings", count=1),
            _settings("restore", f"{_control('cr250', 'exposureMultiplier')} back to the "
                      "value read in 'settings-read'", "settings-restore", "Restore the original"),
            *_cancel_and_finish(),
        ]
        if cr250:
            steps += _repeat("emissive_spot", "the display white patch", xyz_only=True,
                             label="xyz-only")
            steps.append(Step("finish-xyz-only", "Finish the XYZ-only batch", (
                "Batch > Finish batch.",), kind="finish"))
        steps += [
            Step("batch-emissive_tele", "New batch, emissive_tele (if offered)", (
                "Fit the tele optic if available. " + _new_batch("emissive_tele"),
            ), kind="batch", mode="emissive_tele", optional=True),
            Step("repeat-emissive_tele", "Five tele readings", (
                "Aim at the display white patch. Press Measure five times.",
            ), kind="readings", count=5, optional=True),
            Step("finish-emissive_tele", "Finish the tele batch", (
                "Batch > Finish batch.",), kind="finish", optional=True),
        ]
    else:  # PR-655
        steps += [
            Step("backlight", "Record the original backlight level", (
                "Before the first reading, note the instrument's backlight level; the "
                "driver sets 100% after readings and does not restore it.",
            ), fields=(("backlight", "Original backlight level"),)),
            Step("batch-emissive_spot", "New batch, emissive_spot", (
                _new_batch("emissive_spot"),
            ), kind="batch", mode="emissive_spot", gate=True),
            _settings("read", "", "settings-read", "Read current settings"),
            Step("repeat-emissive_spot", "Five readings of the display white", (
                "Aim at the display white patch. Press Measure five times without "
                "moving anything.",
            ), kind="readings", count=5, target="the display white patch"),
            _settings("apply", f"{_control('pr655', 'syncMode')} = 1 (auto)", "o1-sync-auto",
                      "O1: auto sync"),
            Step("o1-reading", "O1: one reading in auto sync", (
                "Aim at the display. Press Measure once.",), kind="readings", count=1),
            _settings("read", "", "o1-read-d602",
                      "O1: read D602 after the exposure"),
            _settings("apply", f"{_control('pr655', 'syncFrequencyHz')} = the display "
                      f"refresh rate in whole Hz, then {_control('pr655', 'syncMode')} = 3 (user)",
                      "o1-user-sync", "O1: SK then SS3"),
            _settings("restore", f"{_control('pr655', 'syncMode')} and "
                      f"{_control('pr655', 'syncFrequencyHz')} back to the values read in "
                      "'settings-read'", "settings-restore", "Restore the original"),
            _settings("apply", f"{_control('pr655', 'cycles')} = 3", "cycles-apply",
                      "Apply cycles 3"),
            Step("reading-cycles", "One reading with 3 cycles", (
                "Press Measure once.",), kind="readings", count=1),
            _settings("restore", f"{_control('pr655', 'cycles')} back to the value read in "
                      "'settings-read'", "cycles-restore", "Restore cycles"),
            Step("dim-reading", "Optional dim patch", (
                "Show a dark grey patch (roughly 0.05-0.2 cd/m2) and press Measure once. "
                "This is an ordinary low-light reading, not a provoked failure; record "
                "what the app reports.",
            ), kind="readings", count=1, optional=True),
            *_cancel_and_finish(),
            Step("batch-ambient_spot", "New batch, ambient_spot (if an irradiance "
                 "accessory is fitted)", (_new_batch("ambient_spot"),),
                 kind="batch", mode="ambient_spot", optional=True),
            Step("repeat-ambient_spot", "Five ambient readings", (
                "Aim at the stable lamp. Press Measure five times.",
            ), kind="readings", count=5, optional=True, target="stable lamp"),
            Step("finish-ambient_spot", "Finish the ambient batch", (
                "Batch > Finish batch.",), kind="finish", optional=True),
            Step("touch-screen", "After closing, the touch screen works", (
                "Device & setup: Remove from app, then check the PR-655 touch screen "
                "responds and note the backlight level.",
            ), fields=(("touch_screen", "Touch screen works (yes/no)"),
                       ("backlight_after", "Backlight level after"))),
        ]
    return steps


# -- records -------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_records(session: Path) -> list[dict]:
    """Return every record after verifying the hash chain; a broken chain refuses."""
    path = session / "steps.jsonl"
    if not path.exists():
        return []
    records, previous = [], None
    for number, line in enumerate(path.read_bytes().splitlines(), 1):
        record = json.loads(line)
        if record.get("previous_sha256") != previous:
            raise ValueError(f"steps.jsonl record {number} does not follow its predecessor")
        records.append(record)
        previous = _sha256(line)
    return records


def append_record(session: Path, record: dict) -> dict:
    records = read_records(session)
    path = session / "steps.jsonl"
    previous = _sha256(path.read_bytes().splitlines()[-1]) if records else None
    record = dict(record, seq=len(records) + 1, recorded_at=_now(), previous_sha256=previous)
    line = json.dumps(record, sort_keys=True, allow_nan=False).encode()
    with open(path, "ab") as stream:
        stream.write(line + b"\n")
    return record


def _load_session(session: Path) -> dict:
    meta = json.loads((session / "session.json").read_text())
    if meta.get("schema") != 2:
        raise ValueError("Bench session schema is not current; preserve it and start a new session")
    return meta


# -- checks over the saved batch ------------------------------------------------------


def _finite_triple(values) -> bool:
    return (isinstance(values, list) and len(values) == 3
            and all(type(v) in (int, float) and math.isfinite(v) for v in values))


def _check(checks: list, name: str, ok: bool, detail: str = "") -> None:
    checks.append(dict(name=name, ok=bool(ok), detail=detail))


def check_identity(review: dict, device: Device, *, rehearsal: bool, serial: str | None,
                   mode: str | None = None, report: dict | None = None) -> list[dict]:
    checks: list[dict] = []
    descriptor = review["manifest"]["descriptor"]
    provenance = descriptor.get("provenance")
    admitted = provenance == ("simulated" if rehearsal else "physical")
    _check(checks, "provenance", admitted, f"{provenance}{' (rehearsal)' if rehearsal else ''}")
    if rehearsal:
        backend = descriptor["device"].get("backend")
        _check(checks, "Demo rehearsal source", backend == "argyll-demo", str(backend))
    model = descriptor["device"].get("model")
    _check(checks, "model", model == device.model, f"{model}")
    actual = descriptor["device"].get("serial")
    if serial:
        _check(checks, "serial", actual == serial, f"saved {actual!r}, operator saw {serial!r}")
    if mode is not None:
        _check(checks, "mode", review["manifest"]["mode"] == mode, review["manifest"]["mode"])
    if not rehearsal and provenance == "physical":
        backend = descriptor["device"].get("backend")
        _check(checks, "Argyll C backend", backend == "argyll-native", str(backend))
        identity = descriptor.get("current_binding_identity") or {}
        actual = identity.get("provenance") if isinstance(identity, dict) else None
        expected = (report or {}).get("binding_receipt")
        _check(checks, "native build receipt", bool(expected) and actual == expected,
               "matches selected instrument software" if expected and actual == expected
               else "missing or different report/version/worker references")
    return checks


def _observations(operation: dict) -> list[dict]:
    return [patch["observation"] for patch in operation["result"]["patches"]]


def _config(item: dict) -> dict:
    return dict(item.get("configuration") or [])


def mode_inventory(review: dict) -> dict:
    """Keep every actual offer and its setup context, without inferring support."""
    descriptor = review["manifest"]["descriptor"]
    return dict(descriptor_sha256=_sha256(json.dumps(
        descriptor, sort_keys=True, allow_nan=False).encode()), rows=[dict(
            mode=row["mode"], label=row["label"],
            condition=(row.get("axes") or {}).get("condition"),
            workflow=row["workflow"], available=row["available"],
            unavailable_reason=row.get("unavailable_reason"),
            operator_setup=row.get("operator_setup"),
            calibration_setup=row.get("calibration_setup", []),
        ) for row in descriptor["modes"]])


def mode_coverage(records: list[dict]) -> list[dict]:
    """Only matching retained measurement checks can cover a descriptor's mode."""
    inventories = {}
    for record in records:
        inventory = (record.get("facts") or {}).get("mode_inventory")
        if inventory is not None:
            inventories[inventory["descriptor_sha256"]] = inventory
    result = []
    for digest, inventory in inventories.items():
        rows = []
        for offer in inventory["rows"]:
            matching = [r for r in records if r.get("kind") == "readings"
                        and (r.get("facts") or {}).get("descriptor_sha256") == digest
                        and r["facts"].get("measured_mode") == offer["mode"]]
            if not offer["available"]:
                state = "not available"
            elif not matching:
                state = "not executed"
            elif any(r.get("automatic") == "fail" or r.get("outcome") == "fail"
                     for r in matching):
                state = "fail"
            elif all(r.get("automatic") == r.get("outcome") == "pass"
                     and (r["facts"].get("repeatability") or {}).get("n", 0) > 0
                     for r in matching):
                state = "pass"
            else:
                state = "not executed"
            rows.append(dict(offer, state=state,
                             evidence_steps=[r["step"] for r in matching]))
        result.append(dict(descriptor_sha256=digest, rows=rows))
    return result


def check_readings(operations: list[dict], device: Device, *, review: dict, count: int,
                   rehearsal: bool, xyz_only: bool) -> tuple[list[dict], dict]:
    checks: list[dict] = []
    spots = [op for op in operations if op["result"] and op["result"]["workflow"] == "spot"]
    accepted = [op for op in spots if op["review_state"] == "accepted"
                and op["result"]["status"] == "completed"]
    _check(checks, "accepted readings", len(accepted) == count,
           f"{len(accepted)} accepted of {count} expected ({len(spots)} spot operations new)")
    mode = review["manifest"]["mode"]
    xyz_rows = []
    for op in accepted:
        for obs in _observations(op):
            reported = obs["backend_reported"]
            label = op["command_id"][:8]
            _check(checks, f"{label} provenance",
                   obs["provenance"] == ("simulated" if rehearsal else "physical"),
                   obs["provenance"])
            _check(checks, f"{label} mode", obs["actual_mode"] == obs["requested_mode"] == mode,
                   f"{obs['requested_mode']} -> {obs['actual_mode']}")
            xyz = (reported.get("xyz") or {}).get("values")
            _check(checks, f"{label} XYZ finite", _finite_triple(xyz), json.dumps(xyz))
            if _finite_triple(xyz):
                xyz_rows.append(xyz)
            spectrum = reported.get("spectrum")
            if device.spectrum and not xyz_only:
                wavelengths = (spectrum or {}).get("wavelengths_nm") or []
                values = (spectrum or {}).get("values") or []
                ok = len(wavelengths) == len(values) >= 2 and (
                    device.bands is None or len(values) == device.bands)
                _check(checks, f"{label} spectrum", ok,
                       f"{len(values)} bands {(spectrum or {}).get('unit')}")
            else:
                _check(checks, f"{label} no spectrum", spectrum is None,
                       "absent" if spectrum is None else "present")
            if device.native and obs["provenance"] == "physical":
                retained = "native_facts_json" in _config(obs)
                _check(checks, f"{label} native facts", retained,
                       "retained" if retained else "absent")
    return checks, _repeatability(xyz_rows)


def _repeatability(rows: list[list[float]]) -> dict:
    """Summarize finite native XYZ; Y keeps its units and x,y have unit one.

    The shared conversion owns signed chromaticity and unavailable domains.
    Statistics use the arithmetic mean and sample standard deviation; null
    means too few values or a result outside finite float range. n counts all
    XYZ rows; n_xy counts only rows accepted by the shared conversion.
    """
    from color_measurement.science import ColorDomainError, xyz_to_xyy

    if not rows:
        return {}
    ys = [row[1] for row in rows]
    chroma = []
    for row in rows:
        try:
            x, y, _ = xyz_to_xyy(row)
        except ColorDomainError:
            continue
        chroma.append((x, y))
    out = dict(n=len(rows), n_xy=len(chroma))
    for name, values in (("Y", ys), ("x", [c[0] for c in chroma]),
                         ("y", [c[1] for c in chroma])):
        for suffix, statistic, required in (("mean", statistics.mean, 1),
                                             ("sd", statistics.stdev, 2)):
            value = None
            if len(values) >= required:
                try:
                    candidate = statistic(values)
                    if math.isfinite(candidate):
                        value = candidate
                except OverflowError:
                    pass
            out[f"{name}_{suffix}"] = value
    return out


def check_calibration(operations: list[dict]) -> list[dict]:
    checks: list[dict] = []
    calibrations = [op for op in operations
                    if op["result"] and op["result"]["workflow"] == "calibrate"]
    _check(checks, "calibration operation", len(calibrations) >= 1, f"{len(calibrations)}")
    for op in calibrations:
        status = (op["result"].get("calibration") or {}).get("status")
        _check(checks, f"{op['command_id'][:8]} calibration",
               op["review_state"] == "accepted" and status == "valid",
               f"{op['review_state']}, status {status}")
    return checks


def _decoded_replies(control: dict) -> list[dict]:
    replies = []
    for reply in control.get("raw_replies") or []:
        try:
            text = bytes.fromhex(reply.get("raw_hex") or "").decode("ascii", "replace")
        except ValueError:
            text = ""
        replies.append(dict(command=reply.get("command"), text=text.strip()))
    return replies


def check_settings(operations: list[dict], *, action: str) -> tuple[list[dict], dict]:
    checks: list[dict] = []
    settings = [op for op in operations
                if op["result"] and op["result"]["workflow"] == "native_controls"]
    _check(checks, "settings operation", len(settings) == 1, f"{len(settings)}")
    if len(settings) != 1:
        return checks, {}
    op = settings[0]
    control_text = _config(op["result"]).get("native_control_json")
    _check(checks, "accepted", op["review_state"] == "accepted", op["review_state"])
    _check(checks, "native_control_json", control_text is not None, "retained")
    if control_text is None:
        return checks, {}
    control = json.loads(control_text)
    _check(checks, "not failed", control.get("failed") is False,
           f"status_code {control.get('status_code')}, problem {control.get('problem')!r}")
    if action in ("apply", "restore"):
        requested = (control.get("requested") or {}).get("present", 0)
        _check(checks, "a change was requested", requested != 0, f"present {requested}")
        _check(checks, "every change confirmed",
               control.get("confirmed") == control.get("changed"),
               f"changed {control.get('changed')}, confirmed {control.get('confirmed')}")
    facts = dict(before=control.get("before"), values=control.get("values"),
                 requested=control.get("requested"),
                 masks={key: control.get(key) for key in
                        ("changed", "attempted", "acknowledged", "confirmed")},
                 replies=[r for r in _decoded_replies(control)
                          if any(word in (r["command"] or "")
                                 for word in ("Exposure", "SyncFreq", "Sync", "D602"))])
    return checks, facts


def check_restored(facts: dict, original: dict | None, keys: tuple[str, ...]) -> list[dict]:
    checks: list[dict] = []
    values = (facts or {}).get("values") or {}
    for key in keys:
        if original is not None and key in original:
            _check(checks, f"{key} restored", values.get(key) == original[key],
                   f"now {values.get(key)!r}, originally {original[key]!r}")
    return checks


def check_cancel(operations: list[dict]) -> list[dict]:
    checks: list[dict] = []
    ordered = [op for op in operations if op["result"] is None or
               op["result"]["workflow"] == "spot"]
    completed = [op for op in ordered if op["review_state"] == "accepted"
                 and op["result"]["status"] == "completed"]
    stopped = [op for op in ordered if op not in completed]
    _check(checks, "cancelled reading published no reading", len(stopped) >= 1,
           ", ".join(f"{op['review_state']}/{(op['result'] or {}).get('status')}"
                     for op in stopped) or "none")
    _check(checks, "cancelled reading has no patches",
           all(not (op["result"] or {}).get("patches") for op in stopped), "")
    _check(checks, "following reading completed", len(completed) >= 1, f"{len(completed)}")
    if stopped and completed:
        _check(checks, "order", ordered.index(stopped[0]) < ordered.index(completed[-1]), "")
    return checks


def check_finish(review: dict) -> list[dict]:
    checks: list[dict] = []
    manifest = review["manifest"]
    _check(checks, "batch finished", manifest["status"] == "finished", manifest["status"])
    states = [op["review_state"] for op in review["operations"]]
    _check(checks, "operations authenticated", "unknown" not in states, ", ".join(states))
    _check(checks, "readings counted", review["sample_count"] >= 1, f"{review['sample_count']}")
    return checks


# -- session state ----------------------------------------------------------------------


@dataclass
class State:
    folder: Path | None = None
    seen: set[str] = field(default_factory=set)
    serial: str | None = None
    original: dict | None = None
    batch_skipped: bool = False


def _state(records: list[dict], device: Device) -> State:
    state = State()
    for record in records:
        if record.get("device") != device.key:
            continue
        if record.get("batch"):
            state.folder = Path(record["batch"])
        state.seen.update(record.get("operations") or [])
        answers = record.get("answers") or {}
        if answers.get("serial"):
            state.serial = answers["serial"]
        if record.get("step") == "settings-read" and record.get("facts"):
            state.original = (record["facts"].get("values") or None)
        if record.get("kind") == "batch":
            state.batch_skipped = record.get("outcome") == "skip"
    return state


def _batch_path(text: str) -> Path:
    text = text.strip().strip("'\"").replace("\\ ", " ")
    return Path(os.path.expanduser(text)).resolve()


def _copy_evidence(session: Path, device: Device, seq: int, step: Step, folder: Path,
                   names: list[str]) -> list[dict]:
    target = session / "evidence" / device.key / f"{seq:03d}-{step.id}"
    target.mkdir(parents=True, exist_ok=False)
    copied = []
    for name in names:
        source = folder / name
        if source.is_file() and not source.is_symlink():
            shutil.copy2(source, target / name)
            copied.append(dict(path=str((target / name).relative_to(session)),
                               sha256=_sha256(source.read_bytes())))
    return copied


class Console:
    def __init__(self, ask=input, say=print):
        self.ask, self.say = ask, say

    def field(self, prompt: str) -> str:
        return self.ask(f"  {prompt}: ").strip()

    def outcome(self, default: str) -> tuple[str, str]:
        while True:
            answer = self.ask(f"  Result [p]ass/[f]ail/[s]kip (Enter = {default}): ").strip()
            choice = {"": default, "p": "pass", "f": "fail", "s": "skip"}.get(answer.lower()[:1])
            if choice is None:
                self.say("  Type p, f or s.")
                continue
            reason = self.ask("  Reason (required): " if choice == "skip"
                              else "  Note (optional): ").strip()
            if choice == "skip" and not reason:
                self.say("  A skip needs a reason.")
                continue
            return choice, reason


def run_device(session: Path, device: Device, console: Console) -> int:
    meta = _load_session(session)
    rehearsal = bool(meta.get("rehearsal"))
    say = console.say
    policy = (meta.get("models") or {}).get(device.key) or {}
    done = {r["step"] for r in read_records(session) if r.get("device") == device.key}
    for step in steps_for(device):
        if step.id in done:
            continue
        records = read_records(session)
        state = _state(records, device)
        # Every step that reads a batch depends on the latest one, so a skipped
        # batch must not leave them checking the previous batch in its place.
        if step.kind not in ("batch", "confirm") and state.batch_skipped:
            append_record(session, dict(device=device.key, step=step.id, kind=step.kind,
                                        outcome="skip", note="its batch was skipped"))
            continue
        say(f"\n== {device.model}: {step.title} [{step.id}]")
        lines = step.lines
        if step.id == "preflight":
            lines = (*lines[:-1], "Launch: " + meta["app"]["launch"])
            if rehearsal:
                lines = ("Rehearsal only: use Demo; do not connect or operate equipment.",
                         lines[-1])
        elif step.id == "initialize" and rehearsal:
            lines = ("Choose Try Demo or Use Demo explicitly, then select " + device.model + ".",)
        for line in lines:
            say(f"  - {line}")
        if step.id == "initialize" and policy:
            floor = policy.get("floor_s")
            allowance = f"operation timeout above {floor:g} s" if floor is not None else \
                "no read-allowance floor"
            say(f"  Floor: {allowance}; opening "
                f"requires {policy.get('opening_requires') or 'nothing extra'}.")
        if step.kind == "settings" and step.action == "restore" and state.original:
            say(f"  Original values: {json.dumps(state.original, sort_keys=True)}")
        reply = console.ask("  Press Enter when done, 's' to skip, 'q' to stop: ").strip()
        if reply.lower() == "q":
            say("Stopped; rerun to resume at this step.")
            return 0
        record = dict(device=device.key, step=step.id, kind=step.kind, mode=step.mode)
        if reply.lower().startswith("s"):
            reason = reply[1:].strip() or console.field("Reason for skipping")
            append_record(session, dict(record, outcome="skip", note=reason))
            continue
        answers = {key: console.field(prompt) for key, prompt in step.fields}
        checks, facts, operations, evidence = [], {}, [], []
        if step.kind == "batch":
            folder = _batch_path(console.field("Batch folder (paste or drag)"))
            record["batch"] = str(folder)
            review = _review(folder, checks)
            if review is not None:
                offered = {m["mode"] for m in review["manifest"]["descriptor"].get("modes", [])
                           if m.get("available")}
                _check(checks, "mode offered", step.mode in offered,
                       ", ".join(sorted(offered)))
                checks += check_identity(review, device, rehearsal=rehearsal,
                                         serial=state.serial, mode=step.mode, report=meta["report"])
                if all(check["ok"] for check in checks):
                    facts["mode_inventory"] = mode_inventory(review)
                operations = [op["command_id"] for op in review["operations"]]
        elif step.kind != "confirm":
            if state.folder is None:
                _check(checks, "batch folder known", False, "no batch step recorded yet")
            else:
                review = _review(state.folder, checks)
                if review is not None:
                    new = [op for op in review["operations"]
                           if op["command_id"] not in state.seen]
                    operations = [op["command_id"] for op in new]
                    checks += check_identity(review, device, rehearsal=rehearsal,
                                             serial=state.serial, report=meta["report"])
                    if step.kind == "readings":
                        found, stats = check_readings(
                            new, device, review=review, count=step.count,
                            rehearsal=rehearsal, xyz_only=step.xyz_only)
                        checks += found
                        facts = dict(repeatability=stats, target=step.target,
                                     measured_mode=review["manifest"]["mode"],
                                     descriptor_sha256=mode_inventory(review)["descriptor_sha256"])
                    elif step.kind == "calibrate":
                        checks += check_calibration(new)
                    elif step.kind == "settings":
                        found, facts = check_settings(new, action=step.action or "read")
                        checks += found
                        if step.action == "restore":
                            requested = facts.get("requested") or {}
                            keys = tuple(k for k, v in requested.items()
                                         if k != "present" and v is not None)
                            _check(checks, "original values known", state.original is not None,
                                   "from settings-read")
                            checks += check_restored(facts, state.original, keys)
                        if facts.get("values"):
                            say("  Current values: " + json.dumps(
                                {k: v for k, v in facts["values"].items() if v is not None},
                                sort_keys=True))
                        for line in facts.get("replies") or []:
                            say(f"  Reply {line['command']}: {line['text']}")
                    elif step.kind == "cancel":
                        checks += check_cancel(new)
                    elif step.kind == "finish":
                        checks += check_finish(review)
                    names = ["batch.json"] + [
                        f"{prefix}-{command}{suffix}" for command in operations
                        for prefix, suffix in (("operation", ".json"), ("recovery", ".json"),
                                               ("diagnostic", ".log"))]
                    if step.kind == "finish":
                        names = sorted(p.name for p in state.folder.iterdir() if p.is_file())
                    evidence = _copy_evidence(session, device, len(records) + 1, step,
                                              state.folder, names)
        for check in checks:
            say(f"  {'ok  ' if check['ok'] else 'FAIL'} {check['name']}: {check['detail']}")
        if facts.get("repeatability"):
            say(f"  Repeatability: {json.dumps(facts['repeatability'])}")
        default = "pass" if all(c["ok"] for c in checks) else "fail"
        outcome, note = console.outcome(default)
        append_record(session, dict(record, outcome=outcome, note=note, answers=answers,
                                    checks=checks, facts=facts, operations=operations,
                                    evidence=evidence, automatic=default))
        if outcome == "fail" and step.gate:
            stop = console.ask("  This step gates the device. Stop this device? [Y/n]: ")
            if stop.strip().lower() != "n":
                say("Stopped this device; record what you saw and continue with another.")
                return 1
    say(f"\nGuided {device.model} steps are recorded. "
        "See the report for unexecuted modes/conditions.")
    return 0


def _review(folder: Path, checks: list) -> dict | None:
    from color_measurement.application import review_saved

    try:
        return review_saved(folder)
    except (OSError, ValueError) as error:
        _check(checks, "saved batch readable", False, f"{type(error).__name__}: {error}")
        return None


# -- start and report --------------------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _packaged_app(path):
    """Bind current application bytes and its native kit without launching either."""
    from build_desktop_app import package_root, verify_package_code

    from color_measurement.discovery import InstrumentBuild
    from color_measurement.exports import _read_regular

    app = Path(path).resolve()
    resources = package_root(app)
    metadata_raw = _read_regular(resources / "PACKAGE.json")
    metadata = json.loads(metadata_raw)
    if not isinstance(metadata, dict) or metadata.get("format") != 1:
        raise ValueError("Application metadata must use the current format")
    report_path = resources / "instrument-software" / "report.json"
    if not report_path.resolve().is_relative_to(app):
        raise ValueError("Instrument software must remain inside the application")
    raw = _read_regular(report_path)
    digest = _sha256(raw)
    if digest != metadata.get("instrument_report_sha256"):
        raise ValueError("Application instrument software identity does not match its report")
    build = InstrumentBuild.from_report(report_path)
    if build._client._report_sha256 != digest or _read_regular(report_path) != raw:
        raise ValueError("Application instrument software changed during authentication")
    inventory = verify_package_code(app, ROOT / "src" / "color_measurement",
                                    report_sha256=digest)
    if inventory != metadata.get("package_sha256"):
        raise ValueError(
            "Application package differs from the current source; use a matching build"
        )
    info = (
        plistlib.loads(_read_regular(app / "Contents" / "Info.plist"))
        if app.suffix == ".app"
        else {}
    )
    report_data = json.loads(raw)
    receipt = dict(
        backend_version=report_data["argyll_version"],
        description_helper_sha256=report_data["description_worker_build_link"]["executable_sha256"],
        session_helper_sha256=report_data["generic_worker_build_link"]["executable_sha256"],
        build_report_sha256=digest,
    )
    application = dict(
        kind="packaged",
        path=str(app),
        bundle_id=info.get("CFBundleIdentifier"),
        metadata_sha256=_sha256(metadata_raw),
        package_sha256=inventory,
        identity=_sha256(json.dumps(inventory, sort_keys=True).encode()),
        launch=shlex.join(
            ["/usr/bin/open", "-n", str(app)]
            if app.suffix == ".app"
            else [str(app / (app.name + (".exe" if os.name == "nt" else "")))]
        ),
    )
    report = dict(
        path=str(report_path),
        sha256=digest,
        binding_receipt=receipt,
        catalog_sha256=_sha256(build.catalog_json().encode()),
    )
    return application, report, build



def start(args) -> int:
    rehearsal = bool(args.rehearsal)
    if args.app is None and not rehearsal:
        raise ValueError("A physical session needs --app (the packaged application)")
    models, report = {}, None
    application = dict(kind="checkout-rehearsal", path=str(ROOT), launch=LAUNCH)
    if args.app is not None:
        application, report, build = _packaged_app(args.app)
        for key, device in DEVICES.items():
            policy = build.model_policy(device.symbol)
            models[key] = dict(display_label=policy["display_label"],
                               floor_s=policy["binding_policy"][
                                   "all_modes_read_allowance_floor_seconds"],
                               opening_requires=policy["opening_requires"],
                               initialization_refusal=policy["initialization_refusal"])
    status = _git("status", "--porcelain")
    session = Path(args.root).resolve() / args.name
    session.parent.mkdir(parents=True, exist_ok=True)
    session.mkdir(mode=0o700)
    meta = dict(
        schema=2, name=args.name, operator=args.operator, started_at=_now(),
        rehearsal=rehearsal, host=dict(system=platform.platform(), machine=platform.machine(),
                                       python=sys.version.split()[0]),
        app=application, runner=dict(commit=_git("rev-parse", "HEAD"), dirty=status.splitlines()),
        report=report, models=models, safety=list(SAFETY),
    )
    with open(session / "session.json", "x") as stream:
        json.dump(meta, stream, indent=2, sort_keys=True)
    (session / "OPERATOR.md").write_text(_operator_sheet(meta, session))
    print(f"Session {session}")
    if status:
        print("Uncommitted files in the checkout (recorded):\n  " + "\n  ".join(
            status.splitlines()))
    return 0


def _operator_sheet(meta: dict, session: Path) -> str:
    folder = shlex.quote(str(session))
    lines = [f"# Bench session {meta['name']}", "",
             f"Operator {meta['operator']}, started {meta['started_at']}"
             + (" — REHEARSAL, not evidence" if meta["rehearsal"] else ""), "",
             f"1. Launch exactly the selected app: `{meta['app']['launch']}`.",
             "2. " + ("Connect instrument > List connected instruments. The app's instrument "
                       "software is already configured; do not select a report."
                       if meta["app"]["kind"] == "packaged" else
                       "Choose Try Demo or Use Demo explicitly. This is rehearsal only."),
             f"3. Per device, in a second terminal in `{ROOT}`, run its command:", "",
             *(f"   - {device.model}: `uv run --locked python tools/bench_session.py "
               f"run --session {folder} --device {key}`"
               for key, device in DEVICES.items()), "",
             "4. At the end, from the same folder: `uv run --locked python "
             f"tools/bench_session.py report --session {folder}`.", "",
             "The selected app's package identity and runner checkout are recorded separately. "
             "Saved native references must match the bundled report and both workers. "
             "These references do not attest which Python/UI process wrote the batch.",
             "", "## Safety", ""]
    lines += [f"- {rule}" for rule in meta["safety"]]
    lines += ["", "## Floors (operation timeout must exceed)", ""]
    for policy in (meta["models"] or {}).values():
        floor = (f"{policy['floor_s']:g} s" if policy["floor_s"] is not None
                 else "no read-allowance floor")
        lines.append(f"- {policy['display_label']}: {floor}"
                     + (f"; opening requires {policy['opening_requires']}"
                        if policy["opening_requires"] else ""))
    lines += ["", "Bring: white tile (i1Pro 3), a display showing white and dark grey "
              "patches, a stable lamp for ambient, the PR irradiance accessory if any."]
    return "\n".join(lines) + "\n"


def report(args) -> int:
    session = Path(args.session).resolve()
    meta = _load_session(session)
    records = read_records(session)
    latest: dict[tuple[str, str], dict] = {}
    for record in records:
        latest[(record["device"], record["step"])] = record
    summary: dict = dict(session=meta, records=len(records), devices={}, mode_coverage={})
    md = [f"# Bench session {meta['name']}" + (" (REHEARSAL)" if meta["rehearsal"] else ""),
          "", f"Operator {meta['operator']}; app {meta['app']['kind']} "
          + f"`{meta['app'].get('identity', meta['runner']['commit'])[:16]}`; report "
          + (f"`{meta['report']['sha256'][:16]}…`" if meta["report"] else "none") + ".", ""]
    targets: dict[str, list] = {}
    for key, device in DEVICES.items():
        steps = steps_for(device)
        rows = [latest.get((key, step.id)) for step in steps]
        if not any(rows):
            continue
        md += [f"## {device.model}", "", "| Step | Outcome | Automatic | Failed checks | Note |",
               "| --- | --- | --- | --- | --- |"]
        device_summary = []
        for step, row in zip(steps, rows):
            if row is None:
                md.append(f"| {step.id} | not run | | | |")
                device_summary.append(dict(step=step.id, outcome="not run"))
                continue
            failed = [f"{c['name']}: {c['detail']}" for c in row.get("checks") or [] if not c["ok"]]
            cells = [step.id, row["outcome"], row.get("automatic") or "", "; ".join(failed),
                     row.get("note") or ""]
            md.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ")
                                         for c in cells) + " |")
            device_summary.append(dict(step=step.id, outcome=row["outcome"],
                                       automatic=row.get("automatic"), failed=failed,
                                       answers=row.get("answers"), facts=row.get("facts")))
            stats = (row.get("facts") or {}).get("repeatability")
            if stats and step.target:
                targets.setdefault(step.target, []).append((device.model, step.id, stats))
        summary["devices"][key] = device_summary
        md.append("")
        for row in rows:
            if row and (row.get("facts") or {}).get("repeatability"):
                md.append(f"- {row['step']} repeatability: " + ", ".join(
                    (f"{name} {value:.6g}" if value is not None else f"{name} unavailable")
                    for name, value in
                    sorted(row["facts"]["repeatability"].items())))
            if row and (row.get("facts") or {}).get("values"):
                md.append(f"- {row['step']} values: "
                          + json.dumps({k: v for k, v in row["facts"]["values"].items()
                                        if v is not None}, sort_keys=True))
            if row and (row.get("facts") or {}).get("replies"):
                md += [f"- {row['step']} reply {r['command']}: `{r['text']}`"
                       for r in row["facts"]["replies"]]
            if row and row.get("answers"):
                md.append(f"- {row['step']} answers: " + json.dumps(row["answers"]))
        md.append("")
        coverage = mode_coverage([r for r in latest.values() if r["device"] == key])
        summary["mode_coverage"][key] = coverage
        md += ["### Descriptor mode coverage", "",
               "Core guided steps do not establish all-mode coverage. An unspecified/default "
               "condition does not cover an explicit M0, M1 or M2 offer. Pass means the retained "
               "reading checks passed, not that accessories or optical accuracy were qualified.",
               ""]
        if not coverage:
            md += ["No valid descriptor inventory was captured; "
                   "mode coverage is not established.", ""]
        for inventory in coverage:
            md += [f"Descriptor `{inventory['descriptor_sha256']}`", "",
                   "| Mode | Condition | Workflow | Reading check | Setup from descriptor |",
                   "| --- | --- | --- | --- | --- |"]
            for offer in inventory["rows"]:
                setup = [offer["operator_setup"]] if offer["operator_setup"] else []
                setup += offer["calibration_setup"]
                detail = "; ".join(item["text"] for item in setup) or "Not specified"
                if not offer["available"]:
                    detail = offer["unavailable_reason"] or detail
                cells = [offer["mode"], offer["condition"] or "unspecified/default",
                         offer["workflow"], offer["state"], detail]
                md.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ")
                                             for c in cells) + " |")
            md.append("")
    if targets:
        md += ["## Exploratory comparison (no pass or fail)", ""]
        for target, items in targets.items():
            for model, step_id, stats in items:
                y_value = (f"{stats['Y_mean']:.6g}" if stats["Y_mean"] is not None
                           else "unavailable")
                spread = (f"sd {stats['Y_sd']:.3g}" if stats.get("Y_sd") is not None
                          else "one reading" if stats["n"] == 1 else "sd unavailable")
                xy = (f"{stats['x_mean']:.5f},{stats['y_mean']:.5f}"
                      if stats.get("x_mean") is not None and stats.get("y_mean") is not None
                      else "unavailable")
                counts = f" (n={stats['n_xy']}/{stats['n']})" if "n_xy" in stats else ""
                md.append(f"- {target}: {model} ({step_id}) Y {y_value} ({spread}), "
                          f"x,y {xy}{counts}")
        md.append("")
    (session / "summary.md").write_text("\n".join(md))
    (session / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    print(session / "summary.md")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    begin = commands.add_parser("start",
                                help="Bind the packaged app and authenticate its native kit")
    begin.add_argument("--name", required=True)
    begin.add_argument("--operator", required=True)
    begin.add_argument("--app", help="Packaged .app with authenticated instrument software")
    begin.add_argument("--root", default=str(ROOT / "local" / "bench"))
    begin.add_argument("--rehearsal", action="store_true",
                       help="Admit simulated (Demo) batches; the session is not evidence")
    proceed = commands.add_parser("run", help="Guide and record one device")
    proceed.add_argument("--session", required=True)
    proceed.add_argument("--device", required=True, choices=sorted(DEVICES))
    summarize = commands.add_parser("report", help="Write summary.md and summary.json")
    summarize.add_argument("--session", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "start":
            return start(args)
        if args.command == "run":
            return run_device(Path(args.session).resolve(), DEVICES[args.device], Console())
        return report(args)
    except (OSError, ValueError) as error:
        print(f"bench_session: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
