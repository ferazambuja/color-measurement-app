/* Wholly substituted CHART instruments. Never touches hardware; nothing here
 * talks to a device, a port or a driver.
 *
 * Three personas, each a transcription of a real driver's capability
 * construction, selected by CM_CHART_PERSONA:
 *
 *   spectroscan / spectroscant  ss.c:385-432   motorised X-Y table, inst_mode_ref_xy
 *   dtp20                       dtp20.c:1456-1468 the only instrument that stores a
 *                                                 chart: s_ref_spot and s_ref_chart
 *   dtp41                       dtp41.c:1131-1158 strip reader with NO inst2_has_scan_toll
 *   munki                       munki.c:129-160 strip reader WITH inst2_has_scan_toll
 *
 * Every other constructible Argyll type is a row of driver_personas.c's table,
 * selected by the same variable.
 *
 * They exist so the generic ladder is exercised on the two workflows the
 * per-device helpers cannot express at all -- a sheet and a pass on instruments
 * from three different families -- against the same session.c that binds the
 * colorimeter. The C differences between them live entirely in this substituted
 * driver; the ladder has none.
 *
 * The SpectroScanT persona is here for one reason: ss.c:414-421 withdraws all
 * three inst2_xy_* bits once the mode is transmission, which is the only
 * capability-withdrawal-after-set_mode any linked driver performs, and therefore
 * the only thing that can exercise the ladder's second capability read.
 *
 * Preconditions are assertions about the ORDER and the ARGUMENTS the ladder must
 * use. They abort rather than returning an error, because a ladder that binds in
 * the wrong order, or forwards a chart geometry it was not given, is a defect in
 * the ladder and not a device condition to report.
 */
#include "session.h"
inst *cm_table_persona(const char *name, int needing, int retained);   /* driver_personas.c */

#ifndef CM_CONTRIBUTED_TEST_SUPPORT
inst_code inst_get_n_a_cals(inst *p, inst_cal_type *needed, inst_cal_type *available) {
    (void)p;
    if (needed) *needed = inst_calt_none;
    if (available) *available = inst_calt_none;
    return inst_ok;
}
#endif


static const char *fixture = "success";
static const char *persona = "spectroscan";
static int initialized, coms, mode_sets, trigger_sets, cleared, held, located, locate_points;
static int strips, sheets, sheets_read, tolerance_sets, filter_sets, xcalstd_sets, average_sets;
static inst_mode active = inst_mode_none;
static inst_meas_cond selected_condition = inst_mrc_none;
static int calibrated;
static int read_discovered_calibration;
/* inst_opt_noinitcalib, as each persona's driver takes it. The i1Pro family and
 * the ColorMunki WAIVE their at-open want (i1pro_imp.c:1596-1630, i1pro3_imp.c:
 * 1083-1125, munki_imp.c:957-985); the SpectroScan's flag MASKS the need itself
 * (ss.c:1333-1349). The DTPs have no handler (inst.c's default refuses). */
static int waived, masked;
static inst_opt_filter requested_filter = inst_opt_filter_unknown;

static int is(const char *name) { return !strcmp(fixture, name); }
static int am(const char *name) { return !strcmp(persona, name); }
/* A fresh start. "retained_calibration" is one whose driver restored a calibration
 * from its own file that is still within its limits: the i1Pro family and the
 * ColorMunki restore at every open (i1pro_imp.c:1388, i1pro3_imp.c:911,
 * munki_imp.c:852); ss.c keeps no file, so a SpectroScan restores nothing.
 * "initial_option_refused" is the same start with the option refused, which no
 * pinned handler does -- it stands for one that would. */
static int restored(void) {
    return is("retained_calibration") || is("initial_option_refused");
}
static int startup(void) { return is("startup") || restored(); }
/* What the restored calibration covers is needed only while the at-open want
 * stands. The integration-time choice is not in the file: only calibrate() sets
 * done_dintsel (i1pro_imp.c:2553, i1pro3_imp.c:1478, munki_imp.c:1705). */
static int retained(void) { return restored() && waived; }
static void require(int value) { if (!value) abort(); }
static int deferred_filter(void) {
    return is("filter_after_calibration") || is("filter_still_wrong")
        || is("filter_readback_failure");
}
static int late_calibration(void) {
    return is("late_calibration") || is("repeated_calibration")
        || is("late_calibration_failure") || is("filter_changes_after_retry");
}
/* CM_SENSOR_POSITION: where the operator left the ColorMunki's dial or the i1Pro
 * 3's adapter. "read" is wrong for this mode at the first reading and right
 * afterwards, because the operator moved it when asked; "stuck" is wrong at every
 * reading; "calibrate" is wrong at the first calibration step that checks it;
 * "both" is wrong at each of those once; "calibrate_stuck" stays wrong during
 * calibration. Unset, it is always right. No other chart
 * persona's driver checks a position. */
static const char *sensor_position = "";
static int position_raised;   /* 1: a calibration step raised it, 2: a reading */
#define MUNKI_SPOS_PROJ  0x48   /* munki_imp.h:368-371 */
#define MUNKI_SPOS_SURF  0x49
#define MUNKI_SPOS_CALIB 0x4A
#define MUNKI_SPOS_AMB   0x4B
#define I1PRO3_SPOS_CAL  0x20   /* i1pro3_imp.h:567-569 */
#define I1PRO3_SPOS_STD  0x21
#define I1PRO3_SPOS_AMB  0x22

/* The code the driver returns when the position is wrong for what it is doing:
 * a reading, or the calibration condition just established.
 * - ColorMunki: munki_imp_measure checks the dial on every reading
 *   (munki_imp.c:2191-2206): ambient, projector for tele, surface otherwise;
 *   munki_imp_calibrate checks it for transmission white (:1071-1073). Its
 *   calibration-position check (:1067-1069) maps to inst_cal_setup instead
 *   (munki.c:583-584), so it is a condition, not a code here.
 * - i1Pro 3: the ambient adapter for ambient reads and the standard one for every
 *   other read (i1pro3_imp.c:8221, :8380, :9589-9600, :9656-9667); the tile when
 *   it calibrates (:7561-7564, :7661, :9383, :9497). */
static int position_code(int calibrating, inst_cal_cond calc) {
    int ambient = IMODETST(active, inst_mode_emis_ambient)
                  || IMODETST(active, inst_mode_emis_ambient_flash);
    int emissive = (active & inst_mode_illum_mask) == inst_mode_emission;
    if (am("munki")) {
        if (calibrating) return calc == inst_calc_man_trans_white ? MUNKI_SPOS_SURF : 0;
        return !emissive ? MUNKI_SPOS_SURF : ambient ? MUNKI_SPOS_AMB
             : IMODETST(active, inst_mode_emis_tele) ? MUNKI_SPOS_PROJ : MUNKI_SPOS_SURF;
    }
    if (am("i1pro3")) {
        if (calibrating) return calc == inst_calc_man_ref_white ? I1PRO3_SPOS_CAL : 0;
        return ambient ? I1PRO3_SPOS_AMB : I1PRO3_SPOS_STD;
    }
    return 0;
}
/* Returns the whole inst_code to fail with, or inst_ok. */
static inst_code misplaced(int calibrating, inst_cal_cond calc) {
    const int step = calibrating ? 1 : 2;
    int code = position_code(calibrating, calc & inst_calc_cond_mask);
    if (!code) return inst_ok;
    if (calibrating && !strcmp(sensor_position, "calibrate_stuck")) return inst_wrong_config | code;
    if (!calibrating && !strcmp(sensor_position, "stuck")) return inst_wrong_config | code;
    if ((position_raised & step) || (strcmp(sensor_position, "both")
                                     && strcmp(sensor_position, calibrating ? "calibrate" : "read")))
        return inst_ok;
    position_raised |= step;
    return inst_wrong_config | code;
}

