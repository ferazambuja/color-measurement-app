"""Spyder X2/2024 calibration slots; actual selection code, no transport or hardware."""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "spyder_calibration_slots", ROOT / "tools/native_argyll/spyder_calibration_slots.py"
)
proof = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proof)

# Independent regression anchors retained from the authenticated repair evidence.


def test_unknown_source_and_archive_refused_before_writes(tmp_path):
    archive = tmp_path / "untrusted.zip"
    archive.write_bytes(b"not the pinned archive")
    output = tmp_path / "proof"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove(archive, output)
    assert not output.exists()


def test_every_advertised_calibration_selects_into_its_own_slot(
    argyll_archive, tmp_path, prepared_production
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    output = tmp_path / "proof"
    report = proof.prove(argyll_archive, output)
    assert report["status"] == "passed"
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report == json.loads((output / "report.json").read_text())
    assert report["patched_sha256"] == {
        proof.SOURCE_PATH: proof.digest((prepared_production / proof.SOURCE_PATH).read_bytes())
    }
    baseline, patched = report["results"]["baseline"], report["results"]["patched"]
    # The original: only OLED and Mini-LED lack a slot, and they are counted, not run.
    log = (output / "baseline/run.log").read_text()
    assert '"OLED" native ix 5 has no slot' in log and '"Mini-LED" native ix 6' in log
    assert (baseline["slots"], baseline["executed"], baseline["skipped"]) == (5, 15, 5)
    # Repaired: X2's five, both 2024 paths for all seven, and a reselection sequence.
    assert (patched["slots"], patched["executed"], patched["skipped"]) == (7, 24, 0)
    with pytest.raises(FileExistsError):
        proof.prove(argyll_archive, output)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("SpX2calinfo cinfo[SPYDX2_MAXCALIBS];", "SpX2calinfo cinfo[SPYDX2_NOCALIBS];"),
        ("(SPYD2024_NOCALIBS > SPYDX2_NOCALIBS", "(SPYD2024_NOCALIBS < SPYDX2_NOCALIBS"),
        ("SpX2calinfo cinfo[SPYDX2_MAXCALIBS];", "SpX2calinfo cinfo[SPYDX2_MAXCALIBS - 1];"),
    ],
    ids=["array-unchanged", "smaller-count", "one-short"],
)
def test_a_regressed_repair_is_seen_by_the_proof(
    argyll_archive, tmp_path, monkeypatch, old, new, mutate_production
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")

    def regressed(text):
        corrected = text
        assert corrected.count(old) == 1
        return corrected.replace(old, new)

    source = mutate_production([proof.SOURCE_PATH], lambda text, path: regressed(text))
    with pytest.raises(AssertionError, match="Unexpected Spyder slot patched result"):
        proof.prove(argyll_archive, tmp_path / "regressed", source=source)
