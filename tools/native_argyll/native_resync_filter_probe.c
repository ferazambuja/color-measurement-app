/* Exercise the exact composed validator and spectral evaluators, without a
 * setter, instrument, transport or new application custom-filter route.
 * The stand-in contains only the fields these functions consume. */
#include <stdio.h>
#include <string.h>
#include <math.h>
#include <float.h>
#include "codes.inc"
typedef struct {
	int spec_n;
	double spec_wl_short, spec_wl_long, norm;
	double spec[XSPECT_MAX_BANDS];
} xspect;
#include "functions.inc"

static int failures;
static void expect(const char *name, int yes) {
	if (!yes) {
		printf("FAIL %s\n", name);
		failures++;
	}
}
static xspect spectrum(int count, double spacing) {
	xspect value;
	int i;
	memset(&value, 0, sizeof value);
	value.spec_n = count;
	value.spec_wl_short = 0.0;
	value.spec_wl_long = spacing * (count - 1);
	value.norm = 2.0;
	for (i = 0; i < count; i++) value.spec[i] = 1.0 + i;
	return value;
}
int main(void) {
	struct { const char *name; int count; double spacing; } cases[] = {
		{"two-boundary", 2, 5.01}, {"three-boundary", 3, 5.01},
		{"two-coarse", 2, 300.0}, {"three-coarse", 3, 150.0},
		{"two-fine", 2, 4.0}, {"three-fine", 3, 4.0}, {"four-coarse", 4, 100.0},
	};
	size_t c;
	for (c = 0; c < sizeof cases / sizeof cases[0]; c++) {
		xspect value = spectrum(cases[c].count, cases[c].spacing);
		double result, wavelength = value.spec_wl_short +
		       0.25 * (value.spec_wl_long - value.spec_wl_short);
		int accepted = inst_valid_custom_filter(&value);
		expect(cases[c].name, accepted);
		if (!accepted) continue; /* The stock validator refuses before lookup. */
		getval_xspec(&value, &result, wavelength);
		expect("accepted filter interpolates linearly",
		       isfinite(result) && fabs(result - (1.0 + 0.25 * (value.spec_n - 1)) / 2.0) < 1e-12);
	}
	{
		xspect value = spectrum(2, 300.0);
		value.spec_n = 1; expect("one band refused", !inst_valid_custom_filter(&value));
		value.spec_n = XSPECT_MAX_BANDS + 1; expect("oversized refused", !inst_valid_custom_filter(&value));
		value = spectrum(2, 300.0); value.spec_wl_long = 0.0;
		expect("flat grid refused", !inst_valid_custom_filter(&value));
		value = spectrum(2, 300.0); value.norm = 0.0;
		expect("zero norm refused", !inst_valid_custom_filter(&value));
		value = spectrum(2, 300.0); value.spec[1] = NAN;
		expect("nonfinite sample refused", !inst_valid_custom_filter(&value));
	}
	printf("%d failures\n", failures);
	return failures != 0;
}
