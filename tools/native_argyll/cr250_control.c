/* Typed application controls over the shared CR contribution engine.
 * Remote Communication 1.36 sections 4.2.1, 4.3.2 and 4.3.3 define commands;
 * CRI App 1.5.10 defines Fixed CR-100 exposure in Fixed Range D.
 * No hardware behavior or physical optics is established by this code.
 * Copyright 2026 Fernando Voltolini de Azambuja.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#include "cr250_control.h"
#include <errno.h>
#include <limits.h>
#include <stdlib.h>

typedef struct {
    const char *key;
    int kind, since_minor;
} control_policy;
static const control_policy policies[CM_CR250_FIELDS] = {
    {"Mode", CM_CR250_CHOICE, 16}, {"ExposureMode", CM_CR250_CHOICE, 4},
    {"Exposure", CM_CR250_MILLISECONDS, 4}, {"RangeMode", CM_CR250_CHOICE, 4},
    {"Range", CM_CR250_CHOICE, 4}, {"ExposureX", CM_CR250_POSITIVE_INTEGER, 4},
    {"SyncMode", CM_CR250_CHOICE, 4}, {"SyncFreq", CM_CR250_HERTZ, 4},
    {"Speed", CM_CR250_CHOICE, 17}, {"Accessory", CM_CR250_CHOICE, 4},
    {"Filter1", CM_CR250_CHOICE, 4}, {"Filter2", CM_CR250_CHOICE, 4},
    {"Filter3", CM_CR250_CHOICE, 4}, {"UserCalibMode", CM_CR250_CHOICE, 16},
    {"Matrix", CM_CR250_PROFILE, 4}, {"Match", CM_CR250_PROFILE, 16},
    {"MaxAutoExposure", CM_CR250_MILLISECONDS, 26}
};

typedef struct {
    cr250 *meter;
    cm_cr250_control_report *report;
    unsigned int started_ms;
    double timeout_s;
    int phase;
} control_operation;

static void controls_dispose(cm_cr250_control_report *report) {
    int field;
    for (field = 0; field < CM_CR250_FIELDS; field++) free(report->controls[field].choices);
    memset(report->controls, 0, sizeof(report->controls));
}
void cm_cr250_control_report_dispose(cm_cr250_control_report *report) {
    if (!report) return;
    controls_dispose(report); memset(report, 0, sizeof(*report));
}

static double value(const cm_cr250_control_request *r, int field) {
    switch (field) {
        case CM_CR250_MODE: return (double)r->mode;
        case CM_CR250_EXPOSURE_MODE: return (double)r->exposureMode;
        case CM_CR250_EXPOSURE: return r->exposureMs;
        case CM_CR250_RANGE_MODE: return (double)r->rangeMode;
        case CM_CR250_RANGE: return (double)r->range;
        case CM_CR250_MULTIPLIER: return (double)r->exposureMultiplier;
        case CM_CR250_SYNC_MODE: return (double)r->syncMode;
        case CM_CR250_FREQUENCY: return r->syncFrequencyHz;
        case CM_CR250_SPEED: return (double)r->speed;
        case CM_CR250_ACCESSORY: return (double)r->accessory;
        case CM_CR250_FILTER1: return (double)r->filter1;
        case CM_CR250_FILTER2: return (double)r->filter2;
        case CM_CR250_FILTER3: return (double)r->filter3;
        case CM_CR250_CORRECTION_MODE: return (double)r->userCalibrationMode;
        case CM_CR250_MATRIX: return (double)r->matrixId;
        case CM_CR250_MATCH: return (double)r->matchId;
        case CM_CR250_MAX_AUTO_EXPOSURE: return r->maxAutoExposureMs;
    }
    return 0;
}
static void put_value(cm_cr250_control_request *r, int field, double v) {
    switch (field) {
        case CM_CR250_MODE: r->mode = (long long)v; break;
        case CM_CR250_EXPOSURE_MODE: r->exposureMode = (long long)v; break;
        case CM_CR250_EXPOSURE: r->exposureMs = v; break;
        case CM_CR250_RANGE_MODE: r->rangeMode = (long long)v; break;
        case CM_CR250_RANGE: r->range = (long long)v; break;
        case CM_CR250_MULTIPLIER: r->exposureMultiplier = (long long)v; break;
        case CM_CR250_SYNC_MODE: r->syncMode = (long long)v; break;
        case CM_CR250_FREQUENCY: r->syncFrequencyHz = v; break;
        case CM_CR250_SPEED: r->speed = (long long)v; break;
        case CM_CR250_ACCESSORY: r->accessory = (long long)v; break;
        case CM_CR250_FILTER1: r->filter1 = (long long)v; break;
        case CM_CR250_FILTER2: r->filter2 = (long long)v; break;
        case CM_CR250_FILTER3: r->filter3 = (long long)v; break;
        case CM_CR250_CORRECTION_MODE: r->userCalibrationMode = (long long)v; break;
        case CM_CR250_MATRIX: r->matrixId = (long long)v; break;
        case CM_CR250_MATCH: r->matchId = (long long)v; break;
        case CM_CR250_MAX_AUTO_EXPOSURE: r->maxAutoExposureMs = v; break;
    }
    r->present |= CM_CR250_BIT(field);
}
static double remaining(control_operation *o) {
    return o->timeout_s - (o->meter->engine.now_ms() - o->started_ms) / 1000.0;
}
static double command_timeout(control_operation *o) {
    double seconds = remaining(o);
    return seconds > 3.0 ? 3.0 : seconds;
}
static int problem(control_operation *o, int code, const char *message) {
    snprintf(o->report->problem, sizeof(o->report->problem), "%s", message);
    o->report->problem[sizeof(o->report->problem) - 1] = 0;
    return code;
}
static int retain(control_operation *o, const char *command, const cr250_reply *reply, int ec) {
    int n = o->report->reply_count;
    if (n >= CM_CR250_CONTROL_REPLIES) return problem(o, CR250_INTERNAL_ERROR, "Control receipt exceeded its reply bound");
    o->report->reply_count++;
    snprintf(o->report->replies[n].command, sizeof(o->report->replies[n].command), "%s", command);
    o->report->replies[n].phase = o->phase;
    o->report->replies[n].error = ec;
    o->report->replies[n].reply = *reply;
    return ec;
}
static int observe(void *context, const char *command, const cr250_reply *reply, int ec) {
    return retain((control_operation *)context, command, reply, ec);
}
static int query(control_operation *o, const char *command, cr250_reply *reply) {
    double seconds = command_timeout(o);
    int ec;
    memset(reply, 0, sizeof(*reply));
    if (o->report->reply_count >= CM_CR250_CONTROL_REPLIES) return CR250_INTERNAL_ERROR;
    if (!(seconds > 0)) return problem(o, CR250_BUDGET_TOO_SHORT, "Control allowance expired before the next command");
    ec = cr250_imp_command(&o->meter->engine, command, reply, seconds);
    return retain(o, command, reply, ec);
}
static int inventory(control_operation *o, const char *key, cr250_inventory *inv) {
    double seconds = command_timeout(o);
    int ec;
    char command[CR250_MAX_COMMAND + 1];
    memset(&o->meter->engine.last_reply, 0, sizeof(o->meter->engine.last_reply));
    if (o->report->reply_count >= CM_CR250_CONTROL_REPLIES) return CR250_INTERNAL_ERROR;
    if (!(seconds > 0)) return problem(o, CR250_BUDGET_TOO_SHORT, "Control allowance expired before the next inventory");
    ec = cr250_imp_inventory(&o->meter->engine, key, inv, seconds);
    snprintf(command, sizeof(command), "RC %s", key);
    return retain(o, command, &o->meter->engine.last_reply, ec);
}
static int unavailable(const cr250 *p, int ec, const cr250_reply *r) {
    return !p->engine.uncertain && ((ec == CR250_OK && !strcmp(r->value, "NA"))
        || (r->complete && r->code != 0 && cr250_native_code(ec) == r->code));
}
static int numeric(const char *text, int kind, double *v) {
    const char *p = text;
    if (kind == CM_CR250_POSITIVE_INTEGER) {
        long long integer;
        if (!cr250_parse_integer(text, &integer) || integer < 1) return 0;
        *v = (double)integer; return 1;
    }
    if (!cr250_parse_number(&p, v)) return 0;
    return !strcmp(p, kind == CM_CR250_HERTZ ? " Hz" : " msec") && *v >= 0;
}
static int identifier(const char *text, long long *v) {
    return cr250_parse_integer(text, v) && *v >= 0;
}
static const cr250_record *choice(const cm_cr250_control_row *row, double id) {
    int i;
    for (i = 0; i < row->choice_count; i++) if ((double)row->choices[i].id == id) return &row->choices[i];
    return NULL;
}
static const char *label(const cm_cr250_control_report *r, const cm_cr250_control_request *target, int field) {
    const cr250_record *record;
    if (!(target->present & CM_CR250_BIT(field))) return NULL;
    record = choice(&r->controls[field], value(target, field));
    return record ? record->name : NULL;
}
static int named(const cm_cr250_control_report *r, const cm_cr250_control_request *target, int field, const char *name) {
    const char *current = label(r, target, field);
    return current && !strcmp(current, name);
}
static int fixed(const cm_cr250_control_report *r, const cm_cr250_control_request *target, int field) {
    return named(r, target, field, "Fixed") || named(r, target, field, "Manual");
}
static int applicable(cr250 *p, int field) {
    if (!cr250_firmware_at_least(&p->engine.id, 1, policies[field].since_minor)) return 0;
    if (p->dtype == instCR100) return field != CM_CR250_SPEED
        && (field != CM_CR250_MATRIX || p->engine.id.instrument_type == 1);
    return field != CM_CR250_MODE && field != CM_CR250_RANGE_MODE
        && field != CM_CR250_RANGE && field != CM_CR250_MATRIX;
}
static int filter_text(const char *text, int slot, char *out) {
    const char *start = text, *end;
    int i;
    if (!strcmp(text, "None")) { strcpy(out, "None"); return 1; }
    for (i = 0; i < 3; i++) {
        size_t n;
        end = strchr(start, ',');
        if ((i < 2 && !end) || (i == 2 && end)) return 0;
        n = end ? (size_t)(end - start) : strlen(start);
        if (!n || n >= CR250_MAX_VALUE) return 0;
        if (i == slot) { memcpy(out, start, n); out[n] = 0; }
        if (end) start = end + 1;
    }
    return 1;
}
static int current_value(control_operation *o, int field, const char *text) {
    cm_cr250_control_row *row = &o->report->controls[field];
    const char *name = text;
    char part[CR250_MAX_VALUE];
    double v;
    long long id;
    int i, matches = 0;
    if (!strcmp(text, "NA")) return CR250_OK;
    if (field >= CM_CR250_FILTER1 && field <= CM_CR250_FILTER3) {
        if (!filter_text(text, field - CM_CR250_FILTER1, part)) return CR250_DATA_PARSE_ERROR;
        name = part;
    }
    if (row->kind == CM_CR250_CHOICE) {
        if ((field == CM_CR250_EXPOSURE_MODE || field == CM_CR250_RANGE_MODE) && !strcmp(name, "Manual")) name = "Fixed";
        for (i = 0; i < row->choice_count; i++) if (!strcmp(row->choices[i].name, name)) { id = row->choices[i].id; matches++; }
        /* None is native absence, not evidence of a configurable ID zero. */
        if (matches == 0 && field >= CM_CR250_FILTER1 && field <= CM_CR250_FILTER3 && !strcmp(name, "None")) return CR250_OK;
        if (matches != 1) return problem(o, CR250_SETUP, "Current setting name has no unique reported inventory ID");
        put_value(&o->report->values, field, (double)id);
    } else if (row->kind == CM_CR250_PROFILE) {
        if (!identifier(text, &id) || !choice(row, (double)id)) return CR250_DATA_PARSE_ERROR;
        put_value(&o->report->values, field, (double)id);
    } else {
        if (!numeric(text, row->kind, &v)) return CR250_DATA_PARSE_ERROR;
        put_value(&o->report->values, field, v);
    }
    return CR250_OK;
}

