"""Generate an application contribution from a clean, exact driver Git commit.

The committed recipe owns the selected paths and attribution. Git owns their
bytes; the pinned official archive owns every patch preimage. This tool never
merges branches, operates an instrument or imports an application overlay.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

from native_argyll.contribution import (
    SCHEMA_VERSION,
    _relative_path,
    _unique_object,
    apply_unified_diff,
    load_series,
    sha256,
)

from color_measurement.argyll_source import ARCHIVE_SHA256, ARCHIVE_VERSION

DEFAULT_RECIPE = Path(__file__).with_name("contribution_recipes") / "cr_pr.json"
DEFAULT_OUTPUT = Path(__file__).with_name("contributions") / "cr_pr"
_COMMIT = re.compile(r"[0-9a-f]{40}")


def git(repository: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", "--no-optional-locks", "--no-replace-objects", "-C", str(repository),
         "--literal-pathspecs", *arguments],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Git failed: {detail or f'{arguments[0]} exited {result.returncode}'}")
    return result.stdout


def require_clean(repository: Path) -> None:
    if git(repository, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("Driver source repository is dirty")


def blob(repository: Path, commit: str, path: str) -> bytes:
    entry = git(repository, "ls-tree", "-z", commit, "--", path)
    expected_path = path.encode("utf-8") + b"\0"
    if not entry or b"\t" not in entry:
        raise ValueError(f"Driver commit has no regular file: {path}")
    header, actual_path = entry.split(b"\t", 1)
    mode, kind, _ = header.split()
    if actual_path != expected_path or mode not in (b"100644", b"100755") or kind != b"blob":
        raise ValueError(f"Driver commit path is not one regular file: {path}")
    return git(repository, "show", f"{commit}:{path}")


def derive(repository: Path, commit: str, archive: Path, recipe_path: Path) -> dict[str, bytes]:
    """Derive every output without changing the source or destination."""
    repository = repository.resolve(strict=True)
    if Path(git(repository, "rev-parse", "--show-toplevel").decode().strip()).resolve(
            strict=True) != repository:
        raise ValueError("Source must name the driver repository root")
    require_clean(repository)
    if not _COMMIT.fullmatch(commit) or git(repository, "cat-file", "-t", commit) != b"commit\n":
        raise ValueError("Source must be an exact lowercase 40-digit Git commit")
    tree = git(repository, "rev-parse", f"{commit}^{{tree}}").decode().strip()
    base = git(repository, "rev-parse", "--verify",
               f"refs/tags/upstream/v{ARCHIVE_VERSION}^{{commit}}").decode().strip()
    git(repository, "merge-base", "--is-ancestor", base, commit)
    archive_bytes = archive.read_bytes()
    if sha256(archive_bytes) != ARCHIVE_SHA256:
        raise ValueError("Official Argyll archive digest differs from the application pin")
    recipe = json.loads(recipe_path.read_text(), object_pairs_hook=_unique_object)
    if (not isinstance(recipe, dict)
            or set(recipe) != {"name", "description", "instrument_types_added", "entries"}
            or not isinstance(recipe["entries"], list) or not recipe["entries"]):
        raise ValueError("Import recipe must declare its metadata and selected entries")
    output: dict[str, bytes] = {}
    entries = []
    paths = set()
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zipped:
        stock_names = set(zipped.namelist())
        for declaration in recipe["entries"]:
            if not isinstance(declaration, dict) or set(declaration) != {"path", "provenance"}:
                raise ValueError("Each import recipe entry needs only path and provenance")
            path = _relative_path(declaration["path"], "recipe")
            if path in paths:
                raise ValueError(f"Import recipe repeats a path: {path}")
            paths.add(path)
            after = blob(repository, commit, path)
            after.decode("utf-8")
            member = f"Argyll_V{ARCHIVE_VERSION}/{path}"
            entry = {"path": path, "post_sha256": sha256(after),
                     "provenance": declaration["provenance"]}
            if member not in stock_names:
                entry.update(operation="add", source=f"files/{path}")
                payload = after
            else:
                before = zipped.read(member)
                if blob(repository, base, path) != before:
                    raise ValueError(
                        f"Git baseline differs from the official stock preimage: {path}")
                if before == after:
                    raise ValueError(f"Recipe selects an unchanged stock file: {path}")
                diff = git(repository, "diff", "--no-ext-diff", "--no-textconv", "--no-color",
                           "--diff-algorithm=myers", "--no-indent-heuristic", "--unified=0",
                           "--inter-hunk-context=0",
                           "--no-renames", "--src-prefix=a/", "--dst-prefix=b/",
                           base, commit, "--", path)
                header = f"--- a/{path}\n".encode()
                start = diff.find(header)
                if start < 0:
                    raise ValueError(f"Git did not produce a text patch for: {path}")
                payload = diff[start:]
                if apply_unified_diff(before.decode("utf-8"), payload.decode("utf-8"), path
                                      ).encode("utf-8") != after:
                    raise ValueError(f"Patch does not reproduce the pinned driver blob: {path}")
                entry.update(operation="modify", source=f"patches/{path}.patch",
                             pre_sha256=sha256(before))
            entry["source_sha256"] = sha256(payload)
            output[entry["source"]] = payload
            entries.append({key: entry[key] for key in
                            ("path", "operation", "source", "source_sha256", "pre_sha256",
                             "post_sha256", "provenance") if key in entry})
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "name": recipe["name"], "description": recipe["description"],
        "base": {"argyll_version": ARCHIVE_VERSION, "archive_sha256": ARCHIVE_SHA256},
        "source_origin": {"repository": repository.name, "commit": commit, "tree": tree},
        "instrument_types_added": recipe["instrument_types_added"], "entries": entries,
    }
    output["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    # Reuse the authoritative manifest/file verifier before publishing anything.
    with tempfile.TemporaryDirectory(prefix="contribution-import-") as temporary:
        stage = Path(temporary)
        for name, data in output.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        load_series(stage)
    require_clean(repository)
    return output


def publish(output: dict[str, bytes], directory: Path, *, check: bool) -> None:
    """Check exact generated bytes, or publish only the declared generated files."""
    directory = directory.absolute()
    for ancestor in (directory, *directory.parents):
        if ancestor.is_symlink():
            raise ValueError("Contribution output must not traverse a symlink")
    if directory.exists() and not directory.is_dir():
        raise ValueError("Contribution output must be a directory")
    present = set()
    if directory.exists():
        for path in directory.rglob("*"):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError("Contribution output contains a non-regular entry")
            if path.is_file():
                present.add(path.relative_to(directory).as_posix())
    if check:
        load_series(directory)
        different = sorted(name for name in set(output) | present
                           if name not in output or name not in present
                           or (directory / name).read_bytes() != output[name])
        if different:
            raise ValueError(f"Generated contribution differs at: {different}")
        return
    if present - set(output):
        raise ValueError(
            f"Refusing to overwrite unlisted output files: {sorted(present - set(output))}")
    # Manifest last: interrupted regeneration cannot authenticate mixed bytes.
    for name in [key for key in output if key != "manifest.json"] + ["manifest.json"]:
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(output[name])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--recipe", type=Path, default=DEFAULT_RECIPE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        output = derive(args.source_repo, args.commit, args.archive, args.recipe)
        publish(output, args.output, check=args.check)
    except (ValueError, OSError, UnicodeError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Contribution import refused: {error}\n")
    print("Contribution matches pinned source" if args.check else "Contribution regenerated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
