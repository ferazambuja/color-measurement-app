"""Build and exercise a strictly offline Argyll 3.5.0 feasibility boundary.

Upstream source is supplied separately, authenticated before extraction, and never
added to the package. The full-library link probe is deliberately NEVER executed.
Only synthetic helpers, the session core with substituted instrument methods,
and extracted functions with inert OS stubs are run.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from native_argyll.driver_manifest import include_standalone_utf16

ARCHIVE_SHA256 = "f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f"
HERE = Path(__file__).resolve().parent
NATIVE = HERE / "native_argyll"
SOURCES = (
    "spectro/conv.c",
    "spectro/sa_conv.c",
    "spectro/aglob.c",
    "spectro/inst.c",
    "spectro/icoms.c",
    "spectro/usbio.c",
    "spectro/hidio.c",
    "spectro/insttypes.c",
    "spectro/disptechs.c",
    "spectro/xrga.c",
    "spectro/pollem.c",
    "spectro/xdg_bds.c",
    "spectro/i1pro3.c",
    "spectro/i1pro3_imp.c",
    "numlib/numsup.c",
    "rspl/rspl1.c",
    "xicc/xspect.c",
    "xicc/ccss.c",
    "xicc/ccmx.c",
    "cgats/pars.c",
    "cgats/cgats.c",
)
LEGACY_SOURCES = tuple(
    name.replace("spectro/i1pro3", "spectro/i1pro") for name in SOURCES
)
LEGACY_NONADAPTIVE_PATCH_SHA256 = "26a845395ec68ef3e171f1c0c1b47fd7e48378dc4a6a060eb60cc2d3cf9f0159"
# legacy_scan_recognition.measurement_patch's whole composition, through the
# flash endpoint and saturation corrections; flash_capture.I1PRO_MEASUREMENT_SHA256
# is the same source before those two.
LEGACY_MEASUREMENT_PATCH_SHA256 = "629b1cc6863671eddbd63308236807fb18c8c192f5c98db7d22cc6e910508d93"




def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_build_platform(system: str) -> None:
    """Refuse unimplemented native recipes before extracting or compiling."""
    if system not in {"Darwin", "Linux", "Windows"}:
        raise ValueError("Native builds require macOS, Linux or Windows MinGW-w64")


def executable_path(output: Path, name: str) -> Path:
    from color_measurement.native_receipt import native_worker_name

    return output / native_worker_name(name)


def compiler_name() -> str:
    """Use the requested compiler, or the platform's supported default."""
    return os.environ.get("CC") or ("clang" if platform.system() == "Windows" else "cc")


def process_entry_flags(system: str) -> list[str]:
    """Use wide Windows CRT startup, retaining Argyll's narrow Win32 API aliases."""
    return ["-municode", "-UUNICODE"] if system == "Windows" else []


def windows_compiler_matches(target: str, machine: str) -> bool:
    """Require the GNU Windows compiler to match the native interpreter host."""
    architecture = {"amd64": "x86_64", "x86_64": "x86_64",
                    "arm64": "aarch64", "aarch64": "aarch64"}.get(machine.lower())
    return architecture is not None and target in {
        f"{architecture}-w64-mingw32", f"{architecture}-w64-windows-gnu",
    }


def windows_dll_imports(inspected: str, *, compiler_target: str) -> list[str]:
    """Validate GNU/LLVM PE inspection and retain the imported runtime DLLs."""
    architecture = compiler_target.split("-", 1)[0]
    formats = {"x86_64": r"(?:pei|coff)-x86-64",
               "aarch64": r"(?:coff-arm64|pei-aarch64(?:-little)?)"}
    if (architecture not in formats
            or not re.search(r"file format " + formats[architecture] + r"\b", inspected)
            or not re.search(r"^Magic\s+020b\s", inspected, re.M)
            or not re.search(r"^\s+executable\s*$", inspected, re.M)):
        raise ValueError("Windows worker must be a matching native PE32+ executable")
    imports = sorted(set(re.findall(r"DLL Name:\s*([^\s]+)", inspected)))
    if not imports:
        raise ValueError("Windows worker import table is empty")
    return imports


def system_link_flags(system: str) -> list[str]:
    """That platform's system dependencies, after any Argyll objects."""
    check_build_platform(system)
    if system == "Darwin":
        frameworks = ("Carbon", "Cocoa", "IOKit", "CoreFoundation", "AudioToolbox", "AppKit")
        return [*[item for name in frameworks for item in ("-framework", name)], "-lm"]
    if system == "Windows":
        # Pinned upstream Jambase's MinGW system-library list. No USB DLL or
        # third-party driver is installed or loaded by this build/link check.
        libraries = ("m", "oleaut32", "uuid", "shlwapi", "setupapi", "ole32", "ws2_32",
                     "psapi", "iphlpapi", "version", "gdi32", "mscms")
        return [f"-l{name}" for name in libraries]
    return ["-pthread", "-lm"]


def support_adapters(report: dict) -> tuple[str, ...]:
    """Driver support adapters linked beside session.c (native_facts.h).

    One per contributed driver that retains facts the vtable does not return, and
    only in a build that composed that driver's series.
    """
    added = report.get("contribution", {}).get("instrument_types_added", ())
    modules = []
    if {"instCR250", "instCR100", "instCR300"} & set(added):
        modules.extend(("cr250_support.c", "cr250_control.c"))
    if {"instPR655", "instPR670"} & set(added):
        modules.extend(("pr655_support.c", "pr655_control.c"))
    return tuple(modules)


def control_adapters(report: dict) -> tuple[str, ...]:
    """Pure driver controls needed by initialized description and constructors."""
    return tuple(name for name in support_adapters(report) if name.endswith("_control.c"))


def cr_read_allowance_floors(floors: Path, enum: str, output: Path,
                             report: dict) -> dict[str, float]:
    """The CR driver's own whole-read floor for ``enum``, per mode word it accepts.

    ``cr_floor.c`` asks ``cr250_required_floor_s`` for every mode word the worker
    can bind -- each route with the colorimeter bit, with and without the spectral
    and non-adaptive bits. Words the row refuses (-1) are left out. The least is
    the type's floor for bind, which comes before the mode is chosen; the frontend
    checks a started read against its exact word. A type that accepts no word has
    no floor to publish, which is a defect, not an empty answer.
    """
    from color_measurement.argyll_capability import MODE_FLAGS, MODE_ROUTES

    words = sorted({route | MODE_FLAGS["colorimeter"] | spectral | nonadaptive
                    for route in MODE_ROUTES.values()
                    for spectral in (0, MODE_FLAGS["spectral"])
                    for nonadaptive in (0, MODE_FLAGS["emis_nonadaptive"])})
    printed = command([str(floors), *(item for word in words for item in (enum, str(word)))],
                      output, report).split()
    if len(printed) != 3 * len(words):
        raise ValueError(f"The CR floor program answered {len(printed)} fields")
    found = {printed[i + 1]: float(printed[i + 2]) for i in range(0, len(printed), 3)
             if printed[i] == enum and float(printed[i + 2]) > 0}
    if not found:
        raise ValueError(f"The CR driver accepts no mode for {enum}")
    return found


def cr250_host_results(printed: str, executable: Path, *, suite: str = "cr250_host_test") -> dict:
    """cr250_host_test's own count line; any failure it printed refuses the build."""
    found = re.findall(r"^" + re.escape(suite) + r": (\d+) checks, (\d+) failures$", printed, re.M)
    if len(found) != 1 or found[0][1] != "0" or "FAIL " in printed:
        raise ValueError(f"{suite} did not report a clean run")
    return {"checks": int(found[0][0]), "failures": 0,
            "executable_sha256": digest(executable.read_bytes())}


def contributed_sanitizer_support(output: Path, report: dict, cc: str, system: str) -> bool:
    """Check the target compiler and loader before claiming sanitized actors.

    MinGW distributions may omit libsanitizer. Their functional driver checks
    still run, with the unavailable instrumentation stated in the receipt.
    Other platforms retain the established sanitizer requirement.
    """
    from native_argyll import sanitizers

    unit = output / "contributed-sanitizer-probe.c"
    unit.write_text("int main(void) { return 0; }\n")
    executable = executable_path(output, "contributed-sanitizer-probe")
    section = report["contributed_sanitizers"] = {"enabled": False, "status": "unavailable"}
    try:
        command([cc, *sanitizers.flags(True), str(unit), "-o", str(executable)], output, report)
        command([str(executable)], output, report)
    except RuntimeError:
        section["reason"] = "Target compiler or loader cannot supply ASan/UBSan; see probe log"
        if system != "Windows":
            raise
        return False
    section.update(enabled=True, status="passed")
    return True


def build_cr250_driver_unit(source: Path, output: Path, report: dict, cc: str,
                            flags: list[str], *, system: str) -> Path:
    """Link the contribution's existing driver-unit runner with fake transport.

    The runner owns its synthetic factories. Foreign constructors abort, as in
    its standalone runner; this binary never links an OS transport implementation.
    The contribution runner asserts its core setter contract. Compile its
    authenticated pre-overlay driver, engine, selector owner, headers and actor together;
    application controls have their own transformed-source proof below.
    The shared inst dispatch and selector owner retain the integrated
    contribution's contract; instrumentation follows the target compiler probe.
    """
    from native_argyll import sanitizers

    constructors = set(re.findall(r"p = \(inst \*\)(new_\w+)\(",
                                  (source / "spectro/inst.c").read_text()))
    constructors.remove("new_cr250")
    stubs = output / "cr-driver-unit-unreachable.c"
    stubs.write_text("#include <stdlib.h>\n" + "".join(
        f"void {name}(void) {{ abort(); }}\n"
        for name in sorted(constructors | {"setup_spyd2"})), encoding="utf-8")
    executable = executable_path(output, "cr-driver-unit")
    core = output / "cr-driver-core"
    for name, expected in report["cr250_driver_core"]["source_sha256"].items():
        if digest((core / name).read_bytes()) != expected:
            raise ValueError(f"CR core runner input changed: {name}")
    sources = [source / name for name in (
        "spectro/insttypes.c",
        "spectro/disptechs.c", "numlib/numsup.c", "xicc/xspect.c",
        "spectro/sa_conv.c", "cgats/cgats.c", "cgats/pars.c")]
    sources += [core / name for name in (
        "cr250_driver_test.c", "cr250.c", "cr250_imp.c", "cr250_fake.c",
        "inst.c", "instappsup.c")]
    command([cc, *flags, *sanitizers.flags(report["contributed_sanitizers"]["enabled"]),
             "-g", *map(str, sources),
             str(stubs), *referenced_members_link_flags(
                 output / "libcm_argyll_offline.a", system), "-o", str(executable)],
            output, report)
    symbols = command(["nm", str(executable)], output, report)
    # new_icoms/new_icompaths here are the runner's scripted factories, not OS
    # implementations. The same absent transport symbols guard its own runner.
    forbidden = ("usb_get_paths", "hid_get_paths", "serial_get_paths",
                 "icoms_close_port", "icoms_set_ser_port", "icoms_set_ser_port_ex",
                 "usb_open_port", "hid_open_port")
    for symbol in forbidden:
        if re.search(r"\b_?" + re.escape(symbol) + r"$", symbols, re.M):
            raise ValueError(f"CR driver-unit runner links OS transport: {symbol}")
    return executable


