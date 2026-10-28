"""Refuse custom filters the spectral interpolators cannot evaluate.

ArgyllCMS 3.5.0's seven ``inst_opt_set_custom_filter`` handlers store any
supplied spectrum, and ``ipatch_convert_custom_filter`` (spectro/inst.c) divides
every band by the interpolated filter value without checking it. Both
interpolators clamp to the filter's wavelength range (xspect.c:4085-4092,
:4159-4166), but the cubic branch selected at and above 5.01 nm spacing
(:4346-4355) can return zero or a negative value from strictly positive samples
(a 400-430 nm filter [9, 1, 1, 9] is zero at 415 nm; [10, 1, 1, 10] is -0.125),
reads four surrounding samples (:4102-4117) so fewer than four are read past the
sample count, and a zero normalisation makes every divisor infinite and every
quotient a fabricated zero (getval_xspec, :4380-4384).

The repair adds ``inst_valid_custom_filter`` (a finite ordered range, a sample
count the selected interpolator can use, finite positive normalisation and finite
samples), refuses such a filter at the setters with ``inst_bad_parameter``
before it is stored, and makes the converter check every actual divisor and
quotient before any patch changes, returning failure with the patches untouched.
It also integrates each corrected scratch spectrum without clamping, which would
turn an overflowed -inf into a finite zero, and refuses nonfinite XYZ before
publishing any patch. Valid filters convert exactly as before. It composes
after the conversion allocation repair, whose int-returning converter carries the failure. The
application offers no custom-filter route. The standalone proof uses pinned
interpolation; the final application's native resync removes the four-sample
restriction because its cubic evaluator has a linear fallback. The production
proof accepts that three-sample filter and uses the actual production interpolator.
This safeguard accepts
unused non-positive samples, and refuses a non-positive applied divisor before
dividing by it. The CR fork's validator and converter carry the same rule.
Extracts retain Graeme W. Gill's GPL-2.0-or-later notice.
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
from native_argyll.munki_ledtemp_slope import ARCHIVE_SHA256, PREFIX, digest
from native_argyll.proof_inputs import before_source, production_proof, production_text
from native_argyll.spyder_calibration_slots import _function

REPAIR_ID = 'recipe:filter-values'

SETTERS = {"spectro/i1pro.c": "imp", "spectro/i1pro3.c": "m", "spectro/munki.c": "imp",
           "spectro/dtp20.c": "p", "spectro/dtp22.c": "p", "spectro/dtp41.c": "p",
           "spectro/ss.c": "p"}
PATHS = ("spectro/inst.c", "spectro/inst.h", *SETTERS)


def _preamble(inst_c: str) -> str:
    start = inst_c.index("#include <stdio.h>")
    end = inst_c.index('#include "insttypeinst.h"\n') + len('#include "insttypeinst.h"\n')
    return inst_c[start:end]


_ONES = ",".join(["1"] * 36)
_TWOS = ",".join(["2"] * 36)
CONVERTER_MAIN = r'''
/* Harness: real interpolation, observer and XYZ integration; a counted factory. */
static int live, allocations, releases, fail_at;
static void stub_del(xsp2cie *p) { live--; releases++; free(p); }
xsp2cie *new_xsp2cie(icxIllumeType ilType, double temp, xspect *custIllum,
                     icxObserverType obType, xspect custObserver[3],
                     icColorSpaceSignature rcs, icxClamping clamp) {
    int j;
    xsp2cie *p = (xsp2cie *)calloc(1, sizeof(xsp2cie));
    if (ilType != icxIT_none || temp != 0.0 || custIllum != NULL
     || obType != icxOT_CIE_1931_2 || custObserver != NULL || rcs != icSigXYZData) abort();
    if (p == NULL || allocations + 1 == fail_at) { free(p); return NULL; }
    allocations++; live++;
    p->isemis = 1; p->clamp = clamp; p->spec_bw = 1.0;
    for (j = 0; j < 3; j++) p->observer[j] = ob_CIE_1931_2[j];
    p->spec_wl_short = p->observer[1].spec_wl_short;
    p->spec_wl_long = p->observer[1].spec_wl_long;
    p->del = stub_del; p->convert = xsp2cie_convert;
    return p;
}
static void number(const char *k, double v, const char *tail) {
    if (v != v) printf("\"%s\":\"nan\"%s", k, tail);
    else if (v > DBL_MAX || v < -DBL_MAX) printf("\"%s\":\"%sinf\"%s", k, v < 0 ? "-" : "", tail);
    else printf("\"%s\":%.17g%s", k, v, tail);
}
static void filt(xspect *f, int n, double lo, double hi, double norm, const double *v, int vlen) {
    int i; memset(f, 0x7f, sizeof *f);   /* poison: unset samples are visible garbage */
    f->spec_n = n; f->spec_wl_short = lo; f->spec_wl_long = hi; f->norm = norm;
    for (i = 0; i < n && i < XSPECT_MAX_BANDS; i++) f->spec[i] = v[i % vlen];
}
static void reading(ipatch *vals, double level) {
    int i, j; memset(vals, 0, 2 * sizeof *vals);
    for (i = 0; i < 2; i++) {
        vals[i].mtype = inst_mrt_emission; vals[i].XYZ_v = 1;
        vals[i].sp.spec_n = 71; vals[i].sp.spec_wl_short = 380.0; vals[i].sp.spec_wl_long = 730.0;
        vals[i].sp.norm = 1.0;
        for (j = 0; j < 71; j++) vals[i].sp.spec[j] = level * (1.0 + 0.01 * j + i);
        vals[i].XYZ[0] = vals[i].XYZ[1] = vals[i].XYZ[2] = -1.0;
    }
}
int main(void) {
    static const double ones[36] = {@@ONES@@}, twos[36] = {@@TWOS@@};
    static const double zin[4] = {9,1,1,9}, nin[4] = {10,1,1,10}, three[3] = {1,1,1};
    static const double tiny[4] = {1e-300,1e-300,1e-300,1e-300};
    static const double tiny_integral[4] = {1e-308,1e-308,1e-308,1e-308};
    double nans[4] = {1, strtod("nan", NULL), 1, 1}, uvzero[61], uvnegative[61];
    struct { const char *name; int n; double lo, hi, norm; const double *v;
             int vlen; double level; int nonemis; } cases[] = {
        {"unit", 36, 380, 730, 1.0, ones, 36, 1.0, 0},
        {"half", 36, 380, 730, 1.0, twos, 36, 1.0, 0},
        {"disabled", 0, 380, 730, 1.0, ones, 36, 1.0, 0},
        {"zero_inside", 4, 400, 430, 1.0, zin, 4, 1.0, 0},
        {"negative_inside", 4, 400, 430, 1.0, nin, 4, 1.0, 0},
        {"zero_norm", 36, 380, 730, 0.0, ones, 36, 1.0, 0},
        {"subnormal_norm", 36, 380, 730, 1e-320, ones, 36, 1.0, 0},
        {"overflow", 4, 380, 730, 1.0, tiny, 4, 1e10, 0},
        {"spectrum_only_overflow", 4, 380, 730, 1.0, tiny, 4, 1e10, 0},
        {"spectrum_only_zero", 4, 400, 430, 1.0, zin, 4, 1.0, 0},
        {"spectrum_only_negative", 36, 380, 730, 1.0, ones, 36, -1.0, 0},
        {"clamped_negative", 36, 380, 730, 1.0, ones, 36, -1.0, 0},
        {"late_integral_overflow", 36, 380, 730, 1.0, tiny_integral, 4, 1.0, 0},
        {"clamped_integral_overflow", 36, 380, 730, 1.0, tiny_integral, 4, -1.0, 0},
        {"three_cubic", 3, 400, 420, 1.0, three, 3, 1.0, 0},
        {"nan_sample", 4, 400, 430, 1.0, nans, 4, 1.0, 0},
        {"reflective_skipped", 4, 400, 430, 1.0, zin, 4, 1.0, 1},
        {"unused_zero", 61, 200, 800, 1.0, uvzero, 61, 1.0, 0},
        {"unused_negative", 61, 200, 800, 1.0, uvnegative, 61, 1.0, 0},
        /* Last: a leak here shows in its own row, before the next row's guard aborts. */
        {"check_allocation_fails", 36, 380, 730, 1.0, ones, 36, 1.0, 0},
    };
    size_t c, n = sizeof cases / sizeof cases[0];
    for (c = 0; c < 61; c++) uvzero[c] = uvnegative[c] = 1.0;
    uvzero[0] = 0.0; uvnegative[0] = -1.0;
    puts("[");
    for (c = 0; c < n; c++) {
        ipatch before[2], after[2]; xspect f; int rc, changed, allfinite = 1, xyzfinite = 1, i, j;
        size_t b;
        if (live != 0) abort();
        allocations = releases = 0;
        reading(before, cases[c].level);
        if (cases[c].nonemis) before[0].mtype = before[1].mtype = inst_mrt_reflective;
        if (strncmp(cases[c].name, "spectrum_only_", 14) == 0)
            before[0].XYZ_v = before[1].XYZ_v = 0;
        if (strstr(cases[c].name, "_integral_overflow") != NULL)
            for (j = 0; j < 71; j++) {
                before[0].sp.spec[j] = 1e-8 * cases[c].level;
                before[1].sp.spec[j] = cases[c].level;
            }
        fail_at = strcmp(cases[c].name, "check_allocation_fails") == 0 ? 2 : 0;
        memcpy(after, before, sizeof after);
        filt(&f, cases[c].n, cases[c].lo, cases[c].hi, cases[c].norm, cases[c].v, cases[c].vlen);
        rc = ipatch_convert_custom_filter(after, 2, &f,
             strncmp(cases[c].name, "clamped_", 8) == 0 ? icxClamp : icxNoClamp);
        changed = memcmp(before, after, sizeof before) != 0;
        for (i = 0; i < 2; i++) for (j = 0; j < after[i].sp.spec_n; j++) {
            double v = after[i].sp.spec[j];
            if (v != v || v > DBL_MAX || v < -DBL_MAX) allfinite = 0;
        }
        for (i = 0; i < 2; i++) for (j = 0; j < 3; j++) {
            double v = after[i].XYZ[j];
            if (after[i].XYZ_v && (v != v || v > DBL_MAX || v < -DBL_MAX)) xyzfinite = 0;
        }
        printf("\t{\"case\":\"%s\",\"rc\":%d,\"changed\":%d,\"all_finite\":%d,",
               cases[c].name, rc, changed, allfinite);
        number("band_415", after[0].sp.spec[7], ",");
        number("band_380", after[0].sp.spec[0], ",");
        number("xyz1_y", after[1].XYZ[1], ",");
        printf("\"xyz_finite\":%d,\"live\":%d,\"allocations\":%d,\"releases\":%d,"
               "\"patch_bytes\":\"", xyzfinite, live, allocations, releases);
        for (b = 0; b < sizeof after; b++) printf("%02x", ((unsigned char *)after)[b]);
        printf("\"");
        printf("}%s\n", c + 1 < n ? "," : "");
    }
    puts("]"); return 0;
}
'''.replace("@@ONES@@", _ONES).replace("@@TWOS@@", _TWOS)

SETTER_MAIN = r'''
static void filt(xspect *f, int n, double lo, double hi, double norm, const double *v, int vlen) {
    int i; memset(f, 0x7f, sizeof *f);
    f->spec_n = n; f->spec_wl_short = lo; f->spec_wl_long = hi; f->norm = norm;
    for (i = 0; i < n && i < XSPECT_MAX_BANDS; i++) f->spec[i] = v[i % vlen];
}
int main(void) {
    static const double ones[36] = {@@ONES@@};
    static const double three[3] = {1,2,1}, neg[4] = {1,-1,1,1}, zero[4] = {1,0,1,1};
    static const double mark[2] = {5,5};
    double nans[4] = {1, strtod("nan", NULL), 1, 1};
    double inf = strtod("inf", NULL), nan = strtod("nan", NULL);
    struct { const char *name; int isnull; int n; double lo, hi, norm;
             const double *v; int vlen; } cases[] = {
        {"null", 1, 0, 0, 0, 0, ones, 36}, {"n0", 0, 0, 380, 730, 1.0, ones, 36},
        {"valid36", 0, 36, 380, 730, 1.0, ones, 36},
        {"valid3_linear", 0, 3, 400, 410, 1.0, three, 3},
        {"n1", 0, 1, 400, 400, 1.0, ones, 36}, {"n602", 0, 602, 300, 901, 1.0, ones, 36},
        {"reversed", 0, 36, 730, 380, 1.0, ones, 36},
        {"nan_short", 0, 36, 0, 730, 1.0, ones, 36},
        {"inf_long", 0, 36, 380, 0, 1.0, ones, 36}, {"norm0", 0, 36, 380, 730, 0.0, ones, 36},
        {"norm_nan", 0, 36, 380, 730, 0, ones, 36},
        {"neg_sample", 0, 4, 400, 430, 1.0, neg, 4},
        {"zero_sample", 0, 4, 400, 430, 1.0, zero, 4},
        {"nan_sample", 0, 4, 400, 430, 1.0, nans, 4},
        {"three_cubic", 0, 3, 0, 10.02, 1.0, three, 3},
    };
    int (*setters[])(state *, int, ...) = {@@SETTERS@@};
    const char *names[] = {@@NAMES@@};
    size_t d, c, nd = sizeof setters / sizeof setters[0], nc = sizeof cases / sizeof cases[0];
    puts("[");
    for (d = 0; d < nd; d++) for (c = 0; c < nc; c++) {
        state p; xspect f, marker; int rc, kept, took;
        filt(&marker, 2, 500, 510, 1.0, mark, 2);
        memset(&p, 0, sizeof p); p.m = &p; p.custfilt_en = 1; p.custfilt = marker;
        filt(&f, cases[c].n, cases[c].lo, cases[c].hi, cases[c].norm, cases[c].v, cases[c].vlen);
        if (!strcmp(cases[c].name, "nan_short")) f.spec_wl_short = nan;
        if (!strcmp(cases[c].name, "inf_long")) f.spec_wl_long = inf;
        if (!strcmp(cases[c].name, "norm_nan")) f.norm = nan;
        rc = setters[d](&p, inst_opt_set_custom_filter, cases[c].isnull ? NULL : &f);
        kept = memcmp(&p.custfilt, &marker, sizeof marker) == 0 && p.custfilt_en == 1;
        took = !cases[c].isnull && memcmp(&p.custfilt, &f, sizeof f) == 0 && p.custfilt_en == 1;
        printf("\t{\"driver\":\"%s\",\"case\":\"%s\",\"rc\":%d,\"en\":%d,"
               "\"kept_prior\":%d,\"took_input\":%d}%s\n",
               names[d], cases[c].name, rc, p.custfilt_en, kept, took,
               (d + 1 < nd || c + 1 < nc) ? "," : "");
    }
    puts("]"); return 0;
}
'''.replace("@@ONES@@", _ONES)

CONV_REFUSE = {"zero_inside", "negative_inside", "zero_norm", "subnormal_norm",
               "overflow",
               "nan_sample", "spectrum_only_overflow", "spectrum_only_zero",
               "late_integral_overflow", "clamped_integral_overflow"}
CONV_ACCEPT = {"unit", "half", "disabled", "reflective_skipped", "spectrum_only_negative",
               "clamped_negative", "unused_zero", "unused_negative", "three_cubic"}
# The second converter's allocation fails; the first one exists only in the repair.
CONV_ALLOC_FAIL = {"check_allocation_fails"}
SET_INVALID = {"n1", "n602", "reversed", "nan_short", "inf_long", "norm0", "norm_nan",
               "nan_sample"}
SET_VALID = {"valid36", "valid3_linear", "neg_sample", "zero_sample", "three_cubic"}
SET_DISABLE = {"null", "n0"}


def _branch(text: str, arg: str) -> str:
    marker = f"if ({arg} == inst_opt_set_custom_filter) {{"
    if text.count(marker) != 1:
        raise ValueError(f"Missing or ambiguous custom-filter branch for {arg}")
    start = text.index(marker)
    i, depth = text.index("{", start), 0
    while True:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[start:i + 1]
        i += 1


def _constants(inst_h: str) -> dict[str, int]:
    out = {}
    for sym in ("inst_ok", "inst_bad_parameter", "inst_opt_set_custom_filter"):
        m = re.search(r"\b" + sym + r"\s*=\s*(0x[0-9a-fA-F]+|[0-9]+)", inst_h)
        if m is None:
            raise ValueError(f"Missing upstream constant: {sym}")
        out[sym] = int(m.group(1), 0)
    return out


def _helpers(inst: str) -> str:
    return "".join(_function(inst, n) for n in ("inst_double_finite", "inst_valid_custom_filter"))


def _converter_source(texts: dict, xspect_c: str, repaired: bool) -> str:
    inst = texts["spectro/inst.c"]
    interp = "".join(_function(xspect_c, n) for n in (
        "getval_raw_xspec_lin_ex", "getval_raw_xspec_poly3_ex", "getval_raw_xspec", "getval_xspec",
        "xsp2cie_sconvert", "xsp2cie_convert"))
    start = xspect_c.index("static xspect ob_CIE_1931_2[3] = {")
    observer = xspect_c[start:xspect_c.index("\n};", start) + 3]
    helpers = _helpers(inst) if repaired else ""
    clamp = re.search(r"^#define CLAMP_XYZ[^\n]*", xspect_c, re.M)
    if clamp is None:
        raise ValueError("Pinned integration clamp configuration is missing")
    # The factory admits XYZ only; fail if an unused Lab branch is ever reached.
    unused_lab = "#define icmXYZ2Lab(w,o,i) abort()\n#define icmXYZ2Lpt(w,o,i) abort()\n"
    return (_preamble(inst) + "#include <float.h>\n" + clamp[0] + "\n"
            + unused_lab + observer + interp + helpers
            + _function(inst, "ipatch_convert_custom_filter") + CONVERTER_MAIN)


def _setter_source(texts: dict, repaired: bool) -> str:
    inst = texts["spectro/inst.c"]
    consts = _constants(texts["spectro/inst.h"])
    helpers = _helpers(inst) if repaired else ""
    funcs, names = [], []
    for path, state in SETTERS.items():
        name = Path(path).stem
        arg = "ot" if name == "i1pro3" else "m"
        setup = "state *m = p;" if name == "i1pro3" else ""
        funcs.append(f"static int {name}_set(state *p, int {arg}, ...) {{\n{setup}\n"
                     + _branch(texts[path], arg) + "\nabort();\n}\n")
        names.append(name)
    return ("#include <stdarg.h>\n#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n"
            "#include <float.h>\n"
            '#include "numsup.h"\n#include "xspect.h"\n'
            + "".join(f"#define {k} {v}\n" for k, v in consts.items())
            + "typedef struct state { int custfilt_en; xspect custfilt; void *m; } state;\n"
            + "typedef state i1proimp; typedef state munkiimp;\n" + helpers + "\n".join(funcs)
            + SETTER_MAIN.replace("@@SETTERS@@", ",".join(n + "_set" for n in names))
                         .replace("@@NAMES@@", ",".join(f'"{n}"' for n in names)))


def _validate(conv: list, sett: list, *, repaired: bool, consts: dict) -> None:
    label = "repaired" if repaired else "original"
    by = {r["case"]: r for r in conv}
    if len(by) != len(conv) or set(by) != CONV_REFUSE | CONV_ACCEPT | CONV_ALLOC_FAIL:
        raise AssertionError(f"{label} filter-values converter census changed")
    for name, r in by.items():
        if name in CONV_ACCEPT:
            ok = r["rc"] == 0 and (name not in ("disabled", "reflective_skipped")
                                   or not r["changed"])
            ok = ok and r["all_finite"] == 1 and r["xyz_finite"] == 1
            if name == "half":
                ok = ok and abs(r["band_380"] - 0.5) < 1e-12
            if name == "unit":
                ok = ok and abs(r["band_380"] - 1.0) < 1e-12
        elif repaired and name in CONV_ALLOC_FAIL:
            ok = r["rc"] == 1 and not r["changed"]
        elif repaired:
            ok = r["rc"] == 2 and not r["changed"]
        else:
            ok = r["rc"] == 0 and r["changed"]
        ok = ok and r["live"] == 0 and r["allocations"] == r["releases"]
        if not ok:
            raise AssertionError(f"{label} filter-values result differs at converter/{name}: {r}")
    if not repaired:
        negative = by["negative_inside"]["band_415"]
        defects = (by["zero_inside"]["all_finite"] == 0,
                   isinstance(negative, float) and negative < 0,
                   by["zero_norm"]["band_380"] == 0.0,
                   by["subnormal_norm"]["band_380"] == 0.0,
                   by["subnormal_norm"]["xyz1_y"] == 0.0,
                   by["overflow"]["all_finite"] == 0,
                   by["late_integral_overflow"]["all_finite"] == 1,
                   by["late_integral_overflow"]["xyz_finite"] == 0,
                   by["clamped_integral_overflow"]["xyz_finite"] == 1,
                   by["clamped_integral_overflow"]["xyz1_y"] == 0.0)
        if not all(defects):
            raise AssertionError(f"original filter-values defects not reproduced: {defects}")
    expected = {(Path(path).stem, case) for path in SETTERS
                for case in SET_INVALID | SET_VALID | SET_DISABLE}
    observed = [(row["driver"], row["case"]) for row in sett]
    if len(observed) != len(expected) or set(observed) != expected:
        raise AssertionError(f"{label} filter-values setter census changed")
    for r in sett:
        c = r["case"]
        if c in SET_DISABLE:
            ok = r["rc"] == consts["inst_ok"] and r["en"] == 0
        elif c in SET_VALID or not repaired:
            ok = r["rc"] == consts["inst_ok"] and r["took_input"] == 1
        else:
            ok = r["rc"] == consts["inst_bad_parameter"] and r["kept_prior"] == 1
        if not ok:
            raise AssertionError(f"{label} filter-values result differs at {r['driver']}/{c}: {r}")


@production_proof
def prove(archive: Path, output: Path, *, source: Path | None = None,
          compiler: str = "cc", sanitize: bool = False,
          variants: tuple[str, ...] = ("baseline", "patched"),
          converter_flags: tuple[str, ...] = ()) -> dict:
    """Original versus repaired converter and setters; a composed tree may be supplied.

    ``variants`` may name the repaired text alone, for a run with ``converter_flags`` that the
    original would trip (it divides by an applied zero). Those flags reach the converter build
    only; the setter harness constructs NaN and infinity with strtod and is not
    part of this division-order check. The
    cross-variant byte comparison needs both variants and runs only then; the report records
    which ran.
    """
    if not variants or any(v not in ("baseline", "patched") for v in variants):
        raise ValueError("Unknown filter-values proof variant")
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    output.mkdir(parents=True, exist_ok=False)
    headers = output / "upstream"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist():
            if n.startswith(PREFIX) and n.endswith(".h"):
                p = headers / n.removeprefix(PREFIX)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(z.read(n))
        (headers / "sa_config.h").write_bytes(z.read(PREFIX + "h/aconfig.h"))
        xspect_c = z.read(PREFIX + "xicc/xspect.c").decode()
        (output / "Argyll-License2.txt").write_bytes(z.read(PREFIX + "License2.txt"))
    before = {p: before_source("filter_values", p, archive) for p in PATHS}
    after = {p: production_text(source, p) for p in PATHS}
    xrga_h = production_text(source, "spectro/xrga.h")
    report = dict(status="incomplete", archive_sha256=ARCHIVE_SHA256, sanitize=sanitize,
                  variants=list(variants), converter_flags=list(converter_flags),
                  before_sha256={p: digest(before[p].encode()) for p in PATHS},
                  patched_sha256={p: digest(after[p].encode()) for p in PATHS},
                  proof_sha256=digest(Path(__file__).read_bytes()), commands=[], results={},
                  scope="exact converter and seven setter branches with pinned interpolators, "
                        "CIE 1931 observer and XYZ integration; counted factory; "
                        "no file parser, device or complete tool",
                  hardware_tested=False)
    system = platform.system()
    try:
        for label, texts in (("baseline", before), ("patched", after)):
            if label not in variants:
                continue
            (headers / "spectro/inst.h").write_text(texts["spectro/inst.h"])
            (headers / "spectro/xrga.h").write_text(xrga_h)
            consts = _constants(texts["spectro/inst.h"])
            rows = {}
            for kind, src in (("converter", _converter_source(
                    texts, production_text(source, "xicc/xspect.c")
                    if label == "patched" else xspect_c, label == "patched")),
                              ("setters", _setter_source(texts, label == "patched"))):
                directory = output / label / kind
                directory.mkdir(parents=True)
                (directory / "probe.c").write_text(src, encoding="utf-8", newline="\n")
                exe = directory / ("probe.exe" if system == "Windows" else "probe")
                includes = [headers, *[headers / p for p in
                                       ("spectro", "numlib", "rspl", "xicc", "cgats", "icc", "h")]]
                argv = [cc, "-std=gnu99", "-O1", "-g", "-DSALONEINSTLIB", "-DENABLE_USB",
                        *(["-DNT", "-DEN_LIBUSB0"] if system == "Windows"
                          else ["-DUNIX", "-pthread"]),
                        *sanitizers.flags(sanitize),
                        *(converter_flags if kind == "converter" else ()),
                        *[f"-I{p}" for p in includes],
                        str(directory / "probe.c"), "-lm", "-o", str(exe)]
                run = subprocess.run(argv, capture_output=True, text=True, timeout=180)
                (directory / "compile.log").write_text(run.stdout + run.stderr)
                report["commands"].append({"argv": argv, "returncode": run.returncode})
                run.check_returncode()
                run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
                (directory / "run.log").write_text(run.stdout + run.stderr)
                sanitizers.refuse(run.stderr, f"{label}/{kind}")
                run.check_returncode()
                if run.stderr.strip():
                    raise RuntimeError(f"Unexpected filter-values proof diagnostic: {label}/{kind}")
                rows[kind] = json.loads(run.stdout)
            _validate(rows["converter"], rows["setters"], repaired=label == "patched",
                      consts=consts)
            report["results"][label] = {"converter": rows["converter"],
                                        "setter_cases": len(rows["setters"])}
        if set(variants) == {"baseline", "patched"}:
            before_rows = {r["case"]: r for r in report["results"]["baseline"]["converter"]}
            after_rows = {r["case"]: r for r in report["results"]["patched"]["converter"]}
            for name in CONV_ACCEPT - {"three_cubic"}:
                if before_rows[name]["patch_bytes"] != after_rows[name]["patch_bytes"]:
                    raise AssertionError(
                        f"repaired filter-values result differs at whole patch/{name}")
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
