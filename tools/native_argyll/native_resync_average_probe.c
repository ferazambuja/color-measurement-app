/* Consistency of averaged readings: an undefined average cannot pass.
 *
 * functions.inc is one exact compiled function from the final driver text,
 * i1pro_average_multimeas, munki_average_multimeas or
 * i1pro3_multimeas_check_raw_consistency, with its own PATCH_CONS_THR (and
 * DARKTHSCAMIN). FAMILY selects the cases. The device structures are stand-ins
 * carrying only the fields those functions read; nothing reaches a device.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <float.h>

#define a1logd(...) ((void)0)

typedef struct { int nraw, nsen; double scan_toll_ratio; } imp;
typedef struct { void *log; imp *m; } dev;
typedef dev i1pro, munki, i1pro3;
typedef imp i1proimp, munkiimp, i1pro3imp;

static int live;
static double *dvector(int nl, int nh) {
	double *v = malloc((size_t)(nh - nl + 1) * sizeof(double));
	if (v == NULL) abort();
	live++;
	return v - nl;
}
static void free_dvector(double *v, int nl, int nh) {
	(void)nh;
	live--;
	free(v + nl);
}
static double vect_avg(double *v, int n) {
	double s = 0.0;
	int i;
	for (i = 0; i < n; i++)
		s += v[i];
	return s / (double)n;
}

#include "functions.inc"

#define NRAW 128
#define NMEAS 3
static double store[NMEAS][NRAW + 1], *rows[NMEAS], avg_store[NRAW + 1];
static int failures, checks;

/* Fill every reading with a flat level, then apply one case's disturbance. */
static void fill(const char *name) {
	int i, j;
	for (i = 0; i < NMEAS; i++) {
		rows[i] = &store[i][1];		/* Index -1 is the shielded cell */
		for (j = -1; j < NRAW; j++)
			rows[i][j] = 1000.0 + i;
	}
	if (!strcmp(name, "nan_cell"))
		rows[1][5] = NAN;
	else if (!strcmp(name, "inf_cell"))
		rows[2][9] = INFINITY;
	else if (!strcmp(name, "overflow"))
		for (i = 0; i < NMEAS; i++)
			for (j = 0; j < NRAW; j++)
				rows[i][j] = DBL_MAX;
	else if (!strcmp(name, "row_overflow"))	/* Each cell's mean is finite */
		for (i = 0; i < NMEAS; i++)
			for (j = 0; j < NRAW; j++)
				rows[i][j] = DBL_MAX / 16.0;
	else if (!strcmp(name, "nan_shielded"))
		rows[0][-1] = NAN;
	else if (!strcmp(name, "inconsistent"))
		for (j = -1; j < NRAW; j++)
			rows[2][j] = 3000.0;
}

static void expect(const char *family, const char *name, int got, int want) {
	checks++;
	if (got != want) {
		printf("FAIL %s %s: inconsistent %d, expected %d\n", family, name, got, want);
		failures++;
	}
}

int main(void) {
	static const char *names[] = { "flat", "inconsistent", "nan_cell", "inf_cell",
	                               "overflow", "row_overflow", "nan_shielded" };
	/* The shielded cell is not part of the raw consistency helper's input. */
	static const int want[] = { 0, 1, 1, 1, 1, 1, FAMILY == 3 ? 0 : 1 };
	imp state = { NRAW, NRAW, 1.0 };
	dev device = { NULL, &state };
	unsigned k;
	for (k = 0; k < sizeof(names) / sizeof(names[0]); k++) {
		int rv;
		fill(names[k]);
		live = 0;
#if FAMILY == 1
		rv = i1pro_average_multimeas(&device, &avg_store[1], rows, NMEAS, NULL, NULL,
		                             0.0, 10.0) & 1;
#elif FAMILY == 2
		rv = munki_average_multimeas(&device, &avg_store[1], rows, NMEAS, NULL, 10.0) & 1;
#else
		rv = i1pro3_multimeas_check_raw_consistency(&device, rows, NMEAS) != 0;
#endif
		expect(FAMILY == 1 ? "i1pro" : FAMILY == 2 ? "munki" : "i1pro3", names[k], rv,
		       want[k]);
		if (live != 0) {
			printf("FAIL %s: %d allocations retained\n", names[k], live);
			failures++;
		}
	}
	printf("average: %d of %d checks failed\n", failures, checks);
	return failures != 0;
}
