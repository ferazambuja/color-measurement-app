/* XYZ-only conversion probe: one driver's pinned spectral-to-XYZ transfer
 * (CM_DRIVER_I1PRO, CM_DRIVER_I1PRO3 or CM_DRIVER_MUNKI), original or repaired,
 * over xrga.c's pinned conversion and inst.c's pinned custom filter, with the
 * EBU TLCI ColorChecker reflectances of xicc/xspect.c as the raw spectra.
 *
 * Stand-ins, both texts alike: the spectral-to-XYZ integrator is a
 * deterministic linear functional of the spectrum (no colorimetric XYZ is
 * claimed). Equal XYZ alone does not prove equal spectra; the probe separately
 * compares the full active spectrum and grid given to the integrator. The filter lookup
 * returns the nearest band, and debug logging is discarded before its
 * arguments are evaluated. The requested standard and the custom filter are
 * stored as the option handlers store them. No transport, instrument or file
 * is touched.
 *
 * stdout: one JSON object; stderr: every check. */
#include "extracted.inc"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define NSAMP 24			/* TLCI_2012_TCS[] has 24 ColorChecker patches */
#define NWAV 36				/* 380..730 nm at 10 nm: the standard-resolution grid */

/* ------------------------------------------------------------------ */
/* Check accounting: failures are counted by id for the JSON.          */

#define MAX_IDS 32
static struct { const char *id; int failures; } g_ids[MAX_IDS];
static int g_nids, g_checks, g_failures;

static void check(const char *id, int ok, const char *detail) {
	int k;
	g_checks++;
	fprintf(stderr, "%s %s: %s\n", ok ? "PASS" : "FAIL", id, detail);
	if (ok)
		return;
	g_failures++;
	for (k = 0; k < g_nids; k++) {
		if (strcmp(g_ids[k].id, id) == 0) {
			g_ids[k].failures++;
			return;
		}
	}
	if (g_nids == MAX_IDS) {
		fprintf(stderr, "too many failing ids\n");
		exit(2);
	}
	g_ids[g_nids].id = id;
	g_ids[g_nids].failures = 1;
	g_nids++;
}

static int xyz_eq(const double a[3], const double b[3]) {
	return memcmp(a, b, 3 * sizeof(double)) == 0;
}

static int sp_eq(const xspect *a, const xspect *b) {
	if (a->spec_n != b->spec_n || a->spec_wl_short != b->spec_wl_short
	 || a->spec_wl_long != b->spec_wl_long || a->norm != b->norm)
		return 0;
	return memcmp(a->spec, b->spec, a->spec_n * sizeof(double)) == 0;
}

/* ------------------------------------------------------------------ */
/* Stand-ins                                                           */

static int g_integrations, g_objects, g_deletes, g_lookups, g_unclamped_integrators;
static icxIllumeType g_illuminant;

/* Retain the last integration batch, including its grid and normalization.
 * A converter integrates an entire batch after the initial native integration;
 * the ring therefore retains its final inputs for comparison between modes. */
static xspect g_integrated[NSAMP];
static int g_input_index;

/* A non-injective linear projection, not a colorimetric integration.
 * Compensating band changes can produce equal outputs, hence input capture. */
static void spy_convert(xsp2cie *self, double *out, xspect *in) {
	int k;
	double a = 0.0, b = 0.0, c = 0.0;
	g_integrated[g_input_index++ % NSAMP] = *in;
	for (k = 0; k < in->spec_n; k++) {
		a += in->spec[k] * (k + 1);
		b += in->spec[k];
		c += in->spec[k] * (in->spec_n - k);
	}
	out[0] = a / (in->spec_n * in->norm);
	out[1] = b / (in->spec_n * in->norm);
	out[2] = c / (in->spec_n * in->norm);
	g_integrations++;
}

static void spy_del(xsp2cie *self) { g_deletes++; }

xsp2cie *new_xsp2cie(icxIllumeType ilType, double temp, xspect *custIllum,
                     icxObserverType obType, xspect custObserver[3],
                     icColorSpaceSignature rcs, icxClamping clamp) {
	static xsp2cie spy;
	memset(&spy, 0, sizeof(spy));
	spy.convert = spy_convert;
	spy.del = spy_del;
	g_objects++;
	if (clamp == icxNoClamp) g_unclamped_integrators++;
	g_illuminant = ilType;
	check("integrator_request", temp == 0.0 && custIllum == NULL && obType == icxOT_CIE_1931_2
	      && custObserver == NULL && rcs == icSigXYZData
	      && (clamp == icxClamp || (CM_PRODUCTION && ilType == icxIT_none && clamp == icxNoClamp)),
	      "CIE 1931 2 degree, XYZ; unclamped only for production filter prevalidation");
	return &spy;
}