/* Complete counted inventories remain owned by the contribution parser. Filter
 * inventory and RS Filter are reused for all three slots in this one operation. */
static int read_controls(control_operation *o) {
    cr250 *p = o->meter;
    cm_cr250_control_report *r = o->report;
    cr250_inventory inv = {0}, filters = {0};
    cr250_reply reply, filter_reply;
    int field, ec = CR250_OK, filters_read = 0, filter_rs_read = 0;
    char cmd[64];
    memset(&r->values, 0, sizeof(r->values));
    controls_dispose(r);
    r->aperture[0] = 0;
    for (field = 0; field < CM_CR250_FIELDS; field++) {
        cm_cr250_control_row *row = &r->controls[field];
        const control_policy *policy = &policies[field];
        const char *key = policy->key;
        int i;
        row->kind = policy->kind; row->read_only = field == CM_CR250_MODE;
        if (!applicable(p, field)) { strcpy(row->problem, "Unavailable for the verified model/firmware"); continue; }
        if (field >= CM_CR250_FILTER1 && field <= CM_CR250_FILTER3) key = "Filter";
        if (field == CM_CR250_MAX_AUTO_EXPOSURE) key = "Exposure";
        if (row->kind == CM_CR250_CHOICE || row->kind == CM_CR250_PROFILE) {
            const cr250_inventory *source;
            if (!strcmp(key, "Filter") && filters_read) source = &filters;
            else {
                cr250_inventory_dispose(&inv);
                ec = inventory(o, key, &inv);
                if (unavailable(p, ec, &p->engine.last_reply)
                 || ((ec == CR250_DATA_PARSE_ERROR || ec == CR250_INVENTORY_SHAPE)
                     && !p->engine.uncertain && p->engine.last_reply.complete)) {
                    strcpy(row->problem, "Inventory unavailable after a complete response"); continue;
                }
                if (ec != CR250_OK) goto done;
                if (!strcmp(key, "Filter")) {
                    filters = inv; memset(&inv, 0, sizeof(inv)); filters_read = 1;
                    source = &filters;
                } else source = &inv;
            }
            if (source->count) {
                row->choices = calloc(source->count, sizeof(*row->choices));
                if (!row->choices) { ec = CR250_INTERNAL_ERROR; goto done; }
            }
            for (i = 0; i < source->count; i++) {
                if (source->rec[i].id < 0 || source->rec[i].id > 9007199254740991LL) {
                    ec = CR250_DATA_PARSE_ERROR; goto done;
                }
                if (field == CM_CR250_CORRECTION_MODE && (!p->model->matrix || p->engine.id.instrument_type != 1)
                    && !strcmp(source->rec[i].name, "Matrix")) continue;
                row->choices[row->choice_count++] = source->rec[i];
            }
            row->available = row->choice_count > 0 && !row->read_only;
        } else {
            double low, high;
            snprintf(cmd, sizeof(cmd), "RC Min%s", key);
            ec = query(o, cmd, &reply);
            if (unavailable(p, ec, &reply)) { strcpy(row->problem, "Numeric minimum unavailable"); continue; }
            if (ec != CR250_OK) goto done;
            if (!numeric(reply.value, row->kind, &low)) { strcpy(row->problem, "Numeric minimum could not be interpreted"); continue; }
            snprintf(cmd, sizeof(cmd), "RC Max%s", key);
            ec = query(o, cmd, &reply);
            if (unavailable(p, ec, &reply)) { strcpy(row->problem, "Numeric maximum unavailable"); continue; }
            if (ec != CR250_OK) goto done;
            if (!numeric(reply.value, row->kind, &high) || !(low > 0 && low <= high)) {
                strcpy(row->problem, "Numeric limits must be positive and ordered"); continue;
            }
            row->available = row->limits_known = 1; row->minimum = low; row->maximum = high;
        }
        if (field == CM_CR250_MAX_AUTO_EXPOSURE || !(row->available || row->choice_count)) continue;
        snprintf(cmd, sizeof(cmd), "RS %s", key);
        if (!strcmp(key, "Filter") && filter_rs_read) reply = filter_reply;
        else {
            ec = query(o, cmd, &reply);
            if (unavailable(p, ec, &reply)) {
                row->available = 0;
                if (ec == CR250_OK) strcpy(row->current_native, reply.value);
                strcpy(row->problem, "Current value unavailable after a complete response"); continue;
            }
            if (ec != CR250_OK) goto done;
            if (!strcmp(key, "Filter")) { filter_reply = reply; filter_rs_read = 1; }
        }
        strcpy(row->current_native, reply.value);
        if ((ec = current_value(o, field, reply.value)) != CR250_OK) {
            if (p->engine.uncertain || (ec != CR250_DATA_PARSE_ERROR && ec != CR250_SETUP)) goto done;
            row->available = 0;
            snprintf(row->problem, sizeof(row->problem), "%s", r->problem[0] ? r->problem : "Current value could not be interpreted");
            r->problem[0] = 0;
        }
    }
    ec = query(o, "RS Aperture", &reply);
    if (!unavailable(p, ec, &reply)) {
        if (ec != CR250_OK) goto done;
        strcpy(r->aperture, reply.value);
    }
    ec = CR250_OK;
done:
    cr250_inventory_dispose(&inv); cr250_inventory_dispose(&filters);
    return ec;
}
static int within(const cm_cr250_control_report *r, const cm_cr250_control_request *target, int field) {
    const cm_cr250_control_row *row = &r->controls[field];
    double v = value(target, field);
    return (target->present & CM_CR250_BIT(field)) && row->available && row->limits_known
        && cr250_finite(v) && v >= row->minimum && v <= row->maximum;
}
/* The engine accepts at most 16 unsigned decimal characters, with no exponent.
 * Find a fixed decimal that round-trips exactly; never silently round a target.
 * Every requested argument is checked before the first persistent setter. */
