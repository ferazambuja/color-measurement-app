/* Three leader/trailer agreement tests over every ordered pair of 18 special
 * doubles. predicates.inc is generated from the texts actually compiled: the
 * original recognizer's `if`, the repaired recognizer's `if`, and the i1Pro
 * guard legacy_scan_recognition composes. Each returns nonzero to refuse.
 * This is a table of the predicates alone; it says nothing about reachability. */
#include <stdio.h>
#include <math.h>
#include <float.h>
#include "predicates.inc"

int main(void) {
	volatile double v[18];
	const char *n[18] = {"-inf", "-DBL_MAX", "-1e300", "-1", "-0.7", "-1e-300", "-denorm_min",
	                     "-0", "+0", "denorm_min", "1e-300", "0.5", "0.7", "1", "1e300",
	                     "DBL_MAX", "+inf", "NaN"};
	int i, k, pairs = 0, changed = 0, same_as_i1pro = 0, defined = 0, defined_unchanged = 0;
	int changed_nan = 0, new_acceptances = 0;
	v[0] = -INFINITY; v[1] = -DBL_MAX; v[2] = -1e300; v[3] = -1.0; v[4] = -0.7; v[5] = -1e-300;
	v[6] = -4.9406564584124654e-324; v[7] = -0.0; v[8] = 0.0; v[9] = 4.9406564584124654e-324;
	v[10] = 1e-300; v[11] = 0.5; v[12] = 0.7; v[13] = 1.0; v[14] = 1e300; v[15] = DBL_MAX;
	v[16] = INFINITY; v[17] = NAN;

	printf("{\"changed_pairs\":[");
	for (i = 0; i < 18; i++) for (k = 0; k < 18; k++) {
		double l = v[i], t = v[k];
		int o = !!original(l, t), r = !!repaired(l, t), g = !!i1pro_guard(l, t);
		pairs++;
		same_as_i1pro += r == g;
		if (isfinite(l) && isfinite(t) && !(l == 0.0 && t == 0.0)) {
			defined++;
			defined_unchanged += o == r;
		}
		if (o != r) {
			changed_nan += isnan(l / t) || isnan(t / l);
			printf("%s[\"%s\",\"%s\"]", changed ? "," : "", n[i], n[k]);
			changed++;
		}
		new_acceptances += o && !r;
	}
	printf("],\"pairs\":%d,\"changed\":%d,\"repaired_equals_i1pro_guard\":%d,"
	       "\"defined_pairs\":%d,\"defined_unchanged\":%d,\"changed_with_nan_ratio\":%d,"
	       "\"new_acceptances\":%d}\n",
	       pairs, changed, same_as_i1pro, defined, defined_unchanged, changed_nan,
	       new_acceptances);
	return (same_as_i1pro == pairs && defined_unchanged == defined && changed_nan == changed
	        && new_acceptances == 0) ? 0 : 1;
}
