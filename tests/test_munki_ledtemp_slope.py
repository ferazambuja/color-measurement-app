"""Unidentifiable or nonfinite ColorMunki thermal compensation is refused, not published."""

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
from conftest import copy_proof_with_probe_mutation  # noqa: E402
from native_argyll import munki_ledtemp_slope as proof  # noqa: E402
from native_argyll.proof_inputs import before_source  # noqa: E402
from native_argyll.spyder_calibration_slots import _function  # noqa: E402

REFUSED = {case for case, row in proof.EXPECTED.items() if row["refusal"] is not None}
SPREAD = {case for case, row in proof.EXPECTED.items() if row["refusal"] == "spread"}


@pytest.fixture(scope="module")
def sources(argyll_archive, tmp_path_factory):
    source, _ = native.prepare(argyll_archive, tmp_path_factory.mktemp("ledtemp") / "build",
                               contribution=native.NATIVE / "contributions/cr_pr")
    baseline = before_source("munki_ledtemp_slope", proof.SOURCE_PATH, argyll_archive)
    return source, baseline, (source / proof.SOURCE_PATH).read_text()


def test_unknown_archive_refused_before_writes(tmp_path):
    archive = tmp_path / "untrusted.zip"
    archive.write_bytes(b"not the pinned archive")
    output = tmp_path / "proof"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove(archive, output)
    assert not output.exists()


def test_production_guards_precede_writes_and_callers_refuse_before_publication(sources):
    _, baseline, patched = sources
    before, after = _function(baseline, proof.FUNCTION), _function(patched, proof.FUNCTION)
    guard = after.index("if (i == nummeas) {")
    refusal = after.index("return MUNKI_RD_WHITEREADINCONS;", guard)
    assert guard < refusal < after.index("*reftemp =")
    assert "free_dvector(ledtemp, 0, nummeas-1);" in after[guard:refusal]
    assert "free_dmatrix(multimes, 0, nummeas-1, -1, m->nraw-1);" in after[guard:refusal]
    assert before.count("b /= stt;") == after.count("b /= stt;") == 1
    # Preserve divide-then-multiply order; validate before publishing the product.
    comp_before = _function(baseline, proof.COMPENSATION)
    comp_after = _function(patched, proof.COMPENSATION)
    assert comp_before.count("targ/attemp") == comp_after.count("targ/attemp") == 1
    assert (comp_after.index("comp = absraw[i][w] * (targ/attemp);")
            < comp_after.index("if (!isfinite(attemp) || !isfinite(comp))")
            < comp_after.index("return MUNKI_RD_WHITEREADINCONS;")
            < comp_after.index("absraw[i][w] = comp;"))
    # Both callers: invalidation inside the existing error branch only.
    caller_after = _function(patched, "munki_imp_calibrate")
    reader_after = _function(patched, proof.READER)
    # The calibration caller returns the error before cal_valid=1 and before saving cache.
    caller = proof.caller_fragment(patched)
    assert caller.index("s->cal_valid = 0") < caller.index("return ev;")
    assert caller_after.index(caller) < caller_after.index("s->cal_valid = 1;")
    assert caller_after.index(caller) < caller_after.index("munki_save_calibration(p);")
    # The reading caller invalidates before its releases and returns before the
    # conversion that would publish the reading.
    reader = proof.read_caller_fragment(patched)
    assert reader.index("s->cal_valid = 0") < reader.index("free_dvector(ledtemp")
    assert reader_after.index(reader) < reader_after.index("munki_absraw_to_abswav(p, numpatches")


