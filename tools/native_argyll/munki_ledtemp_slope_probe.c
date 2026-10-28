/* ColorMunki LED-temperature probe: the pinned LED white measurement and
 * compensation, original or repaired, with the pinned byte conversion,
 * averaging, warm-up count and wavelength conversion and upstream numsup.
 * Calibration cases feed synthetic raw bytes whose LED temperature readings
 * vary or do not, and whose raw bands respond or sit at the dark level.
 * Reading cases supply a compensation table directly, as a restored calibration
 * would, and run the exact compensation call/error branch of the reading path.
 * Consumer cases run the exact averaging and conversion over a nonfinite band.
 * The two transport calls the header declares static are stand-ins (they fill
 * the buffer), and so is the dark subtraction. Returned white, table and
 * readings are checked for finiteness and unchanged outputs on refusal; both
 * extracted caller branches check failure propagation and calibration state.
 * The generated ledtemp.inc retains Graeme W. Gill's GPL-2.0-or-later code.
 * No transport, calibration state machine, cache file or instrument runtime.
 *
 * Usage: probe <case>   or   probe --list
 * stdout: one JSON object; stderr: the function's own log lines. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>
#include <float.h>
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
#define READING (NSEN * 2)
#define SATLIMIT 60000.0    /* synthetic; the instrument's comes from EEPROM, munki_imp.c:9508 */
#define WARMUP 0.05         /* synthetic reflective warm-up time in seconds, munki_imp.c:9532 */
#define INTTIME 0.0182      /* integration time: ceil(0.05 / 0.0182) = 3 warm-up readings */
#define DARK 100.0
#define SENTINEL -1234567.0
#define MAXREP 8
#define NWAV 2              /* synthetic output wavelengths for the consumer case */
#define REFCODE 3000.0      /* reading cases: reference code of the supplied table */
#define READCODE 3050.0     /* reading cases: LED code of every reading */

enum { LED_DRIFT, LED_FLAT, LED_ONE_STEP, LED_WARMUP_FLAT, LED_SECOND_STEP };
enum { KIND_CALIBRATION, KIND_READ, KIND_CONSUMERS };
enum { TABLE_NORMAL, TABLE_DEAD, TABLE_ROOT, TABLE_NEGATIVE_FIT, TABLE_NEGATIVE_FACTOR,
       TABLE_DENOMINATOR_OVERFLOW };

static int g_led = LED_DRIFT;
static int g_requested = -1;
static int g_dead_band = -1;    /* raw band whose bytes equal the dark level, or -1 */

static void put_ushort(unsigned char *bp, unsigned int value) {
	bp[0] = (unsigned char)(value & 0xff);       /* buf2ushort, munki_imp.c:483-488 */
	bp[1] = (unsigned char)((value >> 8) & 0xff);
}

/* The LED temperature reading of measurement r of inummeas, of which the first
 * skipped are warm-up readings the conversion does not use. */
static unsigned int led_value(int r, int inummeas, int skipped) {
	switch (g_led) {
	case LED_FLAT:      return 3000;
	case LED_ONE_STEP:  return r == inummeas - 1 ? 3001 : 3000;
	case LED_SECOND_STEP: return r == skipped + 1 ? 3001 : 3000;
	case LED_WARMUP_FLAT: return r < skipped ? 3000 : 3000 + 10 * (r - skipped);
	default:            return 3000 + 10 * r;
	}
}

/* The byte value of raw band j in reading r: a drift plus a wobble the line
 * cannot fit exactly, or exactly the dark level for the dead band. */
static unsigned int band_value(int j, int r) {
	if (j == g_dead_band)
		return (unsigned int)DARK;
	return 20000 + 50 * j + 10 * r + 200 * (r % 3);
}

static munki_code munki_trigger_one_measure(munki *p, int nummeas, double *inttime, int gainmode,
                                            int calib_measure, int dark_measure) {
	(void)p; (void)inttime; (void)gainmode; (void)calib_measure; (void)dark_measure;
	g_requested = nummeas;
	return MUNKI_OK;
}

