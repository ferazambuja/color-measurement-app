/* Application-only PR controls over the contribution's protocol engine.
 * Copyright 2026 Fernando Voltolini de Azambuja. GPL-2.0-or-later.
 * The selected instrument, lock, parser and clock retain their existing owners.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "pr655.h"

static void apertures_dispose(cm_pr655_aperture *rows, int count) {
    int i;
    if (!rows) return;
    for (i = 0; i < count; i++) free(rows[i].name);
    free(rows);
}
static int apertures_copy(cm_pr655_aperture **dest, const cm_pr655_aperture *rows, int count) {
    cm_pr655_aperture *copy = NULL;
    int i;
    if (count) {
        copy = calloc(count, sizeof(*copy));
        if (!copy) return PR655_INTERNAL_ERROR;
        for (i = 0; i < count; i++) {
            copy[i].id = rows[i].id; copy[i].bandwidth_nm = rows[i].bandwidth_nm;
            copy[i].name = malloc(strlen(rows[i].name) + 1);
            if (!copy[i].name) { apertures_dispose(copy, count); return PR655_INTERNAL_ERROR; }
            strcpy(copy[i].name, rows[i].name);
        }
    }
    *dest = copy; return PR655_OK;
}
static void report_dispose(cm_pr655_control_report *r) {
    pr655_accessories_dispose(r->accessories, r->accessory_count);
    apertures_dispose(r->apertures, r->aperture_storage_count);
    memset(r, 0, sizeof(*r));
}
static void inventory_rows_clear(cm_pr655_control_report *r) {
    pr655_accessories_dispose(r->accessories, r->accessory_count);
    apertures_dispose(r->apertures, r->aperture_storage_count);
    r->accessories = NULL; r->apertures = NULL;
    r->accessory_count = r->aperture_storage_count = 0;
    r->accessory_list_valid = r->aperture_list_valid = 0;
}
static int report_copy(cm_pr655_control_report *dest, const cm_pr655_control_report *source) {
    pr655_accessory *accessories = NULL;
    cm_pr655_aperture *apertures = NULL;
    int aperture_count = source->aperture_list_valid ? source->aperture_count : 0;
    int code;
    if (dest == source) return PR655_OK;
    if ((code = pr655_accessories_copy(&accessories, source->accessories, source->accessory_count)) != PR655_OK)
        return code;
    if ((code = apertures_copy(&apertures, source->apertures, aperture_count)) != PR655_OK) {
        pr655_accessories_dispose(accessories, source->accessory_count); return code;
    }
    report_dispose(dest); *dest = *source;
    dest->accessories = accessories; dest->apertures = apertures;
    dest->aperture_storage_count = aperture_count;
    return PR655_OK;
}
static int same_accessories(const cm_pr655_control_report *a, const cm_pr655_control_report *b) {
    int i;
    if (a->accessory_list_valid != b->accessory_list_valid || a->inventory_counts_valid != b->inventory_counts_valid
     || a->accessory_count != b->accessory_count || a->aperture_count != b->aperture_count) return 0;
    for (i = 0; i < a->accessory_count; i++) {
        const pr655_accessory *x = &a->accessories[i], *y = &b->accessories[i];
        if (x->id != y->id || strcmp(x->name, y->name) || strcmp(x->type, y->type)
         || strcmp(x->photometry, y->photometry) || strcmp(x->radiometry, y->radiometry)) return 0;
    }
    return 1;
}
static int same_apertures(const cm_pr655_control_report *a, const cm_pr655_control_report *b) {
    int i;
    if (a->aperture_list_valid != b->aperture_list_valid || a->aperture_count != b->aperture_count) return 0;
    if (!a->aperture_list_valid) return 1;
    for (i = 0; i < a->aperture_count; i++)
        if (a->apertures[i].id != b->apertures[i].id
         || a->apertures[i].bandwidth_nm != b->apertures[i].bandwidth_nm
         || strcmp(a->apertures[i].name, b->apertures[i].name)) return 0;
    return 1;
}
static void trace_dispose(pr655 *p) {
    int i;
    for (i = 0; i < p->host.exchange_count; i++) pr655_raw_dispose(&p->host.exchanges[i].raw);
    memset(p->host.exchanges, 0, sizeof(p->host.exchanges));
    p->host.exchange_count = p->host.trace_bytes = p->host.overflow = 0;
}

static int family(const inst *p) {
    return p && (p->dtype == instPR655 || p->dtype == instPR670);
}
static inst_code mapped(int code) {
    if (!code) return inst_ok;
    if (code == CM_PR655_ALLOWANCE || code == PR655_BAD_SETUP || code == PR655_REFUSED_COMMAND)
        return inst_wrong_setup | code;
    if (code == PR655_TIMEOUT || code == PR655_COMS_FAIL) return inst_coms_fail | code;
    return inst_protocol_error | code;
}
static double value(const cm_pr655_control_request *v, int field) {
    switch (field) {
    case CM_PR655_EXPOSURE: return v->exposureMilliseconds;
    case CM_PR655_CYCLES: return v->cycles;
    case CM_PR655_SYNC: return v->syncMode;
    case CM_PR655_FREQUENCY: return v->syncFrequencyHz;
    case CM_PR655_PRIMARY: return v->primaryAccessoryId;
    case CM_PR655_ADDON1: return v->addon1Id;
    case CM_PR655_ADDON2: return v->addon2Id;
    case CM_PR655_ADDON3: return v->addon3Id;
    case CM_PR655_APERTURE: return v->apertureId;
    case CM_PR655_SENSITIVITY: return v->sensitivityMode;
    case CM_PR655_DARK: return v->smartDark;
    case CM_PR655_UNITS: return v->photometricUnits;
    case CM_PR655_OBSERVER: return v->instrumentObserver;
    case CM_PR655_SHUTTER: return v->shutterMode;
    case CM_PR655_SPEED: return v->speedModeCode;
    default: return NAN;
    }
}
static void assign(cm_pr655_control_request *v, int field, double n) {
    int i;
    if (field == CM_PR655_FREQUENCY) {
        v->syncFrequencyHz = n; v->present |= CM_PR655_BIT(field); return;
    }
    if (field >= CM_PR655_PRIMARY && field <= CM_PR655_APERTURE) {
        long long id = (long long)n;
        switch (field) {
        case CM_PR655_PRIMARY: v->primaryAccessoryId = id; break;
        case CM_PR655_ADDON1: v->addon1Id = id; break;
        case CM_PR655_ADDON2: v->addon2Id = id; break;
        case CM_PR655_ADDON3: v->addon3Id = id; break;
        case CM_PR655_APERTURE: v->apertureId = id; break;
        }
        v->present |= CM_PR655_BIT(field); return;
    }
    i = (int)n;
    switch (field) {
    case CM_PR655_EXPOSURE: v->exposureMilliseconds = i; break;
    case CM_PR655_CYCLES: v->cycles = i; break;
    case CM_PR655_SYNC: v->syncMode = i; break;
    case CM_PR655_FREQUENCY: v->syncFrequencyHz = n; break;
    case CM_PR655_PRIMARY: v->primaryAccessoryId = i; break;
    case CM_PR655_ADDON1: v->addon1Id = i; break;
    case CM_PR655_ADDON2: v->addon2Id = i; break;
    case CM_PR655_ADDON3: v->addon3Id = i; break;
    case CM_PR655_APERTURE: v->apertureId = i; break;
    case CM_PR655_SENSITIVITY: v->sensitivityMode = i; break;
    case CM_PR655_DARK: v->smartDark = i; break;
    case CM_PR655_UNITS: v->photometricUnits = i; break;
    case CM_PR655_OBSERVER: v->instrumentObserver = i; break;
    case CM_PR655_SHUTTER: v->shutterMode = i; break;
    case CM_PR655_SPEED: v->speedModeCode = i; break;
    }
    v->present |= CM_PR655_BIT(field);
}
static void reconcile_confirmed(pr655 *p, cm_pr655_control_report *r) {
    const cm_pr655_control_request *v = &r->values;
    int field;
    if (!r->setup_valid) { r->confirmed = 0; return; }
    /* Confirmation describes the current report, including failed later
     * readbacks. An observed change of an explicitly confirmed field creates
     * recovery debt; unavailable values cannot establish such a change. */
    for (field = 0; field < CM_PR655_FIELDS; field++)
        if ((r->confirmed & CM_PR655_BIT(field))
         && (!(v->present & CM_PR655_BIT(field)) || value(v, field) != value(&r->requested, field))) {
            if (v->present & r->requested.present & CM_PR655_BIT(field))
                p->host.control_blocked |= CM_PR655_BIT(field);
            r->confirmed &= ~CM_PR655_BIT(field);
        }
}
static void adopt_observed(cm_pr655_control_report *r, cm_pr655_control_report *after) {
    inventory_rows_clear(r);
    r->values = after->values; r->setup = after->setup; r->setup_valid = after->setup_valid;
    r->inventory_counts_valid = after->inventory_counts_valid;
    r->reported_accessory_count = after->reported_accessory_count;
    r->inventory_counts_error = after->inventory_counts_error;
    r->accessory_list_error = after->accessory_list_error; r->aperture_list_error = after->aperture_list_error;
    r->accessory_count = after->accessory_count; r->accessory_list_valid = after->accessory_list_valid;
    r->accessories = after->accessories; after->accessories = NULL; after->accessory_count = 0;
    r->aperture_count = after->aperture_count; r->aperture_list_valid = after->aperture_list_valid;
    r->apertures = after->apertures; after->apertures = NULL;
    r->aperture_storage_count = after->aperture_storage_count; after->aperture_storage_count = 0;
}
static int observed(pr655 *p, cm_pr655_control_report *r) {
    const pr655_setup *s = &p->engine.setup;
    cm_pr655_control_request *v = &r->values;
    pr655_accessory *rows = NULL;
    int code;
    memset(v, 0, sizeof(*v));
    r->setup_valid = p->engine.setup_valid;
    if (!r->setup_valid) {
        inventory_rows_clear(r);
        r->aperture_count = r->inventory_counts_valid = 0;
        r->reported_accessory_count = r->inventory_counts_error = r->accessory_list_error = r->aperture_list_error = 0;
        r->confirmed = 0; return PR655_OK;
    }
    r->inventory_counts_valid = p->engine.inventory_counts_valid;
    r->reported_accessory_count = p->engine.reported_accessory_count;
    r->inventory_counts_error = p->engine.inventory_counts_error;
    r->accessory_list_error = p->engine.accessory_list_error;
    r->aperture_count = p->engine.aperture_count;
    if ((code = pr655_accessories_copy(&rows, p->engine.accessories, p->engine.accessory_count)) != PR655_OK) {
        inventory_rows_clear(r);
        r->aperture_list_error = 0;
        r->setup_valid = 0; r->confirmed = 0; return code;
    }
    r->setup = *s;
    assign(v, CM_PR655_EXPOSURE, s->exposure_ms);
    assign(v, CM_PR655_CYCLES, s->cycles);
    assign(v, CM_PR655_SYNC, s->sync_mode);
    assign(v, CM_PR655_FREQUENCY, s->frequency_hz);
    assign(v, CM_PR655_PRIMARY, s->primary);
    assign(v, CM_PR655_ADDON1, s->addon[0]);
    assign(v, CM_PR655_ADDON2, s->addon[1]);
    assign(v, CM_PR655_ADDON3, s->addon[2]);
    assign(v, CM_PR655_APERTURE, s->aperture);
    if (s->sensitivity_known) assign(v, CM_PR655_SENSITIVITY, s->extended);
    assign(v, CM_PR655_DARK, s->dark_mode);
    assign(v, CM_PR655_UNITS, s->units);
    assign(v, CM_PR655_OBSERVER, s->observer);
    /* SZ cannot be observed from D601/D602. It never enters values.present. */
    pr655_accessories_dispose(r->accessories, r->accessory_count);
    r->accessories = rows; r->accessory_count = p->engine.accessory_count;
    if (r->aperture_list_valid && (!p->engine.inventory_counts_valid
     || r->aperture_storage_count != p->engine.aperture_count)) {
        apertures_dispose(r->apertures, r->aperture_storage_count);
        r->apertures = NULL; r->aperture_list_valid = 0; r->aperture_storage_count = 0;
    }
    r->accessory_list_valid = p->engine.accessory_list_valid;
    reconcile_confirmed(p, r);
    return PR655_OK;
}
static double left(pr655 *p) {
    unsigned int spent = p->engine.now() - p->engine.host_started_ms;
    unsigned int limit = p->engine.host_milliseconds;
    return spent >= limit ? 0.0 : (limit - spent) / 1000.0;
}
static double transaction(pr655 *p) {
    double seconds = left(p);
    return seconds < 10.0 ? seconds : 10.0;
}
static int failure(cm_pr655_control_report *r, int code, const char *problem) {
    r->failed = 1; r->status_code = code;
    snprintf(r->problem, sizeof(r->problem), "%s", problem);
    return code;
}
static int read_apertures(pr655 *p, cm_pr655_control_report *r) {
    int code;
    double seconds = transaction(p);
    apertures_dispose(r->apertures, r->aperture_storage_count);
    r->apertures = NULL; r->aperture_list_valid = 0; r->aperture_storage_count = 0;
    r->aperture_list_error = 0;
    if (!r->inventory_counts_valid) return PR655_OK;
    if (r->aperture_count) {
        r->apertures = calloc(r->aperture_count, sizeof(*r->apertures));
        if (!r->apertures) return PR655_INTERNAL_ERROR;
        r->aperture_storage_count = r->aperture_count;
    }
    if (!(seconds > 0)) {
        apertures_dispose(r->apertures, r->aperture_storage_count);
        r->apertures = NULL; r->aperture_storage_count = 0; return PR655_TIMEOUT;
    }
    code = cm_pr655_engine_apertures(&p->engine, r->apertures, seconds);
    r->aperture_list_valid = code == PR655_OK;
    if (code) {
        r->aperture_list_error = p->engine.device_error;
        apertures_dispose(r->apertures, r->aperture_storage_count);
        r->apertures = NULL; r->aperture_storage_count = 0;
        if (!p->host.overflow && p->engine.state == pr655_ready && (p->engine.device_error == -1000
            || p->engine.device_error == -1035 || p->engine.device_error == -2000)) return PR655_OK;
    }
    return code;
}
static int read_report(pr655 *p, cm_pr655_control_report *r, int apertures) {
    int code;
    double seconds = transaction(p);
    if (!(seconds > 0)) return PR655_TIMEOUT;
    code = pr655_imp_read_setup(&p->engine, seconds);
    { int snapshot = observed(p, r); if (!code) code = snapshot; }
    if (!code && p->host.overflow) code = CM_PR655_TRACE_LIMIT;
    if (code != PR655_OK) return code;
    if (apertures) code = read_apertures(p, r);
    return code;
}
static int inventory_id(const cm_pr655_control_report *r, int field, long long id) {
    int i;
    if (id < 0) return 0;
    if (field == CM_PR655_APERTURE) {
        if (!r->aperture_list_valid) return 0;
        for (i = 0; i < r->aperture_count; i++) if (r->apertures[i].id == id) return 1;
    } else {
        const char *kind = field == CM_PR655_PRIMARY ? "Primary" : "Addon";
        if (!r->accessory_list_valid) return 0;
        for (i = 0; i < r->accessory_count; i++)
            if (r->accessories[i].id == id && !strcmp(r->accessories[i].type, kind)) return 1;
    }
    return 0;
}
static int validate(pr655 *p, const cm_pr655_control_request *q,
                    const cm_pr655_control_report *r, cm_pr655_control_request *target) {
    int field, i, j;
    long long addons[3];
    *target = r->values;
    if (q->present & ~CM_PR655_ALL) return 0;
    if (p->engine.model == 655 && q->present & (CM_PR655_BIT(CM_PR655_APERTURE)
        | CM_PR655_BIT(CM_PR655_SENSITIVITY) | CM_PR655_BIT(CM_PR655_DARK)
        | CM_PR655_BIT(CM_PR655_SPEED))) return 0;
    for (field = 0; field < CM_PR655_FIELDS; field++) if (q->present & CM_PR655_BIT(field)) {
        double n = value(q, field);
        double maximum = field >= CM_PR655_PRIMARY && field <= CM_PR655_APERTURE ? 9007199254740991.0 : 2147483647.0;
        if (!isfinite(n) || n != floor(n) || n < 0 || n > maximum) return 0;
        if (field == CM_PR655_EXPOSURE && (n > (p->engine.model == 655 ? 6000 : 30000)
          || (n != 0 && n < (p->engine.model == 655 ? 3 : 6)))) return 0;
        if (field == CM_PR655_CYCLES && (n < 1 || n > 99)) return 0;
        if (field == CM_PR655_SYNC && n != 0 && n != 1 && n != 3) return 0;
        if (field == CM_PR655_FREQUENCY && (n < 20 || n > 400)) return 0;
        if (field >= CM_PR655_PRIMARY && field <= CM_PR655_APERTURE
         && !inventory_id(r, field, (long long)n)) return 0;
        if ((field == CM_PR655_SENSITIVITY || field == CM_PR655_DARK
          || field == CM_PR655_UNITS || field == CM_PR655_SHUTTER) && n != 0 && n != 1) return 0;
        if (field == CM_PR655_OBSERVER && n != 2 && n != 10) return 0;
        if (field == CM_PR655_SPEED && n > 3) return 0;
        assign(target, field, n);
    }
    addons[0] = target->addon1Id; addons[1] = target->addon2Id; addons[2] = target->addon3Id;
    for (i = 0; i < 3; i++) for (j = 0; j < i; j++)
        if (addons[i] >= 0 && addons[i] == addons[j]) return 0;
    if (target->exposureMilliseconds > 6000
     && (!(target->present & CM_PR655_BIT(CM_PR655_SENSITIVITY)) || target->sensitivityMode != 1)) return 0;
    if ((q->present & CM_PR655_BIT(CM_PR655_SENSITIVITY)) && q->sensitivityMode == 0
     && target->exposureMilliseconds > 6000) return 0;
    if ((q->present & CM_PR655_BIT(CM_PR655_FREQUENCY)) && target->syncMode != 3) return 0;
    if (target->syncMode == 3 && (!isfinite(target->syncFrequencyHz)
     || target->syncFrequencyHz < 20 || target->syncFrequencyHz > 400)) return 0;
    return 1;
}
/* Every readback may change only the requested field, apart from the narrowly
 * verified PR-670 SP/aperture coupling. Numeric gain and the unlabelled setup
 * codes remain facts, not aliases for setter codes. */
