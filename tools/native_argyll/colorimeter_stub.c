/* Wholly substituted COLORIMETER. Never touches hardware; nothing here talks to a
 * device, a port or a driver. It exists so the generic ladder is exercised against
 * an instrument neither per-device helper can serve.
 *
 * Modelled on i1d3.c:3902-3919, the i1 Display 3's own capability construction:
 * emission spot, telephoto and ambient, with NO inst_mode_spectral and NO
 * reflection or transmission. That combination is what makes it useful here --
 * emis_tele is advertised by six drivers (ex1, i1d3, i1pro, kleink10, specbos, ss)
 * and served by none of our helpers, and the absent spectral bit exercises the
 * colorimeter result shape.
 *
 * Preconditions are assertions about the ORDER the ladder must use. They fail
 * loudly rather than returning an error, because a ladder that binds in the wrong
 * order is a defect in the ladder, not a device condition to report.
 */
#include "session.h"
#if defined(CM_CONTRIBUTED_instCR250) || defined(CM_CONTRIBUTED_instCR100) || defined(CM_CONTRIBUTED_instCR300)
inst *cm_cr250_persona(const char *scenario);   /* cr250_persona.c */
#endif
#if defined(CM_CONTRIBUTED_instPR655) || defined(CM_CONTRIBUTED_instPR670)
inst *cm_pr655_persona(const char *scenario);   /* existing PR scripted actor */
#endif

#ifndef CM_CONTRIBUTED_TEST_SUPPORT
inst_code inst_get_n_a_cals(inst *p, inst_cal_type *needed, inst_cal_type *available) {
    (void)p;
    if (needed) *needed = inst_calt_none;
    if (available) *available = inst_calt_none;
    return inst_ok;
}
#endif


static const char *fixture = "success";
static int initialized, coms, mode_sets, mode_checks, trigger_sets, capability_queries, reads, cal_rounds;
static int skipped_here;
static inst_code (*operator_callback)(void *, inst_ui_purp);
static void *operator_context;
/* `corrections` is how many are IN EFFECT: set_disptype clears it, exactly as
 * inst.h:843-845 says the instrument does. `corrections_ever` is never cleared,
 * so read_sample can tell "no correction was asked for" apart from "one was
 * asked for and then silently discarded", which is the failure that would
 * otherwise return plausible wrong numbers with every check passing. */
static int disptype_sets, corrections, corrections_ever, observer_sets;
/* The applied matrix's diagonal mean, which types a Klein reading. */
static double matrix_scale;
static inst_mode active = inst_mode_none;
/* The display type's refresh flag, and whether its rate has been measured. */
static int display_refresh, rate_set;
static double stored_rate = 59.94005994005994;
static int rate_gets, rate_sets;
/* i1d3.c:3245-3257: the effective refresh mode, the mode's overrides first. A
 * change of it means the rate must be measured again (:3253-3256). */
static int refresh_mode(void) {
    if (IMODETST(active, inst_mode_emis_norefresh_ovd)) return 0;
    if (IMODETST(active, inst_mode_emis_refresh_ovd)) return 1;
    return display_refresh;
}

static int is(const char *name) { return !strcmp(fixture, name); }

/* disptechs.c's refresh column: CRT (:74), plasma (:83), the projectors
 * (:359-383) and the unknown entry (:392), which disptech_get_id() returns for
 * any unlisted value (:422-437). Every other listed technology is 0. */
static int technology_refreshes(disptech dtech) {
    static const int fixed[] = { 0x0001, 0x3000, 0x3100, 0x3110, 0x3120, 0x3130, 0x3200,
        0x3210, 0x3220, 0x3230, 0x3300, 0x3310, 0x3320, 0x3330, 0x3400, 0x3410, 0x3420,
        0x3430, 0x3500, 0x3510, 0x3520, 0x3530, 0x3600, 0x3610, 0x3620, 0x3630, 0x3700,
        0x3710, 0x3720, 0x3730, 0x4000, 0x4010, 0x4020 };
    size_t i;
    for (i = 0; i < sizeof(fixed) / sizeof(fixed[0]); i++)
        if ((int)dtech == fixed[i]) return 0;
    return 1;
}

/* i1d3.c:3301 and :3340 (spyd2.c:3194, :3235): a correction makes its own
 * technology's refresh flag the display's, and a changed effective mode clears
 * the stored rate (:3253-3256), so an override set before it is lost. */
static void correction_refresh(disptech dtech) {
    const int before = refresh_mode();
    display_refresh = technology_refreshes(dtech);
    if (refresh_mode() != before) rate_set = 0;
}