/* The drivers' words, and the positions they need, for the codes above:
 * munki.c:487-494 and :653-672; i1pro3.c:399-404 and :590-613. Every other code
 * has no text here, as it had none before, so a failure this stub raises
 * elsewhere still publishes none. */
static char *interp_error(inst *p, int ec) {
    (void)p;
    if ((ec & inst_mask) != inst_wrong_config) return NULL;
    if (am("munki")) switch (ec & inst_imask) {
        case MUNKI_SPOS_PROJ: return "Sensor should be in projector position";
        case MUNKI_SPOS_SURF: return "Sensor should be in surface position";
        case MUNKI_SPOS_AMB:  return "Sensor should be in ambient position";
    }
    if (am("i1pro3")) switch (ec & inst_imask) {
        case I1PRO3_SPOS_CAL:
            return "Standard adapter should be fitted and instrument placed on calibration tile";
        case I1PRO3_SPOS_STD: return "Standard surface measurement adapter should be fitted";
        case I1PRO3_SPOS_AMB: return "Ambient measurement adapter should be fitted";
    }
    return NULL;
}
static inst_config config_enum(inst *p, int ec) {
    (void)p;
    if (am("munki")) switch (ec & inst_imask) {
        case MUNKI_SPOS_PROJ:  return inst_conf_projector;
        case MUNKI_SPOS_SURF:  return inst_conf_surface;
        case MUNKI_SPOS_AMB:   return inst_conf_ambient;
        case MUNKI_SPOS_CALIB: return inst_conf_calibration;
    }
    if (am("i1pro3")) switch (ec & inst_imask) {
        case I1PRO3_SPOS_CAL: return inst_conf_calibration;
        case I1PRO3_SPOS_STD: return inst_conf_surface;
        case I1PRO3_SPOS_AMB: return inst_conf_ambient;
    }
    return inst_conf_unknown;
}

/* DTP20/41/51 learn this from the read response, not from get_n_a_cals(). */
static int read_needs_calibration(void) {
    if (late_calibration() && (!calibrated || is("repeated_calibration"))) {
        read_discovered_calibration = 1;
        return 1;
    }
    return 0;
}

/* A substituted instrument takes exactly the command line a real one takes, so
 * a caller is exercised against the interface it will use with hardware. */
static const char *pinned_serial = "CM-CHART-0001";
/* The name the caller selected the instrument by. A table persona requires its
 * own driver's name here (driver_personas.c), as inst_enum would. */
const char *cm_selected_instrument = "";

#ifndef CM_CHART_DESCRIPTION
int cm_configure(int argc, char **argv) {
    cm_selection selected;
    int consumed = cm_parse_selection(argc, argv, &selected);
    if (consumed < 0) return -1;
    pinned_serial = selected.serial;
    cm_selected_instrument = selected.instrument;
    return consumed;
}
#endif
/* A substituted instrument is never absent and never misidentified. */
const char *cm_attach_stage(void) { return NULL; }
const char *cm_provenance(void) { return "simulated"; }
const char *cm_expected_serial(void) {
    return pinned_serial && pinned_serial[0] ? pinned_serial : NULL;
}
void cm_read_event(void *context, inst_event_type purpose) { (void)context; (void)purpose; }

static int transmissive(void) {
    return (active & inst_mode_illum_mask) == inst_mode_transmission;
}

