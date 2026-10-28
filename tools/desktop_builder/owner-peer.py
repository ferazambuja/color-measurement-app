"""JSON test peer for the actual renderer and software-only DesktopSession.

This actor confirms software-only instructions for Demo or authenticated
substituted Argyll workers. It does not run AppKit, enumerate hardware, or
establish native accessibility or physical validation.
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from uuid import uuid4

from color_measurement import desktop_session
from color_measurement.application import review_saved
from color_measurement.demo import demo_frontends

setup_worker = None
direct_release = None
direct_port = None
direct_port_token = None

root = Path(__file__).resolve().parents[2]
if Path(desktop_session.__file__).resolve() != root / "src/color_measurement/desktop_session.py":
    raise RuntimeError("Renderer test peer must import its own source checkout")

native_report = os.environ.get("CM_RENDERER_NATIVE_REPORT")
native_scenario = os.environ.get("CM_RENDERER_NATIVE_SCENARIO")
if native_report is None:
    if native_scenario is not None:
        raise ValueError("A native renderer scenario requires its authenticated build report")
    session = desktop_session.DesktopSession(demo_frontends())
else:
    personas = {
        "cr250_native": {"cr250", "cr250_native_error"},
        "cr100_native": {"cr100"},
        "pr655_native": {"pr655"},
    }
    chart = native_scenario == "i1pro3"
    valid_actor = (
        os.environ.get("CM_CHART_PERSONA") == "i1pro3"
        and os.environ.get("CM_CHART_FIXTURE") == "startup"
    ) if chart else (
        native_scenario in personas
        and os.environ.get("CM_COLORIMETER_FIXTURE") in personas[native_scenario]
    )
    if not native_report or not valid_actor:
        raise ValueError("Select one matching authenticated description and acquisition actor")
    setup_worker = ThreadPoolExecutor(max_workers=1)
    session = desktop_session.DesktopSession([], setup_executor=setup_worker)
    try:
        session.select_build_report(
            native_report, expected_setup_generation=0, simulated=True,
            source_root=root, scenario=native_scenario,
            simulated_session="chart" if chart else "generic",
        )
    except BaseException:
        setup_worker.shutdown(wait=True)
        raise


def command(action, payload):
    state = session.batch.snapshot()
    return session.request("command", dict(
        command_id=str(uuid4()), generation=state["generation"],
        batch_id=state["batch_id"], action=action, payload=payload,
    ))


def respond():
    if session.batch is None:
        return
    state = session.batch.snapshot()
    instruction = state["instructions"]
    if instruction:
        response = next(value for value in instruction["allowed_responses"] if value != "cancel")
        command("respond", dict(
            operation_id=state["operation_id"], request_id=instruction["request_id"],
            response=response,
        ))


with TemporaryDirectory(prefix="cm-renderer-owner-") as temporary:
    try:
        for line in sys.stdin:
            request = json.loads(line)
            try:
                respond()
                method, params = request["method"], request["params"]
                if method == "choose_direct_port":
                    direct_port_token = str(uuid4())
                    value = dict(port_token=direct_port_token, label="Scripted serial connection",
                                 setup_generation=params["setup_generation"])
                elif method == "connect_direct_serial":
                    # Reuse the protocol tests' transcript and clock. No OS port may open.
                    from color_measurement import serial_instruments
                    tests = Path(desktop_session.__file__).resolve().parents[2] / "tests"
                    sys.path.insert(0, str(tests))
                    from test_serial_instruments import (
                        ScriptedPort,
                        settings_script,
                        shutdown,
                        startup,
                    )
                    from test_serial_transport import Clock

                    def refuse_os_port(*args, **kwargs):
                        raise AssertionError("OS serial opening is forbidden in renderer controls")

                    serial_instruments.open_serial_port = refuse_os_port
                    if params["port_token"] != direct_port_token or direct_port is not None:
                        raise ValueError("Choose one scripted port for one initialization")
                    model = params["model"]
                    direct_port = ScriptedPort(
                        startup(model) + settings_script(model) + shutdown(model)
                    )
                    direct_release, entered = Event(), Event()
                    read = direct_port.read

                    def held_read(size):
                        if len(direct_port.commands) == 1:
                            entered.set()
                            if not direct_release.wait(10):
                                raise RuntimeError(
                                    "Renderer control did not release its scripted port"
                                )
                        return read(size)

                    direct_port.read = held_read
                    if setup_worker is None:
                        setup_worker = ThreadPoolExecutor(max_workers=1)
                    native_params = {
                        key: value for key, value in params.items() if key != "port_token"
                    }
                    value = session.connect_direct_serial(
                        "renderer-scripted-port", transport=direct_port, clock=Clock(),
                        executor=setup_worker, **native_params,
                    )
                    if value["state"] != "pending" or not entered.wait(2):
                        raise RuntimeError("Actual direct setup did not reach its held native read")
                elif method == "test_release_direct_setup":
                    direct_release.set()
                    value = session._equipment_setup._future.result(timeout=5)
                elif method == "test_direct_setup_receipt":
                    value = dict(
                        command_count=len(direct_port.commands),
                        port_closed=not direct_port.is_open,
                        remaining_exchanges=len(direct_port.script),
                    )
                elif method == "create_batch":
                    value = session.create(
                        Path(temporary) / f"batch-{uuid4()}", params["name"], params["notes"],
                        selection_id=params["selection_id"], mode=params["mode"],
                        options=params["options"],
                    )
                elif method == "preload_demo":
                    value = session.preload_demo(
                        batch_id=params["batch_id"], generation=params["generation"],
                        count=params["count"],
                    )
                elif method == "export_analysis":
                    value = session.export_analysis(Path(temporary) / "analysis.json", **params)
                elif method == "test_export_receipt":
                    value = dict(payload=(Path(temporary) / "retried-analysis.json").read_text())
                elif method == "retry_analysis_export":
                    value = session.retry_analysis_export(
                        Path(temporary) / "retried-analysis.json", **params,
                    )
                elif method == "open_record":
                    from color_measurement import exports, operation_exports
                    from color_measurement.operation_records import result_from_json

                    state = session.batch.snapshot()
                    entry = next(item for item in state["operations"] if item["accepted"])
                    result = result_from_json((Path(state["folder"]) / entry["path"]).read_text())
                    format = params["format"]
                    source = Path(temporary) / "original-record"
                    if format.startswith("operation_"):
                        serialized = (operation_exports.to_mat(result) if format.endswith("mat")
                                      else (Path(state["folder"]) / entry["path"]).read_bytes())
                    else:
                        observation = result.patches[0].observation
                        serialized = (exports.to_mat(observation) if format.endswith("mat") else
                                      exports.to_csv(observation).encode() if format.endswith("csv")
                                      else exports.to_json(observation).encode())
                    source.write_bytes(serialized)
                    value = session.open_record(
                        source, format=format, expected_binding_id=params["binding_id"],
                        expected_generation=params["generation"],
                    )
                elif method == "export_record":
                    target = Path(temporary) / ("original-export." + params["format"])
                    value = session.export_record(target, **params)
                elif method == "retry_record_export":
                    value = session.retry_record_export(
                        Path(temporary) / "retried-original.json", **params,
                    )
                elif method == "test_native_export_receipt":
                    value = dict(payload=(Path(temporary) / "retried-original.json").read_text())
                elif method == "test_activity_record":
                    from dataclasses import replace

                    from color_measurement import exports
                    from color_measurement.operation_records import result_from_json, result_to_json

                    state = session.batch.snapshot()
                    entry = next(item for item in state["operations"] if item["accepted"])
                    result = result_from_json((Path(state["folder"]) / entry["path"]).read_text())
                    source = Path(temporary) / "scripted-activity.json"
                    if params["observation_only"]:
                        source.write_text(exports.to_json(result.patches[0].observation))
                        format = "observation_json"
                    else:
                        failed = params["status"] == "failed"
                        result = replace(
                            result, status=params["status"],
                            error="Scripted native failure" if failed else
                            "Scripted cancellation" if params["status"] == "cancelled" else None,
                            error_stage="read_sample" if failed else None,
                            error_native_code=-1 if failed else None,
                            patches=() if params["zero_readings"] else result.patches,
                        )
                        source.write_text(result_to_json(result))
                        format = "operation_json"
                    binding = session.snapshot()["dataset_binding"]
                    value = session.open_record(
                        source, format=format, expected_binding_id=binding["id"],
                        expected_generation=binding["generation"],
                    )
                elif method == "open_saved":
                    value = session.open_saved(
                        session.batch.snapshot()["folder"],
                        expected_binding_id=params["binding_id"],
                        expected_generation=params["generation"],
                    )
                elif method == "activate_live":
                    value = session.activate_live(
                        expected_binding_id=params["binding_id"],
                        expected_generation=params["generation"],
                    )
                elif method == "review":
                    value = review_saved(session.batch.snapshot()["folder"])
                else:
                    value = session.request(method, params)
                reply = dict(id=request["id"], ok=True, value=value)
            except Exception as error:
                reply = dict(id=request["id"], ok=False, error=str(error))
            print(json.dumps(reply, ensure_ascii=False, allow_nan=False), flush=True)
    finally:
        if direct_release is not None:
            direct_release.set()
        if setup_worker is not None:
            setup_worker.shutdown(wait=True)
        if session.batch is not None:
            state = session.batch.snapshot()
            active = next((item for item in state["operations"]
                           if item["operation_id"] == state["operation_id"]), None)
            if active:
                command("cancel", {"command_id": active["command_id"]})
            deadline = time.monotonic() + 10
            while (session.batch.snapshot()["phase"] in {"starting", "active", "saving"}
                   or session.batch.snapshot()["companions"]["saving"]):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Synthetic renderer owner did not stop before cleanup")
                time.sleep(.01)
            summary = session.batch.discard_summary()
            state = session.batch.snapshot()
            outcome = session.batch.command(
                str(uuid4()), state["generation"], "close",
                {"discard_token": summary["token"]}, batch_id=state["batch_id"],
            )
            if outcome["admission"] != "admitted" or outcome["state"] != "completed":
                raise RuntimeError("Synthetic owner refused cleanup: " + str(outcome["reason"]))
        if direct_port is not None:
            session.close_direct_sources()
