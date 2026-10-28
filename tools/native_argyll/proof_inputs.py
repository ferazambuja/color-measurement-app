"""Authenticated frozen predecessors and current production inputs for C proofs.

Predecessors are regression data, never a source generator. The production tree
comes from the normal authenticated import and application-policy preparation.
No instrument, transport or production worker is executed here.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import tempfile
import zipfile
from functools import wraps
from pathlib import Path

FIXTURES = Path(__file__).with_suffix("")
MANIFEST_SHA256 = "487c00ce9db95e5d16244057001834ea649ebc702570c452690bf51f08c4ef3f"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _regular(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"Invalid proof input path: {relative}")
    target = root
    for part in path.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError(f"Symlink in proof input: {relative}")
    if not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Missing or external proof input: {relative}")
    return target


def fixture_manifest() -> dict:
    if FIXTURES.is_symlink():
        raise ValueError("Symlink in proof fixture root")
    raw = _regular(FIXTURES, "manifest.json").read_bytes()
    if _digest(raw) != MANIFEST_SHA256:
        raise ValueError("Proof fixture manifest differs from its committed identity")
    manifest = json.loads(raw)
    if _digest(_regular(FIXTURES, "License2.txt").read_bytes()) != manifest["notice_sha256"]:
        raise ValueError("Proof fixture notice differs from its committed identity")
    declared = {"manifest.json", "License2.txt"}
    declared.update(entry["blob"] for paths in manifest["proofs"].values()
                    for entry in paths.values() if "blob" in entry)
    if {p.name for p in FIXTURES.iterdir()} != declared:
        raise ValueError("Unexpected or missing proof fixture files")
    return manifest


def before_source(proof: str, path: str, archive: Path) -> str:
    """Read one immutable predecessor, authenticated independently of AFTER."""
    manifest = fixture_manifest()
    try:
        entry = manifest["proofs"][proof][path]
    except KeyError as error:
        raise ValueError(f"Unknown proof predecessor: {proof}/{path}") from error
    if "archive_path" in entry:
        data = archive.read_bytes()
        if _digest(data) != manifest["archive_sha256"]:
            raise ValueError("Archive SHA-256 mismatch for proof predecessor")
        with zipfile.ZipFile(io.BytesIO(data)) as zipped:
            content = zipped.read("Argyll_V3.5.0/" + entry["archive_path"])
    else:
        compressed = _regular(FIXTURES, entry["blob"]).read_bytes()
        if _digest(compressed) != entry["blob_sha256"]:
            raise ValueError(f"Proof predecessor blob differs from its digest: {proof}/{path}")
        content = gzip.decompress(compressed)
    if _digest(content) != entry["sha256"]:
        raise ValueError(f"Proof predecessor differs from its digest: {proof}/{path}")
    return content.decode("utf-8")


def production_text(source: Path, path: str) -> str:
    """Read the actual prepared production source, including later repairs."""
    if source.is_symlink():
        raise ValueError("Symlink in production source root")
    return _regular(source, path).read_text(encoding="utf-8")


def production_support(source: Path, baseline: Path, output: Path) -> dict[str, str]:
    """Write current production counterparts of a proof's declared support files.

    Keep baseline support untouched: its source hashes describe the BEFORE
    executable. Current support has a distinct inventory and output directory.
    """
    if source.is_symlink() or baseline.is_symlink():
        raise ValueError("Symlink in proof support root")
    output.mkdir(parents=True, exist_ok=False)
    inventory = {}
    for path in sorted(baseline.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlink in proof support inventory")
        if path.is_dir():
            continue
        relative = path.relative_to(baseline).as_posix()
        _regular(baseline, relative)
        # Match prepare(): the standalone build aliases this official config
        # header into its build directory; it is not a source-root file.
        source_relative = "h/aconfig.h" if relative == "sa_config.h" else relative
        content = _regular(source, source_relative).read_bytes()
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        inventory[relative] = _digest(content)
    return inventory


def production_proof(function):
    """Supply the normal current source for standalone and composed proof calls."""
    @wraps(function)
    def run(archive, output, *, source=None, **options):
        if source is not None:
            return function(archive, output, source=source, **options)
        # Standalone behavior proofs use the same current CR/PR composition as
        # the application, rather than rebuilding a historical repair chain.
        from argyll_native_check import NATIVE, prepare

        with tempfile.TemporaryDirectory(prefix="argyll-proof-source-") as directory:
            prepared, _ = prepare(archive, Path(directory).resolve() / "build",
                                  contribution=NATIVE / "contributions/cr_pr")
            return function(archive, output, source=prepared, **options)
    return run
