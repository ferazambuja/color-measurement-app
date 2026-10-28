"""The public scan-tolerance option refuses a nonfinite ratio before the native setter.

The three handlers are Graeme W. Gill's GPL-2.0-or-later ArgyllCMS 3.5.0 work:
``i1pro_get_set_opt`` (``spectro/i1pro.c:754``, branch :780-787),
``i1pro3_get_set_opt`` (``spectro/i1pro3.c:775``, branch :802-809) and
``munki_get_set_opt`` (``spectro/munki.c:778``, branch :811-818), pristine
line numbers. Each reads the ``inst_opt_scan_toll`` double from its variable
arguments and hands it to its internal setter (``i1pro_imp.c:10386``,
``i1pro3_imp.c:4747``, ``munki_imp.c:8036``), which stores it as
``scan_toll_ratio``; the i1Pro3 setter first floors it at 1e-6. None of the
three tests the value, so NaN and positive infinity reach the consistency
thresholds (``PATCH_CONS_THR * scan_toll_ratio``) unchanged, where a NaN or
infinite limit defeats the ``maxdev > limit`` comparison; the i1Pro3 floor
turns negative infinity into a stored 1e-6, a finite ratio no caller asked
for, while the other two store it as given. ``chartread.c:2687``
parses the ratio with ``atof`` and forwards it at ``:736-739``; the API
specifies a double modifier and no documented range.

The repair returns ``inst_bad_parameter`` from the public handler when the
ratio is not finite, before the setter runs, so the stored ratio and every
finite policy (negative, zero, subnormal, the i1Pro3 floor) are unchanged.
Validation belongs at that boundary: the generic code passed through a native
``*_interp_code`` would be masked. The reviewed fork proposal tests
``toll_ratio >= -DBL_MAX && toll_ratio <= DBL_MAX`` for C89 hosts; these files
include ``<math.h>`` and the composed build's other guards use ``isfinite``, so
this transform does the same. The pin is each handler function, pristine and
repaired. The application already refuses a nonfinite tolerance in its own
request validation; this guard is the driver's, for every caller.
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
from native_argyll.proof_inputs import before_source, production_proof, production_text
from native_argyll.scan_normalization import _function

REPAIR_ID = 'recipe:scan-tolerance-option'

FAMILIES = {"spectro/i1pro.c": "i1pro", "spectro/i1pro3.c": "i1pro3", "spectro/munki.c": "munki"}
PATHS = tuple(FAMILIES)


def signature(path: str) -> str:
    return f"static inst_code\n{FAMILIES[path]}_get_set_opt("


def delegation(path: str) -> str:
    family = FAMILIES[path]
    return f"\t\treturn {family}_interp_code(p, {family}_set_scan_toll(p, toll_ratio));\n"


# The values the option receives, in order: three nonfinite, then finite extremes,
# negatives, signed zero, subnormals, the i1Pro3 floor's neighbours and ordinary ratios.
VALUES = ("NAN", "INFINITY", "-INFINITY", "-DBL_MAX", "-2.", "-0.", "0.", "DBL_MIN/8.",
          "DBL_MIN", "0.999999e-6", "1e-6", "1.000001e-6", ".5", "1.", "2.", "DBL_MAX")
NONFINITE = 3
INST_OK = 0x000000
INST_BAD_PARAMETER = 0x180000
INST_OPT_SCAN_TOLL = 0x0016
PRIOR = 1.25

_HARNESS = r'''
/* Inert option-boundary harness. The option branch and the internal setter are
 * exact ArgyllCMS extracts, GPL-2.0-or-later, Graeme W. Gill; the notice is
 * retained alongside. No transport, instrument or product binary runs. */
#include <stdio.h>
#include <stdarg.h>
#include <float.h>
#include <math.h>
#include <stdlib.h>
@@CONSTANTS@@
typedef int inst_code;
typedef int inst_opt_type;
typedef int @@FAMILY@@_code;
typedef struct { double scan_toll_ratio; } @@FAMILY@@imp;
typedef struct { void *m; } @@FAMILY@@;
#define @@UPPER@@_OK 0
static int delegates;
@@SETTER@@
static int translated(@@FAMILY@@ *p,int code) { (void)p; return code; }
static int delegated(@@FAMILY@@ *p,double value) {
    delegates++;
    return @@FAMILY@@_set_scan_toll(p,value);
}
#define @@FAMILY@@_set_scan_toll(p,v) delegated(p,v)
#define @@FAMILY@@_interp_code(p,v) translated(p,v)
static inst_code option(@@FAMILY@@ *p, inst_opt_type @@ARG@@, ...) {
@@BRANCH@@
    abort();
}
int main(void) {
    double values[]={@@VALUES@@};
    @@FAMILY@@imp state; @@FAMILY@@ p; int i;
    p.m=&state;
    puts("[");
    for(i=0;i<(int)(sizeof(values)/sizeof(values[0]));i++) {
        int rv,calls; double stored;
        state.scan_toll_ratio=@@PRIOR@@; delegates=0;
        rv=option(&p,inst_opt_scan_toll,values[i]); calls=delegates;
        stored=state.scan_toll_ratio;
        if(option(&p,inst_opt_scan_toll,2.)!=inst_ok || state.scan_toll_ratio!=2.) abort();
        printf("%s{\"index\":%d,\"code\":%d,\"calls\":%d,\"stored\":\"%a\"}\n",
               i ? "," : "",i,rv,calls,stored);
    }
    puts("]"); return 0;
}
'''


def _branch(text: str, path: str) -> str:
    """The exact option branch, taken from the pinned handler only."""
    body = _function(text, signature(path))
    match = re.search(r"\tif \((?:m|ot) == inst_opt_scan_toll\) \{", body)
    if match is None:
        raise ValueError(f"Scan-tolerance branch missing in {path}")
    return body[match.start():body.index("\n\t}\n", match.start()) + 4]


def _probe_source(text: str, path: str, setter: str, constants: dict) -> str:
    family = FAMILIES[path]
    defined = "\n".join(f"#define {name} {value:#x}" for name, value in constants.items())
    return (_HARNESS.replace("@@CONSTANTS@@", defined).replace("@@FAMILY@@", family)
            .replace("@@UPPER@@", family.upper()).replace("@@SETTER@@", setter)
            .replace("@@ARG@@", "ot" if family == "i1pro3" else "m")
            .replace("@@BRANCH@@", _branch(text, path)).replace("@@VALUES@@", ",".join(VALUES))
            .replace("@@PRIOR@@", repr(PRIOR)))


def _validate(results: dict) -> None:
    for path, family in FAMILIES.items():
        baseline, patched = results[family]["baseline"], results[family]["patched"]
        if len(baseline) != len(VALUES) or len(patched) != len(VALUES):
            raise AssertionError(f"{family}: {len(baseline)}/{len(patched)} rows, not "
                                 f"{len(VALUES)}")
        for index, value in enumerate(VALUES):
            before, after = baseline[index], patched[index]
            if index < NONFINITE:
                stored = float.fromhex(before["stored"])
                # The original stores the value it was given, except that the
                # i1Pro3 floor turns negative infinity into a finite 1e-6.
                expected = (1e-6 if family == "i1pro3" and value == "-INFINITY"
                            else float(value.lower()))
                if (before["code"] != INST_OK or before["calls"] != 1
                        or (stored != expected and not (stored != stored and value == "NAN"))):
                    raise AssertionError(f"original {family} result for {value}: code "
                                         f"{before['code']:#x}, calls {before['calls']}, "
                                         f"stored {before['stored']}")
                if (after["code"] != INST_BAD_PARAMETER or after["calls"] != 0
                        or float.fromhex(after["stored"]) != PRIOR):
                    raise AssertionError(f"patched {family} result for {value}: code "
                                         f"{after['code']:#x}, calls {after['calls']}, "
                                         f"stored {after['stored']}")
            elif before != after:
                raise AssertionError(f"finite {family} result changed for {value}: {after}")
        floor = float.fromhex(patched[VALUES.index("0.999999e-6")]["stored"])
        if (floor == 1e-6) != (family == "i1pro3"):
            raise AssertionError(f"{family}: setter floor policy changed, stored {floor!r}")


@production_proof
def prove(
    archive: Path,
    output: Path,
    *,
    source: Path | None = None,
    compiler: str = "cc",
    sanitize: bool = False,
) -> dict:
    """Run each exact option branch with its exact setter, original and repaired."""
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        setters = {path: _function(zipped.read(PREFIX + path.replace(".c", "_imp.c")).decode(),
                                   f"{family}_code {family}_set_scan_toll(")
                   for path, family in FAMILIES.items()}
        header = zipped.read(PREFIX + "spectro/inst.h").decode()
        notice = zipped.read(PREFIX + "License2.txt")
    constants = {}
    for name, expected in (("inst_ok", INST_OK), ("inst_bad_parameter", INST_BAD_PARAMETER),
                           ("inst_opt_scan_toll", INST_OPT_SCAN_TOLL)):
        match = re.search(r"\b" + name + r"\s*=\s*(0x[0-9a-fA-F]+|[0-9]+)", header)
        if match is None or int(match.group(1), 0) != expected:
            raise ValueError(f"Pinned inst.h constant changed: {name}")
        constants[name] = expected
    texts = {path: (before_source("scan_tolerance_option", path, archive),
                    production_text(source, path)) for path in PATHS}
    output.mkdir(parents=True, exist_ok=False)
    (output / "Argyll-License2.txt").write_bytes(notice)
    report = {
        "status": "incomplete", "archive_sha256": ARCHIVE_SHA256, "sanitize": sanitize,
        "before_sha256": {path: digest(texts[path][0].encode()) for path in PATHS},
        "patched_sha256": {path: digest(texts[path][1].encode()) for path in PATHS},
        "function_sha256": {path: {
            "before": digest(_function(texts[path][0], signature(path)).encode()),
            "after": digest(_function(texts[path][1], signature(path)).encode())}
            for path in PATHS},
        "setter_sha256": {path: digest(setters[path].encode()) for path in PATHS},
        "constants": constants, "values": list(VALUES), "prior_ratio": PRIOR,
        "proof_source_sha256": digest(Path(__file__).read_bytes()),
        "platform": platform.platform(), "hardware_tested": False,
        "instrument_runtime_linked": False, "commands": [], "results": {},
        "scope": "exact public option branch with variable arguments and exact internal "
                 "setter; other option branches omitted; the native code translator is a "
                 "pass-through stand-in; no instrument initialization, chartread, transport "
                 "or product binary runs",
    }
    windows = platform.system() == "Windows"
    try:
        for path, family in FAMILIES.items():
            before, after = texts[path]
            (output / f"{family}.patch").write_text("".join(difflib.unified_diff(
                before.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile=f"composed/{path}", tofile=f"scan-tolerance/{path}")),
                encoding="utf-8", newline="\n")
            report["results"][family] = {}
            for label, text in (("baseline", before), ("patched", after)):
                unit = output / f"{family}-{label}.c"
                unit.write_text(_probe_source(text, path, setters[path], constants),
                                encoding="utf-8", newline="\n")
                executable = output / (f"{family}-{label}" + (".exe" if windows else ""))
                argv = [cc, "-std=gnu99", "-O1", "-g", *sanitizers.flags(sanitize),
                        str(unit), "-o", str(executable), "-lm"]
                run = subprocess.run(argv, capture_output=True, text=True, timeout=120)
                (output / f"{family}-{label}-compile.log").write_text(
                    run.stdout + run.stderr, encoding="utf-8", newline="\n")
                report["commands"].append({"argv": argv, "returncode": run.returncode})
                run.check_returncode()
                run = subprocess.run([str(executable)], capture_output=True, text=True,
                                     timeout=10)
                (output / f"{family}-{label}.log").write_text(
                    run.stdout + run.stderr, encoding="utf-8", newline="\n")
                sanitizers.refuse(run.stderr, f"{family}/{label}")
                run.check_returncode()
                if run.stderr.strip():
                    raise RuntimeError(f"Unexpected inert-probe diagnostics for {family}/{label}")
                report["results"][family][label] = json.loads(run.stdout)
        _validate(report["results"])
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n",
                                            encoding="utf-8", newline="\n")
    return report
