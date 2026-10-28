/* Original generic initialization/description process boundary.
 * Mode words are checked, but no mode, trigger, option or calibration is set
 * and no measurement is requested.
 * Initialization is ACTIVE device access and can have driver startup effects.
 * A wholly substituted backend runs this same core in software checks. */
#include "description.h"
#include "platform_io.h"
#include "driver_policy.h"
#include "strip_policy.h"
#include "serial_policy.h"
#include "mode_checks.h"
#define CM_DISPLAY_IMPLEMENTATION
#include "display_types.h"
#include <ctype.h>
#include <errno.h>
#include <limits.h>

static FILE *wire;
static const char *operation_id, *selected_path, *expected_serial;
static unsigned sequence, expected_dtype, expected_transport, expected_initialized_dtype;
static int list_passive, describe, identify_serial, initialized_dtype_given;
static int callback_refused;
static const char *accepted_initialization_reason;
static int accept_unassessed_initialization;

static void string(const char *text) { cm_utf8_json_string(wire,text); }
static size_t serialized_string_size(const char *text) {
    size_t bytes=2;
    for (const unsigned char *p=(const unsigned char *)text; *p; p++)
        bytes+=*p<0x20 ? 6 : (*p=='"' || *p=='\\' ? 2 : 1);
    return bytes;
}
static void start(const char *event) {
    fputs("{\"schema_version\":1,\"operation_id\":", wire); string(operation_id);
    fprintf(wire, ",\"sequence\":%u,\"event\":", sequence++); string(event);
    fputs(",\"provenance\":", wire); string(cm_description_provenance());
    fputs(",\"backend_version\":\"3.5.0\"", wire);
}
static int number(const char *text, unsigned *out, unsigned maximum) {
    unsigned long value; char *end;
    if (!text[0]) return 0;
    for (const char *p=text; *p; p++) if (*p < '0' || *p > '9') return 0;
    errno=0; value=strtoul(text, &end, 10);
    if (errno || *end || value > maximum) return 0;
    *out=(unsigned)value; return 1;
}
static int arguments(int argc, char **argv) {
    int dtype_given=0, transport_given=0;
    for (int i=1; i<argc; i++) {
        const char *key=argv[i];
        if (!strcmp(key, "--list-passive")) { if (list_passive++) return 0; continue; }
        if (!strcmp(key, "--describe")) { if (describe++) return 0; continue; }
        if (!strcmp(key, "--allow-serial-identification")) { if (identify_serial++) return 0; continue; }
        if (!strcmp(key, "--accept-unassessed-initialization")) { if (accept_unassessed_initialization++) return 0; continue; }
        if (++i >= argc) return 0;
        if (!strcmp(key, "--operation-id")) { if (operation_id) return 0; operation_id=argv[i]; }
        else if (!strcmp(key, "--path")) { if (selected_path) return 0; selected_path=argv[i]; }
        else if (!strcmp(key, "--expected-serial")) { if (expected_serial) return 0; expected_serial=argv[i]; }
        else if (!strcmp(key, "--accepted-initialization-reason")) {
            if (accepted_initialization_reason || !cm_utf8_text(argv[i], 100)) return 0;
            accepted_initialization_reason=argv[i];
        }
        else if (!strcmp(key, "--expected-dtype")) {
            if (dtype_given++ || !number(argv[i], &expected_dtype, INT_MAX)) return 0;
        } else if (!strcmp(key, "--expected-transport")) {
            if (transport_given++ || !number(argv[i], &expected_transport, icomt_portattr_mask)) return 0;
        } else if (!strcmp(key, "--expected-initialized-dtype")) {
            if (initialized_dtype_given++ || !number(argv[i], &expected_initialized_dtype, INT_MAX)) return 0;
        } else return 0;
    }
    if (!operation_id || !operation_id[0] || strlen(operation_id)>64 || list_passive+describe != 1) return 0;
    for (const unsigned char *p=(const unsigned char *)operation_id; *p; p++)
        if (!( (*p>='a' && *p<='z') || (*p>='A' && *p<='Z') || (*p>='0' && *p<='9') || *p=='_' || *p=='-')) return 0;
    if (list_passive) return !selected_path && !dtype_given && !transport_given && !identify_serial && !expected_serial && !initialized_dtype_given && !accepted_initialization_reason && !accept_unassessed_initialization;
    return selected_path && selected_path[0] && cm_utf8_text(selected_path, CM_DESCRIPTION_PATH_LIMIT)
        && dtype_given && transport_given && (expected_transport & icomt_port_mask)
        && (!expected_serial || (expected_serial[0] && cm_utf8_text(expected_serial, CM_DESCRIPTION_SERIAL_LIMIT)))
        && (!identify_serial || (initialized_dtype_given && expected_initialized_dtype!=instUnknown));
}
static inst_code callback(void *context, inst_ui_purp purpose) {
    (void)context;
    if (purpose == inst_negcoms) return inst_ok;
    callback_refused=1;
    return inst_user_abort;
}
static const char *init_refusal(instType dtype) {
    switch (dtype) {
        /* Argyll 3.5.0 spyd2.c:init_inst downloads PLD for these models. */
        case instSpyder1: case instSpyder2: return "initialization_downloads_pld";
        /* spyd2.c:init_inst unconditionally performs a dummy sensor reading. */
        case instSpyder3: case instSpyder4: case instSpyder5: return "initialization_reads_sensor";
        /* spydX.c / spydX2.c:init_inst directly call AmbMeasure. */
        case instSpyderX: case instSpyderX2: case instSpyder2024: return "initialization_measures_ambient";
        /* smcube.c:init_inst starts a thread consuming complete measurements. */
        case instSMCube: return "initialization_starts_measurement_monitor";
        /* kleink10.c:init_inst takes a startup flicker measurement. Refuse before
         * construction, which itself already attempts device communications. */
        case instKleinK10: return "initialization_measures_flicker";
        /* dtp20.c resets in init_coms and init_inst; dtp22.c / dtp51.c reset
         * device configuration during init_inst. */
        case instDTP20: case instDTP22: case instDTP51: return "initialization_resets_device";
        /* i1pro_imp.c:init performs a USB reset and establishes high power;
         * this is the legacy i1Pro family, not the separate i1Pro3 driver. */
        case instI1Monitor: case instI1Pro: case instI1Pro2: return "initialization_resets_device";
        /* dtp41.c:init_inst changes calibration behavior and expiry settings. */
        case instDTP41: return "initialization_changes_calibration_settings";
        /* specbos.c:init_inst selects automatic calibration for every model;
         * some models also restore default configuration first. */
        case instSpecbos1201: case instSpecbos2501: case instSpecbos: case instSpectraval:
            return "initialization_changes_calibration_settings";
        /* dtp92.c:init_inst sends EFC to select factory calibration. */
        case instDTP92: case instDTP94: return "initialization_selects_factory_calibration";
        /* ss.c:init_inst restores the original white reference unconditionally. */
        case instSpectrolino: return "initialization_restores_white_reference";
        /* ss.c:init_coms/init_inst/del can initialize or move the scanning table. */
        case instSpectroScan: case instSpectroScanT: return "initialization_or_cleanup_moves_table";
        /* huey.c:1130 turns the LEDs off, then huey.c:1145-1168 drives them through
         * an eight-step sequence with 50ms sleeps -- upstream's own comment is
         * "Flash the LEDs, just cos we can!". Each step is a real device command
         * (huey.c:803-817, i1d_set_leds), so initialisation both changes the
         * instrument's visible state and spends ~400ms doing it. */
        case instHuey: return "initialization_changes_indicator_leds";
        /* colorhug.c:609 turns the LEDs off, then colorhug.c:653-666 flashes them
         * through a four-step sequence with 50ms sleeps -- "Flash the LEDs" is
         * upstream's own comment, the same gesture the Huey makes. Found by the
         * provenance test, not by reading: the manual audit windowed the first 40
         * lines of a body that runs to 670 and saw only the firmware read. */
        case instColorHug: case instColorHug2:
        /* i1d3.c:3079-3080 -- "Flash the LED, just cos we can!" -- calls
         * i1d3_set_LEDs(p, i1d3_flash, 0.2, 0.05, 2), a two-pulse flash. Covers
         * the i1Display3 and the Smile, which share this driver. The third and
         * last instrument the provenance test caught in the harmless list. */
        case instI1Disp3: case instSmile:
            return "initialization_changes_indicator_leds";
#ifdef CM_CONTRIBUTED_instPR655
        /* pr655.c:init_coms calls pr655_imp_open, which sends Q/PHOTO/D111.
         * PHOTO enters remote mode and disables the touch screen. A clean close
         * sends Q; an uncertain exchange forbids cleanup writes and may leave
         * remote mode active (pr655.txt, Selection and initialization). */
        case instPR655: return "initialization_enters_remote_mode";
#endif
#ifdef CM_CONTRIBUTED_instPR670
        /* The PR-670 uses the same startup engine and remote-mode effects. */
        case instPR670: return "initialization_enters_remote_mode";
#endif
        default: return NULL;
    }
}

