"""Proof predecessors authenticate independently of editable production source.

These checks read source and synthetic files only; no C compiler, worker or
instrument is invoked.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sys
import types
import zipfile
from pathlib import Path

import pytest
from native_argyll import proof_inputs as proof

BLOB_PROOF = "conversion_allocation"
BLOB_PATH = "spectro/dtp20.c"
ARCHIVE_PROOF = "calibration_repairs"
ARCHIVE_PATH = "spectro/dtp41.c"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def copied_inputs(tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    shutil.copytree(proof.FIXTURES, root)
    monkeypatch.setattr(proof, "FIXTURES", root)
    return root


def rewrite_manifest(root: Path, monkeypatch, manifest: dict) -> None:
    """Trust a synthetic manifest revision to isolate its deeper file checks."""
    raw = json.dumps(manifest, sort_keys=True).encode()
    (root / "manifest.json").write_bytes(raw)
    monkeypatch.setattr(proof, "MANIFEST_SHA256", digest(raw))


def symlink(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        if os.name == "nt" and error.winerror == 1314:
            pytest.skip(f"Windows account cannot create a symlink: {error}")
        raise


def test_committed_predecessors_match_their_independent_archive_or_blob(argyll_archive):
    manifest = proof.fixture_manifest()
    assert manifest["proofs"]
    with zipfile.ZipFile(argyll_archive) as zipped:
        assert (proof.FIXTURES / "License2.txt").read_bytes() == zipped.read(
            "Argyll_V3.5.0/License2.txt")
        sources = set()
        for name, paths in manifest["proofs"].items():
            assert paths
            for path, entry in paths.items():
                text = proof.before_source(name, path, argyll_archive)
                if "archive_path" in entry:
                    expected = zipped.read("Argyll_V3.5.0/" + entry["archive_path"])
                    sources.add("archive")
                else:
                    compressed = (proof.FIXTURES / entry["blob"]).read_bytes()
                    assert digest(compressed) == entry["blob_sha256"]
                    expected = gzip.decompress(compressed)
                    sources.add("blob")
                assert text.encode() == expected
                assert digest(expected) == entry["sha256"]
        assert sources == {"archive", "blob"}


@pytest.mark.parametrize("target", ["manifest.json", "License2.txt", "blob"])
def test_changed_fixture_bytes_are_refused(copied_inputs, target):
    manifest = json.loads((copied_inputs / "manifest.json").read_text())
    if target == "blob":
        target = manifest["proofs"][BLOB_PROOF][BLOB_PATH]["blob"]
    path = copied_inputs / target
    path.write_bytes(path.read_bytes() + b"\nchanged\n")
    with pytest.raises(ValueError, match="differs from"):
        proof.before_source(BLOB_PROOF, BLOB_PATH, copied_inputs / "unused.zip")


def test_plaintext_digest_refuses_recompressed_changed_source(copied_inputs, monkeypatch):
    manifest = json.loads((copied_inputs / "manifest.json").read_text())
    entry = manifest["proofs"][BLOB_PROOF][BLOB_PATH]
    path = copied_inputs / entry["blob"]
    changed = gzip.compress(gzip.decompress(path.read_bytes()) + b"\n/* altered */\n", mtime=0)
    path.write_bytes(changed)
    entry["blob_sha256"] = digest(changed)
    rewrite_manifest(copied_inputs, monkeypatch, manifest)
    with pytest.raises(ValueError, match="Proof predecessor differs from its digest"):
        proof.before_source(BLOB_PROOF, BLOB_PATH, copied_inputs / "unused.zip")


def test_wrong_archive_is_refused_before_reading_a_predecessor(tmp_path):
    archive = tmp_path / "wrong.zip"
    archive.write_bytes(b"not the authenticated source archive")
    with pytest.raises(ValueError, match="Archive SHA-256 mismatch"):
        proof.before_source(ARCHIVE_PROOF, ARCHIVE_PATH, archive)


@pytest.mark.parametrize("name,path", [
    ("unknown-proof", ARCHIVE_PATH),
    (ARCHIVE_PROOF, "spectro/unknown.c"),
    (ARCHIVE_PROOF, "../dtp41.c"),
    (ARCHIVE_PROOF, "/spectro/dtp41.c"),
])
def test_unknown_or_external_predecessors_are_refused(tmp_path, name, path):
    with pytest.raises(ValueError, match="Unknown proof predecessor"):
        proof.before_source(name, path, tmp_path / "unused.zip")


@pytest.mark.parametrize("target", ["root", "manifest.json", "License2.txt", "blob"])
def test_fixture_symlinks_are_refused_even_with_identical_bytes(copied_inputs, target):
    manifest = json.loads((copied_inputs / "manifest.json").read_text())
    if target == "root":
        actual = copied_inputs.with_name("actual-inputs")
        copied_inputs.rename(actual)
        symlink(copied_inputs, actual, directory=True)
    else:
        if target == "blob":
            target = manifest["proofs"][BLOB_PROOF][BLOB_PATH]["blob"]
        path = copied_inputs / target
        actual = copied_inputs.parent / target
        path.rename(actual)
        symlink(path, actual)
    with pytest.raises(ValueError, match="Symlink"):
        proof.before_source(BLOB_PROOF, BLOB_PATH, copied_inputs / "unused.zip")


@pytest.mark.parametrize("change", ["extra", "missing"])
def test_fixture_inventory_refuses_unaccounted_or_missing_files(copied_inputs, change):
    manifest = json.loads((copied_inputs / "manifest.json").read_text())
    if change == "extra":
        (copied_inputs / "unaccounted.c").write_text("/* no declared owner */\n")
    else:
        (copied_inputs / manifest["proofs"][BLOB_PROOF][BLOB_PATH]["blob"]).unlink()
    with pytest.raises(ValueError, match="Unexpected or missing proof fixture files"):
        proof.fixture_manifest()


def test_production_text_reads_current_edits_verbatim(tmp_path):
    current = tmp_path / "spectro" / "driver.c"
    current.parent.mkdir()
    text = "/* A reviewed later comment. */\nint current_result(void) { return 7; }\n"
    current.write_text(text, encoding="utf-8")
    assert proof.production_text(tmp_path, "spectro/driver.c") == text
    current.write_text(text.replace("later comment", "polished comment"), encoding="utf-8")
    assert proof.production_text(tmp_path, "spectro/driver.c") == current.read_text()


@pytest.mark.parametrize("path", ["", ".", "../outside.c", "spectro/../outside.c"])
def test_production_paths_refuse_traversal_even_when_the_target_exists(tmp_path, path):
    (tmp_path / "outside.c").write_text("/* exists */\n")
    (tmp_path / "spectro").mkdir()
    with pytest.raises(ValueError, match="Invalid proof input path"):
        proof.production_text(tmp_path, path)


def test_production_paths_refuse_absolute_paths_and_missing_files(tmp_path):
    actual = tmp_path / "present.c"
    actual.write_text("/* exists */\n")
    with pytest.raises(ValueError, match="Invalid proof input path"):
        proof.production_text(tmp_path, str(actual))
    with pytest.raises(ValueError, match="Missing or external proof input"):
        proof.production_text(tmp_path, "missing.c")


@pytest.mark.parametrize("target", ["root", "directory", "file"])
def test_production_symlinks_are_refused(tmp_path, target):
    root = tmp_path / "source"
    current = root / "spectro" / "driver.c"
    current.parent.mkdir(parents=True)
    current.write_text("/* current */\n")
    if target == "root":
        actual = tmp_path / "actual-source"
        root.rename(actual)
        symlink(root, actual, directory=True)
    elif target == "directory":
        actual = tmp_path / "actual-spectro"
        current.parent.rename(actual)
        symlink(root / "spectro", actual, directory=True)
    else:
        actual = tmp_path / "actual-driver.c"
        current.rename(actual)
        symlink(current, actual)
    with pytest.raises(ValueError, match="Symlink"):
        proof.production_text(root, "spectro/driver.c")


def test_explicit_production_source_is_passed_to_the_proof_without_preparation(tmp_path,
                                                                            monkeypatch):
    monkeypatch.setitem(sys.modules, "argyll_native_check", None)
    source = tmp_path / "explicit-source"
    source.mkdir()
    (source / "driver.c").write_text("/* composed input */\n")

    @proof.production_proof
    def check(archive, output, *, source=None, compiler=None):
        assert archive == tmp_path / "archive.zip"
        assert output == tmp_path / "output"
        assert source == tmp_path / "explicit-source"
        assert compiler == "chosen-compiler"
        return proof.production_text(source, "driver.c")

    assert check(tmp_path / "archive.zip", tmp_path / "output", source=source,
                 compiler="chosen-compiler") == "/* composed input */\n"


@pytest.mark.parametrize("fail", [False, True])
def test_standalone_proof_uses_current_combined_source_and_cleans_it(tmp_path, monkeypatch, fail):
    native = types.ModuleType("argyll_native_check")
    native.NATIVE = tmp_path / "native"
    prepared_sources = []

    def prepare(archive, output, *, contribution):
        assert archive == tmp_path / "archive.zip"
        assert contribution == native.NATIVE / "contributions/cr_pr"
        source = output / "source"
        source.mkdir(parents=True)
        (source / "driver.c").write_text("/* latest combined source */\n")
        prepared_sources.append(source)
        return source, {"source": "synthetic; never compiled"}

    native.prepare = prepare
    monkeypatch.setitem(sys.modules, "argyll_native_check", native)

    @proof.production_proof
    def check(archive, output, *, source=None, sanitize=False):
        assert sanitize is True
        assert source == prepared_sources[-1]
        text = proof.production_text(source, "driver.c")
        if fail:
            raise RuntimeError("synthetic proof failure")
        return text

    if fail:
        with pytest.raises(RuntimeError, match="synthetic proof failure"):
            check(tmp_path / "archive.zip", tmp_path / "output", sanitize=True)
    else:
        assert check(tmp_path / "archive.zip", tmp_path / "output", sanitize=True) == (
            "/* latest combined source */\n")
    assert len(prepared_sources) == 1
    assert not prepared_sources[0].exists()


def test_support_uses_current_bytes_and_the_build_config_alias(tmp_path):
    baseline, current, output = (tmp_path / name for name in ("baseline", "current", "after"))
    baseline.mkdir()
    (baseline / "driver.h").write_bytes(b"old header")
    (baseline / "sa_config.h").write_bytes(b"old config")
    (current / "h").mkdir(parents=True)
    (current / "driver.h").write_bytes(b"current header \xff")
    (current / "h/aconfig.h").write_bytes(b"current config")
    recorded = proof.production_support(current, baseline, output)
    assert recorded == {"driver.h": digest(b"current header \xff"),
                        "sa_config.h": digest(b"current config")}
    assert (output / "driver.h").read_bytes() == b"current header \xff"
    assert (output / "sa_config.h").read_bytes() == b"current config"
    assert (baseline / "driver.h").read_bytes() == b"old header"
    assert (baseline / "sa_config.h").read_bytes() == b"old config"


@pytest.mark.parametrize("directory", [False, True])
def test_support_inventory_refuses_symlinks_before_skipping_directories(tmp_path, directory):
    baseline, current = tmp_path / "baseline", tmp_path / "current"
    baseline.mkdir()
    current.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir() if directory else outside.write_bytes(b"header")
    symlink(baseline / "linked", outside, directory=directory)
    with pytest.raises(ValueError, match="Symlink"):
        proof.production_support(current, baseline, tmp_path / "after")
