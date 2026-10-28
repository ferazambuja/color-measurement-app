/* Exact i1Pro3 exposure-selection block with scripted raw measurements.
 * Averaging, vector helpers and enums are extracted from pinned source. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <float.h>
#include <time.h>
#define STR2(x) #x
#define STR(x) STR2(x)
#define a1logd(...) ((void)0)
#include "codes.inc"
#define MX_NRAW 128
typedef struct { int emiss, adaptive, scan; double inttime, iinttime, targoscale;
                 double done_dintsel; time_t diseldate; } i1pro3_state;
typedef struct { int mmode, nraw; double min_int_time, sens_target; i1pro3_state ms[1]; } i1pro3imp;
typedef struct { void *m; void *log; } i1pro3;

static double level;
static int allocs, frees;
static i1pro3_code i1pro3_spot_simple_emis_raw_meas(i1pro3 *p, double ***praw, int *pn,
                                                    double *pinttime, double t, int dn) {
	i1pro3imp *m = (i1pro3imp *)p->m; int i, j, n = 3; double **r;
	(void)pinttime; (void)t; (void)dn;
	r = malloc(n * sizeof(double *));
	for (i = 0; i < n; i++) {
		r[i] = (double *)malloc((m->nraw + 1) * sizeof(double)) + 1;
		for (j = -1; j < m->nraw; j++) r[i][j] = level;
	}
	allocs++; *praw = r; *pn = n; return I1PRO3_OK;
}
static void i1pro3_free_raw(i1pro3 *p, i1p3_mmode mm, double **r, int n) {
	int i; (void)p; (void)mm; for (i = 0; i < n; i++) free(r[i] - 1); free(r); frees++;
}
#include "average.inc"
/* Returns the selected s->inttime for a probe of uniform channel value lev. */
static double select_inttime(double sens_target, double lev, int *done, i1pro3_code *pev) {
	i1pro3imp mi; i1pro3 pp; i1pro3 *p = &pp; i1pro3imp *m = &mi;
	i1pro3_state *s = &mi.ms[0];
	inst_cal_type ct = inst_calt_emis_int_time, *calt = &ct;
	inst_cal_cond cc = inst_calc_emis_white, *calc = &cc;
	i1pro3_code ev = I1PRO3_OK;
	int mmode = 0; time_t cdate = 1000;
	mi.mmode = 0; mi.nraw = MX_NRAW; mi.min_int_time = 2.5208332863257965e-3;
	mi.sens_target = sens_target;
	s->emiss = 1; s->adaptive = 0; s->scan = 0;
	s->inttime = s->iinttime = 1.8;		/* NAE_INTT, i1pro3_imp.c:171,780 */
	s->targoscale = 1.0;				/* default, i1pro3_imp.c:664 */
	s->done_dintsel = 0; s->diseldate = 0;
	pp.m = m; pp.log = NULL; level = lev;
	do {
#include "block.inc"
	} while (0);
	m->mmode = mmode;
	*done = (int)s->done_dintsel; *pev = ev;
	return s->inttime;
}

int main(void) {
	static const double sat[] = { 45000.0/0.843, 1000.0, 40.0, 10.0 };
	static const double lev[] = { -1e6, -3.0, -1e-300, -0.0, 0.0, 1e-300, 0.25, 1.0, 2.0,
	                              100.0, 1125.0, 20000.0, 53000.0 };
	int a, b, fails = 0, checks = 0;
	printf("block %s\n", STR(BLOCK));
	for (a = 0; a < (int)(sizeof(sat)/sizeof(sat[0])); a++) {
		double st = 0.843 * sat[a], ref, t, minpos = DBL_MAX, maxpos = -DBL_MAX;
		int done; i1pro3_code ev;
		ref = select_inttime(st, 1.0, &done, &ev);
		for (b = 0; b < (int)(sizeof(lev)/sizeof(lev[0])); b++) {
			if (lev[b] <= 0.0) continue;
			t = select_inttime(st, lev[b], &done, &ev);
			if (t < minpos) minpos = t;
			if (t > maxpos) maxpos = t;
		}
		printf(" sens_sat %-10g target %-10g  f(1.0)=%-10g  positive-max range [%g, %g]\n",
		       sat[a], st, ref, minpos, maxpos);
		for (b = 0; b < (int)(sizeof(lev)/sizeof(lev[0])); b++) {
			t = select_inttime(st, lev[b], &done, &ev);
			printf("   max %-8g -> inttime %-10g done %d ev 0x%x\n", lev[b], t, done, ev);
			if (lev[b] <= 0.0) {
				checks++;
				if (!(t == ref)) {
					fails++;
					printf("   FAIL: non-positive max %g selected %g, floor value f(1.0) is %g\n",
					       lev[b], t, ref);
				}
			}
		}
	}
	checks++;
	if (allocs != frees) { fails++; printf("FAIL: %d allocations, %d releases\n", allocs, frees); }
	printf("%s: %d of %d checks failed\n", STR(BLOCK), fails, checks);
	return fails != 0;
}