/* Instruments whose init_inst was READ and found to change nothing disclosable.
 *
 * This list exists because the absence of a refusal above said two different
 * things at once -- "assessed, and it does nothing to the device" and "nobody has
 * looked" -- and only one of those is a claim this project can make. An
 * instrument in neither list is reported `initialization_assessed: false` so a
 * caller can decide, rather than being told nothing and inferring safety.
 *
 * Each entry is a reading of the pinned source, not an assumption, and
 * tests/test_argyll_description.py re-derives every claim from the archive --
 * which is how instColorHug was caught being in this list wrongly:
 *   munki_imp.c:499-881 EEPROM reads and in-memory defaults; no write, no measure
 *   ex1.c:200           reads its alias, sets in-memory timeouts
 *   hcfr.c:406          reads and checks the firmware version
 *   i1disp.c:1802       an unlock handshake, then reads only under debug
 *   i1pro3_imp.c:549    EEPROM read and lamp timing state -- notably NOT the
 *                       usb reset i1pro_imp.c:544 does, which is why the legacy
 *                       i1Pro family is disclosed above and the i1Pro3 is not.
 * The unlock handshakes are protocol authentication, not the kind of state change
 * the refusals above name (firmware download, forced measurement, device reset,
 * calibration change, table movement). */
static int init_harmless(instType dtype) {
    switch (dtype) {
        case instColorMunki:
        case instEX1:
        case instHCFR:
        case instI1Disp1: case instI1Disp2:
        case instI1Pro3:
#ifdef CM_CONTRIBUTED_instCR250
        /* cr250.c init_coms sets the port and calls cr250_imp_identify, which
         * sends only RC Model, RC ID, RC Firmware and RC InstrumentType;
         * init_inst compares the cached firmware with the floor, no I/O. */
        case instCR250:
#endif
#ifdef CM_CONTRIBUTED_instCR100
        /* The CR-100 row of the same driver: the same four identity reads. */
        case instCR100:
#endif
#ifdef CM_CONTRIBUTED_instCR300
        /* The CR-300 row uses the same identity reads and cached firmware check. */
        case instCR300:
#endif
            return 1;
        default: return 0;
    }
}

