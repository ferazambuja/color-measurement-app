"""XYZ-only reads convert before the spectrum is dropped; the actual transfers, no hardware."""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
import argyll_native_check as native  # noqa: E402
from native_argyll import xyz_only_conversion as proof  # noqa: E402
from native_argyll.proof_inputs import before_source, fixture_manifest  # noqa: E402
from native_argyll.spyder_calibration_slots import _function  # noqa: E402


@pytest.fixture(scope="module")
def production(argyll_archive, tmp_path_factory):
    source, _ = native.prepare(argyll_archive, tmp_path_factory.mktemp("xyz-conversion") / "build",
                               contribution=native.NATIVE / "contributions/cr_pr")
    return source


@pytest.fixture(scope="module")
def sources(argyll_archive, production):
    return {driver.name: (before_source("xyz_only_conversion", driver.path, argyll_archive),
                          (production / driver.path).read_text()) for driver in proof.DRIVERS}


def final_clearing(function):
    matches = re.findall(r"\tif \(!m->spec_en\) \{\n\t\tfor \(i = 0; i < nvals; i\+\+\)\n"
                         r"\t\t\tvals\[i\]\.sp\.spec_n = 0;\n\t\}\n", function)
    assert len(matches) == 1
    return matches[0]


def test_unknown_source_and_archive_refused_before_writes(tmp_path):
    archive = tmp_path / "untrusted.zip"
    archive.write_bytes(b"not the pinned archive")
    output = tmp_path / "proof"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove(archive, output)
    assert not output.exists()


@pytest.mark.parametrize("driver", proof.DRIVERS, ids=[d.name for d in proof.DRIVERS])
def test_the_clearing_follows_both_current_production_conversions(sources, driver):
    baseline, patched = sources[driver.name]
    declared = fixture_manifest()["proofs"]["xyz_only_conversion"][driver.path]
    assert proof.digest(baseline.encode()) == declared["sha256"]
    before, after = _function(baseline, driver.function), _function(patched, driver.function)
    # The in-loop clearing is gone; one clearing remains, after both converters.
    assert before.count("\t\tif (!m->spec_en) {\n") == 1
    assert "\t\tif (!m->spec_en) {\n" not in after
    assert before.count("if (!m->spec_en)") == after.count("if (!m->spec_en)") == 1
    assert before.count("spec_n = 0;") == after.count("spec_n = 0;") == 2
    assert after.index("ipatch_convert_xrga(") < after.index("ipatch_convert_custom_filter(")
    clearing = final_clearing(after)
    assert after.index("ipatch_convert_custom_filter(") < after.index(clearing)
    assert after.index(clearing) < after.rindex("return ")
    assert before.index("\t\tif (!m->spec_en) {\n") < before.index("ipatch_convert_xrga(")


def test_an_xyz_only_read_now_carries_the_converted_value(argyll_archive, tmp_path, production,
                                                        sources):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    output = tmp_path / "proof"
    report = proof.prove(argyll_archive, output, source=production)
    assert report["status"] == "passed"
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report == json.loads((output / "report.json").read_text())
    for driver in proof.DRIVERS:
        assert report["baseline_sha256"][driver.path] == proof.digest(
            sources[driver.name][0].encode())
        assert report["patched_sha256"][driver.path] == proof.digest(
            (production / driver.path).read_bytes())
        baseline = report["results"][driver.name]["baseline"]
        patched = report["results"][driver.name]["patched"]
        expected = proof.EXPECTED[driver.name]
        converted = expected["failed"]["xyz_only_target_converted"]
        filtered = expected["failed"]["xyz_only_filter_applied"]
        # The original fails exactly the two checks that ask for the conversion
        # and the filter on an XYZ-only read; every control holds in both texts.
        assert baseline["failed"] == expected["failed"]
        assert baseline["failures"] == converted + filtered and baseline["returncode"] == 1
        assert baseline["xyz_only_target"] == {"skipped": converted, "converted": 0, "other": 0}
        assert baseline["xyz_only_filter"] == {"skipped": filtered, "applied": 0, "other": 0}
        assert (patched["failures"], patched["failed"], patched["returncode"]) == (0, {}, 0)
        assert patched["xyz_only_target"] == {"skipped": 0, "converted": converted, "other": 0}
        assert patched["xyz_only_filter"] == {"skipped": 0, "applied": filtered, "other": 0}
        assert patched["reads"] == baseline["reads"]
        # Current custom-filter safety adds 26 unclamped scratch converters.
        assert (baseline["unclamped_integrators"], patched["unclamped_integrators"]) == (0, 26)
        assert patched["integrators"] - baseline["integrators"] == converted // 24 + 1 + 26
        log = (output / driver.name / "baseline" / "main.log").read_text()
        assert log.count("FAIL xyz_only_target_converted") == converted
        assert log.count("FAIL xyz_only_filter_applied") == filtered
        patch = (output / f"{driver.name}.patch").read_text()
        assert "+\t/* Don't return spectral if not asked for. CM:" in patch
        assert "-\t\tif (!m->spec_en)" in patch
    with pytest.raises(FileExistsError):
        proof.prove(argyll_archive, output, source=production)


