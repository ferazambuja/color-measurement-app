/* DTP41 mode activation, strip fields and calibration (package rounds 9, 12,
 * 14 and 15).
 *
 * functions.inc holds the exact build_strip(), activate_mode(),
 * dtp41_get_n_a_cals() and dtp41_calibrate(), and dtp41_read_strip() and
 * dtp41_read_sample() up to the first command after activation, closed with
 * a return. A scripted dtp41_command() records each
 * command and fails the one asked for. A failed mode command must be returned
 * with nothing more sent and must leave the mode unknown, so the next
 * activation sends both commands. A strip value that doesn't fit its field is
 * refused before any command, with the requested mode unchanged. Calibration
 * needs and the calibration follow the requested mode, with nothing sent. No
 * transport code runs.
 */
#include <stdio.h>
#include <string.h>
#include <math.h>

#include "codes.inc"

typedef struct { int gotcoms, inited; inst_mode mode, lastmode; void *log; int need_cal; } dtp41;
typedef dtp41 inst;
typedef struct { int unused; } ipatch;
typedef int instClamping;
#define a1logw(log, ...) ((void)(log))
#define a1logd(log, ...) ((void)(log))

#define REF (inst_mode_reflection)
#define TRANS (inst_mode_transmission)
#define SPOT (inst_mode_spot)
#define STRIP (inst_mode_strip)

static char sent[8][200];
static int nsent, fail_at = -1, refusing;
#define FAILED inst_coms_fail

/* A refused strip must send no command. Count an unexpected call without
 * reading its buffer, which may be uninitialized in the original or a mutant. */
static inst_code dtp41_command(dtp41 *p, char *in, char *out, int bsize, double to) {
	size_t i, n;
	(void)p; (void)out; (void)bsize; (void)to;
	if (refusing) {
		if (nsent < 8)
			strcpy(sent[nsent], "<unexpected command>");
		return nsent++ == fail_at ? FAILED : inst_ok;
	}
	n = strnlen(in, sizeof sent[0] - 1);
	if (nsent < 8) {
		for (i = 0; i < n; i++)
			sent[nsent][i] = (in[i] >= 32 && in[i] <= 126) || in[i] == '\r' ? in[i] : '?';
		sent[nsent][n] = '\0';
	}
	return nsent++ == fail_at ? FAILED : inst_ok;
}

static inst_code activate_mode(dtp41 *p);
#include "functions.inc"

static dtp41 meter = { 1, 1, inst_mode_none, inst_mode_none, NULL };
static int failures;

static void start(inst_mode mode, inst_mode lastmode, int fail) {
	meter.mode = mode;
	meter.lastmode = lastmode;
	nsent = 0;
	fail_at = fail;
	refusing = 0;
	memset(sent, 0, sizeof sent);
}

/* The commands sent, each without its CR, joined by '|' */
static const char *commands(void) {
	static char text[sizeof sent + 8];
	int i;
	text[0] = '\0';
	for (i = 0; i < nsent && i < 8; i++) {
		size_t n = strcspn(sent[i], "\r");
		if (i > 0)
			strcat(text, "|");
		strncat(text, sent[i], n);
	}
	return text;
}

/* A calibration step's result, request and condition, with nothing sent */
static void expect_cal(const char *name, inst_code got, inst_code want,
                       inst_cal_type calt, inst_cal_type want_calt,
                       inst_cal_cond calc, inst_cal_cond want_calc, int need_cal) {
	if (got != want || calt != want_calt || calc != want_calc
	 || meter.need_cal != need_cal || nsent != 0) {
		printf("FAIL %s: returned 0x%x (want 0x%x), calibration 0x%x (want 0x%x), "
		       "condition 0x%x (want 0x%x), needs calibration %d (want %d), commands %d\n",
		       name, (unsigned)got, (unsigned)want, (unsigned)calt, (unsigned)want_calt,
		       (unsigned)calc, (unsigned)want_calc, meter.need_cal, need_cal, nsent);
		failures++;
	}
}

