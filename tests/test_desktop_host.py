"""Portable host protocol, concurrency and real Qt boundaries without equipment."""

import json
import math
import sys
from copy import deepcopy
from uuid import uuid4

import pytest

from color_measurement.desktop import (
    _bridge_document,
    _bridge_request,
    _close_owner,
    _discard_text,
    _failure_alert,
    _failure_text,
    _instruction_alert,
    _instruction_command,
    _instruction_text,
    _reply_script,
    _require_completed_close,
    _unconfirmed_shutdown_text,
)


def bridge_request(tmp_path, body, **changes):
    workspace = tmp_path / "index.html"
    context = dict(file_url=True, document_path=workspace, workspace=workspace)
    _bridge_document(**(context | changes))
    return _bridge_request(body)


def test_deliberate_serial_chooser_reports_actual_optional_import_failure(monkeypatch):
    import builtins

    from color_measurement.desktop import _choose_serial_port

    original = builtins.__import__
    attempts = []

    def unavailable(name, *args, **kwargs):
        if name == "serial.tools":
            attempts.append(name)
            raise ModuleNotFoundError("Controlled missing serial.tools")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    with pytest.raises(ImportError, match="Direct serial instruments require pySerial"):
        _choose_serial_port(object())
    assert attempts == ["serial.tools"]


def test_local_main_document_accepts_only_the_closed_bridge_envelope(tmp_path):
    body = dict(id="request", method="snapshot", params={})
    assert bridge_request(tmp_path, body) == ("request", "snapshot", {})
    before = deepcopy(body)
    for changes in (
        {"file_url": False},
        {"document_path": tmp_path / "outside" / "index.html"},
    ):
        with pytest.raises(ValueError, match="bundled workspace document"):
            bridge_request(tmp_path, body, **changes)
    assert body == before


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        "request",
        {"id": "request", "method": "snapshot"},
        {"id": "request", "method": "snapshot", "params": {}, "extra": True},
        {"id": 4, "method": "snapshot", "params": {}},
        {"id": "", "method": "snapshot", "params": {}},
        {"id": "request", "method": 4, "params": {}},
        {"id": "request", "method": "", "params": {}},
        {"id": "request", "method": "snapshot", "params": []},
    ],
)
def test_invalid_envelopes_are_refused_before_native_actions(tmp_path, body):
    with pytest.raises(ValueError, match="declared bridge envelope"):
        bridge_request(tmp_path, body)


def test_bridge_limits_are_bytes_and_request_identity_has_its_own_bound(tmp_path):
    body = dict(id="r" * 64, method="command", params={"text": ""})
    overhead = len(json.dumps(body).encode("utf-8"))
    body["params"]["text"] = "x" * (132096 - overhead)
    assert bridge_request(tmp_path, body)[0] == "r" * 64
    body["params"]["text"] += "x"
    with pytest.raises(ValueError, match="size limit"):
        bridge_request(tmp_path, body)
    with pytest.raises(ValueError, match="size limit"):
        bridge_request(tmp_path, dict(id="r" * 65, method="snapshot", params={}))


@pytest.mark.parametrize("value", [math.nan, math.inf, object()])
def test_bridge_refuses_values_without_finite_json_encoding(tmp_path, value):
    with pytest.raises(ValueError, match="finite JSON"):
        bridge_request(tmp_path, dict(id="request", method="command", params={"value": value}))


def parse_reply(script):
    prefix, suffix = "window.ownerReply(", "); null"
    assert script.startswith(prefix) and script.endswith(suffix)
    return json.loads(script[len(prefix) : -len(suffix)])


def test_reply_keeps_identity_and_original_owner_failure():
    reason = "Cancel and confirm software stop before closing"
    script, failure = _reply_script("request", error=ValueError(reason))
    assert parse_reply(script) == dict(id="request", ok=False, value=None, error=reason)
    assert failure is None
    assert parse_reply(_reply_script("request", {"generation": 2})[0])["value"] == {"generation": 2}


@pytest.mark.parametrize("value", [math.nan, math.inf, object()])
def test_unserializable_reply_becomes_an_explicit_same_identity_error(value):
    script, failure = _reply_script("request", value)
    reply = parse_reply(script)
    assert reply == dict(id="request", ok=False, value=None, error=failure)
    assert "could not be serialized" in failure


@pytest.mark.parametrize("error", [StopIteration(), KeyboardInterrupt(), SystemExit()])
def test_empty_failures_have_nonempty_control_or_exception_names(error):
    assert _failure_text(error) == type(error).__name__
    assert parse_reply(_reply_script("request", error=error)[0])["error"] == type(error).__name__


def instruction_state():
    return dict(
        operation_id="operation",
        instructions=dict(
            operation_id="operation", request_id="prompt", allowed_responses=["accept"]
        ),
        operations=[dict(operation_id="operation", command_id="command")],
    )


def test_instruction_actions_keep_exact_request_and_retained_command_identity():
    state = instruction_state()
    before = deepcopy(state)
    assert _instruction_command(state, ("operation", "prompt"), "accept") == (
        "respond",
        dict(operation_id="operation", request_id="prompt", response="accept"),
    )
    assert _instruction_command(state, ("operation", "prompt"), None) == (
        "cancel",
        dict(command_id="command"),
    )
    assert state == before


@pytest.mark.parametrize("accepted", ["accept", None])
def test_changed_operation_or_request_cannot_receive_native_instruction_response(accepted):
    for key in (("another operation", "prompt"), ("operation", "another prompt")):
        with pytest.raises(ValueError, match="no longer current"):
            _instruction_command(instruction_state(), key, accepted)
    state = instruction_state()
    state["instructions"] = None
    with pytest.raises(ValueError, match="no longer current"):
        _instruction_command(state, ("operation", "prompt"), accepted)


def test_missing_retained_cancel_identity_reports_a_reason_instead_of_stopiteration():
    state = instruction_state()
    state["operations"] = []
    with pytest.raises(ValueError, match="no retained command"):
        _instruction_command(state, ("operation", "prompt"), None)


@pytest.mark.parametrize(
    "changed",
    ["app.js", "reference-data.js", "workspace-manifest.json", "nominal-chart-guides.json"],
)
def test_stale_assets_refuse_launch_before_webview_or_owner_creation(
    tmp_path, monkeypatch, changed
):
    import builtins
    import shutil
    from pathlib import Path

    from color_measurement import desktop, desktop_assets_manifest, desktop_native

    monkeypatch.setattr(desktop_native, "startup_failure", lambda reason: None)
    original = Path(desktop.__file__).with_name("desktop_assets")
    # Mirror the checkout layout: the renderer sources sit beside the builder,
    # not in the package, and a launch from a checkout authenticates them too.
    package = tmp_path / "src" / "color_measurement"
    assets = package / "desktop_assets"
    shutil.copytree(original, assets)
    for name in ("science.py", "presentation.py"):
        shutil.copyfile(original.parent / name, package / name)
    sources = tmp_path / "tools" / "desktop_builder" / "renderer"
    shutil.copytree(desktop_assets_manifest.renderer_sources(original), sources)
    for name in ("compiler-identity.json", "package-lock.json"):
        origin = desktop_assets_manifest.renderer_sources(original).parent / name
        shutil.copyfile(origin, sources.parent / name)
    path = (sources if changed == "app.js" else assets) / changed
    path.write_bytes(
        path.read_bytes()
        + (b"\n " if changed == "nominal-chart-guides.json" else b"\nchanged startup bytes\n")
    )
    monkeypatch.setattr(desktop, "__file__", str(package / "desktop.py"))

    import_module = builtins.__import__

    def refuse_native(name, *args, **kwargs):
        if name == "PySide6.QtWebEngineCore":
            pytest.fail("Loaded the WebView before stale assets were refused")
        if name == "PySide6":
            raise ImportError("No native framework in this focused boundary test")
        return import_module(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse_native)
    with pytest.raises(SystemExit, match="desktop assets"):
        desktop.main()


def test_portable_main_does_not_refuse_linux_before_loading_host(monkeypatch):
    import sys
    from types import SimpleNamespace

    from color_measurement import desktop

    seen = []
    monkeypatch.setitem(
        sys.modules,
        "color_measurement.desktop_native",
        SimpleNamespace(run=lambda assets: seen.append(assets)),
    )
    desktop.main()
    assert seen and seen[0].name == "desktop_assets"


def test_asset_refusal_routes_to_startup_error_before_an_owner(monkeypatch):
    from color_measurement import desktop

    def stale(*_):
        raise ValueError("Changed bundled bytes")

    monkeypatch.setattr(desktop.desktop_assets_manifest, "check_renderer", stale)
    failures = []

    def report(reason):
        failures.append(reason)
        raise SystemExit(reason)

    monkeypatch.setattr(desktop, "_startup_failure", report)
    with pytest.raises(SystemExit, match="Changed bundled bytes"):
        desktop.main()
    assert failures == ["Bundled desktop assets failed freshness checks: Changed bundled bytes"]