def test_sanitizer_diagnostic_cannot_be_reported_as_a_pass(argyll_archive, tmp_path, monkeypatch):
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    support = subprocess.run(
        [cc, "-x", "c", "-", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
         "-o", str(tmp_path / "sanitizer-support")],
        input="int main(void) { return 0; }\n", capture_output=True, text=True, timeout=30)
    if support.returncode:
        pytest.skip("Compiler cannot link address/undefined sanitizers")
    module = tmp_path / "source" / "xyz_only_conversion.py"
    module.parent.mkdir()
    original_module = Path(proof.__file__)
    module.write_bytes(original_module.read_bytes())
    harness = original_module.with_name("xyz_only_conversion_probe.c").read_text()
    anchor = "\tcm_grid(&imp);\n"
    assert harness.count(anchor) == 1
    # A deliberate harness-only overflow tests the evidence runner, not Argyll.
    harness = harness.replace(anchor, anchor
        + "\t{ volatile int sanitizer_canary = 2147483647; sanitizer_canary += 1; }\n")
    module.with_name("xyz_only_conversion_probe.c").write_text(harness)
    monkeypatch.setattr(proof, "__file__", str(module))
    output = tmp_path / "canary-proof"
    with pytest.raises(AssertionError, match="Sanitizer diagnostic in i1pro/baseline"):
        proof.prove(argyll_archive, output, sanitize=True)
    assert "runtime error: signed integer overflow" in (
        output / "i1pro/baseline/main.log").read_text()
    assert json.loads((output / "report.json").read_text())["status"] == "incomplete"


@pytest.mark.parametrize(("name", "regress", "seen_as"), [
    # The spectrum dropped between the two converters: the filter is skipped again.
    ("munki", "filter-after-clearing",
     "patched result for munki.*xyz_only_filter_applied"),
    # One driver left as upstream has it while the other two are repaired.
    ("i1pro3", None, "patched result for i1pro3.*xyz_only_target_converted"),
], ids=["filter-after-clearing", "one-driver-unrepaired"])
def test_a_regressed_repair_is_seen_by_the_proof(argyll_archive, tmp_path, production, sources,
                                                 name, regress, seen_as):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    driver, = [driver for driver in proof.DRIVERS if driver.name == name]
    target = production / driver.path
    original = target.read_text()
    function = _function(original, driver.function)
    if regress is None:
        changed = _function(sources[name][0], driver.function)
    else:
        clearing = final_clearing(function)
        changed = function.replace(clearing, "")
        anchor = "\t/* Apply custom filter compensation */"
        assert changed.count(anchor) == 1
        changed = changed.replace(anchor, clearing + "\n" + anchor, 1)
    assert changed != function
    try:
        target.write_text(original.replace(function, changed, 1))
        with pytest.raises(AssertionError, match=seen_as):
            proof.prove(argyll_archive, tmp_path / "regressed", source=production)
    finally:
        target.write_text(original)


def test_equal_projection_cannot_hide_a_different_integrator_input(
        argyll_archive, tmp_path, monkeypatch):
    """A grid change is invisible to the stand-in's sums, but its input check sees it."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    module = tmp_path / "source" / "xyz_only_conversion.py"
    module.parent.mkdir()
    original = Path(proof.__file__)
    module.write_bytes(original.read_bytes())
    harness = original.with_name("xyz_only_conversion_probe.c").read_text()
    # Perturb only the integrator's local input view for XYZ-only target reads.
    # Keep the driver's spectrum and projected XYZ untouched. An output-only
    # comparison cannot see this; the direct active-input comparison must fail.
    anchor = "static int g_input_index;\n"
    assert harness.count(anchor) == 1
    harness = harness.replace(anchor, anchor + "static int input_canary;\n")
    anchor = "\tg_integrated[g_input_index++ % NSAMP] = *in;\n"
    assert harness.count(anchor) == 1
    harness = harness.replace(anchor,
        "\txspect altered = *in;\n"
        "\tif (input_canary) { altered.spec_wl_short += 1.0; in = &altered; }\n" + anchor)
    anchor = "\tev = cm_convert(p, out, NSAMP, g_specrd);\n"
    assert harness.count(anchor) == 1
    harness = harness.replace(anchor, "\tinput_canary = out == g_D;\n" + anchor
                              + "\tinput_canary = 0;\n")
    module.with_name("xyz_only_conversion_probe.c").write_text(harness)
    monkeypatch.setattr(proof, "__file__", str(module))
    output = tmp_path / "mismatched-input"
    with pytest.raises(AssertionError, match="patched result for i1pro.*xyz_only_target_converted"):
        proof.prove(argyll_archive, output)
    result = json.loads((output / "report.json").read_text())["results"]["i1pro"]["patched"]
    assert result["failed"] == {"xyz_only_target_converted": 96}
    # All 96 projected XYZ values still agree; only the input spectra differ.
    assert result["xyz_only_target"] == {"skipped": 0, "converted": 96, "other": 0}


@pytest.mark.parametrize("returncode", [-11, 0, 2])
def test_baseline_process_must_exit_for_the_expected_assertions(returncode):
    """Complete expected JSON cannot turn a crash/other exit into a valid baseline."""
    results = {}
    for name, expected in proof.EXPECTED.items():
        converted = expected["failed"]["xyz_only_target_converted"]
        filtered = expected["failed"]["xyz_only_filter_applied"]
        results[name] = {}
        for index, label in enumerate(("baseline", "patched")):
            results[name][label] = dict(
                checks=expected["checks"][index], lookups=expected["lookups"][index],
                unclamped_integrators=26 if index else 0,
                failures=converted + filtered if index == 0 else 0,
                failed=expected["failed"] if index == 0 else {}, returncode=1 - index,
                xyz_only_target={"skipped": converted if index == 0 else 0,
                                 "converted": converted if index == 1 else 0},
                xyz_only_filter={"skipped": filtered if index == 0 else 0,
                                 "applied": filtered if index == 1 else 0})
    proof._validate(results)
    results["i1pro"]["baseline"]["returncode"] = returncode
    with pytest.raises(AssertionError, match=f"baseline process for i1pro: exit {returncode}"):
        proof._validate(results)
