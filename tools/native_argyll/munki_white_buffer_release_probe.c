/* ColorMunki white-buffer probe: the pinned white measurement, original or
 * repaired, with the pinned byte conversion, averaging and warm-up count and
 * upstream numsup, over synthetic raw bytes. The two transport calls the header
 * declares static are stand-ins here (succeed, fail, or fill the buffer), and so
 * is the dark subtraction. The function's own malloc and free are wrapped to
 * count the reading buffer, its own dmatrix and free_dmatrix to count the
 * measurement matrix. A block still alive at return is a leak by count, not by
 * allocator inspection. A case may fail the buffer allocation once. Every read
 * of a case is recorded. The generated whitemeasure.inc retains Graeme W.
 * Gill's GPL-2.0-or-later code. No transport, calibration state machine,
 * wavelength conversion or instrument runtime.
 *
 * Usage: probe <case>   or   probe --list
 * stdout: one JSON object; stderr: the function's own log lines. */
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
#define NSEN 137            /* sensor bands stored, munki_imp.c:9397 */
#define READING (NSEN * 2)  /* bytes per reading: 4 shielded cells, 2 unused, 128 bands, 3 trailing */
#define SATLIMIT 60000.0    /* synthetic; the instrument's comes from EEPROM, munki_imp.c:9508 */
#define OPTSVAL 50000.0     /* synthetic optimal sensor target, munki_imp.c:9502 */
#define WARMUP 0.05         /* synthetic reflective warm-up time in seconds, munki_imp.c:9532 */
#define INTTIME 0.0182      /* integration time: ceil(0.05 / 0.0182) = 3 warm-up readings */
#define SENTINEL -1234567.0
#define MAXREP 8

/* ------------------------------------------------------------------ */
/* Accounting and fault injection                                      */

enum { FAULT_NONE, FAULT_BUFFER };
enum { STEP_NONE, STEP_TRIGGER, STEP_READ };
enum { FILL_FLAT, FILL_SATURATED, FILL_SATURATED_SKIPPED, FILL_INCONSISTENT };

static int g_fault = FAULT_NONE, g_faults, g_step = STEP_NONE, g_fill = FILL_FLAT;
static int g_mallocs, g_frees, g_matrices, g_matrices_freed;
static int g_requested = -1;        /* readings the function asked the transport for, or -1 */

/* Only the function's own calls are wrapped; numsup keeps the libc ones. */
static void *counting_malloc(size_t size) {
	void *p;
	if (g_fault == FAULT_BUFFER && g_faults == 0) {
		g_faults++;
		return NULL;
	}
	p = malloc(size);
	if (p != NULL)
		g_mallocs++;
	return p;
}

static void counting_free(void *p) {
	if (p != NULL)
		g_frees++;
	free(p);
}

static double **counting_dmatrix(int nrl, int nrh, int ncl, int nch) {
	g_matrices++;
	return dmatrix(nrl, nrh, ncl, nch);
}

static void counting_free_dmatrix(double **m, int nrl, int nrh, int ncl, int nch) {
	g_matrices_freed++;
	free_dmatrix(m, nrl, nrh, ncl, nch);
}

/* ------------------------------------------------------------------ */
/* Stand-ins for the transport (munki_imp.h:643-651, :957-967) and the   */
/* dark subtraction (munki_imp.h:673-683); no USB, no linearisation.     */

static void put_ushort(unsigned char *bp, unsigned int value) {
	bp[0] = (unsigned char)(value & 0xff);       /* buf2ushort, munki_imp.c:483-488 */
	bp[1] = (unsigned char)((value >> 8) & 0xff);
}

static munki_code munki_trigger_one_measure(munki *p, int nummeas, double *inttime, int gainmode,
                                            int calib_measure, int dark_measure) {
	(void)p; (void)inttime; (void)gainmode; (void)calib_measure; (void)dark_measure;
	g_requested = nummeas;
	return g_step == STEP_TRIGGER ? MUNKI_COMS_FAIL : MUNKI_OK;
}

static munki_code munki_readmeasurement(munki *p, int inummeas, int scanflag, unsigned char *buf,
                                        int bsize, int *nummeas, int calib_measure,
                                        int dark_measure) {
	int r, j, k;
	(void)p; (void)scanflag; (void)calib_measure; (void)dark_measure;
	if (g_step == STEP_READ)
		return MUNKI_COMS_FAIL;
	if (bsize < inummeas * READING)
		return MUNKI_INT_ASSERT;        /* the buffer the function sized is too small: not expected */
	for (r = 0; r < inummeas; r++) {
		unsigned char *bp = buf + r * READING;
		memset(bp, 0, READING);
		for (k = 0; k < 4; k++)
			put_ushort(bp + 2 * k, 20);                     /* shielded cells */
		for (j = 0; j < NRAW; j++) {
			unsigned int value = 20000 + 50 * j + 10 * r;
			if (g_fill == FILL_INCONSISTENT)
				value = (r % 2) ? 40000 : 5000;             /* alternate readings far apart */
			if (g_fill == FILL_SATURATED && r == inummeas - 1 && j == 64)
				value = 65000;                              /* last reading: over the limit */
			if (g_fill == FILL_SATURATED_SKIPPED && r == 0 && j == 64)
				value = 65000;                              /* first reading only */
			put_ushort(bp + 12 + 2 * j, value);
		}
		put_ushort(bp + READING - 2, 3000);                 /* LED voltage drop */
	}
	if (nummeas != NULL)
		*nummeas = inummeas;
	return MUNKI_OK;
}

