"""Exercise the current generic frontend Demo without MATLAB, Argyll or hardware.

Run: uv run --locked python examples/generic_frontend.py
This demonstration explicitly accepts synthetic requests. A UI must obtain the
operator's current decision instead. Acquisition and waiting stay in Python.
"""

import json
import time

from color_measurement.demo import demo_catalog_json, demo_frontends
from color_measurement.operation_records import result_from_json


def main():
    print(demo_catalog_json())
    front = next(f for f in demo_frontends()
                 if json.loads(f.describe_json())["device_id"] == "demo:i1pro3")
    snapshot = json.loads(front.describe_json())
    device_id = snapshot["device_id"]
    request = {
        "schema_version": 1,
        "device_id": device_id,
        "capability_id": snapshot["capability_id"],
        "mode": "reflective_strip",
        "workflow": "strip",
        "patch_ids": ["A1", "A2", "A3"],
        "options": {},
        "layout": {},
    }
    operation = front.prepare(json.dumps(request))
    deadline = time.monotonic() + 10
    try:
        operation.dispatch()
        while time.monotonic() < deadline:
            text = operation.poll_event_json()
            if not text:
                time.sleep(0.001)  # A GUI schedules its next poll instead of sleeping.
                continue
            event = json.loads(text)
            print(text)
            if event["kind"] == "instruction_required":
                instruction = event["payload"]
                response = "accept"
                operation.respond(operation.operation_id, instruction["request_id"], response)
            if event["kind"] == "terminal":
                result = result_from_json(operation.result_json())
                if result.status != "completed" or len(result.patches) != 3:
                    raise RuntimeError("Demo strip must complete with three retained patches")
                if result.patches[0].patch_id != "A1":
                    raise RuntimeError("Demo strip must preserve the requested first patch ID")
                print("PASS: three synthetic patches retained through the generic frontend")
                return
        raise RuntimeError("Demo example timed out")
    finally:
        operation.close()


if __name__ == "__main__":
    main()
