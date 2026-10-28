/* Typed CR operator controls over the existing scripted contribution instrument.
 * Copyright 2026 Fernando Voltolini de Azambuja.
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Extends only the fixture's application-only setter answers. Production framing,
 * inventories, timing, transport and inst methods remain the real implementations.
 */
#ifdef main
#undef main
#endif
#define main cm_cr250_host_unrun_main
#include "cr250_host_test.c"
#undef main
#include "cr250_control.h"
#include "cr250_support.h"
#include "native_facts.h"
/* This actor exercises the CR decoder only; PR retains its separate actor. */
#undef CM_PR_ADAPTER
#include "native_controls.h"

static int (*base_control_write)(icoms *, char *, int, double);
static char explicit_exposure[64], explicit_range[32], explicit_speed[32];
static char explicit_accessory[32], explicit_ceiling[64], explicit_filters[3][32];
static char filter_value[128];
static const char *control_refusal, *control_refusal_status;
static int speed_drift_after_multiplier;


/* Existing contribution runner's count over the same fake's sent commands. */
static int sm_sent(void) {
    const char *s = F.sent;
    int n = 0;
    while ((s = strstr(s, "\nSM ")) != NULL) { n++; s += 4; }
    return n;
}

/* This fixture supplies the new operator-only facts through the same fake's
 * existing scripted-response seam. It does not parse or synthesize production
 * reply framing; cr250_fake.c still owns that framing and chunked transport. */