/* CM_SENSOR_POSITION: where the operator left the diffuser arm. "read" is wrong
 * for this mode at the first reading and right afterwards, because the operator
 * moved it when asked; "stuck" is wrong at every reading; unset, it is always
 * right. i1d3_take_XYZ_measurement checks the arm on EVERY reading
 * (i1d3.c:2318-2346): over the optics for ambient, clear of them otherwise.
 * "unmapped" is the answer a driver gives that raises the code without naming a
 * position: specbos.c:1114-1119 raises inst_wrong_config and fills no
 * config_enum, so inst.c's default (:588-589) names nothing and it has no text. */
static const char *sensor_position = "";
static int position_raised;
#define I1D3_SPOS_EMIS 0x05   /* i1d3.h:60 */
#define I1D3_SPOS_AMB  0x06   /* i1d3.h:61 */

/* Returns the whole inst_code to fail the reading with, or inst_ok. */
static inst_code misplaced(void) {
    int code = IMODETST(active, inst_mode_emis_ambient) ? I1D3_SPOS_AMB : I1D3_SPOS_EMIS;
    if (!strcmp(sensor_position, "stuck")) return inst_wrong_config | code;
    if (position_raised) return inst_ok;
    if (!strcmp(sensor_position, "unmapped")) code = 0;
    else if (strcmp(sensor_position, "read")) return inst_ok;
    position_raised = 1;
    return inst_wrong_config | code;
}

/* i1d3.c:3740-3743 and :3849-3863: the driver's words, and the position it
 * needs, for its two position codes. Every other code has no text here, as it
 * had none before, so a failure this stub raises elsewhere still publishes none. */
static char *interp_error(inst *p, int ec) {
    (void)p;
    if ((ec & inst_mask) != inst_wrong_config) return NULL;
    switch (ec & inst_imask) {
        case I1D3_SPOS_EMIS: return "Ambient filter should be removed";
        case I1D3_SPOS_AMB:  return "Ambient filter should be used";
    }
    return NULL;
}
static inst_config config_enum(inst *p, int ec) {
    (void)p;
    switch (ec & inst_imask) {
        case I1D3_SPOS_EMIS: return inst_conf_emission;
        case I1D3_SPOS_AMB:  return inst_conf_ambient;
    }
    return inst_conf_unknown;
}

/* The fixtures that assert a correction's VALUES, and therefore also assert that
 * one was sent at all. */
static int correction_expected(void) {
    return is("matrix_roundtrip") || is("matrix_roundtrip_alt")
        || is("spectra_roundtrip") || is("spectra_roundtrip_alt");
}
static void require(int value) { if (!value) abort(); }

/* A substituted instrument takes exactly the command line a real one takes, so
 * a caller is exercised against the interface it will use with hardware. */
static const char *pinned_serial = "CM-COLORIMETER-0001";

int cm_configure(int argc, char **argv) {
    cm_selection selected;
    int consumed = cm_parse_selection(argc, argv, &selected);
    if (consumed < 0) return -1;
    pinned_serial = selected.serial;
    return consumed;
}
/* A substituted instrument is never absent and never misidentified. */
const char *cm_attach_stage(void) { return NULL; }
const char *cm_provenance(void) { return "simulated"; }
const char *cm_expected_serial(void) {
    return pinned_serial && pinned_serial[0] ? pinned_serial : NULL;
}
void cm_read_event(void *context, inst_event_type purpose) { (void)context; (void)purpose; }

/* i1d3.c:3902-3908 exactly, minus the bits this stub has no behaviour for. */
static void capabilities(inst *p, inst_mode *cap, inst2_capability *cap2, inst3_capability *cap3) {
    (void)p; require(initialized); capability_queries++;
    if (cap) {
        *cap = inst_mode_emis_spot | inst_mode_emis_tele | inst_mode_emis_ambient
             | inst_mode_emis_refresh_ovd | inst_mode_emis_norefresh_ovd
             | inst_mode_emis_nonadaptive | inst_mode_colorimeter;
        /* A colorimeter advertises no spectral bit. The fixture below lets a test
         * assert that a caller which REQUIRES spectral refuses this instrument. */
        if (is("claims_spectral")) *cap |= inst_mode_spectral;
    }
    if (cap2) {
        /* i1d3.c:3922-3926: the default persona models the non-munkdisp
         * variant, which implements read_refrate regardless of display mode.
         * This does not model a ColorMunki Display's missing refresh support. */
        *cap2 = inst2_prog_trig | inst2_user_trig | inst2_disptype
              | inst2_emis_refr_meas | inst2_meas_disp_update
              | inst2_get_refresh_rate | inst2_set_refresh_rate;
        if (is("snapshot_after_mode") && !mode_sets)
            *cap2 &= ~(inst2_emis_refr_meas | inst2_meas_disp_update
                | inst2_get_refresh_rate | inst2_set_refresh_rate);
        if (is("snapshot_no_trigger")) *cap2 &= ~(inst2_prog_trig | inst2_user_trig);
        if (is("stored_unsupported")) *cap2 &= ~(inst2_get_refresh_rate | inst2_set_refresh_rate);
        if (is("stored_device")) *cap2 &= ~inst2_set_refresh_rate;
        /* Ten drivers advertise inst2_ccmx and only two advertise inst2_ccss
         * (i1d3, spyd2), so a persona offering both cannot show that the ladder
         * checks the RIGHT bit: swapping one gate for the other passed. These
         * fixtures offer one at a time. */
        if (!strncmp(fixture, "refresh_", 8)) {
            /* Failure/capability fixtures replace the default advertisement;
             * otherwise a stored-only or withdrawn case would inherit it. */
            *cap2 &= ~inst2_emis_refr_meas;
            if (is("refresh_stored_only")) *cap2 |= inst2_get_refresh_rate;
            else if (is("refresh_after_mode")) {
                /* i1Pro/ColorMunki-style mode-dependent advertisement, using the
                 * same shared method and session as every other fixture. */
                if (mode_sets) *cap2 |= inst2_emis_refr_meas;
            } else if (!is("refresh_withdrawn") || !mode_sets) *cap2 |= inst2_emis_refr_meas;
            if (is("refresh_no_trigger")) *cap2 = inst2_emis_refr_meas;
        }
        if (!strncmp(fixture, "timing_", 7)) {
            if (is("timing_unsupported") || (is("timing_withdrawn") && mode_sets))
                *cap2 &= ~inst2_meas_disp_update;
            if (is("timing_no_trigger")) *cap2 = inst2_meas_disp_update;
        }
        if (!is("ccss_only")) *cap2 |= inst2_ccmx;
        if (!is("ccmx_only")) *cap2 |= inst2_ccss;
    }
    if (cap3) *cap3 = inst3_none;
}

