/* Inert control-flow proof. Argyll function/declaration extracts are generated
 * from the digest-verified archive and retain their upstream notices. This
 * harness uses minimal structural stand-ins, scripted commands and lock counters.
 * No constructor, transport, operating-system lock or measurement tail is linked.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "calibration_declarations.h"

static unsigned checks, failures;
static const char *scenario;
static char failed[4096];
#define CHECK(test) do { \
    ++checks; \
    if (!(test)) { \
        ++failures; \
        if (strlen(failed) + strlen(scenario) + 2 < sizeof(failed)) { \
            if (failed[0]) strcat(failed, ","); \
            strcat(failed, scenario); \
        } \
    } \
} while (0)
static void a1logd(void *log, int level, const char *fmt, ...) {
    (void)log; (void)level; (void)fmt;
}

#ifdef CM_DTP41
typedef struct { int gotcoms, inited, need_cal; inst_mode mode, lastmode; void *log; } dtp41;
typedef dtp41 inst;
#include "calibration_functions.inc"
static void run_checks(void) {
    int transmission, initial_need;
    for (transmission = 0; transmission < 2; ++transmission) {
        for (initial_need = 0; initial_need < 2; ++initial_need) {
            dtp41 p = {.gotcoms=1, .inited=1, .need_cal=initial_need,
                       .mode=transmission ? inst_mode_trans_spot : inst_mode_ref_spot,
                       .lastmode=transmission ? inst_mode_trans_spot : inst_mode_ref_spot, .log=NULL};
            inst_cal_type bit = transmission ? inst_calt_trans_white : inst_calt_ref_white;
            inst_cal_type calt = bit | inst_calt_ap_flag, needed, available;
            inst_cal_cond calc = inst_calc_none;
            inst_calc_id_type idtype;
            char id[CALIDLEN];
            scenario = transmission ? "transmission_ack" : "reflection_unchanged";
            CHECK(dtp41_calibrate((inst *)&p, &calt, &calc, &idtype, id) == inst_cal_setup);
            CHECK(calc == (transmission ? inst_calc_uop_trans_white : inst_calc_uop_ref_white));
            CHECK(p.need_cal == initial_need && calt == (bit | inst_calt_ap_flag));
            CHECK(dtp41_calibrate((inst *)&p, &calt, &calc, &idtype, id) == inst_ok);
            CHECK(calt == inst_calt_ap_flag && p.need_cal == 0);
            CHECK(dtp41_get_n_a_cals((inst *)&p, &needed, &available) == inst_ok);
            CHECK(needed == inst_calt_none && available == bit);
            scenario = transmission ? "transmission_not_requested" : "reflection_not_requested";
            p.need_cal = 1; calt = inst_calt_none;
            CHECK(dtp41_calibrate((inst *)&p, &calt, &calc, &idtype, id) == inst_ok);
            CHECK(p.need_cal == 1 && calt == inst_calt_none);
            scenario = "unsupported_preserved";
            p.need_cal = 1; calt = inst_calt_emis_offset;
            CHECK(dtp41_calibrate((inst *)&p, &calt, &calc, &idtype, id) == inst_unsupported);
            CHECK(p.need_cal == 1 && calt == inst_calt_emis_offset);
            scenario = "preconditions_preserved";
            p.gotcoms = 0;
            CHECK(dtp41_calibrate((inst *)&p, &calt, &calc, &idtype, id) == inst_no_coms);
            p.gotcoms = 1; p.inited = 0;
            CHECK(dtp41_calibrate((inst *)&p, &calt, &calc, &idtype, id) == inst_no_init);
        }
    }
}
#endif

#ifdef CM_DTP92
typedef struct { int gotcoms, inited, dtype, need_offset_cal, need_ratio_cal; void *log; } dtp92;
typedef dtp92 inst;
static int command_count;
static char last_command[8];
static inst_code command_result;
static inst_code dtp92_command(dtp92 *p, char *cmd, char *out, int size, double timeout) {
    (void)p;
    CHECK(size > 0 && ((strcmp(cmd, "CO\r") == 0 && timeout == 12.0)
                       || (strcmp(cmd, "CR\r") == 0 && timeout == 25.0)));
    ++command_count; strcpy(last_command, cmd); out[0] = '\0';
    return command_result;
}
#include "calibration_functions.inc"
static dtp92 device(int model) {
    dtp92 p = {1, 1, model, 1, 1, NULL};
    command_count = 0; command_result = inst_ok; last_command[0] = '\0';
    return p;
}
static inst_code calibrate(dtp92 *p, inst_cal_type *calt, inst_cal_cond *calc) {
    inst_calc_id_type idtype;
    char id[CALIDLEN];
    return dtp92_calibrate((inst *)p, calt, calc, &idtype, id);
}
static void run_checks(void) {
    int model, ratio, failure;
    for (model = 0; model < 2; ++model) for (ratio = 0; ratio < 2; ++ratio) {
        dtp92 p = device(model ? instDTP94 : instDTP92);
        inst_cal_type bit = ratio ? inst_calt_emis_ratio : inst_calt_emis_offset;
        inst_cal_type calt = bit, needed, available;
        inst_cal_cond calc = inst_calc_none;
        if (model && ratio) {
            scenario = "dtp94_ratio_unsupported";
            CHECK(calibrate(&p, &calt, &calc) == inst_unsupported);
            CHECK(command_count == 0 && p.need_offset_cal && p.need_ratio_cal);
            continue;
        }
        scenario = ratio ? "ratio_completion" : "offset_completion";
        CHECK(calibrate(&p, &calt, &calc) == inst_cal_setup);
        CHECK(calc == (ratio ? inst_calc_emis_grey : inst_calc_man_em_dark));
        CHECK(command_count == 0 && p.need_offset_cal && p.need_ratio_cal);
        CHECK(calibrate(&p, &calt, &calc) == inst_ok);
        CHECK(command_count == 1 && !strcmp(last_command, ratio ? "CR\r" : "CO\r"));
        CHECK(calt == 0 && p.need_offset_cal == ratio && p.need_ratio_cal == !ratio);
        CHECK(dtp92_get_n_a_cals((inst *)&p, &needed, &available) == inst_ok);
        CHECK(needed == (ratio ? inst_calt_emis_offset : model ? 0 : inst_calt_emis_ratio));
        for (failure = 0; failure < (ratio ? 3 : 1); ++failure) {
            p = device(model ? instDTP94 : instDTP92); calt = bit;
            calc = ratio ? inst_calc_emis_grey : inst_calc_man_em_dark;
            command_result = failure == 1 ? inst_misread | DTP92_TOO_MUCH_LIGHT
                           : failure == 2 ? inst_misread | DTP92_NOT_ENOUGH_LIGHT : inst_coms_fail;
            scenario = "command_error_preserves_pending";
            CHECK(calibrate(&p, &calt, &calc) == (failure ? inst_cal_setup : inst_coms_fail));
            CHECK(calt == bit && p.need_offset_cal == 1 && p.need_ratio_cal == 1);
            if (failure) CHECK(calc == (failure == 1 ? inst_calc_emis_grey_darker : inst_calc_emis_grey_ligher));
            command_result = inst_ok;
            scenario = "retry_completion";
            CHECK(calibrate(&p, &calt, &calc) == inst_ok);
            CHECK(calt == 0 && p.need_offset_cal == ratio && p.need_ratio_cal == !ratio);
        }
    }
    /* This combined vtable request is not exposed by our current fresh-worker
     * API. It verifies preservation of the other calibration bit and flags. */
    for (model = 0; model < 4; ++model) {
        dtp92 p = device(instDTP92);
        inst_cal_type calt = model == 0 ? inst_calt_available : model == 1 ? inst_calt_needed
                           : model == 2 ? inst_calt_all : inst_calt_emis_offset | inst_calt_emis_ratio;
        inst_cal_type ap = model == 2 ? inst_calt_ap_flag : 0;
        inst_cal_cond calc = inst_calc_none;
        scenario = "combined_partial_and_completion";
        CHECK(calibrate(&p, &calt, &calc) == inst_cal_setup);
        CHECK(calc == inst_calc_man_em_dark && command_count == 0);
        CHECK(calibrate(&p, &calt, &calc) == inst_cal_setup);
        CHECK(calt == (inst_calt_emis_ratio | ap) && calc == inst_calc_emis_grey);
        CHECK(!p.need_offset_cal && p.need_ratio_cal && command_count == 1);
        /* Check the remaining step fails without undoing successful offset. */
        command_result = inst_coms_fail;
        CHECK(calibrate(&p, &calt, &calc) == inst_coms_fail);
        CHECK(calt == (inst_calt_emis_ratio | ap) && !p.need_offset_cal && p.need_ratio_cal);
        command_result = inst_ok;
        CHECK(calibrate(&p, &calt, &calc) == inst_ok);
        CHECK(calt == ap && !p.need_offset_cal && !p.need_ratio_cal && command_count == 3);
    }
}
#endif

