"""ColorMunki nonadaptive calibration; the actual calibrate, no transport or hardware."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
from native_argyll import munki_nonadaptive_calibration as proof  # noqa: E402

# Independent regression anchors retained from the authenticated repair evidence.
BASELINE_SHA256 = "3ed587355460faff06544dceb4c04c2c3a3f2d7edb47d6994d528c247219b2ad"
CORRECTED_SELECTION = "if (ev == MUNKI_RD_SENSORSATURATED || (ev == MUNKI_OK && scale < 1.0)) {"
INVALIDATE = (
    "\t\t\ts->dispswap = 0;\n"
    "\t\t\ts->done_dintsel = 0;\t\t/* CM: a selection being redone is not complete "
    "*/\n"
    "\n"
    "\t\t\t/* Simply measure the full display white, and if it's close to */\n"
)
ORIGINAL_SELECTION = "if (ev == MUNKI_RD_SENSORSATURATED || scale < 1.0) {"
WHITE_LOG = '\t\t\t\ta1logd(p->log,3,"Needed scale is %f\\n",scale);\n'
WHITE_LOGGED_AFTER = (
    "\t\t\t\t\tev = MUNKI_OK;\n"
    "\t\t\t\t}\n"
    "\t\t\t\tif (ev != MUNKI_OK) {\n"
    "\t\t\t\t\tm->mmode = mmode;           /* Restore actual mode */\n"
    "\t\t\t\t\treturn ev;\n"
    "\t\t\t\t}\n"
    '\t\t\t\ta1logd(p->log,3,"Needed scale is %f\\n",scale);\t/* CM: once it is '
    "written */\n"
)


def test_unknown_source_and_archive_refused_before_writes(tmp_path):
    archive = tmp_path / "untrusted.zip"
    archive.write_bytes(b"not the pinned archive")
    output = tmp_path / "proof"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove(archive, output)
    assert not output.exists()


def test_a_failed_probe_no_longer_decides_the_exposure(
    argyll_archive, tmp_path, prepared_production
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    output = tmp_path / "proof"
    report = proof.prove(argyll_archive, output)
    assert report["status"] == "passed"
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report["debug_logging"] == proof.LOGGING_SCOPE
    assert proof.LOGGING_SCOPE in report["limitations"]
    assert report == json.loads((output / "report.json").read_text())
    assert (report["baseline_sha256"], report["patched_sha256"]) == (
        BASELINE_SHA256,
        proof.digest((prepared_production / proof.SOURCE_PATH).read_bytes()),
    )
    baseline, patched = report["results"]["baseline"], report["results"]["patched"]
    failed = set(baseline["failed"])
    assert failed == proof.EXPECTED["baseline"]["failed"]
    # The stale swap to the shortest exposure after a failed second probe, on a
    # first selection and on a forced one, and the read that then uses it.
    for case in ("F2", "F2r", "F2i", "F3", "F4", "F6"):
        assert f"{case}: selected dispswap" in failed, case
        assert f"{case}: read exposure" in failed, case
    # A failed forced re-selection kept its completion and reported nothing needed.
    for case in ("F3", "F4", "F5", "F6"):
        assert f"{case}: done_dintsel" in failed, case
        assert f"{case}: get_n_a_cals reports int-time selection needed" in failed, case
    assert "A2: mk_tele_spot_na stays at the bank its valid scale chose" in failed
    assert "R1: scale not logged after the failed probe" in failed
    # Nothing a valid selection does is among the original's failures.
    valid = {
        "L1",
        "W1",
        "W2",
        "W3",
        "W4",
        "W5",
        "W6",
        "V1",
        "V2",
        "V3",
        "V4",
        "V5",
        "S1",
        "F3c",
        "A1",
    }
    assert not {label for label in failed if label.split(":")[0] in valid}
    for variant in ("baseline", "patched"):
        extract = (output / variant / "extracted.inc").read_text()
        assert proof.DEBUG_OBSERVER in extract
        log = (output / variant / "main.log").read_text()
        observer_check = next(
            line for line in log.splitlines() if "debug argument side effect not evaluated" in line
        )
        assert observer_check.endswith("PASS")
    assert (patched["failures"], patched["failed"], patched["returncode"]) == (0, [], 0)
    assert patched["checks"] == baseline["checks"] and patched["probes"] == baseline["probes"]
    repaired_only = report["results"]["repaired_only"]
    assert (repaired_only["failures"], repaired_only["returncode"]) == (0, 0)
    assert repaired_only["checks"] == proof.EXPECTED["repaired_only"]["checks"] > 0
    with pytest.raises(FileExistsError):
        proof.prove(argyll_archive, output)


def test_sanitizer_diagnostic_cannot_be_reported_as_a_pass(argyll_archive, tmp_path, monkeypatch):
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    # Probe compiler support before the test, since sanitizers are optional on
    # some supported build hosts. This executable is never run.
    support = subprocess.run(
        [
            cc,
            "-x",
            "c",
            "-",
            "-fsanitize=address,undefined",
            "-fno-sanitize-recover=all",
            "-o",
            str(tmp_path / "sanitizer-support"),
        ],
        input="int main(void) { return 0; }\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    if support.returncode:
        pytest.skip("Compiler cannot link address/undefined sanitizers")
    module = tmp_path / "source" / "munki_nonadaptive_calibration.py"
    module.parent.mkdir()
    original_module = Path(proof.__file__)
    module.write_bytes(original_module.read_bytes())
    harness = original_module.with_name("munki_nonadaptive_probe.c").read_text()
    anchor = 'const char *run = argc > 1 ? argv[1] : "main";'
    assert harness.count(anchor) == 1
    # A deliberate harness-only overflow tests the evidence runner, not Argyll.
    harness = harness.replace(
        anchor, anchor + "\n\tvolatile int sanitizer_canary = 2147483647;\n\tsanitizer_canary += 1;"
    )
    module.with_name("munki_nonadaptive_probe.c").write_text(harness)
    monkeypatch.setattr(proof, "__file__", str(module))
    output = tmp_path / "canary-proof"
    with pytest.raises(AssertionError, match="Sanitizer diagnostic in baseline/main"):
        proof.prove(argyll_archive, output, sanitize=True)
    assert "runtime error: signed integer overflow" in (output / "baseline/main.log").read_text()
    assert json.loads((output / "report.json").read_text())["status"] == "incomplete"


@pytest.mark.parametrize(
    ("old", "new", "seen_as"),
    [
        # Only the first site guarded: the second still decides from the stale scale.
        (
            "\t\t\t\t" + CORRECTED_SELECTION,
            "\t\t\t\t" + ORIGINAL_SELECTION,
            "F2: selected dispswap",
        ),
        # Completion not cleared: a failed forced re-selection stays complete.
        (INVALIDATE.splitlines(True)[1], "", "F3: done_dintsel"),
        # The white scale logged where the original logged it, before the probe's
        # result is looked at.
        (
            "\t\t\t\tif (ev == MUNKI_RD_SENSORSATURATED) {\n\t\t\t\t\tscale = 0.0;",
            WHITE_LOG + "\t\t\t\tif (ev == MUNKI_RD_SENSORSATURATED) {\n\t\t\t\t\tscale = 0.0;",
            "R1: scale not logged after the failed probe",
        ),
    ],
    ids=["second-site-only", "still-complete", "log-before-check"],
)
def test_a_regressed_repair_is_seen_by_the_proof(
    argyll_archive, tmp_path, monkeypatch, old, new, seen_as, mutate_production
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")

    def regressed(text):
        corrected = text
        assert corrected.count(old) == 1
        return corrected.replace(old, new)

    source = mutate_production([proof.SOURCE_PATH], lambda text, path: regressed(text))
    with pytest.raises(AssertionError, match=f"patched result.*{seen_as}"):
        proof.prove(argyll_archive, tmp_path / "regressed", source=source)


def test_first_selection_guard_refuses_failed_probe_with_zero_initialized_scale(
    argyll_archive, tmp_path, monkeypatch, mutate_production
):
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    flag = "-ftrivial-auto-var-init=zero"
    support = subprocess.run(
        [cc, flag, "-x", "c", "-c", "-", "-o", str(tmp_path / "zero-init.o")],
        input="int unit(void) { return 0; }\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    if support.returncode:
        pytest.skip("Compiler does not support deterministic local initialization")
    original_flags = proof.sanitizers.flags
    monkeypatch.setattr(
        proof.sanitizers, "flags", lambda sanitize: (*original_flags(sanitize), flag)
    )
    old = "\n\t\t\t" + CORRECTED_SELECTION
    new = "\n\t\t\t" + ORIGINAL_SELECTION

    def regressed(text):
        corrected = text
        assert corrected.count(old) == 1
        return corrected.replace(old, new)

    source = mutate_production([proof.SOURCE_PATH], lambda text, path: regressed(text))
    with pytest.raises(
        AssertionError,
        match=(
            "Unexpected ColorMunki calibration repaired_only result: .*IR1: the error is returned"
        ),
    ):
        proof.prove(argyll_archive, tmp_path / "first-site-regressed", source=source)