/* i1d3_check_mode (i1d3.c:3987-4011): the simple test, then a semantic one that
 * admits only the three emission routes. A bare advertised modifier passes
 * `m & ~cap` and is still refused here -- which is the advisory/definitive split. */
static inst_code check_mode(inst *p, inst_mode m) {
    inst_mode cap;
    require(initialized);
    ++mode_checks;
    capabilities(p, &cap, NULL, NULL);
    if (m & ~cap) return inst_unsupported;
    if (!IMODETST(m, inst_mode_emis_spot) && !IMODETST(m, inst_mode_emis_tele)
     && !IMODETST(m, inst_mode_emis_ambient)) return inst_unsupported;
    return is("check_mode_failure") ? inst_unsupported : inst_ok;
}

static inst_code set_mode(inst *p, inst_mode m) {
    inst_code result = check_mode(p, m);
    if (result != inst_ok) return result;
    require(mode_sets < 2);   /* one round, plus one mode-dependent round */
    {
        const int before = refresh_mode();   /* i1d3_set_mode, i1d3.c:4025 */
        ++mode_sets; active = m;
        if (refresh_mode() != before) rate_set = 0;
    }
    return is("set_mode_failure") ? inst_unsupported : inst_ok;
}

static inst_code init_coms(inst *p, baud_rate br, flow_control fc, double tout) {
    (void)p; (void)br; (void)fc;
    require(!initialized && !coms);
    /* Every upstream caller passes 15.0; a serial sweep depends on it (ss.c:206). */
    require(tout >= 15.0);
    coms = 1;
    return is("init_coms_failure") ? inst_coms_fail : inst_ok;
}

static inst_code init_inst(inst *p) {
    (void)p;
    require(coms && !initialized);
    if (is("init_failure")) return inst_internal_error;
    initialized = 1;
    return inst_ok;
}

static char *get_serial_no(inst *p) {
    (void)p;
    require(initialized);
    if (is("serial_mismatch")) return "CM-WRONG-0002";
    if (is("utf8_serial")) return "CM-\xc3\xb1-1";
    if (is("invalid_serial_bytes")) return "\xed\xa0\x80";
    if (is("utf8_serial_collision")) return "\xc3\xad\xc2\xa0\xc2\x80";
    if (is("empty_serial")) return "";
    return "CM-COLORIMETER-0001";
}

static inst_code options(inst *p, inst_opt_type option, ...) {
    (void)p;
    require(initialized && mode_sets >= 1);
    if (option == inst_opt_set_ccss_obs) {
        va_list arguments;
        int observer;
        va_start(arguments, option);
        observer = va_arg(arguments, int);
        va_end(arguments);
        /* icxOT_default (xspect.h:343) is what spotread passes with no -Q. i1d3
         * maps it to CIE 1931 2 degree itself (i1d3.c:4395-4396), so sending that
         * instead looks identical HERE and diverges on any driver that keeps the
         * distinction. Pin the value the ladder must send, not its effect. */
        require(observer == 0);
        /* spotread.c:1727 sets the observer inside the ccss branch, immediately
         * before the samples and after any display type. Arriving once, before
         * the trigger and before the samples, is the whole contract. */
        /* `!corrections` is deliberately not asserted here: the ordering it would
         * catch is already caught by `require(observer_sets == 1)` in
         * col_cal_spec_set, and a second guard for the same property reads as
         * two pieces of evidence where there is one. */
        require(!trigger_sets);
        ++observer_sets;
        return is("observer_failure") ? inst_unsupported : inst_ok;
    }
    /* The trigger is set last, after any mode-dependent round, matching upstream
     * (spotread.c:1807). A ladder that sets it earlier fails here. */
    require(!trigger_sets && option == inst_opt_trig_prog);
    ++trigger_sets;
    return is("trigger_failure") ? inst_unsupported : inst_ok;
}