def test_refusals_leave_no_valid_state_and_controls_are_identical(argyll_archive, tmp_path,
                                                               sources):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    output = tmp_path / "proof"
    production, before, current = sources
    report = proof.prove(argyll_archive, output, source=production)
    assert report["status"] == "passed"
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report == json.loads((output / "report.json").read_text())
    assert report["baseline_sha256"] == hashlib.sha256(before.encode()).hexdigest()
    assert report["patched_sha256"] == hashlib.sha256(current.encode()).hexdigest()
    baseline, patched = report["results"]["baseline"], report["results"]["patched"]
    for case, expected in proof.EXPECTED.items():
        count = expected["iterations"]
        if expected["kind"] == proof.CONSUMERS:
            for label, nan_flag in ((baseline, 0), (patched, 1)):
                assert label[case]["avg_inconsistent"] == [1]
                assert label[case]["avg_inconsistent_nan_band"] == [nan_flag]
                assert label[case]["converted_finite_unused"] == [1]
                assert label[case]["converted_finite_zero_coef"] == [0]
            continue
        assert baseline[case]["codes"] == [0] * count
        assert patched[case]["codes"] == [0x34 if case in REFUSED else 0] * count
        assert patched[case]["local_blocks"] == [0] * count
        if expected["kind"] == proof.READ and case in REFUSED:
            assert patched[case]["finite"] == patched[case]["output_fnv"] == [None] * count
        else:
            assert patched[case]["finite"] == [1] * count
        if case in REFUSED:
            # Denominator overflow produces a finite zero; other failures publish NaN/Inf.
            assert baseline[case]["finite"] == [int(expected.get("baseline_finite", False))] * count
            assert baseline[case]["cal_valid"] == [1] * count   # and keeps the state valid
            assert patched[case]["cal_valid"] == [0] * count
            assert patched[case]["want_calib"] == [1] * count
            if expected["kind"] == proof.CALIBRATION:
                assert patched[case]["cache_calls"] == [0] * count
                assert patched[case]["white_unchanged"] == [1] * count
                assert patched[case]["unchanged"] == [int(case in SPREAD)] * count
                assert patched[case]["table_written"] == [int(case not in SPREAD)] * count
        else:
            assert baseline[case]["output_fnv"] == patched[case]["output_fnv"]
    # An initialized flag cannot stand for an unpublished numeric observation.
    unavailable = copy.deepcopy(report["results"])
    unavailable["patched"]["read_dead_band"]["finite"] = [1]
    with pytest.raises(AssertionError, match="patched result for read_dead_band: finite"):
        proof._validate(unavailable)
    unflagged = copy.deepcopy(report["results"])
    unflagged["patched"]["consumers_dead_band"]["avg_inconsistent_nan_band"] = [0]
    with pytest.raises(AssertionError, match="consumers_dead_band: avg_inconsistent_nan_band"):
        proof._validate(unflagged)
    # The two accepted sign cases are accepted, and identically, in both texts.
    for case in ("read_negative_fit", "read_negative_factor"):
        assert baseline[case]["codes"] == patched[case]["codes"] == [0]
    for key in ("codes", "finite", "output_fnv", "omitted-read"):
        malformed = copy.deepcopy(report["results"])
        for variant in malformed.values():
            row = variant["flat8_repeat5"]
            if key == "omitted-read":
                row["iterations"] -= 1
                for value in row.values():
                    if isinstance(value, list):
                        value.pop()
            else:
                row[key].append(row[key][-1])
        with pytest.raises(AssertionError, match="read count for flat8_repeat5"):
            proof._validate(malformed)
    with pytest.raises(FileExistsError):
        proof.prove(argyll_archive, output, source=production)


