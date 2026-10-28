"""Source-bound scan normalization proof; no acquisition route or hardware runs."""

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "scan_normalization", ROOT / "tools/native_argyll/scan_normalization.py"
)
proof = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proof)

# Independent regression anchors retained from the authenticated repair evidence.
BASELINE_SHA256 = "9eca875bc2d74574189ef7fe74505dfb9fe0da29fb0edc0042893a6f74737ff6"


@pytest.fixture(scope="module")
def upstream(argyll_archive):
    with zipfile.ZipFile(argyll_archive) as zipped:
        return zipped.read(proof.PREFIX + proof.SOURCE_PATH).decode()


def test_untrusted_source_and_archive_refused_before_writes(tmp_path):
    archive = tmp_path / "untrusted.zip"
    archive.write_bytes(b"not a pinned archive")
    output = tmp_path / "refused"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove(archive, output)
    assert not output.exists()


def test_inert_source_proof_refusals_finite_equivalence_and_cleanup(
    argyll_archive,
    upstream,
    tmp_path,
    prepared_production,
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    output = tmp_path / "proof"
    report = proof.prove(argyll_archive, output)
    assert report["status"] == "passed"
    # Reuse these real probe observations under both interpreter modes.
    altered = copy.deepcopy(report["results"])
    altered["patched"]["finite"]["duration_untouched"] = 0
    for optimization in ("", "-O"):
        for rows, expected in ((report["results"], 0), (altered, 1)):
            command = [
                sys.executable,
                *([optimization] if optimization else []),
                "-c",
                "import json,sys; sys.path.insert(0,sys.argv[1]); "
                "from native_argyll import scan_normalization as proof; "
                "proof._validate(json.load(sys.stdin))",
                str(ROOT / "tools"),
            ]
            run = subprocess.run(
                command, input=json.dumps(rows), capture_output=True, text=True, timeout=10
            )
            assert run.returncode == expected, run.stderr
            if expected:
                assert "AssertionError" in run.stderr
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report["baseline_sha256"] == BASELINE_SHA256
    assert report["patched_sha256"] == proof.digest(
        (prepared_production / proof.SOURCE_PATH).read_bytes()
    )
    assert len(report["results"]["baseline"]) == len(report["results"]["patched"]) == 40
    before = report["results"]["baseline"]["zebra_slot_overflow"]
    after = report["results"]["patched"]["zebra_slot_overflow"]
    assert before["code"] == 0 and before["nonfinite_proxy"] == 300 and before["averaged"] == 3
    assert after["code"] == 0x39 and after["locate_calls"] == after["converted"] == 0
    assert after["raw_frees"] == 3 and after["zebra_frees"] == after["output_untouched"] == 1
    assert json.loads((output / "report.json").read_text()) == report
    assert proof.digest((output / "i1pro3_imp.original.c").read_bytes()) == proof.SOURCE_SHA256
    assert proof.digest((output / "i1pro3_imp.baseline.c").read_bytes()) == BASELINE_SHA256
    assert proof.digest((output / "i1pro3_imp.patched.c").read_bytes()) == report["patched_sha256"]
    assert (
        proof.digest((output / "scan-normalization.patch").read_bytes()) == (report["patch_sha256"])
    )
    with pytest.raises(FileExistsError):
        proof.prove(argyll_archive, output)