def test_missing_qt_is_refused_after_fresh_assets(monkeypatch):
    import builtins

    from color_measurement import desktop

    original = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "desktop_native":
            raise ImportError("Controlled missing desktop dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    with pytest.raises(SystemExit, match=r"color-measurement\[app\]"):
        desktop.main()


def test_native_failure_alert_has_one_default_acknowledgement():
    alert = _failure_alert("Hidden configuration check", ValueError("Original refusal"))
    assert alert.buttons == ["Dismiss"] and alert.default == 0
    assert alert.title == "Hidden configuration check" and alert.text == "Original refusal"


@pytest.mark.parametrize("state", ["failed", "pending", "saving"])
def test_close_callback_cannot_treat_admission_as_completed_shutdown(state):
    with pytest.raises(ValueError, match="Owner cleanup incomplete"):
        _require_completed_close(
            dict(admission="admitted", state=state, reason="Owner cleanup incomplete")
        )
    _require_completed_close(dict(admission="admitted", state="completed", reason=None))


def test_abandonment_discloses_unconfirmed_saving_without_guessing_disk_state():
    recorded = dict(admission="admitted", state="completed", reason=None, shutdown_saved=True)
    closed = dict(admission="admitted", state="completed", reason=None)
    assert _unconfirmed_shutdown_text(recorded) is None
    assert _unconfirmed_shutdown_text(closed) is None
    text = _unconfirmed_shutdown_text(
        dict(
            admission="admitted",
            state="completed",
            effect="owner_abandoned",
            shutdown_saved=False,
            reason="Disk full while publishing batch.json",
        )
    )
    assert "Disk full while publishing batch.json" in text
    assert "unconfirmed" in text and "may already contain" in text and "will quit" in text
    assert "not written" not in text and "reads as open" not in text
    assert "cannot be recovered" not in text


def test_native_exit_requires_exact_acknowledgement_from_the_actual_owner(tmp_path, monkeypatch):
    from color_measurement import exports
    from color_measurement.application import BatchApplication
    from color_measurement.demo import demo_frontends

    owner = BatchApplication(
        tmp_path / "batch",
        frontend=next(
            front
            for front in demo_frontends()
            if json.loads(front.describe_json())["device_id"] == "demo:i1pro3"
        ),
        mode="emissive_spot",
        options={},
    )
    from color_measurement.desktop_session import DesktopSession

    session = DesktopSession([])
    session.batch = owner
    binding = dict(batch_id=owner.snapshot()["batch_id"], generation=owner.snapshot()["generation"])
    lock = exports._folder_lock(owner._folder, ".ti3-export.lock").__enter__()
    descriptor, close = lock.descriptor, exports.os.close

    def refuse_owned_close(value):
        if value == descriptor:
            raise OSError("Controlled retained publication owner")
        return close(value)

    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(exports.os, "close", refuse_owned_close)
            with pytest.raises(OSError, match="retained publication owner"):
                lock.__exit__(None, None, None)
        assert owner.snapshot()["process_exit_ready"]
        contents = (owner._folder / "batch.json").read_bytes()
        command = owner.command

        def wrong_effect(*args, **kwargs):
            return command(*args, **kwargs) | dict(effect="owner_closed")

        with monkeypatch.context() as scoped:
            scoped.setattr(owner, "command", wrong_effect)
            with pytest.raises(ValueError, match="did not acknowledge"):
                _close_owner(
                    session,
                    "confirm_process_exit",
                    **binding,
                    discard_token=owner.discard_summary()["token"],
                )
        with pytest.raises(ValueError):
            _close_owner(
                session, "confirm_process_exit", **binding, discard_token="stale confirmation"
            )
        with pytest.raises(ValueError):
            _close_owner(
                session,
                "confirm_process_exit",
                batch_id=binding["batch_id"],
                generation=binding["generation"] + 1,
                discard_token=owner.discard_summary()["token"],
            )
        result = _close_owner(
            session,
            "confirm_process_exit",
            **binding,
            discard_token=owner.discard_summary()["token"],
        )
        assert result["effect"] == "process_exit_acknowledged"
        assert result["admission"] == "admitted" and result["state"] == "completed"
        assert not owner._closed and owner._lock is not None and lock.descriptor == descriptor
        assert (owner._folder / "batch.json").read_bytes() == contents
    finally:
        close(descriptor)
        lock.descriptor = None
        for key, retired in tuple(exports._RETIRED_PUBLICATION_LOCKS.items()):
            if retired is lock:
                del exports._RETIRED_PUBLICATION_LOCKS[key]
        owner._release_lock()
        owner._closed = True


def test_demo_stop_is_immediate_and_fenced_to_its_load():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from color_measurement.desktop import _stop_demo_preload

    stop, running = Event(), Event()

    def blocked_owner():
        running.set()
        assert stop.wait(2), "Stop was queued behind its own loading worker"

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(blocked_owner)
        assert running.wait(2)
        with pytest.raises(ValueError, match="no longer active"):
            _stop_demo_preload(stop, "new load", "stale load")
        assert not stop.is_set()
        _stop_demo_preload(stop, "new load", "new load")
        future.result(2)


@pytest.mark.parametrize("finished", [False, True])
def test_shutdown_disclosure_after_installed_manifest_sync_failure(tmp_path, monkeypatch, finished):
    import time
    from pathlib import Path
    from uuid import uuid4

    from color_measurement import exports
    from color_measurement.application import BatchApplication
    from color_measurement.demo import demo_frontends

    owner = BatchApplication(
        tmp_path / "batch",
        frontend=next(
            front
            for front in demo_frontends()
            if json.loads(front.describe_json())["device_id"] == "demo:i1pro3"
        ),
        mode="emissive_spot",
        options={},
    )

    def command(action, payload, *, identity=None):
        state = owner.snapshot()
        return owner.command(
            str(uuid4()) if identity is None else identity,
            state["generation"],
            action,
            payload,
            batch_id=state["batch_id"],
        )

    try:
        if finished:
            finish_id = str(uuid4())
            assert command("finish", {}, identity=finish_id)["admission"] == "admitted"
            deadline = time.monotonic() + 8
            while owner.command_outcome(finish_id)["state"] == "pending":
                assert time.monotonic() < deadline, "Finish publication did not settle"
                time.sleep(0.005)
            assert owner.command_outcome(finish_id)["state"] == "completed"
        path = Path(owner.snapshot()["folder"]) / "batch.json"
        original_sync = exports._sync_directory

        def failed_after_replace(folder):
            if folder == path.parent and json.loads(path.read_bytes())["status"] == "abandoned":
                raise OSError("Installed abandonment; directory synchronization failed")
            return original_sync(folder)

        monkeypatch.setattr(exports, "_sync_directory", failed_after_replace)
        outcome = command(
            "abandon",
            dict(
                discard_token=owner.discard_summary()["token"], reason="Explicit test abandonment"
            ),
        )
        _require_completed_close(outcome)
        assert outcome["shutdown_saved"] is False
        assert json.loads(path.read_bytes())["status"] == "abandoned"
        text = _unconfirmed_shutdown_text(outcome)
        assert "directory synchronization failed" in text and "may already contain" in text
        assert "reads as open" not in text and "not written" not in text
    finally:
        if not owner._closed:
            owner._release_lock()
            owner._closed = True


@pytest.mark.parametrize("condition", ["man_ref_white", "man_em_dark"])
def test_instruction_uses_only_current_condition_setup_and_actual_budget(condition):
    from color_measurement.argyll_capability import CALIBRATION_CONDITIONS
    from color_measurement.instrument_frontend import _calibration_detail
    from color_measurement.operator_setup import calibration_setup

    prompt = dict(calibration_condition=CALIBRATION_CONDITIONS[condition])
    if condition == "man_ref_white":
        prompt["reference_id"] = "instrument-reference"
    calibration = _calibration_detail(prompt)
    calibration["setup"] = calibration_setup("X-Rite i1 Pro 3", condition)
    instruction = dict(
        message="Exact backend message",
        response_timeout_seconds=37.5,
        calibration=calibration,
        position=None,
    )
    original = deepcopy(instruction)
    text = _instruction_text(instruction)
    assert "Exact backend message" in text
    assert calibration["instruction"] in text
    assert "37.5 seconds from the request" in text
    assert "Some time may have elapsed" in text
    if calibration["setup"]:
        assert calibration["setup"]["text"] in text
    else:
        assert "tile" not in text and "calibration plate" not in text
    assert instruction == original


def test_position_instruction_keeps_driver_guidance_without_inventing_calibration():
    text = _instruction_text(
        dict(
            kind="sensor_position",
            message="sensor_position",
            response_timeout_seconds=30,
            calibration=None,
            position=dict(needed="ambient", stage="measure", driver_text="Fit the diffuser"),
        )
    )
    assert "Required position for measurement: ambient" in text
    assert "Fit the diffuser" in text and "sensor_position" not in text
    assert "Calibration condition" not in text and "tile" not in text


def test_display_calibration_keeps_reference_level_and_warnings():
    text = _instruction_text(
        dict(
            message="display setup",
            response_timeout_seconds=120,
            calibration=dict(
                condition="em_grey",
                instruction="Display the requested grey",
                setup=None,
                reference_id="display-A",
                reference_id_holds="Display identity",
                display_level=0.25,
                warnings=["Reference qualification warning"],
            ),
            position=None,
        )
    )
    assert "Display identity: display-A" in text
    assert "0.25 of full white" in text and "Reference qualification warning" in text


@pytest.mark.parametrize("uncertain", [False, True])
def test_close_discloses_unsaved_comparison_even_when_other_categories_are_empty(
    tmp_path, uncertain
):
    from color_measurement.application import BatchApplication
    from color_measurement.demo import demo_frontends

    # The real owner's summary shape, so a renamed or reshaped category cannot
    # pass here while the native dialog miscounts it.
    owner = BatchApplication(
        tmp_path / "batch",
        frontend=next(
            front
            for front in demo_frontends()
            if json.loads(front.describe_json())["device_id"] == "demo:i1pro3"
        ),
        mode="emissive_spot",
        options={},
    )
    try:
        summary = owner.discard_summary()
    finally:
        owner._release_lock()
        owner._closed = True
    assert summary["comparison"] is None and isinstance(summary["unknown_outcomes"], dict)
    summary["comparison"] = dict(reference="selected", white="chosen")
    original = deepcopy(summary)
    text = _discard_text(summary, uncertain)
    assert "1 unsaved comparison context (reference and white choices)" in text
    assert "0 unsaved outcomes" in text and "only if accepted" in text
    assert ("cleanup is unconfirmed" in text) is uncertain
    assert summary == original
    summary["comparison"] = None
    assert "0 unsaved comparison context" in _discard_text(summary, uncertain)


@pytest.mark.parametrize("prepared", [False, True])
def test_close_discloses_exact_retained_automatic_file_identities(prepared):
    summary = dict(
        operations=[],
        annotations=[],
        annotation_drafts=[],
        drafts=None,
        comparison=None,
        unknown_outcomes={},
        companions=[
            dict(
                format="mat",
                path="/batch/measurements.mat",
                payload_bytes=1234 if prepared else None,
                source_manifest_sha256="a" * 64,
                payload_sha256="b" * 64 if prepared else None,
                reason="Controlled preparation failure" if not prepared else None,
            )
        ],
        chart=dict(revision=2),
        native_diagnostics=dict(
            settings=dict(debug_level=1),
            captures=[dict(bytes=4, sha256="c" * 64)],
            retention_reasons={"operation": "Original retention failure"},
            publication_reasons={"operation": "Original publication failure"},
        ),
    )
    original = deepcopy(summary)
    text = _discard_text(summary, False)
    assert "1 automatic file awaiting save" in text
    assert (
        "MAT: /batch/measurements.mat (1234 bytes)"
        if prepared
        else "MAT: /batch/measurements.mat (no prepared bytes)"
    ) in text
    assert "Source SHA-256: " + "a" * 64 in text
    if prepared:
        assert "Prepared file SHA-256: " + "b" * 64 in text
    else:
        assert "Prepared file SHA-256" not in text and "Controlled preparation failure" in text
    assert "Unsaved chart state" in text and "Unsaved diagnostic settings" in text
    assert "Original retention failure" in text and "Original publication failure" in text
    assert "4 bytes, SHA-256 " + "c" * 64 in text
    assert "Saved files remain available" in text
    assert summary == original


@pytest.mark.parametrize(
    "kind,calibration,position,label",
    [
        (
            "calibration_setup",
            dict(condition="man_ref_white", instruction="Place on white tile"),
            None,
            "Confirm simulated calibration",
        ),
        ("sample_position", None, None, "Confirm simulated position"),
        (
            "sensor_position",
            None,
            dict(needed="ambient", stage="measure", driver_text="Fit diffuser"),
            "Confirm simulated position",
        ),
        ("operator_setup", None, None, "Confirm simulated instruction"),
    ],
)
@pytest.mark.parametrize(
    "responses",
    [["accept"], ["accept", "cancel"], ["accept", "skip", "cancel"], ["cancel", "accept"]],
)
def test_native_instruction_requires_deliberate_demo_confirmation(
    kind,
    calibration,
    position,
    label,
    responses,
):
    instruction = dict(
        kind=kind,
        message="Actual instruction",
        response_timeout_seconds=120,
        calibration=calibration,
        position=position,
        allowed_responses=responses,
    )
    original = deepcopy(instruction)
    alert = _instruction_alert(instruction, synthetic=True)
    buttons = alert.buttons
    confirm = buttons[responses.index("accept")]
    cancel = buttons[alert.cancel]
    assert len(buttons) == len(responses) + int("cancel" not in responses)
    assert buttons.count("Cancel operation") == 1
    if "skip" in responses:
        assert buttons[responses.index("skip")] == "Skip"
    assert confirm == label and cancel == "Cancel operation"
    assert alert.default is None
    assert "Actual instruction" in alert.text and "120 seconds" in alert.text
    assert instruction == original


# The controller harness uses presentation doubles and real owner executors.
# The Qt cases below separately exercise the actual event loop and bridge.
def test_window_titles_name_the_application_not_a_source():
    """No source is named before the application knows one."""
    import re
    from importlib.resources import files

    from color_measurement.desktop import _WINDOW_TITLE

    shell = files("color_measurement").joinpath("desktop_assets/index.html").read_text()
    assert re.search(r"<title>([^<]*)</title>", shell).group(1) == _WINDOW_TITLE
    assert not re.search(r"demo|simulat|argyll|serial", _WINDOW_TITLE, re.IGNORECASE)
    seed = re.search(r'<span id="sourceContext"[^>]*>([^<]*)</span>', shell).group(1)
    assert seed == "No source selected"


class FakeAlerts:
    def __init__(self, answers):
        self.answers, self.shown = list(answers), []

    def ask(self, dialog):
        self.shown.append((dialog.title, dialog.text, list(dialog.buttons)))
        assert self.answers, f"Unexpected native dialog: {dialog.title!r}"
        return self.answers.pop(0)


class FakeApplication:
    def __init__(self):
        self.terminated = []

    def sharedApplication(self):
        return self

    def terminate_(self, sender):
        self.terminated.append(sender)


class FakeUI:
    def __init__(self, alerts, application):
        self.scripts, self.reloads, self.clipboard = [], 0, []
        self.alerts, self.application = alerts, application

    def evaluate(self, script, handler=None):
        self.scripts.append(script)
        if handler is not None:
            handler(None, None)

    def ask(self, dialog):
        return self.alerts.ask(dialog)

    def reload(self):
        self.reloads += 1

    def quit(self):
        self.application.terminated.append(None)

    def copy_text(self, text):
        self.clipboard.append(text)


class MainQueue:
    """Callbacks the host hands to its UI queue, run by the test thread."""

    def __init__(self):
        import queue

        self._blocks = queue.Queue()

    def __call__(self, block):
        self._blocks.put(block)

    def run(self, count=1, timeout=8):
        import queue

        for _ in range(count):
            try:
                block = self._blocks.get(timeout=timeout)
            except queue.Empty:
                pytest.fail("The host handed nothing to the main queue in time")
            block()

    def run_until(self, predicate, timeout=8):
        """Run queued blocks in order until the predicate holds."""
        import time

        deadline = time.monotonic() + timeout
        while not predicate():
            self.run(timeout=max(0.01, deadline - time.monotonic()))


class HostHarness:
    def __init__(self, tmp_path, answers=(), *, instrument_software=None):
        from concurrent.futures import ThreadPoolExecutor

        from color_measurement.demo import demo_frontends
        from color_measurement.desktop import DesktopHost
        from color_measurement.desktop_session import DesktopSession

        self.alerts = FakeAlerts(answers)
        self.application = FakeApplication()
        self.assets = tmp_path / "desktop_assets"
        self.assets.mkdir()
        (self.assets / "index.html").write_text("<!doctype html>\n")
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="desktop-owner")
        self.demo_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="desktop-demo-load"
        )
        self.session = DesktopSession(demo_frontends(), setup_executor=self.demo_executor)
        self.main_queue = MainQueue()
        self.failures = []
        self.web = FakeUI(self.alerts, self.application)
        self.host = DesktopHost(
            ui=self.web,
            session=self.session,
            assets=self.assets,
            executor=self.executor,
            demo_executor=self.demo_executor,
            dispatch_main=self.main_queue,
            instrument_software=instrument_software,
        )
        self.host.show_failure = lambda title, reason: self.failures.append((title, reason))

    def message(self, request_id, method, params, **changes):
        assert not changes
        self.host.receive(dict(id=request_id, method=method, params=params))

    def replies(self):
        return [
            parse_reply(script)
            for script in self.web.scripts
            if script.startswith("window.ownerReply(")
        ]

    def reply(self, request_id):
        """Run main-queue blocks until the reply with this identity is delivered."""
        self.main_queue.run_until(
            lambda: any(reply["id"] == request_id for reply in self.replies())
        )
        return next(reply for reply in self.replies() if reply["id"] == request_id)

    def create(self, folder):
        settings = self.session.request("settings", {"selection_id": None})
        selection = next(
            row["selection_id"]
            for row in settings["selections"]
            if row["device_id"] == "demo:i1pro3"
        )
        return self.session.create(
            folder, "Host batch", "", selection_id=selection, mode="emissive_spot", options={}
        )

    def wait(self, predicate, timeout=8):
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        pytest.fail("The native host did not settle")

    def close(self):
        from color_measurement.application import BatchApplication

        batch = self.session.batch
        if isinstance(batch, BatchApplication) and not batch._closed:
            if batch._active is not None:
                state = batch.snapshot()
                batch.command(
                    str(uuid4()),
                    state["generation"],
                    "cancel",
                    {"command_id": batch._active},
                    batch_id=state["batch_id"],
                )
                self.wait(lambda: batch.snapshot()["phase"] == "idle")
            batch._release_lock()
            batch._closed = True
        self.demo_executor.shutdown(wait=True)
        self.executor.shutdown(wait=True)


@pytest.fixture
def harness(tmp_path):
    def build(answers=(), *, instrument_software=None):
        built.append(HostHarness(tmp_path, answers, instrument_software=instrument_software))
        return built[-1]

    built = []
    yield build
    for item in built:
        item.close()


