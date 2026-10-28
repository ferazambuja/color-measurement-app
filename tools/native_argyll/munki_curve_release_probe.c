/* ColorMunki curve-release probe: the pinned strip recognizer (the fragment the
 * margin proof compiles), original or repaired, with upstream numsup and rspl1,
 * over synthetic strips. The curve constructor is wrapped to count creations
 * and each curve's own destructor slot to count deletions; the recognizer's
 * own malloc, realloc and free are wrapped to count its heap blocks. A curve
 * or block still alive at return is a leak by count, not by allocator
 * inspection. A case may inject one allocation failure, at the points array
 * or at the curve constructor. Every read of a case is recorded. The generated
 * recognizer.inc retains Graeme W. Gill's GPL-2.0-or-later code. No transport,
 * acquisition, calibration, conversion or instrument runtime.
 *
 * Usage: probe <case>   or   probe --list
 * stdout: one JSON object; stderr: the recognizer's own log lines. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>
#include "sa_config.h"
#include "numsup.h"
#include "rspl1.h"
#include "cgats.h"
#include "xspect.h"
#include "insttypes.h"
#include "conv.h"
#include "icoms.h"
#include "inst.h"
#include "munki.h"
#include "munki_imp.h"

#define NRAW 128            /* raw bands stored, munki_imp.c:9398 */
#define SENTINEL -1234567.0
#define MAXREP 8

/* ------------------------------------------------------------------ */
/* Accounting and fault injection                                      */

enum { FAULT_NONE, FAULT_POINTS, FAULT_CURVE };

static int g_fault = FAULT_NONE, g_faults;
static size_t g_points_bytes;               /* the points array's size for this strip */
static int g_created, g_deleted, g_mallocs, g_frees;
static void (*g_upstream_del)(rspl *);

static void counting_del(rspl *t) {
	g_deleted++;
	g_upstream_del(t);
}

/* rspl1.c:477 gives every curve the same destructor, so one saved pointer serves. */
static rspl *counting_new_rspl(int flags, int di, int fdi) {
	rspl *r;
	if (g_fault == FAULT_CURVE && g_faults == 0) {
		g_faults++;
		return NULL;
	}
	r = new_rspl(flags, di, fdi);
	if (r != NULL) {
		g_created++;
		g_upstream_del = r->del;
		r->del = counting_del;
	}
	return r;
}

/* Only the recognizer's own calls are wrapped; rspl1 and numsup keep the libc ones. */
static void *counting_malloc(size_t size) {
	void *p;
	if (g_fault == FAULT_POINTS && g_faults == 0 && size == g_points_bytes) {
		g_faults++;
		return NULL;
	}
	p = malloc(size);
	if (p != NULL)
		g_mallocs++;
	return p;
}

static void *counting_realloc(void *old, size_t size) {
	int was_null = old == NULL;
	void *p = realloc(old, size);
	if (was_null && p != NULL)
		g_mallocs++;
	return p;
}

static void counting_free(void *p) {
	if (p != NULL)
		g_frees++;
	free(p);
}

#define new_rspl counting_new_rspl
#define malloc counting_malloc
#define realloc counting_realloc
#define free counting_free
#include "recognizer.inc"
#undef new_rspl
#undef malloc
#undef realloc
#undef free

/* ------------------------------------------------------------------ */
/* Synthetic strips                                                    */

typedef struct {
	const char *name;
	int count;          /* patches in the synthetic strip */
	int tnpatch;        /* patches requested from the recognizer */
	int width;          /* rows per segment */
	int repeat;         /* recognitions of the same strip in one process */
	int fault;          /* allocation failure injected once, or none */
} spec;

static const spec cases[] = {
	/* eight patches fit a curve (at least four width points, munki_imp.c:5778) */
	{"valid8",              8, 8, 100, 1, FAULT_NONE},	/* success */
	{"valid8_repeat5",      8, 8, 100, 5, FAULT_NONE},	/* five strips in one process */
	{"sparse6_8",           8, 8,   6, 1, FAULT_NONE},	/* six rows: the narrowest detected */
	{"count_low8",          8, 7, 100, 1, FAULT_NONE},	/* too many consistent: early exit */
	{"count_high8",         8, 9, 100, 1, FAULT_NONE},	/* fails before the curve exists */
	/* three patches give too few points for a curve */
	{"valid3",              3, 3, 100, 1, FAULT_NONE},
	/* one of the two allocations fails */
	{"alloc_points_valid8", 8, 8, 100, 1, FAULT_POINTS},
	{"alloc_curve_valid8",  8, 8, 100, 1, FAULT_CURVE},
};
#define NCASES ((int)(sizeof(cases) / sizeof(cases[0])))