@pytest.mark.parametrize("mutation,seen_as", [
    # Without the spread guard the product guard still refuses the NaN table, but
    # only after the reference and table were written.
    ("dropped", "patched result for flat8: unchanged"),
    ("zero-fallback", "patched result for flat8: codes"),
    ("valid-state", "patched result for flat8: cal_valid"),
    ("vector-leak", "patched result for flat8: local allocation leak"),
    ("matrix-leak", "patched result for flat8: local allocation leak"),
    # The later average refuses the NaN with a different error; it cannot replace
    # the compensation guard's specific refusal.
    ("product-guard-dropped", "patched result for dead_first_band8: codes"),
    ("factor-only", "patched result for read_overflow_later_sample: codes"),
    ("denominator-guard-dropped", "patched result for read_denominator_overflow: codes"),
    ("other-error-code", "patched result for dead_first_band8: codes"),
    ("read-valid-state", "patched result for read_dead_band: cal_valid"),
    ("second-sample-skipped", "patched result for second_step8: codes"),
])
def test_a_regressed_guard_is_seen_by_the_proof(
        argyll_archive, tmp_path, sources, mutate_production, mutation, seen_as):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    def regressed(text):
        white = _function(text, proof.FUNCTION)
        start = white.index("\t{\n\t\tint i;\n\t\tfor (i = 1;")
        end = white.index("\n\t}\n", start) + len("\n\t}\n")
        spread_guard = white[start:end]
        if mutation in ("dropped", "zero-fallback"):
            changed = white.replace(spread_guard, "")
            if mutation == "zero-fallback":
                changed = changed.replace("\t\t\tb /= stt;\n",
                    "\t\t\tif (stt > 0.0)\n\t\t\t\tb /= stt;\n"
                    "\t\t\telse\n\t\t\t\tb = 0.0;\n")
            return text.replace(white, changed)
        if mutation == "second-sample-skipped":
            return text.replace(white, white.replace(spread_guard,
                                spread_guard.replace("i = 1;", "i = 2;")))
        if mutation == "valid-state":
            caller = proof.caller_fragment(text)
            assert caller.count("s->cal_valid = 0;") == 1
            return text.replace(caller, caller.replace("s->cal_valid = 0;", "s->cal_valid = 1;"))
        if mutation == "read-valid-state":
            caller = proof.read_caller_fragment(text)
            assert caller.count("s->cal_valid = 0;") == 1
            return text.replace(caller, caller.replace("s->cal_valid = 0;", "s->cal_valid = 1;"))
        comp = _function(text, proof.COMPENSATION)
        condition = "\t\t\tif (!isfinite(attemp) || !isfinite(comp)) {\n"
        start = comp.index(condition)
        end = comp.index("\n\t\t\t}\n", start) + len("\n\t\t\t}\n")
        product_guard = comp[start:end]
        if mutation == "product-guard-dropped":
            return text.replace(comp, comp.replace(product_guard, ""))
        if mutation == "factor-only":
            # Regression: check a finite positive factor instead of the product.
            return text.replace(comp, comp.replace(condition,
                "\t\t\tif (!isfinite(targ/attemp) || targ/attemp <= 0.0) {\n"))
        if mutation == "denominator-guard-dropped":
            return text.replace(comp, comp.replace(
                "!isfinite(attemp) || !isfinite(comp)", "!isfinite(comp)"))
        if mutation == "other-error-code":
            return text.replace(comp, comp.replace(
                "return MUNKI_RD_WHITEREADINCONS;", "return MUNKI_RD_READINCONS;"))
        release = ("free_dvector(ledtemp, 0, nummeas-1);" if mutation == "vector-leak"
                   else "free_dmatrix(multimes, 0, nummeas-1, -1, m->nraw-1);")
        return text.replace(white, white.replace(spread_guard, spread_guard.replace(release, ";")))

    current = sources[2]
    changed = regressed(current)
    assert changed != current, mutation
    source = mutate_production([proof.SOURCE_PATH], lambda current, path: changed)
    with pytest.raises(AssertionError, match=seen_as):
        proof.prove(argyll_archive, tmp_path / "regressed", source=source)


def test_an_earlier_read_that_differs_is_seen(argyll_archive, tmp_path, monkeypatch, sources):
    """The five reads are compared individually, not only the last one."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    old = "\t\thashes[it] = fnv_add(h, reftemp);\n"
    monkeypatch.setattr(proof, "__file__", str(copy_proof_with_probe_mutation(proof,
        tmp_path, old, old + "\t\tif (it == 0 && c->repeat > 1) hashes[it] ^= 1;\n")))
    with pytest.raises(AssertionError,
                       match="iterations for flat8_repeat5 differ within baseline"):
        proof.prove(argyll_archive, tmp_path / "earlier-read", source=sources[0])
