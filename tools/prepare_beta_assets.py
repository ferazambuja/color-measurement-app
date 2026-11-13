"""Archive a source-checked target candidate, with checksums and source-set binding.

Run only after the target gate and build pass. This prepares local assets; it
never uploads, creates a release, starts an app or runs instrument workers.
"""

import argparse
import hashlib
import json
import os
import platform
import plistlib
import shutil
import subprocess
import tarfile
import tomllib
from pathlib import Path, PurePosixPath

from build_desktop_app import (
    APPLICATION_NAME,
    BUNDLE_IDENTIFIER,
    home_interpreter_prefixes,
    package_root,
    refuse_build_paths,
    verify_package_code,
)
from collect_runtime_sources import verify_sources

ROOT = Path(__file__).resolve().parents[1]
DOCS_URL = "https://github.com/ferazambuja/color-measurement-app/blob/main/docs/"
# Windows/Linux requirements and prompt guidance live in this section, not the macOS guide.
RUNTIME_REQUIREMENTS = "platforms.md#desktop-runtime-requirements"


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package_target(metadata):
    """Use one target name for archives and combined release records."""
    system = {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}[metadata["system"]]
    machine = metadata["architecture"].lower()
    architecture = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    if architecture not in {"x86_64", "arm64"}:
        raise ValueError("Unsupported package architecture")
    return system + "-" + architecture


def copy_sources(sources, output, version):
    """Keep small mutable inputs independent and reuse immutable runtime archives."""
    independent = {f"color_measurement-{version}.tar.gz", "Argyll_V3.5.0_src.zip"}
    for path in sources:
        target = Path(output) / path.name
        if path.name in independent:
            shutil.copy2(path, target)
        else:
            try:
                os.link(path, target)
            except OSError:
                shutil.copy2(path, target)


def verify_distribution_identity(application, metadata):
    """A candidate renamed after freezing is not a distribution build."""
    name = APPLICATION_NAME
    if metadata.get("application_name") != name:
        raise ValueError("Build a distribution without --candidate; do not rename it afterwards")
    application = Path(application)
    if (metadata["system"] == "Darwin") != (application.suffix == ".app"):
        raise ValueError("Distribution layout differs from its target operating system")
    if application.suffix == ".app":
        info = plistlib.loads((application / "Contents/Info.plist").read_bytes())
        expected = dict(
            CFBundleExecutable=name,
            CFBundleName=name,
            CFBundleIdentifier=BUNDLE_IDENTIFIER,
            CFBundleVersion=metadata["version"],
            CFBundleShortVersionString=metadata["version"],
            CMInstrumentSoftwareReportSHA256=metadata["instrument_report_sha256"],
        )
        if application.name != name + ".app" or any(
            info.get(key) != value for key, value in expected.items()
        ):
            raise ValueError("Distribution bundle identity differs from its package metadata")
        executable = application / "Contents/MacOS" / name
    else:
        if application.name != name:
            raise ValueError("Distribution application folder has the wrong name")
        executable = application / (name + (".exe" if metadata["system"] == "Windows" else ""))
    if executable.is_symlink() or not executable.is_file():
        raise ValueError("Distribution executable is missing or a symlink")


def verify_adhoc_signature(application):
    """Check the complete bundle seal and the beta's ad-hoc signing policy.

    The seal is the only integrity check for third-party runtime files after
    freezing. Inspecting a signature does not execute the application.
    """
    verified = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(application)],
        capture_output=True,
    )
    details = subprocess.run(
        ["/usr/bin/codesign", "--display", "--verbose=2", str(application)],
        capture_output=True, text=True,
    )
    required = {"Signature=adhoc", "TeamIdentifier=not set", "Identifier=" + BUNDLE_IDENTIFIER}
    if (verified.returncode or details.returncode
            or not required <= set(details.stderr.splitlines())):
        raise ValueError("Distribution signature is not an intact ad-hoc beta seal")


def verify_source_distribution(archive, root):
    """Compare archived bytes with every included tracked file, without extraction."""
    root = Path(root)
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    expected = {name for name in tracked if name and name != ".gitattributes"}
    with tarfile.open(archive, "r:gz") as source:
        files = {}
        prefix = Path(archive).name.removesuffix(".tar.gz") + "/"
        for entry in source.getmembers():
            if (entry.name != prefix.rstrip("/") and not entry.name.startswith(prefix)
                    or ".." in PurePosixPath(entry.name).parts):
                raise ValueError("Source distribution has an unexpected path")
            if entry.isdir():
                continue
            if not entry.isfile() or not entry.name.startswith(prefix):
                raise ValueError("Source distribution has an unexpected entry")
            name = entry.name[len(prefix):]
            if name in files:
                raise ValueError("Source distribution has a duplicate entry")
            files[name] = entry
        if set(files) != expected | {"PKG-INFO"}:
            raise ValueError("Source distribution inventory differs from tracked source")
        for name in sorted(expected):
            path = root / name
            if path.is_symlink() or source.extractfile(files[name]).read() != path.read_bytes():
                raise ValueError("Source distribution differs from this checkout: " + name)