static int argument(double v, int kind, char out[64]) {
    int precision;
    if (kind == CM_CR250_CHOICE || kind == CM_CR250_PROFILE || kind == CM_CR250_POSITIVE_INTEGER) {
        snprintf(out, 64, "%.0f", v);
        return strlen(out) <= 16;
    }
    for (precision = 0; precision <= 15; precision++) {
        double parsed;
        const char *tail = out;
        snprintf(out, 64, "%.*f", precision, v);
        if (strlen(out) <= 16 && cr250_parse_number(&tail, &parsed) && !*tail && parsed == v) return 1;
    }
    return 0;
}
static int validate(control_operation *o, const cm_cr250_control_request *request, cm_cr250_control_request *target) {
    cm_cr250_control_report *r = o->report;
    cr250 *p = o->meter;
    unsigned int exposure_bits = CM_CR250_BIT(CM_CR250_EXPOSURE) | CM_CR250_BIT(CM_CR250_EXPOSURE_MODE)
                              | CM_CR250_BIT(CM_CR250_RANGE_MODE) | CM_CR250_BIT(CM_CR250_RANGE);
    int field, i, j;
    if (request->present & ~CM_CR250_ALL) return CR250_SETUP;
    *target = r->values;
    for (field = 0; field < CM_CR250_FIELDS; field++) if (request->present & CM_CR250_BIT(field)) {
        const cm_cr250_control_row *row = &r->controls[field];
        double v = value(request, field);
        char encoded[64];
        if (field == CM_CR250_MODE) {
            const cr250_record *record = choice(row, v);
            if (!record || strcmp(record->name, "Colorimeter")) return problem(o, CR250_SETUP, "The acquisition mode may select only reported Colorimeter");
        } else if (!row->available) return problem(o, CR250_SETUP, "Requested control is unavailable for this instrument");
        if (!cr250_finite(v)) return CR250_SETUP;
        if (row->kind == CM_CR250_CHOICE || row->kind == CM_CR250_PROFILE || row->kind == CM_CR250_POSITIVE_INTEGER) {
            if (v < 0 || v > CM_CR250_MAX_ID) return CR250_SETUP;
            if (row->kind != CM_CR250_POSITIVE_INTEGER && !choice(row, v)) return problem(o, CR250_SETUP, "Requested ID is absent from its current inventory");
        }
        put_value(target, field, v);
        if (row->kind != CM_CR250_CHOICE && row->kind != CM_CR250_PROFILE && !within(r, target, field))
            return problem(o, CR250_SETUP, "Requested numeric value is outside reported bounds");
        if (!argument(v, row->kind, encoded))
            return problem(o, CR250_SETUP, "Requested value has no exact supported decimal wire representation");
    }
    if ((request->present & exposure_bits) && !fixed(r, target, CM_CR250_EXPOSURE_MODE)
        && !named(r, target, CM_CR250_EXPOSURE_MODE, "Auto"))
        return problem(o, CR250_SETUP, "Exposure or range changes require a known Auto or Fixed exposure mode");
    if (fixed(r, target, CM_CR250_EXPOSURE_MODE) && (request->present & exposure_bits)) {
        if (!within(r, target, CM_CR250_EXPOSURE)) return problem(o, CR250_SETUP, "Fixed exposure requires a known exposure within positive reported bounds");
        if (p->dtype == instCR100 && (!fixed(r, target, CM_CR250_RANGE_MODE) || !named(r, target, CM_CR250_RANGE, "D")))
            return problem(o, CR250_SETUP, "CR-100 Fixed exposure requires Fixed Range D");
    }
    if ((request->present & CM_CR250_BIT(CM_CR250_EXPOSURE)) && !fixed(r, target, CM_CR250_EXPOSURE_MODE))
        return problem(o, CR250_SETUP, "Exposure milliseconds require Fixed exposure");
    if ((request->present & CM_CR250_BIT(CM_CR250_RANGE)) && !fixed(r, target, CM_CR250_RANGE_MODE))
        return problem(o, CR250_SETUP, "Range requires Fixed range mode");
    if ((request->present & (CM_CR250_BIT(CM_CR250_SYNC_MODE) | CM_CR250_BIT(CM_CR250_FREQUENCY)))
        && named(r, target, CM_CR250_SYNC_MODE, "Manual") && !within(r, target, CM_CR250_FREQUENCY))
        return problem(o, CR250_SETUP, "Manual synchronization requires a known frequency within positive reported bounds");
    if ((request->present & CM_CR250_BIT(CM_CR250_FREQUENCY)) && !named(r, target, CM_CR250_SYNC_MODE, "Manual"))
        return problem(o, CR250_SETUP, "Synchronization frequency requires Manual synchronization");
    if ((request->present & CM_CR250_BIT(CM_CR250_MAX_AUTO_EXPOSURE)) && !named(r, target, CM_CR250_EXPOSURE_MODE, "Auto"))
        return problem(o, CR250_SETUP, "Maximum Auto exposure requires Auto exposure");
    for (field = CM_CR250_MATRIX; field <= CM_CR250_MATCH; field++) {
        const char *mode = field == CM_CR250_MATRIX ? "Matrix" : "Match";
        if ((request->present & CM_CR250_BIT(field)) && !named(r, target, CM_CR250_CORRECTION_MODE, mode))
            return problem(o, CR250_SETUP, "A calibration profile requires its matching correction mode");
        if ((request->present & (CM_CR250_BIT(CM_CR250_CORRECTION_MODE) | CM_CR250_BIT(CM_CR250_MATRIX) | CM_CR250_BIT(CM_CR250_MATCH)))
            && named(r, target, CM_CR250_CORRECTION_MODE, mode)
            && !(target->present & CM_CR250_BIT(field))) return problem(o, CR250_SETUP, "Correction mode requires a known reported profile ID");
    }
    if ((request->present & CM_CR250_BIT(CM_CR250_ACCESSORY)) && (!(r->values.present & CM_CR250_BIT(CM_CR250_ACCESSORY))
        || request->accessory != r->values.accessory) && (named(r, target, CM_CR250_CORRECTION_MODE, "Matrix")
        || (request->present & CM_CR250_BIT(CM_CR250_MATRIX))))
        return problem(o, CR250_SETUP, "Accessory-specific Matrix selection requires a separate accessory change");
    for (i = CM_CR250_FILTER1; i <= CM_CR250_FILTER3; i++) {
        const char *name = label(r, target, i);
        if (!name || !strcmp(name, "None")) continue;
        for (j = CM_CR250_FILTER1; j < i; j++) if ((target->present & CM_CR250_BIT(j)) && value(target, i) == value(target, j))
            return problem(o, CR250_SETUP, "Active filter IDs must be distinct");
    }
    return CR250_OK;
}
static inst_code ready(inst *meter) {
    cr250 *p;
    if (!meter || (meter->dtype != instCR250 && meter->dtype != instCR100 && meter->dtype != instCR300)) return inst_bad_parameter;
    p = (cr250 *)meter;
    if (!p->gotcoms) return inst_no_coms;
    if (!p->inited) return inst_no_init;
    if (!p->engine.identified) return cr250_interp_code(CR250_NOT_IDENTIFIED);
    if (p->engine.uncertain) return cr250_interp_code(CR250_UNCERTAIN);
    return inst_ok;
}
static inst_code finish(control_operation *o, int ec) {
    inst_code rv;
    if (o->meter->raw_overflow && ec == CR250_OK) {
        ec = CR250_REPLY_TOO_LONG;
        o->meter->control_blocked |= o->report->attempted;
    }
    rv = ec == CR250_OK ? inst_ok : ec == CR250_SETUP || ec == CR250_BUDGET_TOO_SHORT
       ? inst_wrong_setup | ec : cr250_interp_code(ec);
    o->report->failed = ec != CR250_OK; o->report->status_code = (int)rv;
    o->report->blocked = o->meter->control_blocked;
    o->meter->raw_tracing = 0;
    o->report->raw_exchanges = o->meter->raw_exchanges;
    o->report->raw_exchange_count = o->meter->raw_count;
    o->report->trace_overflow = o->meter->raw_overflow;
    if (ec != CR250_OK && !o->report->problem[0])
        snprintf(o->report->problem, sizeof(o->report->problem), "%s", cr250_interp_text(ec));
    if (ec != CR250_OK) {
        snprintf(o->meter->capture.problem, sizeof(o->meter->capture.problem), "%s", o->report->problem);
        o->meter->capture.problem[sizeof(o->meter->capture.problem) - 1] = 0;
    }
    return rv;
}
inst_code cm_cr250_control_read(inst *meter, cm_cr250_control_report *report, double timeout_s) {
    control_operation o;
    inst_code rv;
    int ec;
    if (!report) return inst_bad_parameter;
    cm_cr250_control_report_dispose(report);
    if (!cr250_finite(timeout_s) || timeout_s <= 0) { report->failed = 1; report->status_code = inst_bad_parameter; return inst_bad_parameter; }
    if ((rv = ready(meter)) != inst_ok) { report->failed = 1; report->status_code = (int)rv; return rv; }
    o.meter = (cr250 *)meter; o.report = report; o.timeout_s = timeout_s; o.started_ms = o.meter->engine.now_ms(); o.phase = 0;
    amutex_lock(o.meter->lock);
    cm_cr250_trace_begin(o.meter);
    rv = ready(meter);
    if (rv == inst_ok) { ec = read_controls(&o); report->before = report->values; rv = finish(&o, ec); }
    else { report->failed = 1; report->status_code = (int)rv; report->blocked = o.meter->control_blocked;
        o.meter->raw_tracing = 0; }
    amutex_unlock(o.meter->lock);
    return rv;
}

