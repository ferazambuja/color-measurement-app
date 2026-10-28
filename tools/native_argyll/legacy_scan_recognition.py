"""Authenticated original-family strip agreement guard and inert source proof.

The extracted recognizer, numsup and rspl1 remain attributed ArgyllCMS work.
Synthetic raw arrays exercise recognition and averaging, not optical acquisition,
calibration, spectral conversion, USB, caches or an instrument runtime.
"""
from __future__ import annotations

import difflib
import io
import json
import math
import platform
import shutil
import subprocess
import zipfile
from pathlib import Path

from native_argyll import sanitizers
from native_argyll.legacy_nonadaptive_calibration import (
    ARCHIVE_SHA256,
    PREFIX,
    SOURCE_PATH,
    SOURCE_SHA256,
    digest,
)
from native_argyll.proof_inputs import before_source, production_proof, production_text

REPAIR_ID = 'recipe:legacy-scan-recognition'

_NEW = """\t/* CM: undefined/nonfinite ratios cannot confirm relative agreement.
\t * Preserve finite nonzero signed margins and the existing threshold. */
\tif (!isfinite(lead_avg) || !isfinite(trail_avg)
\t || lead_avg == 0.0 || trail_avg == 0.0
\t || !isfinite(lead_avg/trail_avg) || !isfinite(trail_avg/lead_avg)
\t || lead_avg/trail_avg < 0.7 || trail_avg/lead_avg < 0.7) {"""
CASES = (
    "valid1", "valid2", "valid3", "valid32", "reverse3", "negative", "flat", "zero",
    "leader_mismatch", "zero_leaders", "zero_leader", "zero_trailer", "sparse4", "sparse7",
    "count_low", "count_high", "sat9", "sat10", "nan20", "inf20",
)


def _fragment(text: str) -> str:
    start = text.index("#define MIN_SAMPLES 3")
    return text[start:text.index("\n#undef BL", start)]


def _validate(results: dict) -> float:
    baseline, patched = results["baseline"], results["patched"]
    for case in CASES:
        if case not in ("zero_leaders", "nan20", "inf20"):
            # The undefined baseline ratios may set different floating flags.
            if not ({k: v for k, v in baseline[case].items() if k != "fp_exception_mask"} == {
                k: v for k, v in patched[case].items() if k != "fp_exception_mask"
            }):
                raise AssertionError(case)
    if not ((baseline["zero_leaders"]["code"], baseline["zero_leaders"]["flags"],
            baseline["zero_leaders"]["nonfinite_output"]) == (0, 0, 0)):
        raise AssertionError
    for case in ("zero_leaders", "zero_leader", "zero_trailer", "leader_mismatch",
                 "nan20", "inf20"):
        if not (patched[case]["code"] == 0x42 and patched[case]["changed_values"] == 0):
            raise AssertionError(case)
    for case in ("valid1", "valid2", "flat", "zero", "sparse4", "count_high"):
        if not (patched[case]["code"] == 0x3B and patched[case]["changed_values"] == 0):
            raise AssertionError(case)
    if not (patched["count_low"]["code"] == 0x3C):
        raise AssertionError
    if not ((patched["sat9"]["code"], patched["sat9"]["flags"]) == (0, 0)):
        raise AssertionError
    if not ((patched["sat10"]["code"], patched["sat10"]["flags"]) == (0, 2)):
        raise AssertionError
    for case in ("nan20", "inf20"):
        if not (baseline[case]["nonfinite_output"] == 3):
            raise AssertionError
    maximum = 0.0
    for case, count in (("valid3", 3), ("valid32", 32), ("reverse3", 3), ("negative", 3)):
        row = patched[case]
        if not ((row["code"], row["flags"]) == (0, 0)):
            raise AssertionError
        for patch, values in enumerate(row["raw_patch_averages"]):
            index = count - 1 - patch if case == "reverse3" else patch
            value = (600 + index if index % 2 else 200 + index) if count == 32 else (
                200, 600, 350
            )[index]
            for channel, actual in enumerate(values):
                expected = value + .1 * (1000 - value) * math.sin(channel * .1)
                if case == "negative":
                    expected = -expected
                error = abs(actual - expected)
                maximum = max(maximum, error)
                if not (error <= 5e-12):
                    raise AssertionError((case, patch, channel, actual, expected))
    return maximum