def corresponding_sources(directory, version, root=ROOT):
    """Verify all four required source attachments before creating any assets.

    Returns each attachment's verified digest, to bind the delivered copies.
    """
    from color_measurement.argyll_source import ARCHIVE_SHA256

    directory = Path(directory)
    origin = verify_sources(directory)
    argyll = directory / "Argyll_V3.5.0_src.zip"
    if argyll.is_symlink() or sha256(argyll) != ARCHIVE_SHA256:
        raise ValueError("Argyll source archive differs from the pinned archive")
    app = directory / f"color_measurement-{version}.tar.gz"
    if app.is_symlink():
        raise ValueError("Application source archive must not be a symlink")
    app_digest = sha256(app)
    verify_source_distribution(app, root)
    if sha256(app) != app_digest:
        raise ValueError("Application source archive changed during verification")
    return {app: app_digest, argyll: ARCHIVE_SHA256,
            **{directory / item["filename"]: item["sha256"] for item in origin["archives"]}}


def release_notice(system, minimum_macos=None):
    """State the target requirements and its applicable security-prompt guidance."""
    if system == "Darwin":
        return (
            f"Requires macOS {minimum_macos} or later.\n"
            "This beta is ad-hoc signed and is not notarized. Gatekeeper may block it.\n"
            "Follow the guide's app-specific approval instructions.\n"
            "Never disable Gatekeeper or remove quarantine.\n"
        )
    if system == "Windows":
        return (
            "Windows packages are experimental.\n"
            "SmartScreen or system policy may block this beta.\n"
            f"Requirements and SmartScreen guidance: {DOCS_URL}{RUNTIME_REQUIREMENTS}\n"
            "Never disable SmartScreen, antivirus or system policy.\n"
        )
    return (
        "Linux packages are experimental.\n"
        f"Required system libraries: {DOCS_URL}{RUNTIME_REQUIREMENTS}\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application", type=Path, required=True)
    parser.add_argument("--native-report", type=Path, required=True)
    parser.add_argument("--source-directory", type=Path, required=True,
                        help="Matching app sdist, pinned Argyll ZIP and Qt/PySide archives")
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
    verify_distribution_identity(application, metadata)
    if metadata["package_sha256"] != verify_package_code(
        application, ROOT / "src/color_measurement",
        report_sha256=metadata["instrument_report_sha256"],
    ):
        raise ValueError("Packaged source differs from this checkout")
    report = resources / "instrument-software/report.json"
    if sha256(report) != metadata["instrument_report_sha256"]:
        raise ValueError("Packaged worker report changed")
    InstrumentBuild.from_report(report)  # Authentication only, never worker execution.
    native_report_digest = sha256(args.native_report)
    if json.loads(report.read_bytes())["source_report_sha256"] != native_report_digest:
        raise ValueError("Original native report differs from the package's retained identity")
    if metadata["system"] == "Darwin":
        verify_adhoc_signature(application)
    refuse_build_paths(application, home_interpreter_prefixes())
    sources = corresponding_sources(args.source_directory, version)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True).strip()
    args.output.mkdir(parents=True)
    target = package_target(metadata)
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
    copy_sources(sources, args.output, version)
    digests = {path.name: sha256(path) for path in args.output.iterdir()}
    if any(digests[path.name] != digest for path, digest in sources.items()):
        raise ValueError("Delivered source attachment differs from its verified bytes")
    if sha256(args.native_report) != native_report_digest:
        raise ValueError("Original native report changed during preparation")
    manifest = dict(
        format=1,
        beta=True,
        commit=commit,
        tree=tree,
        package=metadata,
        archive=archive.name,
        archive_sha256=digests[archive.name],
        native_report_sha256=native_report_digest,
        production_workers_executed=False,
        corresponding_source={path.name: digests[path.name] for path in sources},
        corresponding_source_required=True,
    )
    if metadata["system"] == "Darwin":
        info = plistlib.loads((application / "Contents/Info.plist").read_bytes())
        manifest.update(minimum_macos=info["LSMinimumSystemVersion"],
                        signing="ad-hoc", notarized=False)
    target_notice = release_notice(metadata["system"], manifest.get("minimum_macos"))
    (args.output / "SOURCE-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (args.output / "RELEASE.txt").write_text(
        f"Color Measurement {version} beta ({target}). Testers wanted.\n"
        f"Source commit: {commit}\nSource tree: {tree}\n\n"
        + target_notice +
        "Verify the application archive against SHA256SUMS before extracting.\n"
        "Keep the complete application folder together.\n"
        "Start with Demo; it is simulated and needs no instrument.\n"
        "Physical instrument operation and accuracy have not been validated.\n"
        f"Guide: {DOCS_URL}getting-started.md\n"
        "Feedback: https://github.com/ferazambuja/color-measurement-app/issues/new/choose\n\n"
        "Distribute all matching app, Argyll and Qt/PySide source attachments together.\n"
        "SOURCE-MANIFEST.json binds their digests to this application archive.\n"
    )
    for name in ("SOURCE-MANIFEST.json", "RELEASE.txt"):
        digests[name] = sha256(args.output / name)
    (args.output / "SHA256SUMS").write_text(
        "".join(digests[name] + "  " + name + "\n" for name in sorted(digests))
    )
    print("Prepared local beta assets:", args.output)


if __name__ == "__main__":
    main()
