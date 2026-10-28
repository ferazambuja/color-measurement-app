/* Source-bound Specbos band-count check.
 * Runs the exact specbos_nbands() over instrument-reported ranges. An accepted
 * range must give pristine's count, fit the 601-band xspect, and match the
 * integer endpoints of the driver's range command. No transport code runs.
 */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "codes.inc"
#include "function.inc"
#include "command.inc"

static int check(int condition, const char *name, double s, double l, int got) {
	if (!condition) printf("FAIL %s: %g - %g gave %d\n", name, s, l, got);
	return condition;
}

int main(void) {
	static const struct { double s, l; int n; } cases[] = {
		{380.0, 780.0, 401},		/* the usual range */
		{230.0, 830.0, XSPECT_MAX_BANDS},	/* the largest that fits */
		{380.0, 381.0, 2},		/* the smallest the interpolators accept */
		{829.0, 830.0, 2},
		{380.4, 780.6, 0},		/* stored 401, command requests 402 */
		{229.4, 829.6, 0},		/* stored 601, command requests 602 */
		{829.5, 830.0, 0},		/* stored two, command requests one */
		{380.4, 780.0, 0},		/* start cannot be sent unchanged */
		{380.0, 780.4, 0},		/* end cannot be sent unchanged */
		{-10.0, 590.0, 0},		/* the command rounds the negative start */
		{999999.0, 1000000.0, 0},	/* at the endpoint ceiling */
		{1e12, 1e12 + 200.0, 0},	/* count fits, command endpoints do not */
		{229.0, 830.0, 0},		/* 602 bands */
		{-1e9, 830.0, 0},		/* far below; (int) of the start is defined */
		{-1e6, -999800.0, 0},	/* at the start floor */
		{380.0, 380.0, 0},		/* one band */
		{780.0, 380.0, 0},		/* descending */
		{830.0, 380.0, 0},
	};
	double nan = 0.0 / 0.0, inf = 1.0 / 0.0;
	int i, n, site, lo, hi, ok = 1;
	for (i = 0; i < (int)(sizeof(cases) / sizeof(cases[0])); i++) {
		n = specbos_nbands(cases[i].s, cases[i].l);
		/* An accepted count is pristine's formula: (int)(l - s + 1.0 + 0.5) */
		ok &= check(n == cases[i].n, "count", cases[i].s, cases[i].l, n);
		if (n != 0) {
			ok &= check(n == (int)((cases[i].l - cases[i].s + 1.0)/1.0 + 0.5),
			            "pristine count", cases[i].s, cases[i].l, n);
			for (site = 0; site < 3; site++) {
				lo = hi = 0;
				ok &= check(command_endpoints(site, cases[i].s, cases[i].l, &lo, &hi),
				            "command format", cases[i].s, cases[i].l, n);
				ok &= check(cases[i].s == (double)lo && cases[i].l == (double)hi,
				            "command endpoints", cases[i].s, cases[i].l, n);
				ok &= check(n == hi - lo + 1,
				            "command count", cases[i].s, cases[i].l, n);
			}
		}
	}
	ok &= check(specbos_nbands(nan, 780.0) == 0, "NaN start", nan, 780.0, 0);
	ok &= check(specbos_nbands(380.0, nan) == 0, "NaN end", 380.0, nan, 0);
	ok &= check(specbos_nbands(-inf, 780.0) == 0, "infinite start", -inf, 780.0, 0);
	ok &= check(specbos_nbands(380.0, inf) == 0, "infinite end", 380.0, inf, 0);
	ok &= check(specbos_nbands(380.0, -inf) == 0, "negative infinite end", 380.0, -inf, 0);
	return ok ? 0 : 1;
}