def test_demo_load_holds_its_own_worker_while_the_owner_keeps_answering(tmp_path, harness):
    import threading

    h = harness()
    h.create(tmp_path / "batch")
    state = h.session.snapshot()["batch"]
    started, workers = threading.Event(), []

    def held_load(*, batch_id, generation, count, stop_event):
        workers.append(threading.current_thread().name)
        started.set()
        assert stop_event.wait(8), "Stop never reached the load"
        return dict(
            batch_id=batch_id,
            requested=count,
            completed=0,
            retained=0,
            state="stopped",
            reason="Demo loading stopped",
        )

    h.session.preload_demo = held_load
    binding = dict(batch_id=state["batch_id"], generation=state["generation"])
    h.message("load", "preload_demo", dict(binding, count=40, load_id="load-1"))
    assert started.wait(8)
    # Catches a load queued on the owner executor: the poll's reply would then
    # wait for the load, which returns only on Stop, and this would time out.
    h.message("poll", "snapshot", {})
    polled = h.reply("poll")
    assert polled["ok"] and polled["value"]["batch"]["batch_id"] == state["batch_id"]
    assert not any(reply["id"] == "load" for reply in h.replies())
    # A second load is refused while one holds the worker.
    h.message("again", "preload_demo", dict(binding, count=40, load_id="load-2"))
    assert h.replies()[-1] == dict(
        id="again", ok=False, value=None, error="A Demo load is already active"
    )
    # Stop answers from the UI thread without either queue and releases the load.
    h.message("halt", "stop_demo_preload", dict(load_id="load-1"))
    assert h.replies()[-1] == dict(id="halt", ok=True, value=None, error=None)
    assert h.reply("load")["value"]["state"] == "stopped"
    # Catches submission to the owner pool: the worker name is the demo pool's.
    assert workers == ["desktop-demo-load_0"]
    assert h.host.demo_stop is None and h.host.demo_load_id is None
    assert h.failures == []


def test_actual_demo_load_answers_polls_and_stops_through_the_host(tmp_path, harness, monkeypatch):
    from threading import Event, current_thread

    h = harness(answers=[1])
    h.create(tmp_path / "batch")
    state = h.session.snapshot()["batch"]
    completed, release = Event(), Event()
    command_outcome = h.session.batch.command_outcome

    def hold_completed_outcome(command_id):
        outcome = command_outcome(command_id)
        if current_thread().name == "desktop-demo-load_0":
            # A real load is still active between readings, when the batch is idle.
            # Hold that boundary so the owner poll cannot race the next reading.
            completed.set()
            assert release.wait(8), "The test never released the completed Demo reading"
        return outcome

    monkeypatch.setattr(h.session.batch, "command_outcome", hold_completed_outcome)
    try:
        h.message(
            "load",
            "preload_demo",
            dict(
                batch_id=state["batch_id"],
                generation=state["generation"],
                count=40,
                load_id="real",
            ),
        )
        assert completed.wait(8), "The real Demo reading did not complete"
        h.message("poll", "snapshot", {})
        polled = h.reply("poll")
        assert polled["ok"] and polled["value"]["batch"]["phase"] == "idle"
        assert polled["value"]["batch"]["sample_count"] == 1
        assert h.session._demo_preload_running
        assert not any(reply["id"] == "load" for reply in h.replies())
        # Catches a poll reply presenting a Demo instruction natively while the load
        # acknowledges its own: the confirmation would answer a consumed instruction.
        # The packet is a captured poll reply with the pending instruction such a
        # poll can observe (about one in a hundred samples during a load).
        pending = dict(
            polled["value"],
            batch=dict(
                polled["value"]["batch"],
                instructions=dict(
                    kind="calibration_setup",
                    operation_id=polled["value"]["batch"]["operation_id"],
                    request_id="captured",
                    message="Simulated setup",
                    response_timeout_seconds=5,
                ),
            ),
        )
        assert h.host.demo_stop is not None
        h.host.reply_value("captured", pending)
        assert h.alerts.shown == [] and h.host.instruction_key is None
        h.message("halt", "stop_demo_preload", dict(load_id="real"))
    finally:
        if h.host.demo_stop is not None:
            h.host.demo_stop.set()
        release.set()
    loaded = h.reply("load")
    assert loaded["ok"], loaded
    assert loaded["value"]["state"] == "stopped" and loaded["value"]["requested"] == 40
    final = h.session.snapshot()["batch"]
    assert final["phase"] == "idle" and final["sample_count"] == loaded["value"]["retained"]
    assert loaded["value"]["completed"] < 40
    assert h.failures == []


def test_unsettled_demo_quit_has_bounded_retry_without_disabling_executors(
    tmp_path,
    harness,
    monkeypatch,
):
    import threading

    from color_measurement import desktop

    h = harness(answers=[1, 0])
    held, release = threading.Event(), threading.Event()

    def uncooperative():
        held.set()
        assert release.wait(8)

    future = h.host.demo_future = h.demo_executor.submit(uncooperative)
    h.host.demo_stop = threading.Event()
    assert held.wait(8)
    real_wait = desktop.wait
    close_sources = h.session.close_direct_sources
    closed = []

    def close_after_demo():
        assert future.done(), "Direct sources must stay owned until Demo stops"
        closed.append(True)
        return close_sources()

    monkeypatch.setattr(h.session, "close_direct_sources", close_after_demo)

    def bounded(futures, timeout=None):
        assert timeout == 30, "Native quit must bound its Demo drain"
        return real_wait(futures, timeout=0)

    monkeypatch.setattr(desktop, "wait", bounded)
    try:
        h.host.stop()
        h.main_queue.run_until(lambda: not h.host.stopping)
        assert h.host.demo_stop.is_set() and not h.application.terminated
        assert h.alerts.shown[-1][0] == "Demo loading has not stopped"
        assert h.alerts.shown[-1][2] == ["Keep application open", "Retry stopping Demo"]
        assert len(h.alerts.shown) == 2 and closed == []
        for executor in (h.executor, h.demo_executor):
            assert not executor._shutdown
        release.set()
        future.result(timeout=8)
        h.host.stop()
        h.main_queue.run_until(lambda: h.host.confirmed)
        assert h.application.terminated == [None] and not h.failures and closed == [True]
    finally:
        release.set()


def test_close_without_a_batch_stops_after_draining_both_workers(tmp_path, harness):
    h = harness()
    h.host.request_close()
    assert h.web.scripts == ["window.requestOwnerClose(); null"]
    assert not h.host.confirmed and h.application.terminated == []
    h.message("empty-close-flushed", "request_close", {})
    assert h.reply("empty-close-flushed")["ok"]
    h.main_queue.run_until(lambda: h.host.confirmed)
    # Catches a stop that leaves a worker running or never terminates the app.
    assert h.application.terminated == [None] and h.host.confirmed
    for pool in (h.executor, h.demo_executor):
        with pytest.raises(RuntimeError, match="shutdown"):
            pool.submit(lambda: None)
    assert h.host.confirmed
    assert h.host.confirmed
    assert h.failures == []


@pytest.mark.parametrize("unsaved", ["none", "draft", "unsynced"])
def test_close_with_a_batch_walks_renderer_then_native_confirmation(
    tmp_path,
    harness,
    monkeypatch,
    unsaved,
):
    from test_application import _command, _snapshot, _wait

    from color_measurement import application

    h = harness(answers=[0, 1])
    h.create(tmp_path / "batch")
    if unsaved == "draft":
        # Retained valid metadata differs from its last canonical publication.
        with h.session.batch._mutex:
            h.session.batch._data["draft"]["notes"] = "Retained unsaved sample note"
            h.session.batch._touch()
    elif unsaved == "unsynced":
        # The manifest is replaced but its directory sync fails: the summary
        # lists no difference, yet the owner still reports an unconfirmed save.
        publish = application._publish_owned_file

        def unsynced(path, payload, previous):
            result = publish(path, payload, previous)
            if path.name == "batch.json":
                raise OSError("Controlled directory sync failure")
            return result

        owner, draft = h.session.batch, _snapshot(h.session.batch)["draft"]
        with monkeypatch.context() as scoped:
            scoped.setattr(application, "_publish_owned_file", unsynced)
            _command(
                owner,
                "edit_draft",
                dict(
                    version=draft["version"],
                    name=draft["name"],
                    notes="Unsynced sample note",
                    options=draft["options"],
                    draft_id=draft["id"],
                    number_samples=draft["number_samples"],
                ),
            )
            _wait(owner, lambda state: state["phase"] == "idle" and state["save_reason"])
        assert not any(
            owner.discard_summary()[key]
            for key in ("operations", "annotations", "drafts", "companions", "unknown_outcomes")
        )
    # Window and application close requests are refused until confirmed and
    # start the renderer reconciliation exactly once.
    h.host.request_close()
    assert h.host.close_phase == "renderer"
    h.host.request_close()
    assert h.web.scripts == ["window.requestOwnerClose(); null"]
    h.message("c1", "cancel_close", {})
    assert h.host.close_phase == "idle" and h.replies()[-1]["id"] == "c1"
    # The renderer's close request inspects the owner off the UI thread; the
    # first native answer keeps working.
    h.message("c2", "request_close", {})
    assert h.host.close_phase == "native" and h.replies()[-1]["id"] == "c2"
    h.main_queue.run()
    assert h.alerts.shown[-1][0] == "Close this batch?"
    assert h.alerts.shown[-1][2] == [
        "Keep working",
        "Close" if unsaved == "none" else "Close and discard unresolved work",
    ]
    if unsaved != "none":
        assert "0 unsaved outcomes" in h.alerts.shown[-1][1]
        assert f"{int(unsaved == 'draft')} unsaved sample drafts" in h.alerts.shown[-1][1]
        assert (
            "The last batch save did not complete: Controlled directory sync failure"
            in h.alerts.shown[-1][1]
        ) is (unsaved == "unsynced")
        assert "Close verifies this exact summary again" in h.alerts.shown[-1][1]
    else:
        assert h.alerts.shown[-1][1] == (
            "All readings and edits are saved. Saved files remain available for review."
        )
    assert h.host.close_phase == "idle" and not h.session.batch._closed
    # A repeated request while native confirmation is pending is not re-entered.
    h.message("c3", "request_close", {})
    h.message("c4", "request_close", {})
    h.main_queue.run()
    assert len(h.alerts.shown) == 2
    # The second answer closes the owner and terminates after disclosure.
    h.main_queue.run_until(lambda: h.host.confirmed)
    assert h.session.batch._closed and h.host.confirmed
    assert h.application.terminated == [None] and h.failures == []


def test_abandoned_retirement_quit_preserves_unconfirmed_stop_warning(
    tmp_path,
    harness,
    monkeypatch,
):
    from color_measurement import application, exports

    h = harness(
        answers=[
            0,
            1,
        ]
    )
    h.create(tmp_path / "batch")
    owner = h.session.batch
    monkeypatch.setattr(application, "_UNCERTAIN_OWNERS", [])
    monkeypatch.setattr(exports, "_RETIRED_PUBLICATION_LOCKS", {})
    from test_application import _retain_actual_unconfirmed_demo_operation

    _retain_actual_unconfirmed_demo_operation(owner, monkeypatch)
    lock, close = owner._lock, exports.os.close
    descriptor = lock.descriptor

    def refuse_close(value):
        if value == descriptor:
            raise OSError("Controlled abandoned lock-release failure")
        return close(value)

    try:
        state = owner.snapshot()
        with monkeypatch.context() as scoped:
            scoped.setattr(exports.os, "close", refuse_close)
            failed = owner.command(
                str(uuid4()),
                state["generation"],
                "abandon",
                dict(
                    discard_token=owner.discard_summary()["token"],
                    reason="Operator explicitly abandoned terminal unconfirmed cleanup",
                ),
                batch_id=state["batch_id"],
            )
        assert failed["state"] == "failed" and owner.snapshot()["process_exit_ready"]
        original = (owner._folder / "batch.json").read_bytes()
        h.message("keep-retired", "request_close", {})
        h.main_queue.run()
        message, disclosure, buttons = h.alerts.shown[-1]
        assert message == "Quit without confirming folder cleanup?"
        assert "Software cleanup is unconfirmed" in disclosure
        assert "does not prove a device stop" in disclosure
        assert buttons[-1] == "Discard unresolved work and quit"
        assert not owner._process_exit_acknowledged and not h.host.confirmed
        h.message("quit-retired", "request_close", {})
        h.main_queue.run_until(lambda: h.host.confirmed)
        assert owner._process_exit_acknowledged and not owner._closed
        assert h.application.terminated == [None] and not h.failures
        assert not any(
            title == "Direct connection cleanup is unconfirmed" for title, _, _ in h.alerts.shown
        )
        assert owner in application._UNCERTAIN_OWNERS
        assert not owner.snapshot()["software_stopped"]
        assert lock.descriptor == descriptor
        assert (owner._folder / "batch.json").read_bytes() == original
    finally:
        # This controlled failure never called the OS close on this known-open FD.
        close(descriptor)
        lock.descriptor = None
        owner._lock = None
        exports._RETIRED_PUBLICATION_LOCKS.clear()
        # The recorded terminator does not exit pytest. Dispose only this known
        # stopped Demo lifetime after its production uncertainty assertions.
        owner._closed = True
        owner._active = owner._operation = None


def test_close_waits_for_an_active_operation_with_a_pending_instruction(tmp_path, harness):
    h = harness(answers=[0])
    h.create(tmp_path / "batch")
    presented = []
    h.host.show_instruction = presented.append
    state = h.session.snapshot()["batch"]
    draft = state["draft"]
    h.message(
        "m",
        "command",
        dict(
            command_id=str(uuid4()),
            generation=state["generation"],
            batch_id=state["batch_id"],
            action="measure",
            payload=dict(
                context_id=state["context_id"], draft_id=draft["id"], draft_version=draft["version"]
            ),
        ),
    )
    assert h.reply("m")["value"]["admission"] == "admitted"
    h.wait(lambda: h.session.snapshot()["batch"]["instructions"] is not None)
    h.message("c", "request_close", {})
    h.main_queue.run()
    # Catches a close that discards an active operation: the only alert is the
    # wait, nothing was closed, and the phase returns to idle.
    assert h.alerts.shown == [
        (
            "Wait for the current operation before closing",
            "Cancel in the workspace, then wait for retained stop and saving facts.",
            [],
        )
    ]
    assert h.host.close_phase == "idle" and not h.session.batch._closed
    assert h.application.terminated == []


