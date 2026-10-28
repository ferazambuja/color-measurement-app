"""The shared display-type list releases its loaded entries on every exit; no hardware."""

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
from conftest import copy_proof_with_probe_mutation  # noqa: E402
from native_argyll import disptype_list_member_release as proof  # noqa: E402
from native_argyll.proof_inputs import before_source  # noqa: E402
from native_argyll.spyder_calibration_slots import _function  # noqa: E402

LEAKING = ("ccss2_ccmx_fails", "ccss2_ccmx_fails_repeat5")
FREE_ARRAY = "\t\t\tfree(list);\n"
LISTING_FAILURE = """\t\tif ((ss_list = list_iccmx(p->dtype, NULL)) == NULL) {
\t\t\tfree(list);
\t\t\treturn inst_internal_error;
\t\t}
"""
RELEASE = "\t\t\tinst_del_disptype_list(list, nlist);\t/* CCSS entries own their path and sets */\n"
LISTING_FAILURE_RELEASED = LISTING_FAILURE.replace(FREE_ARRAY, RELEASE)


@pytest.fixture(scope="module")
def sources(argyll_archive, prepared_production):
    before = before_source("disptype_list_member_release", proof.SOURCE_PATH, argyll_archive)
    return before, (prepared_production / proof.SOURCE_PATH).read_text(encoding="utf-8")


def test_unknown_archive_refused_before_writes(tmp_path):
    archive = tmp_path / "untrusted.zip"
    archive.write_bytes(b"not the pinned archive")
    output = tmp_path / "proof"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove(archive, output)
    assert not output.exists()


def test_current_constructor_releases_members_and_initializes_selector_suffixes(sources):
    original, patched = sources
    before, after = _function(original, proof.FUNCTION), _function(patched, proof.FUNCTION)
    # The CCSS-listing failure keeps free(list): its list holds only static
    # entries, which own nothing. The CCMX-listing failure now releases members.
    assert before.count(FREE_ARRAY) == 2 and after.count(FREE_ARRAY) == 1
    assert before.count("inst_del_disptype_list(") == 1     # the recreation release
    assert after.count("inst_del_disptype_list(") == 2
    assert LISTING_FAILURE in before and LISTING_FAILURE not in after
    assert after.count(LISTING_FAILURE_RELEASED) == 1
    site = after.index(LISTING_FAILURE_RELEASED)
    assert after.index("list_iccmx(p->dtype, NULL)", site) < after.index(RELEASE, site)
    assert after.index(RELEASE, site) < after.index("return inst_internal_error;", site)
    # Current production also initializes a fresh suffix and uses its larger
    # selector pool. The frozen baseline's sel[0] exhaustion test was unreachable.
    assert after.count("list[i].sel[0] = '_';") == 1
    dead_test = "if (list[i].sel[0] == '\\000') {\t/* Ran out of two letter selectors! */"
    assert before.count(dead_test) == 1 and dead_test not in after
    initialized = after.index("list[i].sel[1] = '\\000';")
    assert initialized < after.index("disptechs_set_sel(2, i, &list[i].sel[1]", initialized)
    assert "&k, asels2);" in after
    assert "if (list[i].sel[1] == '\\000')" in after


