"""Source-bound offline probes for the calibration resync's changed branches.

The large white/HR methods and the shared list constructor run with scripted
helpers; no device, transport or EEPROM method is linked. Shared identifier
readers use temporary synthetic files, without a complete calibration loader. Small
error-exit probes exercise the exact branches, not their physical reachability.
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
from collections import Counter
from pathlib import Path

from native_argyll import flash_capture, sanitizers
from native_argyll.disptype_list_release import _shared
from native_argyll.proof_inputs import fixture_manifest, production_text
from native_argyll.spyder_calibration_slots import _define, _enum, _function, digest

from color_measurement.native_receipt import native_worker_name

HERE = Path(__file__).parent


# Failure names describe the original defect, not just a nonzero probe exit.
BASELINE_FAILURES = {
    "plot-fillins": Counter({"strip-reflective plot matrix retained": 1,
                              "polarized-strip-reflective plot matrix retained": 1}),
    "hr": Counter({"refusing mode 1 left valid": 2, "refusing mode 7 left valid": 2,
                   "refusing mode 0 left valid": 1, "emissive hi-res factors not installed": 2,
                   "fine-tune negative band preserves state": 1,
                   "fine-tune underflowed factor preserves state": 1,
                   "fine-tune zero divisor preserves state": 1,
                   "fine-tune overflow preserves state": 1}),
    "state": Counter({"completed offset removed; failed offset retained": 3,
                      "unsupported calibration refused": 1, "zero invalidates stored rate": 1,
                      "invalid rate preserves state": 1,
                      "stored retrieval writes successful rate": 1}),
    "exposure": Counter({"non-positive max -1e+06": 4, "non-positive max -3": 4,
                         "non-positive max -1e-300": 4, "non-positive max -0": 2,
                         "non-positive max 0": 2}),
    "curve-i1pro": Counter({"shifted maxval free": 2, "partial allocation retained": 2}),
    "curve-i1pro3": Counter({"partial allocation retained": 2}),
    "matrix-i1pro": Counter({"partial matrix persisted": 2, "same-object retry incomplete": 2}),
    "matrix-i1pro3": Counter({"partial matrix persisted": 2, "same-object retry incomplete": 2}),
    "cleanup": Counter({**{f"exit {i} restores mode": 1 for i in range(12)},
                        "four wavelength vectors released": 1}),
    "debug": Counter({"quiet level 0 touches diagnostics": 1,
                      "quiet level 2 touches diagnostics": 1}),
    "selectors": Counter({"unterminated selector": 1}),
    "custom-filter": Counter({name: 1 for name in (
        "two-boundary", "three-boundary", "two-coarse", "three-coarse")}),
    **{"converter-" + family: Counter({name + suffix: 1
       for name in ("nonfinite-input", "reflectance-scale-overflow", "integration-overflow",
                    "xyz-percent-overflow", "post-xrga-overflow", "post-filter-overflow")
       for suffix in ("", "-second")}) for family in ("i1pro", "i1pro3", "munki")},
    "scaled-munki": Counter({name: 1 for name in (
        "caller2-overflow", "caller2-overflow-second",
        "caller2a-overflow", "caller2a-overflow-second")}),
    "followup": Counter({"helper entered with stale validity": 84,
                         "refused white calibration invalid": 12, "failure restores mode": 2,
                         "unusable white factors refused": 2, "device maximum validated": 4,
                         "invalid saved exposure never reaches conversion": 5,
                         "invalid restore clears both calibrations and keeps the initial time": 5,
                         "i1pro filter error described": 1, "i1pro3 filter error described": 1,
                         "munki filter error described": 1}),
    "calf": Counter({name: 1 for name in (
        "zero: validity or explicit refusal",
        "negative: untrusted length must not enter fatal allocation",
        "negative: read mode restored", "negative: validity or explicit refusal",
        "negative: refuse before allocation", "truncated: refuse before allocation",
        "no-terminator: validity or explicit refusal",
        "embedded-terminator: validity or explicit refusal",
        "over-cap: validity or explicit refusal", "over-cap: refuse before allocation",
        "eof-short: refuse before allocation")}),
    "white-factors": Counter({"nan_band: returned 0, expected -1": 2,
                              **{name + ": returned 0, expected -1": 1 for name in (
        "inf_band", "negative_reference", "zero_reference", "hires_nan_band",
        "hires_negative_reference", "hires_zero_reference", "zero_white",
        "negative_white", "hires_zero_white")}}),
    "polarized": Counter({name + ": count conversion out of int range": 1
                          for name in ("tiny_white", "zero_white", "nan_white")}),
    "agreement": Counter({f"{value}/{zero} divides by zero": 1
                          for value in ("1", "-1", "0.7", "1e-300", "1e+300")
                          for zero in ("0", "-0")}),
    "dark-completion": Counter({"successful inner dark capture is valid": 1,
                               "successful inner dark metadata matches capture": 1,
                               "failed inner dark remains invalid": 1,
                               "successful dark remains valid after white-stage failure": 3,
                               "failed white stage does not publish white calibration": 1}),
    "gain-switch": Counter({"the final white subtracted the new gain's dark": 2,
                           "the other gain's dark is not left valid": 1,
                           "a switch without a dark for the new gain leaves none valid": 1,
                           "the dim-white warning judges the final white": 2}),
    "dtp41": Counter({name: 1 for name in (
        "sample activation fails", "sample after failure", "strip activation fails",
        "strip after failure",
        "no patches", "four-digit count", "negative patch", "patch of 100 mm",
        "patch rounding to 100.00", "NaN patch", "negative gap", "gap rounding to 100.00",
        "NaN gap", "negative trailer", "NaN trailer", "negative zero widths",
        "negative zero trailer", "calibration after failed transmission activation",
        "transmission white asked after failed activation",
        "transmission white done after failed activation",
        "transmission white by name after failed activation")}),
}
for _family in ("i1pro", "munki", "i1pro3"):
    BASELINE_FAILURES["average-" + _family] = Counter({
        _family + " " + case + ": inconsistent 0, expected 1": 1
        for case in ("nan_cell", "inf_cell", "overflow", "row_overflow",
                     *(("nan_shielded",) if _family != "i1pro3" else ()))
    })
BASELINE_FAILURES["white"] = Counter({
    f"{case}: call {call} {kind} stale": 1
    for case, calls in (
        ("refl spot in-range", (1, 2)), ("refl spot adapt", (1, 2, 3, 4, 5)),
        ("trans spot adapt", (1, 2, 3, 4)), ("refl scan in-range", (1, 2, 3)),
        ("refl scan SAT gain1", (2, 3, 4)), ("trans scan fixed ok", (1,)),
        ("trans spot in-range", (1,)))
    for call in calls for kind in ("error", "variant")
})
BASELINE_FAILURES["white"].update({
    "trans spot adapt no-idark: unforced exit stale": 1,
    "trans scan adapt no-idark: unforced exit stale": 1,
})


def _validate_failures(probe: str, stdout: str, *, baseline: bool) -> None:
    """Require the exact defect names, including repeated observations."""
    failed = Counter()
    current = None
    for line in stdout.splitlines():
        scenario = re.match(r"\[(.*?)\] success", line)
        if scenario:
            current = scenario[1]
        if probe == "white" and "STALE VALID" in line:
            call = re.match(r"\s*fail call (\d+) \((error|variant)\)", line)
            if call is None or current is None:
                raise AssertionError("Resync white failure lacks its scenario")
            failed[f"{current}: call {call[1]} {call[2]} stale"] += 1
        if "FAIL" not in line:
            continue
        match = re.fullmatch(r"\s*FAIL(?:\(compiled\))?:?\s+(.+)", line)
        if match is None:
            raise AssertionError(f"Resync unrecognized failure at {probe}: {line}")
        name = match[1]
        if probe == "white":
            if current is None:
                raise AssertionError("Resync white failure lacks its scenario")
            name = current + ": " + name
        elif probe == "exposure":
            case = re.fullmatch(r"(non-positive max \S+) selected .+", name)
            if case is None:
                raise AssertionError(f"Resync unrelated exposure failure: {name}")
            name = case[1]
        elif probe == "dtp41":
            case, separator, observation = name.partition(": ")
            number = r"0x[0-9a-f]+"
            acquisition = (rf"returned {number} \(want {number}\), sent \"[^\"]*\" "
                           rf"\(want \"[^\"]*\"\), mode {number}/{number} "
                           rf"\(want {number}/{number}\), commands \d+")
            calibration = (rf"returned {number} \(want {number}\), calibration {number} "
                           rf"\(want {number}\), condition {number} \(want {number}\), "
                           r"needs calibration \d+ \(want \d+\), commands \d+")
            calibrating = case.startswith(("calibration ", "transmission white "))
            grammar = calibration if calibrating else acquisition
            if not separator or not re.fullmatch(grammar, observation):
                raise AssertionError(f"Resync unrelated DTP41 failure: {name}")
            name = case
        failed[name] += 1
    expected = BASELINE_FAILURES[probe] if baseline else Counter()
    if failed != expected:
        raise AssertionError(f"Resync {'baseline failures' if baseline else 'behavior'} differs "
                             f"at {probe}: {dict(failed)}; expected {dict(expected)}")
    totals = re.findall(r"(\d+) of \d+ checks failed|(?<!\d)(\d+) failures", stdout)
    if any(int(a or b) != sum(failed.values()) for a, b in totals):
        raise AssertionError(f"Resync failure count disagrees with named checks at {probe}")


def _block(text: str, marker: str) -> str:
    """Exact balanced statement following a unique marker, ignoring C literals."""
    if text.count(marker) != 1:
        raise ValueError(f"Ambiguous resync probe marker: {marker}")
    start = text.index(marker)
    opening = text.index("{", start)
    depth = 0
    for token in re.finditer(
        r'/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]',
        text[opening:],
    ):
        if token[0] == "{":
            depth += 1
        elif token[0] == "}":
            depth -= 1
            if depth == 0:
                return text[start : opening + token.end()] + "\n"
    raise ValueError(f"Unclosed resync probe block: {marker}")


def _codes(source: Path, template: str, *, hr: bool = False) -> str:
    inst = (source / "spectro/inst.h").read_text()
    text = "".join(
        _enum(inst, n) for n in ("inst_code", "inst_cal_type", "inst_cal_cond", "inst_calc_id_type")
    )
    if "I1PRO_" in template:
        h = (source / "spectro/i1pro_imp.h").read_text()
        text += "typedef int i1pro_code;\n"
        names = set(re.findall(r"\bI1PRO_[A-Z_0-9]+", template))
        for name in sorted(names):
            text += _define(h, name)
        if hr:
            text += _enum(h, "i1p_mode")
            c = (source / "spectro/i1pro_imp.c").read_text()
            text += _define(c, "EMIS_SCALE_FACTOR") + _define(c, "AMB_SCALE_FACTOR")
            text += _define((source / "xicc/xspect.h").read_text(), "XSPECT_MAX_BANDS")
        else:
            h = (source / "spectro/insttypes.h").read_text()
            text += _enum(h, "devType") + "typedef devType instType;\n"
    return text


def prove(
    source: Path,
    before: dict[str, str],
    output: Path,
    *,
    compiler: str = "cc",
    sanitize: bool = False,
    groups: tuple[str, ...] | None = None,
) -> dict:
    """Authenticate frozen failures, then probe them against current production source."""
    selected = {"white", "hr", "state", "misc", "cleanup", "selectors", "followup", "calf",
                "custom-filter", "converter-i1pro", "converter-i1pro3", "converter-munki",
                "output", "scaled-munki", "black-debug", "plot-debug", "plot-fillins",
                *PACKAGE_GROUPS}
    if groups is not None:
        if not groups or set(groups) - selected:
            raise ValueError("Unknown or empty native resync probe groups")
        selected = set(groups)
    expected = fixture_manifest()["proofs"]["native_resync"]
    if set(before) != set(expected):
        raise ValueError("Native resync frozen baseline paths differ from manifest")
    for path, entry in expected.items():
        if digest(before[path].encode()) != entry["sha256"]:
            raise ValueError(f"Native resync frozen baseline digest differs: {path}")
    after = {p: production_text(source, p) for p in before}
    output.mkdir(parents=True, exist_ok=False)
    report = dict(
        status="incomplete",
        hardware_tested=False,
        transport_linked=False,
        compiled_source_sha256={p: digest(t.encode()) for p, t in after.items()},
        before_sha256={p: digest(t.encode()) for p, t in before.items()},
        runner_sha256=digest(Path(__file__).read_bytes()),
        sanitize=sanitize,
        results={},
        groups=sorted(selected),
        limitations=[
            "Scripted helpers establish control flow and cleanup, not optical validity.",
            "White-save hooks do not read or write calibration caches.",
            "Error branches are driven directly; full hardware reachability is not proven.",
            "Installed correction listings are synthetic and use a poisoned allocator.",
        ],
    )

    def run(
        label: str,
        code: str,
        files: dict[str, str],
        *,
        args: tuple[str, ...] = (),
        expect_failure: bool = False,
        include_dirs: tuple[Path, ...] = (),
        compile_defs: tuple[str, ...] = (),
        compile_flags: tuple[str, ...] = (),
    ) -> str:
        directory = output / label
        directory.mkdir(parents=True)
        for name, text in {"probe.c": code, **files}.items():
            (directory / name).write_text(text)
        exe = directory / native_worker_name("probe")
        cmd = [
            compiler,
            "-std=gnu99",
            "-O1",
            "-g",
            *sanitizers.flags(sanitize),
            *compile_flags,
            *compile_defs,
            "-I" + str(directory),
            *("-I" + str(path) for path in include_dirs),
            str(directory / "probe.c"),
            "-lm",
            "-o",
            str(exe),
        ]
        build = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        (directory / "compile.log").write_text(build.stdout + build.stderr)
        try:
            build.check_returncode()
        except subprocess.CalledProcessError as error:
            report["results"][label] = dict(
                compile_returncode=build.returncode,
                compile_log=str(directory / "compile.log"),
                source_sha256=digest(code.encode()),
                inputs_sha256={n: digest(t.encode()) for n, t in files.items()},
            )
            raise RuntimeError(
                f"Resync compilation failed at {label}; see {directory / 'compile.log'}"
            ) from error
        run = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=30)
        (directory / "run.log").write_text(run.stdout + run.stderr)
        sanitizers.refuse(run.stderr, label)
        if run.returncode != (1 if expect_failure else 0):
            raise AssertionError(f"Resync behavior differs at {label}; see {directory / 'run.log'}")
        _validate_failures(label.split("/", 1)[1], run.stdout, baseline=expect_failure)
        report["results"][label] = dict(
            returncode=run.returncode,
            source_sha256=digest(code.encode()),
            stdout=run.stdout,
            inputs_sha256={n: digest(t.encode()) for n, t in files.items()},
        )
        return run.stdout

    baseline_converter_controls = {}
    try:
        for label, texts in (("baseline", before), ("repaired", after)):
            failed = label == "baseline"
            cal = _function(texts["spectro/i1pro_imp.c"], "i1pro_imp_calibrate")
            white_block = _block(cal, "\t\tif ((*calt & (inst_calt_ref_white")
            template = (HERE / "native_resync_white_probe.c").read_text()
            codes = _codes(source, template + white_block)
            if "white" in selected:
                run(
                    label + "/white",
                    template,
                    {"block.inc": white_block, "codes.inc": codes},
                    expect_failure=failed,
                )
            template = (HERE / "native_resync_hr_probe.c").read_text()
            c = texts["spectro/i1pro_imp.c"]
            hr = _function(c, "i1pro_create_hr_calfactors")
            white = _function(c, "i1pro_compute_white_cal")
            if "hr" in selected:
                run(
                    label + "/hr",
                    template,
                    {
                        "hr.inc": hr,
                        "white.inc": white,
                        "codes.inc": _codes(source, template + hr + white, hr=True),
                    },
                    expect_failure=failed,
                )
                if not failed and sanitize:
                    # Isolate the fine-tune divisor: the full high-resolution
                    # probe also exercises unrelated stock transmission floors.
                    run(
                        label + "/hr-zero",
                        template,
                        {
                            "hr.inc": hr,
                            "white.inc": white,
                            "codes.inc": _codes(source, template + hr + white, hr=True),
                        },
                        args=("fine-tune-zero",),
                        compile_flags=sanitizers.FLOAT_DIVIDE_BY_ZERO,
                    )
            if "output" in selected:
                i1pro = texts["spectro/i1pro_imp.c"]
                i1pro_header = (source / "spectro/i1pro_imp.h").read_text()
                i1pro_probe = flash_capture._i1pro_saturation_probe(i1pro, i1pro_header)
                for case in ("equal", "scale_flash", "scale_spot"):
                    run(label + "/output-i1pro-" + case, i1pro_probe, {}, args=(case,))
                i1pro3 = texts["spectro/i1pro3_imp.c"]
                i1pro3_probe = flash_capture._probe_source(i1pro3)
                includes = tuple(source / name for name in
                                 ("h", "numlib", "rspl", "cgats", "xicc", "spectro"))
                config = {"sa_config.h": (source / "h/aconfig.h").read_text()}
                for case in ("sat10", "scale_flash"):
                    run(label + "/output-i1pro3-" + case, i1pro3_probe, config,
                        args=(case,), include_dirs=includes,
                        compile_defs=(("-DSALONEINSTLIB", "-DNT", "-DEN_LIBUSB0")
                                      if platform.system() == "Windows" else
                                      ("-DSALONEINSTLIB", "-DUNIX")))
            for family, selector in (("i1pro", 1), ("i1pro3", 2), ("munki", 3)):
                group = "converter-" + family
                if group not in selected:
                    continue
                text = texts[f"spectro/{family}_imp.c"]
                header = (source / f"spectro/{family}_imp.h").read_text()
                codes = "".join(_define(header, name) for name in
                                (family.upper() + "_OK", family.upper() + "_RD_READINCONS",
                                 family.upper() + "_INT_CIECONVFAIL"))
                codes += _define((source / "xicc/xspect.h").read_text(),
                                 "XSPECT_MAX_BANDS")
                weights = re.search(r"static double opt_adj_weights\[21\] = \{.*?\};",
                                    text, re.S)
                if weights is None:
                    raise ValueError(f"{family} optical weight source changed")
                helper_name = "cm_" + family + "_patch_finite"
                helper = (_function(text, helper_name)
                          if "static int " + helper_name + "(" in text else "")
                stdout = run(label + "/" + group,
                             (HERE / "native_resync_converter_probe.c").read_text(),
                             {"codes.inc": codes, "weights.inc": weights.group(),
                              "converter.inc": helper + _function(text, family + "_conv2XYZ")},
                             expect_failure=failed, compile_defs=(f"-DFAMILY={selector}",))
                controls = tuple(line for line in stdout.splitlines()
                                 if line.startswith("CONTROL "))
                if len(controls) != 15:
                    raise AssertionError(f"{family} converter controls are incomplete")
                if failed:
                    baseline_converter_controls[family] = controls
                elif controls != baseline_converter_controls[family]:
                    raise AssertionError(f"{family} finite conversion changed valid output")
            if "scaled-munki" in selected:
                text = texts["spectro/munki_imp.c"]
                helper = (_function(text, "cm_munki_scaled_finite")
                          if "static int cm_munki_scaled_finite(" in text else "")
                functions = _function(text, "munki_scale_specrd") + helper
                for caller, wrapper in (("munki_read_patches_2", "source_caller2"),
                                        ("munki_read_patches_2a", "source_caller2a")):
                    method = _function(text, caller)
                    anchor = "\tmunki_scale_specrd(p, specrd, numpatches, specrd);\n"
                    if method.count(anchor) != 1:
                        raise ValueError(f"Ambiguous ColorMunki publication tail: {caller}")
                    tail = method[method.index(anchor):]
                    functions += (f"static munki_code {wrapper}(munki *p, double **specrd, "
                                  "int numpatches) {\n\tmunki_code ev = MUNKI_OK;\n" + tail)
                header = (source / "spectro/munki_imp.h").read_text()
                stdout = run(label + "/scaled-munki",
                             (HERE / "native_resync_munki_output_probe.c").read_text(),
                             {"functions.inc": functions, "codes.inc": "".join(
                                 _define(header, name) for name in
                                 ("MUNKI_OK", "MUNKI_RD_READINCONS"))}, expect_failure=failed)
                controls = tuple(line for line in stdout.splitlines()
                                 if line.startswith("CONTROL "))
                if len(controls) != 6:
                    raise AssertionError("ColorMunki scaled controls are incomplete")
                if failed:
                    baseline_converter_controls["scaled-munki"] = controls
                elif controls != baseline_converter_controls["scaled-munki"]:
                    raise AssertionError("ColorMunki finite guard changed signed/zero scaling")
            if "black-debug" in selected:
                text = texts["spectro/i1pro_imp.c"]
                config = "".join(_define(text, name) for name in
                                 ("NSEN_MAX", "ENABLE_NONLINCOR", "ENABLE_BKDRIFTC",
                                  "HEURISTIC_BKDRIFTC"))
                files = {"config.inc": config,
                         "caller.inc": _function(text, "i1pro_sub_absraw")}
                for case in ("neutral", "drift"):
                    run(label + "/black-default-" + case,
                        (HERE / "native_resync_black_probe.c").read_text(), files, args=(case,))
                    if not failed:
                        run(label + "/black-plot-" + case,
                            (HERE / "native_resync_black_probe.c").read_text(), files,
                            args=(case,), compile_defs=("-DPLOT_BLACK_SUBTRACT",))
            if "plot-debug" in selected:
                _plot_objects(source, texts, output / label, compiler, report, baseline=failed)
            if "plot-fillins" in selected:
                fragments = []
                for name in ("i1pro3_strip_refl_meas", "i1pro3_pol_strip_refl_meas"):
                    caller = _function(texts["spectro/i1pro3_imp.c"], name)
                    blocks = re.findall(r"#ifdef PLOT_FILLINS\n(.*?)#endif", caller, re.S)
                    if len(blocks) != 3:
                        raise ValueError(f"Fill-in diagnostic blocks changed: {name}")
                    fragments.append(f"static void {name}_plot(int npos, double **pos_sample) {{\n"
                                     "int i;\n" + "\n".join(blocks) + "}\n")
                run(label + "/plot-fillins",
                    (HERE / "native_resync_fillins_probe.c").read_text(),
                    {"callers.inc": "\n".join(fragments)}, expect_failure=failed)
            template = (HERE / "native_resync_state_probe.c").read_text()
            functions = "".join(
                _function(texts[path], fn)
                for path, fn in (
                    ("spectro/kleink10.c", "k10_calibrate"),
                    ("spectro/spydX.c", "spydX_calibrate"),
                    ("spectro/spydX2.c", "spydX2_calibrate"),
                    ("spectro/spyd2.c", "spyd2_calibrate"),
                    ("spectro/spyd2.c", "spyd2_set_refr_rate"),
                    ("spectro/dtp92.c", "dtp92_get_refr_rate"),
                )
            )
            codes = _codes(source, template) + _define(texts["spectro/spyd2.c"], "DEFRRATE")
            codes += _define((source / "spectro/dtp92.h").read_text(), "DTP92_NO_DATA_AVAILABLE")
            if "state" in selected:
                run(
                    label + "/state",
                    template,
                    {"functions.inc": functions, "codes.inc": codes},
                    expect_failure=failed,
                )
            if "misc" in selected:
                _misc_probes(source, texts, label, run, failed)
            if "cleanup" in selected:
                _cleanup_probes(source, texts, label, run, failed)
            if "selectors" in selected:
                report.setdefault("selector_contract", {})[label] = _selector_probe(
                    source, texts, label, run, failed)
            if "followup" in selected:
                _followup_probe(source, texts, label, run, failed)
            if "calf" in selected:
                text = texts["spectro/rspec.c"]
                notice = text[:text.index("*/") + 2] + "\n"
                prefix = _define(text, "CALF_MAXSTRL") if "#define CALF_MAXSTRL" in text else ""
                functions = (
                    notice + prefix + _function(text, "calf_rstrz") + _function(text, "calf_rstrz2")
                )
                run(label + "/calf", (HERE / "native_resync_calf_probe.c").read_text(),
                    {"functions.inc": functions}, expect_failure=failed)
            if "custom-filter" in selected:
                functions = "".join(_function(texts["spectro/inst.c"], name) for name in
                                    ("inst_double_finite", "inst_valid_custom_filter"))
                functions += "".join(_function(texts["xicc/xspect.c"], name) for name in
                                     ("getval_raw_xspec_lin_ex", "getval_raw_xspec_poly3_ex",
                                      "getval_raw_xspec", "getval_xspec"))
                run(label + "/custom-filter", (HERE / "native_resync_filter_probe.c").read_text(),
                    {"functions.inc": functions,
                     "codes.inc": _define((source / "xicc/xspect.h").read_text(),
                                          "XSPECT_MAX_BANDS")},
                    expect_failure=failed)
            _package_probes(source, texts, label, run, failed, selected, report)
        if "output" in selected:
            def output_row(stage: str, family: str, case: str) -> dict:
                key = f"{stage}/output-{family}-{case}"
                return json.loads(report["results"][key]["stdout"])

            for case in ("scale_flash", "scale_spot"):
                old = output_row("baseline", "i1pro", case)
                new = output_row("repaired", "i1pro", case)
                if not (old["code"] == old["ok"] and not old["finite_output"]
                        and new["code"] == new["inconsistent"]):
                    raise AssertionError(f"i1Pro {case} did not refuse overflow")
            old = output_row("baseline", "i1pro", "equal")
            new = output_row("repaired", "i1pro", "equal")
            if not (old["code"] == new["code"] == new["ok"]
                    and old["finite_output"] and new["finite_output"]
                    and old["first"] == new["first"]
                    and old["second"] == new["second"]):
                raise AssertionError("i1Pro ordinary calibrated output changed")
            old = output_row("baseline", "i1pro3", "scale_flash")
            new = output_row("repaired", "i1pro3", "scale_flash")
            if not (old["code"] == old["ok_code"] and not old["finite_output"]
                    and new["code"] == new["inconsistent_code"]
                    and new["output_untouched"]):
                raise AssertionError("i1Pro3 flash did not refuse overflow")
            old = output_row("baseline", "i1pro3", "sat10")
            new = output_row("repaired", "i1pro3", "sat10")
            if not (old["code"] == new["code"] == new["ok_code"]
                    and old["finite_output"] and new["finite_output"]
                    and old["first"] == new["first"]
                    and old["second"] == new["second"]):
                raise AssertionError("i1Pro3 ordinary flash output changed")
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


PLOT_SELECTORS = {
    "spectro/i1pro_imp.c": ("HIGH_RES_PLOT", "HIGH_RES_PLOT_WAVFILT", "HIGH_RES_PLOT_STRAYL",
                            "PLOT_REFRESH", "PLOT_UPDELAY", "PLOT_BLACK_SUBTRACT"),
    "spectro/munki_imp.c": ("PLOT_REFRESH", "PLOT_UPDELAY", "PLOT_TEMPCOMP",
                            "PLOT_RCALCURVE", "PLOT_ECALCURVES"),
    "spectro/i1pro3_imp.c": ("PLOT_FILLINS", "D_STRAYPLOT", "UPSAMPLE_PLOT", "DEBUG_UVFILT"),
}


def _enable_plot_selector(text, path, selector, *, baseline):
    pattern = rf"(?m)^#undef([ \t]+){re.escape(selector)}(?=[ \t]|$)"
    matches = list(re.finditer(pattern, text))
    expected = (2 if path == "spectro/i1pro3_imp.c" and
                (selector == "UPSAMPLE_PLOT" or (baseline and selector == "PLOT_FILLINS")) else 1)
    if len(matches) != expected:
        raise ValueError(f"Debug selector activation changed: {path}: {selector}")
    if path == "spectro/i1pro3_imp.c":
        header = text.index("#if defined(ENABLE_PLOT_SUPPORT)")
        locally_set = baseline and selector != "D_STRAYPLOT"
        if (matches[0].start() > header) != locally_set:
            raise ValueError(f"Debug selector activation changed: {path}: {selector}")
        if selector == "UPSAMPLE_PLOT":
            caller = _function(text, "good_upsample")
            if matches[1].start() < text.index(caller) + len(caller):
                raise ValueError(f"Debug selector activation changed: {path}: {selector}")
    # The two baseline fill-in settings control different caller blocks. The
    # repaired file has one top-level setting. Preserve UPSAMPLE's closing undef.
    count = 1 if selector == "UPSAMPLE_PLOT" else len(matches)
    return re.sub(pattern, r"#define\1" + selector, text, count=count), count


PLOT_ACTIVATION = {
    "PLOT_FILLINS": (r'plot_msg\("Fill-ins on band 50:', 2),
    "D_STRAYPLOT": (r"\bdo_plotNpwz\s*\(\s*xx,\s*yy,\s*36,", 1),
    "UPSAMPLE_PLOT": (r"\bplot_wav2\s*\(\s*m,\s*0,\s*slo,\s*chlo\s*\)", 1),
    "DEBUG_UVFILT": (r"\bplot_raw2\s*\(\s*avgs\[0\],\s*avgs\[1\]\s*\)", 2),
}


def _plot_objects(source, texts, output, compiler, report, *, baseline):
    """Compile actual default/enabled units; never link or execute those objects."""
    includes = tuple(source / name for name in
                     ("h", "numlib", "rspl", "cgats", "xicc", "spectro", "plot"))
    defs = ("-DSALONEINSTLIB", "-DNT", "-DEN_LIBUSB0") if platform.system() == "Windows" else (
        "-DSALONEINSTLIB", "-DUNIX")
    for path, selectors in PLOT_SELECTORS.items():
        if path not in texts:
            continue
        text = texts[path]
        for selector in ("default", *selectors):
            directory = output / (Path(path).stem + "-" + selector)
            directory.mkdir(parents=True)
            enabled = text
            definitions = 0
            if selector != "default":
                enabled, definitions = _enable_plot_selector(
                    text, path, selector, baseline=baseline)
            unit = directory / Path(path).name
            unit.write_text(enabled)
            (directory / "sa_config.h").write_bytes((source / "h/aconfig.h").read_bytes())
            command = [compiler, "-std=gnu99", "-O1",
                       "-Werror=implicit-function-declaration", "-DENABLE_USB", *defs,
                       "-I" + str(directory),
                       *("-I" + str(p) for p in includes), "-c", str(unit),
                       "-o", str(directory / "unit.o")]
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            (directory / "compile.log").write_text(result.stdout + result.stderr)
            key = ("baseline/" if baseline else "repaired/") + Path(path).stem + "-" + selector
            report["results"][key] = {"command": command, "compile_returncode": result.returncode,
                                      "source_sha256": digest(enabled.encode()), "executed": False,
                                      "enabled_definitions": definitions,
                                      "compile_log": str(directory / "compile.log")}
            if path == "spectro/i1pro3_imp.c" and selector != "default":
                preprocess = subprocess.run(command[:command.index("-c")] + ["-E", str(unit)],
                                            capture_output=True, text=True, timeout=120)
                pattern, expected = PLOT_ACTIVATION[selector]
                active = len(re.findall(pattern, preprocess.stdout))
                row = report["results"][key]
                row.update(preprocess_returncode=preprocess.returncode, active_plot_calls=active,
                           preprocessed_sha256=digest(preprocess.stdout.encode()))
                if preprocess.returncode != 0 or active != expected:
                    raise AssertionError(f"Debug plot calls are not active: {key}")
            expected_failure = baseline and selector != "default"
            if expected_failure:
                if result.returncode == 0 or not re.search(
                    r"(?:undeclared|implicit)[^\n]*(?:plot_\w+|do_plot\w*|\bxx\b|\bin\b|\bres\b|adjsub)",
                    result.stderr,
                ):
                    raise AssertionError(f"Expected plotting-declaration refusal missing: {key}")
            elif result.returncode != 0:
                raise AssertionError(f"Consumed driver debug object failed: {key}; see compile.log")


#: Probe groups for the imported shared-driver repairs.
PACKAGE_GROUPS = ("average", "white_factors", "polarized", "agreement", "dark", "dark_completion",
                  "gain_switch", "specbos_range", "i1pro3_timing", "restore_bounds",
                  "munki_timing", "dtp41")


# Source anchors in the i1Pro and ColorMunki restore and trigger guards.
RESTORE_CONJUNCT = "\t\t && (!s->adaptive || (ts.inttime >= -DBL_MAX && ts.inttime <= DBL_MAX"
RESTORE_CLAMP = "\t\t\t/* An adaptive time can be just outside the range after rounding to */\n"
TRIGGER = {"i1pro": ("\tdintclocks = floor(*inttime/m->intclkp + 0.5);\n",
                     "!(dintclocks <= 65535.0)"),
           "munki": ("\tdintclocks = floor((*inttime)/m->intclkp + 0.5);\n",
                     "if (dintclocks < (double)INT_MIN)")}
DARK_PAIRING = "\t\t && (s->adaptive || !ts.dark_valid || ts.dark_gain_mode == ts.gainmode)\n"
I1PRO_MAXGAIN = ("#ifdef USE_HIGH_GAIN_MODE\n\tif (p->dtype != instI1Pro2)\n\t\tmaxgain = 1;")
I1PRO_LOW_RAISE = "\tif (*inttime < m->min_int_time)\n\t\t*inttime = m->min_int_time; \n"


def _restore_bounds_census(family: str, text: str) -> list[str]:
    """Each restore keeps a finite adaptive time in range and a non-adaptive
    dark only at the mode's gain; each trigger converts only a clock count int
    holds; the non-adaptive match and the i1Pro's low raise, which the probe
    relies on, are still in place."""
    problems = []
    restore = _function(text, f"{family}_restore_calibration")
    if RESTORE_CONJUNCT not in restore:
        problems.append(f"{family} restores an adaptive time without a finite range check")
    if "\t\t\ts->inttime = ts.inttime;\n" + RESTORE_CLAMP not in restore:
        problems.append(f"{family} restores an adaptive time without keeping it in range")
    if "(s->adaptive || fabs(s->inttime - ts.inttime) < 0.01)" not in restore:
        raise ValueError(f"{family} non-adaptive restore match changed")
    if "\t\t && ts.gainmode >= 0 && ts.gainmode <= " not in restore \
            or "\t\t && ts.dark_gain_mode >= 0 && ts.dark_gain_mode <= " not in restore:
        problems.append(f"{family} restores a gain mode its build or mode doesn't set")
    elif family == "i1pro" and I1PRO_MAXGAIN not in restore:
        raise ValueError("i1Pro restore no longer derives maxgain from the build")
    if DARK_PAIRING not in restore:
        problems.append(f"{family} restores a non-adaptive dark taken at another gain")
    trigger = _function(text, f"{family}_trigger_one_measure")
    start, check = TRIGGER[family]
    if trigger.count(start) != 1 or check not in trigger[trigger.index(start):]:
        problems.append(f"{family} converts an unchecked clock count to int")
    if family == "i1pro" and I1PRO_LOW_RAISE not in trigger[:trigger.index(start)]:
        raise ValueError("i1Pro trigger no longer raises a low time first")
    return problems


def _restore_bounds_blocks(family: str, text: str) -> dict[str, str]:
    restore = _function(text, f"{family}_restore_calibration")
    condition = restore[restore.index(RESTORE_CONJUNCT):]
    condition = condition[:condition.index("\t\t) {\n")]
    clamp = restore[restore.index(RESTORE_CLAMP):]
    clamp = clamp[:clamp.index("\t\t\t}\n", clamp.index("m->max_int_time;")) + 5]
    trigger = _function(text, f"{family}_trigger_one_measure")
    start = TRIGGER[family][0]
    body = trigger[trigger.index(start):]
    body = body[:body.index("\tintclocks = (int)dintclocks;\n") + 31]
    return {"condition.inc": condition, "clamp.inc": clamp, "trigger.inc": body}


MUNKI_TIMING = "\t/* Refuse timing values the driver can't use: a tick or minimum count */\n"


def _munki_timing_block(munki: str) -> str | None:
    """The init guard on the instrument's timing values, if it follows the
    calibration read in munki_imp_init."""
    init = _function(munki, "munki_imp_init")
    if MUNKI_TIMING not in init:
        return None
    if init.index("\tfree(calbuf);\n\tcalbuf = NULL;\n") > init.index(MUNKI_TIMING):
        raise ValueError("ColorMunki timing guard runs before the calibration read")
    block = init[init.index(MUNKI_TIMING):]
    return block[:block.index("\n\t}\n", block.index("overflows a %g sec. reading"))
                 + len("\n\t}\n")]


I1PRO3_RESTORE_CHECK = (
    "\t/* A restored integration time outside what the mode's calibration sets */\n")


def _i1pro3_restore_census(fn: str) -> list[str]:
    """The initial times are kept before the file is read, and checked after."""
    problems = []
    read = fn.index("\tx.op = 2;")
    call = fn.index("\ti1pro3_nv_op(p, &x, fp);\n", read)
    save = fn.find("\t\titime[i] = m->ms[i].inttime;\n")
    if not read < save < call:
        problems.append("initial integration times are not kept before the read")
    check = fn.find(I1PRO3_RESTORE_CHECK)
    if not fn.index("Checksum didn't verify 2nd time") < check:
        problems.append("restored integration times are not checked after the read")
    return problems


def _package_probes(report_source: Path, texts: dict[str, str], label: str, run, failed: bool,
                    selected: set[str], report: dict) -> None:
    """Each package fix on the final compiled text; families absent from a narrow
    recipe's stage are not probed there."""
    pro, pro3, munki = (texts.get(f"spectro/{f}_imp.c") for f in ("i1pro", "i1pro3", "munki"))
    if "average" in selected:
        template = (HERE / "native_resync_average_probe.c").read_text()
        for family, text, fn in ((1, pro, "i1pro_average_multimeas"),
                                 (2, munki, "munki_average_multimeas"),
                                 (3, pro3, "i1pro3_multimeas_check_raw_consistency")):
            if text is None:
                continue
            constants = _define(text, "PATCH_CONS_THR")
            if family == 2:
                constants += _define(text, "DARKTHSCAMIN")
            code = f"#define FAMILY {family}\n" + template
            run(f"{label}/average-{fn.split('_')[0]}", code,
                {"functions.inc": constants + _function(text, fn)}, expect_failure=failed)
    if munki is not None and "white_factors" in selected:
        # Every caller tests for the refusal; the function is run on reads.
        calls = len(re.findall(r"\bmunki_compute_white_cal\(p,", munki))
        tested = len(re.findall(
            r"if \(\(?(?:warn = )?munki_compute_white_cal\(p,[^;{]*?\)\)? < 0\) \{", munki))
        if calls != 4:
            raise ValueError("ColorMunki white-factor caller census changed")
        if tested != calls and not failed:
            raise AssertionError(f"Resync behavior differs at {label}/white-callers: "
                                 f"{tested} of {calls} callers test the refusal")
        report["results"][label + "/white-callers"] = {"calls": calls, "tested": tested}
        template = (HERE / "native_resync_white_factors_probe.c").read_text()
        run(label + "/white-factors", template,
            {"function.inc": _function(munki, "munki_compute_white_cal")},
            expect_failure=failed)
    if pro3 is not None and "polarized" in selected:
        fn = _function(pro3, "i1pro3_pol_refl_cal")
        start = fn.index("\t\t/* Compute an integration time that achieves targoscale */")
        end = fn.index("\t\tinttime = no_minints * m->min_int_time;\n", start)
        block = fn[start:end] + "\t\tinttime = no_minints * m->min_int_time;\n"
        run(label + "/polarized", (HERE / "native_resync_polarized_probe.c").read_text(),
            {"block.inc": block}, expect_failure=failed)
    if munki is not None and "agreement" in selected:
        _agreement_probe(munki, label, run, failed)
    if munki is not None and "dark_completion" in selected:
        start = "\t\t\tif (s->reflective) {\t/* We read the write reference - check it */"
        end = "\t\t\t*calt &= ~(inst_calt_ref_white\n\t\t\t         | inst_calt_trans_vwhite);"
        if munki.count(start) != 1 or munki.count(end) != 1:
            raise ValueError("ColorMunki reflective-white tail changed")
        block = munki[munki.index(start) : munki.index(end) + len(end)]
        # The proof's headers come from the authenticated compiled source.
        template = (HERE / "native_resync_munki_dark_probe.c").read_text()
        codes = _enum((report_source / "spectro/inst.h").read_text(), "inst_cal_type")
        header = (report_source / "spectro/munki_imp.h").read_text()
        codes += "".join(
            _define(header, name)
            for name in ("MUNKI_OK", "MUNKI_COMS_FAIL", "MUNKI_RD_WHITEREFERROR")
        )
        run(
            label + "/dark-completion",
            template,
            {"block.inc": block, "codes.inc": codes},
            expect_failure=failed,
        )
    if pro is not None and "gain_switch" in selected:
        # The adaptive scan branch of i1pro_imp_calibrate, run as its own if:
        # a white after a gain switch must subtract the new gain's dark.
        start = "\t\t\t} else if (s->adaptive) {\n\t\t\t\tint j"
        end = ("\t\t\t/* We've settled on the inttime and gain mode to get a good white "
               "reference. */")
        if pro.count(start) != 1 or pro.count(end) != 1:
            raise ValueError("i1Pro adaptive scan calibration changed")
        head = "\t\t\t} else "
        block = pro[pro.index(start) + len(head):pro.index(end)]
        template = (HERE / "native_resync_i1pro_gain_probe.c").read_text()
        run(label + "/gain-switch", template,
            {"block.inc": block, "codes.inc": _codes(report_source, template)},
            expect_failure=failed)
    if pro3 is not None and "i1pro3_timing" in selected:
        start = "\t/* Refuse timing parameters that would overflow i1pro3_do_measure()'s */\n"
        if start not in pro3:
            if not failed:
                raise AssertionError(f"Resync behavior differs at {label}/i1pro3-timing: "
                                     "no init guard on the device clock values")
            report["results"][label + "/i1pro3-timing"] = {"guard": False}
        else:
            if failed:
                raise AssertionError(
                    f"Resync baseline has no named failure at {label}/i1pro3-timing")
            block = pro3[pro3.index(start):]
            block = block[:block.index("\n\t}\n", block.index("overflows a scan")) + len("\n\t}\n")]
            header = (report_source / "spectro/i1pro3_imp.h").read_text()
            codes = "typedef int i1pro3_code;\n" + "".join(
                _define(header, name) for name in ("I1PRO3_OK", "I1PRO3_DATA_PARSE_ERROR"))
            codes += "".join(_define(pro3, name) for name in
                             ("MAX_INT_TIME", "MAXSCANTIME", "MX_NSEN", "LEDTURNONMEAS"))
            report["results"][label + "/i1pro3-timing"] = {"guard": True}
            run(label + "/i1pro3-timing-cases",
                (HERE / "native_resync_i1pro3_timing_probe.c").read_text(),
                {"block.inc": block, "codes.inc": codes}, expect_failure=False)
        # A calibration file's integration times bypass that guard's assumptions.
        restore = _function(pro3, "i1pro3_restore_calibration")
        problems = _i1pro3_restore_census(restore)
        if failed and not problems:
            raise AssertionError(f"Resync baseline has no named failure at {label}/i1pro3-restore")
        if problems and not failed:
            raise AssertionError(f"Resync behavior differs at {label}/i1pro3-restore: "
                                 + "; ".join(problems))
        report["results"][label + "/i1pro3-restore"] = {"problems": problems}
        if not failed:
            check = restore[restore.index(I1PRO3_RESTORE_CHECK):]
            check = check[:check.index("\n\t}\n", check.index("s->cal_valid = 0;"))
                          + len("\n\t}\n")]
            run(label + "/i1pro3-restore-cases",
                (HERE / "native_resync_i1pro3_restore_probe.c").read_text(),
                {"block.inc": check, "guard.inc": block, "codes.inc": codes},
                expect_failure=False)
    if "restore_bounds" in selected:
        families = [(f, t) for f, t in (("i1pro", pro), ("munki", munki)) if t is not None]
        problems = [p for f, t in families for p in _restore_bounds_census(f, t)]
        if failed and not problems:
            raise AssertionError(f"Resync baseline has no named failure at {label}/restore-bounds")
        if problems and not failed:
            raise AssertionError(f"Resync behavior differs at {label}/restore-bounds: "
                                 + "; ".join(problems))
        report["results"][label + "/restore-bounds"] = {"problems": problems}
        if not failed:
            for number, (family, text) in enumerate(families, 1):
                header = (report_source / f"spectro/{family}_imp.h").read_text()
                prefix = family.upper()
                codes = "".join(_define(header, f"{prefix}_{n}") for n in
                                ("OK", "INT_INTTOOBIG", "INT_INTTOOSMALL"))
                codes += (f"#define CODE_OK {prefix}_OK\n"
                          f"#define CODE_TOOBIG {prefix}_INT_INTTOOBIG\n"
                          f"#define CODE_TOOSMALL {prefix}_INT_INTTOOSMALL\n")
                run(f"{label}/restore-bounds-{family}",
                    f"#define FAMILY {1 if family == 'i1pro' else 2}\n"
                    + (HERE / "native_resync_restore_probe.c").read_text(),
                    {**_restore_bounds_blocks(family, text), "codes.inc": codes},
                    expect_failure=False)
    if munki is not None and "munki_timing" in selected:
        block = _munki_timing_block(munki)
        if (block is None) != failed:
            raise AssertionError(
                f"Resync baseline has no named failure at {label}/munki-timing" if failed else
                f"Resync behavior differs at {label}/munki-timing: no init guard on the "
                "instrument timing values")
        report["results"][label + "/munki-timing"] = {"guard": block is not None}
        if block is not None:
            header = (report_source / "spectro/munki_imp.h").read_text()
            codes = "".join(_define(header, n) for n in ("MUNKI_OK", "MUNKI_DATA_RANGE"))
            codes += "".join(_define(munki, n) for n in ("MAXSCANTIME", "NSEN_MAX"))
            counts = "".join(_function(munki, f) for f in
                             ("munki_comp_nummeas", "munki_comp_ru_nummeas"))
            run(label + "/munki-timing-cases",
                (HERE / "native_resync_munki_timing_probe.c").read_text(),
                {"block.inc": block, "codes.inc": codes, "counts.inc": counts},
                expect_failure=False)
    specbos = texts.get("spectro/specbos.c")
    if specbos is not None and "specbos_range" in selected:
        problems = _specbos_census(specbos)
        if failed and not problems:
            raise AssertionError(f"Resync baseline has no named failure at {label}/specbos-range")
        if problems and not failed:
            raise AssertionError(f"Resync behavior differs at {label}/specbos-range: "
                                 + "; ".join(problems))
        report["results"][label + "/specbos-range"] = {"problems": problems}
        if not failed:
            run(label + "/specbos-nbands", (HERE / "native_resync_specbos_probe.c").read_text(),
                {"function.inc": _function(specbos, "specbos_nbands"),
                 "command.inc": _specbos_commands(specbos),
                 "codes.inc": _define((report_source / "xicc/xspect.h").read_text(),
                                      "XSPECT_MAX_BANDS")},
                expect_failure=False)
    dtp41 = texts.get("spectro/dtp41.c")
    if dtp41 is not None and "dtp41" in selected:
        inst = (report_source / "spectro/inst.h").read_text()
        codes = "".join(_enum(inst, n) for n in ("inst_code", "inst_mode", "inst_cal_type",
                                                 "inst_cal_cond", "inst_calc_id_type"))
        codes += _define(inst, "CALIDLEN")
        codes += _define(dtp41, "MAX_MES_SIZE") + _define(dtp41, "MAX_RD_SIZE")
        run(label + "/dtp41", (HERE / "native_resync_dtp41_probe.c").read_text(),
            {"functions.inc": _dtp41_functions(dtp41), "codes.inc": codes},
            expect_failure=failed)
    if "dark" in selected:
        missing = _dark_census(texts)
        if failed and not missing:
            raise AssertionError(f"Resync baseline has no named failure at {label}/dark")
        if missing and not failed:
            raise AssertionError(f"Resync behavior differs at {label}/dark: "
                                 + "; ".join(missing))
        report["results"][label + "/dark"] = {"uninvalidated": missing}


