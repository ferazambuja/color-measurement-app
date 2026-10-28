"""Refuse undefined raw strip averages through Argyll's existing inconsistency flag.

The two recognizers are Graeme W. Gill's GPL-2.0-or-later ArgyllCMS work.
This transform changes only validity checks, not averaging or thresholds.
i1Pro3 locates patches separately and is deliberately not treated as this algorithm.
"""

from __future__ import annotations

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
from native_argyll.proof_inputs import (
    before_source,
    production_proof,
    production_support,
    production_text,
)
from native_argyll.spyder_calibration_slots import _function

REPAIR_ID = 'recipe:scan-finite'

PATHS = {"spectro/munki_imp.c": "munki", "spectro/i1pro_imp.c": "i1pro"}
# The sample-average guard is implied by these three checks: a NaN row sum
# requires a nonfinite cell, which contaminates that band's average; an infinite
# row sum makes the sentinel-bounded consistency numerator infinite. The column
# floors (munki_imp.c:5373-5374, i1pro_imp.c:6687-6688) make finite white_avg
# positive. Keep the explicit ratio check: both native scan-tolerance setters
# accept NaN/Inf, so the original threshold comparison alone is not sufficient.


CONTROLS = (
    "valid",
    "negative",
    "reverse",
    "near_zero",
    "sparse",
    "count_mismatch",
    "margin_mismatch",
    "sat9",
    "sat10",
    "neg_row_overflow",
)
INVALID = (
    "nan_cell",
    "nan_row",
    "inf_cell",
    "neginf_cell",
    "endpoint_nan",
    "band_overflow",
    "row_overflow",
    "white_overflow",
    "neg_row_inf_tolerance",
    "neg_row_nan_tolerance",
)
CASES = CONTROLS + INVALID


