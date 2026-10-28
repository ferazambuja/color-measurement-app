"""The device-agnostic polled frontend: one adapter, any ArgyllCMS instrument."""

import hashlib
import json
import os
import time
from pathlib import Path

import pytest
from conftest import native_display_unsupported, supplied_stub

from color_measurement.argyll_capability import mode_word, supported_modes
from color_measurement.instrument import Instrument
from color_measurement.instrument_frontend import InstrumentFrontend
from color_measurement.native_receipt import native_worker_name

COLORIMETER = "X-Rite i1 DisplayPro, ColorMunki Display"


@pytest.mark.parametrize("persona,bins", [("pr655", 101), ("pr670", 201)])
def test_pr_ambient_fixture_reaches_a_qualified_irradiance_reading(persona, bins):
    """The ambient actor must reach the shared consumer, rather than record refusal."""
    path = (Path(__file__).parent / "fixtures" / "generic_frontend" / persona
            / "ambient_spot.spot.json")
    capture = json.loads(path.read_text())
    result = capture["result"]
    assert capture["request"]["mode"] == "ambient_spot"
    assert result["status"] == "completed" and len(result["patches"]) == 1
    observation = result["patches"][0]["observation"]
    quantities = observation["backend_reported"]
    assert quantities["xyz"]["unit"] == "lux"
    assert quantities["spectrum"]["quantity"] == "spectral_irradiance"
    assert quantities["spectrum"]["unit"] == "mW/(m^2 nm)"
    assert len(quantities["spectrum"]["values"]) == bins
    facts = json.loads(dict(observation["configuration"])["native_facts_json"])
    assert facts["capture"]["valid"] and facts["capture"]["geometry_valid"]
    assert facts["capture"]["xyz_publication"]["unit"] == "lux"
    assert facts["capture"]["spectrum"]["published_unit"] == "mW/(m^2.nm)"


def test_native_diagnostic_prepare_is_inert_and_does_not_change_scientific_request(tmp_path):
    helper = tmp_path / "inert-worker"
    helper.write_bytes(b"Inert authenticated helper input; never executed")
    front = InstrumentFrontend(
        Instrument(helper, COLORIMETER,
                   expected_helper_sha256=hashlib.sha256(helper.read_bytes()).hexdigest()),
        mode_capabilities=mode_word("emissive_spot", spectral=False),
        trigger_capabilities=1, filter_capabilities=0, provenance="simulated",
    )
    descriptor = json.loads(front.describe_json())
    request = json.dumps(dict(
        schema_version=1, device_id=descriptor["device_id"],
        capability_id=descriptor["capability_id"],
        mode="emissive_spot", workflow="spot", patch_ids=["P1"], options={}, layout={},
    ))
    plain = front.prepare(request)
    target = tmp_path / "diagnostic.log"
    logged = front.prepare(request, native_diagnostic_target=target, native_debug_level=8)
    assert not target.exists()
    assert plain.native_diagnostics() is None and plain.native_diagnostic_bytes() is None
    assert logged.native_diagnostics()["state"] == "prepared"
    assert logged.native_diagnostic_bytes() is None
    assert plain.request_json == logged.request_json
    assert json.loads(front.describe_json()) == descriptor
    assert descriptor["limits"]["native_diagnostics"]["available"] is True
    argv = logged._meter._argv("emissive_spot", False, (), {})
    assert argv[-2:] == ["--native-debug-level", "8"]
    assert "--native-debug-level" not in plain._meter._argv("emissive_spot", False, (), {})
    with pytest.raises(ValueError, match="owned target"):
        front.prepare(request, native_debug_level=8)
    logged.cancel(logged.operation_id)
    metadata = logged.native_diagnostics()
    assert logged.done and metadata["state"] == "not_started"
    assert metadata["worker_started"] is False and metadata["sha256"] is None
    assert metadata["received_bytes"] == metadata["retained_bytes"] == 0
    assert logged.native_diagnostic_bytes() is None and not target.exists()

    changed = front.prepare(request, native_diagnostic_target=target, native_debug_level=8)
    helper.write_bytes(b"Changed after prepare, so no executable can start")
    changed.dispatch()
    deadline = time.monotonic() + 3
    while not changed.done:
        assert time.monotonic() < deadline
        time.sleep(0.005)
    assert json.loads(changed.result_json())["status"] == "failed"
    assert changed.native_diagnostics()["state"] == "not_started"
    assert changed.native_diagnostics()["worker_started"] is False
    assert not target.exists()


@pytest.mark.parametrize("outcome,queued_prompt,helper_gone", [
    ("read_sample", None, False), ("cancelled", None, False), ("completed", None, False),
    ("read_sample", "instruction_required", False),
    # session.c's answer() takes the first cancel as the queued request's reply,
    # fails "cancelled" and exits, so the supervisor's repeat meets a closed pipe.
    ("cancelled", "instruction_required", True),
    ("cancelled", "read_allowance_required", True),
])
@pytest.mark.parametrize("interruption", ["timeout", "cancelled"])
def test_stop_race_preserves_native_evidence_and_actual_stop(
    outcome, queued_prompt, helper_gone, interruption, monkeypatch,
):
    """Exercise the real client and frontend; only transport and time are scripted.

    An unrelated native failure after a stop request is not acknowledgment. A
    complete late reading still belongs beside the timeout or cancellation, and a
    helper that already stopped is not a new failure.
    """
    from types import SimpleNamespace

    import color_measurement.instrument as module

    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    envelope = {"protocol_version": 1, "provenance": "simulated"}
    events = [dict(envelope, event="connected", serial="TEST",
                   serial_encoding="utf8", spectral=False,
                   display_types=native_display_unsupported(),
                   baseline_mode_checks={"scope": "initialized_before_mode_configuration",
                       "mode_capabilities": 0x10018,
                       "checks": [{"mode_word": 0x10018, "native_code": 0}]},
                   mode=mode_word("emissive_spot", spectral=False)),
              dict(envelope, event="calibration_ready", needed_calibrations=0,
                   available_calibrations=0, remaining_calibrations=0,
                   status_source="driver", skipped=False, performed=False,
                   initial_calibration_suppressed=False)]
    if queued_prompt == "instruction_required":
        events.append(dict(envelope, event="instruction_required",
                           instruction="sample_position", request_id="queued-position"))
    elif queued_prompt:
        events.append(dict(envelope, event="read_allowance_required"))
    if outcome == "completed":
        events.append(dict(envelope, event="result", backend_reported={
            "mtype": 1, "mcond": 0, "XYZ_v": 1, "XYZ": [1, 2, 3],
            "spectrum": None, "duration": 0}))
    else:
        events.append(dict(envelope, event="error", stage=outcome, native_code=17,
                           native_text="original driver diagnostic", native_text_truncated=True))
    events.append(dict(envelope, event="terminal",
                       status="completed" if outcome == "completed" else "failed"))

    class Session:
        stopped = "exited"
        sent = []

        def receive_message(self, **kwargs):
            event = events.pop(0)
            if event["event"] == "calibration_ready":
                if interruption == "timeout":
                    clock.now = 1.1
                else:
                    self.cancel.set()
            return event

        def send(self, *args):
            if helper_gone and self.sent:
                raise BrokenPipeError(32, "Broken pipe")
            self.sent.append(args)

        def finish_messages(self, *, expected_returncode):
            assert expected_returncode == (0 if outcome == "completed" else 1)

        def close(self):
            pass

    session = Session()
    def open_session(meter, *args):
        session.cancel = meter.cancel
        return session
    monkeypatch.setattr(Instrument, "_open_session", open_session)
    front = InstrumentFrontend(Instrument("/never-started", COLORIMETER, timeout=1),
        mode_capabilities=mode_word("emissive_spot", spectral=False),
        trigger_capabilities=1, filter_capabilities=0, provenance="simulated")
    _, result = _drive(front, "emissive_spot")
    assert [command[0] for command in session.sent] == (
        ["cancel"] * (2 if queued_prompt and not helper_gone else 1))
    if interruption == "timeout":
        assert result["status"] == "failed" and result["error_stage"] == "timeout"
    elif outcome == "read_sample":
        assert result["status"] == "failed" and result["error_stage"] == "read_sample"
    else:
        assert result["status"] == "cancelled"
    context = dict(result["configuration"])
    assert context["stop_requested_by"] == (
        "host_deadline" if interruption == "timeout" else "client")
    assert context["stop_acknowledged_by"] == ("helper" if outcome == "cancelled" else "none")
    assert context["worker_stop"] == "exited"
    assert context["device_completion"] == ("completed" if outcome == "completed" else "unknown")
    if outcome == "completed":
        assert len(result["patches"]) == 1
        assert result["patches"][0]["observation"]["backend_reported"]["xyz"]["values"] == [1, 2, 3]
    else:
        assert result["patches"] == []
        assert context["error_native_text"] == "original driver diagnostic"
        assert context["error_native_text_truncated"] == "true"


def _bound(worker, instrument, serial, mode, environment, spectral):
    """Bind once and take the instrument's own three capability words.

    Taken from the binding rather than written down, so the test cannot drift
    from what the instrument actually reports.
    """
    meter = Instrument(helper=worker, instrument=instrument, expected_serial=serial,
                       timeout=30.0, environment=environment,
                expected_helper_sha256=hashlib.sha256(
                    Path(worker).read_bytes()).hexdigest())
    measurement = meter.measure(mode, spectral=spectral)
    connected = next(e for e in measurement.events if e["event"] == "connected")
    return meter, connected


def _frontend(worker, instrument, serial, mode, environment, spectral=False):
    meter, connected = _bound(worker, instrument, serial, mode, environment, spectral)
    return InstrumentFrontend(
        meter,
        mode_capabilities=connected["mode_capabilities"],
        trigger_capabilities=connected["trigger_capabilities"],
        filter_capabilities=connected["filter_capabilities"],
        baseline_mode_checks=connected["baseline_mode_checks"],
        display_types=connected["display_types"],
        strip_policies=connected["strip_policies"],
        provenance="simulated",
        serial=serial,
    )


def _drive(front, mode, workflow="spot", patch_ids=("A1",), options=None,
           layout=None, seconds=12.0):
    snapshot = json.loads(front.describe_json())
    request = json.dumps({
        "schema_version": 1, "device_id": snapshot["device_id"],
        "capability_id": snapshot["capability_id"], "mode": mode,
        "workflow": workflow, "patch_ids": list(patch_ids), "options": options or {},
        "layout": layout or {},
    })
    operation = front.prepare(request)
    try:
        operation.dispatch()
        deadline = time.monotonic() + seconds
        while not operation.done and time.monotonic() < deadline:
            text = operation.poll_event_json()
            if text:
                event = json.loads(text)
                if event["kind"] == "instruction_required":
                    payload = event["payload"]
                    operation.respond(operation.operation_id, payload["request_id"], "accept")
            time.sleep(0.002)
        assert operation.done, "operation did not finish"
        return snapshot, json.loads(operation.result_json())
    finally:
        operation.close()


# -- the descriptor is DERIVED ---------------------------------------------------

def test_the_descriptor_comes_from_the_instrument_not_from_a_table():
    """The whole point. Two different capability words, two different offers, and
    no branch on a device name anywhere between them.

    This is Argyll's own model: `new_inst()` returns one vtable, the application
    calls `capabilities()` and offers what the answer allows. `spotread` is the
    same program for 38 instruments for exactly this reason.
    """
    meter = Instrument(helper="/x", instrument=COLORIMETER, expected_serial="SN-1")
    # An i1 Display 3's word: three emission routes, no spectrum.
    colorimeter = InstrumentFrontend(
        meter, mode_capabilities=93464, trigger_capabilities=234881792,
        filter_capabilities=0, provenance="simulated")
    offered = [entry["mode"] for entry in json.loads(colorimeter.describe_json())["modes"]]
    assert "emissive_spot" in offered and "ambient_spot" in offered
    assert not any(name.startswith("reflective") for name in offered)
    assert json.loads(colorimeter.describe_json())["spectral"] is False

    # A reflective spectrophotometer's word, same class, no code change.
    from color_measurement.argyll_capability import MODE_FLAGS, MODE_ROUTES
    spectro = InstrumentFrontend(
        meter,
        mode_capabilities=MODE_ROUTES["ref_spot"] | MODE_ROUTES["ref_strip"]
        | MODE_FLAGS["spectral"],
        trigger_capabilities=0, filter_capabilities=0, provenance="simulated")
    snapshot = json.loads(spectro.describe_json())
    offered = [entry["mode"] for entry in snapshot["modes"]]
    assert "reflective_spot" in offered and "reflective_strip" in offered
    assert not any(name.startswith("emissive") for name in offered)
    assert snapshot["spectral"] is True


def test_a_reflective_condition_is_offered_only_when_its_filter_is():
    """Every named condition needs its filter in the third capability word."""
    from color_measurement.argyll_capability import CAPABILITY3, MODE_FLAGS, MODE_ROUTES

    meter = Instrument(helper="/x", instrument=COLORIMETER, expected_serial="SN-1")
    primary = MODE_ROUTES["ref_spot"] | MODE_FLAGS["spectral"]
    bare = [e["mode"] for e in json.loads(InstrumentFrontend(
        meter, mode_capabilities=primary, trigger_capabilities=0,
        filter_capabilities=0, provenance="simulated").describe_json())["modes"]]
    assert "reflective_spot" in bare
    assert "reflective_spot_m0" not in bare
    assert "reflective_spot_m2" not in bare

    with_uvcut = [e["mode"] for e in json.loads(InstrumentFrontend(
        meter, mode_capabilities=primary, trigger_capabilities=0,
        filter_capabilities=CAPABILITY3["filter_UVCut"],
        provenance="simulated").describe_json())["modes"]]
    assert "reflective_spot_m2" in with_uvcut
    assert "reflective_spot_m0" not in with_uvcut


@pytest.mark.parametrize("mode, options", [
    ("reflective_spot_m0", {}),
    ("reflective_spot", {"filter": "m0"}),
    ("reflective_spot", {"filter": "custom"}),
])
def test_fixed_uvcut_refuses_unadvertised_conditions_before_start(mode, options):
    from color_measurement.argyll_capability import CAPABILITY3, MODE_ROUTES

    front = InstrumentFrontend(
        Instrument(helper="/unused", instrument="X-Rite i1 Pro 2"),
        mode_capabilities=MODE_ROUTES["ref_spot"], trigger_capabilities=0,
        filter_capabilities=CAPABILITY3["filter_UVCut"], provenance="simulated")
    descriptor = json.loads(front.describe_json())
    request = {"schema_version": 1, "device_id": descriptor["device_id"],
               "capability_id": descriptor["capability_id"], "mode": mode,
               "workflow": "spot", "patch_ids": ["sample"], "options": options,
               "layout": {}}
    with pytest.raises(ValueError, match="not advertised"):
        front._validate_request(json.dumps(request))
    request.update(mode="reflective_spot_m2", options={})
    _, _, configuration = front._validate_request(json.dumps(request))
    assert configuration.flags(request["mode"])["filter"] == 4


@pytest.mark.parametrize("condition, description", [
    ("m0", "legacy illumination; UV unspecified"),
    ("m1", "D50; controlled UV"),
    ("m2", "UV excluded"),
    ("m3", "polarized; UV excluded"),
])
def test_reflective_mode_labels_explain_the_measurement_condition(condition, description):
    from color_measurement.argyll_capability import CAPABILITY3, MODE_ROUTES

    descriptor = _descriptor(mode_capabilities=MODE_ROUTES["ref_spot"],
        filter_capabilities=sum(CAPABILITY3[name] for name in
                                ("filter_none", "filter_D50", "filter_UVCut", "filter_pol")))
    mode = next(mode for mode in descriptor["modes"]
                if mode["mode"] == f"reflective_spot_{condition}")
    assert mode["label"] == f"Reflective spot, {condition.upper()} ({description})"


def test_supported_modes_is_gated_on_all_three_words():
    from color_measurement.argyll_capability import CAPABILITY3, MODE_FLAGS, MODE_ROUTES

    # A route absent from the word is never offered.
    assert supported_modes(MODE_ROUTES["ref_spot"], 0)
    assert not any(n.startswith("emissive") for n in supported_modes(MODE_ROUTES["ref_spot"], 0))
    # A name carrying a mode modifier needs that flag too.
    assert "emissive_spot_nonadaptive" not in supported_modes(MODE_ROUTES["emis_spot"], 0)
    assert "emissive_spot_nonadaptive" in supported_modes(
        MODE_ROUTES["emis_spot"] | MODE_FLAGS["emis_nonadaptive"], 0)
    # And a reflective condition needs its filter.
    assert "reflective_spot_m3" in supported_modes(
        MODE_ROUTES["ref_spot"], CAPABILITY3["filter_pol"])


# -- requests are checked against the descriptor, not a device list --------------

