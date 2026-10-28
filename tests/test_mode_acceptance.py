"""Baseline acceptance preserves geometry, output selection and uncertainty."""

import copy
import importlib.util
import json
import re
import zipfile
from pathlib import Path

import pytest
from conftest import native_display_unsupported

from color_measurement.argyll_capability import MODE_FLAGS, baseline_mode_words, mode_word
from color_measurement.instrument import Instrument
from color_measurement.instrument_frontend import InstrumentFrontend
from color_measurement.mode_acceptance import (
    BASELINE_SCOPE,
    baseline_native_mode,
    validate_baseline_mode_checks,
)
from color_measurement.native_protocol import ProtocolError

PRIMARY = mode_word("emissive_spot") | mode_word("emissive_tele")


def facts(primary=PRIMARY, code=0):
    return {"scope": BASELINE_SCOPE, "mode_capabilities": primary,
            "checks": [{"mode_word": word, "native_code": code}
                       for word in baseline_mode_words(primary)]}


def frontend(checks=None, primary=PRIMARY):
    return InstrumentFrontend(Instrument("/never-started", "instI1Pro3"),
        mode_capabilities=primary, trigger_capabilities=1, filter_capabilities=0,
        baseline_mode_checks=checks, provenance="simulated")


def test_differing_geometry_and_output_answers_do_not_cross_confirm_or_block_requests():
    checks = facts(code=0x50000)
    for check in checks["checks"]:
        if check["mode_word"] == mode_word("emissive_tele"):
            check["native_code"] = 0
        elif check["mode_word"] == mode_word("emissive_spot", spectral=False):
            check["native_code"] = 0x60004
    front = frontend(checks)
    descriptor = json.loads(front.describe_json())
    modes = {entry["mode"]: entry for entry in descriptor["modes"]}
    spot = modes["emissive_spot"]["baseline_native_mode"]
    tele = modes["emissive_tele"]["baseline_native_mode"]
    assert spot["default"]["status"] == "rejected"
    assert spot["xyz_only"] == {"mode_word": 0x10018, "status": "unknown",
        "native_code": 0x60004, "reason": "native_check_inconclusive"}
    assert tele["default"]["status"] == "confirmed"
    assert tele["xyz_only"]["status"] == "rejected"
    assert modes["emissive_spot_adaptive"]["baseline_native_mode"] == spot
    assert modes["emissive_spot"]["available"] is True
    # The baseline is not a future readiness check or an acquisition gate.
    request = json.dumps({"schema_version": 1, "device_id": descriptor["device_id"],
        "capability_id": descriptor["capability_id"], "mode": "emissive_spot",
        "workflow": "spot", "patch_ids": ["A"], "options": {}, "layout": {}})
    accepted, *_ = front._validate_request(request)
    assert accepted["mode"] == "emissive_spot"


@pytest.mark.parametrize("code,status,reason", [
    (0, "confirmed", None), (0x50000, "rejected", "native_mode_rejected"),
    (0x500ab, "rejected", "native_mode_rejected"),
    (None, "unknown", "check_mode_unavailable"),
    (1, "unknown", "native_check_inconclusive"),
    (0x60000, "unknown", "native_check_inconclusive"),
    (0xffffffff, "unknown", "native_check_inconclusive"),
])
def test_only_success_and_unsupported_class_are_definitive(code, status, reason):
    result = baseline_native_mode(facts(code=code), "emissive_spot", spectral=True)["default"]
    assert result == {"mode_word": 0x30018, "status": status,
                      "native_code": code, "reason": reason}


def test_absent_observation_and_later_new_candidate_stay_unknown():
    no_facts = json.loads(frontend().describe_json())
    assert no_facts["baseline_mode_checks"] is None
    assert all(entry["baseline_native_mode"]["default"]["reason"] == "baseline_not_observed"
               for entry in no_facts["modes"])
    # Later caps may expose a route absent at initialization; no false rejection.
    initial = facts(mode_word("emissive_spot", spectral=False))
    later = json.loads(frontend(initial).describe_json())
    modes = {entry["mode"]: entry for entry in later["modes"]}
    assert modes["emissive_spot"]["baseline_native_mode"]["default"]["reason"] == "mode_not_checked"
    assert modes["emissive_spot"]["baseline_native_mode"]["xyz_only"]["status"] == "confirmed"
    assert modes["emissive_tele"]["baseline_native_mode"]["default"]["reason"] == "mode_not_checked"
    xyz = json.loads(frontend(initial, initial["mode_capabilities"]).describe_json())
    assert all(entry["baseline_native_mode"]["xyz_only"] is None for entry in xyz["modes"])