def _dtp41_functions(text: str) -> str:
    """build_strip() and activate_mode(), each read up to its first command
    after activation (the rest of a read parses the instrument's reply), and
    the two calibration functions, which send nothing."""
    code = _function(text, "build_strip") + _function(text, "activate_mode")
    for fn, marker in (("dtp41_read_strip", "\t/* Send strip definition */\n"),
                       ("dtp41_read_sample", "\t/* Set static measurement mode */\n")):
        body = _function(text, fn)
        if body.count(marker) != 1:
            raise ValueError(f"DTP41 first command changed in {fn}")
        end = body.index("\t\treturn ev;\n", body.index(marker)) + len("\t\treturn ev;\n")
        code += body[:end] + "\treturn inst_ok;\n}\n"
    return code + _function(text, "dtp41_get_n_a_cals") + _function(text, "dtp41_calibrate")


def _specbos_census(text: str) -> list[str]:
    """Every Specbos band count goes through specbos_nbands(), the 1501/1511/2501
    range reply is read as doubles, and the third-party calibration retry checks
    its narrowed range before storing it (specbos.c:1215-1232 in pristine)."""
    problems = []
    if "static int specbos_nbands(double wl_short, double wl_long) {" not in text:
        problems.append("no specbos_nbands()")
    unchecked = len(re.findall(r"p->nbands = \(int\)\(", text))
    if unchecked:
        problems.append(f"{unchecked} unchecked band counts")
    calls = len(re.findall(r"\bspecbos_nbands\(p->wl_short|\bspecbos_nbands\(p->wl_short <", text))
    if calls != 3:
        problems.append(f"{calls} of 3 band counts checked")
    if '"%d %d %d",&wstart,&wend,&wstep' in text:
        problems.append("range reply read as int")
    retry = text[text.index("SPECBOS_EXCEED_CAL_WL && !p->badCal"):]
    retry = retry[:retry.index("Re-set the wavelength range")]
    if not ("specbos_nbands(" in retry
            and retry.index("specbos_nbands(") < retry.index("p->wl_short = 380.0")):
        problems.append("retry stores its range before checking it")
    return problems