static int collateral(const cm_pr655_control_report *before,
                      const cm_pr655_control_report *after, int changed, int allow_aperture) {
    int field;
    for (field = 0; field < CM_PR655_FIELDS; field++) {
        if (field == changed || field == CM_PR655_SHUTTER || field == CM_PR655_SPEED) continue;
        if (field == CM_PR655_APERTURE && allow_aperture) continue;
        if ((before->values.present & CM_PR655_BIT(field)) != (after->values.present & CM_PR655_BIT(field))) return 0;
        if ((before->values.present & CM_PR655_BIT(field))
         && value(&before->values, field) != value(&after->values, field)) return 0;
    }
    if (changed != CM_PR655_SPEED && (before->setup.gain != after->setup.gain
        || strcmp(before->setup.text[8], after->setup.text[8]))) return 0;
    if (changed != CM_PR655_EXPOSURE && (before->setup.exposure_mode != after->setup.exposure_mode
     || before->setup.adaptive_known != after->setup.adaptive_known
     || before->setup.adaptive != after->setup.adaptive)) return 0;
    if (changed != CM_PR655_SENSITIVITY && before->setup.capture_mode != after->setup.capture_mode) return 0;
    if (changed != CM_PR655_SYNC && changed != CM_PR655_FREQUENCY
     && before->setup.sync_value != after->setup.sync_value) return 0;
    if (!same_accessories(before, after)) return 0;
    return 1;
}
static int begin_control(pr655 *p, cm_pr655_control_report *r, double seconds) {
    report_dispose(&p->host.control);
    memset(r, 0, sizeof(*r));
    trace_dispose(p);
    p->host.tracing = 0; p->engine.host_active = 0;
    if (!isfinite(seconds) || seconds <= 0 || seconds > 86400)
        return failure(r, PR655_BAD_SETUP, "Control allowance must be finite, positive and at most 24 hours");
    if (!p->inited || !p->gotcoms || p->engine.state != pr655_ready)
        return failure(r, PR655_UNCERTAIN, "Controls require the current clean identified connection");
    p->host.tracing = 1;
    p->engine.host_started_ms = p->engine.now();
    p->engine.host_milliseconds = (unsigned int)ceil(seconds * 1000);
    p->engine.host_active = 1;
    return PR655_OK;
}
static inst_code end_control(pr655 *p, cm_pr655_control_report *r, int code) {
    code = cm_pr655_engine_finish(&p->engine, code);
    if (!code && p->host.overflow) code = CM_PR655_TRACE_LIMIT;
    if (code && !r->failed) failure(r, code, "Native control transaction failed; retained raw replies describe the attempt");
    p->host.tracing = 0; p->engine.host_active = 0;
    r->raw_exchange_count = p->host.exchange_count;
    if (code) p->host.control_blocked |= r->attempted;
    else if ((r->requested.present & p->host.control_blocked) == p->host.control_blocked
        && ((r->confirmed | (r->acknowledged & (CM_PR655_BIT(CM_PR655_SHUTTER)
            | CM_PR655_BIT(CM_PR655_SPEED)))) & p->host.control_blocked) == p->host.control_blocked)
        p->host.control_blocked = 0;
    r->blocked = p->host.control_blocked;
    r->status_code = (int)mapped(code);
    p->host.control = *r;
    return mapped(code);
}
static void initial_report(cm_pr655_control_report *r) {
    r->before = r->values;
    r->setup_before_valid = r->setup_valid;
    if (r->setup_before_valid) r->setup_before = r->setup;
}
inst_code cm_pr655_control_read(inst *meter, cm_pr655_control_report *r, double seconds) {
    pr655 *p;
    int code;
    if (!family(meter) || !r) return inst_bad_parameter;
    p = (pr655 *)meter; amutex_lock(p->lock);
    code = begin_control(p, r, seconds);
    if (!code) code = read_report(p, r, 1);
    initial_report(r);
    { inst_code result = end_control(p, r, code); amutex_unlock(p->lock); return result; }
}
inst_code cm_pr655_control_apply(inst *meter, const cm_pr655_control_request *q,
                               cm_pr655_control_report *r, double seconds) {
    static const int ordinary[] = {CM_PR655_EXPOSURE, CM_PR655_SENSITIVITY,
        CM_PR655_CYCLES, CM_PR655_FREQUENCY, CM_PR655_SYNC, CM_PR655_PRIMARY,
        CM_PR655_ADDON1, CM_PR655_ADDON2, CM_PR655_ADDON3, CM_PR655_APERTURE,
        CM_PR655_DARK, CM_PR655_UNITS, CM_PR655_OBSERVER, CM_PR655_SHUTTER, CM_PR655_SPEED};
    cm_pr655_control_request target;
    cm_pr655_control_report previous = {0}, after = {0};
    pr655 *p;
    int code, i, field, order[CM_PR655_FIELDS], count = 0, acknowledged, coupled_primary = 0;
    if (!family(meter) || !q || !r) return inst_bad_parameter;
    p = (pr655 *)meter; amutex_lock(p->lock);
    code = begin_control(p, r, seconds); r->requested = *q;
    if (!code) code = read_report(p, r, 1);
    initial_report(r);
    if (!code && !validate(p, q, r, &target))
        code = failure(r, PR655_REFUSED_COMMAND, "Complete target violates model, native inventory, exposure or synchronization constraints");
    if (!code && IMODETST(p->mode, inst_mode_emis_nonadaptive) && target.exposureMilliseconds == 0)
        code = failure(r, PR655_REFUSED_COMMAND, "Native Adaptive exposure conflicts with the requested nonadaptive Argyll mode");
    if (!code && (q->present & (CM_PR655_BIT(CM_PR655_SYNC) | CM_PR655_BIT(CM_PR655_FREQUENCY)))
     && ((IMODETST(p->mode, inst_mode_emis_norefresh_ovd) && target.syncMode != 0)
      || (IMODETST(p->mode, inst_mode_emis_refresh_ovd) && target.syncMode != 1)))
        code = failure(r, PR655_REFUSED_COMMAND, "Native synchronization target conflicts with the requested Argyll mode");
    if (!code) {
        memcpy(order, ordinary, sizeof(order));
        if (target.exposureMilliseconds > 6000) { order[0] = CM_PR655_SENSITIVITY; order[1] = CM_PR655_EXPOSURE; }
        /* Leaving User sync disables it before any other setting; entering it
         * installs the valid frequency before enabling User sync. */
        if (target.syncMode != 3) { order[3] = CM_PR655_SYNC; order[4] = CM_PR655_FREQUENCY; }
        {
            int slot, j, at, found;
            long long active[3] = {r->values.addon1Id, r->values.addon2Id, r->values.addon3Id};
            unsigned int done = 0;
            for (at = 0; at < 3; at++) {
                found = -1;
                for (slot = 0; slot < 3; slot++) if (!(done & (1u << slot))) {
                    long long wanted = (q->present & CM_PR655_BIT(CM_PR655_ADDON1 + slot))
                        ? (long long)value(q, CM_PR655_ADDON1 + slot) : active[slot];
                    for (j = 0; j < 3; j++) if (j != slot && wanted >= 0 && active[j] == wanted) break;
                    if (j == 3) { found = slot; break; }
                }
                if (found < 0) { code = failure(r, PR655_REFUSED_COMMAND,
                    "Add-on swap requires an unsupported clearing transaction; no settings were written"); break; }
                order[6 + at] = CM_PR655_ADDON1 + found; done |= 1u << found;
                if (q->present & CM_PR655_BIT(CM_PR655_ADDON1 + found))
                    active[found] = (long long)value(q, CM_PR655_ADDON1 + found);
            }
        }
        for (i = 0; i < CM_PR655_FIELDS; i++) if (q->present & CM_PR655_BIT(i)) {
            if (!(r->values.present & CM_PR655_BIT(i)) || value(q, i) != value(&r->values, i)) {
                r->changed |= CM_PR655_BIT(i); count++;
            } else r->confirmed |= CM_PR655_BIT(i);
        }
        coupled_primary = p->engine.model == 670
            && (r->changed & CM_PR655_BIT(CM_PR655_PRIMARY))
            && (q->present & CM_PR655_BIT(CM_PR655_APERTURE));
        /* SP may select an aperture automatically. Reserve its fresh D117 and
         * a possible SF even when the aperture initially matched the target. */
        if (!code && count && left(p) < 10.0 * (count + 2 + (coupled_primary ? 1
            + !(r->changed & CM_PR655_BIT(CM_PR655_APERTURE)) : 0)))
            code = failure(r, CM_PR655_ALLOWANCE, "Remaining control allowance cannot cover every setter/readback and final inventory");
    }
    for (i = 0; !code && i < CM_PR655_FIELDS; i++) {
        field = order[i];
        if (!(r->changed & CM_PR655_BIT(field))) continue;
        if (field == CM_PR655_APERTURE && (r->confirmed & CM_PR655_BIT(field))) continue;
        if ((code = report_copy(&previous, r)) != PR655_OK) break;
        r->attempted |= CM_PR655_BIT(field);
        code = cm_pr655_engine_set(&p->engine, field, (long long)value(q, field), &acknowledged, transaction(p));
        if (acknowledged) r->acknowledged |= CM_PR655_BIT(field);
        { int snapshot = observed(p, r); if (!code) code = snapshot; }
        /* An optional D112 refusal returns OK even when its copy filled the
         * trace; no further setter may follow a readback that was not kept. */
        if (!code && p->host.overflow) code = CM_PR655_TRACE_LIMIT;
        if (field == CM_PR655_PRIMARY && r->setup_valid
         && r->values.apertureId != previous.values.apertureId) {
            /* Collateral aperture drift creates recovery debt without claiming
             * that SF was attempted or acknowledged. Clear stale confirmation
             * before any further verification can fail. */
            r->confirmed &= ~CM_PR655_BIT(CM_PR655_APERTURE);
            p->host.control_blocked |= CM_PR655_BIT(CM_PR655_APERTURE);
            if (q->present & CM_PR655_BIT(CM_PR655_APERTURE))
                r->changed |= CM_PR655_BIT(CM_PR655_APERTURE);
        }
        if (code) break;
        if (field == CM_PR655_SHUTTER) continue;
        if (field == CM_PR655_SPEED) {
            /* The manual's SG labels conflict and do not map to D601 Gain.
             * Keep the ACK and fresh Gain separately; never invent a confirmed
             * speed code. Every unrelated material setting must still agree. */
            if (!collateral(&previous, r, field, 0)) {
                p->engine.state = pr655_quarantined;
                code = failure(r, PR655_READBACK_ERROR, "Speed command changed an unrelated material condition");
            }
            continue;
        }
        if (!(r->values.present & CM_PR655_BIT(field)) || value(&r->values, field) != value(q, field)
         || !collateral(&previous, r, field, field == CM_PR655_PRIMARY && coupled_primary)) {
            p->engine.state = pr655_quarantined;
            code = failure(r, PR655_READBACK_ERROR, "Readback differs from target or changed an unrequested material condition");
        } else r->confirmed |= CM_PR655_BIT(field);
        if (!code && field == CM_PR655_PRIMARY && coupled_primary) {
            code = read_apertures(p, r);
            if (!code && (!inventory_id(r, CM_PR655_APERTURE, r->values.apertureId)
             || !inventory_id(r, CM_PR655_APERTURE, q->apertureId)
             || !previous.aperture_list_valid
             || !same_apertures(&previous, r))) {
                p->engine.state = pr655_quarantined;
                code = failure(r, PR655_READBACK_ERROR, "Primary accessory changed aperture inventory or reported an unknown aperture");
            }
            if (!code && r->values.apertureId == q->apertureId)
                r->confirmed |= CM_PR655_BIT(CM_PR655_APERTURE);
        }
    }
    if (!code && r->changed) {
        int final_started = 0;
        code = report_copy(&previous, r);
        if (!code) { final_started = 1; code = read_report(p, &after, 1); }
        if (after.setup_valid && after.values.apertureId != previous.values.apertureId) {
            /* Final-only aperture drift also requires explicit recovery, even
             * when SP left the initial target intact and no SF was sent. */
            p->host.control_blocked |= CM_PR655_BIT(CM_PR655_APERTURE);
            r->confirmed &= ~CM_PR655_BIT(CM_PR655_APERTURE);
        }
        if (!code && (!collateral(&previous, &after, -1, 0)
         || previous.aperture_count != after.aperture_count
         || !same_apertures(&previous, &after))) {
            p->engine.state = pr655_quarantined;
            code = failure(r, PR655_READBACK_ERROR, "Material context changed during final control verification");
        }
        /* The final report already owns one complete observation snapshot.
         * Adopt its latest facts even when comparison refused the transaction;
         * retain the initial report, request and write masks independently. */
        if (final_started) {
            adopt_observed(r, &after); reconcile_confirmed(p, r);
        }
    }
    if (!code) {
        /* Explicit native controls own these settings for the following read.
         * Do not let older recipe intent overwrite a confirmed target. */
        if (q->present & CM_PR655_BIT(CM_PR655_CYCLES)) p->averages = 0;
        if (q->present & (CM_PR655_BIT(CM_PR655_SYNC) | CM_PR655_BIT(CM_PR655_FREQUENCY))) {
            p->refresh_hz = 0.0;
            p->host.explicit_sync = 1;
        }
    }
    report_dispose(&previous); report_dispose(&after);
    { inst_code result = end_control(p, r, code); amutex_unlock(p->lock); return result; }
}