/* i1d3.c:4160-4161 bounds-checks the index and refuses out of range. The list
 * length is the driver's own; three is enough to exercise both answers. */
static inst_code get_disptypesel(inst *p, int *count, inst_disptypesel **list,
                                  int allconfig, int recreate) {
    static inst_disptypesel entries[100];
    (void)p; (void)allconfig; (void)recreate;
    memset(entries,0,sizeof(entries));
    *count=3; *list=entries;
    for (int i=0; i<*count; i++) {
        sprintf(entries[i].desc,"Simulated display calibration %d",i);
        entries[i].cbid=i+1; entries[i].ix=i;
        entries[i].refr=i==1; entries[i].dtech=disptech_unknown;
    }
    if (is("display_reordered")) strcpy(entries[0].desc,"Changed display calibration");
    if (is("display_ccmx") || is("display_ccmx_changed")) {
        entries[2].flags=inst_dtflags_ccmx;
        entries[2].path="/simulated/installed.ccmx";
        entries[2].cc_cbid=1;
        entries[2].mat[0][0]=is("display_ccmx_changed") ? 1.01:1.0;
    }
    if (!strncmp(fixture,"display_invalid_",16)) {
        entries[1].flags=inst_dtflags_ccmx;
        entries[1].cc_cbid=1; entries[1].mat[0][0]=1;
        entries[1].path="/simulated/installed.ccmx";
        if (is("display_invalid_label") || is("display_invalid_both"))
            strcpy(entries[1].desc,"Invalid \xc0\xaf");
        if (is("display_invalid_label_changed"))
            strcpy(entries[1].desc,"Invalid \xc0\xae");
        if (is("display_invalid_path") || is("display_invalid_both"))
            entries[1].path="/simulated/invalid\xff.ccmx";
        if (is("display_invalid_all"))
            for (int i=0;i<*count;i++) strcpy(entries[i].desc,"Invalid \xc0\xaf");
    }
    if (!is("display_default_none")) entries[0].flags |= inst_dtflags_default;
    if (is("display_default_later") || is("display_default_unavailable")) {
        entries[0].flags &= ~inst_dtflags_default;
        entries[1].flags |= inst_dtflags_default;
    }
    if (is("display_default_multiple")) entries[1].flags |= inst_dtflags_default;
    if (is("display_default_unavailable")) strcpy(entries[1].desc,"Invalid \xc0\xaf");
    if (is("display_failure")) return inst_coms_fail;
    return inst_ok;
}
static inst_code get_disptechi(inst *p, disptech *technology, int *refresh, int *base) {
    (void)p; *technology=disptech_unknown; *refresh=refresh_mode(); *base=1;
    return inst_ok;
}

static inst_code set_disptype(inst *p, int index) {
    (void)p;
    require(initialized && mode_sets >= 1 && !trigger_sets);
    if (index < 0 || index >= 3) return inst_unsupported;
    if (!strncmp(fixture,"display_invalid_",16)) {
        /* A disabled row must never reach the driver; index 2 must not be
         * renumbered to 1 when an earlier installed row has invalid text. */
        require(index!=1 && !is("display_invalid_all"));
    }
    /* inst.h:843-845: setting a display type CLEARS col_cor_mat() and
     * col_cal_spec_set(). Modelled rather than merely counted, so that a ladder
     * which applies a correction before the display type is caught here instead
     * of silently returning uncorrected readings from real hardware. */
    corrections = observer_sets = 0;
    ++disptype_sets;
    if (is("disptype_failure")) return inst_unsupported;
    /* i1d3.c:4030-4048: index 1 is the refresh display (set_disp_type, :4114). */
    {
        const int before = refresh_mode();
        display_refresh = index == 1;
        if (refresh_mode() != before) rate_set = 0;
    }
    return inst_ok;
}

/* The two corrections a colorimeter accepts (i1d3.c:3916-3917 advertises both).
 *
 * The roundtrip fixtures check the VALUES, not just the call: a correction that
 * arrives transposed, truncated or rescaled still binds cleanly and then reports
 * plausible wrong numbers forever, which is the failure this whole unit exists to
 * prevent. Every pinned value below is exactly representable in binary floating
 * point, so an exact comparison is the right test and not a tolerance question. */
