/* Source-bound i1Pro adaptive-scan gain-switch check.
 * Runs the exact scan calibration branch with scripted measurement helpers. Each
 * dark and white carries the gain it was taken at, so the probe sees which dark a
 * white subtracted (i1pro_whitemeasure subtracts s->dark_data). This proves state
 * transitions and call order, not optical or hardware behavior.
 */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#include "codes.inc"

struct i1pro_state {
	int adaptive, gainmode, dark_valid, dark_gain_mode, idark_valid;
	time_t ddate, iddate;
	double wcaltime, dcaltime, inttime, dark_int_time;
	double *cal_factor[2], *white_data, *dark_data, *idark_data[4];
};
typedef struct i1pro_state i1pro_state;
struct i1proimp { int mmode, transwarn, nraw; double highgain; };
typedef struct i1proimp i1proimp;
struct i1pro { int dtype; void *log; };
typedef struct i1pro i1pro;

static i1pro_state *probe_state;
static int fail_white, white_calls, dark_calls, white_dark_gain;
static double scales[2];

static void a1logd(void *log, int level, const char *fmt, ...) {
	(void)log; (void)level; (void)fmt;
}
static int i1pro_comp_nummeas(i1pro *p, double total, double interval) {
	(void)p; (void)total; (void)interval; return 1;
}
/* A white records which gain's dark it subtracted. */
static i1pro_code i1pro_whitemeasure(i1pro *p, double *f0, double *f1, double *white,
	double *scale, int nummeas, double *inttime, int gainmode, double targoscale, int ltocmode) {
	(void)p; (void)f0; (void)f1; (void)nummeas; (void)inttime; (void)targoscale; (void)ltocmode;
	white_calls++;
	white_dark_gain = (int)probe_state->dark_data[0];
	white[0] = gainmode;
	*scale = scales[white_calls > 1];
	return white_calls == fail_white ? I1PRO_RD_SENSORSATURATED : I1PRO_OK;
}
static i1pro_code i1pro_dark_measure(i1pro *p, double *data, int nummeas,
	double *inttime, int gainmode) {
	(void)p; (void)nummeas; (void)inttime;
	dark_calls++;
	data[-1] = data[0] = gainmode;
	return I1PRO_OK;
}

static i1pro_code probe_scan(i1pro *p, i1proimp *m, i1pro_state *s, double scale,
	inst_cal_cond *calc) {
	int nummeas = 0, mmode = m->mmode;
	time_t cdate = 200;
	i1pro_code ev = I1PRO_OK;
	(void)nummeas;
/* BEGIN_EXTRACT */
#include "block.inc"
/* END_EXTRACT */
	return ev;
}

static int check(int condition, const char *name) {
	if (!condition) printf("FAIL %s\n", name);
	return condition;
}

int main(void) {
	i1pro p;
	i1proimp m;
	i1pro_state st, *s = &st;
	double dark[3], white[1], f0[1], f1[1], idark[4][3];
	inst_cal_cond calc;
	i1pro_code ev;
	int ok = 1, scenario, i;
	/* 0: saturated white at high gain, measured dark; 1: same with an interpolated
	 * dark; 2: the low-gain white fails; 3: no switch; 4: switch, no dark to use;
	 * 5-7: the dim-white warning judges the final white (5: dim only before the
	 * new dark, 6: dim only after it, 7: dim without a switch). */
	for (scenario = 0; scenario <= 7; scenario++) {
		p.dtype = instI1Pro; p.log = NULL;
		m.mmode = 0; m.transwarn = 0; m.nraw = 2; m.highgain = 100.0;
		s->adaptive = 1; s->gainmode = scenario == 3 || scenario == 7 ? 0 : 1;
		s->dark_valid = 1; s->dark_gain_mode = s->gainmode; s->idark_valid = scenario != 4;
		s->ddate = 100; s->iddate = 150; s->wcaltime = s->dcaltime = 1.0;
		s->inttime = 0.01; s->dark_int_time = 0.01;
		s->cal_factor[0] = f0; s->cal_factor[1] = f1; s->white_data = white;
		s->dark_data = dark + 1; dark[0] = dark[1] = dark[2] = s->gainmode;
		for (i = 0; i < 4; i++) {
			s->idark_data[i] = idark[i] + 1;
			idark[i][0] = idark[i][1] = idark[i][2] = i < 2 ? 0 : 1;
		}
		probe_state = s;
		white_calls = dark_calls = 0; white_dark_gain = -1;
		fail_white = scenario == 2 ? 1 : 0;
		scales[0] = scenario == 5 ? 7.0 : scenario == 6 ? 5.0 : 1.0;
		scales[1] = scenario == 5 ? 5.0 : scenario == 6 ? 7.0 : 1.0;
		calc = scenario == 1 || scenario == 4 ? (inst_cal_cond)0 : inst_calc_man_ref_white;
		ev = probe_scan(&p, &m, s, scenario == 7 ? 7.0 : scenario == 3 ? 1.0 : 0.0, &calc);
		if (scenario <= 1) {
			ok &= check(ev == I1PRO_OK && s->gainmode == 0, "switch to low gain succeeds");
			ok &= check(s->dark_valid == 1 && s->dark_gain_mode == 0 && (int)dark[1] == 0,
			            "dark is for the new gain");
			ok &= check(white_dark_gain == 0,
			            "the final white subtracted the new gain's dark");
			printf("scenario %d: whites %d darks %d last white dark gain %d\n",
			       scenario, white_calls, dark_calls, white_dark_gain);
		} else if (scenario == 2) {
			ok &= check(ev != I1PRO_OK, "failed low-gain white is reported");
			ok &= check(s->dark_valid == 0, "the other gain's dark is not left valid");
		} else if (scenario == 3) {
			ok &= check(ev == I1PRO_OK && white_calls == 0 && dark_calls == 1,
			            "without a switch no extra white is measured");
		} else if (scenario == 4) {
			ok &= check(ev == I1PRO_INT_NOINTERPDARK && s->dark_valid == 0,
			            "a switch without a dark for the new gain leaves none valid");
		} else {
			ok &= check(ev == I1PRO_OK, "dim-white scenario succeeds");
			ok &= check(((m.transwarn & 2) != 0) == (scenario != 5),
			            "the dim-white warning judges the final white");
		}
	}
	return ok ? 0 : 1;
}
