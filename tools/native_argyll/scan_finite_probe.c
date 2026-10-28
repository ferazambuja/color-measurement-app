/* Inert raw-strip regression harness. recognizer.inc and caller.inc are exact
 * ArgyllCMS extracts, GPL-2.0-or-later, Graeme W. Gill; notice retained alongside.
 * numsup/rspl1 are linked; no driver transport, calibration or conversion runs. */
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
#ifdef CM_I1PRO
#include "i1pro.h"
#include "i1pro_imp.h"
#define METER i1pro
#define IMPL i1proimp
#define PATCH_CONS_THR 0.1
#define READINCONS I1PRO_RD_READINCONS
#define SET_SCAN_TOLERANCE i1pro_set_scan_toll
#else
#include "munki.h"
#include "munki_imp.h"
#define METER munki
#define IMPL munkiimp
#define PATCH_CONS_THR 0.05
#define READINCONS MUNKI_RD_READINCONS
#define SET_SCAN_TOLERANCE munki_set_scan_toll
#endif
#include "recognizer.inc"
#include "scan_tolerance.inc"

/* Execute the caller's real error branch. Only buffer release and log are
 * substituted here; full acquisition cleanup is not claimed. */
static int cleanup_count;
static void cleanup(double **p, int a, int b, int c, int d) {
    (void)p; (void)a; (void)b; (void)c; (void)d; cleanup_count++;
}
#define free_dmatrix cleanup
#define a1logd(...) ((void)0)
static int caller_result(int rv) {
    double **absraw = NULL;
    int numpatches = 3;
    IMPL implementation = {0}, *m = &implementation;
    m->nraw = 128;
#include "caller.inc"
    return 0;
}
#undef free_dmatrix
#undef a1logd

static const char *scenario;
static int is(const char *name) { return strcmp(scenario, name) == 0; }
static void number(double x) {
    if (isnan(x)) printf("\"NaN\"");
    else if (!isfinite(x)) printf(x > 0 ? "\"Infinity\"" : "\"-Infinity\"");
    else printf("%.17g", x);
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    scenario = argv[1];
    const int width = is("sparse") ? 5 : 100;
    int rows = 5 * width, requested = is("count_mismatch") ? 4 : 3;
    METER meter = {0}; IMPL impl = {0};
    meter.m = &impl; meter.log = g_log;
    impl.nraw = 128;
    double tolerance = is("neg_row_nan_tolerance") ? NAN :
                       is("neg_row_inf_tolerance") ? INFINITY : 1.0;
    if (SET_SCAN_TOLERANCE(&meter, tolerance) != 0) return 4;
#ifdef CM_I1PRO
    impl.mmode = i1p_emiss_scan;
    impl.ms[i1p_emiss_scan].emiss = 1;
    impl.ms[i1p_emiss_scan].scan = 1;
    impl.ms[i1p_emiss_scan].adaptive = 1;
#endif
    double **in = dmatrixz(0, rows-1, -1, 127);
    double **out = dmatrix(0, requested-1, -1, 127);
    int nonfinite_in = 0, nonfinite_out = 0;
    for (int p = 0; p < requested; p++)
        for (int j = -1; j < 128; j++) out[p][j] = -1234567.0;
    for (int i = 0; i < rows; i++) {
        int seg = i / width, within = i % width;
        for (int j = 0; j < 128; j++) {
            double x;
            if (seg == 0 || seg == 4) x = is("near_zero") ? 1e-3 : 1000.0;
            else {
                int k = is("reverse") ? 3-seg : seg-1;
                const double levels[] = {200, 600, 350};
                x = levels[k] * (1.0 + 0.3 * sin(0.1*j + 1.3*k));
            }
            if (is("margin_mismatch") && seg == 4) x = 400.0;
            int negative_overflow = is("neg_row_overflow") ||
                                    is("neg_row_inf_tolerance") || is("neg_row_nan_tolerance");
            if (is("negative") || negative_overflow) x = -x;
            if ((is("sat9") || is("sat10")) && j >= 1 && j <= (is("sat9") ? 9 : 10))
                x = 12000.0;
            int middle = seg == 2 && within == width/2;
            /* One retained row whose sum overflows to -Inf while every band average
             * stays finite; the negated strip floors every column maximum to 1.0. */
            if (middle && negative_overflow) x = -1e307;
            if (middle && is("nan_row")) x = NAN;
            if (middle && j == 20 && is("nan_cell")) x = NAN;
            if (middle && j == 20 && is("inf_cell")) x = INFINITY;
            if (middle && j == 20 && is("neginf_cell")) x = -INFINITY;
            if (middle && j == 0 && is("endpoint_nan")) x = NAN;
            if (seg == 2 && is("band_overflow") && (j == 20 || j == 21))
                x = (j == 20 ? 1 : -1) * (DBL_MAX/4.0);
            if (middle && is("row_overflow") && j >= 20 && j <= 23)
                x = (j < 22 ? 1 : -1) * (DBL_MAX*0.6);
            /* Trimmed patch-edge cells do not enter any patch's returned average,
             * but their independent maxima enter the white normalizer. */
            if (seg == 2 && is("white_overflow") && within >= 2 && within <= 11
                    && j == within)
                x = DBL_MAX/2.0;
            in[i][j] = x;
            nonfinite_in += !isfinite(x);
        }
    }
    int flags = -999, code;
#ifdef CM_I1PRO
    double highest = 0.0;
    code = i1pro_extract_patches_multimeas(&meter, &flags, out, requested, in, rows,
                                          &highest, 10000.0, 0.0182);
#else
    code = munki_extract_patches_multimeas(&meter, &flags, out, requested, in, rows, 0.0182);
#endif
    for (int p = 0; p < requested; p++)
        for (int j = 0; j < 128; j++) nonfinite_out += !isfinite(out[p][j]);
    int error = code == 0 ? caller_result(flags) : code;
    printf("{\"case\":\"%s\",\"code\":%d,\"flags\":%d,\"nonfinite_input\":%d,"
           "\"nonfinite_output\":%d,\"caller_error\":%d,\"cleanup\":%d,\"readincons\":%d,\"values\":[",
           scenario, code, flags, nonfinite_in, nonfinite_out, error, cleanup_count, READINCONS);
    for (int p = 0; p < requested; p++) {
        if (p) putchar(','); putchar('[');
        for (int j = 0; j < 128; j++) { if (j) putchar(','); number(out[p][j]); }
        putchar(']');
    }
    puts("]}");
    free_dmatrix(in, 0, rows-1, -1, 127);
    free_dmatrix(out, 0, requested-1, -1, 127);
    return 0;
}
