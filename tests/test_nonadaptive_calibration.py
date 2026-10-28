"""Source-authenticated calibration regression; only inert extracted C executes."""

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
    "nonadaptive_calibration", ROOT / "tools/native_argyll/nonadaptive_calibration.py"
)
proof = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proof)

# Independent regression anchors retained from the authenticated repair evidence.
SOURCE_SHA256 = "42c684afa3bbb15dbbb880820c55a17f32d79ceaa7f8cbcf07cddaf3f2073e32"


@pytest.fixture(scope="module")
def upstream(argyll_archive):
    with zipfile.ZipFile(argyll_archive) as zipped:
        return zipped.read(proof.PREFIX + proof.SOURCE_PATH).decode()


def test_unknown_source_and_unknown_archive_are_refused_before_writes(tmp_path):
    archive = tmp_path / "untrusted.zip"
    archive.write_bytes(b"not the pinned archive")
    output = tmp_path / "proof"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove(archive, output)
    assert not output.exists()


def test_source_bound_success_saturation_error_masks_state_and_cleanup(argyll_archive, tmp_path):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    output = tmp_path / "proof"
    report = proof.prove(argyll_archive, output)
    assert report["status"] == "passed"
    # Reuse these real probe observations under both interpreter modes.
    altered = copy.deepcopy(report["results"])
    altered["patched"]["success_reduce"]["inttime"] = 99
    for optimization in ("", "-O"):
        for rows, expected in ((report["results"], 0), (altered, 1)):
            command = [
                sys.executable,
                *([optimization] if optimization else []),
                "-c",
                "import json,sys; sys.path.insert(0,sys.argv[1]); "
                "from native_argyll import nonadaptive_calibration as proof; "
                "proof._validate(json.load(sys.stdin))",
                str(ROOT / "tools"),
            ]
            run = subprocess.run(
                command, input=json.dumps(rows), capture_output=True, text=True, timeout=10
            )
            assert run.returncode == expected, run.stderr
            if expected:
                assert "AssertionError" in run.stderr
    assert report["hardware_tested"] is False
    assert report["instrument_runtime_linked"] is False
    assert len(report["results"]["baseline"]) == len(report["results"]["patched"]) == 20
    assert json.loads((output / "report.json").read_text()) == report
    assert proof.digest((output / "i1pro3_imp.original.c").read_bytes()) == SOURCE_SHA256
    assert proof.digest((output / "i1pro3_imp.patched.c").read_bytes()) == report["patched_sha256"]
    assert (
        proof.digest((output / "nonadaptive-calibration.patch").read_bytes())
        == report["patch_sha256"]
    )
    with pytest.raises(FileExistsError):
        proof.prove(argyll_archive, output)