def test_a_mode_the_instrument_does_not_advertise_is_refused():
    meter = Instrument(helper="/x", instrument=COLORIMETER, expected_serial="SN-1")
    front = InstrumentFrontend(meter, mode_capabilities=93464,
                               trigger_capabilities=234881792, filter_capabilities=0,
                               provenance="simulated")
    snapshot = json.loads(front.describe_json())
    for mode, message in (("reflective_spot", "not advertised"),
                          ("not_a_mode_at_all", "not advertised")):
        request = json.dumps({
            "schema_version": 1, "device_id": snapshot["device_id"],
            "capability_id": snapshot["capability_id"], "mode": mode,
            "workflow": "spot", "patch_ids": ["A1"], "options": {}, "layout": {}})
        with pytest.raises(ValueError, match=message):
            front.prepare(request)


def test_a_stale_capability_snapshot_is_refused():
    meter = Instrument(helper="/x", instrument=COLORIMETER, expected_serial="SN-1")
    front = InstrumentFrontend(meter, mode_capabilities=93464,
                               trigger_capabilities=234881792, filter_capabilities=0,
                               provenance="simulated")
    snapshot = json.loads(front.describe_json())
    request = json.dumps({
        "schema_version": 1, "device_id": snapshot["device_id"],
        "capability_id": "0" * 64, "mode": "emissive_spot",
        "workflow": "spot", "patch_ids": ["A1"], "options": {}, "layout": {}})
    with pytest.raises(ValueError, match="Stale or different capability snapshot"):
        front.prepare(request)


def test_a_snapshot_that_changes_between_validation_and_ownership_is_refused(monkeypatch):
    """`_start_worker` re-reads the descriptor under the description lock: a query
    completing between a request's validation and its ownership changes the
    `capability_id` the request was validated on. Here the second description read
    is the changed one, and the start must fail before any worker exists."""
    import color_measurement.instrument as module

    meter = Instrument(helper="/x", instrument=COLORIMETER, expected_serial="SN-1")
    front = InstrumentFrontend(meter, mode_capabilities=93464,
                               trigger_capabilities=234881792, filter_capabilities=0,
                               provenance="simulated")
    snapshot = json.loads(front.describe_json())
    request = json.dumps({
        "schema_version": 1, "device_id": snapshot["device_id"],
        "capability_id": snapshot["capability_id"], "mode": "emissive_spot",
        "workflow": "spot", "patch_ids": ["A1"], "options": {}, "layout": {}})
    genuine = InstrumentFrontend.describe_json
    reads = []

    def described(self, *args, **kwargs):
        text = genuine(self, *args, **kwargs)
        reads.append(len(reads))
        if len(reads) == 1:
            return text  # validation sees the identity the client holds
        changed = json.loads(text)
        changed["capability_id"] = "0" * 64
        return json.dumps(changed)

    monkeypatch.setattr(InstrumentFrontend, "describe_json", described)
    monkeypatch.setattr(module, "HelperSession",
                        lambda *a, **k: pytest.fail("A worker was launched"))
    operation = front.prepare(request)
    operation.dispatch()
    result = json.loads(operation.result_json())
    assert result["status"] == "failed" and result["software_stopped"]
    assert "Stale or different capability snapshot" in result["error"]
    assert operation._thread is None
    assert reads == [0, 1], "validation then the under-lock re-check, nothing else"


def test_the_capability_id_changes_with_the_instruments_own_words():
    meter = Instrument(helper="/x", instrument=COLORIMETER, expected_serial="SN-1")
    def identity(primary):
        return json.loads(InstrumentFrontend(
            meter, mode_capabilities=primary, trigger_capabilities=0,
            filter_capabilities=0, provenance="simulated").describe_json())["capability_id"]
    from color_measurement.argyll_capability import MODE_ROUTES
    assert identity(MODE_ROUTES["ref_spot"]) != identity(MODE_ROUTES["emis_spot"])


# -- and it actually drives an instrument ----------------------------------------

def test_a_colorimeter_produces_an_operation_result_with_its_own_units():
    worker = supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")
    environment = dict(os.environ, CM_COLORIMETER_FIXTURE="success")
    front = _frontend(worker, COLORIMETER, "CM-COLORIMETER-0001",
                      "emissive_spot", environment)
    snapshot, result = _drive(front, "emissive_spot")
    assert result["status"] == "completed"
    assert len(result["patches"]) == 1
    xyz = result["patches"][0]["observation"]["backend_reported"]["xyz"]
    assert xyz["values"] == [21.5, 24.0, 9.75]
    # Keyed on the reported measurement type, so an emissive read is absolute.
    assert xyz["unit"] == "cd/m^2"
    assert xyz["illuminant"] == "none (emissive)"
    assert result["patches"][0]["observation"]["backend_reported"]["spectrum"] is None
    assert dict(
        tuple(pair) for pair in result["configuration"]
    )["capability_id"] == snapshot["capability_id"]


def test_the_same_frontend_drives_a_different_instrument_unchanged():
    """One adapter, a second instrument, no code between them.

    The i1Pro3 and i1Pro adapters are 1,419 lines that each serve one family.
    This is the claim that replaces them: a spectrophotometer with an entirely
    different capability word goes through the same class.
    """
    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    environment = dict(os.environ, CM_CHART_PERSONA="dtp41", CM_CHART_FIXTURE="success")
    front = _frontend(worker, "X-Rite DTP41", "CM-CHART-0001",
                      "reflective_spot", environment, spectral=True)
    snapshot = json.loads(front.describe_json())
    offered = [entry["mode"] for entry in snapshot["modes"]]
    assert snapshot["spectral"] is True, "a spectrophotometer must advertise a spectrum"
    assert any(name.startswith("reflective") for name in offered)
    _, result = _drive(front, "reflective_spot")
    assert result["status"] == "completed"
    reported = result["patches"][0]["observation"]["backend_reported"]
    # A reflective read is relative and computed under upstream's assumed D50.
    assert reported["xyz"]["unit"] == "1"
    assert reported["xyz"]["illuminant"] == "D50"
    assert reported["spectrum"] is not None
    assert reported["spectrum"]["quantity"] == "reflectance"


def test_an_advertised_ambient_mode_completes_through_the_shared_frontend():
    """The substituted colorimeter's ambient mode uses the same polled frontend."""
    worker = supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")
    environment = dict(os.environ, CM_COLORIMETER_FIXTURE="success")
    front = _frontend(worker, COLORIMETER, "CM-COLORIMETER-0001",
                      "emissive_spot", environment)
    # The descriptor advertises ambient; the operation completes that route.
    snapshot = json.loads(front.describe_json())
    assert "ambient_spot" in [entry["mode"] for entry in snapshot["modes"]]
    _, result = _drive(front, "ambient_spot")
    assert result["status"] == "completed"


def test_compiled_bind_failure_reaches_the_frontend_result():
    """The native helper's bind stage survives the polled frontend boundary."""
    worker = supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")
    serial = "CM-COLORIMETER-0001"
    _, connected = _bound(worker, COLORIMETER, serial, "emissive_spot",
                          dict(os.environ, CM_COLORIMETER_FIXTURE="success"), False)
    meter = Instrument(
        helper=worker, instrument=COLORIMETER, expected_serial=serial,
        timeout=30.0,
        environment=dict(os.environ, CM_COLORIMETER_FIXTURE="init_coms_failure"),
        expected_helper_sha256=hashlib.sha256(Path(worker).read_bytes()).hexdigest(),
    )
    front = InstrumentFrontend(
        meter,
        mode_capabilities=connected["mode_capabilities"],
        trigger_capabilities=connected["trigger_capabilities"],
        filter_capabilities=connected["filter_capabilities"],
        baseline_mode_checks=connected["baseline_mode_checks"],
        display_types=connected["display_types"],
        strip_policies=connected["strip_policies"],
        provenance="simulated", serial=serial,
    )
    _, result = _drive(front, "emissive_spot")
    assert result["status"] == "failed"
    assert result["error_stage"] == "init_coms"
    assert result["error_native_code"] == 0x070000  # inst_coms_fail
    assert result["patches"] == []


# -- the fixtures a client pins against --------------------------------------

