"""Refuse a ColorMunki thermal calibration or compensation that has no finite result.

The pinned munki_ledtemp_whitemeasure regression divides by the sum of
squared LED-code deviations (munki_imp.c:4108-4131). Equal 16-bit LED voltage
codes (:4938-4940) make that denominator zero. They do not establish a zero
response derivative. A zero-slope fallback would disable compensation at
later temperatures (:4235-4244) and could persist as a valid calibration.

munki_ledtemp_comp (:4224-4245) scales every raw band of every reading by
the fitted white response at the reference code over the fitted response at
the reading's code. A band whose dark-subtracted white readings are exactly
zero fits to (0, 0), so the factor is 0/0; a fitted response at its root gives
an infinite factor; and a finite factor can still overflow the product. The
original writes the nonfinite value and returns MUNKI_OK. An overflowing fitted
denominator can instead turn a finite input into a false zero. Shared consumers
read every raw band: the spot consistency statistic (:5254-5264) compares
NaN as false and can then never fail, the strip recognizer reads all bands,
the white table is averaged and cached for every band (:3282-3283), and the
wavelength conversion reads the bands its EEPROM windows select
(:6296-6312), where even a zero coefficient propagates NaN.
The current production average also flags nonfinite bands as inconsistent;
the consumer probe retains the earlier failure and requires that later repair.

Reject equal codes before writing the reference or fitted table, releasing
the two temporary arrays. Compute each compensated value before assigning it
and refuse a nonfinite denominator or result with the same existing error, leaving the reading
matrix a private temporary its callers release. Both callers then invalidate
the white calibration and request another: the calibration caller because
earlier probes already modified its white/dark state, the reading caller
because its stored table cannot compensate the reading, which also retires a
cached table from an earlier build at the first reading that uses it. Finite
varying-code inputs and finite compensations retain the original arithmetic
exactly; signs are not judged. The native proof uses actual measurement,
compensation, averaging and conversion functions and both extracted caller
branches; transport, preceding calibration steps and cache I/O are not
executed.
"""

from __future__ import annotations

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
from native_argyll.legacy_nonadaptive_calibration import ARCHIVE_SHA256, PREFIX, digest
from native_argyll.munki_scan_margin import SOURCE_PATH, SOURCE_SHA256, recognizer
from native_argyll.proof_inputs import (
    before_source,
    production_proof,
    production_support,
    production_text,
)
from native_argyll.spyder_calibration_slots import _function

REPAIR_ID = 'recipe:munki-ledtemp-slope'

FUNCTION = "munki_ledtemp_whitemeasure"
COMPENSATION = "munki_ledtemp_comp"
READER = "munki_read_patches_2"
# The functions the proof compiles from the pinned text, in definition order.
COMPILED = ("buf2ushort", "munki_ledtemp_whitemeasure", "munki_ledtemp_comp",
            "munki_sens_to_raw", "munki_average_multimeas", "munki_comp_ru_nummeas",
            "munki_absraw_to_abswav")
DEFINES = ("ENABLE_LEDTEMPC", "PATCH_CONS_THR", "DARKTHSCAMIN")
# Observability only, at the level the function already logs: the fit's
# conditioning in LED voltage-drop codes (not kelvin). stt is per band but equal
# for every band.
# The product is computed before it is assigned; the original operation order
# (divide, then multiply) is kept; finite denominators and products preserve results.
CALLER_START = "\t\t\t\tif ((ev = munki_ledtemp_whitemeasure(p, s->white_data"
CALLER_END = "\n\t\t\t\t/* Compute wavelength white readings from ref temp sensor reading */"
READ_CALLER_START = ("\t\tif ((ev = munki_ledtemp_comp(p, multimes, ledtemp, nummeas, "
                     "s->reftemp, s->iwhite_data))")