def _specbos_commands(text: str) -> str:
    """Compile the three actual range formatters without their transport calls."""
    commands = re.findall(r'sprintf\(mes, "\*(?:para|conf):wran\b.*?;', text, re.S)
    if len(commands) != 3:
        raise ValueError("Specbos range command sites changed")
    branches = "\n".join(
        f"case {i}: {command} break;" for i, command in enumerate(commands)
    )
    return (
        "static int command_endpoints(int site, double start, double end, int *lo, int *hi) {\n"
        "    struct { double wl_short, wl_long; } instrument, *p = &instrument;\n"
        "    char mes[100];\n"
        "    int step;\n"
        "    p->wl_short = start; p->wl_long = end;\n"
        "    switch (site) {\n" + branches + "\n    default: return 0;\n    }\n"
        "    return sscanf(strchr(mes, ' '), \"%d %d %d\", lo, hi, &step) == 3 && step == 1;\n"
        "}\n"
    )


#: First live writes of each dark block in the two calibrate methods, and the one
#: ColorMunki block left as upstream: no ColorMunki mode is both adaptive and
#: scanning (munki_imp.c:681-836), so its adaptive-scan dark is never measured.
#: The i1Pro's block of the same name is reachable and is invalidated.
DARK_WRITES = {"i1pro": 7, "munki": 5}
DARK_UNREACHABLE = {"munki": "Doing adaptive scan black calibration"}