def test_the_published_fixtures_reproduce_from_these_workers():
    """Reproduce pinned fixtures with the authenticated substituted workers.

    Regenerate into a temporary directory and compare under the declared rules:
    shape-guarded per-run exclusions, bounded quantity/derived-metric roundoff,
    CR chroma input validation, and conditioned complete-profile reproduction.
    Changed contracts,
    out-of-policy edits, and missing or extra files fail. Publication separately
    preserves unrounded capture bytes (generate_frontend_fixtures.py --check).
    """
    import subprocess
    import sys
    from pathlib import Path as _Path

    build = _Path(supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")).resolve().parent
    if _Path(supplied_stub("COLOR_MEASUREMENT_CHART_STUB")).resolve().parent != build:
        pytest.skip("the two workers come from different builds")
    report = json.loads((build / "report.json").read_text())
    if not {"instCR250", "instCR100", "instCR300", "instPR655", "instPR670"} <= set(
            report.get("contribution", {}).get("instrument_types_added", ())):
        pytest.skip("a build without the CR/PR series cannot produce all driver fixtures")
    root = _Path(__file__).resolve().parents[1]
    checked = subprocess.run(
        [sys.executable, str(root / "tools/generate_frontend_fixtures.py"),
         "--build", str(build), "--check"],
        cwd=root, capture_output=True, text=True, timeout=600)
    diagnostic = checked.stderr
    if len(diagnostic) > 8000:
        diagnostic = diagnostic[:6000] + "\n... stderr truncated ...\n" + diagnostic[-2000:]
    assert checked.returncode == 0, checked.stdout[-2000:] + diagnostic


def test_the_fixture_check_names_the_differing_lines(tmp_path, monkeypatch):
    """A one-off difference must be diagnosable after the check's temporary
    directory is gone, so the failure names the fields that differ and the files
    that exist on one side only, not just the file. The regeneration step is
    stubbed to the committed files; the workers are exercised by the test above."""
    import importlib.util
    import shutil
    import sys
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1]
    if str(root / "tools") not in sys.path:
        sys.path.insert(0, str(root / "tools"))
    spec = importlib.util.spec_from_file_location(
        "generate_frontend_fixtures", root / "tools/generate_frontend_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    published = tmp_path / "published"
    shutil.copytree(root / "tests/fixtures", published)
    target = published / "generic_frontend/spyder2024/emissive_spot_adaptive.spot.json"
    text = target.read_text()
    assert '"provenance": "simulated"' in text
    target.write_text(text.replace('"provenance": "simulated"', '"provenance": "edited"', 1))
    (published / "discovery" / "extra.json").write_text("{}\n")

    def committed_files(build):
        shutil.copytree(root / "tests/fixtures/generic_frontend", module.OUT)
        shutil.copytree(root / "tests/fixtures/discovery", module.DISCOVERY_OUT)
        return 0

    monkeypatch.setattr(module, "_generate", committed_files)
    monkeypatch.setattr(module, "OUT", published / "generic_frontend")
    monkeypatch.setattr(module, "DISCOVERY_OUT", published / "discovery")
    with pytest.raises(SystemExit) as failure:
        module._check(tmp_path / "unused-build")
    lines = str(failure.value).splitlines()
    assert lines[0].startswith("Published fixtures differ from these workers")
    assert any(line.strip() == "generic_frontend/spyder2024/emissive_spot_adaptive.spot.json"
               for line in lines)
    assert any(line.strip() == "at $.descriptor.provenance" for line in lines)
    assert any(line.strip() == '-"edited"' for line in lines)
    assert any(line.strip() == '+"simulated"' for line in lines)
    assert any(line.strip() == "discovery/extra.json: extra in the tree" for line in lines)
    # Unchanged files stay silent.
    assert not any("ambient_spot.spot.json" in line for line in lines)


def test_the_fixture_check_ignores_the_toolchain_but_not_a_changed_shape(monkeypatch):
    """The discovery envelopes carry the worker's own digest, which two builds of
    one authenticated source on different compilers do not share. Comparing it
    turned an Xcode update into a red gate with nothing changed; blanking it in
    any shape would let a digest that went null, or a field that vanished, pass."""
    import importlib
    from pathlib import Path as _Path

    monkeypatch.syspath_prepend(str(_Path(__file__).resolve().parents[1] / "tools"))
    blank = importlib.import_module("generate_frontend_fixtures")._per_run_blank

    def envelope(digest, nested=False):
        backend = json.dumps({"backend": {"helper_sha256": digest}})
        return json.dumps({"raw": backend}) if nested else backend

    assert blank(envelope("a" * 64)) == blank(envelope("b" * 64))
    assert blank(envelope("a" * 64, nested=True)) == blank(envelope("b" * 64, nested=True))
    assert blank(envelope("a" * 64)) != blank(envelope(None))
    assert blank(envelope("a" * 64)) != blank(envelope("a" * 63))
    assert blank(envelope("a" * 64)) != blank(json.dumps({"backend": {}}))
    # Chart provenance names both workers and the build report; each build differs.
    for key in ("description_helper_sha256", "session_helper_sha256", "build_report_sha256"):
        def provenance(digest, key=key):
            return json.dumps({"provenance": {key: digest, "version": 1}})
        assert blank(provenance("a" * 64)) == blank(provenance("b" * 64))
        assert blank(provenance("a" * 64)) != blank(provenance(None))
        assert blank(provenance("a" * 64)) != blank(json.dumps({"provenance": {"version": 1}}))


@pytest.mark.parametrize("nesting", ("direct", "raw_backend_output", "configuration"))
def test_fixture_reproduction_accepts_only_bounded_float_roundoff(tmp_path, monkeypatch, nesting):
    """Linux's native XYZ Z differed from the Mac capture by two double ULPs.

    Reproduction permits that arithmetic roundoff, including the same observation
    inside native JSON strings. It still rejects changed values, shapes, exact
    zeros, types and identities. Publication must retain the actual new bytes.
    """
    import importlib
    import math
    import re

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))
    generator = importlib.import_module("generate_frontend_fixtures")
    native_source = (Path(__file__).resolve().parents[1]
                     / "tools/native_argyll/contributions/cr_pr/files/spectro")
    for name, constants in {
        "cr250.c": {"UNIT_CHROMA": generator._CR_UNIT_CHROMA,
                    "UNIT_AXIS_NM": generator._CR_UNIT_AXIS_NM,
                    "UNIT_MIN_Y": generator._CR_UNIT_MIN_Y,
                    "UNIT_RATIO_LOW": generator._UNIT_RATIO_LOW,
                    "UNIT_RATIO_HIGH": generator._UNIT_RATIO_HIGH},
        "pr655.c": {"UNIT_MIN_RATIO": generator._UNIT_RATIO_LOW,
                    "UNIT_MAX_RATIO": generator._UNIT_RATIO_HIGH},
        "pr655.h": {"PR655_CHECK_Y": generator._PR_CHECK_Y,
                    "PR655_UNIT_CONSISTENT": 1, "PR655_UNIT_REFUSED": 3},
    }.items():
        source = (native_source / name).read_text()
        for macro, expected in constants.items():
            declarations = re.findall(rf"^#define\s+{macro}\s+([0-9.]+)\b", source, re.M)
            assert len(declarations) == 1 and float(declarations[0]) == expected
    cr_source = (native_source / "cr250.c").read_text()
    assert "double total = native[0] / 4 + native[1] / 4 + native[2] / 4;" in cr_source
    assert "dx = (dz > dx ? dz : dx) / total" in cr_source
    published = tmp_path / "published"
    monkeypatch.setattr(generator, "OUT", published / "generic_frontend")
    monkeypatch.setattr(generator, "DISCOVERY_OUT", published / "discovery")
    original = {"xyz": [68.86545614943437, 53.40405639137513, 0.09214833994606052],
                "negative": -0.09214833994606052, "tiny": math.ulp(0.0),
                "zero": 0.0, "count": 3, "available": True,
                "capability_id": "a" * 64, "label": "0.09214833994606052",
                "text": '{"value": 0.09214833994606052}', "facts_json": '{"value": 1.0}',
                "helper_sha256": "a" * 64,
                "operation_id": "11111111-1111-1111-1111-111111111111",
                "backend_reported": {"spectrum": {
                    "wavelengths_nm": [380.0, 390.0, 400.0],
                    "values": [-0.09214833994606052, math.ulp(0.0), 0.0],
                    "quantity": "spectral_radiance", "unit": "mW/(m^2 sr nm)"}},
                "native": {"spectrum": {
                    "wavelength_start_nm": 380.0, "wavelength_end_nm": 400.0,
                    "spec_n": 3, "norm": 100.0,
                    "samples": [0.09214833994606052, 0.1, 1.0]}},
                "events": [{"event": "native_spectrum", "values": [0.09214833994606052]}],
                "project_computed": {"lab": {"values": [42.0, 0.0, 0.0],
                                             "reference_white": [95.0, 100.0, 108.0]}},
                "grid": {"start_nm": 380.0, "end_nm": 400.0, "step_nm": 10.0,
                         "published_scale": 1000.0},
                "wrong_array_shape": {"XYZ": 1.0},
                "reference_white": [95.0, 100.0, 108.0], "unknown_metadata": 0.5}
    fixtures = Path(__file__).parent / "fixtures/generic_frontend"
    cr_capture = json.loads((fixtures / "cr250/emissive_spot.spot.json").read_bytes())
    cr_facts = json.loads(dict(cr_capture["result"]["patches"][0]["observation"]
                               ["configuration"])["native_facts_json"])["capture"]
    pr_capture = json.loads((fixtures / "pr670/emissive_spot.spot.json").read_bytes())
    pr_facts = json.loads(dict(pr_capture["result"]["patches"][0]["observation"]
                               ["configuration"])["native_facts_json"])
    original["cr"] = {"xyz": cr_facts["xyz"], "unit_check": cr_facts["unit_check"],
                      "spectrum": {key: value for key, value in cr_facts["spectrum"].items()
                                   if key != "values"}}
    original["pr"] = {"unit_check": pr_facts["unit_check"]}
    specbos = json.loads((fixtures / "specbos/emissive_tele.spot.json").read_bytes())[
        "result"]["patches"][0]["observation"]
    original["synthetic"] = {"provenance": specbos["provenance"],
                             "backend_reported": specbos["backend_reported"]}
    original["synthetic_raw"] = next(event for event in json.loads(
        specbos["raw_backend_output"])["events"]
        if event.get("backend_reported", {}).get("spectrum") is not None)

    def wrapped(value):
        if nesting == "raw_backend_output":
            return {nesting: json.dumps(value)}
        if nesting == "configuration":
            return {nesting: [["native_facts_json", json.dumps(value)]]}
        return value

    replacement = json.loads(json.dumps(original))

    def regenerate(build):
        generator._write(generator.OUT / "capture.json", wrapped(replacement))

    monkeypatch.setattr(generator, "_generate", regenerate)
    target = generator.OUT / "capture.json"
    generator._write(target, wrapped(original))
    retained = target.read_bytes()
    replacement["xyz"][2] = 0.09214833994606055
    replacement["backend_reported"]["spectrum"]["values"][0] = -0.09214833994606055
    replacement["native"]["spectrum"]["samples"][0] = 0.09214833994606055
    replacement["events"][0]["values"][0] = 0.09214833994606055
    replacement["project_computed"]["lab"]["values"][0] = math.nextafter(42.0, math.inf)
    # This capture's cancellation residual changes by 12,271,335 result ULPs when its
    # integrated Z changes by just one ULP. Each result must fit its own inputs.
    replacement["cr"]["unit_check"]["xyz"][2] = math.nextafter(
        original["cr"]["unit_check"]["xyz"][2], -math.inf)
    replacement["cr"]["unit_check"]["chroma_error"] = 1.1522174747012416e-08
    replacement["cr"]["unit_check"]["spectrum_mean_nm"] = 538.6667581846415
    replacement["pr"]["unit_check"]["y_ratio"] = 1.000002928257686
    replacement["synthetic"]["backend_reported"]["spectrum"]["values"][399] = 5.865411586436814e-19
    replacement["synthetic_raw"]["backend_reported"]["spectrum"]["samples"][399] = (
        5.865411586436814e-19)
    replacement["helper_sha256"] = "b" * 64
    replacement["operation_id"] = "22222222-2222-2222-2222-222222222222"
    assert generator._check(tmp_path / "unused-build") == 0
    ci_replacement = json.loads(json.dumps(replacement))
    assert target.read_bytes() == retained
    # The tolerance belongs only to reproduction, never to the publisher.
    generator._write(target, wrapped(replacement))
    assert json.loads(target.read_bytes()) == wrapped(replacement)
    generator._write(target, wrapped(original))

    for _ in range(4):
        replacement["cr"]["unit_check"]["chroma_error"] = math.nextafter(
            replacement["cr"]["unit_check"]["chroma_error"], math.inf)
    replacement["pr"]["unit_check"]["y_ratio"] = original["pr"]["unit_check"]["y_ratio"]
    replacement["cr"]["unit_check"]["spectrum_mean_nm"] = original["cr"]["unit_check"][
        "spectrum_mean_nm"]
    for _ in range(4):
        replacement["pr"]["unit_check"]["y_ratio"] = math.nextafter(
            replacement["pr"]["unit_check"]["y_ratio"], -math.inf)
        replacement["cr"]["unit_check"]["spectrum_mean_nm"] = math.nextafter(
            replacement["cr"]["unit_check"]["spectrum_mean_nm"], -math.inf)
    assert generator._check(tmp_path / "unused-build") == 0
    for _ in range(2):
        replacement["xyz"][2] = math.nextafter(replacement["xyz"][2], math.inf)
    assert generator._check(tmp_path / "unused-build") == 0
    replacement["xyz"][2] = math.nextafter(replacement["xyz"][2], math.inf)
    # Five ULPs above the original must fail, just beyond the four-ULP bound.
    invalid = [replacement]
    for key, value in (("zero", math.ulp(0.0)), ("zero", -0.0), ("tiny", -math.ulp(0.0)),
                       ("count", 3.0), ("count", 4), ("available", 1),
                       ("capability_id", "b" * 64), ("helper_sha256", "b" * 63),
                       ("helper_sha256", None), ("operation_id", "malformed"),
                       ("label", "0.09214833994606055"), ("xyz", [1, 2]),
                       ("text", '{"value": 0.09214833994606055}'),
                       ("facts_json", '{"value": 1.0, "value": 1.0}'),
                       ("zero", None), ("zero", math.nan), ("zero", math.inf)):
        invalid.append(dict(original, **{key: value}))
    invalid.append({key: value for key, value in original.items() if key != "count"})
    invalid.append(dict(original, extra="new field"))
    for owner, metric, direction in (("cr", "chroma_error", math.inf),
                                     ("cr", "spectrum_mean_nm", -math.inf),
                                     ("pr", "y_ratio", -math.inf)):
        candidate = json.loads(json.dumps(ci_replacement))
        reference = ci_replacement if metric == "chroma_error" else original
        value = reference[owner]["unit_check"][metric]
        for _ in range(5):
            value = math.nextafter(value, direction)
        candidate[owner]["unit_check"][metric] = value
        invalid.append(candidate)
    for owner, key, value in (("cr", "verdict", "mismatch"), ("pr", "verdict", 3),
                               ("pr", "computed", 2), ("pr", "discriminating", 2)):
        candidate = json.loads(json.dumps(ci_replacement))
        candidate[owner]["unit_check"][key] = value
        invalid.append(candidate)
    # Wavelength coordinates, native grid declarations, normalization, scale and
    # reference conditions remain exact even when only one double ULP changes.
    for keys in (("backend_reported", "spectrum", "wavelengths_nm", 0),
                 ("native", "spectrum", "wavelength_start_nm"),
                 ("native", "spectrum", "wavelength_end_nm"),
                 ("native", "spectrum", "norm"), ("grid", "start_nm"),
                 ("grid", "end_nm"), ("grid", "step_nm"), ("grid", "published_scale"),
                 ("project_computed", "lab", "reference_white", 0),
                 ("reference_white", 0), ("unknown_metadata",), ("wrong_array_shape", "XYZ")):
        candidate = json.loads(json.dumps(original))
        parent = candidate
        for key in keys[:-1]:
            parent = parent[key]
        parent[keys[-1]] = math.nextafter(parent[keys[-1]], math.inf)
        invalid.append(candidate)
    for index, value in ((1, -math.ulp(0.0)), (2, math.ulp(0.0)), (2, -0.0)):
        candidate = json.loads(json.dumps(original))
        candidate["backend_reported"]["spectrum"]["values"][index] = value
        invalid.append(candidate)
    for value in (1, True):
        candidate = json.loads(json.dumps(original))
        candidate["native"]["spectrum"]["samples"][2] = value
        invalid.append(candidate)
    for replacement in invalid:
        with pytest.raises(SystemExit, match="Published fixtures differ") as failure:
            generator._check(tmp_path / "unused-build")
        assert "generic_frontend/capture.json" in str(failure.value)
        assert "at $" in str(failure.value) or "invalid JSON" in str(failure.value)
        assert len(str(failure.value)) < 1000
    # The tail's exception is confined to a complete, same-profile simulated
    # emitted vector. It cannot erase type, zero, source-scale or domain changes.
    assert len(generator._synthetic_profiles()) == 12
    tail_upper = generator._synthetic_emitted_interval(
        generator._synthetic_profiles()[7], 779.0)[1]
    for owner, values_key in (("synthetic", "values"), ("synthetic_raw", "samples")):
        before = json.loads(json.dumps(original[owner]))
        after = json.loads(json.dumps(ci_replacement[owner]))
        for invalid_tail in (math.nextafter(tail_upper, math.inf), 0.0, -0.0,
                             -5.865411586436814e-19):
            candidate = json.loads(json.dumps(after))
            candidate["backend_reported"]["spectrum"][values_key][399] = invalid_tail
            assert generator._fixture_difference(wrapped(before), wrapped(candidate)) is not None
        candidate = json.loads(json.dumps(after))
        candidate["backend_reported"]["spectrum"][values_key] = [
            1000 * value for value in before["backend_reported"]["spectrum"][values_key]]
        assert generator._fixture_difference(wrapped(before), wrapped(candidate)) is not None
        for context_key, value in (("provenance", "physical"), ("provenance", None)):
            a, b = json.loads(json.dumps(before)), json.loads(json.dumps(after))
            a[context_key] = b[context_key] = value
            assert generator._fixture_difference(wrapped(a), wrapped(b)) is not None
        a, b = json.loads(json.dumps(before)), json.loads(json.dumps(after))
        if values_key == "samples":
            for side in (a, b):
                side["backend_reported"]["spectrum"]["norm"] = 100
        else:
            for side in (a, b):
                side["backend_reported"]["spectrum"].update(quantity="reflectance", unit="%")
        assert generator._fixture_difference(wrapped(a), wrapped(b)) is not None
    other_profile = json.loads((fixtures / "specbos_2501/emissive_tele.spot.json").read_bytes())[
        "result"]["patches"][0]["observation"]["backend_reported"]["spectrum"]["values"]
    before = json.loads(json.dumps(original["synthetic"]))
    after = json.loads(json.dumps(before))
    after["backend_reported"]["spectrum"]["values"] = other_profile
    assert generator._fixture_difference(wrapped(before), wrapped(after)) is not None
    after["backend_reported"]["spectrum"]["values"] = list(
        before["backend_reported"]["spectrum"]["values"])
    after["backend_reported"]["spectrum"]["values"][100] = other_profile[100]
    assert generator._fixture_difference(wrapped(before), wrapped(after)) is not None
    ambient = json.loads((fixtures / "specbos/ambient_spot.spot.json").read_bytes())[
        "result"]["patches"][0]["observation"]
    for before, values_key in (({"provenance": ambient["provenance"],
                                "backend_reported": ambient["backend_reported"]}, "values"),
                              (next(event for event in json.loads(
                                  ambient["raw_backend_output"])["events"]
                                  if event.get("backend_reported", {}).get("spectrum") is not None),
                               "samples")):
        after = json.loads(json.dumps(before))
        after["backend_reported"]["spectrum"][values_key][399] = 5.865411586436814e-19
        assert generator._fixture_difference(wrapped(before), wrapped(after)) is None
    # An unchanged overall verdict cannot conceal an individual admission
    # boundary crossing. Both per-metric predicates must retain their result.
    for boundary, direction in ((0.9, -math.inf), (1.1, math.inf)):
        before = json.loads(json.dumps(original["pr"]))
        before["unit_check"]["verdict"] = 3
        before["unit_check"]["y_ratio"] = boundary
        after = json.loads(json.dumps(before))
        after["unit_check"]["y_ratio"] = math.nextafter(boundary, direction)
        assert generator._fixture_difference(wrapped(before), wrapped(after)) is not None
    before = json.loads(json.dumps(original["cr"]))
    before["xyz"] = [0.0, 1.0, 0.0]
    before["unit_check"].update(xyz=[0.05, 1.0, 0.0], chroma_error=0.05, verdict="mismatch")
    after = json.loads(json.dumps(before))
    after["unit_check"]["xyz"][0] = math.nextafter(0.05, math.inf)
    after["unit_check"]["chroma_error"] = math.nextafter(0.05, math.inf)
    assert generator._fixture_difference(wrapped(before), wrapped(after)) is not None
    before = json.loads(json.dumps(original["cr"]))
    before["unit_check"]["verdict"] = "mismatch"
    before["unit_check"]["spectrum_mean_nm"] = before["unit_check"]["mean_nm"] + 5.0
    after = json.loads(json.dumps(before))
    after["unit_check"]["spectrum_mean_nm"] = math.nextafter(
        before["unit_check"]["spectrum_mean_nm"], math.inf)
    assert generator._fixture_difference(wrapped(before), wrapped(after)) is not None
    # Reproducing a diagnostic cannot excuse changes beyond the paired input
    # bound. These are the same source calculation after five integrated-Z ULPs.
    before = json.loads(json.dumps(original["cr"]))
    after = json.loads(json.dumps(before))
    after["unit_check"]["xyz"][2] = 6.250000504095142
    after["unit_check"]["chroma_error"] = 1.1522174665807531e-08
    assert generator._fixture_difference(wrapped(before), wrapped(after)) is not None
    # Native luminance below the check's floor cannot provide its context,
    # even when both residuals fit their inputs and stay below the chroma limit.
    before["xyz"] = [0.0, 0.1, 0.0]
    before["unit_check"].update(xyz=[0.001, 0.1, 0.0], chroma_error=0.01)
    after = json.loads(json.dumps(before))
    after["unit_check"]["xyz"][0] = math.nextafter(0.001, math.inf)
    after["unit_check"]["chroma_error"] = math.nextafter(0.01, math.inf)
    assert generator._fixture_difference(wrapped(before), wrapped(after)) is not None
    # Missing or malformed context on both sides earns no scalar tolerance.
    for owner, keys, value in (("cr", ("xyz",), None),
                                ("cr", ("spectrum",), None),
                                ("cr", ("spectrum", "start_nm"), None),
                                ("cr", ("spectrum", "end_nm"), 380),
                                ("cr", ("spectrum", "step_nm"), 0),
                                ("cr", ("spectrum", "count"), True),
                                ("cr", ("unit_check", "spectrum_w"), 0.0),
                                ("cr", ("unit_check", "mean_nm"), None),
                                ("cr", ("unit_check", "discriminating", "axis"), 1),
                                ("cr", ("xyz",), [-0.125, 0.25, -0.125]),
                                ("cr", ("xyz",), [True, 25.0, 6.25]),
                                ("cr", ("xyz",), ["bad", 25.0, 6.25]),
                                ("cr", ("unit_check", "xyz"), [1.0, 2.0]),
                                ("cr", ("unit_check", "verdict"), []),
                                ("cr", ("unit_check", "discriminating", "chromaticity"), False),
                                ("pr", ("unit_check", "computed"), 0),
                                ("pr", ("unit_check", "discriminating"), 0)):
        before = json.loads(json.dumps(original[owner]))
        after = json.loads(json.dumps(ci_replacement[owner]))
        for side in (before, after):
            parent = side
            for key in keys[:-1]:
                parent = parent[key]
            parent[keys[-1]] = value
        assert generator._fixture_difference(wrapped(before), wrapped(after)) is not None
    if nesting == "direct":
        replacement = original
        target.write_text(retained.decode().replace('"count": 3', '"count": 3, "count": 3', 1))
        with pytest.raises(SystemExit, match="capture.json: invalid JSON"):
            generator._check(tmp_path / "unused-build")


