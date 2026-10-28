"""Connected acquisition regressions: real Python clients, substituted C workers only."""
import hashlib
import json
import os
import time
from pathlib import Path

import pytest
from conftest import (
    authenticate_worker,
    bind_cli_substitute,
    native_display_unsupported,
    retained_build_report,
    supplied_stub,
)

from color_measurement.argyll_capability import CAPABILITY2, mode_word
from color_measurement.argyll_description import NativeDescriptionClient
from color_measurement.discovery import InstrumentBuild
from color_measurement.instrument import (
    WORKFLOW_FACILITIES,
    Configuration,
    Instrument,
    InstrumentError,
)
from color_measurement.instrument_frontend import InstrumentFrontend
from color_measurement.native_protocol import ProtocolError
from color_measurement.operation_records import result_from_json


def worker(kind="generic"):
    return Path(supplied_stub(f"COLOR_MEASUREMENT_{kind.upper()}_STUB"))


def meter(fixture="success", kind="generic", **kwargs):
    env = dict(os.environ)
    env.update({"CM_COLORIMETER_FIXTURE": fixture} if kind == "generic" else
               {"CM_CHART_PERSONA": "i1pro3", "CM_CHART_FIXTURE": fixture})
    return Instrument(worker(kind), "instI1Disp3" if kind == "generic" else "instI1Pro3",
                      environment=env, **kwargs,
               expected_helper_sha256=hashlib.sha256(
                   Path(worker(kind)).read_bytes()).hexdigest())


def operation(device, mode="emissive_spot", *, timeout=3, workflow="spot", spectral=False,
              options=None, display_types=None):
    front = InstrumentFrontend(device, mode_capabilities=mode_word(mode, spectral=spectral),
                               trigger_capabilities=1 | (CAPABILITY2["disptype"]
                                   if display_types is not None else 0),
                               filter_capabilities=0, provenance="simulated",
                               display_types=display_types)
    snapshot = json.loads(front.describe_json())
    prepared_operation = front.prepare(json.dumps({
        "schema_version": 1, "device_id": snapshot["device_id"],
        "capability_id": snapshot["capability_id"], "mode": mode, "workflow": workflow,
        "patch_ids": [] if workflow == "calibrate" else ["P1"], "options": options or {},
        "layout": {}}), timeout=timeout)
    prepared_operation.dispatch()
    return prepared_operation


def finish(op, *, cancel_after_sample=False):
    instructions = []
    deadline = time.monotonic() + 8
    try:
        while not op.done:
            assert time.monotonic() < deadline, "operation failed to stop"
            text = op.poll_event_json()
            if text:
                event = json.loads(text)
                if event["kind"] == "instruction_required":
                    instruction = event["payload"]["kind"]
                    instructions.append(instruction)
                    op.respond(op.operation_id, event["payload"]["request_id"], "accept")
                    if cancel_after_sample and instruction == "sample_position":
                        time.sleep(0.1)  # The substitute is now blocked inside read_sample.
                        op.cancel(op.operation_id)
            else:
                time.sleep(0.002)
        return json.loads(op.result_json()), instructions
    finally:
        op.close()


@pytest.mark.parametrize("explicit", ["m0", "m1", "m3", "unknown", "d65"])
def test_conflicting_condition_is_rejected_before_spawning(explicit):
    with pytest.raises(ValueError, match="conflicts"):
        Instrument(Path("/must-not-run"), "instI1Pro3").measure(
            "reflective_spot_m2", configuration=Configuration(filter=explicit))


def test_optional_calibration_skip_is_explicit_and_not_fresh():
    """An actual generic calibration prompt keeps the driver's skipped outcome.

    The authenticated substituted worker supplies inst_calc_optional_flag; the
    frontend may offer skip only for that prompt, without claiming fresh work.
    """
    op = operation(meter("skippable_calibration"), workflow="calibrate")
    try:
        deadline = time.monotonic() + 8
        events = []
        while time.monotonic() < deadline:
            text = op.poll_event_json()
            if not text:
                time.sleep(.001)
                continue
            event = json.loads(text)
            events.append(event)
            if event["kind"] == "instruction_required":
                payload = event["payload"]
                assert "skip" in payload["allowed_responses"]
                op.respond(op.operation_id, payload["request_id"], "skip")
            if event["kind"] == "terminal":
                break
        else:
            pytest.fail("Skippable calibration did not terminate")
        result = result_from_json(op.result_json())
        assert result.status == "completed" and not result.patches
        assert not result.expected_patch_ids
        outcome = json.loads(dict(result.configuration)["calibration_outcome_json"])
        assert outcome["skipped"] is True and outcome["performed"] is False
        assert any(event["kind"] == "instruction_required" for event in events)
        assert op.done and not op._ownership.locked()
    finally:
        op.close()


def test_native_wrong_condition_cannot_be_saved_as_requested():
    with pytest.raises(ProtocolError, match="condition"):
        meter("wrong_condition", "chart").measure("reflective_spot_m2")
    measured = meter(kind="chart").measure("reflective_spot_m2")
    assert measured.patches[0].mcond == 3


def test_discovery_reaches_real_description_with_exact_effect_acceptance():
    session = worker()
    path = session.with_name("description-stub-worker" + session.suffix)
    authenticate_worker(path)
    client = NativeDescriptionClient.for_offline_tests(path, "i1d3_flash")
    # Ordinal and policy come from the actual native enumeration, not a guessed value.
    native = json.loads(client.list_candidates_json())["data"]["candidates"][0]
    catalog = json.dumps({"instrument_types": [{"enum": "instI1Disp3",
        "native_type": native["dtype"], "source_label": "i1 DisplayPro",
        "initialization_assessed": native["initialization_assessed"],
        "initialization_refusal": native["initialization_refusal"]}]})
    build = InstrumentBuild(client, worker(),
                expected_helper_sha256=hashlib.sha256(
                    Path(worker()).read_bytes()).hexdigest())
    found = build.attached(catalog_json=catalog)[0]
    with pytest.raises(ValueError, match="discloses"):
        build.describe(found)
    described = build.describe(found, accept_disclosed_initialization=True)
    assert described.serial == "SUBSTITUTED-DISPLAY-001"
    assert described.provenance == "simulated"
    # The substituted client links no upstream catalog, so bind is given the one
    # this test enumerated with, as attached() was.
    assert build.bind(described, catalog_json=catalog)._meter.expected_path == found.path
    denied = json.loads(client.describe_json(json.dumps(native),
        accepted_initialization_reason="initialization_resets_device"))
    assert denied["error"]["stage"] == "initialization_refused"
    assert denied["events"][-1]["active_access_attempted"] is False
    with pytest.raises(ValueError, match="Firmware"):
        client.describe_json(json.dumps(native),
                             accepted_initialization_reason="initialization_downloads_pld")


def test_reference_and_read_share_one_native_lifetime():
    device = meter("volatile_reference", "chart")
    with pytest.raises(InstrumentError) as failed:
        device.measure("transmission_spot")
    assert failed.value.stage == "read_sample"
    # A separate calibration cannot prime a new driver instance.
    device.calibrate("transmission_spot")
    with pytest.raises(InstrumentError) as again:
        device.measure("transmission_spot")
    assert again.value.stage == "read_sample"
    prompts = []
    result = device.measure("transmission_spot", calibrate=True,
                           acknowledge=lambda prompt: prompts.append(prompt) or "acknowledge")
    assert len(result.patches) == 1
    assert [p["instruction"] for p in prompts] == ["calibration_setup", "sample_position"]
    assert result.calibration["remaining_calibrations"] == 0


def late_calibration_meter(fixture="late_calibration", persona="dtp41"):
    instrument = {"dtp20": "instDTP20", "dtp41": "instDTP41", "dtp51": "instDTP51",
                  "spectroscan": "instSpectroScan"}[persona]
    return Instrument(worker("chart"), instrument, environment={
        **os.environ, "CM_CHART_PERSONA": persona, "CM_CHART_FIXTURE": fixture},
               expected_helper_sha256=hashlib.sha256(
                   Path(worker("chart")).read_bytes()).hexdigest())


@pytest.mark.parametrize("persona,strip", [("dtp20", False), ("dtp41", False),
                                          ("dtp41", True), ("dtp51", True)])
def test_read_discovered_calibration_retries_in_the_same_lifetime(persona, strip):
    prompts = []
    device = late_calibration_meter(persona=persona)
    options = {"calibrate": True, "spectral": persona != "dtp51",
               "acknowledge": lambda prompt: prompts.append(prompt["instruction"]) or "acknowledge"}
    result = device.measure_strip("reflective_strip", patch_count=4, pass_name="B", guide=15,
        patch_width_mm=6, gap_width_mm=1.5, trailer_width_mm=3, **options) if strip else (
        device.measure("reflective_spot", **options))
    placement = "strip_position" if strip else "sample_position"
    assert prompts == ([placement] if strip else []) + ["calibration_setup", placement]
    assert len(result.patches) == (4 if strip else 1)
    assert result.calibration["performed"] is True
    outcomes = [e for e in result.events if e["event"] == "calibration_ready"]
    assert [e["performed"] for e in outcomes] == [False, True]
    retry = [e for e in result.events if e["event"] == "calibration_retry"]
    assert len(retry) == 1 and retry[0]["native_code"] == 0x110007


@pytest.mark.parametrize("fixture,calibrate,stage,retries", [
    ("late_calibration", False, "read_sample", 0),
    ("repeated_calibration", True, "read_sample", 1),
    ("late_calibration_failure", True, "calibrate", 1),
])
def test_read_calibration_recovery_is_opted_in_and_bounded(fixture, calibrate, stage, retries):
    with pytest.raises(InstrumentError) as failed:
        late_calibration_meter(fixture).measure("reflective_spot", calibrate=calibrate,
                                               acknowledge=lambda _: "acknowledge")
    assert failed.value.stage == stage
    assert len([e for e in failed.value.events if e["event"] == "calibration_retry"]) == retries
    assert not any(e["event"] == "result" for e in failed.value.events)


@pytest.mark.parametrize("fixture,prompts", [
    ("late_calibration_failure", []),
    ("repeated_calibration", ["calibration_setup", "sample_position"]),
])
def test_failed_recalibration_does_not_retain_the_previous_ready_claim(fixture, prompts):
    result, actual = finish(operation(late_calibration_meter(fixture),
                                       mode="reflective_spot"))
    assert result["status"] == "failed" and result["patches"] == []
    assert actual == prompts
    assert result["calibration"]["status"] == "required"
    assert json.loads(dict(result["configuration"])["calibration_outcome_json"]) is None