def _dark_census(texts: dict[str, str]) -> list[str]:
    """Each live dark write follows an invalidation of the data it overwrites.

    The last assignment to that block's valid flag before its first write must
    clear it, in the write's own block or an enclosing one: an invalidation in a
    sibling branch (the white loop's saturated case) does not cover the write.
    """
    missing = []
    for family, count in DARK_WRITES.items():
        text = texts.get(f"spectro/{family}_imp.c")
        if text is None:
            continue
        fn = _function(text, f"{family}_imp_calibrate")
        calls = list(re.finditer(
            rf"{family}_dark_measure\(p, s->(dark_data|idark_data\[0\]),", fn))
        if len(calls) != count:
            raise ValueError(f"{family} dark-write census changed")
        for call in calls:
            prior = fn[:call.start()]
            unreachable = DARK_UNREACHABLE.get(family)
            if unreachable and prior.rfind(unreachable) > prior.rfind("_dark_measure("):
                continue
            flag = "dark_valid" if call[1] == "dark_data" else "idark_valid"
            assigned = list(re.finditer(rf"s->{flag} = (\d)", prior))
            if not (assigned and assigned[-1][1] == "0"
                    and _encloses(fn[assigned[-1].end():call.start()])):
                missing.append(f"{family} line {prior.count(chr(10)) + 1} of calibrate")
    return missing


