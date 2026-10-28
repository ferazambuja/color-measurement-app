/* Source-bound i1Pro3 timing-parameter check.
 * Runs the exact init guard over device clock values. Each named case is refused
 * by one condition alone. A sweep then shows that every accepted pair keeps the
 * display-delay sample buffer (bsize = 2 * nsen1 * nummeas, nsen1 = 134,
 * i1pro3_imp.c:12822, :12885) and the emissive clock count
 * (int)floor(inttime/intclkp + 0.5) with inttime <= MAX_INT_TIME within int, so
 * the delay method needs no bound of its own. No device code runs.
 */
#include <limits.h>
#include <math.h>
#include <stdio.h>

#include "codes.inc"

#define NDMXTIME 2.0		/* i1pro3_imp_meas_delay's capture time */
#define NSEN1 134			/* i1pro3_imp.c:6856 */
#define NRAW 128			/* :6858 */

struct i1pro3imp { unsigned int minintclks; double intclkp; };
typedef struct i1pro3imp i1pro3imp;
struct i1pro3 { void *log; };
typedef struct i1pro3 i1pro3;
static void a1logd(void *log, int level, const char *fmt, ...) {
	(void)log; (void)level; (void)fmt;
}

static i1pro3_code guard(i1pro3 *p, i1pro3imp *m) {
/* BEGIN_EXTRACT */
#include "block.inc"
/* END_EXTRACT */
	return I1PRO3_OK;
}

static int check(int condition, const char *name, unsigned int clks, double clkp) {
	if (!condition) printf("FAIL %s: %u x %g\n", name, clks, clkp);
	return condition;
}

int main(void) {
	static const struct { unsigned int clks; double clkp; int accept; const char *why; } cases[] = {
		{165, 1.5277777492883615e-5, 1, "the real device pair"},
		{1, 2.0, 1, "the longest minimum time"},
		{0, 1.5277777492883615e-5, 0, "zero clocks"},
		{165, 0.0, 0, "zero clock period"},
		{165, -1.5e-5, 0, "negative clock period"},
		{165, 1.0, 0, "minimum time above MAX_INT_TIME"},
		{1, 1.5e-6, 0, "read size: 2 * MX_NSEN * nnmeas"},
		{4294967295u, 1e-12, 0, "zebra clocks: 2 * 10 * minintclks * nnmeas"},
	};
	i1pro3 p;
	i1pro3imp m;
	double nan = 0.0 / 0.0;
	int i, k, accepted = 0, ok = 1;
	static const unsigned int clks[] = {1, 2, 10, 165, 1000, 100000, 10000000, 4294967295u};
	p.log = NULL;
	for (i = 0; i < (int)(sizeof(cases) / sizeof(cases[0])); i++) {
		m.minintclks = cases[i].clks; m.intclkp = cases[i].clkp;
		ok &= check((guard(&p, &m) == I1PRO3_OK) == cases[i].accept, cases[i].why,
		            cases[i].clks, cases[i].clkp);
	}
	m.minintclks = 165; m.intclkp = nan;
	ok &= check(guard(&p, &m) != I1PRO3_OK, "NaN clock period", 165, nan);
	for (i = 0; i < (int)(sizeof(clks) / sizeof(clks[0])); i++) {
		for (k = 0; k <= 2400; k++) {
			double clkp = pow(10.0, -14.0 + k * 0.01), mit, nummeas;
			m.minintclks = clks[i]; m.intclkp = clkp;
			if (guard(&p, &m) != I1PRO3_OK)
				continue;
			accepted++;
			mit = clks[i] * clkp;
			nummeas = floor(NDMXTIME / mit + 0.5);
			ok &= check(2.0 * NSEN1 * nummeas <= (double)INT_MAX, "delay bsize", clks[i], clkp);
			ok &= check(nummeas * (NRAW + 1) * 8.0 <= 4294967295.0, "delay raw bytes", clks[i], clkp);
			ok &= check(floor(MAX_INT_TIME / clkp + 0.5) <= (double)INT_MAX,
			            "emissive clock count", clks[i], clkp);
		}
	}
	printf("accepted %d swept pairs\n", accepted);
	ok &= check(accepted > 0, "sweep accepted some pairs", 0, 0.0);
	return ok ? 0 : 1;
}