@pytest.mark.parametrize(
    "responses,index,expected",
    [
        (["retry", "cancel", "skip"], 2, "skip"),
        (["retry", "cancel", "skip"], 1, "cancel"),
        (["retry", "skip"], 2, None),
        (["cancel", "accept"], 1, "accept"),
    ],
)
def test_instruction_buttons_keep_offered_response_order_and_one_cancel(
    tmp_path,
    harness,
    responses,
    index,
    expected,
):
    from types import SimpleNamespace

    h = harness(answers=[0 + index])
    instruction = dict(
        operation_id="operation",
        request_id="prompt",
        kind="sample_position",
        message="sample_position",
        response_timeout_seconds=30,
        calibration=None,
        position=None,
        allowed_responses=responses,
    )
    state = dict(
        instruction_state(),
        instructions=instruction,
        generation=3,
        batch_id="batch",
        descriptor={
            "synthetic": True,
            "instructions": [
                dict(instruction="sample_position", detail="Place the sample under the sensor.")
            ],
        },
    )
    h.session.batch = SimpleNamespace(snapshot=lambda: state)
    requests = []

    def admitted(method, params):
        requests.append((method, params))
        return dict(admission="admitted", state="completed")

    h.session.request = admitted
    h.host.show_instruction(state)
    h.wait(lambda: len(requests) == 1)
    _, params = requests[0]
    action, payload = _instruction_command(state, ("operation", "prompt"), expected)
    assert params["action"] == action and params["payload"] == payload
    message, text, buttons = h.alerts.shown[-1]
    assert message == "Demo: Position instrument"
    assert buttons.count("Cancel operation") == 1
    assert buttons[index] == (
        "Cancel operation"
        if expected in (None, "cancel")
        else "Confirm simulated position"
        if expected == "accept"
        else expected.capitalize()
    )
    assert "Place the sample under the sensor." in text
    assert "sample_position" not in text


def test_instruction_presentation_is_deduplicated_and_answered_through_the_owner(
    tmp_path,
    harness,
):
    from types import SimpleNamespace

    h = harness(answers=[0, 1])
    instruction = dict(
        operation_id="operation",
        request_id="prompt",
        kind="sample_position",
        message="Place the instrument",
        response_timeout_seconds=30,
        calibration=None,
        position=None,
        allowed_responses=["accept"],
    )
    state = dict(
        instruction_state(),
        instructions=instruction,
        generation=3,
        batch_id="batch",
        descriptor={"synthetic": True},
    )
    h.session.batch = SimpleNamespace(snapshot=lambda: state)
    requests = []

    def admitted(method, params):
        requests.append((method, params))
        return dict(admission="admitted", state="completed")

    h.session.request = admitted
    h.host.show_instruction(state)
    h.wait(lambda: len(requests) == 1)
    method, params = requests[0]
    assert method == "command" and params["action"] == "respond"
    assert params["payload"] == dict(
        operation_id="operation", request_id="prompt", response="accept"
    )
    assert (params["generation"], params["batch_id"]) == (3, "batch")
    assert h.alerts.shown[-1][2] == ["Confirm simulated position", "Cancel operation"]
    # The same instruction is not presented twice; a state without one clears the key.
    h.host.show_instruction(state)
    assert len(h.alerts.shown) == 1 and h.host.instruction_key == ("operation", "prompt")
    h.host.show_instruction(dict(instructions=None))
    assert h.host.instruction_key is None
    # Cancel maps to the retained command, and a refused answer is reported on
    # the main queue with the key released for a retry.
    h.session.request = lambda method, params: dict(admission="refused", reason="Instruction stale")
    h.host.show_instruction(state)
    h.main_queue.run()
    assert h.failures[-1][0] == "Instruction response was not accepted"
    assert str(h.failures[-1][1]) == "Instruction stale" and h.host.instruction_key is None


def test_renderer_process_loss_reloads_and_releases_a_renderer_close_phase(tmp_path, harness):
    h = harness()
    h.create(tmp_path / "batch")
    h.host.request_close()
    assert h.host.close_phase == "renderer"
    h.host.renderer_terminated()
    assert h.host.close_phase == "idle" and h.web.reloads == 1


def test_format_availability_cannot_schedule_acquisition_instructions():
    from color_measurement import desktop
    from color_measurement.demo import demo_frontends
    from color_measurement.desktop_session import DesktopSession

    session = DesktopSession(demo_frontends())
    formats = session.format_capabilities()
    assert "batch" in formats
    assert not desktop._is_session_snapshot(formats)
    snapshot = session.snapshot()
    assert snapshot["batch"] is None
    assert desktop._is_session_snapshot(snapshot)
    assert desktop._is_session_snapshot(
        dict(
            snapshot,
            batch=dict(instructions=None),
        )
    )
    assert not desktop._is_session_snapshot(None)


def test_close_summary_waits_separate_companions_before_discard_identity(monkeypatch):
    from types import SimpleNamespace

    from color_measurement import desktop

    states = iter(
        [
            dict(phase="idle", companions=dict(saving=True)),
            dict(phase="saving", companions=dict(saving=False)),
            dict(phase="cleanup_uncertain", companions=dict(saving=False)),
        ]
    )
    current, waits = {}, []

    def snapshot():
        nonlocal current
        current = dict(next(states, current), process_exit_acknowledged=False)
        return current

    def discard():
        assert not current["companions"]["saving"] and current["phase"] != "saving"
        return {"token": "exact-settled-contents"}

    monkeypatch.setattr(
        desktop, "Event", lambda: SimpleNamespace(wait=lambda seconds: waits.append(seconds))
    )
    session = SimpleNamespace(
        batch=SimpleNamespace(snapshot=snapshot, discard_summary=discard, _closed=False),
        export_recovery=lambda: [],
    )
    result = desktop._close_summary(session)
    assert result["contents"]["token"] == "exact-settled-contents"
    assert result["state"]["phase"] == "cleanup_uncertain"
    assert waits == [0.02, 0.02]


def test_close_summary_timeout_keeps_unsettled_contents_unconfirmed(monkeypatch):
    from types import SimpleNamespace

    from color_measurement import desktop

    ticks = iter([0, 31])
    monkeypatch.setattr(desktop.time, "monotonic", lambda: next(ticks))
    session = SimpleNamespace(
        batch=SimpleNamespace(
            snapshot=lambda: dict(phase="idle", companions=dict(saving=True)),
            discard_summary=lambda: pytest.fail("No discard identity before publication settles"),
        )
    )
    with pytest.raises(ValueError, match="saving has not settled within 30 seconds"):
        desktop._close_summary(session)


def test_native_instruction_preserves_offered_response_and_physical_wording():
    state = instruction_state()
    state["instructions"]["allowed_responses"] = ["retry", "skip"]
    assert _instruction_command(state, ("operation", "prompt"), "skip")[1]["response"] == "skip"
    with pytest.raises(ValueError, match="not offered"):
        _instruction_command(state, ("operation", "prompt"), "accept")
    text = _instruction_text(
        dict(
            message="Place the instrument on its tile",
            calibration=None,
            position=None,
            response_timeout_seconds=120,
        )
    )
    assert "Complete the instrument preparation" in text
    assert "simulated" not in text and "Time limit: 120 seconds" in text


@pytest.mark.parametrize("accepted", [True, False])
def test_instrument_report_chooser_is_single_file_json_only(accepted):
    from pathlib import Path
    from types import SimpleNamespace

    from color_measurement.desktop import _choose_build_report

    chosen = []
    expected = Path("/recording-only/report.json") if accepted else None

    def file_path(title, **options):
        chosen.append(options)
        return expected

    assert _choose_build_report(SimpleNamespace(file_path=file_path)) == expected
    assert chosen == [{"extensions": ["json"]}]


def test_native_report_route_uses_only_chooser_path_and_owner_executor(harness, monkeypatch):
    import threading
    from pathlib import Path

    from color_measurement import desktop

    h = harness()
    chosen, selections = [], []
    path = Path("/recording-only/report.json")
    monkeypatch.setattr(desktop, "_choose_build_report", lambda appkit: chosen.append(True) or path)

    def select(selected, *, expected_setup_generation):
        assert threading.current_thread() is not threading.main_thread()
        selections.append((selected, expected_setup_generation))
        return dict(
            generation=3,
            listing_generation=0,
            state="ready",
            reason=None,
            report=None,
            catalog=None,
            candidates=[],
            bindings=[],
        )

    monkeypatch.setattr(h.session, "select_build_report", select)
    h.message("injected", "choose_build_report", {"setup_generation": 2, "path": "/injected"})
    assert not h.reply("injected")["ok"]
    assert chosen == selections == []
    h.message("selected", "choose_build_report", {"setup_generation": 2})
    assert h.reply("selected")["value"]["generation"] == 3
    assert chosen == [True] and selections == [(path, 2)]
    monkeypatch.setattr(desktop, "_choose_build_report", lambda appkit: None)
    h.message("cancelled", "choose_build_report", {"setup_generation": 3})
    assert h.reply("cancelled")["value"] is None
    assert len(selections) == 1


def test_native_equipment_reply_keeps_packaged_failure_visible_without_selecting_demo(harness):
    status = dict(packaged=True, ready=False, error="Bundled instrument software is unavailable")
    h = harness(instrument_software=status)
    h.message("initial-setup", "equipment_setup", {})
    reply = h.reply("initial-setup")
    assert reply["ok"] is True and reply["value"]["instrument_software"] == status
    assert reply["value"]["state"] == "unselected"
    assert reply["value"]["candidates"] == reply["value"]["bindings"] == []
    assert h.session.settings(None)["selection_id"] is None and h.session.batch is None


@pytest.mark.parametrize("accepted", [True, False])
def test_correction_chooser_is_single_file_and_declares_both_material_kinds(accepted):
    from pathlib import Path
    from types import SimpleNamespace

    from color_measurement.desktop import _choose_correction

    chosen = []
    expected = Path("/recording-only/display.ccmx") if accepted else None

    def file_path(title, **options):
        chosen.append(options)
        return expected

    assert _choose_correction(SimpleNamespace(file_path=file_path)) == expected
    assert chosen == [{"extensions": ["ccmx", "ccss"]}]


@pytest.mark.parametrize("current", [False, True])
def test_correction_route_keeps_native_path_and_exact_scope_on_owner_executor(
    harness,
    monkeypatch,
    current,
):
    import threading
    from pathlib import Path

    from color_measurement import desktop

    h = harness()
    path = Path("/recording-only/display.ccmx")
    chosen, staged = [], []
    method = "choose_current_correction" if current else "choose_correction"
    target = "stage_current_correction" if current else "stage_correction"
    scope = (
        dict(
            batch_id="batch",
            generation=2,
            context_id="context",
            draft_id="draft",
            version=3,
            options={"average": 4},
        )
        if current
        else dict(
            selection_id="selection",
            mode="emissive_spot",
            capability_id="capability",
            settings_generation=7,
            options={"average": 4},
        )
    )
    material = dict(correction={"token": "native-held"}, material={"kind": "ccmx"})
    monkeypatch.setattr(desktop, "_choose_correction", lambda appkit: chosen.append(True) or path)

    def stage(selected, **params):
        assert threading.current_thread().name.startswith("desktop-owner")
        staged.append((selected, params))
        return material

    monkeypatch.setattr(h.session, target, stage)
    h.message("injected", method, dict(scope, path="/injected"))
    assert not h.reply("injected")["ok"]
    assert chosen == staged == []
    h.message("selected", method, scope)
    assert h.reply("selected")["value"] == material
    assert chosen == [True] and staged == [(path, scope)]
    monkeypatch.setattr(desktop, "_choose_correction", lambda appkit: None)
    h.message("cancelled", method, scope)
    assert h.reply("cancelled")["value"] is None and len(staged) == 1

    monkeypatch.setattr(desktop, "_choose_correction", lambda appkit: path)

    def refuse(selected, **params):
        raise ValueError("Material no longer matches this source")

    monkeypatch.setattr(h.session, target, refuse)
    h.message("refused", method, scope)
    reply = h.reply("refused")
    assert not reply["ok"] and "no longer matches" in reply["error"]


def test_summary_copy_uses_native_pasteboard_and_refuses_invalid_text(harness, monkeypatch):
    import threading

    h = harness()
    copied = []

    def write(text):
        assert threading.current_thread() is threading.main_thread()
        copied.append(text)

    monkeypatch.setattr(h.web, "copy_text", write)
    for index, text in enumerate((None, "", " ", "x" * 131073)):
        h.message("invalid-" + str(index), "copy_summary", {"text": text})
        assert not h.reply("invalid-" + str(index))["ok"]
    h.message("extra", "copy_summary", {"text": "Summary", "path": "/injected"})
    assert not h.reply("extra")["ok"]
    assert copied == []
    text = "ΔX: -2 cd/m²\nΔH*: unavailable (neutral hue)"
    h.message("copy", "copy_summary", {"text": text})
    assert h.reply("copy")["value"] == {"copied": True}
    assert copied == [text]

    def failed(text):
        raise ValueError("The summary could not be copied")

    monkeypatch.setattr(h.web, "copy_text", failed)
    h.message("failed", "copy_summary", {"text": text})
    assert "could not be copied" in h.reply("failed")["error"]


