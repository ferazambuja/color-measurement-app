/* Focused app overlay proof. Reuses the contribution's synthetic actor and
 * actual driver constructor/vtable, without running its unchanged test suites.
 * Copyright 2026 Fernando Voltolini de Azambuja. GPL-2.0-or-later.
 */
#define uimain pr655_unchanged_adapter_suite
#include "pr655_driver_test.c"
#undef uimain
#include "pr655_control.h"
#include "pr655_support.h"
#include "native_facts.h"
#include "native_controls.h"

FILE *wire;
void cm_event_start(const char *event) { fprintf(wire, "{\"event\":\"%s\"", event); }
void cm_json_string(const char *s) {
    if (!s) { fputs("null", wire); return; }
    fputc('"', wire);
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') { fputc('\\', wire); fputc(c, wire); }
        else if (c < 32) fprintf(wire, "\\u%04x", c);
        else fputc(c, wire);
    }
    fputc('"', wire);
}
/* Reports contain two complete native setup texts. Keep every test-owned
 * container on the heap, as the production wire caller does: Clang can inline
 * these cases into one main frame larger than Windows' default stack reserve.
 * Inventories remain borrowed from the driver; only the container is freed. */
static cm_pr655_control_report *new_report(void) {
    cm_pr655_control_report *report = calloc(1, sizeof(*report));
    if (!report) abort();
    return report;
}
static void apertures(void) {
    add_owned("D117\r", "0000,2,Half degree,0.0\r\n0000,9,Quarter degree,0.0\r\n0000,15,Eighth degree,0.0\r\n");
}
static void control_steps(void) { setup_steps(); apertures(); }
static int exchange(pr655 *p, const char *command) {
    int i;
    for (i = 0; i < p->host.exchange_count; i++)
        if (!strcmp(p->host.exchanges[i].command, command)) return i;
    return -1;
}
static void normal_capture(void) {
    inst *p = driver(655);
    pr655 *c = (pr655 *)p;
    ipatch out;
    setup_steps(); capture_steps(655, 1, 1, 0, 0, 1e-4, 1, 1, NULL); setup_steps();
    cm_pr655_read_allowance(p, 400);
    CHECK(p->set_mode(p, inst_mode_emis_tele | inst_mode_spectral) == inst_ok);
    CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.XYZ_v);
    CHECK(c->host.exchange_count == 12 && c->host.required_s == 310 && !c->host.tracing && !c->engine.host_active);
    CHECK(exchange(c, "M5") >= 0 && exchange(c, "D2") >= 0 && c->host.exchanges[exchange(c, "M5")].complete);
    CHECK(c->host.exchanges[exchange(c, "M5")].raw.length > 1024);
    cm_pr655_native_facts(p);
    CHECK(pr655_required_floor_s(instPR655, inst_mode_emis_tele | inst_mode_spectral) == 310);
    CHECK(pr655_required_floor_s(instPR670, inst_mode_emis_spot | inst_mode_colorimeter) == 310);
    CHECK(pr655_required_floor_s(instCR250, inst_mode_emis_tele) < 0);
    c->inited = 0;
    CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_no_init);
    CHECK(!c->host.exchange_count && !c->host.tracing && !c->capture.valid && !c->engine.host_active);
    finish(p);
}
static int trigger_waits;
static inst_code slow_trigger(void *context, inst_ui_purp purpose) {
    (void)context;
    if (purpose != inst_armed) return inst_ok;
    return trigger_waits-- > 0 ? inst_ok : inst_user_trig;
}
static void allowance_refusal(void) {
    int i;
    for (i = 0; i < 3; i++) {
        inst *p = driver(655);
        pr655 *c = (pr655 *)p;
        ipatch out;
        setup_steps();
        cm_pr655_read_allowance(p, i == 2 ? 311 : 310);
        if (i == 1) CHECK(p->get_set_opt(p, inst_opt_set_averages, 2) == inst_ok);
        if (i == 2) {
            trigger_waits = 15; p->uicallback = slow_trigger;
            CHECK(p->get_set_opt(p, inst_opt_trig_user) == inst_ok);
        }
        CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CM_PR655_ALLOWANCE);
        CHECK(!out.XYZ_v && !c->capture.native.exposure_started && exchange(c, "M2") < 0 && exchange(c, "SN2") < 0);
        CHECK(c->host.remaining_s < c->host.required_s && c->engine.state == pr655_ready);
        finish(p);
    }
}
static void invalid_allowance(void) {
    const double invalid[] = {NAN, -1.0, 86400.1};
    int i;
    for (i = 0; i < 3; i++) {
        inst *p = driver(655);
        pr655 *c = (pr655 *)p;
        ipatch out;
        int writes = transport.writes;
        cm_pr655_read_allowance(p, invalid[i]);
        CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == PR655_BAD_SETUP);
        CHECK(transport.writes == writes && c->engine.state == pr655_ready && !c->host.exchange_count && !out.XYZ_v);
        finish(p);
    }
}
static void metadata_checks(void) {
    int scenario;
    for (scenario = 0; scenario < 3; scenario++) {
        inst *p = driver(670);
        pr655 *c = (pr655 *)p;
        ipatch out;
        setup_steps(); capture_steps(670, 0, 1, 0, 0, 1e-4, 1, 1, NULL); setup_steps();
        add_owned("D13\r", scenario == 1 ? "0000,Gain,NaN msec\r\n" : "0000,Gain,16500 msec\r\n");
        if (scenario != 1) add_owned("D14\r", scenario == 2 ? "-35\r\n" : "0000,User Sync,59.94 Hertz\r\n");
        cm_pr655_read_allowance(p, 400); cm_pr655_capture_metadata(p, 1);
        CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.XYZ_v && c->capture.valid);
        CHECK(c->host.required_s == 330 && c->host.exposure.queried);
        if (scenario == 0) CHECK(c->host.exposure.parsed && c->host.synchronization.parsed && c->host.synchronization.value == 59.94);
        if (scenario == 1) CHECK(!c->host.exposure.parsed && !c->host.synchronization.queried && c->engine.state == pr655_quarantined);
        if (scenario == 2) CHECK(c->host.exposure.parsed && c->host.synchronization.complete && c->host.exchanges[exchange(c, "D14")].error == -35);
        cm_pr655_native_facts(p); finish(p);
    }
}
static void controls_read_and_validate(void) {
    inst *p = driver(670);
    pr655 *c = (pr655 *)p;
    cm_pr655_control_report *r = new_report();
    cm_pr655_control_request q = {0};
    full_setup(1, 2, 1, 0, 59, 0, 0, 8, -1);
    strcpy(text_setup + strlen(text_setup) - strlen("59.00 Hertz\r\n"), "59.94 Hertz\r\n");
    control_steps();
    CHECK(cm_pr655_control_read(p, r, 30) == inst_ok && r->setup_valid && r->aperture_list_valid);
    CHECK(r->values.syncFrequencyHz == 59.94 && !(r->values.present & CM_PR655_BIT(CM_PR655_SHUTTER)));
    CHECK(r->apertures[1].id == 9 && r->accessories[0].id == 8 && c->host.exchange_count == 5);
    cm_pr655_control_facts(p);
    CHECK((cm_pr655_control_read(p, r, NAN) & inst_imask) == PR655_BAD_SETUP);
    CHECK(!c->host.exchange_count && !c->host.tracing);
    q.present = CM_PR655_BIT(CM_PR655_CYCLES); q.cycles = 1; control_steps();
    CHECK(cm_pr655_control_apply(p, &q, r, 10) == inst_ok && !r->changed && !r->attempted && r->confirmed == q.present);
    q.present = CM_PR655_BIT(CM_PR655_EXPOSURE); q.exposureMilliseconds = 15000;
    control_steps();
    CHECK((cm_pr655_control_apply(p, &q, r, 100) & inst_imask) == PR655_REFUSED_COMMAND);
    CHECK(!r->attempted && !r->acknowledged && exchange(c, "SE15000") < 0 && c->host.control.failed);
    full_setup(1, 2, 1, 0, 59, 0, 0, 8, -1);
    strcpy(text_setup + strlen(text_setup) - strlen("59.00 Hertz\r\n"), "1e100 Hertz\r\n");
    control_steps();
    CHECK(cm_pr655_control_read(p, r, 30) == inst_ok && r->values.syncFrequencyHz == 1e100);
    q.present = CM_PR655_BIT(CM_PR655_FREQUENCY); q.syncFrequencyHz = 59.94;
    control_steps();
    CHECK((cm_pr655_control_apply(p, &q, r, 100) & inst_imask) == PR655_REFUSED_COMMAND && !r->attempted);
    finish(p);
    free(r);
}
static void exposure_order(void) {
    int lower;
    for (lower = 0; lower < 2; lower++) {
        inst *p = driver_with_setup(670, lower ? 15000 : 0, lower, 1);
        pr655 *c = (pr655 *)p;
        cm_pr655_control_report *r = new_report();
        cm_pr655_control_request q = {0};
        control_steps();
        q.present = CM_PR655_BIT(CM_PR655_EXPOSURE) | CM_PR655_BIT(CM_PR655_SENSITIVITY);
        q.exposureMilliseconds = lower ? 3000 : 15000; q.sensitivityMode = !lower;
        add_owned(lower ? "SE3000\r" : "SH1\r", "0000\r\n");
        full_setup(1, 2, 1, 0, 75, lower ? 3000 : 0, 1, 8, -1); setup_steps();
        add_owned(lower ? "SH0\r" : "SE15000\r", "0000\r\n");
        full_setup(1, 2, 1, 0, 75, q.exposureMilliseconds, !lower, 8, -1); setup_steps();
        control_steps();
        CHECK(cm_pr655_control_apply(p, &q, r, 100) == inst_ok && r->confirmed == q.present);
        CHECK(r->attempted == q.present && r->acknowledged == q.present && !r->failed);
        CHECK(exchange(c, lower ? "SE3000" : "SH1") < exchange(c, lower ? "SH0" : "SE15000"));
        finish(p);
        free(r);
    }
}
static void ack_and_collateral(void) {
    int scenario;
    for (scenario = 0; scenario < 4; scenario++) {
        inst *p = driver(670);
        pr655 *c = (pr655 *)p;
        cm_pr655_control_report *r = new_report();
        cm_pr655_control_request q = {0};
        control_steps();
        q.present = CM_PR655_BIT(scenario == 0 ? CM_PR655_SHUTTER : CM_PR655_CYCLES);
        q.shutterMode = 0; q.cycles = 2;
        add_owned(scenario == 0 ? "SZ0\r" : "SN2\r", scenario == 3 ? "-35\r\n" : "0000\r\n");
        if (scenario != 3) {
            full_setup(1, scenario == 2 ? 10 : 2, scenario == 1 ? 1 : scenario == 0 ? 1 : 2, 0, 75, 0, 0, 8, -1);
            if (scenario != 0) setup_steps();
            if (scenario == 0) control_steps();
        }
        if (scenario == 0) {
            CHECK(cm_pr655_control_apply(p, &q, r, 100) == inst_ok && r->acknowledged == q.present && !r->confirmed);
            CHECK(!(r->values.present & CM_PR655_BIT(CM_PR655_SHUTTER)));
        } else {
            CHECK((cm_pr655_control_apply(p, &q, r, 100) & inst_imask) == (scenario == 3 ? PR655_NATIVE_ERROR_BASE + 35 : PR655_READBACK_ERROR));
            CHECK(r->attempted == q.present && !r->confirmed && r->acknowledged == (scenario == 3 ? 0 : q.present));
            CHECK(exchange(c, "SN2") >= 0 && c->host.control.failed);
        }
        cm_pr655_control_facts(p); finish(p);
        free(r);
    }
}
static void raw_and_unverified(void) {
    inst *p = driver(655);
    pr655 *c = (pr655 *)p;
    ipatch out;
    setup_steps(); capture_steps(655, 1, 1, 0, 0, 1e-4, 1000, 1, NULL); setup_steps();
    CHECK(p->set_mode(p, inst_mode_emis_tele | inst_mode_spectral) == inst_ok);
    CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == PR655_UNIT_MISMATCH);
    CHECK(c->capture.native.spectrum_parsed && c->capture.native.xyz_parsed && !c->capture.valid && !out.XYZ_v);
    cm_pr655_native_facts(p); finish(p);
    p = driver(655); c = (pr655 *)p; setup_steps();
    capture_steps(655, 0, 1, 0, 112, 1e-4, 1, 1, NULL); setup_steps();
    CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == PR655_GEOMETRY_ERROR);
    CHECK(c->capture.native.xyz_parsed && c->capture.native.xyz_units == 112);
    cm_pr655_native_facts(p); finish(p);
    p = driver(655); c = (pr655 *)p;
    add_owned("D601\r", "0000,\x80\r\n");
    CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == PR655_DATA_PARSE_ERROR);
    CHECK(c->host.exchange_count == 1 && !c->host.exchanges[0].complete
        && (unsigned char)c->host.exchanges[0].raw.bytes[5] == 0x80);
    cm_pr655_native_facts(p); finish(p);
}
static void model_and_sync_checks(void) {
    inst *p = driver(655);
    cm_pr655_control_request q = {0};
    cm_pr655_control_report *r = new_report();
    pr655 *c = (pr655 *)p;
    control_steps(); q.present = CM_PR655_BIT(CM_PR655_APERTURE); q.apertureId = 9;
    CHECK((cm_pr655_control_apply(p, &q, r, 100) & inst_imask) == PR655_REFUSED_COMMAND && !r->attempted);
    finish(p);
    p = driver(670); c = (pr655 *)p; control_steps();
    q.present = CM_PR655_BIT(CM_PR655_FREQUENCY) | CM_PR655_BIT(CM_PR655_SYNC);
    q.syncFrequencyHz = 120; q.syncMode = 3;
    add_owned("SK120\r", "0000\r\n"); full_setup(1, 2, 1, 0, 120, 0, 0, 8, -1); setup_steps();
    add_owned("SS3\r", "0000\r\n"); full_setup(1, 2, 1, 3, 120, 0, 0, 8, -1); setup_steps(); control_steps();
    CHECK(cm_pr655_control_apply(p, &q, r, 100) == inst_ok && r->confirmed == q.present);
    CHECK(exchange(c, "SK120") < exchange(c, "SS3")); finish(p);
    free(r);
}
static void addon_setup_steps(void) {
    add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
    add_owned("D112\r", "0000,3,3\r\n");
    add_owned("D116\r", "0000,8,Test lens,Primary,Luminance,Radiance\r\n0000,9,First addon,Addon,Luminance,Radiance\r\n0000,15,Second addon,Addon,Luminance,Radiance\r\n");
}
static void addon_swaps(void) {
    inst *p = driver(670);
    pr655 *c = (pr655 *)p;
    cm_pr655_control_request q = {0};
    cm_pr655_control_report *r = new_report();
    full_setup(1, 2, 1, 0, 75, 0, 0, 8, 9);
    /* Two active slots may not be swapped without a calibrated clearing ID. */
    {
        char *end = strstr(numeric_setup, ",9,-1,-1,");
        CHECK(end != NULL);
        snprintf(numeric_setup, sizeof(numeric_setup), "0000,8,9,15,-1,2,1,73,0,87,1,2,0,0,91,75.0\r\n");
    }
    addon_setup_steps(); apertures();
    q.present = CM_PR655_BIT(CM_PR655_ADDON1) | CM_PR655_BIT(CM_PR655_ADDON2);
    q.addon1Id = 15; q.addon2Id = 9;
    CHECK((cm_pr655_control_apply(p, &q, r, 100) & inst_imask) == PR655_REFUSED_COMMAND);
    CHECK(!r->attempted && exchange(c, "SA15") < 0 && exchange(c, "SB9") < 0);
    finish(p);
    free(r);
}
static void primary_setup(int primary, int aperture, int cycles) {
    full_setup(1, 2, cycles, 0, 75, 0, 0, primary, -1);
    snprintf(numeric_setup, sizeof(numeric_setup),
        "0000,%d,-1,-1,-1,%d,1,73,0,87,%d,2,0,0,91,75.0\r\n", primary, aperture, cycles);
    snprintf(text_setup, sizeof(text_setup),
        "0000,%s,None,None,None,%s,Metric,Adaptive,0 msec,Native gain,%d cycles,2 deg,No Smart Dark,No Sync,Standard Sensitivity,75.00 Hertz\r\n",
        primary == 8 ? "Test lens" : "Other primary",
        aperture == 2 ? "Half degree" : aperture == 9 ? "Quarter degree"
            : aperture == 15 ? "Eighth degree" : "Unknown aperture", cycles);
}
static void primary_setup_steps(void) {
    add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
    add_owned("D112\r", "0000,2,3\r\n");
    add_owned("D116\r", "0000,8,Test lens,Primary,Luminance,Radiance\r\n0000,42,Other primary,Primary,Luminance,Radiance\r\n");
}
static void primary_control_steps(int primary, int aperture, int cycles) {
    primary_setup(primary, aperture, cycles); primary_setup_steps(); apertures();
}
static void primary_aperture_coupling(void) {
    const int targets[] = {15, 2, 9, 9};
    int scenario;
    for (scenario = 0; scenario < 4; scenario++) {
        inst *p = driver(670);
        pr655 *c = (pr655 *)p;
        cm_pr655_control_report *r = new_report();
        cm_pr655_control_request q = {0};
        char command[8];
        q.present = CM_PR655_BIT(CM_PR655_PRIMARY) | CM_PR655_BIT(CM_PR655_APERTURE);
        q.primaryAccessoryId = 42; q.apertureId = targets[scenario];
        primary_control_steps(8, 2, 1);
        add_owned("SP42\r", "0000\r\n");
        /* The chosen IDs and labels do not define the largest aperture. Only
         * the observed setup and fresh inventory establish the auto change. */
        primary_setup(42, scenario == 3 ? 2 : 15, 1); primary_setup_steps(); apertures();
        snprintf(command, sizeof(command), "SF%lld\r", q.apertureId);
        if (scenario) {
            add_owned(command, "0000\r\n");
            primary_setup(42, q.apertureId, 1); primary_setup_steps();
        }
        primary_control_steps(42, q.apertureId, 1);
        CHECK(cm_pr655_control_apply(p, &q, r, 100) == inst_ok);
        CHECK(r->before.apertureId == 2 && r->values.apertureId == q.apertureId);
        CHECK(r->changed == q.present && r->confirmed == q.present && !r->blocked && !r->failed);
        CHECK(r->attempted == (scenario ? q.present : CM_PR655_BIT(CM_PR655_PRIMARY)));
        CHECK(r->acknowledged == r->attempted && r->aperture_list_valid && c->engine.state == pr655_ready);
        CHECK(exchange(c, "SP42") >= 0);
        CHECK((exchange(c, scenario == 1 ? "SF2" : scenario == 2 || scenario == 3 ? "SF9" : "SF15") >= 0) == !!scenario);
        CHECK(transport.next == transport.count);
        cm_pr655_control_facts(p); finish(p);
        free(r);
    }
}
static void primary_aperture_refusals(void) {
    int scenario;
    const unsigned int primary = CM_PR655_BIT(CM_PR655_PRIMARY);
    const unsigned int aperture = CM_PR655_BIT(CM_PR655_APERTURE);
    for (scenario = 0; scenario < 15; scenario++) {
        inst *p = driver(scenario == 10 ? 655 : 670);
        pr655 *c = (pr655 *)p;
        cm_pr655_control_report *r = new_report();
        cm_pr655_control_request q = {0};
        int expected = scenario == 4 ? PR655_BAD_SETUP
            : scenario == 7 || scenario == 9 ? PR655_NATIVE_ERROR_BASE + 35 : PR655_READBACK_ERROR;
        q.present = primary | (scenario && scenario != 10 ? aperture : 0);
        if (scenario == 9) { q.present |= CM_PR655_BIT(CM_PR655_CYCLES); q.cycles = 1; }
        q.primaryAccessoryId = 42;
        q.apertureId = scenario == 2 ? 9 : scenario == 3 || scenario == 8 || scenario == 13 ? 15 : 2;
        primary_control_steps(8, 2, 1);
        add_owned("SP42\r", "0000\r\n");
        primary_setup(42, scenario == 1 ? 99 : scenario == 14 ? 2 : 15, scenario == 5 ? 2 : 1);
        if (scenario == 11 || scenario == 12) {
            add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
            add_owned("D112\r", scenario == 11 ? "0000,2,2\r\n" : "0000,2,3\r\n");
            add_owned("D116\r", scenario == 12
                ? "0000,8,Test lens,Primary,Luminance,Radiance\r\n0000,42,Changed primary,Primary,Luminance,Radiance\r\n"
                : "0000,8,Test lens,Primary,Luminance,Radiance\r\n0000,42,Other primary,Primary,Luminance,Radiance\r\n");
        } else primary_setup_steps();
        if (scenario && scenario != 5 && (scenario < 10 || scenario >= 13)) {
            if (scenario == 2)
                add_owned("D117\r", "0000,2,Half degree,0.0\r\n0000,99,New aperture,0.0\r\n0000,15,Eighth degree,0.0\r\n");
            else if (scenario == 3)
                add_owned("D117\r", "0000,2,Changed description,0.0\r\n0000,9,Quarter degree,0.0\r\n0000,15,Eighth degree,0.0\r\n");
            else if (scenario == 4)
                add_owned("D117\r", "0000,2,Half degree,0.0\r\n0000,9,Quarter degree,0.0\r\n0000,9,Duplicate aperture,0.0\r\n");
            else if (scenario == 7) add_owned("D117\r", "-35\r\n");
            else apertures();
        }
        if (scenario == 6 || scenario == 9) {
            add_owned("SF2\r", scenario == 9 ? "-35\r\n" : "0000\r\n");
            if (scenario == 6) { primary_setup(42, 15, 1); primary_setup_steps(); }
        }
        if (scenario == 8) {
            primary_setup_steps();
            add_owned("D117\r", "0000,2,Changed description,0.0\r\n0000,9,Quarter degree,0.0\r\n0000,15,Eighth degree,0.0\r\n");
        }
        if (scenario == 13 || scenario == 14) primary_control_steps(42, 9, 1);
        CHECK((cm_pr655_control_apply(p, &q, r, 100) & inst_imask) == expected);
        CHECK(r->before.apertureId == 2);
        if (scenario == 9) CHECK(!r->setup_valid && !r->values.present && !r->confirmed);
        else CHECK(r->values.apertureId == (scenario == 1 ? 99 : scenario >= 13 ? 9 : 15));
        CHECK(r->attempted == (scenario == 6 || scenario == 9 ? primary | aperture : primary));
        CHECK(r->acknowledged == (scenario == 6 ? primary | aperture : primary));
        CHECK(r->blocked == (primary | aperture) && r->failed);
        CHECK(!(r->changed & ~q.present));
        if (scenario == 0 || scenario == 10) CHECK(r->changed == primary && !(r->requested.present & aperture));
        if (scenario == 14) CHECK(r->changed == primary && r->confirmed == primary);
        CHECK((r->confirmed & aperture) == (scenario == 8 ? aperture : 0));
        CHECK((exchange(c, "SF2") >= 0) == (scenario == 6 || scenario == 9));
        CHECK(c->engine.state == (scenario == 7 || scenario == 9 ? pr655_ready : pr655_quarantined));
        CHECK(transport.next == transport.count);
        cm_pr655_control_facts(p);
        if (scenario == 7) {
            /* An auto change creates aperture debt even though no SF was sent.
             * Readback of primary alone cannot clear that recovery obligation. */
            q.present = primary; primary_control_steps(42, 15, 1);
            CHECK(cm_pr655_control_apply(p, &q, r, 100) == inst_ok && !r->attempted);
            CHECK(r->blocked == (primary | aperture) && r->confirmed == primary);
            q.present |= aperture; q.apertureId = 15; primary_control_steps(42, 15, 1);
            CHECK(cm_pr655_control_apply(p, &q, r, 100) == inst_ok && !r->attempted);
            CHECK(!r->blocked && r->confirmed == q.present && !r->acknowledged);
            cm_pr655_control_facts(p);
        }
        finish(p);
        free(r);
    }
}
static void primary_aperture_preflight(void) {
    int scenario;
    for (scenario = 0; scenario < 4; scenario++) {
        inst *p = driver(670);
        pr655 *c = (pr655 *)p;
        cm_pr655_control_report *r = new_report();
        cm_pr655_control_request q = {0};
        q.present = CM_PR655_BIT(CM_PR655_PRIMARY) | CM_PR655_BIT(CM_PR655_APERTURE);
        q.primaryAccessoryId = 42; q.apertureId = scenario == 0 ? 99 : scenario == 2 ? 15 : 2;
        if (scenario == 3) { q.present |= CM_PR655_BIT(CM_PR655_CYCLES); q.cycles = 2; }
        primary_control_steps(8, 2, 1);
        CHECK((cm_pr655_control_apply(p, &q, r, scenario == 0 ? 100 : scenario == 3 ? 59 : 49) & inst_imask)
            == (scenario ? CM_PR655_ALLOWANCE : PR655_REFUSED_COMMAND));
        CHECK(!r->attempted && !r->acknowledged && !r->blocked && c->engine.state == pr655_ready);
        CHECK(exchange(c, "SP42") < 0 && exchange(c, "SF2") < 0 && exchange(c, "SF15") < 0 && exchange(c, "SN2") < 0);
        CHECK(c->host.exchange_count == 5 && transport.next == transport.count);
        cm_pr655_control_facts(p); finish(p);
        free(r);
    }
}
static void confirmed_drift_recovery(void) {
    const unsigned int primary = CM_PR655_BIT(CM_PR655_PRIMARY);
    const unsigned int cycles = CM_PR655_BIT(CM_PR655_CYCLES);
    unsigned int debt;
    inst *p = driver(670);
    pr655 *c = (pr655 *)p;
    cm_pr655_control_report *r = new_report();
    cm_pr655_control_request q = {0};
    q.present = primary | cycles; q.primaryAccessoryId = 42; q.cycles = 1;
    primary_control_steps(8, 2, 1);
    add_owned("SP42\r", "0000\r\n");
    primary_setup(42, 2, 1); primary_setup_steps();
    primary_control_steps(42, 2, 2);
    CHECK((cm_pr655_control_apply(p, &q, r, 100) & inst_imask) == PR655_READBACK_ERROR);
    CHECK(r->changed == primary && r->attempted == primary && r->acknowledged == primary);
    CHECK(r->before.cycles == 1 && r->values.cycles == 2 && r->confirmed == primary);
    CHECK(r->blocked == (primary | cycles) && exchange(c, "SN1") < 0 && exchange(c, "SN2") < 0);
    CHECK(c->engine.state == pr655_quarantined && transport.next == transport.count);
    debt = r->blocked; cm_pr655_control_facts(p); finish(p);

    /* Rebind the actual driver and carry the failed receipt's logical recovery
     * ledger into the fresh simulated host. Cross-worker ledger retention is
     * owned by the application's existing session, not device initialization. */
    p = driver(670); c = (pr655 *)p; c->host.control_blocked = debt;
    q.present = primary; primary_control_steps(42, 2, 2);
    CHECK(cm_pr655_control_apply(p, &q, r, 100) == inst_ok);
    CHECK(!r->changed && !r->attempted && !r->acknowledged && r->confirmed == primary);
    CHECK(r->blocked == (primary | cycles) && cm_pr655_preflight(p, 300, 0) == PR655_BAD_SETUP);
    cm_pr655_control_facts(p);
    q.present |= cycles; primary_control_steps(42, 2, 2);
    add_owned("SN1\r", "0000\r\n");
    primary_setup(42, 2, 1); primary_setup_steps(); primary_control_steps(42, 2, 1);
    CHECK(cm_pr655_control_apply(p, &q, r, 100) == inst_ok);
    CHECK(r->before.cycles == 2 && r->values.cycles == 1 && r->changed == cycles);
    CHECK(r->attempted == cycles && r->acknowledged == cycles && r->confirmed == q.present && !r->blocked);
    CHECK(c->engine.state == pr655_ready && transport.next == transport.count);
    cm_pr655_control_facts(p); finish(p);
    free(r);
}
static void speed_modes(void) {
    int code;
    for (code = 0; code <= 3; code++) {
        inst *p = driver(670);
        pr655 *c = (pr655 *)p;
        cm_pr655_control_report *r = new_report();
        cm_pr655_control_request q = {0};
        char command[8];
        q.present = CM_PR655_BIT(CM_PR655_SPEED); q.speedModeCode = code;
        control_steps();
        snprintf(command, sizeof(command), "SG%d\r", code);
        add_owned(command, "0000\r\n");
        full_setup(1, 2, 1, 0, 75, 0, 0, 8, -1);
        snprintf(numeric_setup, sizeof(numeric_setup),
                 "0000,8,-1,-1,-1,2,1,73,0,%d,1,2,0,0,91,75.0\r\n", 42 + code);
        setup_steps(); control_steps();
        CHECK(cm_pr655_control_apply(p, &q, r, 100) == inst_ok);
        CHECK(r->attempted == q.present && r->acknowledged == q.present && !r->confirmed);
        CHECK(!(r->values.present & q.present) && r->setup.gain == 42 + code);
        CHECK(r->requested.speedModeCode == code && !r->failed && c->engine.state == pr655_ready);
        cm_pr655_control_facts(p); finish(p);
        free(r);
    }
    for (code = 0; code < 2; code++) {
        inst *p = driver(code ? 670 : 655);
        pr655 *c = (pr655 *)p;
        cm_pr655_control_report *r = new_report();
        cm_pr655_control_request q = {0};
        q.present = CM_PR655_BIT(CM_PR655_SPEED); q.speedModeCode = code ? 4 : 0;
        control_steps();
        CHECK((cm_pr655_control_apply(p, &q, r, 100) & inst_imask) == PR655_REFUSED_COMMAND);
        CHECK(!r->attempted && !r->acknowledged && c->engine.state == pr655_ready);
        finish(p);
        free(r);
    }
    {
        inst *p = driver(670);
        pr655 *c = (pr655 *)p;
        cm_pr655_control_report *r = new_report();
        cm_pr655_control_request q = {0};
        q.present = CM_PR655_BIT(CM_PR655_SPEED); q.speedModeCode = 1;
        control_steps(); add_owned("SG1\r", "0000\r\n");
        full_setup(1, 10, 1, 0, 75, 0, 0, 8, -1); setup_steps();
        CHECK((cm_pr655_control_apply(p, &q, r, 100) & inst_imask) == PR655_READBACK_ERROR);
        CHECK(r->acknowledged == q.present && !r->confirmed && c->engine.state == pr655_quarantined);
        finish(p);
        free(r);
    }
}
static void control_recovery(void) {
    inst *p = driver(670);
    pr655 *c = (pr655 *)p;
    cm_pr655_control_report *r = new_report();
    cm_pr655_control_request q = {0};
    q.present = CM_PR655_BIT(CM_PR655_CYCLES) | CM_PR655_BIT(CM_PR655_SPEED);
    q.cycles = 2; q.speedModeCode = 1;
    control_steps(); add_owned("SN2\r", "0000\r\n");
    full_setup(1, 2, 2, 0, 75, 0, 0, 8, -1); setup_steps();
    add_owned("SG1\r", "-35\r\n");
    CHECK(cm_pr655_control_apply(p, &q, r, 100) != inst_ok);
    CHECK(r->before.cycles == 1 && r->attempted == q.present && r->blocked == q.present);
    CHECK(r->acknowledged == CM_PR655_BIT(CM_PR655_CYCLES));
    CHECK(r->raw_exchange_count == c->host.exchange_count && r->setup_before_valid);
    cm_pr655_control_facts(p);
    control_steps();
    CHECK(cm_pr655_control_read(p, r, 100) == inst_ok && r->blocked == q.present);
    CHECK(cm_pr655_preflight(p, 300, 0) == PR655_BAD_SETUP);
    q.present = CM_PR655_BIT(CM_PR655_CYCLES); control_steps();
    CHECK(cm_pr655_control_apply(p, &q, r, 100) == inst_ok && r->blocked);
    q.present |= CM_PR655_BIT(CM_PR655_SPEED); q.speedModeCode = 0;
    control_steps(); add_owned("SG0\r", "0000\r\n"); setup_steps(); control_steps();
    CHECK(cm_pr655_control_apply(p, &q, r, 100) == inst_ok && !r->blocked);
    CHECK(r->acknowledged == CM_PR655_BIT(CM_PR655_SPEED) && r->confirmed == CM_PR655_BIT(CM_PR655_CYCLES));
    cm_pr655_control_facts(p); finish(p);
    free(r);
}
static void initialized_identity_and_adaptive(void) {
    int model;
    for (model = 655; model <= 670; model += 15) {
        inst *p = driver(model);
        pr655 *c = (pr655 *)p;
        cm_pr655_identity id;
        cm_pr655_control_report *r = new_report();
        cm_pr655_control_request q = {0};
        int writes = transport.writes;
        CHECK(cm_pr655_identity_snapshot(p, &id) && id.model == model);
        CHECK(id.grid.count == (model == 655 ? 101 : 201) && transport.writes == writes);
        c->inited = 0;
        CHECK(!cm_pr655_identity_snapshot(p, &id) && id.model == 0 && !id.serial[0]);
        c->inited = 1; c->engine.state = pr655_quarantined;
        CHECK(!cm_pr655_identity_snapshot(p, &id) && transport.writes == writes);
        c->engine.state = pr655_ready;
        full_setup(1, 2, 1, 0, 75, 0, 0, 8, -1);
        control_steps();
        q.present = CM_PR655_BIT(CM_PR655_EXPOSURE); q.exposureMilliseconds = 0;
        CHECK(cm_pr655_control_apply(p, &q, r, 100) == inst_ok);
        CHECK(!r->attempted && r->confirmed == q.present && r->before.exposureMilliseconds == 0);
        CHECK(r->values.exposureMilliseconds == 0 && r->setup.exposure_ms == 0);
        cm_pr655_control_facts(p); finish(p);
        /* The manual explicitly says D601/D602 report zero in Adaptive mode.
         * A positive numeric exposure paired with Adaptive text is refused. */
        p = driver(model); c = (pr655 *)p;
        full_setup(1, 2, 1, 0, 75, 0, 0, 8, -1);
        snprintf(numeric_setup, sizeof(numeric_setup), "0000,8,-1,-1,-1,2,1,73,125,87,1,2,0,0,91,75.0\r\n");
        control_steps();
        CHECK((cm_pr655_control_apply(p, &q, r, 100) & inst_imask) == PR655_BAD_SETUP);
        CHECK(!r->attempted && !r->setup_before_valid && c->engine.state == pr655_quarantined);
        cm_pr655_control_facts(p); finish(p);
        free(r);
    }
}
static void retained_mapping_refusals(void) {
    int model, scenario;
    for (model = 655; model <= 670; model += 15) for (scenario = 0; scenario < 2; scenario++) {
        inst *p = driver(model);
        pr655 *c = (pr655 *)p;
        ipatch out;
        full_setup(1, scenario ? 2 : 10, 1, 0, 75, 0, 0, 8, -1); setup_steps();
        capture_steps(model, 1, 1, 0, 0, 1e-4, 1, 1, scenario ? "D2" : NULL);
        if (!scenario) setup_steps();
        CHECK(p->set_mode(p, inst_mode_emis_tele | inst_mode_spectral) == inst_ok);
        CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok);
        CHECK(!out.XYZ_v && !out.sp.spec_n && !c->capture.valid && !c->capture.geometry_valid);
        CHECK(c->capture.native.spectrum_parsed && c->capture.native.count == (model == 655 ? 101 : 201));
        CHECK(c->capture.native.xyz_parsed == !scenario);
        cm_pr655_native_facts(p); finish(p);
    }
}
static void closed_control_fields(void) {
    cm_control_fields fields;
    cm_pr655_control_request request = {0};
    const char *invalid[] = {"primaryAccessoryId=9007199254740990.5", "apertureId=9007199254740992",
        "apertureId=9223372036854775808", "cycles=1.99999999999999999", "cycles=2147483648"};
    inst *p = driver(670);
    int writes = transport.writes, i;
    CHECK(cm_control_fields_parse("speedModeCode=0,cycles=2", &fields) && fields.count == 2);
    CHECK(!cm_control_fields_parse("speedModeCode=true", &fields));
    CHECK(!cm_control_fields_parse("speedModeCode=1,speedModeCode=2", &fields));
    CHECK(!cm_control_fields_parse("cycles=0x2", &fields));
    CHECK(!cm_control_fields_parse("cycles=2,", &fields));
    for (i = 0; i < (int)(sizeof(invalid) / sizeof(invalid[0])); i++) {
        CHECK(cm_control_fields_parse(invalid[i], &fields));
        CHECK(!cm_control_decode(&fields, cm_pr_control_fields, CM_PR655_FIELDS, &request, &request.present));
    }
    {
        long legacy = 0;
        unsigned int present = 0;
        const cm_control_field field = {"value", 0, CM_CONTROL_LONG, 0};
        CHECK(cm_control_fields_parse("value=9007199254740990.5", &fields));
        CHECK(!cm_control_decode(&fields, &field, 1, &legacy, &present));
    }
    CHECK(cm_control_fields_parse("arbitraryCommand=1", &fields));
    cm_pr655_read_allowance(p, 100);
    CHECK(cm_native_controls_run(p, "apply", &fields) == inst_bad_parameter && transport.writes == writes);
    finish(p);
}
static void capture_intent_and_optical_context(void) {
    int model, scenario;
    for (model = 655; model <= 670; model += 15) {
        inst *p = driver(model);
        pr655 *c = (pr655 *)p;
        cm_pr655_control_request q = {0};
        cm_pr655_control_report *r = new_report();
        ipatch out;
        CHECK(p->get_set_opt(p, inst_opt_set_averages, 2) == inst_ok);
        CHECK(p->set_refr_rate(p, 60) == inst_ok);
        q.present = CM_PR655_BIT(CM_PR655_CYCLES) | CM_PR655_BIT(CM_PR655_SYNC)
                  | CM_PR655_BIT(CM_PR655_FREQUENCY);
        q.cycles = 3; q.syncMode = 3; q.syncFrequencyHz = 120;
        control_steps();
        add_owned("SN3\r", "0000\r\n");
        full_setup(1, 2, 3, 0, 75, 0, 0, 8, -1); setup_steps();
        add_owned("SK120\r", "0000\r\n");
        full_setup(1, 2, 3, 0, 120, 0, 0, 8, -1); setup_steps();
        add_owned("SS3\r", "0000\r\n");
        full_setup(1, 2, 3, 3, 120, 0, 0, 8, -1); setup_steps(); control_steps();
        CHECK(cm_pr655_control_apply(p, &q, r, 200) == inst_ok && r->confirmed == q.present);
        CHECK(!c->averages && !c->refresh_hz && c->host.explicit_sync);
        cm_pr655_control_facts(p);
        setup_steps(); capture_steps(model, 1, 1, 0, 0, 1e-4, 1, 1, NULL); setup_steps();
        cm_pr655_read_allowance(p, 400);
        CHECK(p->set_mode(p, inst_mode_emis_tele | inst_mode_spectral) == inst_ok);
        CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
        CHECK(c->capture.before.cycles == 3 && c->capture.before.sync_mode == 3
           && c->capture.before.frequency_hz == 120);
        CHECK(exchange(c, "SN2") < 0 && exchange(c, "SK60") < 0 && exchange(c, "SS1") < 0);
        cm_pr655_native_facts(p);
        /* A later ordinary refresh request is new intent and must take effect. */
        CHECK(p->set_refr_rate(p, 60) == inst_ok && !c->host.explicit_sync);
        setup_steps(); add_owned("SK60\r", "0000\r\n");
        full_setup(1, 2, 3, 3, 60, 0, 0, 8, -1);
        add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
        capture_steps(model, 1, 1, 0, 0, 1e-4, 1, 1, NULL); setup_steps();
        CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
        CHECK(c->capture.before.frequency_hz == 60 && exchange(c, "SK60") >= 0);
        cm_pr655_native_facts(p); finish(p);

        p = driver(model); c = (pr655 *)p;
        CHECK(p->set_mode(p, inst_mode_emis_tele | inst_mode_emis_norefresh_ovd) == inst_ok);
        control_steps();
        CHECK((cm_pr655_control_apply(p, &q, r, 200) & inst_imask) == PR655_REFUSED_COMMAND);
        CHECK(!r->attempted && !c->host.explicit_sync);
        cm_pr655_control_facts(p); finish(p);

        p = driver_with_setup(model, 125, 0, 1); c = (pr655 *)p;
        CHECK(p->set_mode(p, inst_mode_emis_tele | inst_mode_emis_nonadaptive) == inst_ok);
        q.present = CM_PR655_BIT(CM_PR655_EXPOSURE); q.exposureMilliseconds = 0;
        control_steps();
        CHECK((cm_pr655_control_apply(p, &q, r, 200) & inst_imask) == PR655_REFUSED_COMMAND);
        CHECK(!r->attempted && exchange(c, "SE0") < 0 && c->engine.state == pr655_ready);
        cm_pr655_control_facts(p); finish(p);

        /* Same IDs cannot conceal a changed declaration of optical dimensions.
         * Both refusal directions retain the full native observation. */
        for (scenario = 0; scenario < 2; scenario++) {
            p = driver(model); c = (pr655 *)p;
            add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
            add_owned("D112\r", "0000,1,3\r\n");
            add_owned("D116\r", scenario
                ? "0000,8,Test lens,Primary,Luminance,Radiance\r\n"
                : "0000,8,Test lens,Primary,Illuminance,Irradiance\r\n");
            capture_steps(model, 1, 1, 0, 0, 1e-4, 1, 1, NULL);
            add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
            add_owned("D112\r", "0000,1,3\r\n");
            add_owned("D116\r", scenario
                ? "0000,8,Test lens,Primary,Illuminance,Irradiance\r\n"
                : "0000,8,Test lens,Primary,Luminance,Radiance\r\n");
            CHECK(p->set_mode(p, inst_mode_emis_tele | inst_mode_spectral) == inst_ok);
            CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == PR655_GEOMETRY_ERROR);
            CHECK(c->capture.native.spectrum_parsed && c->capture.native.xyz_parsed);
            CHECK(!out.XYZ_v && !out.sp.spec_n && !c->capture.geometry_valid
               && !c->capture.xyz_publication_valid);
            cm_pr655_native_facts(p); finish(p);
        }
        free(r);
    }
}
static void optional_inventory_steps(int kind) {
    add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
    add_owned("D112\r", kind == 0 ? "-1000\r\n" : kind == 3 ? "0000,0,0\r\n" : "0000,1,3\r\n");
    if (kind == 0 || kind == 3) return;
    add_owned("D116\r", kind == 1 ? "-1035\r\n" : "0000,8,Test lens,Primary,Luminance,Radiance\r\n");
}
static void optional_inventory_controls(void) {
    int model, kind;
    for (model = 655; model <= 670; model += 15) for (kind = 0; kind < 4; kind++) {
        inst *meter = driver(model);
        pr655 *p = (pr655 *)meter;
        cm_pr655_control_report *report = new_report();
        cm_pr655_control_request request = {0};
        ipatch out;
        optional_inventory_steps(kind);
        if (kind == 2) add_owned("D117\r", "-2000\r\n");
        else if (kind == 1) apertures();
        CHECK(cm_pr655_control_read(meter, report, 30) == inst_ok);
        CHECK(report->setup_valid && report->inventory_counts_valid == (kind != 0));
        CHECK(report->accessory_list_valid == (kind >= 2));
        CHECK(report->aperture_list_valid == (kind == 1 || kind == 3));
        CHECK(report->reported_accessory_count == (kind == 0 || kind == 3 ? 0 : 1));
        CHECK(report->aperture_count == (kind == 0 || kind == 3 ? 0 : 3));
        CHECK(report->inventory_counts_error == (kind == 0 ? -1000 : 0));
        CHECK(report->accessory_list_error == (kind == 1 ? -1035 : 0));
        CHECK(report->aperture_list_error == (kind == 2 ? -2000 : 0));
        cm_pr655_control_facts(meter);
        if (kind < 2) {
            optional_inventory_steps(kind);
            capture_steps(model, 1, 1, 0, 0, 1e-4, 1, 1, NULL);
            optional_inventory_steps(kind);
            CHECK(meter->set_mode(meter, inst_mode_emis_tele | inst_mode_spectral) == inst_ok);
            CHECK((meter->read_sample(meter, "", &out, instNoClamp) & inst_imask) == PR655_GEOMETRY_ERROR);
            CHECK(p->capture.native.spectrum_parsed && p->capture.native.xyz_parsed && !p->capture.valid);
            CHECK(p->engine.state == pr655_ready && !out.XYZ_v && !out.sp.spec_n);
            cm_pr655_native_facts(meter);
        } else if (kind == 2 && model == 670) {
            request.present = CM_PR655_BIT(CM_PR655_APERTURE); request.apertureId = 9;
            optional_inventory_steps(kind); add_owned("D117\r", "-2000\r\n");
            CHECK((cm_pr655_control_apply(meter, &request, report, 100) & inst_imask) == PR655_REFUSED_COMMAND);
            CHECK(!report->attempted && exchange(p, "SF9") < 0 && p->engine.state == pr655_ready);
            request.present = CM_PR655_BIT(CM_PR655_CYCLES); request.cycles = 3;
            optional_inventory_steps(kind); add_owned("D117\r", "-2000\r\n");
            add_owned("SN3\r", "0000\r\n"); full_setup(1, 2, 3, 0, 75, 0, 0, 8, -1); setup_steps();
            optional_inventory_steps(kind); add_owned("D117\r", "-2000\r\n");
            CHECK(cm_pr655_control_apply(meter, &request, report, 100) == inst_ok);
            CHECK(report->confirmed == request.present && report->aperture_list_error == -2000 && !report->aperture_list_valid);
            cm_pr655_control_facts(meter);
        }
        finish(meter);
        free(report);
    }
}
static void final_inventory_facts(void) {
    int scenario;
    for (scenario = 0; scenario < 3; scenario++) {
        inst *meter = driver(670);
        pr655 *p = (pr655 *)meter;
        cm_pr655_control_report *report = new_report();
        cm_pr655_control_request request = {0};
        request.present = CM_PR655_BIT(CM_PR655_CYCLES); request.cycles = 2;
        control_steps(); add_owned("SN2\r", "0000\r\n");
        full_setup(1, 2, 2, 0, 75, 0, 0, 8, -1); setup_steps(); setup_steps();
        add_owned("D117\r", scenario == 1 ? "-2000\r\n" : scenario == 2 ? "0000,2,Half degree,nan\r\n"
            : "0000,2,Half degree,0\r\n0000,99,Changed aperture,0\r\n0000,15,Eighth degree,0\r\n");
        CHECK((cm_pr655_control_apply(meter, &request, report, 100) & inst_imask)
            == (scenario == 2 ? PR655_BAD_SETUP : PR655_READBACK_ERROR));
        CHECK(report->aperture_count == 3 && p->engine.state == pr655_quarantined);
        CHECK(report->aperture_list_valid == !scenario);
        CHECK(report->aperture_list_error == (scenario == 1 ? -2000 : 0));
        if (scenario) CHECK(!report->apertures && !report->aperture_storage_count);
        else CHECK(report->apertures[1].id == 99 && !strcmp(report->apertures[1].name, "Changed aperture"));
        CHECK(report->blocked == request.present && report->attempted == request.present);
        if (scenario == 2) CHECK(exchange(p, "D117") >= 0
            && p->host.exchanges[p->host.exchange_count - 1].error == PR655_BAD_SETUP
            && p->host.exchanges[p->host.exchange_count - 1].raw.length > 0);
        cm_pr655_control_facts(meter); finish(meter);
        free(report);
    }
}
static void resource_envelope(void) {
    int model, i;
    for (model = 655; model <= 670; model += 15) {
        inst *meter = driver(model);
        pr655 *p = (pr655 *)meter;
        cm_pr655_control_report *report = new_report();
        cm_control_fields fields;
        cm_pr655_control_request request = {0};
        char *accessories = inventory_reply(PR655_MAX_ACCESSORIES);
        char *aperture_reply = malloc(PR655_MAX_RESPONSE + 1);
        int used = 0;
        if (!aperture_reply) abort();
        for (i = 0; i < CM_PR655_MAX_APERTURES; i++) {
            const char *suffix = ",0\r\n";
            int prefix = snprintf(aperture_reply + used, PR655_MAX_RESPONSE + 1 - used, "0000,%d,", i);
            int name_n = (i == 0 ? PR655_MAX_LINE + 2 : 1000) - prefix - (int)strlen(suffix);
            used += prefix; memset(aperture_reply + used, '"', name_n); used += name_n;
            strcpy(aperture_reply + used, suffix); used += (int)strlen(suffix);
        }
        add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
        add_owned("D112\r", "0000,1024,1024\r\n");
        add_owned("D116\r", accessories); add_owned("D117\r", aperture_reply);
        free(accessories); free(aperture_reply);
        CHECK(cm_pr655_control_read(meter, report, 30) == inst_ok);
        CHECK(report->accessory_count == 1024 && report->aperture_count == 1024 && report->aperture_list_valid);
        CHECK(report->accessories != p->engine.accessories && report->accessories[0].name != p->engine.accessories[0].name);
        CHECK(strlen(report->accessories[0].name) > 4000 && strlen(report->apertures[0].name) > 4000);
        CHECK(p->host.trace_bytes > 2 * 32768 && !p->host.overflow);
        cm_pr655_control_facts(meter);
        CHECK(cm_control_fields_parse("primaryAccessoryId=9007199254740991,apertureId=4294967296", &fields));
        CHECK(cm_control_decode(&fields, cm_pr_control_fields, CM_PR655_FIELDS, &request, &request.present));
        CHECK(request.primaryAccessoryId == 9007199254740991LL && request.apertureId == 4294967296LL);
        finish(meter);
        free(report);
    }
    {
        inst *meter = driver(670);
        pr655 *p = (pr655 *)meter;
        pr655_raw raw = {0};
        raw.bytes = malloc(PR655_MAX_RESPONSE + 1);
        if (!raw.bytes) abort();
        memset(raw.bytes, 'A', PR655_MAX_RESPONSE); raw.bytes[PR655_MAX_RESPONSE] = 0;
        raw.length = PR655_MAX_RESPONSE; raw.capacity = PR655_MAX_RESPONSE + 1;
        p->host.tracing = 1;
        for (i = 0; i < 4; i++) CHECK(cm_pr655_observe(p, "D116", &raw, 0, 1) == PR655_OK);
        CHECK(p->host.trace_bytes == CM_PR655_MAX_TRACE_BYTES && p->host.exchange_count == 4);
        raw.bytes[0] = 'B'; CHECK(p->host.exchanges[0].raw.bytes[0] == 'A');
        CHECK(cm_pr655_observe(p, "D116", &raw, 0, 1) == CM_PR655_TRACE_LIMIT && p->host.overflow);
        cm_pr655_capture_reset(meter); CHECK(!p->host.exchange_count && !p->host.trace_bytes);
        pr655_raw_dispose(&raw); p->host.tracing = 1;
        for (i = 0; i < CM_PR655_MAX_EXCHANGES; i++) CHECK(cm_pr655_observe(p, "D116", &raw, 0, 1) == PR655_OK);
        CHECK(cm_pr655_observe(p, "D116", &raw, 0, 1) == CM_PR655_TRACE_LIMIT && p->host.overflow);
        finish(meter);
    }
}
/* A full host trace refuses the operation it cannot retain. The exchange that
 * filled it completed cleanly, so the link stays ready, B100 still follows the
 * exposure and destruction still sends Q. */