static const double levels[8] = {200.0, 610.0, 220.0, 630.0, 240.0, 650.0, 260.0, 670.0};

/* Value of segment seg (0 leader, 1..count patches, count+1 trailer) in band j. */
static double segval(const spec *c, int seg, int j) {
	if (seg == 0 || seg == c->count + 1)
		return 1000.0;
	return levels[seg - 1] * (1.0 + 0.3 * sin(0.1 * j + 1.3 * (seg - 1)));
}

/* FNV-1a over the returned averages: equal bytes in, equal hash out. */
static uint64_t fnv(double **out, int rows) {
	uint64_t h = 1469598103934665603ull;
	int p, j, b;
	for (p = 0; p < rows; p++)
		for (j = 0; j < NRAW; j++) {
			unsigned char bytes[sizeof(double)];
			memcpy(bytes, &out[p][j], sizeof(double));
			for (b = 0; b < (int)sizeof(double); b++) {
				h ^= bytes[b];
				h *= 1099511628211ull;
			}
		}
	return h;
}

int main(int argc, char **argv) {
	const spec *c = NULL;
	int i, j, p, it, rows;
	int codes[MAXREP], flagses[MAXREP];
	uint64_t hashes[MAXREP];
	double **in, **out;
	munki meter; munkiimp impl;

	if (argc == 2 && strcmp(argv[1], "--list") == 0) {
		for (i = 0; i < NCASES; i++) puts(cases[i].name);
		return 0;
	}
	if (argc != 2) return 2;
	for (i = 0; i < NCASES; i++)
		if (strcmp(argv[1], cases[i].name) == 0) c = &cases[i];
	if (c == NULL || c->repeat > MAXREP) return 3;
	memset(&meter, 0, sizeof(meter));
	memset(&impl, 0, sizeof(impl));
	meter.m = &impl;
	meter.log = g_log;
	impl.nraw = NRAW;
	impl.scan_toll_ratio = 1.0;      /* munki_imp.c:534 default */
	g_fault = c->fault;
	/* A clean strip yields count + 2 potential patches; the points array holds
	 * npat - 2 of them (munki_imp.c:5752). */
	g_points_bytes = sizeof(co) * (size_t)c->count;

	rows = (c->count + 2) * c->width;
	in = dmatrixz(0, rows - 1, -1, NRAW - 1);
	out = dmatrix(0, c->tnpatch - 1, -1, NRAW - 1);
	for (i = 0; i < rows; i++) {
		for (j = 0; j < NRAW; j++)
			in[i][j] = segval(c, i / c->width, j);
		in[i][-1] = 0.0;    /* shielded cell: not read by the recognizer */
	}

	for (it = 0; it < c->repeat; it++) {
		int flags = -999;
		for (p = 0; p < c->tnpatch; p++)
			for (j = -1; j < NRAW; j++) out[p][j] = SENTINEL;
		/* inttime 0.0182 s: above the 0.012308 s threshold-loosening limit (munki_imp.c:5364) */
		codes[it] = munki_extract_patches_multimeas(&meter, &flags, out, c->tnpatch, in, rows,
		                                            0.0182);
		flagses[it] = flags;
		hashes[it] = fnv(out, c->tnpatch);
	}

	printf("{\"case\":\"%s\",\"iterations\":%d,\"codes\":[", c->name, c->repeat);
	for (it = 0; it < c->repeat; it++) printf("%s%d", it ? "," : "", codes[it]);
	printf("],\"flags\":[");
	for (it = 0; it < c->repeat; it++) printf("%s%d", it ? "," : "", flagses[it]);
	printf("],\"output_fnv\":[");
	for (it = 0; it < c->repeat; it++)
		printf("%s\"%016llx\"", it ? "," : "", (unsigned long long)hashes[it]);
	printf("],\"created\":%d,\"deleted\":%d,\"leaked\":%d,\"mallocs\":%d,\"frees\":%d,"
	       "\"heap_open\":%d,\"faults\":%d}\n",
	       g_created, g_deleted, g_created - g_deleted, g_mallocs, g_frees,
	       g_mallocs - g_frees, g_faults);

	free_dmatrix(in, 0, rows - 1, -1, NRAW - 1);
	free_dmatrix(out, 0, c->tnpatch - 1, -1, NRAW - 1);
	return 0;
}
