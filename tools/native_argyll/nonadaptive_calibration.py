"""Source-bound proof and attributed Argyll 3.5.0 emissive corrections.

The executed C contains five authenticated upstream functions, upstream headers,
and inert substitutes. It links no instrument runtime and never touches hardware
or a calibration cache. The native checker applies the patch to new builds;
this module does not expose a new acquisition mode. Upstream functions remain Graeme
W. Gill's GPL-2.0-or-later work, not original project scientific algorithms.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import io
import json
import platform
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from native_argyll.proof_inputs import before_source, production_proof, production_text

REPAIR_ID = 'recipe:nonadaptive-calibration'

ARCHIVE_SHA256 = "f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f"
SOURCE_PATH = "spectro/i1pro3_imp.c"
PREFIX = "Argyll_V3.5.0/"
FUNCTIONS = (
    "i1pro3_code i1pro3_imp_get_n_a_cals(",
    "i1pro3_code i1pro3_imp_calibrate(\n",
    "i1pro3_code i1pro3_spot_simple_emis_raw_meas(\n",
    "void i1pro3_free_raw(\n",
    "i1pro3_code i1pro3_spot_adapt_emis_raw_meas(\n",
)
CASES = (
    "success_reduce",
    "success_noincrease",
    "success_minimum",
    "saturated",
    "measure_error",
    "wrong_condition",
    "needed_request",
    "pending_other",
    "all_modes_error",
    "adaptive_ignored",
    "scan_ignored",
    "prior_valid_success",
    "prior_valid_saturated",
    "prior_valid_error",
    "read_adaptive_success",
    "read_adaptive_saturated",
    "read_adaptive_inconsistent",
    "read_nonadaptive_success",
    "read_nonadaptive_saturated",
    "read_nonadaptive_inconsistent",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _function(text: str, signature: str) -> str:
    """Extract a whole pinned function, including its final column-zero brace."""
    if text.count(signature) != 1:
        raise ValueError(f"Missing or ambiguous pinned function: {signature}")
    start = text.index(signature)
    end = text.find("\n}\n", start)
    if end < 0:
        raise ValueError(f"Missing pinned function end: {signature}")
    return text[start : end + 3]


# These are synthetic hooks, not implementations of physical measurement,
# black correction, linearization, or instrument destruction. Unexpected calls
# outside the selected branch abort instead of silently simulating success.
_HARNESS = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <time.h>
#include <math.h>
#include "sa_config.h"
#include "numsup.h"
#include "rspl1.h"
#include "cgats.h"
#include "xspect.h"
#include "insttypes.h"
#include "conv.h"
#include "icoms.h"
#include "inst.h"
#include "i1pro3.h"
/* Unused transport token: no USB ABI or runtime is exercised. */
struct _usb_cancelt { int inert; };
#include "i1pro3_imp.h"

static int reads, frees, live_buffers, wrong_free_origin, averages, linearized, saves;
static int probe_error, probe_saturated, probe_inconsistent, read_path, normalized;
static double sample_max = 100000.0;
static double row_storage[4][2][MX_NRAW+1], *rows[4][2];
static int allocated[4], emission_allocations;
static void *unexpected(void) {
    fputs("Unexpected non-substituted calibration path\n", stderr);
    abort();
}
static int probe_measure(i1pro3 *p, i1p3_mmode mm, double ***raw, int *n, double *t) {
    (void)p;
    ++reads;
    if (mm != i1p3mm_em || (!read_path && *t != 0.05)) unexpected();
    if (probe_error) return probe_error;
    int slot;
    for (slot = 0; slot < 4 && allocated[slot]; ++slot) {}
    if (slot == 4) unexpected();
    allocated[slot] = 1;
    ++emission_allocations;
    *n = 2;
    for (int i = 0; i < 2; ++i) {
        rows[slot][i] = row_storage[slot][i] - CM_EMISSION_ROW_ORIGIN;
        for (int j = CM_EMISSION_ROW_ORIGIN; j < 4; ++j) rows[slot][i][j] = sample_max;
    }
    *raw = rows[slot];
    ++live_buffers;
    return I1PRO3_OK;
}
static void probe_free(double **raw, int rlo, int rhi, int clo, int chi) {
    int slot;
    for (slot = 0; slot < 4 && raw != rows[slot]; ++slot) {}
    if (slot == 4 || !allocated[slot] || rlo != 0 || rhi != 1 || chi != 3)
        unexpected();
    allocated[slot] = 0;
    ++frees;
    if (clo != CM_EMISSION_ROW_ORIGIN) ++wrong_free_origin;
    --live_buffers; /* Inert ownership token; never free an invalid pointer. */
}
static void probe_average(i1pro3 *p, double *avg, double **raw, int n) {
    (void)p;
    int slot;
    for (slot = 0; slot < 4 && raw != rows[slot]; ++slot) {}
    if (slot == 4 || !allocated[slot] || n != 2) unexpected();
    ++averages;
    for (int j = 0; j < 4; ++j) avg[j] = (raw[0][j] + raw[1][j]) / 2.0;
}
static double probe_max(double *v, int n) {
    double maximum = v[0];
    for (int i = 1; i < n; ++i) if (v[i] > maximum) maximum = v[i];
    return maximum;
}
#define a1logd(...) ((void)0)
#define time(...) ((time_t)12345)
#define ENABLE_NONVCAL
#define i1pro3_check_calib(...) I1PRO3_OK
#define i1pro3_getboardtemp(p, t) (*(t) = 23.0, I1PRO3_OK)
#define i1pro3_recompute_wav_filters_for_temp(...) (unexpected(), I1PRO3_INTERNAL_ERROR)
#define i1pro3_comp_emis_black(...) ((void)0)
#define i1pro3_multimeas_check_raw_consistency(...) probe_inconsistent
#define i1pro3_vect_lin(...) ((void)0)
#define error(...) unexpected()
#define vect_sub(...) ((void)0)
#define i1pro3_save_calibration(...) (++saves)
#define i1pro3_comp_nummeas(...) 2
#define i1pro3_do_measure(p, mm, raw, n, t, a, b) probe_measure(p, mm, raw, n, t)
#define i1pro3_comp_simple_emis_black(...) ((void)0)
#define i1pro3_multimeas_sub_black(...) ((void)0)
#define i1pro3_multimeas_check_sat(...) probe_saturated
#define i1pro3_multimeas_lin(...) (++linearized)
#define i1pro3_normalize_rawmmeas(...) (read_path ? ++normalized : (unexpected(), 0))
#define i1pro3_average_rawmmeas(...) probe_average(__VA_ARGS__)
#define vect_max(...) probe_max(__VA_ARGS__)
#define free_dmatrix(...) probe_free(__VA_ARGS__)
#define dvectorz(...) ((double *)unexpected())
#define free_dvector(...) unexpected()
#undef vect_cpy
#define vect_cpy(...) unexpected()
#define i1pro3_delay_llampoff(...) unexpected()
#define i1pro3_wl_measure(...) (unexpected(), I1PRO3_INTERNAL_ERROR)
#define i1pro3_match_wl_meas(...) (unexpected(), I1PRO3_INTERNAL_ERROR)
#define i1pro3_compute_wav_filters(...) (unexpected(), I1PRO3_INTERNAL_ERROR)
#define i1pro3_adapt_emis_cal(...) (unexpected(), I1PRO3_INTERNAL_ERROR)
#define i1pro3_pol_refl_cal(...) (unexpected(), I1PRO3_INTERNAL_ERROR)
#define i1pro3_refl_cal(...) (unexpected(), I1PRO3_INTERNAL_ERROR)
#define i1pro3_trans_cal(...) (unexpected(), I1PRO3_INTERNAL_ERROR)
"""
_MAIN = r"""
static int run_read_case(const char *name, i1pro3 *meter, i1pro3_state *s) {
    i1pro3imp *m = (i1pro3imp *)meter->m;
    double *sentinel[1] = {NULL}, **result = sentinel;
    int result_count = -7, caller_frees = 0;
    read_path = 1;
    s->adaptive = strstr(name, "nonadaptive") == NULL;
    if (s->adaptive) {
        m->ms[i1p3_emiss_spot] = *s;
        m->mmode = i1p3_emiss_spot;
        s = &m->ms[m->mmode];
        s->mode = m->mmode;
    }
    s->done_dintsel = 1;
    s->diseldate = 11111;
    m->sens_sat = 200000.0;
    m->max_int_time = 2.0;
    probe_saturated = strstr(name, "saturated") != NULL;
    probe_inconsistent = strstr(name, "inconsistent") != NULL;
    int ev = i1pro3_spot_adapt_emis_raw_meas(meter, &result, &result_count);
    int transferred = result != sentinel;
    int live_before_caller_cleanup = live_buffers;
    if (transferred) {
        i1pro3_free_raw(meter, i1p3mm_em, result, result_count);
        caller_frees = 1;
    }
    printf("{\"case\":\"%s\",\"return_code\":%d,\"ok_code\":%d,"
           "\"saturation_code\":%d,\"inconsistent_code\":%d,\"done\":%d,\"date\":%ld,"
           "\"original_mode\":%d,\"final_mode\":%d,\"reads\":%d,\"frees\":%d,"
           "\"live_buffers\":%d,\"wrong_free_origin\":%d,\"emission_allocations\":%d,"
           "\"transferred\":%d,\"result_count\":%d,\"live_before_caller_cleanup\":%d,"
           "\"caller_frees\":%d,\"normalized\":%d}\n",
           name, ev, I1PRO3_OK, I1PRO3_RD_SENSORSATURATED, I1PRO3_RD_READINCONS,
           (int)s->done_dintsel, (long)s->diseldate, (int)s->mode, (int)m->mmode,
           reads, frees, live_buffers, wrong_free_origin, emission_allocations,
           transferred, result_count, live_before_caller_cleanup, caller_frees, normalized);
    return 0;
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    const char *name = argv[1];
    i1pro3 meter = {0};
    i1pro3imp implementation = {0};
    i1pro3imp *m = &implementation;
    meter.m = m;
    m->mmode = i1p3_emiss_spot_na;
    m->nraw = 4;
    m->sens_target = 45000.0;
    m->min_int_time = 0.01;
    i1pro3_state *s = &m->ms[i1p3_emiss_spot_na];
    s->mode = i1p3_emiss_spot_na;
    s->emiss = 1;
    s->dark_valid = 1;
    s->iinttime = 1.0;
    s->inttime = 0.5; /* Sentinel: selected probe resets to the upstream default. */
    s->targoscale = 0.9;
    inst_cal_type calt = inst_calt_emis_int_time, needed, needed_after, available;
    inst_cal_cond calc = inst_calc_emis_white;
    inst_calc_id_type idtype = inst_calc_id_none;
    char id[CALIDLEN] = {0};
    if (!strcmp(name, "success_noincrease")) sample_max = 100.0;
    if (!strcmp(name, "success_minimum")) sample_max = 10000000.0;
    if (!strncmp(name, "prior_valid_", 12)) {
        s->done_dintsel = 1;
        s->diseldate = 11111; /* Previously valid selection, before this probe. */
    }
    if (!strcmp(name, "saturated") || !strcmp(name, "prior_valid_saturated"))
        probe_saturated = 1;
    if (!strcmp(name, "prior_valid_error")) probe_error = I1PRO3_COMS_FAIL;
    if (!strcmp(name, "measure_error")) probe_error = I1PRO3_COMS_FAIL;
    if (!strcmp(name, "wrong_condition")) calc = inst_calc_none;
    if (!strcmp(name, "needed_request")) calt = inst_calt_needed;
    if (!strcmp(name, "pending_other")) calt |= inst_calt_em_dark;
    if (!strcmp(name, "adaptive_ignored")) { s->adaptive = 1; calt = inst_calt_ap_flag; }
    if (!strcmp(name, "scan_ignored")) { s->scan = 1; calt = inst_calt_ap_flag; }
    if (!strcmp(name, "all_modes_error")) {
        m->mmode = i1p3_emiss_spot;
        m->ms[i1p3_emiss_spot].emiss = 1;
        m->ms[i1p3_emiss_spot].adaptive = 1;
        m->ms[i1p3_emiss_spot].dark_valid = 1;
        calt = inst_calt_ap_flag;
        probe_error = I1PRO3_COMS_FAIL;
    }
    if (!strncmp(name, "read_", 5)) return run_read_case(name, &meter, s);
    i1pro3_imp_get_n_a_cals(&meter, &needed, &available);
    int original_mode = m->mmode;
    int ev = i1pro3_imp_calibrate(&meter, &calt, &calc, &idtype, id);
    i1pro3_imp_get_n_a_cals(&meter, &needed_after, NULL);
    printf("{\"case\":\"%s\",\"return_code\":%d,\"ok_code\":%d,"
           "\"saturation_code\":%d,\"error_code\":%d,\"setup_code\":%d,"
           "\"needed_before\":%u,\"needed_after\":%u,\"available\":%u,\"remaining\":%u,"
           "\"integration_bit\":%u,\"dark_bit\":%u,\"condition\":%u,"
           "\"done\":%d,\"date\":%ld,\"inttime\":%.17g,"
           "\"original_mode\":%d,\"final_mode\":%d,"
           "\"reads\":%d,\"frees\":%d,\"live_buffers\":%d,"
           "\"wrong_free_origin\":%d,\"averages\":%d,\"linearized\":%d,\"saves\":%d}\n",
           name, ev, I1PRO3_OK, I1PRO3_RD_SENSORSATURATED, I1PRO3_COMS_FAIL,
           I1PRO3_CAL_SETUP, (unsigned)needed, (unsigned)needed_after,
           (unsigned)available, (unsigned)calt,
           (unsigned)inst_calt_emis_int_time, (unsigned)inst_calt_em_dark, (unsigned)calc,
           (int)s->done_dintsel, (long)s->diseldate, s->inttime, original_mode, m->mmode,
           reads, frees, live_buffers, wrong_free_origin, averages, linearized, saves);
    return 0;
}
"""


