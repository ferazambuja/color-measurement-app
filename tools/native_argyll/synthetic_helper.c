/* Original synthetic protocol exercise. No instrument object is constructed.
 * Uses the pinned Argyll ipatch declaration, not its runtime/transport code.
 * No real measurements, calibration, files, USB or OS services are accessed. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <math.h>
#include "sa_config.h"
#include "numsup.h"
#include "cgats.h"
#include "xspect.h"
#include "insttypes.h"
#include "conv.h"
#include "icoms.h"
#include "inst.h"

static char active[65] = "";
static char scenario[33] = "";
static unsigned sequence = 0;

static void prefix(const char *event) {
    printf("{\"protocol_version\":1,\"provenance\":\"synthetic\","
           "\"operation_id\":\"%s\",\"sequence\":%u,\"event\":\"%s\"",
           active, sequence++, event);
}

static void event(const char *name) {
    prefix(name);
    puts("}");
}

static void failure(const char *code) {
    prefix("error");
    printf(",\"code\":\"%s\"}\n", code);
    active[0] = '\0';
}

static int valid_identifier(const char *id) {
    size_t i, n = strlen(id);
    if (n == 0 || n > 64) return 0;
    for (i = 0; i < n; i++)
        if (!(isalnum((unsigned char)id[i]) || id[i] == '-' || id[i] == '_')) return 0;
    return 1;
}

static int known_scenario(const char *s) {
    const char *names[] = {"reading", "invalid_xyz", "black", "no_spectrum", "empty",
        "m2", "m3", "custom", "unspecified", "nonfinite", "bad_grid", "bad_norm",
        "zero_spectrum", "calibration_only", "failure", "wait", NULL};
    int i;
    for (i = 0; names[i]; i++) if (!strcmp(s, names[i])) return 1;
    return 0;
}

static void reading(void) {
    ipatch patch = {0};
    int i;
    patch.mtype = inst_mrt_reflective;
    patch.mcond = inst_mrc_D50;
    patch.XYZ_v = 1;
    patch.XYZ[0] = 2.0; patch.XYZ[1] = 3.0; patch.XYZ[2] = 5.0;
    patch.sp.spec_n = 4;
    patch.sp.spec_wl_short = 402.5; patch.sp.spec_wl_long = 417.5;
    patch.sp.norm = 100.0;
    patch.sp.spec[0] = -0.125; patch.sp.spec[1] = 1.2345678901234567;
    patch.sp.spec[2] = 120.25; patch.sp.spec[3] = 0.0;
    patch.duration = 0.25;
    if (!strcmp(scenario, "invalid_xyz") || !strcmp(scenario, "empty")) {
        patch.XYZ_v = 0;
        for (i = 0; i < 3; i++) patch.XYZ[i] = NAN;
    }
    if (!strcmp(scenario, "black")) for (i = 0; i < 3; i++) patch.XYZ[i] = 0.0;
    if (!strcmp(scenario, "no_spectrum") || !strcmp(scenario, "empty")) {
        patch.sp.spec_n = 0;
        patch.sp.norm = patch.sp.spec_wl_short = patch.sp.spec_wl_long = NAN;
        patch.sp.spec[0] = NAN;
    }
    if (!strcmp(scenario, "m2")) patch.mcond = inst_mrc_uvcut;
    if (!strcmp(scenario, "m3")) patch.mcond = inst_mrc_pol;
    if (!strcmp(scenario, "custom")) patch.mcond = inst_mrc_custom;
    if (!strcmp(scenario, "unspecified")) patch.mcond = inst_mrc_none;
    if (!strcmp(scenario, "nonfinite")) patch.XYZ[0] = NAN;
    if (!strcmp(scenario, "bad_grid")) patch.sp.spec_wl_long = 400.0;
    if (!strcmp(scenario, "bad_norm")) patch.sp.norm = 0.0;
    if (!strcmp(scenario, "zero_spectrum")) for (i = 0; i < 4; i++) patch.sp.spec[i] = 0.0;
    if (!patch.XYZ_v && patch.sp.spec_n == 0) { failure("missing_quantities"); return; }
    if (patch.XYZ_v) for (i = 0; i < 3; i++)
        if (!isfinite(patch.XYZ[i])) { failure("nonfinite_xyz"); return; }
    if (patch.sp.spec_n > 0) {
        if (patch.sp.spec_n < 2 || patch.sp.spec_n > XSPECT_MAX_BANDS
            || !isfinite(patch.sp.norm) || patch.sp.norm <= 0
            || !isfinite(patch.sp.spec_wl_short) || !isfinite(patch.sp.spec_wl_long)
            || patch.sp.spec_wl_long <= patch.sp.spec_wl_short) {
            failure("invalid_spectrum"); return;
        }
        for (i = 0; i < patch.sp.spec_n; i++)
            if (!isfinite(patch.sp.spec[i])) { failure("nonfinite_spectrum"); return; }
    }
    prefix("result");
    printf(",\"argyll_version\":\"%s\",\"requested_mode\":\"synthetic_reflective\","
           "\"confirmed_mode\":\"synthetic_reflective\","
           "\"calibration_context\":\"synthetic_not_calibrated\","
           "\"project_computed\":{},\"backend_reported\":{"
           "\"mtype\":%d,\"mcond\":%d,\"XYZ_v\":%d,\"XYZ\":",
           ARGYLL_VERSION_STR, patch.mtype, patch.mcond, patch.XYZ_v);
    if (patch.XYZ_v) printf("[%.17g,%.17g,%.17g]", patch.XYZ[0], patch.XYZ[1], patch.XYZ[2]);
    else printf("null");
    printf(",\"duration\":%.17g,\"spectrum\":", patch.duration);
    if (patch.sp.spec_n == 0) printf("null");
    else {
        printf("{\"spec_n\":%d,\"wavelength_start_nm\":%.17g,"
               "\"wavelength_end_nm\":%.17g,\"norm\":%.17g,\"samples\":[",
               patch.sp.spec_n, patch.sp.spec_wl_short, patch.sp.spec_wl_long, patch.sp.norm);
        for (i = 0; i < patch.sp.spec_n; i++) printf("%s%.17g", i ? "," : "", patch.sp.spec[i]);
        printf("]}");
    }
    puts("}}");
    active[0] = '\0';
}

int main(void) {
    char line[256], version[8], id[65], action[33], argument[33], extra[2];
    int count;
    setvbuf(stdout, NULL, _IONBF, 0);
    while (fgets(line, sizeof(line), stdin)) {
        if (!strchr(line, '\n')) return 2;
        count = sscanf(line, "%7s %64s %32s %32s %1s", version, id, action, argument, extra);
        if (count < 3 || strcmp(version, "v1") || !valid_identifier(id)) return 2;
        if (!strcmp(action, "begin")) {
            if (active[0] || count != 4 || !known_scenario(argument)) return 2;
            strcpy(active, id); strcpy(scenario, argument); sequence = 0;
            event("started");
            event(!strcmp(scenario, "calibration_only") ? "calibration_required" : "ready");
        } else {
            if (count != 3 || !active[0] || strcmp(id, active)) return 2;
            if (!strcmp(action, "cancel")) {
                event("cancelled"); active[0] = '\0';
            } else if (!strcmp(action, "complete")) {
                if (!strcmp(scenario, "calibration_only")) {
                    event("calibration_complete"); active[0] = '\0';
                } else if (!strcmp(scenario, "failure")) failure("synthetic_failure");
                else if (!strcmp(scenario, "wait")) { /* Deliberately await cancellation. */ }
                else reading();
            } else return 2;
        }
    }
    return active[0] ? 3 : 0;
}
