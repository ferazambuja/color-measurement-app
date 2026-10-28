/* Strip operands consumed by the pinned Argyll 3.5.0 drivers. These are software
 * limits, not measured physical limits or automatic target recognition.
 * Shared by description, acquisition validation and the published UI contract. */
#ifndef CM_STRIP_POLICY_H
#define CM_STRIP_POLICY_H

typedef struct {
    int max_patches;
    int widths, trailer, names, guide;
    double width_max;  /* zero means no upper bound in the driver's binning */
    const char *requirement;
    const char *program_instruction;
    const char *switch_instruction;
} cm_strip_policy;

static cm_strip_policy cm_strip_policy_for(instType dtype, inst_mode mode) {
    cm_strip_policy p = {0, 0, 0, 0, 0, 0, NULL,
        "Continuing starts the read immediately; have the strip ready before continuing.",
        "After continuing, use the instrument's physical control to start the scan."};
    /* DTP20 retrieves saved strips through read_chart, not read_strip:
     * dtp20_read_strip:636-638 explicitly rejects saved-data modes. */
    if ((mode & inst_mode_illum_mask) == inst_mode_s_reflection) return p;
    switch (dtype) {
        case instDTP20:
            /* dtp20.c:282-339: width bins, two-digit count, SID enabled. */
            p.max_patches = 99; p.widths = 1;
            p.requirement = "Use a DTP20-compatible strip with its strip-identification field (SID). The driver bins the supplied patch and gap widths; it does not report the SID.";
            break;
        case instDTP41:
            /* dtp41.c:265-313: ddd count, dd.dd widths; dddd trailer in transmission. */
            /* The three-digit command permits 999, but the spectral reply uses
             * MAX_RD_SIZE=100000 and at least 31*8 bytes per patch (:66,497,642).
             * 403 leaves 56 bytes; 404 cannot fit even before status/framing.
             * Also applies to XYZ-only requests: conversion may fetch spectra. */
            p.max_patches = 403; p.widths = 1; p.width_max = 99.99;
            p.trailer = (mode & inst_mode_illum_mask) == inst_mode_transmission;
            /* DTP41-500 manual PDF p.11: rear drive rollers pull the strip;
             * dtp41.c:580-583 starts that read with RM under program trigger. */
            p.program_instruction = "Load the strip against the rear rollers before continuing. Continuing starts the read immediately; the instrument feeds the strip.";
            break;
        case instDTP51:
            /* dtp51.c:312-350: names truncated/padded to 7/3 bytes; two-digit
             * count and guide. The driver does NOT subtract five from guide. */
            p.max_patches = 99; p.names = 1; p.guide = 1;
            /* dtp51.c:500-530 arms 5SW and waits for the feed microswitch. */
            p.switch_instruction = "After continuing, feed the strip into the instrument to activate its feed microswitch.";
            p.requirement = "Patch IDs name returned positions, including any padding. DTP51 chart alignment and target recognition are not performed.";
            break;
        case instI1Monitor: case instI1Pro: case instI1Pro2: case instI1Pro3:
        case instColorMunki:
            /* i1pro.c, i1pro3.c and munki.c read_strip pass only npatch and
             * vals to their measurement routine. Geometry/names are ignored. */
            p.max_patches = 4096;
            break;
        default:
            /* ss_read_strip and inst_read_strip are unsupported. A newly added
             * strip driver needs an operand review before acquiring through us. */
            break;
    }
    if (dtype == instDTP20 || dtype == instI1Monitor || dtype == instI1Pro
        || dtype == instI1Pro2 || dtype == instI1Pro3 || dtype == instColorMunki) {
        /* Manual scanning: i1Pro2 user guide PDF p.20, i1Pro3 guide p.11;
         * i1pro_imp.c:2915-2920 and munki_imp.c:1988-1994 describe moving the
         * instrument after trigger. DTP20 starts its interactive scan with RM
         * at dtp20.c:748-751; its guide specifies starting/ending on white. */
        p.program_instruction = "Continuing starts the read immediately. Move the instrument steadily across the selected row, starting before its first patch and ending beyond its last.";
    }
    return p;
}

