"""Run one simulated scalar workflow. No device enumeration or instrument I/O."""

import argparse
import json
import time
from pathlib import Path

from color_measurement.demo import demo_frontends
from color_measurement.operation_exports import save
from color_measurement.operation_records import result_from_json, result_to_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New JSON or MAT path; no overwrite")
    parser.add_argument("--format", choices=("json", "mat"), default="json")
    args = parser.parse_args()
    front = next(f for f in demo_frontends() if any(
        w["workflow"] == "refresh_frequency" and w["available"]
        for m in json.loads(f.describe_json())["modes"] if m["mode"] == "emissive_spot"
        for w in m["workflows"]))
    descriptor = json.loads(front.describe_json())
    request = {"schema_version": 1, "device_id": descriptor["device_id"],
        "capability_id": descriptor["capability_id"], "mode": "emissive_spot",
        "workflow": "refresh_frequency", "patch_ids": ["RATE-1"], "options": {},
        "layout": {"stimulus_id": "DEMO-WHITE",
                   "stimulus_description": "Synthetic external display stimulus"},
        "event_contract": "retained_patch_v1"}
    operation = front.prepare(json.dumps(request), response_timeout=5)
    try:
        operation.dispatch()
        deadline = time.monotonic() + 15
        while True:
            while raw := operation.poll_event_json():
                event = json.loads(raw)
                if event["kind"] == "instruction_required":
                    operation.respond(operation.operation_id,
                                      event["payload"]["request_id"], "accept")
            if operation.done:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Simulated refresh operation did not stop")
            time.sleep(0.002)
        result = result_from_json(operation.result_json())
        if result.status != "completed":
            raise RuntimeError(result.error)
        if args.output is not None:
            save(result, args.output, format=args.format)
        print(result_to_json(result), end="")
    finally:
        operation.close()


if __name__ == "__main__":
    main()