def _encloses(between: str) -> bool:
    """No block that holds the start of ``between`` closes before its end."""
    depth = 0
    for token in re.finditer(
        r'/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]', between
    ):
        depth += {"{": 1, "}": -1}.get(token[0], 0)
        if depth < 0:
            return False
    return True


def _agreement_probe(munki: str, label: str, run, failed: bool) -> None:
    """The final ColorMunki agreement test divides by neither zero margin and
    refuses exactly what the scan-margin stage's test refuses."""
    from native_argyll.munki_scan_margin import REPAIRED, _agreement_test, _condition

    code = f"""#include <stdio.h>
#include <math.h>
#include <fenv.h>
#pragma STDC FENV_ACCESS ON
static int final_test(double lead_avg, double trail_avg) {{
\treturn {_agreement_test(munki)};
}}
static int stage_test(double lead_avg, double trail_avg) {{
\treturn {_condition(REPAIRED)};
}}
int main(void) {{
 static volatile const double v[] = {{0.0, -0.0, 1.0, -1.0, 0.7, 1e-300, 1e300,
  INFINITY, -INFINITY, NAN}};
 int i, j, failures = 0, n = sizeof(v) / sizeof(v[0]);
 for (i = 0; i < n; i++) for (j = 0; j < n; j++) {{
  int refused, divided;
  feclearexcept(FE_ALL_EXCEPT);
  refused = final_test(v[i], v[j]);
  divided = fetestexcept(FE_DIVBYZERO) != 0;
  if (divided) {{ printf("FAIL %g/%g divides by zero\\n", v[i], v[j]); failures++; }}
  if (refused != stage_test(v[i], v[j])) {{
   printf("FAIL %g/%g outcome changed\\n", v[i], v[j]); failures++;
  }}
 }}
 printf("agreement: %d failures\\n", failures);
 return failures != 0;
}}
"""
    run(label + "/agreement", code, {}, expect_failure=failed)


