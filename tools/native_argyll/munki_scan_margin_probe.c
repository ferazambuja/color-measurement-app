/* Synthetic raw arrays exercise the authenticated Argyll 3.5.0 ColorMunki strip
 * recognizer. The generated recognizer.inc retains Graeme W. Gill's
 * GPL-2.0-or-later code. Only numsup and rspl1 are linked: no transport,
 * acquisition, calibration, conversion or instrument runtime.
 *
 * Usage: probe <case> [debuglevel]   or   probe --list
 * stdout: one JSON object; stderr: the recognizer's own log lines. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <fenv.h>
#include "sa_config.h"
#include "numsup.h"
#include "rspl1.h"
#include "cgats.h"
#include "xspect.h"
#include "insttypes.h"
#include "conv.h"
#include "icoms.h"
#include "inst.h"
#include "munki.h"
#include "munki_imp.h"

/* One recognition creates at most one curve. Track its real destructor, as the
 * curve-release proof does, without changing the authenticated recognizer.
 * This earlier margin-only source retains a curve on some exits; release it
 * after observing the result. The later curve-release proof owns that defect. */
static rspl *retained_curve;
static void (*upstream_curve_del)(rspl *);

static void observed_curve_del(rspl *curve) {
	if (curve != retained_curve) abort();
	retained_curve = NULL;
	upstream_curve_del(curve);
}

static rspl *observed_new_rspl(int flags, int di, int fdi) {
	rspl *curve;
	if (retained_curve != NULL) abort();
	curve = new_rspl(flags, di, fdi);
	if (curve != NULL) {
		retained_curve = curve;
		upstream_curve_del = curve->del;
		curve->del = observed_curve_del;
	}
	return curve;
}

#define new_rspl observed_new_rspl
#include "recognizer.inc"
#undef new_rspl

#define NRAW 128            /* raw bands stored, munki_imp.c:9398 */
#define SENTINEL -1234567.0

typedef struct {
	const char *name;
	int count;          /* patches in the synthetic strip */
	int tnpatch;        /* patches requested from the recognizer */
	int width;          /* rows per segment */
	int ramp;           /* 0: sharp step; else linear transition over the last `ramp` rows */
	double lead, trail; /* margin level in every band */
	double patch[8];    /* patch levels; patch p has spectral shape 1 + 0.3 sin(0.1 j + 1.3 p) */
	double lead_lo, trail_lo;  /* if nonzero: margin level in bands 1..BL-1 only. They are
	                              averaged (munki_imp.c:5702) but lie outside the slope
	                              bands BL..BH, so transitions are still found. */
	int negate;         /* negate every value */
	int special;        /* nonfinite injection, see enum */
} spec;

enum { SP_NONE, SP_NAN20_ALL, SP_INF20_ALL, SP_NAN20_LEADER, SP_NAN20_PATCHES, SP_NAN_ALL,
       SP_UNIFORM };