/* Signed zero is a valid zero length, but pinned DTP41's decimal-stripping
 * formatter would send -000 (the composed driver also drops the sign).
 * Canonicalize only the outgoing geometry, not the request record or
 * any measured quantity. Negative omission sentinels remain intact. */
static double cm_strip_wire_length(double value) {
    return value == 0.0 ? 0.0 : value;
}

/* Fixed strings only; none of these JSON fragments contains driver-supplied text. */
static void cm_strip_number(FILE *out, const char *key, const char *label,
                            const char *type, double minimum, double maximum,
                            int exclusive, const char *unit, const char *help,
                            int *comma) {
    fprintf(out, "%s{\"key\":\"%s\",\"label\":\"%s\",\"type\":\"%s\","
                 "\"required\":true,\"default\":null,\"minimum\":%.17g,"
                 "\"exclusive_minimum\":%s,\"maximum\":",
            (*comma)++ ? "," : "", key, label, type, minimum, exclusive ? "true" : "false");
    if (maximum > 0) fprintf(out, "%.17g", maximum); else fputs("null", out);
    fprintf(out, ",\"unit\":\"%s\",\"help\":\"%s\"}", unit, help);
}

static void cm_print_strip_policy(FILE *out, instType dtype, inst_mode mode) {
    cm_strip_policy p = cm_strip_policy_for(dtype, mode);
    int comma = 0;
    fprintf(out, "{\"max_patches\":%d,\"requirement\":", p.max_patches);
    if (p.requirement) fprintf(out, "\"%s\"", p.requirement); else fputs("null", out);
    fputs(",\"layout_fields\":[", out);
    if (p.names) {
        fputs("{\"key\":\"strip_name\",\"label\":\"Strip name\",\"type\":\"text\","
              "\"required\":false,\"default\":\"STRIP\",\"max_bytes\":7,\"encoding\":\"ascii_printable\","
              "\"help\":\"Name sent to the driver; omission sends STRIP. This is not target recognition.\"},"
              "{\"key\":\"pass_name\",\"label\":\"Pass name\",\"type\":\"text\","
              "\"required\":false,\"default\":\"1\",\"max_bytes\":3,\"encoding\":\"ascii_printable\","
              "\"help\":\"Pass label sent to the driver; omission sends 1.\"}", out);
        comma = 1;
    }
    if (p.guide)
        cm_strip_number(out, "guide", "Guide number", "integer", 0, 99, 0, "",
            "Guide number sent unchanged to the instrument. Use the guide position for this strip.", &comma);
    if (p.widths) {
        cm_strip_number(out, "patch_width_mm", "Patch length", "number",
            p.width_max ? 0.01 : 0, p.width_max, !p.width_max, "mm",
            p.width_max ? "Length along the scan; driver rounds to 0.01 mm."
                        : "Length along the scan; driver selects a discrete width bin.", &comma);
        cm_strip_number(out, "gap_width_mm", "Gap length", "number", 0, p.width_max, 0, "mm",
            p.width_max ? "Gap along the scan; zero means no gap. Driver rounds to 0.01 mm."
                        : "Gap along the scan; zero means no gap. Driver selects a discrete width bin.", &comma);
    }
    if (p.trailer)
        cm_strip_number(out, "trailer_width_mm", "Trailer length", "number", 0, 9999, 0, "mm",
            "Transmission trailer length; zero means none. Driver rounds to whole millimetres.", &comma);
    fputs("]}", out);
}

static void cm_print_strip_policies(FILE *out, const inst *meter) {
    fputs(",\"strip_policies\":{\"reflection\":", out);
    cm_print_strip_policy(out, meter->dtype, inst_mode_ref_strip);
    fputs(",\"transmission\":", out);
    cm_print_strip_policy(out, meter->dtype, inst_mode_trans_strip);
    fputs(",\"emission\":", out);
    cm_print_strip_policy(out, meter->dtype, inst_mode_emis_strip);
    fputs("}", out);
}
#endif
