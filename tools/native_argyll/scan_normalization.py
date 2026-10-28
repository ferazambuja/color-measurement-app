"""Authenticated scan-recognition normalization guard and inert source regression.

Extracted Argyll functions remain Graeme W. Gill's GPL-2.0-or-later work.
No instrument runtime, transport, calibration cache or physical acquisition runs.
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

from native_argyll import sanitizers
from native_argyll.proof_inputs import (
    before_source,
    production_proof,
    production_support,
    production_text,
)

REPAIR_ID = 'recipe:scan-normalization'

ARCHIVE_SHA256 = "f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f"
SOURCE_PATH = "spectro/i1pro3_imp.c"
SOURCE_SHA256 = "42c684afa3bbb15dbbb880820c55a17f32d79ceaa7f8cbcf07cddaf3f2073e32"
NUMLIB_SHA256 = "cf361c4acf302de8ab112f5da90e62943ffa5ff664fbe2eae4646f438a49e4f5"
PREFIX = "Argyll_V3.5.0/"
SCAN_SIGNATURE = "i1pro3_code i1pro3_scan_emis_meas(\n"
VECTOR_FUNCTIONS = ("vect_set", "vect_max_elem", "vect_div3", "vect_add", "vect_scale1",
                    "vect_blend")
CASES = (
    "finite", "mismatch", "negative_denominator", "negative_mismatch",
    "zero_over_zero", "positive_over_zero", "nan_denominator", "inf_denominator",
    "nan_numerator", "inf_numerator", "overflow", "band50_zero", "all_zero",
    "emissive_finite", "emissive_mismatch", "emissive_zero", "emissive_negative",
    "no_zebra_finite", "no_zebra_mismatch", "no_zebra_zero",
    "tiny_finite", "tiny_negative", "negative_zero", "negative_inf_denominator",
    "zero_signal", "leader_overflow", "zero_leaders", "zero_leader", "tiny_trailer",
    "zebra_pair_finite", "zebra_slot_overflow",
)
RATIO_CASES = ("ratio_equal", "ratio_negative_equal", "ratio_mismatch", "ratio_both_zero",
               "ratio_one_zero", "ratio_nan", "ratio_inf", "ratio_finite_overflow",
               "ratio_finite_tiny")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _function(text: str, signature: str) -> str:
    if text.count(signature) != 1:
        raise ValueError(f"Missing or ambiguous pinned function: {signature}")
    start = text.index(signature)
    end = text.find("\n}\n", start)
    if end < 0:
        raise ValueError("Missing pinned function end")
    return text[start:end + 3]


def _recognizer(text: str) -> str:
    start, end = text.index("#define BL 20"), text.index("\n#undef BL")
    return text[start:end]


_HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <float.h>
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
struct _usb_cancelt { int inert; };
#include "i1pro3_imp.h"
@@CONSTANTS@@
static const char *scenario;
static int matrices, vectors, measures, raw_frees, zebra_frees, converted, averaged;
static int locate_calls, locate_code = -1, nonfinite_proxy, curve_constructors;
static i1pro3_patch windows[3];
static int zebra_data[500][3], *zebra[500];
static void unexpected(void) { fputs("Unexpected unsubstituted path\n", stderr); abort(); }
static int is(const char *name) { return !strcmp(scenario,name); }
static int paired_rows(void) { return is("zebra_pair_finite") || is("zebra_slot_overflow"); }
/* Like upstream numsup, store the allocation base before the row pointers so
 * bounds passed to free do not reconstruct or guess an allocation address. */
static double **matrix(int rlo,int rhi,int clo,int chi) {
    if(rlo!=0 || rhi<0 || clo>0 || chi<clo) unexpected();
    double **v=calloc((size_t)rhi+2,sizeof(*v));
    double *data=calloc(((size_t)rhi+1)*(chi-clo+1),sizeof(*data));
    if(!v || !data) unexpected();
    v[0]=data; v++;
    for(int i=0;i<=rhi;i++) v[i]=data+i*(chi-clo+1)-clo;
    matrices++; return v;
}
static void matrix_free(double **v,int a,int b,int c,int d) {
    (void)a;(void)b;(void)c;(void)d;
    if(!v || matrices<=0) unexpected(); free(v[-1]); free(v-1); matrices--;
}
static double *vector(int lo,int hi) {
    if(lo!=0 || hi<0) unexpected(); vectors++;
    double *v=calloc((size_t)hi+1,sizeof(*v)); if(!v) unexpected(); return v;
}
static void vector_free(double *v,int lo,int hi) {
    (void)hi; if(lo!=0 || vectors<=0) unexpected(); free(v); vectors--;
}
static int *ivector_zero(int lo,int hi) {
    if(lo!=0 || hi<0) unexpected(); vectors++;
    int *v=calloc((size_t)hi+1,sizeof(*v)); if(!v) unexpected(); return v;
}
static void ivector_free(int *v,int lo,int hi) {
    (void)hi; if(lo!=0 || vectors<=0) unexpected(); free(v); vectors--;
}
static void curve_delete(rspl *curve) { free(curve); }
static rspl *curve_new(int flags,int di,int fdi) {
    (void)flags; if(di!=1 || fdi!=1) unexpected(); curve_constructors++;
    rspl *v=calloc(1,sizeof(*v)); if(!v) unexpected(); v->del=curve_delete; return v;
}
static double level(int row,int channel) {
    double end = is("mismatch") || is("negative_mismatch") || is("zero_over_zero")
        || is("positive_over_zero") || is("emissive_mismatch") || is("no_zebra_mismatch")
        ? .55 : 1.;
    if(is("tiny_trailer")) end=1e-310;
    if(is("zero_leaders")) end=0.;
    double start=is("zero_leaders") || is("zero_leader") ? 0. : 1.;
    double values[]={start,.15,.60,.15,end}, value=values[row/100];
    if(row<400 && row%100>=80)
        value += (values[row/100+1]-value)*(row%100-80)/20.;
    return is("zero_leaders") || is("zero_leader") || is("tiny_trailer") ? value
        : value+.1*(1.-value)*sin(channel*.1);
}
static inst_code measure(i1pro3 *p,i1p3_mmode mode,double ***out,int *n,
                        double *t,int ***positions,int *npos) {
    (void)t; if(mode!=i1p3mm_em || ++measures>3) unexpected();
    *n = measures==2 ? (paired_rows() ? 1000 : 500) : 1; *out=matrix(0,*n-1,-1,127);
    for(int i=0;i<*n;i++) for(int j=0;j<128;j++) {
        double value=measures==2 ? level(paired_rows() ? i/2 : i,j)*10000. : 1.;
        if(measures==2 && ((j==0 && (is("zero_over_zero") || is("emissive_zero")
                           || is("no_zebra_zero"))) || is("all_zero") || is("zero_signal")))
            value=0.;
        if(measures==2 && j==50 && is("band50_zero")) value=0.;
        if(measures==2 && j==0 && is("nan_numerator")) value=NAN;
        if(measures==2 && j==0 && is("inf_numerator")) value=INFINITY;
        if(measures==2 && j==0 && is("emissive_negative")) value=-value;
        if(measures==2 && j==0 && (is("tiny_finite") || is("tiny_negative")))
            value=level(i,j)*DBL_MIN;
        if(measures==2 && j==0 && is("zebra_slot_overflow"))
            value=(i>=200 && i<800) ? 10000. : 1.;
        (*out)[i][j]=value;
    }
    if(positions && strncmp(scenario,"no_zebra_",9)) {
        for(int i=0;i<500;i++) {
            zebra_data[i][0]=paired_rows() ? 2 : 1;
            zebra_data[i][1]=paired_rows() ? 2*i : i; zebra_data[i][2]=2*i+1;
            zebra[i]=&zebra_data[i][1]; }
        *positions=zebra; *npos=500;
    }
    (void)p; return I1PRO3_OK;
}
static void raw_free(i1pro3 *p,i1p3_mmode mode,double **raw,int n) {
    (void)p;if(mode!=i1p3mm_em) unexpected();
    matrix_free(raw,0,n-1,-1,127);raw_frees++;
}
static void zebra_free(int **v,int n) {
    if(v!=zebra || n!=500 || ++zebra_frees!=1) unexpected();
}
static void convert(i1pro3 *p,int hr,int mode,double **out,double **in,int n) {
    (void)p;(void)hr;(void)in;
    if(mode!=0 || n!=(paired_rows() ? 1000 : 500)) unexpected(); converted++;
    for(int i=0;i<n;i++) out[i][0]=17.;
}
static void average(i1pro3 *p,double *out,double **in,int n,int hr) {
    (void)p;(void)in;(void)n;(void)hr;averaged++;out[0]=17.;
}
#define a1logd(...) ((void)0)
#define error(...) unexpected()
#define dmatrix(...) matrix(__VA_ARGS__)
#define dmatrixz(...) matrix(__VA_ARGS__)
#define free_dmatrix(...) matrix_free(__VA_ARGS__)
#define dvector(...) vector(__VA_ARGS__)
#define dvectorz(...) vector(__VA_ARGS__)
#define free_dvector(...) vector_free(__VA_ARGS__)
#define ivectorz(...) ivector_zero(__VA_ARGS__)
#define free_ivector(...) ivector_free(__VA_ARGS__)
#define new_rspl(...) curve_new(__VA_ARGS__)
#define i1pro3_getadaptype(...) (unexpected(),I1PRO3_INTERNAL_ERROR)
#define i1pro3_getboardtemp(p,t) (*(t)=23.,I1PRO3_OK)
#define i1pro3_recompute_wav_filters_for_temp(...) (unexpected(),I1PRO3_INTERNAL_ERROR)
#define i1pro3_comp_nummeas(...) 1
#define i1pro3_do_measure(...) measure(__VA_ARGS__)
#define i1pro3_free_raw(...) raw_free(__VA_ARGS__)
#define i1pro3_average_rawmmeas(p,o,r,n) vect_set(o,1.,128)
#define i1pro3_comp_simple_emis_black(...) ((void)0)
#define vect_sub(...) ((void)0)
#define i1pro3_vect_lin(...) ((void)0)
#define vect_max(...) 1.
#define i1pro3_comp_emis_black(...) ((void)0)
#define i1pro3_multimeas_sub_black(...) ((void)0)
#define i1pro3_multimeas_check_sat(...) 0
#define i1pro3_multimeas_lin(...) ((void)0)
#define i1pro3_normalize_rawmmeas(...) ((void)0)
#define del_zebix_list(...) zebra_free(__VA_ARGS__)
#define i1pro3_absraw_to_abswav(...) convert(__VA_ARGS__)
#define i1pro3_straylight(...) ((void)0)
#define i1pro3_multimeas_check_wav_consistency(...) 0
#define i1pro3_average_wavmmeas(...) average(__VA_ARGS__)
#define vect_mul(...) ((void)0)
#define i1pro3_extract_patches_flash(...) (unexpected(),I1PRO3_INTERNAL_ERROR)
@@VECTORS@@
@@RECOGNIZER@@
static i1pro3_code locate(i1pro3 *p,i1pro3_patch *patches,int count,
                          double **raw,int n,int **positions) {
    locate_calls++;
    for(int i=0;i<n;i++) for(int j=0;j<128;j++) nonfinite_proxy+=!isfinite(raw[i][j]);
    locate_code=i1pro3_locate_patches(p,patches,count,raw,n,positions);
    if(locate_code==I1PRO3_OK) memcpy(windows,patches,sizeof(windows));
    return locate_code;
}
#define i1pro3_locate_patches(...) locate(__VA_ARGS__)
@@SCAN@@
/* Isolate the actual source predicate as well as exercising it in the full
 * recognizer above; this reaches exact-zero/subnormal boundaries deterministically. */
static int leader_check(double lead_avg,double trail_avg) {
@@LEADER_CHECK@@
        return I1PRO3_RD_LEADTRAILINCONS;
    }
    return I1PRO3_OK;
}
int main(int argc,char **argv) {
    if(argc!=2) return 2;scenario=argv[1];
    if(!strncmp(scenario,"ratio_",6)) {
        double lead=1.,trail=1.;
        if(is("ratio_negative_equal")) lead=trail=-1.;
        if(is("ratio_mismatch")) trail=.55;
        if(is("ratio_both_zero")) lead=trail=0.;
        if(is("ratio_one_zero")) trail=0.;
        if(is("ratio_nan")) lead=NAN;
        if(is("ratio_inf")) lead=trail=INFINITY;
        if(is("ratio_finite_overflow")) trail=DBL_MIN/8.;
        if(is("ratio_finite_tiny")) lead=trail=DBL_MIN/2.;
        printf("{\"code\":%d}\n",leader_check(lead,trail)); return 0;
    }
    i1pro3 instrument={0}; i1pro3imp impl={0}; a1log log={0};
    instrument.m=&impl;instrument.log=&log;impl.nraw=128;impl.nwav[0]=1;
    impl.mmode=i1p3_trans_scan;impl.scan_toll_ratio=1.;impl.min_int_time=.001;
    impl.sens_sat=100000;impl.sens_target=1;
    i1pro3_state *state=&impl.ms[impl.mmode];
    state->trans=strncmp(scenario,"emissive_",9)!=0;state->emiss=!state->trans;
    state->scan=1;state->inttime=.01;state->targoscale=1.;
    double reference[128], output_values[3]={-9876,-9876,-9876},duration=-9876;
    double *outputs[]={&output_values[0],&output_values[1],&output_values[2]};
    for(int j=0;j<128;j++) reference[j]=10000.;
    if(is("zero_over_zero") || is("positive_over_zero") || is("no_zebra_zero")) reference[0]=0.;
    if(is("negative_denominator") || is("negative_mismatch")) reference[0]=-10000.;
    if(is("nan_denominator")) reference[0]=NAN;
    if(is("inf_denominator")) reference[0]=INFINITY;
    if(is("negative_inf_denominator")) reference[0]=-INFINITY;
    if(is("negative_zero")) reference[0]=-0.;
    if(is("overflow")) reference[0]=DBL_MIN;
    if(is("tiny_finite")) reference[0]=DBL_MIN;
    if(is("tiny_negative")) reference[0]=-DBL_MIN;
    if(is("leader_overflow")) reference[0]=1e-304;
    if(is("zebra_slot_overflow")) reference[0]=1e-304;
    if(is("band50_zero")) reference[50]=0.;
    if(is("all_zero")) for(int j=0;j<128;j++) reference[j]=0.;
    state->raw_white=reference;
    int code=i1pro3_scan_emis_meas(&instrument,&duration,outputs,3);
    printf("{\"code\":%d,\"locate_code\":%d,\"locate_calls\":%d,\"nonfinite_proxy\":%d,"
           "\"live_matrices\":%d,\"live_vectors\":%d,\"measures\":%d,\"raw_frees\":%d,"
           "\"zebra_frees\":%d,\"converted\":%d,\"averaged\":%d,\"curve_constructors\":%d,"
           "\"output_untouched\":%d,\"duration_untouched\":%d,\"windows\":[[%d,%d],[%d,%d],[%d,%d]]}\n",
           code,locate_code,locate_calls,nonfinite_proxy,matrices,vectors,measures,raw_frees,
           zebra_frees,converted,averaged,curve_constructors,output_values[0]==-9876 &&
           output_values[1]==-9876 && output_values[2]==-9876,duration==-9876,
           windows[0].ss,windows[0].no,windows[1].ss,windows[1].no,windows[2].ss,windows[2].no);
    if(matrices || vectors) return 3;
    return 0;
}
'''


def _probe_source(source: str, numlib: str) -> str:
    constants = [re.search(r"^#define " + name + r"\b[^\n]*", source, re.M).group()
                 for name in ("MX_NRAW", "MIN_SAMPLES", "POL_MIN_SAMPLES")]
    vectors = []
    for name in VECTOR_FUNCTIONS:
        match = re.search(r"void " + name + r"\([^)]*\)\s*\{", numlib)
        if match is None:
            raise ValueError(f"Missing upstream vector function {name}")
        vectors.append(_function(numlib, match.group()))
    comparison = source.index("lead_avg/trail_avg < 0.7")
    start = source.rfind("\tif (", 0, comparison)
    leader_check = source[start:source.index(" {", comparison) + 2]
    return _HARNESS.replace("@@CONSTANTS@@", "\n".join(constants)).replace(
        "@@VECTORS@@", "\n".join(vectors)
    ).replace(
        "@@RECOGNIZER@@", _recognizer(source)
    ).replace("@@SCAN@@", _function(source, SCAN_SIGNATURE)).replace(
        "@@LEADER_CHECK@@", leader_check
    )


def _validate(results: dict) -> None:
    old, new = results["baseline"], results["patched"]
    unchanged = {"finite", "mismatch", "negative_denominator", "negative_mismatch",
                 "emissive_finite", "emissive_mismatch", "no_zebra_finite", "no_zebra_mismatch",
                 "tiny_finite", "tiny_negative", "zero_signal", "zero_leader", "zero_leaders",
                 "tiny_trailer", "zebra_pair_finite"}
    for case in CASES:
        before, after = old[case], new[case]
        if not (after["live_matrices"] == after["live_vectors"] == 0):
            raise AssertionError(case)
        if not (after["measures"] == after["raw_frees"] == 3):
            raise AssertionError(case)
        if not (after["zebra_frees"] == int(not case.startswith("no_zebra_"))):
            raise AssertionError(case)
        if not (after["duration_untouched"] == 1):
            raise AssertionError(case)
        if case in unchanged:
            if not (before == after):
                raise AssertionError(case)
            expected = (0x3b if case == "zero_signal" else 0x3c if case == "tiny_trailer" else
                        0x42 if "mismatch" in case or case in {"zero_leader", "zero_leaders"}
                        else 0)
            if not (after["code"] == expected):
                raise AssertionError(case)
            if after["code"] == 0:
                if not (after["windows"] == [[114, 54], [214, 54], [314, 54]]):
                    raise AssertionError(case)
                if not (after["converted"] == 1 and after["averaged"] == 3):
                    raise AssertionError(case)
        elif case == "leader_overflow":
            if not (before["code"] == 0 and after["code"] == after["locate_code"] == 0x42):
                raise AssertionError('Proof verdict failed')
            if not (after["locate_calls"] == 1 and after["nonfinite_proxy"] == 0):
                raise AssertionError('Proof verdict failed')
            if not (after["converted"] == after["averaged"] == 0):
                raise AssertionError('Proof verdict failed')
            if not (after["output_untouched"] == 1):
                raise AssertionError('Proof verdict failed')
        else:
            if not (after["code"] == 0x39 and after["locate_calls"] == 0):
                raise AssertionError(case)
            if not (after["converted"] == after["averaged"] == 0):
                raise AssertionError(case)
            if not (after["output_untouched"] == 1):
                raise AssertionError(case)
    for case in ("zero_over_zero", "positive_over_zero", "band50_zero"):
        if not (old[case]["code"] == 0 and old[case]["nonfinite_proxy"] == 500):
            raise AssertionError(case)
    if not (old["all_zero"]["code"] == 0x3b):
        raise AssertionError('Proof verdict failed')
    if not (old["zebra_slot_overflow"]["code"] == 0):
        raise AssertionError('Proof verdict failed')
    if not (old["zebra_slot_overflow"]["nonfinite_proxy"] == 300):
        raise AssertionError('Proof verdict failed')
    if not (old["zebra_slot_overflow"]["averaged"] == 3):
        raise AssertionError('Proof verdict failed')
    for case in RATIO_CASES:
        expected = (0 if case in {"ratio_equal", "ratio_negative_equal", "ratio_finite_tiny"}
                    else 0x42)
        if not (new[case]["code"] == expected):
            raise AssertionError(case)
        if case in {"ratio_both_zero", "ratio_nan", "ratio_inf"}:
            if not (old[case]["code"] == 0):
                raise AssertionError(case)
        else:
            if not (old[case] == new[case]):
                raise AssertionError(case)


@production_proof
def prove(archive: Path, output: Path, *, source: Path | None = None,
          compiler: str = "cc", sanitize: bool = False) -> dict:
    """Compare the exact composed source using inert acquisition/conversion hooks."""
    production_root = source
    production_numsup = production_text(production_root, "numlib/numsup.c")
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        original = zipped.read(PREFIX + SOURCE_PATH).decode()
        numlib = zipped.read(PREFIX + "numlib/numsup.c").decode()
        if digest(original.encode()) != SOURCE_SHA256 or digest(numlib.encode()) != NUMLIB_SHA256:
            raise ValueError("Pinned source identities changed")
        baseline = before_source("scan_normalization", SOURCE_PATH, archive)
        patched = production_text(source, SOURCE_PATH)
        output.mkdir(parents=True, exist_ok=False)
        headers = output / "headers"
        for entry in zipped.infolist():
            if entry.filename.startswith(PREFIX) and entry.filename.endswith(".h"):
                relative = Path(entry.filename.removeprefix(PREFIX))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Unexpected upstream header path")
                target = headers / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zipped.read(entry))
        (headers / "sa_config.h").write_bytes(zipped.read(PREFIX + "h/aconfig.h"))
        (output / "Argyll-License2.txt").write_bytes(zipped.read(PREFIX + "License2.txt"))
    for label, source in (("original", original), ("baseline", baseline), ("patched", patched)):
        (output / f"i1pro3_imp.{label}.c").write_text(source, encoding="utf-8", newline="\n")
    diff = "".join(difflib.unified_diff(
        baseline.splitlines(keepends=True), patched.splitlines(keepends=True),
        fromfile=f"flash-patched/{SOURCE_PATH}", tofile=f"scan-guarded/{SOURCE_PATH}",
    ))
    (output / "scan-normalization.patch").write_text(diff, encoding="utf-8", newline="\n")
    (output / "UPSTREAM.txt").write_text(
        "ArgyllCMS 3.5.0, Graeme W. Gill and credited contributors.\n"
        "https://www.argyllcms.com/Argyll_V3.5.0_src.zip\n"
        "Extracted functions remain GPL-2.0-or-later; headers retain their notices.\n"
        "Local change refuses zero/nonfinite raw recognition divisors, nonfinite quotients,\n"
        "nonfinite final recognition proxies after optional position resampling,\n"
        "and undefined/nonfinite leader/trailer agreement ratios.\n"
        "Finite negative reference channels and ordinary recognition remain unchanged.\n"
        "Only extracted code with inert acquisition, dark, wavelength and conversion hooks runs.\n",
        encoding="utf-8", newline="\n",
    )
    report = {
        "status": "incomplete", "archive_sha256": ARCHIVE_SHA256,
        "upstream_path": SOURCE_PATH, "original_sha256": digest(original.encode()),
        "baseline_sha256": digest(baseline.encode()), "patched_sha256": digest(patched.encode()),
        "numlib_sha256": NUMLIB_SHA256, "patch_sha256": digest(diff.encode()),
        "proof_source_sha256": digest(Path(__file__).read_bytes()),
        "original_recognizer_sha256": digest(_recognizer(original).encode()),
        "patched_recognizer_sha256": digest(_recognizer(patched).encode()),
        "platform": platform.platform(), "hardware_tested": False,
        "instrument_runtime_linked": False, "commands": [], "results": {},
        "native_refusals": {"invalid_normalization": "I1PRO3_RD_READINCONS (0x39)",
                            "invalid_agreement": "I1PRO3_RD_LEADTRAILINCONS (0x42)"},
        "generated_products_sha256": {}, "sanitize": sanitize,
        "scope": "source_recognition_normalization_windows_and_buffer_ownership_only",
        "substitutions": "Acquisition supplies a synthetic 500x128 strip, or 1000 distinct raw "
        "rows mapped two per each of 500 position slots. Dark/linearization/"
        "integration normalization and wavelength conversion/consistency are inert. Allocation "
        "and cleanup hooks count ownership. Extracted numsup vector arithmetic and full upstream "
        "patch recognition run. No-zebra three-patch cases create then delete an unused fitting "
        "object; fitting/interpolation paths are not exercised. Output17 is a conversion sentinel.",
    }
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    windows = platform.system() == "Windows"
    report["production_numsup_sha256"] = digest(production_numsup.encode())
    baseline_headers = headers
    current_headers = output / "production-support"
    report["production_support_sha256"] = production_support(
        production_root, baseline_headers, current_headers)
    try:
        for label, source in (("baseline", baseline), ("patched", patched)):
            headers = current_headers if label == "patched" else baseline_headers
            include = [arg for path in [headers, *[headers / d for d in
                       ("h", "numlib", "rspl", "cgats", "xicc", "spectro")]]
                       for arg in ("-I", str(path))]
            unit = output / f"{label}.c"
            unit.write_text(
                _probe_source(source, production_numsup if label == "patched" else numlib),
                encoding="utf-8", newline="\n")
            executable = output / (label + ".exe" if windows else label)
            argv = [cc, "-std=gnu99", "-O1", "-g", "-DSALONEINSTLIB",
                    *(["-DNT", "-DEN_LIBUSB0"] if windows else ["-DUNIX"]),
                    *sanitizers.flags(sanitize),
                    *include, str(unit), "-o", str(executable), "-lm"]
            run = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            (output / f"{label}-compile.log").write_text(
                run.stdout + run.stderr, encoding="utf-8", newline="\n")
            report["commands"].append({"argv": argv, "returncode": run.returncode})
            run.check_returncode()
            report["generated_products_sha256"].update({
                unit.name: digest(unit.read_bytes()),
                executable.name: digest(executable.read_bytes()),
            })
            report["results"][label] = {}
            for case in (*CASES, *RATIO_CASES):
                run = subprocess.run([str(executable), case], capture_output=True,
                                     text=True, timeout=10)
                (output / f"{label}-{case}.log").write_text(
                    run.stdout + run.stderr, encoding="utf-8", newline="\n")
                sanitizers.refuse(run.stderr, f"{label}/{case}")
                run.check_returncode()
                if run.stderr.strip():
                    raise RuntimeError(f"Unexpected inert-probe diagnostics for {label}/{case}")
                report["results"][label][case] = json.loads(run.stdout)
        _validate(report["results"])
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n",
                                            encoding="utf-8", newline="\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args()
    report = prove(args.archive.resolve(), args.output.resolve(), sanitize=args.sanitize)
    print(json.dumps({"status": report["status"],
                      "cases_per_variant": len(CASES) + len(RATIO_CASES),
                      "hardware_tested": False}))


if __name__ == "__main__":
    main()