READ_CALLER_END = "\t\t\treturn ev;\n\t\t}"
CALIBRATION, READ, CONSUMERS = "calibration", "read", "consumers"
# Baseline accepts an undefined fit and a nonfinite compensation; the repair
# refuses both. Finite controls remain byte-identical. "spread" refusals leave
# every output unchanged; "compensation" refusals happen after the reference
# and table were written, which is why both callers invalidate the calibration.
EXPECTED = {
    "drift8": {"kind": CALIBRATION, "iterations": 1, "refusal": None},
    "flat8": {"kind": CALIBRATION, "iterations": 1, "refusal": "spread"},
    "flat8_repeat5": {"kind": CALIBRATION, "iterations": 5, "refusal": "spread"},
    "flat3": {"kind": CALIBRATION, "iterations": 1, "refusal": "spread"},
    "one_step8": {"kind": CALIBRATION, "iterations": 1, "refusal": None},
    "second_step8": {"kind": CALIBRATION, "iterations": 1, "refusal": None},
    "warmup_flat_drift8": {"kind": CALIBRATION, "iterations": 1, "refusal": None},
    "flat_zero_band8": {"kind": CALIBRATION, "iterations": 1, "refusal": "spread"},
    "flat_first8": {"kind": CALIBRATION, "iterations": 1, "refusal": "spread"},
    "dead_first_band8": {"kind": CALIBRATION, "iterations": 1, "refusal": "compensation"},
    "dead_middle_band8": {"kind": CALIBRATION, "iterations": 1, "refusal": "compensation"},
    "dead_last_band8": {"kind": CALIBRATION, "iterations": 1, "refusal": "compensation"},
    "dead_band_first8": {"kind": CALIBRATION, "iterations": 1, "refusal": "compensation"},
    "dead_band_repeat3": {"kind": CALIBRATION, "iterations": 3, "refusal": "compensation"},
    "read_control": {"kind": READ, "iterations": 1, "refusal": None},
    "read_dead_band": {"kind": READ, "iterations": 1, "refusal": "compensation"},
    "read_root_band": {"kind": READ, "iterations": 1, "refusal": "compensation"},
    "read_overflow_later_sample": {"kind": READ, "iterations": 1, "refusal": "compensation"},
    "read_denominator_overflow": {"kind": READ, "iterations": 1,
                                  "refusal": "compensation", "baseline_finite": True},
    "read_negative_fit": {"kind": READ, "iterations": 1, "refusal": None},
    "read_negative_factor": {"kind": READ, "iterations": 1, "refusal": None},
    "consumers_dead_band": {"kind": CONSUMERS, "iterations": 1, "refusal": None},
}
KEYS = {
    CALIBRATION: ("codes", "warmup", "finite", "zero_slope", "output_fnv", "unchanged",
                  "white_unchanged", "table_written", "local_blocks", "cal_valid", "want_calib",
                  "cache_calls"),
    READ: ("codes", "finite", "output_fnv", "local_blocks", "cal_valid", "want_calib"),
    CONSUMERS: ("avg_inconsistent", "avg_inconsistent_nan_band", "converted_finite_unused",
                "converted_finite_zero_coef", "local_blocks"),
}


def caller_fragment(text: str) -> str:
    """The exact helper call/error branch from munki_imp_calibrate."""
    caller = _function(text, "munki_imp_calibrate")
    if caller.count(CALLER_START) != 1 or caller.count(CALLER_END) != 1:
        raise ValueError("Missing or ambiguous LED calibration caller")
    start = caller.index(CALLER_START)
    return caller[start:caller.index(CALLER_END, start)]


def read_caller_fragment(text: str) -> str:
    """The exact compensation call/error branch from munki_read_patches_2."""
    reader = _function(text, READER)
    if reader.count(READ_CALLER_START) != 1:
        raise ValueError("Missing or ambiguous LED compensation caller")
    start = reader.index(READ_CALLER_START)
    end = reader.index(READ_CALLER_END, start) + len(READ_CALLER_END)
    return reader[start:end]


def fragment(text: str) -> str:
    """The compiled functions with the switch and thresholds they read, at their lines."""
    parts = []
    for name in DEFINES:
        found = re.findall(rf"^#define {name}\b[^\n]*", text, re.M)
        if len(found) != 1:
            raise ValueError(f"Missing or ambiguous pinned define: {name}")
        parts.append(found[0] + "\n")
    for name in COMPILED:
        function = _function(text, name)
        line = text[:text.index(function)].count("\n") + 1
        parts.append(f'#line {line} "spectro/munki_imp.c"\n')
        parts.append(function)
    return "".join(parts)


def _expect(label: str, case: str, row: dict, key: str, value: int | None) -> None:
    if set(row[key]) != {value}:
        raise AssertionError(f"{label} result for {case}: {key} {row[key]}")


