"""Historical Spyder calibration-slot regression fixture and actual-function proof.

Production composition imports the repaired header from Argyll contributions.
The transform below reproduces the historical failure/repair fixture for proof;
it is not applied to worker build source.

Argyll's spydX2.c and spydX2.h remain Graeme W. Gill's GPL-2.0-or-later work.
The Spyder 2024 display list advertises seven native calibrations, OLED and
Mini-LED at native indices 5 and 6 (spydX2.c:1357-1374), but the object holds
SPYDX2_NOCALIBS = 5 calibration slots (spydX2.h:107,137). Every built-in
selection stores through p->cinfo[ix] (set_disp_type, spydX2.c:1431), and the
low-level path then writes the whole calibration there (spydX2_getCalibration,
:291,319-338), so selecting either of those two writes past the array.

The repair sizes the array for whichever model has more native calibrations. It
changes no command, display table, selection rule or calibration arithmetic, and
rejects nothing the driver advertises.
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

ARCHIVE_SHA256 = "f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f"
PREFIX = "Argyll_V3.5.0/"
SOURCE_PATH = "spectro/spydX2.h"
REPAIR_ID = "spyder-calibration-slots"
DRIVER_PATH = "spectro/spydX2.c"
DRIVER_SHA256 = "0605716f89cdd2986a342245c1e60e901314306cde6dcf63cd0dfb4d37113668"
#: Per variant: checks, failures, failed scenarios and skipped probe groups.
#: Baseline skips four individual selections and the entire repeat sequence;
#: its skipped=5 does not mean five individual out-of-bounds selections.
EXPECTED = {
    "baseline": {"checks": 299, "failures": 2, "failed_scenarios": {"slots"}, "skipped": 5},
    "patched": {"checks": 474, "failures": 0, "failed_scenarios": set(), "skipped": 0},
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _between(text: str, start: str, end: str) -> str:
    """The exact text from `start` through the first `end` after it."""
    if text.count(start) != 1:
        raise ValueError(f"Missing or ambiguous pinned extract: {start.strip()}")
    begin = text.index(start)
    finish = text.index(end, begin) + len(end)
    return text[begin:finish] + "\n"


def _function(text: str, name: str) -> str:
    """One function definition, with a return type on its own preceding line."""
    matches = []
    for match in re.finditer(r"^(?:[A-Za-z_][\w \t]*[ \t*])?" + name + r"\s*\(", text, re.M):
        head = text[match.end():text.index("{", match.end())]
        if ";" not in head:
            matches.append(match)
    if len(matches) != 1:
        raise ValueError(f"Missing or ambiguous pinned function: {name}")
    start = matches[0].start()
    previous = text.rfind("\n", 0, start - 1) + 1
    if re.fullmatch(r"(?:static )?[A-Za-z_]\w*[ \t]*\n", text[previous:start]):
        start = previous
    opening = text.index("{", matches[0].end())
    depth = 0
    for token in re.finditer(
        r'/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]', text[opening:]
    ):
        if token[0] == "{":
            depth += 1
        elif token[0] == "}":
            depth -= 1
            if depth == 0:
                return text[start:opening + token.end()] + "\n"
    raise ValueError(f"Missing pinned function end: {name}")


def _enum(text: str, name: str) -> str:
    matches = [m[0] for m in re.finditer(r"typedef enum\s*\{[^}]*\}\s*\w+\s*;", text)
               if re.search(r"\}\s*" + name + r"\s*;", m[0])]
    if len(matches) != 1:
        raise ValueError(f"Missing or ambiguous pinned enum: {name}")
    return matches[0] + "\n"


def _define(text: str, name: str) -> str:
    matches = re.findall(r"^#define\s+" + name + r"\b[^\n]*", text, re.M)
    if len(matches) != 1:
        raise ValueError(f"Missing or ambiguous pinned constant: {name}")
    return matches[0] + "\n"


def _notice(name: str, text: str) -> str:
    prelude = text[:text.index("#include")] if "#include" in text else text[:text.index("typedef")]
    return (f"\n/* Extracted from {name}; its notice follows. */\n"
            + "\n".join(re.findall(r"/\*[\s\S]*?\*/", prelude)) + "\n")


def _extract(header: str, sources: dict[str, str]) -> tuple[str, str]:
    """Declarations and functions, each copied exactly from the pinned archive."""
    inst, driver = sources["spectro/inst.h"], sources[DRIVER_PATH]
    declarations = _notice(SOURCE_PATH, header)
    for name in ("spectro/inst.h", "spectro/disptechs.h", "numlib/numsup.c", "icc/icc_util.c"):
        declarations += _notice(name, sources[name])
    for name in ("inst_code", "inst_mode", "inst_opt_type", "inst_dtflags"):
        declarations += _enum(inst, name)
    declarations += _enum(sources["spectro/disptechs.h"], "disptech")
    for name in ("INST_DTYPE_SEL_LEN", "INST_DTYPE_DESC_LEN"):
        declarations += _define(inst, name)
    declarations += _between(inst, "typedef struct _inst_disptypesel {", "} inst_disptypesel;")
    declarations += _define(sources["spectro/icoms.h"], "ICOM_OK")
    for name in ("SPYDX2_OK", "SPYDX2_CIX_MISMATCH"):
        declarations += _define(header, name)
    # The object as the variant declares it: the calibration array is the defect.
    declarations += _between(header, "/* Extra native calibration info (X2 only) */",
                             "}; typedef struct _spydX2 spydX2;")
    numsup, icc = sources["numlib/numsup.c"], sources["icc/icc_util.c"]
    functions = "".join(_function(numsup, name) for name in (
        "IEEE754todouble", "doubletoIEEE754", "read_ORD8", "read_ORD16_be", "read_ORD32_le",
        "write_ORD8", "write_ORD16_be", "write_ORD32_le"))
    functions += _function(icc, "icmSetUnity3x3") + _function(icc, "icmCpy3x3")
    for table in ("spydX2_disptypesel", "spyd2024_disptypesel"):
        functions += _between(driver, f"static inst_disptypesel {table}[", "\n};")
    functions += _function(driver, "spydX2_getCalibration") + _function(driver, "set_disp_type")
    return declarations, functions


@production_proof
def prove(archive: Path, output: Path, *, source: Path | None = None,
          compiler: str = "cc") -> dict:
    """Build the original and repaired object around the actual selection code.

    The display tables, set_disp_type and spydX2_getCalibration are the pinned
    upstream text. The USB command returns a scripted calibration reply so the
    low-level path writes every calibration field. The harness also replaces the
    common object base, logging and error helpers; it is not the production ABI.
    """
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    names = (DRIVER_PATH, "spectro/inst.h", "spectro/disptechs.h", "spectro/icoms.h",
             "numlib/numsup.c", "icc/icc_util.c")
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        sources = {name: zipped.read(PREFIX + name).decode() for name in names}
        licence = zipped.read(PREFIX + "License2.txt")
    original = before_source("spyder_calibration_slots", SOURCE_PATH, archive)
    if digest(sources[DRIVER_PATH].encode()) != DRIVER_SHA256:
        raise ValueError(f"Spyder calibration-slot proof requires the exact pinned {DRIVER_PATH}")
    patched = production_text(source, SOURCE_PATH)
    output.mkdir(parents=True, exist_ok=False)
    (output / "Argyll-License2.txt").write_bytes(licence)
    (output / "spydX2.original.h").write_text(original)
    (output / "spydX2.patched.h").write_text(patched)
    (output / "spydX2.h.patch").write_text("".join(difflib.unified_diff(
        original.splitlines(True), patched.splitlines(True),
        fromfile="a/" + SOURCE_PATH, tofile="b/" + SOURCE_PATH)))
    harness = Path(__file__).with_name("spyder_calibration_slots_probe.c").read_text()
    report = {
        "status": "incomplete",
        "hardware_tested": False,
        "instrument_runtime_linked": False,
        "archive_sha256": ARCHIVE_SHA256,
        "original_sha256": {SOURCE_PATH: digest(original.encode())},
        "patched_sha256": {SOURCE_PATH: digest(patched.encode())},
        "source_sha256": {name: digest(text.encode()) for name, text in sources.items()},
        "proof_source_sha256": digest(Path(__file__).read_bytes()),
        "probe_source_sha256": digest(harness.encode()),
        "platform": platform.platform(),
        "results": {},
        "commands": [],
        "limitations": [
            "Exact display tables, set_disp_type and spydX2_getCalibration; the USB "
            "command is a stand-in returning scripted calibration replies.",
            "The common object base is reduced to a log pointer; logging and error "
            "helpers are stand-ins. Object sizes/offsets describe this probe, not "
            "the production instrument ABI.",
            "Baseline skips four individual out-of-bounds selections and the whole "
            "five-selection repeat sequence; skipped counts groups, not selections.",
            "Expected identity correction is checked; black-calibration state and "
            "the trailing guard remain unchanged.",
            "Reselection covers the uniformly low-level path only; mixed high/low "
            "cached reselection is not established by this slot proof.",
            "No hardware evidence, readings, full application acceptance or upstream "
            "maintainer acceptance.",
        ],
    }
    try:
        for variant, header in (("baseline", original), ("patched", patched)):
            folder = output / variant
            folder.mkdir()
            declarations, functions = _extract(header, sources)
            (folder / "declarations.h").write_text(declarations)
            (folder / "functions.inc").write_text(functions)
            source = folder / "probe.c"
            source.write_text(harness)
            binary = folder / ("probe.exe" if platform.system() == "Windows" else "probe")
            command = [cc, "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror",
                       "-Wno-unused-variable", "-Wno-unused-but-set-variable",
                       "-Wno-unused-parameter", "-Wno-missing-field-initializers",
                       str(source), "-o", str(binary), "-lm"]
            report["commands"].append(command)
            build = subprocess.run(command, text=True, capture_output=True, timeout=60)
            (folder / "build.log").write_text(build.stdout + build.stderr)
            if build.returncode:
                raise RuntimeError(f"Spyder slot proof compile failed: {folder / 'build.log'}")
            run = subprocess.run([str(binary)], text=True, capture_output=True, timeout=10)
            (folder / "run.log").write_text(run.stdout + run.stderr)
            result = json.loads(run.stdout)
            result.update(returncode=run.returncode, executable_sha256=digest(binary.read_bytes()),
                          extracted_functions_sha256=digest(functions.encode()))
            report["results"][variant] = result
            expected = EXPECTED[variant]
            failed = set(filter(None, result["failed_scenarios"].split(",")))
            if (run.returncode != (1 if expected["failures"] else 0)
                    or result["checks"] != expected["checks"]
                    or result["failures"] != expected["failures"]
                    or failed != expected["failed_scenarios"]
                    or result["skipped"] != expected["skipped"]
                    or (variant == "patched" and run.stderr)):
                raise AssertionError(f"Unexpected Spyder slot {variant} result: {result}; "
                                     f"{run.stderr}")
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compiler", default="cc")
    args = parser.parse_args()
    print(json.dumps(prove(args.archive, args.output, compiler=args.compiler), indent=2))
