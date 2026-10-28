"""ColorMunki strip recognizer releases its fitted curve on every exit; no hardware."""

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
from conftest import copy_proof_with_probe_mutation  # noqa: E402
from native_argyll import munki_curve_release as proof  # noqa: E402
from native_argyll import sanitizers  # noqa: E402
from native_argyll.proof_inputs import fixture_manifest  # noqa: E402
from native_argyll.spyder_calibration_slots import _function  # noqa: E402

RELEASING = ("valid8", "valid8_repeat5", "sparse6_8", "alloc_points_valid8")

# ArgyllCMS mutation excerpts retain Graeme W. Gill's GPL-2.0-or-later terms.
SUCCESS = "\tfree(pat);\t\t/* Otherwise caller will have to do it */\n"
SUCCESS_RELEASED = (SUCCESS
                    + "\tif (pcurve != NULL)\t\t/* CM: released on success */\n"
                    + "\t\tpcurve->del(pcurve);\n")
ALLOC_FAILURE = ("\t\tif (points == NULL || pcurve == NULL) {\n"
                 "\t\t\tfree_dmatrix(fraw, 0, nummeas-1, 0, NFB-1);\n"
                 "\t\t\tfree_ivector(sizepop, 0, nummeas-1);\n"
                 "\t\t\tfree_dvector(slope, 0, nummeas-1);  \n"
                 "\t\t\tfree_dvector(maxval, -1, m->nraw-1);  \n"
                 "\t\t\tfree(pat);\n")
ALLOC_FAILURE_RELEASED = (ALLOC_FAILURE
                          + "\t\t\tfree(points);\t\t\t/* CM: whichever of the two was made */\n"
                          + "\t\t\tif (pcurve != NULL)\n"
                          + "\t\t\t\tpcurve->del(pcurve);\n")


def test_unknown_archive_refused_before_writes(tmp_path):
    archive = tmp_path / "untrusted.zip"
    archive.write_bytes(b"not the pinned archive")
    output = tmp_path / "proof"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove(archive, output)
    assert not output.exists()


def test_late_sparse_exit_releases_the_curve_before_returning(prepared_production):
    """No current synthetic strip reaches this exit; retain its source safety check."""
    text = (prepared_production / proof.SOURCE_PATH).read_text()
    function = _function(text, proof.RECOGNIZER)
    release = ("\t\t\tfree(pat);\n"
               "\t\t\tif (pcurve != NULL)\t\t/* CM: released on this exit too */\n"
               "\t\t\t\tpcurve->del(pcurve);\n")
    log = '\t\t\ta1logd(p->log,1,"Patch recog failed - patches sampled too sparsely\\n");\n'
    assert function.count(release + log) == 1
    site = function.index(release + log)
    assert function.index("pcurve->del(pcurve);", site) < function.index(log, site)
    assert function.index(log, site) < function.index("return MUNKI_RD_NOTENOUGHSAMPLES;", site)