/* Nearest band of the filter spectrum; upstream interpolates. */
int getval_xspec(xspect *sp, double *rv, double wl) {
	double f = (wl - sp->spec_wl_short) / (sp->spec_wl_long - sp->spec_wl_short);
	int k = (int)(f * (sp->spec_n - 1) + 0.5);
	if (k < 0)
		k = 0;
	if (k >= sp->spec_n)
		k = sp->spec_n - 1;
	*rv = sp->spec[k];
	g_lookups++;
	return 0;
}

#include "conversion.inc"	/* xrga.c: equations, convert_xrga, xspec_ and ipatch_convert_xrga */
#include "filter.inc"		/* inst.c: ipatch_convert_custom_filter */
#include "samples.inc"		/* xicc/xspect.c: TLCI_2012_TCS[] */

/* ------------------------------------------------------------------ */
/* Driver glue                                                         */

typedef struct {
	const char *label;
	xcalstd native;		/* what *_imp_init assigns to native_calstd */
	xcalstd target;		/* what inst_opt_set_xcalstd stores */
} cm_config;

enum { KIND_REFL, KIND_EMIS, KIND_TRANS };

#if defined(CM_DRIVER_I1PRO)
typedef i1pro cm_inst;
typedef i1proimp cm_imp;
typedef i1pro_state cm_state;
#define CM_NAME "i1pro"
#define CM_OK I1PRO_OK
#define CM_REFL i1p_refl_spot
#define CM_EMIS i1p_emiss_spot
#define CM_TRANS i1p_trans_spot
#define CM_MIN_WL 0.0				/* i1pro_imp.c:1126: no minimum at standard resolution */
static int cm_convert(cm_inst *p, ipatch *vals, int n, double **specrd) {
	return i1pro_conv2XYZ(p, vals, n, specrd, instClamp);
}
static void cm_grid(cm_imp *m) {		/* i1pro_imp.c:717-718 */
	m->highres = 0;
	m->nwav[0] = NWAV;
	m->wl_short[0] = 380.0;
	m->wl_long[0] = 730.0;
}
static const cm_config cm_configs[] = {
	{ "Rev A-D native GMDI", xcalstd_gmdi, xcalstd_xrga },	/* i1pro_imp.c:505 */
	{ "Rev A-D native GMDI", xcalstd_gmdi, xcalstd_xrdi },
	{ "Rev E native XRGA", xcalstd_xrga, xcalstd_xrdi },	/* i1pro_imp.c:507 */
	{ "Rev E native XRGA", xcalstd_xrga, xcalstd_gmdi },
};
#elif defined(CM_DRIVER_I1PRO3)
typedef i1pro3 cm_inst;
typedef i1pro3imp cm_imp;
typedef i1pro3_state cm_state;
#define CM_NAME "i1pro3"
#define CM_OK I1PRO3_OK
#define CM_REFL i1p3_refl_spot
#define CM_EMIS i1p3_emiss_spot
#define CM_TRANS i1p3_trans_spot
#define CM_MIN_WL HIGHRES_REF_MIN	/* i1pro3_imp.c:695, 380 nm: the grid's first band */
static int cm_convert(cm_inst *p, ipatch *vals, int n, double **specrd) {
	return i1pro3_conv2XYZ(p, vals, n, specrd, 0, instClamp);
}
static void cm_grid(cm_imp *m) {		/* i1pro3_imp.c:6860-6863 */
	m->highres = 0;
	m->nwav[0] = NWAV;
	m->wl_short[0] = 380.0;
	m->wl_long[0] = 730.0;
	m->filt = inst_opt_filter_none;
}
static const cm_config cm_configs[] = {
	{ "native XRGA", xcalstd_xrga, xcalstd_xrdi },			/* i1pro3_imp.c:558 */
	{ "native XRGA", xcalstd_xrga, xcalstd_gmdi },
};
#elif defined(CM_DRIVER_MUNKI)
typedef munki cm_inst;
typedef munkiimp cm_imp;
typedef munki_state cm_state;
#define CM_NAME "munki"
#define CM_OK MUNKI_OK
#define CM_REFL mk_refl_spot
#define CM_EMIS mk_emiss_spot
#define CM_TRANS mk_trans_spot
static int cm_convert(cm_inst *p, ipatch *vals, int n, double **specrd) {
	return munki_conv2XYZ(p, vals, n, specrd, instClamp);
}
static void cm_grid(cm_imp *m) {		/* munki_imp.c:9399-9401 */
	m->nwav = NWAV;
	m->wl_short = 380.0;
	m->wl_long = 730.0;
}
static const cm_config cm_configs[] = {
	{ "native XRGA", xcalstd_xrga, xcalstd_xrdi },			/* munki_imp.c:513 */
	{ "native XRGA", xcalstd_xrga, xcalstd_gmdi },
};
#else
#error "define CM_DRIVER_I1PRO, CM_DRIVER_I1PRO3 or CM_DRIVER_MUNKI"
#endif
#define NCONFIG (int)(sizeof(cm_configs) / sizeof(cm_configs[0]))