def _followup_probe(source: Path, texts: dict[str, str], label: str, run, failed: bool) -> None:
    """Dispatch up to its first success commit; saved-time guards before conversion.

    Scripted helpers write live white data before returning failure. This probe
    does not execute sibling propagation, EEPROM decoding or calibration cache I/O.
    """
    template = (HERE / "native_resync_followup_probe.c").read_text()
    cal = _function(texts["spectro/i1pro3_imp.c"], "i1pro3_imp_calibrate")
    files = {}
    for name, marker in (
        ("reflective", "\t\tif (s->cdate != cdate\n\t\t && (*calt & (inst_calt_ref_white"),
        ("transmission", "\t\tif (s->cdate != cdate\n\t\t && (*calt & (inst_calt_trans_vwhite"),
        ("munki", "\t\tif ((*calt & (inst_calt_ref_white"),
    ):
        text = (
            cal
            if name != "munki"
            else _function(texts["spectro/munki_imp.c"], "munki_imp_calibrate")
        )
        block = _block(text, marker)
        end = block.index("s->cal_valid = 1;") + len("s->cal_valid = 1;")
        prefix = block[:end]
        # Independent census bounds the source we execute and detects new exits.
        # The package port adds ColorMunki's two unusable-white-factor refusals.
        if (
            len(re.findall(r"\breturn\b", prefix))
            != {"reflective": 2, "transmission": 1, "munki": 9 if failed else 11}[name]
        ):
            raise ValueError("White-calibration refusal census changed")
        files[name + ".inc"] = prefix + "\n\t\t}\n"
    c = texts["spectro/i1pro_imp.c"]
    restore = _function(c, "i1pro_restore_refspot_cal")
    # Start right after the saved-time read, so the probe also sees an assignment
    # moved ahead of the validation.
    begin = restore.index("Failed to read calibration integration time")
    begin = restore.index("\t}\n", begin) + len("\t}\n")
    files["restore.inc"] = restore[begin : restore.index("\t/* Get the dark data */", begin)]
    begin = c.index("\t\tm->max_int_time = dp[0];")
    files["maximum.inc"] = c[begin : c.index("\n\n\n", begin)] + "\n"
    errors = (
        '#define ERROR_TEXT "Spectral to CIE conversion or custom filter compensation failed"\n'
    )
    for family in ("i1pro", "i1pro3", "munki"):
        fn = _function(texts[f"spectro/{family}.c"], family + "_interp_error")
        match = re.search(r"case " + family.upper() + r'_INT_CIECONVFAIL:\s*return "[^"]*";', fn)
        if match is None:
            raise ValueError("CIE conversion error branch changed")
        errors += f"static const char *error_{family}(int code) {{switch(code) {{\n"
        errors += match[0] + '\n} return "unknown";}\n'
    files["errors.inc"] = errors
    codes = _codes(source, "") + _enum((source / "spectro/inst.h").read_text(), "inst_opt_filter")
    all_text = template + "".join(files.values())
    for family in ("i1pro", "i1pro3", "munki"):
        h = (source / f"spectro/{family}_imp.h").read_text()
        names = set(re.findall(r"\b" + family.upper() + r"_[A-Z0-9_]+", all_text))
        codes += "".join(_define(h, name) for name in sorted(names))
    files["codes.inc"] = codes
    run(label + "/followup", template, files, expect_failure=failed)