def test_recovered_frontend_retains_both_assessments_and_the_latest_outcome():
    result, prompts = finish(operation(late_calibration_meter(), mode="reflective_spot"))
    assert result["status"] == "completed" and len(result["patches"]) == 1
    assert prompts == ["calibration_setup", "sample_position"]
    configuration = dict(result["configuration"])
    assert json.loads(configuration["calibration_outcome_json"])["performed"] is True
    events = json.loads(configuration["calibration_events_json"])
    assert [e["event"] for e in events] == ["calibration_ready", "calibration_retry",
                                          "instruction_required", "calibration_ready"]


def test_calibration_recovery_can_be_cancelled_before_a_second_read():
    prompts = []
    def respond(prompt):
        prompts.append(prompt["instruction"])
        return "cancel" if prompt["instruction"] == "calibration_setup" else "acknowledge"
    with pytest.raises(InstrumentError) as failed:
        late_calibration_meter().measure("reflective_spot", calibrate=True, acknowledge=respond)
    assert failed.value.stage == "cancelled"
    assert prompts == ["calibration_setup"]
    assert not any(e["event"] == "result" for e in failed.value.events)


@pytest.mark.parametrize("persona,mode,positioned", [("dtp20", "saved_spot", False),
                                                    ("spectroscan", "reflective_spot", True)])
def test_read_calibration_recovery_does_not_replay_stored_or_positioned_work(
    persona, mode, positioned,
):
    with pytest.raises(InstrumentError) as failed:
        late_calibration_meter(persona=persona).measure(
            mode, positioned=positioned, calibrate=True, acknowledge=lambda _: "acknowledge")
    assert failed.value.stage == "read_sample"
    assert not any(e["event"] == "calibration_retry" for e in failed.value.events)


@pytest.mark.parametrize("change", [{"native_code": 0}, {"retry": True},
                                     {"event": "instrument_event"}])
def test_recovery_requires_a_valid_native_transition(monkeypatch, change):
    from color_measurement.native_protocol import HelperSession
    receive = HelperSession.receive_message
    def altered(session, *args, **kwargs):
        message = receive(session, *args, **kwargs)
        return {**message, **change} if message["event"] == "calibration_retry" else message
    monkeypatch.setattr(HelperSession, "receive_message", altered)
    with pytest.raises(ProtocolError, match="Unexpected calibration"):
        late_calibration_meter().measure("reflective_spot", calibrate=True,
                                         acknowledge=lambda _: "acknowledge")


@pytest.mark.parametrize("encoding", ["bytes", "utf8"])
def test_connected_serial_pin_checks_encoding_as_well_as_text(monkeypatch, encoding):
    from color_measurement.native_protocol import HelperSession
    serial = "í\u00a0\u0080"
    receive = HelperSession.receive_message
    def altered(session, *args, **kwargs):
        message = receive(session, *args, **kwargs)
        return ({**message, "serial": serial, "serial_encoding": encoding}
                if message["event"] == "connected" else message)
    monkeypatch.setattr(HelperSession, "receive_message", altered)
    selected = meter(expected_serial=serial)
    # The substitute opens with its own matching serial. Only the connected
    # observation is changed, so the client pin check is the independent guard.
    argv = selected._argv
    monkeypatch.setattr(Instrument, "_argv", lambda *args, **kwargs:
        ["CM-COLORIMETER-0001" if value == serial else value
         for value in argv(*args[1:], **kwargs)])
    if encoding == "bytes":
        with pytest.raises(ProtocolError, match="Confirmed serial"):
            selected.measure("emissive_spot", spectral=False)
    else:
        reading = selected.measure("emissive_spot", spectral=False)
        assert (reading.serial, reading.serial_encoding) == (serial, "utf8")


@pytest.mark.parametrize("pair", [(None, "utf8"), ("A", None), ("🔬", "bytes"),
                                  ("", "utf8"), ("\ud800", "utf8")])
def test_connected_serial_pair_is_validated_before_reading(monkeypatch, pair):
    from color_measurement.native_protocol import HelperSession
    receive = HelperSession.receive_message
    def altered(session, *args, **kwargs):
        message = receive(session, *args, **kwargs)
        return ({**message, "serial": pair[0], "serial_encoding": pair[1]}
                if message["event"] == "connected" else message)
    monkeypatch.setattr(HelperSession, "receive_message", altered)
    with pytest.raises(ProtocolError, match="serial"):
        meter().measure("emissive_spot", spectral=False)


@pytest.mark.parametrize("fixture,encoding", [("invalid_serial_bytes", "bytes"),
    ("utf8_serial_collision", "utf8"), ("empty_serial", None)])
def test_native_serial_pair_is_retained_by_an_unpinned_frontend(fixture, encoding):
    result, _ = finish(operation(meter(fixture)))
    assert result["status"] == "completed" and result["patches"]
    identities = [patch["observation"]["device"] for patch in result["patches"]]
    expected = None if encoding is None else "í\u00a0\u0080"
    assert all((identity["serial"], identity["serial_encoding"]) == (expected, encoding)
               for identity in identities)


@pytest.mark.parametrize("fixture,status", [("no_calibration_needed", "valid"),
                                             ("default_calibration", "unknown")])
def test_calibration_outcome_is_retained_even_without_a_patch(fixture, status):
    result, _ = finish(operation(meter(fixture), workflow="calibrate"))
    assert result["status"] == "completed" and result["patches"] == []
    assert result["calibration"]["status"] == status
    outcome = json.loads(dict(result["configuration"])["calibration_outcome_json"])
    assert outcome["performed"] is False and outcome["remaining_calibrations"] == 0


def test_cancellation_reaches_the_blocked_native_read():
    # The refresh display (index 1), so a calibration precedes the blocked read:
    # i1d3's default non-refresh display needs none (i1d3.c:3346-3355).
    observed = meter().measure("emissive_spot", spectral=False)
    display_types = next(event["display_types"] for event in observed.events
                         if event["event"] == "connected")
    result, instructions = finish(operation(meter("blocked_read"),
        options={"display_type": display_types["entries"][1]["id"]},
        display_types=display_types),
                                  cancel_after_sample=True)
    assert "calibration_setup" in instructions and "sample_position" in instructions
    assert result["status"] == "cancelled" and result["patches"] == []
    assert result["error_stage"] == "cancelled"
    assert result["software_stopped"] is True
    assert json.loads(dict(result["configuration"])["calibration_outcome_json"])["performed"]
    # The helper answered the cancellation itself and was seen to exit; what the
    # device did remains unknown, and the record says so rather than implying it.
    configuration = dict(result["configuration"])
    assert {key: configuration[key] for key in ("stop_requested_by", "stop_acknowledged_by",
            "worker_stop", "device_completion")} == {
        "stop_requested_by": "client", "stop_acknowledged_by": "helper",
        "worker_stop": "exited", "device_completion": "unknown"}


def test_frontend_timeout_actually_bounds_a_noncooperative_native_read():
    started = time.monotonic()
    result, _ = finish(operation(meter("ignores_cancel"), timeout=0.3))
    assert result["status"] == "failed" and result["error_stage"] == "timeout"
    assert time.monotonic() - started < 3
    assert result["instrument_state"] == "not_applicable"  # substituted operation
    # Nothing answered: the host's deadline ended it, and the helper had to be
    # ended too. A stop that no helper acknowledged must not read as one that did.
    configuration = dict(result["configuration"])
    assert configuration["stop_requested_by"] == "host_deadline"
    assert configuration["stop_acknowledged_by"] == "none"
    assert configuration["worker_stop"] in ("terminated", "killed")
    assert configuration["device_completion"] == "unknown"


def test_signed_native_xyz_survives_the_common_path():
    result, _ = finish(operation(meter("negative_xyz")))
    assert result["status"] == "completed"
    observation = result["patches"][0]["observation"]
    assert observation["backend_reported"]["xyz"]["values"][0] == -2.5
    raw = json.loads(observation["raw_backend_output"])
    assert any(event["event"] == "result" for event in raw["events"])
    assert observation["calibration"]["status"] == "valid"


def test_reverse_strip_preserves_native_positions():
    device = meter(kind="chart")
    layout = dict(patch_count=4, pass_name="B", guide=15, patch_width_mm=6.0,
                  gap_width_mm=1.5, trailer_width_mm=3.0)
    forward = device.measure_strip("reflective_strip", **layout)
    reverse = device.measure_strip("reflective_strip", direction="reverse", **layout)
    assert reverse.patches == forward.patches[::-1]
    assert [patch.index for patch in reverse.patches] == [3, 2, 1, 0]
    assert reverse.strip_direction == "reverse"


def test_klein_ambient_needs_an_effective_ambient_matrix():
    """kleink10.c:1422-1425 types every reading by the matrix it applied, not by
    the mode, so a route the matrix disagrees with is refused before anything is
    measured rather than after a reading nobody may publish."""
    from color_measurement.colorimeter_correction import CorrectionMatrix
    device = meter("klein_ambient")
    with pytest.raises(InstrumentError) as refused:
        device.measure("ambient_spot", spectral=False)
    assert refused.value.stage == "ambient_matrix_required"
    unity = CorrectionMatrix(((5., 0., 0.), (0., 5., 0.), (0., 0., 5.)), "Klein")
    with pytest.raises(InstrumentError) as refused:
        device.measure("ambient_spot", spectral=False,
                       configuration=Configuration(correction=unity))
    assert refused.value.stage == "ambient_matrix_required"
    matrix = CorrectionMatrix(((10., 0., 0.), (0., 10., 0.), (0., 0., 10.)), "Klein")
    with pytest.raises(InstrumentError) as refused:
        device.measure("emissive_spot", spectral=False,
                       configuration=Configuration(correction=matrix))
    assert refused.value.stage == "ambient_matrix_on_emissive_route"
    reading = device.measure("ambient_spot", spectral=False,
                             configuration=Configuration(correction=matrix))
    assert reading.patches[0].mtype == 2 and reading.correction == "matrix"
    binding = next(e for e in reading.events if e["event"] == "connected")
    assert binding["ambient_ccmx"] is True
    # The threshold the gate compares against is the one the binding publishes;
    # a driver that types by route publishes none.
    assert binding["ambient_matrix_threshold"] == 5.0
    plain = meter().measure("emissive_spot", spectral=False)
    assert next(e for e in plain.events
                if e["event"] == "connected")["ambient_matrix_threshold"] is None


def driver_meter(persona, symbol, **kwargs):
    """A row of driver_personas.c, selected by the name inst_enum resolves."""
    return Instrument(worker("chart"), symbol,
                      environment={**os.environ, "CM_CHART_PERSONA": persona}, **kwargs,
               expected_helper_sha256=hashlib.sha256(
                   Path(worker("chart")).read_bytes()).hexdigest())