static const char *calstd(xcalstd s) {
	switch (s) {
		case xcalstd_none: return "none";
		case xcalstd_native: return "native";
		case xcalstd_xrdi: return "XRDI";
		case xcalstd_gmdi: return "GMDI";
		case xcalstd_xrga: return "XRGA";
	}
	return "?";
}

/* Select a measurement state and spec_en as *_imp_set_mode does
 * (i1pro_imp.c:1534, i1pro3_imp.c:1009, munki_imp.c:916). */
static void cm_select(cm_imp *m, int kind, int spectral) {
	int index = kind == KIND_REFL ? CM_REFL : kind == KIND_EMIS ? CM_EMIS : CM_TRANS;
	cm_state *s = &m->ms[index];
	m->mmode = index;
	s->reflective = kind == KIND_REFL;
	s->emiss = kind == KIND_EMIS;
	s->trans = kind == KIND_TRANS;
	s->ambient = 0;
	s->flash = 0;
#ifdef CM_MIN_WL
	s->min_wl = CM_MIN_WL;
#endif
	m->spec_en = spectral;
}

/* ------------------------------------------------------------------ */
/* Inputs                                                              */

static double g_rows[NSAMP][NWAV];
static double *g_specrd[NSAMP];
static xspect g_filter;

static void cm_inputs(void) {
	int s, j;
	for (s = 0; s < NSAMP; s++) {
		xspect *t = &TLCI_2012_TCS[s];
		if (t->spec_n != 77 || t->spec_wl_short != 380.0 || t->spec_wl_long != 760.0
		 || t->norm != 1.0) {
			fprintf(stderr, "unexpected TLCI table layout\n");
			exit(2);
		}
		for (j = 0; j < NWAV; j++)
			g_rows[s][j] = t->spec[2 * j];		/* 380 + 10j nm is table index 2j */
		g_specrd[s] = g_rows[s];
	}
	/* A synthetic, smoothly varying filter transmission; not a measured filter. */
	g_filter.spec_n = NWAV;
	g_filter.spec_wl_short = 380.0;
	g_filter.spec_wl_long = 730.0;
	g_filter.norm = 1.0;
	for (j = 0; j < NWAV; j++)
		g_filter.spec[j] = 0.8 + 0.4 * j / (NWAV - 1.0);
}

/* ------------------------------------------------------------------ */
/* Reads                                                               */

static ipatch g_A[NSAMP], g_B[NSAMP], g_C[NSAMP], g_D[NSAMP], g_S0[NSAMP], g_S1[NSAMP];
static ipatch g_E0[NSAMP], g_E1[NSAMP], g_E2[NSAMP], g_E3[NSAMP];
static ipatch g_T0[NSAMP], g_T1[NSAMP], g_T2[NSAMP], g_T3[NSAMP];
static ipatch g_F0[NSAMP], g_F1[NSAMP], g_FR[NSAMP];
static int g_reads;
static xspect g_target_spectral_inputs[NSAMP], g_target_xyz_inputs[NSAMP];
static xspect g_filter_spectral_inputs[NSAMP], g_filter_xyz_inputs[NSAMP];

