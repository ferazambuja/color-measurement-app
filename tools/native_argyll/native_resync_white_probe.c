/* Source-bound white-calibration control-flow probe, adapted from the private
 * upstream corrections review. Hardware and cache I/O are substituted.
 * Actual driver/calibration constants are extracted into codes.inc. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#define STR2(x) #x
#define STR(x) STR2(x)
#define a1logd(...) ((void)0)
#include "codes.inc"
#define NW 4
#define NR 6
#define OLD  7.0	/* previous valid factor */
#define WHITE 5000.0	/* raw white written by a successful white measure */
#define FACT 0.25	/* new factor written by a successful factor computation */
#define REJ  -1.0	/* factor written by a refused factor computation */

typedef struct {
	int scan, adaptive, emiss, trans, reflective, idark_valid, dark_valid, cal_valid;
	int gainmode, want_calib, dark_gain_mode;
	double inttime, wcaltime, targoscale, dadaptime, wadaptime, dcaltime, dark_int_time;
	time_t ddate, iddate, cfdate;
	double *cal_factor[2], *white_data, *dark_data, *idark_data[4];
} i1pro_state;
typedef struct { int mmode, nwav[2], nraw, transwarn; double min_int_time, highgain; double *white_ref[2];
                 i1pro_state ms[1]; } i1proimp;
typedef struct { int debug; } a1log;
typedef struct { i1proimp *m; a1log *log; int dtype; } i1pro;

/* ---- script ---- */
static int ncalls, fail_at, fail_kind;	/* fail_kind 0 = error, 1 = inconsistent/saturated variant */
static int init_sat;		/* first white measure saturates */
static double init_scale;	/* first white measure scale when not saturated */
static int nwm;			/* successful-or-saturated white measures so far */
static int fail_now(void) { return ncalls++ == fail_at; }

static int i1pro_comp_nummeas(i1pro *p, double t, double it) { (void)p; (void)t; (void)it; return 5; }
static i1pro_code i1pro_whitemeasure(i1pro *p, double *w0, double *w1, double *raw, double *scale,
	int n, double *it, int g, double targ, int lt) {
	int j, first = (nwm == 0);
	(void)n; (void)it; (void)g; (void)targ; (void)lt;
	if (fail_now()) {
		if (fail_kind) { for (j = -1; j < NR; j++) raw[j] = 99.0; return I1PRO_RD_WHITEREADINCONS; }
		return I1PRO_COMS_FAIL;
	}
	nwm++;
	if (first && init_sat) { for (j = -1; j < NR; j++) raw[j] = 99.0; return I1PRO_RD_SENSORSATURATED; }
	for (j = -1; j < NR; j++) raw[j] = WHITE;
	if (w0 != NULL) for (j = 0; j < NW; j++) w0[j] = WHITE;
	if (w1 != NULL) for (j = 0; j < NW; j++) w1[j] = WHITE;
	if (scale != NULL) *scale = first ? init_scale : 1.0;
	return I1PRO_OK;
}
static i1pro_code i1pro_dark_measure(i1pro *p, double *d, int n, double *it, int g) {
	(void)p; (void)d; (void)n; (void)it; (void)g; return fail_now() ? I1PRO_COMS_FAIL : I1PRO_OK; }
static i1pro_code i1pro_interp_dark(i1pro *p, double *d, double it, int g) {
	(void)p; (void)d; (void)it; (void)g; return fail_now() ? I1PRO_INT_NOINTERPDARK : I1PRO_OK; }
static i1pro_code i1pro_optimise_sensor(i1pro *p, double *nit, int *ng, double it, int g, int hg,
	int clip, double targ, double scale) {
	(void)p; (void)hg; (void)clip; (void)targ; (void)scale; (void)g;
	if (fail_now()) return I1PRO_RD_LIGHTTOOLOW;
	*nit = it * 2.0; *ng = 0; return I1PRO_OK; }
static i1pro_code i1pro_check_white_reference1(i1pro *p, double *w) {
	(void)p; (void)w; return fail_now() ? I1PRO_RD_WHITEREFERROR : I1PRO_OK; }
