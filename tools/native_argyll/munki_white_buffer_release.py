"""ColorMunki white measurement: release the reading buffer when conversion fails.

munki_whitemeasure (munki_imp.c:3865) allocates the raw USB reading buffer
(:3899), triggers and reads the measurement into it, converts the bytes with
munki_sens_to_raw (:3925) and frees the buffer after the dark subtraction
(:3947). The trigger and read failures free it first (:3909, :3917). The
conversion failure (:3925-3928) frees the measurement matrix and returns
without freeing the buffer. munki_sens_to_raw fails when any raw value exceeds
the saturation limit (:4961) or when the band counts disagree (:4913); its
three callers of munki_whitemeasure are all calibration (:1536, :1672, :1685),
so every saturated white-reference attempt leaves one buffer of
nsen * 2 * (ninvmeas + nummeas) bytes behind. Of the other five
munki_sens_to_raw call sites, two free their own buffer on that failure
(munki_ledtemp_whitemeasure :4086, munki_trialmeasure :4782) and three
process a buffer their caller owns (munki_dark_measure_2 :3731,
munki_read_patches_2 :4320, munki_read_patches_2a :4632), so this function
is the only owner that drops it.

The repair frees the buffer on that exit, as the two earlier exits do.
Returned readings, scale and codes are unchanged.

The input is the composed text the curve release leaves, pinned, and the
AFTER reads current production source. The proof compiles the actual function with the actual
munki_sens_to_raw, munki_average_multimeas and munki_comp_ru_nummeas, original
and repaired, over synthetic raw bytes; the transport calls are stand-ins, and
the buffer and the matrix are counted through the function's own calls.
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

REPAIR_ID = 'recipe:munki-white-buffer-release'

FUNCTION = "munki_whitemeasure"
# The functions the proof compiles from the pinned text, in definition order.
COMPILED = ("buf2ushort", "munki_sens_to_raw", "munki_average_multimeas",
            "munki_comp_ru_nummeas", FUNCTION)
DEFINES = ("PATCH_CONS_THR", "DARKTHSCAMIN")
# Per case, over every read of the case in one process: the function's code;
# warm-up readings it asked the transport for beyond nummeas (None when it never
# reached the transport); buffers allocated; injected
# allocation faults; measurement matrices made; and what the ORIGINAL leaves
# behind (buffers not freed). The repair leaves nothing behind, and neither
# text leaves a matrix behind. Codes, warm-up counts and returned readings must
# be equal in both texts and across the reads of a case.
EXPECTED = {
    "white8": {"iterations": 1, "code": 0x00, "warmup": 0, "mallocs": 1, "faults": 0,
               "matrices": 1, "heap_open": 0},
    "white8_repeat5": {"iterations": 5, "code": 0x00, "warmup": 0, "mallocs": 5, "faults": 0,
                       "matrices": 5, "heap_open": 0},
    # One raw value over the saturation limit: the buffer leaks in the original.
    "saturated8": {"iterations": 1, "code": 0x31, "warmup": 0, "mallocs": 1, "faults": 0,
                   "matrices": 1, "heap_open": 1},
    "saturated8_repeat5": {"iterations": 5, "code": 0x31, "warmup": 0, "mallocs": 5, "faults": 0,
                           "matrices": 5, "heap_open": 5},
    # Reflective: three warm-up readings are skipped; saturation after them still fails.
    "reflective_saturated8": {"iterations": 1, "code": 0x31, "warmup": 3, "mallocs": 1,
                              "faults": 0, "matrices": 1, "heap_open": 1},
    # Saturation only within the skipped readings is not seen.
    "reflective_skipped_saturation8": {"iterations": 1, "code": 0x00, "warmup": 3, "mallocs": 1,
                                       "faults": 0, "matrices": 1, "heap_open": 0},
    # Controls: exits that already released the buffer, or never made it.
    "inconsistent8": {"iterations": 1, "code": 0x34, "warmup": 0, "mallocs": 1, "faults": 0,
                      "matrices": 1, "heap_open": 0},
    "trigger_fails8": {"iterations": 1, "code": 0x72, "warmup": 0, "mallocs": 1, "faults": 0,
                       "matrices": 0, "heap_open": 0},
    "read_fails8": {"iterations": 1, "code": 0x72, "warmup": 0, "mallocs": 1, "faults": 0,
                    "matrices": 0, "heap_open": 0},
    "zero_measures": {"iterations": 1, "code": 0x5A, "warmup": None, "mallocs": 0, "faults": 0,
                      "matrices": 0, "heap_open": 0},
    "alloc_buffer8": {"iterations": 1, "code": 0x62, "warmup": None, "mallocs": 0, "faults": 1,
                      "matrices": 0, "heap_open": 0},
}


def fragment(text: str) -> str:
    """The compiled functions with the two thresholds they read, at their upstream lines."""
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


def _validate(results: dict) -> None:
    for case, expected in EXPECTED.items():
        baseline, patched = results["baseline"][case], results["patched"][case]
        if patched["heap_open"] or patched["frees"] != patched["mallocs"]:
            raise AssertionError(f"patched result for {case}: {patched['heap_open']} heap "
                                 f"block(s) open")
        for label, row in (("baseline", baseline), ("patched", patched)):
            if row["matrices_open"] or row["matrices_freed"] != row["matrices"]:
                raise AssertionError(f"{label} result for {case}: {row['matrices_open']} "
                                     f"matrix(es) open")
            count = expected["iterations"]
            if row["iterations"] != count or any(
                    len(row[key]) != count for key in ("codes", "warmup", "output_fnv")):
                raise AssertionError(f"read count for {case} differs within {label}")
            # Every read of a case sees the same bytes, so every read must agree.
            reads = list(zip(row["codes"], row["warmup"], row["output_fnv"]))
            if len(set(reads)) != 1:
                raise AssertionError(f"iterations for {case} differ within {label}")
        if (baseline["codes"], baseline["warmup"], baseline["output_fnv"]) != (
                patched["codes"], patched["warmup"], patched["output_fnv"]):
            raise AssertionError(f"results for {case} differ between the two texts")
        if set(baseline["codes"]) != {expected["code"]}:
            raise AssertionError(f"codes for {case}: {baseline['codes']}")
        if set(baseline["warmup"]) != {expected["warmup"]}:
            raise AssertionError(f"warm-up readings for {case}: {baseline['warmup']}")
        for label, row in (("baseline", baseline), ("patched", patched)):
            if (row["mallocs"], row["matrices"], row["faults"]) != (
                    expected["mallocs"], expected["matrices"], expected["faults"]):
                raise AssertionError(f"accounting for {case} in {label}: mallocs {row['mallocs']}, "
                                     f"matrices {row['matrices']}, faults {row['faults']}")
        if baseline["heap_open"] != expected["heap_open"]:
            raise AssertionError(f"baseline result for {case}: {baseline['heap_open']} heap "
                                 f"block(s) open")


@production_proof
def prove(archive: Path, output: Path, *, source: Path | None = None,
          compiler: str = "cc", sanitize: bool = False) -> dict:
    """Run the original and repaired white measurement on the same synthetic bytes.

    The function, the byte conversion, the averaging and the warm-up count are
    the pinned upstream text with upstream numsup; the probe stands in for the
    two transport calls and the dark subtraction, supplies instrument state and
    raw bytes, and wraps the function's own malloc, free, dmatrix and
    free_dmatrix to count the buffer and the matrix.
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
            if entry.endswith(".h") or relative.as_posix() in ("numlib/numsup.c", "License2.txt"):
                support[relative] = zipped.read(entry)
        support[Path("sa_config.h")] = zipped.read(PREFIX + "h/aconfig.h")
    baseline = before_source("munki_white_buffer_release", SOURCE_PATH, archive)
    patched = production_text(source, SOURCE_PATH)
    output.mkdir(parents=True, exist_ok=False)
    headers = output / "upstream"
    for relative, content in support.items():
        (headers / relative).parent.mkdir(parents=True, exist_ok=True)
        (headers / relative).write_bytes(content)
    diff = "".join(difflib.unified_diff(
        baseline.splitlines(True), patched.splitlines(True),
        fromfile="curve-released/munki_imp.c", tofile="white-buffer-released/munki_imp.c",
    ))
    (output / "munki-white-buffer-release.patch").write_text(diff)
    harness = Path(__file__).with_name("munki_white_buffer_release_probe.c")
    report: dict = dict(
        status="incomplete", archive_sha256=ARCHIVE_SHA256, original_sha256=SOURCE_SHA256,
        baseline_sha256=digest(baseline.encode()), patched_sha256=digest(patched.encode()),
        recognizer_sha256={"baseline": digest(recognizer(baseline).encode()),
                           "patched": digest(recognizer(patched).encode())},
        function_sha256={"baseline": digest(_function(baseline, FUNCTION).encode()),
                         "patched": digest(_function(patched, FUNCTION).encode())},
        fragment_sha256={"baseline": digest(fragment(baseline).encode()),
                         "patched": digest(fragment(patched).encode())},
        patch_sha256=digest(diff.encode()), proof_source_sha256=digest(Path(__file__).read_bytes()),
        harness_sha256=digest(harness.read_bytes()), platform=platform.platform(),
        hardware_tested=False, instrument_runtime_linked=False, cache_file_io=False,
        scope="actual ColorMunki white measurement, byte conversion, averaging and warm-up "
              "count with numsup over synthetic raw bytes and state; the reading buffer and "
              "the measurement matrix counted through the function's own calls",
        limitations=[
            "Leaks are counted as buffers allocated and not freed through the function's own "
            "malloc and free, and matrices made and not released through its own dmatrix and "
            "free_dmatrix; numsup's internal allocations are not counted and no byte "
            "accounting is claimed.",
            "The trigger and read calls are stand-ins that succeed, fail with a communication "
            "code, or fill the buffer with the case's bytes; no USB transport. The dark "
            "subtraction is a stand-in that subtracts the dark vector and reports the maximum; "
            "no linearisation, gain or integration scaling, so returned readings are synthetic "
            "and compared between the two texts, not against the instrument.",
            "The saturation limit, optimal target and warm-up time are synthetic constants; "
            "the instrument reads its own from EEPROM (munki_imp.c:9502-9532).",
            "The band-count assertion in munki_sens_to_raw (munki_imp.c:4913) is not "
            "exercised; the state matches the instrument's 137/128 (munki_imp.c:9397-9398).",
            "One allocation failure is injected, at the buffer; no other allocation is failed.",
            "How often a physical white reference saturates is not established.",
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
            (directory / "whitemeasure.inc").write_text(fragment(source))
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
                for p in (executable, directory / "probe.c", directory / "whitemeasure.inc")})
        _validate(report["results"])
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