static void cm_read(cm_inst *p, int kind, int spectral, xcalstd target, int filtered,
                    ipatch *out, const char *tag) {
	cm_imp *m = (cm_imp *)p->m;
	int ev;
	cm_select(m, kind, spectral);
	m->target_calstd = target;		/* inst_opt_set_xcalstd: i1pro.c:830, i1pro3.c:895, munki.c:831 */
	m->custfilt_en = filtered;		/* inst_opt_set_custom_filter: i1pro.c:868-869, i1pro3.c:931-932, */
	if (filtered)					/* munki.c:869-870 */
		m->custfilt = g_filter;
	else
		m->custfilt.spec_n = 0;
	memset(out, 0xA5, NSAMP * sizeof(ipatch));	/* every output must be written */
	g_input_index = 0;
	ev = cm_convert(p, out, NSAMP, g_specrd);
	if (out == g_C) memcpy(g_target_spectral_inputs, g_integrated, sizeof(g_integrated));
	if (out == g_D) memcpy(g_target_xyz_inputs, g_integrated, sizeof(g_integrated));
	if (out == g_F0) memcpy(g_filter_spectral_inputs, g_integrated, sizeof(g_integrated));
	if (out == g_F1) memcpy(g_filter_xyz_inputs, g_integrated, sizeof(g_integrated));
	g_reads++;
	check("transfer_returns_ok", ev == CM_OK, tag);
	check("integrator_illuminant", g_illuminant == (kind == KIND_EMIS ? icxIT_none : icxIT_D50),
	      tag);
}

/* Upstream's public conversion of the native spectrum, integrated the same way. */
static void cm_reference(xspect *out, double xyz[3], xspect *native, xcalstd target,
                         xcalstd native_std) {
	xsp2cie *conv;
	xspec_convert_xrga(out, native, xcalstd_nonpol, target, native_std);
	conv = new_xsp2cie(icxIT_D50, 0.0, NULL, icxOT_CIE_1931_2, NULL, icSigXYZData, icxClamp);
	conv->convert(conv, xyz, out);
	xyz[0] *= 100.0;
	xyz[1] *= 100.0;
	xyz[2] *= 100.0;
	conv->del(conv);
}

static int g_target_skipped, g_target_converted, g_target_other;
static int g_filter_skipped, g_filter_applied, g_filter_other;