def test_every_retained_curve_and_block_is_released(
    argyll_archive, prepared_production, tmp_path
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    output = tmp_path / "proof"
    report = proof.prove(argyll_archive, output, source=prepared_production)
    assert report["status"] == "passed"
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report == json.loads((output / "report.json").read_text())
    entry = fixture_manifest()["proofs"]["munki_curve_release"][proof.SOURCE_PATH]
    assert report["baseline_sha256"] == entry["sha256"]
    assert report["patched_sha256"] == proof.digest(
        (prepared_production / proof.SOURCE_PATH).read_bytes())
    baseline, patched = report["results"]["baseline"], report["results"]["patched"]
    assert tuple(baseline) == tuple(patched) == tuple(proof.EXPECTED)
    for case, expected in proof.EXPECTED.items():
        for row in (baseline[case], patched[case]):
            assert (row["created"], row["mallocs"], row["faults"]) == (
                expected["created"], expected["mallocs"], expected["faults"]), case
            # Every read of the case agrees with every other read of it.
            assert len(row["codes"]) == len(row["flags"]) == len(row["output_fnv"])
            assert len(row["codes"]) == row["iterations"] == (5 if "repeat" in case else 1)
            assert set(row["codes"]) == {expected["code"]}, case
            assert len(set(row["flags"])) == len(set(row["output_fnv"])) == 1, case
        # Same codes, flags and returned averages from both texts, read by read.
        for key in ("codes", "flags", "output_fnv"):
            assert baseline[case][key] == patched[case][key], case
        assert (baseline[case]["leaked"], baseline[case]["heap_open"]) == (
            expected["leaked"], expected["heap_open"]), case
        assert (patched[case]["leaked"], patched[case]["heap_open"]) == (0, 0), case
        assert patched[case]["deleted"] == expected["created"], case
        assert patched[case]["frees"] == expected["mallocs"], case
    # The original leaves its curve behind on every successful read that retains
    # one, once per read within a process, and when the points array fails; it
    # leaves the points array behind when the curve fails.
    assert {case for case, row in baseline.items() if row["leaked"]} == set(RELEASING)
    assert baseline["valid8_repeat5"]["leaked"] == 5
    assert {case for case, row in baseline.items() if row["heap_open"]} == {"alloc_curve_valid8"}
    assert baseline["valid3"]["created"] == baseline["valid3"]["deleted"] == 1
    # Both compiled variants can share a broken reporter. An extra array entry
    # or an omitted read must fail even when their malformed records agree.
    for key in ("codes", "flags", "output_fnv", "omitted-read"):
        malformed = copy.deepcopy(report["results"])
        for variant in malformed.values():
            row = variant["valid8_repeat5"]
            if key == "omitted-read":
                row["iterations"] -= 1
                for field in ("codes", "flags", "output_fnv"):
                    row[field].pop()
            else:
                row[key].append(row[key][-1])
        with pytest.raises(AssertionError, match="read count for valid8_repeat5"):
            proof._validate(malformed)
    assert (output / "baseline" / "list.log").exists()
    with pytest.raises(FileExistsError):
        proof.prove(argyll_archive, output, source=prepared_production)


@pytest.mark.parametrize(("old", "new", "seen_as"), [
    # The success release dropped: the successful reads keep their curve again.
    (SUCCESS_RELEASED, SUCCESS, "patched result for valid8: leaked 1 of 1"),
    # The allocation-failure release dropped: a failed points array keeps the curve.
    (ALLOC_FAILURE_RELEASED, ALLOC_FAILURE,
     "patched result for alloc_points_valid8: leaked 1 of 1"),
    # Only the points array kept on that branch: a failed curve keeps the block.
    (ALLOC_FAILURE_RELEASED,
     ALLOC_FAILURE_RELEASED.replace("\t\t\tfree(points);", "\t\t\t(void)points;"),
     "patched result for alloc_curve_valid8: 1 heap block"),
], ids=["success-kept", "curve-kept-on-alloc-failure", "points-kept-on-alloc-failure"])
def test_a_regressed_release_is_seen_by_the_proof(
    argyll_archive, mutate_production, tmp_path, old, new, seen_as
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    def regressed(text, path):
        assert path == proof.SOURCE_PATH
        assert text.count(old) == 1
        return text.replace(old, new, 1)

    source = mutate_production((proof.SOURCE_PATH,), regressed)
    with pytest.raises(AssertionError, match=seen_as):
        proof.prove(argyll_archive, tmp_path / "regressed", source=source)


def test_a_sanitizer_diagnostic_during_discovery_cannot_be_reported_as_a_pass(
        argyll_archive, prepared_production, tmp_path, monkeypatch):
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    support = subprocess.run(
        [cc, "-x", "c", "-", *sanitizers.FLAGS, "-o", str(tmp_path / "sanitizer-support")],
        input="int main(void) { return 0; }\n", capture_output=True, text=True, timeout=30)
    if support.returncode:
        pytest.skip("Compiler cannot link address/undefined sanitizers")
    listing = "\t\tfor (i = 0; i < NCASES; i++) puts(cases[i].name);\n"
    canary = "\t\t{ volatile int sanitizer_canary = 2147483647; sanitizer_canary += 1; }\n"
    monkeypatch.setattr(proof, "__file__", str(copy_proof_with_probe_mutation(proof,
        tmp_path, listing, listing + canary)))
    output = tmp_path / "canary-proof"
    with pytest.raises(AssertionError, match="Sanitizer diagnostic in baseline/--list"):
        proof.prove(argyll_archive, output, source=prepared_production, sanitize=True)
    listing_log = (output / "baseline" / "list.log").read_text()
    assert "runtime error: signed integer overflow" in listing_log
    assert json.loads((output / "report.json").read_text())["status"] == "incomplete"


def test_an_earlier_read_that_differs_is_seen(
    argyll_archive, prepared_production, tmp_path, monkeypatch
):
    """The five reads are compared individually, not only the last one."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    old = "\t\thashes[it] = fnv(out, c->tnpatch);\n"
    monkeypatch.setattr(proof, "__file__", str(copy_proof_with_probe_mutation(proof,
        tmp_path, old, old + "\t\tif (it == 0 && c->repeat > 1) hashes[it] ^= 1;\n")))
    with pytest.raises(AssertionError,
                       match="iterations for valid8_repeat5 differ within baseline"):
        proof.prove(argyll_archive, tmp_path / "earlier-read", source=prepared_production)