def _cleanup_probes(source: Path, texts: dict[str, str], label: str, run, failed: bool) -> None:
    """Twelve exact error branches plus both partial-allocation exits."""
    fragments = []
    for family in ("i1pro", "i1pro3"):
        cal = _function(texts[f"spectro/{family}_imp.c"], family + "_imp_calibrate")
        # The edit's failure log identifies its surrounding if block uniquely,
        # including the two filter calls and three distinct reflection calls.
        messages = (
            [
                "i1pro2_wl_measure()",
                "i1pro2_match_wl_meas()",
                "i1pro_compute_wav_filters()",
                "i1pro_create_hr()",
            ]
            if family == "i1pro"
            else [
                "i1pro3_wl_measure()",
                "i1pro3_match_wl_meas()",
                "i1pro3_compute_wav_filters()",
                "i1pro3_refl_cal",
                "i1pro3_trans_cal",
            ]
        )
        for match in re.finditer(
            r'(?P<ind>\t+)a1logd\(p->log,2,"(?P<msg>[^"\n]+) failed\\n"\);', cal
        ):
            if match["msg"] not in messages:
                continue
            end = (
                match.end()
                + re.search(r"^" + match["ind"][:-1] + r"}", cal[match.end() :], re.M).start()
            )
            branch = cal[match.end() : end]
            if branch.count("return ev;") != 1:
                raise ValueError("Error-exit extract changed")
            i = len(fragments)
            fragments.append(
                f"static int exit{i}(void) {{\n int ev=7, mmode=9;"
                " struct {int mmode, nraw;} obj={1,128}, *m=&obj;"
                " double *wlraw=NULL; (void)wlraw;\n" + branch + "\n}\n"
            )
    if len(fragments) != 12:
        raise ValueError("Calibration error-exit census changed")
    # A macro records state at the exact return and counts the real release call.
    code = """#include <stdio.h>
static int calls, failures;
#define free_dvector(...) (++calls)
"""
    # Observe state at the exact return without evaluating diagnostic arguments.
    for i, f in enumerate(fragments):
        f = f.replace("return ev;",
                      f'if(m->mmode!=mmode) {{failures++; puts("FAIL exit {i} restores mode");}} '
                      'return ev;')
        code += f
    code += "int main(void) {\n"
    for i in range(12):
        code += f' if(exit{i}()!=7) {{failures++; puts("FAIL exit {i} returns error");}}\n'
    code += (
        'if(calls!=4) {failures++; puts("FAIL four wavelength vectors released");} '
        'printf("cleanup: %d failures\\n",failures); '
        "return failures!=0; }\n"
    )
    run(label + "/cleanup", code, {}, expect_failure=failed)
    for family, member in (("i1pro", "mtx_c"), ("i1pro3", "mtx")):
        name = "i1pro_compute_wav_filters" if family == "i1pro" else "i1pro3_compute_wav_filter"
        function = _function(texts[f"spectro/{family}_imp.c"], name)
        block = _block(function, f"\tif (m->{member}[hr][refl].index == NULL)")
        error = family.upper() + "_INT_MALLOC"
        header = (source / f"spectro/{family}_imp.h").read_text()
        code = _define(header, error) + """#include <stdio.h>
#include <stdlib.h>
typedef struct {int *index, *nocoef; double *coef;} matrix;
typedef struct {int nwav[2]; matrix mtx[2][2], mtx_c[2][2];} model;
typedef struct {void *log;} driver;
static int calls, fail_at, live, failures;
static void *take(size_t count, size_t size) {
 void *p;
 if(++calls==fail_at) return NULL;
 p=calloc(count,size); if(p) live++; return p;
}
static void drop(void *p) {if(p) {live--; free(p);}}
#define calloc take
#define free drop
#define a1logd(...) ((void)0)
static int allocate(driver *p, model *m) {
 int hr=0, refl=0;
""" + block + """ return 0;
}
int main(void) {
 int at;
 driver d={0};
 for(at=2;at<=3;at++) {
  model m={0}; m.nwav[0]=4;
  calls=0; fail_at=at;
  if(allocate(&d,&m)!=MALLOC_CODE) failures++;
  if(live || m.MATRIX_MEMBER[0][0].index || m.MATRIX_MEMBER[0][0].nocoef
     || m.MATRIX_MEMBER[0][0].coef) {
   failures++; puts("FAIL partial matrix persisted");
  }
  calls=0; fail_at=0;
  if(allocate(&d,&m)!=0 || !m.MATRIX_MEMBER[0][0].index
     || !m.MATRIX_MEMBER[0][0].nocoef || !m.MATRIX_MEMBER[0][0].coef) {
   failures++; puts("FAIL same-object retry incomplete");
  }
  drop(m.MATRIX_MEMBER[0][0].coef);
  drop(m.MATRIX_MEMBER[0][0].nocoef);
  drop(m.MATRIX_MEMBER[0][0].index);
  if(live) {failures++; puts("FAIL matrix allocation retained");}
 }
 printf("matrix: %d failures\\n",failures); return failures!=0;
}
"""
        code = code.replace("MATRIX_MEMBER", member).replace("MALLOC_CODE", error)
        run(label + "/matrix-" + family, code, {}, expect_failure=failed)


