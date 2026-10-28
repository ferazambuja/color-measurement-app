/* Original inert harness. Extracted driver/converter code carries its original
 * GPL-2.0-or-later notices. No transports or physical acquisition are linked. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "declarations.h"

typedef struct {
    int gotcoms, inited;
    devType dtype;
    void *log;
    inst_mode mode;
    int pisrow, calcount, need_wd_cal, need_t_cal, noinitcalib, forcecalib;
    ss_aft filt;
    xcalstd target_calstd, native_calstd;
    int custfilt_en;
    xspect custfilt;
} ss;
typedef ss inst;

#define a1logd(...) ((void)0)
#define a1logv(...) ((void)0)
static int checks, failures, cases, moves, xyz_reads, calibrations;
static int conversion_calls, conversion_objects, conversion_deletes, custom_calls;
static double last_x, last_y;
static inst_code calibration_result;
static const char *scenario;
static int failure_stage, failures_left;
#define CHECK(expression) do { checks++; if (!(expression)) { failures++; \
    fprintf(stderr, "%s:%d: %s\n", scenario, __LINE__, #expression); } } while (0)

static int injected(int stage) {
    if (failure_stage == stage && failures_left > 0) {
        failures_left--;
        return 1;
    }
    return 0;
}
static double sample(double x, double y, int band) {
    /* A signed finite band plus a varying spectrum make unwanted numeric
     * changes visible. These are synthetic command values, not measurements. */
    return band == 0 ? -0.003 : 0.2 + 0.003*x + 0.002*y + 0.001*band;
}
static inst_code ss_do_MoveAndMeasure(ss *p, double x, double y,
                                      double *spectrum, ss_rvt *valid) {
    int i;
    moves++;
    if (injected(1)) return inst_coms_fail;
    last_x = x; last_y = y;
    for (i = 0; i < 36; i++) spectrum[i] = sample(x, y, i);
    *valid = ss_rvt_True;
    return inst_ok;
}
static inst_code so_do_CParameterRequest(ss *p, ss_cst requested, ss_cst *actual,
    double *xyz, ss_rvt *valid, ss_aft *filter, ss_wbt *white, ss_ilt *illum, ss_ot *observer) {
    xyz_reads++;
    if (injected(2)) return inst_coms_fail;
    CHECK(requested == ss_cst_XYZ);
    *actual = requested; *valid = injected(3) ? ss_rvt_False : ss_rvt_True;
    *filter = p->filt;
    *white = ss_wbt_Abs; *illum = ss_ilt_D50; *observer = ss_ot_TwoDeg;
    xyz[0] = last_x; xyz[1] = last_y; xyz[2] = last_x + last_y;
    return inst_ok;
}
static inst_code ss_calibrate_imp(ss *p, inst_cal_type *type, inst_cal_cond *condition,
                                  inst_calc_id_type *idtype, char id[CALIDLEN]) {
    calibrations++;
    if (injected(4)) return inst_coms_fail;
    if (calibration_result == inst_ok) {
        /* Successful reflective ss_calibrate_imp clears these at ss.c:1517-1518. */
        p->calcount = p->need_wd_cal = 0;
    }
    return calibration_result;
}
#if CM_PRODUCTION
static int ipatch_convert_custom_filter(ipatch *patch, int count, xspect *filter, int clamp) {
    custom_calls++;
    return 0;
}
#else
static void ipatch_convert_custom_filter(ipatch *patch, int count, xspect *filter, int clamp) {
    custom_calls++;
}
#endif

/* Explicit call spy: the real XRGA dispatcher calls this in place of xsp2cie.
 * No test below claims a colorimetric XYZ integration result. */
enum { icSigXYZData = 0x58595a20 };
typedef struct _xsp2cie {
    void (*convert)(struct _xsp2cie *, double *, xspect *);
    void (*del)(struct _xsp2cie *);
} xsp2cie;
static void spy_convert(xsp2cie *self, double *xyz, xspect *spectrum) {
    int i;
    conversion_calls++;
    for (i = 0; i < 3; i++) xyz[i] = spectrum->spec[i] / 100.0;
}
static void spy_delete(xsp2cie *self) { conversion_deletes++; }
static xsp2cie *new_xsp2cie(icxIllumeType illuminant, double temp, xspect *custom_illum,
    icxObserverType observer, xspect *custom_observer, int space, icxClamping clamp) {
    static xsp2cie spy = {spy_convert, spy_delete};
    conversion_objects++;
    CHECK(illuminant == icxIT_D50 && temp == 0.0 && custom_illum == NULL);
    CHECK(observer == icxOT_CIE_1931_2 && custom_observer == NULL);
    CHECK(space == icSigXYZData && clamp == icxClamp);
    return &spy;
}
#include "conversion.inc"
#include "functions.inc"

