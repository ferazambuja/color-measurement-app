"""Reject invalid XRGA targets before any driver stores them.

ArgyllCMS 3.5.0's seven public option handlers store arbitrary xcalstd values.
The converters use that target as a three-element table index (xrga.c:179,197);
native (-1) is the explicit no-conversion sentinel, not a table row. None (-2)
means an absent request in the tools and must not be sent to a driver. Existing
CLI, environment and application inputs already filter to these four targets.

This repair changes only admission, not conversion equations. The proof compiles
the exact setter and effective-standard getter branches with the upstream enum.
It tests all valid prior/native states, invalid boundary values, refusal without
state changes, and recovery. Other option branches, initialization and transport
are not executed. Source extracts retain Graeme W. Gill's GPL-2.0-or-later notice.
"""
from __future__ import annotations

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
from native_argyll.ss_xy_metadata import _enum

REPAIR_ID = 'recipe:xcalstd-option'

BRANCH_SHA256 = {
    "spectro/i1pro.c": "0e056c258e5beb3c7962b45c7651705444fe4524c27a9acaad238c1323f71204",
    "spectro/i1pro3.c": "f47c3e6c664ef04e2a9217eea0669ba1afd5845a6a19c6010b83840223e3779a",
    "spectro/munki.c": "c3103a7266324fcc56bd0f58b4b64ecbbbe56d265cd112cdb5ddf1fcea26170b",
    "spectro/dtp20.c": "c43b54e9d01d26aacfd141839dbe89a2f95499c8a28042572eea9db67a86ebcf",
    "spectro/dtp22.c": "c43b54e9d01d26aacfd141839dbe89a2f95499c8a28042572eea9db67a86ebcf",
    "spectro/dtp41.c": "c43b54e9d01d26aacfd141839dbe89a2f95499c8a28042572eea9db67a86ebcf",
    "spectro/ss.c": "9aa65b797821a7e2a4ec185345b5af63bc65324842580e6b44421adad0086b60",
}
PATHS = tuple(BRANCH_SHA256)
VALUES = (-2147483648, -100, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 100, 2147483647)


def branch(text: str, path: str, *, get: bool = False) -> str:
    arg = "ot" if path == "spectro/i1pro3.c" else "m"
    marker = f"if ({arg} == inst_opt_{'get' if get else 'set'}_xcalstd) {{"
    if text.count(marker) != 1:
        raise ValueError(f"Missing or ambiguous xcalstd branch: {path}")
    start = text.index(marker)
    body = text[start:text.index("\n\t}", start) + 3]
    if body.count("{") != 1 or body.count("}") != 1:
        raise ValueError(f"Unexpected nested xcalstd branch: {path}")
    return body


def _probe_source(texts: dict, header: str, types: str) -> str:
    definitions = []
    for symbol in ("inst_ok", "inst_bad_parameter", "inst_opt_set_xcalstd",
                   "inst_opt_get_xcalstd"):
        value = re.search(r"\b" + symbol + r"\s*=\s*(0x[0-9a-fA-F]+|[0-9]+)", header)
        if value is None:
            raise ValueError(f"Missing upstream constant: {symbol}")
        definitions.append(f"#define {symbol} {value.group(1)}")
    functions = []
    for path, text in texts.items():
        name = Path(path).stem
        arg = "ot" if name == "i1pro3" else "m"
        setup = "state *m = p;" if name == "i1pro3" else ""
        functions.append(f"static int {name}_option(state *p, int {arg}, ...) {{\n"
                         + setup + branch(text, path) + "\n" + branch(text, path, get=True)
                         + "\nabort();\n}\n")
    return ("/* Exact ArgyllCMS option branches, Graeme W. Gill, GPL-2.0-or-later. */\n"
            "#include <stdarg.h>\n#include <stdio.h>\n#include <stdlib.h>\n"
            + _enum(types, "xcalstd") + "\n" + "\n".join(definitions) + "\n"
            "typedef struct state { xcalstd target_calstd, native_calstd; void *m; } state;\n"
            "typedef state i1proimp; typedef state munkiimp;\n"
            + "\n".join(functions) + r'''
int main(void) {
    int values[] = {@@VALUES@@};
    int (*options[])(state *, int, ...) = {@@OPTIONS@@};
    state p;
    int f, n, prior, i;
    puts("[");
    for (f = 0; f < 7; f++) for (n = 0; n < 3; n++) for (prior = -1; prior <= 2; prior++)
        for (i = 0; i < (int)(sizeof(values)/sizeof(values[0])); i++) {
            int rv, grv, recovery; xcalstd effective = xcalstd_none, again = xcalstd_none;
            p.m = &p; p.native_calstd = (xcalstd)n; p.target_calstd = (xcalstd)prior;
            rv = options[f](&p, inst_opt_set_xcalstd, (xcalstd)values[i]);
            grv = options[f](&p, inst_opt_get_xcalstd, &effective);
            printf("%s[%d,%d,%d,%d,%d,%d,%d,%d", f||n||prior!=-1||i ? "," : "",
                   f,n,prior,values[i],rv,(int)p.target_calstd,grv,(int)effective);
            recovery = options[f](&p, inst_opt_set_xcalstd, xcalstd_native);
            grv = options[f](&p, inst_opt_get_xcalstd, &again);
            printf(",%d,%d,%d,%d]\n",recovery,(int)p.target_calstd,grv,(int)again);
        }
    puts("]"); return 0;
}
'''.replace("@@VALUES@@", ",".join(map(str, VALUES)))
            .replace("@@OPTIONS@@", ",".join(Path(p).stem + "_option" for p in PATHS)))