static const double expected_matrix[3][3] = {
    { 0.5,    -0.25,  0.125  },
    { 1.5,     2.25, -3.375  },
    {-0.0625,  4.5,   6.75   },
};
/* A SECOND set, disjoint from the first in every field. One scenario can only
 * ever pin "the values that scenario uses": a helper that hardcodes them passes
 * it. Two scenarios that disagree in matrix, dtech, cbid, sample count and norm
 * cannot both be satisfied by any constant. */
static const double expected_matrix_alt[3][3] = {
    { 1.25,   0.0625, -0.5   },
    {-2.75,   0.375,   8.0   },
    { 0.03125, -1.5,   0.25  },
};

static inst_code col_cor_mat(inst *p, disptech dtech, int cbid, double mtx[3][3]) {
    (void)p;
    require(initialized && mode_sets >= 1);
    /* After the display type that would have cleared it, before the trigger. */
    require(!trigger_sets);
    /* These two are guards against a future ladder edit, NOT evidence about the
     * ladder as it stands: `config->matrix` is a member of a stack struct and can
     * never be NULL, and `--correction-cbid` already refuses anything below 1, so
     * neither can be false for any command line. Kept, because a ladder that
     * started passing a literal NULL or a zero cbid should fail loudly -- but they
     * are not part of what makes this block trustworthy today. */
    require(mtx != NULL);
    require(cbid >= 1);   /* inst.h:1105: "needed 1 if unknown", never 0 */
    /* A correction is never applied to an ambient reading: i1d3.c:2336-2360 and
     * spyd2.c:3105-3127 both skip the matrix on that path. The ladder refuses the
     * combination, and this is what makes the refusal mean something -- the file
     * two blocks above cm_bind states "The instrument is never altered by the
     * refusal", and moving that guard below the application satisfied every
     * assertion in the check while actually calling this function. */
    require(!IMODETST(active, inst_mode_emis_ambient) || is("klein_ambient"));
    if (is("matrix_roundtrip") || is("matrix_roundtrip_alt")) {
        const double (*want)[3] = is("matrix_roundtrip") ? expected_matrix
                                                         : expected_matrix_alt;
        int row, column;
        for (row = 0; row < 3; row++)
            for (column = 0; column < 3; column++)
                require(mtx[row][column] == want[row][column]);
        require(dtech == (is("matrix_roundtrip") ? 0x3510 : 0x4010));
        require(cbid == (is("matrix_roundtrip") ? 1 : 3));
    }
    matrix_scale = (mtx[0][0] + mtx[1][1] + mtx[2][2]) / 3.0;
    ++corrections; ++corrections_ever;
    if (is("correction_failure")) return inst_unsupported;
    correction_refresh(dtech);
    return inst_ok;
}

static inst_code col_cal_spec_set(inst *p, disptech dtech, xspect *sets, int no_sets) {
    (void)p;
    require(initialized && mode_sets >= 1 && !trigger_sets);
    require(!IMODETST(active, inst_mode_emis_ambient));
    /* spotread.c:1721-1734 sets the observer first, then the samples. */
    require(observer_sets == 1);
    /* As above: load_spectra callocs and refuses count < 1 before the correction
     * can become CM_CORRECTION_SPECTRAL, so neither half is falsifiable from a
     * command line. A ladder-edit guard, not evidence. */
    require(sets != NULL && no_sets >= 1);
    if (is("spectra_roundtrip")) {
        int sample, band;
        /* FIVE samples and a norm of 2.5, neither of which is a plausible
         * hardcode. Three samples and a norm of 100.0 were, and a truncated count
         * is not merely lossy: i1d3.c:2584 branches on `nsamp == 3` to a direct
         * 3x3 inverse instead of the least-squares fit, so a count that arrives
         * wrong changes the algorithm. The two .ccss files upstream ships hold 4
         * and 14 samples, and both use norm 1.0. */
        require(no_sets == 5);
        for (sample = 0; sample < no_sets; sample++) {
            require(sets[sample].spec_n == 4);
            require(sets[sample].spec_wl_short == 400.0);
            require(sets[sample].spec_wl_long == 700.0);
            require(sets[sample].norm == 2.5);
            for (band = 0; band < 4; band++)
                require(sets[sample].spec[band] == (double)(sample * 4 + band) * 0.25);
        }
        require(dtech == 0x3510);
    }
    if (is("spectra_roundtrip_alt")) {
        int sample, band;
        require(no_sets == 3);
        for (sample = 0; sample < no_sets; sample++) {
            require(sets[sample].spec_n == 6);
            require(sets[sample].spec_wl_short == 380.0);
            require(sets[sample].spec_wl_long == 730.0);
            require(sets[sample].norm == 1.0);
            for (band = 0; band < 6; band++)
                require(sets[sample].spec[band] == (double)(sample * 6 + band) * 0.5);
        }
        require(dtech == 0x4010);
    }
    ++corrections; ++corrections_ever;
    if (is("correction_failure")) return inst_unsupported;
    correction_refresh(dtech);
    return inst_ok;
}