static void capabilities(inst *p, inst_mode *cap, inst2_capability *cap2, inst3_capability *cap3) {
    (void)p; require(initialized);
    if (cap) {
        if (am("dtp51")) {
            /* dtp51.c:805-807. A strip reader with no spot mode and no spectral
             * bit at all -- it is a colorimeter that reads strips. */
            *cap = inst_mode_ref_strip | inst_mode_colorimeter;
        } else if (am("dtp20")) {
            /* dtp20.c:1456-1461. The saved routes are what make this instrument
             * worth substituting: they are the only ones whose selection depends
             * on a runtime STATUS rather than on any capability word. */
            *cap = inst_mode_ref_spot | inst_mode_ref_strip
                 | inst_mode_s_ref_spot | inst_mode_s_ref_chart
                 | inst_mode_colorimeter | inst_mode_spectral;
        } else if (am("dtp41")) {
            /* dtp41.c:1135-1139, plus the transmission pair its initialised probe adds
             * at :101-105 -- a capability that exists only after init_inst. */
            *cap = inst_mode_ref_spot | inst_mode_ref_strip
                 | inst_mode_colorimeter | inst_mode_spectral;
            if (is("transmission_capable") || is("zero_geometry"))
                *cap |= inst_mode_trans_spot | inst_mode_trans_strip;
        } else if (am("i1pro3")) {
            /* i1pro3.c:127-175, the instrument this project's per-device helper
             * was written for. It is here so the GENERIC ladder is shown driving
             * it: a modular path that cannot be demonstrated on the one
             * instrument the hard-coded path exists for has not replaced it.
             *
             * Ambient and its flash are conditional on I1PRO3_CAP_AMBIENT
             * (:151-155), read from the instrument rather than from the model, so
             * they are gated on a fixture rather than always present. */
            *cap = inst_mode_emis_spot | inst_mode_emis_tele | inst_mode_emis_strip
                 | inst_mode_trans_spot | inst_mode_trans_strip
                 | inst_mode_emis_nonadaptive
                 | inst_mode_ref_spot | inst_mode_ref_strip
                 | inst_mode_colorimeter | inst_mode_spectral;
            if (!is("no_ambient"))
                *cap |= inst_mode_emis_ambient | inst_mode_emis_ambient_flash;
            /* :145-148. i1pro3_imp_highres is static, but the BIT is mode
             * dependent -- offered only once a non-reflective mode is current,
             * which is what makes the ladder's deferred second round
             * (spotread.c:1610-1628) necessary rather than decorative. */
            if (mode_sets >= 1 && (active & inst_mode_illum_mask) != inst_mode_reflection
                && !is("no_highres"))
                *cap |= inst_mode_highres;
        } else if (am("i1pro2")) {
            /* i1pro.c:130-190, as an i1Pro2 -- the other instrument this project
             * carries a per-device helper for, here so the GENERIC ladder is shown
             * driving it too.
             *
             * Three differences from the i1Pro3 persona above, all of them real
             * and all of them the kind of peculiarity a per-device helper used to
             * exist to hold:
             *   - highres is STATIC (:159-160), not mode dependent, so it needs no
             *     deferred second round;
             *   - inst_mode_ref_uv is an i1Pro2-only bit (:154-157);
             *   - cap3 is M2 or nothing (:185-190) -- never M1, never M3. */
            *cap = inst_mode_emis_spot | inst_mode_emis_tele | inst_mode_emis_strip
                 | inst_mode_trans_spot | inst_mode_trans_strip
                 | inst_mode_emis_nonadaptive
                 | inst_mode_ref_spot | inst_mode_ref_strip | inst_mode_ref_uv
                 | inst_mode_colorimeter | inst_mode_spectral;
            if (!is("no_highres")) *cap |= inst_mode_highres;
            if (!is("no_ambient"))
                *cap |= inst_mode_emis_ambient | inst_mode_emis_ambient_flash;
        } else if (am("munki")) {
            /* munki.c:129-143. The highres bit below is NOT the ColorMunki's own
             * behaviour: munki.c:144 tests munki_imp_highres(p) and marks it
             * "Static", and munki_imp.c:7949-7955 is a compile-time constant. It is
             * i1pro3.c:143-146 that makes the bit mode dependent:
             *
             *     if (i1pro3_imp_highres(p)) {
             *         if (s != NULL && !s->reflective) p->cap |= inst_mode_highres;
             *     }
             *
             * That form is borrowed onto this persona deliberately, because no
             * other linked persona has it and the ladder's deferred second round
             * (spotread.c:1610-1628) is otherwise unexercised. The borrowing is
             * the point of a substituted instrument; pretending it were the
             * ColorMunki's own behaviour would not be. */
            *cap = inst_mode_ref_spot | inst_mode_ref_strip | inst_mode_emis_spot
                 | inst_mode_emis_tele | inst_mode_trans_spot | inst_mode_trans_strip
                 | inst_mode_emis_strip | inst_mode_emis_ambient
                 | inst_mode_emis_ambient_flash | inst_mode_emis_nonadaptive
                 | inst_mode_colorimeter | inst_mode_spectral | inst_mode_calibration;
            if (mode_sets >= 1 && !is("no_highres")) *cap |= inst_mode_highres;   /* see above */
        } else {
            /* ss.c:387-405 */
            *cap = inst_mode_ref_spot | inst_mode_emis_spot | inst_mode_emis_tele
                 | inst_mode_colorimeter | inst_mode_spectral | inst_mode_ref_xy;
            if (am("spectroscant")) *cap |= inst_mode_trans_spot;
        }
    }
    if (cap2) {
        /* dtp51.c:809 sets inst2_switch_trig and NOTHING else, commented "Can only
         * be triggered by microswitch". It is the one acquisition driver in the
         * pinned tree without inst2_prog_trig, which is why a ladder that assumes
         * a programmatic trigger cannot bind it. */
        *cap2 = am("dtp51") ? inst2_switch_trig
                            : (inst2_prog_trig | inst2_user_trig | inst2_user_switch_trig);
        if (am("munki")) *cap2 |= inst2_bidi_scan | inst2_has_scan_toll | inst2_no_feedback;
        if (am("i1pro3") || am("i1pro2")) {
            /* i1pro3.c:156-167 and i1pro.c:168-183, which are identical. */
            *cap2 |= inst2_bidi_scan | inst2_has_scan_toll | inst2_no_feedback;
            if ((active & inst_mode_illum_mask) == inst_mode_emission)
                *cap2 |= inst2_meas_disp_update | inst2_emis_refr_meas;
        }
        if (am("munki") && (active & inst_mode_illum_mask) == inst_mode_emission)
            *cap2 |= inst2_meas_disp_update | inst2_emis_refr_meas; /* munki.c:157-163 */
        if (am("dtp41") && is("display_type_capable")) *cap2 |= inst2_disptype;
        if (am("dtp20")) *cap2 |= inst2_has_battery;   /* dtp20.c:1464-1468; no refresh getter */
        if (am("spectroscan") || am("spectroscant")) {
            /* ss.c:413-422: not available once the mode is transmission. */
            if (!transmissive())
                *cap2 |= inst2_xy_holdrel | inst2_xy_locate | inst2_xy_position;
        }
    }
    if (cap3) {
        /* ss.c:424-429; dtp41.c:1146, dtp51.c:817 and munki's reflective filter. */
        *cap3 = am("dtp51") ? inst3_none
              : (am("dtp41") || am("dtp20"))
                ? (is("averaging_capable") ? inst3_average : inst3_none)
              : am("i1pro3")
                /* i1pro3.c:169-176: the filter set exists only for a REFLECTIVE
                 * state, and M3 only with I1PRO3_CAP_POL. An emissive i1Pro3
                 * therefore offers no measurement condition at all, which is the
                 * behaviour that makes the condition an axis of the mode rather
                 * than a property of the instrument. */
                ? (((active & inst_mode_illum_mask) == inst_mode_reflection)
                   ? (inst3_filter_none | inst3_filter_D50 | inst3_filter_UVCut
                      | (is("polarisation_capable") ? inst3_filter_pol : inst3_none))
                   : inst3_none)
              : am("i1pro2")
                /* i1pro.c:185-190: M2 when the instrument carries the UV filter,
                 * M0 otherwise, and only ever for a reflective state. */
                ? (((active & inst_mode_illum_mask) == inst_mode_reflection)
                   ? (is("no_uv_filter") ? inst3_filter_none : inst3_filter_UVCut)
                   : inst3_none)
              : am("munki") ? inst3_filter_UVCut
              : (inst3_filter_none | inst3_filter_D65 | inst3_filter_UVCut
                 | inst3_filter_pol | inst3_filter_Custom);
    }
}

/* ss.c:1810-1839 and dtp41.c:1218-1237: the simple test, then a semantic one
 * naming exactly the routes the driver serves. IMODETST2 admits a route only
 * when the capability word carries it too, which is how a conditional route such
 * as the DTP41's transmission pair stays refused until the probe found it. */
static inst_code check_mode(inst *p, inst_mode m) {
    inst_mode cap;
    require(initialized);
    capabilities(p, &cap, NULL, NULL);
    if (m & ~cap) return inst_unsupported;
    if (am("dtp51")) {
        if (!IMODETST(m, inst_mode_ref_strip)) return inst_unsupported;
    } else if (am("dtp20")) {
        /* dtp20.c's own semantic test: the four routes it serves, no others. */
        if (!IMODETST(m, inst_mode_ref_spot) && !IMODETST(m, inst_mode_ref_strip)
         && !IMODETST(m, inst_mode_s_ref_spot) && !IMODETST(m, inst_mode_s_ref_chart))
            return inst_unsupported;
    } else if (am("i1pro3") || am("i1pro2") || am("munki")) {
        /* Each check_mode delegates to its converter: i1pro.c:672-703,
         * i1pro3.c:632-671 and munki.c:694-730 serve these same nine geometries,
         * with optional non-adaptive spot/tele emission. Their composite bits
         * also advertise trans_spot_a, which none of the converters maps.
         * The ColorMunki dial is checked during measurement, not here
         * (munki_imp.c:2191-2204); this persona places it for the route. */
        if (!IMODETST(m, inst_mode_ref_spot) && !IMODETST(m, inst_mode_ref_strip)
         && !IMODETST(m, inst_mode_trans_spot) && !IMODETST(m, inst_mode_trans_strip)
         && !IMODETST(m, inst_mode_emis_spot) && !IMODETST(m, inst_mode_emis_tele)
         && !IMODETST(m, inst_mode_emis_strip) && !IMODETST(m, inst_mode_emis_ambient)
         && !IMODETST(m, inst_mode_emis_ambient_flash))
            return inst_unsupported;
    } else if (am("dtp41")) {
        if (!IMODETST(m, inst_mode_ref_spot) && !IMODETST(m, inst_mode_ref_strip)
         && !IMODETST2(cap, m, inst_mode_trans_spot)
         && !IMODETST2(cap, m, inst_mode_trans_strip))
            return inst_unsupported;
    } else if (!IMODETST(m, inst_mode_ref_spot) && !IMODETST(m, inst_mode_emis_spot)
            && !IMODETST(m, inst_mode_emis_tele)
            && !IMODETST2(cap, m, inst_mode_ref_xy)
            && !IMODETST2(cap, m, inst_mode_trans_spot)) {
        return inst_unsupported;
    }
    return is("check_mode_failure") ? inst_unsupported : inst_ok;
}