static int g_skipped;       /* what the case expects the function to skip */

static munki_code munki_readmeasurement(munki *p, int inummeas, int scanflag, unsigned char *buf,
                                        int bsize, int *nummeas, int calib_measure,
                                        int dark_measure) {
	int r, j, k;
	(void)p; (void)scanflag; (void)calib_measure; (void)dark_measure;
	if (bsize < inummeas * READING)
		return MUNKI_INT_ASSERT;
	for (r = 0; r < inummeas; r++) {
		unsigned char *bp = buf + r * READING;
		memset(bp, 0, READING);
		for (k = 0; k < 4; k++)
			put_ushort(bp + 2 * k, 20);                     /* shielded cells */
		for (j = 0; j < NRAW; j++)
			put_ushort(bp + 12 + 2 * j, band_value(j, r));
		put_ushort(bp + READING - 2, led_value(r, inummeas, g_skipped));
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

static uint64_t fnv_add(uint64_t h, double value) {
	unsigned char bytes[sizeof(double)];
	int b;
	memcpy(bytes, &value, sizeof(double));
	for (b = 0; b < (int)sizeof(double); b++) {
		h ^= bytes[b];
		h *= 1099511628211ull;
	}
	return h;
}

/* Count only the helper's temporary ownership; numsup internals are excluded. */
static int g_local_blocks;
static void *probe_malloc(size_t n) { void *v = malloc(n); if (v) g_local_blocks++; return v; }
static void probe_free(void *v) { if (v) g_local_blocks--; free(v); }
static double *probe_dvector(int a, int b) {
    double *v = dvector(a,b); if (v) g_local_blocks++; return v;
}
static double **probe_dmatrix(int a, int b, int c, int d) {
    double **v = dmatrix(a,b,c,d); if (v) g_local_blocks++; return v;
}
static void probe_free_dvector(double *v, int a, int b) {
    if (v) g_local_blocks--; free_dvector(v,a,b);
}
static void probe_free_dmatrix(double **v, int a, int b, int c, int d) {
    if (v) g_local_blocks--; free_dmatrix(v,a,b,c,d);
}
#define malloc probe_malloc
#define free probe_free
#define dvector probe_dvector
#define dmatrix probe_dmatrix
#define free_dvector probe_free_dvector
#define free_dmatrix probe_free_dmatrix
#include "ledtemp.inc"

static int g_cache_calls;
/* Only the include is upstream caller code. The success/cache counters below
 * model later continuation; no full calibration or cache I/O is claimed. */
static munki_code caller_probe(munki *p, int nummeas) {
    munkiimp *m = (munkiimp *)p->m;
    munki_state *s = &m->ms[m->mmode];
    mk_mode mmode = m->mmode;
    munki_code ev;
#include "caller.inc"
    s->cal_valid = 1;
    s->want_calib = 0;
    g_cache_calls++;
    return MUNKI_OK;
}

/* The exact compensation call/error branch of munki_read_patches_2 over the
 * three temporaries that function owns at that point, filled as its byte
 * conversion would leave them. Nothing after the branch runs here. */
static int g_overflow_sample = -1, g_overflow_band = 100;
static uint64_t g_read_hash;
static int g_read_finite;
static munki_code read_probe(munki *p, int nummeas) {
    munkiimp *m = (munkiimp *)p->m;
    munki_state *s = &m->ms[m->mmode];
    int numpatches = 1, i, j;
    munki_code ev = MUNKI_OK;
    double **multimes, **absraw, *ledtemp;
    multimes = dmatrix(0, nummeas-1, -1, m->nraw-1);
    ledtemp = dvector(0, nummeas-1);
    absraw = dmatrix(0, numpatches-1, -1, m->nraw-1);
    for (i = 0; i < nummeas; i++) {
        ledtemp[i] = READCODE;
        for (j = -1; j < m->nraw; j++)
            multimes[i][j] = j < 0 ? 0.0 : 1000.0 + j;
    }
    if (g_overflow_sample >= 0)
        multimes[g_overflow_sample][g_overflow_band] = DBL_MAX;   /* finite input */
    g_read_hash = 1469598103934665603ull;
    g_read_finite = -1;     /* No numeric observation before the error branch. */
#include "read_caller.inc"
    g_read_finite = 1;
    for (i = 0; i < nummeas; i++)
        for (j = -1; j < m->nraw; j++) {
            if (!isfinite(multimes[i][j]))
                g_read_finite = 0;
            g_read_hash = fnv_add(g_read_hash, multimes[i][j]);
        }
    free_dvector(ledtemp, 0, nummeas-1);
    free_dmatrix(absraw, 0, numpatches-1, -1, m->nraw-1);
    free_dmatrix(multimes, 0, nummeas-1, -1, m->nraw-1);
    return ev;
}

/* Exact averaging and conversion over a band that is NaN in every reading:
 * the consistency statistic compares NaN as false, and the conversion reads
 * only the raw bands inside its windows, coefficient zero or not. */
static void consumers_probe(munki *p, int *avg_incons, int *avg_incons_nan,
                            int *conv_unused, int *conv_zero_coef) {
    munkiimp *m = (munkiimp *)p->m;
    double **multi = dmatrix(0, 1, -1, NRAW-1), *avg = dvector(-1, NRAW-1);
    double **wav = dmatrix(0, 0, 0, NWAV-1), **row = &avg;
    int indexes[NWAV] = {32, 48}, counts[NWAV] = {16, 16};
    double coef[NWAV * 16] = {0};
    int i, j;
    coef[0] = 1.0; coef[16] = 1.0;              /* one unit tap per output */
    m->nwav = NWAV; m->rmtx_index = indexes; m->rmtx_nocoef = counts; m->rmtx_coef = coef;
    m->straylight = dmatrix(0, NWAV-1, 0, NWAV-1);
    for (i = 0; i < NWAV; i++)
        for (j = 0; j < NWAV; j++)
            m->straylight[i][j] = i == j ? 1.0 : 0.0;
    for (i = 0; i < 2; i++)
        for (j = -1; j < NRAW; j++)
            multi[i][j] = j < 0 ? 0.0 : (i == 0 ? 100.0 : 100000.0);   /* grossly inconsistent */
    *avg_incons = munki_average_multimeas(p, avg, multi, 2, NULL, 0.0);
    multi[0][6] = multi[1][6] = NAN;
    *avg_incons_nan = munki_average_multimeas(p, avg, multi, 2, NULL, 0.0);
    munki_absraw_to_abswav(p, 1, wav, row);     /* windows [32,48) and [48,64) exclude band 6 */
    *conv_unused = isfinite(wav[0][0]) && isfinite(wav[0][1]);
    indexes[0] = 0;                             /* window [0,16) includes band 6, coefficient 0 */
    munki_absraw_to_abswav(p, 1, wav, row);
    *conv_zero_coef = isfinite(wav[0][0]) && isfinite(wav[0][1]);
    free_dmatrix(m->straylight, 0, NWAV-1, 0, NWAV-1);
    free_dmatrix(wav, 0, 0, 0, NWAV-1);
    free_dvector(avg, -1, NRAW-1);
    free_dmatrix(multi, 0, 1, -1, NRAW-1);
}
#undef malloc
#undef free
#undef dvector
#undef dmatrix
#undef free_dvector
#undef free_dmatrix

typedef struct {
	const char *name;
	int kind;
	int nummeas;
	int repeat;
	int led;
	int dead_band;      /* calibration: raw band at the dark level, or -1 */
	int first;          /* calibration: no valid calibration beforehand */
	int table;          /* read: which table the case supplies */
	int overflow;       /* read: reading whose band 100 is DBL_MAX, or -1 */
} spec;

static const spec cases[] = {
	{"drift8",             KIND_CALIBRATION, 8, 1, LED_DRIFT,       -1, 0, 0, -1},
	{"flat8",              KIND_CALIBRATION, 8, 1, LED_FLAT,        -1, 0, 0, -1},
	{"flat8_repeat5",      KIND_CALIBRATION, 8, 5, LED_FLAT,        -1, 0, 0, -1},
	{"flat3",              KIND_CALIBRATION, 3, 1, LED_FLAT,        -1, 0, 0, -1},
	{"one_step8",          KIND_CALIBRATION, 8, 1, LED_ONE_STEP,    -1, 0, 0, -1},
	{"second_step8",       KIND_CALIBRATION, 8, 1, LED_SECOND_STEP, -1, 0, 0, -1},
	{"warmup_flat_drift8", KIND_CALIBRATION, 8, 1, LED_WARMUP_FLAT, -1, 0, 0, -1},
	{"flat_zero_band8",    KIND_CALIBRATION, 8, 1, LED_FLAT,         0, 0, 0, -1},
	{"flat_first8",        KIND_CALIBRATION, 8, 1, LED_FLAT,        -1, 1, 0, -1},
	{"dead_first_band8",   KIND_CALIBRATION, 8, 1, LED_DRIFT,        0, 0, 0, -1},
	{"dead_middle_band8",  KIND_CALIBRATION, 8, 1, LED_DRIFT,       64, 0, 0, -1},
	{"dead_last_band8",    KIND_CALIBRATION, 8, 1, LED_DRIFT,      127, 0, 0, -1},
	{"dead_band_first8",   KIND_CALIBRATION, 8, 1, LED_DRIFT,       64, 1, 0, -1},
	{"dead_band_repeat3",  KIND_CALIBRATION, 8, 3, LED_DRIFT,        0, 0, 0, -1},
	{"read_control",               KIND_READ, 3, 1, LED_DRIFT, -1, 0, TABLE_NORMAL,          -1},
	{"read_dead_band",             KIND_READ, 3, 1, LED_DRIFT, -1, 0, TABLE_DEAD,            -1},
	{"read_root_band",             KIND_READ, 3, 1, LED_DRIFT, -1, 0, TABLE_ROOT,            -1},
	{"read_overflow_later_sample", KIND_READ, 3, 1, LED_DRIFT, -1, 0, TABLE_NORMAL,           1},
	{"read_denominator_overflow", KIND_READ, 3, 1, LED_DRIFT, -1, 0, TABLE_DENOMINATOR_OVERFLOW, -1},
	{"read_negative_fit",          KIND_READ, 3, 1, LED_DRIFT, -1, 0, TABLE_NEGATIVE_FIT,    -1},
	{"read_negative_factor",       KIND_READ, 3, 1, LED_DRIFT, -1, 0, TABLE_NEGATIVE_FACTOR, -1},
	{"consumers_dead_band",   KIND_CONSUMERS, 2, 1, LED_DRIFT, -1, 0, 0, -1},
};
#define NCASES ((int)(sizeof(cases) / sizeof(cases[0])))

/* The table a reading case supplies: intercept and slope per raw band, so that
 * the fitted white response is 20000+w-0.5*code, except the named band. */
static void fill_table(double **iwhite, int table) {
	int j;
	for (j = -1; j < NRAW; j++) {
		iwhite[0][j] = 20000.0 + j;
		iwhite[1][j] = -0.5;
	}
	switch (table) {
	case TABLE_DENOMINATOR_OVERFLOW: /* finite table and target, infinite denominator: false zero */
		iwhite[0][100] = 0.0; iwhite[1][100] = DBL_MAX / ((REFCODE + READCODE) / 2.0);
		break;
	case TABLE_DEAD:            /* zero white response: 0/0 */
		iwhite[0][6] = 0.0; iwhite[1][6] = 0.0;
		break;
	case TABLE_ROOT:            /* fitted response exactly zero at READCODE: 50/0 */
		iwhite[0][100] = READCODE; iwhite[1][100] = -1.0;
		break;
	case TABLE_NEGATIVE_FIT:    /* -2 at REFCODE, -1 at READCODE: factor 2, finite */
		iwhite[0][20] = -62.0; iwhite[1][20] = 0.02;
		break;
	case TABLE_NEGATIVE_FACTOR: /* 2 at REFCODE, -1 at READCODE: factor -2, finite */
		iwhite[0][20] = 182.0; iwhite[1][20] = -0.06;
		break;
	default:
		break;
	}
}

static void print_ints(const char *name, const int *values, int n) {
	int it;
	printf(",\"%s\":[", name);
	for (it = 0; it < n; it++) printf("%s%d", it ? "," : "", values[it]);
	printf("]");
}

int main(int argc, char **argv) {
	const spec *c = NULL;
	int i, j, it;
	int codes[MAXREP], warmup[MAXREP], finite[MAXREP], zero_slope[MAXREP];
	uint64_t hashes[MAXREP];
	int unchanged[MAXREP], white_unchanged[MAXREP], table_written[MAXREP];
	int local_blocks[MAXREP], cal_valid[MAXREP], want_calib[MAXREP], cache_calls[MAXREP];
	double *white, *dark, **iwhite, reftemp;
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
	impl.refinvalidsampt = WARMUP;
	impl.mmode = 0;
	impl.ms[0].reflective = 1;
	dark = dvector(-1, NRAW - 1);
	for (j = -1; j < NRAW; j++)
		dark[j] = j < 0 ? 20.0 : DARK;
	impl.ms[0].dark_data = dark;
	white = dvector(-1, NRAW - 1);
	iwhite = dmatrix(0, 1, -1, NRAW - 1);          /* as munki_imp.c:663 sizes it */
	impl.ms[0].white_data = white;
    impl.ms[0].iwhite_data = iwhite;
    impl.ms[0].inttime = INTTIME;
    g_dead_band = c->dead_band;
    g_led = c->led;
	g_skipped = 3;

	if (c->kind == KIND_CONSUMERS) {
		int a, an, cu, cz;
		g_local_blocks = 0;
		consumers_probe(&meter, &a, &an, &cu, &cz);
		printf("{\"case\":\"%s\",\"iterations\":1,\"avg_inconsistent\":[%d],"
		       "\"avg_inconsistent_nan_band\":[%d],\"converted_finite_unused\":[%d],"
		       "\"converted_finite_zero_coef\":[%d],\"local_blocks\":[%d]}\n",
		       c->name, a, an, cu, cz, g_local_blocks);
		free_dvector(white, -1, NRAW - 1);
		free_dvector(dark, -1, NRAW - 1);
		free_dmatrix(iwhite, 0, 1, -1, NRAW - 1);
		return 0;
	}

	if (c->kind == KIND_READ) {
		for (it = 0; it < c->repeat; it++) {
			fill_table(iwhite, c->table);
			impl.ms[0].reftemp = REFCODE;
			impl.ms[0].cal_valid = 1;
			impl.ms[0].want_calib = 0;
			g_overflow_sample = c->overflow;
			g_local_blocks = 0;
			codes[it] = read_probe(&meter, c->nummeas);
			finite[it] = g_read_finite;
			hashes[it] = g_read_hash;
			local_blocks[it] = g_local_blocks;
			cal_valid[it] = impl.ms[0].cal_valid;
			want_calib[it] = impl.ms[0].want_calib;
		}
		printf("{\"case\":\"%s\",\"iterations\":%d", c->name, c->repeat);
		print_ints("codes", codes, c->repeat);
		printf(",\"finite\":[");
		for (it = 0; it < c->repeat; it++) {
			printf("%s", it ? "," : "");
			if (finite[it] < 0) printf("null");
			else printf("%d", finite[it]);
		}
		printf("]");
		print_ints("local_blocks", local_blocks, c->repeat);
		print_ints("cal_valid", cal_valid, c->repeat);
		print_ints("want_calib", want_calib, c->repeat);
		printf(",\"output_fnv\":[");
		for (it = 0; it < c->repeat; it++) {
			printf("%s", it ? "," : "");
			if (finite[it] < 0) printf("null");
			else printf("\"%016llx\"", (unsigned long long)hashes[it]);
		}
		printf("]}\n");
		free_dvector(white, -1, NRAW - 1);
		free_dvector(dark, -1, NRAW - 1);
		free_dmatrix(iwhite, 0, 1, -1, NRAW - 1);
		return 0;
	}

	for (it = 0; it < c->repeat; it++) {
		uint64_t h = 1469598103934665603ull;
		int all_finite = 1, all_zero = 1, all_unchanged = 1, all_white = 1;
		int any_table = 0;
		for (j = -1; j < NRAW; j++) {
			white[j] = SENTINEL; iwhite[0][j] = SENTINEL; iwhite[1][j] = SENTINEL;
		}
        impl.ms[0].reftemp = SENTINEL;
        impl.ms[0].cal_valid = !c->first;
        impl.ms[0].want_calib = 0;
        g_requested = -1; g_cache_calls = 0; g_local_blocks = 0;
        codes[it] = caller_probe(&meter, c->nummeas);
        reftemp = impl.ms[0].reftemp;
        cal_valid[it] = impl.ms[0].cal_valid;
        want_calib[it] = impl.ms[0].want_calib;
        cache_calls[it] = g_cache_calls;
        local_blocks[it] = g_local_blocks;
        if (reftemp != SENTINEL) all_unchanged = 0;
		warmup[it] = g_requested < 0 ? -1 : g_requested - c->nummeas;
		for (j = -1; j < NRAW; j++) {
            if (white[j] != SENTINEL)
                all_unchanged = all_white = 0;
            if (iwhite[0][j] != SENTINEL || iwhite[1][j] != SENTINEL)
                all_unchanged = 0, any_table = 1;
			if (!isfinite(white[j]) || !isfinite(iwhite[0][j]) || !isfinite(iwhite[1][j]))
				all_finite = 0;
			if (iwhite[1][j] != 0.0)
				all_zero = 0;
			h = fnv_add(fnv_add(fnv_add(h, white[j]), iwhite[0][j]), iwhite[1][j]);
		}
		unchanged[it] = all_unchanged;
		white_unchanged[it] = all_white;
		table_written[it] = any_table;
		finite[it] = all_finite; zero_slope[it] = all_zero;
		hashes[it] = fnv_add(h, reftemp);
	}

	printf("{\"case\":\"%s\",\"iterations\":%d", c->name, c->repeat);
	print_ints("codes", codes, c->repeat);
	print_ints("warmup", warmup, c->repeat);
	print_ints("finite", finite, c->repeat);
	print_ints("zero_slope", zero_slope, c->repeat);
	print_ints("unchanged", unchanged, c->repeat);
	print_ints("white_unchanged", white_unchanged, c->repeat);
	print_ints("table_written", table_written, c->repeat);
	print_ints("local_blocks", local_blocks, c->repeat);
	print_ints("cal_valid", cal_valid, c->repeat);
	print_ints("want_calib", want_calib, c->repeat);
	print_ints("cache_calls", cache_calls, c->repeat);
	printf(",\"output_fnv\":[");
	for (it = 0; it < c->repeat; it++)
		printf("%s\"%016llx\"", it ? "," : "", (unsigned long long)hashes[it]);
	printf("]}\n");

	free_dvector(white, -1, NRAW - 1);
	free_dvector(dark, -1, NRAW - 1);
	free_dmatrix(iwhite, 0, 1, -1, NRAW - 1);
	return 0;
}
