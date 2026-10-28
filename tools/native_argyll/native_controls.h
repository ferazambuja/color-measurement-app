/* Typed control dispatch inside the existing selected Argyll lifetime.
 * Copyright 2026 Fernando Voltolini de Azambuja. GPL-2.0-or-later.
 * Field decoding is local; inventories and complete-target constraints remain
 * in the driver adapters. No arbitrary instrument command is accepted.
 */
#ifndef CM_NATIVE_CONTROLS_H
#define CM_NATIVE_CONTROLS_H
#include <errno.h>
#include <math.h>
#include <stddef.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#ifdef CM_CR_ADAPTER
#include "cr250_control.h"
#endif
#ifdef CM_PR_ADAPTER
#include "pr655_control.h"
#include "pr655.h"
#endif

#define CM_CONTROL_FIELDS_MAX 17
#define CM_CONTROL_TEXT_MAX 4096
typedef struct {
    int count;
    char names[CM_CONTROL_FIELDS_MAX][32];
    char tokens[CM_CONTROL_FIELDS_MAX][64];
    double values[CM_CONTROL_FIELDS_MAX];
} cm_control_fields;

/* Canonical name=decimal pairs. Empty means no requested fields. Reject empty
 * items, duplicates, booleans, hex, nonfinite values and trailing text. */
static int cm_control_fields_parse(const char *text, cm_control_fields *out) {
    const char *p = text;
    memset(out, 0, sizeof(*out));
    if (!text || strlen(text) > CM_CONTROL_TEXT_MAX) return 0;
    while (*p) {
        const char *end = strchr(p, ','), *equal;
        char number[64], *tail;
        size_t name_n, value_n;
        int i;
        if (!end) end = p + strlen(p);
        if (out->count >= CM_CONTROL_FIELDS_MAX || !(equal = memchr(p, '=', end - p))) return 0;
        name_n = (size_t)(equal - p); value_n = (size_t)(end - equal - 1);
        if (!name_n || name_n >= sizeof(out->names[0]) || !value_n || value_n >= sizeof(number)) return 0;
        for (i = 0; i < (int)name_n; i++)
            if (!((p[i] >= 'a' && p[i] <= 'z') || (p[i] >= 'A' && p[i] <= 'Z')
                || (i && p[i] >= '0' && p[i] <= '9'))) return 0;
        memcpy(out->names[out->count], p, name_n);
        out->names[out->count][name_n] = 0;
        for (i = 0; i < out->count; i++) if (!strcmp(out->names[i], out->names[out->count])) return 0;
        memcpy(number, equal + 1, value_n); number[value_n] = 0;
        memcpy(out->tokens[out->count], number, value_n + 1);
        for (i = 0; i < (int)value_n; i++)
            if (!((number[i] >= '0' && number[i] <= '9') || strchr(".+-eE", number[i]))) return 0;
        errno = 0;
        out->values[out->count] = strtod(number, &tail);
        if (tail == number || *tail || errno == ERANGE || !isfinite(out->values[out->count])) return 0;
        out->count++;
        if (!*end) break;
        p = end + 1;
        if (!*p) return 0;
    }
    return 1;
}

enum { CM_CONTROL_INT, CM_CONTROL_LONG, CM_CONTROL_REAL, CM_CONTROL_INT64 };
typedef struct { const char *name; int field, kind; size_t offset; } cm_control_field;

/* Integer requests have canonical integer tokens. Checking the original token
 * prevents a rounded binary64 fraction from becoming an admitted setting ID. */
static int cm_control_integer(const char *text, long long *value) {
    const char *p = text;
    char *end;
    long long n;
    if (*p == '+' || *p == '-') p++;
    if (*p < '0' || *p > '9') return 0;
    while (*p >= '0' && *p <= '9') p++;
    if (*p) return 0;
    errno = 0; n = strtoll(text, &end, 10);
    if (errno || *end || n < 0) return 0;
    *value = n; return 1;
}

static int cm_control_decode(const cm_control_fields *values, const cm_control_field *fields,
                             int count, void *request, unsigned int *present) {
    int i, j;
    for (i = 0; i < values->count; i++) {
        double v = values->values[i];
        for (j = 0; j < count; j++) if (!strcmp(values->names[i], fields[j].name)) break;
        if (j == count) return 0;
        if (fields[j].kind == CM_CONTROL_REAL) memcpy((char *)request + fields[j].offset, &v, sizeof(v));
        else if (fields[j].kind == CM_CONTROL_INT) {
            int n;
            long long exact;
            if (!cm_control_integer(values->tokens[i], &exact) || exact > INT_MAX) return 0;
            n = (int)exact; memcpy((char *)request + fields[j].offset, &n, sizeof(n));
        } else if (fields[j].kind == CM_CONTROL_INT64) {
            long long n;
            if (!cm_control_integer(values->tokens[i], &n) || n > 9007199254740991LL) return 0;
            memcpy((char *)request + fields[j].offset, &n, sizeof(n));
        } else {
            long n;
            long long exact;
            /* JSON-safe and exactly representable on 32-bit and 64-bit hosts. */
            if (!cm_control_integer(values->tokens[i], &exact) || exact > 9007199254740991LL
             || exact > LONG_MAX) return 0;
            n = (long)exact; memcpy((char *)request + fields[j].offset, &n, sizeof(n));
        }
        *present |= 1u << fields[j].field;
    }
    return 1;
}

