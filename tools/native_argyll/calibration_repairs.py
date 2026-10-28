"""Pinned ArgyllCMS 3.5.0 calibration control-flow repairs and inert proof.

The three driver sources remain Graeme W. Gill's GPL-2.0-or-later work.
Local changes repair DTP41 acknowledgement, DTP92 completion bookkeeping and
Specbos error cleanup. They introduce no commands, retries or calibration bypass.
Executed extracts use scripted command results and lock counters, not transports.
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

REPAIR_ID = 'recipe:calibration-repairs'

ARCHIVE_SHA256 = "f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f"
PREFIX = "Argyll_V3.5.0/"
SOURCE_SHA256 = {
    "spectro/dtp41.c": "7de062ca76cc153de32b8fff0c031d12fd9f7918882993a201ae72b740c4220e",
    "spectro/dtp92.c": "d717cbd8af5fbd2d3d1954cd95ae4825749ac11e7a0dc6dd1a2df1bd607b1456",
    "spectro/specbos.c": "c7e17ec2634d257385ce1b5075b579ed735264fba21e745d7d2d354590f33438",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


PATHS = tuple(SOURCE_SHA256)
EXPECTED_CHECKS = {"dtp41": 52, "dtp92": 109, "specbos": 400}
BASELINE_FAILURES = {
    "dtp41": (7, {"transmission_ack", "transmission_not_requested"}),
    "dtp92": (
        31,
        {
            "offset_completion",
            "ratio_completion",
            "retry_completion",
            "combined_partial_and_completion",
        },
    ),
    "specbos": (
        40,
        {
            "read_average_unlock",
            "calibration_initial_average_cleanup",
            "calibration_read_average_unlock",
            "calibration_restore_average_cleanup",
        },
    ),
}


def _function(text: str, name: str) -> str:
    matches = list(re.finditer(r"^(?:static )?inst_code\s+" + name + r"\s*\(", text, re.M))
    if len(matches) != 1:
        raise ValueError(f"Missing or ambiguous pinned function: {name}")
    start = matches[0].start()
    opening = text.index("{", matches[0].end())
    # Ignore comments and string/character literals while balancing braces.
    depth = 0
    for token in re.finditer(
        r'/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]', text[opening:]
    ):
        if token[0] == "{":
            depth += 1
        elif token[0] == "}":
            depth -= 1
            if depth == 0:
                return text[start : opening + token.end()] + "\n"
    raise ValueError(f"Missing pinned function end: {name}")


def _enum(text: str, name: str) -> str:
    matches = [
        m[0]
        for m in re.finditer(r"typedef enum\s*\{[^}]*\}\s*\w+\s*;", text)
        if re.search(r"\}\s*" + name + r"\s*;", m[0])
    ]
    if len(matches) != 1:
        raise ValueError(f"Missing or ambiguous pinned enum: {name}")
    return matches[0] + "\n"


def _define(text: str, name: str) -> str:
    matches = re.findall(r"^#define\s+" + name + r"\s+[^\n]+", text, re.M)
    if len(matches) != 1:
        raise ValueError(f"Missing or ambiguous pinned constant: {name}")
    return matches[0] + "\n"


def _extract(driver: str, source: str, headers: dict[str, str]) -> tuple[str, str]:
    declarations = source[: source.index("#include")]
    for name, header in headers.items():
        declarations += f"\n/* Extracts from {name}; original notice follows. */\n"
        prelude = (
            header[: header.index("#include")]
            if "#include" in header
            else header[: header.index("typedef")]
        )
        declarations += "\n".join(re.findall(r"/\*[\s\S]*?\*/", prelude)) + "\n"
    inst = headers["spectro/inst.h"]
    for name in (
        "inst_code",
        "inst_mode",
        "inst_cal_type",
        "inst_cal_cond",
        "inst_calc_id_type",
        "inst_ui_purp",
    ):
        declarations += _enum(inst, name)
    declarations += _enum(headers["spectro/insttypes.h"], "devType")
    declarations += _define(inst, "CALIDLEN")
    if driver == "dtp92":
        declarations += _define(source, "MAX_RD_SIZE")
        for name in ("DTP92_TOO_MUCH_LIGHT", "DTP92_NOT_ENOUGH_LIGHT"):
            declarations += _define(headers["spectro/dtp92.h"], name)
    if driver == "specbos":
        for name in (
            "MAX_MES_SIZE",
            "MAX_RD_SIZE",
            "DEFAULT_NAV",
            "DEFAULT_TRANS_NAV",
            "EXPL1211AVG",
        ):
            declarations += _define(source, name)
        for name in ("SPECBOS_SPOS_AMB", "SPECBOS_SPOS_EMIS"):
            declarations += _define(headers["spectro/specbos.h"], name)
        declarations += (
            "enum {\n"
            + "\n".join(
                re.search(r"^\s*" + name + r"\s*=.*$", inst, re.M)[0]
                for name in ("inst_opt_trig_prog", "inst_opt_trig_user")
            )
            + "\n};\n"
        )
        read = _function(source, "specbos_read_sample")
        # Exact prefix, then explicitly synthetic success. Measurement/parsing is
        # excluded; this proves only lock ownership on the real early exits.
        read = read[: read.index("\n\t/* Adjust timeout to account for averaging */")]
        read += "\n/* TEST ONLY: unexecuted measurement tail replaced by synthetic success. */\n"
        read += "if (val) val->sp.spec_n = 3;\namutex_unlock(p->lock);\nreturn inst_ok;\n}\n"
        functions = _function(source, "set_average") + read
    else:
        functions = ""
    functions += _function(source, driver + "_get_n_a_cals")
    functions += _function(source, driver + "_calibrate")
    return declarations, functions


@production_proof
def prove(archive: Path, output: Path, *, compiler: str = "cc",
          source: Path | None = None) -> dict:
    """Compare frozen predecessors and production functions without a transport."""
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        originals = {path: before_source("calibration_repairs", path, archive) for path in PATHS}
        patched = {path: production_text(source, path) for path in PATHS}
        header_names = (
            "spectro/inst.h",
            "spectro/insttypes.h",
            "spectro/dtp92.h",
            "spectro/specbos.h",
        )
        headers = {path: zipped.read(PREFIX + path).decode() for path in header_names}
        licence = zipped.read(PREFIX + "License2.txt")
    production_headers = {path: production_text(source, path) for path in header_names}
    output.mkdir(parents=True, exist_ok=False)
    (output / "Argyll-License2.txt").write_bytes(licence)
    for path, header in headers.items():
        retained = output / "headers" / path
        retained.parent.mkdir(parents=True, exist_ok=True)
        retained.write_text(header)
    harness = Path(__file__).with_name("calibration_repairs_probe.c").read_text()
    report = {
        "status": "incomplete",
        "hardware_tested": False,
        "instrument_runtime_linked": False,
        "archive_sha256": ARCHIVE_SHA256,
        "original_sha256": {path: digest(text.encode()) for path, text in originals.items()},
        "patched_sha256": {path: digest(text.encode()) for path, text in patched.items()},
        "header_sha256": {path: digest(text.encode()) for path, text in headers.items()},
        "production_header_sha256": {
            path: digest(text.encode()) for path, text in production_headers.items()},
        "proof_source_sha256": digest(Path(__file__).read_bytes()),
        "probe_source_sha256": digest(harness.encode()),
        "platform": platform.platform(),
        "results": {},
        "commands": [],
        "limitations": [
            "Exact DTP41/DTP92 calibration and need-assessment; scripted command results.",
            "Exact Specbos calibration, assessment and averaging functions plus read prefix only.",
            "Specbos successful measurement tail is synthetic; no spectrum arithmetic is tested.",
            "Minimal structural stand-ins and lock counters; no threads, mutex APIs or transport.",
            "No hardware evidence, full application acceptance or upstream maintainer acceptance.",
        ],
    }
    try:
        for path in PATHS:
            driver = Path(path).stem
            (output / (driver + ".original.c")).write_bytes(originals[path].encode())
            (output / (driver + ".patched.c")).write_bytes(patched[path].encode())
            diff = "".join(
                difflib.unified_diff(
                    originals[path].splitlines(True),
                    patched[path].splitlines(True),
                    fromfile="a/" + path,
                    tofile="b/" + path,
                )
            )
            (output / (driver + ".patch")).write_text(diff)
            report["results"][driver] = {}
            for variant, sources in (("baseline", originals), ("patched", patched)):
                folder = output / driver / variant
                folder.mkdir(parents=True)
                support = headers if variant == "baseline" else production_headers
                declarations, functions = _extract(driver, sources[path], support)
                (folder / "calibration_declarations.h").write_text(declarations)
                (folder / "calibration_functions.inc").write_text(functions)
                source = folder / "probe.c"
                source.write_text(harness)
                binary = folder / ("probe.exe" if platform.system() == "Windows" else "probe")
                command = [
                    cc,
                    "-std=c99",
                    "-O1",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-Wno-unused-variable",
                    "-Wno-unused-but-set-variable",
                    "-Wno-unused-parameter",
                    "-D" + "CM_" + driver.upper(),
                    str(source),
                    "-o",
                    str(binary),
                ]
                if driver == "dtp41":
                    # The original defect provokes this diagnostic; allow it so
                    # a regressed patch must fail the behavioral proof as well.
                    command.insert(6, "-Wno-error=misleading-indentation")
                report["commands"].append(command)
                build = subprocess.run(command, text=True, capture_output=True, timeout=60)
                (folder / "build.log").write_text(build.stdout + build.stderr)
                if build.returncode:
                    raise RuntimeError(f"Calibration proof compile failed: {folder / 'build.log'}")
                run = subprocess.run([str(binary)], text=True, capture_output=True, timeout=10)
                (folder / "run.log").write_text(run.stdout + run.stderr)
                result = json.loads(run.stdout)
                result.update(
                    returncode=run.returncode,
                    executable_sha256=digest(binary.read_bytes()),
                    extracted_functions_sha256=digest(functions.encode()),
                )
                report["results"][driver][variant] = result
                expected_return = 1 if variant == "baseline" else 0
                expected_failures, expected_scenarios = (
                    BASELINE_FAILURES[driver] if variant == "baseline" else (0, set())
                )
                failed_scenarios = set(filter(None, result["failed_scenarios"].split(",")))
                if (
                    run.stderr
                    or run.returncode != expected_return
                    or result["checks"] != EXPECTED_CHECKS[driver]
                    or result["failures"] != expected_failures
                    or failed_scenarios != expected_scenarios
                ):
                    raise AssertionError(
                        f"Unexpected {driver} {variant} result: {result}; {run.stderr}"
                    )
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
