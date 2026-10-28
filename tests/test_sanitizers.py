"""Sanitizer runs of every source-bound proof fail closed; no runner recovers."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
from native_argyll import (  # noqa: E402
    ccss_cgats,
    display_timing_spectral,
    disptype_list_member_release,
    disptype_list_release,
    filter_values,
    i1pro3_wavelength_finite,
    ieee754_special,
    legacy_scan_recognition,
    munki_curve_release,
    munki_dark_copy,
    munki_ledtemp_slope,
    munki_scan_margin,
    munki_white_buffer_release,
    sanitizers,
    scan_normalization,
    scan_tolerance_option,
    ss_protocol,
    xcalstd_option,
)

NATIVE = ROOT / "tools" / "native_argyll"
# A deliberate harness-only signed overflow; it tests the evidence runner, not Argyll.
CANARY = "\t{ volatile int sanitizer_canary = 2147483647; sanitizer_canary += 1; }\n"
RUNNERS = ("conversion_allocation.py", "disptype_list_member_release.py",
           "display_timing.py", "display_timing_spectral.py",
           "disptype_list_release.py", "filter_values.py", "i1pro3_wavelength_finite.py",
           "ccss_cgats.py", "ieee754_special.py",
           "legacy_scan_recognition.py",
           "munki_curve_release.py", "munki_dark_copy.py", "munki_ledtemp_slope.py",
           "munki_nonadaptive_calibration.py",
           "munki_scan_margin.py",
           "munki_white_buffer_release.py", "native_resync_proof.py",
           "scan_normalization.py", "scan_finite.py",
           "scan_tolerance_option.py", "spyder_black_cache.py",
           "ss_protocol.py", "xcalstd_option.py",
           "xyz_only_conversion.py")


def test_flags_disable_recovery_and_every_marker_is_refused():
    assert sanitizers.flags(True) == list(sanitizers.FLAGS)
    assert "-fno-sanitize-recover=all" in sanitizers.flags(True)
    assert sanitizers.FLOAT_DIVIDE_BY_ZERO == (
        "-fsanitize=float-divide-by-zero",
        "-fno-sanitize-recover=float-divide-by-zero",
    )
    assert sanitizers.flags(False) == []
    assert sanitizers.UNDEFINED_FLAGS == (
        "-fsanitize=undefined,float-cast-overflow", "-fno-sanitize-recover=all",
    )
    assert sanitizers.diagnostic("PASS one\nNeeded scale is 1.0\n") is None
    sanitizers.refuse("PASS one\nNeeded scale is 1.0\n", "baseline/x")
    # This fixed diagnostic does not derive from MARKERS: deleting the ASan
    # marker must make the test fail even if the table-driven loop shrinks.
    with pytest.raises(AssertionError, match="Sanitizer diagnostic in baseline/x; see log"):
        sanitizers.refuse("probe.c:12:3: ERROR: AddressSanitizer: heap-buffer-overflow\n",
                          "baseline/x")
    for marker in sanitizers.MARKERS:
        line = f"probe.c:12:3: {marker} something"
        assert sanitizers.diagnostic(f"PASS one\n{line}\nPASS two\n") == line
        with pytest.raises(AssertionError, match="Sanitizer diagnostic in baseline/x; see log"):
            sanitizers.refuse(f"PASS one\n{line}\n", "baseline/x")


def test_float_to_integer_overflow_has_a_specific_nonrecovering_diagnostic(sanitizing_cc, tmp_path):
    from color_measurement.native_receipt import native_worker_name

    executable = tmp_path / native_worker_name("float-cast-canary")
    subprocess.run(
        [sanitizing_cc, "-x", "c", "-", *sanitizers.flags(True), "-o", str(executable)],
        input="int main(void) { volatile double x = 1e300; volatile int n = (int)x; return n; }\n",
        capture_output=True, text=True, timeout=30, check=True,
    )
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "outside the range of representable values of type 'int'" in result.stderr
    assert sanitizers.diagnostic(result.stderr) is not None


@pytest.mark.parametrize("line", [
    "probe.c:12:3: runtime error: signed integer overflow",
    "probe.c:12:3: ERROR: LeakSanitizer: detected memory leaks",
    "probe.c:12:3: UndefinedBehaviorSanitizer: undefined behaviour",
], ids=["runtime-error", "leak-sanitizer", "undefined-behaviour-sanitizer"])
def test_fixed_non_asan_diagnostics_are_refused(line):
    # Literal fixtures catch a missing marker even if the table-driven loop shrinks.
    stderr = f"PASS one\n{line}\nPASS two\n"
    assert sanitizers.diagnostic(stderr) == line
    with pytest.raises(AssertionError, match="Sanitizer diagnostic in baseline/x; see log"):
        sanitizers.refuse(stderr, "baseline/x")


def test_every_runner_takes_its_flags_and_its_refusal_from_one_place():
    """A runner with private flags could recover again, unseen."""
    owners = sorted(p.name for p in NATIVE.glob("*.py") if "-fsanitize" in p.read_text())
    assert owners == ["sanitizers.py"]
    runners = sorted(p.name for p in NATIVE.glob("*.py")
                     if ("sanitize: bool = False" in p.read_text()
                         or "sanitizers.flags(" in p.read_text()))
    assert runners == sorted(RUNNERS)
    for name in runners:
        text = (NATIVE / name).read_text()
        assert "sanitizers.flags(" in text, name
        assert "sanitizers.refuse(run.stderr, " in text, name


def test_a_new_runner_cannot_declare_sanitization_without_enabling_it(tmp_path, monkeypatch):
    """A missing flags call must not hide a newly declared runner from the census."""
    for source in NATIVE.glob("*.py"):
        shutil.copyfile(source, tmp_path / source.name)
    (tmp_path / "unqualified_runner.py").write_text(
        "def prove(*, sanitize: bool = False):\n    return {}\n"
    )
    monkeypatch.setattr(sys.modules[__name__], "NATIVE", tmp_path)
    with pytest.raises(AssertionError, match="unqualified_runner"):
        test_every_runner_takes_its_flags_and_its_refusal_from_one_place()


@pytest.fixture(scope="module")
def sanitizing_cc(tmp_path_factory):
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    # Sanitizers are optional on some supported build hosts; this executable is never run.
    support = subprocess.run(
        [cc, "-x", "c", "-", *sanitizers.FLAGS,
         "-o", str(tmp_path_factory.mktemp("sanitizer-support") / "support")],
        input="int main(void) { return 0; }\n", capture_output=True, text=True, timeout=30)
    if support.returncode:
        pytest.skip("Compiler cannot link address/undefined sanitizers")
    return cc


def _copy_with_canary(tmp_path, module, files, anchor):
    """A copy of the runner whose first harness file overflows right after `anchor`."""
    source = tmp_path / "source"
    source.mkdir()
    original = Path(module.__file__)
    (source / original.name).write_bytes(original.read_bytes())
    for name in files:
        text = original.with_name(name).read_text()
        if name == files[0]:
            assert text.count(anchor) == 1
            text = text.replace(anchor, anchor + CANARY)
        (source / name).write_text(text)
    return source / original.name


@pytest.mark.parametrize(("module", "files", "anchor", "first"), [
    # The margin recognizer logs on stderr, so this runner once recovered from a
    # diagnostic and reported a pass over it.
    (munki_scan_margin, ("munki_scan_margin_probe.c", "munki_scan_margin_table.c"),
     "\tmemset(&meter, 0, sizeof(meter));\n", "baseline/"),
    (legacy_scan_recognition, ("legacy_scan_probe.c",), "    scenario=argv[1];\n",
     "baseline/valid1"),
    (disptype_list_release, ("disptype_list_release_probe.c",), "\tdel_only();\n",
     "baseline/"),
    (munki_curve_release, ("munki_curve_release_probe.c",), "\tmemset(&meter, 0, sizeof(meter));\n",
     "baseline/valid8"),
    (munki_white_buffer_release, ("munki_white_buffer_release_probe.c",),
     "\tmemset(&meter, 0, sizeof(meter));\n", "baseline/white8"),
    (disptype_list_member_release, ("disptype_list_member_release_probe.c",),
     "\tinstrument.log = &quiet;\n", "baseline/static_ccmx_fails"),
    (munki_ledtemp_slope, ("munki_ledtemp_slope_probe.c",),
     "\tmemset(&meter, 0, sizeof(meter));\n", "baseline/drift8"),
    (munki_dark_copy, ("munki_dark_copy_probe.c", "munki_nonadaptive_probe.c"),
     "    pairing_matrix();\n", "baseline"),
    (ss_protocol, ("ss_protocol_probe.c",),
     "\tp = new_probe_ss();\n", "baseline/moveabs/:D1A001"),
    (ccss_cgats, ("ccss_cgats_probe.c",),
     "    rv = cs->read_ccss(cs, argv[1]);\n", "baseline/real_columns"),
], ids=["munki-scan-margin", "legacy-scan-recognition", "disptype-list-release",
        "munki-curve-release", "munki-white-buffer-release", "disptype-list-member-release",
        "munki-ledtemp-slope", "munki-dark-copy", "ss-protocol", "ccss-cgats"])
def test_a_harness_diagnostic_cannot_be_reported_as_a_pass(
        sanitizing_cc, argyll_archive, tmp_path, monkeypatch, module, files, anchor, first):
    monkeypatch.setattr(module, "__file__",
                        str(_copy_with_canary(tmp_path, module, files, anchor)))
    output = tmp_path / "canary-proof"
    with pytest.raises(AssertionError, match=f"Sanitizer diagnostic in {first}"):
        module.prove(argyll_archive, output, sanitize=True)
    assert json.loads((output / "report.json").read_text())["status"] == "incomplete"
    logs = [p for p in output.rglob("*.log")
            if "runtime error: signed integer overflow" in p.read_text()]
    assert len(logs) == 1 and "baseline" in logs[0].relative_to(output).parts


def test_spectral_delay_harness_diagnostic_cannot_be_reported_as_a_pass(
        sanitizing_cc, argyll_archive, prepared_production, tmp_path, monkeypatch):
    """The repaired-only sanitizer run must not report success after a diagnostic."""
    from native_argyll.proof_inputs import before_source

    anchor = "    memset(&a, 0, sizeof(a)); memset(&b, 0, sizeof(b));\n"
    source = tmp_path / "source"
    source.mkdir()
    probe = Path(display_timing_spectral.__file__).with_name("display_timing_spectral_probe.c")
    text = probe.read_text()
    assert text.count(anchor) == 1
    (source / probe.name).write_text(text.replace(
        anchor, anchor + "#ifdef TIMING_PROOF_REPAIRED\n" + CANARY + "#endif\n"))
    monkeypatch.setattr(display_timing_spectral, "__file__", str(source / "runner.py"))
    texts = {path: before_source("display_timing_spectral", path, argyll_archive)
             for path in display_timing_spectral.SOURCES}
    output = tmp_path / "canary-proof"
    with pytest.raises(AssertionError, match="Sanitizer diagnostic in i1pro/repaired/control"):
        display_timing_spectral.prove(texts, output, compiler=sanitizing_cc,
                                     source=prepared_production, sanitize=True)
    assert json.loads((output / "report.json").read_text())["status"] == "incomplete"
    assert "runtime error: signed integer overflow" in (
        output / "i1pro/repaired/control.log").read_text()
    assert not (output / "i1pro/repaired/overflow.log").exists()


def test_scan_normalization_harness_diagnostic_cannot_be_reported_as_a_pass(
        sanitizing_cc, argyll_archive, tmp_path, monkeypatch):
    """This runner generates its harness in Python, so the canary goes in there."""
    anchor = "if(argc!=2) return 2;scenario=argv[1];\n"
    generate = scan_normalization._probe_source

    def with_canary(source, numlib):
        text = generate(source, numlib)
        assert text.count(anchor) == 1
        return text.replace(anchor, anchor + CANARY)

    monkeypatch.setattr(scan_normalization, "_probe_source", with_canary)
    output = tmp_path / "canary-proof"
    with pytest.raises(AssertionError, match="Sanitizer diagnostic in baseline/finite"):
        scan_normalization.prove(argyll_archive, output, sanitize=True)
    assert json.loads((output / "report.json").read_text())["status"] == "incomplete"
    assert "runtime error: signed integer overflow" in (output / "baseline-finite.log").read_text()
    assert not (output / "patched-finite.log").exists()


def test_i1pro3_wavelength_harness_diagnostic_cannot_be_reported_as_a_pass(
        sanitizing_cc, argyll_archive, tmp_path, monkeypatch):
    """This runner extends the generated scan harness, so the canary goes in there."""
    anchor = "    if(argc!=5) return 2;\n    scenario=argv[1];\n"
    generate = i1pro3_wavelength_finite._probe_source

    def with_canary(source, numlib):
        text = generate(source, numlib)
        assert text.count(anchor) == 1
        return text.replace(anchor, anchor + CANARY)

    monkeypatch.setattr(i1pro3_wavelength_finite, "_probe_source", with_canary)
    output = tmp_path / "canary-proof"
    with pytest.raises(AssertionError,
                       match="Sanitizer diagnostic in baseline/valid/hr0/zebra0/emissive0"):
        i1pro3_wavelength_finite.prove(argyll_archive, output, sanitize=True)
    assert json.loads((output / "report.json").read_text())["status"] == "incomplete"
    assert "runtime error: signed integer overflow" in (
        output / "baseline-valid-hr0-zebra0-emissive0.log").read_text()
    assert not (output / "patched-compile.log").exists()


def test_scan_tolerance_harness_diagnostic_cannot_be_reported_as_a_pass(
        sanitizing_cc, argyll_archive, tmp_path, monkeypatch):
    """This runner generates its harness in Python, so the canary goes in there."""
    anchor = "    p.m=&state;\n"
    generate = scan_tolerance_option._probe_source

    def with_canary(*args):
        text = generate(*args)
        assert text.count(anchor) == 1
        return text.replace(anchor, anchor + CANARY)

    monkeypatch.setattr(scan_tolerance_option, "_probe_source", with_canary)
    output = tmp_path / "canary-proof"
    with pytest.raises(AssertionError, match="Sanitizer diagnostic in i1pro/baseline"):
        scan_tolerance_option.prove(argyll_archive, output, sanitize=True)
    assert json.loads((output / "report.json").read_text())["status"] == "incomplete"
    assert "runtime error: signed integer overflow" in (output / "i1pro-baseline.log").read_text()
    assert not (output / "i1pro-patched.log").exists()


def test_xcalstd_harness_diagnostic_cannot_be_reported_as_a_pass(
        sanitizing_cc, argyll_archive, tmp_path, monkeypatch):
    generate = xcalstd_option._probe_source

    def with_canary(*args):
        text = generate(*args)
        assert text.count("int main(void) {\n") == 1
        return text.replace("int main(void) {\n", "int main(void) {\n" + CANARY)

    monkeypatch.setattr(xcalstd_option, "_probe_source", with_canary)
    output = tmp_path / "canary-proof"
    with pytest.raises(AssertionError, match="Sanitizer diagnostic in baseline"):
        xcalstd_option.prove(argyll_archive, output, sanitize=True)
    assert json.loads((output / "report.json").read_text())["status"] == "incomplete"


def test_filter_values_harness_diagnostic_cannot_be_reported_as_a_pass(
        sanitizing_cc, argyll_archive, tmp_path, monkeypatch):
    """This runner generates its harnesses in Python, so the canary goes in there."""
    anchor = "int main(void) {\n"
    generate = filter_values._converter_source

    def with_canary(*args):
        text = generate(*args)
        assert text.count(anchor) == 1
        return text.replace(anchor, anchor + CANARY)

    monkeypatch.setattr(filter_values, "_converter_source", with_canary)
    output = tmp_path / "canary-proof"
    with pytest.raises(AssertionError, match="Sanitizer diagnostic in baseline/converter"):
        filter_values.prove(argyll_archive, output, sanitize=True)
    assert json.loads((output / "report.json").read_text())["status"] == "incomplete"
    log = (output / "baseline/converter/run.log").read_text()
    assert "runtime error: signed integer overflow" in log
    assert not (output / "patched").exists()


def test_ieee754_special_harness_diagnostic_cannot_be_reported_as_a_pass(
        sanitizing_cc, argyll_archive, tmp_path, monkeypatch):
    """This runner generates its harness in Python, so the canary goes in there."""
    anchor = "int main(void) {\n"
    generate = ieee754_special._probe_source

    def with_canary(text):
        source = generate(text)
        assert source.count(anchor) == 1
        return source.replace(anchor, anchor + CANARY)

    monkeypatch.setattr(ieee754_special, "_probe_source", with_canary)
    output = tmp_path / "canary-proof"
    with pytest.raises(AssertionError, match="Sanitizer diagnostic in baseline"):
        ieee754_special.prove(argyll_archive, output, sanitize=True)
    assert json.loads((output / "report.json").read_text())["status"] == "incomplete"
    assert "runtime error: signed integer overflow" in (output / "baseline.log").read_text()
    assert not (output / "patched.log").exists()


def test_margin_predicate_table_is_sanitized_too(
        sanitizing_cc, argyll_archive, tmp_path, monkeypatch):
    """The same runner compiles a second executable after both recognizers."""
    monkeypatch.setattr(munki_scan_margin, "__file__", str(_copy_with_canary(
        tmp_path, munki_scan_margin,
        ("munki_scan_margin_table.c", "munki_scan_margin_probe.c"), "int main(void) {\n")))
    output = tmp_path / "table-canary"
    with pytest.raises(AssertionError, match="Sanitizer diagnostic in predicate-table"):
        munki_scan_margin.prove(argyll_archive, output, sanitize=True)
    assert json.loads((output / "report.json").read_text())["status"] == "incomplete"
    assert "runtime error: signed integer overflow" in (
        output / "predicate-table" / "table.log").read_text()
