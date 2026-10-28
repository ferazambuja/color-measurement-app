/* Inert source-bound conversion probe. The converter body and weights
 * come from the composed driver; CIE integration and corrections are bounded
 * stand-ins. No transport, instrument or product executable is linked. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "codes.inc"

typedef int i1pro_code;
typedef int i1pro3_code;
typedef int munki_code;
typedef int instClamping;
typedef int icxClamping;
typedef struct {
	int spec_n;
	double spec_wl_short, spec_wl_long, norm;
	double spec[XSPECT_MAX_BANDS];
} xspect;
typedef struct {
	char loc[16];
	int mtype, mcond, XYZ_v;
	double XYZ[3], duration;
	xspect sp;
} ipatch;
typedef struct { int emiss, ambient, flash, trans; double min_wl; } cm_state;
typedef struct {
    int mmode, highres, capabilities2, custfilt_en, spec_en;
    int target_calstd, native_calstd, filt;
#if FAMILY == 3
    int nwav;
    double wl_short, wl_long;
#else
    int nwav[2];
	double wl_short[2], wl_long[2];
#endif
	xspect custfilt;
    cm_state ms[1];
} cm_imp;
typedef struct { void *m; } cm_device;
#if FAMILY == 1
typedef cm_state i1pro_state;
typedef cm_imp i1proimp;
typedef cm_device i1pro;
#define CM_OK I1PRO_OK
#define CM_INCONS I1PRO_RD_READINCONS
#define CM_FILTER I1PRO_INT_CIECONVFAIL
#define CM_CONVERT(p,v,n,r) i1pro_conv2XYZ(p,v,n,r,0)
#elif FAMILY == 2
typedef cm_state i1pro3_state;
typedef cm_imp i1pro3imp;
typedef cm_device i1pro3;
#define CM_OK I1PRO3_OK
#define CM_INCONS I1PRO3_RD_READINCONS
#define CM_FILTER I1PRO3_INT_CIECONVFAIL
#define CM_CONVERT(p,v,n,r) i1pro3_conv2XYZ(p,v,n,r,0,0)
#elif FAMILY == 3
typedef cm_state munki_state;
typedef cm_imp munkiimp;
typedef cm_device munki;
#define CM_OK MUNKI_OK
#define CM_INCONS MUNKI_RD_READINCONS
#define CM_FILTER MUNKI_INT_CIECONVFAIL
#define CM_CONVERT(p,v,n,r) munki_conv2XYZ(p,v,n,r,0)
#else
#error Unknown source family
#endif
typedef struct _xsp2cie {
	void (*convert)(struct _xsp2cie *, double *, xspect *);
	void (*del)(struct _xsp2cie *);
} xsp2cie;

#define XSPECT_WL(s,l,n,i) ((s) + (i) * ((l) - (s)) / ((n) - 1.0))
#define inst_mrt_none 0
#define inst_mrt_ambient_flash 1
#define inst_mrt_ambient 2
#define inst_mrt_emission_flash 3
#define inst_mrt_emission 4
#define inst_mrt_transmissive 5
#define inst_mrt_reflective 6
#define inst_mrc_none 0
#define inst_mrc_D50 1
#define inst_mrc_uvcut 2
#define inst_mrc_pol 3
#define inst_opt_filter_D50 1
#define inst_opt_filter_UVCut 2
#define inst_opt_filter_pol 3
#define I1PRO_CAP2_UV_FILT 1
#define icxIT_none 0
#define icxIT_D50 1
#define icxOT_CIE_1931_2 0
#define icSigXYZData 0
#define xcalstd_nonpol 0
#define a1logd(...) ((void)0)

static int conversions, deletions, xrga_calls, filter_calls;
static int fault_row, sum_xyz, poison_xrga, poison_filter, refuse_filter, reflective;
static void convert_stub(xsp2cie *self, double *xyz, xspect *sp) {
	(void)self;
	conversions++;
	xyz[0] = sum_xyz ? sp->spec[0] + sp->spec[1] : sp->spec[0];
	xyz[1] = sp->spec[0];
	xyz[2] = sp->spec[1];
}
static void delete_stub(xsp2cie *self) { (void)self; deletions++; }
static xsp2cie converter = { convert_stub, delete_stub };
#define new_xsp2cie(...) (&converter)
static int xrga_stub(ipatch *values) {
	xrga_calls++;
    if (poison_xrga && reflective) values[fault_row].XYZ[2] = INFINITY;
	return 0;
}
#define ipatch_convert_xrga(values,...) xrga_stub(values)
static int filter_stub(ipatch *values, int count, xspect *filter, instClamping clamp) {
	(void)count; (void)filter; (void)clamp;
	filter_calls++;
    if (poison_filter) values[fault_row].XYZ[0] = INFINITY;
    return refuse_filter;
}
#define ipatch_convert_custom_filter(...) filter_stub(__VA_ARGS__)

#include "weights.inc"
#include "converter.inc"

static int failures;
static void expect(const char *name, int condition) {
	if (!condition) {
		printf("FAIL %s\n", name);
		failures++;
	}
}
static void one(const char *name) {
    cm_imp state;
    cm_device meter;
    ipatch result[2];
    double band[2][2] = {{1.0, 2.0}, {3.0, 4.0}};
    double *rows[2] = {band[0], band[1]};
    int rc, invalid = 0, j, i, finite = 1;
    int count = strstr(name, "second") || strstr(name, "two") ? 2 : 1;
    int before_cie = 0, before_correction = 0;
	memset(&state, 0, sizeof state);
	memset(&result, 0, sizeof result);
	meter.m = &state;
#if FAMILY == 3
    state.nwav = 2;
    state.wl_short = 380.0;
    state.wl_long = 780.0;
#else
    state.nwav[0] = 2;
    state.wl_short[0] = 380.0;
    state.wl_long[0] = 780.0;
#endif
	state.spec_en = 1;
	state.ms[0].emiss = 1;
	conversions = deletions = xrga_calls = filter_calls = 0;
    sum_xyz = poison_xrga = poison_filter = refuse_filter = 0;
    fault_row = strstr(name, "second") ? 1 : 0;
    if (!strcmp(name, "normal") || !strcmp(name, "normal-two")) {
		/* Valid emissive control. */
    } else if (!strcmp(name, "normal-xyz-only") || !strcmp(name, "normal-xyz-only-two")) {
		state.spec_en = 0;
    } else if (!strcmp(name, "normal-reflectance")) {
		state.ms[0].emiss = 0;
    } else if (!strcmp(name, "negative-emissive-two")) {
        band[0][0] = band[1][0] = -0.25;
    } else if (!strcmp(name, "negative-reflectance-two")) {
        state.ms[0].emiss = 0;
        band[0][0] = band[1][0] = -0.25;
    } else if (!strcmp(name, "zero-two")) {
        memset(band, 0, sizeof band);
    } else if (!strcmp(name, "filter-refusal-two")) {
        state.custfilt_en = refuse_filter = 1;
    } else if (strstr(name, "nonfinite-input")) {
        band[fault_row][0] = INFINITY; invalid = before_cie = 1;
    } else if (strstr(name, "reflectance-scale-overflow")) {
		state.ms[0].emiss = 0;
        band[fault_row][0] = band[fault_row][1] = 1e307;
        invalid = before_cie = 1;
    } else if (strstr(name, "integration-overflow")) {
        band[fault_row][0] = band[fault_row][1] = 1e308;
        sum_xyz = 1; invalid = before_correction = 1;
    } else if (strstr(name, "xyz-percent-overflow")) {
		state.ms[0].emiss = 0;
        band[fault_row][0] = band[fault_row][1] = 1e305;
        sum_xyz = 1; invalid = before_correction = 1;
    } else if (strstr(name, "post-xrga-overflow")) {
        /* The real XRGA helper changes reflective data only. */
        state.ms[0].emiss = 0;
		poison_xrga = 1; invalid = 1;
    } else if (strstr(name, "post-filter-overflow")) {
		state.custfilt_en = poison_filter = 1; invalid = 1;
    } else {
        expect("known case", 0);
        return;
	}
    reflective = !state.ms[0].emiss;
    before_correction |= before_cie;
    rc = CM_CONVERT(&meter, result, count, rows);
    for (i = 0; i < count; i++) {
        for (j = 0; j < result[i].sp.spec_n; j++)
            if (!isfinite(result[i].sp.spec[j])) finite = 0;
        if (result[i].XYZ_v)
            for (j = 0; j < 3; j++)
                if (!isfinite(result[i].XYZ[j])) finite = 0;
    }
	if (invalid) {
        expect(name, rc == CM_INCONS && deletions == 1
               && conversions == (before_cie ? fault_row : fault_row + 1)
		       && xrga_calls == (before_correction ? 0 : 1)
               && filter_calls == (poison_filter ? 1 : 0));
	} else {
        expect(name, rc == (refuse_filter ? CM_FILTER : CM_OK) && deletions == 1
               && conversions == count && xrga_calls == 1
               && filter_calls == refuse_filter && finite);
        for (i = 0; i < count; i++) {
            expect(name, result[i].XYZ_v
                   && (refuse_filter || result[i].sp.spec_n == (state.spec_en ? 2 : 0)));
            printf("CONTROL %s %d %a %a %a %a\n", name, i,
                   result[i].sp.spec[0], result[i].sp.spec[1], result[i].XYZ[0], result[i].XYZ[1]);
        }
	}
}
int main(void) {
	static const char *cases[] = {
        "normal", "normal-xyz-only", "normal-reflectance", "normal-two",
        "normal-xyz-only-two", "negative-emissive-two", "negative-reflectance-two",
        "zero-two", "filter-refusal-two",
		"nonfinite-input", "reflectance-scale-overflow", "integration-overflow",
        "xyz-percent-overflow", "post-xrga-overflow", "post-filter-overflow",
        "nonfinite-input-second", "reflectance-scale-overflow-second",
        "integration-overflow-second", "xyz-percent-overflow-second",
        "post-xrga-overflow-second", "post-filter-overflow-second"
	};
	size_t i;
	for (i = 0; i < sizeof cases / sizeof cases[0]; i++) one(cases[i]);
	printf("%d failures\n", failures);
	return failures != 0;
}
