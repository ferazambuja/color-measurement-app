/* ColorMunki white factors: a read that cannot give usable factors is refused.
 *
 * function.inc is the compiled munki_compute_white_cal. The stand-in structures
 * carry only the fields it reads. Each case reads a white, then checks the
 * return and that every factor of an accepted white is finite and positive,
 * which is what each reading's calibration multiplies by.
 */
#include <stdio.h>
#include <string.h>
#include <math.h>
#include <float.h>

#define HIGH_RES
#define a1logd(...) ((void)0)

typedef struct { int unused; } munki_state;
typedef struct { int nwav1, nwav2, hr_inited, mmode; munki_state ms[1]; } munkiimp;
typedef struct { void *log; munkiimp *m; } munki;

#include "function.inc"

#define N1 36
#define N2 108
static double ref1[N1], read1[N1], fac1[N1], ref2[N2], read2[N2], fac2[N2];
static int failures, checks;

static int usable(const double *v, int n) {
	int j;
	for (j = 0; j < n; j++)
		if (!(v[j] > 0.0 && v[j] <= DBL_MAX))
			return 0;
	return 1;
}

static void run(const char *name, int reflective, int hires, int want) {
	munkiimp state = { N1, N2, hires, 0, { { 0 } } };
	munki device = { NULL, &state };
	int j, rv;
	for (j = 0; j < N1; j++) { ref1[j] = 0.9; read1[j] = 20000.0; fac1[j] = -7.0; }
	for (j = 0; j < N2; j++) { ref2[j] = 0.9; read2[j] = 20000.0; fac2[j] = -7.0; }
	if (!strcmp(name, "nan_band")) read1[7] = NAN;
	else if (!strcmp(name, "inf_band")) read1[7] = INFINITY;
	else if (!strcmp(name, "negative_reference")) ref1[3] = -0.5;
	else if (!strcmp(name, "zero_reference")) ref1[3] = 0.0;
	else if (!strcmp(name, "zero_white")) memset(read1, 0, sizeof(read1));
	else if (!strcmp(name, "negative_white")) for (j = 0; j < N1; j++) read1[j] = -100.0;
	else if (!strcmp(name, "low_band")) read1[11] = 10.0;
	else if (!strcmp(name, "hires_nan_band")) read2[40] = NAN;
	else if (!strcmp(name, "hires_negative_reference")) ref2[40] = -0.5;
	else if (!strcmp(name, "hires_zero_reference")) ref2[40] = 0.0;
	else if (!strcmp(name, "hires_zero_white")) memset(read2, 0, sizeof(read2));
	rv = munki_compute_white_cal(&device, fac1, reflective ? ref1 : NULL, read1,
	                             fac2, reflective ? ref2 : NULL, read2);
	checks++;
	if (rv != want) {
		printf("FAIL %s: returned %d, expected %d\n", name, rv, want);
		failures++;
	} else if (rv >= 0 && !(usable(fac1, N1) && (!hires || usable(fac2, N2)))) {
		printf("FAIL %s: accepted factors are not finite and positive\n", name);
		failures++;
	}
}

int main(void) {
	/* Reflective: upstream floors a dim white at white_ref/1000, so zero is usable. */
	run("flat", 1, 1, 0);
	run("zero_white", 1, 0, 0);
	run("nan_band", 1, 0, -1);
	run("inf_band", 1, 0, -1);
	run("negative_reference", 1, 0, -1);
	run("zero_reference", 1, 0, -1);
	run("hires_nan_band", 1, 1, -1);
	run("hires_negative_reference", 1, 1, -1);
	run("hires_zero_reference", 1, 1, -1);
	/* Transmission: factors are 1/white, with a 0.4% of average floor. */
	run("flat", 0, 1, 0);
	run("low_band", 0, 0, 1);
	run("zero_white", 0, 0, -1);
	run("negative_white", 0, 0, -1);
	run("nan_band", 0, 0, -1);
	run("hires_zero_white", 0, 1, -1);
	printf("white factors: %d of %d checks failed\n", failures, checks);
	return failures != 0;
}
