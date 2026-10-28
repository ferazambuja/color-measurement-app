"""Pinned Spyder X2/2024 selection-path repair and inert actual-function proof.

Argyll's spydX2.c remains Graeme W. Gill's GPL-2.0-or-later work. set_disp_type
(spydX2.c:1421-1489) decides whether a Spyder 2024 reads through the firmware's
high-level XYZ command or the low-level path over the inbuilt calibration
(`usehl`, read at :908, :1000 and :1064), but only inside the branch that first
reads an inbuilt matrix (:1425-1452). Selecting an entry already loaded, by
index (spydX2_set_disptype), as the default, as the base of a correction matrix
(set_base_disp_type from spydX2_col_cor_mat, :963) or through a CCMX list entry,
which carries its base's index and never enters that branch (inst.c:1019,1031),
keeps the previous selection's path. When the support mask and display-number
limit leave both high- and low-level indices, a cached reselection reads index 0 on the
low-level path over a calibration slot never loaded, or an unsupported index
through the high-level command.

The repair decides the path from the selected native index on every selection
and applies it wherever the index is applied. Its inputs (is2024 and forcell
from the constructor, :1660-1671; mxdnp1 and dnomask from init, :271-278) are
fixed before any selection, so an index first read on the low-level path stays
loaded and one chosen high-level never needs the read: calibration loading
stays cached, no state is added, and no command, table or arithmetic moves.
"""

from __future__ import annotations

import argparse
import difflib
import io
import json
import platform
import shutil
import subprocess
import zipfile
from pathlib import Path

from native_argyll.proof_inputs import before_source, production_proof, production_text
from native_argyll.spyder_calibration_slots import (
    ARCHIVE_SHA256,
    DRIVER_PATH,
    DRIVER_SHA256,
    PREFIX,
    SOURCE_PATH,
    _extract,
    _function,
    digest,
)

REPAIR_ID = 'recipe:spyder-selection-path'

#: Selector/correction functions, including two internal helpers, from the pinned text.
SELECTORS = ("spydX2_set_disptype", "set_default_disp_type", "set_base_disp_type",
             "spydX2_col_cor_mat")
#: Per variant: failures and the scenarios that failed. The original keeps the
#: previous selection's path on every cached reselection under a partial mask,
#: on both mixed directions, past the firmware's display-number limit, under a
#: CCMX list entry, under spydX2_col_cor_mat and after a failed load is retried.
EXPECTED = {
    "baseline": {"checks": 151, "failures": 7, "failed_scenarios": {
        "mixed_high_low_high", "mixed_low_high_low", "max_display_boundary", "ccmx_list",
        "correction_matrix", "failed_load"}},
    "patched": {"checks": 151, "failures": 0, "failed_scenarios": set()},
}


def _extract_path(header: str, sources: dict[str, str]) -> tuple[str, str]:
    """The slot proof's extraction plus selector/correction functions, definitions before uses."""
    declarations, functions = _extract(header, sources)
    functions += "".join(_function(sources[DRIVER_PATH], name) for name in SELECTORS)
    return declarations, functions