def test_record_routes_keep_paths_native_and_execute_exact_owner_methods(harness, monkeypatch):
    import threading
    from pathlib import Path

    from color_measurement import desktop

    h = harness()
    inputs, outputs, effects = [], [], []
    source = Path("/recording-only/original.json")
    choices = iter([Path("/recording-only/output.sp"), Path("/recording-only/output.json")])
    monkeypatch.setattr(
        desktop, "_choose_record_input", lambda appkit, format: inputs.append(format) or source
    )
    h.host.choose_export = lambda format: outputs.append(format) or next(choices)

    def opening(path, **params):
        assert threading.current_thread() is not threading.main_thread()
        effects.append(("open", path, params))
        return {"opened": True}

    def exporting(path, **params):
        assert threading.current_thread() is not threading.main_thread()
        effects.append(("export", path, params))
        return {"path": str(path)}

    monkeypatch.setattr(h.session, "open_record", opening)
    monkeypatch.setattr(h.session, "export_record", exporting)
    h.message(
        "injected-source",
        "open_record",
        dict(
            binding_id=None,
            generation=0,
            format="operation_json",
            path="/injected",
        ),
    )
    assert not h.reply("injected-source")["ok"] and not inputs
    h.message(
        "open",
        "open_record",
        dict(
            binding_id=None,
            generation=0,
            format="operation_json",
        ),
    )
    assert h.reply("open")["value"] == {"opened": True}
    assert effects[0] == (
        "open",
        source,
        dict(
            format="operation_json",
            expected_binding_id=None,
            expected_generation=0,
        ),
    )
    params = dict(
        binding_id="source",
        generation=1,
        dataset_revision="revision",
        reading_ref=None,
        format="sp",
    )
    h.message("injected-output", "export_record", dict(params, canonical_path="/injected"))
    assert not h.reply("injected-output")["ok"] and not outputs
    h.message("export", "export_record", params)
    assert h.reply("export")["ok"]
    assert outputs == ["sp", "json"]
    assert effects[1] == (
        "export",
        Path("/recording-only/output.sp"),
        dict(
            params,
            canonical_path=Path("/recording-only/output.json"),
        ),
    )
    choices = iter([Path("/recording-only/cancelled.sp"), None])
    h.message("cancel-companion", "export_record", params)
    assert h.reply("cancel-companion")["value"] is None and len(effects) == 2


def test_native_recovery_and_close_dispatch_original_record_retry(harness, monkeypatch):
    from pathlib import Path

    h = harness(answers=[1])
    pending = dict(
        recovery_id="held-native",
        format="sp",
        payload_sha256="original",
        native_format="sp",
        reason="Parent directory unavailable",
        published=False,
        attempted_path="/missing/original.json",
        retry_method="retry_record_export",
    )
    monkeypatch.setattr(h.session, "export_recovery", lambda: [pending])
    effects = []

    def retry(path, **params):
        effects.append((path, params))
        return {"path": str(path)}

    monkeypatch.setattr(h.session, "retry_record_export", retry)
    monkeypatch.setattr(
        h.session, "retry_analysis_export", lambda *args, **kwargs: pytest.fail("Wrong retry owner")
    )
    choices = iter([Path("/new/original.sp"), Path("/new/original.json")])
    h.host.choose_export = lambda format: next(choices)
    h.message("retry", "retry_record_export", {"recovery_id": "held-native"})
    assert h.reply("retry")["ok"]
    assert effects == [
        (
            Path("/new/original.sp"),
            dict(
                recovery_id="held-native",
                canonical_path=Path("/new/original.json"),
            ),
        )
    ]
    h.message("wrong-retry", "retry_analysis_export", {"recovery_id": "held-native"})
    assert not h.reply("wrong-retry")["ok"] and len(effects) == 1
    choices = iter([Path("/close/original.sp"), Path("/close/original.json")])
    h.host.confirm_close({"pending_exports": [pending]})
    h.wait(lambda: len(effects) == 2)
    assert effects[-1] == (
        Path("/close/original.sp"),
        dict(
            recovery_id="held-native",
            canonical_path=Path("/close/original.json"),
        ),
    )


def test_direct_native_port_tokens_keep_chooser_separate_from_initialization_and_reconciliation(
    harness,
    monkeypatch,
):
    import threading

    from color_measurement import desktop

    h = harness()
    chosen, effects = [], []
    monkeypatch.setattr(
        desktop,
        "_choose_serial_port",
        lambda appkit: chosen.append(True) or "/recording-only/serial-port",
    )

    def initialize(port, **params):
        assert threading.current_thread() is not threading.main_thread()
        effects.append((port, params))
        return {"command_id": params["command_id"], "state": "completed", "result": {}}

    monkeypatch.setattr(h.session, "connect_direct_serial", initialize)
    h.message("injected-port", "choose_direct_port", {"setup_generation": 0, "port": "/injected"})
    assert not h.reply("injected-port")["ok"] and not chosen
    h.message("choose", "choose_direct_port", {"setup_generation": 0})
    selected = h.reply("choose")["value"]
    assert chosen == [True] and not effects
    params = dict(
        command_id=str(uuid4()),
        setup_generation=0,
        port_token=selected["port_token"],
        model="CR-100",
        expected_serial="expected-native-serial",
        accept_disclosed_initialization=True,
        response_timeout=5,
        measurement_timeout=300,
    )
    h.message("injected-effect", "connect_direct_serial", dict(params, port="/injected"))
    assert not h.reply("injected-effect")["ok"] and not effects
    h.message("stale-port", "connect_direct_serial", dict(params, setup_generation=1))
    assert not h.reply("stale-port")["ok"] and not effects
    h.message("initialize", "connect_direct_serial", params)
    assert h.reply("initialize")["ok"]
    assert len(chosen) == len(effects) == 1
    assert effects[0][0] == "/recording-only/serial-port"
    assert {key: value for key, value in effects[0][1].items() if key != "executor"} == {
        key: value for key, value in params.items() if key != "port_token"
    }
    assert effects[0][1]["executor"] is h.demo_executor
    # The real shared ledger query does not invoke this native path or chooser.
    monkeypatch.setattr(
        h.session._equipment_setup,
        "outcome",
        lambda command_id: {
            "command_id": command_id,
            "state": "completed",
            "result": {},
        },
    )
    h.message("reconcile", "equipment_outcome", {"command_id": params["command_id"]})
    assert h.reply("reconcile")["ok"]
    assert len(chosen) == len(effects) == 1
    h.message("reused-port", "connect_direct_serial", dict(params, command_id=str(uuid4())))
    assert not h.reply("reused-port")["ok"] and len(effects) == 1
    h.message("changed-intent", "connect_direct_serial", dict(params, expected_serial="changed"))
    assert not h.reply("changed-intent")["ok"] and len(effects) == 1
    h.message("new-choice", "choose_direct_port", {"setup_generation": 0})
    assert h.reply("new-choice")["ok"] and len(h.host.direct_ports) == 1
    h.message("retired-choice", "connect_direct_serial", params)
    assert not h.reply("retired-choice")["ok"] and len(effects) == 1
    h.message("stale-choice", "choose_direct_port", {"setup_generation": 1})
    assert not h.reply("stale-choice")["ok"] and len(chosen) == 2
    monkeypatch.setattr(desktop, "_choose_serial_port", lambda appkit: None)
    h.message("cancelled", "choose_direct_port", {"setup_generation": 0})
    assert h.reply("cancelled")["value"] is None and len(effects) == 1


def test_pyserial_notice_authentication_rejects_version_or_bytes_without_opening_a_port(
    tmp_path,
    monkeypatch,
):
    import runpy
    import shutil
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    functions = runpy.run_path(str(root / "tools/build_desktop_app.py"))
    notice = functions["serial_notices"]
    target = tmp_path / "tools/desktop_runtime_licenses"
    target.mkdir(parents=True)
    for name in ("PYSERIAL.txt", "SERIAL-ORIGIN.json"):
        shutil.copyfile(root / "tools/desktop_runtime_licenses" / name, target / name)
    # The notice belongs to the locked pySerial. Read the lock, not the
    # environment: pySerial is installed only with the app extra.
    origin = json.loads((target / "SERIAL-ORIGIN.json").read_text())
    locked = [
        package["version"]
        for package in tomllib.loads((root / "uv.lock").read_text())["package"]
        if package["name"] == origin["component"]
    ]
    assert locked == [origin["version"]]
    # Answer only for the receipt's distribution, so a lookup of another name fails.
    monkeypatch.setattr(
        functions["importlib"].metadata,
        "version",
        lambda name: (
            locked[0]
            if name == origin["component"]
            else pytest.fail(f"serial_notices asked for {name!r}, not {origin['component']!r}")
        ),
    )
    assert len(notice(tmp_path)[0][1]) == 2
    original = (target / "PYSERIAL.txt").read_bytes()
    (target / "PYSERIAL.txt").write_bytes(original + b"Changed notice")
    with pytest.raises(ValueError, match="notice changed"):
        notice(tmp_path)
    (target / "PYSERIAL.txt").write_bytes(original)
    monkeypatch.setattr(functions["importlib"].metadata, "version", lambda name: "future-version")
    with pytest.raises(ValueError, match="bundled version"):
        notice(tmp_path)


@pytest.fixture(params=["Darwin", "Linux", "Windows"])
def instrument_software(tmp_path, monkeypatch, request):
    """Retained production-report shape with inert workers and original notices."""
    import hashlib
    import platform
    import runpy
    from pathlib import Path

    from test_discovery import CATALOG, _report

    from color_measurement import argyll_description
    from color_measurement.discovery import InstrumentBuild
    from color_measurement.instrument import Instrument

    def refuse(*args, **kwargs):
        pytest.fail("Startup and packaging must not access devices or run workers")

    monkeypatch.setattr(platform, "system", lambda: request.param)
    monkeypatch.setattr(argyll_description, "HelperSession", refuse)
    monkeypatch.setattr(Instrument, "_open_session", refuse)
    for method in ("attached", "describe", "bind"):
        monkeypatch.setattr(InstrumentBuild, method, refuse)
    source = tmp_path / "retained-build"
    source.mkdir()
    path = _report(source, override={"driver_manifest": json.loads(CATALOG)})
    report = json.loads(path.read_bytes())
    for name, content in (
        ("UPSTREAM-NOTICES.md", b"Retained upstream notice\n"),
        ("License2.txt", b"Retained native license text\n"),
    ):
        (source / name).write_bytes(content)
    report["upstream_license_inventory"] = dict(
        notices_path="UPSTREAM-NOTICES.md",
        notices_sha256=hashlib.sha256((source / "UPSTREAM-NOTICES.md").read_bytes()).hexdigest(),
        license_texts={
            "License2.txt": dict(
                retained_path="License2.txt",
                sha256=hashlib.sha256((source / "License2.txt").read_bytes()).hexdigest(),
            )
        },
    )
    private_path = str(source / "private-development-command")
    report["command"] = [private_path]
    report["original_check_source_sha256"] = {private_path: "0" * 64}
    report["generic_worker_build_link"]["command"] = [private_path]
    path.write_text(json.dumps(report), encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    prepare = runpy.run_path(str(root / "tools/build_desktop_app.py"))[
        "prepare_instrument_software"
    ]
    return path, root, prepare


def test_packaged_instrument_software_is_portable_authenticated_and_inert(
    tmp_path,
    instrument_software,
):
    import hashlib
    import os
    import shutil
    import stat
    from pathlib import Path

    from color_measurement.demo import demo_frontends
    from color_measurement.desktop import _configure_instrument_software
    from color_measurement.desktop_session import DesktopSession
    from color_measurement.discovery import InstrumentBuild
    from color_measurement.native_receipt import native_worker_name

    source, root, prepare = instrument_software
    original = source.read_bytes()
    result = prepare(source, tmp_path / "staging")
    assert source.read_bytes() == original
    assert result["source_report_sha256"] == hashlib.sha256(original).hexdigest()
    resources = tmp_path / "App" / "Contents" / "Resources"
    for relative, files in result["data_files"]:
        folder = resources / relative
        folder.mkdir(parents=True, exist_ok=True)
        for file in files:
            shutil.copy2(file, folder / Path(file).name)
    report = resources / "instrument-software" / "report.json"
    encoded = report.read_bytes()
    projected = json.loads(encoded)
    original_report = json.loads(original)
    assert projected["build_scope"] == original_report["build_scope"] == "all_public"
    assert projected["build_system"] == original_report["build_system"]
    assert hashlib.sha256(encoded).hexdigest() == result["report_sha256"]
    assert projected["source_report_sha256"] == result["source_report_sha256"]
    assert b"private-development-command" not in encoded
    assert "original_check_source_sha256" not in projected and "command" not in projected
    assert "command" not in projected["generic_worker_build_link"]
    for name in ("generic-worker-never-run", "argyll-description-worker-never-run"):
        name = native_worker_name(name)
        worker = report.parent / name
        assert worker.read_bytes() == (source.parent / name).read_bytes()
        if os.name != "nt":
            assert stat.S_IMODE(worker.stat().st_mode) == 0o755
        else:
            # Windows exposes DOS write permission, not POSIX executable bits.
            assert worker.is_file() and worker.stat().st_mode & stat.S_IWRITE
    for relative, digest in projected["bundled_license_sha256"].items():
        retained = report.parent / relative
        assert hashlib.sha256(retained.read_bytes()).hexdigest() == digest
        assert retained.read_bytes() == (source.parent / retained.name).read_bytes()
    shutil.rmtree(source.parent)
    build = InstrumentBuild.from_report(report)
    assert build.binding_context() == dict(kind="production-workers", provenance="physical")
    session = DesktopSession(demo_frontends())
    before = session.settings(None)
    configured = _configure_instrument_software(session, resources, result["report_sha256"])
    assert configured == dict(packaged=True, ready=True, error=None)
    ready = session.request("equipment_setup", {})
    assert ready["state"] == "ready" and ready["generation"] == 1
    assert ready["catalog"]["instrument_types"] and ready["candidates"] == []
    assert ready["bindings"] == [] and session.settings(None) == before
    assert before["selection_id"] is None and session.batch is None
    assert all(row["provenance"] == "simulated" for row in before["selections"])


@pytest.mark.parametrize(
    "fault",
    [
        "missing_report",
        "changed_report",
        "missing_worker",
        "changed_worker",
        "missing_notice",
        "changed_notice",
        "missing_upstream_notice",
        "changed_upstream_notice",
        "empty_digest",
        "malformed_digest",
        "wrong_digest",
        "wrong_digest_type",
    ],
)
def test_packaged_startup_refuses_missing_or_unauthenticated_software_without_demo_fallback(
    tmp_path,
    instrument_software,
    fault,
):
    from color_measurement.demo import demo_frontends
    from color_measurement.desktop import _configure_instrument_software
    from color_measurement.desktop_session import DesktopSession
    from color_measurement.native_receipt import native_worker_name

    source, root, prepare = instrument_software
    resources = tmp_path / "Resources"
    destination = resources / "instrument-software"
    result = prepare(source, destination)
    expected = result["report_sha256"]
    report = destination / "report.json"
    worker = destination / native_worker_name("generic-worker-never-run")
    notice = destination / "licenses" / "License2.txt"
    # The CR/PR copyright notices: the report's notices_path, not a licence text.
    upstream = destination / "licenses" / "UPSTREAM-NOTICES.md"
    if fault == "missing_report":
        report.unlink()
    elif fault == "changed_report":
        report.write_bytes(report.read_bytes() + b"\n")
    elif fault == "missing_worker":
        worker.unlink()
    elif fault == "changed_worker":
        worker.write_bytes(worker.read_bytes() + b"Changed packaged worker")
    elif fault == "missing_notice":
        notice.unlink()
    elif fault == "changed_notice":
        notice.write_bytes(notice.read_bytes() + b"Changed packaged notice")
    elif fault == "missing_upstream_notice":
        upstream.unlink()
    elif fault == "changed_upstream_notice":
        upstream.write_bytes(upstream.read_bytes() + b"Changed upstream notice")
    else:
        expected = dict(
            empty_digest="",
            malformed_digest="not-a-sha256",
            wrong_digest="0" * 64,
            wrong_digest_type=42,
        )[fault]
    session = DesktopSession(demo_frontends())
    before = session.settings(None)
    status = _configure_instrument_software(session, resources, expected)
    assert status["packaged"] is True and status["ready"] is False
    assert isinstance(status["error"], str) and status["error"].strip()
    assert session.settings(None) == before and before["selection_id"] is None
    assert session.batch is None
    setup = session.request("equipment_setup", {})
    assert setup["state"] == "unselected" and setup["report"] is None
    assert setup["candidates"] == setup["bindings"] == []


def test_source_cli_startup_keeps_manual_software_selection_and_demo_explicit(
    tmp_path,
    monkeypatch,
):
    from color_measurement.demo import demo_frontends
    from color_measurement.desktop import _configure_instrument_software
    from color_measurement.desktop_session import DesktopSession

    session = DesktopSession(demo_frontends())
    before = session.settings(None)
    monkeypatch.setenv("CM_ARGYLL_BUILD_REPORT", "/not-selected/private-report.json")
    monkeypatch.setattr(
        session,
        "select_build_report",
        lambda *a, **k: pytest.fail("Guessed a source CLI build report"),
    )
    assert _configure_instrument_software(session, tmp_path / "missing-resources", None) == dict(
        packaged=False,
        ready=False,
        error=None,
    )
    assert session.settings(None) == before and before["selection_id"] is None
    assert session.request("equipment_setup", {})["state"] == "unselected"


@pytest.mark.parametrize("frozen,has_identity", [(False, False), (True, False), (True, True)])
def test_main_configures_bundle_software_before_exposing_the_host(
    tmp_path,
    instrument_software,
    monkeypatch,
    frozen,
    has_identity,
):
    import sys
    from types import ModuleType

    from color_measurement import desktop_native
    from color_measurement.demo import demo_frontends
    from color_measurement.desktop import _configure_instrument_software
    from color_measurement.desktop_session import DesktopSession

    source, _, prepare = instrument_software
    resources = tmp_path / "Resources"
    prepared = prepare(source, resources / "instrument-software")
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(resources), raising=False)
    module = ModuleType("color_measurement._packaged_identity")
    if has_identity:
        module.REPORT_SHA256 = prepared["report_sha256"]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    root, digest = desktop_native.package_identity()
    session = DesktopSession(demo_frontends())
    status = _configure_instrument_software(session, root, digest)
    assert status["packaged"] is frozen and status["ready"] is (frozen and has_identity)
    assert bool(status["error"]) is (frozen and not has_identity)
    setup = session.request("equipment_setup", {})
    assert setup["state"] == ("ready" if frozen and has_identity else "unselected")
    assert setup["candidates"] == setup["bindings"] == []
    assert session.settings(None)["selection_id"] is None and session.batch is None


