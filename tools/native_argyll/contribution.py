"""Authenticated contribution series applied to the pinned stock Argyll source.

The native check extracts only the digest-pinned official 3.5.0 archive and
then applies this project's own transforms to files that already exist. A
driver written outside the archive cannot enter that pipeline as an unreviewed
checkout path: it enters as a *series* kept in this repository, with a manifest
that names every path it touches, the operation, the digest of the series file
itself, the digest the stock file must have before the change (modifications
only) and the digest the file must have afterwards.

Application is exact. Additions require an absent target. Modifications are
unified diffs applied at their stated line numbers with every context and
removed line compared byte for byte; there is no fuzz and no offset search. A
digest that disagrees at any point stops the build. After the whole series has
been applied, the complete source tree is hashed again and must equal the stock
map with exactly the series' post-images substituted, so a series cannot write
anywhere it did not declare.

Authorship and terms are copied from the manifest into the build receipt per
entry. Upstream files keep their upstream author and licence; a change to one
carries its own author and terms, which the file headers state. The manifest
records the terms the owner assigned; this module never chooses them, and the
build's notice audit refuses a header that disagrees with the manifest.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

SCHEMA_VERSION = 2
OPERATIONS = ("add", "modify")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_GIT_HEX = re.compile(r"^[0-9a-f]{40}$")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Entry:
    path: str
    operation: str
    source: str
    source_sha256: str
    pre_sha256: str | None
    post_sha256: str
    provenance: dict


@dataclass(frozen=True)
class Series:
    name: str
    directory: Path
    manifest_sha256: str
    base_archive_sha256: str
    instrument_types_added: tuple[str, ...]
    source_origin: dict[str, str]
    entries: tuple[Entry, ...]


def _relative_path(text: object, what: str) -> str:
    """A forward-slash relative path with no traversal, no empty and no dot parts."""
    if not isinstance(text, str) or not text or "\\" in text or "\x00" in text:
        raise ValueError(f"{what}: path must be a non-empty forward-slash string")
    pure = PurePosixPath(text)
    if (pure.is_absolute() or PureWindowsPath(text).drive
            or text.startswith("/") or text.endswith("/")):
        raise ValueError(f"{what}: absolute or directory path {text!r}")
    if any(part in ("", ".", "..") for part in text.split("/")):
        raise ValueError(f"{what}: traversal or empty segment in {text!r}")
    return pure.as_posix()


def _digest(record: dict, key: str, what: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError(f"{what}: {key} must be a lowercase SHA-256 hex digest")
    return value


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate contribution manifest key: {key}")
        result[key] = value
    return result


def load_series(directory: Path) -> Series:
    """Read and validate a series directory. Nothing is applied here."""
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Contribution series must be a directory")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Contribution series has no manifest.json")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"), object_pairs_hook=_unique_object)
    if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != SCHEMA_VERSION):
        raise ValueError("Unsupported contribution manifest schema")
    name = manifest.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
        raise ValueError("Contribution manifest needs a short plain name")
    base = manifest.get("base")
    if not isinstance(base, dict):
        raise ValueError("Contribution manifest needs a base archive record")
    base_sha = _digest(base, "archive_sha256", "base")
    origin = manifest.get("source_origin")
    if not isinstance(origin, dict) or set(origin) != {"repository", "commit", "tree"}:
        raise ValueError("Contribution manifest needs exact source repository, commit and tree")
    if (not isinstance(origin["repository"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", origin["repository"])):
        raise ValueError("Contribution source repository must be a short plain name")
    for key in ("commit", "tree"):
        if not isinstance(origin[key], str) or not _GIT_HEX.fullmatch(origin[key]):
            raise ValueError(f"Contribution source {key} must be an exact lowercase Git hash")
    added = manifest.get("instrument_types_added", [])
    if (not isinstance(added, list)
            or not all(isinstance(a, str) and re.fullmatch(r"inst[A-Za-z0-9_]+", a) for a in added)
            or len(set(added)) != len(added)):
        raise ValueError("instrument_types_added must list distinct inst* enum names")
    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError("Contribution manifest lists no entries")

    entries: list[Entry] = []
    seen_paths: set[str] = set()
    seen_sources: set[str] = set()
    for index, record in enumerate(raw_entries):
        what = f"entry {index}"
        if not isinstance(record, dict):
            raise ValueError(f"{what}: not an object")
        path = _relative_path(record.get("path"), what)
        operation = record.get("operation")
        if operation not in OPERATIONS:
            raise ValueError(f"{what}: operation {operation!r} is not one of {OPERATIONS}")
        source = _relative_path(record.get("source"), what)
        expected_root = "files/" if operation == "add" else "patches/"
        if not source.startswith(expected_root):
            raise ValueError(f"{what}: {operation} source must live under {expected_root}")
        if path in seen_paths:
            raise ValueError(f"{what}: duplicate target path {path}")
        if source in seen_sources:
            raise ValueError(f"{what}: duplicate series file {source}")
        seen_paths.add(path)
        seen_sources.add(source)
        source_sha = _digest(record, "source_sha256", what)
        post_sha = _digest(record, "post_sha256", what)
        pre_sha = _digest(record, "pre_sha256", what) if operation == "modify" else None
        if operation == "add" and "pre_sha256" in record:
            raise ValueError(f"{what}: an addition has no pre-image")
        if operation == "add" and source_sha != post_sha:
            raise ValueError(f"{what}: an added file's series digest is its post-image digest")
        provenance = record.get("provenance")
        if not isinstance(provenance, dict) or not provenance:
            raise ValueError(f"{what}: provenance record is required")
        for key, value in provenance.items():
            if not isinstance(key, str) or not isinstance(value, str) or not value:
                raise ValueError(f"{what}: provenance values are non-empty strings")
        required = ({"author", "terms"} if operation == "add" else
                    {"upstream_author", "upstream_license", "change_author", "change_terms"})
        if not required <= provenance.keys():
            raise ValueError(f"{what}: provenance omits required authorship or terms")
        entries.append(Entry(path, operation, source, source_sha, pre_sha, post_sha, provenance))

    # Every file in the series directory is referenced by exactly one entry.
    present = set()
    for file in sorted(directory.rglob("*")):
        if file.is_symlink():
            raise ValueError(f"Contribution series contains a symlink: {file.name}")
        if file.is_file():
            present.add(file.relative_to(directory).as_posix())
        elif not file.is_dir():
            raise ValueError(f"Contribution series contains a non-regular entry: {file.name}")
    present.discard("manifest.json")
    if present != seen_sources:
        raise ValueError(
            f"Series files and manifest disagree: unlisted {sorted(present - seen_sources)}, "
            f"missing {sorted(seen_sources - present)}"
        )
    for entry in entries:
        if sha256((directory / entry.source).read_bytes()) != entry.source_sha256:
            raise ValueError(f"Series file digest mismatch: {entry.source}")

    return Series(
        name=name,
        directory=directory,
        manifest_sha256=sha256(manifest_bytes),
        base_archive_sha256=base_sha,
        instrument_types_added=tuple(added),
        source_origin=dict(origin),
        entries=tuple(entries),
    )


def _tokens(diff: str, path: str | None = None) -> list[list[tuple[str, str]]]:
    """Hunks of (kind, line) with 'no newline' markers folded into the line."""
    lines = diff.splitlines(keepends=True)
    if len(lines) < 2 or not lines[0].startswith("--- ") or not lines[1].startswith("+++ "):
        raise ValueError("Unified diff requires both file headers")
    if path is not None and lines[:2] != [f"--- a/{path}\n", f"+++ b/{path}\n"]:
        raise ValueError("Unified diff headers differ from the declared target")
    index = 2
    hunks: list[list[tuple[str, str]]] = []
    while index < len(lines):
        header = _HUNK.match(lines[index])
        if header is None:
            raise ValueError("Unified diff has text outside its hunks")
        old_start = int(header[1])
        old_count = int(header[2]) if header[2] is not None else 1
        new_start = int(header[3])
        new_count = int(header[4]) if header[4] is not None else 1
        index += 1
        body: list[tuple[str, str]] = [("@", f"{old_start} {old_count} {new_start} {new_count}")]
        while index < len(lines) and not lines[index].startswith("@@"):
            line = lines[index]
            index += 1
            if line.startswith("\\"):
                if (line != "\\ No newline at end of file\n" or len(body) < 2
                        or not body[-1][1].endswith("\n")):
                    raise ValueError("Unified diff has an invalid newline marker")
                kind, text = body[-1]
                body[-1] = (kind, text[:-1] if text.endswith("\n") else text)
                continue
            if line[:1] not in (" ", "-", "+"):
                raise ValueError("Unified diff has an unclassified line")
            body.append((line[0], line[1:]))
        hunks.append(body)
    if not hunks:
        raise ValueError("Unified diff has no hunks")
    return hunks


def apply_unified_diff(before: str, diff: str, path: str | None = None) -> str:
    """Apply at the stated positions only; any disagreement is an error."""
    old = before.splitlines(keepends=True)
    out: list[str] = []
    position = 0
    for body in _tokens(diff, path):
        old_start, old_count, new_start, new_count = (int(v) for v in body[0][1].split())
        start = old_start - 1 if old_count > 0 else old_start
        if start < position or start > len(old):
            raise ValueError("Unified diff hunk is out of order or beyond the file")
        out.extend(old[position:start])
        position = start
        if len(out) != (new_start - 1 if new_count > 0 else new_start):
            raise ValueError("Unified diff new-side position does not match")
        removed = added = 0
        for kind, text in body[1:]:
            if kind in (" ", "-"):
                if position >= len(old) or old[position] != text:
                    raise ValueError("Unified diff context or removed line does not match")
                position += 1
                removed += 1
            if kind in (" ", "+"):
                out.append(text)
                added += 1
        if removed != old_count or added != new_count:
            raise ValueError("Unified diff hunk counts do not match its lines")
    out.extend(old[position:])
    return "".join(out)


def _reject_symlinks(source: Path, relative: str) -> Path:
    if source.is_symlink():
        raise ValueError("Contribution source root is a symlink")
    target = source
    for part in relative.split("/"):
        target = target / part
        if target.is_symlink():
            raise ValueError(f"Contribution target crosses a symlink: {relative}")
    return target


def apply_series(series: Series, source: Path, archive_sha256: str) -> list[dict]:
    """Apply every entry in order and return one receipt row per entry."""
    if series.base_archive_sha256 != archive_sha256:
        raise ValueError("Contribution series was authored against a different base archive")
    receipt = []
    for entry in series.entries:
        target = _reject_symlinks(source, entry.path)
        series_bytes = (series.directory / entry.source).read_bytes()
        if sha256(series_bytes) != entry.source_sha256:
            raise ValueError(f"Series file digest mismatch: {entry.source}")
        if entry.operation == "add":
            if target.exists():
                raise ValueError(f"Contribution adds a path that already exists: {entry.path}")
            after = series_bytes
        else:
            if not target.is_file():
                raise ValueError(f"Contribution modifies a path that is absent: {entry.path}")
            before = target.read_bytes()
            if sha256(before) != entry.pre_sha256:
                raise ValueError(f"Pre-image digest mismatch: {entry.path}")
            after = apply_unified_diff(
                before.decode("utf-8"), series_bytes.decode("utf-8"), entry.path
            ).encode("utf-8")
        if sha256(after) != entry.post_sha256:
            raise ValueError(f"Post-image digest mismatch: {entry.path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(after)
        receipt.append({
            "path": entry.path,
            "operation": entry.operation,
            "series_file": entry.source,
            "source_sha256": entry.source_sha256,
            "pre_sha256": entry.pre_sha256,
            "post_sha256": entry.post_sha256,
            **{f"provenance_{key}": value for key, value in entry.provenance.items()},
        })
    return receipt


def tree_digests(source: Path) -> dict[str, str]:
    if source.is_symlink():
        raise ValueError("Contribution source root is a symlink")
    result = {}
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Composed source contains a symlink: {path.name}")
        if path.is_file():
            result[path.relative_to(source).as_posix()] = sha256(path.read_bytes())
        elif not path.is_dir():
            raise ValueError(f"Composed source contains a non-regular entry: {path.name}")
    return result


def verify_tree(source: Path, stock: dict[str, str], receipt: list[dict]) -> None:
    """The tree after application is the stock map with only the declared changes."""
    expected = dict(stock)
    for row in receipt:
        if row["operation"] == "add" and row["path"] in expected:
            raise ValueError(f"Addition collides with a stock path: {row['path']}")
        expected[row["path"]] = row["post_sha256"]
    actual = tree_digests(source)
    if actual != expected:
        changed = sorted(p for p in set(actual) | set(expected) if actual.get(p) != expected.get(p))
        raise ValueError(f"Composed source differs from stock plus series at: {changed}")


def apply_contribution(directory: Path, source: Path, stock: dict[str, str],
                       archive_sha256: str) -> dict:
    """Load, apply and verify a series; return the receipt section for report.json."""
    series = load_series(directory)
    receipt = apply_series(series, source, archive_sha256)
    verify_tree(source, stock, receipt)
    return {
        "name": series.name,
        "directory_name": series.directory.name,
        "manifest_sha256": series.manifest_sha256,
        "base_archive_sha256": series.base_archive_sha256,
        "instrument_types_added": list(series.instrument_types_added),
        "source_origin": dict(series.source_origin),
        "entries": receipt,
    }