#ifdef CM_SPECBOS
typedef int instClamping;
typedef struct { int spec_n; } spectrum;
typedef struct { spectrum sp; } ipatch;
typedef struct {
    int gotcoms, inited, doing_cal, trig, model, laser, refrmode, rrset, noaverage, setnav;
    inst_mode mode;
    double measto;
    spectrum trans_white;
    int *lock;
    void *log, *uic_cntx;
    inst_code (*uicallback)(void *, inst_ui_purp);
} specbos;
typedef specbos inst;
static int lock_depth, lock_errors, average_count, fail_average, abort_trigger, fail_laser;
static void amutex_lock(int *lock) { if (*lock) ++lock_errors; ++*lock; }
static void amutex_unlock(int *lock) { if (*lock != 1) ++lock_errors; --*lock; }
static void msec_sleep(int time) { (void)time; }
static inst_code specbos_command(specbos *p, char *cmd, char *out, int size, double timeout) {
    (void)p; (void)size; (void)timeout; out[0] = '\0';
    if (!strncmp(cmd, "*para:aver ", 11) || !strncmp(cmd, "*conf:aver ", 11)) {
        ++average_count;
        return average_count == fail_average ? inst_coms_fail : inst_ok;
    }
    if (!strcmp(cmd, "*contr:laser 0\r")) return fail_laser ? inst_coms_fail : inst_ok;
    if (!strcmp(cmd, "*contr:buttonled 0\r")) return inst_ok;
    CHECK(0); return inst_internal_error;
}
static inst_code specbos_get_headno(specbos *p, int *pos, int lock) {
    (void)lock; *pos = !!(p->mode & inst_mode_ambient); return inst_ok;
}
static inst_code specbos_interp_code(inst *p, int code) { (void)p; return inst_wrong_config | code; }
static inst_code specbos_imp_measure_set_refresh(specbos *p) { p->rrset = 1; return inst_ok; }
static inst_code specbos_measure_set_refresh(specbos *p) {
    inst_code rv;
    amutex_lock(p->lock); rv = specbos_imp_measure_set_refresh(p); amutex_unlock(p->lock);
    return rv;
}
static inst_code trigger(void *p, inst_ui_purp why) {
    (void)p; (void)why; return abort_trigger ? inst_user_abort : inst_user_trig;
}
#include "calibration_functions.inc"
static specbos device(int model) {
    specbos p;
    memset(&p, 0, sizeof(p));
    lock_depth = lock_errors = average_count = fail_average = abort_trigger = fail_laser = 0;
    p.gotcoms = p.inited = 1; p.model = model; p.trig = inst_opt_trig_prog;
    p.mode = inst_mode_emis_tele; p.measto = 10.0; p.setnav = 1; p.lock = &lock_depth;
    return p;
}
static void run_checks(void) {
    int models[] = {1201, 1211, 1501, 1511, 2501};
    int index, user, failure, ambient;
    for (index = 0; index < 5; ++index) {
        for (user = 0; user < 2; ++user) for (failure = 0; failure < 2; ++failure) {
            specbos p = device(models[index]);
            p.noaverage = 3;
            if (user) { p.trig = inst_opt_trig_user; p.uicallback = trigger; }
            fail_average = failure;
            scenario = failure ? "read_average_unlock" : "read_success_unchanged";
            CHECK(specbos_read_sample((inst *)&p, "synthetic", NULL, 0) == (failure ? inst_coms_fail : inst_ok));
            CHECK(lock_depth == 0 && lock_errors == 0 && average_count == 1);
            /* EXPL1211AVG is enabled in the pinned source: older models map
             * requested 3 to 1, whereas 1501/1511/2501 retain the request. */
            CHECK(p.setnav == (failure || models[index] == 1201 || models[index] == 1211 ? 1 : 3));
        }
        for (ambient = 0; ambient < 2; ++ambient) for (failure = 0; failure < 4; ++failure) {
            specbos p = device(models[index]);
            inst_mode initial_mode = (ambient ? inst_mode_trans_spot_a : inst_mode_trans_spot) | inst_mode_spectral;
            inst_cal_type calt = inst_calt_trans_vwhite;
            inst_cal_cond calc = inst_calc_none;
            inst_calc_id_type idtype;
            char id[CALIDLEN];
            p.mode = initial_mode;
            scenario = "calibration_condition_unchanged";
            CHECK(specbos_calibrate((inst *)&p, &calt, &calc, &idtype, id) == inst_cal_setup);
            CHECK(calc == inst_calc_man_trans_white && average_count == 0 && !p.doing_cal);
            fail_average = failure;
            scenario = failure == 1 ? "calibration_initial_average_cleanup"
                     : failure == 2 ? "calibration_read_average_unlock"
                     : failure == 3 ? "calibration_restore_average_cleanup" : "calibration_success_unchanged";
            CHECK(specbos_calibrate((inst *)&p, &calt, &calc, &idtype, id) == (failure ? inst_coms_fail : inst_ok));
            CHECK(p.mode == initial_mode && p.doing_cal == 0);
            CHECK(lock_depth == 0 && lock_errors == 0);
            CHECK(calt == (failure ? inst_calt_trans_vwhite : 0));
            CHECK(average_count == (failure == 1 ? 1 : 3));
            /* The original driver commits the synthetic reference before the
             * final averaging reset; this repair does not change that policy. */
            CHECK(p.trans_white.spec_n == (failure == 1 || failure == 2 ? 0 : 3));
        }
        {
            specbos p = device(models[index]);
            scenario = "abort_unlock_unchanged";
            p.uicallback = trigger; abort_trigger = 1;
            CHECK(specbos_read_sample((inst *)&p, "synthetic", NULL, 0) == inst_user_abort);
            CHECK(lock_depth == 0 && !lock_errors && !average_count);
            p = device(models[index]); fail_laser = 1;
            scenario = "laser_error_unlock_unchanged";
            CHECK(specbos_read_sample((inst *)&p, "synthetic", NULL, 0) == inst_coms_fail);
            CHECK(lock_depth == 0 && !lock_errors && !average_count);
        }
    }
}
#endif

int main(void) {
    run_checks();
    printf("{\"checks\":%u,\"failures\":%u,\"failed_scenarios\":\"%s\"}\n", checks, failures, failed);
    return failures ? 1 : 0;
}
