"""ColorMunki strip recognizer: release the patch-width curve on every exit.

munki_extract_patches_multimeas allocates a points array and fits an rspl curve
to the widths of the patches it finds (munki_imp.c:5752-5753). Four existing
sites delete the curve: too few points to fit (:5779, which then continues),
a patch too sparse inside the window search (:5866), too many consistent
patches (:5882) and no window finding enough (:5896). It is kept when a used
patch turns out too sparse after trimming (:5997-6006) and on success
(:6039-6053), so every successful read that retains a fitted curve leaves it
behind. When one of the two allocations fails (:5755-5763) neither the points
array nor the curve that was made is released. Argyll's chartread reads a
chart's strips in one process, so the success leak accumulates per strip; this
application's worker exits after each operation, which bounds it without
removing it.

The repair releases the curve at those two exits, as the four sites already
do, and releases whichever of the two allocations was made when the other
fails. Returned averages, flags and codes are unchanged.

The BEFORE input is the frozen predecessor; AFTER reads current production
source. The proof compiles the recognizer fragment the margin
proof compiles, original and repaired, with upstream numsup and rspl1, counts
curve creations and deletions through the curve's own destructor slot, and
shows the original leaks where the repair does not, with identical results.
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
from native_argyll.munki_scan_margin import SOURCE_PATH, SOURCE_SHA256, recognizer
from native_argyll.proof_inputs import (
    before_source,
    production_proof,
    production_support,
    production_text,
)
from native_argyll.spyder_calibration_slots import _function

REPAIR_ID = 'recipe:munki-curve-release'

RECOGNIZER = "munki_extract_patches_multimeas"
# The two trailing spaces after the vector frees are upstream's.
# Per case, over every read of the case in one process: the recognizer's code;
# curves created; the recognizer's own heap blocks allocated (the patch table
# and the points array); injected allocation faults; and what the ORIGINAL
# leaves behind (curves not deleted, heap blocks not freed). The repair leaves
# nothing behind. Codes, flags and returned averages must be equal in both
# texts and across the reads of a case.
EXPECTED = {
    "valid8": {"iterations": 1, "code": 0x00, "created": 1, "mallocs": 2, "faults": 0,
               "leaked": 1, "heap_open": 0},
    "valid8_repeat5": {"iterations": 5, "code": 0x00, "created": 5, "mallocs": 10, "faults": 0,
                       "leaked": 5, "heap_open": 0},
    "sparse6_8": {"iterations": 1, "code": 0x00, "created": 1, "mallocs": 2, "faults": 0,
                  "leaked": 1, "heap_open": 0},
    "count_low8": {"iterations": 1, "code": 0x3C, "created": 1, "mallocs": 2, "faults": 0,
                   "leaked": 0, "heap_open": 0},
    "count_high8": {"iterations": 1, "code": 0x3B, "created": 0, "mallocs": 1, "faults": 0,
                    "leaked": 0, "heap_open": 0},
    "valid3": {"iterations": 1, "code": 0x00, "created": 1, "mallocs": 2, "faults": 0,
               "leaked": 0, "heap_open": 0},
    # The points array fails: the curve that was made leaks in the original.
    "alloc_points_valid8": {"iterations": 1, "code": 0x62, "created": 1, "mallocs": 1, "faults": 1,
                            "leaked": 1, "heap_open": 0},
    # The curve fails: the points array that was made leaks in the original.
    "alloc_curve_valid8": {"iterations": 1, "code": 0x62, "created": 0, "mallocs": 2, "faults": 1,
                           "leaked": 0, "heap_open": 1},
}


def _validate(results: dict) -> None:
    for case, expected in EXPECTED.items():
        baseline, patched = results["baseline"][case], results["patched"][case]
        if patched["leaked"] or patched["deleted"] != patched["created"]:
            raise AssertionError(f"patched result for {case}: leaked {patched['leaked']} of "
                                 f"{patched['created']}")
        if patched["heap_open"] or patched["frees"] != patched["mallocs"]:
            raise AssertionError(f"patched result for {case}: {patched['heap_open']} heap "
                                 f"block(s) open")
        for label, row in (("baseline", baseline), ("patched", patched)):
            count = expected["iterations"]
            if row["iterations"] != count or any(
                    len(row[key]) != count for key in ("codes", "flags", "output_fnv")):
                raise AssertionError(f"read count for {case} differs within {label}")
            # Every read of a case sees the same input, so every read must agree.
            reads = list(zip(row["codes"], row["flags"], row["output_fnv"]))
            if len(set(reads)) != 1:
                raise AssertionError(f"iterations for {case} differ within {label}")
        if (baseline["codes"], baseline["flags"], baseline["output_fnv"]) != (
                patched["codes"], patched["flags"], patched["output_fnv"]):
            raise AssertionError(f"results for {case} differ between the two texts")
        if set(baseline["codes"]) != {expected["code"]}:
            raise AssertionError(f"codes for {case}: {baseline['codes']}")
        for label, row in (("baseline", baseline), ("patched", patched)):
            if (row["created"], row["mallocs"], row["faults"]) != (
                    expected["created"], expected["mallocs"], expected["faults"]):
                raise AssertionError(f"accounting for {case} in {label}: created {row['created']}, "
                                     f"mallocs {row['mallocs']}, faults {row['faults']}")
        if (baseline["leaked"], baseline["heap_open"]) != (
                expected["leaked"], expected["heap_open"]):
            raise AssertionError(f"baseline result for {case}: leaked {baseline['leaked']}, "
                                 f"{baseline['heap_open']} heap block(s) open")


@production_proof
def prove(archive: Path, output: Path, *, source: Path | None = None,
          compiler: str = "cc", sanitize: bool = False) -> dict:
    """Run the original and repaired recognizer on the same synthetic strips.

    The recognizer, numsup and rspl1 are the pinned upstream text; the probe
    supplies raw arrays and instrument state, wraps the curve constructor to
    count creations and each curve's destructor slot to count deletions.
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
    baseline = before_source("munki_curve_release", SOURCE_PATH, archive)
    patched = production_text(source, SOURCE_PATH)
    output.mkdir(parents=True, exist_ok=False)
    headers = output / "upstream"
    for relative, content in support.items():
        (headers / relative).parent.mkdir(parents=True, exist_ok=True)
        (headers / relative).write_bytes(content)
    diff = "".join(difflib.unified_diff(
        baseline.splitlines(True), patched.splitlines(True),
        fromfile="conversion-patched/munki_imp.c", tofile="curve-released/munki_imp.c",
    ))
    (output / "munki-curve-release.patch").write_text(diff)
    harness = Path(__file__).with_name("munki_curve_release_probe.c")
    report: dict = dict(
        status="incomplete", archive_sha256=ARCHIVE_SHA256, original_sha256=SOURCE_SHA256,
        baseline_sha256=digest(baseline.encode()), patched_sha256=digest(patched.encode()),
        recognizer_sha256={"original": digest(recognizer(original).encode()),
                           "baseline": digest(recognizer(baseline).encode()),
                           "patched": digest(recognizer(patched).encode())},
        function_sha256={"baseline": digest(_function(baseline, RECOGNIZER).encode()),
                         "patched": digest(_function(patched, RECOGNIZER).encode())},
        patch_sha256=digest(diff.encode()), proof_source_sha256=digest(Path(__file__).read_bytes()),
        harness_sha256=digest(harness.read_bytes()), platform=platform.platform(),
        hardware_tested=False, instrument_runtime_linked=False, cache_file_io=False,
        scope="actual ColorMunki recognizer, numsup and rspl1 with synthetic raw arrays and "
              "state; curve creations and deletions counted through the curve's own slots",
        limitations=[
            "Leaks are counted as curves created and not deleted, through the constructor "
            "and the destructor slot, and as the recognizer's own heap blocks allocated and "
            "not freed, through its malloc, realloc and free; rspl1's and numsup's internal "
            "allocations are not counted and no byte accounting is claimed.",
            "Allocation failures are injected once per case, at the points array or at the "
            "curve constructor; no other allocation is failed. A NULL constructor models "
            "standalone rspl1.c:465-467. Full rspl.c:129-131 calls fatal error() instead; "
            "its failure behavior is not replayed. The points-allocation failure applies "
            "with either constructor when the curve is successfully created.",
            "No acquisition, dark or white correction, wavelength conversion, cache I/O or "
            "optical tolerance; synthetic raw units only, sharp steps.",
            "The success and allocation-failure exits are exercised. Two of the four existing "
            "deletion sites are controls: too few fit points (valid3) and too many consistent "
            "patches (count_low8). Sparse-within-window and exhausted-window releases are "
            "preserved and source-checked, not exercised by these eight cases. "
            "No synthetic strip reached the "
            "late too-sparse exit with a curve alive: a patch detected at five rows or fewer "
            "is merged by the slope gain control when its neighbours are as short, and left "
            "unused when they are wide. That hunk is checked as text and compiled, not "
            "executed; a failed search does not prove it unreachable.",
            "How often a physical strip reaches the late failure is not established.",
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
            (directory / "recognizer.inc").write_text(recognizer(source))
            (directory / "probe.c").write_bytes(harness.read_bytes())
            executable = directory / ("probe.exe" if system == "Windows" else "probe")
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
                for p in (executable, directory / "probe.c", directory / "recognizer.inc")})
        _validate(report["results"])
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