static void cm_standard_cases(cm_inst *p) {
	cm_imp *m = (cm_imp *)p->m;
	int k, s;
	for (k = 0; k < NCONFIG; k++) {
		const cm_config *cf = &cm_configs[k];
		char cfg[96];
		snprintf(cfg, sizeof(cfg), "%s -> %s", cf->label, calstd(cf->target));
		m->native_calstd = cf->native;	/* *_imp_init: i1pro_imp.c:505-508, i1pro3_imp.c:558, munki_imp.c:513 */

		cm_read(p, KIND_REFL, 0, xcalstd_native, 0, g_A, "refl XYZ-only native");
		cm_read(p, KIND_REFL, 1, xcalstd_native, 0, g_B, "refl spectral native");
		cm_read(p, KIND_REFL, 1, cf->target, 0, g_C, "refl spectral target");
		cm_read(p, KIND_REFL, 0, cf->target, 0, g_D, "refl XYZ-only target");
		cm_read(p, KIND_REFL, 0, cf->native, 0, g_S0, "refl XYZ-only same standard");
		cm_read(p, KIND_REFL, 1, cf->native, 0, g_S1, "refl spectral same standard");
		cm_read(p, KIND_EMIS, 0, xcalstd_native, 0, g_E0, "emis XYZ-only native");
		cm_read(p, KIND_EMIS, 0, cf->target, 0, g_E1, "emis XYZ-only target");
		cm_read(p, KIND_EMIS, 1, xcalstd_native, 0, g_E2, "emis spectral native");
		cm_read(p, KIND_EMIS, 1, cf->target, 0, g_E3, "emis spectral target");
		cm_read(p, KIND_TRANS, 0, xcalstd_native, 0, g_T0, "trans XYZ-only native");
		cm_read(p, KIND_TRANS, 0, cf->target, 0, g_T1, "trans XYZ-only target");
		cm_read(p, KIND_TRANS, 1, xcalstd_native, 0, g_T2, "trans spectral native");
		cm_read(p, KIND_TRANS, 1, cf->target, 0, g_T3, "trans spectral target");

		for (s = 0; s < NSAMP; s++) {
			xspect ref;
			double refxyz[3];
			char detail[160];
			snprintf(detail, sizeof(detail), "%s, sample %d", cfg, s + 1);

			/* Output shape: XYZ-only reads never carry a spectrum, spectral reads do. */
			check("xyz_only_reads_carry_no_spectrum",
			      g_A[s].sp.spec_n == 0 && g_D[s].sp.spec_n == 0 && g_S0[s].sp.spec_n == 0
			      && g_E0[s].sp.spec_n == 0 && g_E1[s].sp.spec_n == 0
			      && g_T0[s].sp.spec_n == 0 && g_T1[s].sp.spec_n == 0, detail);
			check("spectral_reads_carry_the_spectrum",
			      g_B[s].sp.spec_n == NWAV && g_C[s].sp.spec_n == NWAV
			      && g_S1[s].sp.spec_n == NWAV && g_E2[s].sp.spec_n == NWAV
			      && g_E3[s].sp.spec_n == NWAV && g_T2[s].sp.spec_n == NWAV
			      && g_T3[s].sp.spec_n == NWAV, detail);
			check("measurement_types",
			      g_A[s].mtype == inst_mrt_reflective && g_B[s].mtype == inst_mrt_reflective
			      && g_C[s].mtype == inst_mrt_reflective && g_D[s].mtype == inst_mrt_reflective
			      && g_E0[s].mtype == inst_mrt_emission && g_T0[s].mtype == inst_mrt_transmissive
			      && g_A[s].XYZ_v == 1 && g_B[s].XYZ_v == 1 && g_C[s].XYZ_v == 1
			      && g_D[s].XYZ_v == 1 && g_A[s].loc[0] == '\0' && g_A[s].duration == 0.0,
			      detail);

			/* Controls, equal in both texts. */
			check("native_xyz_mode_invariant", xyz_eq(g_A[s].XYZ, g_B[s].XYZ), detail);
			cm_reference(&ref, refxyz, &g_B[s].sp, cf->target, cf->native);
			check("spectral_target_equals_public_conversion",
			      xyz_eq(g_C[s].XYZ, refxyz) && sp_eq(&g_C[s].sp, &ref), detail);
			check("conversion_changes_xyz", !xyz_eq(g_C[s].XYZ, g_B[s].XYZ), detail);
			check("same_standard_no_conversion",
			      xyz_eq(g_S0[s].XYZ, g_A[s].XYZ) && xyz_eq(g_S1[s].XYZ, g_B[s].XYZ)
			      && sp_eq(&g_S1[s].sp, &g_B[s].sp), detail);
			check("emissive_not_converted",
			      xyz_eq(g_E1[s].XYZ, g_E0[s].XYZ) && xyz_eq(g_E3[s].XYZ, g_E2[s].XYZ)
			      && sp_eq(&g_E3[s].sp, &g_E2[s].sp), detail);
			check("transmissive_not_converted",
			      xyz_eq(g_T1[s].XYZ, g_T0[s].XYZ) && xyz_eq(g_T3[s].XYZ, g_T2[s].XYZ)
			      && sp_eq(&g_T3[s].sp, &g_T2[s].sp), detail);

			/* The question: an XYZ-only reflective read with a requested standard
			 * carries the converted XYZ, the value the spectral read carries. */
			check("xyz_only_target_converted", xyz_eq(g_D[s].XYZ, g_C[s].XYZ)
			      && sp_eq(&g_target_xyz_inputs[s], &g_target_spectral_inputs[s]), detail);
			if (xyz_eq(g_D[s].XYZ, g_C[s].XYZ))
				g_target_converted++;
			else if (xyz_eq(g_D[s].XYZ, g_A[s].XYZ))
				g_target_skipped++;
			else
				g_target_other++;
		}
	}
}

