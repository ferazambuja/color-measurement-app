"""ColorMunki white measurement releases its reading buffer on every exit; no hardware."""

import copy
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
from conftest import copy_proof_with_probe_mutation  # noqa: E402
from native_argyll import munki_white_buffer_release as proof  # noqa: E402
from native_argyll.proof_inputs import fixture_manifest  # noqa: E402
from native_argyll.spyder_calibration_slots import _function  # noqa: E402

LEAKING = ("saturated8", "saturated8_repeat5", "reflective_saturated8")
MATRIX_CALL = "free_dmatrix(multimes, 0, nummeas-1, -1, m->nraw-1);"
MATRIX_RELEASE = "\t\t" + MATRIX_CALL + "\n"      # the two failure-exit forms

# ArgyllCMS mutation excerpts retain Graeme W. Gill's GPL-2.0-or-later terms.
CONVERSION_FAILURE = (
    "\tif ((rv = munki_sens_to_raw(p, multimes, NULL, buf, ninvmeas, nummeas, m->satlimit,\n"
    "\t\t                                                     &darkthresh)) != MUNKI_OK) {\n"
    "\t\tfree_dmatrix(multimes, 0, nummeas-1, -1, m->nraw-1);\n"
    "\t\treturn rv;\n"
    "\t}\n")
CONVERSION_FAILURE_RELEASED = CONVERSION_FAILURE.replace(
    "\t\treturn rv;\n",
    "\t\tfree(buf);\t\t\t/* CM: the reading buffer, as the earlier exits do */\n\t\treturn rv;\n")


def _exits_before_the_success_release(function):
    """The blocks that return between the buffer's allocation and its success-path free."""
    allocated = function.index("return MUNKI_INT_MALLOC;")
    released = function.index("\tdarkthresh = trackmax[0];\n\tfree(buf);\n")
    window = function[allocated:released]
    return [block for block in re.findall(r"\{([^{}]*)\}", window) if "return " in block]


def test_unknown_archive_refused_before_writes(tmp_path):
    archive = tmp_path / "untrusted.zip"
    archive.write_bytes(b"not the pinned archive")
    output = tmp_path / "proof"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove(archive, output)
    assert not output.exists()


def test_current_white_measurement_releases_its_own_failure_resources(prepared_production):
    function = _function((prepared_production / proof.SOURCE_PATH).read_text(), proof.FUNCTION)
    assert function.count("malloc(") == function.count("dmatrix(0,") == 1
    assert function.count(MATRIX_CALL) == 3
    assert function.count("free(buf);") == 4
    assert function.count(CONVERSION_FAILURE_RELEASED) == 1
    exits = _exits_before_the_success_release(function)
    assert len(exits) == 3
    assert all("free(buf);" in block for block in exits)


