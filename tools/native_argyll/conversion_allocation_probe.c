/* Original inert allocation/control-flow harness. Extracted Argyll functions
 * remain Graeme W. Gill's GPL-2.0-or-later work with their retained notices.
 * No transport, acquisition, file IO or calibration runs. */
#include "preamble.inc"
#include <stdio.h>
#include <math.h>

static int checks, failures, allocations, releases, integrations, live, fail_factory;
static xsp2cie objects[2];
static int occupied[2];
#define CHECK(x) do { checks++; if (!(x)) { failures++; \
    fprintf(stderr, "line %d: %s\n", __LINE__, #x); } } while (0)

static void spy_convert(xsp2cie *p, double *xyz, xspect *sp) {
    int i;
    integrations++;
    for (i = 0; i < 3; i++) {
        xyz[i] = sp->spec[i] / sp->norm;
        if (p->clamp && xyz[i] < 0.0) xyz[i] = 0.0;
    }
}
static void spy_delete(xsp2cie *p) {
    int slot;
    for (slot = 0; slot < 2 && p != &objects[slot]; slot++) {}
    CHECK(slot < 2 && occupied[slot]);
    if (slot < 2 && occupied[slot]) {
        occupied[slot] = 0;
        live--; releases++;
    }
}
xsp2cie *new_xsp2cie(icxIllumeType illuminant, double temp, xspect *custom,
                     icxObserverType observer, xspect observers[3],
                     icColorSpaceSignature space, icxClamping clamp) {
    int slot;
    xsp2cie *object;
    allocations++;
    CHECK(illuminant == icxIT_D50 || illuminant == icxIT_none);
    CHECK(temp == 0.0 && custom == NULL && observers == NULL);
    CHECK(observer == icxOT_CIE_1931_2 && space == icSigXYZData);
    if (fail_factory) return NULL;
    for (slot = 0; slot < 2 && occupied[slot]; slot++) {}
    CHECK(slot < 2 && (live == 0 || (live == 1 && illuminant == icxIT_none
                                                && clamp == icxNoClamp)));
    if (slot == 2) return NULL;
    object = &objects[slot];
    memset(object, 0, sizeof(*object));
    object->convert = spy_convert; object->del = spy_delete; object->clamp = clamp;
    occupied[slot] = 1;
    live++;
    return object;
}
int getval_xspec(xspect *s, double *value, double wl) {
    int k = (int)((wl - s->spec_wl_short) * (s->spec_n - 1)
                  / (s->spec_wl_long - s->spec_wl_short) + 0.5);
    if (k < 0) k = 0;
    if (k >= s->spec_n) k = s->spec_n - 1;
    *value = s->spec[k] / s->norm;
    return 0;
}
#include "functions.inc"

static xspect filter;
static void reset(int failing) {
    CHECK(live == 0 && !occupied[0] && !occupied[1]);
    allocations = releases = integrations = 0;
    fail_factory = failing;
}
static void setup_filter(void) {
    int i;
    memset(&filter, 0, sizeof(filter));
    filter.spec_n = 6; filter.spec_wl_short = 400.0;
    filter.spec_wl_long = 450.0; filter.norm = 1.0;
    for (i = 0; i < 6; i++) filter.spec[i] = 0.8 + i * 0.03;
}
static void fixture(ipatch *vals, int n, inst_meas_type type, int xyz) {
    int i, j;
    memset(vals, 0, n * sizeof(*vals));
    for (i = 0; i < n; i++) {
        vals[i].loc[0] = 'A' + i; vals[i].mtype = type;
        vals[i].mcond = inst_mrc_none; vals[i].XYZ_v = xyz;
        vals[i].XYZ[0] = 11.0; vals[i].XYZ[1] = 22.0; vals[i].XYZ[2] = 33.0;
        vals[i].duration = 0.2;
        vals[i].sp.spec_n = 6; vals[i].sp.spec_wl_short = 400.0;
        vals[i].sp.spec_wl_long = 450.0;
        vals[i].sp.norm = type == inst_mrt_reflective ? 100.0 : 1.0;
        for (j = 0; j < 6; j++) vals[i].sp.spec[j] = j == 0 ? -0.1 : 0.2 + 0.03*j + 0.01*i;
    }
}
static int apply(int filtered, ipatch *v, int n, int clamp) {
    return filtered ? ipatch_convert_custom_filter(v, n, &filter, clamp)
                    : ipatch_convert_xrga(v, n, xcalstd_nonpol, xcalstd_xrdi,
                                           xcalstd_xrga, clamp);
}
static void baseline(int filtered, ipatch *v, int n, int clamp) {
    if (filtered) ipatch_convert_custom_filter_baseline(v, n, &filter, clamp);
    else ipatch_convert_xrga_baseline(v, n, xcalstd_nonpol, xcalstd_xrdi,
                                    xcalstd_xrga, clamp);
}

/* A minimal state for source-bound call fragments. These are not drivers. */
typedef struct {
    xcalstd target_calstd, native_calstd;
    int custfilt_en;
    ss_aft filt;
    xspect custfilt;
} proof_driver;
static void setup_driver(proof_driver *p, int enabled) {
    p->target_calstd = xcalstd_xrdi; p->native_calstd = xcalstd_xrga;
    p->custfilt_en = enabled; p->filt = ss_aft_NoFilter; p->custfilt = filter;
}
#include "callers.inc"

static void failed_batches(int filtered, inst_meas_type type) {
    ipatch v[2], saved[2];
    int first_xyz;
    for (first_xyz = 0; first_xyz < 2; first_xyz++) {
        fixture(v, 2, type, 1);
        v[0].XYZ_v = first_xyz;
        memcpy(saved, v, sizeof(v));
        reset(1);
        CHECK(apply(filtered, v, 2, instClamp) == 1);
        CHECK(memcmp(v, saved, sizeof(v)) == 0);
        CHECK(allocations == 1 && integrations == 0 && releases == 0 && live == 0);
        /* The same inputs work after failure, without retaining failed state. */
        reset(0);
        CHECK(apply(filtered, v, 2, instClamp) == 0);
        CHECK(allocations == 1 + filtered && releases == 1 + filtered && live == 0);
        CHECK(integrations == (1 + filtered) * (first_xyz + 1));
    }
}
static void success_pairs(int filtered, inst_meas_type type) {
    ipatch a[3], b[3], original[3];
    int xyz, clamp;
    for (xyz = 0; xyz < 2; xyz++) for (clamp = 0; clamp < 2; clamp++) {
        fixture(a, 3, type, xyz);
        /* A mixed batch also preserves an ineligible patch unchanged. */
        a[1].mtype = inst_mrt_transmissive;
        memcpy(b, a, sizeof(a)); memcpy(original, a, sizeof(a));
        reset(0); baseline(filtered, a, 3, clamp);
        CHECK(allocations == xyz && releases == xyz && live == 0);
        reset(0); CHECK(apply(filtered, b, 3, clamp) == 0);
        CHECK(allocations == (1 + filtered)*xyz && releases == (1 + filtered)*xyz
              && integrations == 2*(1 + filtered)*xyz && live == 0);
        CHECK(memcmp(a, b, sizeof(a)) == 0);
        CHECK(memcmp(&b[1], &original[1], sizeof(ipatch)) == 0);
        CHECK(memcmp(b[0].sp.spec, original[0].sp.spec, 6*sizeof(double)) != 0);
    }
}
static void noops(int filtered) {
    ipatch v[2], saved[2];
    inst_meas_type type = filtered ? inst_mrt_emission : inst_mrt_reflective;
    fixture(v, 2, inst_mrt_transmissive, 1); memcpy(saved, v, sizeof(v));
    reset(1); CHECK(apply(filtered, v, 2, instClamp) == 0);
    CHECK(allocations == 0 && memcmp(v, saved, sizeof(v)) == 0);
    fixture(v, 2, type, 1); v[0].sp.spec_n = v[1].sp.spec_n = 0;
    memcpy(saved, v, sizeof(v));
    reset(1); CHECK(apply(filtered, v, 2, instClamp) == 0);
    CHECK(allocations == 0 && memcmp(v, saved, sizeof(v)) == 0);
    reset(1); CHECK(apply(filtered, NULL, 0, instClamp) == 0);
    CHECK(apply(filtered, NULL, -1, instClamp) == 0 && allocations == 0);
    /* A spectrum-only correction must still run without a converter allocation. */
    fixture(v, 2, type, 0); memcpy(saved, v, sizeof(v));
    reset(1); CHECK(apply(filtered, v, 2, instClamp) == 0);
    CHECK(allocations == 0 && memcmp(v[0].sp.spec, saved[0].sp.spec, 6*sizeof(double)) != 0);
    CHECK(memcmp(v[0].XYZ, saved[0].XYZ, sizeof(v[0].XYZ)) == 0 && !v[0].XYZ_v);
    fixture(v, 2, type, 1); memcpy(saved, v, sizeof(v));
    reset(1);
    if (filtered) {
        filter.spec_n = 0;
        CHECK(apply(1, v, 2, instClamp) == 0);
        setup_filter();
    } else {
        CHECK(ipatch_convert_xrga(v, 2, xcalstd_nonpol, xcalstd_native, xcalstd_xrga, 1) == 0);
        CHECK(ipatch_convert_xrga(v, 2, xcalstd_nonpol, xcalstd_xrdi, xcalstd_native, 1) == 0);
        CHECK(ipatch_convert_xrga(v, 2, xcalstd_nonpol, xcalstd_xrga, xcalstd_xrga, 1) == 0);
    }
    CHECK(allocations == 0 && memcmp(v, saved, sizeof(v)) == 0);
}
static void callers(void) {
    int i;
    ipatch v[2], saved[2];
    for (i = 0; i < (int)(sizeof(caller_cases)/sizeof(caller_cases[0])); i++) {
        fixture(v, 2, caller_cases[i].filtered ? inst_mrt_emission : inst_mrt_reflective, 1);
        memcpy(saved, v, sizeof(v));
        reset(1); CHECK(caller_cases[i].run(v, 1) == caller_cases[i].error);
        CHECK(allocations == 1 && live == 0 && memcmp(v, saved, sizeof(v)) == 0);
        reset(0); CHECK(caller_cases[i].run(v, 1) == inst_ok);
        CHECK(allocations == 1 + caller_cases[i].filtered
              && releases == 1 + caller_cases[i].filtered && live == 0);
        if (caller_cases[i].filtered) {
            memcpy(v, saved, sizeof(v)); reset(1);
            CHECK(caller_cases[i].run(v, 0) == inst_ok);
            CHECK(allocations == 0 && memcmp(v, saved, sizeof(v)) == 0);
        }
    }
}
int main(int argc, char **argv) {
    int i;
    inst_meas_type types[] = {inst_mrt_emission, inst_mrt_ambient,
                              inst_mrt_emission_flash, inst_mrt_ambient_flash};
    setup_filter();
    if (argc == 2) {
        ipatch v[2];
        int filtered = strcmp(argv[1], "baseline-filter-oom") == 0;
        if (!filtered && strcmp(argv[1], "baseline-xrga-oom") != 0) return 2;
        fixture(v, 2, filtered ? inst_mrt_emission : inst_mrt_reflective, 1);
        reset(1); baseline(filtered, v, 2, instClamp);
        return 0; /* The pristine helper must not reach this with failed allocation. */
    }
    failed_batches(0, inst_mrt_reflective); success_pairs(0, inst_mrt_reflective); noops(0);
    for (i = 0; i < 4; i++) { failed_batches(1, types[i]); success_pairs(1, types[i]); }
    noops(1); callers();
    CHECK(live == 0);
    printf("{\"checks\":%d,\"failures\":%d,\"callers\":%d}\n", checks, failures,
           (int)(sizeof(caller_cases)/sizeof(caller_cases[0])));
    return failures ? 1 : 0;
}