static i1pro_code i1pro_compute_white_cal(i1pro *p, double *cf0, double *r0, double *w0,
	double *cf1, double *r1, double *w1, int ft) {
	int j; (void)p; (void)r0; (void)w0; (void)r1; (void)w1; (void)ft;
	if (fail_now()) { for (j = 0; j < NW; j++) cf0[j] = cf1[j] = REJ; return I1PRO_RD_WHITEREFERROR; }
	for (j = 0; j < NW; j++) cf0[j] = cf1[j] = FACT;
	return fail_kind ? I1PRO_CAL_TRANSWHITEWARN : I1PRO_OK;
}


/* One-mode copy of the calibrate loop body around the extracted block. */
static i1pro_code block_fn(i1pro *p, inst_cal_type *calt, inst_cal_cond *calc) {
	i1pro_code ev = I1PRO_OK;
	i1proimp *m = (i1proimp *)p->m;
	int mmode = m->mmode;
	time_t cdate = 1000;
	int nummeas = 0, ltocmode = 0, i;
	i1pro_state *s = &m->ms[0];
	m->mmode = 0;
#include "block.inc"
	m->mmode = mmode;
	(void)nummeas; (void)ltocmode; (void)i; (void)cdate;
	return ev;
}

typedef struct { int ev, cal_valid, calls, pending, g0, g1; double cf; } outcome;
static double cf0[NW], cf1[NW], wd[NR+1], dd[NR+1], id[4][NR+1], wr0[NW], wr1[NW];

static outcome run(int cfg, int k, int kind) {
	i1proimp mi; i1pro pp; i1pro_state *s = &mi.ms[0];
	inst_cal_type ct; inst_cal_cond cc = 0; outcome o; int i, j;
	memset(&mi, 0, sizeof(mi));
	mi.nwav[0] = mi.nwav[1] = NW; mi.nraw = NR; mi.min_int_time = 0.004; mi.highgain = 9.5;
	mi.white_ref[0] = wr0; mi.white_ref[1] = wr1;
	pp.m = &mi; pp.log = NULL; pp.dtype = instI1Pro;
	for (j = 0; j < NW; j++) { cf0[j] = cf1[j] = OLD; wr0[j] = wr1[j] = 0.9; }
	for (j = 0; j <= NR; j++) { wd[j] = dd[j] = 1.0; for (i = 0; i < 4; i++) id[i][j] = 1.0; }
	s->cal_factor[0] = cf0; s->cal_factor[1] = cf1; s->white_data = wd + 1; s->dark_data = dd + 1;
	for (i = 0; i < 4; i++) s->idark_data[i] = id[i] + 1;
	s->cal_valid = 1; s->dark_valid = 1; s->idark_valid = 1; s->inttime = 0.02; s->targoscale = 0.9;
	s->wcaltime = s->dadaptime = s->wadaptime = s->dcaltime = 0.5; s->cfdate = 1;
	init_sat = 0; init_scale = 1.0;
	switch (cfg) {
	case 0: s->reflective = 1; s->adaptive = 1; cc = inst_calc_man_ref_white; break;
	case 1: s->reflective = 1; s->adaptive = 1; cc = inst_calc_man_ref_white; init_scale = 0.2; break;
	case 2: s->reflective = 1; s->adaptive = 1; cc = inst_calc_man_ref_white; init_sat = 1; break;
	case 3: s->trans = 1; s->adaptive = 1; cc = inst_calc_man_trans_white; init_scale = 0.2; break;
	case 4: s->trans = 1; s->adaptive = 1; cc = inst_calc_man_trans_white; init_scale = 0.2; s->idark_valid = 0; break;
	case 5: s->trans = 1; s->adaptive = 1; cc = inst_calc_man_trans_white; init_sat = 1; break;
	case 6: s->trans = 1; s->adaptive = 1; cc = inst_calc_man_trans_white; init_sat = 1; s->idark_valid = 0; break;
	case 7: s->reflective = 1; s->scan = 1; s->adaptive = 1; cc = inst_calc_man_ref_white; break;
	case 8: s->reflective = 1; s->scan = 1; s->adaptive = 1; cc = inst_calc_man_ref_white; init_sat = 1; s->gainmode = 0; break;
	case 9: s->reflective = 1; s->scan = 1; s->adaptive = 1; cc = inst_calc_man_ref_white; init_sat = 1; s->gainmode = 1; break;
	case 10: s->trans = 1; s->scan = 1; s->adaptive = 0; cc = inst_calc_man_trans_white; init_sat = 1; break;
	case 11: s->trans = 1; s->scan = 1; s->adaptive = 0; cc = inst_calc_man_trans_white; break;
	case 12: s->trans = 1; s->scan = 1; s->adaptive = 1; cc = inst_calc_man_trans_white; init_scale = 0.05; s->idark_valid = 0; break;
	case 13: s->trans = 1; s->adaptive = 1; cc = inst_calc_man_trans_white; init_scale = 1.0; break;
	}
	ct = s->reflective ? inst_calt_ref_white : inst_calt_trans_vwhite;
	o.g0 = s->gainmode;
	ncalls = 0; nwm = 0; fail_at = k; fail_kind = kind;
	o.ev = block_fn(&pp, &ct, &cc);
	o.cal_valid = s->cal_valid; o.cf = cf0[0]; o.calls = ncalls; o.g1 = s->gainmode;
	o.pending = (ct & (inst_calt_ref_white | inst_calt_trans_vwhite)) != 0;
	return o;
}

