/* Substituted CR-family instrument for the common session worker: the REAL
 * cr250.c and cr250_imp.c over the series' own scripted instrument.
 *
 * Every other persona in colorimeter_stub.c and chart_stub.c is a hand-written
 * vtable shaped after a driver. This one is not: new_cr250 is the production
 * constructor, and every request is one the production driver wrote. Only the
 * transport is substituted, by cr250_fake.c -- the same scripted instrument the
 * series' own driver test uses, so the persona and that test cannot come to
 * disagree about what the instrument says. The adapter in cr250_support.c then
 * reads a real cr250 struct filled by the real transaction.
 *
 * The worker that links this holds no OS transport and no other constructor:
 * cr250.c, cr250_imp.c and cr250_fake.c reach the port only through the icoms
 * methods cr250_fake_attach sets, and argyll_native_check.py checks the binary's
 * symbols for new_icoms, new_icompaths and every other driver's constructor.
 *
 * The shared test support links the actual inst.c display-list/selector owner.
 * Its transport factories abort and foreign constructors abort. This persona
 * constructs the driver directly and installs the default UI callback below.
 *
 * Time is virtual (cr250_fake_now/sleep): a read with nothing pending advances
 * the clock by its timeout, so an absent reply exhausts a deadline at once
 * instead of waiting out a real 120 s measurement budget. */
#include "session.h"
#if defined(CM_CONTRIBUTED_instCR250) || defined(CM_CONTRIBUTED_instCR100) || defined(CM_CONTRIBUTED_instCR300)
#include "cr250_fake.h"

#define F cr250_fake_state

static const char *scenario = "cr250";

static int is(const char *name) { return !strcmp(scenario, name); }

/* inst.c's default set_uicallback, which new_inst would have installed. */
static void set_uicallback(inst *pp, inst_code (*uicallback)(void *, inst_ui_purp),
                           void *cntx) {
    pp->uicallback = uicallback;
    pp->uic_cntx = cntx;
}

/* The binding must be released, closing the one port it opened, whatever the
 * ladder decided -- and no scenario may measure twice, nor one refused before M
 * measure at all. A long exposure measures or not depending on the allowance;
 * its case asserts that. */
static void must_release(void) {
    if (F.deleted != 1 || F.opened != 1) abort();
    if (F.measures > 1) abort();
    if ((is("cr250_model") || is("cr250_firmware") || is("cr250_setup")) && F.measures)
        abort();
    if (F.guard_violations) abort();    /* a request inside the 200 ms after a spectrum */
}

/* Each scenario is the fake's default instrument with one documented departure. */
static void script(void) {
    if (is("cr250_model")) { F.override_cmd = "RC Model"; F.override_value = "CR-300"; }
    else if (is("cr250_firmware")) { F.override_cmd = "RC Firmware"; F.override_value = "1.25"; }
    /* The operator left a user calibration active; the driver sets None, but this
     * unit keeps its value, so the readback refuses before M. */
    else if (is("cr250_setup")) {
        cr250_fake_set_setting("UserCalibMode", "Match");
        F.sm_ignored = 1;
    }
    /* The same operator setting on a unit that accepts the write: the read goes
     * ahead and the write is recorded, not restored. */
    else if (is("cr250_writes")) cr250_fake_set_setting("UserCalibMode", "Match");
    else if (is("cr250_warning")) { F.override_cmd = "RM Warnings"; F.override_value = "3"; }
    else if (is("cr250_native_error")) {
        F.native_cmd = "M"; F.native_status = "ER:-17:M:Not enough light";
    }
    /* 60 s exposure ceiling: budget max(120, 3 x 60 + 5) = 185 s. */
    else if (is("cr250_long_exposure")) {
        F.override_cmd = "RC MaxExposure"; F.override_value = "60000.0 msec";
    }
    /* A signed native XYZ, read XYZ-only: the spectrum would contradict it. */
    else if (is("cr250_negative")) {
        F.override_cmd = "RM XYZ"; F.override_value = "-0.5,0.25,-0.001";
    }
    /* RM Spectrum in mW where the instrument's own totals are in W: the unit
     * check refuses the spectral read. */
    else if (is("cr250_spectrum_mismatch")) F.sp_scale = 1000;
    /* Finite samples that overflow once scaled to mW: no unit number exists. */
    else if (is("cr250_spectrum_overflow")) F.sp_fault = "huge";
    /* Two samples that cancel in the sum but each overflow once scaled. */
    else if (is("cr250_spectrum_cancel")) F.sp_fault = "cancel";
    /* A dim capture whose spectrum is just bright enough to compare luminance,
     * with a native Y below the floor for chromaticity. */
    else if (is("cr250_near_floor")) {
        F.sp_level = .0082;
        cr250_fake_spectrum();
        F.override_cmd = "RM XYZ"; F.override_value = "0.2,0.19,0.03";
    }
    /* Native X and Z whose plain sum would overflow: the chromaticity error
     * must still be formed, and refuse. */
    else if (is("cr250_native_sum_overflow")) {
        F.override_cmd = "RM XYZ"; F.override_value = "1e308,25,1e308";
    }
    /* A malformed RM Radiometric, refused after triplet() parsed part of it. */
    else if (is("cr250_broken_radiometric")) {
        F.override_cmd = "RM Radiometric"; F.override_value = "0,0,broken";
    }
    /* A dark capture: RM Radiometric of the model's type, all zero. */
    else if (is("cr250_zero_radiometric")) {
        F.override_cmd = "RM Radiometric"; F.override_value = "0,0,0";
    }
    else if (is("cr100")) { F.model = "CR-100"; F.type = "1"; F.serial = "SYNTHETIC-100"; }
    else if (is("cr300")) { F.model = "CR-300"; F.type = "2"; F.serial = "SYNTHETIC-300"; }
    else if (!is("cr250")) abort();
}

inst *cm_cr250_persona(const char *name) {
    icoms *icom = calloc(1, sizeof(*icom));
    const instType dtype = !strncmp(name, "cr100", 5) ? instCR100
                         : !strncmp(name, "cr300", 5) ? instCR300 : instCR250;
    cr250 *p;
    if (!icom) abort();
    scenario = name;
    cr250_fake_reset();
    script();
    cr250_fake_attach(icom);
    if (!(p = new_cr250(icom, dtype))) abort();
    p->engine.now_ms = cr250_fake_now;
    p->engine.sleep_ms = cr250_fake_sleep;
    p->set_uicallback = set_uicallback;
    atexit(must_release);
    return (inst *)p;
}
#endif