@pytest.mark.parametrize("scenario,performed,expected", [
    ("not_needed", False, []),
    ("internal", True, []),
    ("message", True, ["calibration_setup"]),
    ("optional_skipped", False, ["calibration_setup", "sample_position"]),
])
def test_initial_sample_confirmation_follows_operator_setup_not_calibration_work(
        scenario, performed, expected):
    """Measure already asserts sample readiness. Only a setup that can change
    placement or the stimulus needs another readiness acknowledgement."""
    prompts = []
    if scenario == "not_needed":
        device = meter("no_calibration_needed")
    elif scenario == "internal":
        device = chart_meter("spectroscan", "instSpectroScan", "startup")
    elif scenario == "message":
        device = chart_meter("i1pro3", "instI1Pro3", "message_calibration")
    else:
        device = meter("skippable_calibration")

    def respond(prompt):
        prompts.append(prompt["instruction"])
        skipped = scenario == "optional_skipped" and prompt["instruction"] == "calibration_setup"
        return "skip" if skipped else "acknowledge"

    result = device.measure("reflective_spot" if scenario == "internal" else "emissive_spot",
                           spectral=scenario in ("internal", "message"),
                           calibrate=True, acknowledge=respond)
    assert len(result.patches) == 1
    assert result.calibration["performed"] is performed
    assert prompts == expected


@pytest.mark.parametrize("persona,symbol", [
    ("huey", "instHuey"), ("hcfr", "instHCFR"),
    ("colorhug", "instColorHug"), ("colorhug2", "instColorHug2")])
def test_personas_preserve_the_upstream_default_calibration_assessment(persona, symbol):
    """These constructors supply no assessment; inst.c backfills its default.

    Zero masks from that default mean unknown readiness. A substitute wrapper
    with identical masks has a different identity and used to make the generic
    client publish a driver-reported valid calibration for all four models.
    """
    result, prompts = finish(operation(driver_meter(persona, symbol)))
    assert result["status"] == "completed" and len(result["patches"]) == 1
    assert prompts == []
    ready = json.loads(dict(result["configuration"])["calibration_outcome_json"])
    assert ready["status_source"] == "upstream_default"
    assert ready["needed_calibrations"] == ready["remaining_calibrations"] == 0
    assert ready["performed"] is False
    assert result["calibration"]["status"] == "unknown"
    assert result["patches"][0]["observation"]["calibration"]["status"] == "unknown"


def test_a_calibration_the_driver_leaves_marked_still_completes():
    """spydX.c:990-1004 returns inst_ok with its black offset still set in *calt.
    inst.h:1032-1033 makes inst_ok the end of the negotiation; what remains is what
    get_n_a_cals then reports. Reading the residue instead failed every SpyderX
    start as calibration_incomplete."""
    from color_measurement.instrument import calibration_context

    prompts = []
    reading = driver_meter("spyderx", "instSpyderX").measure(
        "emissive_spot", spectral=False, calibrate=True, acknowledge=collected(prompts))
    assert [p["instruction"] for p in prompts] == ["calibration_setup", "sample_position"]
    assert reading.calibration["performed"] is True
    assert reading.calibration["remaining_calibrations"] == 0
    assert calibration_context(reading.calibration).status == "valid"


@pytest.mark.parametrize("persona,symbol,bitmap", [
    ("spyderx", "instSpyderX", 0x0e), ("spyderx2", "instSpyderX2", 0x1e),
    ("spyder2024", "instSpyder2024", 0x7e)])
def test_spyder_catalogue_refresh_flags_do_not_invent_acquisition_refresh(persona, symbol, bitmap):
    from color_measurement.display_types import DisplayTypeSelection
    from color_measurement.instrument import Configuration

    meter = driver_meter(persona, symbol)
    reading = meter.measure("emissive_spot", spectral=False, calibrate=True,
                            acknowledge=lambda _: "acknowledge")
    catalogue = next(e["display_types"] for e in reading.events if e["event"] == "connected")
    assert sum(entry["refresh_mode"] << i
               for i, entry in enumerate(catalogue["entries"])) == bitmap
    assert catalogue["readback"]["refresh_mode"] == 0
    selected = next(entry for entry in catalogue["entries"] if entry["refresh_mode"] == 1)
    choice = DisplayTypeSelection.from_catalogue(catalogue, selected["id"])
    changed = meter.measure("emissive_spot", spectral=False, calibrate=True,
                            acknowledge=lambda _: "acknowledge",
                            configuration=Configuration(display_type=choice))
    after = next(e["display_types"] for e in changed.events if e["event"] == "connected")
    assert after["applied_choice_id"] == selected["id"]
    assert after["readback"]["refresh_mode"] == 0


def dtp92_meter(fixture, persona="dtp92", symbol="instDTP92"):
    """A DTP92-family row following the calibration-repaired build."""
    return Instrument(worker("chart"), symbol, environment={
        **os.environ, "CM_CHART_PERSONA": persona, "CM_CHART_FIXTURE": fixture},
               expected_helper_sha256=hashlib.sha256(
                   Path(worker("chart")).read_bytes()).hexdigest())


@pytest.mark.parametrize("persona,symbol", [("dtp92", "instDTP92"), ("dtp94", "instDTP94")])
def test_repaired_dtp92_calibration_clears_only_the_read_reported_need(
        persona, symbol):
    """Composition check using the repaired driver's persona. The separate
    source-extracted proof checks the actual upstream masks and need flags."""
    from color_measurement.argyll_capability import CALIBRATION_CONDITIONS, CALIBRATION_TYPES

    result, prompts = finish(operation(dtp92_meter("read_needs_calibration", persona, symbol),
                                       "emissive_spot"))
    assert result["status"] == "completed" and len(result["patches"]) == 1
    assert prompts.count("calibration_setup") == 1
    events = json.loads(dict(result["configuration"])["calibration_events_json"])
    assert [e["event"] for e in events] == ["calibration_ready", "calibration_retry",
                                          "instruction_required", "calibration_ready"]
    assert events[2]["calibration_condition"] == CALIBRATION_CONDITIONS["man_em_dark"]
    ready = events[-1]
    assert ready["needed_calibrations"] == CALIBRATION_TYPES["emis_offset"]
    assert (ready["performed"], ready["remaining_calibrations"]) == (
        True, 0)
    assert result["calibration"]["status"] == "valid"


def chart_meter(persona, symbol, fixture):
    return Instrument(worker("chart"), symbol, environment={
        **os.environ, "CM_CHART_PERSONA": persona, "CM_CHART_FIXTURE": fixture},
               expected_helper_sha256=hashlib.sha256(
                   Path(worker("chart")).read_bytes()).hexdigest())


def setups(prompts):
    return [p["calibration_condition"] for p in prompts if p["instruction"] == "calibration_setup"]


@pytest.mark.parametrize("persona,symbol", [
    ("i1pro3", "instI1Pro3"), ("i1pro2", "instI1Pro2"), ("munki", "instColorMunki"),
    ("i1pro", "instI1Pro"), ("smcube", "instSMCube")])
def test_a_reading_keeps_the_calibration_its_driver_retained(persona, symbol):
    """One helper runs per operation, so every reading is a fresh open, and these
    drivers ask for their whole calibration at every open even when the one they
    restored is valid (i1pro3_imp.c:666-668 with :1083-1093). A reading waives
    that request, as spotread -N does; with nothing valid restored the driver's
    own checks still ask for all of it."""
    from color_measurement.instrument import calibration_context

    spectral = persona != "smcube"   # an XYZ reflectometer (smcube.c:873-899)
    prompts = []
    kept = chart_meter(persona, symbol, "retained_calibration").measure(
        "reflective_spot", spectral=spectral, calibrate=True, acknowledge=collected(prompts))
    assert prompts == []
    assert kept.calibration["initial_calibration_suppressed"] is True
    assert (kept.calibration["needed_calibrations"], kept.calibration["performed"]) == (0, False)
    context = calibration_context(kept.calibration)
    assert context.status == "valid" and context.detail.startswith(
        "No calibration was performed in this operation. The driver was asked to keep")

    prompts = []
    fresh = chart_meter(persona, symbol, "startup" if persona in ("i1pro3", "i1pro2", "munki")
                        else "success").measure(
        "reflective_spot", spectral=spectral, calibrate=True, acknowledge=collected(prompts))
    assert [p["instruction"] for p in prompts] == ["calibration_setup", "sample_position"]
    assert fresh.calibration["initial_calibration_suppressed"] is True
    assert fresh.calibration["performed"] is True
    assert calibration_context(fresh.calibration).detail.startswith("Driver reports no")


def test_a_refused_waiver_is_recorded_and_leaves_the_drivers_request():
    """No pinned handler refuses the option at losecs 0, but one that did would
    leave the driver asking for everything -- and the outcome must not say the
    request was waived."""
    prompts = []
    reading = chart_meter("i1pro3", "instI1Pro3", "initial_option_refused").measure(
        "reflective_spot", calibrate=True, acknowledge=collected(prompts))
    assert [p["instruction"] for p in prompts] == ["calibration_setup", "sample_position"]
    assert reading.calibration["initial_calibration_suppressed"] is False
    assert reading.calibration["performed"] is True


@pytest.mark.parametrize("persona,symbol", [
    ("spyderx2", "instSpyderX2"), ("spyder2024", "instSpyder2024")])
def test_a_driver_that_never_reads_the_waiver_is_not_asked(persona, symbol):
    """spydX2.c accepts the option but reads the flag only under ENABLE_BLACK_CAL,
    which it leaves undefined (:68, :1001-1004). Asking would waive nothing, and
    the record would then claim a waiver. Its stub row accepts the option as the
    driver does, so asking it would show here."""
    reading = driver_meter(persona, symbol).measure(
        "emissive_spot", spectral=False, calibrate=True, acknowledge=lambda _: "acknowledge")
    assert reading.calibration["initial_calibration_suppressed"] is False
    assert reading.calibration["performed"] is False


def test_a_calibration_on_its_own_is_a_fresh_one():
    """The operator asking to calibrate is asking for a new calibration, so the
    driver's at-open request stands even with a valid one restored."""
    prompts = []
    done = chart_meter("i1pro3", "instI1Pro3", "retained_calibration").calibrate(
        "reflective_spot", acknowledge=collected(prompts))
    assert [p["instruction"] for p in prompts] == ["calibration_setup"]
    assert done.calibration["initial_calibration_suppressed"] is False
    assert done.calibration["performed"] is True


