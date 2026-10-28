"""Shared display-type list: release loaded entries when the CCMX listing fails.

inst_creat_disptype_list (inst.c:912) grows one list of display types from the
driver's static table, the installed CCSS files and the matching installed CCMX
files. A CCSS entry takes ownership of the file's path and spectral sets
(:981, :983); inst_del_disptype_list (:842-854) releases both with the array.
When list_iccmx fails (:996-999) the function frees the array alone, so every
CCSS entry already moved into it leaks its path and its sets. list_iccmx fails
only when an allocation fails (:1174-1178, :1189-1198, :1220-1230, :1232-1243),
and CCSS entries exist only where the driver asks for them (i1d3.c:4076, :4153,
:4176; spyd2.c:3795, :3856, :3879, :3909 from hardware version 7). The earlier
list_iccss failure (inst.c:956-959) frees an array holding only static entries, which
own nothing: the struct copy at :950 carries no path or sets and no driver table
sets them. expand_dlist releases the whole list itself when its realloc fails
(:863-866), so the three growth exits are complete.

The repair releases the list through its own destructor on that one exit.
Nothing else changes.

The selector-exhaustion exit (:1110-1113) does not orphan the list: it is
stored through the caller's pointers (:1105-1108) before the error is
returned, all 40 call sites pass the
instrument's own pointers, and every destructor releases them since the
display-list release. The exit is also unreachable as written: the two-letter
branch sets sel[0] to '_' (:1077), hands &sel[1] to disptechs_set_sel (:1078),
which writes only through that pointer (disptechs.c:518, :549), and then tests
sel[0] (inst.c:1079). With a zeroed suffix, exhaustion leaves a bare "_" and returns
inst_ok. A loaded entry may instead retain a nonzero heap byte at sel[1], which
the assignment helper mistakes for an existing selector. The poisoned-heap
proof observes that earlier failure deterministically; it does not exercise
the real selector lookup or repair selector assignment.

The pin is the function, so the repair composes onto inst.c after the
constructor check the broad build already applies. The proof compiles the exact
list functions and selector assignment over a tracked allocator, with scripted
CCSS and CCMX listings, original and repaired.

The authenticated CR/PR contribution already owns the member release, fresh
suffix initialization and cleanup before selector-exhaustion handoff. The proof
compares the frozen failing baseline with that current production constructor
over the same scripted listings, without rebuilding an earlier repair chain.
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
from native_argyll.disptype_list_release import _shared
from native_argyll.proof_inputs import before_source, production_proof, production_text
from native_argyll.spyder_calibration_slots import ARCHIVE_SHA256, PREFIX, _function, digest

REPAIR_ID = 'recipe:disptype-list-member-release'

SOURCE_PATH = "spectro/inst.c"
FUNCTION = "inst_creat_disptype_list"
CONTRIBUTED_FUNCTION_SHA256 = "9b723bfebcae34485738d12fcbb266a4be56773b91f632d3dfed9d0b7f934bda"
#: The files the proof compiles from; the list helpers and their declarations.
SOURCES = ("spectro/inst.h", "spectro/inst.c", "spectro/disptechs.h", "spectro/disptechs.c",
           "icc/icc_util.c")
INST_OK, INTERNAL_ERROR = 0x000000, 0x060000       # inst.h:139, :145
# Per case, over every attempt in one process: the code; whether a list was
# handed back through the caller's pointers; its entries; two-letter selectors
# whose second byte the constructor never wrote (the probe poisons fresh heap
# bytes, so this is observed, not inherited from zero pages); blocks the
# ORIGINAL leaves allocated after every handed list is released as the
# destructors release it. The repair leaves none.
EXPECTED = {
    "static_ccmx_fails": {"iterations": 1, "code": INTERNAL_ERROR, "handed": 0, "entries": 0,
                          "unassigned": 0, "left": 0},
    # Two CCSS entries already own a path and a set each when the CCMX listing fails.
    "ccss2_ccmx_fails": {"iterations": 1, "code": INTERNAL_ERROR, "handed": 0, "entries": 0,
                         "unassigned": 0, "left": 4},
    "ccss2_ccmx_fails_repeat5": {"iterations": 5, "code": INTERNAL_ERROR, "handed": 0,
                                 "entries": 0, "unassigned": 0, "left": 20},
    "ccss2_ccmx3": {"iterations": 1, "code": INST_OK, "handed": 1, "entries": 7,
                    "unassigned": 0, "left": 0},
    "ccss_fails": {"iterations": 1, "code": INTERNAL_ERROR, "handed": 0, "entries": 0,
                   "unassigned": 0, "left": 0},
    # The second growth fails inside the CCMX loop: expand_dlist releases everything.
    "ccss2_ccmx6_grow_fails": {"iterations": 1, "code": INTERNAL_ERROR, "handed": 0,
                               "entries": 0, "unassigned": 0, "left": 0},
    # 2 static + 130 corrections; 61 single-letter selectors, then all 71
    # two-letter entries keep "_" plus the heap byte, because the loaded entries
    # initialise only sel[0] and disptechs_set_sel treats a non-zero sel[1] as
    # assigned (disptechs.c:496-499); the exhaustion exit at :1079 is not taken.
    "ccmx130": {"iterations": 1, "code": INST_OK, "handed": 1, "entries": 132,
                "unassigned": 71, "left": 0},
}
# The contributed constructor writes each two-letter suffix before assigning it
# and draws suffixes from a larger set, so no entry keeps the heap byte; every
# other outcome, and the release on every exit, matches the stock repair.
EXPECTED_CONTRIBUTED = {
    case: {**expected, "unassigned": 0, "left": 0} for case, expected in EXPECTED.items()
}


def _validate(results: dict) -> None:
    variants = {"baseline": EXPECTED, "patched": EXPECTED_CONTRIBUTED}
    for case in EXPECTED:
        for label, expectations in variants.items():
            row, expected = results[label][case], expectations[case]
            if label != "baseline" and row["left"]:
                raise AssertionError(f"{label} result for {case}: {row['left']} block(s) left")
            if row["untracked_frees"]:
                raise AssertionError(f"{label} result for {case}: untracked free")
            count = expected["iterations"]
            if row["iterations"] != count or any(
                    len(row[key]) != count
                    for key in ("codes", "handed", "entries", "unassigned")):
                raise AssertionError(f"attempt count for {case} differs within {label}")
            attempts = list(zip(row["codes"], row["handed"], row["entries"], row["unassigned"]))
            if len(set(attempts)) != 1:
                raise AssertionError(f"attempts for {case} differ within {label}")
            if attempts[0] != (expected["code"], expected["handed"], expected["entries"],
                               expected["unassigned"]):
                raise AssertionError(f"{label} result for {case}: {attempts[0]}")
        baseline = results["baseline"][case]
        if baseline["left"] != EXPECTED[case]["left"]:
            raise AssertionError(f"baseline result for {case}: {baseline['left']} block(s) left")


@production_proof
def prove(archive: Path, output: Path, *, source: Path | None = None,
          compiler: str = "cc", sanitize: bool = False) -> dict:
    """Run the exact list construction, original and repaired, over scripted listings.

    The failing constructor is frozen; the patched variant is the current
    production constructor, including its initialized selectors and member
    release. The list helpers run over a tracked allocator; CCSS and CCMX listings are
    scripted stand-ins that allocate what the real readers allocate for the
    members the constructor takes, and may fail as the real readers fail.
    """
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        sources = {name: zipped.read(PREFIX + name).decode() for name in SOURCES}
        licence = zipped.read(PREFIX + "License2.txt")
    baseline = before_source("disptype_list_member_release", SOURCE_PATH, archive)
    patched = production_text(source, SOURCE_PATH)
    variants = (("baseline", baseline), ("patched", patched))
    output.mkdir(parents=True, exist_ok=False)
    (output / "Argyll-License2.txt").write_bytes(licence)
    diff = "".join(difflib.unified_diff(
        baseline.splitlines(True), patched.splitlines(True),
        fromfile="a/" + SOURCE_PATH, tofile="b/" + SOURCE_PATH))
    (output / "disptype-list-member-release.patch").write_text(diff)
    harness = Path(__file__).with_name("disptype_list_member_release_probe.c")
    report: dict = dict(
        status="incomplete", archive_sha256=ARCHIVE_SHA256,
        source_sha256={name: digest(text.encode()) for name, text in sources.items()},
        function_sha256={label: digest(_function(text, FUNCTION).encode())
                         for label, text in variants},
        patch_sha256=digest(diff.encode()), proof_source_sha256=digest(Path(__file__).read_bytes()),
        harness_sha256=digest(harness.read_bytes()), platform=platform.platform(),
        hardware_tested=False, instrument_runtime_linked=False,
        scope="exact inst_creat_disptype_list, expand_dlist, inst_del_disptype_list and "
              "disptechs_set_sel over a tracked allocator with scripted CCSS and CCMX listings",
        limitations=[
            "The CCSS and CCMX listings are scripted stand-ins: each entry allocates a path, "
            "a description and, for CCSS, an opaque sample-set block as the readers do; no "
            "file is read and no spectrum is parsed. A listing failure returns NULL having "
            "allocated nothing, as the readers do after releasing their partial work.",
            "Blocks are counted by wrapping malloc, calloc, realloc, free and strdup in the "
            "compiled unit; what is left is a count of live blocks, not a leak detector's "
            "report, and no byte accounting is claimed.",
            "The common object base is reduced to the log and type members the constructor "
            "reads; the static table is this probe's two entries.",
            "One growth failure is injected, at the second realloc; the first growth and "
            "the CCSS-loop growth take the same released exit and are source-checked.",
            "Fresh heap bytes are poisoned with 0x7f, including each realloc tail, so a "
            "selector byte the constructor never wrote is observed as such; 130 scripted "
            "corrections against 61 fallback characters show every two-letter selector "
            "unassigned. No physical instrument is claimed to install that many.",
        ],
        sanitize=sanitize, commands=[], results={}, products_sha256={},
    )
    system = platform.system()
    try:
        for label, text in variants:
            directory = output / label
            directory.mkdir()
            declarations, functions = _shared({**sources, SOURCE_PATH: text})
            (directory / "declarations.h").write_text(declarations)
            (directory / "functions.inc").write_text(functions)
            (directory / "probe.c").write_bytes(harness.read_bytes())
            executable = directory / ("probe.exe" if system == "Windows" else "probe")
            command = [cc, "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror",
                       "-Wno-unused-variable", "-Wno-unused-but-set-variable",
                       "-Wno-unused-parameter", "-Wno-missing-field-initializers",
                       "-Wno-char-subscripts", *sanitizers.flags(sanitize),
                       *(["-O0", "-g"] if sanitize else []),
                       str(directory / "probe.c"), "-o", str(executable), "-lm"]
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
            report["products_sha256"].update({
                str(p.relative_to(output)): digest(p.read_bytes())
                for p in (executable, directory / "probe.c", directory / "functions.inc",
                          directory / "declarations.h")})
        _validate(report["results"])
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