def test_the_buffer_is_released_when_the_conversion_refuses(
    argyll_archive, prepared_production, tmp_path
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    output = tmp_path / "proof"
    report = proof.prove(argyll_archive, output, source=prepared_production)
    assert report["status"] == "passed"
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report == json.loads((output / "report.json").read_text())
    entry = fixture_manifest()["proofs"]["munki_white_buffer_release"][proof.SOURCE_PATH]
    assert report["baseline_sha256"] == entry["sha256"]
    assert report["patched_sha256"] == proof.digest(
        (prepared_production / proof.SOURCE_PATH).read_bytes())
    assert report["function_sha256"]["baseline"] != report["function_sha256"]["patched"]
    baseline, patched = report["results"]["baseline"], report["results"]["patched"]
    assert tuple(baseline) == tuple(patched) == tuple(proof.EXPECTED)
    for case, expected in proof.EXPECTED.items():
        for row in (baseline[case], patched[case]):
            assert (row["mallocs"], row["matrices"], row["faults"]) == (
                expected["mallocs"], expected["matrices"], expected["faults"]), case
            assert row["matrices_open"] == 0 and row["matrices_freed"] == row["matrices"], case
            # Every read of the case agrees with every other read of it.
            assert len(row["codes"]) == len(row["warmup"]) == len(row["output_fnv"])
            assert len(row["codes"]) == row["iterations"] == (5 if "repeat" in case else 1)
            assert set(row["codes"]) == {expected["code"]}, case
            assert set(row["warmup"]) == {expected["warmup"]}, case
            assert len(set(row["output_fnv"])) == 1, case
        # Same codes, warm-up counts and returned readings from both texts, read by read.
        for key in ("codes", "warmup", "output_fnv"):
            assert baseline[case][key] == patched[case][key], case
        assert baseline[case]["heap_open"] == expected["heap_open"], case
        assert patched[case]["heap_open"] == 0 and patched[case]["frees"] == expected["mallocs"]
    # The original leaves the buffer behind exactly when the conversion refuses a
    # saturated reading, once per attempt within a process; every other exit,
    # including the inconsistency refusal after the buffer is freed, releases it.
    assert {case for case, row in baseline.items() if row["heap_open"]} == set(LEAKING)
    assert baseline["saturated8_repeat5"]["heap_open"] == 5
    # The warm-up count is the actual munki_comp_ru_nummeas over the probe's
    # constants: ceil(0.05 / 0.0182) = 3 in reflective mode, none otherwise, and
    # no transport call at all when nothing was requested or nothing allocated.
    assert baseline["reflective_saturated8"]["warmup"] == [3]
    assert baseline["reflective_skipped_saturation8"]["warmup"] == [3]
    assert baseline["white8"]["warmup"] == [0] and baseline["trigger_fails8"]["warmup"] == [0]
    assert baseline["zero_measures"]["warmup"] == baseline["alloc_buffer8"]["warmup"] == [None]
    # A saturated value among the skipped readings is not seen; the readings
    # averaged differ from the plain success, so the returned readings differ.
    assert baseline["reflective_skipped_saturation8"]["codes"] == [0]
    skipped = baseline["reflective_skipped_saturation8"]
    assert baseline["white8"]["output_fnv"] != skipped["output_fnv"]
    # Nothing is returned on the failure exits: they share the sentinel hash.
    failures = {baseline[case]["output_fnv"][0] for case in
                ("saturated8", "trigger_fails8", "read_fails8", "zero_measures", "alloc_buffer8")}
    assert len(failures) == 1 and failures != {baseline["white8"]["output_fnv"][0]}
    # Both compiled variants can share a broken reporter. An extra array entry
    # or an omitted read must fail even when their malformed records agree.
    for key in ("codes", "warmup", "output_fnv", "omitted-read"):
        malformed = copy.deepcopy(report["results"])
        for variant in malformed.values():
            row = variant["saturated8_repeat5"]
            if key == "omitted-read":
                row["iterations"] -= 1
                for field in ("codes", "warmup", "output_fnv"):
                    row[field].pop()
            else:
                row[key].append(row[key][-1])
        with pytest.raises(AssertionError, match="read count for saturated8_repeat5"):
            proof._validate(malformed)
    assert (output / "baseline" / "list.log").exists()
    with pytest.raises(FileExistsError):
        proof.prove(argyll_archive, output, source=prepared_production)


@pytest.mark.parametrize(("old", "new", "seen_as"), [
    # The release dropped: the saturated attempts keep their buffer again.
    (CONVERSION_FAILURE_RELEASED, CONVERSION_FAILURE,
     "patched result for saturated8: 1 heap block"),
    # The matrix release lost while adding the buffer's: the other block leaks.
    (CONVERSION_FAILURE_RELEASED,
     CONVERSION_FAILURE_RELEASED.replace(MATRIX_RELEASE, ""),
     "patched result for saturated8: 1 matrix"),
], ids=["buffer-kept", "matrix-kept"])
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


def test_an_earlier_read_that_differs_is_seen(
    argyll_archive, prepared_production, tmp_path, monkeypatch
):
    """The five reads are compared individually, not only the last one."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    old = "\t\thashes[it] = fnv(absraw, optscale);\n"
    monkeypatch.setattr(proof, "__file__", str(copy_proof_with_probe_mutation(proof,
        tmp_path, old, old + "\t\tif (it == 0 && c->repeat > 1) hashes[it] ^= 1;\n")))
    with pytest.raises(AssertionError,
                       match="iterations for white8_repeat5 differ within baseline"):
        proof.prove(argyll_archive, tmp_path / "earlier-read", source=prepared_production)
