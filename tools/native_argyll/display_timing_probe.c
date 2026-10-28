/* Source-bound display-delay proof. No transport, hardware or Argyll executable. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <float.h>
#include <limits.h>

typedef int inst_code;
enum { inst_ok = 0, inst_no_coms = 1, inst_no_init = 2,
       inst_internal_error = 3, inst_misread = 4, inst_coms_fail = 5,
       inst_decode_fail = 6 };
typedef struct { int debug; } a1log;
typedef struct { a1log *log; } icoms;
typedef struct {
	int gotcoms, inited, th_en;
	double whitestamp;
	a1log *log;
	icoms *icom;
	void *lock;
} inst;
typedef inst i1d3;
typedef inst kleink10;

#define a1logd(...) ((void)0)
#define a1loge(...) ((void)0)
#define MAX_MES_SIZE 500
#define ec_ec 0

static double clock_us;
static int calls, locks, unlocks;
static int scenario, driver;
enum { CONTROL, AGED, LAST, SINGLE, TWO, ZERO, MEASURE_ERROR,
       COMMAND_ERROR, DECODE_ERROR, FLAT_ZERO, FLAT_POSITIVE, REVERSED,
       REVERSED_NEGATIVE,
       MISSING_MARKER, NAN_SAMPLE, NAN_UNUSED, INF_SAMPLE, OVERFLOW_SUM,
       NAN_CLOCK, NAN_STAMP, OLD_STAMP, TINY_SPACING, FUTURE_STAMP };
enum { I1D3, KLEIN };

double usec_time(void) { return clock_us; }

static double interval(void) {
	if (scenario == SINGLE) return 2.1;
	if (scenario == TWO) return calls == 2 ? 2.1 : 0.05;
	if (scenario == LAST) {
		if (calls == 1 || calls == 2) return 0.05;
		if (calls == 3) return 0.10;
		return 2.0;
	}
	if (scenario == ZERO) return 0.0;
	if (scenario == TINY_SPACING) return 0.00001;
	return driver == I1D3 ? 0.002 : 0.02;
}

static double level(void) {
	int at_transition = calls >= (driver == I1D3 ? 150 : 15);
	if (scenario == LAST) return calls >= 4 ? 100.0 : 0.0;
	if (scenario == FLAT_ZERO) return 0.0;
	if (scenario == FLAT_POSITIVE) return 100.0;
	if (scenario == REVERSED) return at_transition ? 0.0 : 100.0;
	if (scenario == REVERSED_NEGATIVE) return at_transition ? -2e8 : -1e8;
	if (scenario == MISSING_MARKER) return at_transition ? 0.0 : -5e-324;
	if (scenario == NAN_SAMPLE && calls == (driver == I1D3 ? 150 : 15)) return NAN;
	if (scenario == INF_SAMPLE && calls == (driver == I1D3 ? 150 : 15)) return INFINITY;
	return at_transition ? 100.0 : 0.0;
}

static inst_code i1d3_freq_measure(i1d3 *p, double *inttime, double rgb[3]) {
	(void)p; (void)inttime;
	calls++;
	if (scenario == MEASURE_ERROR && calls == 2) return inst_coms_fail;
	clock_us += interval() * 1000000.0;
	if (scenario == NAN_CLOCK && calls == 150) clock_us = NAN;
	rgb[0] = level(); rgb[1] = 0.0; rgb[2] = 0.0;
	if (scenario == NAN_UNUSED && calls == 150) rgb[2] = NAN;
	if (scenario == OVERFLOW_SUM && calls == 150) {
		rgb[0] = 1e308; rgb[1] = 1e308;
	}
	return inst_ok;
}

static inst_code k10_command(kleink10 *p, const char *cmd, char *reply,
                            int size, int *bread, int expect, int ec, double timeout) {
	(void)p; (void)cmd; (void)reply; (void)size; (void)expect; (void)ec; (void)timeout;
	calls++;
	if (scenario == COMMAND_ERROR && calls == 2) return inst_coms_fail;
	clock_us += interval() * 1000000.0;
	if (scenario == NAN_CLOCK && calls == 15) clock_us = NAN;
	*bread = 1;
	return inst_ok;
}

static inst_code decodeN5(kleink10 *p, double xyz[3], void *unused,
                          char *reply, int bread) {
	(void)p; (void)unused; (void)reply; (void)bread;
	if (scenario == DECODE_ERROR && calls == 2) return inst_decode_fail;
	xyz[0] = 0.0; xyz[1] = level(); xyz[2] = 0.0;
	if (scenario == NAN_UNUSED && calls == 15) xyz[0] = NAN;
	return inst_ok;
}

#define amutex_lock(lock) ((void)(lock), locks++)
#define amutex_unlock(lock) ((void)(lock), unlocks++)

#define NDSAMPS 400
#define DINTT 0.005
#define NDMXTIME 2.0
#include "i1d3.inc"
#undef NDSAMPS
#undef DINTT
#undef NDMXTIME

#define NDSAMPS 40
#define NDMXTIME 2.0
#include "kleink10.inc"
#undef NDSAMPS
#undef NDMXTIME

int main(int argc, char **argv) {
	a1log log = { 7 };
	icoms icom = { &log };
	inst p;
	int display = -999, instrument = -999, result;
	if (argc != 3) return 2;
	driver = strcmp(argv[1], "i1d3") == 0 ? I1D3 : KLEIN;
	if (strcmp(argv[2], "control") == 0) scenario = CONTROL;
	else if (strcmp(argv[2], "aged") == 0) scenario = AGED;
	else if (strcmp(argv[2], "last") == 0) scenario = LAST;
	else if (strcmp(argv[2], "single") == 0) scenario = SINGLE;
	else if (strcmp(argv[2], "two") == 0) scenario = TWO;
	else if (strcmp(argv[2], "zero") == 0) scenario = ZERO;
	else if (strcmp(argv[2], "measure_error") == 0) scenario = MEASURE_ERROR;
	else if (strcmp(argv[2], "command_error") == 0) scenario = COMMAND_ERROR;
	else if (strcmp(argv[2], "decode_error") == 0) scenario = DECODE_ERROR;
	else if (strcmp(argv[2], "flat_zero") == 0) scenario = FLAT_ZERO;
	else if (strcmp(argv[2], "flat_positive") == 0) scenario = FLAT_POSITIVE;
	else if (strcmp(argv[2], "reversed") == 0) scenario = REVERSED;
	else if (strcmp(argv[2], "reversed_negative") == 0) scenario = REVERSED_NEGATIVE;
	else if (strcmp(argv[2], "missing_marker") == 0) scenario = MISSING_MARKER;
	else if (strcmp(argv[2], "nan_sample") == 0) scenario = NAN_SAMPLE;
	else if (strcmp(argv[2], "nan_unused") == 0) scenario = NAN_UNUSED;
	else if (strcmp(argv[2], "inf_sample") == 0) scenario = INF_SAMPLE;
	else if (strcmp(argv[2], "overflow_sum") == 0) scenario = OVERFLOW_SUM;
	else if (strcmp(argv[2], "nan_clock") == 0) scenario = NAN_CLOCK;
	else if (strcmp(argv[2], "nan_stamp") == 0) scenario = NAN_STAMP;
	else if (strcmp(argv[2], "old_stamp") == 0) scenario = OLD_STAMP;
	else if (strcmp(argv[2], "tiny_spacing") == 0) scenario = TINY_SPACING;
	else if (strcmp(argv[2], "future_stamp") == 0) scenario = FUTURE_STAMP;
	else return 2;
	clock_us = (scenario == AGED ? 3600.0 : scenario == OLD_STAMP ? 3000000.0 : 0.0)
	           * 1000000.0;
	calls = locks = unlocks = 0;
	memset(&p, 0, sizeof(p));
	p.gotcoms = p.inited = p.th_en = 1;
	p.whitestamp = scenario == NAN_STAMP ? NAN
                     : scenario == OLD_STAMP ? 0.0
                     : scenario == FUTURE_STAMP ? 2147483928000.0 : clock_us;
	p.log = &log; p.icom = &icom;
	if (driver == I1D3)
		result = i1d3_meas_delay(&p, &display, &instrument);
	else
		result = k10_meas_delay(&p, &display, &instrument);
	printf("%d %d %d %d %d %d %d %d\n", result, calls, locks, unlocks,
	       log.debug, p.th_en, display, instrument);
	return 0;
}
