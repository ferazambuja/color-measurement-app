"""Pinned driver control flow; no real acquisition, threads or transport."""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "calibration_repairs", ROOT / "tools/native_argyll/calibration_repairs.py"
)
proof = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proof)


def test_unknown_source_and_archive_refused_before_writes(tmp_path):
    archive = tmp_path / "untrusted.zip"
    archive.write_bytes(b"not the pinned archive")
    output = tmp_path / "proof"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove(archive, output)
    assert not output.exists()


def test_real_function_completion_error_state_and_lock_proof(argyll_archive, tmp_path):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    output = tmp_path / "proof"
    report = proof.prove(argyll_archive, output)
    assert report["status"] == "passed"
    assert report["hardware_tested"] is report["instrument_runtime_linked"] is False
    assert report == json.loads((output / "report.json").read_text())
    assert set(report["patched_sha256"]) == set(proof.PATHS)
    for path in proof.PATHS:
        driver = Path(path).stem
        assert (
            proof.digest((output / f"{driver}.original.c").read_bytes())
            == proof.SOURCE_SHA256[path]
        )
        assert (
            proof.digest((output / f"{driver}.patched.c").read_bytes())
            == report["patched_sha256"][path]
        )
        for variant in ("baseline", "patched"):
            result = report["results"][driver][variant]
            assert result["checks"] == proof.EXPECTED_CHECKS[driver]
            assert result["failures"] == (
                proof.BASELINE_FAILURES[driver][0] if variant == "baseline" else 0
            )
    with pytest.raises(FileExistsError):
        proof.prove(argyll_archive, output)


@pytest.mark.parametrize(
    ("path", "old", "new"),
    [
        (
            "spectro/dtp41.c",
            "if ((*calc & inst_calc_cond_mask) != inst_calc_uop_trans_white) {",
            "if (1) {",
        ),
        ("spectro/dtp92.c", "*calt &= ~inst_calt_emis_offset;", "*calt &= inst_calt_emis_offset;"),
        ("spectro/dtp92.c", "*calt &= ~inst_calt_emis_ratio;", "*calt &= inst_calt_emis_ratio;"),
        ("spectro/dtp92.c", "p->need_offset_cal = 0;", "/* retain stale offset need */"),
        ("spectro/dtp92.c", "p->need_ratio_cal = 0;", "/* retain stale ratio need */"),
        (
            "spectro/specbos.c",
            "\t\tamutex_unlock(p->lock);\n\t\treturn rv;\n\t}\n\n\t/* Adjust timeout",
            "\t\treturn rv;\n\t}\n\n\t/* Adjust timeout",
        ),
        (
            "spectro/specbos.c",
            "if ((ev = set_average(p, 50, 1)) != inst_ok) {\n"
            "\t\t\tp->mode = cmode;\n\t\t\tp->doing_cal = 0;",
            "if ((ev = set_average(p, 50, 1)) != inst_ok) {",
        ),
        (
            "spectro/specbos.c",
            "if ((ev = set_average(p, 1, 1)) != inst_ok) {\n"
            "\t\t\tp->mode = cmode;\n\t\t\tp->doing_cal = 0;",
            "if ((ev = set_average(p, 1, 1)) != inst_ok) {",
        ),
    ],
    ids=[
        "dtp41-ack",
        "dtp92-offset-mask",
        "dtp92-ratio-mask",
        "dtp92-offset-need",
        "dtp92-ratio-need",
        "specbos-read-unlock",
        "specbos-initial-cleanup",
        "specbos-final-cleanup",
    ],
)
def test_each_repair_is_seen_by_inert_proof(
    argyll_archive, tmp_path, monkeypatch, path, old, new, mutate_production
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")

    def regressed(text):
        corrected = text
        assert corrected.count(old) == 1
        return corrected.replace(old, new)

    source = mutate_production([path], lambda text, path: regressed(text))
    with pytest.raises(AssertionError, match=f"Unexpected {Path(path).stem} patched result"):
        proof.prove(argyll_archive, tmp_path / "regressed", source=source)