def test_the_synthetic_tail_exception_has_its_declared_width_and_scope(monkeypatch):
    """Bound the emitted-spectrum interval independently of the code that computes it.

    The neighbouring case only checks that a value just beyond the interval's own
    upper end fails, which holds for any width. Here the width comes from the
    declared budget: four ULPs of the dominant exp argument, plus at most sixteen
    result ULPs of rounding.
    """
    import importlib
    import math

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))
    generator = importlib.import_module("generate_frontend_fixtures")
    fixtures = Path(__file__).parent / "fixtures/generic_frontend"
    observation = json.loads((fixtures / "specbos/emissive_tele.spot.json").read_bytes())[
        "result"]["patches"][0]["observation"]
    spectrum = observation["backend_reported"]["spectrum"]
    profile = generator._synthetic_profiles()[7]
    nm, value = spectrum["wavelengths_nm"][399], spectrum["values"][399]
    # At 779 nm the 640 nm component dominates; the 445 nm one is below 1e-160.
    z = max(-0.5 * ((nm - centre) / width) ** 2 for centre, width, _ in profile)
    budget = math.expm1(4 * math.ulp(z))
    lower, upper = generator._synthetic_emitted_interval(profile, nm)
    for half_width in (upper / value - 1, 1 - lower / value):
        assert budget <= half_width <= budget + 16 * 2.0**-52

    def synthetic(values, wavelengths=None, parent="backend_reported"):
        copy = dict(spectrum, values=values)
        if wavelengths is not None:
            copy["wavelengths_nm"] = wavelengths
        return {"provenance": "simulated", parent: {"spectrum": copy}}

    def tail_changed(values):
        changed = list(values)
        for _ in range(43):
            changed[399] = math.nextafter(changed[399], 0.0)
        return changed

    # The admitted case: a 43-ULP tail change on the stub's integer-nm grid.
    assert generator._fixture_difference(
        synthetic(spectrum["values"]), synthetic(tail_changed(spectrum["values"]))) is None
    # The same change in a spectrum outside backend_reported earns no exception.
    assert generator._fixture_difference(
        synthetic(spectrum["values"], parent="copy"),
        synthetic(tail_changed(spectrum["values"]), parent="copy")) is not None
    # A grid the stub's integer arithmetic does not reproduce earns none either,
    # even when the vector is computed from the same profile at those wavelengths.
    shifted = [wavelength + 0.5 for wavelength in spectrum["wavelengths_nm"]]
    values = [1000.0 * sum(amplitude * math.exp(-0.5 * math.pow((x - centre) / width, 2))
                           for centre, width, amplitude in profile) for x in shifted]
    assert generator._fixture_difference(
        synthetic(values, shifted), synthetic(tail_changed(values), shifted)) is not None
    # Embedded JSON whose top level is a list is no quantity array: one ULP differs.
    assert generator._fixture_difference(
        {"facts_json": json.dumps([0.5])},
        {"facts_json": json.dumps([math.nextafter(0.5, 1.0)])}) is not None


def test_refreshed_fixture_records_keep_exact_current_request_and_descriptor_context():
    """Read back current captures through real validators without starting a worker."""
    from color_measurement.application import _check_result
    from color_measurement.instrument_frontend import _validate_descriptor_request
    from color_measurement.operation_records import result_from_json

    root = Path(__file__).parent / "fixtures/generic_frontend"
    checked = 0
    for path in sorted(root.rglob("*.json")):
        capture = json.loads(path.read_bytes())
        if not {"request", "result", "events"} <= capture.keys():
            continue
        descriptor = capture["descriptor"]
        request = capture["request"]
        _validate_descriptor_request(request, descriptor)
        result = result_from_json(json.dumps(capture["result"]))
        reserved = dict(request)
        reserved.setdefault("event_contract", descriptor["default_event_contract"])
        _check_result(result, dict(operation_id=result.operation_id, request=reserved), descriptor)
        started = next(event for event in capture["events"] if event["kind"] == "started")
        assert started["payload"]["capability_id"] == request["capability_id"]
        checked += 1
    assert checked > 300


def test_the_published_fixtures_still_describe_these_instruments():
    """A client (the MATLAB UI) pins its tests against tests/fixtures/generic_frontend.

    Regenerating the descriptor here and comparing means a contract change shows
    up as a failure on this side rather than as a silent divergence on theirs.
    Only describe.json is compared: it is the whole contract surface, and the
    recorded results contain timestamps that are expected to differ.
    """
    import json as _json
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1] / "tests/fixtures/generic_frontend"
    index = _json.loads((root / "index.json").read_text())
    worker = supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")
    front = _frontend(worker, COLORIMETER, "CM-COLORIMETER-0001", "emissive_spot",
                      dict(os.environ, CM_COLORIMETER_FIXTURE="success"))
    published = _json.loads((root / "i1_display_3/describe.json").read_text())
    assert _json.loads(front.describe_json()) == published, (
        "The descriptor changed; regenerate with tools/generate_frontend_fixtures.py "
        "and tell the client, rather than editing the fixture by hand"
    )
    assert index["personas"]["i1_display_3"]["capability_id"] == published["capability_id"]


def test_every_constructible_instrument_type_is_published():
    """The demo and a client's picker offer the personas published here, so a type
    without one is an instrument nobody can try. Derived from the build's own
    catalog: a type upstream adds, or a persona dropped, fails here. The two that
    are refused say why, in discovery, rather than being absent in silence."""
    import json as _json
    from pathlib import Path as _Path

    from color_measurement.argyll_capability import CONTRIBUTED_INSTRUMENTS, INSTRUMENTS

    fixtures = _Path(__file__).resolve().parent / "fixtures"
    index = _json.loads((fixtures / "generic_frontend/index.json").read_text())
    catalog = _json.loads((fixtures / "discovery/catalog.json").read_text())
    names = {name: symbol for table in (INSTRUMENTS, CONTRIBUTED_INSTRUMENTS)
             for symbol, spellings in table.items() for name in spellings}
    published = [names[persona["instrument"]] for persona in index["personas"].values()]
    assert len(published) == len(set(published)), "one persona per type"
    constructible = {row["enum"] for row in catalog["instrument_types"] if row["constructor"]}
    refused = {row["enum"] for row in catalog["instrument_types"]
               if row["initialization_refusal"] == "initialization_downloads_pld"}
    assert refused == {"instSpyder1", "instSpyder2"}
    assert set(published) == constructible - refused
    shown = _json.loads((fixtures / "discovery/refuses_firmware_download.json").read_text())
    assert {unit["initialization_refusal"] for unit in shown["units"]} == {
        "initialization_downloads_pld"}
    assert {entry["requires"] for entry in shown["opening"]} == {"not_permitted"}


def test_a_discovery_capture_records_every_value_a_picker_reads():
    """A client's double replays these units in place of DiscoveredInstrument. A
    value the live object gives and the capture leaves out -- opening_requires
    was one -- has to be invented by that double, shaped by its assumption."""
    import dataclasses
    from pathlib import Path as _Path

    from color_measurement.discovery import DiscoveredInstrument

    public = {field.name for field in dataclasses.fields(DiscoveredInstrument)} | {
        name for name, value in vars(DiscoveredInstrument).items()
        if isinstance(value, property)}
    captures = sorted((_Path(__file__).resolve().parent / "fixtures/discovery").glob("*.json"))
    units = [(path.name, unit) for path in captures
             for unit in json.loads(path.read_text()).get("units") or []]
    assert units, "no discovery capture lists a unit"
    for name, unit in units:
        assert set(unit) == public, name


def test_a_cancel_answered_to_a_prompt_is_honoured_and_not_coerced():
    """Cancel was accepted by the client and then ignored by the instrument.

    Renamed from `test_a_cancelled_operation_does_not_report_a_measurement`,
    which claimed more than it drove: it exercises the PROMPT path only and says
    nothing about a cancel arriving after the instrument has returned. That case
    is covered separately, and its answer is the opposite -- real readings are
    kept.

    The ladder's reply vocabulary is only "acknowledge" and "skip", so a cancel
    cannot be expressed to it and has to end the session. Mapping it to
    "acknowledge" -- which is what happened -- meant an operator pressed Cancel
    and the measurement ran to completion and was published as one.
    """
    import color_measurement.instrument_frontend as module

    meter = Instrument(helper="/x", instrument=COLORIMETER, expected_serial="SN-1")
    front = InstrumentFrontend(meter, mode_capabilities=93464,
                               trigger_capabilities=234881792, filter_capabilities=0,
                               provenance="simulated")
    operation = object.__new__(module.InstrumentOperation)
    operation._cancel = __import__("threading").Event()

    # A cancel already requested is honoured without asking again.
    operation._cancel.set()
    assert operation._acknowledge({"instruction": "place_on_white", "request_id": "r1"}) == "cancel"

    # And a cancel answered to the prompt is honoured too, rather than coerced.
    operation._cancel.clear()
    asked = {}

    def _ask(kind, message, responses, **kw):
        asked["responses"] = responses
        return "cancel"

    operation._ask = _ask
    assert operation._acknowledge({"instruction": "place_on_white", "request_id": "r1"}) == "cancel"
    assert "cancel" in asked["responses"], "a client must be offered cancel at a prompt"
    assert front is not None


def _workflows(entry):
    """Just the workflow names; each entry also carries bounds and layout keys."""
    return [item["workflow"] for item in entry["workflows"]]


def test_only_workflows_that_can_run_are_offered():
    """calibrate goes through the spot path, which refuses a non-spot mode, and
    xy is refused at run until the fiducial exchange exists.

    `calibrate` used to ALSO require inst_mode_calibration, and that was wrong
    twice over. Only ``munki.c`` sets that bit in the whole pinned tree, so the
    workflow reached 1 instrument of 38 -- and the bit does not mean "can
    calibrate": inst.h:251-252 defines it as "Configured for calibration", a
    SENSOR POSITION reported through ``meas_config()`` (inst.h:812,
    munki.c:213-217), because the ColorMunki's dial has a calibration detent.

    The ladder never believed it. `calibrate` is a command-line flag that never
    enters the mode word, and ``get_n_a_cals()`` -- which every driver implements
    -- is the authority. The stub test driving a filter-change calibration on a
    SpectroScan, which advertises no such bit, is the proof.
    """
    meter = Instrument(helper="/x", instrument=COLORIMETER, expected_serial="SN-1")
    from color_measurement.argyll_capability import MODE_FLAGS, MODE_ROUTES

    # Every spot mode offers it, whatever the instrument advertises.
    plain = json.loads(InstrumentFrontend(
        meter, mode_capabilities=MODE_ROUTES["ref_spot"], trigger_capabilities=0,
        filter_capabilities=0, provenance="simulated").describe_json())
    assert _workflows(plain["modes"][0]) == ["spot", "mode_snapshot", "calibrate"]

    declaring = json.loads(InstrumentFrontend(
        meter, mode_capabilities=MODE_ROUTES["ref_spot"] | MODE_FLAGS["calibration"],
        trigger_capabilities=0, filter_capabilities=0,
        provenance="simulated").describe_json())
    assert _workflows(declaring["modes"][0]) == ["spot", "mode_snapshot", "calibrate"]

    # A strip mode never offers calibrate, because measure() refuses it there.
    strip = json.loads(InstrumentFrontend(
        meter, mode_capabilities=MODE_ROUTES["ref_strip"] | MODE_FLAGS["calibration"],
        trigger_capabilities=0, filter_capabilities=0,
        provenance="simulated").describe_json())
    assert _workflows(strip["modes"][0]) == ["strip", "mode_snapshot"]
    # Capability bits alone do not tell us which strip operands the driver consumes.
    assert not strip["modes"][0]["available"]
    assert strip["modes"][0]["workflows"][0]["layout_keys"] == []

    # X-Y is a CAPABILITY question, not a frontend one. A sheet cannot be located
    # without inst2_xy_locate and inst2_xy_position, so an instrument advertising
    # neither is reported unavailable WITH the reason rather than hidden.
    from color_measurement.argyll_capability import CAPABILITY2

    sheet = json.loads(InstrumentFrontend(
        meter, mode_capabilities=MODE_ROUTES["ref_xy"], trigger_capabilities=0,
        filter_capabilities=0, provenance="simulated").describe_json())
    entry = sheet["modes"][0]
    assert entry["available"] is False
    assert "positioning and location" in entry["unavailable_reason"]

    positioning = (CAPABILITY2["xy_locate"] | CAPABILITY2["xy_position"]
                   | CAPABILITY2["xy_holdrel"])
    located = json.loads(InstrumentFrontend(
        meter, mode_capabilities=MODE_ROUTES["ref_xy"],
        trigger_capabilities=positioning, filter_capabilities=0,
        provenance="simulated").describe_json())
    entry = located["modes"][0]
    assert entry["available"] is True and entry["unavailable_reason"] is None
    assert entry["requires_positioning"] is True
    # `hexagonal` changes the navigation vectors, so a client reading a hexagonal
    # chart must be able to say so.
    assert "hexagonal" in entry["workflows"][0]["layout_keys"]
    assert entry["workflows"][0]["required_layout_keys"] == ["passes", "steps"]


# -- the descriptor is a COMPLETE contract ---------------------------------------

def _descriptor(**caps):
    meter = Instrument(helper="/x", instrument=COLORIMETER, expected_serial="SN-1")
    words = {"mode_capabilities": 0, "trigger_capabilities": 0, "filter_capabilities": 0}
    return json.loads(InstrumentFrontend(
        meter, provenance="simulated", **{**words, **caps}).describe_json())


def test_near_duplicate_modes_point_at_the_one_they_duplicate():
    """`emissive_spot` and `emissive_spot_adaptive` compose the SAME instrument
    word: adaptive acquisition IS the default and the suffix only says so out
    loud. A client offering both as separate choices offers one measurement
    twice, and nothing in the descriptor said they were the same."""
    from color_measurement.argyll_capability import MODE_FLAGS, MODE_ROUTES

    # `nonadaptive` is advertised separately from the route, so the word needs it
    # for that name to be offered at all (supported_modes gates on exactly this).
    snapshot = _descriptor(
        mode_capabilities=MODE_ROUTES["emis_spot"] | MODE_FLAGS["emis_nonadaptive"])
    entries = {entry["mode"]: entry for entry in snapshot["modes"]}
    assert entries["emissive_spot"]["canonical"] is True
    assert entries["emissive_spot_adaptive"]["canonical"] is False
    assert entries["emissive_spot_adaptive"]["equivalent_to"] == "emissive_spot"
    # A genuinely different composition is NOT folded in: nonadaptive sets
    # inst_mode_emis_nonadaptive, so it is its own measurement.
    assert entries["emissive_spot_nonadaptive"]["canonical"] is True


def test_a_mode_says_what_it_will_return_before_anything_is_measured():
    """A UI has to label an axis and pick a scale before the first reading."""
    from color_measurement.argyll_capability import MODE_ROUTES

    emissive = _descriptor(mode_capabilities=MODE_ROUTES["emis_spot"])["modes"][0]
    assert emissive["expected_measurement_type_name"] == "emission"
    assert emissive["expected_quantities"]["xyz_unit"] == "cd/m^2"
    reflective = _descriptor(mode_capabilities=MODE_ROUTES["ref_spot"])["modes"][0]
    assert reflective["expected_measurement_type_name"] == "reflective"
    assert reflective["expected_quantities"]["illuminant"] == "D50"
    # Label and axes come from one vocabulary rather than each client's own split.
    assert reflective["label"] == "Reflective spot"
    assert reflective["axes"]["illumination"] == "reflective"


def test_every_option_is_gated_on_the_capability_the_helper_checks():
    """An option offered without its capability collects a failure instead of a
    reading. Each gate here is the one session.c tests before calling the driver."""
    from color_measurement.argyll_capability import (
        CAPABILITY2,
        CAPABILITY3,
        MODE_FLAGS,
        MODE_ROUTES,
    )

    bare = {option["id"]: option for option in
            _descriptor(mode_capabilities=MODE_ROUTES["ref_spot"])["options"]}
    assert bare["highres"]["available"] is False
    assert bare["averages"]["available"] is False
    assert bare["display_type"]["available"] is False
    assert bare["correction"]["available"] is False
    # xcalstd has no capability word; the driver refuses it at set_xcalstd.
    assert bare["xcalstd"]["available"] is True
    assert bare["xcalstd"]["applies_to"] == ["reflective_spot"]
    for route, expected in (("s_ref_spot", ["saved_spot"]),
                            ("emis_spot", None), ("trans_spot", None)):
        option = next(option for option in _descriptor(
            mode_capabilities=MODE_ROUTES[route])["options"] if option["id"] == "xcalstd")
        assert option["available"] is (expected is not None)
        assert option["applies_to"] == expected

    rich = {option["id"]: option for option in _descriptor(
        mode_capabilities=MODE_ROUTES["ref_spot"] | MODE_FLAGS["highres"],
        trigger_capabilities=CAPABILITY2["disptype"] | CAPABILITY2["ccmx"],
        filter_capabilities=CAPABILITY3["average"] | CAPABILITY3["filter_D50"],
    )["options"]}
    assert rich["highres"]["available"] and rich["averages"]["available"]
    assert not rich["display_type"]["available"]  # Capability alone cannot name choices.
    assert rich["correction"]["available"]
    assert rich["correction"]["kinds"] == ["ccmx"]
    # M1 became selectable because the tertiary word carries its filter; M3 did not.
    assert "m1" in rich["filter"]["choices"] and "m3" not in rich["filter"]["choices"]


def test_an_option_applies_to_every_mode_or_to_named_ones_never_to_none():
    """applies_to is null or a non-empty list. An empty list and null decode alike
    in a client's JSON reader -- MATLAB's jsondecode gives both as empty -- so a
    filter published with [] on a colorimeter read as a filter for every mode. An
    option no mode can take is unavailable everywhere instead, with its reason."""
    from pathlib import Path

    from color_measurement.argyll_capability import MODE_ROUTES

    fixtures = Path(__file__).resolve().parent / "fixtures/generic_frontend"
    emissive = {option["id"]: option for option in
                _descriptor(mode_capabilities=MODE_ROUTES["emis_spot"])["options"]}
    assert emissive["filter"]["applies_to"] is None
    assert emissive["filter"]["available"] is False
    assert (emissive["filter"]["unavailable_reason"]
            == "This instrument advertises no reflective mode")
    reflective = {option["id"]: option for option in
                  _descriptor(mode_capabilities=MODE_ROUTES["ref_spot"])["options"]}
    assert "reflective_spot" in reflective["filter"]["applies_to"]
    personas = sorted(p.name for p in fixtures.iterdir() if p.is_dir())
    assert personas
    for persona in personas:
        for option in json.loads((fixtures / persona / "describe.json").read_text())["options"]:
            scope = option["applies_to"]
            assert scope is None or (isinstance(scope, list) and scope), (persona, option["id"])