@pytest.mark.parametrize(
    "fault",
    [
        "generic-worker-never-run",
        "argyll-description-worker-never-run",
        "UPSTREAM-NOTICES.md",
        "License2.txt",
        "relative_escape",
        "symlink_escape",
    ],
)
def test_instrument_software_refuses_changed_or_escaped_inputs_before_staging(
    tmp_path,
    instrument_software,
    fault,
):
    from color_measurement.native_receipt import native_worker_name

    source, root, prepare = instrument_software
    if fault in {"relative_escape", "symlink_escape"}:
        outside = tmp_path / "outside-notice.md"
        outside.write_bytes((source.parent / "UPSTREAM-NOTICES.md").read_bytes())
        report = json.loads(source.read_bytes())
        if fault == "relative_escape":
            report["upstream_license_inventory"]["notices_path"] = "../outside-notice.md"
        else:
            link = source.parent / "linked-notice.md"
            link.symlink_to(outside)
            report["upstream_license_inventory"]["notices_path"] = link.name
        source.write_text(json.dumps(report), encoding="utf-8")
    else:
        if fault.endswith("-never-run"):
            fault = native_worker_name(fault)
        path = source.parent / fault
        path.write_bytes(path.read_bytes() + b"Changed retained input")
    destination = tmp_path / "rejected-staging"
    with pytest.raises(ValueError):
        prepare(source, destination)
    assert not destination.exists()


@pytest.mark.parametrize("changed_by_signing", [False, True])
def test_instrument_software_finalization_seals_only_the_app_and_rechecks_workers(
    tmp_path,
    instrument_software,
    monkeypatch,
    changed_by_signing,
):
    import runpy

    from color_measurement.discovery import InstrumentBuild
    from color_measurement.native_receipt import native_worker_name

    source, root, prepare = instrument_software
    prepared = prepare(source, tmp_path / "staging")
    functions = runpy.run_path(str(root / "tools/build_desktop_app.py"))
    application = tmp_path / "Color Measurement.app"
    software = application / "Contents" / "Resources" / "instrument-software"
    commands = []

    def sign(command, *, check):
        assert check is True
        assert command == ["/usr/bin/codesign", "--force", "--sign", "-", str(application)]
        InstrumentBuild.from_report(software / "report.json")
        commands.append(command)
        if changed_by_signing:
            worker = software / native_worker_name("generic-worker-never-run")
            worker.write_bytes(worker.read_bytes() + b"Changed during signing")

    monkeypatch.setitem(
        functions["install_instrument_software"].__globals__, "sign_qt_helper", lambda app: None
    )
    monkeypatch.setattr(functions["subprocess"], "run", sign)
    if changed_by_signing:
        with pytest.raises(ValueError, match="does not match"):
            functions["install_instrument_software"](application, prepared)
    else:
        functions["install_instrument_software"](application, prepared)
        InstrumentBuild.from_report(software / "report.json")
        for name in ("generic-worker-never-run", "argyll-description-worker-never-run"):
            name = native_worker_name(name)
            assert (software / name).read_bytes() == (source.parent / name).read_bytes()
    assert len(commands) == 1


@pytest.mark.parametrize("phase", ["description", "registration"])
def test_argyll_setup_keeps_snapshot_and_close_responsive_until_the_observed_ending(
    tmp_path,
    harness,
    monkeypatch,
    phase,
):
    from contextlib import contextmanager
    from threading import Event

    from test_desktop_setup import connect_params
    from test_discovery import _build, _candidates

    from color_measurement import desktop
    from color_measurement.discovery import InstrumentBuild

    h = harness()
    build, client = _build(_candidates(("usb:/recording-only", 21, 0x02, None)))
    client._describe["provenance"] = "simulated"
    monkeypatch.setattr(InstrumentBuild, "from_report", classmethod(lambda cls, path: build))
    report = tmp_path / "recording-report.json"
    report.write_text('{"software_only": true}', encoding="utf-8")
    h.session.select_build_report(report, expected_setup_generation=0)
    h.message("list", "refresh_devices", dict(command_id=str(uuid4()), setup_generation=1))
    assert h.reply("list")["value"]["state"] == "pending"
    h.session._equipment_setup._future.result(timeout=2)
    entered, release, draining = Event(), Event(), Event()
    original, calls = client.describe_json, []
    terminal = dict(
        event="terminal",
        initialization_performed=True,
        software_stopped=True,
        cleanup="destructor_returned",
        instrument_state="unknown",
    )

    def describe(*args, **kwargs):
        calls.append((args, kwargs))
        if phase == "description":
            entered.set()
            assert release.wait(8)
        result = original(*args, **kwargs)
        client.last_events = (terminal,)
        return result

    transaction = h.session._equipment_registration_transaction

    @contextmanager
    def registered():
        with transaction():
            yield
        if phase == "registration":
            entered.set()
            assert release.wait(8)

    wait = desktop.wait

    def tracked_wait(jobs, *, timeout):
        assert h.session._equipment_setup._future in jobs
        draining.set()
        return wait(jobs, timeout=timeout)

    monkeypatch.setattr(client, "describe_json", describe)
    monkeypatch.setattr(h.session, "_equipment_registration_transaction", registered)
    monkeypatch.setattr(desktop, "wait", tracked_wait)
    monkeypatch.setattr(
        desktop,
        "_choose_serial_port",
        lambda *args: pytest.fail("Opened chooser during active setup"),
    )
    params = connect_params(h.session)
    try:
        h.message("connect", "connect_device", params)
        assert h.reply("connect")["value"]["state"] == "pending" and entered.wait(2)
        future = h.session._equipment_setup._future
        h.executor.submit(lambda: True).result(timeout=1)
        h.message("before-close", "snapshot", {})
        assert h.reply("before-close")["ok"]
        h.message("another-port", "choose_direct_port", dict(setup_generation=1))
        assert not h.reply("another-port")["ok"]
        h.host.stop()
        assert h.host.stopping and draining.wait(2)
        h.executor.submit(lambda: True).result(timeout=1)
        h.message("while-closing", "snapshot", {})
        assert h.reply("while-closing")["ok"]
        h.message("close-outcome", "equipment_outcome", dict(command_id=params["command_id"]))
        assert h.reply("close-outcome")["value"]["state"] == "pending"
        h.message("close-setup", "equipment_setup", {})
        setup_reply = h.reply("close-setup")
        assert setup_reply["ok"]
        active = setup_reply["value"]["active_setup"]
        assert active["command_id"] == params["command_id"]
        assert active["cancel_requested"] is (phase == "description") and not future.done()
        assert len(calls) == 1 and not h.application.terminated
    finally:
        release.set()
        h.main_queue.run_until(lambda: h.host.confirmed or bool(h.failures))
    result = future.result(timeout=2)
    assert result["state"] == ("failed" if phase == "description" else "completed")
    assert result["native_events"] == [terminal] and len(calls) == 1
    assert len(h.session.request("equipment_setup", {})["bindings"]) == (
        0 if phase == "description" else 1
    )
    assert h.application.terminated == [None] and not h.failures and not h.alerts.shown


@pytest.mark.parametrize("queued", [False, True])
def test_native_direct_initialization_keeps_owner_poll_cancel_and_quit_responsive(
    harness,
    monkeypatch,
    queued,
):
    from threading import Event

    from test_desktop_direct import ready_port
    from test_serial_transport import Clock

    from color_measurement import desktop

    h, port = harness(), ready_port()
    entered, release, admit, draining = Event(), Event(), Event(), Event()
    read = port.read

    def held_read(size):
        entered.set()
        assert release.wait(5), "The test did not release the scripted initialization"
        return read(size)

    port.read = held_read
    connect = h.session.connect_direct_serial

    def initialize(path, **params):
        result = connect(path, transport=port, clock=Clock(), **params)
        if queued:
            assert entered.wait(2), "Actual initialization did not begin before its reply"
        return result

    monkeypatch.setattr(h.session, "connect_direct_serial", initialize)
    monkeypatch.setattr(desktop, "_choose_serial_port", lambda appkit: "scripted-native-port")
    jobs = []
    wait = desktop.wait

    def tracked_wait(futures, **kwargs):
        jobs.extend(futures)
        draining.set()
        return wait(futures, **kwargs)

    monkeypatch.setattr(desktop, "wait", tracked_wait)
    try:
        h.message("choose", "choose_direct_port", dict(setup_generation=0))
        selected = h.reply("choose")["value"]
        params = dict(
            command_id=str(uuid4()),
            setup_generation=0,
            port_token=selected["port_token"],
            model="CR-100",
            expected_serial="CR-SOFTWARE",
            accept_disclosed_initialization=True,
            response_timeout=5,
            measurement_timeout=300,
        )
        if queued:
            h.executor.submit(lambda: admit.wait(5))
        h.message("initialize", "connect_direct_serial", params)
        if queued:
            h.host.stop()  # Quit arrives before the queued admission publishes its Future.
            admit.set()
        pending = h.reply("initialize")["value"]
        assert pending["state"] == "pending" and entered.wait(2)
        if not queued:
            h.message("poll", "snapshot", {})
            assert h.reply("poll")["ok"]
            h.message("outcome", "equipment_outcome", dict(command_id=params["command_id"]))
            assert h.reply("outcome")["value"]["state"] == "pending"
            h.message("cancel", "cancel_setup", dict(command_id=params["command_id"]))
            assert h.reply("cancel")["value"]["state"] == "pending"
            h.host.stop()
        assert draining.wait(2), "Quit did not wait for the actual admitted initialization"
        assert h.session._equipment_setup._future in jobs
        assert h.session.request("equipment_setup", {})["active_setup"]["cancel_requested"]
        assert port.is_open and not h.application.terminated
        assert h.host.stopping and not h.application.terminated
    finally:
        admit.set()
        release.set()
    h.main_queue.run_until(lambda: h.application.terminated)
    assert not port.is_open and not h.alerts.shown
    assert (
        h.session.request("equipment_outcome", dict(command_id=params["command_id"]))["state"]
        == "failed"
    )