/* inst.c:2198-2202 applies the custom filter to emission types with a spectrum. */
static void cm_filter_cases(cm_inst *p) {
	cm_imp *m = (cm_imp *)p->m;
	int s;
	m->native_calstd = cm_configs[0].native;
	cm_read(p, KIND_EMIS, 0, xcalstd_native, 0, g_E0, "emis XYZ-only unfiltered");
	cm_read(p, KIND_EMIS, 1, xcalstd_native, 0, g_E2, "emis spectral unfiltered");
	cm_read(p, KIND_EMIS, 1, xcalstd_native, 1, g_F0, "emis spectral filtered");
	cm_read(p, KIND_EMIS, 0, xcalstd_native, 1, g_F1, "emis XYZ-only filtered");
	cm_read(p, KIND_REFL, 1, xcalstd_native, 0, g_B, "refl spectral unfiltered");
	cm_read(p, KIND_REFL, 1, xcalstd_native, 1, g_FR, "refl spectral filtered");
	for (s = 0; s < NSAMP; s++) {
		ipatch copy = g_E2[s];
		char detail[64];
		snprintf(detail, sizeof(detail), "filter, sample %d", s + 1);
		check("filter_changes_xyz",
		      !xyz_eq(g_F0[s].XYZ, g_E2[s].XYZ) && !sp_eq(&g_F0[s].sp, &g_E2[s].sp), detail);
		ipatch_convert_custom_filter(&copy, 1, &g_filter, instClamp);
		check("filter_equals_public_filter",
		      xyz_eq(copy.XYZ, g_F0[s].XYZ) && sp_eq(&copy.sp, &g_F0[s].sp), detail);
		check("filter_not_applied_to_reflective",
		      xyz_eq(g_FR[s].XYZ, g_B[s].XYZ) && sp_eq(&g_FR[s].sp, &g_B[s].sp), detail);
		check("xyz_only_reads_carry_no_spectrum", g_F1[s].sp.spec_n == 0 && g_E0[s].sp.spec_n == 0,
		      detail);

		/* The question: an XYZ-only emissive read with a custom filter carries the
		 * filtered XYZ, the value the spectral read carries. */
		check("xyz_only_filter_applied", xyz_eq(g_F1[s].XYZ, g_F0[s].XYZ)
		      && sp_eq(&g_filter_xyz_inputs[s], &g_filter_spectral_inputs[s]), detail);
		if (xyz_eq(g_F1[s].XYZ, g_F0[s].XYZ))
			g_filter_applied++;
		else if (xyz_eq(g_F1[s].XYZ, g_E0[s].XYZ))
			g_filter_skipped++;
		else
			g_filter_other++;
	}
}

int main(void) {
	static cm_inst inst_obj;
	static cm_imp imp;
	int k;

	memset(&inst_obj, 0, sizeof(inst_obj));
	memset(&imp, 0, sizeof(imp));
	inst_obj.m = (void *)&imp;
	cm_grid(&imp);
	cm_inputs();

	/* Actual stand-in counterexample: equal outputs do not identify a spectrum.
	 * Verify the capture preserves the difference before testing the drivers. */
	{
		xspect a = {0}, b;
		double av[3], bv[3];
		a.spec_n = NWAV; a.norm = 1.0;
		for (k = 0; k < NWAV; k++) a.spec[k] = 1.0;
		b = a;
		b.spec[0] += 0.125; b.spec[1] -= 0.25; b.spec[2] += 0.125;
		spy_convert(NULL, av, &a); spy_convert(NULL, bv, &b);
		check("projection_collision_inputs_preserved", xyz_eq(av, bv)
		      && sp_eq(&g_integrated[0], &a) && sp_eq(&g_integrated[1], &b)
		      && !sp_eq(&g_integrated[0], &g_integrated[1]), "three-band cancellation");
	}

	cm_standard_cases(&inst_obj);
	cm_filter_cases(&inst_obj);
	check("integrator_objects_released", g_objects == g_deletes && g_objects > 0, "new/del");

	printf("{\"driver\":\"%s\",\"checks\":%d,\"failures\":%d,\"failed\":{",
	       CM_NAME, g_checks, g_failures);
	for (k = 0; k < g_nids; k++)
		printf("%s\"%s\":%d", k ? "," : "", g_ids[k].id, g_ids[k].failures);
	printf("},\"reads\":%d,\"integrations\":%d,\"integrators\":%d,\"lookups\":%d,\"unclamped_integrators\":%d,"
	       "\"xyz_only_target\":{\"skipped\":%d,\"converted\":%d,\"other\":%d},"
	       "\"xyz_only_filter\":{\"skipped\":%d,\"applied\":%d,\"other\":%d}}\n",
	       g_reads, g_integrations, g_objects, g_lookups, g_unclamped_integrators,
	       g_target_skipped, g_target_converted, g_target_other,
	       g_filter_skipped, g_filter_applied, g_filter_other);
	return g_failures ? 1 : 0;
}