#define P3 {200.0, 600.0, 350.0}
#define P8 {200.0, 610.0, 220.0, 630.0, 240.0, 650.0, 260.0, 670.0}
static const spec cases[] = {
	/* ordinary finite scans; sharp steps unless named _ramp */
	{"valid3",                  3, 3, 100,  0, 1000, 1000, P3, 0, 0, 0, SP_NONE},
	{"valid8",                  8, 8, 100,  0, 1000, 1000, P8, 0, 0, 0, SP_NONE},
	{"reverse3",                3, 3, 100,  0, 1000, 1000, {350.0, 600.0, 200.0}, 0, 0, 0, SP_NONE},
	{"valid3_ramp",             3, 3, 100, 20, 1000, 1000, P3, 0, 0, 0, SP_NONE},
	{"reverse3_ramp",           3, 3, 100, 20, 1000, 1000, {350.0, 600.0, 200.0}, 0, 0, 0, SP_NONE},
	/* finite signed margins */
	{"negative3",               3, 3, 100,  0, 1000, 1000, P3, 0, 0, 1, SP_NONE},
	{"near_zero_same_sign",     3, 3, 100,  0, 1e-3, 1.2e-3, P3, 0, 0, 0, SP_NONE},
	{"near_zero_opposite_sign", 3, 3, 100,  0, 1e-3, -1e-3, P3, 0, 0, 0, SP_NONE},
	{"leader_mismatch",         3, 3, 100,  0, 1000, 400, P3, 0, 0, 0, SP_NONE},
	/* the 0.7 threshold: exactly on it for each ratio, and just below */
	{"boundary_lead_0p7",       3, 3, 100,  0, 700, 1000, P3, 0, 0, 0, SP_NONE},
	{"boundary_trail_0p7",      3, 3, 100,  0, 1000, 700, P3, 0, 0, 0, SP_NONE},
	{"below_lead_0p699",        3, 3, 100,  0, 699, 1000, P3, 0, 0, 0, SP_NONE},
	{"below_trail_0p699",       3, 3, 100,  0, 1000, 699, P3, 0, 0, 0, SP_NONE},
	/* exactly one zero margin: a zero ratio already refuses */
	{"zero_leader_only",        3, 3, 100,  0, 0, 1000, P3, 0, 0, 0, SP_NONE},
	{"zero_trailer_only",       3, 3, 100,  0, 1000, 0, P3, 0, 0, 0, SP_NONE},
	/* counts and minimum sizes */
	{"count_low",               3, 2, 100,  0, 1000, 1000, P3, 0, 0, 0, SP_NONE},
	{"count_high",              3, 4, 100,  0, 1000, 1000, P3, 0, 0, 0, SP_NONE},
	{"valid1",                  1, 1, 100,  0, 1000, 1000, {200.0}, 0, 0, 0, SP_NONE},
	{"valid2",                  2, 2, 100,  0, 1000, 1000, {200.0, 600.0}, 0, 0, 0, SP_NONE},
	{"sparse10",                3, 3,  10,  0, 1000, 1000, P3, 0, 0, 0, SP_NONE},
	{"sparse5",                 3, 3,   5,  0, 1000, 1000, P3, 0, 0, 0, SP_NONE},
	{"uniform",                 3, 3, 100,  0, 300, 300, {300.0, 300.0, 300.0}, 0, 0, 0, SP_UNIFORM},
	{"all_nan",                 3, 3, 100,  0, 1000, 1000, P3, 0, 0, 0, SP_NAN_ALL},
	/* undefined agreement */
	{"zero_margins3",           3, 3, 100,  0, 0, 0, P3, 0, 0, 0, SP_NONE},
	{"zero_margins1",           1, 1, 100,  0, 0, 0, {200.0}, 0, 0, 0, SP_NONE},
	{"zero_margins3_ramp",      3, 3, 100, 20, 0, 0, P3, 0, 0, 0, SP_NONE},
	{"nan_band20",              3, 3, 100,  0, 1000, 1000, P3, 0, 0, 0, SP_NAN20_ALL},
	{"inf_band20",              3, 3, 100,  0, 1000, 1000, P3, 0, 0, 0, SP_INF20_ALL},
	{"nan_leader_masks_mismatch", 3, 3, 100, 0, 1000, 400, P3, 0, 0, 0, SP_NAN20_LEADER},
	{"overflow_masks_mismatch", 3, 3, 100,  0, 1000, 1000, P3, 1e306, 4e305, 0, SP_NONE},
	{"overflow_agree",          3, 3, 100,  0, 1000, 1000, P3, 1e306, 1e306, 0, SP_NONE},
	{"large_no_overflow_mismatch", 3, 3, 100, 0, 1000, 1000, P3, 1e300, 4e299, 0, SP_NONE},
	{"nan_patch_rows_only",     3, 3, 100,  0, 1000, 1000, P3, 0, 0, 0, SP_NAN20_PATCHES},
};
#define NCASES ((int)(sizeof(cases) / sizeof(cases[0])))

/* Value of segment seg (0 leader, 1..count patches, count+1 trailer) in band j. */
static double segval(const spec *c, int seg, int j) {
	if (seg == 0)
		return (c->lead_lo != 0.0 && j >= 1 && j < BL) ? c->lead_lo : c->lead;
	if (seg == c->count + 1)
		return (c->trail_lo != 0.0 && j >= 1 && j < BL) ? c->trail_lo : c->trail;
	return c->patch[seg - 1] * (1.0 + 0.3 * sin(0.1 * j + 1.3 * (seg - 1)));
}
static void number(double x) {
	if (isnan(x)) printf("\"NaN\"");
	else if (!isfinite(x)) printf(x > 0 ? "\"Infinity\"" : "\"-Infinity\"");
	else printf("%.17g", x);
}