@production_proof
def prove(archive: Path, output: Path, *, compiler: str = "cc", sanitize: bool = False,
          source: Path | None = None) -> dict:
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Expected the pinned Argyll 3.5.0 source archive")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError("C compiler unavailable")
    output.mkdir(parents=True, exist_ok=False)
    headers = output / "upstream"
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        original = zipped.read(PREFIX + SOURCE_PATH).decode()
        if digest(original.encode()) != SOURCE_SHA256:
            raise ValueError("Unexpected original-family source")
        for entry in zipped.namelist():
            if not entry.startswith(PREFIX):
                continue
            relative = Path(entry.removeprefix(PREFIX))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe upstream path")
            if entry.endswith(".h") or relative.as_posix() in (
                "numlib/numsup.c", "rspl/rspl1.c", "License2.txt",
            ):
                destination = headers / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(zipped.read(entry))
        (headers / "sa_config.h").write_bytes(zipped.read(PREFIX + "h/aconfig.h"))
    baseline = before_source("legacy_scan_recognition", SOURCE_PATH, archive)
    # Bind the extracted recognizer to the complete source the library builds.
    # The reference-factor guard is outside this fragment and has its own proof.
    patched = production_text(source, SOURCE_PATH)
    production_support = {name: production_text(source, name)
                          for name in ("numlib/numsup.c", "rspl/rspl1.c")}
    diff = "".join(difflib.unified_diff(
        baseline.splitlines(True), patched.splitlines(True),
        fromfile="calibration-patched/i1pro_imp.c", tofile="scan-patched/i1pro_imp.c",
    ))
    (output / "legacy-scan.patch").write_text(diff)
    harness = Path(__file__).with_name("legacy_scan_probe.c")
    report = dict(
        status="incomplete", archive_sha256=ARCHIVE_SHA256, original_sha256=SOURCE_SHA256,
        baseline_sha256=digest(baseline.encode()), patched_sha256=digest(patched.encode()),
        patch_sha256=digest(diff.encode()), proof_source_sha256=digest(Path(__file__).read_bytes()),
        harness_sha256=digest(harness.read_bytes()), platform=platform.platform(),
        hardware_tested=False, instrument_runtime_linked=False, cache_file_io=False,
        scope="actual recognizer, numsup and rspl1 with synthetic raw arrays and state",
        limitations="No acquisition, dark correction, spectral conversion or optical tolerance. "
        "Final native/Python output guards independently validate all published values.",
        sanitize=sanitize, commands=[], results={}, products_sha256={},
        support_sha256={str(p.relative_to(headers)): digest(p.read_bytes())
                        for p in sorted(headers.rglob("*")) if p.is_file()},
        production_support_sha256={name: digest(text.encode())
                                   for name, text in production_support.items()},
    )
    system = platform.system()
    try:
        for label, source in (("baseline", baseline), ("patched", patched)):
            directory = output / label
            directory.mkdir()
            support = headers
            if label == "patched":
                support = directory / "production-support"
                for name, text in production_support.items():
                    path = support / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text)
            (directory / "recognizer.inc").write_text(_fragment(source))
            (directory / "probe.c").write_bytes(harness.read_bytes())
            executable = directory / ("probe.exe" if system == "Windows" else "probe")
            includes = [headers, *[headers / p for p in
                        ("spectro", "numlib", "rspl", "xicc", "cgats", "h")]]
            command = [cc, "-std=gnu99", "-O1", "-g", "-DSALONEINSTLIB", "-DENABLE_USB",
                       *(["-DNT", "-DEN_LIBUSB0"] if system == "Windows"
                         else ["-DUNIX", "-pthread"]),
                       *sanitizers.flags(sanitize),
                       *["-I" + str(p) for p in includes], str(directory / "probe.c"),
                       str(support / "numlib/numsup.c"), str(support / "rspl/rspl1.c"), "-lm",
                       *(["-framework", "CoreFoundation", "-lobjc"] if system == "Darwin"
                         else ["-lwinmm"] if system == "Windows" else []),
                       "-o", str(executable)]
            run = subprocess.run(command, capture_output=True, text=True, timeout=60)
            (directory / "compile.log").write_text(run.stdout + run.stderr)
            report["commands"].append(dict(argv=command, returncode=run.returncode))
            run.check_returncode()
            report["results"][label] = {}
            for case in CASES:
                run = subprocess.run([str(executable), case], capture_output=True,
                                     text=True, timeout=10)
                (directory / f"{case}.log").write_text(run.stdout + run.stderr)
                sanitizers.refuse(run.stderr, f"{label}/{case}")
                run.check_returncode()
                if run.stderr.strip():
                    raise RuntimeError(f"Unexpected recognizer diagnostics: {label}/{case}")
                report["results"][label][case] = json.loads(run.stdout)
            report["products_sha256"].update({str(p.relative_to(output)): digest(p.read_bytes())
                for p in (executable, directory / "probe.c", directory / "recognizer.inc")})
        report["analytical_max_abs_error"] = _validate(report["results"])
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
