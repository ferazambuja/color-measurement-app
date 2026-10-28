"""Pinned ColorMunki strip-margin agreement repair and inert actual-recognizer proof.

Argyll's munki_imp.c remains Graeme W. Gill's GPL-2.0-or-later work. Its strip
recognizer averages the white leader and trailer and refuses the swipe when
``lead_avg/trail_avg < 0.7 || trail_avg/lead_avg < 0.7`` (munki_imp.c:5715, the
same text as i1pro_imp.c:7019). Both comparisons are false for an undefined
ratio, so two zero margins (0/0), a NaN margin or two overflowed margins
(Inf/Inf) pass the agreement test they were meant to fail, and the patch
averages returned afterwards can be finite and correct, which hides it.

The repair requires both ratios to meet the existing threshold. It adds no
brightness floor, changes no other rule, and leaves every pair whose ratios are
defined exactly where the original left it. The i1Pro/i1Pro2 guard that
legacy_scan_recognition composes spells the same predicate out; the proof's
predicate table holds the two to each other over 324 special pairs.
"""

from __future__ import annotations

import argparse
import difflib
import io
import json
import platform
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from native_argyll import sanitizers
from native_argyll.flash_capture import SHARED_SOURCE_SHA256
from native_argyll.legacy_nonadaptive_calibration import ARCHIVE_SHA256, PREFIX, digest
from native_argyll.legacy_scan_recognition import _NEW as I1PRO_GUARD
from native_argyll.proof_inputs import (
    before_source,
    production_proof,
    production_support,
    production_text,
)

REPAIR_ID = 'recipe:munki-scan-margin'

SOURCE_PATH = "spectro/munki_imp.c"
SOURCE_SHA256 = SHARED_SOURCE_SHA256["munki"]
#: munki_imp.c after the shared flash-window refusal, which the broad build
#: composes first. This repair applies to that text only.
REPAIRED = """\t/* CM: both ratios must meet the threshold. An undefined ratio (0/0, NaN,
\t * Inf/Inf) fails neither original comparison and was accepted as agreement. */
\tif (!(lead_avg/trail_avg >= 0.7 && trail_avg/lead_avg >= 0.7)) {"""
_LOG = re.compile(r"Leader avg = (\S+), trailer avg = (\S+)")
_FP = ("fe_invalid", "fe_divbyzero", "fe_overflow")

#: Recognizer return per case as (original, repaired): 0x00 MUNKI_OK, 0x3B
#: MUNKI_RD_NOTENOUGHPATCHES, 0x3C MUNKI_RD_TOOMANYPATCHES and 0x42
#: MUNKI_RD_LEADTRAILINCONS (munki_imp.h:332,358-359,365). The probe's case
#: table (munki_scan_margin_probe.c) defines each input; this order is the
#: order the proof checks them in, so a regressed repair is named by its first
#: disagreeing case.
EXPECTED = {
    # Finite scans the recognizer accepts, including both exact 0.7 boundaries,
    # signed and near-zero margins, ramped transitions and sparse rows.
    "valid3": (0x00, 0x00), "valid8": (0x00, 0x00), "reverse3": (0x00, 0x00),
    "valid3_ramp": (0x00, 0x00), "reverse3_ramp": (0x00, 0x00), "negative3": (0x00, 0x00),
    "near_zero_same_sign": (0x00, 0x00), "boundary_lead_0p7": (0x00, 0x00),
    "boundary_trail_0p7": (0x00, 0x00), "sparse10": (0x00, 0x00),
    # Recognition returns these rows; current production flags them inconsistent.
    "nan_patch_rows_only": (0x00, 0x00),
    # Defined disagreement the original already refuses: below the threshold,
    # opposite signs, one zero margin, and a large mismatch that does not overflow.
    "near_zero_opposite_sign": (0x42, 0x42), "leader_mismatch": (0x42, 0x42),
    "below_lead_0p699": (0x42, 0x42), "below_trail_0p699": (0x42, 0x42),
    "zero_leader_only": (0x42, 0x42), "zero_trailer_only": (0x42, 0x42),
    "large_no_overflow_mismatch": (0x42, 0x42),
    # Refused before or after the agreement test for other reasons.
    "count_low": (0x3C, 0x3C), "count_high": (0x3B, 0x3B), "valid1": (0x3B, 0x3B),
    "valid2": (0x3B, 0x3B), "sparse5": (0x3B, 0x3B), "uniform": (0x3B, 0x3B),
    "all_nan": (0x3B, 0x3B),
    # Undefined agreement the original accepts: 0/0, NaN and Inf/Inf margins.
    # zero_margins1 passed the original's test and then failed the median rule.
    "zero_margins3": (0x00, 0x42), "zero_margins3_ramp": (0x00, 0x42),
    "zero_margins1": (0x3B, 0x42), "nan_band20": (0x00, 0x42), "inf_band20": (0x00, 0x42),
    "nan_leader_masks_mismatch": (0x00, 0x42), "overflow_masks_mismatch": (0x00, 0x42),
    "overflow_agree": (0x00, 0x42),
}
CASES = tuple(EXPECTED)
#: Recognitions whose patch rows can carry one nonfinite band. The current
#: production recognizer flags those averages inconsistent; its caller refuses
#: them instead of publishing them as a successful reading.
NONFINITE_PATCH_ROWS = frozenset({"nan_band20", "inf_band20", "nan_patch_rows_only"})
#: The predicate table's outcome: every ordered pair of its 18 special values.
TABLE = {
    "pairs": 324, "changed": 43, "repaired_equals_i1pro_guard": 324,
    "defined_pairs": 221, "defined_unchanged": 221, "changed_with_nan_ratio": 43,
    "new_acceptances": 0,
}


