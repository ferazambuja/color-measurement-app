"""Authenticated original-family calibration corrections and inert C proof.

Only the extracted Argyll calibration function executes, with substituted
measurement, status, buffer and cache-save hooks. No instrument runtime links,
hardware/cache file access, new acquisition route or build integration occurs.
Upstream source remains Graeme W. Gill's GPL-2.0-or-later work; this is an
attributed control-flow correction, not an original measurement algorithm.
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

REPAIR_ID = 'recipe:legacy-nonadaptive-calibration'

ARCHIVE_SHA256 = "f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f"
SOURCE_PATH = "spectro/i1pro_imp.c"
SOURCE_SHA256 = "8b8516cdd89e21de7ded3d58e1b03663c9908a343c82d393fedf9d1960ee6de6"
PREFIX = "Argyll_V3.5.0/"
FUNCTION = "i1pro_code i1pro_imp_calibrate(\n"
ORIGINAL_SELECTION = "if (ev == I1PRO_RD_SENSORSATURATED || scale < 1.0) {"
CORRECTED_SELECTION = "if (ev == I1PRO_RD_SENSORSATURATED || (ev == I1PRO_OK && scale < 1.0)) {"
INVALIDATE_SELECTION = (
    "\t\t\ts->done_dintsel = 0; /* A failed refresh must not retain validity. */\n"
)
_SCAN_SATURATION = "\t\t\tif (ev == I1PRO_RD_SENSORSATURATED) {\n\t\t\t\tscale = 0.0;"
_REFUSE_SCAN_SATURATION = """\t\t\tif (ev == I1PRO_RD_SENSORSATURATED) {
\t\t\t\t/* CM: fixed-time scan cannot recover or use unwritten white output. */
\t\t\t\tif (s->scan && !s->adaptive) {
\t\t\t\t\tm->mmode = mmode;
\t\t\t\t\treturn ev;
\t\t\t\t}
\t\t\t\tscale = 0.0;"""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _function(text: str, signature: str) -> str:
    if text.count(signature) != 1:
        raise ValueError(f"Missing or ambiguous pinned function: {signature}")
    start = text.index(signature)
    end = text.find("\n}\n", start)
    if end < 0:
        raise ValueError(f"Missing pinned function end: {signature}")
    return text[start : end + 3]


_HARNESS = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
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
#include "i1pro.h"
#include "i1pro_imp.h"
#ifndef CM_PATCHED
#error "An explicit baseline/patched harness variant is required"
#endif

typedef struct { int code, writes_scale; double scale; } probe;
static probe plan[4];
static int plan_count, reads, allocations, frees, live_buffer, saves, scale_initialized;
static double buffer[5], dark[4][5], probe_times[4], supplied_scales[4];
static int returned_codes[4], written[4];
static void unexpected(const char *why) {
    fprintf(stderr, "Unexpected path: %s\n", why); abort();
}
static double *allocate_buffer(int low, int high) {
    if (low != -1 || high != 3 || live_buffer) unexpected("buffer allocation");
    memset(buffer, 0, sizeof(buffer)); ++allocations; live_buffer = 1;
    return buffer + 1;
}
static void release_buffer(double *value, int low, int high) {
    if (value != buffer + 1 || low != -1 || high != 3 || !live_buffer)
        unexpected("buffer release");
    ++frees; live_buffer = 0;
}
static int calibration_status(i1pro *p, inst_cal_type *needed, inst_cal_type *available) {
    i1proimp *m = (i1proimp *)p->m;
    *available = inst_calt_em_dark | inst_calt_emis_int_time;
    *needed = m->ms[i1p_emiss_spot_na].done_dintsel ? inst_calt_none : inst_calt_emis_int_time;
    return I1PRO_OK;
}
static int measurement_count(i1pro *p, double read_time, double integration_time) {
    (void)p;
    if (read_time != DISP_INTT || integration_time <= 0) unexpected("measurement count");
    return 1;
}
static int white_probe(i1pro *p, double *a, double *b, double *data, double *scale,
                       int count, double *integration_time, int gain, double target, int lamp) {
    i1proimp *m = (i1proimp *)p->m;
    probe result;
    if (m->mmode != i1p_emiss_spot_na || a || b || data != buffer + 1 || !live_buffer
        || count != 1 || gain != 0 || target != 0.9 || lamp != 0 || reads >= plan_count)
        unexpected("measurement arguments or unplanned extra probe");
    result = plan[reads]; probe_times[reads] = *integration_time;
    returned_codes[reads] = result.code; written[reads] = result.writes_scale;
    supplied_scales[reads] = result.scale;
    if (result.writes_scale) { *scale = result.scale; scale_initialized = 1; }
    /* Never exercise baseline's undefined first non-saturation error path.
     * The corrected expression short-circuits without consulting scale. */
    if (!CM_PATCHED && result.code != I1PRO_RD_SENSORSATURATED && !scale_initialized)
        unexpected("excluded uninitialized scale path");
    ++reads; return result.code;
}

#define a1logd(...) ((void)0)
#define time(...) ((time_t)1234567)
#define msec_time(...) 0
#define ENABLE_NONVCAL
#define i1pro_imp_get_n_a_cals(...) calibration_status(__VA_ARGS__)
#define i1pro_comp_nummeas(...) measurement_count(__VA_ARGS__)
#define i1pro_whitemeasure(...) white_probe(__VA_ARGS__)
#define dvectorz(...) allocate_buffer(__VA_ARGS__)
#define free_dvector(...) release_buffer(__VA_ARGS__)
#define i1pro_save_calibration(...) (++saves, I1PRO_OK)
#define i1pro_update_log(...) (unexpected("EEPROM log"), I1PRO_INTERNAL_ERROR)
#define i1pro2_wl_measure(...) (unexpected("wavelength measure"), I1PRO_INTERNAL_ERROR)
#define i1pro2_match_wl_meas(...) (unexpected("wavelength matching"), I1PRO_INTERNAL_ERROR)
#define i1pro_check_white_reference1(...) (unexpected("white reference"), I1PRO_INTERNAL_ERROR)
#define i1pro_compute_wav_filters(...) (unexpected("wavelength filters"), I1PRO_INTERNAL_ERROR)
#define i1pro_compute_white_cal(...) (unexpected("white calibration"), I1PRO_INTERNAL_ERROR)
#define i1pro_create_hr(...) (unexpected("high resolution"), I1PRO_INTERNAL_ERROR)
#define i1pro_dark_measure(...) (unexpected("dark acquisition"), I1PRO_INTERNAL_ERROR)
#define i1pro_interp_dark(...) (unexpected("dark interpolation"), I1PRO_INTERNAL_ERROR)
#define i1pro_optimise_sensor(...) (unexpected("sensor optimization"), I1PRO_INTERNAL_ERROR)
#define i1pro_prepare_idark(...) (unexpected("adaptive dark"), I1PRO_INTERNAL_ERROR)
#define plot_raw2(...) unexpected("plot")
"""
_MAIN = r"""
static void run_case(const char *name, const probe *steps, int count, int prior_swap,
                     instType dtype, int prior_done, i1p_mode original_mode) {
    i1pro meter;
    i1proimp implementation;
    i1pro_state *state;
    inst_cal_type remaining = inst_calt_emis_int_time;
    inst_cal_cond condition = inst_calc_emis_white;
    inst_calc_id_type reference_type = inst_calc_id_none;
    char reference[CALIDLEN] = {0};
    int code, dark_slot = -1;
    memset(&meter, 0, sizeof(meter)); memset(&implementation, 0, sizeof(implementation));
    reads = allocations = frees = live_buffer = saves = scale_initialized = 0;
    plan_count = count; memcpy(plan, steps, count * sizeof(*steps));
    meter.m = &implementation; meter.dtype = dtype;
    implementation.mmode = original_mode;
    if (original_mode != i1p_emiss_spot_na) remaining |= inst_calt_ap_flag;
    implementation.nraw = 4; implementation.min_int_time = 0.01;
    state = &implementation.ms[i1p_emiss_spot_na];
    state->mode = i1p_emiss_spot_na; state->emiss = 1; state->adaptive = 0;
    state->dark_valid = state->cal_valid = 1; state->ddate = (time_t)1234567;
    state->done_dintsel = prior_done; state->diseldate = prior_done ? 123 : 0;
    state->inttime = DISP_INTT; state->dark_int_time2 = DISP_INTT2;
    state->dark_int_time3 = DISP_INTT3; state->dark_int_time4 = DISP_INTT4;
    state->dark_data = dark[0] + 1; state->dark_data2 = dark[1] + 1;
    state->dark_data3 = dark[2] + 1; state->dark_data4 = dark[3] + 1;
    state->targoscale = 0.9; state->wreadtime = DISP_INTT;
    if (prior_swap) {
        double *time_slot = prior_swap == 1 ? &state->dark_int_time2
            : prior_swap == 2 ? &state->dark_int_time3 : &state->dark_int_time4;
        double **data_slot = prior_swap == 1 ? &state->dark_data2
            : prior_swap == 2 ? &state->dark_data3 : &state->dark_data4;
        double t = state->inttime, *p = state->dark_data;
        state->inttime = *time_slot; *time_slot = t;
        state->dark_data = *data_slot; *data_slot = p;
        state->dispswap = prior_swap;
    }
    code = i1pro_imp_calibrate(&meter, &remaining, &condition, &reference_type, reference);
    for (int i = 0; i < 4; ++i) if (state->dark_data == dark[i] + 1) dark_slot = i;
    if (implementation.mmode != original_mode || dark_slot != state->dispswap
        || allocations != 1 || frees != 1 || live_buffer != 0 || reads < 1 || reads > count)
        unexpected("mode, ownership, probe count or matching dark pointer");
    double bank_seconds[4] = {state->inttime, state->dark_int_time2,
        state->dark_int_time3, state->dark_int_time4};
    double *bank_data[4] = {state->dark_data, state->dark_data2,
        state->dark_data3, state->dark_data4};
    int slots[4] = {-1,-1,-1,-1};
    for (int i=0;i<4;++i) for (int j=0;j<4;++j)
        if (bank_data[i]==dark[j]+1) slots[i]=j;
    for (int i=0;i<4;++i) if (slots[i]<0) unexpected("unknown dark bank");
    printf("{\"case\":\"%s\",\"dtype\":%d,\"return_code\":%d,\"done\":%.17g,"
           "\"remaining\":%u,\"selected_seconds\":%.17g,\"swap\":%d,\"dark_slot\":%d,"
           "\"prior_swap\":%d,\"prior_done\":%d,\"selection_date\":%lld,"
           "\"allocations\":%d,\"frees\":%d,\"live_buffers\":%d,"
           "\"inert_cache_save_hooks\":%d,\"original_mode\":%d,\"final_mode\":%d,"
           "\"dark_date\":%lld,\"bank_seconds\":[%.17g,%.17g,%.17g,%.17g],"
           "\"dark_slots\":[%d,%d,%d,%d],\"probes\":[",name,dtype,code,state->done_dintsel,
           (unsigned)remaining,state->inttime,state->dispswap,dark_slot,prior_swap,
           prior_done,(long long)state->diseldate,
           allocations,frees,live_buffer,saves,original_mode,implementation.mmode,
           (long long)state->ddate,bank_seconds[0],bank_seconds[1],bank_seconds[2],bank_seconds[3],
           slots[0],slots[1],slots[2],slots[3]);
    for (int i = 0; i < reads; ++i) {
        printf("%s{\"seconds\":%.17g,\"return_code\":%d,\"wrote_scale\":%s,\"scale\":",
               i ? "," : "",probe_times[i],returned_codes[i],written[i] ? "true" : "false");
        if (written[i]) printf("%.17g",supplied_scales[i]); else fputs("null",stdout);
        fputc('}',stdout);
    }
    puts("]}");
}
int main(void) {
    const probe keep[] = {{I1PRO_OK,1,1.5}};
    const probe second[] = {{I1PRO_OK,1,.5},{I1PRO_OK,1,1.5}};
    const probe third[] = {{I1PRO_OK,1,.5},{I1PRO_OK,1,.5},{I1PRO_OK,1,1.5}};
    const probe fourth[] = {{I1PRO_OK,1,.5},{I1PRO_OK,1,.5},{I1PRO_OK,1,.5}};
    const probe swallowed[] = {{I1PRO_OK,1,.5},{I1PRO_COMS_FAIL,0,0},{I1PRO_OK,1,1.5}};
    const probe third_error[] = {{I1PRO_OK,1,.5},{I1PRO_OK,1,.5},{I1PRO_COMS_FAIL,0,0}};
    const probe third_saturation[] = {{I1PRO_OK,1,.5},{I1PRO_OK,1,.5},
        {I1PRO_RD_SENSORSATURATED,0,0}};
    const probe first_saturation[] = {{I1PRO_RD_SENSORSATURATED,0,0},{I1PRO_OK,1,1.5}};
    const probe two_saturations[] = {{I1PRO_RD_SENSORSATURATED,0,0},
        {I1PRO_RD_SENSORSATURATED,0,0},{I1PRO_OK,1,1.5}};
    const probe all_saturation[] = {{I1PRO_RD_SENSORSATURATED,0,0},
        {I1PRO_RD_SENSORSATURATED,0,0},{I1PRO_RD_SENSORSATURATED,0,0}};
#if CM_PATCHED
    const probe first_error[] = {{I1PRO_COMS_FAIL,0,0}};
#endif
    printf("{\"constants\":{\"ok\":%d,\"communications_error\":%d,\"saturation\":%d,"
           "\"integration_bit\":%u,\"nonadaptive_ordinal\":%d}}\n",
           I1PRO_OK,I1PRO_COMS_FAIL,I1PRO_RD_SENSORSATURATED,
           (unsigned)inst_calt_emis_int_time,i1p_emiss_spot_na);
    instType models[] = {instI1Pro,instI1Pro2};
    for (int model=0;model<2;++model) {
        instType dtype=models[model];
        run_case("keep_2_seconds",keep,1,0,dtype,0,i1p_emiss_spot_na);
        run_case("select_0_8_seconds",second,2,0,dtype,0,i1p_emiss_spot_na);
        run_case("select_0_3_seconds",third,3,0,dtype,0,i1p_emiss_spot_na);
        run_case("select_unprobed_0_1_seconds",fourth,3,0,dtype,0,i1p_emiss_spot_na);
        run_case("second_error_swallowed",swallowed,3,0,dtype,0,i1p_emiss_spot_na);
        run_case("third_error_changes_selection",third_error,3,0,dtype,0,i1p_emiss_spot_na);
        run_case("third_saturation_changes_selection",third_saturation,3,0,dtype,0,i1p_emiss_spot_na);
        run_case("first_saturation_then_success",first_saturation,2,0,dtype,0,i1p_emiss_spot_na);
        run_case("two_saturations_then_success",two_saturations,3,0,dtype,0,i1p_emiss_spot_na);
        run_case("all_saturation",all_saturation,3,0,dtype,0,i1p_emiss_spot_na);
        run_case("undo_prior_0_8_seconds",keep,1,1,dtype,0,i1p_emiss_spot_na);
        run_case("undo_prior_0_3_seconds",keep,1,2,dtype,0,i1p_emiss_spot_na);
        run_case("undo_prior_0_1_seconds",keep,1,3,dtype,0,i1p_emiss_spot_na);
        run_case("prior_valid_success",keep,1,2,dtype,1,i1p_emiss_spot_na);
        run_case("prior_valid_second_error",swallowed,3,2,dtype,1,i1p_emiss_spot_na);
        run_case("prior_done_preserved_on_third_error",third_error,3,2,dtype,1,i1p_emiss_spot_na);
        run_case("prior_done_preserved_on_third_saturation",third_saturation,3,2,dtype,1,i1p_emiss_spot_na);
        run_case("all_modes_second_error",swallowed,3,0,dtype,0,i1p_emiss_spot);
#if CM_PATCHED
        run_case("first_probe_error",first_error,1,0,dtype,0,i1p_emiss_spot_na);
        run_case("prior_valid_first_probe_error",first_error,1,2,dtype,1,i1p_emiss_spot_na);
#endif
    }
    return 0;
}
"""


def _probe_source(text: str) -> str:
    definitions = []
    for name in ("DISP_INTT", "DISP_INTT2", "DISP_INTT3", "DISP_INTT4"):
        found = re.findall(r"^#define " + name + r"\s+[^\n]+", text, re.M)
        if len(found) != 1:
            raise ValueError(f"Missing or ambiguous pinned integration time: {name}")
        definitions.append(found[0])
    function = _function(text, FUNCTION)
    line = text[: text.index(FUNCTION)].count("\n") + 1
    return (
        "/* ArgyllCMS 3.5.0 extracted function: Graeme W. Gill, GPL-2.0-or-later.\n"
        " * Synthetic harness and substitutions do not implement a device protocol. */\n"
        + "\n".join(definitions)
        + "\n"
        + _HARNESS
        + f'\n#line {line} "Argyll-3.5.0/{SOURCE_PATH}"\n'
        + function
        + '\n#line 1 "legacy-nonadaptive-inert-main"\n'
        + _MAIN
    )


def _validate(results: dict, constants: dict, patched_only: dict) -> None:
    baseline, patched = results["baseline"], results["patched"]
    if baseline.keys() != patched.keys() or len(baseline) != 36:
        raise RuntimeError("Incomplete paired legacy proof cases")
    invariant = {
        "keep_2_seconds",
        "select_0_8_seconds",
        "select_0_3_seconds",
        "select_unprobed_0_1_seconds",
        "first_saturation_then_success",
        "two_saturations_then_success",
        "all_saturation",
        "third_saturation_changes_selection",
        "undo_prior_0_8_seconds",
        "undo_prior_0_3_seconds",
        "undo_prior_0_1_seconds",
        "prior_valid_success",
    }
    for key, old in baseline.items():
        new = patched[key]
        name = old["case"]
        if name in invariant and old != new:
            raise RuntimeError(f"Unaffected legacy behavior changed: {key}")
        for row in (old, new):
            if not (row["allocations"] == row["frees"] == 1 and row["live_buffers"] == 0):
                raise AssertionError('Proof verdict failed')
            if not (row["final_mode"] == row["original_mode"]):
                raise AssertionError('Proof verdict failed')
            if not (row["dark_date"] == 1234567):
                raise AssertionError('Proof verdict failed')
            if not (sorted(row["dark_slots"]) == [0, 1, 2, 3]):
                raise AssertionError('Proof verdict failed')
            if not (all(
                time == (2.0, 0.8, 0.3, 0.1)[slot]
                for time, slot in zip(row["bank_seconds"], row["dark_slots"], strict=True)
            )):
                raise AssertionError('Proof verdict failed')
        if name in {"second_error_swallowed", "prior_valid_second_error", "all_modes_second_error"}:
            if not (len(old["probes"]) == 3 and old["return_code"] == constants["ok"]):
                raise AssertionError('Proof verdict failed')
            if not (old["done"] == 1 and not old["remaining"] & constants["integration_bit"]):
                raise AssertionError('Proof verdict failed')
            if not (old["selected_seconds"] == 0.3 and old["inert_cache_save_hooks"] == 1):
                raise AssertionError('Proof verdict failed')
            if not (len(new["probes"]) == 2 and new["selected_seconds"] == 0.8):
                raise AssertionError('Proof verdict failed')
        elif name in {"third_error_changes_selection", "prior_done_preserved_on_third_error"}:
            if not (len(new["probes"]) == len(old["probes"]) == 3):
                raise AssertionError('Proof verdict failed')
            if not (old["selected_seconds"] == 0.1 and new["selected_seconds"] == 0.3):
                raise AssertionError('Proof verdict failed')
        elif name == "prior_done_preserved_on_third_saturation":
            if not (old["done"] == 1 and new["done"] == 0):
                raise AssertionError('Proof verdict failed')
            if not (old["selected_seconds"] == new["selected_seconds"] == 0.1):
                raise AssertionError('Proof verdict failed')
        if name not in invariant:
            expected = (
                constants["saturation"]
                if name.endswith("saturation")
                else constants["communications_error"]
            )
            if not (new["return_code"] == expected and new["done"] == 0):
                raise AssertionError('Proof verdict failed')
            if not (new["remaining"] & constants["integration_bit"]):
                raise AssertionError('Proof verdict failed')
            if not (new["selection_date"] == (123 if new["prior_done"] else 0)):
                raise AssertionError('Proof verdict failed')
            if not (new["inert_cache_save_hooks"] == 0):
                raise AssertionError('Proof verdict failed')
        if name == "select_unprobed_0_1_seconds":
            if not (new["return_code"] == constants["ok"] and new["selected_seconds"] == 0.1):
                raise AssertionError('Proof verdict failed')
            if not ([p["seconds"] for p in new["probes"]] == [2, 0.8, 0.3]):
                raise AssertionError('Proof verdict failed')
        if name in {"all_saturation", "third_saturation_changes_selection"}:
            if not (new["return_code"] == constants["saturation"] and new["done"] == 0):
                raise AssertionError('Proof verdict failed')
            if not (new["selected_seconds"] == 0.1):
                raise AssertionError('Proof verdict failed')
    expected = {
        f"{model}:{case}"
        for model in (21, 22)
        for case in ("first_probe_error", "prior_valid_first_probe_error")
    }
    if patched_only.keys() != expected or baseline.keys() & expected:
        raise RuntimeError("Missing patched-only controls or unsafe baseline case inclusion")
    for row in patched_only.values():
        if not (len(row["probes"]) == 1):
            raise AssertionError('Proof verdict failed')
        if not (row["probes"][0] == {
            "seconds": 2,
            "return_code": constants["communications_error"],
            "wrote_scale": False,
            "scale": None,
        }):
            raise AssertionError('Proof verdict failed')
        if not (row["return_code"] == constants["communications_error"] and row["done"] == 0):
            raise AssertionError('Proof verdict failed')
        if not (row["remaining"] == constants["integration_bit"]):
            raise AssertionError('Proof verdict failed')
        if not (row["allocations"] == row["frees"] == 1 and row["live_buffers"] == 0):
            raise AssertionError('Proof verdict failed')
        if not (row["inert_cache_save_hooks"] == 0):
            raise AssertionError('Proof verdict failed')
        if not (row["final_mode"] == row["original_mode"]):
            raise AssertionError('Proof verdict failed')
        if not (row["selection_date"] == (123 if row["prior_done"] else 0)):
            raise AssertionError('Proof verdict failed')
        if not (row["dark_date"] == 1234567):
            raise AssertionError('Proof verdict failed')
        if not (row["bank_seconds"] == [2, 0.8, 0.3, 0.1]):
            raise AssertionError('Proof verdict failed')
        if not (row["dark_slots"] == [0, 1, 2, 3]):
            raise AssertionError('Proof verdict failed')


@production_proof
def prove(archive: Path, output: Path, *, compiler: str = "cc",
          source: Path | None = None) -> dict:
    """Compile/run only inert extracted baseline and corrected functions."""
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    output = output.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        original = before_source("legacy_nonadaptive_calibration", SOURCE_PATH, archive)
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
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"Argyll-3.5.0/{SOURCE_PATH}",
            tofile=f"proposed-local-patch/{SOURCE_PATH}",
        )
    )
    for name, text in (
        ("i1pro_imp.original.c", original),
        ("i1pro_imp.patched.c", patched),
        ("legacy-nonadaptive-calibration.patch", diff),
    ):
        (output / name).write_text(text, encoding="utf-8", newline="\n")
    (output / "UPSTREAM.txt").write_text(
        "ArgyllCMS 3.5.0, Graeme W. Gill and credited contributors.\n"
        "https://www.argyllcms.com/Argyll_V3.5.0_src.zip\n"
        "Extracted driver functions remain GPL-2.0-or-later upstream work.\n"
        "Headers retain their own notices and terms; see Argyll-License2.txt.\n"
        "The patch guards scale, invalidates refreshed integration selection, and refuses\n"
        "saturation before reusing unwritten nonadaptive-scan white output.\n"
        "Only source-extracted functions with inert hooks execute; no driver runtime.\n",
        encoding="utf-8",
        newline="\n",
    )
    report = {
        "status": "incomplete",
        "hardware_tested": False,
        "instrument_runtime_linked": False,
        "cache_file_io": False,
        "build_integration": False,
        "new_mode_exposed": False,
        "archive_sha256": ARCHIVE_SHA256,
        "upstream_path": SOURCE_PATH,
        "original_sha256": digest(original.encode()),
        "patched_sha256": digest(patched.encode()),
        "patch_sha256": digest(diff.encode()),
        "proof_source_sha256": digest(Path(__file__).read_bytes()),
        "platform": platform.platform(),
        "function": FUNCTION.strip(),
        "function_original_sha256": digest(_function(original, FUNCTION).encode()),
        "function_patched_sha256": digest(_function(patched, FUNCTION).encode()),
        "scope": "source_calibration_control_flow_buffers_and_standard_transmission_factors",
        "undefined_uninitialized_scale_paths_excluded": True,
        "baseline_uninitialized_scale_cases_executed": 0,
        "limitations": [
            "Baseline excludes initial non-saturation failures before scale assignment.",
            "Valid dark calibration is synthetic prerequisite state; optical behavior is untested.",
            "Failure preserves selection and dark-calibration dates; no expiry is inferred.",
            "Clearing done_dintsel updates calibration reporting; direct reads remain unguarded.",
            "A future application route must require successful calibration before reading.",
            "The frozen baseline can leave a cached needed query at zero on a forced "
            "transmission-white error. Current source invalidates white calibration and "
            "reports the pending need; old factors and dates remain unchanged.",
        ],
        "commands": [],
        "results": {},
        "patched_only_results": {},
        "transmission_scan_results": {},
        "generated_products_sha256": {},
    }
    include = [
        f"-I{path}"
        for path in [
            headers,
            *[headers / p for p in ("h", "numlib", "rspl", "cgats", "xicc", "spectro")],
        ]
    ]
    windows = platform.system() == "Windows"
    flags = [
        "-std=gnu99",
        "-O1",
        "-DSALONEINSTLIB",
        "-DENABLE_USB",
        *(["-DNT", "-DEN_LIBUSB0"] if windows else ["-DUNIX"]),
        *include,
    ]

    def run(argv, label):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        (output / f"{label}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        report["commands"].append({"argv": argv, "returncode": result.returncode})
        if result.returncode:
            raise RuntimeError(f"{label} failed; see retained log")
        return result.stdout

    try:
        report["compiler_version"] = run([cc, "--version"], "compiler-version")
        for variant, source in (("baseline", original), ("patched", patched)):
            unit = output / f"{variant}.c"
            unit.write_text(_probe_source(source), encoding="utf-8", newline="\n")
            executable = output / (variant + ".exe" if windows else variant)
            run(
                [
                    cc,
                    *flags,
                    f"-DCM_PATCHED={int(variant == 'patched')}",
                    str(unit),
                    "-lm",
                    "-o",
                    str(executable),
                ],
                f"{variant}-compile",
            )
            rows = [
                json.loads(line) for line in run([str(executable)], f"{variant}-run").splitlines()
            ]
            constants = rows[0]["constants"]
            if "constants" in report and report["constants"] != constants:
                raise RuntimeError("Compiled native constants differ between variants")
            report["constants"] = constants
            paired = {}
            for row in rows[1:]:
                key = f"{row['dtype']}:{row['case']}"
                if row["case"] in {"first_probe_error", "prior_valid_first_probe_error"}:
                    if variant != "patched":
                        raise RuntimeError("Unsafe baseline first-error case executed")
                    report["patched_only_results"][key] = row
                else:
                    paired[key] = row
            report["results"][variant] = paired
            # Same authenticated calibration function, now in the fixed-time
            # transmission-scan state. Real factor arithmetic, inert acquisition.
            scan = output / f"{variant}-transmission-scan"
            scan.mkdir()
            for name, signature in (
                ("calibrate.inc", FUNCTION),
                ("status.inc", "i1pro_code i1pro_imp_get_n_a_cals("),
                ("measurement_count.inc", "int i1pro_comp_nummeas(\n"),
                ("white_factor.inc", "i1pro_code i1pro_compute_white_cal(\n"),
            ):
                (scan / name).write_text(_function(source, signature))
            definitions = []
            for name in ("DISP_INTT", "DISP_INTT2", "DISP_INTT3", "DISP_INTT4",
                         "WLCALTOUT", "DCALTOUT", "DCALTOUT2", "WCALTOUT"):
                matches = re.findall(r"^#define " + name + r"\s+[^\n]+", source, re.M)
                if len(matches) != 1:
                    raise ValueError(f"Unexpected native definition: {name}")
                definitions.append(matches[0])
            (scan / "definitions.inc").write_text("\n".join(definitions) + "\n")
            harness = Path(__file__).with_name("legacy_transmission_calibration_probe.c")
            scan_unit = scan / "probe.c"
            scan_unit.write_bytes(harness.read_bytes())
            scan_executable = scan / ("probe.exe" if windows else "probe")
            run([cc, *flags, f"-I{scan}", f"-DCM_PATCHED={int(variant == 'patched')}",
                 str(scan_unit), "-lm", "-o", str(scan_executable)], f"{variant}-scan-compile")
            report["transmission_scan_results"][variant] = [
                json.loads(run([str(scan_executable), model, case],
                               f"{variant}-scan-{model}-{case}"))
                for model in ("i1pro", "i1pro2")
                for case in ("validwhite", "saturated", "misread",
                             "cached_validwhite", "cached_saturated", "cached_misread")
            ]
        for before, after in zip(report["transmission_scan_results"]["baseline"],
                                 report["transmission_scan_results"]["patched"], strict=True):
            if before["case"] in ("misread", "cached_misread"):
                # Later white-calibration safety invalidates state before a
                # capture that may overwrite it. Preserve every other result.
                if not (before["cal_valid"] == 1 and after["cal_valid"] == 0
                        and before["needed"] == (0 if before["case"].startswith("cached_")
                                                 else 0x1000)
                        and after["needed"] == 0x1000):
                    raise AssertionError("Failed transmission white must invalidate calibration")
                unchanged = set(before) - {"cal_valid", "needed"}
                if set(before) != set(after) or any(before[key] != after[key] for key in unchanged):
                    raise AssertionError("Unrelated transmission-white failure result changed")
            elif before["case"] not in ("saturated", "cached_saturated"):
                if not (before == after):
                    raise AssertionError('Proof verdict failed')
        report["transmission_scan_paired_case_count"] = 12
        _validate(report["results"], report["constants"], report["patched_only_results"])
        report["paired_case_count"] = len(report["results"]["patched"])
        report["patched_only_case_count"] = len(report["patched_only_results"])
        report["status"] = "passed"
    except (AssertionError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report["status"] = "failed"
        report["error"] = str(error) or type(error).__name__
        raise
    finally:
        report["generated_products_sha256"] = {
            path.relative_to(output).as_posix(): digest(path.read_bytes())
            for path in sorted(output.rglob("*"))
            if path.is_file() and path.name != "report.json"
        }
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compiler", default="cc")
    args = parser.parse_args()
    result = prove(args.archive, args.output, compiler=args.compiler)
    print(
        f"PASS: {result['paired_case_count']} paired legacy control-flow cases; "
        f"{result['patched_only_case_count']} patched-only first-error cases; "
        f"{result['transmission_scan_paired_case_count']} paired transmission-scan cases"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