@pytest.mark.parametrize("persona,symbol,mode,condition", [
    # No file holds the integration time: only calibrate() sets done_dintsel
    # (i1pro3_imp.c:1108, :1478).
    ("i1pro3", "instI1Pro3", "emissive_spot_nonadaptive", "emis_white"),
    # A transmission reference belongs to its optical setup (session.c).
    ("i1pro3", "instI1Pro3", "transmission_spot", "man_trans_white"),
    ("i1pro", "instI1Pro", "transmission_spot", "man_trans_white"),
])
def test_what_a_retained_calibration_does_not_hold_is_still_asked(persona, symbol, mode,
                                                                    condition):
    from color_measurement.argyll_capability import CALIBRATION_CONDITIONS

    prompts = []
    reading = chart_meter(persona, symbol, "retained_calibration").measure(
        mode, calibrate=True, acknowledge=collected(prompts))
    assert setups(prompts) == [CALIBRATION_CONDITIONS[condition]]
    assert reading.calibration["initial_calibration_suppressed"] is True
    assert reading.calibration["performed"] is True


@pytest.mark.parametrize("persona,symbol", [
    ("spectroscan", "instSpectroScan"), ("spectrolino", "instSpectrolino")])
def test_a_driver_whose_option_masks_its_need_is_not_asked_to_keep_one(persona, symbol):
    """ss.c accepts the option too, but its flag masks the white need itself, with
    no validity test (ss.c:1333-1349), and ss.c keeps no calibration file: waived,
    the white would never be measured. Both SpectroScan stubs model that masking,
    so asking them would drop the calibration asserted here."""
    reading = chart_meter(persona, symbol, "retained_calibration").measure(
        "reflective_spot", calibrate=True, acknowledge=lambda _: "acknowledge")
    assert reading.calibration["initial_calibration_suppressed"] is False
    assert reading.calibration["performed"] is True


def test_a_read_that_needs_calibration_again_after_it_is_refused_within_the_bound():
    """Success of the calibration is not success of the read: a read that
    reports the need again after it ends the operation, with no measurement."""
    with pytest.raises(InstrumentError) as failed:
        dtp92_meter("read_needs_calibration_again").measure(
            "emissive_spot", spectral=False, calibrate=True, acknowledge=lambda _: "acknowledge")
    assert failed.value.stage == "read_sample"
    assert [e["event"] for e in failed.value.events
            if e["event"] in ("calibration_retry", "calibration_ready")] == [
        "calibration_ready", "calibration_retry", "calibration_ready"]
    assert not any(e["event"] == "result" for e in failed.value.events)


def test_a_reading_whose_driver_states_no_type_is_published_under_its_route():
    """huey.c:1246 leaves every emissive reading inst_mrt_none."""
    result, _ = finish(operation(driver_meter("huey", "instHuey"), "emissive_spot"))
    assert result["status"] == "completed"
    observation = result["patches"][0]["observation"]
    facts = dict(observation["configuration"])
    assert facts["native_mtype"] == "0"
    assert facts["units_source"].startswith("requested mode")
    assert observation["backend_reported"]["xyz"]["unit"] == "cd/m^2"


def test_direct_acquisition_does_not_authorize_firmware_download():
    with pytest.raises(InstrumentError) as failed:
        meter("firmware_initialization").measure("emissive_spot", spectral=False)
    assert failed.value.stage == "initialization_downloads_pld"


def test_optional_spectrum_is_retained_if_instrument_provides_it():
    reading = meter(kind="chart").measure("reflective_spot", spectral=False)
    assert reading.spectral and reading.patches[0].spectrum is not None


def test_reverse_strip_frontend_keeps_ids_and_matching_raw_native_rows():
    device = meter(kind="chart")
    binding = next(e for e in device.measure("reflective_spot").events
                   if e["event"] == "connected")
    front = InstrumentFrontend(device, mode_capabilities=binding["mode_capabilities"],
        trigger_capabilities=binding["trigger_capabilities"],
        filter_capabilities=binding["filter_capabilities"], provenance="simulated",
        strip_policies=binding["strip_policies"])
    snapshot = json.loads(front.describe_json())
    op = front.prepare(json.dumps({"schema_version": 1, "device_id": snapshot["device_id"],
        "capability_id": snapshot["capability_id"], "mode": "reflective_strip",
        "workflow": "strip", "patch_ids": ["A", "B", "C", "D"], "options": {},
        "layout": {"direction": "reverse"}}))
    op.dispatch()
    result, _ = finish(op)
    assert result["status"] == "completed"
    for patch, native_index in zip(result["patches"], [3, 2, 1, 0], strict=True):
        observation = patch["observation"]
        assert dict(observation["configuration"])["native_patch_index"] == str(native_index)
        raw = json.loads(observation["raw_backend_output"])
        assert [e["index"] for e in raw["events"] if e["event"] == "strip_patch"] == [native_index]


def test_default_notifications_cannot_overflow_on_a_complete_native_spectrum(monkeypatch):
    from color_measurement.argyll_capability import MODE_FLAGS
    from color_measurement.instrument import Measurement, Patch
    from color_measurement.native_protocol import NativeSpectrum
    patch = Patch(1, 0, (1., 2., 3.), NativeSpectrum(300., 900., 1.,
        tuple(0.123456789012345 + index for index in range(601))), 0.)
    measured = Measurement("X-Rite i1 Pro 3", None, "emissive_spot", "spot", "simulated",
        True, ("emis_spot",), patches=(patch,),
        events=({"event": "result", "index": 0, "raw_fixture": "x" * 12000},))
    monkeypatch.setattr(Instrument, "measure", lambda *args, **kwargs: measured)
    front = InstrumentFrontend(Instrument("/never-run", "instI1Pro3"),
        mode_capabilities=mode_word("emissive_spot") | MODE_FLAGS["spectral"],
        trigger_capabilities=1, filter_capabilities=0, provenance="simulated")
    snapshot = json.loads(front.describe_json())
    assert snapshot["default_event_contract"] == "retained_patch_v1"
    op = front.prepare(json.dumps({"schema_version": 1, "device_id": snapshot["device_id"],
        "capability_id": snapshot["capability_id"], "mode": "emissive_spot", "workflow": "spot",
        "patch_ids": ["P1"], "options": {}, "layout": {}}))
    op.dispatch()
    result, _ = finish(op)
    assert result["status"] == "completed"
    assert len(result["patches"][0]["observation"]["backend_reported"]["spectrum"]["values"]) == 601
    assert dict(result["configuration"])["event_contract"] == "retained_patch_v1"


def test_fixed_optical_filter_uses_shared_readback_without_a_working_setter():
    env = dict(os.environ, CM_CHART_PERSONA="i1pro2", CM_CHART_FIXTURE="success")
    device = Instrument(worker("chart"), "instI1Pro2", environment=env,
                 expected_helper_sha256=hashlib.sha256(
                     Path(worker("chart")).read_bytes()).hexdigest())
    assert device.measure("reflective_spot_m2").patches[0].mcond == 3
    with pytest.raises(InstrumentError) as failed:
        device.measure("reflective_spot_m0")
    assert failed.value.stage == "set_filter"


def table_meter(fixture):
    return Instrument(worker("chart"), "instSpectroScan",
        environment=dict(os.environ, CM_CHART_PERSONA="spectroscan", CM_CHART_FIXTURE=fixture),
               expected_helper_sha256=hashlib.sha256(
                   Path(worker("chart")).read_bytes()).hexdigest())


@pytest.mark.parametrize("workflow", ["spot", "calibrate"])
def test_filter_change_can_complete_during_calibration(workflow):
    device = table_meter("filter_after_calibration")
    prompts = []
    options = {"acknowledge": lambda prompt: prompts.append(prompt) or "acknowledge"}
    if workflow == "spot":
        measured = device.measure("reflective_spot_m2", calibrate=True, **options)
        assert measured.patches[0].mcond == 3
    else:
        measured = device.calibrate("reflective_spot_m2", **options)
        assert not measured.patches
    # inst_calc_change_filter, from inst.h.
    assert any(p.get("calibration_condition") == 0x10000 for p in prompts)
    assert measured.calibration["performed"]


@pytest.mark.parametrize("fixture,calibrate,code", [
    ("filter_after_calibration", False, 0x130000),  # inst_wrong_config
    ("filter_still_wrong", True, 0x130000),
    ("filter_readback_failure", True, 0x7000B),    # inst_coms_fail plus original subcode
])
def test_filter_readback_failure_prevents_acquisition(fixture, calibrate, code):
    with pytest.raises(InstrumentError) as failed:
        table_meter(fixture).measure("reflective_spot_m2", calibrate=calibrate,
                                     acknowledge=lambda _: "acknowledge")
    assert failed.value.stage == "filter_readback"
    assert failed.value.native_code == code
    assert not any(e["event"] == "result" for e in failed.value.events)


def test_fixed_filter_readback_preserves_communication_failure():
    device = Instrument(worker("chart"), "instI1Pro2", environment=dict(
        os.environ, CM_CHART_PERSONA="i1pro2", CM_CHART_FIXTURE="fixed_filter_readback_failure"),
                 expected_helper_sha256=hashlib.sha256(
                     Path(worker("chart")).read_bytes()).hexdigest())
    with pytest.raises(InstrumentError) as failed:
        device.measure("reflective_spot_m2")
    assert failed.value.stage == "filter_readback"
    assert failed.value.native_code == 0x7000B


def test_filter_is_rechecked_after_read_discovered_calibration():
    device = Instrument(worker("chart"), "instI1Pro2", environment=dict(
        os.environ, CM_CHART_PERSONA="i1pro2", CM_CHART_FIXTURE="filter_changes_after_retry"),
                 expected_helper_sha256=hashlib.sha256(
                     Path(worker("chart")).read_bytes()).hexdigest())
    with pytest.raises(InstrumentError) as failed:
        device.measure("reflective_spot_m2", calibrate=True,
                       acknowledge=lambda _: "acknowledge")
    assert failed.value.stage == "filter_readback"
    assert failed.value.native_code == 0x130000
    assert any(e["event"] == "calibration_retry" for e in failed.value.events)
    assert not any(e["event"] == "result" for e in failed.value.events)


def table_operation(device, workflow):
    from color_measurement.argyll_capability import CAPABILITY2
    mode = "reflective_spot" if workflow == "spot" else "reflective_xy"
    front = InstrumentFrontend(device, mode_capabilities=mode_word(mode),
        trigger_capabilities=CAPABILITY2["prog_trig"] | CAPABILITY2["xy_locate"] |
                             CAPABILITY2["xy_position"] | CAPABILITY2["xy_holdrel"],
        filter_capabilities=0, provenance="simulated")
    snapshot = json.loads(front.describe_json())
    layout = {} if workflow == "spot" else dict(passes=2, steps=3)
    if workflow == "xy":
        device.environment["CM_CHART_XY_VECTORS"] = "10,20,2,4,0,0,0.5,1"
    prepared_operation = front.prepare(json.dumps({
        "schema_version": 1, "device_id": snapshot["device_id"],
        "capability_id": snapshot["capability_id"], "mode": mode, "workflow": workflow,
        "patch_ids": [f"P{i}" for i in range(1 if workflow == "spot" else 6)],
        "options": {}, "layout": layout}))
    prepared_operation.dispatch()
    return prepared_operation