def test_snapshot_is_detached_and_acceptance_changes_its_identity():
    checks = facts()
    front = frontend(checks)
    before = front.describe_json()
    checks["checks"][0]["native_code"] = 0x50000
    assert front.describe_json() == before
    changed = json.loads(frontend(checks).describe_json())
    assert changed["capability_id"] != json.loads(before)["capability_id"]


# A replaced mode word also leaves its candidate uncovered; each case names the
# refusal it must reach first.
@pytest.mark.parametrize("mutate,refusal", [
    (lambda value: value.update(scope="configured"), "Invalid baseline mode checks"),
    (lambda value: value.update(extra=0), "Invalid baseline mode checks"),
    (lambda value: value.pop("checks"), "Invalid baseline mode checks"),
    (lambda value: value.update(mode_capabilities=True), "Invalid baseline mode checks"),
    (lambda value: value.update(mode_capabilities=-1), "Invalid baseline mode checks"),
    (lambda value: value.update(mode_capabilities=2**32), "Invalid baseline mode checks"),
    (lambda value: value.update(checks=None), "Invalid baseline mode checks"),
    (lambda value: value["checks"].pop(), "omit an advertised candidate"),
    (lambda value: value["checks"].append(value["checks"][0]), "duplicate or unadvertised"),
    (lambda value: value.update(checks=value["checks"] * 11), "duplicate or unadvertised"),
    (lambda value: value["checks"][0].update(mode_word=True), "duplicate or unadvertised"),
    (lambda value: value["checks"][0].update(mode_word=-1), "duplicate or unadvertised"),
    (lambda value: value["checks"][0].update(mode_word=2**32), "duplicate or unadvertised"),
    (lambda value: value["checks"][0].update(mode_word=0x10018 | MODE_FLAGS["highres"]),
     "duplicate or unadvertised"),
    (lambda value: value["checks"][0].update(native_code=True), "duplicate or unadvertised"),
    (lambda value: value["checks"][0].update(native_code=-1), "duplicate or unadvertised"),
    (lambda value: value["checks"][0].update(native_code=2**32), "duplicate or unadvertised"),
    (lambda value: value["checks"][0].update(status="confirmed"), "duplicate or unadvertised"),
])
def test_malformed_incomplete_or_invented_checks_are_refused(mutate, refusal):
    value = facts()
    mutate(value)
    with pytest.raises(ValueError, match=refusal):
        validate_baseline_mode_checks(value)


def test_native_order_is_not_semantic_and_initial_mask_must_agree_when_known():
    value = facts()
    value["checks"].reverse()
    assert validate_baseline_mode_checks(value) == value
    saved = copy.deepcopy(value)
    detached = validate_baseline_mode_checks(value)
    detached["checks"][0]["native_code"] = None
    assert value == saved
    with pytest.raises(ValueError, match="different initial"):
        validate_baseline_mode_checks(value, mode_capabilities=0)
    assert validate_baseline_mode_checks(facts(0))["checks"] == []


def test_status_classification_constants_match_live_upstream_enum(argyll_archive):
    with zipfile.ZipFile(argyll_archive) as archive:
        header = archive.read("Argyll_V3.5.0/spectro/inst.h").decode()
    for name, expected in (("inst_ok", 0), ("inst_mask", 0xff0000),
                           ("inst_unsupported", 0x50000)):
        values = re.findall(r"^\s*" + name + r"\s*=\s*(0x[0-9a-fA-F]+),", header, re.M)
        assert [int(value, 16) for value in values] == [expected]