def _probe_source(text: str) -> str:
    # Retain exact upstream definitions of constants, rather than a parallel enum.
    definitions = []
    for name in ("MX_NRAW", "LED_OFF_TIME"):
        matches = re.findall(rf"^#define {name}\s+[^\n]+", text, flags=re.MULTILINE)
        if len(matches) != 1:
            raise ValueError(f"Missing or ambiguous pinned constant: {name}")
        definitions.append(matches[0])
    # The transport/acquisition routine is not executed. Bind the inert matrix
    # origin to its actual emission allocation branch, rather than a test guess.
    acquisition = _function(text, "i1pro3_code i1pro3_do_measure(\n")
    origin = re.findall(
        r"\} else \{\n\t\tnummeas = anummeas; \n"
        r"\t\traw = dmatrix\(0, nummeas-1, (-?\d+), m->nraw-1\);",
        acquisition,
    )
    if origin != ["-1"]:
        raise ValueError("Pinned emission allocation origin changed")
    definitions.append(f"#define CM_EMISSION_ROW_ORIGIN {origin[0]}")
    return (
        "/* Extracted ArgyllCMS 3.5.0 functions: Graeme W. Gill, GPL-2.0-or-later. */\n"
        + "\n".join(definitions)
        + "\n"
        + _HARNESS
        + "\n"
        + "\n".join(_function(text, signature) for signature in FUNCTIONS)
        + _MAIN
    )