static inst_code set_mode(inst *p, inst_mode m) {
    inst_code result = check_mode(p, m);
    if (result != inst_ok) return result;
    require(mode_sets < 2);
    ++mode_sets; active = m;
    return inst_ok;
}

static inst_code init_coms(inst *p, baud_rate br, flow_control fc, double tout) {
    (void)p; (void)br; (void)fc;
    require(!initialized && !coms);
    /* ss.c:206 sweeps seven baud rates inside this deadline. The i1Pro3
     * USB init_coms ignores the caller's serial timeout (i1pro3.c:83-124). */
    if (!am("i1pro3")) require(tout >= 15.0);
    coms = 1;
    return inst_ok;
}

static inst_code init_inst(inst *p) {
    (void)p;
    require(coms && !initialized);
    initialized = 1;
    return inst_ok;
}

static char *get_serial_no(inst *p) { (void)p; require(initialized); return "CM-CHART-0001"; }

static inst_code options(inst *p, inst_opt_type option, ...) {
    if (option == inst_opt_noinitcalib) {
        va_list args;
        int losecs;
        /* After init, before the mode: where spotread -N asks (spotread.c:1538). */
        require(initialized && !mode_sets);
        va_start(args, option); losecs = va_arg(args, int); va_end(args);
        require(losecs == 0);
        if (is("initial_option_refused")) return inst_unsupported;
        if (am("i1pro3") || am("i1pro2") || am("munki")) { waived = 1; return inst_ok; }
        if (am("spectroscan") || am("spectroscant")) { masked = 1; return inst_ok; }  /* ss.c:1887-1901 */
        return inst_unsupported;
    }
    if (option == inst_opt_scan_toll) {
        va_list args;
        double tolerance;
        /* Only ever reachable on a persona advertising inst2_has_scan_toll, and
         * only after the mode is set. */
        require(am("munki") && mode_sets >= 1 && trigger_sets == 1);
        va_start(args, option);
        tolerance = va_arg(args, double);
        va_end(args);
        require(tolerance == 0.75);   /* exactly what the caller asked for */
        ++tolerance_sets;
        return is("scan_tolerance_failure") ? inst_unsupported : inst_ok;
    }
    if (option == inst_stat_get_filter && am("i1pro2")) {
        va_list args;
        if (is("fixed_filter_readback_failure")) return inst_coms_fail | 11;
        va_start(args, option);
        *va_arg(args, inst_opt_filter *) = is("no_uv_filter")
            || (is("filter_changes_after_retry") && calibrated)
            ? inst_opt_filter_none : inst_opt_filter_UVCut;
        va_end(args);
        return inst_ok;
    }
    if (option == inst_stat_get_filter && deferred_filter()) {
        /* ss.c:1908-1938 only records the requested filter; :1479-1489
         * checks the physical filter during calibration and asks to change it.
         * Its getter (:2019) can also fail during actual communication. */
        va_list args;
        require(am("spectroscan"));
        if (is("filter_readback_failure")) return inst_coms_fail | 11;
        va_start(args, option);
        *va_arg(args, inst_opt_filter *) = calibrated && !is("filter_still_wrong")
            ? requested_filter : inst_opt_filter_none;
        va_end(args);
        return inst_ok;
    }
    if (option == inst_stat_get_filter) return inst_unsupported;
    if (option == inst_opt_set_filter && am("i1pro2")) return inst_unsupported;
    if (option == inst_opt_set_filter) {
        va_list args;
        int selected;
        require(mode_sets >= 1 && !trigger_sets);   /* before the trigger, after the mode */
        va_start(args, option); selected = va_arg(args, int); va_end(args);
        requested_filter = (inst_opt_filter)selected;
        /* The condition asked for must be one THIS persona's cap3 advertises.
         *
         * This used to assert `selected == inst_opt_filter_UVCut` -- the only
         * condition any persona then offered -- which tested a particular value
         * rather than the property, and so admitted a caller asking for a filter
         * the instrument never claimed while refusing a legitimate M0 or M1. */
        {
            inst3_capability cap3;
            static const struct { int selection; inst3_capability bit; } conditions[] = {
                { inst_opt_filter_none,   inst3_filter_none   },
                { inst_opt_filter_D50,    inst3_filter_D50    },
                { inst_opt_filter_D65,    inst3_filter_D65    },
                { inst_opt_filter_UVCut,  inst3_filter_UVCut  },
                { inst_opt_filter_pol,    inst3_filter_pol    },
                { inst_opt_filter_Custom, inst3_filter_Custom },
            };
            size_t i;
            capabilities(p, NULL, NULL, &cap3);
            for (i = 0; i < sizeof(conditions) / sizeof(conditions[0]); i++)
                if (conditions[i].selection == selected) break;
            require(i < sizeof(conditions) / sizeof(conditions[0]));
            if (!(cap3 & conditions[i].bit)) return inst_unsupported;
        }
        switch (selected) {
            case inst_opt_filter_D50: selected_condition=inst_mrc_D50; break;
            case inst_opt_filter_D65: selected_condition=inst_mrc_D65; break;
            case inst_opt_filter_UVCut: selected_condition=inst_mrc_uvcut; break;
            case inst_opt_filter_pol: selected_condition=inst_mrc_pol; break;
            case inst_opt_filter_Custom: selected_condition=inst_mrc_custom; break;
            default: selected_condition=inst_mrc_none;
        }
        ++filter_sets;
        return is("filter_failure") ? inst_unsupported : inst_ok;
    }
    if (option == inst_opt_set_xcalstd) {
        va_list args;
        int selected;
        require(mode_sets >= 1 && !trigger_sets && filter_sets >= 1);   /* spotread order */
        va_start(args, option); selected = va_arg(args, int); va_end(args);
        require(selected == xcalstd_xrga);
        ++xcalstd_sets;
        return inst_ok;
    }
    if (option == inst_stat_saved_readings) {
        /* A runtime status, not a capability (inst.h:467-468): what the
         * instrument is holding right now. */
        va_list args;
        inst_stat_savdrd *out;
        require(am("dtp20") && initialized);
        va_start(args, option); out = va_arg(args, inst_stat_savdrd *); va_end(args);
        if (is("nothing_stored")) { *out = inst_stat_savdrd_none; return inst_ok; }
        if (is("chart_only_stored")) { *out = inst_stat_savdrd_chart; return inst_ok; }
        *out = inst_stat_savdrd_spot | inst_stat_savdrd_chart;
        return inst_ok;
    }
    if (option == inst_opt_set_averages) {
        va_list args;
        int count;
        require(mode_sets >= 1 && !trigger_sets && am("dtp41"));
        va_start(args, option); count = va_arg(args, int); va_end(args);
        require(count == 20);
        ++average_sets;
        return inst_ok;
    }
    /* The trigger is set last, after any mode-dependent round (spotread.c:1807),
     * and must be one this instrument actually advertises. dtp51_get_set_opt
     * (dtp51.c:868) accepts only inst_opt_trig_switch, so a ladder that assumed
     * inst_opt_trig_prog aborts here rather than quietly arming nothing. */
    require(initialized && mode_sets >= 1 && !trigger_sets);
    require(option == (am("dtp51") ? inst_opt_trig_switch : inst_opt_trig_prog));
    ++trigger_sets;
    return inst_ok;
}