def test_successful_user_trigger_spot_is_a_measurement():
    reading = meter("spot_user_trigger", "chart").measure("reflective_spot")
    patch = reading.patches[0]
    assert patch.spectrum is not None and patch.xyz is not None
    assert all(0 < value <= 100 for value in patch.xyz), "not a reflective reading"


@pytest.mark.parametrize("workflow,fixture,stage,count", [
    ("spot", "xy_restore_failure", "xy_restore", 1),
    ("xy", "xy_release_failure", "xy_release", 6),
])
def test_cleanup_failure_retains_completed_native_measurement(workflow, fixture, stage, count):
    device = table_meter(fixture)
    with pytest.raises(InstrumentError) as failed:
        if workflow == "spot":
            device.measure("reflective_spot", positioned=True)
        else:
            device.measure_sheet("reflective_xy", passes=2, steps=3,
                geometry=(1., 2., 3., 4., 5., 6., 7., 8.))
    assert failed.value.stage == stage
    assert len(failed.value.measurement.patches) == count
    result, _ = finish(table_operation(device, workflow))
    assert result["status"] == "failed" and result["error_stage"] == stage
    assert len(result["patches"]) == count
    published = []
    for patch in result["patches"]:
        obs = patch["observation"]
        raw = json.loads(obs["raw_backend_output"])
        # Carried unchanged: the reading the worker emitted for this patch.
        emitted = next(e for e in raw["events"] if "backend_reported" in e)["backend_reported"]
        assert obs["backend_reported"]["xyz"]["values"] == emitted["XYZ"]
        assert any(e.get("stage") == stage for e in raw["events"])
        if workflow == "xy":
            # ss.c:829: an X-Y patch states no type, and its route gives the units.
            facts = dict(obs["configuration"])
            assert facts["native_mtype"] == "0"
            assert facts["units_source"].startswith("requested mode")
            assert obs["backend_reported"]["xyz"]["unit"] == "1"
        published.append(tuple(emitted["XYZ"]))
    assert len(set(published)) == count, "a batch repeated one reading"


@pytest.mark.parametrize("workflow,fixture", [("spot", "read_failure"), ("xy", "read_xy_failure")])
def test_failed_read_does_not_retain_driver_partial_data(workflow, fixture):
    result, _ = finish(table_operation(table_meter(fixture), workflow))
    assert result["status"] == "failed" and result["patches"] == []


def test_notification_failure_retains_the_entire_read_batch(monkeypatch):
    from color_measurement.frontend import _PolledOperation
    def fail_notification(self, data):
        raise ValueError("notification unavailable")
    monkeypatch.setattr(_PolledOperation, "_notify_patch", fail_notification)
    result, _ = finish(table_operation(table_meter("success"), "xy"))
    assert result["status"] == "failed" and "notification unavailable" in result["error"]
    assert len(result["patches"]) == 6
    assert [p["patch_id"] for p in result["patches"]] == [f"P{i}" for i in range(6)]


@pytest.mark.parametrize("fixture,stored", [("nothing_stored", 0), ("chart_only_stored", 8)])
def test_saved_spot_checks_its_own_status_before_reading(fixture, stored):
    device = Instrument(worker("chart"), "instDTP20", environment=dict(os.environ,
        CM_CHART_PERSONA="dtp20", CM_CHART_FIXTURE=fixture),
                 expected_helper_sha256=hashlib.sha256(
                     Path(worker("chart")).read_bytes()).hexdigest())
    with pytest.raises(InstrumentError) as failed:
        device.measure("saved_spot")
    assert failed.value.stage == "no_saved_readings"
    assert not any(e["event"] == "result" for e in failed.value.events)
    assert next(e["stored"] for e in failed.value.events
                if e["event"] == "saved_readings") == stored


def test_native_chart_boundary_refuses_overflowing_geometry():
    device = Instrument(worker("chart"), "instDTP20", environment=dict(os.environ,
        CM_CHART_PERSONA="dtp20", CM_CHART_FIXTURE="success"),
                 expected_helper_sha256=hashlib.sha256(
                     Path(worker("chart")).read_bytes()).hexdigest())
    # Bypass Python validation deliberately to exercise the C boundary as well.
    with pytest.raises(InstrumentError) as failed:
        device._run("saved_chart", True, (), None, read_chart=1,
            chart_passes=2147483647, chart_steps=2147483647, chart_sheets="2147483647")
    assert failed.value.stage == "chart_geometry"
    assert not any(e["event"] == "chart_patch" for e in failed.value.events)


@pytest.mark.parametrize("invalid", [dict(passes=2**31-1, steps=2**31-1, sheets=(2**31-1,)),
    dict(passes=1.0), dict(patch_count=True), dict(steps=False), dict(sheets=(True,)),
    dict(passes=65, sheets=(1,)*65), dict(chart_id=-1),
    dict(patch_count=4096, passes=2, steps=2049, sheets=(2,))])
def test_invalid_chart_geometry_is_rejected_before_spawning(invalid, monkeypatch):
    monkeypatch.setattr(Instrument, "_run",
                        lambda *args, **kwargs: pytest.fail("Invalid chart reached session launch"))
    layout = dict(patch_count=1, passes=1, steps=1, sheets=(1,), chart_id=0) | invalid
    with pytest.raises(ValueError):
        Instrument("/must-not-run", "instDTP20").measure_chart("saved_chart", **layout)


@pytest.mark.parametrize("fixture,retained", [
    ("xy_restore_failure", True), ("read_failure", False)])
def test_cli_reports_failure_and_saves_any_completed_read(monkeypatch, capsys, tmp_path,
                                                          fixture, retained):
    import io

    from color_measurement.cli import main
    monkeypatch.setenv("CM_CHART_PERSONA", "spectroscan")
    monkeypatch.setenv("CM_CHART_FIXTURE", fixture)
    monkeypatch.setattr("sys.stdin", io.StringIO("accept\n" * 5))
    target = tmp_path / "retained.json"
    report = retained_build_report(tmp_path / "build")
    loader_calls = bind_cli_substitute(monkeypatch, worker("chart"), report)
    code = main(["acquire", "--device", "instSpectroScan", "--mode", "reflective_spot",
                 "--build-report", str(report), "--output", str(target)])
    assert code == 1
    assert loader_calls == [report]
    captured = capsys.readouterr()
    assert "native code" in captured.err and "Traceback" not in captured.err
    assert target.exists() is retained
    if retained:
        obs = json.loads(captured.out)
        assert json.loads(target.read_text()) == obs
        assert any("xy_restore" in warning for warning in obs["warnings"])
        raw = json.loads(obs["raw_backend_output"])
        emitted = next(e for e in raw["events"] if "backend_reported" in e)["backend_reported"]
        assert obs["backend_reported"]["xyz"]["values"] == emitted["XYZ"]
    else:
        assert captured.out == ""


# --- CR-250: the REAL driver over the substituted worker's scripted link --------


def cr250(fixture, **kwargs):
    """``cr250_persona.c``: production ``cr250.c`` and ``cr250_imp.c`` with only the
    transport substituted. It exists only in a worker built with the CR-250 series
    composed, which the worker's own receipt records; a stock worker has none."""
    path = worker()
    report = json.loads((path.parent / "report.json").read_text())
    if "instCR250" not in report.get("contribution", {}).get("instrument_types_added", ()):
        pytest.skip("CR-250 persona exists only in a worker that composed the series")
    # The client's timeout reaches the driver as its allowance, and the real driver
    # refuses one read with less than its budget plus the reads after M. The 30 s
    # default would be refused, correctly; tests that are not about the allowance
    # give a CR instrument the time it genuinely needs.
    kwargs.setdefault("timeout", 300)
    return Instrument(path, "instCR100" if fixture.startswith("cr100") else "instCR250",
                      environment=dict(os.environ, CM_COLORIMETER_FIXTURE=fixture), **kwargs,
               expected_helper_sha256=hashlib.sha256(
                   Path(path).read_bytes()).hexdigest())


def test_cr250_driver_facts_reach_the_saved_observation():
    """The failure this catches: a native fact dropped, reshaped or re-derived
    between the driver's own struct and the record a client saves."""
    measurement = cr250("cr250").measure("emissive_spot", spectral=False)
    facts = measurement.native_facts
    assert facts["driver"] == "cr250" and facts["identity"] == {
        "verified": True, "model": "CR-250", "serial": "SYNTHETIC-250",
        "firmware": "1.34.0-S28", "firmware_components": [1, 34, 0], "instrument_type": 2}
    assert len(facts["replies"]) == facts["capture"]["reply_count"] > 0
    assert [r["context"] for r in facts["replies"]].count("M") == 1
    assert facts["capture"]["xyz"] == list(measurement.patches[0].xyz) == [12.5, 25, 6.25]
    assert facts["factory_calibration"] == "unknown"

    result, _ = finish(operation(cr250("cr250"), timeout=300))
    assert result["status"] == "completed"
    configuration = dict(result["patches"][0]["observation"]["configuration"])
    assert json.loads(configuration["native_facts_json"])["identity"]["serial"] == "SYNTHETIC-250"
    assert result_from_json(json.dumps(result)).status == "completed"


def test_cr250_warning_publishes_no_reading_but_keeps_its_evidence():
    """A warning-bearing capture must not become a reading, and its replies are
    the evidence of why -- so they survive onto the failed result, with the
    driver's own words, and nothing parses those words for facts."""
    with pytest.raises(InstrumentError) as refused:
        cr250("cr250_warning").measure("emissive_spot", spectral=False)
    error = refused.value
    assert error.stage == "read_sample" and error.measurement is None
    assert "last capture reports warnings" in error.native_text
    assert error.native_text_truncated is False
    assert error.native_facts["capture"]["valid"] is False
    warning = next(r for r in error.native_facts["replies"] if r["context"] == "RM Warnings")
    assert warning["value"] == "3"

    result, _ = finish(operation(cr250("cr250_warning"), timeout=300))
    assert result["status"] == "failed" and result["patches"] == []
    assert result["error_stage"] == "read_sample"
    configuration = dict(result["configuration"])
    assert "last capture reports warnings" in configuration["error_native_text"]
    assert configuration["error_native_text_truncated"] == "false"
    assert json.loads(configuration["native_facts_json"])["capture"]["valid"] is False
    assert result_from_json(json.dumps(result)).error_stage == "read_sample"


