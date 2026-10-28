/* i1Pro3 polarized exposure: the count is computed from a bounded time.
 *
 * block.inc is the compiled statement sequence of i1pro3_pol_refl_cal from the
 * target-scale integration time to the rounded count. Its `2 * (int)ceil(...)`
 * is undefined when the doubled value leaves int range. ceil() is wrapped to
 * record that instead of converting it, so the unrepaired text fails by name
 * rather than by undefined behaviour, and a sanitized run checks the rest.
 */
#include <stdio.h>
#include <string.h>
#include <limits.h>
#include <math.h>

#define a1logd(...) ((void)0)

static int out_of_range;
static double probe_ceil(double value) {
	double rounded = (ceil)(value);
	if (!(rounded >= INT_MIN / 2 && rounded <= INT_MAX / 2)) {
		out_of_range++;
		return 0.0;		/* Never converted: the defect is recorded instead */
	}
	return rounded;
}
#define ceil(x) probe_ceil(x)

#define MIN_INT 0.0025
static double exposure(double inttime, double maxval) {
	struct { double targoscale; } state = { 0.9 }, *s = &state;
	struct { double sens_target, min_int_time; } imp = { 30000.0, MIN_INT }, *m = &imp;
	int no_minints;
#include "block.inc"
	return inttime;
}

int main(void) {
	static const struct { const char *name; double maxval, want; } cases[] = {
		{ "nominal", 36000.0, 4 * MIN_INT },		/* 0.0075 s rounds up to 4 minimums */
		{ "bright", 1e6, 2 * MIN_INT },
		{ "dim_in_range", 1e-3, 10 * MIN_INT },
		{ "negative_white", -500.0, 2 * MIN_INT },
		{ "tiny_white", 1e-300, 10 * MIN_INT },	/* No longer exposure is allowed */
		{ "zero_white", 0.0, 10 * MIN_INT },
		{ "nan_white", NAN, 2 * MIN_INT },
	};
	int failures = 0;
	unsigned k;
	for (k = 0; k < sizeof(cases) / sizeof(cases[0]); k++) {
		double got;
		out_of_range = 0;
		got = exposure(0.01, cases[k].maxval);
		if (out_of_range) {
			printf("FAIL %s: count conversion out of int range\n", cases[k].name);
			failures++;
		} else if (!(fabs(got - cases[k].want) <= 1e-12)) {
			printf("FAIL %s: inttime %g, expected %g\n", cases[k].name, got, cases[k].want);
			failures++;
		}
	}
	printf("polarized: %d of %u checks failed\n", failures,
	       (unsigned)(sizeof(cases) / sizeof(cases[0])));
	return failures != 0;
}