def _validate(results: dict) -> None:
    for case, expected in EXPECTED.items():
        kind, refusal = expected["kind"], expected["refusal"]
        keys = KEYS[kind]
        baseline, patched = results["baseline"][case], results["patched"][case]
        for label, row in (("baseline", baseline), ("patched", patched)):
            count = expected["iterations"]
            if row["iterations"] != count or any(len(row[k]) != count for k in keys):
                raise AssertionError(f"read count for {case} differs within {label}")
            if len(set(zip(*(row[k] for k in keys)))) != 1:
                raise AssertionError(f"iterations for {case} differ within {label}")
            refused = label == "patched" and refusal is not None
            if kind == CONSUMERS:
                # The frozen predecessor misses NaN inconsistency; the current
                # average must flag it. Conversion still reads only its windows,
                # including a bad band even when its coefficient is zero.
                for key, value in (("avg_inconsistent", 1),
                                   ("avg_inconsistent_nan_band", int(label == "patched")),
                                   ("converted_finite_unused", 1),
                                   ("converted_finite_zero_coef", 0), ("local_blocks", 0)):
                    _expect(label, case, row, key, value)
                continue
            _expect(label, case, row, "codes", 0x34 if refused else 0)
            if kind == CALIBRATION and set(row["warmup"]) != {3}:
                raise AssertionError(f"{label} result for {case}: warm-up {row['warmup']}")
            if set(row["local_blocks"]) != {0}:
                raise AssertionError(f"{label} result for {case}: local allocation leak")
            _expect(label, case, row, "cal_valid", int(not refused))
            _expect(label, case, row, "want_calib", int(refused))
            if kind == CALIBRATION:
                _expect(label, case, row, "cache_calls", int(not refused))
                # A spread refusal writes nothing; a compensation refusal has
                # already written the reference and table but never the white.
                _expect(label, case, row, "unchanged", int(refused and refusal == "spread"))
                _expect(label, case, row, "white_unchanged", int(refused))
                _expect(label, case, row, "table_written",
                        int(not (refused and refusal == "spread")))
            # A nonfinite denominator hides its failure as a finite zero in the
            # baseline; the other refused cases have nonfinite baseline outputs.
            finite = label == "patched" or expected.get("baseline_finite", refusal is None)
            # A refused reading has no downstream numeric observation or hash.
            _expect(label, case, row, "finite", None if refused and kind == READ else int(finite))
            if refused and kind == READ:
                _expect(label, case, row, "output_fnv", None)
        if (refusal is None and kind != CONSUMERS
                and baseline["output_fnv"] != patched["output_fnv"]):
            raise AssertionError(f"outputs for {case} differ between the two texts")