#: A VALID correction, so the refusal under test is the capability one and not a
#: parse failure that would pass for the same reason by accident.
_CCMX = (
    'CCMX\n\nDESCRIPTOR "t"\nORIGINATOR "t"\nINSTRUMENT "X-Rite i1 DisplayPro"\n'
    'DISPLAY "LCD"\nKEYWORD "COLOR_REP"\nCOLOR_REP "XYZ"\n'
    "NUMBER_OF_FIELDS 3\nBEGIN_DATA_FORMAT\nXYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\n"
    "NUMBER_OF_SETS 3\nBEGIN_DATA\n1.0 0.0 0.0\n0.0 1.0 0.0\n0.0 0.0 1.0\nEND_DATA\n"
)


def test_a_correction_is_refused_for_an_instrument_that_cannot_apply_one():
    """Accepting it would bind, return inst_ok from every call, and publish an
    uncorrected reading the caller believes was corrected."""
    from color_measurement.argyll_capability import MODE_ROUTES

    meter = Instrument(helper="/x", instrument=COLORIMETER, expected_serial="SN-1")
    front = InstrumentFrontend(meter, mode_capabilities=MODE_ROUTES["emis_spot"],
                               trigger_capabilities=0, filter_capabilities=0,
                               provenance="simulated")
    snapshot = json.loads(front.describe_json())
    assert snapshot["accepts_correction"] is False
    request = {"schema_version": 1, "device_id": snapshot["device_id"],
               "capability_id": snapshot["capability_id"], "mode": "emissive_spot",
               "workflow": "spot", "patch_ids": ["A1"],
               "options": {"correction": {"text": _CCMX}}, "layout": {}}
    with pytest.raises(ValueError, match="colorimeter correction"):
        front.prepare(json.dumps(request))


def _scaled_ccmx(scale):
    return _CCMX.replace("1.0 0.0 0.0\n0.0 1.0 0.0\n0.0 0.0 1.0\n",
                         f"{scale!r} 0.0 0.0\n0.0 {scale!r} 0.0\n0.0 0.0 {scale!r}\n")


def test_a_matrix_typed_reading_states_its_matrix_and_is_refused_before_opening():
    """kleink10.c:1422-1425 types each reading by the matrix it applied, so an
    ambient_spot without an illuminance-scaled one was published available and
    failed at ambient_matrix_required only after Measure opened the instrument."""
    from color_measurement.argyll_capability import CAPABILITY2, MODE_ROUTES

    words = dict(mode_capabilities=MODE_ROUTES["emis_spot"] | MODE_ROUTES["emis_ambient"],
                 trigger_capabilities=CAPABILITY2["ccmx"], filter_capabilities=0,
                 ambient_ccmx=True, provenance="simulated")
    meter = Instrument(helper="/x", instrument="Klein K-10", expected_serial="K1")
    front = InstrumentFrontend(meter, ambient_matrix_threshold=5, **words)
    snapshot = json.loads(front.describe_json())
    modes = {entry["mode"]: entry for entry in snapshot["modes"]}
    assert (modes["ambient_spot"]["correction_required"],
            modes["ambient_spot"]["correction_matrix_diagonal_mean"]) == (True, {"above": 5.0})
    assert (modes["emissive_spot"]["correction_required"],
            modes["emissive_spot"]["correction_matrix_diagonal_mean"]) == (False, {"at_most": 5.0})

    def request(mode, scale=None):
        return json.dumps({
            "schema_version": 1, "device_id": snapshot["device_id"],
            "capability_id": snapshot["capability_id"], "mode": mode, "workflow": "spot",
            "patch_ids": ["A1"], "layout": {},
            "options": {} if scale is None else {"correction": {"text": _scaled_ccmx(scale)}}})

    # Each refused at prepare(), so no worker exists to open anything.
    for mode, scale, refusal in (("ambient_spot", None, "needs a .ccmx scaled for illuminance"),
                                 ("ambient_spot", 5.0, "label the reading emission"),
                                 ("emissive_spot", 10.0, "label the reading ambient")):
        with pytest.raises(ValueError, match=refusal):
            front.prepare(request(mode, scale))
    # The boundary is the driver's strict ">": 5.0 is emission, just above it ambient.
    front._validate_request(request("ambient_spot", 5.000001))
    front._validate_request(request("emissive_spot", 5.0))
    front._validate_request(request("emissive_spot"))
    # A driver typing by route states nothing and requires nothing.
    plain = json.loads(InstrumentFrontend(meter, **words).describe_json())
    assert {(entry["correction_required"], entry["correction_matrix_diagonal_mean"])
            for entry in plain["modes"]} == {(False, None)}


def test_the_descriptor_names_its_own_contract_and_what_provenance_means():
    """A client holding an offline fixture descriptor and this one had no field
    that told them apart, and no answer for what `replay` means."""
    from color_measurement.argyll_capability import MODE_ROUTES

    snapshot = _descriptor(mode_capabilities=MODE_ROUTES["ref_spot"])
    assert snapshot["contract"] == "argyll_generic_instrument_v1"
    # `simulated` values are invented; `replay` values were MEASURED, earlier.
    assert (snapshot["synthetic"], snapshot["live"]) == (True, False)
    meter = Instrument(helper="/x", instrument=COLORIMETER, expected_serial="SN-1")
    for provenance, synthetic, live in (("physical", False, True), ("replay", False, False)):
        other = json.loads(InstrumentFrontend(
            meter, mode_capabilities=MODE_ROUTES["ref_spot"], trigger_capabilities=0,
            filter_capabilities=0, provenance=provenance).describe_json())
        assert (other["synthetic"], other["live"]) == (synthetic, live)
    # Limits a client must respect, and the operator instructions it must render.
    assert snapshot["limits"]["max_request_bytes"] == 16384
    assert {entry["kind"] for entry in snapshot["instructions"]} == {
        "present_calibration_reference", "notify_calibration_condition",
        "place_sheet", "sight_fiducial", "present_sample", "present_strip",
        "reposition_sensor", "present_refresh_stimulus"}
    # Exactly one instruction takes no answer: a condition the INSTRUMENT
    # triggers. A client that offered it a button would be waiting on a press
    # that means nothing, and the ladder does not read a reply for it.
    assert [entry["instruction"] for entry in snapshot["instructions"]
            if not entry["answered"]] == ["calibration_condition"]


def test_a_client_may_take_patch_references_instead_of_whole_patches():
    """Both notification contracts retain a completed spectral reading.

    References identify the full patch in the retained result; inline events
    carry that same patch. This tests notification shape and retention, not the
    maximum spectral frame size.
    """
    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    environment = dict(os.environ, CM_CHART_PERSONA="dtp41", CM_CHART_FIXTURE="success")
    front = _frontend(worker, "X-Rite DTP41", "CM-CHART-0001",
                      "reflective_spot", environment, spectral=True)
    descriptor = json.loads(front.describe_json())
    assert descriptor["event_contracts"] == ["patch_v1", "retained_patch_v1"]
    readings = []
    for contract in (None, "patch_v1"):
        result, events = _drive_answering(
            front, "reflective_spot", "spot", ["A1"], event_contract=contract)
        assert result["status"] == "completed", result.get("error")
        assert result["expected_patch_ids"] == ["A1"]
        assert len(result["patches"]) == 1
        patch = result["patches"][0]
        assert (patch["index"], patch["patch_id"]) == (0, "A1")
        observation = patch["observation"]
        assert observation["sample_id"] == "A1"
        assert observation["provenance"] == "simulated"
        reported = observation["backend_reported"]
        assert reported["xyz"] is not None
        assert reported["xyz"]["unit"] == "1"
        assert len(reported["xyz"]["values"]) == 3
        assert any(value != 0 for value in reported["xyz"]["values"])
        assert reported["spectrum"] is not None
        assert reported["spectrum"]["quantity"] == "reflectance"
        readings.append(reported)
        started, = [event for event in events if event["kind"] == "started"]
        confirmed = "retained_patch_v1" if contract is None else contract
        assert started["payload"]["event_contract"] == confirmed
        assert dict(result["configuration"])["event_contract"] == confirmed
        notified = [event for event in events
                    if event["kind"] in {"patch", "patch_reference"}]
        notification, = notified
        if contract is None:
            assert notification["kind"] == "patch_reference"
            assert notification["payload"] == {
                "index": patch["index"], "patch_id": patch["patch_id"],
                "observation_id": observation["observation_id"],
            }
        else:
            assert notification["kind"] == "patch"
            assert notification["payload"] == patch
        terminal, = [event for event in events if event["kind"] == "terminal"]
        assert terminal["payload"]["status"] == "completed"
    assert readings[0] == readings[1]
    request = {
        "schema_version": 1, "device_id": descriptor["device_id"],
        "capability_id": descriptor["capability_id"], "mode": "reflective_spot",
        "workflow": "spot", "patch_ids": ["A1"], "options": {}, "layout": {},
        "event_contract": "whatever",
    }
    with pytest.raises(ValueError, match="Unknown event contract"):
        front.prepare(json.dumps(request))



def _drive_answering(front, mode, workflow, patch_ids, seconds=15.0, layout=None,
                     event_contract=None):
    """Drive an operation answering every instruction, and keep the events.

    Unlike ``_drive`` this exercises the REAL ``_ask``: the answered-calibration
    path had no such test, so a metadata field the payload builder refused
    reached clients as a failed operation instead of a prompt. The client found
    it; nothing here could have.
    """
    snapshot = json.loads(front.describe_json())
    request = {
        "schema_version": 1, "device_id": snapshot["device_id"],
        "capability_id": snapshot["capability_id"], "mode": mode,
        "workflow": workflow, "patch_ids": list(patch_ids), "options": {},
        "layout": layout or {},
    }
    if event_contract is not None:
        request["event_contract"] = event_contract
    operation = front.prepare(json.dumps(request))
    events = []

    def drain():
        while True:
            raw = operation.poll_event_json()
            if not raw:
                return
            event = json.loads(raw)
            events.append(event)
            if event["kind"] == "instruction_required":
                operation.respond(operation.operation_id,
                                  event["payload"]["request_id"], "accept")
    try:
        operation.dispatch()
        deadline = time.monotonic() + seconds
        while True:
            drain()
            if operation.done:
                drain()
                break
            assert time.monotonic() < deadline, "operation did not finish"
            time.sleep(0.01)
        return json.loads(operation.result_json()), events
    finally:
        operation.close()


def test_an_answered_calibration_reaches_the_client_as_a_named_prompt():
    """The path the client actually walks, end to end and through the real `_ask`.

    A SpectroScan raises ``inst_calc_change_filter`` and names the filter in
    ``id[]`` (ss.c). It advertises no ``inst_mode_calibration`` -- only munki.c
    does, anywhere -- which is why this also proves the workflow is not gated on
    that bit.
    """
    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    front = _frontend(worker, "GretagMacbeth SpectroScan", "CM-CHART-0001",
                      "reflective_spot",
                      dict(os.environ, CM_CHART_PERSONA="spectroscan",
                           CM_CHART_FIXTURE="filter_after_calibration"), spectral=True)
    result, events = _drive_answering(front, "reflective_spot_m2", "calibrate", [])
    assert result["status"] == "completed", result.get("error")
    prompts = [e["payload"] for e in events if e["kind"] == "instruction_required"]
    assert len(prompts) == 1
    block = prompts[0]["calibration"]
    assert block["condition"] == "change_filter"
    assert block["action"] == "change_filter"
    # The string is a FILTER, and the block says so -- a client rendering it as a
    # reference serial would tell the operator to fit a tile called UV cut Filter.
    assert (block["reference_id"], block["reference_id_holds"]) == (
        "UV cut Filter", "The filter to fit")
    assert block["reference"] == "filt_UV"
    assert block["acknowledged"] is True and block["display_level"] is None


def test_a_notified_calibration_reaches_the_client_without_a_question():
    """The DTP22 click: shown, never asked. It arrives as `instruction_notified`,
    so a client cannot offer a button for a press that means nothing."""
    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    front = _frontend(worker, "X-Rite DTP22", None, "reflective_spot",
                      dict(os.environ, CM_CHART_PERSONA="dtp22",
                           CM_CHART_FIXTURE="success"), spectral=True)
    result, events = _drive_answering(front, "reflective_spot", "calibrate", [])
    assert result["status"] == "completed", result.get("error")
    assert not [e for e in events if e["kind"] == "instruction_required"]
    notified = [e["payload"] for e in events if e["kind"] == "instruction_notified"]
    assert len(notified) == 1
    block = notified[0]["calibration"]
    assert (block["condition"], block["action"]) == ("man_ref_whitek", "click_reference")
    assert block["acknowledged"] is False
    assert (block["reference_id"], block["reference"]) == ("22", "ref_sn")


def test_a_display_patch_condition_carries_the_level_to_display():
    """`display_level` is the application's to compute -- no driver reads it back
    -- so a client that is not told it has to invent a grey."""
    worker = supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")
    front = _frontend(worker, COLORIMETER, "CM-COLORIMETER-0001", "emissive_spot",
                      dict(os.environ,
                           CM_COLORIMETER_FIXTURE="grey_ladder_calibration"))
    result, events = _drive_answering(front, "emissive_spot", "calibrate", [])
    assert result["status"] == "completed", result.get("error")
    blocks = [e["payload"]["calibration"] for e in events
              if e["kind"] == "instruction_required"]
    assert [b["action"] for b in blocks] == ["display_patch"] * 3
    assert [b["condition"] for b in blocks] == [
        "emis_grey", "emis_grey_darker", "emis_grey_ligher"]
    assert [round(b["display_level"], 6) for b in blocks] == [0.6, 0.42, 0.588]


def test_a_sheet_is_driven_through_this_frontend():
    """X-Y, end to end, which this frontend refused outright until now.

    Nothing was missing but the call. The "operator-sighted fiducial exchange" it
    was reported unavailable for is the `xy_sheet` / `xy_locate` instruction pair
    -- the same acknowledgement contract every other operator condition uses. The
    client sights and accepts; the INSTRUMENT reports where its sight ended up, so
    no coordinate is ever sent by the client, and `navigation_vectors` derives the
    sheet from the points the instrument published.
    """
    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    front = _frontend(worker, "GretagMacbeth SpectroScan", "CM-CHART-0001",
                      "reflective_spot",
                      dict(os.environ, CM_CHART_PERSONA="spectroscan",
                           CM_CHART_FIXTURE="success",
                           CM_CHART_XY_VECTORS="10,20,2,4,0,0,0.5,1"), spectral=True)
    snapshot = json.loads(front.describe_json())
    entry = next(e for e in snapshot["modes"] if e["workflow"] == "xy")
    assert entry["available"] is True, entry["unavailable_reason"]
    assert entry["requires_positioning"] is True

    result, events = _drive_answering(
        front, entry["mode"], "xy", [f"P{i + 1}" for i in range(6)],
        layout={"passes": 2, "steps": 3})
    assert result["status"] == "completed", result.get("error")
    assert len(result["patches"]) == 6
    # One placement, then one sighting per fiducial `sheet_layout` calls for.
    assert [e["payload"]["kind"] for e in events
            if e["kind"] == "instruction_required"] == [
        "xy_sheet", "xy_locate", "xy_locate", "xy_locate"]


def test_a_dtp41_descriptor_advertises_no_xy_route():
    """An instrument without the positioning pair publishes no `xy` workflow."""
    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    front = _frontend(worker, "X-Rite DTP41", "CM-CHART-0001", "reflective_spot",
                      dict(os.environ, CM_CHART_PERSONA="dtp41",
                           CM_CHART_FIXTURE="success"), spectral=True)
    snapshot = json.loads(front.describe_json())
    assert not any(e["workflow"] == "xy" for e in snapshot["modes"]), (
        "a DTP41 advertises no ref_xy route at all")


# --- a stored route's runtime status --------------------------------------------
#
# These drive the real frontend with a real Instrument whose one helper call is
# replaced. That is a different thing from a wire double: `Measurement` is this
# package's own type, so substituting one cannot encode a guess about a format
# somebody else owns. It exists because every other test here needs a substituted
# worker and skips without one, which left this path unexercised by default.