int main(int argc, char **argv) {
	const spec *c = NULL;
	int i, j, p, rows, code, flags = -999, fp;
	int nonfinite_in = 0, nonfinite_out = 0, changed = 0, compared = 0;
	double **in, **out, maxrel = 0.0;
	munki meter; munkiimp impl;

	if (argc == 2 && strcmp(argv[1], "--list") == 0) {
		for (i = 0; i < NCASES; i++) puts(cases[i].name);
		return 0;
	}
	if (argc < 2 || argc > 3) return 2;
	for (i = 0; i < NCASES; i++)
		if (strcmp(argv[1], cases[i].name) == 0) c = &cases[i];
	if (c == NULL) return 3;
	memset(&meter, 0, sizeof(meter));
	memset(&impl, 0, sizeof(impl));
	meter.m = &impl;
	meter.log = g_log;
	g_log->debug = argc == 3 ? atoi(argv[2]) : 0;
	impl.nraw = NRAW;
	impl.scan_toll_ratio = 1.0;      /* munki_imp.c:534 default */

	rows = (c->count + 2) * c->width;
	in = dmatrixz(0, rows - 1, -1, NRAW - 1);
	out = dmatrix(0, c->tnpatch - 1, -1, NRAW - 1);
	for (p = 0; p < c->tnpatch; p++)
		for (j = -1; j < NRAW; j++) out[p][j] = SENTINEL;

	for (i = 0; i < rows; i++) {
		int seg = i / c->width, within = i % c->width;
		for (j = 0; j < NRAW; j++) {
			double x = segval(c, seg, j);
			if (c->ramp > 0 && seg < c->count + 1 && within >= c->width - c->ramp)
				x += (segval(c, seg + 1, j) - x) * (within - c->width + c->ramp) / (double)c->ramp;
			if (c->negate) x = -x;
			if (c->special == SP_NAN_ALL) x = NAN;
			if (c->special == SP_UNIFORM) x = 300.0;
			if (j == 20 && c->special == SP_NAN20_ALL) x = NAN;
			if (j == 20 && c->special == SP_INF20_ALL) x = INFINITY;
			if (j == 20 && c->special == SP_NAN20_LEADER && seg == 0) x = NAN;
			if (j == 20 && c->special == SP_NAN20_PATCHES && seg >= 1 && seg <= c->count) x = NAN;
			in[i][j] = x;
			nonfinite_in += !isfinite(x);
		}
		in[i][-1] = 0.0;    /* shielded cell: not read by the recognizer */
	}

	feclearexcept(FE_ALL_EXCEPT);
	/* inttime 0.0182 s: above the 0.012308 s threshold-loosening limit (munki_imp.c:5364) */
	code = munki_extract_patches_multimeas(&meter, &flags, out, c->tnpatch, in, rows, 0.0182);
	fp = fetestexcept(FE_INVALID | FE_DIVBYZERO | FE_OVERFLOW);

	for (p = 0; p < c->tnpatch; p++)
		for (j = 0; j < NRAW; j++) {
			nonfinite_out += !isfinite(out[p][j]);
			changed += out[p][j] != SENTINEL;
		}
	/* On success, every finite returned average against its plateau value; a
	 * nonfinite band is counted above and skipped here. */
	if (code == MUNKI_OK && c->special != SP_NAN_ALL && c->special != SP_UNIFORM) {
		for (p = 0; p < c->tnpatch && p < c->count; p++)
			for (j = 0; j < NRAW; j++) {
				double want = segval(c, p + 1, j) * (c->negate ? -1.0 : 1.0);
				double rel = fabs(out[p][j] - want) / fabs(want);
				if (!isfinite(out[p][j])) continue;
				compared++;
				if (!(rel <= maxrel)) maxrel = rel;
			}
	}

	printf("{\"case\":\"%s\",\"code\":%d,\"flags\":%d,\"requested\":%d,\"present\":%d,"
	       "\"rows\":%d,\"nonfinite_input\":%d,\"nonfinite_output\":%d,\"changed_values\":%d,"
	       "\"compared\":%d,\"fe_invalid\":%d,\"fe_divbyzero\":%d,\"fe_overflow\":%d,"
	       "\"max_rel_err_vs_plateau\":",
	       c->name, code, flags, c->tnpatch, c->count, rows, nonfinite_in, nonfinite_out,
	       changed, compared, !!(fp & FE_INVALID), !!(fp & FE_DIVBYZERO), !!(fp & FE_OVERFLOW));
	if (compared) number(maxrel); else printf("null");
	/* Retain every returned band: two sampled bands and a maximum error cannot
	 * establish that all other averages remained unchanged. */
	printf(",\"values\":[");
	for (p = 0; p < c->tnpatch; p++) {
		if (p) putchar(',');
		putchar('[');
		for (j = 0; j < NRAW; j++) { if (j) putchar(','); number(out[p][j]); }
		putchar(']');
	}
	printf("],\"retained_curve\":%d}\n", retained_curve != NULL);

	free_dmatrix(in, 0, rows - 1, -1, NRAW - 1);
	free_dmatrix(out, 0, c->tnpatch - 1, -1, NRAW - 1);
	if (retained_curve != NULL)
		retained_curve->del(retained_curve);
	return 0;
}