static int traced_exchanges;
static const char *trace_full_command;
static int trace_fills(void *context, const char *command, const pr655_raw *raw, int error,
                       int complete) {
    if (trace_full_command ? strcmp(trace_full_command, command) != 0 : traced_exchanges-- > 0)
        return cm_pr655_observe(context, command, raw, error, complete);
    ((pr655 *)context)->host.overflow = 1;
    return CM_PR655_TRACE_LIMIT;
}
static void trace_limit_keeps_link(void) {
    inst *p = driver(655);
    pr655 *c = (pr655 *)p;
    cm_pr655_control_report *r = new_report();
    ipatch out;
    inst_code rv;
    c->engine.host_observe = trace_fills;
    setup_steps(); capture_steps(655, 0, 1, 0, 0, 1e-4, 1, 1, NULL);
    cm_pr655_read_allowance(p, 400);
    CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_colorimeter) == inst_ok);
    traced_exchanges = 5; /* D601, D602, D112, D116 and B00; M2 fills the trace */
    rv = p->read_sample(p, "", &out, instNoClamp);
    CHECK((rv & inst_imask) == CM_PR655_TRACE_LIMIT && !out.XYZ_v && c->host.overflow);
    CHECK(c->capture.native.xyz_complete && c->capture.native.xyz_parsed);
    CHECK(fabs(c->capture.native.xyz[0] - 1.25 * constructed_y(655, 1e-4)) < 1e-12);
    CHECK(fabs(c->capture.native.xyz[1] - constructed_y(655, 1e-4)) < 1e-12);
    CHECK(fabs(c->capture.native.xyz[2] + 0.125 * constructed_y(655, 1e-4)) < 1e-12);
    CHECK(c->engine.state == pr655_ready && strstr(transport.wire, "M2\rB100\r") != NULL);
    add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
    add_owned("D112\r", "0000,1,3\r\n");
    traced_exchanges = 2; /* D112 fills the trace; no D116 or D117 follows */
    CHECK(cm_pr655_control_read(p, r, 30) != inst_ok && c->engine.state == pr655_ready);
    finish(p);
    CHECK(transport.wire[strlen(transport.wire) - 1] == 'Q');
    free(r);
}
static void trace_limit_keeps_complete_spectrum(void) {
    int model;
    for (model = 655; model <= 670; model += 15) {
        inst *p = driver(model);
        pr655 *c = (pr655 *)p;
        ipatch out;
        int i, count = model == 655 ? 101 : 201;
        inst_code rv;
        setup_steps(); capture_steps(model, 1, 1, 0, 0, 1e-4, 1, 1, NULL);
        /* Trace exhaustion after M5 stops before D2; B100 still follows. */
        for (i = 0; i < transport.count; i++)
            if (!strcmp(transport.steps[i].command, "D2\r")) break;
        if (i + 2 != transport.count) abort();
        transport.steps[i] = transport.steps[i + 1]; transport.count--;
        c->engine.host_observe = trace_fills; trace_full_command = "M5";
        cm_pr655_read_allowance(p, 400);
        CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
        rv = p->read_sample(p, "", &out, instNoClamp);
        CHECK((rv & inst_imask) == CM_PR655_TRACE_LIMIT && !out.XYZ_v && !out.sp.spec_n);
        CHECK(c->host.overflow && !c->capture.valid && c->engine.state == pr655_ready);
        CHECK(c->capture.native.spectrum_complete && c->capture.native.spectrum_parsed);
        CHECK(c->capture.native.count == count && !c->capture.native.xyz_parsed);
        CHECK(c->capture.native.spectrum[0] == 1e-4 && c->capture.native.spectrum[count - 1] == 1e-4);
        CHECK(c->capture.native.spectrum_raw.length > 0);
        CHECK(strstr(transport.wire, "M5\rB100\r") != NULL && strstr(transport.wire, "D2\r") == NULL);
        finish(p); trace_full_command = NULL;
        CHECK(transport.wire[strlen(transport.wire) - 1] == 'Q');
    }
}
static void trace_overflow_error_precedence(void) {
    int scenario, i;
    for (scenario = 0; scenario < 6; scenario++) {
        inst *p = driver(scenario & 1 ? 670 : 655);
        pr655 *c = (pr655 *)p;
        cm_pr655_control_report *r = new_report();
        ipatch out;
        inst_code rv;
        c->engine.host_observe = trace_fills;
        if (scenario < 2) {
            trace_full_command = scenario ? "D2" : "M5";
            setup_steps();
            if (!scenario) {
                add_owned("B00\r", "Backlight set to 0 %\r\n");
                add_owned("M5\r", "-8\r\n");
                add_owned("B100\r", "Backlight set to 100 %\r\n");
            } else {
                capture_steps(670, 1, 1, 0, 0, 1e-4, 1, 1, NULL);
                for (i = 0; i < transport.count; i++)
                    if (!strcmp(transport.steps[i].command, "D2\r")) transport.steps[i].reply = "-8\r\n";
            }
            cm_pr655_read_allowance(p, 400);
            CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
            rv = p->read_sample(p, "", &out, instNoClamp);
            CHECK((rv & inst_imask) == PR655_NATIVE_ERROR_BASE + 8 && !out.XYZ_v);
            CHECK(c->host.overflow && c->engine.state == pr655_ready);
            CHECK(strstr(transport.wire, "B100\r") != NULL);
        } else if (scenario < 4) {
            trace_full_command = scenario == 2 ? "D112" : "D117";
            if (scenario == 2) {
                add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
                add_owned("D112\r", "-1000\r\n");
            } else { setup_steps(); add_owned("D117\r", "-1000\r\n"); }
            rv = cm_pr655_control_read(p, r, 30);
            CHECK(rv != inst_ok && r->failed && c->host.overflow);
            CHECK(c->engine.state == pr655_ready);
        } else {
            trace_full_command = scenario == 4 ? "D13" : "D14";
            setup_steps(); capture_steps(scenario & 1 ? 670 : 655, 0, 1, 0, 0, 1e-4, 1, 1, NULL); setup_steps();
            add_owned("D13\r", scenario == 4 ? "-1000\r\n" : "0000,Gain,100 msec\r\n");
            if (scenario == 5) add_owned("D14\r", "-35\r\n");
            cm_pr655_read_allowance(p, 400); cm_pr655_capture_metadata(p, 1);
            CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_colorimeter) == inst_ok);
            rv = p->read_sample(p, "", &out, instNoClamp);
            CHECK((rv & inst_imask) == CM_PR655_TRACE_LIMIT && !out.XYZ_v);
            CHECK(c->host.overflow && c->capture.native.xyz_parsed && c->engine.state == pr655_ready);
        }
        finish(p);
        CHECK(transport.wire[strlen(transport.wire) - 1] == 'Q');
        trace_full_command = NULL;
        free(r);
    }
}
/* An optional D112 refusal cannot hide a full trace from the next setter. The
 * readback after SN3 refuses D112 and its copy fails, so SS1 is never sent. */
