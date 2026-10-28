/* Run an extracted Argyll delay method against synthetic spectra and clocks. */
#include <float.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef METHOD_INCLUDE
#error METHOD_INCLUDE must name the extracted source file
#endif

#define NDMXTIME 2.0
#define XSPECT_WL(lo, hi, count, index) ((lo) + ((hi) - (lo)) * (index) / ((count) - 1))
/* Argyll 3.5.0's actual values (inst.h:139,145,164; i1pro_imp.h:358,371,419,441;
 * i1pro3_imp.h:537,550,587,604; munki_imp.h:320,332,364,392). Invented stand-ins
 * cannot show what the driver wrapper reports: it masks with inst_imask first. */
#define inst_ok 0x000000
#define inst_internal_error 0x060000
#define inst_imask 0x00ffff
#define I1PRO_OK 0x00
#define I1PRO3_OK 0x00
#define MUNKI_OK 0x00
#define I1PRO_RD_NOTRANS_FOUND 0x41
#define I1PRO3_RD_NOTRANS_FOUND 0x41
#define MUNKI_RD_NOTRANS_FOUND 0x41
#define I1PRO_INT_MALLOC 0x63
#define I1PRO3_INT_MALLOC 0x63
#define MUNKI_INT_MALLOC 0x62
#define I1PRO_INTERNAL_ERROR 0x71
#define I1PRO3_INTERNAL_ERROR 0x71
#define MUNKI_INTERNAL_ERROR 0x71
#define a1logd(...) ((void)0)
#define a1loge(...) ((void)0)

typedef int i1pro_code;
typedef int i1pro3_code;
typedef int munki_code;
typedef struct { double sec, rgb[3], tot; } i1rgbdsamp;
typedef struct { int unused; } i1pro_state;
typedef i1pro_state i1pro3_state;
typedef i1pro_state munki_state;
typedef struct {
    int mmode, highres, nwav[2];
    double min_int_time, whitestamp, trigstamp;
    double wl_short[2], wl_long[2];
    i1pro_state ms[1];
} i1proimp;
typedef i1proimp i1pro3imp;
typedef struct {
    int mmode, nwav;
    double min_int_time, whitestamp, trigstamp, wl_short, wl_long;
    munki_state ms[1];
} munkiimp;
typedef struct { void *log; void *m; } i1pro;
typedef i1pro i1pro3;
typedef i1pro munki;

static int matrices_created, matrices_freed, samples_created, samples_freed;
static int fail_sample_alloc, case_kind;

static double usec_time(void) { return case_kind == 5 ? 0.0/0.0 : 1000000.0; }

static double **dmatrix(int low_row, int high_row, int low_col, int high_col) {
    int i, count = high_row - low_row + 1;
    double **m = (double **)malloc((size_t)count * sizeof(*m));
    (void)low_col;
    for (i = 0; i < count; ++i)
        m[i] = (double *)calloc((size_t)high_col + 2, sizeof(**m));
    ++matrices_created;
    return m;
}

static void free_dmatrix(double **m, int lr, int hr, int lc, int hc) {
    int i;
    (void)lr; (void)lc; (void)hc;
    if (m == NULL) return;
    for (i = 0; i <= hr; ++i) free(m[i]);
    free(m);
    ++matrices_freed;
}

static void *probe_calloc(size_t n, size_t size) {
    if (fail_sample_alloc) return NULL;
    ++samples_created;
    return calloc(n, size);
}

static void probe_free(void *p) {
    if (p != NULL) ++samples_freed;
    free(p);
}

static int fill(double **m, int n) {
    int i, j;
    if (case_kind == 4) return 9;
    for (i = 0; i < n; ++i)
        for (j = 0; j < 3; ++j)
            m[i][j] = i < n/2 || case_kind == 3 ? 0.0 :
                      case_kind == 1 && i == n/2 ? DBL_MAX : 10.0;
    return inst_ok;
}