static void expect(const char *name, inst_code got, inst_code want, const char *sends,
                   inst_mode mode, inst_mode lastmode) {
	if (got != want || (refusing && nsent != 0) || strcmp(commands(), sends) != 0
	 || meter.mode != mode || meter.lastmode != lastmode) {
		printf("FAIL %s: returned 0x%x (want 0x%x), sent \"%s\" (want \"%s\"), "
		       "mode 0x%x/0x%x (want 0x%x/0x%x), commands %d\n", name, (unsigned)got, (unsigned)want,
		       commands(), sends, (unsigned)meter.mode, (unsigned)meter.lastmode,
		       (unsigned)mode, (unsigned)lastmode, nsent);
		failures++;
	}
}

static inst_code strip(int npatch, double pwid, double gwid, double twid) {
	return dtp41_read_strip((inst *)&meter, "unused", npatch, "unused", 0, pwid, gwid, twid, NULL);
}

int main(void) {
	static const struct { const char *name; inst_mode illum; int npatch; double pwid, gwid, twid; }
	refused[] = {
		{ "no patches", REF, 0, 10.0, 2.0, 0.0 },
		{ "four-digit count", REF, 1000, 10.0, 2.0, 0.0 },
		{ "negative patch", REF, 10, -0.01, 2.0, 0.0 },
		{ "patch of 100 mm", REF, 10, 100.0, 2.0, 0.0 },
		{ "patch rounding to 100.00", REF, 10, 99.996, 2.0, 0.0 },
		{ "NaN patch", REF, 10, NAN, 2.0, 0.0 },
		{ "negative gap", REF, 10, 10.0, -1.0, 0.0 },
		{ "gap rounding to 100.00", REF, 10, 10.0, 99.996, 0.0 },
		{ "NaN gap", REF, 10, 10.0, NAN, 0.0 },
		{ "negative trailer", TRANS, 10, 10.0, 2.0, -1.0 },
		{ "NaN trailer", TRANS, 10, 10.0, 2.0, NAN },
	};
	static const struct { const char *name; inst_mode illum; int npatch; double pwid, gwid, twid;
	                      const char *definition; } kept[] = {
		{ "one patch", REF, 1, 0.0, 99.99, 0.0, "0010000999908DS" },
		{ "999 patches", REF, 999, 99.99, 0.0, 0.0, "999999900000" "8DS" },
		{ "negative zero widths", REF, 4, -0.0, -0.0, 0.0, "00400000000" "08DS" },
		{ "negative zero trailer", TRANS, 4, 6.0, 0.0, -0.0, "0040600000008" "0000DS" },
		{ "longest trailer", TRANS, 4, 6.0, 0.0, 9999.4, "0040600000008" "9999DS" },
		{ "trailer clamped", TRANS, 4, 6.0, 0.0, 1e6, "0040600000008" "9999DS" },
	};
	static const struct { const char *name; inst_mode mode, lastmode; inst_cal_type white; }
	cals[] = {
		{ "calibration after failed transmission activation", inst_mode_none, TRANS | SPOT,
		  inst_calt_trans_white },
		{ "calibration in transmission", TRANS | SPOT, TRANS | SPOT, inst_calt_trans_white },
		{ "calibration after failed reflection activation", inst_mode_none, REF | SPOT,
		  inst_calt_ref_white },
		{ "calibration in reflection", REF | STRIP, REF | STRIP, inst_calt_ref_white },
	};
	inst_cal_type calt;
	inst_cal_cond calc;
	inst_calc_id_type idtype;
	char id[CALIDLEN];
	inst_code ev;
	int i;

	/* A spot read whose mode command fails returns it and sends nothing more */
	start(REF | STRIP, REF | STRIP, 0);
	ev = dtp41_read_sample((inst *)&meter, "unused", NULL, 0);
	expect("sample activation fails", ev, FAILED, "0013CF", inst_mode_none, REF | SPOT);
	/* The next activation sets both modes again */
	start(meter.mode, meter.lastmode, -1);
	ev = dtp41_read_sample((inst *)&meter, "unused", NULL, 0);
	expect("sample after failure", ev, inst_ok, "0019CF|0013CF|0013CF", REF | SPOT, REF | SPOT);

	/* A strip read whose mode command fails sends no strip definition */
	start(TRANS | SPOT, TRANS | SPOT, 0);
	ev = strip(10, 10.0, 2.0, 5.0);
	expect("strip activation fails", ev, FAILED, "0113CF", inst_mode_none, TRANS | STRIP);
	start(meter.mode, meter.lastmode, -1);
	ev = strip(10, 10.0, 2.0, 5.0);
	expect("strip after failure", ev, inst_ok, "0119CF|0113CF|0101000020008" "0005DS",
	       TRANS | STRIP, TRANS | STRIP);

	/* The trailer follows the requested mode: built before activation */
	start(inst_mode_none, TRANS | SPOT, -1);
	ev = strip(3, 5.0, 0.0, 12.0);
	expect("trailer as requested", ev, inst_ok, "0119CF|0113CF|0030500000008" "0012DS",
	       TRANS | STRIP, TRANS | STRIP);

	/* A value that doesn't fit its field is refused with nothing sent */
	for (i = 0; i < (int)(sizeof refused / sizeof refused[0]); i++) {
		start(refused[i].illum | SPOT, refused[i].illum | SPOT, -1);
		refusing = 1;
		ev = strip(refused[i].npatch, refused[i].pwid, refused[i].gwid, refused[i].twid);
		expect(refused[i].name, ev, inst_bad_parameter, "",
		       refused[i].illum | SPOT, refused[i].illum | SPOT);
	}

	/* Boundary values are sent, a signed zero without its sign */
	for (i = 0; i < (int)(sizeof kept / sizeof kept[0]); i++) {
		char sends[64];
		start(kept[i].illum | STRIP, kept[i].illum | STRIP, -1);
		ev = strip(kept[i].npatch, kept[i].pwid, kept[i].gwid, kept[i].twid);
		snprintf(sends, sizeof sends, "%s", kept[i].definition);
		expect(kept[i].name, ev, inst_ok, sends, kept[i].illum | STRIP, kept[i].illum | STRIP);
	}

	/* Calibration needs follow the requested mode: a failed activation leaves
	 * the mode unknown, and a transmission read needs a transmission white */
	for (i = 0; i < (int)(sizeof cals / sizeof cals[0]); i++) {
		inst_cal_type needed = inst_calt_none, available = inst_calt_none;
		start(cals[i].mode, cals[i].lastmode, -1);
		refusing = 1;
		meter.need_cal = 1;
		ev = dtp41_get_n_a_cals((inst *)&meter, &needed, &available);
		expect_cal(cals[i].name, ev, inst_ok, needed, cals[i].white, available, cals[i].white, 1);
	}

	/* The calibration asks for that white, then completes */
	start(inst_mode_none, TRANS | SPOT, -1);
	refusing = 1;
	meter.need_cal = 1;
	calt = inst_calt_needed;
	calc = inst_calc_none;
	ev = dtp41_calibrate((inst *)&meter, &calt, &calc, &idtype, id);
	expect_cal("transmission white asked after failed activation", ev, inst_cal_setup,
	           calt, inst_calt_trans_white, calc, inst_calc_uop_trans_white, 1);
	ev = dtp41_calibrate((inst *)&meter, &calt, &calc, &idtype, id);
	expect_cal("transmission white done after failed activation", ev, inst_ok,
	           calt, inst_calt_none, calc, inst_calc_uop_trans_white, 0);

	/* Asked for by name, the transmission white is supported */
	start(inst_mode_none, TRANS | SPOT, -1);
	refusing = 1;
	meter.need_cal = 1;
	calt = inst_calt_trans_white;
	calc = inst_calc_none;
	ev = dtp41_calibrate((inst *)&meter, &calt, &calc, &idtype, id);
	expect_cal("transmission white by name after failed activation", ev, inst_cal_setup,
	           calt, inst_calt_trans_white, calc, inst_calc_uop_trans_white, 1);

	/* A reflection read still asks for the reflection white */
	start(REF | SPOT, REF | SPOT, -1);
	refusing = 1;
	meter.need_cal = 1;
	calt = inst_calt_needed;
	calc = inst_calc_none;
	ev = dtp41_calibrate((inst *)&meter, &calt, &calc, &idtype, id);
	expect_cal("reflection white asked", ev, inst_cal_setup,
	           calt, inst_calt_ref_white, calc, inst_calc_uop_ref_white, 1);

	printf("%d failures\n", failures);
	return failures != 0;
}