def cr250_driver_unit_results(printed: str, executable: Path, *, sanitize: bool) -> dict:
    """Require the authoritative driver runner's nonempty all-green count."""
    from native_argyll import sanitizers

    sanitizers.refuse(printed, "CR driver-unit")
    found = re.findall(r"^cr250_driver_test: (\d+) checks, (\d+) failures$", printed, re.M)
    if len(found) != 1 or int(found[0][0]) <= 0 or found[0][1] != "0":
        raise ValueError("cr250_driver_test did not report a clean run")
    return {"status": "passed", "checks": int(found[0][0]), "failures": 0,
            "executable_sha256": digest(executable.read_bytes()),
            "sanitize": sanitize, "hardware_tested": False,
            "scope": "existing contribution core runner before application overlays; "
                     "integrated contribution inst dispatch and selector owner, fake transport"}


def build_contributed_test_support(source: Path, output: Path, report: dict, cc: str,
                                   flags: list[str]) -> tuple[list[str], set[str]]:
    """Compile the actual shared selector owner once for scripted model personas.

    Its constructor/transport entry points have private test names. Transport
    factories and every foreign constructor abort. Real drivers see only the
    existing actors' icoms methods; no OS transport implementation is linked.
    """
    from native_argyll import sanitizers

    added = set(report.get("contribution", {}).get("instrument_types_added", ()))
    allowed = set()
    sources = ["spectro/inst.c", "spectro/insttypes.c", "spectro/disptechs.c",
               "numlib/numsup.c", "xicc/xspect.c", "spectro/sa_conv.c",
               "cgats/cgats.c", "cgats/pars.c"]
    if added & {"instCR250", "instCR100", "instCR300"}:
        sources += ["spectro/cr250.c", "spectro/cr250_imp.c", "spectro/cr250_fake.c"]
        allowed.add("new_cr250")
    if added & {"instPR655", "instPR670"}:
        sources += ["spectro/pr655.c", "spectro/pr655_imp.c"]
        allowed.add("new_pr655")
    constructors = set(re.findall(r"p = \(inst \*\)(new_\w+)\(",
                                  (source / "spectro/inst.c").read_text()))
    renamed = {"new_inst": "cm_source_new_inst", "new_icoms": "cm_unreachable_icoms",
               "new_icompaths": "cm_unreachable_icompaths",
               "ser_inst_type": "cm_source_ser_inst_type"}
    renamed.update({name: "cm_unreachable_" + name for name in constructors - allowed})
    stubs = output / "contributed-unreachable.c"
    stubs.write_text("#include <stdlib.h>\n" + "".join(
        f"void {name}(void) {{ abort(); }}\n" for name in sorted(
            {renamed[name] for name in constructors - allowed}
            | {"setup_spyd2", "cm_unreachable_icoms", "cm_unreachable_icompaths"}))
        + 'char *baud_rate_to_str(int br) { (void)br; return "synthetic"; }\n')
    objects = []
    for name in sources:
        obj = output / ("persona_" + name.replace("/", "_") + ".o")
        definitions = [f"-D{k}={v}" for k, v in renamed.items()] if name == "spectro/inst.c" else []
        command([cc, *flags, *sanitizers.flags(report["contributed_sanitizers"]["enabled"]),
                 *definitions, "-c", str(source / name),
                 "-o", str(obj)], output, report)
        objects.append(str(obj))
    obj = output / "contributed-unreachable.o"
    command([cc, *flags, *sanitizers.flags(report["contributed_sanitizers"]["enabled"]),
             "-c", str(stubs), "-o", str(obj)], output, report)
    objects.append(str(obj))
    report["contributed_persona_support"] = {
        "scope": "actual shared inst selector owner; renamed transport factories abort; "
                 "scripted icoms only",
        "source_sha256": {name: digest((source / name).read_bytes()) for name in sources},
        "entry_point_renames": renamed, "aborting_stubs_sha256": digest(stubs.read_bytes()),
        "allowed_constructors": sorted(allowed),
        "sanitize": report["contributed_sanitizers"]["enabled"], "hardware_tested": False}
    return objects, allowed


def build_pr655_host_check(source: Path, output: Path, report: dict, cc: str,
                          flags: list[str], *, system: str) -> Path:
    """Run application PR proofs using the contribution's existing actor/factories."""
    from native_argyll import sanitizers

    # This actor exercises PR controls only; compiling the generic typed decoder
    # with CR adapters would make its PR test reach an unrelated real CR object.
    flags = [*flags, *(f"-UCM_CONTRIBUTED_{enum}"
                      for enum in ("instCR250", "instCR100", "instCR300"))]
    constructors = (set(re.findall(r"p = \(inst \*\)(new_\w+)\(",
                                   (source / "spectro/inst.c").read_text()))
                    - {"new_pr655", "new_cr250"})
    stubs = output / "pr-host-unreachable.c"
    stubs.write_text("#include <stdlib.h>\n" + "".join(
        f"void {name}(void) {{ abort(); }}\n" for name in sorted(constructors | {"setup_spyd2"})))
    executable = executable_path(output, "pr-host-test")
    sources = [source / name for name in (
        "spectro/inst.c", "spectro/insttypes.c", "spectro/disptechs.c",
        "spectro/pr655.c", "spectro/pr655_imp.c",
        "numlib/numsup.c", "xicc/xspect.c", "spectro/sa_conv.c", "cgats/cgats.c", "cgats/pars.c")]
    # inst.c owns CR serial identification too. Resolve it with the already
    # authenticated core driver/engine pair, avoiding an archive pull of the
    # application CR driver and its unrelated control adapters.
    sources += [output / "cr-driver-core" / name for name in ("cr250.c", "cr250_imp.c")]
    command([cc, *flags, *sanitizers.flags(report["contributed_sanitizers"]["enabled"]),
             str(NATIVE / "pr655_host_test.c"),
             str(NATIVE / "pr655_control.c"), str(NATIVE / "pr655_support.c"),
             *map(str, sources), str(stubs), *referenced_members_link_flags(
                 output / "libcm_argyll_offline.a", system), "-o", str(executable)], output, report)
    symbols = command(["nm", str(executable)], output, report)
    # The included production-test actor defines new_icoms/new_icompaths. Guard
    # their provenance with its compiled path/hash and reject OS implementations.
    for symbol in ("usb_get_paths", "hid_get_paths", "serial_get_paths", "icoms_close_port",
                   "icoms_set_ser_port", "icoms_set_ser_port_ex", "usb_open_port", "hid_open_port"):
        if re.search(r"\b_?" + re.escape(symbol) + r"$", symbols, re.M):
            raise ValueError(f"PR host runner links OS transport: {symbol}")
    report["pr655_host_actor_sha256"] = digest(
        (source / "spectro/pr655_driver_test.c").read_bytes())
    return executable


def referenced_members_link_flags(library: Path, system: str) -> list[str]:
    """Link only the library members a substituted worker actually references.

    The description stub needs Argyll's own helpers -- serial_policy.h's typed
    path calls dev_category (insttypes.c) -- but never its transport or device
    constructors. Force-loading the archive would put those in the binary;
    resolving by reference does not, and forbid_linked_transport proves it.
    """
    return [str(library), *system_link_flags(system)]


def forbid_linked_transport(executable: Path, constructors, output: Path, report: dict) -> None:
    """A substituted worker must hold no OS transport and no device constructor.

    Checked on the symbols the binary defines, because that is the property: a
    worker that cannot reach new_icoms or a new_<driver> cannot open a port or
    construct a real instrument, whatever its arguments say.
    """
    defined = set(re.findall(r"^\S*\s+[TtDdBbSs]\s+_?(\w+)$",
                             command(["nm", str(executable)], output, report), re.M))
    forbidden = {"new_icoms", "new_icompaths", "new_icompaths_sel", "new_inst",
                 "ser_inst_type", "fast_ser_inst_type", *constructors}
    if defined & forbidden:
        raise ValueError(f"Substituted worker links transport or constructors: "
                         f"{sorted(defined & forbidden)}")


def library_link_flags(library: Path, system: str) -> list[str]:
    """Link every upstream object, followed by that platform's system dependencies."""
    if system == "Darwin":
        return [f"-Wl,-force_load,{library}", *system_link_flags(system)]
    # Leave whole-archive mode before resolving system libraries.
    return ["-Wl,--whole-archive", str(library), "-Wl,--no-whole-archive",
            *system_link_flags(system)]
















def extract_function(source: str, signature: str, next_marker: str) -> str:
    """Extract pinned whole functions; fail closed if boundaries are ambiguous."""
    if source.count(signature) != 1:
        raise ValueError(f"Missing or ambiguous function: {signature}")
    start = source.index(signature)
    end = source.index(next_marker, start)
    body = source[start:end].rstrip()
    if not body.endswith("}"):
        raise ValueError(f"Function boundary changed: {signature}")
    return body + "\n"


def standalone_utf16(source: Path, report: dict, *, private_linkage: bool = False) -> str:
    """Supply the upstream serial-descriptor converter omitted by SALONEINSTLIB.

    Retain exact upstream definitions, algorithm and both copyright notices in
    a generated header. Nothing from the ICC numerical implementation is linked.
    """
    parts, extracts = [], []

    def extract(name: str, start: str, end: str, *, inclusive: bool = False) -> str:
        path = source / name
        text = path.read_text(encoding="utf-8")
        if digest(path.read_bytes()) != report["upstream_source_sha256"][name]:
            raise ValueError(
                f"UTF compatibility input changed after archive authentication: {name}"
            )
        if text.count(start) != 1:
            raise ValueError(f"Missing or ambiguous UTF compatibility section: {name}")
        first = text.index(start)
        last = text.index(end, first) + (len(end) if inclusive else 0)
        body = text[first:last]
        line = text[:first].count("\n") + 1
        extracts.append({"path": name, "line": line, "sha256": digest(body.encode())})
        return f'#line {line} "{name}"\n{body}\n'

    parts.append(extract("icc/icc_util.c", "/* \n * International Color", "*/", inclusive=True))
    parts.append(extract("icc/icc.h", "typedef ORD32\ticmUTF32;", "typedef ORD8\ticmUTF8;",
                         inclusive=True))
    parts.append(extract("icc/icc_util.h", "typedef enum {\n    icmUTF_ok", "} icmUTFerr;",
                         inclusive=True))
    unicode = extract("icc/icc_util.c", "/*\n * Copyright 2001-2004 Unicode, Inc.",
                      "/*\n * Index into the table below")
    parts.append(unicode)
    parts.append(extract("icc/icc_util.c", "static const icmUTF8 firstByteMark[7]", ";",
                         inclusive=True))
    converter = extract(
        "icc/icc_util.c", "size_t icmUTF16toUTF8(",
        "\n/* --------------------------------------------------------------------- */",
    )
    if private_linkage:
        converter = converter.replace("\nsize_t icmUTF16toUTF8(",
                                      "\nstatic size_t icmUTF16toUTF8(", 1)
    parts.append(converter)
    name = "icc/cm_utf16_compat.h"
    header = source / name
    header.write_text(
        "/* Generated from authenticated Argyll source; no conversion changes. */\n"
        "#ifndef CM_UTF16_COMPAT_H\n#define CM_UTF16_COMPAT_H\n"
        + "\n".join(parts) + "\n#endif\n", encoding="utf-8", newline="\n",
    )
    report["standalone_utf16_compatibility"] = {
        "generated_path": name, "generated_sha256": digest(header.read_bytes()),
        "upstream_excerpts": extracts,
        "licenses": ["MIT", "Unicode copyright and redistribution notice retained verbatim"],
        "local_linkage_adjustment": "static per translation unit" if private_linkage else None,
    }
    notice = re.search(r"/\*\n \* Copyright 2001-2004 Unicode, Inc\..*?\*/", unicode, re.DOTALL)
    return notice[0] + "\n"