def _validate(results: dict) -> None:
    baseline, patched = results["baseline"], results["patched"]
    for name in ("success_reduce", "success_noincrease", "success_minimum"):
        old, new = baseline[name], patched[name]
        if not (old["return_code"] == old["ok_code"] and old["done"] == 0):
            raise AssertionError('Proof verdict failed')
        if not (old["remaining"] & old["integration_bit"] and old["live_buffers"] == 1):
            raise AssertionError('Proof verdict failed')
        if not (old["averages"] == old["frees"] == old["saves"] == 0):
            raise AssertionError('Proof verdict failed')
        if not (new["return_code"] == new["ok_code"] and new["done"] == 1):
            raise AssertionError('Proof verdict failed')
        if not (new["remaining"] == 0 and new["date"] == 12345):
            raise AssertionError('Proof verdict failed')
        if not (new["reads"] == new["averages"] == new["frees"] == new["saves"] == 1):
            raise AssertionError('Proof verdict failed')
    if not (abs(patched["success_reduce"]["inttime"] - 0.02025) < 1e-12):
        raise AssertionError('Proof verdict failed')
    if not (patched["success_noincrease"]["inttime"] == 1.0):
        raise AssertionError('Proof verdict failed')
    if not (patched["success_minimum"]["inttime"] == 0.01):
        raise AssertionError('Proof verdict failed')
    old, new = baseline["saturated"], patched["saturated"]
    if not (old["return_code"] == old["ok_code"] and old["done"] == 1):
        raise AssertionError('Proof verdict failed')
    if not (old["remaining"] == 0 and old["wrong_free_origin"] == 1):
        raise AssertionError('Proof verdict failed')
    if not (new["return_code"] == new["saturation_code"] and new["done"] == 0):
        raise AssertionError('Proof verdict failed')
    if not (new["remaining"] & new["integration_bit"] and new["date"] == 0):
        raise AssertionError('Proof verdict failed')
    if not (new["reads"] == new["frees"] == 1 and new["saves"] == 0):
        raise AssertionError('Proof verdict failed')
    for name in ("measure_error", "all_modes_error"):
        new = patched[name]
        if not (new["return_code"] == new["error_code"] and new["done"] == 0):
            raise AssertionError('Proof verdict failed')
        if not (new["date"] == new["averages"] == new["frees"] == new["saves"] == 0):
            raise AssertionError('Proof verdict failed')
        if not (new["reads"] == 1 and new["final_mode"] == new["original_mode"]):
            raise AssertionError('Proof verdict failed')
    if not (patched["measure_error"]["remaining"] & patched["measure_error"]["integration_bit"]):
        raise AssertionError('Proof verdict failed')
    if not (baseline["all_modes_error"]["final_mode"]
            != baseline["all_modes_error"]["original_mode"]):
        raise AssertionError('Proof verdict failed')
    new = patched["pending_other"]
    if not (new["done"] == 1 and new["remaining"] == new["dark_bit"]):
        raise AssertionError('Proof verdict failed')
    if not (new["return_code"] == new["setup_code"] and new["saves"] == 0):
        raise AssertionError('Proof verdict failed')
    new = patched["wrong_condition"]
    if not (new["return_code"] == new["setup_code"] and new["reads"] == new["done"] == 0):
        raise AssertionError('Proof verdict failed')
    if not (new["remaining"] & new["integration_bit"]):
        raise AssertionError('Proof verdict failed')
    new = patched["needed_request"]
    if not (new["needed_before"] & new["integration_bit"]):
        raise AssertionError('Proof verdict failed')
    if not (new["return_code"] == new["ok_code"] and new["remaining"] == 0):
        raise AssertionError('Proof verdict failed')
    if not (new["done"] == new["reads"] == 0):
        raise AssertionError('Proof verdict failed')  # Upstream deferrable-request semantics.
    for name in ("adaptive_ignored", "scan_ignored"):
        new = patched[name]
        if not (new["reads"] == new["done"] == 0 and new["inttime"] == 0.5):
            raise AssertionError('Proof verdict failed')
    for name in ("prior_valid_success", "prior_valid_saturated", "prior_valid_error"):
        old, new = baseline[name], patched[name]
        if not (old["needed_before"] == new["needed_before"] == 0):
            raise AssertionError('Proof verdict failed')
        if not (old["done"] == 1 and old["needed_after"] == 0):
            raise AssertionError('Proof verdict failed')
        if not (new["reads"] == 1 and new["live_buffers"] == 0):
            raise AssertionError('Proof verdict failed')
        if name == "prior_valid_success":
            if not (new["return_code"] == new["ok_code"] and new["done"] == 1):
                raise AssertionError('Proof verdict failed')
            if not (new["needed_after"] == new["remaining"] == 0 and new["date"] == 12345):
                raise AssertionError('Proof verdict failed')
            if not (abs(new["inttime"] - 0.02025) < 1e-12):
                raise AssertionError('Proof verdict failed')
            if not (new["averages"] == new["frees"] == new["saves"] == 1):
                raise AssertionError('Proof verdict failed')
        else:
            expected = new["saturation_code"] if name.endswith("saturated") else new["error_code"]
            if not (new["return_code"] == expected and new["done"] == 0):
                raise AssertionError('Proof verdict failed')
            if not (new["needed_after"] & new["integration_bit"]):
                raise AssertionError('Proof verdict failed')
            if not (new["remaining"] & new["integration_bit"]):
                raise AssertionError('Proof verdict failed')
            if not (new["date"] == 11111 and new["inttime"] == 1.0):
                raise AssertionError('Proof verdict failed')
            if not (new["averages"] == new["saves"] == 0):
                raise AssertionError('Proof verdict failed')
    for name in (case for case in CASES if case.startswith("read_")):
        old, new = baseline[name], patched[name]
        # Full helper execution allocates four emission matrices. Success passes
        # exactly one to its caller; each error frees all four without publishing.
        if not (old["emission_allocations"] == new["emission_allocations"] == 4):
            raise AssertionError('Proof verdict failed')
        if not (old["reads"] == new["reads"] == 4):
            raise AssertionError('Proof verdict failed')
        if not (old["frees"] == new["frees"] == 4):
            raise AssertionError('Proof verdict failed')
        if not (new["done"] == 1 and new["date"] == 11111):
            raise AssertionError('Proof verdict failed')
        if name.endswith("success"):
            if not (old == new):
                # The patch does not change the successful path.
                raise AssertionError('Proof verdict failed')
            if not (new["return_code"] == new["ok_code"]):
                raise AssertionError('Proof verdict failed')
            if not (new["transferred"] == new["caller_frees"] == new["normalized"] == 1):
                raise AssertionError('Proof verdict failed')
            if not (new["live_before_caller_cleanup"] == 1 and new["result_count"] == 2):
                raise AssertionError('Proof verdict failed')
        else:
            expected = (
                new["saturation_code"] if name.endswith("saturated") else new["inconsistent_code"]
            )
            if not (old["return_code"] == new["return_code"] == expected):
                raise AssertionError('Proof verdict failed')
            if not (old["wrong_free_origin"] == 1 and new["wrong_free_origin"] == 0):
                raise AssertionError('Proof verdict failed')
            if not (new["transferred"] == new["caller_frees"] == new["normalized"] == 0):
                raise AssertionError('Proof verdict failed')
            if not (new["live_before_caller_cleanup"] == 0 and new["result_count"] == -7):
                raise AssertionError('Proof verdict failed')
    for new in patched.values():
        if not (new["wrong_free_origin"] == new["live_buffers"] == 0):
            raise AssertionError('Proof verdict failed')
        if not (new["final_mode"] == new["original_mode"]):
            raise AssertionError('Proof verdict failed')


