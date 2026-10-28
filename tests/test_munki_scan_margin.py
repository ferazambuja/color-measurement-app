"""ColorMunki strip-margin agreement; the actual recognizer, no transport or hardware."""

import copy
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
import argyll_native_check as native  # noqa: E402
from native_argyll import munki_scan_margin as proof  # noqa: E402
from native_argyll.proof_inputs import before_source  # noqa: E402
from native_argyll.spyder_calibration_slots import _function  # noqa: E402

UNDEFINED = {"zero_margins3", "zero_margins3_ramp", "zero_margins1", "nan_band20", "inf_band20",
             "nan_leader_masks_mismatch", "overflow_masks_mismatch", "overflow_agree"}
CURRENT_AGREEMENT = ("lead_avg == 0.0 || trail_avg == 0.0\n"
                     "\t || !(lead_avg/trail_avg >= 0.7 && trail_avg/lead_avg >= 0.7)")


@pytest.fixture(scope="module")
def production(argyll_archive, tmp_path_factory):
    source, _ = native.prepare(argyll_archive, tmp_path_factory.mktemp("margin") / "build",
                               contribution=native.NATIVE / "contributions/cr_pr")
    return source


def test_unknown_archive_refused_before_writes(tmp_path):
    archive = tmp_path / "untrusted.zip"
    archive.write_bytes(b"not the pinned archive")
    output = tmp_path / "proof"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove(archive, output)
    assert not output.exists()


def test_agreement_predicates_come_from_frozen_and_current_sources(argyll_archive, production):
    baseline = before_source("munki_scan_margin", proof.SOURCE_PATH, argyll_archive)
    patched = (production / proof.SOURCE_PATH).read_text()
    # The table compiles the tests as written, not a restatement of them.
    assert proof._agreement_test(proof.recognizer(baseline)) == (
        "lead_avg/trail_avg < 0.7 || trail_avg/lead_avg < 0.7")
    assert proof._agreement_test(proof.recognizer(patched)) == CURRENT_AGREEMENT
    assert proof._condition(proof.I1PRO_GUARD).startswith("!isfinite(lead_avg)")


def test_the_repaired_recognizer_refuses_undefined_agreement_and_keeps_valid_scans(
        argyll_archive, tmp_path, production):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    output = tmp_path / "proof"
    report = proof.prove(argyll_archive, output, source=production)
    assert report["status"] == "passed"
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report == json.loads((output / "report.json").read_text())
    assert (report["baseline_sha256"], report["patched_sha256"]) == (
        hashlib.sha256(before_source("munki_scan_margin", proof.SOURCE_PATH,
                                     argyll_archive).encode()).hexdigest(),
        hashlib.sha256((production / proof.SOURCE_PATH).read_bytes()).hexdigest())
    assert report["recognizer_sha256"]["baseline"] == report["recognizer_sha256"]["original"]
    baseline, patched = report["results"]["baseline"], report["results"]["patched"]
    assert baseline["valid8"]["retained_curve"] == 1
    assert baseline["valid3"]["retained_curve"] == 0
    assert {row["retained_curve"] for row in patched.values()} == {0}
    assert baseline["nan_patch_rows_only"]["flags"] == 0
    assert patched["nan_patch_rows_only"]["flags"] == 1
    assert set(baseline) == set(patched) == set(proof.CASES) and len(proof.CASES) == 33
    # The original accepts two zero margins and returns correct, finite averages:
    # checking only finiteness cannot expose the missing margin agreement.
    zero = baseline["zero_margins3"]
    assert (zero["code"], zero["nonfinite_output"], zero["changed_values"]) == (0, 0, 384)
    assert zero["max_rel_err_vs_plateau"] < 1e-12
    assert zero["leader_trailer"] == ["0.000000", "0.000000"]
    assert (patched["zero_margins3"]["code"], patched["zero_margins3"]["changed_values"]) == (
        0x42, 0)
    assert {case for case, (before, after) in proof.EXPECTED.items()
            if before != after} == UNDEFINED
    assert {patched[case]["code"] for case in UNDEFINED} == {0x42}
    # A NaN leader hid a genuine 0.4 mismatch; overflowed margins hid another.
    assert baseline["nan_leader_masks_mismatch"]["leader_trailer"][1] == "400.000000"
    assert baseline["overflow_masks_mismatch"]["code"] == 0
    assert baseline["large_no_overflow_mismatch"]["code"] == 0x42
    # Every unchanged control keeps all output values, including nonfinite bands.
    for case in set(proof.CASES) - UNDEFINED:
        assert baseline[case]["code"] == patched[case]["code"], case
        assert baseline[case]["values"] == patched[case]["values"], case
    assert report["analytical_max_rel_error"] <= 1e-12
    table = report["results"]["predicate_table"]
    assert {key: table[key] for key in proof.TABLE} == proof.TABLE and table["returncode"] == 0
    assert len(table["changed_pairs"]) == 43
    assert all(set(pair) & {"NaN", "+inf", "-inf", "+0", "-0"} for pair in table["changed_pairs"])
    for case, field, value in (("nan_patch_rows_only", "flags", 0),
                               ("valid8", "retained_curve", 1)):
        malformed = copy.deepcopy(report["results"])
        malformed["patched"][case][field] = value
        with pytest.raises(AssertionError, match=case):
            proof._validate(malformed)
    with pytest.raises(FileExistsError):
        proof.prove(argyll_archive, output, source=production)


def test_a_changed_unsampled_average_is_seen(
        argyll_archive, tmp_path, production, mutate_production):
    """A one-ULP change in band 37 escaped the former two-band/max-error summary."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    text = (production / proof.SOURCE_PATH).read_text()
    function = _function(text, "munki_extract_patches_multimeas")
    before, marker, after = function.rpartition("return MUNKI_OK;")
    assert marker and text.count(function) == 1
    changed = before + "pavg[0][37] = nextafter(pavg[0][37], INFINITY);\n\t" + marker + after
    source = mutate_production([proof.SOURCE_PATH],
                               lambda current, path: text.replace(function, changed))
    with pytest.raises(AssertionError, match="valid3"):
        proof.prove(argyll_archive, tmp_path / "changed-average", source=source)


@pytest.mark.parametrize(("old", "new", "seen_as"), [
    # The exact threshold no longer meets itself: an accepted boundary control moves.
    (">= 0.7", "> 0.7", "patched boundary_lead_0p7"),
    # One agreeing ratio excuses the other: a genuine mismatch is accepted.
    ("&&", "||", "patched leader_mismatch"),
    # The original test back under the new comment: undefined agreement accepted.
    (CURRENT_AGREEMENT,
     "lead_avg/trail_avg < 0.7 || trail_avg/lead_avg < 0.7", "patched zero_margins3"),
], ids=["boundary-moved", "either-ratio", "original-restored"])
def test_a_regressed_repair_is_seen_by_the_proof(
        argyll_archive, tmp_path, production, mutate_production, old, new, seen_as):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    text = (production / proof.SOURCE_PATH).read_text()
    line = "\tif (" + CURRENT_AGREEMENT + ") {"
    assert text.count(line) == 1 and old in line
    source = mutate_production([proof.SOURCE_PATH],
                               lambda current, path: text.replace(line, line.replace(old, new)))
    with pytest.raises(AssertionError, match=seen_as):
        proof.prove(argyll_archive, tmp_path / "regressed", source=source)