def write_mode_check_candidates(output: Path) -> dict:
    from color_measurement.argyll_capability import baseline_mode_candidates

    # One vocabulary owns requests and baseline queries. The generated table has
    # no instrument identities and is part of the recorded native build inputs.
    candidates = baseline_mode_candidates()
    header = output / "mode_check_candidates.h"
    header.write_text(
        "/* Generated from argyll_capability.baseline_mode_candidates. */\n"
        "#ifndef CM_MODE_CHECK_CANDIDATES_H\n#define CM_MODE_CHECK_CANDIDATES_H\n"
        f"#define CM_MODE_CHECK_CAPACITY {2 * len(candidates)}\n"
        "static const inst_mode cm_mode_check_candidates[] = {\n"
        + "".join(f"    (inst_mode)0x{word:08x}u,\n" for word in candidates)
        + "};\n#endif\n", encoding="utf-8", newline="\n",
    )
    return {
        "generated_path": header.name, "generated_sha256": digest(header.read_bytes()),
        "candidate_words": list(candidates),
    }


def prepare(archive: Path, output: Path, *, contribution: Path | None = None) -> tuple[Path, dict]:
    """Prepare the current broad production import and application policies."""
    contribution_root = NATIVE / "contributions"
    contribution = (contribution or contribution_root / "repairs").absolute()
    try:
        relative = contribution.relative_to(contribution_root)
    except ValueError as error:
        raise ValueError(
            "Contribution must live under tools/native_argyll/contributions") from error
    if len(relative.parts) != 1 or relative.name in (".", ".."):
        raise ValueError("Contribution must be one named directory under contributions")
    archive_bytes = archive.read_bytes()
    if digest(archive_bytes) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match the pinned official 3.5.0 source")
    output.mkdir(parents=True, exist_ok=False)
    unpacked = output / "source"
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zipped:
        for entry in zipped.infolist():
            name = Path(entry.filename)
            if name.is_absolute() or ".." in name.parts or entry.is_dir():
                raise ValueError("Unexpected archive member")
        zipped.extractall(unpacked)
    source = unpacked / "Argyll_V3.5.0"
    if not (source / "spectro/i1pro3.c").is_file():
        raise ValueError("Unexpected pinned archive layout")
    report = {
        "schema_version": 1,
        "argyll_version": "3.5.0",
        "archive_sha256": ARCHIVE_SHA256,
        "platform": platform.platform(),
        "scope": "offline_feasibility_only",
        "status": "incomplete",
        "hardware_tested": False,
        "library_probe_executed": False,
        "commands": [],
        "patches": [],
        "upstream_source_sha256": {
            p.relative_to(source).as_posix(): digest(p.read_bytes())
            for p in sorted(source.rglob("*"))
            if p.is_file()
        },
        "original_check_source_sha256": {
            p.relative_to(HERE.parent).as_posix(): digest(p.read_bytes())
            for p in sorted(NATIVE.glob("*"))
            if p.is_file()
        },
    }
    for path in [Path(__file__).resolve(),
                 *sorted((HERE.parent / "src/color_measurement").glob("*.py"))]:
        report["original_check_source_sha256"][path.relative_to(HERE.parent).as_posix()] = digest(
            path.read_bytes()
        )
    if contribution is not None:
        # An authenticated series enters here, after the stock map is recorded and
        # before this project's own transforms, so those transforms' exact-anchor
        # checks below run against the composed text and refuse a series that
        # moved what they pin.
        from native_argyll.contribution import apply_contribution
        from native_argyll.contribution_notices import audit_additions

        report["contribution"] = apply_contribution(
            contribution, source, report["upstream_source_sha256"], ARCHIVE_SHA256,
        )
        # These nested files are build inputs too. Use the hashes that were
        # verified while composing, not a later re-read of possibly changed files.
        receipt = report["contribution"]
        report["contribution_license_inventory"] = audit_additions(source, receipt["entries"])
        prefix = contribution.relative_to(HERE.parent).as_posix()
        inputs = report["original_check_source_sha256"]
        inputs[f"{prefix}/manifest.json"] = receipt["manifest_sha256"]
        for row in receipt["entries"]:
            inputs[f"{prefix}/{row['series_file']}"] = row["source_sha256"]
        if "instCR250" in receipt["instrument_types_added"]:
            # Keep the existing core assertions paired with their core inputs.
            # These few temporary files are retired with ordinary build output.
            core = output / "cr-driver-core"
            core.mkdir()
            hashes = {}
            entries = {row["path"]: row for row in receipt["entries"]}
            names = {"spectro/inst.c", "spectro/instappsup.c", "spectro/inst.h"}
            names.update(name for name in entries if Path(name).parent == Path("spectro")
                         and Path(name).name.startswith("cr250")
                         and Path(name).suffix in {".c", ".h"})
            for name in sorted(names):
                content = (source / name).read_bytes()
                expected = (entries[name]["post_sha256"] if name in entries
                            else report["upstream_source_sha256"][name])
                if digest(content) != expected:
                    raise ValueError(f"CR core input changed: {name}")
                (core / Path(name).name).write_bytes(content)
                hashes[Path(name).name] = digest(content)
            report["cr250_driver_core"] = {"source_sha256": hashes,
                                          "source_origin": receipt["source_origin"]}
    from native_argyll.display_catalogue import prepare_display_digest

    prepare_display_digest(source, report)
    return prepare_application_policies(source, output, report)


def finish_prepare(source: Path, output: Path, report: dict, unicode_notice: str):
    report["baseline_mode_query_table"] = write_mode_check_candidates(output)
    shutil.copyfile(source / "h/aconfig.h", output / "sa_config.h")
    shutil.copyfile(source / "License2.txt", output / "Argyll-License2.txt")
    (output / "UPSTREAM.txt").write_text(
        "ArgyllCMS 3.5.0 by Graeme W. Gill and other credited contributors.\n"
        "https://www.argyllcms.com/Argyll_V3.5.0_src.zip\n"
        "Selected instrument code retains GPL-2.0-or-later and MIT declarations;\n"
        "files retain their own notices. See the dependency inventory, source headers and\n"
        "source/Argyll_V3.5.0/doc/ArgyllDoc.html. This is a modified offline build,\n"
        "not an upstream binary. Generated patches are recorded in report.json.\n"
        + unicode_notice, encoding="utf-8", newline="\n",
    )
    return source, report


def prepare_application_policies(source: Path, output: Path, report: dict):
    """The production build applies no historical general-repair transform."""
    from native_argyll import ccss_grid_policy, cr250_host, no_termination, pr655_host
    from native_argyll.driver_manifest import passive_serial_patches

    policies = passive_serial_patches()
    patches = [*no_termination.PATCHES.items(), *policies.items(),
               (ccss_grid_policy.PATH, ccss_grid_policy.patch)]
    added = set(report["contribution"]["instrument_types_added"])
    if added & {"instCR250", "instCR100"}:
        patches.extend(cr250_host.PATCHES)
    if added & {"instPR655", "instPR670"}:
        patches.extend(pr655_host.PATCHES)
    unicode_notice = ""
    if platform.system() in {"Linux", "Windows"}:
        unicode_notice = standalone_utf16(
            source, report, private_linkage=platform.system() == "Windows")
        patches.append(("spectro/usbio.c", include_standalone_utf16))
        if platform.system() == "Windows":
            patches.append(("spectro/hidio.c", lambda text: include_standalone_utf16(
                text, '#include "icoms.h"')))
    for name, apply in patches:
        path = source / name
        before = path.read_text(encoding="utf-8")
        after = apply(before)
        path.write_text(after, encoding="utf-8", newline="\n")
        diff_name = name.replace("/", "_") + ".patch"
        # Some policies share a file. Retain one cumulative post-import diff.
        previous = next((row for row in report["patches"] if row["path"] == name), None)
        if previous is None:
            previous = {"path": name, "before_sha256": digest(before.encode()),
                        "diff": diff_name, "role": "application-policy"}
            report["patches"].append(previous)
            original = before
        else:
            original = report["_policy_inputs"][name]
        report.setdefault("_policy_inputs", {})[name] = original
        previous["after_sha256"] = digest(after.encode())
        (output / diff_name).write_text("".join(difflib.unified_diff(
            original.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"imported-argyll/{name}", tofile=f"application-policy/{name}")),
            encoding="utf-8", newline="\n")
    report.pop("_policy_inputs", None)
    report.update(build_system=platform.system(), build_scope="all_public",
                  driver_scope="all_public", portable_repairs_imported=True)
    return finish_prepare(source, output, report, unicode_notice)


def prepare_proof_inputs(archive: Path, source: Path, output: Path, report: dict):
    """Authenticate frozen predecessors; production is never reconstructed from them."""
    from native_argyll.proof_inputs import MANIFEST_SHA256, before_source, fixture_manifest

    manifest = fixture_manifest()
    report["proof_fixture_manifest_sha256"] = MANIFEST_SHA256
    for key, proof in (("scan_finite_repair", "scan_finite"),
                       ("conversion_allocation_repair", "conversion_allocation"),
                       ("filter_values_repair", "filter_values"),
                       ("spectral_display_timing_repair", "display_timing_spectral"),
                       ("native_resync_repair", "native_resync")):
        folder = output / "proof-predecessors" / proof
        before, after = {}, {}
        for name in manifest["proofs"][proof]:
            text = before_source(proof, name, archive)
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            before[name] = digest(text.encode())
            after[name] = digest((source / name).read_bytes())
        report[key] = dict(role="frozen proof predecessor; not a production transform",
                           inputs=folder.relative_to(output).as_posix(),
                           before_sha256=before, after_sha256=after)
    for path in sorted((NATIVE / "proof_inputs").iterdir()):
        report["original_check_source_sha256"][path.relative_to(HERE.parent).as_posix()] = digest(
            path.read_bytes())