static void set_uicallback(inst *p, inst_code (*cb)(void *, inst_ui_purp), void *ctx) {
    (void)p;
    operator_callback=cb; operator_context=ctx;
    require(cb && !coms);   /* installed before any I/O, so an abort is answerable */
}

/* A colorimeter measures tristimulus and nothing else: spec_n stays 0. Returning
 * a zero-filled spectrum instead would be a fabricated measurement. */
static inst_code read_sample(inst *p, char *name, ipatch *val, instClamping clamp) {
    (void)p; (void)name; (void)clamp;
    require(initialized && mode_sets >= 1 && trigger_sets == 1);
    /* A correction that was applied and then cleared -- by a ladder that set the
     * display type afterwards -- must never reach a reading. inst.h:843-845 makes
     * that ordering silent on real hardware: every call returns inst_ok and the
     * XYZ is simply uncorrected. */
    require(!corrections_ever || corrections == 1);
    /* And the roundtrip fixtures mean "a correction MUST have arrived", not only
     * "if one arrives it must be right". Verifying values when called says
     * nothing when the caller never sends one: dropping the whole correction
     * wiring out of the Python client left every assertion here satisfied and the
     * session completing normally with uncorrected XYZ. */
    require(!correction_expected() || corrections == 1);
    ++reads;
    if (is("blocked_read")) {
        while (operator_callback(operator_context, inst_armed) != inst_user_abort) usleep(10000);
        return inst_user_abort;
    }
    if (is("ignores_cancel")) { for (;;) usleep(10000); }
    /* The race a cancel can meet: the driver sees the abort and fails with its
     * own code anyway. Once the operator has asked to stop, neither retry may ask
     * anything more -- no position prompt, no calibration retry. */
    if (is("abort_then_wrong_position") || is("abort_then_needs_calibration")) {
        while (operator_callback(operator_context, inst_armed) != inst_user_abort) usleep(10000);
        return is("abort_then_wrong_position") ? inst_wrong_config | I1D3_SPOS_AMB
                                               : inst_needs_cal;
    }
    if (is("read_failure")) return inst_misread;
    {
        inst_code position = misplaced();
        if (position != inst_ok) return position;
    }
    memset(val, 0, sizeof(*val));
    val->mtype = IMODETST(active, inst_mode_emis_ambient) ? inst_mrt_ambient : inst_mrt_emission;
    /* kleink10.c:1422-1425: the type follows the applied matrix's scale, not
     * the requested mode. session.c refuses a route the matrix disagrees with,
     * so reaching here with the wrong type means that refusal is gone. */
    if (is("klein_ambient"))
        val->mtype = corrections && matrix_scale > 5.0 ? inst_mrt_ambient : inst_mrt_emission;
    val->mcond = inst_mrc_none;
    val->XYZ_v = 1;
    val->XYZ[0] = 21.5; val->XYZ[1] = 24.0; val->XYZ[2] = 9.75;
    if (is("negative_xyz")) { require(clamp == instNoClamp); val->XYZ[0] = -2.5; }
    val->duration = 0.0;
    val->sp.spec_n = is("fabricated_spectrum") ? 36 : 0;
    return inst_ok;
}

/* i1d3.c:3346-3355: a refresh-rate calibration exists only in refresh mode, and
 * is needed there until the rate is measured (rrset, :1711, :3710). The mode
 * follows the display type the caller selected -- i1d3_disptypesel
 * (:4030-4057) makes "nl", a non-refresh display, the default and "rc" the
 * refresh one -- unless the mode overrides it, norefresh first (:3245-3257).
 * So an i1Display left at its default needs no calibration at all.
 *
 * The exchange fixtures below drive a calibration on purpose -- the skip, the
 * failure, dtp92's grey ladder -- and need one whatever the display. */
static inst_code get_n_a_cals(inst *p, inst_cal_type *needed, inst_cal_type *available) {
    const int refresh = refresh_mode() || is("skippable_calibration")
                        || is("grey_ladder_calibration") || is("calibration_failure");
    (void)p; require(initialized);
    if (needed) *needed = refresh && !rate_set && !is("no_calibration_needed")
                          ? inst_calt_ref_freq : inst_calt_none;
    if (available) *available = refresh ? inst_calt_ref_freq : inst_calt_none;
    return is("calibration_status_failure") ? inst_internal_error : inst_ok;
}

/* i1d3.c:3405-3419, the real shape: the condition is checked ON ENTRY and the
 * call re-requests until the caller hands it back. `*calc` is bidirectional
 * (inst.h:1048), so a ladder that clears it between rounds can never satisfy this
 * and will spin to its round cap -- which is exactly the defect the previous
 * version of this stub could not see, because it never read *calc at all.
 *
 * With "skippable_calibration" the requested condition carries
 * inst_calc_optional_flag, and a caller that skips must return it STILL SET
 * (inst.h:1037-1038). Returning it cleared means "proceed", so performing the
 * calibration then would be performing one the operator declined. */