@pytest.mark.parametrize("change", ["absent", "null", "duplicate", "rejected", "new_candidate"])
def test_acquisition_requires_well_formed_facts_without_using_them_as_a_readiness_gate(
    change, monkeypatch,
):
    primary = mode_word("emissive_spot", spectral=False)
    checks = facts(primary, 0x50000)
    if change == "duplicate":
        checks["checks"].append(checks["checks"][0])
    if change == "new_candidate":
        checks = facts(0)
    envelope = {"protocol_version": 1, "provenance": "simulated"}
    connected = dict(envelope, event="connected", serial="TEST", serial_encoding="utf8",
                     spectral=False, mode=primary,
                     display_types=native_display_unsupported(),
                     baseline_mode_checks=None if change == "null" else checks)
    if change == "absent":
        connected.pop("baseline_mode_checks")
    events = [connected, dict(envelope, event="result", backend_reported={
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
    device = Instrument("/never-started", "instI1Disp3")
    if change in {"rejected", "new_candidate"}:
        result = device.measure("emissive_spot", spectral=False)
        assert len(result.patches) == 1
        assert result.events[0]["baseline_mode_checks"] == checks
    else:
        with pytest.raises(ProtocolError, match="baseline mode"):
            device.measure("emissive_spot", spectral=False)


@pytest.mark.parametrize("contradiction", [None, "descriptor", "connected", "physical"])
def test_demo_generation_preserves_only_consistent_simulated_baseline_facts(
    tmp_path, contradiction,
):
    path = Path(__file__).parents[1] / "tools/generate_demo_data.py"
    spec = importlib.util.spec_from_file_location("demo_data_mode_checks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    module.SOURCE = tmp_path / "captures"
    directory = module.SOURCE / "persona"
    directory.mkdir(parents=True)
    checks = facts()
    descriptor = json.loads(frontend(checks).describe_json())
    if contradiction == "descriptor":
        descriptor["modes"][0]["baseline_native_mode"]["default"]["status"] = "rejected"
    if contradiction == "physical":
        descriptor["provenance"] = "physical"
    connected = {"event": "connected", "baseline_mode_checks": copy.deepcopy(checks),
                 "strip_policies": {}}
    if contradiction == "connected":
        connected["baseline_mode_checks"]["checks"][0]["native_code"] = 0x50000
    capture = {"request": {"mode": "emissive_spot", "workflow": "spot",
                           "options": {}, "layout": {}},
               "result": {"status": "completed", "patches": [{"observation": {
                   "raw_backend_output": json.dumps({"events": [connected, {"event": "result"}]})}
               }]}}
    (module.SOURCE / "index.json").write_text(json.dumps({"personas": ["persona"]}))
    (directory / "describe.json").write_text(json.dumps(descriptor))
    (directory / "emissive_spot.spot.json").write_text(json.dumps(capture))
    if contradiction is not None:
        with pytest.raises(SystemExit, match="baseline mode checks|must be simulated"):
            module.generate()
    else:
        persona = json.loads(module.generate())["personas"]["persona"]
        assert persona["baseline_mode_checks"] == checks
        assert persona["routes"]["emissive_spot"]["connected"]["baseline_mode_checks"] == checks


def test_complete_mode_vocabulary_and_duplicate_refusals_share_one_authority():
    from color_measurement.argyll_capability import baseline_mode_words
    from color_measurement.mode_acceptance import validate_baseline_mode_checks

    primary = 0xffffffff
    words = baseline_mode_words(primary)
    facts = {"scope": "initialized_before_mode_configuration",
             "mode_capabilities": primary,
             "checks": [{"mode_word": word, "native_code": 0} for word in words]}
    assert validate_baseline_mode_checks(facts) == facts
    duplicate = {**facts, "checks": [facts["checks"][0]] * 41}
    with pytest.raises(ValueError, match="duplicate or unadvertised"):
        validate_baseline_mode_checks(duplicate)
    with pytest.raises(ValueError, match="omit an advertised candidate"):
        validate_baseline_mode_checks({**facts, "checks": facts["checks"][:-1]})


@pytest.mark.parametrize("routes", ["none", "disagree", "agree"])
def test_demo_generation_distinguishes_no_default_route_from_policy_disagreement(
    tmp_path, routes,
):
    """A persona whose captures all failed has no policies to disagree about."""
    path = Path(__file__).parents[1] / "tools/generate_demo_data.py"
    spec = importlib.util.spec_from_file_location("demo_data_strip_policies", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    module.SOURCE = tmp_path / "captures"
    directory = module.SOURCE / "persona"
    directory.mkdir(parents=True)
    checks = facts()
    (module.SOURCE / "index.json").write_text(json.dumps({"personas": ["persona"]}))
    (directory / "describe.json").write_text(frontend(checks).describe_json())

    def capture(mode, status, policies):
        connected = {"event": "connected", "baseline_mode_checks": checks,
                     "strip_policies": policies}
        events = json.dumps({"events": [connected, {"event": "result"}]})
        (directory / f"{mode}.spot.json").write_text(json.dumps({
            "request": {"mode": mode, "workflow": "spot", "options": {}, "layout": {}},
            "result": {"status": status,
                       "patches": [{"observation": {"raw_backend_output": events}}]}}))

    policy = {"strip": {"minimum": 2}}
    if routes == "none":
        capture("emissive_spot", "failed", policy)
        with pytest.raises(SystemExit, match="no completed default route"):
            module.generate()
        return
    capture("emissive_spot", "completed", policy)
    capture("emissive_tele", "completed", policy if routes == "agree" else {})
    if routes == "disagree":
        with pytest.raises(SystemExit, match="disagree on strip policies"):
            module.generate()
    else:
        persona = json.loads(module.generate())["personas"]["persona"]
        assert persona["strip_policies"] == policy
        assert sorted(persona["routes"]) == ["emissive_spot", "emissive_tele"]
