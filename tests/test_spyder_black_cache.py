"""Actual six-channel cache/read definitions with an inert cache and transport."""

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import argyll_native_check as native  # noqa: E402
from native_argyll import spyder_black_cache as proof  # noqa: E402


@pytest.fixture(scope="module")
def composition(argyll_archive, tmp_path_factory):
    source, report = native.prepare(
        argyll_archive,
        tmp_path_factory.mktemp("spyder-cache") / "build",
    )
    return source, report


def test_unknown_archive_refused_before_output(tmp_path):
    archive = tmp_path / "unknown.zip"
    archive.write_bytes(b"untrusted source")
    output = tmp_path / "proof"
    with pytest.raises(ValueError, match="Archive SHA-256"):
        proof.prove(archive, output)
    assert not output.exists()


def test_six_offsets_survive_restart_in_actual_compiled_functions(
    argyll_archive,
    composition,
    tmp_path,
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    source, _ = composition
    driver = source / proof.SOURCE_PATH
    output = tmp_path / "proof"
    result = proof.prove(argyll_archive, output, source=source)
    assert result == json.loads((output / "report.json").read_text())
    assert result["compiled_source_sha256"] == native.digest(driver.read_bytes())
    assert result["hardware_tested"] is result["filesystem_tested"] is False
    assert result["runs"]["fixed"]["stdout"] == (
        "before=[15,14,19] after=[15,14,19] restore=0 legacy=2 checks=33 failures=0"
    )
    assert result["runs"]["original"]["stdout"] == (
        "before=[15,14,19] after=[20,20,19] restore=0 legacy=0 checks=25 failures=0"
    )
    for name, failures in (("save_regression", 12), ("restore_regression", 17)):
        run = result["runs"][name]
        assert run["exit"] == 1 and "failed line" in run["stderr"]
        assert run["stdout"].endswith(f"checks=33 failures={failures}")
    with pytest.raises(FileExistsError):
        proof.prove(argyll_archive, output, source=source)


def test_incomplete_production_cache_is_rejected_by_its_behavior(
    argyll_archive,
    composition,
    tmp_path,
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    source, _ = composition
    compiled = (source / proof.SOURCE_PATH).read_text()
    anchor = "calf_rints(&x, p->bcal, 6);"
    assert compiled.count(anchor) == 1
    changed_source = tmp_path / "source"
    changed = changed_source / proof.SOURCE_PATH
    changed.parent.mkdir(parents=True)
    changed.write_text(compiled.replace(anchor, "calf_rints(&x, p->bcal, 5);"))
    output = tmp_path / "proof"
    with pytest.raises(ValueError, match="fixed proof failed"):
        proof.prove(argyll_archive, output, source=changed_source)
    assert "failed line" in (output / "fixed.run.stderr").read_text()
    assert "failures=0" not in (output / "fixed.run.stdout").read_text()


def test_proof_binds_crlf_bytes_and_accepts_a_harmless_comment(
    argyll_archive, composition, tmp_path
):
    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable")
    source, _ = composition
    original = (source / proof.SOURCE_PATH).read_bytes()
    assert b"\r" not in original
    commented = original + b"\n/* Harmless production comment. */\n"
    raw = commented.replace(b"\n", b"\r\n")
    supplied_source = tmp_path / "source"
    supplied = supplied_source / proof.SOURCE_PATH
    supplied.parent.mkdir(parents=True)
    supplied.write_bytes(raw)
    result = proof.prove(argyll_archive, tmp_path / "proof", source=supplied_source)
    assert result["compiled_source_sha256"] == native.digest(raw)
    assert result["compiled_source_sha256"] != native.digest(original)
    extraction = json.loads((tmp_path / "proof/source-extraction.json").read_text())
    assert extraction["coordinate_sources"]["candidate"] == {
        "encoding": "utf-8",
        "normalization": "universal newlines to LF",
        "sha256": native.digest(commented),
    }
    for span in extraction["candidate"].values():
        copied = commented[span["start_byte"]:span["end_byte"]]
        assert native.digest(copied) == span["copied_sha256"]