def _selector_probe(source: Path, texts: dict[str, str], label: str, run, failed: bool) -> dict:
    names = ("spectro/inst.h", "spectro/disptechs.h", "spectro/disptechs.c", "icc/icc_util.c")
    sources = {n: (source / n).read_text() for n in names}
    sources["spectro/inst.c"] = texts["spectro/inst.c"]
    declarations, functions = _shared(sources)
    # The frozen predecessor includes CR/PR's expanded suffix pool. Production
    # may instead use the repairs projection's smaller pool. Select the contract
    # from the independent instrument registry, never from the constructor or its
    # alphabet: shortening that alphabet must still fail the observer below.
    registry = production_text(source, "spectro/insttypes.h")
    types = _enum(registry, "devType")
    types = re.sub(r"/\*.*?\*/|//[^\n]*", "", types, flags=re.S)
    contributed = bool(re.search(r"\binstCR250\b", types))
    empty = 1 if failed or contributed else 10
    template = (HERE / "disptype_list_member_release_probe.c").read_text()
    template = template.replace(
        "#define a1loge(...) ((void)0)",
        "#define a1loge(...) ((void)0)\n#define a1logw(...) ((void)0)",
    )
    template = template.replace(
        "list[i].sel = NULL;\n\t\tlist[i].oem",
        'list[i].sel = i == 129 ? "Z" : NULL;\n\t\tlist[i].oem',
    )
    # Reuse the actual poisoned constructor. This observer checks termination,
    # assigned-key uniqueness, retained counts and the same by-index labels.
    marker = "\t\t/* Release what was handed back, as every destructor does. */"
    observe = """
        if(list == NULL || n != 132 || codes[it] != inst_ok) return 1;
        {int empty=0;
        for(k=0;k<n;k++) {
            int j; size_t len=0;
            while(len<INST_DTYPE_SEL_LEN && list[k].sel[len]) len++;
            if(len==INST_DTYPE_SEL_LEN) {
                puts("FAIL unterminated selector"); inst_del_disptype_list(list,n); return 1;
            }
            if(!len) empty++;
            if(len) for(j=0;j<k;j++) if(!strcmp(list[k].sel,list[j].sel)) {
                puts("FAIL duplicate selector"); inst_del_disptype_list(list,n); return 1;
            }
            if(k>=2) {
                char expected[64]; snprintf(expected,sizeof expected,"Scripted %03d",k-2);
                if(strcmp(expected,list[k].desc)) {
                    puts("FAIL index label changed"); inst_del_disptype_list(list,n); return 1;
                }
                if(list[k].ix!=0 || list[k].cc_cbid!=1 || list[k].no_sets!=0
                   || list[k].dtech!=disptech_lcd || list[k].refr!=0
                   || list[k].mat[0][0] != 1.0+(k-2)/16.0
                   || list[k].mat[1][1] != list[k].mat[0][0]
                   || list[k].mat[2][2] != list[k].mat[0][0]) {
                    puts("FAIL index payload changed"); inst_del_disptype_list(list,n); return 1;
                }
            }
        }
        if(empty!=EMPTY || strcmp(list[131].sel,"Z") || strcmp(list[0].sel,"n")
           || strcmp(list[1].sel,"r")) {
            puts("FAIL selector capacity or assigned primary changed");
            inst_del_disptype_list(list,n); return 1;
        }}
"""
    observe = observe.replace("EMPTY", str(empty))
    template = template.replace(marker, observe + "\n" + marker)
    run(
        label + "/selectors",
        template,
        {"declarations.h": declarations, "functions.inc": functions},
        args=("ccmx130",),
        expect_failure=False,
    )
    return {"empty": empty, "registry_sha256": digest(registry.encode())}


def _misc_probes(source: Path, texts: dict[str, str], label: str, run, failed: bool) -> None:
    cal = _function(texts["spectro/i1pro3_imp.c"], "i1pro3_imp_calibrate")
    block = _block(cal, "\t\tif (s->diseldate != cdate")
    template = (HERE / "native_resync_exposure_probe.c").read_text()
    h = (source / "spectro/i1pro3_imp.h").read_text()
    codes = _codes(source, "") + "typedef int i1pro3_code;\n" + _enum(h, "i1p3_mmode")
    for name in sorted(set(re.findall(r"\bI1PRO3_[A-Z0-9_]+", block + template))):
        codes += _define(h, name)
    numsup = (source / "numlib/numsup.c").read_text()
    average = "".join(
        _function(numsup, fn) for fn in ("vect_set", "vect_add", "vect_scale1", "vect_max")
    )
    average += _function(texts["spectro/i1pro3_imp.c"], "i1pro3_average_rawmmeas")
    run(
        label + "/exposure",
        template,
        {"block.inc": block, "codes.inc": codes, "average.inc": average},
        expect_failure=failed,
    )
    release_vector = _function((source / "numlib/numsup.c").read_text(), "free_dvector")
    for family, fn in (
        ("i1pro", "i1pro_extract_patches_multimeas"),
        ("i1pro3", "i1pro3_locate_patches"),
    ):
        c = texts[f"spectro/{family}_imp.c"]
        block = _block(_function(c, fn), "\t\tif (points == NULL || pcurve == NULL)")
        code = (
            """#include <stdio.h>
#include <stdlib.h>
static int live, failures;
static void *maxval_base, *maxval_value;
typedef struct curve {void (*del)(struct curve*);} curve;
static void drop(void *p) {
 if(p==maxval_value && p!=maxval_base) {
  failures++; puts("FAIL shifted maxval free"); p=maxval_base;
 }
 if(p) {live--; free(p);}
}
static void del(curve *p) {drop(p);}
#define free drop
#define free_dmatrix(p,...) drop(p)
#define free_ivector(p,...) drop(p)
#define a1logd(...) ((void)0)
"""
            + release_vector
            + """
static int branch(void *fraw,void *sizepop,void *slope,void *maxval,void *pat,
                  void *points,curve *pcurve) {
 struct {int nraw;} dimensions={2}, *m=&dimensions;
 int nummeas=2;
"""
            + block
            + """ return 0;
}
static void *take(void) {void *p=malloc(16); if(!p) abort(); live++; return p;}
int main(void) {
 int mode; for(mode=0;mode<2;mode++) {
  void *f=take(),*sz=take(),*sl=take(),*mvbase=take(),*pat=take();
  void *mv=(double*)mvbase+MAXVAL_SHIFT;
  maxval_base=mvbase; maxval_value=mv;
  void *points=mode==0?NULL:take(); curve *cv=mode==1?NULL:take();
  if(cv) cv->del=del;
  if(branch(f,sz,sl,mv,pat,points,cv)!=MALLOC_CODE) failures++;
  if(live) {failures++; puts("FAIL partial allocation retained");
   /* Release the original's observed leak outside the extracted branch. */
   if(points) drop(points); if(cv) drop(cv);
  }
 }
 printf("curve: %d failures\\n",failures); return failures!=0;
}
"""
        )
        error = family.upper() + "_INT_MALLOC"
        code = code.replace("MALLOC_CODE", error).replace(
            "MAXVAL_SHIFT", "1" if family == "i1pro" else "0"
        )
        header = (source / f"spectro/{family}_imp.h").read_text()
        code = _define(header, error) + code
        run(label + "/curve-" + family, code, {}, expect_failure=failed)
    # Diagnostic work should be absent at quiet levels, unchanged at level 3.
    if "spectro/munki_imp.c" in texts:
        marker = (
            "\t\t/* CM: The fit's conditioning, in LED voltage-drop codes rather than kelvin. */"
        )
        block = _block(texts["spectro/munki_imp.c"], marker)
        code = (
            """#include <stdio.h>
static int accesses, logs, failures;
#define ledtemp (++accesses, codes)
#define a1logd(...) (++logs)
static void probe(int debug) {
 struct {int debug;} lg={debug}; struct {void *unused; __typeof__(lg) *log;} pp={0,&lg}, *p=&pp;
 double codes[3]={1,2,3}, ref=2, *reftemp=&ref, stt=2; int nummeas=3,i;
 accesses=logs=0;
"""
            + block
            + """
 if(debug<3 && (accesses || logs)) {
  failures++; printf("FAIL quiet level %d touches diagnostics\\n",debug);
 }
 if(debug==3 && (!accesses || logs!=1)) {
  failures++; puts("FAIL verbose diagnostic changed");
 }
}
int main(void) {probe(0);probe(2);probe(3);
 printf("debug: %d failures\\n",failures);return failures!=0;}
"""
        )
        run(label + "/debug", code, {}, expect_failure=failed)
