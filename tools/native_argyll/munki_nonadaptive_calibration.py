"""Pinned ColorMunki nonadaptive calibration repair and inert actual-function proof.

Argyll's munki_imp.c remains Graeme W. Gill's GPL-2.0-or-later work.
munki_imp_calibrate's display integration-time selection (munki_imp.c:1646-1710)
probes the display white and switches to a shorter exposure when the probe
saturates or its returned scale is below 1. munki_whitemeasure writes that
scale only on success (:3976; every error return precedes it), yet the
selection consults it after any other error (:1676, :1689), so a failed second
probe decides from the first probe's scale, and a first-probe failure decides
from a value nothing wrote. A forced re-selection also undoes the previous swap
(:1659-1667) without clearing `done_dintsel`, so a failed re-selection changes
the exposure and dark bank while `get_n_a_cals` (:988) omits the need to
repeat integration-time selection. The white-reference calibration logs the same
scale before checking the probe's result (:1538), an evaluated argument either way.

The repair consults the scale only after a successful probe, clears the
selection's completion where its swap is undone, and logs the white scale once
it is written. The saturation fallback, the error returns, the paired
exposure/dark-bank swaps and the mode restoration are unchanged. Whether a
selection that saturates twice should complete on the last bank is a policy
question left as upstream has it.
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
from native_argyll.legacy_nonadaptive_calibration import ARCHIVE_SHA256, PREFIX, digest
from native_argyll.legacy_nonadaptive_calibration import _function as _signature_function
from native_argyll.munki_scan_margin import SOURCE_PATH, SOURCE_SHA256
from native_argyll.proof_inputs import (
    before_source,
    production_proof,
    production_support,
    production_text,
)
from native_argyll.spyder_calibration_slots import _function

REPAIR_ID = 'recipe:munki-nonadaptive-calibration'

CALIBRATE = "munki_imp_calibrate"
# Both proof variants observe debug-call reachability without evaluating its
# variadic arguments. In particular, the original R1 path passes an unwritten
# scale; a variadic function stub would still evaluate it before entry.
DEBUG_OBSERVER = (
    "/* Proof-only observation; upstream debug values are not evaluated. */\n"
    "static void cm_observe_debug(const char *fmt);\n"
    "#define a1logd(log, level, fmt, ...) cm_observe_debug(fmt)\n"
)
LOGGING_SCOPE = (
    "Both variants intercept a1logd with a format-only macro before argument "
    "evaluation. This proves debug-call reachability, not evaluation or "
    "formatting of upstream debug values. A side-effect argument checks the "
    "interception; production source and its patch are unchanged."
)
#: The upstream functions the probe compiles, in file order; everything else it
#: needs is a scripted stand-in or a tripwire (munki_nonadaptive_probe.c).
FUNCTIONS = (
    "add_munkiimp", "buf2ushort", "munki_imp_set_mode", "munki_imp_get_n_a_cals",
    "munki_imp_calibrate", "munki_imp_measure", "munki_dark_measure_1",
    "munki_dark_measure_2", "munki_dark_measure", "munki_whitemeasure",
    "munki_read_patches_1", "munki_trigger_one_measure", "munki_sens_to_raw",
    "munki_comp_nummeas", "munki_comp_ru_nummeas",
)
#: numsup's vector and matrix allocators, by their exact signature lines.
ALLOCATORS = ("double *dvectorz(\n", "void free_dvector(\n", "double **dmatrix(\n",
              "double **dmatrixz(\n", "void free_dmatrix(\n")
#: Checks the original fails, by "CASE what": the stale swap to the shortest
#: exposure after a failed second probe (first selections F2*, forced F3/F4/F6
#: and the second display mode of an all-mode pass A2), the completion a failed
#: forced re-selection keeps (F3-F6), and the white scale logged before its
#: probe's failure is checked (R1). The repaired text fails none, and its
#: first-probe-failure cases are deterministic.
_STALE_BANK = ("selected dispswap", "current dark bank (by allocation identity)",
               "current exposure s->inttime", "read used dark bank", "read exposure")
_STALE_COMPLETION = ("done_dintsel", "get_n_a_cals reports int-time selection needed")
BASELINE_FAILURES = frozenset(
    [f"{case}: {what}" for case in ("F2", "F2r", "F2i", "F3", "F4", "F6") for what in _STALE_BANK]
    + [f"{case}: {what}" for case in ("F3", "F4", "F5", "F6") for what in _STALE_COMPLETION]
    + ["A2: mk_tele_spot_na stays at the bank its valid scale chose",
       "R1: scale not logged after the failed probe"])
EXPECTED = {
    "baseline": {"checks": 416, "failures": 40, "failed": BASELINE_FAILURES},
    "patched": {"checks": 416, "failures": 0, "failed": frozenset()},
    "repaired_only": {"checks": 59, "failures": 0, "failed": frozenset()},
}


def _between(text: str, start: str, end: str) -> str:
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError(f"Missing or ambiguous pinned extract: {start.strip()[:40]!r}")
    begin = text.index(start)
    return text[begin:text.index(end, begin)] + "\n"


def extracted(driver: str, numsup: str) -> str:
    """The driver's preamble and the functions the probe drives, exactly as pinned.

    Each function carries a #line directive, so diagnostics cite the driver's
    own line in the text compiled (original or repaired). DEBUG_OBSERVER is
    inserted after the declarations, before those otherwise exact functions.
    """
    parts = [_between(driver, "/* \n * Argyll Color Management System\n",
                      "munki_code munki_touch_calibration(munki *p);")]
    parts.append(DEBUG_OBSERVER)
    for signature in ALLOCATORS:
        parts.append(_signature_function(numsup, signature))
    for name in FUNCTIONS:
        body = _function(driver, name)
        line = driver[:driver.index(body)].count("\n") + 1
        parts.append(f'#line {line} "Argyll-3.5.0/{SOURCE_PATH}"\n{body}')
    return "".join(parts)


def mode_state(driver: str) -> str:
    """munki_imp_init's mode-state block (:628-848), given the `m` init declares."""
    block = _between(driver, "\t/* Set up the current state of each mode */\n",
                     "\n#ifdef ENABLE_NONVCAL\n\t/* Restore the all modes calibration")
    return ("/* munki_imp_init's mode-state block, ArgyllCMS 3.5.0, GPL-2.0-or-later. */\n"
            "static void cm_upstream_mode_state_init(munki *p) {\n"
            "\tmunkiimp *m = (munkiimp *)p->m;\n" + block + "}\n")


