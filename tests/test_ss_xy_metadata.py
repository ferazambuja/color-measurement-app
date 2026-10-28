"""SpectroScan X-Y metadata, retry outcomes and real XRGA routing; no devices."""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "ss_xy_metadata", ROOT / "tools/native_argyll/ss_xy_metadata.py"
)
proof = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proof)

# Independent regression anchors retained from the authenticated repair evidence.
INVALID_XYZ = (
    "\t\t\t\t\t\t\tif (rv == inst_ok)\n\t\t\t\t\t\t\t\trv = inst_wrong_setup | ss_et_NoValidMeas;\n"
)


def test_unrecognized_inputs_refused_before_writes(tmp_path):
    archive = tmp_path / "unknown.zip"
    archive.write_bytes(b"unverified")
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove(archive, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_complete_xy_function_metadata_conversion_retries_and_untouched_native_data(
    argyll_archive,
    tmp_path,
    prepared_production,
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for the inert actual-function proof")
    output = tmp_path / "proof"
    report = proof.prove(argyll_archive, output)
    assert report["status"] == "passed"
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report == json.loads((output / "report.json").read_text())
    for variant, failed in (("baseline", proof.BASELINE_FAILURES), ("patched", 0)):
        result = report["results"][variant]
        assert result["cases"] == 32
        assert result["checks"] == proof.EXPECTED_CHECKS[variant]
        assert result["failures"] == failed
        assert result["groups"] == proof.EXPECTED_GROUPS[variant]
    assert report["results"]["patched"]["conversion_sha256"] == proof.digest(
        proof.xrga_conversion((prepared_production / "spectro/xrga.c").read_text()).encode()
    )
    # The current status-returning helper is compiled with its current headers.
    # Numerical routing controls above remain the same despite the evolved API.

    assert (
        proof.digest((output / "ss.patched.c").read_bytes())
        == report["patched_sha256"][proof.SOURCE_PATH]
    )
    with pytest.raises(FileExistsError):
        proof.prove(argyll_archive, output)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("vals[patch].mtype = inst_mrt_reflective;", "vals[patch].mtype = inst_mrt_none;"),
        ("vals[patch].mcond = inst_mrc_uvcut;", "vals[patch].mcond = inst_mrc_D65;"),
        (INVALID_XYZ, ""),
    ],
    ids=["conversion-type-gate", "filter-condition", "invalid-xyz-error"],
)
def test_actual_function_proof_detects_each_repair_regression(
    argyll_archive,
    tmp_path,
    monkeypatch,
    old,
    new,
    mutate_production,
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for the inert actual-function proof")

    def regressed(text):
        patched = text
        assert patched.count(old) == 1
        return patched.replace(old, new)

    source = mutate_production([proof.SOURCE_PATH], lambda text, path: regressed(text))
    with pytest.raises(AssertionError, match="Unexpected SS X-Y patched"):
        proof.prove(argyll_archive, tmp_path / "regressed", source=source)


@pytest.mark.parametrize("guard", [0, 1, 2], ids=["calibration", "move", "xyz"])
def test_actual_function_proof_detects_each_exhausted_retry_regression(
    argyll_archive,
    tmp_path,
    monkeypatch,
    guard,
    mutate_production,
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for the inert actual-function proof")

    def regressed(text):
        patched = text
        before = proof._function(patched, proof.READ_SIGNATURE)
        parts = before.split("if (try + 1 < notries)")
        assert len(parts) == 4
        after = parts[0] + "".join(
            ("if (try < notries)" if i == guard else "if (try + 1 < notries)") + part
            for i, part in enumerate(parts[1:])
        )
        return patched.replace(before, after)

    source = mutate_production([proof.SOURCE_PATH], lambda text, path: regressed(text))
    with pytest.raises(AssertionError, match="Unexpected SS X-Y patched"):
        proof.prove(argyll_archive, tmp_path / "regressed", source=source)