def prepare_with_proof_inputs(archive: Path, output: Path, **options):
    """Actual production source plus authenticated frozen proof predecessors."""
    source, report = prepare(archive, output, **options)
    prepare_proof_inputs(archive, source, output, report)
    return source, report


def prove_spectral_display_timing(source: Path, output: Path, report: dict, cc: str) -> dict:
    from native_argyll import display_timing_spectral

    stage = report["spectral_display_timing_repair"]
    before = {p: (output / stage["inputs"] / p).read_text(encoding="utf-8")
              for p in stage["before_sha256"]}
    return display_timing_spectral.prove(before, output / "spectral-display-timing-proof",
                                         compiler=cc, source=source)


def command(args: list[str], output: Path, report: dict) -> str:
    index = len(report["commands"])
    entry = {"argv": args, "log": f"command-{index:02}.log"}
    report["commands"].append(entry)
    result = subprocess.run(args, cwd=output, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=180)
    (output / entry["log"]).write_text(result.stdout + result.stderr, encoding="utf-8")
    entry["returncode"] = result.returncode
    if result.returncode:
        raise RuntimeError(f"Command failed: {args[0]}; see {output / entry['log']}")
    return result.stdout


def compile_with_dependencies(args: list[str], output: Path, report: dict) -> str:
    """Keep Windows compiler paths unambiguous in generated Make dependencies."""
    if platform.system() == "Windows":
        # GCC accepts forward slashes; its Make dependencies then retain drive
        # letters without interpreting path separators as escaping characters.
        args = [arg.replace("\\", "/") for arg in args]
    return command(args, output, report)


def dependency_names(text: str) -> list[str]:
    """Decode GCC Make prerequisites, preserving native Windows separators."""
    text = text.replace("\\\n", " ")
    if not text.startswith("object:"):
        raise ValueError("Unexpected compiler dependency target")
    # Make escaping is narrower than shell escaping: a Windows backslash before
    # an ordinary letter is a path separator, not an escape of that letter.
    tokens = re.findall(r"(?:\\[\s#:$\\]|[^\s])+", text[len("object:"):])
    return [re.sub(r"\\([\s#:$\\])", r"\1", token).replace("$$", "$") for token in tokens]


def upstream_dependencies(
    dependencies: list[Path], source: Path, output: Path, sources: tuple | None = None,
) -> list[str]:
    """Read compiler dependencies, including included C files and the copied config."""
    names = set()
    for dependency in dependencies:
        for name in dependency_names(dependency.read_text(encoding="utf-8")):
            path = Path(name)
            path = (output / path).resolve()
            if path == output / "sa_config.h":
                if path.read_bytes() != (source / "h/aconfig.h").read_bytes():
                    raise ValueError("Copied upstream configuration has changed")
                names.add("h/aconfig.h")
            elif path.is_relative_to(source):
                names.add(path.relative_to(source).as_posix())
    if not set(SOURCES if sources is None else sources).issubset(names):
        raise ValueError("Compiler dependency inventory omits a compiled upstream unit")
    return sorted(names)


def retain_upstream_notices(
    source: Path, output: Path, names: list[str], sources: tuple | None = None,
    contributed: dict | None = None,
) -> dict:
    """Retain declarations and exact texts; this is not distribution clearance.

    ``contributed`` maps a path added by an authenticated contribution series to
    its manifest receipt row. Such a file is not upstream's work: its terms are
    what its owner assigned, and the manifest and the file header must say the
    same thing. ``GPL-2.0-or-later`` -- the terms of the spectro drivers it sits
    beside -- is recorded as that identifier with Argyll's License2.txt retained;
    ``unassigned`` records that no terms exist yet. A disagreement in either
    direction is refused, because a receipt naming one licence over a header
    stating another would misstate the file. That is a statement of the file's
    status, not a clearance, and it never applies to a path the series did not add.
    """
    inventory = []
    contributed = contributed or {}
    license_texts = {}
    retained = [
        (output / "UPSTREAM.txt").read_text(encoding="utf-8"),
        "\nCompiler-selected upstream dependency notices follow. SDK/system headers are\n"
        "excluded. SALONEINSTLIB selects the standalone instrument support paths;\n"
        "removing it changes the dependency and license closure (see xicc/ccmx.c).\n"
        "Individual declarations and source texts govern; this inventory does not\n"
        "establish redistribution permission for a combined product.\n",
    ]
    for name in names:
        path = source / name
        text = path.read_text(encoding="utf-8")
        notices = [
            match for match in re.finditer(r"/\*.*?\*/", text, re.DOTALL)
            if "Copyright" in match[0]
        ]
        if not notices:
            raise ValueError(f"No upstream copyright notice found: {name}")
        notice = notices[0]
        declaration = notice[0]
        provenance = {}
        if name in contributed:
            from native_argyll.contribution_notices import check_declaration

            row = contributed[name]
            provenance = {k[len("provenance_"):]: v for k, v in row.items()
                          if k.startswith("provenance_")}
            license_id = check_declaration(name, declaration, provenance)
        elif "GNU GENERAL PUBLIC LICENSE Version 2 or later" in declaration or (
            "GNU General Public License" in declaration
            and "either version 2 of the License" in declaration
            and "(at your option) any later version" in declaration
        ):
            license_id = "GPL-2.0-or-later"
        elif "Neither the name of the axTLS project" in declaration:
            license_id = "BSD-3-Clause"
        elif (
            '"MIT"' in declaration or "Permission is hereby granted, free of charge" in declaration
        ):
            license_id = "MIT"
        else:
            raise ValueError(f"Unreviewed upstream license declaration: {name}")
        references = []
        referenced = set(re.findall(r"License\d*\.txt", declaration))
        if license_id == "GPL-2.0-or-later" and not referenced:
            # Bundled libusb-win32 driver_api.h names GPL v2+ without a filename;
            # retain the actual GPL text shipped in the authenticated archive.
            referenced.add("License2.txt")
        for reference in sorted(referenced):
            candidates = (path.parent / reference, source / reference)
            target = next((p for p in candidates if p.is_file()), None)
            if target is None:
                raise ValueError(f"Missing upstream license text for {name}: {reference}")
            relative = target.relative_to(source).as_posix()
            destination = output / "upstream-licenses" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, destination)
            license_texts[relative] = {
                "sha256": digest(target.read_bytes()),
                "retained_path": destination.relative_to(output).as_posix(),
            }
            references.append(relative)
        inventory.append({
            "path": name,
            "role": (
                "translation_unit" if name in (SOURCES if sources is None else sources)
                else "included_dependency"
            ),
            "compiled_source_sha256": digest(path.read_bytes()),
            "declared_license": license_id,
            "notice_line": text[:notice.start()].count("\n") + 1,
            "notice_sha256": digest(declaration.encode()),
            "license_texts": references,
            "license_text_in_notice": not references,
            **({"contributed": True, "provenance": provenance} if provenance else {}),
        })
        retained.extend((f"\n--- {name} ({license_id}) ---\n", declaration, "\n"))
    (output / "UPSTREAM.txt").write_text("".join(retained), encoding="utf-8", newline="\n")
    return {
        "scope": "compiler-selected upstream units and dependencies; excludes system SDK",
        "standalone_instrument_library": True,
        "redistribution_review_completed": False,
        "files": inventory,
        "license_texts": license_texts,
        "notices_path": "UPSTREAM.txt",
        "notices_sha256": digest((output / "UPSTREAM.txt").read_bytes()),
    }


from color_measurement.argyll_capability import (  # noqa: E402
    INSTRUMENTS as INSTRUMENT_TYPES,
)


def require_passive_serial(flags: list[str], report: dict, source: Path | None = None) -> None:
    """Require passive serial enumeration in the actual platform source.

    Linux must omit its candidate-open validity probe as well as automatic fast
    identification. Windows lists registry metadata; macOS lists IORegistry
    metadata. Explicit selected-port acquisition is outside this function.
    """
    if not any("ENABLE_SERIAL" in flag or "ENABLE_FAST_SERIAL" in flag for flag in flags):
        return
    compiled = "spectro/icoms_nt.c" if platform.system() == "Windows" else "spectro/icoms_ux.c"
    patched = {entry["path"] for entry in report.get("patches", ())}
    if compiled not in patched:
        raise ValueError(
            f"Serial support is compiled in without driver_manifest's passive-serial "
            f"patch on {compiled}: enumeration could open and identify serial ports."
        )
    if source is None:
        raise ValueError("Passive serial verification requires the compiled source")
    text = (source / compiled).read_text(encoding="utf-8", errors="replace")
    if "/* CM: passive catalog */" not in text:
        raise ValueError(f"{compiled} lacks the passive-serial marker")
    from native_argyll.no_termination import _code, _function

    body, _ = _function(text, "int serial_get_paths(", compiled)
    code = _code(body)
    if re.search(r"\bfast_ser_dev_type\s*\(", code):
        raise ValueError(f"{compiled} still calls fast_ser_dev_type during enumeration")
    if re.search(r"\b(?:open|CreateFile[AW]?)\s*\(", code):
        raise ValueError(f"{compiled} still opens a serial candidate during enumeration")


def record_usb_match_scope(report: dict) -> str:
    """Require the current broad USB matcher and record its enumeration scope."""
    if report.get("driver_scope") != "all_public":
        raise ValueError(f"Unknown driver scope {report.get('driver_scope')!r}")
    if any(row["path"] == "spectro/insttypes.c" for row in report.get("patches", ())):
        raise ValueError("The broad source must not install a narrowed USB matcher")
    report["usb_match_scope"] = "all_supported"
    return "all_supported"


def library_sources(source: Path, scope: str, system: str) -> tuple[str, ...]:
    """Select the full driver union before applying the target's source rules."""
    if scope != "all_public":
        raise ValueError(f"Unsupported native source scope: {scope}")
    from native_argyll.driver_manifest import instrument_sources

    selected = (*SOURCES, *instrument_sources(source))
    return tuple(dict.fromkeys(name for name in selected
                              if system != "Windows" or name != "spectro/pollem.c"))