def _validate(results: dict) -> None:
    for run, expected in EXPECTED.items():
        result = results[run]
        failed = set(result["failed"])
        if (result["checks"] != expected["checks"] or result["failures"] != expected["failures"]
                or failed != expected["failed"] or result["returncode"] != (1 if failed else 0)):
            raise AssertionError(f"Unexpected ColorMunki calibration {run} result: {result}")
    # Every check the original fails is one the repair passes, on the same inputs.
    if not (results["baseline"]["checks"] == results["patched"]["checks"]):
        raise AssertionError('Proof verdict failed')
    if not (results["baseline"]["probes"] == results["patched"]["probes"]):
        raise AssertionError('Proof verdict failed')
    if not (results["patched"]["tripwires"] == results["baseline"]["tripwires"] == 0):
        raise AssertionError('Proof verdict failed')
    if not (results["repaired_only"]["tripwires"] == 0):
        raise AssertionError('Proof verdict failed')


@production_proof
def prove(archive: Path, output: Path, *, source: Path | None = None,
          compiler: str = "cc", sanitize: bool = False) -> dict:
    """Drive the original and repaired calibrate over the same scripted probes.

    munki_imp_calibrate, munki_whitemeasure and the dark, trigger, conversion
    and mode functions they call are the pinned text; the USB trigger, read and
    status are scripted stand-ins, as are the dark subtraction, the average
    and the spectral read. Nothing links a transport or runs an instrument.
    """
    production_root = source
    production_numsup = production_text(production_root, "numlib/numsup.c")
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    support = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        original = zipped.read(PREFIX + SOURCE_PATH).decode()
        numsup = zipped.read(PREFIX + "numlib/numsup.c").decode()
        if digest(original.encode()) != SOURCE_SHA256:
            raise ValueError("Unexpected ColorMunki source")
        for entry in zipped.namelist():
            if not entry.startswith(PREFIX):
                continue
            relative = Path(entry.removeprefix(PREFIX))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe upstream path")
            if entry.endswith(".h") or relative.as_posix() == "License2.txt":
                support[relative] = zipped.read(entry)
        support[Path("sa_config.h")] = zipped.read(PREFIX + "h/aconfig.h")
    baseline = before_source("munki_nonadaptive_calibration", SOURCE_PATH, archive)
    patched = production_text(source, SOURCE_PATH)
    output.mkdir(parents=True, exist_ok=False)
    headers = output / "upstream"
    for relative, content in support.items():
        (headers / relative).parent.mkdir(parents=True, exist_ok=True)
        (headers / relative).write_bytes(content)
    diff = "".join(difflib.unified_diff(
        baseline.splitlines(True), patched.splitlines(True),
        fromfile="margin-patched/munki_imp.c", tofile="calibration-patched/munki_imp.c",
    ))
    (output / "munki-nonadaptive-calibration.patch").write_text(diff)
    harness = Path(__file__).with_name("munki_nonadaptive_probe.c")
    report: dict = dict(
        status="incomplete", archive_sha256=ARCHIVE_SHA256, original_sha256=SOURCE_SHA256,
        baseline_sha256=digest(baseline.encode()), patched_sha256=digest(patched.encode()),
        calibrate_sha256={"baseline": digest(_function(baseline, CALIBRATE).encode()),
                          "patched": digest(_function(patched, CALIBRATE).encode())},
        numsup_sha256=digest(numsup.encode()), patch_sha256=digest(diff.encode()),
        proof_source_sha256=digest(Path(__file__).read_bytes()),
        harness_sha256=digest(harness.read_bytes()), platform=platform.platform(),
        hardware_tested=False, instrument_runtime_linked=False, cache_file_io=False,
        scope="actual munki_imp_calibrate, whitemeasure, dark, trigger, conversion and "
              "mode functions over scripted USB primitives; synthetic EEPROM constants",
        limitations=[
            LOGGING_SCOPE,
            "The USB trigger, read and status, the dark subtraction, the average and "
            "the spectral read are scripted stand-ins; the scale each white probe yields "
            "is set by the scripted maximum, not measured.",
            "First-probe failures read a value nothing wrote in the original; those cases "
            "run on the repaired text only, where they are defined.",
            "No optical accuracy, saturation or noise consequence is measured; the "
            "consequence shown is the exposure and dark bank the next read uses.",
            "Allocation-failure paths are not injected.",
            "A selection that saturates twice still returns saturated with the last "
            "bank swapped in and no completion, as upstream leaves it.",
        ],
        debug_logging=LOGGING_SCOPE,
        sanitize=sanitize, commands=[], results={}, products_sha256={},
        support_sha256={relative.as_posix(): digest(content)
                        for relative, content in sorted(support.items())},
    )
    system = platform.system()
    report["production_numsup_sha256"] = digest(production_numsup.encode())
    baseline_headers = headers
    current_headers = output / "production-support"
    report["production_support_sha256"] = production_support(
        production_root, baseline_headers, current_headers)
    try:
        for label, source, runs in (("baseline", baseline, ("main",)),
                                    ("patched", patched, ("main", "repaired-only"))):
            headers = current_headers if label == "patched" else baseline_headers
            directory = output / label
            directory.mkdir()
            (directory / "extracted.inc").write_text(
                extracted(source, production_numsup if label == "patched" else numsup))
            (directory / "mode_state.inc").write_text(mode_state(source))
            (directory / "probe.c").write_bytes(harness.read_bytes())
            executable = directory / ("probe.exe" if system == "Windows" else "probe")
            includes = [headers, directory, *[headers / p for p in
                        ("spectro", "numlib", "rspl", "xicc", "cgats", "icc", "h")]]
            command = [cc, "-std=gnu99", "-O1", "-g", "-DSALONEINSTLIB", "-DENABLE_USB",
                       *(["-DNT", "-DEN_LIBUSB0"] if system == "Windows"
                         else ["-DUNIX", "-pthread"]),
                       *sanitizers.flags(sanitize),
                       *["-I" + str(p) for p in includes], str(directory / "probe.c"), "-lm",
                       "-o", str(executable)]
            run = subprocess.run(command, capture_output=True, text=True, timeout=120)
            (directory / "compile.log").write_text(run.stdout + run.stderr)
            report["commands"].append(dict(argv=command, returncode=run.returncode))
            run.check_returncode()
            for mode in runs:
                run = subprocess.run([str(executable), mode], capture_output=True, text=True,
                                     timeout=30)
                (directory / f"{mode}.log").write_text(run.stderr)
                sanitizers.refuse(run.stderr, f"{label}/{mode}")
                key = label if mode == "main" else "repaired_only"
                report["results"][key] = dict(json.loads(run.stdout), returncode=run.returncode)
            report["products_sha256"].update({str(p.relative_to(output)): digest(p.read_bytes())
                for p in (executable, directory / "probe.c", directory / "extracted.inc",
                          directory / "mode_state.inc")})
        _validate(report["results"])
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