def test_closed_owner_keep_open_then_close_retries_actual_direct_cleanup(
    tmp_path,
    harness,
    monkeypatch,
):
    from test_desktop_direct import connect, ready_port

    h = harness(answers=[1, 0, 1])
    port = ready_port()
    connected, _ = connect(h.session, port)
    assert connected["state"] == "completed"
    h.create(tmp_path / "closed-with-owned-direct-port")
    close = port.close
    calls = []

    def failed_once():
        calls.append(True)
        if len(calls) == 1:
            raise OSError("Controlled direct host-port close failure")
        close()

    monkeypatch.setattr(port, "close", failed_once)
    h.message("first-close", "request_close", {})
    h.main_queue.run_until(lambda: len(h.alerts.shown) == 2 and not h.host.stopping)
    assert not h.host.confirmed and port.is_open
    h.message("close-again", "request_close", {})
    h.main_queue.run_until(lambda: h.host.confirmed or bool(h.failures))
    assert h.host.confirmed and h.application.terminated == [None], h.failures
    assert calls == [True, True] and not port.is_open
    assert [title for title, _, _ in h.alerts.shown] == [
        "Close this batch?",
        "Direct connection cleanup is unconfirmed",
    ]


def test_native_quit_keeps_failed_direct_cleanup_owned_and_blocks_new_effects(harness, monkeypatch):
    import threading
    from threading import Event

    h = harness(answers=[0])
    entered, release = Event(), Event()
    calls = []

    def cleanup():
        assert threading.current_thread() is not threading.main_thread()
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return {"failures": [{"model": "CR-100", "reason": "Host port close unconfirmed"}]}

    monkeypatch.setattr(h.session, "close_direct_sources", cleanup)
    h.host.stop()
    assert entered.wait(5)
    h.message("late-choose", "choose_direct_port", {"setup_generation": 0})
    reply = h.reply("late-choose")
    assert not reply["ok"] and "releasing its owned connections" in reply["error"]
    release.set()
    h.main_queue.run_until(lambda: not h.host.stopping)
    assert not h.host.confirmed and not h.application.terminated
    assert h.alerts.shown[-1][0] == "Direct connection cleanup is unconfirmed"
    assert h.executor.submit(lambda: True).result()
    assert h.demo_executor.submit(lambda: True).result()
    monkeypatch.setattr(h.session, "close_direct_sources", lambda: {"failures": []})
    h.host.stop()
    h.main_queue.run_until(lambda: h.host.confirmed)
    assert h.application.terminated == [None] and len(calls) == 1


@pytest.mark.parametrize("decision", ["keep", "retry", "quit"])
def test_cleanup_modal_keeps_effect_admission_closed(harness, monkeypatch, decision):
    answer = {
        "keep": 0,
        "retry": 1,
        "quit": 2,
    }[decision]
    h = harness(answers=[answer])
    reports = iter(
        [
            {"failures": [{"model": "CR-100", "reason": "Host port close unconfirmed"}]},
            {"failures": []},
        ]
    )
    monkeypatch.setattr(h.session, "close_direct_sources", lambda: next(reports))
    original_modal = FakeAlerts.ask

    def modal(alert, dialog):
        assert h.host.stopping
        for method in (
            "create_batch",
            "choose_direct_port",
            "connect_direct_serial",
            "reopen_direct_serial",
        ):
            h.message(method, method, {})
            reply = h.reply(method)
            assert not reply["ok"]
            assert "releasing its owned connections" in reply["error"]
        h.message("during-close", "snapshot", {})
        assert h.reply("during-close")["ok"]
        return original_modal(alert, dialog)

    monkeypatch.setattr(FakeAlerts, "ask", modal)
    h.host.stop()
    h.main_queue.run_until(lambda: not h.host.stopping or h.host.confirmed)
    if decision == "keep":
        assert not h.host.stopping and not h.host.confirmed
        assert h.executor.submit(lambda: True).result()
        assert h.demo_executor.submit(lambda: True).result()
    else:
        assert h.host.stopping and h.host.confirmed
        assert h.application.terminated == [None]


def test_direct_cleanup_waits_for_actual_demo_preload_to_release_its_owner(
    tmp_path,
    harness,
    monkeypatch,
):
    import threading

    h = harness()
    h.create(tmp_path / "preload-close")
    owner = h.session.batch
    h.wait(lambda: not owner.snapshot()["companions"]["saving"])
    entered, release, cleanup_started = (threading.Event() for _ in range(3))
    original_snapshot = owner.snapshot
    snapshots = 0

    def held_snapshot():
        nonlocal snapshots
        state = original_snapshot()
        if threading.current_thread().name.startswith("desktop-demo-load"):
            snapshots += 1
            if snapshots == 2:
                entered.set()
                assert release.wait(8), "The owned Demo preload was not released"
        return state

    monkeypatch.setattr(owner, "snapshot", held_snapshot)
    close_sources = h.session.close_direct_sources

    def observed_cleanup():
        cleanup_started.set()
        return close_sources()

    monkeypatch.setattr(h.session, "close_direct_sources", observed_cleanup)
    state = owner.snapshot()
    try:
        h.message(
            "load",
            "preload_demo",
            dict(
                batch_id=state["batch_id"],
                generation=state["generation"],
                count=40,
                load_id="preload-close",
            ),
        )
        assert entered.wait(8) and h.session._demo_preload_running
        closed = _close_owner(
            h.session,
            "close",
            batch_id=state["batch_id"],
            generation=state["generation"],
            discard_token=owner.discard_summary()["token"],
        )
        assert closed["state"] == "completed"
        h.host.stop()
        assert h.host.demo_stop.is_set()
        assert not cleanup_started.wait(0.1), "Direct cleanup raced the active Demo preload"
        release.set()
        h.main_queue.run_until(lambda: h.host.confirmed)
        assert cleanup_started.is_set() and not h.session._demo_preload_running
        assert h.application.terminated == [None] and not h.alerts.shown
    finally:
        release.set()


@pytest.fixture(scope="module")
def qt_app():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    app = widgets.QApplication.instance() or widgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app


def qt_until(app, predicate, timeout=12):
    import time

    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Qt operation did not settle"
        app.processEvents()
        time.sleep(0.005)


def test_qt_main_queue_dispatches_and_releases_captures_while_sender_lives(qt_app):
    import gc
    import threading
    import weakref
    from concurrent.futures import ThreadPoolExecutor

    from color_measurement.desktop_native import qt_types

    MainQueue = qt_types().MainQueue
    queue = MainQueue()

    class Snapshot(dict):
        pass

    delivered, references = [], []

    def post(index):
        value = Snapshot(index=index)
        references.append(weakref.ref(value))
        queue(lambda: delivered.append((value["index"], threading.current_thread())))

    with ThreadPoolExecutor(max_workers=1) as sender:
        for index in range(8):
            sender.submit(post, index).result(2)
        qt_until(qt_app, lambda: len(delivered) == 8)
        gc.collect()
        assert delivered == [(index, threading.main_thread()) for index in range(8)]
        assert all(reference() is None for reference in references)
        assert sender.submit(lambda: True).result(2)


def test_windows_icon_contains_all_native_sizes(qt_app):
    import struct
    from pathlib import Path

    from PySide6.QtGui import QColorSpace, QImage

    path = Path(__file__).resolve().parents[1] / "tools/desktop_builder/app-icon.ico"
    payload = path.read_bytes()
    sizes = (16, 24, 32, 48, 64, 128, 256)
    assert struct.unpack_from("<HHH", payload) == (0, 1, len(sizes))
    end = 6 + 16 * len(sizes)
    for index, size in enumerate(sizes):
        width, height, colors, reserved, planes, depth, length, offset = struct.unpack_from(
            "<BBBBHHII", payload, 6 + 16 * index
        )
        dimension = size if size < 256 else 0
        assert (width, height, colors, reserved, planes, depth) == (
            dimension, dimension, 0, 0, 1, 32
        )
        assert offset == end
        image = QImage.fromData(payload[offset:offset + length], "PNG")
        assert (image.width(), image.height()) == (size, size)
        image = image.convertedToColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
        assert image.pixelColor(size // 8, size // 2).name() == "#181a1e"
        end += length
    assert end == len(payload)


def _desktop_string(value):
    """Desktop Entry `string` value: only these escapes exist; others are invalid."""
    escapes = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    decoded, index = [], 0
    while index < len(value):
        if value[index] == "\\":
            decoded.append(escapes[value[index + 1]])
            index += 2
        else:
            decoded.append(value[index])
            index += 1
    return "".join(decoded)


def _desktop_exec(value):
    """Exec after string unescaping: %% field codes, then double-quoted arguments."""
    import re

    value = _desktop_string(value)
    assert re.fullmatch(r"(?:[^%]|%%)*", value), "unescaped field code"
    value = value.replace("%%", "%")
    arguments, index = [], 0
    while index < len(value):
        if value[index] == " ":
            index += 1
        elif value[index] == '"':
            argument, index = [], index + 1
            while value[index] != '"':
                if value[index] == "\\":
                    assert value[index + 1] in '"`$\\', "invalid quoted escape"
                    index += 1
                else:
                    assert value[index] not in '"`$', "unescaped reserved character"
                argument.append(value[index])
                index += 1
            arguments.append("".join(argument))
            index += 1
        else:
            stop = value.find(" ", index)
            stop = len(value) if stop < 0 else stop
            arguments.append(value[index:stop])
            index = stop
    return arguments


@pytest.mark.skipif(sys.platform == "win32", reason="Linux menu uses a POSIX shell")
def test_linux_menu_registration_follows_the_extracted_folder(tmp_path):
    import os
    import shutil
    import subprocess
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "tools/desktop_builder/install-linux-desktop.sh"
    application = tmp_path / "Color Measurement"
    application.mkdir()
    launcher = application / "Add to Applications.sh"
    shutil.copy2(source, launcher)
    program = application / "Color Measurement"
    program.write_text("#!/bin/sh\nexit 0\n")
    program.chmod(0o755)
    icon = application / "_internal/color_measurement/desktop_assets/app-icon.svg"
    icon.parent.mkdir(parents=True)
    icon.write_text("<svg/>")
    data = tmp_path / "desktop data"
    environment = dict(os.environ, XDG_DATA_HOME=str(data))
    entry = data / "applications/io.github.ferazambuja.colormeasurement.desktop"
    # Every character the Exec quoting and string rules treat specially.
    for name in ("Color Measurement", "Moved Color % Measurement",
                 "Odd $HOME `id` \"quoted\" back\\slash \\s 100% 'single' &;|<>~*?#()"):
        application = application.rename(tmp_path / name)
        subprocess.run(["sh", str(application / launcher.name)], env=environment, check=True)
        text = entry.read_text()
        assert text.startswith("[Desktop Entry]\n")
        values = dict(line.split("=", 1) for line in text.splitlines()[1:])
        assert _desktop_exec(values["Exec"]) == [
            "/usr/bin/env", str(application / "Color Measurement")]
        assert _desktop_string(values["TryExec"]) == str(application / "Color Measurement")
        assert _desktop_string(values["Icon"]) == str(
            application / "_internal/color_measurement/desktop_assets/app-icon.svg")
        assert values["Name"] == "Color Measurement"
        assert values["StartupWMClass"] == "Color Measurement"
    # A trailing newline must not resolve to an existing folder without it.
    shutil.copytree(application, tmp_path / "linebreak")
    for name in ("line\nbreak", "line\rbreak", "linebreak\n", "linebreak\r"):
        entry.unlink()
        application = application.rename(tmp_path / name)
        refused = subprocess.run(["sh", str(application / launcher.name)], env=environment,
                                 capture_output=True, text=True, check=False)
        assert refused.returncode != 0 and "line breaks" in refused.stderr
        assert not entry.exists()
        application = application.rename(tmp_path / "Color Measurement")
        subprocess.run(["sh", str(application / launcher.name)], env=environment, check=True)
    entry.unlink()
    (application / "Color Measurement").unlink()
    failed = subprocess.run(["sh", str(application / launcher.name)], env=environment, check=False)
    assert failed.returncode != 0 and not entry.exists()


def test_qt_choice_has_no_implicit_physical_acceptance_and_keeps_three_choices(qt_app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from color_measurement.desktop import Dialog
    from color_measurement.desktop_native import qt_types

    ChoiceBox = qt_types().ChoiceBox
    dialog = Dialog("Cleanup unconfirmed", "Original reason", ["Keep open", "Retry", "Quit"])
    box = ChoiceBox(dialog, None)
    box.show()
    qt_app.processEvents()
    assert [button.text() for button in box.buttons()] == dialog.buttons
    assert not any(button.isDefault() or button.autoDefault() for button in box.buttons())
    QTest.keyClick(box, Qt.Key.Key_Return)
    qt_app.processEvents()
    assert box.isVisible() and box.clickedButton() is None
    QTest.keyClick(box, Qt.Key.Key_Escape)
    qt_until(qt_app, lambda: not box.isVisible())
    assert not box.isVisible() and box.clickedButton().text() == "Keep open"


def test_qt_real_channel_demo_navigation_and_renderer_recovery(qt_app, harness, monkeypatch):
    import threading
    from pathlib import Path

    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    from color_measurement import desktop
    from color_measurement.desktop_native import qt_types

    QtDesktop = qt_types().QtDesktop
    h = harness()
    assets = Path(desktop.__file__).with_name("desktop_assets").resolve()
    ui = QtDesktop(qt_app, assets)
    assert qt_app.desktopFileName() == "io.github.ferazambuja.colormeasurement"
    icon = ui.window.windowIcon().pixmap(64, 64).toImage()
    assert not icon.isNull()
    assert icon.pixelColor(8, 32).name() == "#181a1e"
    h.host.ui = ui
    h.host.dispatch_main = ui.queue
    ui.bind(h.host)
    received, requests, opened = [], [], []
    original = h.host.receive

    def receive(body):
        assert threading.current_thread() is threading.main_thread()
        requests.append(body)
        original(body)

    h.host.receive = receive
    # This tests real QWebChannel and both script worlds, not a mock transport.
    ui.page.load(QUrl.fromLocalFile(str(assets / "index.html")))

    def javascript(script):
        values = []
        ui.page.runJavaScript(script, values.append)
        qt_until(qt_app, lambda: bool(values))
        return values[0]

    try:
        qt_until(qt_app, lambda: any(body["method"] == "snapshot" for body in requests))
        delivered = []
        ui.evaluate(
            "window.qtDeliveryProof = 'confirmed'; null",
            lambda value, error: delivered.append((value, error)),
        )
        qt_until(qt_app, lambda: bool(delivered))
        assert delivered[0][1] is None, repr(delivered)
        assert javascript("window.qtDeliveryProof") == "confirmed"
        assert not h.failures, h.failures
        assert javascript("typeof window.qt") == "undefined"
        assert javascript("typeof window.ownerTransport.postMessage") == "function"
        h.create(h.assets / "demo-batch")
        # Session snapshots pass through native serialization and the actual renderer.
        ui.page.runJavaScript(
            "window.ownerTransport.postMessage({id:'qt-proof',method:'snapshot',params:{}});true",
            received.append,
        )
        qt_until(qt_app, lambda: any(body["id"] == "qt-proof" for body in requests))
        # Refuse foreign requests before calling the session owner.
        original_url = ui.page.url
        monkeypatch.setattr(ui.page, "url", lambda: QUrl.fromLocalFile("/foreign/index.html"))
        failures = []
        reply = h.host.reply_value
        h.host.reply_value = lambda *a, **k: failures.append((a, k))
        ui.bridge.submit('{"id":"foreign","method":"snapshot","params":{}}')
        assert failures[0][0] == ("invalid",)
        assert "bundled workspace" in str(failures[0][1]["error"])
        h.host.reply_value = reply
        monkeypatch.setattr(ui.page, "url", original_url)
        monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
        page = ui.page
        click = page.NavigationType.NavigationTypeLinkClicked
        other = page.NavigationType.NavigationTypeOther
        assert page.acceptNavigationRequest(
            QUrl.fromLocalFile(str(assets / "index.html")), other, True
        )
        assert not page.acceptNavigationRequest(
            QUrl.fromLocalFile(str(assets / "index.html")), other, False
        )
        assert not page.acceptNavigationRequest(QUrl("https://example.invalid/"), other, True)
        assert opened == []
        assert not page.acceptNavigationRequest(QUrl("https://example.invalid/"), click, True)
        assert opened == ["https://example.invalid/"]
        assert page.createWindow(None) is None and page.chooseFiles() == []
        scripts = list(page.scripts().toList())
        assert len(scripts) == 2 and all(not script.runsOnSubFrames() for script in scripts)
        assert ui.profile.isOffTheRecord()
        h.host.close_phase = "renderer"
        before = len(requests)
        ui._crashed(None, 1)
        assert h.host.close_phase == "idle" and not h.session.batch._closed
        qt_until(qt_app, lambda: len(requests) > before)
        # All native close events veto termination until the controller confirms it.
        ui.window.show()
        ui.window.close()
        assert ui.window.isVisible() and h.host.close_phase == "renderer"
        h.host.close_phase = "idle"
    finally:
        # Owned test window only; no production workers or physical devices are touched.
        h.host.confirmed = True
        ui.window.close()
        from PySide6.QtCore import QCoreApplication, QEvent

        ui.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qt_app.processEvents()


@pytest.mark.parametrize("fault", [None, "bytecode", "resource", "symlink", "trailing"])
def test_frozen_code_is_checked_against_source_without_import_or_execution(tmp_path, fault):
    import importlib.util
    import marshal
    import runpy
    from pathlib import Path

    functions = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "tools/build_desktop_app.py")
    )
    source = tmp_path / "source"
    source.mkdir()
    text = b'raise RuntimeError("This code must never execute")\n'
    (source / "__init__.py").write_bytes(text)
    (source / "resource.txt").write_bytes(b"Original resource")
    app = tmp_path / "application"
    target = app / "_internal/color_measurement"
    target.mkdir(parents=True)
    code = target / "__init__.pyc"
    code.write_bytes(
        importlib.util.MAGIC_NUMBER
        + b"\0" * 12
        + marshal.dumps(compile(text, "private-build-path", "exec"))
    )
    (target / "resource.txt").write_bytes(b"Original resource")
    if fault == "bytecode":
        code.write_bytes(code.read_bytes()[:16] + marshal.dumps(compile("pass", "x", "exec")))
    elif fault == "resource":
        (target / "resource.txt").write_bytes(b"Changed")
    elif fault == "trailing":
        code.write_bytes(code.read_bytes() + b"unexpected bytes")
    elif fault == "symlink":
        external = tmp_path / "external.pyc"
        external.write_bytes(code.read_bytes())
        code.unlink()
        try:
            code.symlink_to(external)
        except OSError as error:
            pytest.skip(f"Account cannot create source-escape fixture: {error}")
    if fault is None:
        result = functions["verify_package_code"](app, source)
        assert set(result) == {"__init__.py", "resource.txt"}
    else:
        with pytest.raises(ValueError):
            functions["verify_package_code"](app, source)


