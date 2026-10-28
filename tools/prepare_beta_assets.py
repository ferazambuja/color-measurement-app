"""Archive a source-checked target candidate, with checksums and source-set binding.

Run only after the target gate and build pass. This prepares local assets; it
never uploads, creates a release, starts an app or runs instrument workers.
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tomllib
from pathlib import Path

from build_desktop_app import package_root, verify_package_code

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application", type=Path, required=True)
    parser.add_argument("--native-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose an unused asset directory")
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
    if dirty.strip():
        parser.error("Commit the complete candidate before preparing its assets")
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from color_measurement.discovery import InstrumentBuild

    application = args.application.resolve()
    resources = package_root(application)
    metadata = json.loads((resources / "PACKAGE.json").read_text())
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    if (metadata.get("format") != 1 or metadata.get("version") != version
            or metadata.get("system") != platform.system()
            or metadata.get("architecture") != platform.machine()):
        raise ValueError("Application metadata must match this source and target")
    if metadata["package_sha256"] != verify_package_code(
        application, ROOT / "src/color_measurement"
    ):
        raise ValueError("Packaged source differs from this checkout")
    report = resources / "instrument-software/report.json"
    if sha256(report) != metadata["instrument_report_sha256"]:
        raise ValueError("Packaged worker report changed")
    InstrumentBuild.from_report(report)  # Authentication only, never worker execution.
    if json.loads(report.read_bytes())["source_report_sha256"] != sha256(args.native_report):
        raise ValueError("Original native report differs from the package's retained identity")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True).strip()
    args.output.mkdir(parents=True)
    target = metadata["system"].lower() + "-" + metadata["architecture"].lower()
    name = f"Color-Measurement-{metadata['version']}-{target}"
    if platform.system() == "Darwin":
        archive = args.output / (name + ".zip")
        subprocess.run(
            [
                "/usr/bin/ditto",
                "-c",
                "-k",
                "--norsrc",
                "--keepParent",
                str(application),
                str(archive),
            ],
            check=True,
        )
    else:
        archive = Path(
            shutil.make_archive(
                str(args.output / name),
                "zip" if os.name == "nt" else "gztar",
                root_dir=application.parent,
                base_dir=application.name,
            )
        )
    manifest = dict(
        beta=True,
        commit=commit,
        tree=tree,
        package=metadata,
        archive=archive.name,
        archive_sha256=sha256(archive),
        native_report_sha256=sha256(args.native_report),
        production_workers_executed=False,
        corresponding_source_artifact="corresponding-source-" + commit,
        corresponding_source_required=True,
    )
    (args.output / "SOURCE-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (args.output / "README.txt").write_text(
        "Color Measurement beta candidate. No physical instrument qualification.\n"
        "Keep the complete application folder together.\n"
        "This binary is incomplete for distribution without the matching app, Argyll and "
        "Qt/PySide Corresponding Source set named in SOURCE-MANIFEST.json.\n"
        "No release was published by this preparation command.\n"
    )
    paths = sorted(args.output.iterdir())
    (args.output / "SHA256SUMS").write_text(
        "".join(sha256(path) + "  " + path.name + "\n" for path in paths)
    )
    print("Prepared local beta assets:", args.output)


if __name__ == "__main__":
    main()
