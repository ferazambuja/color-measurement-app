"""Authenticated flash-capture corrections and inert source regressions.

Extracted Argyll functions remain Graeme W. Gill's GPL-2.0-or-later work.
Only inert source proofs execute: no instrument runtime, USB, or calibration cache.
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

REPAIR_ID = 'recipe:flash-capture'

ARCHIVE_SHA256 = "f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f"
SOURCE_PATH = "spectro/i1pro3_imp.c"
SOURCE_SHA256 = "42c684afa3bbb15dbbb880820c55a17f32d79ceaa7f8cbcf07cddaf3f2073e32"
PREFIX = "Argyll_V3.5.0/"
SHARED_FLASH_SIGNATURES = {
    "spectro/i1pro_imp.c": "i1pro_code i1pro_extract_patches_flash(\n",
    "spectro/munki_imp.c": "munki_code munki_extract_patches_flash(\n",
}
SHARED_SOURCE_SHA256 = {
    "i1pro": "8b8516cdd89e21de7ded3d58e1b03663c9908a343c82d393fedf9d1960ee6de6",
    "munki": "1086372fd9cfbd4229b0affc04337a10ce81bf1e139faa46df4cb5030d984ebe",
}
# Original i1Pro/i1Pro2 source after the existing calibration, scan and
# reference-factor repairs, before this module's endpoint refusal. Keeping the
# import direction measurement_patch -> flash_capture avoids a composition cycle.
I1PRO_MEASUREMENT_SHA256 = "3d07ae9dcfc9dde6495b339d53a8564d22f587878c8ab19a9e79a4970b227bfa"
I1PRO_ENDPOINT_SHA256 = "abbb1689e0ade8dd0803481c56a9ae0869780589dc02e778595e6f247f3eaaca"
I1PRO_COMPOSED_ENDPOINT_SHA256 = "190ded3b9cf0ae828a56566072cffa35297f2939196d324cc8c3bdff27c40c0e"
I1PRO_SATURATION_SIGNATURES = (
    "i1pro_code i1pro_read_patches_2(\n",
    "int i1pro_average_multimeas(\n",
    "double i1pro_raw_to_absraw(\n",
    "i1pro_code i1pro_extract_patches_flash(\n",
    "void i1pro_absraw_to_abswav(\n",
    "void i1pro_scale_specrd(\n",
)
FUNCTIONS = (
    "i1pro3_code i1pro3_extract_patches_flash(\n",
    "int i1pro3_multimeas_check_sat(\n",
    "void i1pro3_free_raw(\n",
    "i1pro3_code i1pro3_scan_emis_meas(\n",
    "void i1pro3_absraw_to_abswav(\n",
)
CASES = (
    "interior", "two_pulses", "last_only", "crosses_end", "penultimate_tail",
    "penultimate_only", "third_last", "no_flash", "background_short", "background_minimum",
    "empty", "one", "two", "sat10", "sat11", "sat11_long", "sat128", "sat_last",
    "sat10_zebra", "end_last_zebra", "sat11_zebra", "nonflash11", "nonflash128", "nonflash_all",
    "converter_with_aux", "converter_without_aux", "threshold_channel0",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _function(text: str, signature: str) -> str:
    if text.count(signature) != 1:
        raise ValueError(f"Missing or ambiguous pinned function: {signature}")
    start = text.index(signature)
    if signature == I1PRO_SATURATION_SIGNATURES[0]:
        # This pinned function has a column-zero inner brace, and unmatched
        # braces inside disabled APPEND_MEAN_EMMIS_VAL debug code. Preserve the
        # complete source, including that disabled block, using its next marker.
        marker = "/* Take a measurement reading using the current mode, part 2a */"
        if text.count(marker) != 1:
            raise ValueError("Pinned i1Pro read-patches end marker changed")
        body = text[start:text.index(marker, start)].rstrip()
        if not body.endswith("\n\treturn ev;\n}"):
            raise ValueError("Pinned i1Pro read-patches ending changed")
        return body + "\n"
    end = text.find("\n}\n", start)
    if end < 0:
        raise ValueError("Missing pinned function end")
    return text[start:end + 3]


SHARED_ENDPOINT_CASES = (
    "interior", "last", "crosses_end", "penultimate", "tail", "third_last",
    "two", "early", "minimum", "none", "signed_interior", "threshold_channel0",
)
_ENDPOINT_SUPPORT = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#define a1logd(...) ((void)0)
static int vectors;
static double *dvectorz(int low, int high) {
    double *v=calloc((size_t)(high-low+1),sizeof(double));
    if(!v) abort();
    vectors++; return v-low;
}
static void free_dvector(double *v, int low, int high) {
    (void)high; vectors--; free(v+low);
}
'''
_ENDPOINT_MAIN = r'''
int main(int argc, char **argv) {
    if(argc!=2) return 2;
    const char *name=argv[1];
    DEVICE meter={0}; IMP impl={0}; meter.m=&impl; impl.nraw=2;
    double values[20][2], *rows[20], output[2]={-9876,-9876}, duration=-9876;
    int count=20, p1=12, p2=-1, flags=99;
    if(!strcmp(name,"last")) p1=19;
    if(!strcmp(name,"crosses_end")) {p1=18;p2=19;}
    if(!strcmp(name,"penultimate") || !strcmp(name,"tail")) p1=18;
    if(!strcmp(name,"third_last")) p1=17;
    if(!strcmp(name,"two")) {p1=10;p2=14;}
    if(!strcmp(name,"early")) p1=8;
    if(!strcmp(name,"minimum")) p1=9;
    if(!strcmp(name,"none")) p1=-1;
    for(int i=0;i<count;i++) {
        rows[i]=values[i];
        for(int j=0;j<2;j++) values[i][j]=p1<0 ? 0 : 1;
    }
    if(p1>=0) values[p1][0]=values[p1][1]=101;
    if(p2>=0) values[p2][0]=values[p2][1]=101;
    if(!strcmp(name,"tail")) values[19][0]=values[19][1]=11;
    /* Channel 0 detects an interior pulse; channel 1 retains a finite signed
     * background-subtracted contribution. Neither channel may be clamped. */
    if(!strcmp(name,"signed_interior")) {
        for(int i=0;i<count;i++) values[i][1]=-5;
        values[p1][1]=-15;
    }
    /* Mean 6 and maximum 78 give an exact threshold of 24. Only channel 0
     * reaches it at the excluded last row. */
    if(!strcmp(name,"threshold_channel0")) {
        values[p1][0]=values[p1][1]=78;
        values[19][0]=24; values[19][1]=1;
    }
    int code=CALL;
    printf("{\"code\":%d,\"ok_code\":%d,\"inconsistent_code\":%d,"
           "\"no_flash_code\":%d,\"background_code\":%d,\"duration\":%.17g,"
           "\"first\":%.17g,\"second\":%.17g,\"flags\":%d,\"live_vectors\":%d}\n",
           code,OK_CODE,INCONSISTENT_CODE,NO_FLASH_CODE,BACKGROUND_CODE,
           duration,output[0],output[1],flags,vectors);
    return vectors != 0;
}
'''


def _shared_probe_source(source: str, header: str, family: str) -> str:
    constants = []
    for suffix in ("_OK", "_RD_READINCONS", "_RD_NOFLASHES", "_RD_NOAMBB4FLASHES"):
        matches = re.findall(r"^#define\s+" + family.upper() + suffix + r"\s+[^\n]+",
                             header, re.M)
        if len(matches) != 1:
            raise ValueError("Pinned shared flash return code changed")
        constants.append(matches[0])
    main = _ENDPOINT_MAIN.replace("DEVICE", family).replace("IMP", family + "imp")
    main = main.replace("CALL", family + "_extract_patches_flash("
                        "&meter,&flags,&duration,output,rows,count,.01)")
    for marker, suffix in (("OK_CODE", "_OK"), ("INCONSISTENT_CODE", "_RD_READINCONS"),
                           ("NO_FLASH_CODE", "_RD_NOFLASHES"),
                           ("BACKGROUND_CODE", "_RD_NOAMBB4FLASHES")):
        main = main.replace(marker, family.upper() + suffix)
    return (source[:source.index("#include")] + _ENDPOINT_SUPPORT
            + "\n".join(constants) + "\n"
            + f"typedef int {family}_code;\n"
            + f"typedef struct {{ int nraw; }} {family}imp;\n"
            + f"typedef struct {{ void *m; }} {family};\n"
            + _function(source, SHARED_FLASH_SIGNATURES[f"spectro/{family}_imp.c"]) + main)


def _validate_shared_endpoints(results: dict) -> None:
    changed = {"last", "crosses_end", "penultimate", "tail", "threshold_channel0"}
    before, after = results["baseline"], results["patched"]
    if not (set(before) == set(after) == set(SHARED_ENDPOINT_CASES)):
        raise AssertionError
    for case in SHARED_ENDPOINT_CASES:
        old, new = before[case], after[case]
        if not (old["live_vectors"] == new["live_vectors"] == 0):
            raise AssertionError
        if case in changed:
            if not (old["code"] == old["ok_code"]):
                raise AssertionError
            if not (new["code"] == new["inconsistent_code"]):
                raise AssertionError(f"{case}: patched endpoint not refused as inconsistent")
            if not (new["duration"] == new["first"] == new["second"] == -9876):
                raise AssertionError
            if not (new["flags"] == 99):
                raise AssertionError
        else:
            if not (old == new):
                raise AssertionError(case)
    if not (before["last"]["first"] == before["last"]["second"] == 0):
        raise AssertionError
    if not (before["last"]["duration"] == .01):
        raise AssertionError
    if not (before["crosses_end"]["first"] == before["tail"]["first"] == 1):
        raise AssertionError
    for case, expected in (("interior", 1), ("third_last", 1), ("two", 2), ("minimum", 1)):
        if not (after[case]["code"] == after[case]["ok_code"]):
            raise AssertionError
        if not (after[case]["first"] == after[case]["second"] == expected):
            raise AssertionError
    if not (after["early"]["code"] == after["early"]["background_code"]):
        raise AssertionError
    if not (after["none"]["code"] == after["none"]["no_flash_code"]):
        raise AssertionError
    if not (after["signed_interior"]["code"] == after["signed_interior"]["ok_code"]):
        raise AssertionError
    if not (after["signed_interior"]["first"] == 1):
        raise AssertionError
    if not (abs(after["signed_interior"]["second"] + .1) < 1e-12):
        raise AssertionError


@production_proof
def prove_shared_endpoints(archive: Path, output: Path, *, source: Path | None = None,
                           compiler: str = "cc") -> dict:
    """Prove sibling endpoint refusal using exact extractors, inert raw input only.

    Return function digests so the builder can bind these functions to its full
    composed sources without importing the legacy composition into this module.
    """
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        originals, patched, headers = {}, {}, {}
        for family in SHARED_SOURCE_SHA256:
            path = f"spectro/{family}_imp.c"
            originals[path] = before_source("flash_endpoints", path, archive)
            patched[path] = production_text(source, path)
            headers[family] = zipped.read(PREFIX + f"spectro/{family}_imp.h")
        licence = zipped.read(PREFIX + "License2.txt")
    output.mkdir(parents=True, exist_ok=False)
    (output / "Argyll-License2.txt").write_bytes(licence)
    report = {
        "status": "incomplete", "archive_sha256": ARCHIVE_SHA256,
        "hardware_tested": False, "instrument_runtime_linked": False,
        "proof_source_sha256": digest(Path(__file__).read_bytes()),
        "platform": platform.platform(), "commands": [], "results": {},
        "original_sha256": {path: digest(text.encode()) for path, text in originals.items()},
        "patched_sha256": {path: digest(text.encode()) for path, text in patched.items()},
        "original_function_sha256": {
            path: digest(_function(originals[path], sig).encode())
            for path, sig in SHARED_FLASH_SIGNATURES.items()
        },
        "patched_function_sha256": {
            path: digest(_function(patched[path], sig).encode())
            for path, sig in SHARED_FLASH_SIGNATURES.items()
        },
        "header_sha256": {family: digest(data) for family, data in headers.items()},
        "scope": "exact_flash_extractors_with_synthetic_raw_input_and_inert_allocations",
        "limitations": [
            "No raw acquisition, calibration, transport, conversion or full driver runtime.",
            "Terminal-window refusal does not establish completeness of accepted physical pulses.",
            "This proof does not establish saturation safety of the earlier raw processing path.",
        ],
    }
    try:
        for family in SHARED_SOURCE_SHA256:
            path = f"spectro/{family}_imp.c"
            (output / f"{family}_imp.h").write_bytes(headers[family])
            (output / f"{family}_imp.original.c").write_text(originals[path])
            (output / f"{family}_imp.patched.c").write_text(patched[path])
            patch = "".join(difflib.unified_diff(
                originals[path].splitlines(True), patched[path].splitlines(True),
                fromfile="a/" + path, tofile="b/" + path,
            ))
            (output / f"{family}-flash-boundary.patch").write_text(patch)
            report["results"][family] = {}
            for label, text in (("baseline", originals[path]), ("patched", patched[path])):
                unit = output / f"{family}-{label}.c"
                unit.write_text(_shared_probe_source(text, headers[family].decode(), family))
                binary = unit.with_suffix(".exe" if platform.system() == "Windows" else "")
                command = [cc, "-std=c99", "-Wall", "-Wextra", "-Werror",
                           "-Wno-unused-variable", "-Wno-unused-but-set-variable",
                           str(unit), "-o", str(binary)]
                build = subprocess.run(command, capture_output=True, text=True, timeout=60)
                (output / f"{family}-{label}-compile.log").write_text(build.stdout + build.stderr)
                report["commands"].append({"argv": command, "returncode": build.returncode})
                build.check_returncode()
                report["results"][family][label] = {}
                for case in SHARED_ENDPOINT_CASES:
                    run = subprocess.run([str(binary), case], capture_output=True,
                                         text=True, timeout=10)
                    (output / f"{family}-{label}-{case}.log").write_text(run.stdout + run.stderr)
                    run.check_returncode()
                    if run.stderr:
                        raise AssertionError(f"Unexpected shared flash diagnostic: {run.stderr}")
                    report["results"][family][label][case] = json.loads(run.stdout)
            _validate_shared_endpoints(report["results"][family])
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


_HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
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
struct _usb_cancelt { int inert; };
#include "i1pro3_imp.h"
static int live, frees, vectors, measures, linearized, normalized, zebra_frees;
static int capture_rows, pulse_channels, pulse_last, all_saturated, with_zebra;
static int packed_conversion, packed_aux_copy_attempts;
static int scale_overflow;
static int allocated[5], nr[5], lo[5], hi[5];
static double storage[5][64][140], *rows[5][64];
static int zebra_value, *zebra_rows[] = {&zebra_value};
static void unexpected(void) { fputs("Unexpected unsubstituted path\n", stderr); abort(); }
static double **matrix(int rlo, int rhi, int clo, int chi) {
    int slot;
    for (slot=0; slot<5 && allocated[slot]; slot++) {}
    if (slot==5 || rlo!=0 || rhi>=64 || clo < -9 || chi>=128) unexpected();
    allocated[slot]=1; nr[slot]=rhi+1; lo[slot]=clo; hi[slot]=chi; live++;
    for (int i=0;i<=rhi;i++) { rows[slot][i]=storage[slot][i]-clo;
        for(int j=clo;j<=chi;j++) rows[slot][i][j]=0; }
    return rows[slot];
}
static void matrix_free(double **v, int rlo, int rhi, int clo, int chi) {
    int slot;
    for(slot=0;slot<5 && v!=rows[slot];slot++) {}
    if(slot==5 || !allocated[slot] || rlo!=0 || rhi+1!=nr[slot]
       || clo!=lo[slot] || chi!=hi[slot]) unexpected();
    allocated[slot]=0; live--; frees++;
}
static double *vector_zero(int lo, int hi) {
    double *p=calloc((size_t)(hi-lo+1),sizeof(double)); if(!p) abort(); vectors++; return p-lo;
}
static void vector_free(double *v, int lo, int hi) { (void)hi; vectors--; free(v+lo); }
static int measure(i1pro3 *p, i1p3_mmode mode, double ***out, int *n,
                   double *t, int ***positions, int *npos) {
    i1pro3imp *m=p->m; (void)t;
    if(mode!=i1p3mm_em || ++measures>3) unexpected();
    *n = measures==2 ? capture_rows : 1;
    *out=matrix(0,*n-1,-1,m->nraw-1);
    for(int i=0;i<*n;i++) for(int j=0;j<m->nraw;j++)
        (*out)[i][j] = measures==2 ? ((all_saturated || i==(pulse_last?*n-1:12))
                          && j<pulse_channels ? 2000.0 : 1.0) : 0;
    if(positions && with_zebra) { *positions=zebra_rows; *npos=1; }
    return I1PRO3_OK;
}
static void average(i1pro3 *p, double *out, double **raw, int n) {
    (void)raw;(void)n; i1pro3imp *m=p->m;
    for(int j=0;j<m->nraw;j++) out[j]=1;
}
static void zebra_free(int **v,int n) {
    if(v!=zebra_rows || n!=1 || ++zebra_frees!=1) unexpected();
}
#define a1logd(...) ((void)0)
#define error(...) unexpected()
#define i1pro3_getadaptype(...) (unexpected(), I1PRO3_INTERNAL_ERROR)
#define i1pro3_getboardtemp(p,t) (*(t)=23.0,I1PRO3_OK)
#define i1pro3_recompute_wav_filters_for_temp(...) (unexpected(),I1PRO3_INTERNAL_ERROR)
#define i1pro3_comp_nummeas(...) 1
#define i1pro3_do_measure(...) measure(__VA_ARGS__)
#define i1pro3_average_rawmmeas(...) average(__VA_ARGS__)
#define i1pro3_comp_simple_emis_black(...) ((void)0)
#define i1pro3_vect_lin(...) ((void)0)
#define i1pro3_comp_emis_black(...) ((void)0)
#define i1pro3_multimeas_sub_black(...) ((void)0)
#define i1pro3_multimeas_lin(...) (++linearized)
#define i1pro3_normalize_rawmmeas(...) (++normalized)
#define i1pro3_straylight(...) ((void)0)
#define i1pro3_locate_patches(...) I1PRO3_RD_READINCONS
#define i1pro3_multimeas_check_wav_consistency(...) (unexpected(),1)
#define i1pro3_average_wavmmeas(...) unexpected()
#define del_zebix_list(...) zebra_free(__VA_ARGS__)
#define dmatrix(...) matrix(__VA_ARGS__)
#define free_dmatrix(...) matrix_free(__VA_ARGS__)
#define dvectorz(...) vector_zero(__VA_ARGS__)
#define free_dvector(...) vector_free(__VA_ARGS__)
#define vect_sub(...) ((void)0)
#define vect_max(...) 1.0
#define vect_set(...) ((void)0)
#define vect_max_elem(...) ((void)0)
#define vect_div3(...) ((void)0)
#define vect_add(...) unexpected()
#define vect_scale1(...) unexpected()
#define vect_blend(...) unexpected()
static void probe_vect_mul(double *values, const double *factors, int count) {
    if(scale_overflow) for(int i=0;i<count;i++) values[i]*=factors[i];
}
#define vect_mul(...) probe_vect_mul(__VA_ARGS__)
#undef vect_cpy
/* Observe the baseline's invalid auxiliary-copy branch without evaluating its
 * packed input[-1] expression. The separately retained ASan reproduction uses
 * the unmodified converter and real memcpy. Ordinary copies remain real. */
#define vect_cpy(out,in,n) ((packed_conversion && (n)==1) \
    ? (void)(++packed_aux_copy_attempts) : (void)memcpy(out,in,(n)*sizeof(double)))
'''
_MAIN = r'''
int main(int argc, char **argv) {
    if(argc!=2) return 2;
    const char *name=argv[1];
    i1pro3imp *m=calloc(1,sizeof(*m)); i1pro3 meter={0}; meter.m=m;
    double output[128], *out[1]={output}, duration=-9876;
    int indices[128], counts[128]; double coefficients[128];
    for(int j=0;j<128;j++) {indices[j]=j;counts[j]=1;coefficients[j]=1;}
    m->mtx[0][0].index=indices; m->mtx[0][0].nocoef=counts;
    m->mtx[0][0].coef=coefficients;
    for(int j=0;j<128;j++) output[j]=-9876;
    int code;
    if(!strncmp(name,"converter_",10)) {
        double packed[2]={2,6}, padded[3]={77,2,6}, converted[3]={-9876,0,0};
        int auxiliary=!strcmp(name,"converter_with_aux");
        double *input[]={auxiliary?padded+1:packed}, *destination[]={converted+1};
        m->nwav[0]=2; indices[0]=indices[1]=0; counts[0]=counts[1]=2;
        coefficients[0]=.25; coefficients[1]=.75;
        coefficients[2]=2; coefficients[3]=-.5;
        i1pro3_absraw_to_abswav(&meter,0,auxiliary?0:2,destination,input,1);
        if(converted[1]!=5 || converted[2]!=1 || converted[0]!=(auxiliary?77:-9876))
            unexpected();
        output[0]=converted[1];output[1]=converted[2];code=I1PRO3_OK;
    } else if(!strncmp(name,"sat",3) || !strncmp(name,"nonflash",8)
              || !strcmp(name,"scale_flash")
              || !strncmp(name,"end_",4)) {
        packed_conversion=1;
        m->nraw=m->nwav[0]=128; m->sens_sat=1000; m->min_int_time=.01;
        m->mmode=i1p3_amb_flash;
        i1pro3_state *s=&m->ms[m->mmode]; s->flash=strncmp(name,"nonflash",8)!=0;
        s->inttime=.01; s->scan=s->emiss=1;
        double scale[128];
        scale_overflow=!strcmp(name,"scale_flash");
        for(int j=0;j<128;j++) scale[j]=1e308;
        if(scale_overflow) s->cal_factor[0]=scale;
        capture_rows=strstr(name,"long")?60:20;
        pulse_channels=strstr(name,"128") || strstr(name,"last") ?128:
                       strstr(name,"10") || scale_overflow ?10:11;
        if (!strncmp(name,"end_",4)) pulse_channels=2;
        pulse_last=strstr(name,"last")!=NULL; all_saturated=strstr(name,"all")!=NULL;
        with_zebra=strstr(name,"zebra")!=NULL;
        code=i1pro3_scan_emis_meas(&meter,&duration,out,1);
    } else {
        double data[32][2],*ptrs[32]; int count=20,p1=12,p2=-1; double baseline=1;
        m->nraw=2;
        if(!strcmp(name,"two_pulses")) {p1=10;p2=14;}
        if(!strcmp(name,"last_only")) p1=19;
        if(!strcmp(name,"crosses_end")) {p1=18;p2=19;}
        if(!strncmp(name,"penultimate",11)) p1=18;
        if(!strcmp(name,"third_last")) p1=17;
        if(!strcmp(name,"no_flash")) {p1=-1;baseline=0;}
        if(!strcmp(name,"background_short")) p1=8;
        if(!strcmp(name,"background_minimum")) p1=9;
        if(!strcmp(name,"empty")) {count=0;p1=-1;}
        if(!strcmp(name,"one")) {count=1;p1=0;}
        if(!strcmp(name,"two")) {count=2;p1=1;}
        for(int i=0;i<32;i++) {ptrs[i]=data[i];for(int j=0;j<2;j++) data[i][j]=baseline;}
        if(p1>=0) data[p1][0]=data[p1][1]=101;
        if(p2>=0) data[p2][0]=data[p2][1]=101;
        if(!strcmp(name,"penultimate_tail")) data[19][0]=data[19][1]=11;
        if(!strcmp(name,"threshold_channel0")) {
            data[p1][0]=data[p1][1]=78;
            data[19][0]=24; data[19][1]=1;
        }
        code=i1pro3_extract_patches_flash(&meter,&duration,output,ptrs,count,.01);
    }
    int untouched=1;
    for(int j=0;j<128;j++) if(output[j]!=-9876) untouched=0;
    int finite_output=isfinite(output[0]) && isfinite(output[1]);
    printf("{\"case\":\"%s\",\"code\":%d,\"ok_code\":%d,\"saturation_code\":%d,"
           "\"inconsistent_code\":%d,\"no_flash_code\":%d,\"background_code\":%d,"
           "\"duration\":%.17g,\"first\":%.17g,\"second\":%.17g,\"live_buffers\":%d,"
           "\"live_vectors\":%d,\"frees\":%d,\"measures\":%d,\"linearized\":%d,"
           "\"normalized\":%d,\"zebra_frees\":%d,\"output_untouched\":%d,"
           "\"packed_aux_copy_attempts\":%d,\"finite_output\":%d}\n",
           name,code,I1PRO3_OK,
           I1PRO3_RD_SENSORSATURATED,I1PRO3_RD_READINCONS,I1PRO3_RD_NOFLASHES,
           I1PRO3_RD_NOAMBB4FLASHES,duration,isfinite(output[0])?output[0]:0.0,
           isfinite(output[1])?output[1]:0.0,live,vectors,frees,
           measures,linearized,normalized,zebra_frees,untouched,packed_aux_copy_attempts,
           finite_output);
    free(m); return 0;
}
'''


def _probe_source(text: str) -> str:
    constants = re.findall(r"^#define MX_NRAW\s+[^\n]+", text, re.M)
    if len(constants) != 1:
        raise ValueError("Pinned maximum raw count changed")
    return ("/* ArgyllCMS 3.5.0, Graeme W. Gill, GPL-2.0-or-later. */\n"
            + constants[0] + "\n" + _HARNESS
            + "\n".join(_function(text, sig) for sig in FUNCTIONS) + _MAIN)


def _validate(results: dict) -> None:
    old, new = results["baseline"], results["patched"]
    changed = {"last_only", "crosses_end", "penultimate_tail", "penultimate_only",
               "sat11", "sat11_long", "sat128", "sat_last", "sat11_zebra",
               "sat10_zebra", "end_last_zebra", "threshold_channel0"}
    for case in CASES:
        before, after = old[case], new[case]
        if not (after["live_buffers"] == after["live_vectors"] == 0):
            raise AssertionError(f"flash {case}: behavior or cleanup differs")
        if not (after["packed_aux_copy_attempts"] == 0):
            raise AssertionError(f"flash {case}: behavior or cleanup differs")
        if case not in changed:
            if not ({k: v for k, v in before.items() if k != "packed_aux_copy_attempts"} == {
                k: v for k, v in after.items() if k != "packed_aux_copy_attempts"
            }):
                raise AssertionError(f"flash {case}: preserved result differs")
        if case in {
            "last_only", "crosses_end", "penultimate_tail", "penultimate_only", "threshold_channel0"
        }:
            if not (before["code"] == before["ok_code"]):
                raise AssertionError(f"flash {case}: behavior or cleanup differs")
            if not (after["code"] == after["inconsistent_code"]):
                raise AssertionError(f"flash {case}: behavior or cleanup differs")
        if case in {"sat11", "sat11_long", "sat128", "sat_last", "sat11_zebra"}:
            if not (before["code"] == before["ok_code"]):
                raise AssertionError(f"flash {case}: behavior or cleanup differs")
            if not (after["code"] == after["saturation_code"]):
                raise AssertionError(f"flash {case}: behavior or cleanup differs")
            if not (after["frees"] == after["measures"] == 3):
                raise AssertionError(f"flash {case}: behavior or cleanup differs")
            if not (after["linearized"] == after["normalized"] == 0):
                raise AssertionError(f"flash {case}: behavior or cleanup differs")
            if not (after["zebra_frees"] == int(case == "sat11_zebra")):
                raise AssertionError(f"flash {case}: behavior or cleanup differs")
        if after["code"] != after["ok_code"]:
            if not (after["duration"] == after["first"] == after["second"] == -9876):
                raise AssertionError(f"flash {case}: behavior or cleanup differs")
            if not (after["output_untouched"] == 1):
                raise AssertionError(f"flash {case}: behavior or cleanup differs")
    if not (old["last_only"]["first"] == 0):
        raise AssertionError('flash last_only: baseline capture differs')
    if not (old["crosses_end"]["first"] == old["penultimate_tail"]["first"] == 1):
        raise AssertionError('flash crosses_end/penultimate_tail: baseline captures differ')
    if not (new["interior"]["first"] == 1 and new["two_pulses"]["first"] == 2):
        raise AssertionError('flash interior/two_pulses: capture values differ')
    if not (new["sat10"]["code"] == new["sat10"]["ok_code"]):
        raise AssertionError('flash sat10: expected successful capture')
    if not (old["sat10"]["packed_aux_copy_attempts"] == 1):
        raise AssertionError('flash sat10: baseline auxiliary copy count differs')
    for case in ("sat10_zebra", "end_last_zebra"):
        if not (old[case]["zebra_frees"] == 0 and new[case]["zebra_frees"] == 1):
            raise AssertionError(f"flash {case}: behavior or cleanup differs")
        if not (old[case]["code"] == old[case]["ok_code"]):
            raise AssertionError(f"flash {case}: behavior or cleanup differs")
        expected = new[case]["ok_code"] if case == "sat10_zebra" else new[case]["inconsistent_code"]
        if not (new[case]["code"] == expected):
            raise AssertionError(f"flash {case}: behavior or cleanup differs")
    if not (new["sat10_zebra"]["first"] == old["sat10_zebra"]["first"]):
        raise AssertionError('flash sat10_zebra: capture value differs')
    if not (new["sat10_zebra"]["duration"] == old["sat10_zebra"]["duration"]):
        raise AssertionError('flash sat10_zebra: capture duration differs')
    for case in ("converter_with_aux", "converter_without_aux"):
        if not (new[case]["first"] == 5 and new[case]["second"] == 1):
            raise AssertionError(f"flash {case}: converter result differs")
    if not (new["nonflash11"]["linearized"] == new["nonflash128"]["linearized"] == 1):
        raise AssertionError('flash nonflash11/nonflash128: linearization differs')
    if not (new["nonflash_all"]["code"] == new["nonflash_all"]["saturation_code"]):
        raise AssertionError('flash nonflash_all: expected saturation refusal')


@production_proof
def prove(archive: Path, output: Path, *, source: Path | None = None,
          compiler: str = "cc") -> dict:
    """Verify the actual composed source against existing nonadaptive-patched source."""
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        original = zipped.read(PREFIX + SOURCE_PATH).decode()
        baseline = before_source("flash_capture", SOURCE_PATH, archive)
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
        fromfile=f"nonadaptive-patched/{SOURCE_PATH}",
        tofile=f"flash-refusal-patched/{SOURCE_PATH}",
    ))
    (output / "flash-capture.patch").write_text(diff, encoding="utf-8", newline="\n")
    (output / "UPSTREAM.txt").write_text(
        "ArgyllCMS 3.5.0, Graeme W. Gill and credited contributors.\n"
        "https://www.argyllcms.com/Argyll_V3.5.0_src.zip\n"
        "Extracted functions remain GPL-2.0-or-later; headers retain their notices.\n"
        "Local changes refuse terminal flash windows and per-row flash saturation,\n"
        "suppress auxiliary copying from the packed flash average, and release flash positions.\n"
        "No complete physical pulse, numerical calibration, or hardware validation is claimed.\n"
        "Only extracted functions with inert acquisition and conversion hooks execute.\n",
        encoding="utf-8", newline="\n",
    )
    report = {"status": "incomplete", "archive_sha256": ARCHIVE_SHA256,
              "upstream_path": SOURCE_PATH, "original_sha256": digest(original.encode()),
              "baseline_sha256": digest(baseline.encode()),
              "patched_sha256": digest(patched.encode()),
              "patch_sha256": digest(diff.encode()), "proof_source_sha256": digest(
                  Path(__file__).read_bytes()), "platform": platform.platform(),
              "nonadaptive_proof_source_sha256": digest(
                  Path(__file__).with_name("nonadaptive_calibration.py").read_bytes()),
              "functions": list(FUNCTIONS), "hardware_tested": False,
              "instrument_runtime_linked": False, "commands": [], "results": {},
              "generated_products_sha256": {},
              "scope": "source_control_flow_matrix_arithmetic_and_inert_buffer_ownership_only",
              "auxiliary_copy_hook": "Baseline packed auxiliary-copy attempts are counted without "
              "evaluating nonexistent input[-1]; all matrix arithmetic uses the actual converter. "
              "Direct padded/packed converter cases use real copies where applicable. "
              "Acquisition, physical calibration and straylight processing remain substituted."}
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    windows = platform.system() == "Windows"
    include = [arg for path in [headers, *[headers / d for d in
               ("h", "numlib", "rspl", "cgats", "xicc", "spectro")]] for arg in ("-I", str(path))]
    try:
        for label, source in (("baseline", baseline), ("patched", patched)):
            unit = output / f"{label}.c"
            unit.write_text(_probe_source(source), encoding="utf-8", newline="\n")
            executable = output / (label + ".exe" if windows else label)
            argv = [cc, "-std=gnu99" if windows else "-std=c99", "-DSALONEINSTLIB",
                    *(["-DNT", "-DEN_LIBUSB0"] if windows else ["-DUNIX"]),
                    *include, str(unit), "-o", str(executable)]
            run = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            (output / f"{label}-compile.log").write_text(
                run.stdout + run.stderr, encoding="utf-8", newline="\n")
            report["commands"].append({"argv": argv, "returncode": run.returncode})
            run.check_returncode()
            report["generated_products_sha256"].update({unit.name: digest(unit.read_bytes()),
                                                        executable.name: digest(
                                                            executable.read_bytes())})
            report["results"][label] = {}
            for case in CASES:
                run = subprocess.run([str(executable), case], capture_output=True,
                                     text=True, timeout=10)
                (output / f"{label}-{case}.log").write_text(
                    run.stdout + run.stderr, encoding="utf-8", newline="\n")
                run.check_returncode()
                report["results"][label][case] = json.loads(run.stdout)
        _validate(report["results"])
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n",
                                            encoding="utf-8", newline="\n")
    return report


I1PRO_SATURATION_CASES = (
    "below", "equal", "over", "one_over", "negative", "near_zero", "zero", "signed_zero_dark",
    "gain_below", "gain_equal", "gain_over", "gain_negative", "long_integration",
    "legacy_edge0", "legacy_edge127", "revE_edge0", "revE_edge127", "revE_over",
    "revE_equal", "disabled_threshold", "wrong_count", "spot_equal", "spot_over", "strip",
)
_I1PRO_SATURATION_SUPPORT = _ENDPOINT_SUPPORT + r'''
#include <fenv.h>
#define a1logw(...) ((void)0)
typedef int i1pro_code;
typedef struct {
    int scan, flash, reflective;
    double *dark_data, *cal_factor[2];
} i1pro_state;
typedef struct { int *index, *nocoef; double *coef; } matrix;
typedef struct {
    int nraw, nsen, highres, mmode, nwav[2];
    double sens_dark, highgain, sens_sat0, sens_sat1;
    i1pro_state ms[1];
    matrix mtx[2][2];
    double *straylight[2][2];
} i1proimp;
typedef struct { void *m; int dtype; } i1pro;
enum { instI1Pro=1, instI1Pro2=2 };
static int matrix_allocs, matrix_frees, live_matrices, sensor_calls, strip_calls;
static double input[20][129];
static double *dvector(int low,int high) { return dvectorz(low,high); }
static double **dmatrix(int rl,int rh,int cl,int ch) {
    if(rl!=0) abort();
    double **rows=calloc((size_t)(rh+1),sizeof(*rows));
    if(!rows) abort();
    for(int i=0;i<=rh;i++) {
        double *v=calloc((size_t)(ch-cl+1),sizeof(double));
        if(!v) abort();
        rows[i]=v-cl;
    }
    matrix_allocs++;live_matrices++;return rows;
}
static void free_dmatrix(double **rows,int rl,int rh,int cl,int ch) {
    (void)rl;(void)ch;
    for(int i=0;i<=rh;i++) free(rows[i]+cl);
    free(rows);matrix_frees++;live_matrices--;
}
double i1pro_raw_to_absraw(i1pro *,double,double,int);
/* Synthetic sensor conversion retains the real gain/time scaling, with an
 * identity linearization and zero black input. It is not an ADC/USB model. */
static i1pro_code i1pro_sens_to_absraw(i1pro *p,double **out,unsigned char *buf,
                                    int count,double time,int gain,double *dark) {
    (void)buf;
    i1proimp *m=p->m;sensor_calls++;
    for(int i=0;i<count;i++) for(int j=-1;j<m->nraw;j++)
        out[i][j]=i1pro_raw_to_absraw(p,input[i][j+1],time,gain);
    *dark=m->sens_dark;
    return I1PRO_OK;
}
static void i1pro_sub_absraw(i1pro *p,int count,double time,int gain,
                             double **values,double *black) {
    (void)time;(void)gain;
    i1proimp *m=p->m;
    for(int i=0;i<count;i++) for(int j=-1;j<m->nraw;j++) values[i][j]-=black[j];
}
static i1pro_code i1pro_extract_patches_multimeas(i1pro *p,...) {
    (void)p;strip_calls++;return I1PRO_RD_NOTENOUGHPATCHES;
}
'''
_I1PRO_SATURATION_MAIN = r'''
int main(int argc,char **argv) {
    if(argc!=2)return 2;
    const char *name=argv[1];
    i1proimp impl={0};i1pro meter={&impl,instI1Pro};
    double black[129]={0},cal[2]={2,3},coeff[2]={1,1};
    int indexes[2]={2,3},counts[2]={1,1};
    double stray0[2]={1,0},stray1[2]={0,1};
    impl.nraw=128;impl.nsen=128;impl.nwav[0]=2;
    impl.sens_sat0=10;impl.sens_sat1=20;impl.highgain=4;impl.sens_dark=1;
    impl.ms[0].scan=impl.ms[0].flash=1;
    impl.ms[0].dark_data=black+1;impl.ms[0].cal_factor[0]=cal;
    impl.mtx[0][0]=(matrix){indexes,counts,coeff};
    impl.straylight[0][0]=stray0;impl.straylight[0][1]=stray1;
    int gain=!strncmp(name,"gain_",5),npatch=1;
    double time=!strcmp(name,"long_integration")?.2:.01;
    double peak=gain?19:9;
    if(strstr(name,"equal"))peak=gain?20:10;
    if(strstr(name,"over"))peak=gain?21:11;
    if(!strncmp(name,"revE",4)){meter.dtype=instI1Pro2;impl.nsen=134;}
    if(!strcmp(name,"disabled_threshold")){impl.sens_sat0=0;peak=100;}
    if(!strcmp(name,"wrong_count")){npatch=2;peak=11;}
    int scale_overflow=!strcmp(name,"scale_flash") || !strcmp(name,"scale_spot");
    if(scale_overflow)cal[0]=1e308;
    if(!strncmp(name,"spot",4) || !strcmp(name,"scale_spot")){
        impl.ms[0].scan=impl.ms[0].flash=0;
    }
    if(!strcmp(name,"strip"))impl.ms[0].flash=0;
    int zero=!strcmp(name,"zero"),signed_zero=!strcmp(name,"signed_zero_dark");
    if(zero||signed_zero)impl.sens_dark=0;
    if(!strcmp(name,"near_zero"))peak=1e-12;
    for(int i=0;i<20;i++) {
        for(int j=0;j<129;j++)input[i][j]=0;
        input[i][3]=zero||signed_zero||!strcmp(name,"near_zero")?0:1;
        input[i][4]=input[i][3];
    }
    if(!zero){input[12][3]=peak;input[12][4]=peak;}
    if(!strcmp(name,"one_over"))input[12][4]=9;
    if(!strcmp(name,"negative")||!strcmp(name,"gain_negative")||signed_zero)
        for(int i=0;i<20;i++)input[i][4]=-input[i][3];
    if(strstr(name,"edge0"))input[12][1]=11;
    if(strstr(name,"edge127"))input[12][128]=11;
    if(!strncmp(name,"spot",4))
        for(int i=0;i<20;i++)input[i][3]=input[i][4]=peak;
    /* Independent policy oracle: actual upstream spot averaging. Its
     * irrelevant consistency ratio gets a positive scale; this does not change
     * its saturation bit. Caller proof below uses the actual chosen dark value. */
    double oracle_values[20][129],*oracle_rows[20],average[129];
    for(int i=0;i<20;i++) {
        oracle_rows[i]=oracle_values[i]+1;
        for(int j=-1;j<128;j++)oracle_rows[i][j]=
            i1pro_raw_to_absraw(&meter,input[i][j+1],time,gain);
    }
    double threshold=i1pro_raw_to_absraw(&meter,gain?impl.sens_sat1:impl.sens_sat0,time,gain);
    int oracle=i1pro_average_multimeas(&meter,average+1,oracle_rows,20,NULL,NULL,threshold,1)&2;
    double saved[20][129];memcpy(saved,input,sizeof(input));
    double output[2]={-9876,-9876},*outputs[1]={output},duration=-9876;
    feclearexcept(FE_ALL_EXCEPT);
    int code=i1pro_read_patches_2(&meter,&duration,outputs,npatch,time,gain,20,NULL,0);
    int arithmetic=fetestexcept(FE_INVALID|FE_DIVBYZERO|FE_OVERFLOW);
    int finite_output=isfinite(output[0]) && isfinite(output[1]);
    printf("{\"code\":%d,\"ok\":%d,\"inconsistent\":%d,\"saturated\":%d,\"wrong_count\":%d,"
           "\"no_flash\":%d,\"strip_error\":%d,\"oracle_saturated\":%d,"
           "\"duration\":%.17g,\"first\":%.17g,\"second\":%.17g,"
           "\"matrix_allocs\":%d,\"matrix_frees\":%d,\"live_matrices\":%d,"
           "\"live_vectors\":%d,\"arithmetic_errors\":%d,\"input_unchanged\":%d,"
           "\"sensor_calls\":%d,\"strip_calls\":%d,\"finite_output\":%d}\n",
           code,I1PRO_OK,I1PRO_RD_READINCONS,I1PRO_RD_SENSORSATURATED,I1PRO_INT_WRONGPATCHES,
           I1PRO_RD_NOFLASHES,I1PRO_RD_NOTENOUGHPATCHES,oracle,duration,
           isfinite(output[0])?output[0]:0.0,isfinite(output[1])?output[1]:0.0,
           matrix_allocs,matrix_frees,live_matrices,vectors,
           arithmetic,memcmp(saved,input,sizeof(input))==0,sensor_calls,strip_calls,
           finite_output);
    return live_matrices||vectors||(arithmetic&&!scale_overflow);
}
'''


def _i1pro_saturation_probe(source: str, header: str) -> str:
    names = ("I1PRO_OK", "I1PRO_RD_READINCONS", "I1PRO_RD_SENSORSATURATED",
             "I1PRO_RD_NOFLASHES", "I1PRO_RD_NOAMBB4FLASHES", "I1PRO_INT_WRONGPATCHES",
             "I1PRO_RD_NOTENOUGHPATCHES", "PATCH_CONS_THR")
    constants = []
    for name in names:
        matches = re.findall(r"^#define\s+" + name + r"\s+[^\n]+", source + header, re.M)
        if len(matches) != 1:
            raise ValueError(f"Pinned saturation proof constant changed: {name}")
        constants.append(matches[0])
    # Every conditional in these six extracted bodies is a debug/plot branch,
    # unset in the upstream default build. No ADC linearization is extracted.
    bodies = {sig: _function(source, sig) for sig in I1PRO_SATURATION_SIGNATURES}
    helper = "static int cm_i1pro_scaled_finite("
    if helper in source:
        bodies[helper] = _function(source, helper)
    declarations = "\n".join(body[:body.index("{")].rstrip() + ";" for body in bodies.values())
    return (source[:source.index("#include")] + "\n".join(constants) + "\n"
            + _I1PRO_SATURATION_SUPPORT + declarations + "\n"
            + "\n".join(bodies.values()) + _I1PRO_SATURATION_MAIN)


def _validate_i1pro_saturation(results: dict) -> None:
    before, after = results["baseline"], results["patched"]
    if not (set(before) == set(after) == set(I1PRO_SATURATION_CASES)):
        raise AssertionError
    refused = {"over", "one_over", "gain_over", "revE_over", "revE_edge0", "revE_edge127"}
    for case in I1PRO_SATURATION_CASES:
        old, new = before[case], after[case]
        for row in (old, new):
            if not (row["matrix_allocs"] == row["matrix_frees"] == 2):
                raise AssertionError(case)
            if not (row["live_matrices"] == row["live_vectors"] == row["arithmetic_errors"] == 0):
                raise AssertionError
            if not (row["input_unchanged"] == row["sensor_calls"] == 1):
                raise AssertionError
        if case in refused:
            if not (old["code"] == old["ok"] and old["oracle_saturated"] == 2):
                raise AssertionError(case)
            if not (new["code"] == new["saturated"] and new["oracle_saturated"] == 2):
                raise AssertionError(f"{case}: patched reading not refused as saturated")
            if not (new["first"] == new["second"] == -9876 and new["duration"] == 0):
                raise AssertionError
        else:
            if not (old == new):
                raise AssertionError(case)
    if not (after["equal"]["first"] == 18 and after["equal"]["second"] == 27):
        raise AssertionError
    if not (after["gain_equal"]["first"] == 9.5 and after["gain_equal"]["second"] == 14.25):
        raise AssertionError
    if not (after["negative"]["first"] > 0 and after["negative"]["second"] < 0):
        raise AssertionError
    if not (after["signed_zero_dark"]["first"] > 0 and after["signed_zero_dark"]["second"] < 0):
        raise AssertionError
    if not (after["zero"]["code"] == after["zero"]["no_flash"]):
        raise AssertionError
    if not (after["wrong_count"]["code"] == after["wrong_count"]["wrong_count"]):
        raise AssertionError
    if not (after["spot_over"]["code"] == after["spot_over"]["saturated"]):
        raise AssertionError
    if not (after["strip"]["code"] == after["strip"]["strip_error"]):
        raise AssertionError
    for case in ("below", "equal", "negative", "near_zero", "gain_below", "gain_equal",
                 "gain_negative", "legacy_edge0", "legacy_edge127", "revE_equal",
                 "disabled_threshold", "long_integration", "signed_zero_dark"):
        if not (after[case]["code"] == after[case]["ok"]):
            raise AssertionError(case)
        if not (after[case]["oracle_saturated"] == 0):
            raise AssertionError(case)


@production_proof
def prove_i1pro_saturation(archive: Path, output: Path, *, source: Path | None = None,
                          compiler: str = "cc") -> dict:
    """Execute real flash caller, spot policy, gain scaling and spectral conversion.

    Acquisition, ADC linearization and black calibration remain inert. This is
    parity with the existing converted-threshold policy, not physical ADC proof.
    """
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    path = "spectro/i1pro_imp.c"
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        original = zipped.read(PREFIX + path).decode()
        header = zipped.read(PREFIX + "spectro/i1pro_imp.h")
        licence = zipped.read(PREFIX + "License2.txt")
    baseline = before_source("i1pro_flash_saturation", path, archive)
    patched = production_text(source, path)
    output.mkdir(parents=True, exist_ok=False)
    (output / "Argyll-License2.txt").write_bytes(licence)
    (output / "i1pro_imp.h").write_bytes(header)
    patch = "".join(difflib.unified_diff(baseline.splitlines(True), patched.splitlines(True),
                                      fromfile="a/" + path, tofile="b/" + path))
    (output / "i1pro-flash-saturation.patch").write_text(patch)
    report = {
        "status": "incomplete", "archive_sha256": ARCHIVE_SHA256,
        "hardware_tested": False, "instrument_runtime_linked": False,
        "original_sha256": digest(original.encode()),
        "baseline_sha256": digest(baseline.encode()), "patched_sha256": digest(patched.encode()),
        "accepted_input_sha256": [I1PRO_ENDPOINT_SHA256, I1PRO_COMPOSED_ENDPOINT_SHA256],
        "proof_source_sha256": digest(Path(__file__).read_bytes()),
        "header_sha256": digest(header), "patch_sha256": digest(patch.encode()),
        "patched_function_sha256": {
            signature: digest(_function(patched, signature).encode())
            for signature in I1PRO_SATURATION_SIGNATURES
        },
        "platform": platform.platform(), "commands": [], "results": {},
        "scope": "actual_flash_caller_spot_policy_gain_scaling_and_spectral_conversion",
        "limitations": [
            "Sensor/ADC conversion, black calibration and acquisition are synthetic; no USB.",
            "Matches spot policy after black subtraction; not complete physical ADC safety.",
            "Strip recognition is inert; its unchanged dispatch and refusal are checked only.",
        ],
    }
    try:
        for label, source in (("baseline", baseline), ("patched", patched)):
            (output / f"i1pro_imp.{label}.c").write_text(source)
            unit = output / f"{label}.c"
            unit.write_text(_i1pro_saturation_probe(source, header.decode()))
            binary = unit.with_suffix(".exe" if platform.system() == "Windows" else "")
            command = [cc, "-std=c99", "-Wall", "-Wextra", "-Werror",
                       "-Wno-unused-variable", "-Wno-unused-but-set-variable",
                       "-Wno-unused-parameter", str(unit), "-o", str(binary)]
            if platform.system() != "Windows":
                command.append("-lm")
            build = subprocess.run(command, capture_output=True, text=True, timeout=60)
            (output / f"{label}-compile.log").write_text(build.stdout + build.stderr)
            report["commands"].append({"argv": command, "returncode": build.returncode})
            build.check_returncode()
            report["results"][label] = {}
            for case in I1PRO_SATURATION_CASES:
                run = subprocess.run([str(binary), case], capture_output=True,
                                     text=True, timeout=10)
                (output / f"{label}-{case}.log").write_text(run.stdout + run.stderr)
                run.check_returncode()
                if run.stderr:
                    raise AssertionError(f"Unexpected proof stderr: {label}/{case}")
                report["results"][label][case] = json.loads(run.stdout)
        _validate_i1pro_saturation(report["results"])
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = prove(args.archive.resolve(), args.output.resolve())
    print(json.dumps({"status": report["status"], "cases_per_variant": len(CASES),
                      "hardware_tested": False}))


if __name__ == "__main__":
    main()
