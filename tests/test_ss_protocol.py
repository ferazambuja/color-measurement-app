"""Whole-unit SpectroScan communication and unsigned wire decoding, no device."""

import copy
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from native_argyll import ss_protocol as proof  # noqa: E402

# Independent regression anchors retained from the authenticated repair evidence.
NEW_COM = (
    "\t\t  && p->rbuf[2] == 'A'\n"
    "\t\t  && p->rbuf[3] == '0') {\t/* COMM Error after the D1 prefix */"
)
OLD_COM = "\t\t  && p->rbuf[0] == 'A'\n\t\t  && p->rbuf[1] == '0') {\t/* COMM Error */"
PATCHED_HASHES = {
    "spectro/ss_imp.c": "4c907899d1f8c044be46aa4ace2a2457c2a6e662df1af436dd92d309a639813c",
    "spectro/ss_imp.h": "7f92ffd8ccab8c21dd478a99e68ac5382ea48990028a304c718874b5d34d3654",
}


@pytest.fixture(scope="module")
def receipt(argyll_archive, tmp_path_factory):
    if shutil.which("cc") is None:
        pytest.skip("C compiler required for the inert protocol proof")
    return proof.prove(argyll_archive, tmp_path_factory.mktemp("ss") / "proof", sanitize=True)


def test_protocol_errors_and_all_decode_controls(receipt, prepared_production):
    assert receipt["status"] == "passed"
    assert len(receipt["results"]["patched"]) == 31
    assert receipt["patched_sha256"] == {
        p: proof.digest((prepared_production / p).read_bytes()) for p in proof.SOURCE_HASHES
    }


@pytest.mark.parametrize(
    "arg,changes",
    [
        (":D1A008", {"inst_class": "0x90000", "inst_code": "0x90088"}),
        (":D18000", {"snerr": "0xf7", "inst_class": "0x90000", "inst_code": "0x900f7"}),
        (":2603", {"snerr": "0x0", "remaining": "2603"}),
        ("00000080", {"negative": 0}),
    ],
)
def test_receipt_rejects_wrong_classes_controls_and_zero_sign(receipt, arg, changes):
    rows = copy.deepcopy(receipt["results"]["patched"])
    row = next(r for r in rows if r["arg"] == arg)
    row.update(changes)
    with pytest.raises(AssertionError, match=arg):
        proof.validate(rows, repaired=True, sanitize=True)


@pytest.mark.parametrize(
    "which,seen_as",
    [
        ("communication-offset", r"moveabs :D1A001"),
        ("signed-shift", r"patched/double/000080BF"),
    ],
)
def test_compiled_regressions_are_caught(
    argyll_archive, tmp_path, monkeypatch, which, seen_as, mutate_production
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler required for the inert protocol proof")

    def regressed(text, path):
        repaired = text
        if path.endswith(".c"):
            if which == "communication-offset":
                assert NEW_COM in repaired
                repaired = repaired.replace(NEW_COM, OLD_COM)
            else:
                repaired = repaired.replace(
                    "((unsigned int)h2b(p, p->rbuf[6]) << 28)", "(h2b(p, p->rbuf[6]) << 28)"
                )
        return repaired

    source = mutate_production(proof.SOURCE_HASHES, regressed)
    with pytest.raises(AssertionError, match=seen_as):
        proof.prove(argyll_archive, tmp_path / "proof", sanitize=True, source=source)


@pytest.mark.parametrize("fault", ["exit", "missing", "unrelated"])
def test_baseline_requires_the_expected_signed_shift(receipt, fault):
    rows = copy.deepcopy(receipt["results"]["baseline"])
    row = next(r for r in rows if (r["kind"], r["arg"]) == ("double", "000080BF"))
    if fault == "exit":
        row["returncode"] = 0
    elif fault == "missing":
        row["diagnostic"] = None
    else:
        row["diagnostic"] = "unrelated member access within null pointer"
    with pytest.raises(AssertionError, match="baseline signed shift no longer reproduced"):
        proof.validate(rows, repaired=False, sanitize=True)