@pytest.mark.parametrize("fixture,stage,text", [
    ("cr250_firmware", "init_inst", "requires firmware 1.26"),
    ("cr250_model", "init_coms", "not the expected model"),
])
def test_cr250_identity_refusals_carry_the_driver_text(fixture, stage, text):
    """An instrument that is not the registered model, or too old for the
    commands the row issues, is refused before any measurement, and an identity the
    driver did not verify is never published as one."""
    with pytest.raises(InstrumentError) as refused:
        cr250(fixture).measure("emissive_spot", spectral=False)
    assert refused.value.stage == stage and text in refused.value.native_text
    identity = refused.value.native_facts["identity"]
    assert identity["verified"] is (fixture == "cr250_firmware")
    if fixture == "cr250_model":
        assert identity["model"] is None
        assert refused.value.native_facts["last_reply"]["value"] == "CR-300"


def test_cr250_budget_that_will_not_fit_is_refused_before_measuring():
    """The client's own timeout reaches the driver as an allowance. A unit whose
    60 s exposure ceiling makes a 185 s budget, under a 150 s session, must be
    refused BEFORE M with its budget kept -- not measured and then cut off by the
    host mid-exposure, which is what happened before the allowance existed."""
    with pytest.raises(InstrumentError) as refused:
        cr250("cr250_long_exposure", timeout=150).measure("emissive_spot", spectral=False)
    error = refused.value
    assert error.stage == "read_sample" and error.native_code & 0xFFFF == 0xFF0D
    capture = error.native_facts["capture"]
    assert capture["measurement_budget_s"] == 185 and 0 < capture["host_allowance_s"] <= 150
    assert capture["required_s"] > 185   # the reads after M are reserved too
    assert "M" not in [reply["context"] for reply in error.native_facts["replies"]]
    measurement = cr250("cr250_long_exposure", timeout=400).measure(
        "emissive_spot", spectral=False)
    assert [r["context"] for r in measurement.native_facts["replies"]].count("M") == 1


@pytest.mark.parametrize("spent", [25, 200])
def test_cr250_client_deadline_contract_under_virtual_clock(monkeypatch, spent):
    """Contract stress only; independent clock injection is not a timing reproduction."""
    from types import SimpleNamespace

    import color_measurement.instrument as module

    clock = SimpleNamespace(offset=0.0)
    real_now = time.monotonic
    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: real_now() + clock.offset))
    original = module.HelperSession.receive_message

    def delayed_event(session, **kwargs):
        event = original(session, **kwargs)
        if event.get("event") == "connected":
            # Client time spent before the read: the grant must come from the
            # client's own deadline, whatever the worker's clock says.
            clock.offset = spent
        return event

    monkeypatch.setattr(module.HelperSession, "receive_message", delayed_event)
    with pytest.raises(InstrumentError) as refused:
        cr250("cr250", timeout=200).measure("emissive_spot", spectral=False)
    if spent == 200:
        assert refused.value.stage in ("timeout", "cancelled")
    else:
        capture = refused.value.native_facts["capture"]
        assert refused.value.stage == "read_sample"
        assert 170 < capture["host_allowance_s"] < 176 < capture["required_s"]
        assert "M" not in [r["context"] for r in refused.value.native_facts["replies"]]


@pytest.mark.parametrize("delay_at", ["delivery", "operator"])
def test_only_operator_pause_is_excluded_from_the_client_read_allowance(monkeypatch, delay_at):
    """Exercise the common client with genuine calibration/setup frames and a
    scripted allowance request. CR itself needs no operator setup. Native CR
    allowance acceptance and refusal are checked separately below."""
    from types import SimpleNamespace

    import color_measurement.instrument as module

    device = meter("skippable_calibration", timeout=200)
    baseline = device.measure("emissive_spot", spectral=False, calibrate=True,
                              acknowledge=lambda _: "acknowledge")
    events = [dict(event) for event in baseline.events]
    # The client joins row frames into the connected summary in place. Restore
    # its wire shape before replaying those same frames through a second client.
    connected = next(event for event in events if event["event"] == "connected")
    connected["display_types"] = {
        key: value for key, value in connected["display_types"].items() if key != "entries"}
    position = next(i for i, event in enumerate(events) if event["event"] == "result")
    events.insert(position, {"event": "read_allowance_required", "protocol_version": 1,
                             "provenance": "simulated"})
    grants = []
    clock = SimpleNamespace(offset=0.0)
    real_now = time.monotonic
    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: real_now() + clock.offset))

    class Session:
        stopped = "exited"

        def receive_message(self, **kwargs):
            event = events.pop(0)
            if delay_at == "delivery" and event["event"] == "instruction_required":
                clock.offset += 25
            return event

        def send(self, command, value=""):
            if command == "read_allowance":
                grants.append(float(value))

        def finish_messages(self, *, expected_returncode):
            assert expected_returncode == 0

        def close(self):
            pass

    monkeypatch.setattr(Instrument, "_open_session", lambda *args: Session())

    def acknowledge(prompt):
        if delay_at == "operator":
            clock.offset += 250  # No wall-clock wait and no hardware.
        return "acknowledge"

    measurement = device.measure(
        "emissive_spot", spectral=False, calibrate=True, acknowledge=acknowledge)
    assert len(measurement.patches) == len(grants) == 1
    assert clock.offset == (500 if delay_at == "operator" else 50)
    assert 190 < grants[0] <= 200 if delay_at == "operator" else 140 < grants[0] <= 150


def test_cr250_allowance_delivery_delay_consumes_client_time(monkeypatch):
    """Both processes use real clocks. Delayed delivery consumes the client's
    deadline before its fresh grant; a grant too small must never send M."""
    import color_measurement.instrument as module

    required = cr250("cr250").measure(
        "emissive_spot", spectral=False).native_facts["capture"]["required_s"]
    original = module.HelperSession.receive_message
    observed = {"grants": 0, "delay_s": 0.0}

    def delay():
        began = time.monotonic()
        time.sleep(2.0)
        observed["delay_s"] += time.monotonic() - began

    def receive(session, **kwargs):
        event = original(session, **kwargs)
        if event.get("event") == "read_allowance_required":
            observed["grants"] += 1
            delay()
        return event

    monkeypatch.setattr(module.HelperSession, "receive_message", receive)
    device = cr250("cr250", timeout=required + 1.5)
    with pytest.raises(InstrumentError) as refused:
        device.measure("emissive_spot", spectral=False, calibrate=True,
                       acknowledge=lambda _: pytest.fail("CR invented an operator setup"))
    assert refused.value.stage == "read_sample"
    facts = refused.value.native_facts
    capture = facts["capture"]
    assert 0 < capture["host_allowance_s"] < capture["required_s"]
    assert "M" not in [r["context"] for r in facts["replies"]]
    assert observed["grants"] == 1 and observed["delay_s"] >= 2.0


def test_cr250_spectral_read_publishes_the_instruments_spectrum_in_argylls_unit():
    """Unknown raw units stay separate from inferred density and actual publication.

    Argyll's emission unit is mW/(m^2.sr.nm) (xspect.h:40-42). A consistent
    same-capture check supports density under the driver model, not a
    manufacturer-confirmed native denominator. Original bins are carried.
    """
    measurement = cr250("cr250").measure("emissive_spot", spectral=True)
    facts, patch = measurement.native_facts, measurement.patches[0]
    grid = facts["capture"]["spectrum"]
    assert grid["unit"] is None
    assert grid["inferred_unit"] == "W/(sr.m^2.nm)"
    assert grid["unit_basis"] == "same_capture_consistency_under_driver_model"
    assert grid["published_unit"] == "mW/(m^2.sr.nm)" and grid["published_scale"] == 1000
    original = tuple(grid["values"])
    assert original == tuple(value for event in measurement.events
                             if event["event"] == "native_spectrum" for value in event["values"])
    spectrum = patch.spectrum
    assert len(spectrum.samples) == grid["count"] == 81 and spectrum.norm == 1
    assert (spectrum.start_nm, spectrum.end_nm) == (grid["start_nm"], grid["end_nm"])
    assert facts["capture"]["unit_check"]["verdict"] == "consistent"
    assert facts["capture"]["valid"] is True
    assert spectrum.samples == tuple(value * grid["published_scale"] for value in original)
    watts = sum(spectrum.samples) * grid["step_nm"] / 1000
    assert watts == pytest.approx(facts["capture"]["radiometric"]["radiance_w_sr_m2"], rel=1e-3)
    assert tuple(grid["values"]) == original


def test_cr250_xyz_only_leaves_the_spectrum_and_its_cost_out():
    """XYZ alone from a spectral instrument: bound without the spectral bit, so the
    driver sends no RM Spectrum and needs less time. The failure: an XYZ-only
    request that still takes, or waits for, a spectrum."""
    xyz_only = Configuration(xyz_only=True)
    measurement = cr250("cr250").measure("emissive_spot", spectral=False,
                                         configuration=xyz_only)
    facts = measurement.native_facts
    assert measurement.patches[0].spectrum is None
    assert "RM Spectrum" not in [r["context"] for r in facts["replies"]]
    assert facts["capture"]["unit_check"]["verdict"] == "not_checked"
    spectral = cr250("cr250").measure("emissive_spot", spectral=True).native_facts
    assert facts["capture"]["required_s"] < spectral["capture"]["required_s"]
    with pytest.raises(ValueError, match="xyz_only"):
        cr250("cr250").measure("emissive_spot", configuration=xyz_only)


def test_cr250_driver_writes_are_recorded_and_unconfirmed_ones_refused():
    """The driver sets what the read needs and records each write with both
    values; the instrument keeps it. One the unit did not take refuses before M."""
    facts = cr250("cr250_writes").measure("emissive_spot", spectral=False).native_facts
    assert facts["writes"] == [{"key": "UserCalibMode", "before": "Match", "target": "None",
                                "after": "None", "confirmed": True}]
    assert next(s for s in facts["settings"] if s["key"] == "UserCalibMode")["driver_set"]
    with pytest.raises(InstrumentError) as refused:
        cr250("cr250_setup").measure("emissive_spot", spectral=False)
    facts = refused.value.native_facts
    assert refused.value.stage == "read_sample"
    assert facts["writes"][0]["confirmed"] is False and facts["writes"][0]["after"] == "Match"
    assert "M" not in [r["context"] for r in facts["replies"]]


def test_cr100_is_an_xyz_colorimeter_of_the_same_driver():
    """The CR-100 row: its own identity, no spectrum, and XYZ-only refused as a
    request made against an instrument with no spectrum to leave out."""
    measurement = cr250("cr100").measure("emissive_spot", spectral=False)
    assert measurement.native_facts["identity"]["model"] == "CR-100"
    assert measurement.patches[0].spectrum is None
    assert list(measurement.patches[0].xyz) == [12.5, 25, 6.25]
    with pytest.raises(InstrumentError) as refused:
        cr250("cr100").measure("emissive_spot", spectral=False,
                               configuration=Configuration(xyz_only=True))
    assert refused.value.stage == "xyz_only_unavailable"


