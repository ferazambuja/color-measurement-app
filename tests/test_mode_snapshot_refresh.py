"""Configured-mode interrogation and stored-rate facts, without device access."""

import hashlib
import json
import os
import time
from pathlib import Path

import pytest
from conftest import supplied_stub

from color_measurement import instrument
from color_measurement.argyll_capability import CAPABILITY2, mode_word
from color_measurement.demo import demo_frontends
from color_measurement.instrument import (
    Configuration,
    Instrument,
    InstrumentError,
    StoredRefreshRate,
)
from color_measurement.instrument_frontend import InstrumentFrontend
from color_measurement.operation_exports import from_mat, to_mat
from color_measurement.operation_records import result_from_json, result_to_json


def meter(fixture):
    return Instrument(Path(supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")), "instI1Disp3",
        environment={**os.environ, "CM_COLORIMETER_FIXTURE": fixture},
               expected_helper_sha256=hashlib.sha256(
                   Path(supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")).read_bytes()).hexdigest())


def front(fixture, caps=0):
    return InstrumentFrontend(meter(fixture), mode_capabilities=mode_word("emissive_spot"),
        trigger_capabilities=caps, filter_capabilities=0, provenance="simulated")


def drive(client, workflow="mode_snapshot", options=None, layout=None, *, cancel=False,
          mode="emissive_spot"):
    descriptor = json.loads(client.describe_json())
    request = dict(schema_version=1, device_id=descriptor["device_id"],
        capability_id=descriptor["capability_id"], mode=mode, workflow=workflow,
        patch_ids=[], event_contract="retained_patch_v1",
        options=options or {}, layout=layout or {})
    operation = client.prepare(json.dumps(request), response_timeout=3, timeout=3)
    try:
        operation.dispatch()
        deadline = time.monotonic() + 8
        while not operation.done:
            while raw := operation.poll_event_json():
                event = json.loads(raw)
                if event["kind"] == "instruction_required":
                    operation.respond(operation.operation_id,
                                      event["payload"]["request_id"],
                                      "cancel" if cancel else "accept")
            assert time.monotonic() < deadline, "query did not stop"
            time.sleep(.002)
        return result_from_json(operation.result_json())
    finally:
        operation.close()


@pytest.mark.parametrize("rate", [None, 0, 5, 60.0, 150])
def test_override_is_configuration_with_explicit_invalidation(rate):
    config = Configuration(refresh_rate=rate)
    assert config.flags("emissive_spot").get("refresh_rate") == rate


@pytest.mark.parametrize("rate", [True, False, "60", -1, .1, 4.99, 150.01,
                                    float("nan"), float("inf"), 10**400])
def test_invalid_override_refuses_before_any_helper(rate):
    with pytest.raises(ValueError):
        Configuration(refresh_rate=rate)


@pytest.mark.parametrize("status,code,value", [
    ("available", 0, 0), ("available", 0, float("nan")),
    ("available", 0, float("inf")), ("available", 0, True),
    ("available", 0, 10**400), ("available", 0x110000, 60),
    ("unsupported", 0x50000, 0), ("undetermined", 0x110000, None),
])
def test_missing_or_invalid_state_never_becomes_a_numeric_reading(status, code, value):
    with pytest.raises(ValueError):
        StoredRefreshRate(status, code, value)


def test_snapshot_recomputes_offers_from_configured_mode_not_identity():
    client = front("snapshot_after_mode")
    before = json.loads(client.describe_json())
    offered = next(row for row in before["modes"] if row["mode"] == "emissive_spot")
    assert not {"refresh_frequency", "display_timing", "stored_refresh"} & {
        row["workflow"] for row in offered["workflows"]}
    result = drive(client)
    assert result.status == "completed" and not result.patches and not result.expected_patch_ids
    facts = json.loads(dict(result.configuration)["mode_snapshot_json"])
    assert facts["source"] == "mode_snapshot" and facts["requested_options"] == {}
    assert facts["stored_refresh"]["status"] == "needs_calibration"
    after = json.loads(client.describe_json())
    assert after["capability_id"] != before["capability_id"]
    offered = next(row for row in after["modes"] if row["mode"] == "emissive_spot")
    assert {"refresh_frequency", "display_timing", "stored_refresh"} <= {
        row["workflow"] for row in offered["workflows"]}
    assert offered["capability_snapshot"]["source"] == "mode_snapshot"
    assert next(row for row in after["options"] if row["id"] == "refresh_rate")["available"]
    assert result_from_json(result_to_json(result)) == result


def test_query_does_not_require_or_set_a_read_trigger():
    result = meter("snapshot_no_trigger").mode_snapshot("emissive_spot")
    binding = next(event for event in result.events if event["event"] == "connected")
    assert binding["trigger"] is None and not result.has_readings
    assert result.calibration is None
    assert not {"result", "refresh_frequency", "display_timing", "instruction_required"} & {
        event["event"] for event in result.events}


@pytest.mark.parametrize("rate", [0, 5, 75, 150])
def test_setter_then_getter_preserves_override_and_invalidation(rate):
    result = meter("success").mode_snapshot("emissive_spot",
        configuration=Configuration(refresh_rate=rate))
    assert not result.has_readings
    assert result.stored_refresh.status == ("available" if rate else "undetermined")
    assert result.stored_refresh.value == (rate or None)
    binding = next(event for event in result.events if event["event"] == "connected")
    assert binding["refresh_rate_override_hz"] == rate


@pytest.mark.parametrize("rate", [0, 85])
def test_override_applies_after_display_selection_before_calibration(rate):
    client = front("success", CAPABILITY2["get_refresh_rate"])
    first = drive(client)
    descriptor = json.loads(client.describe_json())
    choice = descriptor["display_types"]["entries"][1]["id"]
    result = drive(client, "stored_refresh", {"display_type": choice, "refresh_rate": rate},
                   {"calibrate": True})
    assert first.status == result.status == "completed"
    fact = json.loads(dict(result.configuration)["stored_refresh_json"])
    assert fact["value"] == (rate or None)
    assert fact["status"] == ("available" if rate else "undetermined")
    outcome = json.loads(dict(result.configuration)["calibration_outcome_json"])
    assert not outcome["performed"], "override was cleared or applied after calibration"
    assert not result.patches


@pytest.mark.parametrize("fixture,status,value", [
    ("success", "needs_calibration", None),
    ("stored_misread", "undetermined", None),
    ("stored_unsupported", "not_advertised", None),
    ("stored_device", "available", 75.0),
])
def test_query_retains_native_state_without_fabricating_measurements(fixture, status, value):
    result = meter(fixture).stored_refresh_rate()
    assert (result.stored_refresh.status, result.stored_refresh.value) == (status, value)
    # A getter the configured word lacks was never called, so it has no code.
    assert (result.stored_refresh.native_code is None) == (status == "not_advertised")
    assert not result.has_readings and result.patches == ()


@pytest.mark.parametrize("fixture,stage", [
    ("stored_zero", "invalid_stored_refresh_rate"),
    ("stored_nan", "invalid_stored_refresh_rate"),
    ("stored_inf", "invalid_stored_refresh_rate"),
    ("stored_unwritten", "invalid_stored_refresh_rate"),
    ("stored_error", "get_refresh_rate"),
    ("stored_set_error", "set_refresh_rate"),
])
def test_getter_and_setter_failures_keep_native_diagnostics(fixture, stage):
    with pytest.raises(InstrumentError) as caught:
        meter(fixture).mode_snapshot("emissive_spot", configuration=Configuration(
            refresh_rate=60 if fixture == "stored_set_error" else None))
    assert caught.value.stage == stage
    assert caught.value.worker_stop == "exited"


def test_setter_is_not_inferred_from_getter_capability():
    with pytest.raises(InstrumentError, match="refresh_rate_unavailable"):
        meter("stored_device").mode_snapshot("emissive_spot",
            configuration=Configuration(refresh_rate=60))


@pytest.mark.parametrize("workflow", ["refresh", "timing"])
def test_override_cannot_turn_native_frequency_or_delay_into_an_echo(workflow):
    device = Instrument("/must-not-run", "instI1Disp3")
    with pytest.raises(ValueError, match="Non-photometric"):
        if workflow == "refresh":
            device.measure_refresh(configuration=Configuration(refresh_rate=60))
        else:
            device.measure_display_timing(presenter=lambda _: None,
                configuration=Configuration(refresh_rate=60))


def test_stored_fact_survives_json_and_mat_without_a_patch():
    pytest.importorskip("scipy.io")
    result = drive(front("success", CAPABILITY2["set_refresh_rate"]),
                   options={"refresh_rate": 60})
    assert result.status == "completed" and not result.patches
    assert from_mat(to_mat(result)) == result
    assert json.loads(dict(result.configuration)["stored_refresh_json"])["value"] == 60


def test_queries_do_not_require_acquisition_geometry_or_a_read_allowance():
    client = InstrumentFrontend(meter("success"),
        mode_capabilities=mode_word("reflective_strip"), trigger_capabilities=0,
        filter_capabilities=0, provenance="simulated", read_allowance_floor=120)
    descriptor = json.loads(client.describe_json())
    entry = descriptor["modes"][0]
    assert not entry["available"]
    request = dict(schema_version=1, device_id=descriptor["device_id"],
        capability_id=descriptor["capability_id"], mode=entry["mode"], workflow="mode_snapshot",
        patch_ids=[], event_contract="retained_patch_v1", options={}, layout={})
    # The stub refuses this unsupported reflective mode at native check_mode,
    # rather than the frontend demanding strip geometry or a read budget.
    operation = client.prepare(json.dumps(request), response_timeout=3, timeout=3)
    try:
        operation.dispatch()
        deadline = time.monotonic() + 8
        while not operation.done:
            assert time.monotonic() < deadline
            time.sleep(.002)
        result = result_from_json(operation.result_json())
        assert result.status == "failed" and result.error_stage == "check_mode"
        assert json.loads(client.describe_json()) == descriptor
    finally:
        operation.close()


def test_snapshot_returns_the_new_descriptor_identity_and_refuses_old_requests():
    client = front("success")
    before = json.loads(client.describe_json())
    result = drive(client)
    facts = json.loads(dict(result.configuration)["mode_snapshot_json"])
    after = json.loads(client.describe_json())
    assert facts["capability_id"] == after["capability_id"] != before["capability_id"]
    old = dict(schema_version=1, device_id=before["device_id"],
        capability_id=before["capability_id"], mode="emissive_spot", workflow="mode_snapshot",
        patch_ids=[], options={}, layout={}, event_contract="retained_patch_v1")
    with pytest.raises(ValueError, match="Stale or different"):
        client.prepare(json.dumps(old))


def test_cancelled_stored_query_keeps_the_descriptor_and_no_fake_sample():
    client = front("skippable_calibration", CAPABILITY2["get_refresh_rate"])
    before = json.loads(client.describe_json())
    result = drive(client, "stored_refresh", layout={"calibrate": True}, cancel=True)
    assert result.status == "cancelled" and not result.patches
    assert "stored_refresh_json" not in dict(result.configuration)
    assert json.loads(client.describe_json()) == before


def test_late_cancel_retains_native_context_without_switching_descriptor(monkeypatch):
    original = Instrument.mode_snapshot
    def cancel_after_native_completion(self, *args, **kwargs):
        measurement = original(self, *args, **kwargs)
        self.cancel.set()
        return measurement
    monkeypatch.setattr(Instrument, "mode_snapshot", cancel_after_native_completion)
    client = front("success")
    before = json.loads(client.describe_json())
    result = drive(client)
    assert result.status == "cancelled" and not result.patches
    assert "stored_refresh_json" in dict(result.configuration)
    assert "mode_snapshot_json" not in dict(result.configuration)
    assert json.loads(client.describe_json()) == before


def test_failed_query_publication_rolls_back_descriptor(monkeypatch):
    from color_measurement.instrument_frontend import InstrumentOperation
    original = InstrumentOperation._stage_event
    def fail_terminal(self, kind, payload):
        if kind == "terminal":
            raise RuntimeError("injected query publication failure")
        return original(self, kind, payload)
    monkeypatch.setattr(InstrumentOperation, "_stage_event", fail_terminal)
    client = front("success")
    before = json.loads(client.describe_json())
    result = drive(client)
    assert result.status == "failed" and "Terminal publication" in result.error
    assert not result.patches
    assert json.loads(client.describe_json()) == before


# -- challenge findings: retention, completion and descriptor scope -----------

def demo_front(persona):
    from color_measurement import demo
    return next(f for f in demo.demo_frontends() if f._meter.persona == persona)


def rewrite_demo(monkeypatch, change):
    """Pass every in-process demo event through `change`, which may drop one (None)."""
    from color_measurement.demo import _DemoSession
    original = _DemoSession._produce
    def produce(self, *args, **kwargs):
        for event in original(self, *args, **kwargs):
            event = change(event)
            if event is not None:
                yield event
    monkeypatch.setattr(_DemoSession, "_produce", produce)


def stored_frame(**fields):
    return lambda event: ({**event, **fields} if event["event"] == "stored_refresh_rate"
                          else event)


@pytest.mark.parametrize("change", [
    # A numeric value beside a non-success code, and a frame missing its value.
    stored_frame(status="needs_calibration", native_code=0x110000, value=60.0),
    lambda event: ({k: v for k, v in event.items() if k != "value"}
                   if event["event"] == "stored_refresh_rate" else event),
], ids=["value_beside_failure", "missing_value"])
def test_a_refused_stored_frame_is_not_retained_and_keeps_its_diagnostic(monkeypatch, change):
    rewrite_demo(monkeypatch, change)
    client = demo_front("i1_display_3")
    before = json.loads(client.describe_json())
    result = drive(client)
    assert result.status == "failed" and "Invalid stored-refresh state" in result.error
    assert "stored_refresh_json" not in dict(result.configuration)
    assert json.loads(client.describe_json()) == before


def test_an_override_the_driver_did_not_accept_is_not_retained(monkeypatch):
    rewrite_demo(monkeypatch, lambda event: (
        {**event, "refresh_rate_override_hz": 75} if event["event"] == "connected" else event))
    result = drive(demo_front("i1_display_3"), options={"refresh_rate": 60})
    assert result.status == "failed" and "differs from request" in result.error
    assert "refresh_rate_override_hz" not in dict(result.configuration)


@pytest.mark.parametrize("override", [False, "0", {}, []],
                         ids=["boolean_zero", "string_zero", "object", "array"])
def test_a_refused_override_type_is_not_retained_as_configuration(monkeypatch, override):
    rewrite_demo(monkeypatch, lambda event: (
        {**event, "refresh_rate_override_hz": override}
        if event["event"] == "connected" else event))
    client = demo_front("i1_display_3")
    before = json.loads(client.describe_json())
    result = drive(client, options={"refresh_rate": 0})
    assert result.status == "failed" and "differs from request" in result.error
    assert "refresh_rate_override_hz" not in dict(result.configuration)
    assert json.loads(client.describe_json()) == before


@pytest.mark.parametrize("status,code", [("invalid", 0), ("failed", 0x070008)])
def test_a_completion_after_an_unusable_stored_rate_is_refused(monkeypatch, status, code):
    """session.c fails the operation after either; a completion would be a lie."""
    rewrite_demo(monkeypatch, stored_frame(status=status, native_code=code, value=None))
    client = demo_front("i1_display_3")
    before = json.loads(client.describe_json())
    result = drive(client)
    assert result.status == "failed" and "unusable stored refresh" in result.error
    assert json.loads(client.describe_json()) == before


def test_a_configured_mode_query_cannot_ask_the_operator(monkeypatch):
    def insert(event):
        if event["event"] == "stored_refresh_rate":
            return {"protocol_version": 1, "provenance": "simulated",
                    "event": "instruction_required", "instruction": "sensor_position",
                    "request_id": "unexpected"}
        return event
    rewrite_demo(monkeypatch, insert)
    client = demo_front("i1_display_3")
    before = json.loads(client.describe_json())
    result = drive(client)
    assert result.status == "failed" and "operator instruction" in result.error
    assert json.loads(client.describe_json()) == before


def test_a_queried_display_selection_stays_in_its_snapshot():
    client = demo_front("i1_display_3")
    before = json.loads(client.describe_json())["display_types"]
    choice = before["entries"][1]["id"]
    result = drive(client, options={"display_type": choice})
    assert result.status == "completed"
    descriptor = json.loads(client.describe_json())
    # Every operation starts at the driver default: the selection is the query's.
    assert descriptor["display_types"]["applied_choice_id"] is None
    assert descriptor["display_types"]["readback"] == before["readback"]
    facts = json.loads(dict(result.configuration)["mode_snapshot_json"])
    assert facts["display_types"]["applied_choice_id"] == choice


def test_a_snapshot_configures_every_name_of_the_same_word():
    client = demo_front("i1pro2")
    def workflows(mode):
        return {item["workflow"] for entry in json.loads(client.describe_json())["modes"]
                if entry["mode"] == mode for item in entry["workflows"]}
    equivalent = {entry["mode"]: entry["equivalent_to"]
                  for entry in json.loads(client.describe_json())["modes"]}
    assert equivalent["emissive_spot_adaptive"] == "emissive_spot"
    assert equivalent["emissive_spot_nonadaptive"] == "emissive_spot_nonadaptive"
    assert "refresh_frequency" not in workflows("emissive_spot_adaptive")
    assert drive(client, mode="emissive_spot_adaptive").status == "completed"
    # The word the query configured, under either of its names; not another word.
    for mode in ("emissive_spot", "emissive_spot_adaptive"):
        assert {"refresh_frequency", "display_timing"} <= workflows(mode), mode
    assert "refresh_frequency" not in workflows("emissive_spot_nonadaptive")


@pytest.mark.parametrize("described_default", [True, False])
def test_a_query_correction_does_not_become_the_descriptor_default(described_default):
    from copy import deepcopy
    from dataclasses import replace

    client = demo_front("i1_display_3")
    measurement = client._meter.mode_snapshot("emissive_spot")
    facts = deepcopy(measurement.mode_snapshot)
    facts["correction"] = "ccmx"
    readback = facts["display_types"]["readback"]
    readback["refresh_mode"] = 1 - readback["refresh_mode"]
    assert facts["display_types"]["applied_choice_id"] is None
    if not described_default:
        client._display_types = None
    before = json.loads(client.describe_json())["display_types"]
    retained = client._retain_mode_snapshot(
        "emissive_spot", {"correction": {"path": "/simulated/correction.ccmx"}},
        replace(measurement, mode_snapshot=facts))
    assert retained["display_types"]["readback"] == readback
    assert json.loads(client.describe_json())["display_types"] == before


def test_a_native_query_correction_preserves_the_described_default():
    client = front("success", CAPABILITY2["ccmx"])
    assert drive(client).status == "completed"
    before = json.loads(client.describe_json())["display_types"]
    text = ('CCMX\n\nINSTRUMENT "Xrite DTP94"\nDISPLAY "CRT"\nCOLOR_REP "XYZ"\n'
            'TECHNOLOGY "CRT"\n\nNUMBER_OF_FIELDS 3\nBEGIN_DATA_FORMAT\n'
            "XYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\n\nNUMBER_OF_SETS 3\nBEGIN_DATA\n"
            "1 0 0\n0 1 0\n0 0 1\nEND_DATA\n")
    result = drive(client, options={"correction": {"text": text}})
    assert result.status == "completed"
    query = json.loads(dict(result.configuration)["mode_snapshot_json"])
    assert query["display_types"]["applied_choice_id"] is None
    assert query["display_types"]["readback"]["refresh_mode"] == 1
    assert before["readback"]["refresh_mode"] == 0
    assert json.loads(client.describe_json())["display_types"] == before


def test_a_request_validated_before_a_query_committed_is_refused(monkeypatch):
    # This is the shared Python admission boundary, independent of driver I/O.
    client = next(f for f in demo_frontends() if f._meter.persona == "i1pro3")
    original = InstrumentFrontend._validate_request
    meter_type = type(client._meter)
    original_query = meter_type.mode_snapshot
    committed = []
    queries = []

    def counted_query(self, *args, **kwargs):
        queries.append(args)
        return original_query(self, *args, **kwargs)

    def validate_then_commit(self, text):
        validated = original(self, text)
        if not committed:
            committed.append(None)
            committed[0] = drive(client)   # another client's query lands here
        return validated
    monkeypatch.setattr(InstrumentFrontend, "_validate_request", validate_then_commit)
    monkeypatch.setattr(meter_type, "mode_snapshot", counted_query)
    monkeypatch.setattr(instrument, "HelperSession",
                        lambda *a, **kw: pytest.fail("Admission race attempted native transport"))
    refused = drive(client)
    assert refused.status == "failed" and refused.software_stopped
    assert "Stale or different capability snapshot" in refused.error
    assert dict(refused.configuration)["dispatch_failure_stage"] == "stale_snapshot"
    assert dict(refused.configuration)["stop_requested_by"] == "host_failure"
    assert refused.error_stage is None and refused.error_native_code is None
    assert not refused.patches and not refused.expected_patch_ids
    assert committed[0].status == "completed"
    assert len(queries) == 1, "The stale held operation must not dispatch a second query"
    assert not client._ownership.locked()


def test_a_withdrawn_setter_publishes_why():
    client = front("stored_device", CAPABILITY2["set_refresh_rate"])
    rate = next(o for o in json.loads(client.describe_json())["options"]
                if o["id"] == "refresh_rate")
    assert rate["available"]
    assert drive(client).status == "completed"   # this driver withdraws the setter
    rate = next(o for o in json.loads(client.describe_json())["options"]
                if o["id"] == "refresh_rate")
    assert not rate["available"] and rate["unavailable_reason"]


def test_an_override_survives_a_correction_that_changes_refresh_mode(tmp_path):
    """i1d3.c:3301 (spyd2.c:3194): a correction's technology sets the refresh mode,
    and a change clears the stored rate, so the override must follow it."""
    from color_measurement.colorimeter_correction import read_correction
    path = tmp_path / "crt.ccmx"
    path.write_text(
        'CCMX\n\nINSTRUMENT "Xrite DTP94"\nDISPLAY "CRT"\nCOLOR_REP "XYZ"\n'
        'TECHNOLOGY "CRT"\n\nNUMBER_OF_FIELDS 3\nBEGIN_DATA_FORMAT\n'
        "XYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\n\nNUMBER_OF_SETS 3\nBEGIN_DATA\n"
        "1 0 0\n0 1 0\n0 0 1\nEND_DATA\n")
    result = meter("success").stored_refresh_rate(calibrate=True, configuration=Configuration(
        refresh_rate=85, correction=read_correction(path)))
    assert (result.stored_refresh.status, result.stored_refresh.value) == ("available", 85)
    assert not result.calibration["performed"]


# Each refused before a connected event: returncode 2 and nothing on the wire.
NATIVE_REFUSALS = {
    "rate_low": {"refresh_rate": 4.99},
    "rate_high": {"refresh_rate": 150.01},
    "rate_nan": {"refresh_rate": "nan"},
    "rate_inf": {"refresh_rate": "inf"},
    "rate_overflow": {"refresh_rate": "1e400"},
    "snapshot_reads": {"mode_snapshot": 1, "read": 1},
    "snapshot_calibrates": {"mode_snapshot": 1, "calibrate": 1},
    "snapshot_strip": {"mode_snapshot": 1, "read_strip": 1},
    "stored_timing": {"stored_refresh": 1, "read_timing": 1},
}


@pytest.mark.parametrize("name", NATIVE_REFUSALS)
def test_the_worker_refuses_what_python_never_sends(name):
    import subprocess
    device = meter("success")
    command = device._argv("emissive_spot", False, [], NATIVE_REFUSALS[name])
    run = subprocess.run(command, capture_output=True, text=True, timeout=10,
                         env=device.environment)
    assert run.returncode == 2 and not run.stdout, run.stdout + run.stderr


def test_the_worker_takes_one_refresh_rate():
    import subprocess
    device = meter("success")
    command = device._argv("emissive_spot", False, [], {"mode_snapshot": 1, "refresh_rate": 60})
    at = command.index("--refresh-rate")
    run = subprocess.run([*command, *command[at:at + 2]], capture_output=True, text=True,
                         timeout=10, env=device.environment)
    assert run.returncode == 2 and not run.stdout, run.stdout + run.stderr


def test_the_override_cannot_accompany_a_measured_rate():
    """session.c refuses it after attaching nothing; Python never sends it."""
    import subprocess
    device = meter("success")
    command = device._argv("emissive_spot", False, [], {"read_refresh": 1, "refresh_rate": 60})
    run = subprocess.run(command, capture_output=True, text=True, timeout=10,
                         env=device.environment)
    events = [json.loads(line) for line in run.stdout.splitlines()]
    assert run.returncode != 0
    assert [e.get("stage") for e in events if e["event"] == "error"] == ["refresh_configuration"]
    assert not [e for e in events if e["event"] == "connected"]


def test_every_published_refresh_bit_is_one_its_driver_advertises(argyll_archive, tmp_path):
    """A persona's get/set refresh bits must appear in its own driver's source.

    The DTP20 persona once carried inst2_get_refresh_rate where dtp20.c:1464-1468
    sets inst2_has_battery, and the fixtures and demo offered stored_refresh on a
    chart reader. The chain is the composed tree's: inst_name() names the type,
    new_inst() its constructor, and the constructor's file advertises the bits.
    """
    import re
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import argyll_native_check as native
    root = Path(__file__).resolve().parents[1]
    source, _ = native.prepare(argyll_archive, tmp_path / "build",
                               contribution=root / "tools/native_argyll/contributions/cr_pr")
    spectro = source / "spectro"
    def code(path):
        text = path.read_text(errors="replace")
        return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))
    names = code(spectro / "insttypes.c")
    names = names[names.index("char *inst_name(instType itype)"):]
    types = {model: itype for itype, model in re.findall(
        r'case (inst\w+):\s*return "([^"]+)";', names[:names.index("\n}\n")])}
    constructors = {}
    for condition, constructor in re.findall(
            r"if\s*\(([^;]*?)\)\s*p = \(inst \*\)new_(\w+)\(", code(spectro / "inst.c")):
        for itype in re.findall(r"\binst[A-Z]\w*", condition):
            constructors.setdefault(itype, constructor)
    drivers = {path.name: code(path) for path in spectro.glob("*.c")}
    def driver(constructor):
        (name,) = [name for name, text in drivers.items()
                   if re.search(rf"\bnew_{constructor}\([^)]*\)\s*\{{", text)]
        return drivers[name]
    fixtures = root / "tests/fixtures/generic_frontend"
    from color_measurement import demo
    published = {persona: json.loads((fixtures / persona / "describe.json").read_text())
                 for persona in json.loads((fixtures / "index.json").read_text())["personas"]}
    words = [(f"fixture {persona}", d["device"]["model"], d["trigger_capabilities"])
             for persona, d in published.items()]
    words += [(f"demo {persona}", row["model"], row["capabilities"]["trigger_capabilities"])
              for persona, row in demo._data()["personas"].items()]
    checked = 0
    for where, model, word in words:
        text = driver(constructors[types[model]])
        for bit in ("get_refresh_rate", "set_refresh_rate"):
            if word & CAPABILITY2[bit]:
                assert f"inst2_{bit}" in text, f"{where} ({model}) advertises {bit}"
                checked += 1
    assert checked, "No persona advertises a refresh bit: the census checked nothing"