static inst_code calibrate(inst *p, inst_cal_type *calt, inst_cal_cond *calc,
                           inst_calc_id_type *idtype, char id[CALIDLEN]) {
    const inst_cal_cond wanted = inst_calc_emis_80pc;
    const int optional = is("skippable_calibration");
    (void)p;
    require(initialized && calt && calc && idtype && id);
    if (is("calibration_failure")) return inst_internal_error;
    ++cal_rounds;
    require(cal_rounds <= 4);   /* this driver settles in two; more is a ladder bug */

    /* dtp92.c's cell-ratio calibration: a FEEDBACK LOOP, not one condition. The
     * driver asks for a grey patch, measures it, and asks for a darker or lighter
     * one until the level suits. The level itself is the application's to compute
     * -- dispsup.c:186-200 starts at 0.6, multiplies by 0.7 or by 1.4 clamped to
     * 1.0, and gives up after four adjustments -- and no driver reads it back, so
     * a client that is not told the level would have to invent a grey. */
    if (is("grey_ladder_calibration")) {
        const inst_cal_cond ladder[3] = {
            inst_calc_emis_grey, inst_calc_emis_grey_darker, inst_calc_emis_grey_ligher,
        };
        *idtype = inst_calc_id_none;
        id[0] = 0;
        if (cal_rounds <= 3) {
            *calc = ladder[cal_rounds - 1];
            return inst_cal_setup;
        }
        require((*calc & inst_calc_cond_mask) == inst_calc_emis_grey_ligher);
        *calt = inst_calt_none;
        rate_set = 1;   /* done: get_n_a_cals, which the session re-asks, clears */
        return inst_ok;
    }

    if (optional && (*calc & inst_calc_optional_flag)) {
        /* Declined. The flag came back set, so do NOT calibrate. */
        skipped_here = 1;
        *calt = inst_calt_none;
        return inst_ok;
    }
    if ((*calc & inst_calc_cond_mask) != wanted) {
        /* i1d3.c:3416-3418 exactly: name the condition and ask again. */
        *calc = wanted | (optional ? inst_calc_optional_flag : 0);
        *idtype = inst_calc_id_none;
        id[0] = 0;
        return inst_cal_setup;
    }
    /* The condition is established and the flag cleared: proceed. The rate is
     * now measured (i1d3.c:1711). */
    require(!skipped_here);
    *calt = inst_calt_none;
    rate_set = 1;
    return inst_ok;
}

static inst_code set_refr_rate(inst *p, double hz) {
    (void)p; require(initialized && mode_sets && !reads && !cal_rounds);
    rate_sets++;
    if (is("stored_set_error")) return inst_coms_fail | 9;
    if (hz != 0.0 && (hz < 5.0 || hz > 150.0)) return inst_bad_parameter;  /* i1d3.c:3677-3678 */
    /* i1d3.c:3657-3664,3680-3710: zero is set but undetermined.
     * rrset suppresses automatic refresh calibration even when invalid. */
    stored_rate = hz; rate_set = 1;
    return inst_ok;
}

static inst_code get_refr_rate(inst *p, double *hz) {
    (void)p; require(initialized && mode_sets && !reads);
    rate_gets++;
    if (is("stored_error")) return inst_coms_fail | 8;
    if (is("stored_misread")) return inst_misread | 3;
    if (is("stored_unwritten")) return inst_ok;
    if (is("stored_nan")) { *hz=NAN; return inst_ok; }
    if (is("stored_inf")) { *hz=INFINITY; return inst_ok; }
    if (is("stored_zero")) { *hz=0; return inst_ok; }
    if (is("stored_device")) { *hz=75.0; return inst_ok; }
    if (!rate_set) return inst_needs_cal;
    if (stored_rate == 0.0) { *hz=0.0; return inst_misread; }
    *hz=stored_rate; return inst_ok;
}

static inst_code read_refrate(inst *p, double *hz) {
    (void)p;
    require(initialized && mode_sets && !reads && trigger_sets == 0);
    reads++;
    if (is("refresh_block")) {
        while (operator_callback(operator_context, inst_armed) != inst_user_abort) usleep(10000);
        return inst_user_abort;
    }
    if (is("refresh_error")) return inst_coms_fail | 7;
    if (is("refresh_needs_cal")) return inst_needs_cal | 8;
    if (is("refresh_unwritten")) return inst_ok;
    if (is("refresh_zero")) *hz = 0.0;
    else if (is("refresh_negative")) *hz = -60.0;
    else if (is("refresh_nan")) *hz = NAN;
    else if (is("refresh_inf")) *hz = INFINITY;
    else *hz = 59.94005994005994;
    return inst_ok;
}