def recognizer(text: str) -> str:
    """The whole recognizer, with the consistency threshold it reads (munki_imp.c:157)."""
    threshold = re.findall(r"^#define PATCH_CONS_THR\b[^\n]*", text, re.M)
    if len(threshold) != 1 or text.count("#define MIN_SAMPLES 3") != 1:
        raise ValueError("Missing or ambiguous pinned ColorMunki recognizer")
    start = text.index("#define MIN_SAMPLES 3")
    return threshold[0] + "\n" + text[start:text.index("\n#undef BL", start)] + "\n"


def _condition(guard: str) -> str:
    """The controlling expression of an `if (...) {`, however many lines it spans."""
    start = guard.index("if (") + len("if (")
    return guard[start:guard.index(") {", start)]


def _agreement_test(fragment: str) -> str:
    """The compiled agreement test: the `if` guarding the leader/trailer refusal."""
    marker = "Patch recog failed - swipe didn't start and end on the media"
    if fragment.count(marker) != 1:
        raise ValueError("Missing or ambiguous pinned agreement test")
    head = fragment[:fragment.index(marker)]
    return _condition(head[head.rindex("\n\tif (") + 1:])


def _predicates(baseline: str, patched: str) -> str:
    """Three predicates, each true when the scan is refused, from the texts compiled."""
    conditions = (("original", _agreement_test(baseline)), ("repaired", _agreement_test(patched)),
                  ("i1pro_guard", _condition(I1PRO_GUARD)))
    return "".join(f"static int {name}(double lead_avg, double trail_avg) {{\n"
                   f"\treturn {condition};\n}}\n" for name, condition in conditions)


def _validate(results: dict) -> float:
    baseline, patched = results["baseline"], results["patched"]
    for case, (before, after) in EXPECTED.items():
        for variant, rows, want in (("baseline", baseline, before), ("patched", patched, after)):
            if rows[case]["code"] != want:
                raise AssertionError(
                    f"{variant} {case}: code {rows[case]['code']:#04x}, expected {want:#04x}")
    maximum = 0.0
    for variant, rows in (("baseline", baseline), ("patched", patched)):
        for case, row in rows.items():
            # The frozen baseline predates the curve-release repair. The current
            # recognizer must release every curve before returning, even on error.
            if row["retained_curve"] != int(variant == "baseline" and case == "valid8"):
                raise AssertionError((variant, case, "recognizer curve lifetime"))
            if not (len(row["values"]) == row["requested"] and all(
                len(patch) == 128 for patch in row["values"])):
                raise AssertionError((variant, case, "output shape"))
            if row["code"] != 0:
                # Refusal writes nothing and leaves the caller's flags alone.
                if not ((row["changed_values"], row["flags"]) == (0, -999)):
                    raise AssertionError((variant, case, row))
                continue
            # Recognition may return rows with an inconsistency flag. The later
            # nonfinite-average repair sets it for these NaN patch rows, and the
            # production caller turns that flag into MUNKI_RD_READINCONS.
            flags = int(variant == "patched" and case == "nan_patch_rows_only")
            if not ((row["changed_values"], row["flags"]) == (128 * row["requested"], flags)):
                raise AssertionError((variant, case, row))
            if not (row["nonfinite_output"] == (
                row["requested"] if case in NONFINITE_PATCH_ROWS else 0)):
                raise AssertionError((variant, case, row))
            error = row["max_rel_err_vs_plateau"]
            if not (error is not None and error <= 1e-12):
                raise AssertionError((variant, case, error))
            maximum = max(maximum, error)
    # Flags and lifetimes have exact per-variant checks above. Every returned
    # band and all other observations still match when the verdict is unchanged.
    separately_checked = {*_FP, "flags", "retained_curve"}
    for case, (before, after) in EXPECTED.items():
        strip = {k: v for k, v in baseline[case].items() if k not in separately_checked}
        if before == after:
            # Compare every returned band as well as the verdict and flags.
            if not (strip == {k: v for k, v in patched[case].items()
                             if k not in separately_checked}):
                raise AssertionError(case)
        else:
            # Same averages compared; only the verdict moves, before any output.
            if not (baseline[case]["leader_trailer"] == patched[case]["leader_trailer"]):
                raise AssertionError(case)
    # The original accepts two zero margins and returns correct finite averages,
    # so checking only output finiteness cannot expose the missing agreement.
    zero = baseline["zero_margins3"]
    if not (zero["leader_trailer"] == ["0.000000", "0.000000"] and zero["nonfinite_output"] == 0):
        raise AssertionError('Proof verdict failed')
    # A NaN leader and overflowed margins each hid a genuine 0.4 mismatch.
    if not (baseline["nan_leader_masks_mismatch"]["leader_trailer"][1] == "400.000000"):
        raise AssertionError('Proof verdict failed')
    if not (baseline["overflow_masks_mismatch"]["leader_trailer"] == (
        baseline["overflow_agree"]["leader_trailer"])):
        raise AssertionError('Proof verdict failed')
    table = results["predicate_table"]
    if not (table["returncode"] == 0 and {k: table[k] for k in TABLE} == TABLE):
        raise AssertionError(table)
    return maximum