static int init_assessed(instType dtype) {
    return init_refusal(dtype) != NULL || init_harmless(dtype);
}
/* Accept only the particular startup effect the caller was shown. Firmware
 * loading remains prohibited; a generic acknowledgement cannot authorize it. */
static const char *init_blocker(instType dtype) {
    const char *reason=init_refusal(dtype);
    if (reason) {
        if (strcmp(reason, "initialization_downloads_pld") &&
            accepted_initialization_reason && !strcmp(reason, accepted_initialization_reason))
            return NULL;
        return reason;
    }
    if (dtype!=instUnknown && !init_assessed(dtype) && !accept_unassessed_initialization)
        return "initialization_unassessed";
    return NULL;
}
static int identity_allowed(instType selected, instType actual) {
    /* Pinned i1pro_imp.c:522-563 can use the i1Pro driver for a path identified
     * as i1Pro2. No unrelated family transition is inferred from this case. */
    return actual!=instUnknown && (selected==actual || (selected==instI1Pro2 && actual==instI1Pro));
}
static void init_refusal_event(instType dtype, int *comma) {
    const char *reason=init_refusal(dtype);
    if (!reason) return;
    fprintf(wire, "%s{\"dtype\":%d,\"reason\":", (*comma)++ ? "," : "", (int)dtype);
    string(reason); fputc('}', wire);
}
static void candidate(icompath *path) {
    fputs("{\"path\":", wire); string(path->name);
    fprintf(wire, ",\"dtype\":%u,\"transport\":%u,\"initialization_assessed\":%s,"
                  "\"initialization_refusal\":",
        (unsigned)path->dtype, (unsigned)path->dctype & icomt_portattr_mask,
        init_assessed(path->dtype) ? "true" : "false");
    string(init_refusal(path->dtype)); fputc('}', wire);
}
CM_PROCESS_MAIN(cm_description_main) {
    icompaths *paths=NULL; icompath *selected=NULL, typed_path; inst *meter=NULL;
    int typed=0;
    const char *stage=NULL, *refusal=NULL, *serial=NULL;
    unsigned code=0; int count=0, success=0, initialized=0, constructed=0;
    int initialization_attempted=0, active_access_attempted=0, destroyed=0;
    instType actual=instUnknown, constructor_type=instUnknown;
    inst_mode cap=0, modes=0; inst2_capability cap2=0; inst3_capability cap3=0;
    inst_cal_cond conditions=inst_calc_unknown;
    inst_code rv, config_code=inst_unsupported; int configuration=-1;
    cm_mode_checks baseline_mode_checks;
    cm_display_catalogue display_types;
    if (!cm_description_backend_options(&argc, &argv) || !arguments(argc, argv)) {
        fputs("Invalid description arguments\n", stderr); return 2;
    }
    wire=cm_open_wire();
    if (!wire) return 2;
    start("started"); fputs(",\"operation\":", wire); string(list_passive ? "list_passive" : "describe");
    fputs(",\"initialization_refusals\":[", wire);
    {
        int comma=0;
        for (int dtype=instDTP22; dtype<=instColorHug2; dtype++)
            init_refusal_event((instType)dtype, &comma);
        /* Contributed enums follow the last official instrument type. Their
         * assessed startup effects belong in the same published refusal table. */
#ifdef CM_CONTRIBUTED_instPR655
        init_refusal_event(instPR655, &comma);
#endif
#ifdef CM_CONTRIBUTED_instPR670
        init_refusal_event(instPR670, &comma);
#endif
    }
    fputs("]}\n", wire);
    paths=cm_description_paths();
    if (!paths) { stage="enumeration_failed"; goto done; }
    if (paths->npaths<0 || paths->npaths>CM_DESCRIPTION_CANDIDATE_LIMIT || (paths->npaths && !paths->paths)) { stage="candidate_limit"; goto done; }
    for (int i=0; i<paths->npaths; i++) {
        icompath *path=paths->paths[i];
        if (!path || !path->name || !path->name[0] || !cm_utf8_text(path->name, CM_DESCRIPTION_PATH_LIMIT)) {
            stage="invalid_candidate_metadata"; goto done;
        }
        if (selected_path && !strcmp(selected_path, path->name)) { selected=path; count++; }
    }
    if (list_passive) {
        size_t payload=0;
        for (int i=0; i<paths->npaths; i++) payload+=serialized_string_size(paths->paths[i]->name)+160;
        /* Reserve at least 2 KiB for frame metadata. Fail before emitting any
         * candidate payload; never truncate a possibly selected candidate. */
        if (payload>14*1024) { stage="candidate_payload_limit"; goto done; }
        start("candidates"); fputs(",\"discovery_scope\":\"os_path_metadata_without_protocol_identification\",\"initialization_performed\":false,\"candidates\":[", wire);
        for (int i=0; i<paths->npaths; i++) { if (i) fputc(',', wire); candidate(paths->paths[i]); }
        fputs("]}\n", wire); success=1; goto done;
    }
    if (count!=1) { stage=count ? "ambiguous_path" : "path_not_found"; goto done; }
    if ((unsigned)selected->dtype!=expected_dtype || ((unsigned)selected->dctype & icomt_portattr_mask)!=expected_transport) {
        stage="candidate_identity_mismatch"; goto done;
    }
    refusal=init_blocker(selected->dtype);
    if (!refusal && initialized_dtype_given) refusal=init_blocker((instType)expected_initialized_dtype);
    if (refusal) { stage="initialization_refused"; goto done; }
    /* The listed path, dtype and transport were checked above; only a copy of
     * that exact path receives the caller's type (serial_policy.h). */
    typed=initialized_dtype_given && cm_unidentified_serial(selected)
        && cm_typed_serial((instType)expected_initialized_dtype);
    if (typed) {
        if (identify_serial) { stage="serial_identification_not_applicable"; goto done; }
        typed_path=cm_typed_serial_path(selected, (instType)expected_initialized_dtype);
    } else if (selected->dtype==instUnknown) {
        if (!cm_unidentified_serial(selected) || !identify_serial) {
            stage="serial_identification_requires_explicit_request"; goto done;
        }
        if (!cm_serial_driver_type((instType)expected_initialized_dtype)) {
            stage="expected_type_has_no_serial_driver"; goto done;
        }
    } else if (identify_serial) { stage="serial_identification_not_applicable"; goto done; }
    active_access_attempted=1;
    meter=cm_description_construct(typed ? &typed_path : selected, identify_serial,
        selected->dtype==instUnknown ? (instType)expected_initialized_dtype : selected->dtype, callback, NULL);
    if (!meter) { stage="construction_failed"; goto done; }
    constructed=1;
    if (!meter->get_itype || !meter->del || !meter->init_coms || !meter->init_inst || !meter->capabilities || !meter->get_serial_no) {
        stage="incomplete_driver_methods"; goto done;
    }
    actual=meter->get_itype(meter);
    refusal=init_blocker(actual);
    if (refusal) { stage="initialization_refused"; goto done; }
    if (actual==instUnknown || (selected->dtype!=instUnknown && actual!=selected->dtype)
        || (typed && (unsigned)actual!=expected_initialized_dtype)) {
        stage="constructed_identity_mismatch"; goto done;
    }
    constructor_type=actual;
    if (callback_refused) { stage="callback_refused"; goto done; }
    rv=meter->init_coms(meter, baud_nc, fc_nc, 5.0);
    if (rv!=inst_ok || callback_refused) { stage=callback_refused ? "callback_refused" : "init_coms_failed"; code=(unsigned)rv; goto done; }
    actual=meter->get_itype(meter);
    refusal=init_blocker(actual);
    if (refusal) { stage="initialization_refused"; goto done; }
    if (!identity_allowed(constructor_type, actual)) { stage="communications_identity_mismatch"; goto done; }
    initialization_attempted=1;
    rv=meter->init_inst(meter);
    if (rv!=inst_ok || callback_refused) { stage=callback_refused ? "callback_refused" : "init_inst_failed"; code=(unsigned)rv; goto done; }
    initialized=1;
    actual=meter->get_itype(meter);
    refusal=init_blocker(actual);
    if (refusal) { stage="initialization_refused"; goto done; }
    if (!identity_allowed(constructor_type, actual) || (initialized_dtype_given && (unsigned)actual!=expected_initialized_dtype)) {
        stage="initialized_identity_mismatch"; goto done;
    }
    serial=meter->get_serial_no(meter);
    if (serial && !serial[0]) serial=NULL;
    if (serial && !cm_utf8_text(serial, CM_DESCRIPTION_SERIAL_LIMIT)) { stage="invalid_serial_metadata"; goto done; }
    if (expected_serial && (!serial || strcmp(expected_serial, serial))) { stage="serial_identity_mismatch"; goto done; }
    meter->capabilities(meter, &cap, &cap2, &cap3);
    if (meter->meas_config) config_code=meter->meas_config(meter, &modes, &conditions, &configuration);
    if (callback_refused) { stage="callback_refused"; goto done; }
    cm_collect_mode_checks(meter, cap, &baseline_mode_checks);
    if (callback_refused) { stage="callback_refused"; goto done; }
    cm_collect_display_types(meter,cap2,&display_types);
    cm_emit_display_types(wire,&display_types,start,string);
    start("description"); fputs(",\"access_scope\":\"selected_device_active_initialization\",\"candidate\":", wire); candidate(selected);
    fprintf(wire, ",\"initialized_dtype\":%u,\"serial\":", (unsigned)actual); string(serial);
    fputs(",\"native_identity\":", wire);
    cm_description_native_identity(wire, meter);
    fprintf(wire, ",\"capabilities\":{\"primary\":%u,\"secondary\":%u,\"tertiary\":%u},\"current_configuration\":{\"status\":", (unsigned)cap, (unsigned)cap2, (unsigned)cap3);
    string(config_code==inst_ok ? "available" : "unavailable");
    fprintf(wire, ",\"native_code\":%u,\"index\":", (unsigned)config_code);
    if (config_code==inst_ok) fprintf(wire, "%d,\"modes\":%u,\"calibration_conditions\":%u", configuration, (unsigned)modes, (unsigned)conditions);
    else fputs("null,\"modes\":null,\"calibration_conditions\":null", wire);
    fprintf(wire, "},\"ambient_ccmx\":%s", cm_ambient_ccmx(meter) ? "true" : "false");
    cm_print_matrix_threshold(wire, meter);
    cm_print_strip_policies(wire, meter);
    cm_print_mode_checks(wire, &baseline_mode_checks);
    cm_print_display_types(wire,&display_types,meter,-1,string);
    fputs(",\"application_measurement_routes\":[]}\n", wire); success=1;
done:
    if (meter && meter->del) { meter->del(meter); destroyed=1; }
    if (paths) paths->del(paths);
    cm_description_backend_finished();
    if (!success) {
        start("error"); fputs(",\"stage\":", wire); string(stage);
        fprintf(wire, ",\"native_code\":%u,\"refusal_reason\":", code); string(refusal); fputs("}\n", wire);
    }
    start("terminal"); fputs(",\"status\":", wire); string(success ? "completed" : "failed");
    fprintf(wire, ",\"initialization_performed\":%s,\"initialization_completed\":%s,\"active_access_attempted\":%s,\"callback_refused\":%s,\"cleanup\":", initialization_attempted ? "true" : "false", initialized ? "true" : "false", active_access_attempted ? "true" : "false", callback_refused ? "true" : "false");
    string(destroyed ? "destructor_returned" : (constructed ? "destructor_unavailable" : "not_constructed"));
    fputs(",\"software_stopped\":true,\"instrument_state\":\"unknown\"}\n", wire);
    if (fclose(wire)) return 2;
    return success ? 0 : 1;
}