def library_compile_flags(source: Path, output: Path, report: dict, system: str) -> list[str]:
    """Share the exact library/worker compiler context with production-source checks."""
    check_build_platform(system)
    includes = ([output]
                + [source / d for d in ("spectro", "numlib", "rspl", "xicc", "cgats", "h")]
                + [NATIVE])
    # SALONEINSTLIB selects standalone support, avoiding the wider AGPL dependency
    # paths documented in xicc/ccmx.c. It is part of this pinned build's contract.
    flags = ["-std=gnu99", "-O1", "-DSALONEINSTLIB", "-DENABLE_USB"]
    flags += ["-DNT", "-DEN_LIBUSB0"] if system == "Windows" else ["-DUNIX"]
    if system == "Windows":
        # Argyll numsup.h/icoms.h otherwise choose XP before including windows.h.
        # Strict UTF-16 conversion and GetTickCount64 require Vista-era APIs.
        flags += ["-DWINVER=0x0600", "-D_WIN32_WINNT=0x0600"]
    if system in {"Darwin", "Linux"}:
        flags.append("-pthread")
    flags += ["-DENABLE_SERIAL", "-DENABLE_FAST_SERIAL"]
    # __FILE__ can enter assertion strings even without debug information. Map
    # owned source/build roots before linking and authenticating worker bytes.
    # Compilers use the last matching map, so nested roots follow their parents.
    mappings = ((HERE.parent.resolve(), "./color-measurement-app"),
                (output.resolve(), "./native-build"), (source.resolve(), "./argyll-source"))
    flags += [f"-ffile-prefix-map={path}={stable}" for path, stable in
              sorted(mappings, key=lambda item: len(str(item[0])))]
    # Project C names a contributed enum only under CM_CONTRIBUTED_<enum>
    # (serial_policy.h), so a stock build compiles none of those names. The
    # set is the series manifest's declaration, which load_series restricts
    # to inst* identifiers and driver_manifest checks against the header.
    flags += [f"-DCM_CONTRIBUTED_{enum}" for enum in
              report.get("contribution", {}).get("instrument_types_added", ())]
    return flags + [f"-I{p}" for p in includes]


def build_library(source: Path, output: Path, report: dict, cc: str) -> list[str]:
    system = platform.system()
    flags = library_compile_flags(source, output, report, system)
    # pollem.c is UNIX-only, matching the platform split in upstream spectro/Jamfile.
    sources = library_sources(source, report.get("driver_scope"), system)
    require_passive_serial(flags, report, source)
    record_usb_match_scope(report)
    # The broad library links the same original-i1Pro implementation. Enforce
    # its no-EEPROM-write policy wherever that source is compiled, before any
    # object or real-device worker can be produced.
    if "spectro/i1pro_imp.c" in sources:
        report["eeprom_write_policy"] = prove_i1pro_eeprom_policy(
            source, output, report, cc, flags,
        )
    dependencies = []

    def dependency_flags(stem: str) -> list[str]:
        dependency = output / f"{stem}.d"
        dependencies.append(dependency)
        return ["-MMD", "-MF", str(dependency), "-MT", "object"]

    objects = []
    for name in sources:
        obj = output / (name.replace("/", "_") + ".o")
        compile_with_dependencies(
            [cc, *flags, *dependency_flags(obj.stem), "-c", str(source / name), "-o", str(obj)],
            output, report,
        )
        objects.append(str(obj))
    # The PR application overlay registers pure callbacks in its constructor.
    # Keep their owner in the same archive so every linked tool resolves them,
    # including the description and link probes that emit no session events.
    for name in control_adapters(report):
        obj = output / f"application_{name}.o"
        compile_with_dependencies(
            [cc, *flags, *dependency_flags(obj.stem), "-c", str(NATIVE / name),
             "-o", str(obj)], output, report)
        objects.append(str(obj))
    library = output / "libcm_argyll_offline.a"
    archiver = shutil.which("ar")
    if archiver is None:
        raise ValueError("An ar static-library archiver is required")
    command([archiver, "rcs", str(library), *objects], output, report)
    # Force-load every object: a successful archive creation alone cannot prove link closure.
    link_flags = library_link_flags(library, system)
    compile_with_dependencies(
        [
            cc,
            *flags,
            *dependency_flags("link_probe"),
            str(NATIVE / "link_probe.c"),
            *link_flags,
            "-o",
            str(executable_path(output, "library-link-probe-never-run")),
        ],
        output,
        report,
    )
    report["library_build_link"] = {
        "status": "passed",
        "translation_units": list(sources),
        "library_sha256": digest(library.read_bytes()),
        "linked_probe_sha256": digest(
            executable_path(output, "library-link-probe-never-run").read_bytes()
        ),
        "executed": False,
    }
    # The generic worker, linked against the REAL attach unit and the real pinned
    # drivers. Built and link-checked, never executed: running it enumerates
    # devices. This is the artifact that shows the ladder is a program that can
    # drive an instrument, not only a stub.
    generic_worker = executable_path(output, "generic-worker-never-run")
    generic_objects = []
    for name in ("session.c", "session_attach.c",
                 *(name for name in support_adapters(report)
                   if name not in control_adapters(report))):
        obj = output / f"generic_{name}.o"
        compile_with_dependencies(
            [cc, *flags, *dependency_flags(f"generic_{name}"), "-c", str(NATIVE / name),
             "-o", str(obj)],
            output, report,
        )
        generic_objects.append(str(obj))
    command([cc, *flags, *process_entry_flags(system), *generic_objects, *link_flags,
             "-o", str(generic_worker)],
            output, report)
    report["generic_worker_build_link"] = {
        "status": "passed",
        "scope": "real attach unit over the pinned drivers; selection by inst_enum name",
        "executable_sha256": digest(generic_worker.read_bytes()),
        "executed": False,
    }
    if system == "Windows":
        inspector = shutil.which("objdump") or shutil.which("llvm-objdump")
        if inspector is None:
            raise ValueError("Windows MinGW-w64 binutils objdump is required")
        pe = command([inspector, "-p", str(generic_worker)], output, report)
        report["generic_worker_build_link"]["runtime_dll_imports"] = windows_dll_imports(
            pe, compiler_target=report["compiler_target"])
    description_objects = []
    for name in ("description.c", "description_attach.c"):
        obj = output / f"{name}.o"
        compile_with_dependencies(
            [cc, *flags, *dependency_flags(name), "-c", str(NATIVE / name), "-o", str(obj)],
            output, report,
        )
        description_objects.append(str(obj))
    description_worker = executable_path(output, "argyll-description-worker-never-run")
    command([
        cc, *flags, *process_entry_flags(system), *description_objects, *link_flags,
        "-o", str(description_worker),
    ], output, report)
    report["description_worker_build_link"] = {
        "status": "passed", "executed": False,
        "executable_sha256": digest(description_worker.read_bytes()),
    }
    if system == "Windows":
        pe = command([inspector, "-p", str(description_worker)], output, report)
        report["description_worker_build_link"]["runtime_dll_imports"] = windows_dll_imports(
            pe, compiler_target=report["compiler_target"])
    contributed = {row["path"]: row
                   for row in report.get("contribution", {}).get("entries", [])
                   if row["operation"] == "add"}
    report["upstream_license_inventory"] = retain_upstream_notices(
        source, output, upstream_dependencies(dependencies, source, output, sources), sources,
        **({"contributed": contributed} if contributed else {}),
    )
    from native_argyll.driver_manifest import reconcile_linked_drivers

    report["driver_manifest"] = reconcile_linked_drivers(
        source, output, report, cc, flags, library, command,
    )
    report["coverage_summary"] = report["driver_manifest"]["coverage_summary"]
    return flags


def legacy_eeprom_bodies(preprocessed: str, macros: str) -> dict[str, str]:
    """Require the actual compiler-selected writer and log updater to omit writes."""
    if re.search(r"^#define ENABLE_WRITE\b", macros, re.M):
        raise ValueError("Legacy ENABLE_WRITE must remain disabled")
    bodies = {}
    for name in ("i1pro_writeEEProm", "i1pro_update_log"):
        matches = list(re.finditer(
            r"^i1pro_code\s+" + name + r"\s*\([^;{}]*\)\s*\{", preprocessed, re.M,
        ))
        if len(matches) != 1:
            raise ValueError(f"Missing or ambiguous compiled legacy function: {name}")
        start = matches[0].start()
        end = preprocessed.index("\n}", matches[0].end()) + 2
        body = preprocessed[start:end]
        if re.search(r"\b(?:usb_control|usb_write)\s*\(", body) or (
            name == "i1pro_update_log" and re.search(r"\bi1pro_writeEEProm\s*\(", body)
        ):
            raise ValueError(f"Compiled legacy EEPROM write path is active: {name}")
        bodies[name] = body
    return bodies






def prove_i1pro_eeprom_policy(
    source: Path, output: Path, report: dict, cc: str, flags: list,
) -> dict:
    """Preprocess the linked original-i1Pro source and require writes disabled."""
    implementation = source / "spectro/i1pro_imp.c"
    preprocessed = command([cc, *flags, "-E", "-P", str(implementation)], output, report)
    # The pinned source undef must also override a command-line attempt to enable it.
    macros = command([cc, *flags, "-DENABLE_WRITE", "-dM", "-E", str(implementation)],
                     output, report)
    bodies = legacy_eeprom_bodies(preprocessed, macros)
    return {
        "status": "passed", "enable_write": False,
        "scope": "actual compiler preprocessing; no EEPROM or hardware calls executed",
        "compiled_source_sha256": digest(implementation.read_bytes()),
        "preprocessed_sha256": digest(preprocessed.encode()),
        "forced_macro_inventory_sha256": digest(macros.encode()),
        "function_sha256": {name: digest(body.encode()) for name, body in bodies.items()},
    }