def _validate(rows: list, *, repaired: bool) -> None:
    expected = []
    for f, path in enumerate(PATHS):
        for native in range(3):
            for prior in range(-1, 3):
                for target in VALUES:
                    refused = repaired and target not in (-1, 0, 1, 2)
                    stored = prior if refused else target
                    expected.append([f, native, prior, target, 0x180000 if refused else 0,
                                     stored, 0, native if stored == -1 else stored,
                                     0, -1, 0, native])
    if rows != expected:
        first = next((i for i, pair in enumerate(zip(rows, expected)) if pair[0] != pair[1]),
                     min(len(rows), len(expected)))
        raise AssertionError(f"{'repaired' if repaired else 'original'} xcalstd result differs "
                             f"at case {first}: {rows[first:first+1]} != {expected[first:first+1]}")


@production_proof
def prove(archive: Path, output: Path, *, source: Path | None = None,
          compiler: str = "cc", sanitize: bool = False) -> dict:
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        # Census the entire source archive, not a handpicked driver subset.
        census = sorted(name.removeprefix(PREFIX) for name in z.namelist()
                        if name.endswith(".c") and re.search(
                            rb"if\s*\([^\n]*==\s*inst_opt_set_xcalstd\s*\)", z.read(name)))
        if census != sorted(PATHS):
            raise ValueError(f"xcalstd option census changed: {census}")
        header = z.read(PREFIX + "spectro/inst.h").decode()
        types = z.read(PREFIX + "spectro/insttypes.h").decode()
        notice = z.read(PREFIX + "License2.txt")
    after = {p: production_text(source, p) for p in PATHS}
    before = {p: before_source("xcalstd_option", p, archive) for p in PATHS}
    output.mkdir(parents=True, exist_ok=False)
    (output / "Argyll-License2.txt").write_bytes(notice)
    report = dict(status="incomplete", archive_sha256=ARCHIVE_SHA256, census=census,
                  before_sha256={p: digest(before[p].encode()) for p in PATHS},
                  patched_sha256={p: digest(after[p].encode()) for p in PATHS},
                  proof_sha256=digest(Path(__file__).read_bytes()), sanitize=sanitize,
                  scope="exact setter/getter branches; no initialization, converters or transport",
                  hardware_tested=False, commands=[], results={})
    try:
        for label, texts in (("baseline", before), ("patched", after)):
            unit = output / f"{label}.c"
            unit.write_text(_probe_source(texts, header, types), encoding="utf-8", newline="\n")
            exe = output / (label + (".exe" if platform.system() == "Windows" else ""))
            argv = [cc, "-std=c99", "-O1", "-g", *sanitizers.flags(sanitize),
                    str(unit), "-o", str(exe)]
            run = subprocess.run(argv, capture_output=True, text=True, timeout=120)
            (output / f"{label}-compile.log").write_text(run.stdout + run.stderr)
            report["commands"].append({"argv": argv, "returncode": run.returncode})
            run.check_returncode()
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)
            (output / f"{label}.log").write_text(run.stdout + run.stderr)
            sanitizers.refuse(run.stderr, label)
            run.check_returncode()
            if run.stderr.strip():
                raise RuntimeError(f"Unexpected xcalstd proof diagnostic: {label}")
            rows = json.loads(run.stdout)
            _validate(rows, repaired=label == "patched")
            report["results"][label] = {"cases": len(rows), "sha256": digest(run.stdout.encode())}
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