static void trace_limit_stops_next_setter(void) {
    inst *p = driver(670);
    pr655 *c = (pr655 *)p;
    cm_pr655_control_report *r = new_report();
    cm_pr655_control_request q = {0};
    inst_code rv;
    c->engine.host_observe = trace_fills;
    optional_inventory_steps(0);
    add_owned("SN3\r", "0000\r\n"); full_setup(1, 2, 3, 0, 75, 0, 0, 8, -1);
    optional_inventory_steps(0);
    q.present = CM_PR655_BIT(CM_PR655_CYCLES) | CM_PR655_BIT(CM_PR655_SYNC);
    q.cycles = 3; q.syncMode = 1;
    traced_exchanges = 6; /* D601, D602, D112, SN3, D601 and D602; D112 fills it */
    rv = cm_pr655_control_apply(p, &q, r, 100);
    CHECK((rv & inst_imask) == CM_PR655_TRACE_LIMIT && r->failed && c->host.overflow);
    CHECK(r->attempted == CM_PR655_BIT(CM_PR655_CYCLES) && c->engine.state == pr655_ready);
    CHECK(strstr(transport.wire, "SS1\r") == NULL);
    finish(p);
    CHECK(transport.wire[strlen(transport.wire) - 1] == 'Q');
    free(r);
}
static int exchange_step(const char *command) {
    int i;
    for (i = transport.count - 1; i >= 0; i--)
        if (!strcmp(transport.steps[i].command, command)) return i;
    abort();
}
/* Input arriving between exchanges belongs to the next request's record: a
 * late blank line leads D112's complete record, and a stale-input refusal
 * records D112 with the bytes that caused it and sends no D112 byte. A second
 * line read with D112's own reply stays in D112's record and stops D116. */