def _chart_frontend(monkeypatch, measurement):
    from color_measurement import instrument_frontend as module

    meter = Instrument(helper="/nonexistent/worker", instrument="X-Rite DTP20",
                       expected_serial="CM-CHART-0001")
    monkeypatch.setattr(type(meter), "measure_chart",
                        lambda self, mode, **kw: measurement, raising=True)
    monkeypatch.setattr(module, "observation_from_patch",
                        lambda *a, **k: pytest.fail("no patch was supplied"))
    # The word that advertises `saved_chart` and nothing else, from the same
    # composer the ladder uses -- not a constant copied into the test.
    return InstrumentFrontend(
        meter, mode_capabilities=mode_word("saved_chart", spectral=False),
        trigger_capabilities=0x30001, filter_capabilities=0,
        provenance="simulated", serial="CM-CHART-0001")


def _stored_measurement(stored):
    from color_measurement.instrument import Measurement

    return Measurement(
        instrument="X-Rite DTP20", serial="CM-CHART-0001", serial_encoding="utf8",
        mode="saved_chart",
        workflow="chart", provenance="simulated", spectral=False,
        confirmed_routes=("saved_chart",), stored=stored,
    )


def _run_chart(front, patch_ids=("P1",)):
    snapshot = json.loads(front.describe_json())
    request = {"schema_version": 1, "device_id": snapshot["device_id"],
               "capability_id": snapshot["capability_id"], "mode": "saved_chart",
               "workflow": "chart", "patch_ids": list(patch_ids), "options": {},
               "layout": {"passes": 1, "steps": 1, "sheets": [1], "chart_id": 0}}
    operation = front.prepare(json.dumps(request))
    operation.dispatch()
    deadline = time.monotonic() + 10
    events = []
    while time.monotonic() < deadline:
        text = operation.poll_event_json()
        if text:
            events.append(json.loads(text))
            continue
        if operation.done:
            while (text := operation.poll_event_json()):
                events.append(json.loads(text))
            break
        time.sleep(0.001)
    return json.loads(operation.result_json()), events


def test_a_stored_routes_runtime_status_reaches_the_client_on_the_result(monkeypatch):
    """`inst_stat_saved_readings` is a RUNTIME status, not a capability
    (inst.h:467-468), so no descriptor field can carry it and a client cannot ask
    without opening the instrument. The documentation used to say "ask
    `Instrument.stored_readings()`", which a frontend client cannot do: it holds
    no `Instrument`, and that call is a whole helper session of its own.
    """
    front = _chart_frontend(monkeypatch, _stored_measurement(3))
    result, _ = _run_chart(front)
    configuration = dict(tuple(pair) for pair in result["configuration"])
    assert configuration["stored_readings"] == "3"
    # The status here is `failed` -- the substituted measurement returns no
    # patches for the one requested -- and the point is that the runtime status
    # travels anyway. A client learns what the instrument holds even when the
    # operation it asked for could not be completed.
    assert result["status"] == "failed"
    assert len({key for key, _ in (tuple(p) for p in result["configuration"])}) == len(
        result["configuration"]), "configuration must stay unique key/value PAIRS"


def test_a_route_that_reports_no_stored_status_adds_no_key(monkeypatch):
    front = _chart_frontend(monkeypatch, _stored_measurement(None))
    result, _ = _run_chart(front)
    assert "stored_readings" not in dict(tuple(pair) for pair in result["configuration"]), (
        "absent is not zero: an instrument that was never asked has no status")


def test_the_started_event_confirms_the_event_contract(monkeypatch):
    """Confirmed at the FIRST event, not only on the result of the operation it
    governs -- a client that opted in should not have to finish to find out."""
    front = _chart_frontend(monkeypatch, _stored_measurement(1))
    _, events = _run_chart(front)
    started = next(event for event in events if event["kind"] == "started")
    assert started["payload"]["event_contract"] == "retained_patch_v1"


def test_a_cancel_after_the_instrument_returned_keeps_what_was_measured(monkeypatch):
    """The case the prompt-path test never reached.

    A cancel cannot be expressed to the ladder, so one raised while the
    instrument is working lands AFTER it has returned. Those patches are real
    readings of the operator's real sample. Discarding them on the way out would
    throw away data the instrument actually produced -- the same rule that keeps
    a complete batch after a cleanup failure.

    `status` answers "did this run to completion?"; `patches` answers "what was
    measured?". They are separate questions, and this pins them apart.
    """
    from color_measurement.instrument import Measurement, Patch

    measured = Measurement(
        instrument="X-Rite DTP20", serial="CM-CHART-0001", serial_encoding="utf8",
        mode="saved_chart",
        workflow="chart", provenance="simulated", spectral=False,
        confirmed_routes=("saved_chart",), stored=1,
        patches=(Patch(mtype=5, mcond=0, xyz=(20.0, 21.0, 22.0), spectrum=None,
                       duration_seconds=0.0, index=0),),
    )
    meter = Instrument(helper="/nonexistent/worker", instrument="X-Rite DTP20",
                       expected_serial="CM-CHART-0001")
    front = InstrumentFrontend(
        meter, mode_capabilities=mode_word("saved_chart", spectral=False),
        trigger_capabilities=0x30001, filter_capabilities=0,
        provenance="simulated", serial="CM-CHART-0001")

    running = []

    def cancel_then_return(self, mode, **kw):
        # Exactly the race: the client cancels while the instrument is working,
        # and the instrument returns anyway because nothing could tell it. The
        # worker thread is already running when this is called, so it waits for
        # `start` to hand back the handle rather than racing it.
        deadline = time.monotonic() + 5
        while not running and time.monotonic() < deadline:
            time.sleep(0.001)
        assert running, "the operation handle never arrived"
        running[0].cancel(running[0].operation_id)
        return measured

    monkeypatch.setattr(type(meter), "measure_chart", cancel_then_return, raising=True)
    snapshot = json.loads(front.describe_json())
    operation = front.prepare(json.dumps({
        "schema_version": 1, "device_id": snapshot["device_id"],
        "capability_id": snapshot["capability_id"], "mode": "saved_chart",
        "workflow": "chart", "patch_ids": ["P1"], "options": {},
        "layout": {"passes": 1, "steps": 1, "sheets": [1], "chart_id": 0}}))
    operation.dispatch()
    running.append(operation)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not operation.done:
        operation.poll_event_json() or time.sleep(0.001)
    result = json.loads(operation.result_json())

    assert result["status"] == "cancelled"
    assert len(result["patches"]) == 1, "a real reading is not discarded on the way out"
    assert result["patches"][0]["observation"]["backend_reported"]["xyz"]["values"] == [
        20.0, 21.0, 22.0]
    assert result["software_stopped"] is True


def test_the_published_retained_captures_pin_the_opt_in_shape():
    """One recording per persona that reads, so a client pins the contract from
    what the ladder actually emitted rather than from its own double.

    `retained_patch_v1` replaces each patch event's whole observation with a
    reference, which is what keeps a 601-band reading inside the frame cap. The
    shape that matters to a client is: the contract is confirmed on `started`,
    and the patch events are `patch_reference` rather than `patch`.
    """
    from pathlib import Path

    index = json.loads((Path(__file__).resolve().parents[1]
                        / "tests/fixtures/generic_frontend/index.json")
                       .read_text(encoding="utf-8"))
    captures = {name: persona["retained_patch_v1"]
                for name, persona in index["personas"].items()
                if persona.get("retained_patch_v1")}
    assert captures, "no retained captures were published"
    for name, capture in captures.items():
        assert capture["status"] == "completed", name
        assert capture["started_event_contract"] == "retained_patch_v1", name
        assert "patch_reference" in capture["event_kinds"], name
        assert "patch" not in capture["event_kinds"], (
            f"{name}: retained_patch_v1 must replace the patch event, not add to it")


def test_the_published_spectrum_mismatch_carries_the_instruments_own_values():
    """A refused publication retains native originals as an unverified observation.

    The failure remains failed; the spectrum is evidence, not qualified output.
    """
    from pathlib import Path

    published = Path(__file__).resolve().parent / "fixtures/generic_frontend/cr250"
    result = json.loads((published / "failure.spectrum_mismatch.json").read_text())["result"]
    assert result["status"] == "failed" and len(result["patches"]) == 1
    capture = json.loads(dict(result["configuration"])["native_facts_json"])["capture"]
    assert capture["unit_check"]["verdict"] == "mismatch"
    assert len(capture["spectrum"]["values"]) == capture["spectrum"]["count"] == 81
    assert capture["spectrum"]["unit"] is None
    assert capture["spectrum"]["published_unit"] is None
    observation = result["patches"][0]["observation"]
    spectrum = observation["backend_reported"]["spectrum"]
    assert "unverified" in spectrum["quantity"] and "unverified" in spectrum["unit"]
    assert spectrum["values"] == capture["spectrum"]["values"]


@pytest.mark.parametrize("ending", [
    "grace", "protocol", "native", "native_single", "native_partial", "native_invalid_end",
    "cancelled", "callback_group", "callback_native", "malformed_facts", "unknown_stop",
    "unknown_callback", "unknown_protocol", "unknown_completed", "completed", "late_completed",
    "control_completed", "unknown_control_completed",
    "control_native_partial", "unknown_control_native_partial"])
def test_grouped_ending_reaches_terminal_and_saved_result(
        ending, monkeypatch, tmp_path):
    """Run the real client and operation, with scripted transport and double faults."""
    from test_frontend import event_until
    from test_instrument_replies import _error, _session

    import color_measurement.instrument as module
    from color_measurement.instrument import InstrumentError
    from color_measurement.instrument_frontend import InstrumentOperation
    from color_measurement.native_protocol import ProtocolError
    from color_measurement.operation_exports import load, save
    from color_measurement.operation_records import result_from_json
    from color_measurement.operations import OperationBusyError

    completed = ending in {"protocol", "completed", "late_completed", "unknown_completed",
                          "control_completed", "unknown_control_completed"}
    session, meter, _ = _session("acknowledge", _error("broken_pipe", monkeypatch),
                                 "completed" if completed else "native_error", monkeypatch)
    session.events.insert(1, {"protocol_version": 1, "provenance": "simulated",
        "event": "native_facts", "driver": "scripted", "capture": {"reply_count": 0},
        "identity": {"serial": "TEST"}})
    if ending == "malformed_facts":
        session.events[1]["capture"]["reply_count"] = 1
    native_callback = ending in {"callback_native", "unknown_callback"}
    unconfirmed = ending.startswith("unknown_")
    callback = (InstrumentError("cancelled", 17) if native_callback else
                ExceptionGroup("operator callback failed", [
                    InstrumentError("cancelled", 17), RuntimeError("operator UI failed")]))
    # A callback may itself hold a prior native group. Its metadata must not
    # change this operation's principal callback failure into a native stop.
    callback.instrument_error = (callback if native_callback
                                 else callback.exceptions[0])
    callback_failures = []
    if ending in {"callback_group", "callback_native", "unknown_callback"}:
        def fail_callback(*args):
            raise callback
        monkeypatch.setattr(InstrumentOperation, "_acknowledge", fail_callback)
        measure = Instrument.measure

        def measured(self, *args, **kwargs):
            try:
                return measure(self, *args, **kwargs)
            except Exception as error:
                callback_failures.append(error)
                raise
        monkeypatch.setattr(Instrument, "measure", measured)
    if ending in {"grace", "cancelled", "callback_group", "callback_native",
                  "malformed_facts", "unknown_stop", "unknown_callback"}:
        session.events[-2:] = []
        receive = session.receive_message

        def receive_message(**kwargs):
            if session.events:
                return receive(**kwargs)
            session.clock.now += 0.5
            raise TimeoutError("waiting for terminal")
        session.receive_message = receive_message
    elif ending in {"protocol", "native_invalid_end", "unknown_protocol"}:
        finish = session.finish_messages

        def finish_messages(**kwargs):
            finish(**kwargs)
            raise ProtocolError("Helper exited before the expected event")
        session.finish_messages = finish_messages
    if ending in {"native_partial", "native_invalid_end", "completed", "late_completed",
                  "unknown_completed", "control_completed", "unknown_control_completed",
                  "control_native_partial", "unknown_control_native_partial"}:
        # Feed a real validated reading before the real native error frame. Only
        # a fully validated terminal drain may retain that reading after cleanup.
        session.events.insert(2, {"protocol_version": 1, "provenance": "simulated",
            "event": "calibration_ready", "needed_calibrations": 0,
            "available_calibrations": 0, "remaining_calibrations": 0,
            "status_source": "driver", "skipped": False, "performed": False,
            "initial_calibration_suppressed": False})
        session.events.insert(-1 if ending.endswith("completed") else -2,
            {"protocol_version": 1, "provenance": "simulated",
            "event": "result", "backend_reported": {
                "mtype": 1, "mcond": 0, "XYZ_v": 1, "XYZ": [1, 2, 3],
                "spectrum": None, "duration": 0}})
    if ending.endswith("completed"):
        session.send = lambda command, argument="": session.sent.append((command, argument))
    if ending == "late_completed":
        receive = session.receive_message

        def receive_message(**kwargs):
            event = receive(**kwargs)
            if event["event"] == "calibration_ready":
                session.clock.now = 4.0  # Expire the host deadline before the late result.
            return event
        session.receive_message = receive_message
    if unconfirmed:
        session.stopped = None  # close() fails before observing this started helper's end.
        for event in session.events:
            # Exercise the device-state branch with scripted frames, without
            # launching a helper or accessing any instrument.
            event["provenance"] = "physical"

    cleanup = []
    original_control = KeyboardInterrupt("session close failed")
    controls = []
    if "control_" in ending:
        import threading

        monkeypatch.setattr(threading, "excepthook", lambda args: controls.append(args.exc_value))

    def close():
        session.closed = True
        cleanup.append("session")
        if "control_" in ending:
            raise original_control
        raise RuntimeError("session close failed")

    def close_resources(self):
        cleanup.append("resources")
        if ending != "native_single":
            raise RuntimeError("resources close failed")

    session.close = close
    monkeypatch.setattr(module.ExitStack, "close", close_resources)
    front = InstrumentFrontend(meter,
        mode_capabilities=mode_word("emissive_spot", spectral=False),
        trigger_capabilities=1, filter_capabilities=0,
        provenance="physical" if unconfirmed else "simulated")
    snapshot = json.loads(front.describe_json())
    operation = front.prepare(json.dumps({"schema_version": 1,
        "device_id": snapshot["device_id"], "capability_id": snapshot["capability_id"],
        "mode": "emissive_spot", "workflow": "spot", "patch_ids": ["A1"],
        "options": {}, "layout": {}}))
    try:
        operation.dispatch()
        events = event_until(operation, response="cancel" if ending == "cancelled" else "accept")
        result = result_from_json(operation.result_json())
        assert result.status == "failed"
        retained_read = ending in {"native_partial", "completed", "late_completed",
                                  "unknown_completed", "control_completed",
                                  "unknown_control_completed", "control_native_partial",
                                  "unknown_control_native_partial"}
        assert len(result.patches) == int(retained_read)
        if retained_read:
            assert result.patches[0].observation.backend_reported.xyz.values == (1.0, 2.0, 3.0)
            assert "native_facts_json" in dict(result.patches[0].observation.configuration)
        assert result.software_stopped == (not unconfirmed)
        assert result.instrument_state == (
            "unknown" if unconfirmed else "not_applicable")
        assert operation.done and operation._ownership.locked() == unconfirmed
        assert session.closed and cleanup == ["session", "resources"]
        assert "session close failed" in result.error
        if "control_" in ending:
            operation._thread.join(2)
            assert controls == [original_control]
            assert original_control.worker_stop == ("unknown" if unconfirmed else "exited")
            assert original_control.measurement.patches[0].xyz == (1.0, 2.0, 3.0)
            assert original_control.events[-1]["event"] == "terminal"
        assert ("resources close failed" in result.error) == (ending != "native_single")
        context = dict(result.configuration)
        if unconfirmed:
            assert context["worker_stop"] == context["device_completion"] == "unknown"
            commands = list(session.sent)
            with pytest.raises(OperationBusyError):
                front.prepare(result.request_json)
            assert session.sent == commands
        if ending in {"grace", "native", "native_single", "native_partial", "cancelled",
                      "malformed_facts", "unknown_stop", "late_completed",
                      "control_native_partial", "unknown_control_native_partial"}:
            expected_stage = {"grace": "timeout", "native": "read_sample",
                              "native_single": "read_sample", "unknown_stop": "timeout",
                              "late_completed": "timeout",
                              "native_partial": "read_sample",
                              "malformed_facts": "timeout",
                              "cancelled": "cancelled", "control_native_partial": "read_sample",
                              "unknown_control_native_partial": "read_sample"}[ending]
            assert result.error_stage == expected_stage
            native_ending = ending.startswith("native") or "control_native_partial" in ending
            assert result.error_native_code == (458752 if native_ending else 0)
            assert expected_stage in result.error
            if ending == "malformed_facts":
                assert "native_facts_json" not in context
                assert "Native replies are incomplete" in result.error
            else:
                facts_context = (dict(result.patches[0].observation.configuration)
                                 if retained_read else context)
                assert "native_facts_json" in facts_context
                assert json.loads(facts_context["native_facts_json"])["identity"] == {
                    "serial": "TEST"}
            if native_ending:
                assert context["error_native_text"] == "queued driver ending"
                assert context["error_native_text_truncated"] == "true"
            else:
                assert context["stop_requested_by"] == (
                    "client" if ending == "cancelled" else "host_deadline")
                assert context["stop_acknowledged_by"] == "none"
                assert context["worker_stop"] == (
                    "unknown" if ending == "unknown_stop" else "exited")
                assert context["device_completion"] == (
                    "completed" if ending == "late_completed" else "unknown")
        else:
            assert result.error_stage is None and result.error_native_code is None
            assert "stop_requested_by" not in context
            if ending not in {"completed", "unknown_completed", "control_completed",
                              "unknown_control_completed"}:
                assert ("Helper exited before the expected event"
                        if ending in {"protocol", "native_invalid_end", "unknown_protocol"}
                        else "cancelled (native code 17)" if native_callback
                        else "operator callback failed") in result.error
            if ending in {"callback_group", "callback_native", "unknown_callback"}:
                assert len(callback_failures) == 1 and callback_failures[0] is callback
                assert callback.instrument_error is None and callback.measurement is None
                if ending == "callback_group":
                    assert "operator UI failed" in result.error
        terminal = events[-1]["payload"]
        assert sum(event["kind"] == "terminal" for event in events) == 1
        for key in ("status", "error", "error_stage", "error_native_code", "software_stopped"):
            assert terminal[key] == getattr(result, key)
        assert load(save(result, tmp_path / "failed.json").path) == result
        if unconfirmed:
            # Retrieving, saving and closing a terminal operation are not
            # confirmation that its helper stopped. Recovery must preserve the
            # result without making the same live backend admit another Start.
            operation.close()
            operation._thread.join(3)
            assert not operation._thread.is_alive()
            assert operation._ownership.locked()
            assert result_from_json(operation.result_json()) == result
            assert load(tmp_path / "failed.json") == result
            commands = list(session.sent)
            with pytest.raises(OperationBusyError):
                front.prepare(result.request_json)
            assert session.sent == commands
    finally:
        operation.close()
        operation._thread.join(3)
        assert not operation._thread.is_alive()


