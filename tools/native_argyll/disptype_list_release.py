"""Pinned owned display-list release for three destructors, with an inert proof.

Argyll's kleink10.c, spydX.c and spydX2.c remain Graeme W. Gill's GPL-2.0-or-later
work. Ten drivers build their display-type list with inst_creat_disptype_list
into p->dtlist and p->ndtlist; the header says the instrument manages that
storage (inst.h:832-833) and the helper's own comment says the driver frees it
(inst.c:840-841). Seven destructors do, with inst_del_disptype_list between the
transport release and the virtual destructor (huey.c:1430, spyd2.c:3496 and
five more). Three never do: k10_del (kleink10.c:2475-2484), spydX_del
(spydX.c:1071-1085) and spydX2_del (spydX2.c:1159-1173). Every successful
init_inst creates the list (kleink10.c:519, spydX.c:747, spydX2.c:819), so each
instance leaks one list array plus the path of every matching installed CCMX.
Argyll's callers only borrow the list (instappsup.c:482, 529, 562), and the
only other release is the helper's own, on recreation (inst.c:927-930), so a
release in the destructor cannot double free inside upstream.

The repair adds the convention's one line to each destructor and changes nothing
else: no list, selection, correction, calibration or command moves. Borrowed
static tables (smcube.c, specbos.c) are not touched; the release belongs to the
three owners only.
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

from native_argyll import sanitizers
from native_argyll.proof_inputs import before_source, production_proof, production_text
from native_argyll.spyder_calibration_slots import (
    ARCHIVE_SHA256,
    PREFIX,
    _between,
    _define,
    _enum,
    _extract,
    _function,
    _notice,
    digest,
)
from native_argyll.spyder_selection_path import (
    SELECTORS as SPYDX2_SELECTORS,
)

REPAIR_ID = 'recipe:disptype-list-release'

KLEINK10_PATH = "spectro/kleink10.c"
SPYDX_PATH = "spectro/spydX.c"
SPYDX2_PATH = "spectro/spydX2.c"
DESTRUCTORS = {KLEINK10_PATH: "k10_del", SPYDX_PATH: "spydX_del", SPYDX2_PATH: "spydX2_del"}
DRIVERS = {KLEINK10_PATH: "kleink10", SPYDX_PATH: "spydX", SPYDX2_PATH: "spydX2"}
#: Scenarios whose list the original destructor leaves allocated, per driver.
LEAKING = ("init_del", "get_del", "repeat", "recreate", "ccmx")
#: Live blocks the original leaves after del: the list array, plus one moved
#: CCMX path string per matching installed correction in the ccmx scenario.
LEFT = {"del_only": 0, "init_del": 1, "get_del": 1, "repeat": 1, "recreate": 1, "ccmx": 8,
        "programmed": 1}
EXPECTED = {
    "baseline": {
        "kleink10": {"checks": 79, "failures": 6, "failed": set(LEAKING) | {"programmed"}},
        "spydX": {"checks": 71, "failures": 5, "failed": set(LEAKING)},
        "spydX2": {"checks": 71, "failures": 5, "failed": set(LEAKING)},
    },
    "patched": {
        "kleink10": {"checks": 79, "failures": 0, "failed": set()},
        "spydX": {"checks": 71, "failures": 0, "failed": set()},
        "spydX2": {"checks": 71, "failures": 0, "failed": set()},
    },
}


def _list_helpers(sources: dict[str, str]) -> str:
    """The exact list constructor, growth, destructor and selector assignment."""
    functions = _function(sources["spectro/disptechs.c"], "disptechs_set_sel")
    for name in ("inst_del_disptype_list", "expand_dlist", "inst_creat_disptype_list"):
        functions += _function(sources["spectro/inst.c"], name)
    return functions


def _helper_notices(sources: dict[str, str]) -> str:
    return "".join(_notice(name, sources[name])
                   for name in ("spectro/inst.c", "spectro/disptechs.c"))


def _shared(sources: dict[str, str]) -> tuple[str, str]:
    """Declarations the Klein and SpyderX units need, and the exact helpers they call."""
    inst = sources["spectro/inst.h"]
    declarations = ""
    for name in ("spectro/inst.h", "spectro/disptechs.h", "icc/icc_util.c"):
        declarations += _notice(name, sources[name])
    declarations += _helper_notices(sources)
    for name in ("inst_code", "inst_mode", "inst_opt_type", "inst_dtflags"):
        declarations += _enum(inst, name)
    declarations += _enum(sources["spectro/disptechs.h"], "disptech")
    for name in ("INST_DTYPE_SEL_LEN", "INST_DTYPE_DESC_LEN"):
        declarations += _define(inst, name)
    declarations += _between(inst, "typedef struct _inst_disptypesel {", "} inst_disptypesel;")
    icc = sources["icc/icc_util.c"]
    functions = _function(icc, "icmSetUnity3x3") + _function(icc, "icmCpy3x3")
    return declarations, functions + _list_helpers(sources)


def _extract_driver(path: str, sources: dict[str, str]) -> tuple[str, str]:
    """Declarations and exact functions for one driver, definitions before uses."""
    driver = sources[path]
    if path == SPYDX2_PATH:
        # The slot proof's extraction: the repaired header, enums, tables, exact
        # spydX2_getCalibration and set_disp_type; then the list helpers, the
        # selectors, the getter and the destructor.
        header = sources["spectro/spydX2.h"]
        declarations, functions = _extract(header, {**sources, path: driver})
        if ("IEEE754_special_value(" in functions
                and "static double IEEE754_special_value(" not in functions):
            functions = _function(sources["numlib/numsup.c"], "IEEE754_special_value") + functions
        declarations += _helper_notices(sources)
        functions += _list_helpers(sources)
        for name in (*SPYDX2_SELECTORS, "spydX2_get_disptypesel", "spydX2_del"):
            functions += _function(driver, name)
        return declarations, functions
    declarations, functions = _shared(sources)
    if path == KLEINK10_PATH:
        header = sources["spectro/kleink10.h"]
        declarations += _notice("spectro/kleink10.h", header) + _notice(path, driver)
        declarations += _enum(header, "k10_dtype")
        declarations += _between(header, "/* Klein K10 communication object */",
                                 "}; typedef struct _kleink10 kleink10;")
        functions += _between(driver, "static inst_disptypesel k10_disptypesel[98] = {", "\n};")
        names = ("set_disp_type", "set_default_disp_type", "k10_set_disptype",
                 "k10_col_cor_mat", "k10_get_disptypesel", "k10_del")
    else:
        header = sources["spectro/spydX.h"]
        declarations += _notice("spectro/spydX.h", header) + _notice(path, driver)
        declarations += _between(header, "/* Extra native calibration info */",
                                 "}; typedef struct _spydX spydX;")
        functions += _between(driver, "static inst_disptypesel spydX_disptypesel[", "\n};")
        names = ("set_disp_type", "set_default_disp_type", "spydX_set_disptype",
                 "set_base_disp_type", "spydX_col_cor_mat", "spydX_get_disptypesel", "spydX_del")
    for name in names:
        functions += _function(driver, name)
    return declarations, functions


def _validate_result(variant: str, driver: str, result: dict, stderr: str, returncode: int) -> None:
    """Check one actual display-list observation, including the baseline leak."""
    expected = EXPECTED[variant][driver]
    failed = set(filter(None, result["failed_scenarios"].split(",")))
    left = {name: 0 for name in result["left"]} if variant == "patched" else {
        name: LEFT[name] for name in result["left"]}
    if (returncode != (1 if expected["failures"] else 0)
            or result["driver"] != driver
            or result["checks"] != expected["checks"]
            or result["failures"] != expected["failures"]
            or failed != expected["failed"]
            or result["left"] != left
            or result["untracked_frees"] != 0
            or (variant == "patched" and stderr)):
        raise AssertionError(f"Unexpected display-list release {variant}/{driver} "
                             f"result: {result}; {stderr}")


def _validate_state_pair(driver: str, before: dict, after: dict) -> None:
    """Keep selection state and calibration command counts unchanged."""
    if before["commands"] != after["commands"]:
        raise AssertionError(f"Display-list release changed {driver} command count")
    if before["state"] != after["state"]:
        raise AssertionError(f"Display-list release changed {driver} selection state: "
                             f"{before['state']} != {after['state']}")

@production_proof
def prove(archive: Path, output: Path, *, compiler: str = "cc", sanitize: bool = False,
          source: Path | None = None) -> dict:
    """Create, reuse, recreate, correct and delete lists through the exact functions.

    Each driver's list helpers, selectors, getter and destructor are the pinned
    text, compiled over a tracked allocator, so what the destructor leaves
    allocated is counted, not inferred. Installed CCMX files are a scripted
    list; calibration reads are stand-ins (the Spyder X2 read is exact over the
    slot proof's scripted command). The transport and virtual destructor are
    counted stand-ins.
    """
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    names = (KLEINK10_PATH, SPYDX_PATH, SPYDX2_PATH, "spectro/kleink10.h", "spectro/spydX.h",
             "spectro/spydX2.h", "spectro/inst.h", "spectro/inst.c", "spectro/disptechs.h",
             "spectro/disptechs.c", "spectro/icoms.h", "numlib/numsup.c", "icc/icc_util.c")
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        sources = {name: zipped.read(PREFIX + name).decode() for name in names}
        licence = zipped.read(PREFIX + "License2.txt")
    # Frozen driver predecessors retain the established selection prerequisites.
    # The current header's six calibration slots are common to both variants.
    inputs = dict(sources)
    inputs.update({path: before_source("disptype_list_release", path, archive)
                   for path in DRIVERS})
    inputs["spectro/spydX2.h"] = production_text(source, "spectro/spydX2.h")
    production_sources = {path: production_text(source, path) for path in names}
    patched = {path: production_sources[path] for path in DRIVERS}
    output.mkdir(parents=True, exist_ok=False)
    (output / "Argyll-License2.txt").write_bytes(licence)
    for path in DRIVERS:
        stem = Path(path).name
        (output / f"{stem}.original").write_text(inputs[path])
        (output / f"{stem}.patched").write_text(patched[path])
        (output / f"{stem}.patch").write_text("".join(difflib.unified_diff(
            inputs[path].splitlines(True), patched[path].splitlines(True),
            fromfile="a/" + path, tofile="b/" + path)))
    harness = Path(__file__).with_name("disptype_list_release_probe.c").read_text()
    report: dict = {
        "status": "incomplete",
        "hardware_tested": False,
        "instrument_runtime_linked": False,
        "sanitize": sanitize,
        "archive_sha256": ARCHIVE_SHA256,
        "input_sha256": {path: digest(text.encode()) for path, text in inputs.items()
                         if path in DRIVERS},
        "patched_sha256": {path: digest(text.encode()) for path, text in patched.items()},
        "source_sha256": {name: digest(text.encode()) for name, text in sources.items()},
        "production_support_sha256": {
            name: digest(text.encode()) for name, text in production_sources.items()
            if name not in DRIVERS},
        "common_spyder_header_sha256": digest(inputs["spectro/spydX2.h"].encode()),
        "proof_source_sha256": digest(Path(__file__).read_bytes()),
        "probe_source_sha256": digest(harness.encode()),
        "platform": platform.platform(),
        "results": {},
        "commands": [],
        "limitations": [
            "Exact inst_creat_disptype_list, expand_dlist, inst_del_disptype_list, "
            "disptechs_set_sel, each driver's display tables, set_disp_type, default and "
            "indexed selection, base selection and correction where the driver has them, "
            "getter and destructor. The Spyder X2 calibration read is exact over a "
            "scripted command; the Klein and SpyderX calibration reads are stand-ins.",
            "Installed CCMX files are a scripted list of seven matching and three "
            "non-matching entries; no file system is read. CCSS lists are not requested "
            "by these drivers and the stand-in returns none.",
            "The common object base is reduced to the log, transport, type, readiness "
            "and virtual-destructor members; the transport, mutex and virtual destructor "
            "are counted stand-ins. Sizes are this probe's, not the production ABI.",
            "Allocation is counted by wrapping malloc, calloc, realloc, free and strdup "
            "in the extracted unit; what a destructor leaves allocated is the count of "
            "live blocks, not a platform leak detector's report.",
            "The Spyder X2/2024 driver runs as the X2 model: its two models share the "
            "destructor, list fields and getter under proof.",
            "No hardware evidence, readings, full application acceptance or upstream "
            "maintainer acceptance.",
        ],
    }
    try:
        for variant in ("baseline", "patched"):
            report["results"][variant] = {}
            for path, driver in DRIVERS.items():
                folder = output / variant / driver
                folder.mkdir(parents=True)
                text = patched[path] if variant == "patched" else inputs[path]
                variant_sources = production_sources if variant == "patched" else inputs
                declarations, functions = _extract_driver(path, {**variant_sources, path: text})
                (folder / "declarations.h").write_text(declarations)
                (folder / "functions.inc").write_text(functions)
                source = folder / "probe.c"
                source.write_text(harness)
                binary = folder / ("probe.exe" if platform.system() == "Windows" else "probe")
                command = [cc, "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror",
                           "-Wno-unused-variable", "-Wno-unused-but-set-variable",
                           "-Wno-unused-parameter", "-Wno-missing-field-initializers",
                           "-Wno-char-subscripts", f"-DCM_{driver.upper()}",
                           *sanitizers.flags(sanitize), *(["-O0", "-g"] if sanitize else []),
                           str(source), "-o", str(binary), "-lm"]
                report["commands"].append(command)
                build = subprocess.run(command, text=True, capture_output=True, timeout=120)
                (folder / "build.log").write_text(build.stdout + build.stderr)
                if build.returncode:
                    raise RuntimeError(
                        f"Display-list release proof compile failed: {folder / 'build.log'}")
                run = subprocess.run([str(binary)], text=True, capture_output=True, timeout=30)
                (folder / "run.log").write_text(run.stdout + run.stderr)
                sanitizers.refuse(run.stderr, f"{variant}/{driver}")
                result = json.loads(run.stdout)
                result.update(returncode=run.returncode,
                              executable_sha256=digest(binary.read_bytes()),
                              extracted_functions_sha256=digest(functions.encode()))
                report["results"][variant][driver] = result
                _validate_result(variant, driver, result, run.stderr, run.returncode)
        # Compare the recorded selection summary and calibration command counts.
        # This is not an exhaustive comparison of the driver or list contents.
        for driver in DRIVERS.values():
            _validate_state_pair(driver, report["results"]["baseline"][driver],
                                 report["results"]["patched"][driver])
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compiler", default="cc")
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prove(args.archive, args.output, compiler=args.compiler,
                           sanitize=args.sanitize), indent=2))