inst_code cm_cr250_control_apply(inst *meter, const cm_cr250_control_request *request,
                                cm_cr250_control_report *report, double timeout_s) {
    static const int order[] = { CM_CR250_MODE, CM_CR250_ACCESSORY, CM_CR250_FILTER1,
        CM_CR250_FILTER2, CM_CR250_FILTER3, CM_CR250_RANGE_MODE, CM_CR250_RANGE,
        CM_CR250_EXPOSURE, CM_CR250_EXPOSURE_MODE, CM_CR250_MULTIPLIER,
        CM_CR250_FREQUENCY, CM_CR250_SYNC_MODE, CM_CR250_SPEED,
        CM_CR250_MATRIX, CM_CR250_MATCH, CM_CR250_CORRECTION_MODE, CM_CR250_MAX_AUTO_EXPOSURE };
    control_operation o;
    cm_cr250_control_request target;
    cr250 *p;
    inst_code rv;
    cr250_record saved_correction;
    char saved_accessory[CR250_MAX_VALUE], encoded[64], cmd[CR250_MAX_COMMAND + 1];
    int ec, i, field, correction_mode = 0, saved_mode = 0;
    int selector_changed = 0, setters = 0, correction_requested;
    long long correction_id = -1;
    if (!report) return inst_bad_parameter;
    cm_cr250_control_report_dispose(report);
    if (!request || !cr250_finite(timeout_s) || timeout_s <= 0) {
        report->failed = 1; report->status_code = inst_bad_parameter; return inst_bad_parameter;
    }
    report->requested = *request;
    if ((rv = ready(meter)) != inst_ok) { report->failed = 1; report->status_code = (int)rv; return rv; }
    p = (cr250 *)meter;
    o.meter = p; o.report = report; o.timeout_s = timeout_s; o.started_ms = p->engine.now_ms(); o.phase = 0;
    amutex_lock(p->lock);
    cm_cr250_trace_begin(p);
    if ((rv = ready(meter)) != inst_ok) { report->failed = 1; report->status_code = (int)rv;
        p->raw_tracing = 0; amutex_unlock(p->lock); return rv; }
    ec = read_controls(&o);
    report->before = report->values;
    if (ec == CR250_OK) ec = validate(&o, request, &target);
    if (ec != CR250_OK) { rv = finish(&o, ec); amutex_unlock(p->lock); return rv; }
    for (field = 0; field < CM_CR250_FIELDS; field++) if (request->present & CM_CR250_BIT(field)) {
        if (field == CM_CR250_MAX_AUTO_EXPOSURE || !(report->values.present & CM_CR250_BIT(field))
            || value(request, field) != value(&report->values, field)) {
            report->changed |= CM_CR250_BIT(field); setters++;
        }
    }
    correction_requested = !!(request->present & (CM_CR250_BIT(CM_CR250_CORRECTION_MODE)
        | CM_CR250_BIT(CM_CR250_MATRIX) | CM_CR250_BIT(CM_CR250_MATCH)));
    if (!correction_requested) { correction_mode = p->correction_mode; correction_id = p->correction.id; }
    else if (named(report, &target, CM_CR250_CORRECTION_MODE, "Matrix")) {
        correction_mode = 1; correction_id = target.matrixId;
    } else if (named(report, &target, CM_CR250_CORRECTION_MODE, "Match")) {
        correction_mode = 2; correction_id = target.matchId;
    } else if (!named(report, &target, CM_CR250_CORRECTION_MODE, "None")) {
        ec = problem(&o, CR250_SETUP, "The current correction mode is unavailable or outside None/Matrix/Match");
    }
    /* get_disptypesel's bounded current path uses at most four 3-second
     * transactions. It changes no native setting. Reserve it and all planned
     * SM/RS deadlines before changing either persistent settings or intent. */
    if (ec == CR250_OK && setters && remaining(&o) < setters * 6.0
        + (3 * CM_CR250_FIELDS + 1) * 3.0 + (correction_requested && (correction_mode || p->correction_mode) ? 12.0 : 0.0))
        ec = problem(&o, CR250_BUDGET_TOO_SHORT, "Control allowance cannot cover planned setters and calibration selection");
    if (ec != CR250_OK) { rv = finish(&o, ec); amutex_unlock(p->lock); return rv; }
    saved_mode = p->correction_mode; saved_correction = p->correction;
    strcpy(saved_accessory, p->correction_accessory);
    o.phase = 1;
    if (correction_requested && (correction_mode || saved_mode)) {
        p->control_observe = observe; p->control_context = &o;
        rv = cm_cr250_select_profile_locked(p, correction_mode, correction_id,
            correction_mode ? label(report, &target, correction_mode == 1 ? CM_CR250_MATRIX : CM_CR250_MATCH) : NULL);
        p->control_observe = NULL; p->control_context = NULL;
        if (rv != inst_ok) {
            inst_code selection_status = rv;
            finish(&o, problem(&o, CR250_SETUP, "Requested calibration could not be selected through its current Argyll catalogue"));
            report->status_code = (int)selection_status;
            amutex_unlock(p->lock); return selection_status;
        }
        selector_changed = 1;
    }
    /* Auto and no-correction are written first when requested, matching the
     * current direct recipe. Every other setter keeps its documented order. */
    for (i = -2; ec == CR250_OK && i < (int)(sizeof(order)/sizeof(order[0])); i++) {
        cr250_reply reply;
        if (i == -2) field = CM_CR250_EXPOSURE_MODE;
        else if (i == -1) field = CM_CR250_CORRECTION_MODE;
        else field = order[i];
        if (!(report->changed & CM_CR250_BIT(field))) continue;
        if (i == -2 && !named(report, &target, field, "Auto")) continue;
        if (i == -1 && !named(report, &target, field, "None")) continue;
        if (i >= 0 && ((field == CM_CR250_EXPOSURE_MODE && named(report, &target, field, "Auto"))
            || (field == CM_CR250_CORRECTION_MODE && named(report, &target, field, "None")))) continue;
        if (!(command_timeout(&o) > 0)) { ec = problem(&o, CR250_BUDGET_TOO_SHORT, "Control allowance expired before setter"); break; }
        if (report->reply_count >= CM_CR250_CONTROL_REPLIES) { ec = CR250_INTERNAL_ERROR; break; }
        if (!argument(value(&target, field), policies[field].kind, encoded)) { ec = CR250_INTERNAL_ERROR; break; }
        snprintf(cmd, sizeof(cmd), "SM %s %s", policies[field].key, encoded);
        report->attempted |= CM_CR250_BIT(field);
        ec = cr250_imp_setting(&p->engine, policies[field].key, encoded, &reply, command_timeout(&o));
        retain(&o, cmd, &reply, ec);
        if (ec != CR250_OK) break;
        report->acknowledged |= CM_CR250_BIT(field);
        if (field == CM_CR250_MAX_AUTO_EXPOSURE) continue;
        /* Profiles are read only in their active mode. Defer their confirmation
         * until UserCalibMode has been written and the fresh report is read. */
        if (field == CM_CR250_MATRIX || field == CM_CR250_MATCH) continue;
        snprintf(cmd, sizeof(cmd), "RS %s", field >= CM_CR250_FILTER1 && field <= CM_CR250_FILTER3 ? "Filter" : policies[field].key);
        ec = query(&o, cmd, &reply);
        if (ec != CR250_OK) break;
        report->values.present &= ~CM_CR250_BIT(field);
        ec = current_value(&o, field, reply.value);
        if (ec != CR250_OK) break;
        if (!(report->values.present & CM_CR250_BIT(field)) || value(&report->values, field) != value(&target, field)) {
            ec = problem(&o, CR250_SETUP, "Setting readback differs from the requested target"); break;
        }
        report->confirmed |= CM_CR250_BIT(field);
    }
    if (ec == CR250_OK) {
        report->confirmed &= ~request->present;
        o.phase = 2;
        ec = read_controls(&o);
    }
    if (ec == CR250_OK) for (field = 0; field < CM_CR250_FIELDS; field++) {
        if (!(request->present & CM_CR250_BIT(field)) || field == CM_CR250_MAX_AUTO_EXPOSURE) continue;
        if (!(report->values.present & CM_CR250_BIT(field)) || value(&report->values, field) != value(request, field)) {
            /* An observed final drift creates debt even when this field needed
             * no write initially. Unavailable facts do not prove a change. */
            if (report->values.present & CM_CR250_BIT(field)) p->control_blocked |= CM_CR250_BIT(field);
            if (ec == CR250_OK) ec = problem(&o, CR250_SETUP, "Final current settings differ from the requested target");
        } else report->confirmed |= CM_CR250_BIT(field);
    }
    if (ec == CR250_OK) {
        /* Capture honors explicit native intent while keeping ordinary Argyll
         * defaults when this application control owner was never called. */
        const char *sync = label(report, &report->values, CM_CR250_SYNC_MODE);
        if (fixed(report, &report->values, CM_CR250_EXPOSURE_MODE)) p->control_exposure_mode = 2;
        else if (named(report, &report->values, CM_CR250_EXPOSURE_MODE, "Auto")) p->control_exposure_mode = 1;
        if (sync) snprintf(p->control_sync_name, sizeof(p->control_sync_name), "%s", sync);
        if (request->present & CM_CR250_BIT(CM_CR250_MULTIPLIER)) p->averages = 0;
        if (request->present & (CM_CR250_BIT(CM_CR250_SYNC_MODE) | CM_CR250_BIT(CM_CR250_FREQUENCY))) p->refresh_hz = 0;
    } else if (selector_changed) {
        /* No hardware rollback is attempted. Failed intent must not become a
         * successful future recipe; actual partial settings remain in receipts. */
        p->correction_mode = saved_mode; p->correction = saved_correction;
        strcpy(p->correction_accessory, saved_accessory);
    }
    if (ec != CR250_OK) p->control_blocked |= report->attempted;
    else if ((request->present & p->control_blocked) == p->control_blocked
        && ((report->confirmed | (report->acknowledged & CM_CR250_BIT(CM_CR250_MAX_AUTO_EXPOSURE)))
            & p->control_blocked) == p->control_blocked) p->control_blocked = 0;
    rv = finish(&o, ec);
    amutex_unlock(p->lock);
    return rv;
}