def test_failed_cleanup_does_not_adopt_configured_snapshot(monkeypatch):
    """A failed query keeps observed events without changing the next descriptor."""
    from test_frontend import event_until
    from test_instrument_replies import _session

    from color_measurement.operation_records import result_from_json

    session, meter, _ = _session("acknowledge", None, "completed", monkeypatch)
    session.events[:] = [session.events[0], {
        "protocol_version": 1, "provenance": "simulated", "event": "stored_refresh_rate",
        "status": "not_advertised", "native_code": None, "value": None,
        "unit": "Hz", "native_method": "get_refr_rate"}, session.events[-1]]
    session.events[0].update(mode_capabilities=mode_word("emissive_spot", spectral=False),
                             trigger_capabilities=1, filter_capabilities=0, correction="none")

    def close():
        session.closed = True
        raise RuntimeError("session close failed")

    session.close = close
    front = InstrumentFrontend(meter,
        mode_capabilities=mode_word("emissive_spot", spectral=False),
        trigger_capabilities=1, filter_capabilities=0, provenance="simulated")
    before = json.loads(front.describe_json())
    operation = front.prepare(json.dumps({"schema_version": 1,
        "device_id": before["device_id"], "capability_id": before["capability_id"],
        "mode": "emissive_spot", "workflow": "mode_snapshot", "patch_ids": [],
        "options": {}, "layout": {}}))
    try:
        operation.dispatch()
        event_until(operation)
        result = result_from_json(operation.result_json())
        assert result.status == "failed" and not result.patches
        assert result.error_stage is None and result.error_native_code is None
        assert session.finished and session.closed
        assert "session close failed" in result.error
        assert json.loads(front.describe_json()) == before
        assert "mode_snapshot_json" not in dict(result.configuration)
        state = json.loads(dict(result.configuration)["stored_refresh_json"])
        assert state["status"] == "not_advertised"
        assert state["native_code"] is None and state["value"] is None
        assert result.software_stopped and not operation._ownership.locked()
    finally:
        operation.close()
        operation._thread.join(3)
        assert not operation._thread.is_alive()


def test_prepared_frozen_request_normalizes_once_without_worker_or_native_session(monkeypatch):
    from color_measurement.demo import demo_frontends
    from color_measurement.instrument_frontend import InstrumentOperation

    front = next(
        front
        for front in demo_frontends()
        if json.loads(front.describe_json())["device_id"] == "demo:i1pro3"
    )
    descriptor = json.loads(front.describe_json())
    request = dict(
        schema_version=1, device_id=descriptor["device_id"],
        capability_id=descriptor["capability_id"], mode="emissive_spot",
        workflow="spot", patch_ids=["prepare-only"], options={}, layout={},
    )
    validations = []
    validate = front._validate_request

    def counted(text):
        validations.append(text)
        return validate(text)

    def forbidden(*args, **kwargs):
        pytest.fail("Request preparation attempted worker dispatch or device I/O")

    monkeypatch.setattr(front, "_validate_request", counted)
    monkeypatch.setattr(InstrumentOperation, "_start_worker", forbidden)
    monkeypatch.setattr(Instrument, "_open_session", forbidden)
    operation = front.prepare(json.dumps(request))
    frozen = operation.request_json
    assert len(validations) == 1
    assert json.loads(frozen) == dict(request, event_contract="retained_patch_v1")
    assert not operation.done and operation._thread is None
    assert not front._ownership.locked()
    request["patch_ids"][0] = "mutated-source"
    decoded = json.loads(frozen)
    decoded["patch_ids"][0] = "mutated-copy"
    assert operation.request_json == frozen
    with pytest.raises(AttributeError):
        operation.request_json = "{}"
    operation.cancel(operation.operation_id)
    assert operation.done and json.loads(operation.result_json())["software_stopped"]
    bad = dict(request, options={"unsupported_setting": True})
    with pytest.raises(ValueError):
        front.prepare(json.dumps(bad))
    stale = dict(request, capability_id="stale")
    with pytest.raises(ValueError, match="Stale"):
        front.prepare(json.dumps(stale))


@pytest.mark.parametrize("contribution", [
    None, {"instrument_types_added": []}, {"instrument_types_added": ["instCR100"]},
    {"instrument_types_added": [["instCR250"], "instCR100"]},
    {"instrument_types_added": ["instCR100", "instCR250"]},
    {"instrument_types_added": ["instCR250", "instCR100", "instCR300", "instPR655"]},
])
def test_descriptor_refresh_refuses_missing_driver_series_before_projection(
    tmp_path, monkeypatch, contribution,
):
    import importlib

    generator = importlib.import_module("generate_frontend_fixtures")
    build, output = tmp_path / "build", tmp_path / "fixtures"
    build.mkdir()
    output.mkdir()
    (build / "report.json").write_text(json.dumps({"contribution": contribution}))
    index = output / "index.json"
    index.write_text(json.dumps({"personas": {}}))
    original = index.read_bytes()
    calls = []
    monkeypatch.setattr(generator, "OUT", output)
    monkeypatch.setattr(generator, "PERSONAS", ())
    monkeypatch.setattr(generator, "authenticate_stub", lambda worker: calls.append(worker))
    with pytest.raises(SystemExit, match="composed with the CR/PR series"):
        generator._refresh_descriptors(build)
    assert calls == []
    assert index.read_bytes() == original


def test_descriptor_refresh_qualifies_driver_series_and_workers_without_acquisition(
    tmp_path, monkeypatch,
):
    import importlib

    generator = importlib.import_module("generate_frontend_fixtures")
    build, output = tmp_path / "build", tmp_path / "fixtures"
    build.mkdir()
    output.mkdir()
    (build / "report.json").write_text(json.dumps({
        "contribution": {"instrument_types_added": [
            "instCR250", "instCR100", "instCR300", "instPR655", "instPR670"]},
    }))
    (output / "index.json").write_text(json.dumps({"personas": {}}))
    calls = []
    monkeypatch.setattr(generator, "OUT", output)
    monkeypatch.setattr(generator, "PERSONAS", ())
    monkeypatch.setattr(generator, "authenticate_stub", lambda worker: calls.append(worker.name))
    assert generator._require_driver_series(build) == {
        "instCR250", "instCR100", "instCR300", "instPR655", "instPR670"}
    assert generator._refresh_descriptors(build, check=True) == 0
    assert calls == [native_worker_name("generic-session-stub-worker"),
                     native_worker_name("generic-chart-stub-worker")]


@pytest.fixture(params=["Darwin", "Windows"])
def descriptor_projection(tmp_path, monkeypatch, request):
    """Inert worker bytes and retained metadata exercise the existing projection owner."""
    import importlib
    from types import SimpleNamespace

    from color_measurement.argyll_capability import baseline_mode_words
    from color_measurement.discovery import InstrumentBuild

    monkeypatch.setattr("color_measurement.native_receipt.platform.system", lambda: request.param)
    generator = importlib.import_module("generate_frontend_fixtures")
    build, output = tmp_path / "build with spaces", tmp_path / "fixtures"
    build.mkdir()
    output.mkdir()
    helper = build / native_worker_name("generic-session-stub-worker")
    helper.write_bytes(b"Inert acquisition helper; never executable")
    (build / native_worker_name("generic-chart-stub-worker")).write_bytes(
        b"Inert chart helper; never executable")
    digest = hashlib.sha256(helper.read_bytes()).hexdigest()
    row = dict(enum="instCR250", native_type=39, native_facts=True,
               native_controls=True, read_allowance_floor_s=120.0)
    report = dict(contribution={"instrument_types_added": [
        "instCR250", "instCR100", "instCR300", "instPR655", "instPR670"]},
        driver_manifest={"instrument_types": [row]})
    report_path = build / "report.json"
    report_path.write_text(json.dumps(report))
    owner = InstrumentBuild(object(), helper, expected_helper_sha256=digest, timeout=300)
    policy = owner.binding_policy("instCR250", catalog_json=json.dumps(report["driver_manifest"]))
    instrument, serial = "Colorimetry Research CR-250", "PROJECTION-ONLY"
    packet = dict(device_id=instrument, initialized_type=39,
        native_identity=dict(driver="cr250", native_controls=True, identity=dict(
            verified=True, model="CR-250", serial=serial, firmware="1.26",
            firmware_components=[1, 26], instrument_type=2)),
        provenance=dict(backend_version="3.5.0", description_helper_sha256="1" * 64,
            session_helper_sha256=digest,
            build_report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest()))
    primary = mode_word("emissive_spot", spectral=True)
    connected = dict(event="connected", mode_capabilities=primary,
        trigger_capabilities=16, filter_capabilities=0,
        baseline_mode_checks=dict(scope="initialized_before_mode_configuration",
            mode_capabilities=primary,
            checks=[dict(mode_word=word, native_code=0) for word in baseline_mode_words(primary)]),
        display_types=dict(native_display_unsupported(), entries=[]), strip_policies={
            name: dict(max_patches=0, requirement=None, layout_fields=[])
            for name in ("reflection", "transmission", "emission")})
    meter = Instrument(helper, instrument, expected_serial=serial, timeout=300,
                       expected_helper_sha256=digest)
    front = InstrumentFrontend(meter,
        mode_capabilities=primary, trigger_capabilities=16, filter_capabilities=0,
        baseline_mode_checks=connected["baseline_mode_checks"],
        display_types=connected["display_types"], strip_policies=connected["strip_policies"],
        provenance="simulated", serial=serial, read_allowance_floor=120,
        native_facts=True, native_controls_available=True, binding_policy=policy,
        current_binding_identity=packet)
    descriptor = json.loads(front.describe_json())
    folder = output / "cr250"
    folder.mkdir()
    (folder / "describe.json").write_text(json.dumps(descriptor, indent=2, sort_keys=True) + "\n")
    (output / "index.json").write_text(json.dumps(dict(personas={
        "cr250": dict(capability_id=descriptor["capability_id"])}),
        indent=2, sort_keys=True) + "\n")
    (folder / "retained-native.json").write_text(json.dumps(dict(result=dict(patches=[dict(
        observation=dict(raw_backend_output=json.dumps(dict(events=[connected]))))]))))
    monkeypatch.setattr(generator, "OUT", output)
    monkeypatch.setattr(generator, "PERSONAS", ((
        "cr250", "generic-session-stub-worker", instrument, serial,
        {"CM_COLORIMETER_FIXTURE": "cr250"},
        "emissive_spot", True),))
    monkeypatch.setattr(generator, "authenticate_stub", lambda *_: None)
    monkeypatch.setattr(generator, "_frontend", lambda *a, **kw: pytest.fail("Projection acquired"))
    monkeypatch.setattr(Instrument, "_open_session",
                        lambda *a, **kw: pytest.fail("Projection started a worker"))
    return SimpleNamespace(generator=generator, build=build, output=output, helper=helper,
                           descriptor=descriptor, packet=packet, policy=policy)


def test_descriptor_projection_preserves_policy_capability_and_original_receipt(
    descriptor_projection,
):
    case = descriptor_projection
    before = {path.relative_to(case.output): path.read_bytes()
              for path in case.output.rglob("*.json")}
    # Reformatting the current report changes its digest without changing worker
    # bytes. Projection must retain the receipt that actually supplied identity.
    report = case.build / "report.json"
    report.write_text(json.dumps(json.loads(report.read_bytes()), indent=4))
    assert hashlib.sha256(report.read_bytes()).hexdigest() != case.packet["provenance"][
        "build_report_sha256"]
    assert case.generator._refresh_descriptors(case.build) == 0
    current = json.loads((case.output / "cr250/describe.json").read_bytes())
    assert current["limits"]["binding_policy"] == case.policy
    assert current["current_binding_identity"] == case.packet
    assert current["capability_id"] == case.descriptor["capability_id"]
    assert {path.relative_to(case.output): path.read_bytes()
            for path in case.output.rglob("*.json")} == before


@pytest.mark.parametrize("change", ["changed_helper", "missing_receipt"])
def test_descriptor_projection_requires_new_capture_before_any_write(
    descriptor_projection, monkeypatch, change,
):
    import shlex

    case = descriptor_projection
    if change == "changed_helper":
        case.helper.write_bytes(b"Changed acquisition helper, never executable")
    else:
        original = json.loads((case.output / "cr250/describe.json").read_bytes())
        del original["current_binding_identity"]["provenance"]
        (case.output / "cr250/describe.json").write_text(json.dumps(original))
    before = {path.relative_to(case.output): path.read_bytes()
              for path in case.output.rglob("*.json")}
    monkeypatch.setattr(case.generator, "_write", lambda *a, **kw: pytest.fail("Projection wrote"))
    with pytest.raises(SystemExit,
                       match="missing native receipt or changed acquisition helper") as refused:
        case.generator._refresh_descriptors(case.build)
    command = shlex.split(str(refused.value).splitlines()[-1])
    assert command == ["uv", "run", "--locked", "--extra", "mat", "--extra", "app", "python",
        "tools/generate_frontend_fixtures.py", "--build", str(case.build.resolve()),
        "--refresh-current-captures", "--personas", "cr250"]
    assert {path.relative_to(case.output): path.read_bytes()
            for path in case.output.rglob("*.json")} == before


def test_descriptor_projection_names_missing_new_persona_before_any_write(
    descriptor_projection, monkeypatch,
):
    import shlex

    case = descriptor_projection
    monkeypatch.setattr(case.generator, "PERSONAS", (*case.generator.PERSONAS, (
        "pr670", "generic-session-stub-worker", "Photo Research PR-670", "PROJECTION-ONLY", {},
        "emissive_spot", True)))
    before = {path.relative_to(case.output): path.read_bytes()
              for path in case.output.rglob("*.json")}
    monkeypatch.setattr(case.generator, "_write", lambda *a, **kw: pytest.fail("Projection wrote"))
    with pytest.raises(SystemExit,
                       match="pr670: missing persona descriptor or index entry") as refused:
        case.generator._refresh_descriptors(case.build)
    command = shlex.split(str(refused.value).splitlines()[-1])
    assert command[command.index("--build") + 1] == str(case.build.resolve())
    assert command[-3:] == ["--refresh-current-captures", "--personas", "pr670"]
    assert {path.relative_to(case.output): path.read_bytes()
            for path in case.output.rglob("*.json")} == before


def test_fixture_descriptor_identity_uses_content_without_its_digest():
    import importlib

    generator = importlib.import_module("generate_frontend_fixtures")
    descriptor = json.loads((Path(__file__).parent /
                             "fixtures/generic_frontend/cr100/describe.json").read_bytes())
    generator._validate_descriptor_identity(descriptor)
    descriptor["mode_capabilities"] ^= 1
    with pytest.raises(ValueError, match="content identity differs"):
        generator._validate_descriptor_identity(descriptor)