def test_loaded_entries_are_released_when_the_ccmx_listing_fails(
        argyll_archive, tmp_path, sources, prepared_production):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    output = tmp_path / "proof"
    report = proof.prove(argyll_archive, output, source=prepared_production)
    assert report["status"] == "passed"
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report == json.loads((output / "report.json").read_text())
    assert report["function_sha256"] == {
        label: proof.digest(_function(text, proof.FUNCTION).encode())
        for label, text in zip(("baseline", "patched"), sources)}
    baseline, patched = report["results"]["baseline"], report["results"]["patched"]
    assert tuple(baseline) == tuple(patched) == tuple(proof.EXPECTED)
    for case, expected in proof.EXPECTED.items():
        for row, variant_expected in ((baseline[case], expected),
                (patched[case], proof.EXPECTED_CONTRIBUTED[case])):
            assert row["untracked_frees"] == 0, case
            assert len(row["codes"]) == row["iterations"] == (5 if "repeat" in case else 1)
            for key, field in (("codes", "code"), ("handed", "handed"), ("entries", "entries"),
                               ("unassigned", "unassigned")):
                assert set(row[key]) == {variant_expected[field]}, (case, key)
        for key in ("codes", "handed", "entries"):
            assert baseline[case][key] == patched[case][key], case
        assert baseline[case]["allocations"] == patched[case]["allocations"], case
        assert baseline[case]["left"] == expected["left"], case
        assert patched[case]["left"] == 0, case
    # The original leaves the two paths and two sample-set blocks of the loaded
    # CCSS entries behind, once per attempt; a static-only list, the CCSS-listing
    # failure, a failed growth and every handed-back list leave nothing.
    assert {case for case, row in baseline.items() if row["left"]} == set(LEAKING)
    assert baseline["ccss2_ccmx_fails"]["left"] == 4
    assert baseline["ccss2_ccmx_fails_repeat5"]["left"] == 20
    assert baseline["ccss2_ccmx6_grow_fails"]["left"] == 0     # expand_dlist released it
    # With more calibrations than the 61 fallback characters, the list
    # is still handed back and released and no error is returned; over poisoned
    # heap every one of the 71 two-letter entries keeps "_" plus the heap byte,
    # because loaded entries initialise only sel[0]. With zero pages the same
    # case once showed ten bare "_": that count was the heap's, not the code's.
    assert baseline["ccmx130"]["codes"] == [proof.INST_OK]
    assert baseline["ccmx130"]["handed"] == [1] and baseline["ccmx130"]["entries"] == [132]
    assert baseline["ccmx130"]["unassigned"] == [71]
    assert patched["ccmx130"]["unassigned"] == [0]
    assert baseline["ccss2_ccmx3"]["entries"] == [7]           # 2 static + 2 CCSS + 3 CCMX
    # Both compiled variants can share a broken reporter. An extra array entry
    # or an omitted attempt must fail even when their malformed records agree.
    for key in ("codes", "handed", "entries", "unassigned", "omitted-attempt"):
        malformed = copy.deepcopy(report["results"])
        for variant in malformed.values():
            row = variant["ccss2_ccmx_fails_repeat5"]
            if key == "omitted-attempt":
                row["iterations"] -= 1
                for field in ("codes", "handed", "entries", "unassigned"):
                    row[field].pop()
            else:
                row[key].append(row[key][-1])
        with pytest.raises(AssertionError, match="attempt count for ccss2_ccmx_fails_repeat5"):
            proof._validate(malformed)
    # Refuse missing baseline leakage, an untracked free and divergent attempts.
    for field, value, message in (
        ("left", 0, "baseline result for ccss2_ccmx_fails"),
        ("untracked_frees", 1, "untracked free"),
        ("codes", [proof.INST_OK], "baseline result for ccss2_ccmx_fails"),
    ):
        malformed = copy.deepcopy(report["results"])
        malformed["baseline"]["ccss2_ccmx_fails"][field] = value
        with pytest.raises(AssertionError, match=message):
            proof._validate(malformed)
    malformed = copy.deepcopy(report["results"])
    malformed["baseline"]["ccss2_ccmx_fails_repeat5"]["codes"][1] = proof.INST_OK
    with pytest.raises(AssertionError, match="attempts for ccss2_ccmx_fails_repeat5"):
        proof._validate(malformed)
    # The production constructor must release members and overwrite every fresh
    # suffix; a malformed current result cannot inherit baseline acceptance.
    for field, value, message in (
            ("left", 4, "patched result for ccss2_ccmx_fails: 4 block"),
            ("unassigned", [71], r"patched result for ccss2_ccmx_fails: \(")):
        malformed = copy.deepcopy(report["results"])
        malformed["patched"]["ccss2_ccmx_fails"][field] = value
        with pytest.raises(AssertionError, match=message):
            proof._validate(malformed)
    patch = (output / "disptype-list-member-release.patch").read_text()
    assert patch.count("+\t\t\tinst_del_disptype_list(list, nlist);") == 1
    assert patch.count("-\t\t\tfree(list);") == 1
    assert (output / "baseline" / "list.log").exists()
    with pytest.raises(FileExistsError):
        proof.prove(argyll_archive, output, source=prepared_production)


def test_selector_initialization_regression_is_seen_by_the_proof(
        argyll_archive, tmp_path, mutate_production):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    def regressed(text, path):
        body = _function(text, proof.FUNCTION)
        initialized = "\t\t\tlist[i].sel[1] = '\\000';\n"
        assert body.count(initialized) == 1
        return text.replace(body, body.replace(initialized, ""))

    source = mutate_production([proof.SOURCE_PATH], regressed)
    with pytest.raises(AssertionError, match="patched result for ccmx130"):
        proof.prove(argyll_archive, tmp_path / "uninitialized", source=source)


@pytest.mark.parametrize(("old", "new", "seen_as"), [
    # The release dropped: the loaded entries leak again.
    (LISTING_FAILURE_RELEASED, LISTING_FAILURE,
     "patched result for ccss2_ccmx_fails: 4 block"),
    # The destructor called over no entries: the array goes, the members stay.
    (LISTING_FAILURE_RELEASED,
     LISTING_FAILURE_RELEASED.replace("(list, nlist)", "(list, 0)"),
     "patched result for ccss2_ccmx_fails: 4 block"),
], ids=["release-dropped", "count-zeroed"])
def test_a_regressed_release_is_seen_by_the_proof(argyll_archive, tmp_path, mutate_production,
                                                  old, new, seen_as):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    def regressed(text, path):
        body = _function(text, proof.FUNCTION)
        assert body.count(old) == 1
        return text.replace(body, body.replace(old, new))

    source = mutate_production([proof.SOURCE_PATH], regressed)
    with pytest.raises(AssertionError, match=seen_as):
        proof.prove(argyll_archive, tmp_path / "regressed", source=source)


def test_an_earlier_attempt_that_differs_is_seen(
        argyll_archive, tmp_path, monkeypatch, prepared_production):
    """The five attempts are compared individually, not only the last one."""
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    old = "\t\tentries[it] = n;\n"
    monkeypatch.setattr(proof, "__file__", str(copy_proof_with_probe_mutation(proof,
        tmp_path, old, old + "\t\tif (it == 0 && c->repeat > 1) entries[it] ^= 1;\n")))
    with pytest.raises(AssertionError,
                       match="attempts for ccss2_ccmx_fails_repeat5 differ within baseline"):
        proof.prove(argyll_archive, tmp_path / "earlier-attempt", source=prepared_production)
