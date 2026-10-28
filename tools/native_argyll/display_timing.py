"""Pinned Argyll 3.5.0 display-delay sampling repair and inert source proof.

i1d3.c and kleink10.c compare a process-relative clock with a two-second
per-acquisition limit. A successful read that crosses the limit is then omitted
from the sample count. Klein additionally computes spacing before checking the
count, so its first-read timeout indexes before the array and a single retained
sample divides by zero. The repair changes only these two delay methods.

The proof compiles their actual authenticated functions with a scripted clock,
measurement replies, lock and logging stand-ins. It does not link instrument
transport or execute an Argyll tool. i1d3's separate refresh sampler is intact.
"""

from __future__ import annotations

import difflib
import json
import shutil
import subprocess
from pathlib import Path

from native_argyll import sanitizers
from native_argyll.proof_inputs import before_source, production_proof, production_text
from native_argyll.spyder_calibration_slots import ARCHIVE_SHA256, _function, digest

from color_measurement.native_receipt import native_worker_name

REPAIR_ID = 'recipe:display-timing'

I1D3 = "spectro/i1d3.c"
KLEIN = "spectro/kleink10.c"
FUNCTIONS = {I1D3: "i1d3_meas_delay", KLEIN: "k10_meas_delay"}


def _compile(texts: dict[str, str], directory: Path, compiler: str, sanitize: bool,
             extra_flags: tuple[str, ...] = ()) -> Path:
    directory.mkdir(parents=True)
    for path, name in FUNCTIONS.items():
        function = _function(texts[path], name)
        source_line = texts[path].index(function)
        (directory / f"{name.split('_')[0]}.inc").write_text(
            f'#line {texts[path].count(chr(10), 0, source_line) + 1} "{path}"\n' + function)
    # The Klein include has a distinct filename from its function prefix.
    (directory / "kleink10.inc").write_bytes((directory / "k10.inc").read_bytes())
    executable = directory / native_worker_name("probe")
    command = [compiler, "-std=gnu89", "-O1", "-g", *sanitizers.flags(sanitize),
               *extra_flags,
               "-I" + str(directory), str(Path(__file__).with_name("display_timing_probe.c")),
               "-lm", "-o", str(executable)]
    run = subprocess.run(command, capture_output=True, text=True, timeout=60)
    (directory / "compile.log").write_text(run.stdout + run.stderr)
    run.check_returncode()
    return executable


def _run(executable: Path, driver: str, case: str, directory: Path) -> tuple[int, ...]:
    run = subprocess.run([str(executable), driver, case], capture_output=True,
                         text=True, timeout=12)
    (directory / f"{driver}-{case}.log").write_text(run.stdout + run.stderr)
    sanitizers.refuse(run.stderr, f"{driver}/{case}")
    if run.returncode:
        raise AssertionError(f"Source-bound {driver}/{case} exited {run.returncode}")
    fields = run.stdout.strip().split()
    if len(fields) != 8:
        raise AssertionError(f"Malformed source-bound {driver}/{case} result")
    return tuple(map(int, fields))