def test_xyz_only_is_a_frontend_option_that_reaches_the_driver():
    """A client asks for XYZ alone through the published option, on an instrument
    that offers a spectrum, and the driver then takes none. The published CR-100
    descriptor does not offer it: there is no spectrum to leave out."""
    result, _ = finish(operation(cr250("cr250"), timeout=300, spectral=True,
                                 options={"xyz_only": True}))
    assert result["status"] == "completed"
    observation = result["patches"][0]["observation"]
    assert observation["backend_reported"].get("spectrum") is None
    facts = json.loads(dict(observation["configuration"])["native_facts_json"])
    assert facts["capture"]["unit_check"]["verdict"] == "not_checked"

    published = Path(__file__).parent / "fixtures/generic_frontend"
    offered = {persona: next(o for o in json.loads(
                   (published / persona / "describe.json").read_text())["options"]
                   if o["id"] == "xyz_only")["available"]
               for persona in ("cr250", "cr100")}
    assert offered == {"cr250": True, "cr100": False}


def test_a_refused_spectrum_keeps_the_instruments_own_values():
    """On a unit-check mismatch there is no reading, so the spectrum the instrument
    sent is the only evidence of which factor is wrong. It must survive, in W."""
    with pytest.raises(InstrumentError) as refused:
        cr250("cr250_spectrum_mismatch").measure("emissive_spot", spectral=True)
    capture = refused.value.native_facts["capture"]
    assert capture["unit_check"]["verdict"] == "mismatch"
    # The native XYZ the spectrum was judged against is evidence too: parsed
    # before the refusal, so kept, though no reading was published.
    assert capture["valid"] is False and capture["xyz"] == [12.5, 25, 6.25]
    values = capture["spectrum"]["values"]
    assert len(values) == capture["spectrum"]["count"] == 81
    watts = sum(values) * capture["spectrum"]["step_nm"]
    assert watts / capture["radiometric"]["radiance_w_sr_m2"] == pytest.approx(1000, rel=1e-3)


def test_a_started_read_is_held_to_its_own_modes_floor():
    """bind() can only hold a timeout to the type's least floor; a started read is
    held to the floor of the exact word it binds. 180 s passes the CR-250's 174 s
    type floor but not a spectral spot read's 182 s -- and an XYZ-only one's 177 s
    it does pass. Native-facts readings refuse patch_v1 before any worker runs."""
    floors = {mode_word("emissive_spot", spectral=True): 182.0,
              mode_word("emissive_spot", spectral=False): 177.0}
    device = cr250("cr250", timeout=180)
    front = InstrumentFrontend(device, mode_capabilities=mode_word("emissive_spot"),
                               trigger_capabilities=1, filter_capabilities=0,
                               provenance="simulated", read_allowance_floor=174.0,
                               read_allowance_floors=floors, native_facts=True)
    snapshot = json.loads(front.describe_json())
    assert snapshot["event_contracts"] == ["retained_patch_v1"]
    assert snapshot["limits"]["read_allowance_floor_seconds_by_mode"]["emissive_spot"] == {
        "default": 182.0, "xyz_only": 177.0}

    def request(options=None, contract=None):
        body = {"schema_version": 1, "device_id": snapshot["device_id"],
                "capability_id": snapshot["capability_id"], "mode": "emissive_spot",
                "workflow": "spot", "patch_ids": ["P1"], "options": options or {}, "layout": {}}
        if contract:
            body["event_contract"] = contract
        return json.dumps(body)

    with pytest.raises(ValueError, match="182"):
        front.prepare(request())
    with pytest.raises(ValueError, match="retained_patch_v1"):
        front.prepare(request(contract="patch_v1"))
    prepared_operation = front.prepare(request({"xyz_only": True}))
    prepared_operation.dispatch()
    finish(prepared_operation)   # held to 177 s: it starts


def position_meter(persona, position, fixture="success", **kwargs):
    """A substituted instrument whose dial, adapter or diffuser arm starts where
    CM_SENSOR_POSITION says (chart_stub.c, colorimeter_stub.c)."""
    if persona == "i1d3":
        return Instrument(worker(), "instI1Disp3", environment={
            **os.environ, "CM_COLORIMETER_FIXTURE": fixture, "CM_SENSOR_POSITION": position},
            **kwargs,
                   expected_helper_sha256=hashlib.sha256(
                       Path(worker()).read_bytes()).hexdigest())
    return Instrument(worker("chart"), {"munki": "instColorMunki", "i1pro3": "instI1Pro3"}[persona],
                      environment={**os.environ, "CM_CHART_PERSONA": persona,
                                   "CM_CHART_FIXTURE": fixture, "CM_SENSOR_POSITION": position},
                      **kwargs,
               expected_helper_sha256=hashlib.sha256(
                   Path(worker("chart")).read_bytes()).hexdigest())


def collected(prompts, answer="acknowledge"):
    return lambda prompt: prompts.append(prompt) or answer


@pytest.mark.parametrize("persona,mode,spectral,configuration,text", [
    # munki_imp.c:2191-2206: the dial this mode needs.
    ("munki", "emissive_tele", True, 1, "Sensor should be in projector position"),
    ("munki", "ambient_spot", True, 7, "Sensor should be in ambient position"),
    ("munki", "reflective_spot", True, 2, "Sensor should be in surface position"),
    # i1pro3_imp.c:8221, :9589-9600: the adapter.
    ("i1pro3", "ambient_spot", True, 7, "Ambient measurement adapter should be fitted"),
    ("i1pro3", "reflective_spot", True, 2,
     "Standard surface measurement adapter should be fitted"),
    # i1d3.c:2318-2346: the diffuser arm, over the optics only for ambient.
    ("i1d3", "ambient_spot", False, 7, "Ambient filter should be used"),
    ("i1d3", "emissive_spot", False, 4, "Ambient filter should be removed"),
])
def test_a_wrong_position_is_named_then_read_again_once_acknowledged(
        persona, mode, spectral, configuration, text):
    """spotread.c:2323-2326 goes back to the operator on inst_wrong_config; the
    session used to end the read there. The prompt names the position the
    driver's own config_enum gives, with its own words, and one reading follows."""
    prompts = []
    result = position_meter(persona, "read").measure(mode, spectral=spectral,
                                                     acknowledge=collected(prompts))
    assert len(result.patches) == 1
    assert [(p["instruction"], p["request_id"], p["stage"], p["configuration"],
             p["native_text"], p["native_code"] >> 16) for p in prompts] == [
        ("sensor_position", "position-retry", "read_sample", configuration, text, 0x13)]


def test_a_position_left_wrong_fails_the_read_after_its_one_retry():
    """Bounded like recalibrate_read: a second wrong position is the driver's
    failure, with its words, not another prompt."""
    prompts = []
    with pytest.raises(InstrumentError) as failed:
        position_meter("munki", "stuck").measure("emissive_tele", acknowledge=collected(prompts))
    assert (failed.value.stage, failed.value.native_code) == ("read_sample", 0x130048)
    assert failed.value.native_text == "Sensor should be in projector position"
    assert [p["instruction"] for p in prompts] == ["sensor_position"]
    assert not any(e["event"] == "result" for e in failed.value.events)


@pytest.mark.parametrize("persona,mode,conditions,configuration", [
    # munki_imp.c:1071-1073: transmission white needs the dial at surface. Its
    # calibration position (:1067-1069) is a condition instead (munki.c:583-584).
    ("munki", "transmission_spot", 2, 2),
    # i1pro3_imp.c:7661: a reflective white needs the instrument on its tile.
    ("i1pro3", "reflective_spot", 1, 5),
])
def test_a_calibration_step_names_the_position_and_is_tried_again(
        persona, mode, conditions, configuration):
    """instappsup.c:210-233 retries calibrate() after the operator's key, and the
    condition stays established: the driver checks the same one again."""
    prompts = []
    result = position_meter(persona, "calibrate", "startup").measure(
        mode, calibrate=True, acknowledge=collected(prompts))
    assert len(result.patches) == 1
    assert result.calibration["remaining_calibrations"] == 0
    assert [p["instruction"] for p in prompts] == (
        ["calibration_setup"] * conditions + ["sensor_position", "sample_position"])
    position = prompts[conditions]
    assert (position["stage"], position["configuration"]) == ("calibrate", configuration)


@pytest.mark.parametrize("cancel", [False, True])
def test_calibration_reposition_alone_requires_return_to_sample(cancel):
    """No preceding calibration_setup can hide a missed position-recovery latch."""
    device = chart_meter("i1pro3", "instI1Pro3", "calibration_position_only")
    prompts = []

    def respond(prompt):
        prompts.append(prompt["instruction"])
        return "cancel" if cancel else "acknowledge"

    if cancel:
        with pytest.raises(InstrumentError) as failed:
            device.measure("reflective_spot", calibrate=True, acknowledge=respond)
        assert failed.value.stage == "cancelled"
        assert prompts == ["sensor_position"]
        assert not any(e["event"] == "result" for e in failed.value.events)
    else:
        result = device.measure("reflective_spot", calibrate=True, acknowledge=respond)
        assert len(result.patches) == 1
        assert prompts == ["sensor_position", "sample_position"]


def test_a_calibration_position_left_wrong_keeps_the_terminal_driver_diagnostic():
    """The calibration retry has the same bound and diagnostic contract as reads.

    The final calibrate() failure formerly passed through fail() and discarded
    interp_error, although the preceding position prompt retained its text.
    """
    prompts = []
    with pytest.raises(InstrumentError) as failed:
        position_meter("i1pro3", "calibrate_stuck", "startup").measure(
            "reflective_spot", calibrate=True, acknowledge=collected(prompts))
    assert (failed.value.stage, failed.value.native_code) == ("calibrate", 0x130020)
    assert failed.value.native_text == (
        "Standard adapter should be fitted and instrument placed on calibration tile")
    assert failed.value.native_text_truncated is False
    assert [p["instruction"] for p in prompts] == ["calibration_setup", "sensor_position"]
    assert not any(e["event"] == "result" for e in failed.value.events)


def test_calibrating_and_reading_each_get_their_own_retry():
    """One operation, two positions: the i1Pro 3 calibrates on its tile
    (i1pro3_imp.c:7561-7564) and reads ambient light through its ambient head
    (:9589-9593). An operator who missed both is asked about each once; a single
    retry per operation would have failed the read after fixing the tile."""
    prompts = []
    result = position_meter("i1pro3", "both", "startup").measure(
        "ambient_spot", calibrate=True, acknowledge=collected(prompts))
    assert len(result.patches) == 1
    assert [(p["instruction"], p.get("stage"), p.get("configuration")) for p in prompts] == [
        ("calibration_setup", None, None), ("sensor_position", "calibrate", 5),
        ("sample_position", None, None), ("sensor_position", "read_sample", 7)]