#ifdef CM_CR_ADAPTER
#define CR_FIELD(name, field, kind) {#name, field, kind, offsetof(cm_cr250_control_request, name)}
static const cm_control_field cm_cr_control_fields[] = {
    CR_FIELD(mode, CM_CR250_MODE, CM_CONTROL_INT64),
    CR_FIELD(exposureMode, CM_CR250_EXPOSURE_MODE, CM_CONTROL_INT64),
    CR_FIELD(exposureMs, CM_CR250_EXPOSURE, CM_CONTROL_REAL),
    CR_FIELD(rangeMode, CM_CR250_RANGE_MODE, CM_CONTROL_INT64),
    CR_FIELD(range, CM_CR250_RANGE, CM_CONTROL_INT64),
    CR_FIELD(exposureMultiplier, CM_CR250_MULTIPLIER, CM_CONTROL_INT64),
    CR_FIELD(syncMode, CM_CR250_SYNC_MODE, CM_CONTROL_INT64),
    CR_FIELD(syncFrequencyHz, CM_CR250_FREQUENCY, CM_CONTROL_REAL),
    CR_FIELD(speed, CM_CR250_SPEED, CM_CONTROL_INT64),
    CR_FIELD(accessory, CM_CR250_ACCESSORY, CM_CONTROL_INT64),
    CR_FIELD(filter1, CM_CR250_FILTER1, CM_CONTROL_INT64),
    CR_FIELD(filter2, CM_CR250_FILTER2, CM_CONTROL_INT64),
    CR_FIELD(filter3, CM_CR250_FILTER3, CM_CONTROL_INT64),
    CR_FIELD(userCalibrationMode, CM_CR250_CORRECTION_MODE, CM_CONTROL_INT64),
    CR_FIELD(matrixId, CM_CR250_MATRIX, CM_CONTROL_INT64),
    CR_FIELD(matchId, CM_CR250_MATCH, CM_CONTROL_INT64),
    CR_FIELD(maxAutoExposureMs, CM_CR250_MAX_AUTO_EXPOSURE, CM_CONTROL_REAL)
};
#undef CR_FIELD
#endif
#ifdef CM_PR_ADAPTER
#define PR_FIELD(name, field, kind) {#name, field, kind, offsetof(cm_pr655_control_request, name)}
static const cm_control_field cm_pr_control_fields[] = {
    PR_FIELD(exposureMilliseconds, CM_PR655_EXPOSURE, CM_CONTROL_INT),
    PR_FIELD(cycles, CM_PR655_CYCLES, CM_CONTROL_INT),
    PR_FIELD(syncMode, CM_PR655_SYNC, CM_CONTROL_INT),
    PR_FIELD(syncFrequencyHz, CM_PR655_FREQUENCY, CM_CONTROL_REAL),
    PR_FIELD(primaryAccessoryId, CM_PR655_PRIMARY, CM_CONTROL_INT64),
    PR_FIELD(addon1Id, CM_PR655_ADDON1, CM_CONTROL_INT64),
    PR_FIELD(addon2Id, CM_PR655_ADDON2, CM_CONTROL_INT64),
    PR_FIELD(addon3Id, CM_PR655_ADDON3, CM_CONTROL_INT64),
    PR_FIELD(apertureId, CM_PR655_APERTURE, CM_CONTROL_INT64),
    PR_FIELD(sensitivityMode, CM_PR655_SENSITIVITY, CM_CONTROL_INT),
    PR_FIELD(smartDark, CM_PR655_DARK, CM_CONTROL_INT),
    PR_FIELD(photometricUnits, CM_PR655_UNITS, CM_CONTROL_INT),
    PR_FIELD(instrumentObserver, CM_PR655_OBSERVER, CM_CONTROL_INT),
    PR_FIELD(shutterMode, CM_PR655_SHUTTER, CM_CONTROL_INT),
    PR_FIELD(speedModeCode, CM_PR655_SPEED, CM_CONTROL_INT)
};
#undef PR_FIELD
#endif

static double cm_native_control_allowance(const inst *meter) {
#ifdef CM_CR_ADAPTER
    if (cm_cr_family(meter)) return ((const cr250 *)meter)->host_allowance_s;
#endif
#ifdef CM_PR_ADAPTER
    if (cm_pr_family(meter)) return ((const pr655 *)meter)->host.allowance_s;
#endif
    (void)meter;
    return 0;
}

static inst_code cm_native_controls_run(inst *meter, const char *action, const cm_control_fields *fields) {
    inst_code rv;
    double allowance = cm_native_control_allowance(meter);
    int apply = !strcmp(action, "apply");
    if (!(allowance > 0)) return inst_wrong_setup;
#ifdef CM_CR_ADAPTER
    if (cm_cr_family(meter)) {
        cm_cr250_control_request q = {0};
        cm_cr250_control_report *report;
        if (!cm_control_decode(fields, cm_cr_control_fields, CM_CR250_FIELDS, &q, &q.present)) return inst_bad_parameter;
        if (!(report = calloc(1, sizeof(*report)))) return inst_internal_error;
        rv = apply ? cm_cr250_control_apply(meter, &q, report, allowance)
                   : cm_cr250_control_read(meter, report, allowance);
        cm_cr250_control_report_event(report);
        cm_cr250_control_report_dispose(report);
        free(report);
        return rv;
    }
#endif
#ifdef CM_PR_ADAPTER
    if (cm_pr_family(meter)) {
        cm_pr655_control_request q = {0};
        cm_pr655_control_report *report;
        if (!cm_control_decode(fields, cm_pr_control_fields, CM_PR655_FIELDS, &q, &q.present)) return inst_bad_parameter;
        if (!(report = calloc(1, sizeof(*report)))) return inst_internal_error;
        rv = apply ? cm_pr655_control_apply(meter, &q, report, allowance)
                   : cm_pr655_control_read(meter, report, allowance);
        cm_pr655_control_facts(meter);
        free(report);
        return rv;
    }
#endif
    (void)rv; (void)apply; (void)fields;
    return inst_unsupported;
}
#endif
