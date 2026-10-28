/* Source-bound i1Pro3 restored integration time check.
 * Runs the exact restore check over the modes whose time a calibration file
 * restores (i1pro3_imp.c:2833-2838). Named cases keep or refuse one time each; a
 * refused mode must get its initial time back and be left uncalibrated. A sweep
 * over device clocks the exact init guard accepts then shows that every kept
 * time leaves i1pro3_do_measure()'s scan counts (:12757-12765, :12824-12825,
 * :12843-12845, :12863-12864, :12875-12885, :12897-12899) within int, and that
 * no kept polarized time gives tint 1, where an odd count calls error() (:12769-12770). Polarized
 * calibration writes 2 to 10 minimum times (:8802-8812). No device code runs.
 */
#include <limits.h>
#include <math.h>
#include <stdio.h>

#include "codes.inc"

#define i1p3_no_modes 6		/* The restored modes below, not the driver's 11 */
#define NSEN1 134			/* i1pro3_imp.c:6856 */

typedef struct { int pol, refl; double inttime; int dark_valid, cal_valid; } i1pro3_state;
struct i1pro3imp {
	unsigned int minintclks; double intclkp, min_int_time, max_int_time;
	i1pro3_state ms[i1p3_no_modes];
};
typedef struct i1pro3imp i1pro3imp;
struct i1pro3 { void *log; };
typedef struct i1pro3 i1pro3;
static void a1logd(void *log, int level, const char *fmt, ...) {
	(void)log; (void)level; (void)fmt;
}

static i1pro3_code guard(i1pro3 *p, i1pro3imp *m) {
#include "guard.inc"
	return I1PRO3_OK;
}

static void restore(i1pro3 *p, i1pro3imp *m, double *itime) {
	int i;
/* BEGIN_EXTRACT */
#include "block.inc"
/* END_EXTRACT */
}

/* Polarized spot and scan start at 4, emissive scan and transmissive scan at 2,
 * the adaptive emissive spot and the flash scan at 1 minimum time (:716-897). */
static const struct { int pol, refl; double initial; const char *name; } modes[] = {
	{1, 1, 4.0, "pol spot"}, {1, 1, 4.0, "pol scan"}, {0, 0, 1.0, "emis spot"},
	{0, 0, 2.0, "emis scan"}, {0, 0, 1.0, "amb flash"}, {0, 0, 2.0, "trans scan"},
};

static void setup(i1pro3imp *m, double *itime) {
	int i;
	for (i = 0; i < i1p3_no_modes; i++) {
		m->ms[i].pol = modes[i].pol;
		m->ms[i].refl = modes[i].refl;
		itime[i] = modes[i].initial * m->min_int_time;
		m->ms[i].dark_valid = m->ms[i].cal_valid = 1;
	}
}

/* Restore one mode's time; return 1 if kept, 0 if refused, -1 if inconsistent. */
static int restored(i1pro3 *p, i1pro3imp *m, int mode, double t) {
	double itime[i1p3_no_modes];
	i1pro3_state *s = &m->ms[mode];
	int i;
	setup(m, itime);
	for (i = 0; i < i1p3_no_modes; i++)
		m->ms[i].inttime = itime[i];
	s->inttime = t;
	restore(p, m, itime);
	for (i = 0; i < i1p3_no_modes; i++)	/* The other modes are untouched */
		if (i != mode && (m->ms[i].inttime != itime[i]
		                  || !m->ms[i].dark_valid || !m->ms[i].cal_valid))
			return -1;
	if (s->dark_valid && s->cal_valid && s->inttime == t)
		return 1;
	if (!s->dark_valid && !s->cal_valid && s->inttime == itime[mode])
		return 0;
	return -1;
}

static int check(int condition, const char *name, int mode, double mit, double t) {
	if (!condition) printf("FAIL %s: %s, min %g, restored %g\n", name, modes[mode].name, mit, t);
	return condition;
}

/* i1pro3_do_measure()'s int counts for a scan at this time, as doubles. */
static int counts_fit(i1pro3imp *m, int mode, double inttime) {
	double tint = 1.0, nnskip = 0.0, intclocks, nummeas, nnmeas;
	if (inttime < m->min_int_time)
		inttime = m->min_int_time;
	if (modes[mode].refl) {
		tint = floor(inttime / m->min_int_time + 0.5);
		if (tint > INT_MAX) return 0;
		if (tint > 1)
			tint = 2 * floor(inttime / (2.0 * m->min_int_time) + 0.5);
		else if (tint < 1)
			tint = 1;
		inttime = m->min_int_time * tint;
		nnskip = LEDTURNONMEAS;
		if (tint > 1)
			nnskip = floor(tint/2) * floor((nnskip + floor(tint/2) - 1) / floor(tint/2));
	}
	intclocks = floor(inttime / m->intclkp + 0.5);
	if (intclocks > INT_MAX) return 0;
	if (!modes[mode].refl)
		inttime = intclocks * m->intclkp;
	nummeas = floor(MAXSCANTIME / inttime + 0.5);
	if (nummeas < 1) nummeas = 1;
	nnmeas = modes[mode].refl ? floor(nummeas * tint / 2) + nnskip : nummeas;
	return nnmeas <= INT_MAX
	    && 2.0 * (modes[mode].refl ? MX_NSEN : NSEN1) * nnmeas <= INT_MAX
	    && nnmeas * (modes[mode].refl ? 2 : 1) * intclocks <= INT_MAX;
}