static const char *cfname[] = { "refl spot in-range", "refl spot adapt", "refl spot SAT",
	"trans spot adapt", "trans spot adapt no-idark", "trans spot SAT", "trans spot SAT no-idark",
	"refl scan in-range", "refl scan SAT gain0", "refl scan SAT gain1", "trans scan fixed SAT",
	"trans scan fixed ok", "trans scan adapt no-idark", "trans spot in-range" };
static const char *cfs(double v) { return v == OLD ? "OLD" : v == WHITE ? "WHITE" : v == FACT ? "FACT" : v == REJ ? "REJ" : "?"; }

int main(void) {
	int cfg, k, kind, fails = 0, checks = 0, exits = 0, diffs = 0;
	printf("block %s\n", STR(BLOCK));
	for (cfg = 0; cfg < 14; cfg++) {
		outcome ok = run(cfg, -1, 0);
		printf("[%s] success path: ev 0x%x cal_valid %d cf %s calls %d pending %d gain %d->%d\n",
		       cfname[cfg], ok.ev, ok.cal_valid, cfs(ok.cf), ok.calls, ok.pending, ok.g0, ok.g1);
		for (kind = 0; kind < 2; kind++) {
			outcome base = run(cfg, -1, kind);
			if (kind == 1) {	/* warning variant of the success path */
				printf("[%s] success (warn variant): ev 0x%x cal_valid %d cf %s\n", cfname[cfg], base.ev, base.cal_valid, cfs(base.cf));
			}
			checks++;
			if (base.ev == I1PRO_OK && !(base.cal_valid == 1 && base.cf == FACT && !base.pending)) {
				fails++; printf("  FAIL: successful calibration not valid with new factors\n");
			}
			for (k = 0; k < ok.calls; k++) {
				outcome o = run(cfg, k, kind);
				int stale = (o.ev != I1PRO_OK && o.cal_valid && o.cf != OLD);
				exits++;
				printf("  fail call %d (%s): ev 0x%02x cal_valid %d cf %-5s gain %d->%d %s\n", k, kind ? "variant" : "error",
				       o.ev, o.cal_valid, cfs(o.cf), o.g0, o.g1, stale ? "STALE VALID" : "");
				checks++;
				if (stale) fails++;
				if (o.ev == I1PRO_OK) { checks++; if (!(o.cal_valid == 1 && o.cf == FACT)) fails++; }
			}
		}
		/* saturated or pre-write exits: also print the result with no injected failure */
		if (ok.ev != I1PRO_OK) {
			checks++;
			if (ok.cal_valid && ok.cf != OLD) { fails++; printf("  FAIL: unforced exit stale\n"); }
		}
	}
	(void)diffs;
	printf("%s: %d exits exercised, %d of %d checks failed\n", STR(BLOCK), exits, fails, checks);
	return fails != 0;
}