static void input_before_request_is_traced(void) {
    static const char *late[] = {"\r\n", "ZZ\x80\r\n", NULL};
    static const char *expected[] = {"\r\n0000,0,0\r\n", "ZZ\x80\r\n", "0000,1,3\r\nJUNK\r\n"};
    int scenario;
    for (scenario = 0; scenario < 3; scenario++) {
        inst *p = driver(670);
        pr655 *c = (pr655 *)p;
        cm_pr655_control_report *r = new_report();
        int d112, sent, length = (int)strlen(expected[scenario]);
        inst_code rv;
        add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
        add_owned("D112\r", scenario == 2 ? "0000,1,3\r\nJUNK\r\n" : "0000,0,0\r\n");
        if (scenario == 2) {
            add_owned("D116\r", "0000,8,Test lens,Primary,Luminance,Radiance\r\n");
            transport.fragment = 4096; /* both D112 lines arrive in one read */
        }
        transport.late = late[scenario];
        transport.late_step = exchange_step("D112\r");
        transport.late_skip = 1; /* D602's own surplus check reads nothing */
        sent = transport.wire_bytes;
        rv = cm_pr655_control_read(p, r, 30);
        d112 = exchange(c, "D112");
        CHECK(transport.late == NULL && c->host.exchange_count == 3 && d112 == 2);
        CHECK(d112 == 2 && c->host.exchanges[d112].complete == !scenario
           && c->host.exchanges[d112].error == (scenario ? PR655_STALE_INPUT : 0)
           && c->host.exchanges[d112].raw.length == length
           && !memcmp(c->host.exchanges[d112].raw.bytes, expected[scenario], length));
        if (scenario) CHECK((rv & inst_imask) == PR655_STALE_INPUT && c->engine.state == pr655_quarantined
                         && !strstr(transport.wire + sent, scenario == 1 ? "D112" : "D116"));
        else CHECK(rv == inst_ok && c->engine.state == pr655_ready && r->setup_valid);
        finish(p);
        free(r);
    }
}
static void ambient_publication_facts(void) {
    int model, units;
    for (model = 655; model <= 670; model += 15) for (units = 0; units <= 1; units++) {
        inst *p = driver(model);
        pr655 *c = (pr655 *)p;
        ipatch out;
        FILE *saved = wire;
        char facts[8192] = {0};
        full_setup(units, 2, 1, 0, 75, units ? 0 : 200, 0, 8, -1); ambient_setup_steps();
        ambient_capture_steps(model, 1, units, 1e-4, 1, 1); ambient_setup_steps();
        cm_pr655_read_allowance(p, 400);
        CHECK(pr655_required_floor_s(p->dtype, inst_mode_emis_ambient | inst_mode_spectral) == 310);
        CHECK(p->set_mode(p, inst_mode_emis_ambient | inst_mode_spectral) == inst_ok);
        CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.mtype == inst_mrt_ambient);
        CHECK(c->capture.native_to_published_xyz_scale == c->capture.unit.xyz_scale);
        CHECK(fabs(out.XYZ[1] - constructed_y(model, 1e-4)) < 1e-11);
        wire = tmpfile(); if (!wire) abort();
        cm_pr655_native_facts(p); rewind(wire);
        CHECK(fgets(facts, sizeof(facts), wire) != NULL && strchr(facts, '\n') != NULL);
        CHECK(strstr(facts, "\"unit\":\"lux\"") != NULL);
        CHECK(strstr(facts, "\"inferred_unit\":\"W/(m^2.nm)\"") != NULL);
        CHECK(strstr(facts, "\"published_unit\":\"mW/(m^2.nm)\"") != NULL);
        CHECK(strstr(facts, units ? "\"xyz_scale\":\"lux\"" : "\"xyz_scale\":\"foot-candle\"") != NULL);
        CHECK(strstr(facts, "\"xyz_units\":112,\"spectrum_units\":12") != NULL);
        CHECK(strstr(facts, "\"unit_basis\":\"manual M5 integral and irradiance definition") != NULL);
        fclose(wire); wire = saved; finish(p);
    }
}
/* A refused body whose M5 units code contradicts the geometry keeps its bins
 * but states no unit basis: radiance code 11 under ambient optics, irradiance
 * code 12 under radiance optics. */
