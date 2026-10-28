/* Source-bound i1Pro / ColorMunki restored integration time check.
 * Runs the exact restore conjunct and clamp from *_restore_calibration and the
 * exact clock conversion from *_trigger_one_measure. A restored adaptive time
 * must be finite with a usable range, and then lies in [min, max]; a restored
 * gain mode is 0, or 1 only where high gain is set (i1Pro: maxgain from the
 * build and instrument; ColorMunki: the mode's auto_gain); a non-adaptive
 * mode's valid dark must be at the mode's gain; a trigger
 * converts to int only a clock count that int holds (built with UBSan
 * float-cast-overflow, so a NaN or out-of-range conversion traps). No device
 * code runs. FAMILY 1 is the i1Pro, 2 the ColorMunki.
 */
#include <float.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>

#include "codes.inc"

#if FAMILY == 1
enum { instI1Pro, instI1Pro2 };
#endif

typedef struct { int adaptive; double inttime; int gainmode, dark_gain_mode, auto_gain, dark_valid; } state;
typedef struct { double min_int_time, max_int_time, intclkp; } imp;
typedef struct { int dtype; } dev;

static int maxgain, gain, dark_gain;	/* The restored gain modes and the limit */
static int dark_valid = 1;		/* Whether the restored dark is valid */

/* 1 if restored (time in *out), 0 if the mode keeps its own calibration */
static int restore(state *s, imp *m, double t, double *out) {
	state ts;
	ts.adaptive = s->adaptive;
	ts.inttime = t;
	ts.gainmode = gain;
	ts.dark_gain_mode = dark_gain;
	ts.dark_valid = dark_valid;
	s->auto_gain = maxgain;
	if (1
#include "condition.inc"
	) {
		s->inttime = ts.inttime;
#include "clamp.inc"
		*out = s->inttime;
		return 1;
	}
	return 0;
}

static int trigger(dev *p, imp *m, double *inttime, int *clocks) {
	double dintclocks;
	int intclocks;
	(void)p;
#include "trigger.inc"
	*clocks = intclocks;
	return CODE_OK;
}

static int check(int condition, const char *name, double a, double b) {
	if (!condition) printf("FAIL %s: %g %g\n", name, a, b);
	return condition;
}