@production_proof
def prove(archive: Path, output: Path, *, source: Path | None = None,
          compiler: str = "cc", sanitize: bool = False) -> dict:
    """Run the original and repaired LED white measurement on the same synthetic bytes."""
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
            if entry.endswith(".h") or relative.as_posix() in ("numlib/numsup.c", "License2.txt"):
                support[relative] = zipped.read(entry)
        support[Path("sa_config.h")] = zipped.read(PREFIX + "h/aconfig.h")
    baseline = before_source("munki_ledtemp_slope", SOURCE_PATH, archive)
    patched = production_text(source, SOURCE_PATH)
    output.mkdir(parents=True, exist_ok=False)
    headers = output / "upstream"
    for relative, content in support.items():
        (headers / relative).parent.mkdir(parents=True, exist_ok=True)
        (headers / relative).write_bytes(content)
    diff = "".join(difflib.unified_diff(
        baseline.splitlines(True), patched.splitlines(True),
        fromfile="white-buffer-released/munki_imp.c", tofile="ledtemp-slope/munki_imp.c",
    ))
    (output / "munki-ledtemp-slope.patch").write_text(diff)
    harness = Path(__file__).with_name("munki_ledtemp_slope_probe.c")
    report: dict = dict(
        status="incomplete", archive_sha256=ARCHIVE_SHA256, original_sha256=SOURCE_SHA256,
        baseline_sha256=digest(baseline.encode()), patched_sha256=digest(patched.encode()),
        recognizer_sha256={"baseline": digest(recognizer(baseline).encode()),
                           "patched": digest(recognizer(patched).encode())},
        function_sha256={"baseline": digest(_function(baseline, FUNCTION).encode()),
                         "patched": digest(_function(patched, FUNCTION).encode())},
        compensation_sha256={"baseline": digest(_function(baseline, COMPENSATION).encode()),
                             "patched": digest(_function(patched, COMPENSATION).encode())},
        fragment_sha256={"baseline": digest(fragment(baseline).encode()),
                         "patched": digest(fragment(patched).encode())},
        caller_sha256={"baseline": digest(caller_fragment(baseline).encode()),
                       "patched": digest(caller_fragment(patched).encode())},
        read_caller_sha256={"baseline": digest(read_caller_fragment(baseline).encode()),
                            "patched": digest(read_caller_fragment(patched).encode())},
        patch_sha256=digest(diff.encode()), proof_source_sha256=digest(Path(__file__).read_bytes()),
        harness_sha256=digest(harness.read_bytes()), platform=platform.platform(),
        hardware_tested=False, instrument_runtime_linked=False, cache_file_io=False,
        scope="actual ColorMunki LED-temperature white measurement, byte conversion, "
              "compensation, averaging, warm-up count and wavelength conversion with numsup "
              "over synthetic raw bytes and tables, through both extracted caller branches",
        limitations=[
            "The trigger and read calls are stand-ins that fill the buffer with the case's "
            "bytes; no USB transport. The dark subtraction is a stand-in that subtracts the "
            "dark vector; no linearisation, gain or integration scaling, so returned values "
            "are synthetic and compared between the two texts and against their own mean.",
            "The saturation limit and warm-up time are synthetic constants; the instrument "
            "reads its own from EEPROM (munki_imp.c:9508, :9532). Temperatures are the "
            "16-bit readings the stand-in writes; whether a physical instrument reports the "
            "same reading for every measurement of a calibration, or a raw band whose white "
            "readings equal its dark level exactly, is not established.",
            "The mode state's white and compensation table are the probe's arrays; the "
            "calibration cache is not written or read. The reading cases supply the table "
            "directly, as a restored cache would, and run the compensation call/error branch "
            "only: averaging, recognition and conversion after it are not run there.",
            "The helper's buffer/vector/matrix lifetimes are counted, not numsup internals.",
            "The extracted calibration call/error branch runs with stand-in preceding state "
            "and success/cache counters. Full calibration and cache serialization are "
            "source-traced, not executed. Existing calibration files are not migrated; a "
            "stored table with a zero-response band is refused at its first reading.",
            "The consumer cases use a synthetic two-output read-window matrix, not an EEPROM "
            "map; they establish source behaviour, not which physical bands are read.",
        ],
        sanitize=sanitize, commands=[], results={}, products_sha256={},
        support_sha256={relative.as_posix(): digest(content)
                        for relative, content in sorted(support.items())},
    )
    system = platform.system()
    baseline_headers = headers
    current_headers = output / "production-support"
    report["production_support_sha256"] = production_support(
        production_root, baseline_headers, current_headers)
    try:
        for label, source in (("baseline", baseline), ("patched", patched)):
            headers = current_headers if label == "patched" else baseline_headers
            directory = output / label
            directory.mkdir()
            (directory / "ledtemp.inc").write_text(fragment(source))
            (directory / "caller.inc").write_text(caller_fragment(source))
            (directory / "read_caller.inc").write_text(read_caller_fragment(source))
            (directory / "probe.c").write_bytes(harness.read_bytes())
            executable = directory / ("probe.exe" if system == "Windows" else "probe")
            includes = [headers, *[headers / p for p in
                        ("spectro", "numlib", "rspl", "xicc", "cgats", "h")]]
            command = [cc, "-std=gnu99", "-O1", "-g", "-DSALONEINSTLIB", "-DENABLE_USB",
                       *(["-DNT", "-DEN_LIBUSB0"] if system == "Windows"
                         else ["-DUNIX", "-pthread"]),
                       *sanitizers.flags(sanitize),
                       *["-I" + str(p) for p in includes], str(directory / "probe.c"),
                       str(headers / "numlib/numsup.c"), "-lm",
                       *(["-framework", "CoreFoundation", "-lobjc"] if system == "Darwin"
                         else ["-lwinmm"] if system == "Windows" else []),
                       "-o", str(executable)]
            run = subprocess.run(command, capture_output=True, text=True, timeout=120)
            (directory / "compile.log").write_text(run.stdout + run.stderr)
            report["commands"].append(dict(argv=command, returncode=run.returncode))
            run.check_returncode()
            listed = subprocess.run([str(executable), "--list"], capture_output=True, text=True,
                                    timeout=10)
            (directory / "list.log").write_text(listed.stderr)
            sanitizers.refuse(listed.stderr, f"{label}/--list")
            listed.check_returncode()
            cases = tuple(listed.stdout.split())
            if cases != tuple(EXPECTED):
                raise RuntimeError(f"{label} probe lists {cases}; expected {tuple(EXPECTED)}")
            report["results"][label] = {}
            for case in cases:
                run = subprocess.run([str(executable), case], capture_output=True, text=True,
                                     timeout=20)
                (directory / f"{case}.log").write_text(run.stderr)
                sanitizers.refuse(run.stderr, f"{label}/{case}")
                run.check_returncode()
                report["results"][label][case] = json.loads(run.stdout)
            report["products_sha256"].update({str(p.relative_to(output)): digest(p.read_bytes())
                for p in (executable, directory / "probe.c", directory / "ledtemp.inc",
                          directory / "caller.inc", directory / "read_caller.inc")})
        _validate(report["results"])
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