def test_a_strip_gets_its_own_retry_after_the_calibration_used_one():
    """The same per-step contract on a strip: a ColorMunki transmission strip
    checks the dial for its transmission white (munki_imp.c:1071-1073) and again
    for the pass (:2191-2206). Filing read_strip under the calibration step would
    leave the pass no retry; only a strip whose calibration AND read both prompt
    can show that."""
    prompts = []
    result = position_meter("munki", "both", "startup").measure_strip(
        "transmission_strip", patch_count=4, pass_name="B", guide=15, patch_width_mm=6,
        gap_width_mm=1.5, trailer_width_mm=3, calibrate=True, acknowledge=collected(prompts))
    assert len(result.patches) == 4
    assert [(p["instruction"], p.get("stage")) for p in prompts] == [
        ("calibration_setup", None), ("calibration_setup", None),
        ("sensor_position", "calibrate"), ("strip_position", None),
        ("sensor_position", "read_strip")]


def test_a_strip_names_the_position_and_is_read_again():
    prompts = []
    result = position_meter("munki", "read").measure_strip(
        "reflective_strip", patch_count=4, pass_name="B", guide=15, patch_width_mm=6,
        gap_width_mm=1.5, trailer_width_mm=3, acknowledge=collected(prompts))
    assert len(result.patches) == 4
    assert [(p["instruction"], p["stage"], p["configuration"]) for p in prompts] == [
        ("sensor_position", "read_strip", 2)]


def test_a_session_refusal_that_shares_the_code_is_not_a_position():
    """session.c refuses a sheet that cannot hold its patches, and a serial that
    is not the selected unit's, with inst_wrong_config: the code a driver's wrong
    position uses. No position fixes either, so each stays a refusal with no
    prompt. Python never sends that sheet, so the C boundary is reached directly;
    the instrument's dial is wrong too, so a prompt would have a reason to appear."""
    prompts = []
    table = Instrument(worker("chart"), "instSpectroScan", environment=dict(
        os.environ, CM_CHART_PERSONA="spectroscan", CM_CHART_FIXTURE="success"),
                expected_helper_sha256=hashlib.sha256(
                    Path(worker("chart")).read_bytes()).hexdigest())
    with pytest.raises(InstrumentError) as sheet:
        table._run("reflective_xy", True, WORKFLOW_FACILITIES["xy"], collected(prompts),
                   sheet=(2, 3, False, 0), read_xy=7, xy_passes=2, xy_steps=3,
                   xy_pass_name=" A", xy_step_name=" 1", calibrate=0,
                   xy_geometry="10.0,20.0,2.0,4.0,0.0,0.0,0.5,1.0")
    assert (sheet.value.stage, sheet.value.native_code) == ("sheet_geometry", 0x130000)
    unit = position_meter("munki", "read", expected_serial="CM-OTHER-0002")
    with pytest.raises(InstrumentError) as serial:
        unit.measure("emissive_tele", acknowledge=collected(prompts))
    assert (serial.value.stage, serial.value.native_code) == ("serial_mismatch", 0x130000)
    assert prompts == []


def answered(op, answer=lambda payload: "accept"):
    """Drive a frontend operation, answering each prompt; keep each payload."""
    payloads, deadline = [], time.monotonic() + 8
    try:
        while not op.done:
            assert time.monotonic() < deadline, "operation failed to stop"
            text = op.poll_event_json()
            if not text:
                time.sleep(0.002)
                continue
            event = json.loads(text)
            if event["kind"] == "instruction_required":
                payloads.append(event["payload"])
                op.respond(op.operation_id, event["payload"]["request_id"],
                           answer(event["payload"]))
        return json.loads(op.result_json()), payloads
    finally:
        op.close()


@pytest.mark.parametrize("persona,position,mode,spectral,needed,text", [
    ("munki", "read", "emissive_tele", True, "projector",
     "Sensor should be in projector position"),
    ("i1d3", "read", "ambient_spot", False, "ambient", "Ambient filter should be used"),
    # specbos.c:1114-1119 raises the code and fills no config_enum: inst.c's
    # default names nothing, and the driver has no words for it here.
    ("i1d3", "unmapped", "emissive_spot", False, "unknown", None),
])
def test_the_frontend_publishes_the_position_the_driver_needs(
        persona, position, mode, spectral, needed, text):
    result, payloads = answered(operation(position_meter(persona, position), mode=mode,
                                          spectral=spectral))
    assert result["status"] == "completed" and len(result["patches"]) == 1
    # No calibration setup occurs. Only the driver's real position request waits.
    assert [p["kind"] for p in payloads] == ["sensor_position"]
    assert [p["kind"] for p in payloads].count("sensor_position") == 1
    prompt = payloads[-1]
    assert prompt["allowed_responses"] == ["accept", "cancel"]
    assert prompt["position"] == {"needed": needed, "stage": "read_sample",
                                  "driver_text": text}
    assert prompt["calibration"] is None


def test_cancelling_at_the_position_prompt_reads_nothing():
    result, payloads = answered(
        operation(position_meter("munki", "read"), mode="emissive_tele", spectral=True),
        lambda payload: "cancel" if payload["kind"] == "sensor_position" else "accept")
    assert [p["kind"] for p in payloads] == ["sensor_position"]
    assert result["status"] == "cancelled" and result["patches"] == []


_POSITION = {"protocol_version": 1, "provenance": "simulated",
             "event": "instruction_required", "instruction": "sensor_position",
             "request_id": "position-retry", "stage": "read_sample", "configuration": 1,
             "native_code": 0x130048, "native_text": "Sensor should be in projector position",
             "native_text_truncated": False}


@pytest.mark.parametrize("change", [
    None,
    {"stage": "sheet_geometry"},        # a session refusal, not a driver's step
    {"stage": "calibrate"},             # this read asked for no calibration
    {"stage": "read_strip"},            # nor for a strip
    {"native_code": 0x110007},          # a calibration need, not a position
    {"request_id": "sample-retry"},
    {"configuration": -1},
    {"native_text": 7},
    {"native_text_truncated": "false"},
    "twice",
])
def test_a_position_request_the_helper_cannot_have_made_is_refused(change, monkeypatch):
    """session.c asks at most once, at a stage the request runs, for a driver's
    inst_wrong_config. Anything else is a protocol fault, not a prompt to show:
    a wrong position invented at a session refusal would tell an operator to turn
    a dial for a request no dial can fix. The unchanged request is accepted."""
    envelope = {"protocol_version": 1, "provenance": "simulated"}
    prompt = _POSITION if change in (None, "twice") else {**_POSITION, **change}
    events = [dict(envelope, event="connected", serial="TEST",
                   serial_encoding="utf8", spectral=False,
                   display_types=native_display_unsupported(),
                   baseline_mode_checks={"scope": "initialized_before_mode_configuration",
                       "mode_capabilities": 0x10018,
                       "checks": [{"mode_word": 0x10018, "native_code": 0}]},
                   mode=mode_word("emissive_spot", spectral=False)),
              prompt, *([_POSITION] if change == "twice" else []),
              dict(envelope, event="result", backend_reported={
                  "mtype": 1, "mcond": 0, "XYZ_v": 1, "XYZ": [1, 2, 3],
                  "spectrum": None, "duration": 0}),
              dict(envelope, event="terminal", status="completed")]

    class Session:
        stopped = "exited"

        def receive_message(self, **kwargs):
            return events.pop(0)

        def send(self, *args):
            pass

        def finish_messages(self, *, expected_returncode):
            assert expected_returncode == 0

        def close(self):
            pass

    monkeypatch.setattr(Instrument, "_open_session", lambda *args: Session())
    device = Instrument(Path("/never-started"), "instI1Disp3")
    if change is None:
        assert len(device.measure("emissive_spot", spectral=False).patches) == 1
        return
    with pytest.raises(ProtocolError, match="sensor position"):
        device.measure("emissive_spot", spectral=False)


@pytest.mark.parametrize("fixture", ["abort_then_wrong_position",
                                     "abort_then_needs_calibration"])
def test_a_cancel_the_driver_answers_with_another_failure_asks_nothing_more(fixture):
    """The operator cancels during the read and the driver fails with its own code
    anyway. Neither retry may follow: no position prompt, no calibration retry,
    no second placement. The read publishes nothing."""
    observed = meter().measure("emissive_spot", spectral=False)
    display_types = next(e["display_types"] for e in observed.events if e["event"] == "connected")
    result, instructions = finish(operation(meter(fixture),
        options={"display_type": display_types["entries"][1]["id"]},
        display_types=display_types), cancel_after_sample=True)
    assert instructions == ["calibration_setup", "sample_position"]
    assert result["status"] == "failed" and result["patches"] == []
    events = json.loads(dict(result["configuration"]).get("calibration_events_json", "[]"))
    assert not any(e["event"] == "calibration_retry" for e in events)


@pytest.mark.parametrize("waived", [True, False, None, "false"])
def test_a_calibration_outcome_says_whether_the_request_was_waived(waived, monkeypatch):
    """Whether the reading stands on a calibration its driver retained is part of
    the outcome, so an outcome that does not say, or says it in another type, is
    refused rather than read as either."""
    envelope = {"protocol_version": 1, "provenance": "simulated"}
    ready = dict(envelope, event="calibration_ready", needed_calibrations=0,
                 available_calibrations=0, remaining_calibrations=0,
                 status_source="driver", skipped=False, performed=False)
    if waived is not None:
        ready["initial_calibration_suppressed"] = waived
    events = [dict(envelope, event="connected", serial="TEST",
                   serial_encoding="utf8", spectral=False,
                   display_types=native_display_unsupported(),
                   baseline_mode_checks={"scope": "initialized_before_mode_configuration",
                       "mode_capabilities": 0x10018,
                       "checks": [{"mode_word": 0x10018, "native_code": 0}]},
                   mode=mode_word("emissive_spot", spectral=False)), ready,
              dict(envelope, event="result", backend_reported={
                  "mtype": 1, "mcond": 0, "XYZ_v": 1, "XYZ": [1, 2, 3],
                  "spectrum": None, "duration": 0}),
              dict(envelope, event="terminal", status="completed")]

    class Session:
        stopped = "exited"

        def receive_message(self, **kwargs):
            return events.pop(0)

        def send(self, *args):
            pass

        def finish_messages(self, *, expected_returncode):
            assert expected_returncode == 0

        def close(self):
            pass

    monkeypatch.setattr(Instrument, "_open_session", lambda *args: Session())
    device = Instrument(Path("/never-started"), "instI1Disp3")
    if type(waived) is bool:
        reading = device.measure("emissive_spot", spectral=False, calibrate=True)
        assert reading.calibration["initial_calibration_suppressed"] is waived
        return
    with pytest.raises(ProtocolError, match="at-open calibration was waived"):
        device.measure("emissive_spot", spectral=False, calibrate=True)