def test_fixture_write_preserves_original_run_fields_but_updates_changed_observations(tmp_path):
    import importlib

    generator = importlib.import_module("generate_frontend_fixtures")
    path = tmp_path / "capture.json"
    original = dict(operation_id="1" * 8 + "-1111-1111-1111-" + "1" * 12, value=2)
    replacement = dict(operation_id="2" * 8 + "-2222-2222-2222-" + "2" * 12, value=2)
    generator._write(path, original)
    retained = path.read_bytes()
    generator._write(path, replacement)
    assert path.read_bytes() == retained
    replacement["value"] = 3
    generator._write(path, replacement)
    assert json.loads(path.read_bytes()) == replacement
    # A new authenticated build must publish the observations actually captured
    # from it, even when scientific values and logical capabilities are stable.
    replacement["current_binding_identity"] = {
        "provenance": {"session_helper_sha256": "a" * 64,
                       "description_helper_sha256": "b" * 64,
                       "build_report_sha256": "c" * 64}}
    generator._write(path, replacement)
    for field in ("session_helper_sha256", "description_helper_sha256", "build_report_sha256"):
        fresh = json.loads(json.dumps(replacement))
        fresh["current_binding_identity"]["provenance"][field] = "d" * 64
        assert generator._per_run_blank(json.dumps(fresh)) == generator._per_run_blank(
            json.dumps(replacement))
        generator._write(path, fresh)
        assert json.loads(path.read_bytes()) == fresh
        replacement = fresh


def test_scoped_fixture_refresh_never_drives_unselected_personas(tmp_path, monkeypatch):
    import importlib
    from types import SimpleNamespace

    generator = importlib.import_module("generate_frontend_fixtures")
    output, build = tmp_path / "fixtures", tmp_path / "build"
    output.mkdir()
    build.mkdir()
    for worker in ("generic-session-stub-worker", "generic-chart-stub-worker",
                   "description-stub-worker"):
        (build / native_worker_name(worker)).write_bytes(b"not executed")
    monkeypatch.setattr(generator, "OUT", output)
    discovery = tmp_path / "discovery"
    monkeypatch.setattr(generator, "DISCOVERY_OUT", discovery)
    catalog = {"instrument_types": [
        {"enum": "instPR655", "read_allowance_floors_s": {"65800": 310}},
        {"enum": "instI1Disp3"},
    ]}
    generator._write(build / "driver-manifest.json", catalog)
    generator._write(discovery / "catalog.json", {"instrument_types": []})
    retained_discovery = discovery / "native" / "preserved" / "list.json"
    generator._write(retained_discovery, {"original": True})
    discovery_bytes = retained_discovery.read_bytes()
    monkeypatch.setattr(generator, "_require_driver_series", lambda build: {
        "instCR250", "instCR100", "instCR300", "instPR655", "instPR670"})
    monkeypatch.setattr(generator, "authenticate_stub", lambda worker: None)
    monkeypatch.setattr(generator, "PERSONAS", tuple(
        (name, "generic-session-stub-worker", name, None, {}, "emissive_spot", False)
        for name in ("picked", "untouched", "i1_display_3")))
    index = dict(personas={name: dict(calibration_retained=None, capability_id=name)
                          for name in ("picked", "untouched", "i1_display_3")},
                 calibrations=[], scenarios=[], native_failures=[])
    generator._write(output / "index.json", index)
    unrelated = output / "untouched" / "original.json"
    generator._write(unrelated, {"value": "original"})
    original = unrelated.read_bytes()
    generator._write(output / "picked" / "obsolete.json", {"value": "obsolete"})
    generator._write(output / "picked" / "calibration_retained.json", {"original": True})
    calls = []

    def frontend(build, worker, instrument, *args):
        calls.append(instrument)
        assert instrument == "picked"
        descriptor = dict(modes=[], capability_id="picked", event_contracts=[])
        return None, SimpleNamespace(describe_json=lambda: json.dumps(descriptor))

    monkeypatch.setattr(generator, "_frontend", frontend)
    monkeypatch.setattr(generator, "_refusals", lambda *args: {})
    assert generator._generate(build, refresh_current_captures=True, selected=["picked"]) == 0
    assert calls == ["picked"] and unrelated.read_bytes() == original
    assert not (output / "picked" / "obsolete.json").exists()
    assert (output / "picked" / "calibration_retained.json").exists()
    current = json.loads((output / "index.json").read_bytes())
    assert current["personas"]["untouched"] == index["personas"]["untouched"]
    assert current["personas"]["i1_display_3"] == index["personas"]["i1_display_3"]
    assert json.loads((discovery / "catalog.json").read_bytes()) == catalog
    assert json.loads((discovery / "catalog-without-contribution.json").read_bytes()) == {
        "instrument_types": [{"enum": "instI1Disp3"}],
    }
    assert retained_discovery.read_bytes() == discovery_bytes


def test_scoped_native_discovery_only_runs_selected_cases(tmp_path, monkeypatch):
    import importlib
    from types import SimpleNamespace

    generator = importlib.import_module("generate_frontend_fixtures")
    output = tmp_path / "discovery"
    output.mkdir()
    monkeypatch.setattr(generator, "DISCOVERY_OUT", output)
    monkeypatch.setattr(generator, "authenticate_stub", lambda worker: None)
    calls = []

    def client(worker, scenario):
        calls.append(scenario)
        return SimpleNamespace(list_candidates_json=lambda: json.dumps(
            {"status": "completed", "data": {"candidates": []}}))

    monkeypatch.setattr(generator, "NativeDescriptionClient",
                        SimpleNamespace(for_offline_tests=client))
    unrelated = output / "native" / "nothing_attached" / "list.json"
    generator._write(unrelated, {"original": True})
    original = unrelated.read_bytes()
    catalog = json.dumps({"instrument_types": [{"enum": "instCR250", "native_type": 39}]})
    cases = generator._capture_native_messages(tmp_path, catalog, selected={"two_of_one_model"})
    assert calls == ["success"] and set(cases) == {"two_of_one_model"}
    assert unrelated.read_bytes() == original
    with pytest.raises(ValueError, match="Unknown native discovery"):
        generator._capture_native_messages(tmp_path, catalog, selected={"nonexistent"})
    assert calls == ["success"]


def test_mixed_fixture_scope_refuses_unknown_discovery_before_frontend_writes(
    tmp_path, monkeypatch,
):
    import importlib

    generator = importlib.import_module("generate_frontend_fixtures")
    discovery = tmp_path / "discovery"
    discovery.mkdir()
    (discovery / "index.json").write_text(json.dumps({"native_messages": {"valid": {}}}))
    monkeypatch.setattr(generator, "DISCOVERY_OUT", discovery)
    monkeypatch.setattr(generator, "_generate", lambda *args, **kw: pytest.fail("Frontend writes"))
    with pytest.raises(ValueError, match="existing native discovery"):
        generator.main(["--build", str(tmp_path), "--refresh-current-captures",
                        "--personas", "cr250", "--native-discovery", "typo"])


@pytest.mark.parametrize("retention_failure", [False, True])
@pytest.mark.parametrize("ending", ["native_error", "cancelled"])
@pytest.mark.parametrize("native_bin_count,refuse_mapping", [
    (4, False), (10000, False), (0, False), (4, True), (1, True),
])
def test_cr_refused_publication_retains_unknown_native_observation_and_failed_terminal(
    monkeypatch, tmp_path, retention_failure, ending, native_bin_count, refuse_mapping,
):
    import math

    from test_frontend import event_until
    from test_instrument import cr_qualified_facts
    from test_instrument_replies import _session

    from color_measurement.operation_exports import load, save
    from color_measurement.operation_records import result_from_json

    session, _, _ = _session("not_used", RuntimeError("never sent"), ending, monkeypatch)
    if ending == "cancelled":
        next(event for event in session.events if event["event"] == "error")["stage"] = "cancelled"
    session.events[0]["spectral"] = True
    session.events[0]["mode"] = mode_word("emissive_spot", spectral=True)
    facts = cr_qualified_facts(verdict="inconclusive", published=False, serial="TEST")
    if native_bin_count == 0:
        facts["capture"]["identity"] = dict(
            verified=True, model="CR-250", serial="TEST", firmware="1.34",
            firmware_components=[1, 34], instrument_type=2,
        )
        facts["identity"] = dict(verified=False, model=None, serial=None, firmware=None,
                                 firmware_components=None, instrument_type=None)
        facts["link_uncertain"] = True
        facts["capture"]["spectrum"] = None
    elif native_bin_count > 601:
        facts["capture"]["unit_check"]["verdict"] = "not_checked"
        facts["capture"]["spectrum"].update(
            count=native_bin_count, start_nm=380, step_nm=0.05,
            end_nm=380 + (native_bin_count - 1) * 0.05,
            values=[(-0.2, 0.0, 0.4)[index % 3] for index in range(native_bin_count)],
        )
    elif refuse_mapping:
        step = 0.75 * math.ulp(500) if native_bin_count > 1 else 1
        facts["capture"]["unit_check"]["verdict"] = "not_checked"
        facts["capture"]["spectrum"].update(
            count=native_bin_count, start_nm=500, step_nm=step,
            end_nm=500 + (native_bin_count - 1) * step,
            values=[0.1, -0.2, 0.0, 0.4][:native_bin_count],
        )
    if retention_failure:
        facts["capture"].get("identity", facts["identity"])["serial"] = "ANOTHER-UNIT"
    head = dict(facts, event="native_facts", protocol_version=1, provenance="simulated")
    values = head["capture"]["spectrum"].pop("values") if native_bin_count else []
    count = (len(values) + 99) // 100
    head["capture"]["spectrum_chunk_count"] = count
    raw = []
    if native_bin_count == 0:
        wire = b"ER,\x00partial"
        head["capture"]["raw_exchange_count"] = 1
        raw = [dict(event="native_raw", protocol_version=1, provenance="simulated",
                    driver="cr250", command="RM Exposure",
                    exchange_index=0, exchange_count=1, complete=False, error=273,
                    byte_offset=0, byte_total=len(wire), raw_hex=wire.hex())]
    elif refuse_mapping:
        grid = head["capture"]["spectrum"]
        wire = (f"OK:0:RM Spectrum:{grid['start_nm']:.17g},{grid['end_nm']:.17g},"
                f"{grid['step_nm']:.17g},{native_bin_count}\r\n"
                + "".join(f"{value:.17g}\r\n" for value in values)).encode("ascii")
        head["capture"]["raw_exchange_count"] = 1
        raw = [dict(event="native_raw", protocol_version=1, provenance="simulated",
                    driver="cr250", command="RM Spectrum",
                    exchange_index=0, exchange_count=1, complete=True, error=0xff10,
                    byte_offset=0, byte_total=len(wire), raw_hex=wire.hex())]
    session.events[1:1] = [head, *raw, *(dict(
        event="native_spectrum", protocol_version=1, provenance="simulated", driver="cr250",
        spectrum_chunk_index=index, spectrum_chunk_count=count, offset=index * 100,
        values=values[index * 100:(index + 1) * 100],
    ) for index in range(count))]
    meter = Instrument("/never-open", "instCR250", timeout=3)
    mode = "emissive_spot"
    front = InstrumentFrontend(
        meter, mode_capabilities=mode_word(mode, spectral=True), trigger_capabilities=16,
        filter_capabilities=0, provenance="simulated", serial="TEST", native_facts=True,
    )
    descriptor = json.loads(front.describe_json())
    operation = front.prepare(json.dumps(dict(
        schema_version=1, device_id=descriptor["device_id"],
        capability_id=descriptor["capability_id"], mode=mode, workflow="spot",
        patch_ids=["raw"], options={}, layout={}, event_contract="retained_patch_v1",
    )))
    operation.dispatch()
    events = event_until(operation)
    result = result_from_json(operation.result_json())
    expected_status, expected_stage = ("cancelled", "cancelled") if ending == "cancelled" else (
        "failed", "read_sample")
    if retention_failure:
        assert result.status == expected_status and result.error_stage == expected_stage
        assert "native original retention refused" in result.error
        assert result.software_stopped and result.patches == ()
        assert not any(event["kind"] == "patch_reference" for event in events)
        assert session.closed and session.finished
        operation.close()
        return
    assert any(event["kind"] == "patch_reference" for event in events), result.error
    assert events[-1]["kind"] == "terminal"
    assert result.status == expected_status and result.error_stage == expected_stage
    assert result.software_stopped and len(result.patches) == 1
    observation = result.patches[0].observation
    if native_bin_count and not refuse_mapping:
        assert observation.backend_reported.spectrum.values == tuple(values)
        assert "unverified" in observation.backend_reported.spectrum.unit
    else:
        assert observation.backend_reported.spectrum is None
        retained = json.loads(dict(observation.configuration)["native_facts_json"])
        if native_bin_count == 0:
            assert retained["identity"]["verified"] is False and retained["link_uncertain"] is True
            assert retained["capture"]["identity"]["serial"] == "TEST"
        else:
            assert retained["capture"]["spectrum"]["values"] == values
            assert retained["capture"]["spectrum"]["count"] == native_bin_count
            refusal = dict(observation.configuration)["native_spectrum_mapping_refusal"]
            assert refusal in observation.warnings
            reason = "strictly increasing" if native_bin_count > 1 else "grid is inconsistent"
            assert reason in refusal
        assert retained["raw_replies"][0]["raw_hex"] == wire.hex()
    assert observation.backend_reported.xyz.values == (1, -2, 3)
    context = dict(result.configuration)
    assert context["argyll_publication_status"] == "refused"
    assert context["native_observation_retained"] == "true"
    assert load(save(result, tmp_path / "refused-native.json").path) == result
    assert session.closed and session.finished
    operation.close()


@pytest.mark.parametrize("case", ["calibration", "already_retained"])
def test_native_failure_retention_creates_only_one_original_for_a_spot_request(case):
    from test_instrument import cr_qualified_facts

    from color_measurement.instrument import InstrumentError

    meter = Instrument("/never-open", "instCR250", timeout=3)
    mode = "emissive_spot"
    front = InstrumentFrontend(
        meter, mode_capabilities=mode_word(mode, spectral=True), trigger_capabilities=16,
        filter_capabilities=0, provenance="simulated", serial="TEST", native_facts=True,
    )
    descriptor = json.loads(front.describe_json())
    workflow = "calibrate" if case == "calibration" else "spot"
    operation = front.prepare(json.dumps(dict(
        schema_version=1, device_id=descriptor["device_id"],
        capability_id=descriptor["capability_id"], mode=mode, workflow=workflow,
        patch_ids=[] if case == "calibration" else ["raw"], options={}, layout={},
        event_contract="retained_patch_v1",
    )))
    facts = cr_qualified_facts(verdict="inconclusive", published=False, serial="TEST")
    failure = InstrumentError("read_sample", 0x70000)
    failure.native_facts = facts
    try:
        operation._retain_native_failure(failure)
        held = tuple(operation._patches)
        if case == "calibration":
            assert held == ()
        else:
            assert len(held) == 1
            operation._retain_native_failure(failure)
            assert tuple(operation._patches) == held
    finally:
        operation.close()


def test_cancelled_native_measurement_refusal_keeps_its_publication_note(monkeypatch):
    from test_frontend import event_until
    from test_instrument import _measurement, _patch, cr_qualified_facts

    from color_measurement.instrument import InstrumentError
    from color_measurement.native_protocol import NativeSpectrum
    from color_measurement.operation_records import result_from_json

    facts = cr_qualified_facts(serial="ANOTHER-UNIT")
    failure = InstrumentError("cancelled", 0x70000)
    failure.native_facts = facts
    failure.measurement = _measurement(
        instrument="Colorimetry Research CR-250", serial="TEST", mode="emissive_spot",
        provenance="simulated", confirmed_routes=("emis_spot",), native_facts=facts,
        patches=(_patch(mtype=1, spectrum=NativeSpectrum(380, 410, 1, (100, -200, 0, 400))),),
    )

    def refused_read(*args, **kwargs):
        raise failure

    monkeypatch.setattr(Instrument, "_run", refused_read)
    front = InstrumentFrontend(
        Instrument("/never-open", "instCR250", timeout=3),
        mode_capabilities=mode_word("emissive_spot", spectral=True), trigger_capabilities=16,
        filter_capabilities=0, provenance="simulated", serial="TEST", native_facts=True,
    )
    descriptor = json.loads(front.describe_json())
    operation = front.prepare(json.dumps(dict(
        schema_version=1, device_id=descriptor["device_id"],
        capability_id=descriptor["capability_id"], mode="emissive_spot", workflow="spot",
        patch_ids=["raw"], options={}, layout={},
    )))
    operation.dispatch()
    event_until(operation)
    result = result_from_json(operation.result_json())
    assert result.status == "cancelled" and result.error_stage == "cancelled"
    assert result.error_native_code == 0x70000
    assert "result publication failed" in result.error
    assert "different reported serial" in result.error
    assert result.software_stopped and result.patches == ()
    operation.close()
