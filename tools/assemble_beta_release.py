"""Combine verified target deliveries into one local release asset directory."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

from prepare_beta_assets import (
    ROOT,
    copy_sources,
    corresponding_sources,
    package_target,
    sha256,
)


def read_candidate(directory):
    """Validate every candidate file before using its release metadata."""
    directory = Path(directory)
    digests = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\]+)", line)
        if match is None:
            raise ValueError("Invalid candidate checksum entry")
        digest, name = match.groups()
        if name in {".", "..", "SHA256SUMS"} or name in digests:
            raise ValueError("Duplicate or invalid candidate checksum name")
        path = directory / name
        if path.is_symlink() or not path.is_file() or sha256(path) != digest:
            raise ValueError("Candidate checksum differs: " + name)
        digests[name] = digest
    encoded = (directory / "SOURCE-MANIFEST.json").read_bytes()
    release = (directory / "RELEASE.txt").read_bytes()
    if (hashlib.sha256(encoded).hexdigest() != digests.get("SOURCE-MANIFEST.json")
            or hashlib.sha256(release).hexdigest() != digests.get("RELEASE.txt")):
        raise ValueError("Candidate records changed during verification")
    manifest = json.loads(encoded)
    if manifest.get("format") != 1 or manifest.get("beta") is not True:
        raise ValueError("Expected a prepared beta candidate")
    archive = manifest["archive"]
    sources = manifest["corresponding_source"]
    expected = {archive, "SOURCE-MANIFEST.json", "RELEASE.txt", *sources}
    if set(digests) != expected or len(sources) != 4:
        raise ValueError("Candidate checksum inventory differs")
    if (digests.get(archive) != manifest["archive_sha256"]
            or any(digests.get(name) != digest for name, digest in sources.items())):
        raise ValueError("Candidate manifest differs from delivered bytes")
    return manifest, release.decode("utf-8")


def assemble(candidates, output, *, commit, tree):
    """Require one source revision and one package for each selected target."""
    output = Path(output)
    if output.exists():
        raise ValueError("Choose an unused release directory")
    targets = {}
    releases = []
    prepared = []
    first = None
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    for directory in map(Path, candidates):
        manifest, release = read_candidate(directory)
        package = manifest["package"]
        target = package_target(package)
        suffix = ".tar.gz" if package["system"] == "Linux" else ".zip"
        if (package["version"] != version or manifest["archive"] !=
                f"Color-Measurement-{version}-{target}{suffix}"):
            raise ValueError("Candidate archive name or version differs")
        if manifest["commit"] != commit or manifest["tree"] != tree:
            raise ValueError("Candidate source revision differs from this release")
        if target in targets:
            raise ValueError("Duplicate release target")
        if first is None:
            first = manifest
        elif (manifest["corresponding_source"] != first["corresponding_source"]
              or package["package_sha256"] != first["package"]["package_sha256"]):
            raise ValueError("Targets have different source sets or versions")
        targets[target] = manifest
        releases.append(release)
        prepared.append((directory, manifest))
    if first is None:
        raise ValueError("At least one target is required")
    source_directory = Path(candidates[0])
    output.mkdir(parents=True)
    copy_sources([source_directory / name for name in first["corresponding_source"]],
                 output, first["package"]["version"])
    for directory, manifest in prepared:
        archive = manifest["archive"]
        shutil.copy2(directory / archive, output / archive)
        if sha256(output / archive) != manifest["archive_sha256"]:
            raise ValueError("Copied target archive changed")
    if any(sha256(output / name) != digest
           for name, digest in first["corresponding_source"].items()):
        raise ValueError("Copied source archive changed")
    combined = dict(format=2, beta=True, commit=commit, tree=tree,
                    corresponding_source=first["corresponding_source"], targets=targets)
    (output / "SOURCE-MANIFEST.json").write_text(json.dumps(combined, indent=2) + "\n")
    (output / "RELEASE.txt").write_text("\n\n".join(releases))
    files = sorted(output.iterdir())
    (output / "SHA256SUMS").write_text(
        "".join(sha256(path) + "  " + path.name + "\n" for path in files))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip():
        parser.error("Commit the complete release source before assembly")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True).strip()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    corresponding_sources(args.candidates[0], version)
    assemble(args.candidates, args.output, commit=commit, tree=tree)
    print("Prepared combined beta assets:", args.output)


if __name__ == "__main__":
    main()