int main(void) {
	double nan = 0.0 / 0.0, inf = 1.0 / 0.0, out = 0.0;
	static const double ts[] = {-1e300, -1.0, 0.0, 1e-300, 0.001, 0.0047, 0.5, 1.0, 2.0,
	                            2.0000000000000004, 5.0, 1e300};
	static const double clkp[] = {-1e-6, 0.0, 1e-12, 1e-6, 36e-6, 68e-6, 1e-3, 1.0};
	static const double times[] = {-1e300, -1e6, -1.0, 0.0, 1e-9, 0.0047, 1.0, 2.0, 3.0,
	                               100.0, 1e6, 1e300};
	state s;
	imp m;
	dev d;
	int i, j, k, restored, clocks = 0, kept = 0, refused = 0, converted = 0, ok = 1;

	m.min_int_time = 0.0047; m.max_int_time = 2.0;
	s.adaptive = 1;
	/* Named restores */
	restored = restore(&s, &m, 1.0, &out);
	ok &= check(restored && out == 1.0, "in range kept", 1.0, out);
	restored = restore(&s, &m, 0.001, &out);
	ok &= check(restored && out == m.min_int_time, "low clamped", 0.001, out);
	restored = restore(&s, &m, 5.0, &out);
	ok &= check(restored && out == m.max_int_time, "high clamped", 5.0, out);
	restored = restore(&s, &m, -1.0, &out);
	ok &= check(restored && out == m.min_int_time, "negative clamped", -1.0, out);
	ok &= check(!restore(&s, &m, nan, &out), "NaN refused", nan, 0.0);
	ok &= check(!restore(&s, &m, inf, &out), "infinity refused", inf, 0.0);
	ok &= check(!restore(&s, &m, -inf, &out), "negative infinity refused", -inf, 0.0);
	{	/* No usable range: refuse a finite time too */
		static const double ranges[][2] = {{0.0, 2.0}, {-1.0, 2.0}, {0.0047, 0.001},
		                                   {0.0047, 1.0/0.0}};
		for (i = 0; i < 4; i++) {
			m.min_int_time = ranges[i][0]; m.max_int_time = ranges[i][1];
			ok &= check(!restore(&s, &m, 1.0, &out), "unusable range refused",
			            ranges[i][0], ranges[i][1]);
		}
		m.min_int_time = nan; m.max_int_time = 2.0;
		ok &= check(!restore(&s, &m, 1.0, &out), "NaN minimum refused", nan, 2.0);
		m.min_int_time = 0.0047; m.max_int_time = nan;
		ok &= check(!restore(&s, &m, 1.0, &out), "NaN maximum refused", 0.0047, nan);
	}
	/* A non-adaptive time is outside this block; its own conjunct requires the */
	/* mode's time within 0.01 s. Here it is restored unchanged. */
	m.min_int_time = 0.0047; m.max_int_time = 2.0;
	s.adaptive = 0;
	restored = restore(&s, &m, 5.0, &out);
	ok &= check(restored && out == 5.0, "non-adaptive unchanged", 5.0, out);

	/* Restored gain modes: 0, or 1 only where high gain is set */
	{
		static const int pairs[][2] = {{0, 0}, {1, 1}, {1, 0}, {0, 1}, {2, 2}, {2, 0}, {0, 2},
		                               {-1, -1}, {-1, 0}, {0, -1}};
		m.min_int_time = 0.0047; m.max_int_time = 2.0;
		s.adaptive = 1;
		for (k = 0; k < 2; k++) {
			maxgain = k;
			for (i = 0; i < (int)(sizeof(pairs) / sizeof(pairs[0])); i++) {
				int expect = pairs[i][0] >= 0 && pairs[i][0] <= k && pairs[i][1] >= 0
				          && pairs[i][1] <= k;
				gain = pairs[i][0]; dark_gain = pairs[i][1];
				ok &= check(restore(&s, &m, 1.0, &out) == expect, "gain mode restore",
				            gain * 10 + dark_gain, k);
			}
		}
		maxgain = gain = dark_gain = 0;
	}

	/* A non-adaptive mode keeps a valid dark only at its own gain; an adaptive
	 * mode (the loop above) measures its dark again, and an invalid dark is not
	 * used. High gain is set so that a mismatch can occur. */
	{
		static const int pairs[][2] = {{0, 0}, {1, 1}, {1, 0}, {0, 1}};
		m.min_int_time = 0.0047; m.max_int_time = 2.0;
		maxgain = 1;
		s.adaptive = 0;
		for (k = 0; k < 2; k++) {
			dark_valid = k;
			for (i = 0; i < 4; i++) {
				int expect = !dark_valid || pairs[i][0] == pairs[i][1];
				gain = pairs[i][0]; dark_gain = pairs[i][1];
				ok &= check(restore(&s, &m, 1.0, &out) == expect, "dark gain pairing",
				            gain * 10 + dark_gain, k);
			}
		}
		maxgain = gain = dark_gain = 0;
		dark_valid = 1;
	}

	/* Every kept adaptive time lies in [min, max] */
	s.adaptive = 1;
	for (i = 0; i < (int)(sizeof(ts) / sizeof(ts[0])); i++) {
		for (j = 0; j < (int)(sizeof(ts) / sizeof(ts[0])); j++) {
			m.min_int_time = ts[i]; m.max_int_time = ts[j];
			for (k = 0; k < (int)(sizeof(ts) / sizeof(ts[0])); k++) {
				if (restore(&s, &m, ts[k], &out)) {
					kept++;
					ok &= check(out >= m.min_int_time && out <= m.max_int_time && m.min_int_time > 0.0,
					            "kept time outside its range", ts[k], out);
				} else {
					refused++;
				}
			}
		}
	}

	/* Named triggers */
#if FAMILY == 1
	d.dtype = instI1Pro; m.intclkp = 68e-6; m.min_int_time = 0.004716;
	{ double t = 1.0; int result = trigger(&d, &m, &t, &clocks);
	  ok &= check(result == CODE_OK && clocks == 14706,
	                              "i1Pro clocks", 1.0, clocks); }
	{ double t = 5.0; ok &= check(trigger(&d, &m, &t, &clocks) == CODE_TOOBIG,
	                              "i1Pro above 65535 clocks", 5.0, 0.0); }
	{ double t = nan; ok &= check(trigger(&d, &m, &t, &clocks) == CODE_TOOBIG,
	                              "i1Pro NaN", nan, 0.0); }
	d.dtype = instI1Pro2; m.intclkp = 36e-6;
	{ double t = 2.0; int result = trigger(&d, &m, &t, &clocks);
	  ok &= check(result == CODE_OK && clocks == 55556,
	                              "i1Pro2 clocks", 2.0, clocks); }
	{ double t = 1e5; ok &= check(trigger(&d, &m, &t, &clocks) == CODE_TOOBIG,
	                              "i1Pro2 above int", 1e5, 0.0); }
	{ double t = nan; ok &= check(trigger(&d, &m, &t, &clocks) == CODE_TOOBIG,
	                              "i1Pro2 NaN", nan, 0.0); }
#else
	d.dtype = 0; m.intclkp = 1e-6;
	{ double t = 1.0; int result = trigger(&d, &m, &t, &clocks);
	  ok &= check(result == CODE_OK && clocks == 1000000,
	                              "ColorMunki clocks", 1.0, clocks); }
	{ double t = 1e300; ok &= check(trigger(&d, &m, &t, &clocks) == CODE_TOOBIG,
	                                "ColorMunki above int", 1e300, 0.0); }
	{ double t = -1e300; ok &= check(trigger(&d, &m, &t, &clocks) == CODE_TOOSMALL,
	                                 "ColorMunki below int", -1e300, 0.0); }
	{ double t = nan; ok &= check(trigger(&d, &m, &t, &clocks) == CODE_TOOBIG,
	                              "ColorMunki NaN", nan, 0.0); }
#endif
	/* Every time and clock period: a conversion the sanitizer accepts, or a refusal. */
	/* The i1Pro's period is never negative: an unsigned byte in microseconds */
	/* (i1pro_getmcmode's pbuf[4]) or the i1Pro2's checked 36 us; and its trigger */
	/* first raises a time below min_int_time to it (a census checks that line). */
	m.min_int_time = 0.004716;
	for (k = 0; k < 2; k++) {
		d.dtype = k;
		for (i = 0; i < (int)(sizeof(clkp) / sizeof(clkp[0])); i++) {
			if (FAMILY == 1 && clkp[i] < 0.0)
				continue;
			for (j = 0; j <= (int)(sizeof(times) / sizeof(times[0])); j++) {
				double t = j < (int)(sizeof(times) / sizeof(times[0])) ? times[j] : nan;
				m.intclkp = clkp[i];
				if (FAMILY == 1 && t < m.min_int_time)
					t = m.min_int_time;
				if (trigger(&d, &m, &t, &clocks) == CODE_OK)
					converted++;
			}
		}
	}
	printf("%d kept and %d refused restores, %d conversions\n", kept, refused, converted);
	ok &= check(kept > 0 && refused > 0 && converted > 0, "sweep exercised", kept, refused);
	return ok ? 0 : 1;
}