@production_proof
def prove(archive: Path, output: Path, *, source: Path | None = None,
          compiler: str = "cc") -> dict:
    """Retain baseline defects and patched checks in a new local output folder."""
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        original = before_source("nonadaptive_calibration", SOURCE_PATH, archive)
        patched = production_text(source, SOURCE_PATH)
        output.mkdir(parents=True, exist_ok=False)
        headers = output / "headers"
        for entry in zipped.infolist():
            if entry.filename.startswith(PREFIX) and entry.filename.endswith(".h"):
                relative = Path(entry.filename.removeprefix(PREFIX))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Unexpected upstream header path")
                path = headers / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(zipped.read(entry))
        (headers / "sa_config.h").write_bytes(zipped.read(PREFIX + "h/aconfig.h"))
        (output / "Argyll-License2.txt").write_bytes(zipped.read(PREFIX + "License2.txt"))
    (output / "i1pro3_imp.original.c").write_text(original, encoding="utf-8", newline="\n")
    (output / "i1pro3_imp.patched.c").write_text(patched, encoding="utf-8", newline="\n")
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"Argyll-3.5.0/{SOURCE_PATH}",
            tofile=f"proposed-local-patch/{SOURCE_PATH}",
        )
    )
    (output / "nonadaptive-calibration.patch").write_text(diff, encoding="utf-8", newline="\n")
    (output / "UPSTREAM.txt").write_text(
        "ArgyllCMS 3.5.0, Graeme W. Gill and credited contributors.\n"
        "https://www.argyllcms.com/Argyll_V3.5.0_src.zip\n"
        "Extracted instrument functions retain GPL-2.0-or-later ownership.\n"
        "Headers retain their individual upstream notices and licensing terms.\n"
        "The local patch changes probe validity, status and emissive rejection cleanup.\n"
        "Only source-extracted functions with inert hooks execute; no driver runtime.\n",
        encoding="utf-8",
        newline="\n",
    )
    report = {
        "status": "incomplete",
        "hardware_tested": False,
        "instrument_runtime_linked": False,
        "archive_sha256": ARCHIVE_SHA256,
        "upstream_path": SOURCE_PATH,
        "original_sha256": digest(original.encode()),
        "patched_sha256": digest(patched.encode()),
        "patch_sha256": digest(diff.encode()),
        "proof_source_sha256": digest(Path(__file__).read_bytes()),
        "platform": platform.platform(),
        "functions": list(FUNCTIONS),
        "commands": [],
        "results": {},
        "generated_products_sha256": {},
        "scope": "source_control_flow_and_inert_buffer_ownership_only",
    }
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    include = [
        arg
        for path in [
            headers,
            *[headers / d for d in ("h", "numlib", "rspl", "cgats", "xicc", "spectro")],
        ]
        for arg in ("-I", path.as_posix())
    ]
    windows = platform.system() == "Windows"
    platform_flags = ["-DNT", "-DEN_LIBUSB0"] if windows else ["-DUNIX"]
    try:
        for variant, source in (("baseline", original), ("patched", patched)):
            c_file = output / f"{variant}.c"
            c_file.write_text(_probe_source(source), encoding="utf-8", newline="\n")
            executable = output / (variant + ".exe" if windows else variant)
            argv = [
                cc,
                "-std=gnu99" if windows else "-std=c99",
                "-DSALONEINSTLIB",
                *platform_flags,
                *include,
                c_file.as_posix(),
                "-o",
                executable.as_posix(),
            ]
            build = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            (output / f"{variant}-compile.log").write_text(
                build.stdout + build.stderr, encoding="utf-8", newline="\n"
            )
            report["commands"].append({"argv": argv, "returncode": build.returncode})
            build.check_returncode()
            report["generated_products_sha256"].update(
                {
                    c_file.name: digest(c_file.read_bytes()),
                    executable.name: digest(executable.read_bytes()),
                }
            )
            report["results"][variant] = {}
            for case in CASES:
                run = subprocess.run(
                    [str(executable), case], capture_output=True, text=True, timeout=10
                )
                (output / f"{variant}-{case}.log").write_text(
                    run.stdout + run.stderr, encoding="utf-8", newline="\n"
                )
                run.check_returncode()
                report["results"][variant][case] = json.loads(run.stdout)
        _validate(report["results"])
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = prove(args.archive.resolve(), args.output.resolve())
    print(
        json.dumps(
            {"status": report["status"], "cases_per_variant": len(CASES), "hardware_tested": False}
        )
    )


if __name__ == "__main__":
    main()