#include "timing_stub.h"
static void set_uicallback(inst *p, inst_code (*cb)(void *, inst_ui_purp), void *ctx) {
    (void)p;
    timing_callback = cb; timing_context = ctx;
    require(cb && !coms);
}

/* A spectrometer's patch. The values are markers, not measurements: this
 * instrument is wholly substituted and publishes provenance "simulated". */
/* The measurement type of the ACTIVE mode, the way a driver sets it.
 *
 * Keyed on the illumination actually selected rather than on the instrument's
 * class: a ColorMunki and a SpectroScan both serve emissive routes, and stamping
 * every reading inst_mrt_reflective made a published emissive reading carry
 * relative units under an assumed D50 (inst.h:92-94). The units a reading
 * carries follow its own inst_meas_type, so getting this wrong here does not
 * merely mislabel the stub -- it produces a fixture that says an emissive
 * measurement is a reflectance. */
static inst_meas_type measurement_type(void) {
    if (transmissive()) return inst_mrt_transmissive;
    if (IMODETST(active, inst_mode_emis_ambient_flash)) return inst_mrt_ambient_flash;
    if (IMODETST(active, inst_mode_emis_ambient)) return inst_mrt_ambient;
    if ((active & inst_mode_illum_mask) == inst_mode_emission) return inst_mrt_emission;
    return inst_mrt_reflective;
}

/* Twelve illustrative shapes ported from Fernando Voltolini de Azambuja's
 * MATLAB serial demo. Both demos use Gaussian components {centre nm, width nm,
 * amplitude W/(sr.m^2.nm)}, unused rows zero. They are not measured sources,
 * standard illuminants, references or models of any instrument. */
static const double shapes[12][3][3] = {
    {{450, 18, .0024}, {540, 30, .0018}, {620, 28, .0011}},     /* cool white mix */
    {{450, 20, .0006}, {550, 38, .0015}, {625, 38, .0026}},     /* warm white mix */
    {{450, 18, .0015}, {540, 27, .0020}, {620, 23, .0017}},     /* neutral white mix */
    {{450, 9, .0030}}, {{530, 11, .0030}}, {{635, 10, .0030}},  /* narrow blue, green, red */
    {{455, 12, .0018}, {525, 20, .0022}},                       /* cyan mix */
    {{445, 12, .0020}, {640, 15, .0026}},                       /* magenta mix */
    {{590, 14, .0030}},                                         /* amber peak */
    {{555, 115, .0016}},                                        /* broadband */
    {{450, 10, .0020}, {570, 65, .0022}},                       /* phosphor-like white */
    {{450, 18, .000015}, {540, 27, .000020}, {620, 23, .000017}}, /* low signal */
};

static double shape_at(int k, double nm) {
    double sum = 0.0;
    for (int c = 0; c < 3; c++)
        if (shapes[k][c][2] > 0.0)
            sum += shapes[k][c][2] * exp(-0.5 * pow((nm - shapes[k][c][0]) / shapes[k][c][1], 2));
    return sum;
}

/* A reading of shape k on a driver's own grid -- `bands` samples from `first` to
 * `last` nm: emitted light in Argyll's mW/(m^2.sr.nm) (xspect.h:40-42), or, for a
 * reflective or transmissive reading, a factor from 5 to 95 % with the shape's
 * peaks as the colour. `emitted` says which, because a driver may leave the
 * reading's own type unset (huey.c:1246, ss.c:829). XYZ comes from that spectrum
 * as the upstream drivers compute it -- no illuminant for emission, D50 and x100
 * otherwise (i1pro_imp.c:10426-10428,10497-10501; munki_imp.c:8074-8076) -- so the
 * reading's colour and its curve agree. driver_personas.c reads on its drivers'
 * grids through this too. */
void cm_synthetic_reading(ipatch *val, int k, int emitted, double first, double last,
                          int bands) {
    const double step = (last - first) / (bands - 1);
    double peak = 0.0;
    xsp2cie *conv;
    val->sp.spec_n = bands;
    val->sp.spec_wl_short = first; val->sp.spec_wl_long = last;
    for (int i = 0; i < val->sp.spec_n; i++)
        if (shape_at(k, first + step * i) > peak) peak = shape_at(k, first + step * i);
    val->sp.norm = emitted ? 1.0 : 100.0;
    for (int i = 0; i < val->sp.spec_n; i++) {
        double s = shape_at(k, first + step * i);
        val->sp.spec[i] = emitted ? 1000.0 * s : 5.0 + 90.0 * s / peak;
    }
    if ((conv = new_xsp2cie(emitted ? icxIT_none : icxIT_D50, 0.0, NULL, icxOT_CIE_1931_2,
                            NULL, icSigXYZData, icxNoClamp)) == NULL)
        abort();
    conv->convert(conv, val->XYZ, &val->sp);
    conv->del(conv);
    if (!emitted)
        for (int j = 0; j < 3; j++) val->XYZ[j] *= 100.0;
}

static void fill(ipatch *val, int index) {
    int k = 0;
    memset(val, 0, sizeof(*val));
    val->mtype = measurement_type();
    val->mcond = is("wrong_condition") ? inst_mrc_none : selected_condition;
    if (am("i1pro2") && (active & inst_mode_illum_mask) == inst_mode_reflection)
        val->mcond = is("no_uv_filter") ? inst_mrc_none : inst_mrc_uvcut;
    /* Each persona starts at its own shape, and a strip's patches differ. */
    for (const char *c = persona; *c; c++) k += *c;
    cm_synthetic_reading(val, (k + index) % 12, (active & inst_mode_illum_mask) == inst_mode_emission,
                         380.0, 730.0, 36);
    val->XYZ_v = 1;
    val->duration = 0.0;
    if (am("dtp51")) memset(&val->sp, 0, sizeof(val->sp));   /* no inst_mode_spectral: XYZ only */
}

static inst_code read_sample(inst *p, char *name, ipatch *val, instClamping clamp) {
    if (is("volatile_reference") && !calibrated) return inst_needs_cal;
    (void)p; (void)name; (void)clamp;
    require(initialized && mode_sets >= 1 && trigger_sets == 1);
    if (read_needs_calibration()) return inst_needs_cal | 7;
    {
        inst_code position = misplaced(0, inst_calc_none);
        if (position != inst_ok) return position;
    }
    fill(val, 0);
    if (is("spot_user_trigger")) return inst_user_trig | 7;
    return is("read_failure") ? inst_misread : inst_ok;
}

/* dtp41.c / munki.c read_strip. inst_user_trig is returned on the "switch"
 * fixture to prove the ladder keeps a pass the instrument actually read. */