def build_substituted_workers(source: Path, output: Path, report: dict, cc: str,
                              flags: list[str], *, system: str) -> None:
    """Build/check the existing substituted protocols, with original receipt fields.

    This helper is shared by full native acceptance and the explicit binding-only
    scope. It never runs a production acquisition or description worker.
    """
    # The generic ladder, built and exercised against a wholly substituted
    # colorimeter. It binds a route neither per-device helper serves, so this
    # is the artifact that shows the ladder is generic rather than the claim.
    generic = executable_path(output, "generic-session-stub-worker")
    adapters = [str(NATIVE / name) for name in support_adapters(report)]
    # The CR-family persona is the REAL driver over the series' scripted
    # instrument (cr250_persona.c, cr250_fake.c): the driver's two sources,
    # numsup.c for logging and time, and the spectrum-to-XYZ conversion its unit
    # check and the fake's spectrum both use (xspect.c with sa_conv.c and the
    # CGATS reader it pulls in). Nothing here can reach a port or construct
    # another instrument.
    from native_argyll import sanitizers

    added = set(report.get("contribution", {}).get("instrument_types_added", ()))
    persona = []
    allowed = set()
    test_flags = list(flags)
    if adapters:
        contributed_sanitizer_support(output, report, cc, system)
        common, allowed = build_contributed_test_support(source, output, report, cc, flags)
        test_flags += ["-DCM_CONTRIBUTED_TEST_SUPPORT",
                       *sanitizers.flags(report["contributed_sanitizers"]["enabled"])]
        if "new_cr250" in allowed:
            persona.append(str(NATIVE / "cr250_persona.c"))
        if "new_pr655" in allowed:
            persona.append(str(NATIVE / "pr655_persona.c"))
        persona += [*common, *system_link_flags(system)]
    # Only CM_PROCESS_MAIN entrypoints use wide Windows CRT startup. Ordinary
    # main probes below share compiler/sanitizer flags, not worker link startup.
    worker_flags = [*test_flags, *process_entry_flags(system)]
    command(
        [
            cc,
            *worker_flags,
            str(NATIVE / "session.c"),
            str(NATIVE / "colorimeter_stub.c"),
            *adapters,
            *persona,
            *referenced_members_link_flags(output / "libcm_argyll_offline.a", system),
            "-o",
            str(generic),
        ],
        output,
        report,
    )
    # The same session.c, linked to substituted SpectroScan / DTP41 / ColorMunki
    # drivers: the strip and sheet workflows neither per-device helper has.
    chart = executable_path(output, "generic-chart-stub-worker")
    chart_sources = [
        str(NATIVE / "session.c"),
        str(NATIVE / "chart_stub.c"),
        # Every other constructible type, one row each, over the same
        # substituted reading.
        str(NATIVE / "driver_personas.c"),
        *adapters,
        # The substituted readings take their XYZ from their spectrum with
        # upstream's own conversion, as the drivers do. xspect.c pulls in
        # sa_conv.c and the CGATS reader, the same set the CR persona
        # links; none of it can reach a port.
        *(persona if adapters else (str(source / name) for name in (
            "numlib/numsup.c", "xicc/xspect.c", "spectro/sa_conv.c",
            "cgats/cgats.c", "cgats/pars.c"))),
        *system_link_flags(system),
    ]
    command([cc, *worker_flags, *chart_sources,
             *referenced_members_link_flags(output / "libcm_argyll_offline.a", system),
             "-o", str(chart)], output, report)
    constructors = {row["constructor"] for row in
                    report.get("driver_manifest", {}).get("instrument_types", ())
                    if row.get("constructor")}
    # Contributed constructors use only scripted icoms. Other constructors and
    # every OS instrument transport remain forbidden by defined-symbol checks.
    forbid_linked_transport(generic, constructors - allowed, output, report)
    forbid_linked_transport(chart, constructors - allowed, output, report)
    sanitized_chart = None
    if system != "Windows":
        from native_argyll import sanitizers
        from native_argyll.generic_session_check import patch_count_allocation

        # The chart worker again, sanitized: as composed, and with the
        # stored-chart grid allocation sized to the requested patches, which
        # the check requires to overflow. The copy is compiled from its own
        # directory with this tree's headers first, as session.c finds them.
        earlier = output / "patch-count-allocation" / "session.c"
        earlier.parent.mkdir(exist_ok=True)
        earlier.write_text(patch_count_allocation((NATIVE / "session.c").read_text()),
                           encoding="utf-8", newline="\n")
        sanitized_chart = {}
        for name, session in (("repaired", NATIVE / "session.c"),
                              ("patch_count_allocation", earlier)):
            worker = executable_path(output, f"sanitized-chart-stub-worker-{name}")
            command([cc, f"-I{NATIVE}", *worker_flags, *sanitizers.flags(True), str(session),
                     *chart_sources[1:], *referenced_members_link_flags(
                         output / "libcm_argyll_offline.a", system), "-o", str(worker)],
                    output, report)
            forbid_linked_transport(worker, constructors - allowed, output, report)
            sanitized_chart[name] = worker
    if persona and "new_cr250" not in command(["nm", str(generic)], output, report):
        raise ValueError("The CR-250 persona does not link the real constructor")
    from native_argyll.generic_session_check import exercise_generic_session

    report["generic_session"] = exercise_generic_session(
        generic, chart, output, cr250="new_cr250" in allowed,
        pr655="new_pr655" in allowed, compiler=cc,
        sanitized_chart=sanitized_chart)
    if "new_cr250" in allowed:
        from native_argyll import sanitizers

        driver_unit = build_cr250_driver_unit(source, output, report, cc, flags, system=system)
        driver_output = command([str(driver_unit)], output, report)
        sanitizers.refuse((output / report["commands"][-1]["log"]).read_text(),
                         "CR driver-unit execution")
        report["cr250_driver_unit"] = cr250_driver_unit_results(
            driver_output, driver_unit, sanitize=report["contributed_sanitizers"]["enabled"])
        print(f"PASS: CR contribution driver-unit "
              f"({report['cr250_driver_unit']['checks']} checks).", flush=True)
        # The whole-read floor, asked of cr250_required_floor_s as composed onto
        # the driver by cr250_host.py, for every mode word the worker could
        # send, and published beside the
        # budget floor the manifest read from the header. The retained
        # driver-manifest.json is rewritten so its copy and the report's agree.
        floors = executable_path(output, "cr-floor")
        command([cc, *test_flags, str(NATIVE / "cr_floor.c"),
                 *(str(NATIVE / name) for name in control_adapters(report)), *persona,
                 *referenced_members_link_flags(output / "libcm_argyll_offline.a", system),
                 "-o", str(floors)],
                output, report)
        forbid_linked_transport(floors, constructors - allowed, output, report)
        added = report["contribution"]["instrument_types_added"]
        for row in report["driver_manifest"]["instrument_types"]:
            if row["enum"] in set(added) & {"instCR250", "instCR100", "instCR300"}:
                by_word = cr_read_allowance_floors(floors, row["enum"], output, report)
                row["read_allowance_floors_s"] = by_word
                row["read_allowance_floor_s"] = min(by_word.values())
        if {"instPR655", "instPR670"} & set(added):
            pr_floors = executable_path(output, "pr-floor")
            command([cc, *flags, str(NATIVE / "pr_floor.c"), "-o", str(pr_floors)], output, report)
            forbid_linked_transport(pr_floors, constructors, output, report)
            for row in report["driver_manifest"]["instrument_types"]:
                if row["enum"] in {"instPR655", "instPR670"}:
                    by_word = cr_read_allowance_floors(pr_floors, row["enum"], output, report)
                    row["read_allowance_floors_s"] = by_word
                    row["read_allowance_floor_s"] = min(by_word.values())
        # The allowance and reply retention this application composes onto the
        # driver (cr250_host.py), exercised on that transformed source over the
        # series' scripted instrument. A failed check fails the build.
        host_test = executable_path(output, "cr-host-test")
        command([cc, *test_flags, str(NATIVE / "cr250_control_test.c"),
                 str(NATIVE / "cr250_support.c"), str(NATIVE / "cr250_control.c"), *persona,
                 *referenced_members_link_flags(output / "libcm_argyll_offline.a", system),
                 "-o", str(host_test)],
                output, report)
        forbid_linked_transport(host_test, constructors - allowed, output, report)
        host_output = command([str(host_test)], output, report)
        sanitizers.refuse((output / report["commands"][-1]["log"]).read_text(),
                         "CR host-test execution")
        report["cr250_host_test"] = cr250_host_results(
            host_output, host_test, suite="cr250_control_test")
        print(f"PASS: CR host allowance and reply retention "
              f"({report['cr250_host_test']['checks']} checks).", flush=True)
    if "new_pr655" in allowed:
        host_test = build_pr655_host_check(source, output, report, cc, flags, system=system)
        host_output = command([str(host_test)], output, report)
        sanitizers.refuse((output / report["commands"][-1]["log"]).read_text(),
                          "PR host-test execution")
        found = re.findall(r"^pr655_host_test: (\d+) checks, (\d+) failures$", host_output, re.M)
        if len(found) != 1 or int(found[0][0]) <= 0 or found[0][1] != "0":
            raise ValueError("PR host-test did not report a clean run")
        report["pr655_host_test"] = {"checks": int(found[0][0]), "failures": 0,
                                    "executable_sha256": digest(host_test.read_bytes()),
                                    "sanitize": report["contributed_sanitizers"]["enabled"],
                                    "hardware_tested": False}
        print(f"PASS: PR controls, retained observations and mapping "
              f"({report['pr655_host_test']['checks']} checks).", flush=True)
    # Which types' drivers publish retained native facts (native_facts.h, one
    # support adapter per contributed driver): their readings carry them, which
    # no inline patch event can hold. Stated for every row, never left absent.
    adapted = ({"instCR250", "instCR100", "instCR300", "instPR655", "instPR670"}
               & set(report.get("contribution", {}).get("instrument_types_added", ()))
               if adapters else set())
    if "driver_manifest" in report:
        for row in report["driver_manifest"]["instrument_types"]:
            row["native_facts"] = row["enum"] in adapted
            row["native_controls"] = row["enum"] in adapted
        (output / "driver-manifest.json").write_text(
            json.dumps(report["driver_manifest"], indent=2) + "\n")
    report["generic_session"]["executable_sha256"] = digest(generic.read_bytes())
    report["generic_session"]["chart_executable_sha256"] = digest(chart.read_bytes())
    # The ladder is generic; the BUILD is not. inst_enum() resolves every name
    # in every build, but new_inst's constructor dispatch is compiled: the build
    # links every public driver along with ENABLE_SERIAL. Naming an unlinked
    # instrument fails as a bare "attach", indistinguishable from an absent
    # device -- so state the set, and refuse to let it drift unnoticed.
    from native_argyll.driver_manifest import parse_dispatch

    linked = set(parse_dispatch(
        command([cc, *flags, "-E", "-P", str(source / "spectro/inst.c")], output, report)
    ))
    report["generic_worker_build_link"]["linked_instrument_types"] = sorted(linked)
    # The broad build must reach every instrument the vocabulary names EXCEPT
    # the Spectrocam, whose constructor upstream comments out (inst.c, "NYI").
    # Pinning that difference is what keeps "37 of 38" an explained number
    # rather than a surprise.
    contributed = set(report.get("contribution", {}).get("instrument_types_added", []))
    expected = (set(INSTRUMENT_TYPES) | contributed) - {"instSpectrocam"}
    if linked != expected:
        raise ValueError(
            f"Generic worker links {len(linked)} instruments, expected {len(expected)}: "
            f"missing {sorted(expected - linked)}, unexpected {sorted(linked - expected)}"
        )
    report["generic_session"]["linked_instrument_types"] = sorted(linked)
    print(f"NOTE: this build's generic worker can construct {len(linked)} of "
          f"{len(INSTRUMENT_TYPES) + len(contributed)} instruments: "
          f"{', '.join(sorted(linked))}.", flush=True)
    # Counted from the report rather than written here, which is how this line
    # came to say "six" while the check drove seven.
    personas = report["generic_session"]["personas"]
    real = sum(persona.endswith("(real driver)") for persona in personas)
    print(f"PASS: one generic ladder drove {len(personas)} instrument personas "
          f"({len(personas) - real} substituted, {real} real contributed drivers over "
          "simulated serial) across every acquisition workflow.", flush=True)

    from native_argyll.no_termination import prove_no_termination

    if "no_kill" not in report:
        report["no_kill"] = prove_no_termination(source, output, report, cc, command)
    from native_argyll.description_check import (
        exercise_description,
        prove_selection_guard,
        prove_session_selection,
    )

    description_stub = executable_path(output, "description-stub-worker")
    # Reuse the chart vtable for i1Pro3 description and acquisition. Only
    # acquisition command parsing/attachment is omitted from this object.
    description_chart = output / "description-chart-persona.o"
    command([cc, *test_flags, "-DCM_CHART_DESCRIPTION", "-c",
             str(NATIVE / "chart_stub.c"), "-o", str(description_chart)],
            output, report)
    command([
        cc, *worker_flags, str(NATIVE / "description.c"), str(NATIVE / "description_stub.c"),
        str(description_chart), str(NATIVE / "driver_personas.c"),
        *(str(NATIVE / name) for name in control_adapters(report)), *persona,
        *referenced_members_link_flags(output / "libcm_argyll_offline.a", system),
        "-o", str(description_stub),
    ], output, report)
    forbid_linked_transport(
        description_stub,
        {row["constructor"] for row in report["driver_manifest"]["instrument_types"]
         if row.get("constructor")} - allowed,
        output, report)
    report["description_workflow"] = exercise_description(
        description_stub, output, report["driver_manifest"]["instrument_types"])
    report["description_workflow"]["executable_sha256"] = digest(
        description_stub.read_bytes()
    )
    report["session_selection_guard"] = prove_session_selection(output, report, cc,
        flags, command,
        link_flags=referenced_members_link_flags(output / "libcm_argyll_offline.a", system))
    report["selection_guard"] = prove_selection_guard(source, output, report, cc, flags,
                                                      command)
    print("PASS: generic description with wholly substituted instrument.", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, required=True, help="New directory; never overwritten"
    )
    parser.add_argument(
        "--substituted-binding", action="store_true",
        help="Build the broad catalog and run only existing substituted binding protocols; "
             "does not replay full native/scientific regressions or qualify production workers",
    )
    parser.add_argument(
        "--contribution", type=Path,
        help="Directory of an authenticated contribution series (manifest.json, files/, "
             "patches/) composed onto the stock archive before this application's source "
             "policies",
    )
    args = parser.parse_args(argv)
    report = {"status": "failed"}
    output = args.output.resolve()
    owned = False
    try:
        system = platform.system()
        check_build_platform(system)
        cc = shutil.which(compiler_name())
        if cc is None:
            raise ValueError("A C compiler and platform development headers are required")
        if shutil.which("ar") is None:
            raise ValueError("An ar static-library archiver is required")
        if output.exists():
            raise ValueError("Output already exists; choose a new directory")
        series = {"contribution": args.contribution.absolute()} if args.contribution else {}
        source, report = prepare(args.archive.resolve(), output, **series)
        owned = True
        report["build_system"] = system
        report["compiler"] = command([cc, "--version"], output, report).strip()
        if system == "Windows":
            target = command([cc, "-dumpmachine"], output, report).strip()
            if not windows_compiler_matches(target, platform.machine()):
                raise ValueError("Windows recipe requires a matching native Windows-GNU compiler")
            report["compiler_target"] = target
            report["windows_usb_route"] = "EN_LIBUSB0; bundled driver API; no driver installed"
        report["platform_pipe_regression"] = prove_platform_io(output, report, cc)
        if args.substituted_binding:
            report["check_scope"] = "substituted_binding"
            report["scope"] = (
                "Broad library/catalog built without production execution; existing substituted "
                "description/acquisition and transport guards, "
                "not full native regression acceptance"
            )
            flags = build_library(source, output, report, cc)
            build_substituted_workers(source, output, report, cc, flags, system=system)
            report["status"] = "passed"
            return 0
        prepare_proof_inputs(args.archive.resolve(), source, output, report)
        import importlib

        proofs = (
            ('calibration_repairs_regression', 'calibration_repairs', 'prove'),
            ('ss_xy_metadata_regression', 'ss_xy_metadata', 'prove'),
            ('ss_protocol_regression', 'ss_protocol', 'prove'),
            ('spyder_calibration_slots_regression', 'spyder_calibration_slots', 'prove'),
            ('spyder_selection_path_regression', 'spyder_selection_path', 'prove'),
            ('spyder_black_cache_regression', 'spyder_black_cache', 'prove'),
            ('disptype_list_release_regression', 'disptype_list_release', 'prove'),
            ('display_timing_regression', 'display_timing', 'prove'),
            ('disptype_list_member_release_regression', 'disptype_list_member_release', 'prove'),
            ('munki_scan_margin_regression', 'munki_scan_margin', 'prove'),
            ('munki_nonadaptive_calibration_regression', 'munki_nonadaptive_calibration', 'prove'),
            ('xyz_only_conversion_regression', 'xyz_only_conversion', 'prove'),
            ('munki_curve_release_regression', 'munki_curve_release', 'prove'),
            ('munki_white_buffer_release_regression', 'munki_white_buffer_release', 'prove'),
            ('munki_ledtemp_slope_regression', 'munki_ledtemp_slope', 'prove'),
            ('munki_dark_copy_regression', 'munki_dark_copy', 'prove'),
            ('shared_flash_boundary_regression', 'flash_capture', 'prove_shared_endpoints'),
            ('i1pro_flash_saturation_regression', 'flash_capture', 'prove_i1pro_saturation'),
            ('scan_finite_regression', 'scan_finite', 'prove'),
            ('scan_tolerance_regression', 'scan_tolerance_option', 'prove'),
            ('xcalstd_option_regression', 'xcalstd_option', 'prove'),
            ('ieee754_special_regression', 'ieee754_special', 'prove'),
            ('ccss_cgats_regression', 'ccss_cgats', 'prove'),
            ('filter_values_regression', 'filter_values', 'prove'),
            ('conversion_allocation_regression', 'conversion_allocation', 'prove'),
            ('nonadaptive_calibration_regression', 'nonadaptive_calibration', 'prove'),
            ('flash_capture_regression', 'flash_capture', 'prove'),
            ('scan_normalization_regression', 'scan_normalization', 'prove'),
            ('i1pro3_wavelength_finite_regression', 'i1pro3_wavelength_finite', 'prove'),
            ('legacy_nonadaptive_calibration_regression',
             'legacy_nonadaptive_calibration', 'prove'),
            ('legacy_scan_regression', 'legacy_scan_recognition', 'prove'),
        )
        for key, module_name, function_name in proofs:
            module = importlib.import_module("native_argyll." + module_name)
            proof = getattr(module, function_name)(
                args.archive.resolve(), output / key, compiler=cc, source=source)
            if proof["status"] != "passed":
                raise ValueError(f"Native behavior proof failed: {module_name}")
            report[key] = proof
        from native_argyll.legacy_reflective_calibration import prove as prove_reflective

        report["legacy_reflective_calibration_regression"] = prove_reflective(
            args.archive.resolve(), source, output / "legacy-reflective-calibration-proof",
            compiler=cc)
        report["spectral_display_timing_regression"] = prove_spectral_display_timing(
            source, output, report, cc)
        from native_argyll.native_resync_proof import prove as prove_native_resync

        stage = report["native_resync_repair"]
        report["native_resync_regression"] = prove_native_resync(
            source, {name: (output / stage["inputs"] / name).read_text(encoding="utf-8")
                     for name in stage["before_sha256"]},
            output / "native-resync-proof", compiler=cc)
        flags = build_library(source, output, report, cc)
        if "standalone_utf16_compatibility" in report:
            report["standalone_utf16_regression"] = prove_standalone_utf16(
                source, output, report, cc, flags,
            )
        print(
            "PASS: selected Argyll drivers compiled and fully linked (never executed).",
            flush=True,
        )
        from native_argyll.protocol import exercise_helper

        helper = executable_path(output, "synthetic-helper")
        command([cc, *flags, str(NATIVE / "synthetic_helper.c"), "-o", str(helper)], output, report)
        report["synthetic_protocol"] = exercise_helper(helper)
        report["synthetic_protocol"].update(
            executable=str(helper), executable_sha256=digest(helper.read_bytes())
        )
        print("PASS: synthetic helper process protocol.", flush=True)
        from native_argyll.no_termination import prove_no_termination

        report["no_kill"] = prove_no_termination(source, output, report, cc, command)
        # The proof checks each probe's object imports on every target.
        print("PASS: source-bound no-service-termination regression "
              "(probe imports checked inert before each run).", flush=True)

        display_probe = executable_path(output, "display-types-probe")
        command([cc, *flags, str(NATIVE / "display_types_probe.c"),
                 "-o", str(display_probe)], output, report)
        display_output = command([str(display_probe)], output, report)
        display_frames = [json.loads(line) for line in display_output.splitlines()]
        from color_measurement.display_types import catalogue as join_display_catalogue

        display_summary = display_frames[-1]
        display_catalogue = join_display_catalogue(
            display_summary["display_types"],
            [{key: value for key, value in row.items() if key != "event"}
             for row in display_frames[:-1]])
        if display_summary["failures"] != 0 or len(display_catalogue["entries"]) != 1000:
            raise ValueError("Native display catalogue proof failed")
        report["display_catalogue_proof"] = {
            "status": "passed", "entries": len(display_catalogue["entries"]),
            "scope": "loaded matrix/spectrum identities, bounded rows and partial readback",
            "source_sha256": digest((NATIVE / "display_types_probe.c").read_bytes()),
        }

        build_substituted_workers(source, output, report, cc, flags, system=system)
        report["status"] = "passed"
        return 0
    except (OSError, ValueError, RuntimeError, AssertionError, subprocess.SubprocessError) as error:
        report["status"] = "failed"
        report["error"] = str(error)
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    finally:
        if owned:
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n",
                                              encoding="utf-8", newline="\n")


