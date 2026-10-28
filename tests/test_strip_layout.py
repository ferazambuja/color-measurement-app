"""Driver-consumed strip fields; actual pinned formatters, no hardware or transport."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from zipfile import ZipFile

import pytest
from conftest import supplied_stub

from color_measurement.argyll_capability import CAPABILITY2, mode_word
from color_measurement.instrument import Instrument, InstrumentError
from color_measurement.instrument_frontend import InstrumentFrontend
from color_measurement.strip_layout import validate_layout, validate_policies, workflow_fields

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def native_policy(argyll_archive, tmp_path_factory):
    cc = shutil.which("cc")
    if not cc:
        pytest.skip("C compiler unavailable for pinned strip formatting proof")
    folder = tmp_path_factory.mktemp("strip-policy")

    def enum(text, name):
        end = text.index("} " + name + ";") + len("} " + name + ";")
        return text[text.rindex("typedef enum {", 0, end):end]

    with ZipFile(argyll_archive) as z:
        source = {name: z.read(f"Argyll_V3.5.0/spectro/{name}").decode()
                  for name in ("inst.h", "insttypes.h", "dtp20.c", "dtp41.c", "dtp51.c")}
        (folder / "License2.txt").write_bytes(z.read("Argyll_V3.5.0/License2.txt"))
    code = ('#include <stdio.h>\n#include <string.h>\n'
            + enum(source["inst.h"], "inst_mode") + "\n"
            + enum(source["insttypes.h"], "devType") + "\ntypedef devType instType;\n"
            + 'typedef struct { instType dtype; inst_mode mode; void *log; } inst;\n'
            + 'typedef inst dtp20; typedef inst dtp41;\n#define a1logw(...) ((void)0)\n'
            + '#include "' + str(ROOT / "tools/native_argyll/strip_policy.h") + '"\n')
    for driver in ("dtp20", "dtp41", "dtp51"):
        text = source[f"{driver}.c"]
        marker = "static void\nbuild_strip("
        assert text.count(marker) == 1
        start = text.index(marker)
        body = text[start:text.index("\n}", start) + 2]
        code += body.replace("build_strip(", f"{driver}_build_strip(") + "\n"
    code += r'''
int main(void) {
    inst meters[] = {{instDTP20,0,0}, {instDTP41,0,0}, {instDTP51,0,0},
                     {instI1Pro2,0,0}, {instI1Pro3,0,0}, {instColorMunki,0,0},
                     {instSpectroScan,0,0}};
    int i; char buffer[200];
    for (i = 0; i < 7; i++) {
        fputs("{\"native\":true", stdout);
        cm_print_strip_policies(stdout, &meters[i]); puts("}");
    }
    dtp20_build_strip(&meters[0], buffer, "unused", 99, "unused", 99, 10, 2.5, 123);
    *strchr(buffer, '\r') = 0; puts(buffer);
    dtp41_build_strip(&meters[1], buffer, "unused", 999, "unused", 99, 10, 2.5, 123);
    *strchr(buffer, '\r') = 0; puts(buffer);
    meters[1].mode = inst_mode_trans_strip;
    dtp41_build_strip(&meters[1], buffer, "unused", 4, "unused", 99, 99.99, 0, 9999);
    *strchr(buffer, '\r') = 0; puts(buffer);
    dtp51_build_strip(buffer, "STRIP", 99, "B", 99);
    *strchr(buffer, '\r') = 0; puts(buffer);
    dtp41_build_strip(&meters[1], buffer, "unused", 4, "unused", -1, 6, -0.0, -0.0);
    *strchr(buffer, '\r') = 0; puts(buffer);
    dtp41_build_strip(&meters[1], buffer, "unused", 4, "unused", -1, 6,
                     cm_strip_wire_length(-0.0), cm_strip_wire_length(-0.0));
    *strchr(buffer, '\r') = 0; puts(buffer);
    return 0;
}
'''
    (folder / "proof.c").write_text(code)
    executable = folder / "proof"
    subprocess.run([cc, "-std=c99", str(folder / "proof.c"), "-o", str(executable)],
                   capture_output=True, check=True)
    rows = subprocess.run([str(executable)], capture_output=True, check=True,
                          text=True).stdout.splitlines()
    policies = dict(zip(("dtp20", "dtp41", "dtp51", "i1pro2", "i1pro3", "munki", "ss"),
                        (validate_policies(json.loads(row)["strip_policies"])
                         for row in rows[:7]), strict=True))
    return policies, rows[7:]


def test_policy_limits_against_actual_driver_wire_format(native_policy):
    policies, commands = native_policy
    assert commands == ["05499DS", "9991000025008DS", "00499990000089999DS",
                        "STRIP  1000F1000000B  589999000",
                        "0040600-00008-000DS", "00406000000080000DS"]
    assert [policies[name]["reflection"]["max_patches"] for name in
            ("dtp20", "dtp41", "dtp51", "i1pro2", "i1pro3", "munki", "ss")] == [
                99, 403, 99, 4096, 4096, 4096, 0]


def test_dtp41_count_is_bounded_by_spectral_reply_buffer(argyll_archive, native_policy):
    # A command's three-digit field is not its response capacity. This is a
    # source-derived software ceiling, not proof of physical strip acceptance.
    with ZipFile(argyll_archive) as z:
        source = z.read("Argyll_V3.5.0/spectro/dtp41.c").decode()
    capacity = int(re.search(r"#define MAX_RD_SIZE\s+(\d+)", source)[1])
    bands, stride = map(int, re.search(r"strlen\(tp\) < \((\d+) \* (\d+) - 1\)", source).groups())
    assert '"0403TS\\r", buf, MAX_RD_SIZE' in source
    assert "tp += strlen(tp) + 1;" in source  # separator per patch
    assert "XCALSTD_NEEDED" in source and "|| p->custfilt_en" in source
    count = native_policy[0]["dtp41"]["reflection"]["max_patches"]
    assert capacity - count * bands * stride == 56
    assert (count + 1) * bands * stride > capacity


@pytest.mark.parametrize("persona,mode,required", [
    ("dtp20", "reflective_strip", {"patch_width_mm", "gap_width_mm"}),
    ("dtp41", "reflective_strip", {"patch_width_mm", "gap_width_mm"}),
    ("dtp41", "transmission_strip", {"patch_width_mm", "gap_width_mm", "trailer_width_mm"}),
    ("dtp51", "reflective_strip", {"guide"}),
    ("i1pro2", "reflective_strip", set()),
    ("i1pro3", "reflective_strip", set()),
    ("munki", "reflective_strip", set()),
])
def test_required_operands_follow_native_policy(native_policy, persona, mode, required):
    policy = native_policy[0][persona]
    fields = workflow_fields(policy, mode, 0)
    assert set(fields["required_layout_keys"]) == required
    layout = {"patch_width_mm": 10, "gap_width_mm": 0, "trailer_width_mm": 0, "guide": 0}
    layout = {k: v for k, v in layout.items() if k in required}
    validate_layout(layout, fields)
    for key in required:
        with pytest.raises(ValueError, match="requires"):
            validate_layout({k: v for k, v in layout.items() if k != key}, fields)
    assert "scan_tolerance" not in fields["layout_keys"]


@pytest.mark.parametrize("key,value", [
    ("patch_width_mm", 0), ("patch_width_mm", 100), ("patch_width_mm", float("inf")),
    ("gap_width_mm", -1), ("gap_width_mm", float("nan")), ("gap_width_mm", None),
    ("patch_width_mm", True), ("guide", 15), ("trailer_width_mm", 3),
    ("direction", "reverse"),
])
def test_unused_missing_or_invalid_strip_fields_refuse(native_policy, key, value):
    fields = workflow_fields(native_policy[0]["dtp41"], "reflective_strip", 0)
    with pytest.raises(ValueError):
        validate_layout({"patch_width_mm": 10, "gap_width_mm": 0, key: value}, fields)


def test_defaults_are_not_fabricated_target_geometry(native_policy):
    fields = workflow_fields(native_policy[0]["dtp51"], "reflective_strip", 0)
    layout = {"guide": 0}
    validate_layout(layout, fields)
    assert layout == {"guide": 0}
    for name in ("strip_name", "pass_name"):
        f = next(f for f in fields["layout_fields"] if f["key"] == name)
        assert f["default"] == {"strip_name": "STRIP", "pass_name": "1"}[name]
        for value in ("", "x" * (f["max_bytes"] + 1), "é", "x\r"):
            with pytest.raises(ValueError):
                validate_layout({"guide": 0, name: value}, fields)


def test_actual_ui_shape_is_accepted_only_for_consuming_driver(native_policy):
    # Actual product callback captured 2026-09-28: four named patches, 10/2.5 mm,
    # no invented guide, trailer or direction. No MATLAB echo rewriting here.
    layout = {"patch_width_mm": 10, "gap_width_mm": 2.5}
    for persona, allowed in (("dtp41", True), ("dtp51", False), ("i1pro3", False)):
        frontend = InstrumentFrontend(Instrument("/never-opened", "instDTP41"),
            mode_capabilities=mode_word("reflective_strip", spectral=False),
            trigger_capabilities=CAPABILITY2["prog_trig"], filter_capabilities=0,
            strip_policies=native_policy[0][persona], provenance="simulated")
        descriptor = json.loads(frontend.describe_json())
        mode = next(m for m in descriptor["modes"] if m["mode"] == "reflective_strip")
        workflow = mode["workflows"][0]
        assert workflow["layout_keys"] == workflow_fields(
            native_policy[0][persona], "reflective_strip", 0)["layout_keys"]
        request = json.dumps({"schema_version": 1, "device_id": descriptor["device_id"],
                              "capability_id": descriptor["capability_id"],
                              "mode": "reflective_strip", "workflow": "strip",
                              "patch_ids": ["A", "B", "C", "D"], "options": {},
                              "layout": layout})
        if allowed:
            validated, _, _ = frontend._validate_request(request)
            assert validated["layout"] == layout
        else:
            with pytest.raises(ValueError):
                frontend._validate_request(request)


def test_unknown_policy_does_not_advertise_acquisition(native_policy):
    assert workflow_fields(native_policy[0]["ss"], "reflective_strip", 0) is None
    assert workflow_fields({}, "reflective_strip", 0) is None


def test_strip_driver_census_and_unused_scanner_operands(argyll_archive):
    with ZipFile(argyll_archive) as z:
        sources = {Path(name).name: z.read(name).decode("latin1") for name in z.namelist()
                   if name.startswith("Argyll_V3.5.0/spectro/") and name.endswith(".c")}
    assignments = {}
    for filename, text in sources.items():
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)
        handlers = re.findall(r"p->read_strip\s*=\s*(\w+)\s*;", code)
        if handlers:
            assignments[filename] = handlers
    assert assignments == {
        "inst.c": ["read_strip"],  # upstream's unsupported default
        **{f"{d}.c": [f"{d}_read_strip"]
           for d in ("dtp20", "dtp41", "dtp51", "ss", "i1pro", "i1pro3", "munki")}}
    for driver in ("i1pro", "i1pro3", "munki"):
        text = sources[f"{driver}.c"]
        start = text.index(f"\n{driver}_read_strip(")
        body = text[text.index("{", start):text.index("\n}", start) + 2]
        body = re.sub(r"/\*.*?\*/|//[^\n]*", "", body, flags=re.S)
        assert not re.search(r"\b(name|pname|sguide|pwid|gwid|twid)\b", body)
        assert "npatch" in body and "vals" in body


@pytest.mark.parametrize("persona,mode,values,stage", [
    ("dtp41", "reflective_strip", {"patch_count": 404}, "strip_patch_count"),
    ("dtp41", "reflective_strip", {"patch_count": 4}, "strip_widths"),
    ("dtp41", "reflective_strip", {"patch_count": 4, "patch_width_mm": 100,
                                  "gap_width_mm": 0}, "strip_widths"),
    ("dtp51", "reflective_strip", {"patch_count": 4}, "strip_guide"),
    ("dtp51", "reflective_strip", {"patch_count": 4, "guide": 100}, "strip_guide"),
    ("dtp51", "reflective_strip", {"patch_count": 4, "guide": 15,
                                  "strip_name": "x\r"}, "strip_names"),
])
def test_native_boundary_refuses_before_calibration_or_read(persona, mode, values, stage):
    helper = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    meter = Instrument(helper, f"inst{persona.upper()}", environment={
        **os.environ, "CM_CHART_PERSONA": persona, "CM_CHART_FIXTURE": "success"},
                expected_helper_sha256=hashlib.sha256(
                    Path(helper).read_bytes()).hexdigest())
    with pytest.raises(InstrumentError) as error:
        meter.measure_strip(mode, spectral=False, calibrate=True, **values)
    assert error.value.stage == stage
    assert not {"strip_patch", "calibration_ready", "instruction_required"} & {
        event["event"] for event in error.value.events}


def test_native_canonicalizes_only_outgoing_zero_geometry():
    helper = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    meter = Instrument(helper, "instDTP41", environment={
        **os.environ, "CM_CHART_PERSONA": "dtp41", "CM_CHART_FIXTURE": "zero_geometry"},
                expected_helper_sha256=hashlib.sha256(
                    Path(helper).read_bytes()).hexdigest())
    result = meter.measure_strip("transmission_strip", patch_count=4, spectral=False,
        patch_width_mm=6, gap_width_mm=-0.0, trailer_width_mm=-0.0)
    assert len(result.patches) == 4  # the stand-in rejects either signed wire zero


def test_native_feed_instruction_and_unused_widths():
    helper = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    meter = Instrument(helper, "instDTP51", environment={
        **os.environ, "CM_CHART_PERSONA": "dtp51", "CM_CHART_FIXTURE": "success"},
                expected_helper_sha256=hashlib.sha256(
                    Path(helper).read_bytes()).hexdigest())
    prompts = []

    def cancel(prompt):
        prompts.append(prompt)
        return "cancel"

    with pytest.raises(InstrumentError) as error:
        meter.measure_strip("reflective_strip", patch_count=4, guide=15,
                            spectral=False, calibrate=True, acknowledge=cancel)
    assert error.value.stage == "cancelled"
    prompt = next(p for p in prompts if p.get("instruction") == "strip_position")
    assert prompt["effective_trigger"] == "instrument_switch"
    assert "feed the strip" in prompt["trigger_guidance"]
    assert "button" not in prompt["trigger_guidance"]
    assert not any(e["event"] == "strip_patch" for e in error.value.events)


def test_manual_scan_instruction_uses_selected_row_and_actual_trigger():
    from color_measurement.operator_setup import mode_setup
    helper = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    meter = Instrument(helper, "instI1Pro2", environment={
        **os.environ, "CM_CHART_PERSONA": "i1pro2", "CM_CHART_FIXTURE": "success"},
                expected_helper_sha256=hashlib.sha256(
                    Path(helper).read_bytes()).hexdigest())
    prompts = []

    def acknowledge(prompt):
        if prompt.get("instruction") == "strip_position":
            prompts.append(prompt)
            return "cancel"
        return "acknowledge"

    with pytest.raises(InstrumentError, match="cancelled"):
        meter.measure_strip("reflective_strip", patch_count=4, calibrate=True,
                            acknowledge=acknowledge)
    assert prompts[0]["effective_trigger"] == "programmatic"
    assert "Move the instrument" in prompts[0]["trigger_guidance"]
    assert "selected row" in prompts[0]["trigger_guidance"]
    for model in ("X-Rite i1 Pro 2", "X-Rite i1 Pro 3"):
        setup = mode_setup(model, "ref_strip")
        assert "selected row" in setup["text"]
        assert "row #1" in setup["quote"]  # the original manual excerpt is preserved


def test_product_strip_request_reaches_native_operands_without_echo_rewriting():
    helper = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    meter = Instrument(helper, "instDTP41", environment={
        **os.environ, "CM_CHART_PERSONA": "dtp41", "CM_CHART_FIXTURE": "ui_strip"},
                expected_helper_sha256=hashlib.sha256(
                    Path(helper).read_bytes()).hexdigest())
    binding = next(e for e in meter.measure("reflective_spot").events
                   if e["event"] == "connected")
    front = InstrumentFrontend(meter, mode_capabilities=binding["mode_capabilities"],
        trigger_capabilities=binding["trigger_capabilities"],
        filter_capabilities=binding["filter_capabilities"],
        baseline_mode_checks=binding["baseline_mode_checks"],
        strip_policies=binding["strip_policies"], provenance="simulated")
    descriptor = json.loads(front.describe_json())
    # Exact product callback shape/operands from the native UI capture, with only
    # the current binding identifiers substituted. The stub checks the native
    # read_strip arguments, including omission sentinels; it cannot echo a layout.
    names = ["Paper white", "Neutral grey", "Cyan patch", "Magenta patch"]
    op = front.prepare(json.dumps({"schema_version": 1, "device_id": descriptor["device_id"],
        "capability_id": descriptor["capability_id"], "mode": "reflective_strip",
        "workflow": "strip", "patch_ids": names,
        "options": {"xyz_only": False, "xcalstd": "none"},
        "layout": {"patch_width_mm": 10, "gap_width_mm": 2.5},
        "event_contract": "retained_patch_v1"}))
    op.dispatch()
    try:
        events = []
        deadline = time.monotonic() + 10
        while not op.done and time.monotonic() < deadline:
            raw = op.poll_event_json()
            if not raw:
                time.sleep(0.005)
                continue
            event = json.loads(raw)
            events.append(event)
            if event["kind"] == "instruction_required":
                op.respond(op.operation_id, event["payload"]["request_id"], "accept")
        assert op.done
        result = json.loads(op.result_json())
        assert result["status"] == "completed", result.get("error")
        assert [p["patch_id"] for p in result["patches"]] == names
        prompt = next(e["payload"] for e in events if e["kind"] == "instruction_required"
                      and e["payload"]["kind"] == "strip_position")
        assert "starts the read immediately" in prompt["message"]
        assert "the instrument feeds the strip" in prompt["message"]
    finally:
        op.close()


TEXT_FIELD = {"key": "strip_name", "label": "Strip", "type": "text", "required": False,
              "default": None, "help": "Name", "max_bytes": 32, "encoding": "ascii_printable"}


def _policies(**changes):
    policy = {"max_patches": 0, "requirement": None, "layout_fields": []}
    policies = {name: dict(policy) for name in ("reflection", "transmission", "emission")}
    policies["reflection"].update(changes)
    return policies


@pytest.mark.parametrize("policies, message", [
    ({k: v for k, v in _policies().items() if k != "emission"}, "Invalid native strip policies"),
    (_policies(max_patches=4097), "Invalid native strip operand policy"),
    (_policies(requirement=5), "Invalid native strip operand policy"),
    (_policies(layout_fields={}), "Invalid native strip operand policy"),
    (_policies(layout_fields=[{"key": "guide", "type": "boolean"}]), "Invalid strip field"),
    (_policies(layout_fields=[TEXT_FIELD | {"max_bytes": 129}]), "Invalid strip text bounds"),
    (_policies(layout_fields=[TEXT_FIELD | {"required": True, "default": "x"}]),
     "Required strip fields cannot have guessed defaults"),
    (_policies(layout_fields=[TEXT_FIELD, TEXT_FIELD]), "Invalid or duplicate strip field"),
])
def test_malformed_native_strip_policies_are_refused(policies, message):
    """Every policy the compiled fixture passes is valid, so until now no check in
    validate_policies had a case that could fire it."""
    with pytest.raises(ValueError, match=message):
        validate_policies(policies)


NUMERIC_FIELD = {"key": "patch_width_mm", "label": "Patch width", "type": "number",
                 "required": False, "default": None, "help": "Width",
                 "minimum": 0, "maximum": 100, "exclusive_minimum": False, "unit": "mm"}


@pytest.mark.parametrize("changes", [
    {"minimum": -1}, {"minimum": float("nan")}, {"minimum": True},
    {"maximum": float("inf")}, {"minimum": 2, "maximum": 1},
    {"exclusive_minimum": 1}, {"unit": None},
])
def test_invalid_numeric_strip_policy_bounds_are_refused(changes):
    # All other policy and field facts are valid, so only the numeric guard rejects.
    policies = _policies(layout_fields=[NUMERIC_FIELD | changes])
    with pytest.raises(ValueError, match="Invalid strip numeric bounds"):
        validate_policies(policies)
