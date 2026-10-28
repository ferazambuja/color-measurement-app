"""Pinned SpectroScan X-Y metadata/retry repair and inert actual-function proof.

Argyll's ss.c and xrga.c remain Graeme W. Gill's GPL-2.0-or-later work.
The repair copies the reflective condition policy from ss_read_sample into the
successful reflective X-Y path and preserves a failed fifth attempt as an error.
It changes no command definitions or measurement arithmetic.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import io
import json
import platform
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from native_argyll.proof_inputs import before_source, production_proof, production_text

REPAIR_ID = 'recipe:ss-xy-metadata'

ARCHIVE_SHA256 = "f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f"
PREFIX = "Argyll_V3.5.0/"
SOURCE_PATH = "spectro/ss.c"
READ_SIGNATURE = "static inst_code\nss_read_xy(\n"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _function(text: str, signature: str) -> str:
    if text.count(signature) != 1:
        raise ValueError(f"Missing or ambiguous pinned function: {signature}")
    start = text.index(signature)
    end = text.find("\n}\n", start)
    if end < 0:
        raise ValueError("Missing pinned function end")
    return text[start:end + 3]


def _enum(text: str, name: str) -> str:
    matches = [m[0] for m in re.finditer(r"typedef enum\s*\{[^}]*\}\s*\w+\s*;", text)
               if re.search(r"\}\s*" + name + r"\s*;", m[0])]
    if len(matches) != 1:
        raise ValueError(f"Missing pinned enum {name}")
    return matches[0] + "\n"


def _define(text: str, name: str) -> str:
    # Continue only backslash-terminated physical lines.
    starts = list(re.finditer(r"^#define\s+" + name + r"\b", text, re.M))
    if len(starts) != 1:
        raise ValueError(f"Missing pinned define {name}")
    lines = text[starts[0].start():].splitlines(True)
    result = ""
    for line in lines:
        result += line
        if not line.rstrip().endswith("\\"):
            return result
    raise ValueError(f"Unterminated pinned define {name}")


def xrga_conversion(xrga: str) -> str:
    """Extract the supplied XRGA source, including its declared dispatcher return type."""
    constants = xrga[xrga.index("/* A conversion equation */"):
                     xrga.index("/* Core conversion code.")]
    dispatcher = re.findall(r"^(?:int|void) ipatch_convert_xrga\(", xrga, re.M)
    if len(dispatcher) != 1:
        raise ValueError("Missing or ambiguous XRGA dispatcher declaration")
    return constants + "".join(_function(xrga, signature) for signature in (
        "static void convert_xrga(", "void xspec_convert_xrga(", dispatcher[0]))


def _extract(source: str, sources: dict[str, str]) -> tuple[str, str, str]:
    declarations = ""
    for name, body in sources.items():
        prelude = body[:body.index("typedef") if "typedef" in body else body.index("#include")]
        declarations += f"\n/* Extracted declarations from {name}. */\n"
        declarations += "\n".join(re.findall(r"/\*[\s\S]*?\*/", prelude)) + "\n"
    for path, names in {
        "spectro/inst.h": ("inst_code", "inst_mode", "inst_cal_type", "inst_cal_cond",
                           "inst_calc_id_type", "instClamping"),
        "spectro/insttypes.h": ("devType", "xcalstd"),
        "spectro/ss_imp.h": ("ss_aft", "ss_rvt", "ss_cst", "ss_wbt", "ss_ilt", "ss_ot", "ss_et"),
        "xicc/xspect.h": ("inst_meas_type", "inst_meas_cond", "icxIllumeType",
                          "icxObserverType", "icxClamping"),
        "spectro/xrga.h": ("xcalpol",),
    }.items():
        declarations += "".join(_enum(sources[path], name) for name in names)
    for path, names in {
        "spectro/inst.h": ("CALIDLEN", "ICOM_MAX_LOC_LEN"),
        "xicc/xspect.h": ("XSPECT_MAX_BANDS", "XSPECT_COPY_INFO", "XSPECT_XWL"),
        "spectro/xrga.h": ("XCALSTD_NEEDED",),
    }.items():
        declarations += "".join(_define(sources[path], name) for name in names)
    declarations += re.search(r"typedef struct \{[^}]*\} xspect;", sources["xicc/xspect.h"])[0]
    declarations += "\n" + re.search(
        r"struct _ipatch \{[^}]*\}; typedef struct _ipatch ipatch;", sources["spectro/inst.h"]
    )[0] + "\n"
    conversion = xrga_conversion(sources["spectro/xrga.c"])
    functions = source[:source.index("#include")]
    functions += _define(source, "SS_REF_CAL_COUNT") + _define(source, "SS_TRANS_CAL_COUNT")
    functions += _function(source, "static void inc_calcount(")
    functions += _function(source, "static void check_calcount(")
    functions += _function(source, READ_SIGNATURE)
    return declarations, conversion, functions


@production_proof
def prove(archive: Path, output: Path, *, compiler: str = "cc",
          source: Path | None = None) -> dict:
    """Run real X-Y and XRGA functions with fake commands and a XYZ call spy."""
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    paths = (SOURCE_PATH, "spectro/xrga.c", "spectro/inst.h", "spectro/insttypes.h",
             "spectro/ss_imp.h", "spectro/xrga.h", "xicc/xspect.h")
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        sources = {path: zipped.read(PREFIX + path).decode() for path in paths}
        licence = zipped.read(PREFIX + "License2.txt")
    original = before_source("ss_xy_metadata", SOURCE_PATH, archive)
    sources[SOURCE_PATH] = original
    patched = production_text(source, SOURCE_PATH)
    production_sources = {path: production_text(source, path) for path in paths}
    output.mkdir(parents=True, exist_ok=False)
    (output / "Argyll-License2.txt").write_bytes(licence)
    (output / "ss.original.c").write_bytes(original.encode())
    (output / "ss.patched.c").write_bytes(patched.encode())
    (output / "ss.patch").write_text("".join(difflib.unified_diff(
        original.splitlines(True), patched.splitlines(True),
        fromfile="a/" + SOURCE_PATH, tofile="b/" + SOURCE_PATH)))
    harness_path = Path(__file__).with_name("ss_xy_metadata_probe.c")
    harness = harness_path.read_text()
    report = {
        "status": "incomplete", "hardware_tested": False, "instrument_runtime_linked": False,
        "archive_sha256": ARCHIVE_SHA256,
        "original_sha256": {path: digest(text.encode()) for path, text in sources.items()},
        "production_support_sha256": {
            path: digest(text.encode()) for path, text in production_sources.items()
            if path != SOURCE_PATH},
        "patched_sha256": {SOURCE_PATH: digest(patched.encode())},
        "patched_function_sha256": {
            READ_SIGNATURE: digest(_function(patched, READ_SIGNATURE).encode())},
        "proof_source_sha256": digest(Path(__file__).read_bytes()),
        "probe_source_sha256": digest(harness.encode()), "results": {}, "commands": [],
        "limitations": [
            "Complete ss_read_xy, calibration counters and XRGA spectral conversion functions.",
            "Fake commands and calibration; no physical table, transport or native runtime.",
            "Spectral-to-XYZ is an explicit call spy, not a scientific XYZ accuracy test.",
            "Retry cases cover first-patch exhaustion and fifth-attempt success at four stages.",
            "Not a general proof of calibration, custom-filter or geometry correctness.",
        ],
    }
    try:
        for variant, source in (("baseline", original), ("patched", patched)):
            folder = output / variant
            folder.mkdir()
            support = sources if variant == "baseline" else production_sources
            declarations, conversion, functions = _extract(source, support)
            for name, content in (("declarations.h", declarations), ("conversion.inc", conversion),
                                  ("functions.inc", functions), ("probe.c", harness)):
                (folder / name).write_text(content)
            binary = folder / ("probe.exe" if platform.system() == "Windows" else "probe")
            command = [cc, "-std=c99", "-DSALONEINSTLIB", "-O1", "-Wall", "-Wextra", "-Werror",
                       f"-DCM_PRODUCTION={int(variant == 'patched')}",
                       "-Wno-unused-parameter", "-Wno-unused-variable",
                       "-Wno-unused-but-set-variable",
                       str(folder / "probe.c"), "-lm", "-o", str(binary)]
            report["commands"].append(command)
            build = subprocess.run(command, text=True, capture_output=True, timeout=60)
            (folder / "build.log").write_text(build.stdout + build.stderr)
            if build.returncode:
                raise RuntimeError(f"SS X-Y proof compilation failed: {folder / 'build.log'}")
            run = subprocess.run([str(binary)], text=True, capture_output=True, timeout=10)
            (folder / "run.log").write_text(run.stdout + run.stderr)
            result = json.loads(run.stdout)
            result.update(returncode=run.returncode, executable_sha256=digest(binary.read_bytes()),
                          extracted_functions_sha256=digest(functions.encode()),
                          conversion_sha256=digest(conversion.encode()))
            report["results"][variant] = result
            if (result["cases"] != 32 or result["checks"] != EXPECTED_CHECKS[variant]
                    or result["groups"] != EXPECTED_GROUPS[variant]):
                raise AssertionError(
                    f"Unexpected SS X-Y {variant} coverage: {result}; {run.stderr}")
            expected = BASELINE_FAILURES if variant == "baseline" else 0
            if (run.returncode != bool(expected) or result["failures"] != expected
                    or (not expected and run.stderr)):
                raise AssertionError(f"Unexpected SS X-Y {variant} result: {result}")
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


# Conversion invokes three XYZ-spy checks once per converted grid. A lost failed
# patch incorrectly reaches the next patch, adding one XYZ-command check per case.
EXPECTED_GROUPS = {
    "baseline": {
        "metadata": {"cases": 24, "checks": 4636, "failures": 2150},
        "retry": {"cases": 8, "checks": 77, "failures": 16},
    },
    "patched": {
        "metadata": {"cases": 24, "checks": 4666, "failures": 0},
        "retry": {"cases": 8, "checks": 73, "failures": 0},
    },
}
EXPECTED_CHECKS = {variant: sum(group["checks"] for group in groups.values())
                   for variant, groups in EXPECTED_GROUPS.items()}
BASELINE_FAILURES = 2166


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prove(args.archive, args.output), indent=2))
