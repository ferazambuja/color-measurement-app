/* Baseline inst_mode checks on an already initialized instrument.
 * This is shared by description and acquisition metadata. It does not set a
 * mode, option or calibration. Call ONLY after the initial capabilities query:
 * DTP41 capabilities may populate its cache by sending commands. check_mode is
 * state-dependent, not pure; the pinned initialized call graph is reviewed.
 */
#ifndef CM_MODE_CHECKS_H
#define CM_MODE_CHECKS_H
#include "mode_check_candidates.h"

typedef struct {
    inst_mode capabilities;
    unsigned count;
    int method_available;
    struct { inst_mode word; inst_code code; } checks[CM_MODE_CHECK_CAPACITY];
} cm_mode_checks;

static inline void cm_collect_mode_checks(inst *meter, inst_mode caps, cm_mode_checks *out) {
    unsigned i, output;
    out->capabilities = caps;
    out->count = 0;
    out->method_available = meter->check_mode != NULL;
    for (i = 0; i < sizeof(cm_mode_check_candidates)/sizeof(cm_mode_check_candidates[0]); i++) {
        inst_mode base = cm_mode_check_candidates[i];
        if ((base & caps) != base) continue;
        for (output = 0; output < ((caps & inst_mode_spectral) ? 2u : 1u); output++) {
            inst_mode word = base | inst_mode_colorimeter | (output ? inst_mode_spectral : 0);
            out->checks[out->count].word = word;
            out->checks[out->count].code = meter->check_mode
                ? meter->check_mode(meter, word) : inst_unsupported;
            out->count++;
        }
    }
}

static inline void cm_print_mode_checks(FILE *stream, const cm_mode_checks *value) {
    unsigned i;
    fprintf(stream, ",\"baseline_mode_checks\":{\"scope\":\"initialized_before_mode_configuration\","
                    "\"mode_capabilities\":%u,\"checks\":[", (unsigned)value->capabilities);
    for (i = 0; i < value->count; i++) {
        fprintf(stream, "%s{\"mode_word\":%u,\"native_code\":", i ? "," : "",
                (unsigned)value->checks[i].word);
        if (value->method_available) fprintf(stream, "%u", (unsigned)value->checks[i].code);
        else fputs("null", stream);
        fputc('}', stream);
    }
    fputs("]}", stream);
}
#endif