/* The real read writes back the integration time it used (i1pro_imp.c:2769,
 * i1pro3_imp.c:1630, munki_imp.c:1843 pass &inttime); these cases return a bad one. */
static void readback(double *it) {
    if (case_kind == 12) *it = 0.0;
    else if (case_kind == 13) *it = INFINITY;
}

static int i1pro_read_patches_all(i1pro *p, double **m, int n, double *it, int x) {
    (void)p; (void)x; readback(it); return fill(m, n);
}
static int i1pro3_spot_simple_emis_meas(i1pro3 *p, double **m, int n, double *it, int hr) {
    (void)p; (void)hr; readback(it); return fill(m, n);
}
static int munki_read_patches_all(munki *p, double **m, int n, double *it, int x) {
    (void)p; (void)x; readback(it); return fill(m, n);
}

#define calloc probe_calloc
#define free probe_free
#include METHOD_INCLUDE
#undef calloc
#undef free

int main(int argc, char **argv) {
    int display = -88, instrument = -99, code;
    i1proimp a;
    munkiimp b;
    i1pro p;
    if (argc != 2) return 2;
    if (!strcmp(argv[1], "control")) case_kind = 0;
    else if (!strcmp(argv[1], "overflow")) case_kind = 1;
    else if (!strcmp(argv[1], "missing_stamp")) case_kind = 2;
    else if (!strcmp(argv[1], "flat")) case_kind = 3;
    else if (!strcmp(argv[1], "read_error")) case_kind = 4;
    else if (!strcmp(argv[1], "nan_clock")) case_kind = 5;
    else if (!strcmp(argv[1], "alloc_fail")) case_kind = 6;
    else if (!strcmp(argv[1], "large_stamp")) case_kind = 7;
    else if (!strcmp(argv[1], "inttime_zero")) case_kind = 8;
    else if (!strcmp(argv[1], "inttime_nan")) case_kind = 9;
    else if (!strcmp(argv[1], "inttime_large")) case_kind = 10;
    else if (!strcmp(argv[1], "reaction_only")) case_kind = 11;
    else if (!strcmp(argv[1], "readback_zero")) case_kind = 12;
    else if (!strcmp(argv[1], "readback_inf")) case_kind = 13;
    else return 2;
    memset(&a, 0, sizeof(a)); memset(&b, 0, sizeof(b));
    a.min_int_time = b.min_int_time = case_kind == 8 ? 0.0
        : case_kind == 9 ? NAN : case_kind == 10 ? 2.0 : 0.1;
    a.whitestamp = b.whitestamp = case_kind == 2 ? -1.0
        : case_kind == 11 ? 1e16 : 1000000.0;
    a.trigstamp = b.trigstamp = case_kind == 7 || case_kind == 11 ? 1e16 : 1000000.0;
    a.nwav[0] = b.nwav = case_kind == 1 ? 2 : 3;
    a.wl_short[0] = b.wl_short = case_kind == 1 ? 520.0 : 600.0;
    a.wl_long[0] = b.wl_long = 610.0;
    p.log = NULL;
    fail_sample_alloc = case_kind == 6;
#if defined(TEST_I1PRO)
    p.m = &a;
    code = i1pro_imp_meas_delay(&p, &display, &instrument);
#elif defined(TEST_I1PRO3)
    p.m = &a;
    code = i1pro3_imp_meas_delay(&p, &display, &instrument);
#elif defined(TEST_MUNKI)
    p.m = &b;
    code = munki_imp_meas_delay(&p, &display, &instrument);
#else
#error Select a driver
#endif
    /* The last field is what the driver wrapper reports: its *_interp_code
     * masks with inst_imask, so zero after masking reaches callers as inst_ok. */
    printf("%d %d %d %d %d %d %d %d\n", code, display, instrument,
           matrices_created, matrices_freed, samples_created, samples_freed,
           (code & inst_imask) != 0);
    return 0;
}