int cm_pr655_observe(void *context, const char *command, const pr655_raw *raw, int error, int complete) {
    pr655 *p = (pr655 *)context;
    cm_pr655_exchange *e;
    if (!p->host.tracing) return PR655_OK;
    if (p->host.exchange_count >= CM_PR655_MAX_EXCHANGES
     || raw->length > CM_PR655_MAX_TRACE_BYTES - p->host.trace_bytes) {
        p->host.overflow = 1; return CM_PR655_TRACE_LIMIT;
    }
    e = &p->host.exchanges[p->host.exchange_count];
    snprintf(e->command, sizeof(e->command), "%s", command);
    if (pr655_raw_copy(&e->raw, raw) != PR655_OK) {
        p->host.overflow = 1; return CM_PR655_TRACE_LIMIT;
    }
    p->host.exchange_count++; p->host.trace_bytes += raw->length;
    e->error = error; e->complete = complete;
    return PR655_OK;
}
void cm_pr655_read_allowance(inst *meter, double seconds) {
    if (family(meter)) ((pr655 *)meter)->host.allowance_s = seconds;
}
void cm_pr655_capture_metadata(inst *meter, int enabled) {
    if (family(meter)) ((pr655 *)meter)->host.capture_metadata = !!enabled;
}
void cm_pr655_capture_reset(inst *meter) {
    pr655 *p = (pr655 *)meter;
    p->host.tracing = 0; p->engine.host_active = 0;
    trace_dispose(p);
    p->host.required_s = p->host.remaining_s = 0;
    memset(&p->host.exposure, 0, sizeof(p->host.exposure));
    memset(&p->host.synchronization, 0, sizeof(p->host.synchronization));
}
void cm_pr655_host_dispose(inst *meter) {
    pr655 *p = (pr655 *)meter;
    trace_dispose(p); report_dispose(&p->host.control);
}
int cm_pr655_capture_begin(inst *meter, unsigned int started_ms) {
    pr655 *p = (pr655 *)meter;
    cm_pr655_capture_reset(meter);
    p->host.started_ms = started_ms;
    p->host.tracing = 1;
    p->engine.host_started_ms = started_ms;
    if (!isfinite(p->host.allowance_s) || p->host.allowance_s < 0 || p->host.allowance_s > 86400) {
        snprintf(p->capture.problem, sizeof(p->capture.problem),
            "Read allowance must be zero or finite, positive and at most 24 hours");
        return PR655_BAD_SETUP; /* Host refusal before any exchange; link stays clean. */
    }
    p->engine.host_active = p->host.allowance_s > 0;
    p->engine.host_milliseconds = (unsigned int)ceil(p->host.allowance_s * 1000);
    return PR655_OK;
}
int cm_pr655_preflight(inst *meter, double measurement_s, int setters) {
    pr655 *p = (pr655 *)meter;
    p->host.required_s = measurement_s + 10.0 + (p->host.capture_metadata ? 20.0 : 0.0) + setters * 10.0;
    p->host.remaining_s = p->engine.host_active ? left(p) : 0;
    if (p->host.control_blocked) {
        snprintf(p->capture.problem, sizeof(p->capture.problem),
            "A failed settings application requires explicit recovery before measurement");
        return PR655_BAD_SETUP;
    }
    if (p->engine.host_active && p->host.remaining_s < p->host.required_s) {
        snprintf(p->capture.problem, sizeof(p->capture.problem),
            "Read allowance remaining %.3f s is below required %.3f s", p->host.remaining_s, p->host.required_s);
        return CM_PR655_ALLOWANCE;
    }
    return PR655_OK;
}
int cm_pr655_capture_end(inst *meter, int native_code) {
    pr655 *p = (pr655 *)meter;
    int code = cm_pr655_engine_finish(&p->engine, native_code);
    if (!code && p->host.capture_metadata && p->capture.valid) {
        /* Optional metadata cannot invalidate a complete verified observation.
         * A failed transaction may quarantine the link; no later query follows. */
        int metadata_code = cm_pr655_engine_metadata(&p->engine, 13, &p->host.exposure, 10.0);
        if (!metadata_code) (void)cm_pr655_engine_metadata(&p->engine, 14, &p->host.synchronization, 10.0);
        (void)cm_pr655_engine_finish(&p->engine, metadata_code);
    }
    if (!code && p->host.overflow) code = CM_PR655_TRACE_LIMIT;
    p->host.tracing = 0; p->engine.host_active = 0;
    return code;
}