@production_proof
def prove(
    archive: Path,
    output: Path,
    *,
    source: Path | None = None,
    compiler: str = "cc",
    sanitize: bool = False,
) -> dict:
    production_root = source
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Expected pinned ArgyllCMS 3.5.0 archive")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError("C compiler unavailable")
    output.mkdir(parents=True, exist_ok=False)
    headers = output / "upstream"
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        originals = {p: zipped.read(PREFIX + p).decode() for p in PATHS}
        for entry in zipped.namelist():
            if not entry.startswith(PREFIX):
                continue
            rel = Path(entry.removeprefix(PREFIX))
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError("Unsafe archive path")
            if entry.endswith(".h") or rel.as_posix() in (
                "numlib/numsup.c",
                "rspl/rspl1.c",
                "License2.txt",
            ):
                target = headers / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zipped.read(entry))
        (headers / "sa_config.h").write_bytes(zipped.read(PREFIX + "h/aconfig.h"))
    harness = Path(__file__).with_name("scan_finite_probe.c").read_text()
    report = dict(
        status="incomplete",
        archive_sha256=ARCHIVE_SHA256,
        hardware_tested=False,
        instrument_runtime_linked=False,
        sanitize=sanitize,
        before_sha256={},
        patched_sha256={},
        results={},
        commands=[],
        probe_sha256=digest(harness.encode()),
        proof_sha256=digest(Path(__file__).read_bytes()),
        tolerance_setter_sha256={},
        limitations=[
            "Synthetic raw strips, not physical attainable calibration states.",
            "Exact recognizers and caller error branches; transport and caller "
            "buffers are stand-ins. No optical or spectral-conversion proof.",
            "i1Pro3 uses a different averaging path and is outside this repair.",
            "The caller excerpt only handles inconsistency; saturation controls "
            "check recognizer flags, not full caller dispatch.",
            "Nonfinite tolerance cases exercise upstream setters, which accept those "
            "values. The application's native request parser rejects them; this proof "
            "does not authorize or add nonfinite application settings.",
        ],
    )
    baseline_headers = headers
    current_headers = output / "production-support"
    report["production_support_sha256"] = production_support(
        production_root, baseline_headers, current_headers)
    try:
        for path, family in PATHS.items():
            before = before_source("scan_finite", path, archive)
            after = production_text(source, path)
            report["before_sha256"][path] = digest(before.encode())
            report["patched_sha256"][path] = digest(after.encode())
            (output / (family + ".patch")).write_text(
                "".join(
                    difflib.unified_diff(
                        before.splitlines(True),
                        after.splitlines(True),
                        fromfile="a/" + path,
                        tofile="b/" + path,
                    )
                )
            )
            report["results"][family] = {}
            report["tolerance_setter_sha256"][family] = {}
            for variant, text in (("baseline", before), ("patched", after)):
                setter = _function(text, family + "_set_scan_toll")
                report["tolerance_setter_sha256"][family][variant] = digest(setter.encode())
                headers = current_headers if variant == "patched" else baseline_headers
                folder = output / family / variant
                folder.mkdir(parents=True)
                start = text.index("#define MIN_SAMPLES 3")
                fragment = text[start : text.index("\n#undef BL", start)]
                # Read the actual caller's bit1 error branch, including its cleanup.
                caller = text[text.index(family + "_code " + family + "_read_patches_2(") :]
                end = caller.index("return " + family.upper() + "_RD_READINCONS;")
                begin = max(caller.rfind("if (rv & 1)", 0, end), caller.rfind("if (rv)", 0, end))
                branch = caller[begin : caller.index("\n\t}", end) + 3]
                if begin < 0:
                    raise ValueError("Caller inconsistency branch missing")
                (folder / "caller.inc").write_text(branch)
                (folder / "recognizer.inc").write_text(fragment)
                (folder / "scan_tolerance.inc").write_text(setter)
                # Retain the original upstream notice alongside the extracted function.
                (folder / "NOTICE.txt").write_text(
                    originals[path][: originals[path].index("#include")]
                )
                unit = folder / "probe.c"
                unit.write_text(harness)
                exe = folder / ("probe.exe" if platform.system() == "Windows" else "probe")
                system = platform.system()
                argv = [
                    cc,
                    "-std=gnu99",
                    "-O1",
                    "-g",
                    "-DSALONEINSTLIB",
                    "-DENABLE_USB",
                    "-DCM_" + family.upper(),
                    *sanitizers.flags(sanitize),
                    *(["-DNT", "-DEN_LIBUSB0"] if system == "Windows" else ["-DUNIX", "-pthread"]),
                    *[
                        "-I" + str(d)
                        for d in [
                            headers,
                            *[
                                headers / x
                                for x in ("spectro", "numlib", "rspl", "xicc", "cgats", "h")
                            ],
                        ]
                    ],
                    str(unit),
                    str(headers / "numlib/numsup.c"),
                    str(headers / "rspl/rspl1.c"),
                    "-lm",
                    *(
                        ["-framework", "CoreFoundation", "-lobjc"]
                        if system == "Darwin"
                        else ["-lwinmm"]
                        if system == "Windows"
                        else []
                    ),
                    "-o",
                    str(exe),
                ]
                build = subprocess.run(argv, capture_output=True, text=True, timeout=120)
                (folder / "build.log").write_text(build.stdout + build.stderr)
                report["commands"].append(argv)
                build.check_returncode()
                rows = {}
                for case in CASES:
                    run = subprocess.run(
                        [str(exe), case], text=True, capture_output=True, timeout=20
                    )
                    (folder / (case + ".log")).write_text(run.stderr)
                    sanitizers.refuse(run.stderr, family + "/" + variant + "/" + case)
                    run.check_returncode()
                    rows[case] = json.loads(run.stdout)
                report["results"][family][variant] = rows
                report.setdefault("extracted_sha256", {})[family + "/" + variant] = digest(
                    fragment.encode()
                )
            baseline, repaired = (report["results"][family][v] for v in ("baseline", "patched"))
            for case in CONTROLS:
                if baseline[case] != repaired[case]:
                    raise AssertionError(f"{family}/{case}: finite control changed")
            for case in INVALID:
                row = repaired[case]
                if row["code"] != 0:
                    raise AssertionError(f"{family}/{case}: expected averaging-tail refusal")
                if not (row["flags"] & 1):
                    raise AssertionError(f"{family}/{case}: undefined patch accepted")
                if row["caller_error"] != row["readincons"] or row["cleanup"] != 1:
                    raise AssertionError(f"{family}/{case}: caller did not refuse and clean up")
            for case in ("nan_cell", "band_overflow", "endpoint_nan", "white_overflow"):
                row = baseline[case]
                if row["code"] != 0 or row["flags"] & 1:
                    raise AssertionError(f"{family}/{case}: baseline did not reproduce")
            for case in ("valid", "negative", "reverse", "near_zero"):
                if repaired[case]["code"] != 0 or repaired[case]["flags"] != 0:
                    raise AssertionError(f"{family}/{case}: valid control refused")
            # At default tolerance the original threshold flags the overflowing
            # negative row; nonfinite native tolerances defeat that comparison.
            row = baseline["neg_row_overflow"]
            if row["code"] != 0 or not (row["flags"] & 1) or row["nonfinite_output"]:
                raise AssertionError(f"{family}/neg_row_overflow: ratio test did not flag")
            for case in ("neg_row_inf_tolerance", "neg_row_nan_tolerance"):
                row = baseline[case]
                if row["code"] != 0 or row["flags"] & 1 or row["nonfinite_output"]:
                    raise AssertionError(f"{family}/{case}: nonfinite threshold did not reproduce")
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