static void contradicting_units_state_no_basis(void) {
    int ambient;
    for (ambient = 0; ambient <= 1; ambient++) {
        inst *p = driver(655);
        pr655 *c = (pr655 *)p;
        ipatch out;
        FILE *saved = wire;
        char facts[16384] = {0};
        int i;
        full_setup(1, 2, 1, 0, 75, 0, 0, 8, -1);
        if (ambient) { ambient_setup_steps(); ambient_capture_steps(655, 1, 1, 1e-4, 1, 1); }
        else { setup_steps(); capture_steps(655, 1, 1, 12, 0, 1e-4, 1, 1, NULL); }
        for (i = 0; ambient && i < transport.count; i++)
            if (!strcmp(transport.steps[i].command, "M5\r")) {
                char *code = strchr((char *)transport.steps[i].reply, ',');
                if (!code || strncmp(code, ",12,", 4)) abort();
                code[2] = '1';
            }
        if (ambient) ambient_setup_steps(); else setup_steps();
        cm_pr655_read_allowance(p, 400);
        CHECK(p->set_mode(p, (ambient ? inst_mode_emis_ambient : inst_mode_emis_tele)
                             | inst_mode_spectral) == inst_ok);
        CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == PR655_GEOMETRY_ERROR);
        CHECK(c->capture.native.spectrum_parsed && !out.sp.spec_n);
        CHECK(c->capture.native.spectrum_units == (ambient ? 11 : 12));
        wire = tmpfile(); if (!wire) abort();
        cm_pr655_native_facts(p); rewind(wire);
        CHECK(fgets(facts, sizeof(facts), wire) != NULL && strchr(facts, '\n') != NULL);
        CHECK(strstr(facts, "\"unit_basis\":null") != NULL);
        CHECK(strstr(facts, "definition; per-capture") == NULL);
        fclose(wire); wire = saved; finish(p);
    }
}
int main(void) {
    const char *output = getenv("PR655_FACTS_OUTPUT");
    wire = output ? fopen(output, "w") : tmpfile();
    if (!wire) { perror("PR host-test facts output"); return 2; }
    normal_capture(); allowance_refusal(); invalid_allowance(); metadata_checks(); controls_read_and_validate();
    exposure_order(); ack_and_collateral(); raw_and_unverified(); model_and_sync_checks(); addon_swaps();
    primary_aperture_coupling(); primary_aperture_refusals(); primary_aperture_preflight();
    confirmed_drift_recovery();
    speed_modes(); control_recovery(); initialized_identity_and_adaptive(); retained_mapping_refusals(); closed_control_fields();
    capture_intent_and_optical_context();
    resource_envelope();
    optional_inventory_controls();
    final_inventory_facts();
    trace_limit_keeps_link();
    trace_limit_keeps_complete_spectrum();
    trace_overflow_error_precedence();
    trace_limit_stops_next_setter();
    input_before_request_is_traced();
    ambient_publication_facts();
    contradicting_units_state_no_basis();
    fclose(wire);
    printf("pr655_host_test: %d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
