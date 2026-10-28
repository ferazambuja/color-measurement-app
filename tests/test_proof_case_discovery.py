"""Case discovery in the probe-listing proof runners.

Four runners ask their compiled probe to list its cases before running them, and
each checked the listing's exit status and its names with the same test; one
parametrized test now covers them (the bodies were byte-identical)."""

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
from native_argyll import (  # noqa: E402
    disptype_list_member_release,
    munki_curve_release,
    munki_ledtemp_slope,
    munki_white_buffer_release,
)

PROOFS = (disptype_list_member_release, munki_curve_release, munki_ledtemp_slope,
          munki_white_buffer_release)


@pytest.mark.parametrize("proof", PROOFS, ids=lambda module: module.__name__.rsplit(".", 1)[-1])
def test_case_discovery_failures_cannot_be_ignored(argyll_archive, tmp_path, monkeypatch, proof):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    listing = "\t\tfor (i = 0; i < NCASES; i++) puts(cases[i].name);\n\t\treturn 0;\n"
    # Every name printed, then an unsuccessful exit.
    monkeypatch.setattr(proof, "__file__", str(copy_proof_with_probe_mutation(
        proof, tmp_path / "exit", listing, listing.replace("return 0;", "return 1;"))))
    with pytest.raises(subprocess.CalledProcessError):
        proof.prove(argyll_archive, tmp_path / "exit-proof")
    report = json.loads((tmp_path / "exit-proof" / "report.json").read_text())
    assert report["status"] == "incomplete"
    # One name too many, exit 0.
    extra = listing.replace("return 0;", 'puts("extra");\n\t\treturn 0;')
    monkeypatch.setattr(proof, "__file__", str(copy_proof_with_probe_mutation(
        proof, tmp_path / "extra", listing, extra)))
    with pytest.raises(RuntimeError, match="baseline probe lists"):
        proof.prove(argyll_archive, tmp_path / "extra-proof")
