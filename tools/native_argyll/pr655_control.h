/* Application-only PR controls and retained facts. No arbitrary commands.
 * Copyright 2026 Fernando Voltolini de Azambuja.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef CM_PR655_CONTROL_H
#define CM_PR655_CONTROL_H
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
#include "pr655_imp.h"

enum {
    CM_PR655_EXPOSURE, CM_PR655_CYCLES, CM_PR655_SYNC, CM_PR655_FREQUENCY,
    CM_PR655_PRIMARY, CM_PR655_ADDON1, CM_PR655_ADDON2, CM_PR655_ADDON3,
    CM_PR655_APERTURE, CM_PR655_SENSITIVITY, CM_PR655_DARK, CM_PR655_UNITS,
    CM_PR655_OBSERVER, CM_PR655_SHUTTER, CM_PR655_SPEED, CM_PR655_FIELDS
};
#define CM_PR655_BIT(field) (1u << (field))
#define CM_PR655_ALL ((1u << CM_PR655_FIELDS) - 1u)
#define CM_PR655_ALLOWANCE 0xff40
#define CM_PR655_TRACE_LIMIT 0xff41
#define CM_PR655_MAX_EXCHANGES 256
#define CM_PR655_MAX_TRACE_BYTES (4 * 1024 * 1024)
#define CM_PR655_MAX_APERTURES PR655_MAX_ACCESSORIES

/* Semantic field names match the shared Python native-controls request contract.
 * Choice values are native codes:
 * sync0/1/3, sensitivity0/1, units0/1, shutter0/1. present distinguishes an
 * explicit zero from absence. No negative accessory clearing code is accepted. */
typedef struct {
    unsigned int present;
    int exposureMilliseconds, cycles, syncMode;
    double syncFrequencyHz;
    long long primaryAccessoryId, addon1Id, addon2Id, addon3Id, apertureId;
    int sensitivityMode, smartDark, photometricUnits, instrumentObserver, shutterMode;
    int speedModeCode; /* PR-670 SG0..3; no assumed mapping to reported Gain */
} cm_pr655_control_request;

typedef struct {
    long long id;
    char *name;
    double bandwidth_nm;
} cm_pr655_aperture;

typedef struct {
    cm_pr655_control_request before, values, requested;
    unsigned int changed, attempted, acknowledged, confirmed, blocked;
    int status_code, failed, setup_valid, setup_before_valid, aperture_list_valid;
    int raw_exchange_count;
    pr655_setup setup_before, setup;
    int accessory_count, aperture_count, inventory_counts_valid, accessory_list_valid;
    int reported_accessory_count, inventory_counts_error, accessory_list_error, aperture_list_error;
    int aperture_storage_count; /* owned allocation length, independent of report validity */
    pr655_accessory *accessories;
    cm_pr655_aperture *apertures;
    char problem[256];
} cm_pr655_control_report;

typedef struct {
    char command[32];
    int complete, error;
    pr655_raw raw;
} cm_pr655_exchange;

typedef struct {
    int queried, complete, parsed, error;
    char description[PR655_MAX_TEXT];
    double value;
} cm_pr655_metadata;

typedef struct {
    double allowance_s, required_s, remaining_s;
    unsigned int control_blocked;
    int explicit_sync; /* successful native sync intent supersedes recipe overrides */
    unsigned int started_ms;
    int capture_metadata, tracing, overflow, exchange_count, trace_bytes;
    cm_pr655_exchange exchanges[CM_PR655_MAX_EXCHANGES];
    cm_pr655_control_report control;
    cm_pr655_metadata exposure, synchronization;
} cm_pr655_host;

/* Both operations stay in the already owned instrument lifetime. Apply validates
 * the entire target before its first setter and returns separate ACK/readback
 * masks. SZ has no documented report: acknowledged never means confirmed.
 * Returned report inventories borrow the instrument's host-owned snapshot.
 * They remain valid until its next control operation or destruction. The caller
 * owns only the report container; it does not free the inventory pointers. */
inst_code cm_pr655_control_read(inst *, cm_pr655_control_report *, double timeout_s);
inst_code cm_pr655_control_apply(inst *, const cm_pr655_control_request *,
                                cm_pr655_control_report *, double timeout_s);
void cm_pr655_read_allowance(inst *, double seconds);
void cm_pr655_capture_metadata(inst *, int enabled); /* no I/O */
void cm_pr655_native_facts(inst *);
void cm_pr655_control_report_event(const cm_pr655_control_report *);
void cm_pr655_control_facts(inst *);
/* Pure whole-read minimum: measurement floor plus final setup. The executed
 * capture checks hold this bound to cm_pr655_preflight's actual call path. */
static double pr655_required_floor_s(instType dtype, inst_mode mode) {
    inst_mode base = mode & ~(inst_mode_colorimeter | inst_mode_spectral | inst_mode_emis_nonadaptive
                            | inst_mode_emis_norefresh_ovd | inst_mode_emis_refresh_ovd);
    if ((dtype != instPR655 && dtype != instPR670)
     || (base != inst_mode_emis_spot && base != inst_mode_emis_tele
         && base != inst_mode_emis_ambient)) return -1.0;
    return 310.0;
}

/* App overlay entry points; these do not exist in the contribution. */
int cm_pr655_engine_set(pr655imp *, int field, long long value, int *acknowledged, double timeout_s);
int cm_pr655_engine_apertures(pr655imp *, cm_pr655_aperture *, double timeout_s);
int cm_pr655_engine_metadata(pr655imp *, int code, cm_pr655_metadata *, double timeout_s);
int cm_pr655_observe(void *, const char *, const pr655_raw *, int error, int complete);
int cm_pr655_engine_finish(pr655imp *, int code);
void cm_pr655_capture_reset(inst *);
void cm_pr655_host_dispose(inst *); /* frees retained heap data without I/O */
int cm_pr655_capture_begin(inst *, unsigned int started_ms);
int cm_pr655_preflight(inst *, double measurement_s, int setters);
int cm_pr655_capture_end(inst *, int native_code);
#endif