@production_proof
def prove(archive: Path, output: Path, *, source: Path | None = None,
          compiler: str = "cc") -> dict:
    """Drive the original and repaired selectors over the same units and lists.

    Both variants use the repaired seven-slot header, so the selector is the
    only difference. Each unit's support mask and display-number limit are set
    as spydX2_getInstInfo would; the lists are built as inst_creat_disptype_list
    builds them. The USB command is the slot proof's scripted stand-in, with one
    injected transport failure.
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
    header = production_text(source, SOURCE_PATH)
    original = before_source("spyder_selection_path", DRIVER_PATH, archive)
    if digest(original.encode()) != DRIVER_SHA256:
        raise ValueError(f"Spyder selection-path proof requires the exact pinned {DRIVER_PATH}")
    patched = production_text(source, DRIVER_PATH)
    output.mkdir(parents=True, exist_ok=False)
    (output / "Argyll-License2.txt").write_bytes(licence)
    (output / "spydX2.original.c").write_text(original)
    (output / "spydX2.patched.c").write_text(patched)
    (output / "spydX2.c.patch").write_text("".join(difflib.unified_diff(
        original.splitlines(True), patched.splitlines(True),
        fromfile="a/" + DRIVER_PATH, tofile="b/" + DRIVER_PATH)))
    harness = Path(__file__).with_name("spyder_calibration_slots_probe.c").read_text()
    report: dict = {
        "status": "incomplete",
        "hardware_tested": False,
        "instrument_runtime_linked": False,
        "archive_sha256": ARCHIVE_SHA256,
        "original_sha256": {DRIVER_PATH: DRIVER_SHA256},
        "patched_sha256": {DRIVER_PATH: digest(patched.encode())},
        # The build binds this function as compiled; the display-list release that
        # ends the driver's chain binds the whole file.
        "selector_sha256": digest(_function(patched, "set_disp_type").encode()),
        "header_sha256": digest(header.encode()),
        "source_sha256": {name: digest(text.encode()) for name, text in sources.items()},
        "proof_source_sha256": digest(Path(__file__).read_bytes()),
        "probe_source_sha256": digest(harness.encode()),
        "platform": platform.platform(),
        "results": {},
        "commands": [],
        "limitations": [
            "Exact display tables, set_disp_type, spydX2_getCalibration and the four "
            "selector/correction functions over the repaired seven-slot header; "
            "the USB command is a "
            "stand-in returning scripted calibration replies or one injected failure.",
            "The common object base is reduced to a log pointer and the two readiness "
            "flags; logging, error and list-creation helpers are stand-ins. Sizes are "
            "this probe's, not the production ABI.",
            "Support masks and display-number limits are set as the driver's init would "
            "store them; no firmware value or its frequency is established.",
            "The wrong path's consequence is source-traced, not measured: the stale "
            "low-level setup sends the unloaded slot's zero v1 (spydX2.c:382,394). "
            "If setup and the subsequent measurement both succeed (:591-599), the "
            "zero matrix, gain and offset yield zero XYZ (:609-633); an error from "
            "either operation is returned. Echoing zero alone is insufficient.",
            "The selections counter counts only successful select_entry helper calls: "
            "26 of 33 top-level selector/correction calls. Seven direct calls, including "
            "two expected refusals, are not counted there.",
            "Calibration inventory and execution are source-traced, not exercised by "
            "this selector proof. The pinned driver disables mandatory black calibration "
            "with #undef ENABLE_BLACK_CAL (spydX2.c:68,1001-1005).",
            "No hardware evidence, readings, full application acceptance or upstream "
            "maintainer acceptance.",
        ],
    }
    try:
        for variant, driver in (("baseline", original), ("patched", patched)):
            folder = output / variant
            folder.mkdir()
            declarations, functions = _extract_path(header, {**sources, DRIVER_PATH: driver})
            (folder / "declarations.h").write_text(declarations)
            (folder / "functions.inc").write_text(functions)
            source = folder / "probe.c"
            source.write_text(harness)
            binary = folder / ("probe.exe" if platform.system() == "Windows" else "probe")
            command = [cc, "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror",
                       "-Wno-unused-variable", "-Wno-unused-but-set-variable",
                       "-Wno-unused-parameter", "-Wno-missing-field-initializers",
                       "-DSPYDX2_PATH_PROBE", str(source), "-o", str(binary), "-lm"]
            report["commands"].append(command)
            build = subprocess.run(command, text=True, capture_output=True, timeout=60)
            (folder / "build.log").write_text(build.stdout + build.stderr)
            if build.returncode:
                raise RuntimeError(f"Spyder path proof compile failed: {folder / 'build.log'}")
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
                    or (variant == "patched" and run.stderr)):
                raise AssertionError(f"Unexpected Spyder path {variant} result: {result}; "
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