@production_proof
def prove(archive: Path, output: Path, *, compiler: str = "cc", sanitize: bool = False,
          source: Path | None = None) -> dict:
    """Execute actual delay methods on scripted samples, without instrument IO."""
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    original = {path: before_source("display_timing", path, archive) for path in FUNCTIONS}
    repaired = {path: production_text(source, path) for path in FUNCTIONS}
    output.mkdir(parents=True, exist_ok=False)
    for path in FUNCTIONS:
        diff = difflib.unified_diff(original[path].splitlines(True),
                                    repaired[path].splitlines(True),
                                    fromfile=f"a/{path}", tofile=f"b/{path}")
        (output / f"{Path(path).stem}.patch").write_text("".join(diff))
    baseline_common = ("control", "last", "flat_zero", "flat_positive", "reversed",
                       "reversed_negative", "nan_sample", "nan_unused", "inf_sample")
    patched_common = baseline_common + ("aged", "nan_clock", "nan_stamp", "old_stamp")
    scenarios = {
        "baseline": {"i1d3": baseline_common + ("aged", "single", "overflow_sum",
                                                "measure_error"),
                     "kleink10": baseline_common + ("missing_marker", "command_error",
                                                    "decode_error")},
        "patched": {"i1d3": patched_common + ("single", "overflow_sum",
                                              "measure_error"),
                    "kleink10": patched_common + ("missing_marker", "single", "two",
                                                   "zero", "tiny_spacing", "future_stamp",
                                                   "command_error", "decode_error")},
    }
    results: dict[str, dict[str, dict[str, tuple[int, ...]]]] = {}
    observed_aged_out_of_bounds: bool | None = None
    for variant, sources in (("baseline", original), ("patched", repaired)):
        directory = output / variant
        executable = _compile(sources, directory, cc, sanitize)
        results[variant] = {driver: {case: _run(executable, driver, case, directory)
                                     for case in cases}
                            for driver, cases in scenarios[variant].items()}
        if variant == "baseline" and sanitize:
            # The old first-read timeout indexes samp[-1]. Never execute that
            # undefined path without the sanitizers that prove and stop it.
            failure = subprocess.run([str(executable), "kleink10", "aged"],
                                     capture_output=True, text=True, timeout=10)
            (directory / "kleink10-aged-undefined.log").write_text(
                failure.stdout + failure.stderr)
            observed_aged_out_of_bounds = (failure.returncode != 0
                                             and "index -1 out of bounds" in failure.stderr)
            if not observed_aged_out_of_bounds:
                raise AssertionError("Klein baseline first-read out-of-bounds not reproduced")
    baseline, patched = results["baseline"], results["patched"]
    for driver, full in (("i1d3", 400), ("kleink10", 40)):
        control = baseline[driver]["control"]
        if control != patched[driver]["control"] or control[:2] != (0, full):
            raise AssertionError(f"{driver} full-array control changed")
        if patched[driver]["aged"] != control:
            raise AssertionError(f"{driver} aged-clock control changed")
        if patched[driver]["last"][1] != 4:
            raise AssertionError(f"{driver} last-sample count changed")
        if patched[driver]["last"][4:6] != (7, 1):
            raise AssertionError(f"{driver} did not restore debug/thread state")
    if baseline["i1d3"]["aged"][0:2] != (3, 1):
        raise AssertionError("i1d3 aged-clock failure not reproduced")
    if baseline["i1d3"]["last"][0] != 4 or patched["i1d3"]["last"][0] != 0:
        raise AssertionError("i1d3 drops the last acquired sample")
    # The original Klein threshold accepts an all-dark truncated set; the
    # repair uses the fourth sample and refuses all-dark inputs explicitly.
    old_last, new_last = baseline["kleink10"]["last"], patched["kleink10"]["last"]
    if old_last[0] != 0 or new_last[0] != 0 or old_last[6:8] == new_last[6:8]:
        raise AssertionError("Klein last acquired sample did not affect the result")
    if (baseline["i1d3"]["single"][0], patched["i1d3"]["single"][0]) != (3, 4):
        raise AssertionError("i1d3 first timeout count not checked")
    for case in ("single", "two", "zero", "tiny_spacing"):
        row = patched["kleink10"][case]
        if row[0] != 3 or row[2:6] != (1, 1, 7, 1):
            raise AssertionError(f"Klein {case} spacing refusal changed")
    if (patched["kleink10"]["future_stamp"][0] != 3
            or patched["kleink10"]["future_stamp"][2:6] != (1, 1, 7, 1)):
        raise AssertionError("Klein future-stamp lookahead refusal changed")
    for driver in ("i1d3", "kleink10"):
        restored = (0, 0, 7, 1) if driver == "i1d3" else (1, 1, 7, 1)
        for case in ("flat_positive", "reversed"):
            if baseline[driver][case][0] != 4 or patched[driver][case][0] != 4:
                raise AssertionError(f"{driver}/{case} contrast refusal changed")
        if patched[driver]["flat_zero"][0] != 4:
            raise AssertionError(f"{driver} accepts a flat dark series")
        for case in ("nan_sample", "nan_unused", "inf_sample"):
            if (baseline[driver][case][0] != 0 or patched[driver][case][0] != 4
                    or patched[driver][case][2:6] != restored):
                raise AssertionError(f"{driver}/{case} nonfinite sample or state changed")
        for case in ("nan_clock", "nan_stamp", "old_stamp"):
            if patched[driver][case][0] != 3 or patched[driver][case][2:6] != restored:
                raise AssertionError(f"{driver}/{case} invalid timing or state changed")
    if baseline["kleink10"]["flat_zero"][0] != 0:
        raise AssertionError("Klein original flat-dark publication not reproduced")
    if (baseline["kleink10"]["reversed_negative"][0],
            patched["kleink10"]["reversed_negative"][0]) != (0, 4):
        raise AssertionError("Klein reversed negative contrast not refused")
    if baseline["i1d3"]["reversed_negative"][0] != 4:
        raise AssertionError("i1Display 3 reversed contrast control changed")
    if (baseline["kleink10"]["missing_marker"][0],
            patched["kleink10"]["missing_marker"][0]) != (0, 4):
        raise AssertionError("Klein missing-threshold marker not refused")
    if (baseline["i1d3"]["overflow_sum"][0],
            patched["i1d3"]["overflow_sum"][0]) != (0, 4) or (
            patched["i1d3"]["overflow_sum"][2:6] != (0, 0, 7, 1)):
        raise AssertionError("i1Display 3 overflowing channel sum not refused")
    for driver, cases in (("i1d3", ("measure_error",)),
                          ("kleink10", ("command_error", "decode_error"))):
        for case in cases:
            row = patched[driver][case]
            if row != baseline[driver][case] or row[0] != (6 if case == "decode_error" else 5):
                raise AssertionError(f"{driver}/{case} error propagation changed")
    report = {"status": "passed", "archive_sha256": ARCHIVE_SHA256,
              "input_sha256": {path: digest(text.encode()) for path, text in original.items()},
              "patched_sha256": {path: digest(text.encode()) for path, text in repaired.items()},
              "sanitize": sanitize,
              "hardware_tested": False, "instrument_runtime_linked": False,
              "baseline_klein_aged_out_of_bounds": observed_aged_out_of_bounds,
              "results": results}
    (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report
