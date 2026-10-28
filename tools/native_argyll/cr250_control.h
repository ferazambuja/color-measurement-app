/* Application-only CR controls in the owned Argyll instrument lifetime.
 * Copyright 2026 Fernando Voltolini de Azambuja.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef CM_CR250_CONTROL_H
#define CM_CR250_CONTROL_H
#ifndef SALONEINSTLIB
#include "aconfig.h"
#else
#include "sa_config.h"
#endif
#include "numsup.h"
#include "cgats.h"
#include "xspect.h"
#include "insttypes.h"
#include "conv.h"
#include "icoms.h"
#include "inst.h"
#include "cr250.h"

enum {
    CM_CR250_MODE, CM_CR250_EXPOSURE_MODE, CM_CR250_EXPOSURE,
    CM_CR250_RANGE_MODE, CM_CR250_RANGE, CM_CR250_MULTIPLIER,
    CM_CR250_SYNC_MODE, CM_CR250_FREQUENCY, CM_CR250_SPEED,
    CM_CR250_ACCESSORY, CM_CR250_FILTER1, CM_CR250_FILTER2, CM_CR250_FILTER3,
    CM_CR250_CORRECTION_MODE, CM_CR250_MATRIX, CM_CR250_MATCH,
    CM_CR250_MAX_AUTO_EXPOSURE, CM_CR250_FIELDS
};
#define CM_CR250_BIT(field) (1u << (field))
#define CM_CR250_ALL ((1u << CM_CR250_FIELDS) - 1u)
/* Native inventory parsing already bounds textual IDs. This additional bound
 * declares the exact integer contract of the JSON consumer on every platform. */
#define CM_CR250_MAX_ID 9007199254740991.0
#define CM_CR250_CONTROL_REPLIES 128 /* two reports, 17 SM/RS pairs and selectors */

typedef struct {
    unsigned int present; /* explicit zero differs from an absent setting */
    long long mode, exposureMode, rangeMode, range, exposureMultiplier, syncMode;
    long long speed, accessory, filter1, filter2, filter3, userCalibrationMode;
    long long matrixId, matchId;
    double exposureMs, syncFrequencyHz, maxAutoExposureMs;
} cm_cr250_control_request;

enum {
    CM_CR250_CHOICE, CM_CR250_PROFILE, CM_CR250_POSITIVE_INTEGER,
    CM_CR250_MILLISECONDS, CM_CR250_HERTZ
};

typedef struct {
    int available, read_only, kind, limits_known;
    int choice_count;
    cr250_record *choices; /* owned count-allocated exact reported IDs/names/types */
    double minimum, maximum;
    char current_native[CR250_MAX_VALUE]; /* NA retained; values.present stays clear */
    char problem[192];
} cm_cr250_control_row;

typedef struct {
    cm_cr250_control_request before, values, requested;
    unsigned int changed, attempted, acknowledged, confirmed, blocked;
    int status_code, failed;
    const cm_cr250_exchange *raw_exchanges; /* borrowed until next control/read/destruction */
    int raw_exchange_count, trace_overflow;
    cm_cr250_control_row controls[CM_CR250_FIELDS];
    int reply_count;
    struct {
        char command[CR250_MAX_COMMAND + 1];
        int phase, error; /* 0 initial report, 1 apply, 2 final report */
        cr250_reply reply; /* complete remains false when no status was parsed */
    } replies[CM_CR250_CONTROL_REPLIES];
    char aperture[CR250_MAX_VALUE]; /* read-only model fact, never guessed */
    char problem[256];
} cm_cr250_control_report;

/* No free-form commands or parallel transport. Each operation uses the real
 * contribution engine under its instrument lock. Apply validates the complete
 * target before the first SM. Acknowledgment and readback are separate masks;
 * MaxAutoExposure has no documented RS and never gains a confirmed/current bit.
 * Matrix/Match selections use the existing inst calibration selector, rather
 * than setting UserCalibMode behind the driver's selected calibration.
 * Zero-initialize each caller-owned report before first use. Read/apply releases
 * previous row allocations when reusing it. Dispose every success or failure
 * report after serializing/consuming it and before releasing its container. */
inst_code cm_cr250_control_read(inst *, cm_cr250_control_report *, double timeout_s);
inst_code cm_cr250_control_apply(inst *, const cm_cr250_control_request *,
                                cm_cr250_control_report *, double timeout_s);
void cm_cr250_control_report_event(const cm_cr250_control_report *);
void cm_cr250_control_report_dispose(cm_cr250_control_report *);
/* Application overlay entry point. Caller must hold the driver's lock. */
inst_code cm_cr250_select_profile_locked(cr250 *, int mode, long long id, const char *name);
#endif