static ss initialized(void) {
    ss p;
    memset(&p, 0, sizeof(p));
    p.gotcoms = p.inited = 1;
    p.dtype = instSpectroScan;
    p.mode = inst_mode_ref_xy | inst_mode_colorimeter | inst_mode_spectral;
    p.filt = ss_aft_NoFilter;
    p.native_calstd = xcalstd_gmdi;
    p.target_calstd = xcalstd_native;
    return p;
}
static void reset(const char *name) {
    scenario = name;
    cases++;
    moves = xyz_reads = calibrations = conversion_calls = 0;
    conversion_objects = conversion_deletes = custom_calls = 0;
    calibration_result = inst_ok;
    failure_stage = failures_left = 0;
}
static void grid(ss_aft filter, inst_meas_cond condition, int converted, int fast_step) {
    ss p = initialized();
    ipatch values[6], sentinel;
    int patch, band, axis;
    double ax = fast_step ? 0 : 10, ay = fast_step ? 10 : 0;
    double px = fast_step ? 10 : 0, py = fast_step ? 0 : 10;
    reset(converted ? "converted_grid" : "native_grid");
    memset(values, 0xa5, sizeof(values));
    sentinel = values[5];
    p.filt = filter;
    if (fast_step) p.dtype = instSpectroScanT;
    if (converted) p.target_calstd = xcalstd_xrga;
    CHECK(ss_read_xy((inst *)&p, 2, 3, 5, "A", "1", 10, 20, ax, ay, 0, 0,
                     px, py, values) == inst_ok);
    CHECK(moves == 5 && xyz_reads == 5 && calibrations == 0 && p.calcount == 5);
    CHECK(conversion_calls == (converted ? 5 : 0));
    CHECK(conversion_objects == (converted ? 1 : 0));
    CHECK(conversion_deletes == conversion_objects && custom_calls == 0);
    CHECK(memcmp(&values[5], &sentinel, sizeof(sentinel)) == 0);
    for (patch = 0; patch < 5; patch++) {
        xspect raw, expected;
        int pass = patch / 3, step = patch % 3;
        double x = 10 + pass*ax + step*px, y = 20 + pass*ay + step*py;
        memset(&raw, 0, sizeof(raw));
        raw.spec_n = 36; raw.spec_wl_short = 380; raw.spec_wl_long = 730; raw.norm = 100;
        for (band = 0; band < 36; band++) raw.spec[band] = 100.0*sample(x, y, band);
        xspec_convert_xrga(&expected, &raw,
            filter == ss_aft_PolFilter ? xcalstd_pol : xcalstd_nonpol,
            p.target_calstd, p.native_calstd);
        CHECK(values[patch].mtype == inst_mrt_reflective);
        CHECK(values[patch].mcond == condition);
        CHECK(values[patch].XYZ_v == 1 && values[patch].loc[0] == '\0');
        CHECK(values[patch].duration == 0.0);
        CHECK(values[patch].sp.spec_n == 36 && values[patch].sp.spec_wl_short == 380
              && values[patch].sp.spec_wl_long == 730 && values[patch].sp.norm == 100);
        for (band = 0; band < 36; band++)
            CHECK(values[patch].sp.spec[band] == expected.spec[band]);
        for (axis = 0; axis < 3; axis++) {
            double wanted = converted ? expected.spec[axis] : axis == 0 ? x : axis == 1 ? y : x+y;
            CHECK(fabs(values[patch].XYZ[axis] - wanted) < 1e-12);
        }
    }
}
static void early(int variant) {
    ss p = initialized();
    ipatch value, before;
    inst_code expected = inst_ok;
    reset("early_refusal");
    memset(&value, 0xa5, sizeof(value));
    before = value;
    if (variant == 0) { p.gotcoms = 0; expected = inst_no_coms; }
    if (variant == 1) { p.inited = 0; expected = inst_no_init; }
    if (variant == 2) { p.dtype = instSpectrolino; expected = inst_unsupported; }
    if (variant == 3) {
        p.need_wd_cal = 1; calibration_result = inst_cal_setup; expected = inst_needs_cal;
    }
    CHECK(ss_read_xy((inst *)&p, 1, 1, 1, "A", "1", 0, 0, 1, 0, 0, 0, 0, 1, &value)
          == expected);
    CHECK(memcmp(&value, &before, sizeof(value)) == 0);
    CHECK(moves == 0 && xyz_reads == 0 && conversion_calls == 0);
    CHECK(calibrations == (variant == 3));
}
static void retry_case(int stage, int failures_to_inject) {
    static const char *names[][2] = {
        {"move_exhausted", "move_fifth_succeeds"},
        {"xyz_error_exhausted", "xyz_error_fifth_succeeds"},
        {"xyz_invalid_exhausted", "xyz_invalid_fifth_succeeds"},
        {"calibration_exhausted", "calibration_fifth_succeeds"}
    };
    ss p = initialized();
    ipatch values[2], untouched;
    int exhausted = failures_to_inject == 5, attempted;
    inst_code rv;
    reset(names[stage - 1][!exhausted]);
    failure_stage = stage;
    failures_left = failures_to_inject;
    if (stage == 4) p.need_wd_cal = 1;
    p.custfilt_en = 1; /* Spy reveals fall-through after a lost failed patch. */
    memset(values, 0, sizeof(values));
    untouched = values[1];
    rv = ss_read_xy((inst *)&p, 1, 2, 2, "A", "1", 10, 20, 0, 10, 0, 0,
                    10, 0, values);
    attempted = stage == 4 ? calibrations : stage == 1 ? moves : xyz_reads;
    if (exhausted) {
        CHECK(rv == (stage == 3 ? (inst_wrong_setup | ss_et_NoValidMeas) : inst_coms_fail));
        CHECK(attempted == 5);
        CHECK(values[0].XYZ_v == 0);
        CHECK(memcmp(&values[1], &untouched, sizeof(untouched)) == 0);
    } else {
        CHECK(rv == inst_ok);
        CHECK(attempted == (stage == 4 ? 5 : 6));
        CHECK(values[0].XYZ_v == 1 && values[1].XYZ_v == 1);
        CHECK(values[0].XYZ[0] == 10.0 && values[1].XYZ[0] == 20.0);
    }
    CHECK(failures_left == 0);
    CHECK(conversion_calls == 0 && conversion_objects == 0 && conversion_deletes == 0);
    CHECK(custom_calls == !exhausted);
}
int main(void) {
    static const ss_aft filters[] = {ss_aft_NoFilter, ss_aft_PolFilter, ss_aft_D65Filter,
                                   ss_aft_UVCutFilter, ss_aft_CustomFilter};
    static const inst_meas_cond conditions[] = {inst_mrc_none, inst_mrc_pol, inst_mrc_D65,
                                               inst_mrc_uvcut, inst_mrc_custom};
    int f, converted, fast_step, metadata_checks, metadata_failures, metadata_cases;
    for (f = 0; f < 5; f++)
        for (converted = 0; converted < 2; converted++)
            for (fast_step = 0; fast_step < 2; fast_step++)
                grid(filters[f], conditions[f], converted, fast_step);
    for (f = 0; f < 4; f++) early(f);
    metadata_checks = checks;
    metadata_failures = failures;
    metadata_cases = cases;
    for (f = 1; f <= 4; f++) {
        retry_case(f, 5);
        retry_case(f, 4);
    }
    printf("{\"cases\":%d,\"checks\":%d,\"failures\":%d,"
           "\"groups\":{\"metadata\":{\"cases\":%d,\"checks\":%d,\"failures\":%d},"
           "\"retry\":{\"cases\":%d,\"checks\":%d,\"failures\":%d}}}\n",
           cases, checks, failures, metadata_cases, metadata_checks, metadata_failures,
           cases - metadata_cases, checks - metadata_checks, failures - metadata_failures);
    return failures != 0;
}