def prove_platform_io(output: Path, report: dict, cc: str) -> dict:
    """Run process arguments and pipes only, with exact bytes and explicit EOF."""
    from color_measurement.native_protocol import HelperSession

    unit = output / "platform-pipe-regression.c"
    executable = executable_path(output, "platform-pipe-regression")
    unit.write_text(r'''
#include "platform_io.h"
#include <string.h>
static FILE *wire;
static void bytes(const char *kind, unsigned char *value, size_t used) {
    fprintf(wire, "{\"kind\":\"%s\",\"hex\":\"", kind);
    for (size_t i = 0; i < used; ++i) fprintf(wire, "%02x", value[i]);
    fputs("\"}\n", wire);
}
CM_PROCESS_MAIN(cm_platform_probe_main) {
    char byte;
    unsigned char value[256];
    size_t used = 0, last = 0;
    int result;
#ifdef _WIN32
    if (argc > 1 && !strcmp(argv[1], "dependencies")) {
        /* Pinned hidio.c:128-132 resolves precisely these three OS exports. */
        const char *names[] = {"HidD_GetHidGuid", "HidD_GetAttributes",
                               "HidD_GetSerialNumberString"};
        HMODULE library = LoadLibraryA("hid.dll");
        int available = 1;
        if (!library) return 6;
        for (size_t i = 0; i < sizeof(names)/sizeof(names[0]); i++)
            if (!GetProcAddress(library, names[i])) available = 0;
        if (!FreeLibrary(library)) return 7;
        if (!available) return 8;
        puts("{\"windows_hid_exports\":3,\"library_released\":true}");
        return 0;
    }
#endif
    wire = cm_open_wire();
    if (!wire) return 2;
    if (argc == 3 && !strcmp(argv[1], "input-file")) {
        FILE *file = cm_open_input_file(argv[2]);
        int ch, failed;
        if (!file) { fclose(wire); return 9; }
        while ((ch = fgetc(file)) != EOF) {
            if (used == sizeof(value)) { fclose(file); fclose(wire); return 5; }
            value[used++] = (unsigned char)ch;
        }
        failed = ferror(file);
        if (fclose(file)) failed = 1;
        if (failed) { fclose(wire); return 10; }
        bytes("input_file", value, used);
        fclose(wire); return 0;
    }
    if (argc > 1 && !strcmp(argv[1], "arguments")) {
        fputs("{\"argv_hex\":[", wire);
        for (int i = 2; i < argc; i++) {
            if (i > 2) fputc(',', wire);
            fputc('"', wire);
            for (const unsigned char *s = (const unsigned char *)argv[i]; *s; s++)
                fprintf(wire, "%02x", *s);
            fputc('"', wire);
        }
        fputc(']', wire);
#ifdef _WIN32
        fprintf(wire, ",\"ansi_code_page\":%u", GetACP());
#endif
        fputs("}\n", wire);
        /* Description's backend may legally rewrite argv before returning. */
        if (argc > 2) argv[2] = argv[0];
        fclose(wire); return 0;
    }
    puts("ordinary output stays on stderr"); fflush(stdout);
    fprintf(wire, "{\"kind\":\"ready\",\"empty\":%d}\n", cm_stdin_byte(&byte, 0));
    if (argc > 1) {
        if (cm_stdin_byte(&byte, 1) != CM_IO_BYTE) return 3;
        value[0] = (unsigned char)byte; bytes("byte", value, 1);
        fprintf(wire, "{\"kind\":\"eof\",\"code\":%d}\n", cm_stdin_byte(&byte, 1));
        fclose(wire); return 0;
    }
    for (;;) {
        result = cm_stdin_byte(&byte, 0);
        if (result == CM_IO_ERROR) return 4;
        if (result == CM_IO_EOF) { bytes("eof", value, used); fclose(wire); return 0; }
        if (result == CM_IO_PENDING) {
            if (used != last) { bytes("partial", value, used); last = used; }
            cm_sleep_ms(1); continue;
        }
        if (used == sizeof(value)) return 5;
        value[used++] = (unsigned char)byte;
        if (byte == '\n') { bytes("line", value, used); used = last = 0; }
    }
}
'''.lstrip(), encoding="utf-8", newline="\n")
    system = platform.system()
    command([cc, "-std=gnu99", "-O1", *process_entry_flags(system), f"-I{NATIVE}",
             str(unit), "-o", str(executable)],
            output, report)
    cases = []
    for blocking in (False, True):
        with HelperSession([str(executable), *(["blocking"] if blocking else [])],
                           timeout=3) as session:
            def expect(message: dict) -> None:
                if session.receive_message() != message or not session.last_raw.endswith("\n"):
                    raise RuntimeError("Process-pipe adapter changed bytes, framing or EOF")
                if session.last_raw.endswith("\r\n"):
                    raise RuntimeError("Wire must preserve LF without CRT text translation")

            expect({"kind": "ready", "empty": 0})
            if blocking:
                session.process.stdin.write(b"Z")
                session.process.stdin.flush()
                expect({"kind": "byte", "hex": "5a"})
                session.process.stdin.close()
                expect({"kind": "eof", "code": -1})
            else:
                prefix, suffix, tail = b"v1 op can", b"cel\n", b"unfinished\x1a\r\0"
                session.process.stdin.write(prefix)
                session.process.stdin.flush()
                expect({"kind": "partial", "hex": prefix.hex()})
                session.process.stdin.write(suffix)
                session.process.stdin.flush()
                expect({"kind": "line", "hex": (prefix + suffix).hex()})
                session.process.stdin.write(tail)
                session.process.stdin.flush()
                session.process.stdin.close()
                # A poll may observe the tail before EOF becomes visible.
                message = session.receive_message()
                if message == {"kind": "partial", "hex": tail.hex()}:
                    message = session.receive_message()
                if message != {"kind": "eof", "hex": tail.hex()}:
                    raise RuntimeError("Process-pipe EOF discarded incomplete input bytes")
            session.finish_messages()
            if session.stderr.strip() != b"ordinary output stays on stderr":
                raise RuntimeError("Ordinary stdout was not isolated from the protocol wire")
            cases.append("blocking_byte_and_eof" if blocking else "fragmented_poll_cancel_and_eof")
    samples = ["", "CM-ñ-1", "é", "e\u0301", "中", "🧪", 'a "quote"',
               "a b", "C:\\space here\\ñ\\", "\\\\"]
    expected = [value.encode("utf-8").hex() for value in samples]
    observed = json.loads(command([str(executable), "arguments", *samples], output, report))
    if observed.get("argv_hex") != expected:
        raise RuntimeError("Process command line did not retain exact UTF-8 argument bytes")
    arguments = {"received_utf8_hex": observed["argv_hex"], "legacy_narrow": None}
    cases.append("utf8_command_line_arguments")
    unicode_directory = output / "input-é-中-🧪"
    unicode_directory.mkdir()
    input_bytes = b"2 400.5 410.5 100 1\n-0 1.2345678901234567\n"
    staged = unicode_directory / "spectra-é-中-🧪.txt"
    staged.write_bytes(input_bytes)
    file_observations = []
    for payload in (input_bytes, input_bytes + b"\x1auntouched tail\r\n"):
        staged.write_bytes(payload)
        row = json.loads(command([str(executable), "input-file", str(staged)], output, report))
        if row != {"kind": "input_file", "hex": payload.hex()}:
            raise RuntimeError("UTF-8 input filename or file bytes changed")
        file_observations.append(row)
    cases.append("utf8_input_file_bytes")
    dependencies = None
    if system == "Windows":
        # Observe the former CRT entrypoint with the same compiler, arguments and
        # bytes reporter. This baseline is evidence, never a fallback for workers.
        legacy_unit = output / "platform-arguments-narrow.c"
        text = unit.read_text()
        anchor = "CM_PROCESS_MAIN(cm_platform_probe_main)"
        if text.count(anchor) != 1:
            raise ValueError("Platform argument probe entrypoint is ambiguous")
        legacy_unit.write_text(text.replace(anchor, "int main(int argc, char **argv)"),
                               encoding="utf-8", newline="\n")
        legacy = executable_path(output, "platform-arguments-narrow")
        command([cc, "-std=gnu99", "-O1", f"-I{NATIVE}", str(legacy_unit),
                 "-o", str(legacy)], output, report)
        arguments["legacy_narrow"] = json.loads(
            command([str(legacy), "arguments", *samples], output, report))
        # Invalid UTF-16 must fail before the probe body or any argument parser.
        invalid = subprocess.run([str(executable), "arguments", "\ud800"],
                                 capture_output=True, timeout=10)
        (output / "platform-arguments-invalid.log").write_bytes(invalid.stdout + invalid.stderr)
        if invalid.returncode != 2 or invalid.stdout or b"UTF-8" not in invalid.stderr:
            raise RuntimeError("Process command line did not refuse invalid UTF-16")
        arguments["invalid_utf16_refused"] = True
        dependencies = json.loads(command([str(executable), "dependencies"], output, report))
        if dependencies != {"windows_hid_exports": 3, "library_released": True}:
            raise RuntimeError("Windows HID runtime dependency probe did not release its library")
    return {
        "status": "passed",
        "scope": "owned subprocess arguments, pipes and OS library availability; no equipment",
        "command_line": arguments,
        "input_file": file_observations,
        "runtime_dependencies": dependencies,
        "cases": cases, "source_sha256": digest(unit.read_bytes()),
        "executable_sha256": digest(executable.read_bytes()),
    }