@pytest.mark.parametrize("fault", [None, "changed", "symlink"])
def test_frozen_sysconfig_drops_a_home_interpreter_prefix_and_refuses_build_paths(
    tmp_path, fault
):
    import importlib.util
    import marshal
    import runpy
    from pathlib import Path

    functions = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "tools/build_desktop_app.py")
    )
    prefix = "/home/builder/.local/share/uv/python/cpython-3.11-linux"
    source = (
        "build_time_vars = {'prefix': %r, 'LIBDIR': %r, 'CC': 'clang'}\n"
        % (prefix, prefix + "/lib")
    ).encode()
    app = tmp_path / "application"
    (app / "_internal").mkdir(parents=True)
    frozen = app / "_internal/_sysconfigdata__linux_x86_64-linux-gnu.pyc"
    compiled = source if fault != "changed" else source.replace(b"clang", b"gcc")
    frozen.write_bytes(
        importlib.util.MAGIC_NUMBER
        + (1).to_bytes(4, "little")
        + importlib.util.source_hash(compiled)
        + marshal.dumps(compile(compiled, frozen.with_suffix(".py").name, "exec"))
    )
    with pytest.raises(ValueError, match="build-host path: _internal/_sysconfigdata_"):
        functions["refuse_build_paths"](app, [prefix])
    if fault == "symlink":
        external = tmp_path / "external.pyc"
        frozen.rename(external)
        try:
            frozen.symlink_to(external)
        except OSError as error:
            pytest.skip(f"Account cannot create source-escape fixture: {error}")
    if fault:
        with pytest.raises(ValueError, match="sysconfig data"):
            functions["replace_frozen_prefix"](frozen, source, prefix)
        return
    functions["replace_frozen_prefix"](frozen, source, prefix)
    functions["refuse_build_paths"](app, [prefix])
    raw = frozen.read_bytes()
    namespace = {}
    exec(marshal.loads(raw[16:]), namespace)
    assert namespace["build_time_vars"] == {
        "prefix": "/install", "LIBDIR": "/install/lib", "CC": "clang"
    }
    patched = source.replace(prefix.encode(), b"/install")
    assert raw[4:16] == (1).to_bytes(4, "little") + importlib.util.source_hash(patched)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows sysconfig has no data module")
def test_frozen_sysconfig_of_this_interpreter_keeps_no_home_prefix(tmp_path):
    import importlib.util
    import marshal
    import runpy
    from pathlib import Path

    functions = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "tools/build_desktop_app.py")
    )
    data = functions["sysconfig_data"]()
    source = Path(data.__file__).read_bytes()
    app = tmp_path / "application"
    (app / "_internal").mkdir(parents=True)
    frozen = app / "_internal" / (data.__name__ + ".pyc")
    frozen.write_bytes(
        importlib.util.MAGIC_NUMBER
        + (1).to_bytes(4, "little")
        + importlib.util.source_hash(source)
        + marshal.dumps(compile(source, frozen.with_suffix(".py").name, "exec"))
    )
    functions["neutralize_sysconfig"](app)
    prefix = data.build_time_vars["prefix"].encode()
    home = prefix in [p.encode() for p in functions["home_interpreter_prefixes"]()]
    assert (prefix in frozen.read_bytes()) is not home
    functions["refuse_build_paths"](app, functions["home_interpreter_prefixes"]())


def test_repeated_renderer_crashes_stop_reloading_and_keep_the_owner(tmp_path, harness):
    h = harness()
    h.create(tmp_path / "crash-batch")
    for _ in range(3):
        h.host.renderer_terminated()
    assert h.web.reloads == 2
    assert h.failures[-1][0] == "The workspace renderer keeps stopping"
    assert not h.session.batch._closed and not h.host.confirmed


def test_dead_renderer_close_uses_native_exact_confirmation_instead_of_stalling(tmp_path, harness):
    h = harness(answers=[2, 1])
    h.create(tmp_path / "native-close")

    def dead(script, callback=None):
        if callback:
            callback(None, ValueError("Workspace renderer is unavailable"))

    h.web.evaluate = dead
    h.host.request_close()
    h.main_queue.run_until(lambda: h.host.confirmed)
    assert h.session.batch._closed and h.application.terminated == [None]
    assert len(h.alerts.shown) == 2
    assert "cannot be recovered" in h.alerts.shown[0][1]
    assert h.alerts.shown[1][0] == "Close this batch?"


def test_package_builder_checks_current_native_inputs_before_any_staging(tmp_path, monkeypatch):
    import platform
    import runpy
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    from color_measurement import native_receipt
    from color_measurement.discovery import InstrumentBuild

    root = Path(__file__).resolve().parents[1]
    script = runpy.run_path(str(root / "tools/build_desktop_app.py"))
    report = {"build_system": platform.system()}
    monkeypatch.setattr(
        InstrumentBuild,
        "from_report",
        lambda path: SimpleNamespace(_client=SimpleNamespace(_build_report=report)),
    )
    checked = []

    def refuse_current_inputs(value, *, source_root):
        checked.append((value, source_root))
        raise RuntimeError("Native source has changed since this build")

    monkeypatch.setattr(native_receipt, "check_build_sources", refuse_current_inputs)
    output = tmp_path / "output"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "builder",
            "--output",
            str(output),
            "--build",
            str(tmp_path / "build"),
            "--instrument-build-report",
            str(tmp_path / "report.json"),
        ],
    )
    with pytest.raises(RuntimeError, match="Native source has changed"):
        script["main"]()
    assert checked == [(report, root)] and not output.exists()


@pytest.mark.parametrize("fault", [None, "missing", "changed"])
def test_corresponding_runtime_source_set_requires_actual_matching_archives(
    tmp_path, monkeypatch, fault
):
    import hashlib
    import runpy
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = runpy.run_path(str(root / "tools/collect_runtime_sources.py"))
    folder = tmp_path / "tools/desktop_runtime_licenses"
    folder.mkdir(parents=True)
    (folder / "QT-ORIGIN.json").write_text(
        json.dumps(
            {
                "archives": [
                    {
                        "filename": "source.tar.xz",
                        "sha256": hashlib.sha256(b"retained source").hexdigest(),
                    }
                ]
            }
        )
    )
    archive = tmp_path / "source.tar.xz"
    if fault != "missing":
        archive.write_bytes(b"changed source" if fault == "changed" else b"retained source")
    function = script["verify_sources"]
    monkeypatch.setitem(function.__globals__, "ROOT", tmp_path)
    if fault:
        with pytest.raises(ValueError):
            function(tmp_path)
    else:
        assert function(tmp_path)["archives"][0]["filename"] == archive.name


@pytest.mark.parametrize("name", ["science.py", "presentation.py"])
def test_frozen_verification_requires_the_plaintext_freshness_inputs(tmp_path, name):
    import importlib.util
    import marshal
    import runpy
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    verify = runpy.run_path(str(root / "tools/build_desktop_app.py"))["verify_package_code"]
    source = tmp_path / "source"
    source.mkdir()
    payload = b"value = 1\n"
    (source / name).write_bytes(payload)
    application = tmp_path / "application"
    package = application / "_internal/color_measurement"
    package.mkdir(parents=True)
    (package / Path(name).with_suffix(".pyc")).write_bytes(
        importlib.util.MAGIC_NUMBER + b"\0" * 12 + marshal.dumps(compile(payload, name, "exec")))
    plaintext = package / name
    plaintext.write_bytes(payload)
    assert set(verify(application, source)) == {name}
    plaintext.unlink()
    with pytest.raises(ValueError, match="freshness source is missing"):
        verify(application, source)


@pytest.mark.parametrize(("encoding", "present"), [
    ("utf-8", True), ("utf-16-le", True), ("utf-16-be", True), ("utf-8", False),
])
def test_package_scan_refuses_encoded_interpreter_paths(tmp_path, encoding, present):
    import runpy
    from pathlib import Path

    functions = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "tools/build_desktop_app.py")
    )
    prefix = r"C:\Users\Builder\.venv"
    application = tmp_path / "application"
    application.mkdir()
    content = (prefix if present else "unrelated runtime data").encode(encoding)
    (application / "runtime.dat").write_bytes(content)
    if present:
        with pytest.raises(ValueError, match="build-host path: runtime.dat"):
            functions["refuse_build_paths"](application, [prefix])
    else:
        functions["refuse_build_paths"](application, [prefix])
