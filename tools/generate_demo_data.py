"""Reduce existing synthetic captures to packaged demo I/O templates. No helpers run."""

import argparse
import hashlib
import json
from pathlib import Path

from color_measurement.mode_acceptance import (
    baseline_native_mode,
    validate_baseline_mode_checks,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/fixtures/generic_frontend"
OUTPUT = ROOT / "src/color_measurement/data/demo.json"
PATCH_EVENTS = {"result", "strip_patch", "xy_patch", "chart_patch"}


def _setup(events):
    """Everything the helper said before the reading."""
    return [event for event in events
            if event["event"] not in PATCH_EVENTS | {"connected", "terminal", "display_type"}]


def _route(request, result):
    """The native I/O of one completed capture: the connection, everything the
    helper said before the reading, and one patch event per patch."""
    raw = [json.loads(patch["observation"]["raw_backend_output"])["events"]
           for patch in result["patches"]]
    return {
        "connected": next(event for event in raw[0] if event["event"] == "connected"),
        "setup": _setup(raw[0]),
        "patches": [next(event for event in events if event["event"] in PATCH_EVENTS)
                    for events in raw],
        "example_layout": request["layout"],
    }


def generate():
    sources = {}

    def read(path):
        content = path.read_bytes()
        sources[path.relative_to(ROOT).as_posix()] = hashlib.sha256(content).hexdigest()
        return json.loads(content)

    index = read(SOURCE / "index.json")
    personas = {}
    for name in sorted(index["personas"]):
        descriptor = read(SOURCE / name / "describe.json")
        if descriptor["provenance"] != "simulated":
            raise SystemExit(f"{name}: demo source must be simulated")
        checks = validate_baseline_mode_checks(descriptor["baseline_mode_checks"])
        for mode in descriptor["modes"]:
            expected = baseline_native_mode(checks, mode["mode"], spectral=descriptor["spectral"])
            if mode["baseline_native_mode"] != expected:
                raise SystemExit(f"{name}: descriptor contradicts its baseline mode checks")
        persona = {"model": descriptor["device"]["model"], "capabilities": {
            key: descriptor[key] for key in
            ("mode_capabilities", "trigger_capabilities", "filter_capabilities")},
            "display_types": descriptor["display_types"],
            "baseline_mode_checks": checks,
            "routes": {}, "calibrations": {}, "misplaced": {}, "retained": {}}
        for path in sorted((SOURCE / name).glob("*.json")):
            # Scenarios are outcomes a client must handle, not the persona's routes.
            if (path.name in {"describe.json", "refusals.json", "calibration_retained.json"}
                    or path.stem.endswith(".retained")
                    or path.name.startswith("scenario.")):
                continue
            capture = read(path)
            request, result = capture["request"], capture["result"]
            # Default templates cannot come from an explicit correction or
            # calibration selection: the latter also changes startup prompts.
            if (result["status"] != "completed" or "correction" in request["options"]
                    or request["options"].get("display_type") is not None):
                continue
            mode = request["mode"]
            # Colour route templates contain ipatch events. Non-photometric outcomes have
            # no such event and must never overwrite that mode's colour template.
            if any(p["observation"].get("record_kind") in {"scalar", "timing"}
                   for p in result["patches"]):
                continue
            if request["workflow"] in {"mode_snapshot", "stored_refresh"}:
                continue
            if request["workflow"] == "calibrate":
                persona["calibrations"][mode] = json.loads(
                    dict(result["configuration"])["calibration_events_json"])
                continue
            persona["routes"][mode] = _route(request, result)
        # What a reading says once its mode has been calibrated in the demo: the
        # same route from a start whose driver restored that calibration.
        kept = SOURCE / name / "calibration_retained.json"
        if kept.is_file():
            persona["retained"] = {mode: _setup(events)
                                   for mode, events in read(kept)["routes"].items()
                                   if mode in persona["routes"]}
        # A recorded run with the sensor, dial or adapter in the wrong place, which
        # the demo replays only when asked. Where a route has more than one, the
        # one that asks most shows most (the i1Pro 3's tile, then its head).
        for path in sorted((SOURCE / name).glob("scenario.*.json")):
            capture = read(path)
            request, result = capture["request"], capture["result"]
            asked = sum(event["kind"] == "instruction_required"
                        and event["payload"]["kind"] == "sensor_position"
                        for event in capture["events"])
            if result["status"] != "completed" or not asked:
                continue
            kept = persona["misplaced"].get(request["mode"])
            if kept is None or asked > kept["asked"]:
                persona["misplaced"][request["mode"]] = {
                    **_route(request, result), "asked": asked,
                    "scenario": path.stem.removeprefix("scenario.")}
        # Everything below reads the default routes' connections; a persona
        # whose captures all failed has no binding to agree or disagree with.
        if not persona["routes"]:
            raise SystemExit(f"{name}: no completed default route recorded")
        # The binding's matrix policy, as its recorded connections published it,
        # so the demo's descriptor states the same requirements as the live one.
        thresholds = {route["connected"].get("ambient_matrix_threshold")
                      for route in persona["routes"].values()}
        if len(thresholds) > 1:
            raise SystemExit(f"{name}: recorded bindings disagree on the matrix threshold")
        persona["ambient_matrix_threshold"] = thresholds.pop()
        policies = [route["connected"]["strip_policies"] for route in persona["routes"].values()]
        if any(p != policies[0] for p in policies):
            raise SystemExit(f"{name}: recorded bindings disagree on strip policies")
        persona["strip_policies"] = policies[0]
        for route in (*persona["routes"].values(), *persona["misplaced"].values()):
            if route["connected"]["baseline_mode_checks"] != persona["baseline_mode_checks"]:
                raise SystemExit(f"{name}: recorded bindings disagree on baseline mode checks")
        personas[name] = persona
    return json.dumps({"schema_version": 1, "provenance": "simulated",
                       "source_sha256": sources, "personas": personas},
                      indent=2, sort_keys=True) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = generate()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text() != content:
            raise SystemExit("Demo data differs; run tools/generate_demo_data.py")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(content)


if __name__ == "__main__":
    main()
