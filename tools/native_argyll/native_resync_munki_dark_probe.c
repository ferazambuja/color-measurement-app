/* Source-bound ColorMunki reflective-white completion check.
 * Scripted helpers prove state transitions, not optical or hardware behavior.
 * Expected metadata follows the same driver's successful initial-dark branch.
 */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

typedef int munki_code;
#include "codes.inc"

struct munki_state {
    int reflective, gainmode, dark_valid, want_dcalib, dark_gain_mode;
    int cal_valid, want_calib;
    time_t ddate, cfdate;
    double dcaltime, inttime, dark_int_time, reftemp;
    double *dark_data, *white_data, *iwhite_data, *cal_factor1, *cal_factor2;
};
typedef struct munki_state munki_state;
struct munkiimp {
    munki_state ms[1];
    int mmode, transwarn;
    double ledwaittime, calscantime;
    double *white_ref1, *white_ref2;
};
typedef struct munkiimp munkiimp;
struct munki { munkiimp *m; void *log; };
typedef struct munki munki;
static int fail_step, dark_calls, white_calls;
static void a1logd(void *log, int level, const char *fmt, ...) {
    (void)log; (void)level; (void)fmt;
}
static void msec_sleep(int ms) { (void)ms; }
static int munki_comp_nummeas(munki *p, double total, double interval) {
    (void)p; (void)total; (void)interval; return 1;
}
static munki_code munki_dark_measure(munki *p, double *data, int nummeas,
                                      double *inttime, int gainmode) {
    (void)p; (void)nummeas; (void)gainmode;
    dark_calls++;
    data[0] = 42.0;
    *inttime = 0.25;
    return fail_step == 1 ? MUNKI_COMS_FAIL : MUNKI_OK;
}
static munki_code munki_ledtemp_whitemeasure(munki *p, double *white,
                                               double *iwhite, double *reftemp,
                                               int nummeas, double inttime, int gainmode) {
    (void)p; (void)nummeas; (void)inttime; (void)gainmode;
    white_calls++;
    white[0] = iwhite[0] = *reftemp = 1.0;
    return fail_step == 2 ? MUNKI_COMS_FAIL : MUNKI_OK;
}
static munki_code munki_compute_wav_whitemeas(munki *p, double *factor1,
                                                 double *factor2, double *white) {
    (void)p; (void)white;
    factor1[0] = factor2[0] = 1.0;
    return fail_step == 3 ? MUNKI_COMS_FAIL : MUNKI_OK;
}
static int munki_compute_white_cal(munki *p, double *factor1, double *ref1,
                                     double *read1, double *factor2, double *ref2,
                                     double *read2) {
    (void)p; (void)factor1; (void)ref1; (void)read1;
    (void)factor2; (void)ref2; (void)read2;
    return fail_step == 4 ? -1 : 0;
}
static munki_code probe_white_tail(munki *p, inst_cal_type *calt) {
    munkiimp *m = p->m;
    munki_state *s = &m->ms[m->mmode];
    int nummeas = 0, warn = 0, mmode = m->mmode;
    time_t cdate = 200;
    munki_code ev = MUNKI_OK;
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
    munkiimp m;
    munki p;
    munki_state *s;
    inst_cal_type calt;
    double dark[1], white[1], iwhite[1], factor1[1], factor2[1], ref1[1], ref2[1];
    munki_code ev;
    int ok = 1;
    int step;
    for (step = 0; step <= 4; step++) {
        s = &m.ms[0];
        m.mmode = 0; m.transwarn = 0; m.ledwaittime = 0.0;
        m.calscantime = 1.0; m.white_ref1 = ref1; m.white_ref2 = ref2;
        s->reflective = 1; s->gainmode = 1;
        s->dark_valid = 1; s->want_dcalib = 1;
        s->ddate = 100; s->dark_int_time = 0.75; s->dark_gain_mode = 0;
        s->cal_valid = 0; s->want_calib = 1; s->cfdate = 100;
        s->dcaltime = 1.0; s->inttime = 0.5;
        s->dark_data = dark; s->white_data = white; s->iwhite_data = iwhite;
        s->cal_factor1 = factor1; s->cal_factor2 = factor2;
        dark[0] = 10.0;
        p.m = &m; p.log = NULL; calt = inst_calt_ref_white;
        fail_step = step; dark_calls = white_calls = 0;
        ev = probe_white_tail(&p, &calt);
        if (step == 0) {
            ok &= check(ev == MUNKI_OK && s->cal_valid == 1,
                        "successful white calibration reports success");
            ok &= check(dark_calls == 1 && white_calls == 1 && dark[0] == 42.0,
                        "successful path captured dark and white");
            ok &= check(s->dark_valid == 1 && s->want_dcalib == 0,
                        "successful inner dark capture is valid");
            ok &= check(s->ddate == 200 && s->dark_int_time == 0.25 && s->dark_gain_mode == 1,
                        "successful inner dark metadata matches capture");

            printf("success: code=%d dark_valid=%d cal_valid=%d ddate=%ld dark_int_time=%.2f want_dcalib=%d\n",
                   ev, s->dark_valid, s->cal_valid, (long)s->ddate,
                   s->dark_int_time, s->want_dcalib);
        } else if (step == 1) {
            ok &= check(ev == MUNKI_COMS_FAIL && s->dark_valid == 0,
                        "failed inner dark remains invalid");
            ok &= check(white_calls == 0, "failed dark advanced white");
            ok &= check(s->want_dcalib == 1, "failed inner dark pending flag changed");
            ok &= check(s->ddate == 100, "failed inner dark date changed");
            ok &= check(s->dark_int_time == 0.75,
                        "failed inner dark integration time changed");
            ok &= check(s->dark_gain_mode == 0, "failed inner dark gain mode changed");
        } else {
            ok &= check(ev != MUNKI_OK && s->cal_valid == 0,
                        "failed white stage does not publish white calibration");
            ok &= check(s->dark_valid == 1 && s->want_dcalib == 0 && s->ddate == 200
                        && s->dark_int_time == 0.25 && s->dark_gain_mode == 1,
                        "successful dark remains valid after white-stage failure");

        }
    }
    return ok ? 0 : 1;
}
