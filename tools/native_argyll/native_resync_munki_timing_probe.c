/* Source-bound ColorMunki timing-parameter check.
 * Runs the exact munki_imp_init guard over instrument timing values (tick and
 * minimum count, munki_imp.c:548-551; calibration, LED pre-heat, invalid-sample
 * and calibration-scan times, :9513-9536). Each named case is refused by one
 * condition. For every accepted set, the driver's own count functions
 * (munki_comp_nummeas, munki_comp_ru_nummeas) size the longest read at the
 * shortest integration time, nsen * 2 * (ninvmeas + nummeas) as
 * munki_whitemeasure does (:3898); built with UBSan, so an int conversion
 * out of range traps. Reflective times start at the calibration time (:684-712,
 * targoscale 1.0); the others are adaptive, no shorter than the minimum.
 * No device code runs.
 */
#include <float.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>

#include "codes.inc"

struct munkiimp {
	int tickdur, minintcount;
	double intclkp, min_int_time, max_int_time, cal_int_time;
	double ledpreheattime, refinvalidsampt, calscantime;
};
typedef struct munkiimp munkiimp;
struct munki { void *log; };
typedef struct munki munki;
static void a1logd(void *log, int level, const char *fmt, ...) {
	(void)log; (void)level; (void)fmt;
}

#include "counts.inc"

static int guard(munki *p, munkiimp *m) {
/* BEGIN_EXTRACT */
#include "block.inc"
/* END_EXTRACT */
	return MUNKI_OK;
}

static void setup(munkiimp *m, int tick, int count, double cal, double preheat,
                  double invalid, double calscan) {
	m->tickdur = tick; m->minintcount = count;
	m->intclkp = (double)m->tickdur * 1e-6;		/* :548 */
	m->min_int_time = m->intclkp * (double)m->minintcount;	/* :551 */
	m->max_int_time = 4.5;				/* :552 */
	m->cal_int_time = cal; m->ledpreheattime = preheat;
	m->refinvalidsampt = invalid; m->calscantime = calscan;
}

static int check(int condition, const char *name, int tick, int count, double cal) {
	if (!condition) printf("FAIL %s: tick %d x %d, cal %g\n", name, tick, count, cal);
	return condition;
}

int main(void) {
	static const int ticks[] = {1, 2, 10, 100, 1000, 100000};
	static const int counts[] = {1, 10, 100, 1000, 10000, 1000000};
	static const double cals[] = {1e-9, 1e-6, 0.001, 0.018208, 1.0, 100.0};
	static const double longs[] = {0.0, 1.0, 100.0, 100000.0};
	double nan = 0.0 / 0.0, inf = 1.0 / 0.0;
	munki p;
	munkiimp m;
	int a, b, c, d, e, ok = 1, accepted = 0, refused = 0;
	p.log = NULL;
	{	/* The documented values, then one refusal per condition */
		static const struct { int tick, count; double cal, preheat, invalid, calscan; int accept;
		                      const char *why; } cases[] = {
			{1, 10000, 0.018208, 1.0, 0.1, 1.0, 1, "the documented values"},
			{0, 10000, 0.018208, 1.0, 0.1, 1.0, 0, "zero tick"},
			{-1, 10000, 0.018208, 1.0, 0.1, 1.0, 0, "negative tick"},
			{1, 0, 0.018208, 1.0, 0.1, 1.0, 0, "zero minimum count"},
			{1, -5, 0.018208, 1.0, 0.1, 1.0, 0, "negative minimum count"},
			{1000, 5000, 0.018208, 1.0, 0.1, 1.0, 0, "minimum above the 4.5 s maximum"},
			{1, 10000, 0.0, 1.0, 0.1, 1.0, 0, "zero calibration time"},
			{1, 10000, -0.018, 1.0, 0.1, 1.0, 0, "negative calibration time"},
			{1, 10000, 0.0 / 0.0, 1.0, 0.1, 1.0, 0, "NaN calibration time"},
			{1, 10000, 1.0 / 0.0, 1.0, 0.1, 1.0, 0, "infinite calibration time"},
			{1, 1, 0.018208, 1.0, 0.1, 1.0, 0, "a 1 us minimum overflows a 20 s scan"},
			{1, 10000, 1e-9, 1.0, 0.1, 1.0, 0, "a 1 ns calibration time overflows a scan"},
			{1, 10000, 0.018208, 100000.0, 0.1, 1.0, 0, "a long LED pre-heat overflows"},
			{1, 10000, 0.018208, 1.0, 0.1, 100000.0, 0, "a long calibration scan overflows"},
		};
		for (a = 0; a < (int)(sizeof(cases) / sizeof(cases[0])); a++) {
			setup(&m, cases[a].tick, cases[a].count, cases[a].cal, cases[a].preheat,
			      cases[a].invalid, cases[a].calscan);
			ok &= check((guard(&p, &m) == MUNKI_OK) == cases[a].accept, cases[a].why,
			            cases[a].tick, cases[a].count, cases[a].cal);
		}
	}
	(void)nan; (void)inf;
	/* Every accepted set: the longest read at the shortest time fits */
	for (a = 0; a < (int)(sizeof(ticks) / sizeof(ticks[0])); a++)
	for (b = 0; b < (int)(sizeof(counts) / sizeof(counts[0])); b++)
	for (c = 0; c < (int)(sizeof(cals) / sizeof(cals[0])); c++)
	for (d = 0; d < (int)(sizeof(longs) / sizeof(longs[0])); d++)
	for (e = 0; e < (int)(sizeof(longs) / sizeof(longs[0])); e++) {
		double tmin, times[3];
		int k;
		setup(&m, ticks[a], counts[b], cals[c], longs[d], (d + e) & 1 ? 65.535 : 0.04, longs[e]);
		if (guard(&p, &m) != MUNKI_OK) {
			refused++;
			continue;
		}
		accepted++;
		tmin = m.min_int_time < m.cal_int_time ? m.min_int_time : m.cal_int_time;
		times[0] = MAXSCANTIME; times[1] = m.ledpreheattime; times[2] = m.calscantime;
		for (k = 0; k < 3; k++) {
			double n = (double)munki_comp_ru_nummeas(&p, m.refinvalidsampt, tmin)
			         + (double)munki_comp_nummeas(&p, times[k], tmin);
			ok &= check(2.0 * NSEN_MAX * n <= (double)INT_MAX, "accepted read overflows",
			            ticks[a], counts[b], cals[c]);
		}
	}
	printf("%d accepted and %d refused timing sets\n", accepted, refused);
	ok &= check(accepted > 0 && refused > 0, "sweep exercised", 0, 0, 0.0);
	return ok ? 0 : 1;
}