/* The common timing method is exercised over a process-local marker only.
 * This simulates orchestration, not a display sensor or physical timing. */
static int timing_marker, timing_initialized;
static inst_code white_change(inst *p, int init) {
    (void)p;
    if (init) {
        timing_marker = 0; timing_initialized = 1;
        return is("timing_init_error") ? inst_coms_fail : inst_ok;
    }
    require(timing_initialized);
    if (is("timing_marker_error")) return inst_coms_fail;
    timing_marker = 1;
    return inst_ok;
}
static inst_code meas_delay(inst *p, int *delay, int *reaction) {
    double started, now, hold_seconds;
    (void)p;
    require(initialized && mode_sets && !reads && trigger_sets == 0 && timing_initialized);
    reads++;
    require(cm_monotonic_checked(&started));
    /* A white marker remains valid for two seconds after the native request.
     * Keep this synthetic capture alive through that window on every platform. */
    hold_seconds = is("timing_wait") ? 3.0 : 2.1;
    do {
        if (operator_callback(operator_context, inst_armed) == inst_user_abort)
            return inst_user_abort;
        cm_sleep_ms(1);
        require(cm_monotonic_checked(&now));
    } while (now < started + hold_seconds);
    if (is("timing_error")) { *reaction = -230; return inst_misread; }
    if (is("timing_unwritten")) return inst_ok;
    *delay = is("timing_negative") ? -1 : is("timing_zero") ? 0 : 37;
    if (is("timing_reaction_unwritten")) return inst_ok;
    *reaction = is("timing_positive") ? 12 : -230;
    return inst_ok;
}

static int deleted;
static void del(inst *p) { require(!deleted); ++deleted; free(p); }

/* A binding that was attached must be released. Registered at attach so it runs
 * whatever the ladder decided, including a refusal that returns early. */
static void must_release(void) {
    require(deleted == 1);
    if (!strncmp(fixture, "snapshot_", 9)) require(!reads && !cal_rounds && !trigger_sets);
    if (is("stored_unsupported")) require(!rate_gets && !rate_sets);
    if (is("display_reordered") || is("display_ccmx_changed")) require(disptype_sets==0);
}

inst *cm_attach(inst_code (*uicallback)(void *, inst_ui_purp), void *context) {
    inst *p;
    const char *selected = getenv("CM_COLORIMETER_FIXTURE");
    const char *position = getenv("CM_SENSOR_POSITION");
    (void)uicallback; (void)context;
    if (selected && *selected) fixture = selected;
    if (position && *position) sensor_position = position;
    require(!*sensor_position || !strcmp(sensor_position, "read")
            || !strcmp(sensor_position, "stuck") || !strcmp(sensor_position, "unmapped"));
#if defined(CM_CONTRIBUTED_instCR250) || defined(CM_CONTRIBUTED_instCR100) || defined(CM_CONTRIBUTED_instCR300)
    /* The real CR-family driver over the series' scripted instrument
     * (cr250_persona.c), not a hand-written vtable; it carries its own release
     * check. */
    if (!strncmp(fixture, "cr250", 5) || !strncmp(fixture, "cr100", 5) || !strncmp(fixture, "cr300", 5))
        return cm_cr250_persona(fixture);
#endif
#if defined(CM_CONTRIBUTED_instPR655) || defined(CM_CONTRIBUTED_instPR670)
    if (!strncmp(fixture, "pr655", 5) || !strncmp(fixture, "pr670", 5))
        return cm_pr655_persona(fixture);
#endif
    if (is("attach_failure")) return NULL;
    if (!(p = calloc(1, sizeof(inst)))) return NULL;
    atexit(must_release);
    p->dtype = is("firmware_initialization") ? instSpyder2
             : is("klein_ambient") ? instKleinK10 : instI1Disp3;   /* insttypes.h:49 */
    p->capabilities = capabilities;
    p->check_mode = check_mode;
    p->set_mode = set_mode;
    p->init_coms = init_coms;
    p->init_inst = init_inst;
    p->get_serial_no = get_serial_no;
    p->get_set_opt = options;
    p->set_disptype = set_disptype;
    p->get_disptypesel = get_disptypesel;
    p->get_disptechi = get_disptechi;
    p->col_cor_mat = col_cor_mat;
    p->col_cal_spec_set = col_cal_spec_set;
    p->read_sample = read_sample;
    p->read_refrate = read_refrate;
    p->get_refr_rate = get_refr_rate;
    p->set_refr_rate = set_refr_rate;
    p->meas_delay = meas_delay;
    p->white_change = white_change;
    p->get_n_a_cals = is("default_calibration") ? inst_get_n_a_cals : get_n_a_cals;
    p->calibrate = calibrate;
    p->set_uicallback = set_uicallback;
    p->del = del;
    if (p->dtype == instI1Disp3) {
        p->interp_error = interp_error;
        p->config_enum = config_enum;
    }
    return p;
}
