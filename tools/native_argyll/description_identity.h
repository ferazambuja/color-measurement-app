/* Initialized identity serialization shared by actual attachment and scripted
 * production-driver description personas. The snapshots issue no instrument I/O.
 */
#ifndef CM_DESCRIPTION_IDENTITY_H
#define CM_DESCRIPTION_IDENTITY_H
#include "native_text.h"
#include "cr250_support.h"
#include "pr655_support.h"

static void cm_write_initialized_identity(FILE *out, inst *meter) {
#if defined(CM_CONTRIBUTED_instCR250) || defined(CM_CONTRIBUTED_instCR100) || defined(CM_CONTRIBUTED_instCR300)
    cr250_identity identity;
    if (cm_cr250_identity_snapshot(meter, &identity)) {
        fputs("{\"driver\":\"cr250\",\"native_controls\":true,\"identity\":{\"verified\":true,\"model\":", out);
        cm_utf8_json_string(out, identity.model);
        fputs(",\"serial\":", out); cm_utf8_json_string(out, identity.serial);
        fputs(",\"firmware\":", out); cm_utf8_json_string(out, identity.firmware);
        fputs(",\"firmware_components\":", out);
        if (identity.nfw > 0 && identity.nfw <= 3) {
            fputc('[', out);
            for (int i = 0; i < identity.nfw; i++)
                fprintf(out, "%s%d", i ? "," : "", identity.fw[i]);
            fputc(']', out);
        } else fputs("null", out);
        fputs(",\"instrument_type\":", out);
        if (identity.instrument_type >= 0) fprintf(out, "%d", identity.instrument_type);
        else fputs("null", out);
        fputs("}}", out);
        return;
    }
#endif
#if defined(CM_CONTRIBUTED_instPR655) || defined(CM_CONTRIBUTED_instPR670)
    cm_pr655_identity pr_identity;
    if (cm_pr655_identity_snapshot(meter, &pr_identity)) {
        const pr655_grid *grid = &pr_identity.grid;
        fputs("{\"driver\":\"pr655\",\"native_controls\":true,\"identity\":{\"verified\":true,\"model\":", out);
        cm_utf8_json_string(out, pr_identity.model == 655 ? "PR-655" : "PR-670");
        fputs(",\"serial\":", out); cm_utf8_json_string(out, pr_identity.serial);
        fputs(",\"firmware\":", out); cm_utf8_json_string(out, pr_identity.firmware);
        fprintf(out, ",\"grid\":{\"count\":%d,\"start_nm\":%d,\"end_nm\":%d,\"step_nm\":%d,"
                     "\"pixels\":%d,\"first_pixel\":%d,\"last_pixel\":%d,\"bandwidth_nm\":%.17g}}}",
                grid->count, grid->start_nm, grid->end_nm, grid->step_nm, grid->pixels,
                grid->first_pixel, grid->last_pixel, grid->bandwidth_nm);
        return;
    }
#endif
    (void)meter;
    fputs("null", out);
}

#endif