static inst_code read_strip(inst *p, char *name, int npatch, char *pname, int sguide,
                            double pwid, double gwid, double twid, ipatch *vals) {
    (void)p;
    require(initialized && mode_sets >= 1 && trigger_sets == 1);
    require(am("dtp41") || am("munki") || am("dtp20") || am("dtp51") || am("i1pro3")
            || am("i1pro2"));
    /* Match the operands each upstream driver actually consumes. An ignored
     * operand must not become mandatory just because the stand-in checked it. */
    require(npatch == 4);
    if (am("dtp51")) {
        require(name && !strcmp(name, "STRIP") && pname && !strcmp(pname, "B"));
        require(sguide == 15);
    }
    if (is("zero_geometry")) {
        require(am("dtp41") && pwid == 6.0 && gwid == 0.0 && !signbit(gwid));
        require(twid == 0.0 && !signbit(twid));
    } else if (is("ui_strip")) {
        /* Product callback captured with 10/2.5 mm and no other layout fields.
         * No request echo rewriting: omitted operands must stay absent/default. */
        require(am("dtp41") && pwid == 10.0 && gwid == 2.5);
        require(sguide == -1 && twid == -1.0);
        require(!strcmp(name, "STRIP") && !strcmp(pname, "1"));
    } else if (am("dtp41") || am("dtp20")) require(pwid == 6.0 && gwid == 1.5);
    if (!is("zero_geometry") && am("dtp41")
        && (active & inst_mode_illum_mask) == inst_mode_transmission)
        require(twid == 3.0);
    ++strips;
    if (read_needs_calibration()) return inst_needs_cal | 7;
    {
        inst_code position = misplaced(0, inst_calc_none);
        if (position != inst_ok) return position;
    }
    if (is("read_strip_failure")) return inst_misread;
    for (int i = 0; i < npatch; i++) fill(&vals[i], i);
    if (is("strip_malformed_last")) vals[npatch - 1].sp.spec[1] = NAN;
    return is("strip_user_trigger") ? inst_user_trig : inst_ok;
}

/* ss.c's X-Y table. Each step records that it happened, and every later step
 * requires the earlier ones, so the ladder cannot reorder chartread's sequence
 * without aborting here. */
static inst_code xy_clear(inst *p) {
    (void)p; require(initialized);
    ++cleared; held = 0;
    return is("xy_clear_failure") || (is("xy_release_failure") && cleared > 1)
        ? inst_misread : inst_ok;
}
static inst_code xy_sheet_hold(inst *p) {
    /* No prior clear is required: spotread.c:1951-1963 holds the table straight
     * after binding, and only chartread clears first (:1070-1073, asserted in
     * read_xy below). */
    (void)p; require(initialized && !held);
    held = 1;
    return is("xy_hold_failure") ? inst_misread : inst_ok;
}
/* Part of a faithful inst2_xy_holdrel vtable (inst.h:893-894), and NOT called by
 * the ladder: chartread.c:1344-1353 releases with xy_clear, not with this. It is
 * here because the driver has it, not because anything exercises it. */
static inst_code xy_sheet_release(inst *p) { (void)p; require(held); held = 0; return inst_ok; }
static inst_code xy_locate_start(inst *p) {
    (void)p; require(held && !located);
    located = 1;
    return is("xy_locate_failure") ? inst_misread : inst_ok;
}
static inst_code xy_get_location(inst *p, double *x, double *y) {
    (void)p; require(located == 1 && locate_points < 3);
    /* Distinct per call, so a ladder that publishes one point three times fails. */
    *x = 10.0 + locate_points; *y = 20.0 + 2 * locate_points;
    ++locate_points;
    return inst_ok;
}
static inst_code xy_locate_end(inst *p) { (void)p; require(located == 1); located = 2; return inst_ok; }
/* spotread.c:2251 moves the head (measure=1), :2267 returns the sight (measure=0).
 * The pair must balance even when the reading itself failed, so a ladder that
 * returns early on a misread leaves this unbalanced and the final check aborts. */
static int positions[2];
static inst_code xy_position(inst *p, int measure, double x, double y) {
    (void)p; (void)x; (void)y;
    require(held && (measure == 0 || measure == 1));
    require(measure == 1 ? positions[1] == positions[0] : positions[1] == positions[0] + 1);
    ++positions[measure ? 1 : 0];
    if (!measure && is("xy_restore_failure")) return inst_misread;
    return inst_ok;
}

static inst_code read_xy(inst *p, int pis, int sip, int npatch, char *pname, char *sname,
                         double ox, double oy, double ax, double ay,
                         double aax, double aay, double px, double py, ipatch *vals) {
    (void)p;
    require(initialized && mode_sets >= 1 && held);
    require(cleared >= 1);   /* chartread.c:1070-1073 clears before the sheet */
    require(am("spectroscan") || am("spectroscant"));
    require(pis == 2 && sip == 3 && npatch == 6);
    require(pname && !strcmp(pname, " A") && sname && !strcmp(sname, " 1"));
    /* The eight navigation vectors, in chartread's order, must be EXACTLY what
     * the caller sent. The ladder does not compute them -- it relays them -- and
     * the default 1..8 is deliberately arbitrary so that a ladder inventing its
     * own cannot pass.
     *
     * The expectation is settable because two different properties are worth
     * proving with the same stub. The native check sends 1..8 to prove relay. A
     * caller driving the OPERATOR-SIGHTED path derives its vectors from the three
     * points this stub reports -- (10,20), (11,22), (12,24) -- and sets this to
     * the derived set to prove the derivation. Hardcoding 1..8 meant only the
     * explicit-geometry path could ever satisfy read_xy, so the sighted exchange
     * had never once been executed here: the test named for it passed `geometry=`
     * and skipped the sighting entirely. */
    {
        const char *wanted = getenv("CM_CHART_XY_VECTORS");
        double v[8] = {1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0};
        if (wanted && *wanted)
            require(sscanf(wanted, "%lf,%lf,%lf,%lf,%lf,%lf,%lf,%lf",
                           &v[0], &v[1], &v[2], &v[3], &v[4], &v[5], &v[6], &v[7]) == 8);
        require(ox == v[0] && oy == v[1] && ax == v[2] && ay == v[3]);
        require(aax == v[4] && aay == v[5] && px == v[6] && py == v[7]);
    }
    ++sheets;
    if (is("read_xy_failure")) return inst_misread;
    /* Preserve the pristine ss.c unknown-type case in this generic fixture.
     * ss_xy_metadata separately checks the composed driver's reflective metadata. */
    for (int i = 0; i < npatch; i++) { fill(&vals[i], i); vals[i].mtype = inst_mrt_none; }
    return inst_ok;
}

/* dtp20.c:440. The chart is already in the instrument, so there is no placement
 * and no trigger (inst.h:877-878); the pass counts arrive zero terminated. */
static inst_code read_chart(inst *p, int npatch, int pich, int sip, int *pis, int chid,
                            ipatch *vals) {
    int sheets = 0, passes = 0;
    (void)p;
    require(initialized && mode_sets >= 1 && am("dtp20"));
    require((npatch == 12 && pich == 4 && sip == 3 && !is("partial_chart"))
            || (npatch == 5 && pich == 2 && sip == 3 && is("partial_chart")));
    require(chid == 7);
    require(pis != NULL);
    while (pis[sheets]) passes += pis[sheets++];
    /* The list must be terminated and must account for exactly the chart's
     * passes; a ladder that forwarded an unterminated list would walk off here. */
    require(sheets == (is("partial_chart") ? 1 : 2) && passes == pich);
    ++sheets_read;
    if (is("read_chart_failure")) return inst_misread;
    /* dtp20.c: read_chart fills every slot, including a partial last row. */
    for (int i = 0; i < pich * sip; i++) fill(&vals[i], i);
    return inst_ok;
}