def prove_standalone_utf16(
    source: Path, output: Path, report: dict, cc: str, flags: list[str],
) -> dict:
    """Exercise only string conversion, including sizing and surrogate repair."""
    cases = ("", "12345", "A\u00e9\u4e2d", "\U0001f9ea", "\ud800", "\udc00", "\ud800A")
    rows = []
    expected = []
    for value in cases:
        encoded = value.encode("utf-16-le", errors="surrogatepass")
        units = [int.from_bytes(encoded[i:i + 2], "little") for i in range(0, len(encoded), 2)]
        rows.append("{" + ",".join(str(unit) for unit in [*units, 0]) + "}")
        utf8 = encoded.decode("utf-16-le", errors="replace").encode("utf-8") + b"\0"
        expected.append(f"{len(utf8)}:{utf8.hex()}")
    unit, executable = output / "utf16-regression.c", executable_path(output, "utf16-regression")
    unit.write_text(
        '#include "numsup.h"\n#include "' + (source / "icc/cm_utf16_compat.h").as_posix() + '"\n'
        "int main(void) {\n"
        "icmUTF16 inputs[][8] = {" + ",".join(rows) + "};\n"
        "for (size_t i = 0; i < sizeof(inputs)/sizeof(inputs[0]); ++i) {\n"
        "unsigned char out[64]; size_t count = icmUTF16toUTF8(NULL, NULL, inputs[i]);\n"
        "if (count > sizeof(out) || icmUTF16toUTF8(NULL, out, inputs[i]) != count) return 1;\n"
        'printf("%zu:", count); for (size_t j = 0; j < count; ++j) printf("%02x", out[j]);\n'
        'puts(""); } return 0; }\n', encoding="utf-8", newline="\n",
    )
    command([cc, *flags, str(unit), "-o", str(executable)], output, report)
    actual = command([str(executable)], output, report).splitlines()
    if actual != expected:
        raise RuntimeError("Standalone UTF16 converter changed serial-descriptor text or sizing")
    return {
        "status": "passed", "scope": "string conversion only; no device or instrument runtime",
        "cases": len(cases), "source_sha256": digest(unit.read_bytes()),
        "executable_sha256": digest(executable.read_bytes()),
    }




if __name__ == "__main__":
    raise SystemExit(main())