@production_proof
def prove(archive: Path, output: Path, *, source: Path | None = None,
          compiler: str = "cc", sanitize: bool = False) -> dict:
    """Run the original and repaired recognizer on the same synthetic strips.

    The recognizer, numsup and rspl1 are the pinned upstream text; the probe
    supplies raw arrays and instrument state only. The predicate table restates
    nothing: it compiles the agreement test from each compiled recognizer and
    the composed i1Pro guard.
    """
    production_root = source
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    support = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        original = zipped.read(PREFIX + SOURCE_PATH).decode()
        if digest(original.encode()) != SOURCE_SHA256:
            raise ValueError("Unexpected ColorMunki source")
        for entry in zipped.namelist():
            if not entry.startswith(PREFIX):
                continue
            relative = Path(entry.removeprefix(PREFIX))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe upstream path")
            if entry.endswith(".h") or relative.as_posix() in (
                "numlib/numsup.c", "rspl/rspl1.c", "License2.txt",
            ):
                support[relative] = zipped.read(entry)
        support[Path("sa_config.h")] = zipped.read(PREFIX + "h/aconfig.h")
    baseline = before_source("munki_scan_margin", SOURCE_PATH, archive)
    patched = production_text(source, SOURCE_PATH)
    output.mkdir(parents=True, exist_ok=False)
    headers = output / "upstream"
    for relative, content in support.items():
        (headers / relative).parent.mkdir(parents=True, exist_ok=True)
        (headers / relative).write_bytes(content)
    diff = "".join(difflib.unified_diff(
        baseline.splitlines(True), patched.splitlines(True),
        fromfile="flash-patched/munki_imp.c", tofile="margin-patched/munki_imp.c",
    ))
    (output / "munki-scan-margin.patch").write_text(diff)
    harness = Path(__file__).with_name("munki_scan_margin_probe.c")
    table = Path(__file__).with_name("munki_scan_margin_table.c")
    report: dict = dict(
        status="incomplete", archive_sha256=ARCHIVE_SHA256, original_sha256=SOURCE_SHA256,
        baseline_sha256=digest(baseline.encode()), patched_sha256=digest(patched.encode()),
        recognizer_sha256={"original": digest(recognizer(original).encode()),
                           "baseline": digest(recognizer(baseline).encode()),
                           "patched": digest(recognizer(patched).encode())},
        patch_sha256=digest(diff.encode()), proof_source_sha256=digest(Path(__file__).read_bytes()),
        harness_sha256=digest(harness.read_bytes()), table_sha256=digest(table.read_bytes()),
        i1pro_guard_sha256=digest(I1PRO_GUARD.encode()), platform=platform.platform(),
        hardware_tested=False, instrument_runtime_linked=False, cache_file_io=False,
        scope="actual ColorMunki recognizer, numsup and rspl1 with synthetic raw arrays and "
              "state; the compiled agreement tests over 324 special pairs",
        limitations=[
            "The frozen baseline retains a curve in valid8; the fixture records and "
            "then releases it. The current recognizer must release every curve itself, "
            "as required by its later curve-release repair and independent lifecycle proof.",
            "No acquisition, dark or white correction, wavelength conversion, cache I/O or "
            "optical tolerance; synthetic raw units only.",
            "The recognizer is compiled from the text the broad build compiles; the "
            "flash-window refusal lies outside it and has its own proof.",
            "Physical prevalence of zero, NaN or overflowed margins is not established; an "
            "undefined comparison is refused because it cannot confirm agreement.",
            "No brightness floor is added; two agreeing near-zero margins still pass.",
            "Nonfinite patch rows are outside the margin repair: nan_patch_rows_only "
            "returns recognition success with three nonfinite bands in both variants. "
            "Current production must also set the inconsistency flag its caller refuses. "
            "The plateau comparison covers finite averages only; all bands are retained.",
        ],
        sanitize=sanitize, commands=[], results={}, products_sha256={},
        support_sha256={relative.as_posix(): digest(content)
                        for relative, content in sorted(support.items())},
    )
    system = platform.system()
    executable_name = "probe.exe" if system == "Windows" else "probe"
    baseline_headers = headers
    current_headers = output / "production-support"
    report["production_support_sha256"] = production_support(
        production_root, baseline_headers, current_headers)
    try:
        for label, source in (("baseline", baseline), ("patched", patched)):
            headers = current_headers if label == "patched" else baseline_headers
            directory = output / label
            directory.mkdir()
            (directory / "recognizer.inc").write_text(recognizer(source))
            (directory / "probe.c").write_bytes(harness.read_bytes())
            executable = directory / executable_name
            includes = [headers, *[headers / p for p in
                        ("spectro", "numlib", "rspl", "xicc", "cgats", "h")]]
            command = [cc, "-std=gnu99", "-O1", "-g", "-DSALONEINSTLIB", "-DENABLE_USB",
                       *(["-DNT", "-DEN_LIBUSB0"] if system == "Windows"
                         else ["-DUNIX", "-pthread"]),
                       *sanitizers.flags(sanitize),
                       *["-I" + str(p) for p in includes], str(directory / "probe.c"),
                       str(headers / "numlib/numsup.c"), str(headers / "rspl/rspl1.c"), "-lm",
                       *(["-framework", "CoreFoundation", "-lobjc"] if system == "Darwin"
                         else ["-lwinmm"] if system == "Windows" else []),
                       "-o", str(executable)]
            run = subprocess.run(command, capture_output=True, text=True, timeout=120)
            (directory / "compile.log").write_text(run.stdout + run.stderr)
            report["commands"].append(dict(argv=command, returncode=run.returncode))
            run.check_returncode()
            report["results"][label] = {}
            for case in CASES:
                # Debug level 2 makes the recognizer log the two averages it compared.
                run = subprocess.run([str(executable), case, "2"], capture_output=True,
                                     text=True, timeout=10)
                (directory / f"{case}.log").write_text(run.stderr)
                # The recognizer's log shares stderr, so a diagnostic must be
                # refused by content, not by stderr being non-empty.
                sanitizers.refuse(run.stderr, f"{label}/{case}")
                run.check_returncode()
                row = json.loads(run.stdout)
                logged = _LOG.search(run.stderr)
                row["leader_trailer"] = list(logged.groups()) if logged else None
                report["results"][label][case] = row
            report["products_sha256"].update({str(p.relative_to(output)): digest(p.read_bytes())
                for p in (executable, directory / "probe.c", directory / "recognizer.inc")})
        directory = output / "predicate-table"
        directory.mkdir()
        (directory / "predicates.inc").write_text(_predicates(recognizer(baseline),
                                                              recognizer(patched)))
        (directory / "table.c").write_bytes(table.read_bytes())
        executable = directory / ("table.exe" if system == "Windows" else "table")
        command = [cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-I" + str(directory),
                   *sanitizers.flags(sanitize),
                   str(directory / "table.c"), "-lm", "-o", str(executable)]
        run = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (directory / "compile.log").write_text(run.stdout + run.stderr)
        report["commands"].append(dict(argv=command, returncode=run.returncode))
        run.check_returncode()
        run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
        (directory / "table.log").write_text(run.stdout + run.stderr)
        sanitizers.refuse(run.stderr, "predicate-table")
        report["results"]["predicate_table"] = dict(json.loads(run.stdout),
                                                    returncode=run.returncode)
        report["products_sha256"].update({str(p.relative_to(output)): digest(p.read_bytes())
            for p in (executable, directory / "table.c", directory / "predicates.inc")})
        report["analytical_max_rel_error"] = _validate(report["results"])
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