/* A fresh start (fixture "startup", nothing valid restored; "retained_calibration",
 * see retained()): what each driver needs before its first reading, and the
 * conditions it asks for, in order. The i1Pro, i1Pro 3 and ColorMunki set their
 * want-calibration flags at every init, so even a restored calibration is redone
 * unless that want is waived (i1pro_imp.c:1144-1145; i1pro3_imp.c:666-668;
 * munki_imp.c:676-677); the SpectroScan's first set_mode asks for its white
 * (ss.c:1856-1873). The i1Display needs nothing at startup.
 *
 * - i1Pro 2, with its wavelength LED: the white tile serves wavelength, dark and
 *   white (i1pro_imp.c:1742,1808,2224); ambient uses the ambient dark position
 *   (:2575-2581); transmission then asks for its white (:2607-2612, 2224-2225).
 * - i1Pro 3: the white tile (i1pro3_imp.c:1492-1502), then transmission white.
 * - ColorMunki: the dial at its calibration position for every mode
 *   (munki_imp.c:1061,1716-1734), then transmission white (:1735-1741).
 * - SpectroScan: no operator condition; it moves to its own tile
 *   (ss.c:1452-1462), and the T assumes its aperture is fitted (:1587-1607).
 * - A non-adaptive emissive spot or tele on any of the three spectrometers also
 *   needs its integration time chosen, which only that calibration marks done
 *   (i1pro_imp.c:1631-1636,2553; i1pro3_imp.c:1107-1112,1478; munki_imp.c,
 *   munki_imp_get_n_a_cals): after the first condition it asks for a full-white
 *   display patch (i1pro_imp.c:2614-2620; i1pro3_imp.c:1527-1532;
 *   munki_imp.c:1743-1749). An ambient or strip route needs none.
 * The DTP20, DTP41 and DTP51 ask only once the instrument itself reports a need;
 * the DTP22, with no persona here, calibrates at every open (dtp22.c:1191).
 * Returns the conditions' count; *bits is what get_n_a_cals reports. */
static int startup_calibration(inst_cal_cond conds[2], inst_cal_type *bits) {
    inst_mode illum = active & inst_mode_illum_mask;
    int trans = illum == inst_mode_transmission, refl = illum == inst_mode_reflection;
    int ambient = IMODETST(active, inst_mode_emis_ambient)
                  || IMODETST(active, inst_mode_emis_ambient_flash);
    int int_time = illum == inst_mode_emission
                   && (active & (inst_mode_spot | inst_mode_tele))
                   && (active & inst_mode_emis_nonadaptive);
    *bits = inst_calt_none;
    if (!startup()) return 0;
    if (am("i1pro2") || am("i1pro3")) {
        *bits = inst_calt_wavelength | (refl ? (am("i1pro2") ? inst_calt_ref_dark : 0)
                                               | inst_calt_ref_white
                                        : trans ? inst_calt_trans_dark | inst_calt_trans_vwhite
                                        : inst_calt_em_dark)
                | (int_time ? inst_calt_emis_int_time : 0);
        conds[0] = am("i1pro2") && ambient ? inst_calc_man_am_dark : inst_calc_man_ref_white;
        conds[1] = int_time ? inst_calc_emis_white : inst_calc_man_trans_white;
        return trans || int_time ? 2 : 1;
    }
    if (am("munki")) {
        *bits = (refl ? inst_calt_ref_dark | inst_calt_ref_white
                 : trans ? inst_calt_trans_dark | inst_calt_trans_vwhite : inst_calt_em_dark)
                | (int_time ? inst_calt_emis_int_time : 0);
        conds[0] = inst_calc_man_cal_smode;
        conds[1] = int_time ? inst_calc_emis_white : inst_calc_man_trans_white;
        return trans || int_time ? 2 : 1;
    }
    if (am("spectroscan") || am("spectroscant")) {
        *bits = inst_calt_ref_white | (trans ? inst_calt_trans_white : 0);
        return 0;
    }
    return 0;
}

static int calibration_fixture(void) {
    return is("sensor_calibration")
        || is("calibration_position_only")
        || is("white_calibration") || is("message_calibration")
        || is("uop_calibration") || is("volatile_reference") || late_calibration()
        || deferred_filter();
}

static inst_code get_n_a_cals(inst *p, inst_cal_type *needed, inst_cal_type *available) {
    inst_cal_cond conds[2];
    inst_cal_type startup_bits;
    (void)p; require(initialized);
    startup_calibration(conds, &startup_bits);
    if (startup()) {
        /* Retained: only the integration time, which no file holds. Masked: the
         * SpectroScan's whites, ss.c:1333-1349, restored or not. Session.c asks a
         * transmission white of its own accord from `available`. */
        if (needed) *needed = calibrated ? inst_calt_none
                            : retained() ? startup_bits & inst_calt_emis_int_time
                            : masked ? startup_bits & ~(inst_calt_ref_white | inst_calt_trans_white)
                            : startup_bits;
        if (available) *available = startup_bits;
        return inst_ok;
    }
    /* A calibration the driver holds is no longer needed (i1pro_imp.c:1554-1659,
     * dtp20.c, dtp41.c): once calibrate() returns inst_ok this is what the
     * session publishes as remaining. The volatile reference is volatile because
     * it lives only as long as the driver instance, not because it is asked for
     * again once held. A late calibration is re-discovered by the read below. */
    if (needed) *needed = calibrated ? inst_calt_none
                        : is("volatile_reference") ? inst_calt_trans_vwhite
                        : calibration_fixture() ? inst_calt_ref_white : inst_calt_none;
    if (available) *available = is("volatile_reference") ? inst_calt_trans_vwhite : inst_calt_ref_white;
    if (needed && late_calibration())
        *needed = read_discovered_calibration ? inst_calt_ref_white : inst_calt_none;
    return inst_ok;
}

/* Conditions the i1Pro family never raises, each in the shape its own driver
 * raises it. Before these, every calibration this project had ever driven was one
 * of the seven an i1Pro asks for. The DTP22's click (inst_calc_man_ref_whitek,
 * dtp22.c:712-720) is not here: DTP20 acknowledges its white and then sends CR
 * (dtp20.c:1174-1187), so the click lives on the dtp22 row of driver_personas.c,
 * and tests/test_instrument.py proves a client that answers nothing completes it. */
static int cal_rounds;
static int startup_step;

