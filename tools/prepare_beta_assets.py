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
import tempfile
import tomllib
import zipfile
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
    if metadata["system"] == "Linux":
        # The menu script writes into the user's home; deliver the committed bytes only.
        launcher = application / "Add to Applications.sh"
        committed = ROOT / "tools/desktop_builder/install-linux-desktop.sh"
        if (launcher.is_symlink() or not launcher.is_file()
                or not os.access(launcher, os.X_OK)
                or launcher.read_bytes() != committed.read_bytes()):
            raise ValueError("Distribution menu script differs from this source")


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


def create_archive(application, base):
    """Archive the application folder with its parent name, as users extract it."""
    if platform.system() == "Darwin":
        archive = base.with_name(base.name + ".zip")
        subprocess.run(
            ["/usr/bin/ditto", "-c", "-k", "--norsrc", "--keepParent",
             str(application), str(archive)],
            check=True,
        )
        return archive
    return Path(shutil.make_archive(
        str(base), "zip" if os.name == "nt" else "gztar",
        root_dir=application.parent, base_dir=application.name,
    ))


def extract_archive(archive, destination):
    """Unpack with the platform's ordinary extractor; macOS keeps the bundle seal."""
    if platform.system() == "Darwin":
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(destination)],
                       check=True)
    elif os.name == "nt":
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(destination)
    else:
        subprocess.run(["tar", "-xzf", str(archive), "-C", str(destination)], check=True)


def verify_application(application, version):
    """Authenticate one application folder without running it or its workers."""
    from color_measurement.discovery import InstrumentBuild

    resources = package_root(application)
    metadata = json.loads((resources / "PACKAGE.json").read_text())
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
    # Authentication only, never worker execution.
    build = InstrumentBuild.from_report(resources / "instrument-software/report.json")
    if build._client._report_sha256 != metadata["instrument_report_sha256"]:
        raise ValueError("Packaged worker report changed")
    if metadata["system"] == "Darwin":
        verify_adhoc_signature(application)
    refuse_build_paths(application, home_interpreter_prefixes())
    return metadata, build


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
    application_source = args.application.resolve()
    if args.output.parent.resolve().is_relative_to(application_source):
        parser.error("Choose an asset directory outside the application folder")
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
    if dirty.strip():
        parser.error("Commit the complete candidate before preparing its assets")
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    target = package_target(dict(system=platform.system(), architecture=platform.machine()))
    name = f"Color-Measurement-{version}-{target}"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Every check reads the extracted archive: the staged folder may change after archiving.
    with tempfile.TemporaryDirectory(prefix=".cm-", dir=args.output.parent) as work:
        work = Path(work)
        archive = create_archive(application_source, work / name)
        archive_digest = sha256(archive)
        extract_archive(archive, work)
        application = work / application_source.name
        metadata, build = verify_application(application, version)
        # Read once: the recorded check scope must come from the authenticated bytes.
        native_report = args.native_report.read_bytes()
        native_report_digest = hashlib.sha256(native_report).hexdigest()
        if build._client._build_report["source_report_sha256"] != native_report_digest:
            raise ValueError("Original native report differs from the package's retained identity")
        native_check_scope = json.loads(native_report).get("check_scope")
        if native_check_scope not in ("full_native_regressions", "substituted_binding"):
            raise ValueError("Original native report does not declare a known check scope")
        if metadata["system"] == "Darwin":
            info = plistlib.loads((application / "Contents/Info.plist").read_bytes())
        sources = corresponding_sources(args.source_directory, version)
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT,
                                       text=True).strip()
        delivery = work / "delivery"
        delivery.mkdir()
        archive = archive.rename(delivery / archive.name)
        copy_sources(sources, delivery, version)
        digests = {path.name: sha256(path) for path in delivery.iterdir()}
        if any(digests[path.name] != digest for path, digest in sources.items()):
            raise ValueError("Delivered source attachment differs from its verified bytes")
        if digests[archive.name] != archive_digest:
            raise ValueError("Application archive changed after verification")
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
            native_check_scope=native_check_scope,
            production_workers_executed=False,
            corresponding_source={path.name: digests[path.name] for path in sources},
            corresponding_source_required=True,
        )
        if metadata["system"] == "Darwin":
            manifest.update(minimum_macos=info["LSMinimumSystemVersion"],
                            signing="ad-hoc", notarized=False)
        target_notice = release_notice(metadata["system"], manifest.get("minimum_macos"))
        if manifest["native_check_scope"] == "substituted_binding":
            target_notice += (
                "Preview with partial target checks: native functional and app/profile tests.\n"
                "Full native source-regression qualification remains open on this target.\n"
            )
        (delivery / "SOURCE-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (delivery / "RELEASE.txt").write_text(
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
            digests[name] = sha256(delivery / name)
        (delivery / "SHA256SUMS").write_text(
            "".join(digests[name] + "  " + name + "\n" for name in sorted(digests))
        )
        # Claim the unused name only after every asset and record is ready.
        args.output.mkdir()
        try:
            for path in delivery.iterdir():
                path.rename(args.output / path.name)
        except BaseException:
            shutil.rmtree(args.output)
            raise
    print("Prepared local beta assets:", args.output)


if __name__ == "__main__":
    main()