void munki_sub_raw_to_absraw(munki *p, int nummeas, double inttime, int gainmode, double **absraw,
                             double *sub, double *trackmax, int ntrackmax, double *maxv) {
	munkiimp *m = (munkiimp *)p->m;
	double max = -1e38;
	int i, j;
	(void)inttime; (void)gainmode; (void)trackmax; (void)ntrackmax;
	for (i = 0; i < nummeas; i++)
		for (j = -1; j < m->nraw; j++) {
			absraw[i][j] -= sub[j];
			if (j >= 0 && absraw[i][j] > max)
				max = absraw[i][j];
		}
	if (maxv != NULL)
		*maxv = max;
}

#define malloc counting_malloc
#define free counting_free
#define dmatrix counting_dmatrix
#define free_dmatrix counting_free_dmatrix
#include "whitemeasure.inc"
#undef malloc
#undef free
#undef dmatrix
#undef free_dmatrix

/* ------------------------------------------------------------------ */
/* Cases                                                               */

typedef struct {
	const char *name;
	int nummeas;        /* readings requested */
	int repeat;         /* measurements in one process */
	int reflective;     /* reflective mode: warm-up readings are skipped */
	int fill;           /* what the transport writes */
	int step;           /* which transport call fails, or none */
	int fault;          /* allocation failure injected once, or none */
} spec;

static const spec cases[] = {
	{"white8",                         8, 1, 0, FILL_FLAT,              STEP_NONE,    FAULT_NONE},
	{"white8_repeat5",                 8, 5, 0, FILL_FLAT,              STEP_NONE,    FAULT_NONE},
	{"saturated8",                     8, 1, 0, FILL_SATURATED,         STEP_NONE,    FAULT_NONE},
	{"saturated8_repeat5",             8, 5, 0, FILL_SATURATED,         STEP_NONE,    FAULT_NONE},
	{"reflective_saturated8",          8, 1, 1, FILL_SATURATED,         STEP_NONE,    FAULT_NONE},
	{"reflective_skipped_saturation8", 8, 1, 1, FILL_SATURATED_SKIPPED, STEP_NONE,    FAULT_NONE},
	{"inconsistent8",                  8, 1, 0, FILL_INCONSISTENT,      STEP_NONE,    FAULT_NONE},
	{"trigger_fails8",                 8, 1, 0, FILL_FLAT,              STEP_TRIGGER, FAULT_NONE},
	{"read_fails8",                    8, 1, 0, FILL_FLAT,              STEP_READ,    FAULT_NONE},
	{"zero_measures",                  0, 1, 0, FILL_FLAT,              STEP_NONE,    FAULT_NONE},
	{"alloc_buffer8",                  8, 1, 0, FILL_FLAT,              STEP_NONE,    FAULT_BUFFER},
};
#define NCASES ((int)(sizeof(cases) / sizeof(cases[0])))

/* FNV-1a over the returned readings and scale: equal bytes in, equal hash out. */
static uint64_t fnv(const double *absraw, double optscale) {
	uint64_t h = 1469598103934665603ull;
	int j, b;
	for (j = -1; j <= NRAW; j++) {
		unsigned char bytes[sizeof(double)];
		double value = j < NRAW ? absraw[j] : optscale;
		memcpy(bytes, &value, sizeof(double));
		for (b = 0; b < (int)sizeof(double); b++) {
			h ^= bytes[b];
			h *= 1099511628211ull;
		}
	}
	return h;
}

int main(int argc, char **argv) {
	const spec *c = NULL;
	int i, j, it;
	int codes[MAXREP], warmup[MAXREP];
	uint64_t hashes[MAXREP];
	double *absraw, *dark, optscale, inttime;
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
	impl.nsen = NSEN;
	impl.satlimit = SATLIMIT;
	impl.optsval = OPTSVAL;
	impl.refinvalidsampt = WARMUP;
	impl.mmode = 0;
	impl.ms[0].reflective = c->reflective;
	dark = dvector(-1, NRAW - 1);
	for (j = -1; j < NRAW; j++)
		dark[j] = j < 0 ? 20.0 : 100.0;
	impl.ms[0].dark_data = dark;
	absraw = dvector(-1, NRAW - 1);
	g_fault = c->fault;
	g_step = c->step;
	g_fill = c->fill;

	for (it = 0; it < c->repeat; it++) {
		for (j = -1; j < NRAW; j++) absraw[j] = SENTINEL;
		optscale = SENTINEL;
		inttime = INTTIME;
		g_requested = -1;
		codes[it] = munki_whitemeasure(&meter, absraw, &optscale, c->nummeas, &inttime, 0, 1.0);
		warmup[it] = g_requested < 0 ? -1 : g_requested - c->nummeas;
		hashes[it] = fnv(absraw, optscale);
	}

	printf("{\"case\":\"%s\",\"iterations\":%d,\"codes\":[", c->name, c->repeat);
	for (it = 0; it < c->repeat; it++) printf("%s%d", it ? "," : "", codes[it]);
	printf("],\"warmup\":[");
	for (it = 0; it < c->repeat; it++) {
		if (warmup[it] < 0) printf("%snull", it ? "," : "");     /* transport never reached */
		else printf("%s%d", it ? "," : "", warmup[it]);
	}
	printf("],\"output_fnv\":[");
	for (it = 0; it < c->repeat; it++)
		printf("%s\"%016llx\"", it ? "," : "", (unsigned long long)hashes[it]);
	printf("],\"mallocs\":%d,\"frees\":%d,\"heap_open\":%d,\"matrices\":%d,\"matrices_freed\":%d,"
	       "\"matrices_open\":%d,\"faults\":%d}\n",
	       g_mallocs, g_frees, g_mallocs - g_frees, g_matrices, g_matrices_freed,
	       g_matrices - g_matrices_freed, g_faults);

	free_dvector(absraw, -1, NRAW - 1);
	free_dvector(dark, -1, NRAW - 1);
	return 0;
}