int main(void) {
	static const unsigned int clks[] = {1, 2, 10, 165, 1000, 100000, 10000000, 4294967295u};
	double nan = 0.0 / 0.0, inf = 1.0 / 0.0;
	i1pro3 p;
	i1pro3imp m;
	int mode, k, j, n, r, ok = 1, kept = 0, refused = 0, sweeps = 0;
	p.log = NULL;
	m.max_int_time = MAX_INT_TIME;		/* :6927 */

	/* Named cases at the real minimum time and at one above 1 s, where an */
	/* emissive or transmissive scan's initial time exceeds max_int_time. */
	for (k = 0; k < 2; k++) {
		double mit = k == 0 ? 165 * 1.5277777492883615e-5 : 1.5;
		m.min_int_time = mit;
		for (mode = 0; mode < i1p3_no_modes; mode++) {
			double initial = modes[mode].initial * mit;
			double top = modes[mode].pol ? 10.0 * mit
			           : initial > MAX_INT_TIME ? initial : MAX_INT_TIME;
			const struct { double t; int keep; const char *why; } cases[] = {
				{mit, !modes[mode].pol, "the minimum time (polarized: below 2)"},
				{2.0 * mit, 2.0 * mit <= top, "two minimum times"},
				{nextafter(2.0 * mit, 0.0), !modes[mode].pol && 2.0 * mit <= top,
				 "just below two minimum times"},
				{1.2 * mit, !modes[mode].pol && 1.2 * mit <= top,
				 "1.2 minimum times (polarized: tint 1)"},
				{initial, 1, "the initial time"},
				{top, 1, "the largest calibration sets"},
				{nextafter(top, inf), 0, "above what calibration sets"},
				{nextafter(mit, 0.0), 0, "below the minimum time"},
				{1e-300, 0, "a tiny time"},
				{0.0, 0, "zero"},
				{-mit, 0, "a negative time"},
				{1e300, 0, "a huge time"},
				{inf, 0, "infinity"},
				{-inf, 0, "negative infinity"},
				{nan, 0, "NaN"},
			};
			for (j = 0; j < (int)(sizeof(cases) / sizeof(cases[0])); j++) {
				r = restored(&p, &m, mode, cases[j].t);
				ok &= check(r == cases[j].keep, cases[j].why, mode, mit, cases[j].t);
			}
		}
	}
	/* The polarized limit is calibration's, not max_int_time: 11 minimum times */
	/* is below 2 s on a real device and must still be refused. */
	m.min_int_time = 165 * 1.5277777492883615e-5;
	ok &= check(restored(&p, &m, 1, 11.0 * m.min_int_time) == 0, "11 minimum times",
	            1, m.min_int_time, 11.0 * m.min_int_time);

	/* Every kept time, over every device clock the init guard accepts. */
	for (k = 0; k < (int)(sizeof(clks) / sizeof(clks[0])); k++) {
		for (n = 0; n <= 400; n++) {
			double ts[9];
			m.minintclks = clks[k];
			m.intclkp = pow(10.0, -12.0 + 12.5 * n / 400.0);
			if (guard(&p, &m) != I1PRO3_OK)
				continue;
			m.min_int_time = m.minintclks * m.intclkp;	/* As :6923 */
			sweeps++;
			for (mode = 0; mode < i1p3_no_modes; mode++) {
				double initial = modes[mode].initial * m.min_int_time;
				double top = modes[mode].pol ? 10.0 * m.min_int_time
				           : initial > MAX_INT_TIME ? initial : MAX_INT_TIME;
				ts[0] = m.min_int_time; ts[1] = initial; ts[2] = top;
				ts[3] = nextafter(top, inf); ts[4] = 3.0 * m.min_int_time;
				ts[5] = 0.5 * (m.min_int_time + top); ts[6] = 1e9 * m.min_int_time;
				ts[7] = 1.0; ts[8] = 1.2 * m.min_int_time;
				for (j = 0; j < 9; j++) {
					r = restored(&p, &m, mode, ts[j]);
					ok &= check(r >= 0, "consistent outcome", mode, m.min_int_time, ts[j]);
					if (r == 1) {
						kept++;
						ok &= check(counts_fit(&m, mode, ts[j]), "kept time overflows a scan",
						            mode, m.min_int_time, ts[j]);
						if (modes[mode].pol)
							ok &= check(floor(ts[j] / m.min_int_time + 0.5) >= 2.0,
							            "kept polarized time gives tint 1", mode,
							            m.min_int_time, ts[j]);
					} else if (r == 0) {
						refused++;
					}
				}
			}
		}
	}
	printf("%d clock pairs, %d kept and %d refused restored times\n", sweeps, kept, refused);
	ok &= check(sweeps > 0 && kept > 0 && refused > 0, "sweep exercised", 0, 0.0, 0.0);
	return ok ? 0 : 1;
}