static inst_code calibrate(inst *p, inst_cal_type *calt, inst_cal_cond *calc,
                           inst_calc_id_type *idtype, char id[CALIDLEN]) {
    inst_cal_cond wanted;
    (void)p;
    require(initialized && calt && calc && idtype && id);
    if (is("late_calibration_failure")) return inst_internal_error;
    /* Each of these settles in two, a startup in three, or four when the operator
     * first has to move what a condition's position check named. */
    require(++cal_rounds <= 4);
    /* A vtable contract control, not a prediction of an i1Pro 3 calibration:
     * wrong-position recovery can precede every inst_cal_setup condition. */
    if (is("calibration_position_only")) {
        if (cal_rounds == 1) return inst_wrong_config | I1PRO3_SPOS_CAL;
        *calt = inst_calt_none;
        calibrated = 1;
        return inst_ok;
    }
    if (startup()) {
        inst_cal_cond conds[2];
        inst_cal_type bits;
        int n = startup_calibration(conds, &bits);
        /* The first condition serves what the restored calibration holds; what
         * is left is the second -- the transmission white or the display white. */
        if (retained() && n) {
            conds[0] = conds[1];
            n--;
        }
        /* The driver checks the position once the operator has established the
         * condition, before measuring it; the step stays open until it passes. */
        inst_code position = misplaced(1, *calc);
        if (position != inst_ok) return position;
        /* The condition the ladder hands back is the one established: it
         * completes that step, and the next is asked for (instappsup.c:186-189). */
        while (startup_step < n && (*calc & inst_calc_cond_mask) == conds[startup_step])
            startup_step++;
        if (startup_step < n) {
            *calc = conds[startup_step];
            *idtype = conds[startup_step] == inst_calc_man_ref_white ? inst_calc_id_ref_sn
                                                                    : inst_calc_id_none;
            if (*idtype == inst_calc_id_ref_sn) snprintf(id, CALIDLEN, "WHITE-0001");
            else id[0] = 0;
            return inst_cal_setup;
        }
        *calt = inst_calt_none;
        calibrated = 1;
        return inst_ok;
    }
    if (!calibration_fixture()) { *calt = inst_calt_none; return inst_ok; }
    wanted = deferred_filter() ? inst_calc_change_filter
           : is("volatile_reference") ? inst_calc_man_trans_white
           : is("white_calibration") ? inst_calc_man_ref_white
           : is("message_calibration") ? inst_calc_message
           : is("uop_calibration") || late_calibration() ? inst_calc_uop_ref_white
           : inst_calc_man_cal_smode;              /* munki_imp.c, the sensor dial */
    if ((*calc & inst_calc_cond_mask) != wanted) {
        *calc = wanted;
        if (deferred_filter()) {
            *idtype = inst_calc_id_filt_UV;
            snprintf(id, CALIDLEN, "UV cut Filter");  /* ss.c:102,113 */
        } else if (is("white_calibration")) {
            /* i1pro_imp.c / i1pro3_imp.c: the tile serial travels in id[], and
             * instappsup.c:256-259 prints it as "S/N %s". */
            *idtype = inst_calc_id_ref_sn;
            snprintf(id, CALIDLEN, "WHITE-0001");
        } else if (is("uop_calibration")) {
            /* dtp41.c / dtp51.c: the operator runs the calibration from the
             * instrument's own panel; nothing is placed and no id travels. */
            *idtype = inst_calc_id_none;
            id[0] = 0;
        } else if (is("message_calibration")) {
            /* inst_calc_message carries the driver's own text in id[], which the
             * operator is shown verbatim (instappsup.c:308-311). */
            *idtype = inst_calc_id_none;
            snprintf(id, CALIDLEN, "Warming up: wait for the lamp to stabilise");
        } else {
            *idtype = inst_calc_id_none;
            id[0] = 0;
        }
        return inst_cal_setup;
    }
    /* The ladder handed the condition back, with the optional flag cleared
     * (instappsup.c:419-420) whichever path it took. */
    require(!(*calc & inst_calc_optional_flag));
    *calt = inst_calt_none;
    calibrated = 1;
    read_discovered_calibration = 0;
    return inst_ok;
}

/* dispsup.c:374. The index is one the caller took from get_disptypesel(); this
 * substituted instrument only records that it arrived before the trigger. */
static inst_code get_disptypesel(inst *p, int *count, inst_disptypesel **list,
                                  int allconfig, int recreate) {
    static inst_disptypesel entries[3];
    (void)p; (void)allconfig; (void)recreate;
    memset(entries,0,sizeof(entries));
    *count=3; *list=entries;
    for (int i=0; i<3; i++) {
        sprintf(entries[i].desc,"Simulated display calibration %d",i);
        entries[i].cbid=i+1; entries[i].ix=i;
    }
    entries[0].flags=inst_dtflags_default;
    return inst_ok;
}

static inst_code set_disptype(inst *p, int index) {
    (void)p;
    require(initialized && mode_sets >= 1 && !trigger_sets);
    require(index == 2);
    return is("display_type_failure") ? inst_unsupported : inst_ok;
}

static int deleted;
static void del(inst *p) {
    /* Every head move must have been answered by a sight restore. */
    require(positions[0] == positions[1]);
    require(!deleted); ++deleted;
    free(p);
}

static void must_release(void) { require(deleted == 1); }

/* Description and acquisition share this exact substituted vtable. This is
 * not the real i1pro3 USB driver or an instrument-response emulator. */
inst *cm_chart_persona(const char *which, const char *selected) {
    inst *p;
    const char *position = getenv("CM_SENSOR_POSITION");
    if (selected && *selected) fixture = selected;
    if (which && *which) persona = which;
    if (position && *position) sensor_position = position;
    require(!*sensor_position || !strcmp(sensor_position, "read")
            || !strcmp(sensor_position, "stuck") || !strcmp(sensor_position, "calibrate")
            || !strcmp(sensor_position, "both") || !strcmp(sensor_position, "calibrate_stuck"));
    /* Every other Argyll type is a row of driver_personas.c's table. */
    if (!(am("spectroscan") || am("spectroscant") || am("dtp41") || am("munki")
          || am("dtp20") || am("dtp51") || am("i1pro3") || am("i1pro2"))) {
        /* Reads that report a calibration need, for a row whose driver keeps it. */
        const int needing = is("read_needs_calibration") ? 1
                          : is("read_needs_calibration_again") ? 2 : 0;
        require(!*sensor_position && (is("success") || is("retained_calibration") || needing));
        return cm_table_persona(persona, needing, is("retained_calibration"));
    }
    if (!(p = calloc(1, sizeof(inst)))) return NULL;
    atexit(must_release);
    /* insttypes.h:35-36, :32, :54 */
    p->dtype = am("dtp51") ? instDTP51
             : am("dtp20") ? instDTP20 : am("dtp41") ? instDTP41 : am("munki") ? instColorMunki
             : am("i1pro3") ? instI1Pro3 : am("i1pro2") ? instI1Pro2
             : am("spectroscant") ? instSpectroScanT : instSpectroScan;
    p->capabilities = capabilities;
    p->check_mode = check_mode;
    p->set_mode = set_mode;
    p->init_coms = init_coms;
    p->init_inst = init_inst;
    p->get_serial_no = get_serial_no;
    p->get_set_opt = options;
    p->read_sample = read_sample;
    p->get_n_a_cals = get_n_a_cals;
    p->calibrate = calibrate;
    p->set_uicallback = set_uicallback;
    p->meas_delay = stub_meas_delay;
    p->white_change = stub_white_change;
    p->set_disptype = set_disptype;
    p->get_disptypesel = get_disptypesel;
    p->del = del;
    if (am("munki") || am("i1pro3")) {   /* munki.c:1091, i1pro3.c:1033 */
        p->interp_error = interp_error;
        p->config_enum = config_enum;
    }
    /* Each driver fills only the vtable slots it serves, exactly as upstream
     * does; the ladder must therefore check for the method, not for a name. */
    if (am("dtp20")) {
        p->read_strip = read_strip;
        p->read_chart = read_chart;
    } else if (am("dtp41") || am("munki") || am("dtp51") || am("i1pro3") || am("i1pro2")) {
        /* The i1Pro3 scans three routes (reflective, emissive and transmissive)
         * and serves no chart or X-Y slot -- i1pro3.c fills read_strip and
         * neither read_chart nor any xy_* method. */
        p->read_strip = read_strip;
    } else {
        p->xy_clear = xy_clear;
        p->xy_sheet_hold = xy_sheet_hold;
        p->xy_sheet_release = xy_sheet_release;
        p->xy_locate_start = xy_locate_start;
        p->xy_get_location = xy_get_location;
        p->xy_locate_end = xy_locate_end;
        p->xy_position = xy_position;
        p->read_xy = read_xy;
    }
    return p;
}

#ifndef CM_CHART_DESCRIPTION
inst *cm_attach(inst_code (*uicallback)(void *, inst_ui_purp), void *context) {
    (void)uicallback; (void)context;
    return cm_chart_persona(getenv("CM_CHART_PERSONA"), getenv("CM_CHART_FIXTURE"));
}
#endif