static int control_write(icoms *icom, char *bytes, int length, double timeout) {
    char command[128];
    char setter_reply[192];
    const char *reply = NULL;
    int result, extra_setter = 0;
    size_t n = length > 0 ? (size_t)length : strlen(bytes);
    if (n >= sizeof(command)) abort();
    memcpy(command, bytes, n); command[n] = 0;
    while (n && (command[n - 1] == '\r' || command[n - 1] == '\n')) command[--n] = 0;
    F.native_cmd = NULL; F.native_status = NULL; F.override3_cmd = NULL;
    if (speed_drift_after_multiplier && !strcmp(command, "SM ExposureX 2")) {
        strcpy(explicit_speed, "Fast"); speed_drift_after_multiplier = 0;
    }
    if (!strcmp(command, "SM Exposure 50")) { strcpy(explicit_exposure, "50.000 msec"); extra_setter = 1; }
    else if (!strcmp(command, "SM Exposure 50.1")) { strcpy(explicit_exposure, "50.100 msec"); extra_setter = 1; }
    else if (!strcmp(command, "SM RangeMode 1")) { cr250_fake_set_setting("RangeMode", "Fixed"); extra_setter = 1; }
    else if (!strcmp(command, "SM Range 3")) { strcpy(explicit_range, "D"); extra_setter = 1; }
    else if (!strcmp(command, "SM Speed 2")) { strcpy(explicit_speed, "Fast"); extra_setter = 1; }
    else if (!strcmp(command, "SM Speed 0")) { strcpy(explicit_speed, "Slow"); extra_setter = 1; }
    else if (!strcmp(command, "SM Accessory 20")) { strcpy(explicit_accessory, "Lens"); extra_setter = 1; }
    else if (!strcmp(command, "SM MaxAutoExposure 200")) { strcpy(explicit_ceiling, "200.000 msec"); extra_setter = 1; }
    else if (!strcmp(command, "SM Filter1 3")) { strcpy(explicit_filters[0], "ND-100-1"); extra_setter = 1; }
    else if (!strcmp(command, "SM Filter2 4")) { strcpy(explicit_filters[1], "ND-100-2"); extra_setter = 1; }
    else if (!strcmp(command, "SM Filter3 5")) { strcpy(explicit_filters[2], "ND-100-3"); extra_setter = 1; }
    if (extra_setter) {
        snprintf(setter_reply, sizeof(setter_reply), "OK:0:%s:No errors", command);
        F.native_cmd = command; F.native_status = setter_reply;
    }
    if (control_refusal && !strcmp(command, control_refusal)) {
        F.native_cmd = command; F.native_status = control_refusal_status;
    }
    if (!strcmp(command, "RC Accessory")) reply = "2\r\n0,Standard,Radiance\r\n20,Lens,Radiance";
    else if ((!strcmp(command, "RS Accessory") || !strcmp(command, "RM Accessory")) && explicit_accessory[0]) reply = explicit_accessory;
    else if ((!strcmp(command, "RS Exposure") || !strcmp(command, "RM Exposure")) && explicit_exposure[0]) reply = explicit_exposure;
    else if ((!strcmp(command, "RS Range") || !strcmp(command, "RM Range")) && explicit_range[0]) reply = explicit_range;
    else if ((!strcmp(command, "RS Speed") || !strcmp(command, "RM Speed")) && explicit_speed[0]) reply = explicit_speed;
    else if (!strcmp(command, "RM MaxAutoExposure") && explicit_ceiling[0]) reply = explicit_ceiling;
    else if (!strcmp(command, "RS Filter")) {
        snprintf(filter_value, sizeof(filter_value), "%s,%s,%s", explicit_filters[0], explicit_filters[1], explicit_filters[2]);
        reply = filter_value;
    } else if (!strcmp(command, "RM Filter")) {
        int i, used = 0;
        filter_value[0] = 0;
        for (i = 0; i < 3; i++) if (strcmp(explicit_filters[i], "None")) {
            used += snprintf(filter_value + used, sizeof(filter_value) - used,
                             "%s%s", used ? "," : "", explicit_filters[i]);
        }
        reply = used ? filter_value : "None";
    }
    if (reply) { F.override3_cmd = command; F.override3_value = reply; }
    result = base_control_write(icom, bytes, length, timeout);
    F.native_cmd = NULL; F.native_status = NULL; F.override3_cmd = NULL;
    return result;
}
static inst *controlled(instType dtype) {
    inst *p = driver_of(dtype, NULL);
    memset(explicit_exposure, 0, sizeof(explicit_exposure)); memset(explicit_range, 0, sizeof(explicit_range));
    memset(explicit_speed, 0, sizeof(explicit_speed)); memset(explicit_accessory, 0, sizeof(explicit_accessory));
    memset(explicit_ceiling, 0, sizeof(explicit_ceiling));
    control_refusal = control_refusal_status = NULL;
    speed_drift_after_multiplier = 0;
    strcpy(explicit_filters[0], "None"); strcpy(explicit_filters[1], "None"); strcpy(explicit_filters[2], "None");
    base_control_write = p->icom->write; p->icom->write = control_write;
    return p;
}
static void controls(void) {
    const instType models[] = { instCR250, instCR300, instCR100 };
    int model;
    cm_cr250_control_report *report = calloc(1, sizeof(*report));
    if (!report) abort();
    for (model = 0; model < 3; model++) {
        inst *p = controlled(models[model]);
        cr250 *c = (cr250 *)p;
        cm_cr250_control_request request;
        cr250_identity identity;
        ipatch out;
        int writes;
        CHECK(cm_cr250_control_read(p, report, 500) == inst_ok);
        CHECK(report->reply_count > 0 && report->before.present == report->values.present);
        CHECK(report->values.present & CM_CR250_BIT(CM_CR250_EXPOSURE_MODE));
        CHECK(report->controls[CM_CR250_EXPOSURE].limits_known);
        CHECK(!(report->values.present & CM_CR250_BIT(CM_CR250_MAX_AUTO_EXPOSURE)));
        CHECK(!(report->values.present & CM_CR250_BIT(CM_CR250_FILTER1)));
        CHECK(!strcmp(report->controls[CM_CR250_FILTER1].current_native, "None,None,None"));
        CHECK(report->controls[CM_CR250_SPEED].available == (models[model] != instCR100));
        cm_cr250_control_report_event(report);
        writes = F.writes;
        CHECK(cm_cr250_identity_snapshot(p, &identity) && !strcmp(identity.model, F.model));
        CHECK(F.writes == writes);
        memset(&request, 0, sizeof(request));
        request.present = CM_CR250_BIT(CM_CR250_MULTIPLIER); request.exposureMultiplier = 2;
        CHECK(cm_cr250_control_apply(p, &request, report, 500) == inst_ok);
        CHECK(report->changed == request.present && report->acknowledged == request.present && report->confirmed == request.present);
        CHECK(report->values.exposureMultiplier == 2 && !c->averages);
        CHECK(report->before.exposureMultiplier == 1 && report->attempted == request.present);
        CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.exposure_x == 2);
        /* The complete invalid target is refused before any SM, even though
         * another requested field would be independently valid. */
        memset(&request, 0, sizeof(request));
        request.present = CM_CR250_BIT(CM_CR250_MULTIPLIER) | CM_CR250_BIT(CM_CR250_EXPOSURE);
        request.exposureMultiplier = 3; request.exposureMs = 0;
        writes = sm_sent();
        CHECK((cm_cr250_control_apply(p, &request, report, 500) & inst_mask) == inst_wrong_setup);
        CHECK(sm_sent() == writes && !report->acknowledged);
        memset(&request, 0, sizeof(request));
        request.present = CM_CR250_BIT(CM_CR250_EXPOSURE_MODE) | CM_CR250_BIT(CM_CR250_EXPOSURE);
        request.exposureMode = 1; request.exposureMs = 50;
        if (models[model] == instCR100) {
            writes = sm_sent();
            CHECK((cm_cr250_control_apply(p, &request, report, 500) & inst_mask) == inst_wrong_setup);
            CHECK(sm_sent() == writes && strstr(report->problem, "Fixed Range D"));
            request.present |= CM_CR250_BIT(CM_CR250_RANGE_MODE) | CM_CR250_BIT(CM_CR250_RANGE);
            request.rangeMode = 1; request.range = 3;
        }
        CHECK(cm_cr250_control_apply(p, &request, report, 500) == inst_ok);
        CHECK(c->control_exposure_mode == 2 && (report->confirmed & request.present) == request.present);
        cm_cr250_control_report_event(report);
        CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.fixed_exposure_ms == 50);
        /* An ordinary later Argyll mode request restores its recipe. */
        CHECK(p->set_mode(p, inst_mode_emis_spot) == inst_ok && !c->control_exposure_mode);
        p->del(p);
    }
    {
        inst *p = controlled(instCR300);
        cm_cr250_control_request request;
        cr250 *c = (cr250 *)p;
        ipatch out;
        memset(&request, 0, sizeof(request));
        request.present = CM_CR250_BIT(CM_CR250_ACCESSORY) | CM_CR250_BIT(CM_CR250_FILTER1)
                        | CM_CR250_BIT(CM_CR250_FILTER2) | CM_CR250_BIT(CM_CR250_FILTER3)
                        | CM_CR250_BIT(CM_CR250_SPEED) | CM_CR250_BIT(CM_CR250_MAX_AUTO_EXPOSURE);
        request.accessory = 20; request.filter1 = 3; request.filter2 = 4; request.filter3 = 5;
        request.speed = 2; request.maxAutoExposureMs = 200;
        CHECK(cm_cr250_control_apply(p, &request, report, 500) == inst_ok);
        CHECK((report->acknowledged & request.present) == request.present);
        CHECK(!(report->confirmed & CM_CR250_BIT(CM_CR250_MAX_AUTO_EXPOSURE)));
        CHECK(!(report->values.present & CM_CR250_BIT(CM_CR250_MAX_AUTO_EXPOSURE)));
        cm_cr250_control_report_event(report);
        CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.max_auto_exposure_ms == 200);
        CHECK(c->capture.filters[0].id == 3 && c->capture.filters[1].id == 4 && c->capture.filters[2].id == 5);
        p->del(p);
    }
    cm_cr250_control_report_dispose(report); free(report);
}
static void calibration_controls(void) {
    const instType models[] = { instCR250, instCR300, instCR100 };
    int model;
    cm_cr250_control_report *r = calloc(1, sizeof(*r));
    if (!r) abort();
    for (model = 0; model < 3; model++) {
        inst *p = controlled(models[model]);
        cr250 *c = (cr250 *)p;
        cm_cr250_control_request q = {0};
        ipatch out;
        q.present = CM_CR250_BIT(CM_CR250_CORRECTION_MODE) | CM_CR250_BIT(CM_CR250_MATCH);
        q.userCalibrationMode = 2; q.matchId = 9;
        CHECK(cm_cr250_control_apply(p, &q, r, 500) == inst_ok);
        CHECK(c->correction_mode == 2 && c->correction.id == 9 && (r->confirmed & q.present) == q.present);
        cm_cr250_control_report_event(r);
        CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.correction.id == 9);
        q.present = CM_CR250_BIT(CM_CR250_CORRECTION_MODE); q.userCalibrationMode = 0;
        CHECK(cm_cr250_control_apply(p, &q, r, 500) == inst_ok && !c->correction_mode);
        if (models[model] == instCR100) {
            q.present |= CM_CR250_BIT(CM_CR250_MATRIX); q.userCalibrationMode = 1; q.matrixId = 7;
            CHECK(cm_cr250_control_apply(p, &q, r, 500) == inst_ok && c->correction_mode == 1 && c->correction.id == 7);
            CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.correction.id == 7);
            q.present = CM_CR250_BIT(CM_CR250_ACCESSORY); q.accessory = 20;
            {
                int writes = sm_sent();
                CHECK((cm_cr250_control_apply(p, &q, r, 500) & inst_mask) == inst_wrong_setup);
                CHECK(sm_sent() == writes && !r->attempted);
            }
            cm_cr250_control_report_event(r);
        }
        p->del(p);
    }
    cm_cr250_control_report_dispose(r); free(r);
}
static void validation_and_recovery(void) {
    cm_cr250_control_report *r = calloc(1, sizeof(*r));
    cm_cr250_control_request q = {0};
    inst *p = controlled(instCR300);
    cr250 *c = (cr250 *)p;
    ipatch out;
    int writes, measures;
    if (!r) abort();
    q.present = CM_CR250_BIT(CM_CR250_FILTER1) | CM_CR250_BIT(CM_CR250_FILTER2);
    q.filter1 = q.filter2 = 3; writes = sm_sent();
    CHECK((cm_cr250_control_apply(p, &q, r, 500) & inst_mask) == inst_wrong_setup);
    CHECK(sm_sent() == writes && !r->attempted);
    q.present = CM_CR250_BIT(CM_CR250_MULTIPLIER) | CM_CR250_BIT(CM_CR250_EXPOSURE_MODE) | CM_CR250_BIT(CM_CR250_EXPOSURE);
    q.exposureMultiplier = 3; q.exposureMode = 1; q.exposureMs = nextafter(50.0, 51.0);
    CHECK((cm_cr250_control_apply(p, &q, r, 500) & inst_mask) == inst_wrong_setup);
    CHECK(sm_sent() == writes && strstr(r->problem, "decimal wire"));
    q.present = CM_CR250_BIT(CM_CR250_EXPOSURE_MODE) | CM_CR250_BIT(CM_CR250_EXPOSURE); q.exposureMs = 50.1;
    CHECK(cm_cr250_control_apply(p, &q, r, 500) == inst_ok && r->values.exposureMs == 50.1);
    CHECK(cr250_fake_sent("SM Exposure 50.1"));
    cm_cr250_control_report_event(r);
    q.present = CM_CR250_BIT(CM_CR250_MULTIPLIER); q.exposureMultiplier = 3; writes = sm_sent();
    CHECK((cm_cr250_control_apply(p, &q, r, 70) & inst_imask) == CR250_BUDGET_TOO_SHORT);
    CHECK(sm_sent() == writes && !r->attempted && !c->control_blocked);
    q.present |= CM_CR250_BIT(CM_CR250_SPEED); q.speed = 2;
    control_refusal = "SM Speed 2"; control_refusal_status = "ER:-560:Invalid speed:2";
    CHECK(cm_cr250_control_apply(p, &q, r, 500) != inst_ok);
    CHECK(r->attempted == q.present && r->acknowledged == CM_CR250_BIT(CM_CR250_MULTIPLIER));
    CHECK(r->blocked == q.present && c->control_blocked == q.present && c->engine.identified);
    CHECK(r->before.exposureMultiplier == 1 && r->values.exposureMultiplier == 3 && r->reply_count > 0);
    cm_cr250_control_report_event(r);
    measures = F.measures;
    CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SETUP && !out.XYZ_v);
    CHECK(F.measures == measures);
    control_refusal = NULL;
    q.present = 0;
    CHECK(cm_cr250_control_apply(p, &q, r, 500) == inst_ok && r->blocked && !r->attempted);
    CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SETUP && F.measures == measures);
    q.present = CM_CR250_BIT(CM_CR250_SPEED);
    CHECK(cm_cr250_control_apply(p, &q, r, 500) == inst_ok && r->blocked);
    q.present |= CM_CR250_BIT(CM_CR250_MULTIPLIER);
    CHECK(cm_cr250_control_apply(p, &q, r, 500) == inst_ok && !r->blocked && !r->attempted);
    CHECK((r->confirmed & q.present) == q.present);
    cm_cr250_control_report_event(r);
    CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && F.measures == measures + 1);
    p->del(p); cm_cr250_control_report_dispose(r); free(r);
}
static void control_resource_envelope(void) {
    const instType models[] = { instCR250, instCR300, instCR100 };
    int model;
    cm_cr250_control_report *report = calloc(1, sizeof(*report));
    char *list = malloc(16000);
    if (!report || !list) abort();
    for (model = 0; model < 3; model++) {
        inst *p = controlled(models[model]);
        cm_control_fields fields;
        cm_cr250_control_request request = {0};
        inst_code status;
        int i, used = snprintf(list, 16000, "256\r\n");
        for (i = 0; i < 256; i++) {
            long long id = i == 255 ? 9007199254740991LL : i;
            used += snprintf(list + used, 16000 - used, "%lld,", id);
            if (!i) { memset(list + used, 'A', 4000); used += 4000; }
            else used += snprintf(list + used, 16000 - used, "ND-%d", i);
            /* The shared fake appends the reply's final CRLF. */
            used += snprintf(list + used, 16000 - used, ",Radiance%s", i == 255 ? "" : "\r\n");
        }
        F.override_cmd = "RC Filter"; F.override_value = list;
        status = cm_cr250_control_read(p, report, 500);
        CHECK(status == inst_ok);
        if (status != inst_ok) {
            cm_cr250_control_report_dispose(report); p->del(p); continue;
        }
        CHECK(report->controls[CM_CR250_FILTER1].choice_count == 256);
        CHECK(report->controls[CM_CR250_FILTER2].choice_count == 256);
        CHECK(report->controls[CM_CR250_FILTER3].choice_count == 256);
        CHECK(report->controls[CM_CR250_FILTER1].choices != report->controls[CM_CR250_FILTER2].choices);
        if (report->controls[CM_CR250_FILTER1].choice_count != 256
         || report->controls[CM_CR250_FILTER2].choice_count != 256
         || report->controls[CM_CR250_FILTER3].choice_count != 256
         || !report->controls[CM_CR250_FILTER1].choices
         || !report->controls[CM_CR250_FILTER2].choices
         || !report->controls[CM_CR250_FILTER3].choices) {
            cm_cr250_control_report_dispose(report); p->del(p); continue;
        }
        CHECK(report->controls[CM_CR250_FILTER1].choices[255].id == 9007199254740991LL);
        CHECK(strlen(report->controls[CM_CR250_FILTER1].choices[0].name) == 4000);
        report->controls[CM_CR250_FILTER1].choices[0].name[0] = 'B';
        CHECK(report->controls[CM_CR250_FILTER2].choices[0].name[0] == 'A');
        CHECK(cm_cr250_control_read(p, report, 500) == inst_ok);
        CHECK(report->controls[CM_CR250_FILTER1].choices[0].name[0] == 'A');
        cm_cr250_control_report_event(report);
        CHECK(cm_control_fields_parse("filter1=9007199254740991,accessory=4294967296", &fields));
        CHECK(cm_control_decode(&fields, cm_cr_control_fields, CM_CR250_FIELDS, &request, &request.present));
        CHECK(request.filter1 == 9007199254740991LL && request.accessory == 4294967296LL);
        CHECK(cm_control_fields_parse("filter1=9007199254740990.5", &fields));
        CHECK(!cm_control_decode(&fields, cm_cr_control_fields, CM_CR250_FIELDS, &request, &request.present));
        F.override_cmd = "RC MinExposureX"; F.override_value = "1.00000000000000001";
        CHECK(cm_cr250_control_read(p, report, 500) == inst_ok);
        CHECK(!report->controls[CM_CR250_MULTIPLIER].available && !report->controls[CM_CR250_MULTIPLIER].limits_known);
        F.override_value = "1e0";
        CHECK(cm_cr250_control_read(p, report, 500) == inst_ok);
        CHECK(report->controls[CM_CR250_MULTIPLIER].limits_known && report->controls[CM_CR250_MULTIPLIER].minimum == 1);
        F.override_cmd = "RS ExposureX"; F.override_value = "1.99999999999999999";
        CHECK(cm_cr250_control_read(p, report, 500) == inst_ok);
        CHECK(!(report->values.present & CM_CR250_BIT(CM_CR250_MULTIPLIER))
            && !report->controls[CM_CR250_MULTIPLIER].available && !report->failed);
        cm_cr250_control_report_event(report);
        cm_cr250_control_report_dispose(report);
        CHECK(!report->controls[CM_CR250_FILTER1].choices && !report->raw_exchanges && !report->reply_count);
        cm_cr250_control_report_dispose(report);
        p->del(p);
    }
    free(list); free(report);
}
static int (*raw_base_read)(icoms *, char *, int, int *, char *, int, double);
static int raw_fault, raw_fault_injected;
static int binary_reply_read(icoms *icom, char *bytes, int size, int *count,
                             char *terminators, int needed, double timeout) {
    int code = raw_base_read(icom, bytes, size, count, terminators, needed, timeout);
    if (!raw_fault_injected && *count > 0 && last_request_is("RC ExposureMode")) {
        bytes[0] = raw_fault == 1 ? '\000' : (char)0x80;
        raw_fault_injected = 1;
    }
    return code;
}
static int (*raw_base_observe)(void *, const char *, const cr250_raw *, int, int);
static int overflow_measure_observe(void *context, const char *command, const cr250_raw *raw,
                                   int error, int complete) {
    cr250 *p = context;
    if (!strcmp(command, "M")) p->raw_bytes = CM_CR250_RAW_BYTES;
    return raw_base_observe(context, command, raw, error, complete);
}
static void control_raw_retention(void) {
    cm_cr250_control_report *report = calloc(1, sizeof(*report));
    int scenario;
    if (!report) abort();
    for (scenario = 1; scenario <= 3; scenario++) {
        inst *p = controlled(instCR250);
        cr250 *c = (cr250 *)p;
        int i, found = 0, writes;
        if (scenario < 3) {
            raw_fault = scenario; raw_fault_injected = 0;
            raw_base_read = p->icom->read; p->icom->read = binary_reply_read;
        } else { F.override_cmd = "RC Filter"; F.override_value = "2\r\n1,First,Radiance"; }
        CHECK(cm_cr250_control_read(p, report, 500) != inst_ok && report->failed);
        CHECK(report->raw_exchange_count > 0 && !c->raw_tracing);
        for (i = 0; i < report->raw_exchange_count; i++) {
            const cm_cr250_exchange *exchange = &report->raw_exchanges[i];
            if (scenario < 3 && !strcmp(exchange->command, "RC ExposureMode")) {
                found = exchange->raw.length > 0 && !exchange->complete
                    && memchr(exchange->raw.bytes, scenario == 1 ? 0 : 0x80, exchange->raw.length) != NULL;
            } else if (scenario == 3 && !strcmp(exchange->command, "RC Filter")) {
                found = !exchange->complete && exchange->raw.length > 0
                    && strstr(exchange->raw.bytes, "First,Radiance") != NULL;
            }
        }
        CHECK(found); cm_cr250_control_report_event(report);
        writes = F.writes;
        CHECK(cm_cr250_control_read(p, report, -1) == inst_bad_parameter);
        CHECK(!report->raw_exchange_count && !report->raw_exchanges && F.writes == writes);
        cm_cr250_control_report_dispose(report); p->del(p);
    }
    {
        inst *p = controlled(instCR250);
        cr250 *c = (cr250 *)p;
        ipatch out;
        CHECK(cm_cr250_control_read(p, report, 500) == inst_ok && report->raw_exchange_count > 0);
        CHECK(cm_cr250_control_read(p, report, 500) == inst_ok && report->raw_exchange_count == c->raw_count);
        cm_cr250_control_report_dispose(report);
        raw_base_observe = c->engine.host_observe; c->engine.host_observe = overflow_measure_observe;
        CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok);
        CHECK(c->raw_overflow && !out.XYZ_v && !out.sp.spec_n && !c->capture.valid);
        cm_cr250_native_facts(p); p->del(p);
    }
    free(report);
}
static void optional_control_facts(void) {
    cm_cr250_control_report *report = calloc(1, sizeof(*report));
    const char *commands[] = { "RC Filter", "RS Speed", "RC Filter" };
    const char *values[] = { "ER:-560:Unsupported configuration:Filter",
        "ER:-560:Unsupported configuration:Speed", "2\r\n1,First,Radiance\r\n1,Duplicate,Radiance" };
    int scenario;
    if (!report) abort();
    for (scenario = 0; scenario < 3; scenario++) {
        inst *p = controlled(instCR250);
        cr250 *c = (cr250 *)p;
        cm_cr250_control_request request = {0};
        int i, retained = 0, writes;
        F.override_cmd = commands[scenario]; F.override_value = values[scenario];
        CHECK(cm_cr250_control_read(p, report, 500) == inst_ok && !c->engine.uncertain);
        CHECK(!report->controls[scenario == 1 ? CM_CR250_SPEED : CM_CR250_FILTER1].available);
        CHECK(report->controls[CM_CR250_MULTIPLIER].available);
        for (i = 0; i < report->reply_count; i++)
            if (!strcmp(report->replies[i].command, commands[scenario]) && report->replies[i].error) retained++;
        CHECK(retained > 0 && report->raw_exchange_count > 0);
        request.present = CM_CR250_BIT(CM_CR250_MULTIPLIER); request.exposureMultiplier = 2;
        writes = sm_sent();
        CHECK(cm_cr250_control_apply(p, &request, report, 500) == inst_ok);
        CHECK(report->confirmed == request.present && sm_sent() == writes + 1 && !c->engine.uncertain);
        cm_cr250_control_report_event(report);
        cm_cr250_control_report_dispose(report); p->del(p);
    }
    {
        const instType models[] = { instCR250, instCR300, instCR100 };
        int model;
        for (model = 0; model < 3; model++) {
            inst *p = controlled(models[model]);
            cr250 *c = (cr250 *)p;
            cm_cr250_control_request request = {0};
            int writes;
            request.present = CM_CR250_BIT(CM_CR250_CORRECTION_MODE) | CM_CR250_BIT(CM_CR250_MATCH);
            request.userCalibrationMode = 2; request.matchId = 9;
            CHECK(cm_cr250_control_apply(p, &request, report, 500) == inst_ok && c->correction_mode == 2);
            F.override_cmd = "RS UserCalibMode"; F.override_value = "Unknown native mode";
            request.present = CM_CR250_BIT(CM_CR250_MULTIPLIER); request.exposureMultiplier = 2;
            writes = sm_sent();
            CHECK(cm_cr250_control_apply(p, &request, report, 500) == inst_ok);
            CHECK(report->confirmed == request.present && !(report->values.present & CM_CR250_BIT(CM_CR250_CORRECTION_MODE)));
            CHECK(c->correction_mode == 2 && c->correction.id == 9 && sm_sent() == writes + 1);
            cm_cr250_control_report_event(report);
            request.present = CM_CR250_BIT(CM_CR250_CORRECTION_MODE); request.userCalibrationMode = 0;
            writes = sm_sent();
            CHECK((cm_cr250_control_apply(p, &request, report, 500) & inst_mask) == inst_wrong_setup);
            CHECK(!report->attempted && sm_sent() == writes && c->correction_mode == 2);
            cm_cr250_control_report_dispose(report); p->del(p);
        }
    }
    free(report);
}
static void warning_control_facts(void) {
    const char *commands[] = { "RC Filter", "RS Speed", "RC MinExposureX" };
    const char *statuses[] = {
        "OK:2:RC Filter:2\r\n1,First,Radiance\r\n2,Second,Radiance",
        "OK:2:RS Speed:Normal", "OK:2:RC MinExposureX:1"
    };
    const int fields[] = { CM_CR250_FILTER1, CM_CR250_SPEED, CM_CR250_MULTIPLIER };
    cm_cr250_control_report *report = calloc(1, sizeof(*report));
    int scenario;
    if (!report) abort();
    for (scenario = 0; scenario < 3; scenario++) {
        inst *p = controlled(instCR250);
        cr250 *c = (cr250 *)p;
        int i, found = 0;
        control_refusal = commands[scenario]; control_refusal_status = statuses[scenario];
        CHECK(cm_cr250_control_read(p, report, 500) == inst_ok && !c->engine.uncertain);
        CHECK(!report->controls[fields[scenario]].available);
        CHECK(!(report->values.present & CM_CR250_BIT(fields[scenario])));
        for (i = 0; i < report->reply_count; i++)
            if (!strcmp(report->replies[i].command, commands[scenario])
             && report->replies[i].reply.complete && report->replies[i].reply.code == 2
             && report->replies[i].error == CR250_NATIVE_WARNING_BASE + 2) found++;
        CHECK(found > 0 && report->raw_exchange_count > 0);
        cm_cr250_control_report_event(report);
        cm_cr250_control_report_dispose(report); p->del(p);
    }
    free(report);
}
static void unavailable_dependency_and_intent(void) {
    cm_cr250_control_report *report = calloc(1, sizeof(*report));
    inst *p = controlled(instCR100);
    cr250 *c = (cr250 *)p;
    cm_cr250_control_request request = {0};
    int writes, scenario;
    if (!report) abort();
    request.present = CM_CR250_BIT(CM_CR250_EXPOSURE_MODE) | CM_CR250_BIT(CM_CR250_EXPOSURE)
                    | CM_CR250_BIT(CM_CR250_RANGE_MODE) | CM_CR250_BIT(CM_CR250_RANGE);
    request.exposureMode = 1; request.exposureMs = 50; request.rangeMode = 1; request.range = 3;
    CHECK(cm_cr250_control_apply(p, &request, report, 500) == inst_ok && c->control_exposure_mode == 2);
    F.override_cmd = "RS ExposureMode"; F.override_value = "Unknown native mode";
    for (scenario = 0; scenario < 3; scenario++) {
        request.present = scenario == 0 ? CM_CR250_BIT(CM_CR250_RANGE_MODE)
                        : scenario == 1 ? CM_CR250_BIT(CM_CR250_RANGE) : CM_CR250_BIT(CM_CR250_EXPOSURE);
        request.rangeMode = 0; request.range = 0; request.exposureMs = 50;
        writes = sm_sent();
        CHECK((cm_cr250_control_apply(p, &request, report, 500) & inst_mask) == inst_wrong_setup);
        CHECK(!report->attempted && sm_sent() == writes && c->control_exposure_mode == 2);
    }
    request.present = CM_CR250_BIT(CM_CR250_MULTIPLIER); request.exposureMultiplier = 2;
    writes = sm_sent();
    CHECK(cm_cr250_control_apply(p, &request, report, 500) == inst_ok);
    CHECK(report->confirmed == request.present && sm_sent() == writes + 1 && c->control_exposure_mode == 2);
    CHECK(!(report->values.present & CM_CR250_BIT(CM_CR250_EXPOSURE_MODE)));
    cm_cr250_control_report_event(report);
    cm_cr250_control_report_dispose(report); p->del(p);
    p = controlled(instCR250); c = (cr250 *)p;
    request.present = CM_CR250_BIT(CM_CR250_SYNC_MODE) | CM_CR250_BIT(CM_CR250_FREQUENCY);
    request.syncMode = 2; request.syncFrequencyHz = 60;
    CHECK(cm_cr250_control_apply(p, &request, report, 500) == inst_ok);
    CHECK(!strcmp(c->control_sync_name, "Manual"));
    F.override_cmd = "RS SyncMode"; F.override_value = "Unknown native sync";
    request.present = CM_CR250_BIT(CM_CR250_MULTIPLIER); request.exposureMultiplier = 2;
    writes = sm_sent();
    CHECK(cm_cr250_control_apply(p, &request, report, 500) == inst_ok);
    CHECK(report->confirmed == request.present && sm_sent() == writes + 1);
    CHECK(!strcmp(c->control_sync_name, "Manual") && !(report->values.present & CM_CR250_BIT(CM_CR250_SYNC_MODE)));
    cm_cr250_control_report_event(report);
    cm_cr250_control_report_dispose(report); p->del(p); free(report);
}
static void final_unattempted_drift_debt(void) {
    cm_cr250_control_report *report = calloc(1, sizeof(*report));
    inst *p = controlled(instCR250);
    cr250 *c = (cr250 *)p;
    cm_cr250_control_request request = {0};
    const unsigned int multiplier = CM_CR250_BIT(CM_CR250_MULTIPLIER), speed = CM_CR250_BIT(CM_CR250_SPEED);
    int writes;
    if (!report) abort();
    strcpy(explicit_speed, "Slow"); speed_drift_after_multiplier = 1;
    request.present = multiplier | speed; request.exposureMultiplier = 2; request.speed = 0;
    CHECK((cm_cr250_control_apply(p, &request, report, 500) & inst_mask) == inst_wrong_setup);
    CHECK(report->attempted == multiplier && report->acknowledged == multiplier);
    CHECK(report->requested.speed == 0 && report->values.speed == 2);
    CHECK(report->blocked == request.present && c->control_blocked == request.present);
    cm_cr250_control_report_event(report);
    request.present = multiplier; writes = sm_sent();
    CHECK(cm_cr250_control_apply(p, &request, report, 500) == inst_ok);
    CHECK(report->blocked == (multiplier | speed) && !report->attempted && sm_sent() == writes);
    request.present |= speed;
    CHECK(cm_cr250_control_apply(p, &request, report, 500) == inst_ok);
    CHECK(report->attempted == speed && report->acknowledged == speed && report->confirmed == request.present);
    CHECK(!report->blocked && !c->control_blocked && report->values.speed == 0);
    cm_cr250_control_report_event(report);
    cm_cr250_control_report_dispose(report); p->del(p); free(report);
}
static int aperture_trace_fault;
static int failing_aperture_observe(void *context, const char *command, const cr250_raw *raw,
                                    int error, int complete) {
    cr250 *p = context;
    if (!strcmp(command, "RS Aperture")) {
        if (aperture_trace_fault == 1) p->raw_bytes = CM_CR250_RAW_BYTES;
        else {
            p->raw_overflow = 1;
            if (aperture_trace_fault == 2) return CR250_INTERNAL_ERROR;
        }
    }
    return raw_base_observe(context, command, raw, error, complete);
}
static void optional_trace_failure_precedence(void) {
    cm_cr250_control_report *report = calloc(1, sizeof(*report));
    int scenario;
    if (!report) abort();
    for (scenario = 0; scenario < 6; scenario++) {
        inst *p = controlled(instCR250);
        cr250 *c = (cr250 *)p;
        int expected = scenario < 2 || scenario == 4 ? CR250_REPLY_TOO_LONG
                     : scenario < 4 ? CR250_INTERNAL_ERROR : CR250_DATA_PARSE_ERROR;
        control_refusal = "RS Aperture";
        control_refusal_status = scenario == 5 ? "OK:invalid:RS Aperture:1 deg"
            : scenario % 2 ? "ER:-560:Unsupported configuration:Aperture" : "OK:2:RS Aperture:1 deg";
        aperture_trace_fault = scenario < 2 ? 1 : scenario < 4 ? 2 : 3;
        raw_base_observe = c->engine.host_observe; c->engine.host_observe = failing_aperture_observe;
        CHECK((cm_cr250_control_read(p, report, 500) & inst_imask) == expected);
        CHECK(report->failed && report->trace_overflow && report->raw_exchange_count > 0);
        CHECK(!c->raw_tracing && report->aperture[0] == 0);
        cm_cr250_control_report_event(report);
        cm_cr250_control_report_dispose(report); p->del(p);
    }
    free(report);
}
int main(int argc, char **argv) {
    (void)argc; (void)argv;
    setvbuf(stdout, NULL, _IONBF, 0);
    wire = cr250_facts_output();
    if (!wire) return 2;
    host_checks();
    controls();
    calibration_controls(); validation_and_recovery();
    control_resource_envelope();
    control_raw_retention();
    optional_control_facts();
    warning_control_facts();
    unavailable_dependency_and_intent();
    final_unattempted_drift_debt();
    optional_trace_failure_precedence();
    fclose(wire);
    printf("cr250_control_test: %d checks, %d failures\n", checks, failures);
    return failures != 0;
}
