"""Build a one-folder Qt desktop application on its target operating system.

Production workers are authenticated and copied after freezing, never executed
or re-signed by PyInstaller. There is no cross-compilation or upload in this tool.
"""

import argparse
import hashlib
import importlib.metadata
import io
import json
import marshal
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

APPLICATION_NAME = "Color Measurement"
BUNDLE_IDENTIFIER = "io.github.ferazambuja.colormeasurement"


def prepare_instrument_software(report_path, destination):
    """Stage authenticated production workers and portable metadata; never run them.

    The runtime keeps the existing InstrumentBuild report contract. Historical
    commands, logs and source inventories are omitted from emitted metadata.
    Worker bytes, including compiled debug strings, are retained unchanged.
    """
    from color_measurement.discovery import InstrumentBuild
    from color_measurement.native_receipt import native_worker_name

    report_path = Path(report_path).resolve()
    original = report_path.read_bytes()
    build = InstrumentBuild.from_report(report_path)
    source_digest = hashlib.sha256(original).hexdigest()
    if build._client._report_sha256 != source_digest:
        raise ValueError("Instrument software report changed during authentication")
    report = build._client._build_report
    runtime = {
        key: report[key]
        for key in (
            "status",
            "argyll_version",
            "archive_sha256",
            "build_scope",
            "build_system",
            "driver_scope",
            "driver_manifest",
        )
    }
    runtime["source_report_sha256"] = source_digest
    payloads = {}
    for section, filename in (
        ("description_worker_build_link", "argyll-description-worker-never-run"),
        ("generic_worker_build_link", "generic-worker-never-run"),
    ):
        filename = native_worker_name(filename, report=report)
        link = report[section]
        if link.get("executed") is not False:
            raise ValueError("Instrument software requires unexecuted production worker receipts")
        runtime[section] = {
            key: link[key]
            for key in (
                "status",
                "executed",
                "executable_sha256",
            )
        }
        worker = report_path.parent / filename
        content = worker.read_bytes()
        if hashlib.sha256(content).hexdigest() != link["executable_sha256"]:
            raise ValueError("Instrument software worker changed during staging")
        payloads[filename] = (content, 0o755)

    notices = report.get("upstream_license_inventory")
    if not isinstance(notices, dict) or not notices.get("license_texts"):
        raise ValueError("Instrument software requires its retained native notices")
    declared = [(notices.get("notices_path"), notices.get("notices_sha256"))]
    declared.extend(
        (item["retained_path"], item["sha256"]) for item in notices["license_texts"].values()
    )
    retained = {}
    for relative, expected in declared:
        if not isinstance(relative, str) or not relative:
            raise ValueError("Instrument software notice needs a relative path")
        path = (report_path.parent / relative).resolve()
        if Path(relative).is_absolute() or not path.is_relative_to(report_path.parent):
            raise ValueError("Instrument software notice must remain beside its build report")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError("Instrument software notice changed")
        name = "licenses/" + path.relative_to(report_path.parent).as_posix()
        payloads[name] = (content, 0o644)
        retained[name] = expected
    runtime["bundled_license_sha256"] = retained
    if report_path.read_bytes() != original:
        raise ValueError("Instrument software report changed during staging")
    encoded = (
        json.dumps(runtime, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")
    payloads["report.json"] = (encoded, 0o644)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    groups = {}
    for name, (content, mode) in payloads.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(mode)
        relative_parent = path.relative_to(destination).parent
        target = (Path("instrument-software") / relative_parent).as_posix()
        groups.setdefault(target, []).append(str(path))
    return dict(
        data_files=list(groups.items()),
        report_sha256=hashlib.sha256(encoded).hexdigest(),
        source_report_sha256=source_digest,
    )


def serial_notices(root):
    """Retain pySerial's source license, which its wheel does not carry as a file."""
    folder = root / "tools" / "desktop_runtime_licenses"
    receipt = folder / "SERIAL-ORIGIN.json"
    origin = json.loads(receipt.read_text())
    notice = folder / origin["path"]
    if importlib.metadata.version(origin["component"]) != origin["version"]:
        raise ValueError("Update pySerial runtime notices for the bundled version")
    if hashlib.sha256(notice.read_bytes()).hexdigest() != origin["sha256"]:
        raise ValueError("pySerial runtime notice changed")
    return [("licenses/runtime", [str(receipt), str(notice)])]


def package_root(application):
    """Locate portable data without relying on a macOS-only bundle identity."""
    application = Path(application)
    return (
        application / "Contents" / "Resources"
        if application.suffix == ".app"
        else application / "_internal"
    )


def install_instrument_software(application, prepared):
    """Copy authenticated workers after freezing and preserve their exact bytes."""
    from color_measurement.discovery import InstrumentBuild

    resources = package_root(application)
    for relative, files in prepared["data_files"]:
        target = resources / relative
        target.mkdir(parents=True, exist_ok=True)
        for source in files:
            shutil.copy2(source, target / Path(source).name)
    report = resources / "instrument-software" / "report.json"
    if hashlib.sha256(report.read_bytes()).hexdigest() != prepared["report_sha256"]:
        raise ValueError("Packaged instrument software report changed")
    InstrumentBuild.from_report(report)
    if application.suffix == ".app":
        sign_qt_helper(application)

        # Qt/Python libraries are already signed by PyInstaller. Never --deep:
        # changing nested worker signatures would invalidate their fingerprints.
        subprocess.run(
            ["/usr/bin/codesign", "--force", "--sign", "-", str(application)], check=True
        )
    InstrumentBuild.from_report(report)


def sign_qt_helper(application):
    """Keep the upstream helper's ad-hoc beta policy after PyInstaller freezing.

    PyInstaller adds hardened runtime without a Team ID. Its library validation
    rejects the ad-hoc Qt framework, so the renderer dies before starting. Qt's
    distributed helper uses ad-hoc signing without hardened runtime. Restore that
    policy only for this helper, then reseal its framework; do not alter workers.
    A future notarized build needs the documented Qt entitlement/signing route.
    """
    framework = Path(application) / "Contents/Frameworks/PySide6/Qt/lib/QtWebEngineCore.framework"
    helper = framework / "Versions/A/Helpers/QtWebEngineProcess.app"
    for target in (helper, framework):
        subprocess.run(
            ["/usr/bin/codesign", "--force", "--sign", "-", "--options", "0", str(target)],
            check=True,
        )


def verify_package_code(application, source, *, report_sha256=None):
    """Compare frozen noarchive code and resources against current source, without execution."""
    import importlib.util

    application, source = Path(application), Path(source)
    resources = package_root(application)
    package = resources / "color_measurement"
    if application.suffix == ".app":
        code = application / "Contents" / "Frameworks" / "color_measurement"
    else:
        code = package
    inventory = {}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise ValueError("Application source must not contain symlinks")
        if not path.is_file():
            continue
        content = path.read_bytes()
        target = code / relative.with_suffix(".pyc") if path.suffix == ".py" else package / relative
        if target.is_symlink() or not target.resolve().is_relative_to(application.resolve()):
            raise ValueError("Frozen package file must remain inside the application")
        if path.suffix == ".py":
            frozen = code / relative.with_suffix(".pyc")
            raw = frozen.read_bytes()
            if raw[:4] != importlib.util.MAGIC_NUMBER:
                raise ValueError("Frozen code requires the build interpreter")
            stream = io.BytesIO(raw[16:])
            actual = marshal.load(stream)
            if stream.read():
                raise ValueError("Frozen code has trailing data: " + relative.as_posix())
            expected = compile(content, relative.as_posix(), "exec", optimize=0)
            if actual != expected:
                raise ValueError("Frozen code differs from source: " + relative.as_posix())
            retained_source = package / relative
            required_source = relative.as_posix() in {"science.py", "presentation.py"}
            if (required_source and not retained_source.is_file()
                    or retained_source.is_symlink()):
                raise ValueError("Frozen freshness source is missing or a symlink: "
                                 + relative.as_posix())
            if retained_source.exists() and retained_source.read_bytes() != content:
                raise ValueError("Frozen source differs from bytecode: " + relative.as_posix())
        elif (package / relative).read_bytes() != content:
            raise ValueError("Frozen resource differs from source: " + relative.as_posix())
        inventory[relative.as_posix()] = hashlib.sha256(content).hexdigest()
    if report_sha256 is not None:
        frozen = code / "_packaged_identity.pyc"
        if frozen.is_symlink() or not frozen.resolve().is_relative_to(application.resolve()):
            raise ValueError("Frozen worker identity must remain inside the application")
        raw = frozen.read_bytes()
        stream = io.BytesIO(raw[16:])
        identity = marshal.load(stream)
        expected = compile(
            "REPORT_SHA256 = " + repr(report_sha256) + "\n",
            "_packaged_identity.py",
            "exec",
            optimize=0,
        )
        if raw[:4] != importlib.util.MAGIC_NUMBER or stream.read() or identity != expected:
            raise ValueError("Frozen worker identity differs from its report")
    return inventory


def sysconfig_data():
    """The interpreter's generated build-data module; Windows has none."""
    import sysconfig

    if os.name == "nt":
        return None
    return importlib.import_module(sysconfig._get_sysconfigdata_name())


def home_interpreter_prefixes():
    """Interpreter/environment locations in the builder's home, e.g. uv-managed Python."""
    data = sysconfig_data()
    prefixes = {sys.prefix, sys.base_prefix}
    if data is not None:
        prefixes.add(data.build_time_vars["prefix"])
    home = Path.home().resolve()
    return sorted(p for p in prefixes if p and Path(p).resolve().is_relative_to(home))


def neutralize_sysconfig(application):
    """Remove a home-folder interpreter prefix from the frozen sysconfig data module.

    uv records its managed interpreter's install location there, and PyInstaller
    freezes the module unchanged, so the package would publish the builder's home
    path. The frozen runtime does not use those build-time paths. Restore the
    python-build-standalone placeholder only after the frozen code is shown to
    equal the interpreter's own source, so nothing else is rewritten.
    """
    data = sysconfig_data()
    if data is None:
        return
    frozen = package_root(application) / (data.__name__ + ".pyc")
    if not frozen.exists() and not frozen.is_symlink():
        return
    prefix = data.build_time_vars["prefix"]
    if prefix in home_interpreter_prefixes():
        replace_frozen_prefix(frozen, Path(data.__file__).read_bytes(), prefix)


def replace_frozen_prefix(frozen, source, prefix, neutral="/install"):
    """Rewrite one frozen module's string paths after checking it against its source."""
    import importlib.util

    frozen = Path(frozen)
    if frozen.is_symlink():
        raise ValueError("Frozen sysconfig data must not be a symlink")
    raw = frozen.read_bytes()
    filename = frozen.with_suffix(".py").name
    stream = io.BytesIO(raw[16:])
    if (
        raw[:4] != importlib.util.MAGIC_NUMBER
        or marshal.load(stream) != compile(source, filename, "exec", optimize=0)
        or stream.read()
    ):
        raise ValueError("Frozen sysconfig data differs from the build interpreter")
    patched = source.replace(prefix.encode("utf-8"), neutral.encode("utf-8"))
    # Hash-based pycs (flag bit 0) carry the source hash; timestamp pycs keep theirs.
    flags = raw[4:8]
    check = importlib.util.source_hash(patched) if flags[0] & 1 else raw[8:16]
    code = compile(patched, filename, "exec", optimize=0)
    frozen.write_bytes(raw[:4] + flags + check + marshal.dumps(code))


def refuse_build_paths(application, paths):
    """Refuse a package whose files record any of these build-host paths."""
    application = Path(application)
    markers = set()
    for path in paths:
        value = str(path)
        if value:
            markers.update((os.fsencode(value), value.encode("utf-16-le"),
                            value.encode("utf-16-be")))
    for path in sorted(application.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        content = path.read_bytes()
        if any(marker in content for marker in markers):
            raise ValueError(
                "Package records a build-host path: " + path.relative_to(application).as_posix()
            )


def runtime_notices(root, destination):
    """Retain actual wheel notices and the authenticated Qt/Python license texts."""
    destination.mkdir(parents=True)
    receipts = {}
    for name in ("numpy", "scipy", "pyinstaller"):
        installed = importlib.metadata.distribution(name)
        notices = {}
        for relative in installed.files or ():
            if any(word in relative.name.lower() for word in ("license", "copying", "notice")):
                content = installed.locate_file(relative).read_bytes()
                target = destination / name / str(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                notices[str(relative)] = hashlib.sha256(content).hexdigest()
        if not notices:
            raise ValueError("No installed runtime notices for " + name)
        receipts[name] = dict(version=installed.version, files=notices)
    folder = root / "tools" / "desktop_runtime_licenses"
    qt = json.loads((folder / "QT-ORIGIN.json").read_text())
    for name in ("pyside6", "pyside6-addons", "pyside6-essentials", "shiboken6"):
        version = importlib.metadata.version(name)
        if version != qt["version"]:
            raise ValueError("Update Qt notices for " + name)
        receipts[name] = dict(version=version)
    # Preserve original notices and their source references. Runtime versions are
    # recorded separately; unchanged license terms do not imply identical binaries.
    for origin_name in ("ORIGIN.json", "QT-ORIGIN.json"):
        origin = json.loads((folder / origin_name).read_text())
        entries = origin["notices"] if isinstance(origin, dict) else origin
        for item in entries:
            if item.get("component", "").startswith(("PyObjC", "py2app")):
                continue
            path = folder / item["path"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("Retained runtime notice changed: " + item["path"])
            target = destination / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        shutil.copy2(folder / origin_name, destination / origin_name)
    for _, files in serial_notices(root):
        for name in files:
            shutil.copy2(name, destination / Path(name).name)
    python_license = (
        Path(sys.base_prefix)
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "LICENSE.txt"
    )
    if sys.platform == "win32":
        python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file() and sys.platform == "linux":
        python_license = (
            Path("/usr/share/doc")
            / (f"python{sys.version_info.major}.{sys.version_info.minor}")
            / "copyright"
        )
    if not python_license.is_file():
        raise ValueError("The build interpreter must retain its Python license")
    shutil.copy2(python_license, destination / "PYTHON-BUNDLED.txt")
    receipts["Python"] = dict(
        version=platform.python_version(),
        license_sha256=hashlib.sha256(python_license.read_bytes()).hexdigest(),
    )
    (destination / "RUNTIME.json").write_text(json.dumps(receipts, indent=2) + "\n")
    shutil.copy2(folder / "NOTICE.md", destination / "NOTICE.md")


def worker_architecture(path):
    """Read the compiled target from PE/ELF/Mach-O headers without executing it."""
    import struct

    data = Path(path).read_bytes()
    if (data.startswith(b"\x7fELF") and len(data) >= 20
            and data[4] == 2 and data[5] in (1, 2)):
        order = "little" if data[5] == 1 else "big"
        machine = int.from_bytes(data[18:20], order)
        names = {62: "x86_64", 183: "arm64"}
    elif data.startswith(b"MZ") and len(data) >= 64:
        offset = struct.unpack_from("<I", data, 60)[0]
        if data[offset : offset + 4] != b"PE\0\0" or len(data) < offset + 6:
            raise ValueError("Worker has an invalid PE header")
        machine = struct.unpack_from("<H", data, offset + 4)[0]
        names = {0x8664: "x86_64", 0xAA64: "arm64"}
    elif data[:4] in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf") and len(data) >= 8:
        machine = int.from_bytes(data[4:8], "little" if data[0] == 0xCF else "big")
        names = {0x1000007: "x86_64", 0x100000C: "arm64"}
    else:
        raise ValueError("Worker must be a native 64-bit PE, ELF or Mach-O executable")
    if machine not in names:
        raise ValueError("Worker has an unsupported target architecture")
    return names[machine]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--instrument-build-report", type=Path, required=True)
    parser.add_argument("--candidate", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    from color_measurement.desktop_assets_manifest import check_reference, check_renderer
    from color_measurement.discovery import InstrumentBuild

    assets = root / "src" / "color_measurement" / "desktop_assets"
    check_renderer(assets, root / "tools" / "desktop_builder" / "renderer")
    check_reference(assets)
    original = InstrumentBuild.from_report(args.instrument_build_report)
    if original._client._build_report["build_system"] != platform.system():
        parser.error("Build on the workers' target operating system")
    from color_measurement.native_receipt import check_build_sources, native_worker_name

    check_build_sources(original._client._build_report, source_root=root)

    architecture = {"amd64": "x86_64", "aarch64": "arm64"}.get(
        platform.machine().lower(), platform.machine().lower()
    )
    for name in ("generic-worker-never-run", "argyll-description-worker-never-run"):
        worker = args.instrument_build_report.parent / native_worker_name(
            name, report=original._client._build_report
        )
        if worker_architecture(worker) != architecture:
            parser.error("Workers must match the application target architecture")
    output, build = args.output.resolve(), args.build.resolve()
    name = APPLICATION_NAME + (" Candidate" if args.candidate else "")
    application = output / (name + ".app" if sys.platform == "darwin" else name)
    if application.exists() or build.exists():
        parser.error("Choose unused output and build paths; existing artifacts are preserved")
    output.mkdir(parents=True, exist_ok=True)
    build.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="cm-freeze-", dir=build) as staging:
        staging = Path(staging)
        package = staging / "src" / "color_measurement"
        shutil.copytree(
            root / "src" / "color_measurement",
            package,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        prepared = prepare_instrument_software(
            args.instrument_build_report, staging / "instrument-software"
        )
        (package / "_packaged_identity.py").write_text(
            "REPORT_SHA256 = " + repr(prepared["report_sha256"]) + "\n"
        )
        runtime_notices(root, staging / "licenses" / "runtime")
        shutil.copy2(root / "LICENSE", staging / "licenses" / "LICENSE")
        shutil.copytree(root / "LICENSES", staging / "licenses" / "components")
        # Use noarchive so application bytecode can be compared to its source
        # without running the packaged application or any bundled worker.
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--windowed",
            "--debug",
            "noarchive",
            "--optimize",
            "0",
            "--noupx",
            "--name",
            name,
            "--distpath",
            str(output),
            "--workpath",
            str(build / "work"),
            "--specpath",
            str(staging),
            "--paths",
            str(staging / "src"),
            "--hidden-import",
            "color_measurement._packaged_identity",
            "--collect-data",
            "color_measurement",
            "--collect-submodules",
            "color_measurement",
            "--add-data",
            str(package / "desktop_assets") + os.pathsep + "color_measurement/desktop_assets",
            "--add-data",
            str(staging / "licenses") + os.pathsep + "licenses",
            "--exclude-module",
            "tkinter",
            "--exclude-module",
            "pytest",
            "--exclude-module",
            "ruff",
        ]
        # Freshness manifests authenticate the actual scientific source files.
        # Retain those inputs as well as their independently checked bytecode.
        for module in ("science.py", "presentation.py"):
            command += ["--add-data", str(package / module) + os.pathsep + "color_measurement"]
        if sys.platform == "darwin":
            command += [
                "--icon",
                str(root / "tools/desktop_builder/app-icon.icns"),
                "--osx-bundle-identifier",
                BUNDLE_IDENTIFIER + (".candidate" if args.candidate else ""),
                "--codesign-identity",
                "-",
            ]
        elif sys.platform == "win32":
            command += ["--icon", str(root / "tools/desktop_builder/app-icon.ico")]
        command += [str(root / "tools" / "desktop_entry.py")]
        subprocess.run(command, cwd=staging, check=True)
        if sys.platform == "linux" and not args.candidate:
            launcher = application / "Add to Applications.sh"
            shutil.copy2(root / "tools/desktop_builder/install-linux-desktop.sh", launcher)
            launcher.chmod(0o755)
        neutralize_sysconfig(application)
        inventory = verify_package_code(
            application, root / "src" / "color_measurement", report_sha256=prepared["report_sha256"]
        )
        resources = package_root(application)
        (resources / "PACKAGE.json").write_text(
            json.dumps(
                dict(
                    format=1,
                    version=importlib.metadata.version("color-measurement"),
                    application_name=name,
                    system=platform.system(),
                    architecture=platform.machine(),
                    python=platform.python_version(),
                    package_sha256=inventory,
                    instrument_report_sha256=prepared["report_sha256"],
                ),
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        if sys.platform == "darwin":
            path = application / "Contents" / "Info.plist"
            info = plistlib.loads(path.read_bytes())
            info.update(
                CFBundleVersion=importlib.metadata.version("color-measurement"),
                CFBundleShortVersionString=importlib.metadata.version("color-measurement"),
                LSMinimumSystemVersion="27.0",
                NSHumanReadableCopyright="Copyright © 2026 Fernando Voltolini de Azambuja",
                CMInstrumentSoftwareReportSHA256=prepared["report_sha256"],
            )
            path.write_bytes(plistlib.dumps(info))
        install_instrument_software(application, prepared)
        # Exact home-folder interpreter/environment paths only: a short checkout
        # path such as the Linux container's /project also occurs in wheels.
        refuse_build_paths(application, home_interpreter_prefixes())
        if sys.platform == "darwin":
            # BUNDLE has copied the owned COLLECT directory. Keep one application.
            shutil.rmtree(output / name)
        shutil.rmtree(build / "work")
        print("Built and source-checked:", application)
        print("Production workers authenticated but never executed.")


if __name__ == "__main__":
    main()
