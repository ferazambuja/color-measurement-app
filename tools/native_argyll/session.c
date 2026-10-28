/* Shared Argyll acquisition session: selection, capability negotiation,
 * calibration, operator exchange, reading and cleanup through the inst vtable.
 * Python supplies composed mode words. Genuine driver exceptions use bounded
 * source-backed policy; no per-family session implementation is required.
 */
#define CM_DISPLAY_IMPLEMENTATION
#include "session.h"
#include "driver_policy.h"
#include "strip_policy.h"
#include "native_facts.h"
#include "native_controls.h"
#include "native_text.h"
#include <limits.h>

FILE *wire;
static int cancelled, bad_input;
/* Upper bound for a fresh client allowance requested immediately before a read.
 * The client owns its deadline, including operator-wait pauses. A separate
 * worker-start timer cannot reconstruct that deadline. Zero disables exchange. */
static double read_allowance_s;
static char operation_id[65];

/* Driver diagnostics and calibration reference IDs can contain invalid UTF-8.
 * Escape each high byte separately to keep JSON valid and retain its value. */
static void string(const char *value) {
    if (!value) { fputs("null", wire); return; }
    fputc('"', wire);
    for (const unsigned char *s = (const unsigned char *)value; *s; s++) {
        if (*s == '"' || *s == '\\') fprintf(wire, "\\%c", *s);
        else if (*s < 0x20 || *s >= 0x80) fprintf(wire, "\\u%04x", *s);
        else fputc(*s, wire);
    }
    fputc('"', wire);
}

/* Description and session must publish the same serial for a valid UTF-8 pin.
 * Preserve the byte-escaped form only when a driver returns invalid UTF-8. */
static const char *serial_encoding(const char *value) {
    if (!value) return NULL;
    return cm_utf8_text(value, 200) ? "utf8" : "bytes";
}
static void serial_string(const char *value) {
    if (value && strcmp(serial_encoding(value), "utf8") == 0)
        cm_utf8_json_string(wire, value);
    else
        string(value);
}

/* Numeric arguments arrive as text on the command line and on the wire. Every
 * unsigned value is canonical decimal: no sign, base prefix or leading zero.
 * Trailing characters are malformed, never silently truncated. */
static int u32(const char *text, unsigned long *out) {
    char *end;
    const unsigned char *digit;
    if (!text || !*text) return 0;
    if (text[0] == '0' && text[1]) return 0;
    for (digit = (const unsigned char *)text; *digit; digit++) {
        if (*digit < '0' || *digit > '9') return 0;
    }
    errno = 0;
    *out = strtoul(text, &end, 10);
    return !errno && !*end && *out <= 0xFFFFFFFFul;
}

static int i32(const char *text, long *out) {
    char *end;
    if (!text || !*text) return 0;
    errno = 0;
    *out = strtol(text, &end, 10);
    return !errno && !*end && *out >= INT_MIN && *out <= INT_MAX;
}

/* `count` finite doubles in strtod's syntax (so a sign, leading white space or a
 * hexadecimal form is accepted), separated by single commas and nothing else. */
/* strtod may round a nonzero magnitude to zero without setting ERANGE. Its
 * consumed mantissa, rather than errno, distinguishes that loss from a zero
 * explicitly supplied by the caller. The exponent's digits are not mantissa. */
static int nonzero_mantissa(const char *first, const char *last) {
    const char *p = first;
    int hex = 0;
    while (p < last && isspace((unsigned char)*p)) p++;
    if (p < last && (*p == '+' || *p == '-')) p++;
    if (last - p >= 2 && p[0] == '0' && (p[1] == 'x' || p[1] == 'X')) {
        hex = 1;
        p += 2;
    }
    for (; p < last; p++) {
        if ((!hex && (*p == 'e' || *p == 'E'))
            || (hex && (*p == 'p' || *p == 'P'))) break;
        if ((*p >= '1' && *p <= '9')
            || (hex && ((*p >= 'a' && *p <= 'f')
                        || (*p >= 'A' && *p <= 'F')))) return 1;
    }
    return 0;
}
static int doubles(const char *text, double *out, int count) {
    char *end;
    int i;
    if (!text) return 0;
    for (i = 0; i < count; i++) {
        errno = 0;
        out[i] = strtod(text, &end);
        /* ERANGE may mean a representable subnormal, not just overflow.
         * Whether a zeroed underflow sets it is implementation-defined. */
        if (end == text || !isfinite(out[i])
            || (out[i] == 0.0 && nonzero_mantissa(text, end))
            || (errno && errno != ERANGE)) return 0;
        text = end;
        if (i + 1 < count) {
            if (*text != ',') return 0;
            text++;
        }
    }
    return !*text;
}

/* Up to `limit` positive integers separated by single commas. Returns how many
 * were read, or 0 for anything malformed. */
static int ints(const char *text, int *out, int limit) {
    char *end;
    int count = 0;
    long value;
    if (!text || !*text) return 0;
    for (;;) {
        errno = 0;
        value = strtol(text, &end, 10);
        if (end == text || errno || value < 1 || value > INT_MAX || count >= limit) return 0;
        out[count++] = (int)value;
        text = end;
        if (*text != ',') break;
        text++;
    }
    return *text ? 0 : count;
}

/* Read one bounded whitespace-separated field from Python's numeric spectra
 * file. An overlong field is malformed; never let scanf convert an unbounded
 * integer before its range can be checked. */
static int spectrum_token(FILE *file, char *text, size_t capacity) {
    size_t used = 0;
    int c;
    do {
        c = fgetc(file);
    } while (c != EOF && isspace((unsigned char)c));
    if (c == EOF) return 0;
    do {
        if (c == 0 || used + 1 >= capacity) return 0;
        text[used++] = (char)c;
        c = fgetc(file);
    } while (c != EOF && !isspace((unsigned char)c));
    text[used] = '\0';
    return 1;
}

static int spectrum_header(FILE *file, int *bands, double *start, double *end,
                           double *norm, int *count) {
    char text[128];
    long value;
    if (!spectrum_token(file, text, sizeof(text)) || !i32(text, &value)) return 0;
    *bands = (int)value;
    if (!spectrum_token(file, text, sizeof(text)) || !doubles(text, start, 1)) return 0;
    if (!spectrum_token(file, text, sizeof(text)) || !doubles(text, end, 1)) return 0;
    if (!spectrum_token(file, text, sizeof(text)) || !doubles(text, norm, 1)) return 0;
    if (!spectrum_token(file, text, sizeof(text)) || !i32(text, &value)) return 0;
    *count = (int)value;
    return 1;
}

/* Load a calibration spectral set from a plain numeric file.
 *
 * Deliberately NOT CGATS. xicc/ccss.c's reader depends on the cgats library and
 * on disptech string lookup, and this project keeps measurement parsing in
 * Python: src/color_measurement/colorimeter_correction.py has
 * already read and validated the .ccss. This reads back what it wrote.
 *
 * It arrives as a file rather than an argument because one .ccss may hold 601
 * bands (XSPECT_MAX_BANDS, xspect.h:81) times N samples, and Linux caps a single
 * argv element at 128 KiB (MAX_ARG_STRLEN).
 *
 * Layout, whitespace separated, no header line:
 *     <bands> <start_nm> <end_nm> <norm> <samples>
 *     then <samples> x <bands> values, sample-major
 *
 * The fields set per sample are exactly those XSPECT_SET_INFO sets
 * (xspect.h:94-100), which is what read_ccss_cgats fills for each sample. */
static int load_spectra(const char *path, cm_configuration *config) {
    FILE *file;
    int bands, count, i, j;
    double start, end, norm;
    int trailing;
    xspect *samples;

    if (!path || !(file = cm_open_input_file(path))) return 0;
    if (!spectrum_header(file, &bands, &start, &end, &norm, &count)
     || bands < 2 || bands > XSPECT_MAX_BANDS
     || count < 1 || count > CM_MAX_CAL_SAMPLES
     || !isfinite(start) || !isfinite(end) || start <= 0.0 || end <= start
     || !isfinite(norm) || norm <= 0.0) {
        fclose(file);
        return 0;
    }
    if (!(samples = (xspect *)calloc((size_t)count, sizeof(xspect)))) {
        fclose(file);
        return 0;
    }
    for (i = 0; i < count; i++) {
        samples[i].spec_n = bands;
        samples[i].spec_wl_short = start;
        samples[i].spec_wl_long = end;
        samples[i].norm = norm;
        for (j = 0; j < bands; j++) {
            char text[128];
            if (!spectrum_token(file, text, sizeof(text))
             || !doubles(text, &samples[i].spec[j], 1)
             || !isfinite(samples[i].spec[j] / norm)) {
                free(samples);
                fclose(file);
                return 0;
            }
        }
    }
    /* Anything left over means the header disagreed with the body, which is a
     * miscount somewhere upstream of here and not something to read past.
     *
     * Checked character by character rather than with fscanf("%lf"): a matching
     * failure returns 0, not 1, so scanning for a number accepts a file whose
     * tail is a word or a comment and rejects only one whose tail is numeric. */
    while ((trailing = fgetc(file)) != EOF) {
        if (!isspace((unsigned char)trailing)) {
            free(samples);
            fclose(file);
            return 0;
        }
    }
    fclose(file);
    config->samples = samples;
    config->sample_count = count;
    return 1;
}

/* The eight sheet navigation vectors, in chartread.c's own order
 * (:1305-1306): ox oy ax ay aax aay px py. */
static int cm_navigation(const char *text, cm_xy_request *request) {
    double v[8];
    if (!doubles(text, v, 8)) return 0;
    request->origin[0] = v[0];         request->origin[1] = v[1];
    request->pass_increment[0] = v[2]; request->pass_increment[1] = v[3];
    request->pass_offset[0] = v[4];    request->pass_offset[1] = v[5];
    request->step_increment[0] = v[6]; request->step_increment[1] = v[7];
    return 1;
}

static void start(const char *event) {
    fputs("{\"protocol_version\":1,\"provenance\":", wire);
    string(cm_provenance());
    fputs(",\"event\":", wire); string(event);
}

static int fail(const char *stage, inst_code code) {
    start("error"); fputs(",\"stage\":", wire); string(stage);
    fprintf(wire, ",\"native_code\":%u}\n", (unsigned)code);
    return 0;
}

/* The same envelope for a failure the DRIVER returned, carrying the driver's own
 * words as every upstream caller prints them: interp_error with the whole
 * inst_code (spotread.c:397, :1217, :1233). The text is the driver's; nothing
 * here parses it for facts, which travel typed in native_facts instead. It is
 * bounded, and a longer text is cut and says so. Escaping is string()'s: each
 * byte at or above 0x80 becomes its own \u00XX, so a cut inside a multibyte
 * sequence still leaves valid JSON. */
#define CM_NATIVE_TEXT_MAX 512
static void native_text(inst *meter, inst_code code) {
    const char *text = meter && meter->interp_error ? meter->interp_error(meter, code) : NULL;
    char bounded[CM_NATIVE_TEXT_MAX + 1];
    int truncated = 0;
    if (text && strlen(text) > CM_NATIVE_TEXT_MAX) {
        memcpy(bounded, text, CM_NATIVE_TEXT_MAX);
        bounded[CM_NATIVE_TEXT_MAX] = '\0';
        text = bounded;
        truncated = 1;
    }
    fprintf(wire, ",\"native_code\":%u,\"native_text\":", (unsigned)code);
    string(text);
    fprintf(wire, ",\"native_text_truncated\":%s", truncated ? "true" : "false");
}

static int native_fail(inst *meter, const char *stage, inst_code code) {
    start("error"); fputs(",\"stage\":", wire); string(stage);
    native_text(meter, code);
    fputs("}\n", wire);
    return 0;
}

/* For a linked driver support adapter (native_facts.h): it writes whole events
 * with the session's own envelope and escaping rather than a second JSON writer. */
void cm_event_start(const char *event) { start(event); }
void cm_json_string(const char *value) { string(value); }

/* Line protocol, matching the per-device helpers: "v1 <operation-id> <action>
 * [argument]". The operation id is pinned on first sight so a second client
 * cannot answer this session's prompts. */
static int command(char *action, char *argument, int wait) {
    static char line[512];
    static size_t used;
    char version[8], id[65], extra[2], byte;
    int count, ready;
    for (;;) {
        ready = cm_stdin_byte(&byte, wait);
        if (ready == CM_IO_ERROR) { bad_input = 1; return 0; }
        if (ready == CM_IO_PENDING) return 0;
        if (ready == CM_IO_EOF) { cancelled = 1; return 0; }
        if (byte == '\n') { line[used] = 0; used = 0; break; }
        if (!byte || used >= sizeof(line) - 1) { bad_input = 1; return 0; }
        line[used++] = byte;
    }
    argument[0] = 0;
    count = sscanf(line, "%7s %64s " CM_SCAN_ACTION " " CM_SCAN_ARGUMENT " %1s",
                   version, id, action, argument, extra);
    if (count < 3 || count > 4 || strcmp(version, "v1")) { bad_input = 1; return 0; }
    if (!operation_id[0]) {
        size_t i;
        for (i = 0; id[i]; i++)
            if (!(isalnum((unsigned char)id[i]) || id[i] == '-' || id[i] == '_')) {
                bad_input = 1; return 0;
            }
        strcpy(operation_id, id);
    } else if (strcmp(id, operation_id)) { bad_input = 1; return 0; }
    if (!strcmp(action, "cancel") && count == 3) { cancelled = 1; return 0; }
    return 1;
}

#include "display_timing.h"

/* Operator intent, collapsed to one abort at the driver boundary exactly as
 * instappsup.c:69-71 does. inst_negcoms is answered ok so that opening comms is
 * never mistaken for an armed instrument. Programmatic triggering must never
 * return inst_user_trig: upstream can turn that into a success with no reading. */
static inst_code callback(void *context, inst_ui_purp purpose) {
    char action[CM_ACTION_MAX + 1], argument[CM_ARGUMENT_MAX + 1];
    (void)context;
    if (cm_timing_active) return cm_timing_aborted() ? inst_user_abort : inst_ok;
    if (purpose == inst_negcoms) return inst_ok;
    if (command(action, argument, 0)) bad_input = 1;   /* nothing else is expected here */
    return cancelled || bad_input ? inst_user_abort : inst_ok;
}

/* --usb-index N --expected-serial S --instrument "<name Argyll accepts>".
 * Argument parsing only: it opens nothing and resolves no name. */
int cm_parse_selection(int argc, char **argv, cm_selection *out) {
    long index;
    if (argc < 7 || strcmp(argv[1], "--usb-index") || strcmp(argv[3], "--expected-serial")
        || strcmp(argv[5], "--instrument")) return -1;
    if (argv[2][0] < '0' || argv[2][0] > '9'
        || !i32(argv[2], &index) || index < 1) return -1;
    /* An EMPTY serial is "do not pin", not a malformed argument. inst.c:99-101
     * defines the default get_serial_no as returning "" and inst.c:755-756 installs
     * it for every driver that does not override it -- the DTP22/41/51/92/94, HCFR,
     * SMCube, SpyderX and SpyderX2 all inherit it. Requiring a non-empty pin would
     * make those nine instruments unattachable, which is the opposite of the point. */
    if (strlen(argv[4]) > 200 || !argv[6][0] || strlen(argv[6]) > 200)
        return -1;
    out->index = (int)index;
    out->serial = argv[4];
    out->instrument = argv[6];
    out->identify_serial = 0;
    out->path = NULL;
    int consumed = 6;
    /* One optional trailing pair. Optional because every existing caller and both
     * substituted instruments pass three pairs, and a serial identification is a
     * permission a caller grants deliberately rather than a field it must fill. */
    if (argc >= 9 && !strcmp(argv[7], "--allow-serial-identification")) {
        if (strcmp(argv[8], "0") && strcmp(argv[8], "1")) return -1;
        out->identify_serial = argv[8][0] == '1';
        consumed = 8;
    }
    if (argc >= consumed + 3 && !strcmp(argv[consumed+1], "--expected-path")) {
        if (!argv[consumed+2][0] || strlen(argv[consumed+2]) > 4096) return -1;
        out->path = argv[consumed+2];
        consumed += 2;
    }
    return consumed;
}

/* One operator exchange. `ask` opens the event so the caller can add its own
 * fields and close it; `answer` blocks for the reply and collapses cancellation
 * and protocol abuse into a published failure. */
static void ask(const char *instruction, const char *request_id) {
    start("instruction_required");
    fputs(",\"instruction\":", wire); string(instruction);
    fputs(",\"request_id\":", wire); string(request_id);
}

static int answer(char *action, char *argument) {
    if (!command(action, argument, 1))
        return fail(cancelled ? "cancelled" : "protocol",
                    cancelled ? inst_user_abort : inst_internal_error);
    return 1;
}

static int read_allowance(inst *meter) {
    char action[CM_ACTION_MAX + 1], argument[CM_ARGUMENT_MAX + 1];
    double remaining, started;
    if (read_allowance_s <= 0 || !cm_native_wants_allowance(meter)) return 1;
    started = cm_monotonic_s();
    start("read_allowance_required");
    fputs("}\n", wire);
    if (!answer(action, argument)) return 0;
    if (strcmp(action, "read_allowance") || !doubles(argument, &remaining, 1)
     || !isfinite(remaining) || remaining <= 0 || remaining > read_allowance_s)
        return fail("protocol", inst_internal_error);
    /* Deduct the whole exchange, conservatively including the request transit
     * that the client already counted. No shared cross-process clock epoch is
     * assumed. Never pass zero: the driver interprets that as no limit. */
    remaining -= cm_monotonic_s() - started;
    cm_native_read_allowance(meter, remaining > 0.001 ? remaining : 0.001);
    return 1;
}

static int native_controls(cm_binding *bound) {
    cm_control_fields fields;
    inst_code rv;
    if (!bound->controls_action) return 1;
    if (!cm_native_wants_allowance(bound->meter)) return fail("native_controls_unavailable", inst_unsupported);
    if (!cm_control_fields_parse(bound->controls_fields, &fields)) return fail("native_controls_fields", inst_bad_parameter);
    if (!read_allowance(bound->meter)) return 0;
    rv = cm_native_controls_run(bound->meter, bound->controls_action, &fields);
    if (rv != inst_ok) return native_fail(bound->meter, "native_controls", rv);
    return 1;
}

static int acknowledged(const char *instruction, const char *request_id) {
    char action[CM_ACTION_MAX + 1], argument[CM_ARGUMENT_MAX + 1];
    ask(instruction, request_id);
    fputs("}\n", wire);
    if (!answer(action, argument)) return 0;
    if (strcmp(action, "acknowledge")) return fail("protocol", inst_internal_error);
    return 1;
}

/* A driver that finds its sensor, dial or adapter where this mode cannot work
 * returns inst_wrong_config, "Retry with correct inst. config./sensor posn.
 * needed" (inst.h:156). spotread.c:2323-2326 and chartread.c:1643-1645 report it
 * and go back to the operator's key; instappsup.c:210-233 does the same for
 * calibrate(). It is the operator's to fix: the ColorMunki's dial
 * (munki_imp.c:2191-2206), the i1Display's diffuser arm (i1d3.c:2318-2346), the
 * i1Pro 3's adapter or tile (i1pro3_imp.c:7561-7564, :9589-9600). The driver
 * names the position it needs through config_enum (inst.h:1166-1167); the
 * default new_inst installs answers inst_conf_unknown (inst.c:588-589, :825-826),
 * and a substituted instrument may leave the slot empty, which is the same answer.
 *
 * Only a DRIVER's return comes here. This file's own refusals reuse
 * inst_wrong_config for a request that does not fit -- serial_mismatch,
 * patch_count, sheet_geometry and the rest -- and no position fixes those, so
 * the callers pass the driver's read_sample, read_strip or calibrate() code and
 * nothing else. One acknowledged retry for calibrating and one for reading,
 * bounded like recalibrate_read's: each step needs one position, and the two can
 * need different ones -- the i1Pro 3's tile to calibrate, then its ambient head to
 * read (i1pro3_imp.c:7561-7564, :9589-9593).
 * Return 1 to retry, 0 when no retry applies, -1 when the exchange failed. */
#define CM_RETRIED_CALIBRATE 1
#define CM_RETRIED_READ 2
static int reposition(cm_binding *bound, inst_code rv, const char *stage) {
    char action[CM_ACTION_MAX + 1], argument[CM_ARGUMENT_MAX + 1];
    const int step = strcmp(stage, "calibrate") ? CM_RETRIED_READ : CM_RETRIED_CALIBRATE;
    inst_config needed;
    if ((rv & inst_mask) != inst_wrong_config || cancelled || (bound->position_retried & step))
        return 0;
    bound->position_retried |= step;
    needed = bound->meter->config_enum ? bound->meter->config_enum(bound->meter, (int)rv)
                                       : inst_conf_unknown;
    ask("sensor_position", "position-retry");
    fputs(",\"stage\":", wire); string(stage);
    fprintf(wire, ",\"configuration\":%d", (int)needed);
    native_text(bound->meter, rv);
    fputs("}\n", wire);
    if (!answer(action, argument)) return -1;
    if (strcmp(action, "acknowledge")) return fail("protocol", inst_internal_error) - 1;
    return 1;
}

/* The ladder. `want` is the composed mode word; `need2` the inst2 facilities the
 * requested workflow needs (0 for none). `spectral_required` says whether a
 * missing inst_mode_spectral is a fault for THIS instrument class or simply means
 * a colorimeter -- no capability word distinguishes those, so it is policy the
 * caller states rather than something this file can infer. */
int cm_bind(inst_mode want, inst2_capability need2, int spectral_required,
            int wants_scan_events, const cm_configuration *config, cm_binding *out) {
    inst_mode caps = inst_mode_none, mode = want;
    inst2_capability caps2 = inst2_none;
    inst3_capability caps3 = inst3_none;
    inst_opt_type trigger;
    const char *serial;
    inst_code rv;

    inst *meter;
    /* Upstream's i1Pro, i1Pro3 and ColorMunki drop the spectrum before the XRGA
     * conversion in XYZ-only mode (repaired in the broad build's composed drivers by
     * xyz_only_conversion.py); DTP20/22/41 and SpectroScan return conversion
     * spectra despite XYZ-only (dtp20.c:555-556, ss.c:1287-1289). The result
     * shape is still inconsistent across drivers, so refuse the combination
     * before opening an instrument. Native/none request no conversion.
     * xrga.c:200-202 skips a patch without spectrum data. */
    if (config->xyz_only && config->xcalstd >= 0)
        return fail("xcalstd_requires_spectrum", inst_unsupported);
    if (config->xcalstd != xcalstd_none
        && (want & inst_mode_illum_mask) != inst_mode_reflection
        && (want & inst_mode_illum_mask) != inst_mode_s_reflection)
        return fail("xcalstd_requires_reflection", inst_unsupported);
    if ((config->refresh_frequency || config->display_timing) && (config->highres || config->xyz_only
        || config->averages || config->xcalstd != xcalstd_none
        || config->filter != inst_opt_filter_unknown || config->correction != CM_CORRECTION_NONE
        || config->refresh_rate_given))
        return fail(config->display_timing ? "timing_configuration" : "refresh_configuration", inst_bad_parameter);
    meter = cm_attach(callback, NULL);
    if (!meter) {
        const char *why = cm_attach_stage();
        return fail(why ? why : "attach", inst_coms_fail);
    }
    /* The binding owns the instrument from the moment it exists, not from the
     * moment the ladder succeeds. Every upstream caller closes with it->del(it) on
     * each of its own failure paths (spotread.c:1605, chartread.c:1135); assigning
     * here does the same thing once, because main() releases the binding whatever
     * the outcome. Setting it only on success would leave the instrument open on
     * every refusal below -- a real device left claimed by a process that has
     * given up on it. */
    out->meter = meter;
    out->controls_action = config->controls_action;
    out->controls_fields = config->controls_fields;
    meter->set_uicallback(meter, callback, NULL);
    if (wants_scan_events) {
        if (!meter->set_event_callback) return fail("event_callback", inst_unsupported);
        meter->set_event_callback(meter, cm_read_event, NULL);
    }
    /* 15.0 s, matching every upstream caller (chartread.c:487, dispsup.c:286,
     * spotread.c:1214, illumread.c:525). A serial instrument needs it: ss.c:206
     * uses this deadline for a sweep across seven baud rates. */
    if ((rv = meter->init_coms(meter, baud_nc, fc_nc, 15.0)) != inst_ok) {
        cm_native_facts(meter);
        return native_fail(meter, "init_coms", rv);
    }
    if (cancelled) return fail("cancelled", inst_user_abort);
    /* Acquisition authorizes ordinary driver initialization, not firmware writes.
     * spyd2.c:init_inst downloads PLD for Spyder1/2. Description also refuses
     * these models; enforce the prohibition for direct acquisition callers. */
    if (meter->dtype == instSpyder1 || meter->dtype == instSpyder2)
        return fail("initialization_downloads_pld", inst_unsupported);
    if ((rv = meter->init_inst(meter)) != inst_ok) {
        cm_native_facts(meter);
        return native_fail(meter, "init_inst", rv);
    }

    serial = meter->get_serial_no(meter);
    if (serial && !serial[0]) serial = NULL;
    if (serial && strlen(serial) > CM_NATIVE_IDENTITY_TEXT_LIMIT)
        return fail("serial_invalid", inst_wrong_config);
    if (cm_expected_serial() && (!serial || strcmp(serial, cm_expected_serial())))
        return fail("serial_mismatch", inst_wrong_config);

    /* One helper runs per operation, so every reading is a fresh open, and these
     * drivers ask for their full calibration at every open even when the one they
     * restored is valid (i1pro3_imp.c:666-668 with :1083-1093). A reading asks
     * them, as spotread -N does after init and before set_mode (spotread.c:
     * 1538-1540), to keep what they retained; what they still need after that is
     * their own validity judgement and is negotiated as any other need. A
     * refusal leaves the driver's default -- more calibration, never less -- and
     * the outcome says which applied. */
    if (config->reads && cm_waives_initial_calibration(meter))
        out->initial_calibration_suppressed =
            meter->get_set_opt(meter, inst_opt_noinitcalib, 0) == inst_ok;

    /* Initialize before checking modes: the implementations consult driver
     * state, and ss.c:2191 populates cap from an
     * unconfirmed dtype that init_inst then re-derives (ss.c:550). */
    meter->capabilities(meter, &caps, &caps2, &caps3);

    cm_collect_mode_checks(meter, caps, &out->baseline_mode_checks);

    /* Spectral is added only when advertised. A colorimeter carries no such bit
     * (i1d3.c:3902-3908); a spectrophotometer that has lost it is faulty, which is
     * why the caller states which of the two this instrument is. A caller may also
     * ask for XYZ alone from a spectral instrument, and then the bit stays off.
     * That is this host's request, not upstream practice: spotread adds the bit
     * whenever it is advertised (spotread.c:1586-1588). A driver whose spectral
     * read costs time or checks of its own (cr250.c) honours its absence. */
    if (config->xyz_only) {
        if (spectral_required) return fail("protocol", inst_internal_error);
        /* Leaving out a spectrum the instrument cannot return is a request made
         * against the wrong instrument, refused as highres is. */
        if (!IMODETST(caps, inst_mode_spectral))
            return fail("xyz_only_unavailable", inst_unsupported);
        mode &= ~inst_mode_spectral;
    }
    else if (IMODETST(caps, inst_mode_spectral)) mode |= inst_mode_spectral;
    else if (spectral_required) return fail("spectral_unavailable", inst_unsupported);

    /* Refresh measurement is mode-dependent in i1pro.c:179-181,
     * i1pro3.c:162-164 and munki.c:160-162. Its definitive capability
     * check belongs after set_mode, not on the initialized reflective state. */
    {
        inst2_capability initial_need = need2;
        if (config->refresh_frequency) initial_need &= ~inst2_emis_refr_meas;
        if (config->display_timing) initial_need &= ~inst2_meas_disp_update;
        if ((caps2 & initial_need) != initial_need)
            return fail("facilities_unavailable", inst_unsupported);
    }

    /* No advisory pre-check on the requested word. check_mode is the probe:
     * drivers test `m & ~cap -> inst_unsupported` (i1d3.c:4000), or delegate to
     * a conversion/validation helper. Argyll's own applications have no `(caps & mode) != mode`
     * gate anywhere; they ask and then degrade (chartread.c:752-790). Adding one
     * here would only turn a recoverable refusal into a hard failure, and mint a
     * stage name no driver asked for. */
    /* check_mode is the ONE vtable slot new_inst does not backfill: inst.c:749-799
     * fills every other NULL entry with a default -- set_mode at :761-762, and a
     * static check_mode default does exist at inst.c:140 -- but nothing installs
     * it. Every linked acquisition driver assigns its own, so this is
     * unreachable today; it is guarded because a NULL call is the difference
     * between a named refusal and a crash, and because a substituted instrument
     * is not built through new_inst at all. */
    if (!meter->check_mode) return fail("check_mode_unavailable", inst_unsupported);
    if ((rv = meter->check_mode(meter, mode)) != inst_ok) return fail("check_mode", rv);
    if ((rv = meter->set_mode(meter, mode)) != inst_ok) return fail("set_mode", rv);

    /* set_mode may change the capabilities (inst.h:826-827), so every decision
     * from here uses the second read. ss.c:414-421 is the proof on a third
     * driver: it drops the three inst2_xy_* bits once the mode is transmission. */
    meter->capabilities(meter, &caps, &caps2, &caps3);
    if ((caps2 & need2) != need2) return fail("facilities_changed", inst_unsupported);

    /* Configuration, between the mode and the trigger, in the order the upstream
     * applications apply it: the mode-dependent second round first
     * (spotread.c:1610-1628), the display type next (dispsup.c:363-381, which is
     * where the only application that sets one puts it), then the reflective
     * options (spotread.c:1631-1677).
     *
     * One deliberate divergence, applied to all of them: where an interactive
     * upstream application warns and carries on with what the instrument CAN do,
     * this refuses and names the stage. spotread has an operator reading the
     * warning; a programmatic caller does not, and a reading made under
     * conditions other than the ones requested is not the reading that was asked
     * for. Earlier configuration steps may already have changed instrument state.
     *
     * The filter and the calibration standard are sent without a capability
     * pre-check, exactly as spotread.c:1631-1637 and :1670-1677 do: the driver's
     * refusal is the answer, and a pre-check here would only guess at it.
     */
    if (config->highres) {
        if (!IMODETST(caps, inst_mode_highres))
            return fail("highres_unavailable", inst_unsupported);
        /* Deferred to here, not composed into the first word: inst_mode_highres is
         * mode dependent, so it may only appear in the capability word AFTER the
         * first set_mode (inst.h:826-827). This second round is the reason a
         * factored bind routine could not express this ladder. */
        mode |= inst_mode_highres;
        if ((rv = meter->check_mode(meter, mode)) != inst_ok) return fail("check_mode", rv);
        if ((rv = meter->set_mode(meter, mode)) != inst_ok) return fail("set_mode", rv);
        meter->capabilities(meter, &caps, &caps2, &caps3);
        if ((caps2 & need2) != need2) return fail("facilities_changed", inst_unsupported);
    }
    cm_collect_display_types(meter,caps2,&out->display_types);
    out->display_selected=-1;
    cm_emit_display_types(wire,&out->display_types,start,string);
    if (config->display_type >= 0) {
        char identity[65];
        if (out->display_types.code!=inst_ok || !meter->set_disptype)
            return fail("display_type_unavailable",out->display_types.code);
        if (!config->display_catalogue_id || !config->display_choice_id
            || strcmp(config->display_catalogue_id,out->display_types.digest)
            || config->display_type>=out->display_types.count
            || !cm_display_identity(&out->display_types.rows[config->display_type],
                                    config->display_type,identity)
            || strcmp(config->display_choice_id,identity))
            return fail("display_type_stale",inst_wrong_config);
        if (cm_display_text_reason(&out->display_types.rows[config->display_type]))
            return fail("display_type_unavailable",inst_wrong_config);
        if (config->correction!=CM_CORRECTION_NONE
            && (out->display_types.rows[config->display_type].flags
                & (inst_dtflags_ccmx | inst_dtflags_ccss)))
            return fail("display_correction_conflict",inst_bad_parameter);
        if ((out->display_types.rows[config->display_type].flags & inst_dtflags_ccmx)
            && (mode & inst_mode_illum_mask)==inst_mode_emission
            && (mode & inst_mode_sub_mask)==inst_mode_ambient
            && !cm_ambient_ccmx(meter))
            return fail("ambient_display_matrix_unsupported",inst_unsupported);
        if ((rv=meter->set_disptype(meter,config->display_type))!=inst_ok)
            return fail("set_display_type",rv);
        out->display_selected=config->display_type;
    }
    if (config->filter != inst_opt_filter_unknown) {
        rv = meter->get_set_opt(meter, inst_opt_set_filter, (inst_opt_filter)config->filter);
        /* A fixed optical filter has a getter but no setter (i1pro.c:802-818).
         * Accept an unsupported setter only when the actual filter already
         * matches. A successful setter can instead record a pending change:
         * ss.c:1908-1938 sets p->filt, and :1479-1489 asks the operator to change
         * the physical filter during calibration. Verify that case afterward. */
        if (rv != inst_ok) {
            inst_opt_filter actual = inst_opt_filter_unknown;
            inst_code readback;
            if ((rv & inst_mask) != inst_unsupported) return fail("set_filter", rv);
            readback = meter->get_set_opt(meter, inst_stat_get_filter, &actual);
            if (readback != inst_ok && (readback & inst_mask) != inst_unsupported)
                return fail("filter_readback", readback);
            if (readback != inst_ok || actual != (inst_opt_filter)config->filter)
                return fail("set_filter", rv);
        }
    }
    if (config->averages > 0) {
        if (!(caps3 & inst3_average)) return fail("averaging_unavailable", inst_unsupported);
        if ((rv = meter->get_set_opt(meter, inst_opt_set_averages, config->averages)) != inst_ok)
            return fail("set_averages", rv);
    }
    if (config->xcalstd != xcalstd_none
        && (rv = meter->get_set_opt(meter, inst_opt_set_xcalstd,
                                    (xcalstd)config->xcalstd)) != inst_ok)
        return fail("set_xcalstd", rv);

    /* The colorimeter correction goes LAST of the configuration steps, and that
     * order is load-bearing rather than tidy: set_disptype "clears col_cor_mat()
     * and col_cal_spec_set()" (inst.h:843-845). Applying a correction before the
     * display type would silently discard it, and the reading that followed would
     * be uncorrected while every capability check still passed. spotread.c sets
     * the display type at :1508 and the correction at :1680 for this reason.
     *
     * Both slots are backfilled by inst.c:811-813, so a driver that implements
     * neither still has a callable entry returning inst_unsupported; the
     * capability word is the real gate, and it is checked first so the refusal
     * names the missing facility rather than the driver's generic refusal. */
    int applied = CM_CORRECTION_NONE;
    if (config->correction != CM_CORRECTION_NONE
     && IMODETST(mode, inst_mode_emis_ambient)
     && !(cm_ambient_ccmx(meter) && config->correction == CM_CORRECTION_MATRIX)) {
        /* Source-backed exception: kleink10.c:1408 applies CCMX unconditionally,
         * and :1423 chooses ambient units from its scale. i1disp, huey, spyd2,
         * spydX, spydX2 and i1d3 bypass CCMX for ambient. ColorHug advertises no
         * ambient route. Keep unassessed correction behavior unavailable. */
        return fail("correction_not_applied_to_ambient", inst_unsupported);
    }
    if (cm_matrix_sets_type(meter)) {
        /* Refused before anything is measured: the reading would come back
         * typed for the other route, and no route may publish it. */
        const int labels_ambient = config->correction == CM_CORRECTION_MATRIX
                                   && cm_matrix_labels_ambient(config->matrix);
        if (IMODETST(mode, inst_mode_emis_ambient) && !labels_ambient)
            return fail("ambient_matrix_required", inst_unsupported);
        if (!IMODETST(mode, inst_mode_emis_ambient) && labels_ambient)
            return fail("ambient_matrix_on_emissive_route", inst_unsupported);
    }
    if (config->correction == CM_CORRECTION_MATRIX) {
        /* col_cor_mat takes double[3][3] (inst.h:1106) while `config` is const
         * here, so passing config->matrix discards a qualifier: a C constraint
         * violation, a new warning against a previously warning-free build, and a
         * hard error under -Werror. The copy is three rows; the const stays. */
        double matrix[3][3];
        if (!(caps2 & inst2_ccmx)) return fail("correction_matrix_unavailable", inst_unsupported);
        memcpy(matrix, config->matrix, sizeof(matrix));
        if ((rv = meter->col_cor_mat(meter, (disptech)config->correction_dtech,
                                     config->correction_cbid, matrix)) != inst_ok)
            return fail("set_correction_matrix", rv);
        applied = CM_CORRECTION_MATRIX;
    } else if (config->correction == CM_CORRECTION_SPECTRAL) {
        if (!(caps2 & inst2_ccss)) return fail("correction_spectral_unavailable", inst_unsupported);
        /* spotread.c:1727 sets the observer inside the ccss branch and before the
         * samples, unconditionally. icxOT_default (xspect.h:343) is the value it
         * passes when no -Q was given, and it means "the instrument's default,
         * usually CIE 1931 2 degree". A custom observer is a separate feature and
         * is not offered here; passing the default keeps the one call upstream
         * makes rather than leaving the observer at whatever a prior session set. */
        if ((rv = meter->get_set_opt(meter, inst_opt_set_ccss_obs,
                                     icxOT_default, (xspect *)NULL)) != inst_ok)
            return fail("set_correction_observer", rv);
        if ((rv = meter->col_cal_spec_set(meter, (disptech)config->correction_dtech,
                                          config->samples, config->sample_count)) != inst_ok)
            return fail("set_correction_spectra", rv);
        applied = CM_CORRECTION_SPECTRAL;
    }

    /* dispsup.c:2407-2418 applies the rate after display/correction selection.
     * i1d3 update_refmode invalidates it when effective refresh mode changes. */
    meter->capabilities(meter, &caps, &caps2, &caps3);
    if (config->refresh_rate_given) {
        if (!(caps2 & inst2_set_refresh_rate) || !meter->set_refr_rate)
            return fail("refresh_rate_unavailable", inst_unsupported);
        if ((rv = meter->set_refr_rate(meter, config->refresh_rate)) != inst_ok)
            return native_fail(meter, "set_refresh_rate", rv);
        meter->capabilities(meter, &caps, &caps2, &caps3);
    }

    /* The trigger is CHOSEN from the capability word, never assumed. No upstream
     * application assumes one: chartread.c:1412-1432 and spotread.c:1790-1806 both
     * walk the bits and abort only when none is offered.
     *
     * dtp51.c:809 is why this matters: it sets inst2_switch_trig and nothing else,
     * commented "Can only be triggered by microswitch". It is the one acquisition
     * driver in the pinned tree without inst2_prog_trig, so asking for a
     * programmatic trigger unconditionally excluded the DTP51 from the ladder
     * entirely -- and the DTP51 is the instrument this file's own strip comments
     * name most often.
     *
     * Order differs from chartread deliberately and for one reason: a programmatic
     * caller has no operator at a keyboard, so a trigger it can raise itself comes
     * first. inst_opt_trig_user is NOT offered at all -- it is raised by the
     * uicallback returning inst_user_trig, which this file's callback must never
     * do (see callback() above), so accepting it would arm a trigger nothing can
     * pull. The instrument's own switch is a real alternative and is offered. */
    if (config->inspect_only || config->refresh_frequency || config->display_timing) trigger = inst_opt_trig_prog;
    else if (caps2 & inst2_prog_trig) trigger = inst_opt_trig_prog;
    else if (caps2 & inst2_switch_trig) trigger = inst_opt_trig_switch;
    else if (caps2 & inst2_user_switch_trig) trigger = inst_opt_trig_user_switch;
    else return fail("no_trigger_available", inst_unsupported);
    if (!config->inspect_only && !config->refresh_frequency && !config->display_timing
        && (rv = meter->get_set_opt(meter, trigger)) != inst_ok)
        return fail("set_trigger", rv);

    out->meter = meter;
    out->confirmed_mode = mode;
    out->caps = caps; out->caps2 = caps2; out->caps3 = caps3;
    out->spectral = IMODETST(mode, inst_mode_spectral);   /* as bound, not as advertised */
    out->trigger = config->inspect_only || config->refresh_frequency || config->display_timing
        ? (inst_opt_type)-1 : trigger;
    out->filter = (inst_opt_filter)config->filter;
    out->serial = serial;
    out->correction = applied;
    out->refresh_rate_given = config->refresh_rate_given;
    out->refresh_rate = config->refresh_rate;
    out->calibration_allowed = 0;
    out->calibration_retries = 0;
    out->position_retried = 0;
    return 1;
}

/* Readiness, not setter success, is the point at which the actual filter must
 * match. A getter is optional, but a failed getter is not an absent getter.
 * ss.c:2019 can return a communication failure; never swallow its native code.
 * Per-patch mcond validation in Python remains required as a separate check. */
static int verify_filter(const cm_binding *bound) {
    inst_opt_filter actual = inst_opt_filter_unknown;
    inst_code rv;
    if (bound->filter == inst_opt_filter_unknown) return 1;
    rv = bound->meter->get_set_opt(bound->meter, inst_stat_get_filter, &actual);
    if ((rv & inst_mask) == inst_unsupported) return 1;
    if (rv != inst_ok) return fail("filter_readback", rv);
    if (actual != bound->filter) return fail("filter_readback", inst_wrong_config);
    return 1;
}

void cm_bind_report(const cm_binding *bound) {
    char trigger_text[32];
    if ((int)bound->trigger == -1) strcpy(trigger_text, "null");
    else snprintf(trigger_text, sizeof(trigger_text), "%u", (unsigned)bound->trigger);
    start("connected");
    fputs(",\"serial\":", wire); serial_string(bound->serial);
    fputs(",\"serial_encoding\":", wire); string(serial_encoding(bound->serial));
    fprintf(wire, ",\"mode\":%u,\"mode_capabilities\":%u,\"trigger_capabilities\":%u,"
                  "\"filter_capabilities\":%u,\"trigger\":%s,\"spectral\":%s,"
                  "\"ambient_ccmx\":%s",
            (unsigned)bound->confirmed_mode, (unsigned)bound->caps, (unsigned)bound->caps2,
            (unsigned)bound->caps3, trigger_text,
            bound->spectral ? "true" : "false",
            cm_ambient_ccmx(bound->meter) ? "true" : "false");
    cm_print_matrix_threshold(wire, bound->meter);
    cm_print_strip_policies(wire, bound->meter);
    cm_print_mode_checks(wire, &bound->baseline_mode_checks);
    cm_print_display_types(wire,&bound->display_types,
                           bound->meter,bound->display_selected,string);
    fputs(",\"refresh_rate_override_hz\":", wire);
    if (bound->refresh_rate_given) fprintf(wire, "%.17g", bound->refresh_rate);
    else fputs("null", wire);
    fprintf(wire, ",\"correction\":\"%s\"}\n",
            /* The explicit correction option accepted by the driver. Factory
             * or display-type calibration is separate; "none" does not mean
             * that every native calibration/correction is absent. */
            bound->correction == CM_CORRECTION_MATRIX ? "matrix"
            : bound->correction == CM_CORRECTION_SPECTRAL ? "spectral" : "none");
}

void cm_bind_release(cm_binding *bound) {
    if (bound->meter && bound->meter->del) bound->meter->del(bound->meter);
    bound->meter = NULL;
}

/* Calibration negotiation, for any instrument.
 *
 * Needed-driven: ask get_n_a_cals what this instrument needs now, request exactly
 * that, and let the driver drive. Both per-device helpers answer "how paranoid"
 * three different ways (round caps 4 and 8, an acknowledgement mask against a
 * bool, three different recheck rules) and none of those differences is device
 * driven. This keeps the one rule upstream has: loop until calibrate() stops
 * returning inst_cal_setup, with a bound so a driver that never settles cannot
 * hang the session.
 *
 * `calc` IS BIDIRECTIONAL. inst.h:1048 declares it "Current condition/desired
 * condition", and inst.h:1027-1029 says to retry "with the current setup
 * indicated by *calc". Every driver tests it on entry and re-requests when it is
 * wrong -- i1d3.c:3416-3418 is the shortest example:
 *
 *     if ((*calc & inst_calc_cond_mask) != inst_calc_emis_80pc) {
 *         *calc = inst_calc_emis_80pc;
 *         return inst_cal_setup;
 *     }
 *
 * so it MUST carry forward between rounds, exactly as upstream's own loop does
 * (instappsup.c:186-189 declares calc outside the loop and never resets it).
 * Clearing it each round makes the driver re-request the same condition until the
 * round cap, which is a calibration that can never succeed on any real driver.
 *
 * The operator answers acknowledge or skip. Skip is only offered when the driver
 * sets inst_calc_optional_flag, and answering it means calling calibrate() again
 * WITH THE FLAG STILL SET (inst.h:1035-1040; instappsup.c:411-413 loops back
 * without clearing it), while proceeding clears it (:421). Getting that backwards
 * would silently perform a calibration the operator declined.
 */
#define CM_CAL_ROUNDS 8
#define CM_CAL_GREY_TRIES 4

/* The display patch an emissive condition asks for, as a fraction of full white.
 *
 * dispsup.c:172-204 is the canonical implementation, and it lives in the
 * APPLICATION: no driver reads `inst->cal_gy_level` -- only dispsup.c and
 * instappsup.c write it -- so nothing computes this for us. Here the CLIENT owns
 * the display, so the level has to reach it or it would have to guess, and "a
 * grey patch" is not a colour.
 *
 * `emis_grey` starts the ladder at 0.6; `darker` multiplies by 0.7, `ligher` by
 * 1.4 clamped to 1.0, and after four adjustments upstream gives up
 * (dispsup.c:186-200). Returns nz when a level applies to this condition.
 */
static int cm_display_level(inst_cal_cond calc, double *level, int *tries) {
    switch (calc & inst_calc_cond_mask) {
        case inst_calc_emis_white:  *level = 1.0; return 1;
        case inst_calc_emis_80pc:   *level = 0.8; return 1;
        case inst_calc_emis_grey:   *level = 0.6; *tries = 0; return 1;
        case inst_calc_emis_grey_darker: *level *= 0.7; (*tries)++; return 1;
        case inst_calc_emis_grey_ligher:
            *level *= 1.4;
            if (*level > 1.0) *level = 1.0;
            (*tries)++;
            return 1;
        default: return 0;
    }
}

int cm_calibrate(cm_binding *bound) {
    inst_cal_type needed = inst_calt_none, available = inst_calt_none, calt;
    inst_cal_cond calc = inst_calc_none;
    inst_calc_id_type idtype = inst_calc_id_none;
    char id[CALIDLEN], action[CM_ACTION_MAX + 1], argument[CM_ARGUMENT_MAX + 1];
    inst_code rv;
    int round, skipped = 0, performed = 0, established = 0, clicks, leveled, tries = 0;
    double level = 0.0;

    bound->calibration_setup_requested = 0;
    if ((rv = bound->meter->get_n_a_cals(bound->meter, &needed, &available)) != inst_ok)
        return fail("calibration_status", rv);
    /* A transmission reference belongs to this optical setup. Do not silently
     * reuse a cached white from another sample/light-source arrangement. */
    if ((bound->confirmed_mode & inst_mode_illum_mask) == inst_mode_transmission)
        needed |= available & (inst_calt_trans_white | inst_calt_trans_vwhite);
    if (needed == inst_calt_none) {
        start("calibration_ready");
        fprintf(wire, ",\"needed_calibrations\":0,\"available_calibrations\":%u,"
                      "\"remaining_calibrations\":0,\"status_source\":\"%s\","
                      "\"skipped\":false,\"performed\":false,"
                      "\"initial_calibration_suppressed\":%s}\n", (unsigned)available,
                bound->meter->get_n_a_cals == inst_get_n_a_cals ? "upstream_default" : "driver",
                bound->initial_calibration_suppressed ? "true" : "false");
        return 1;
    }
    /* A driver reporting calibrations it needs but filling no calibrate slot would
     * be dereferenced below. `check_mode` gets the same treatment at bind time and
     * for the same reason: the vtable is the contract, so test the method. */
    if (!bound->meter->calibrate) return fail("calibrate_unavailable", inst_unsupported);
    calt = needed;
    /* calc is NOT reset here: it carries the condition the operator established
     * into the next round. See the note above. */
    for (round = 0; round < CM_CAL_ROUNDS; round++) {
        if (cancelled) return fail("cancelled", inst_user_abort);
        id[0] = 0;
        idtype = inst_calc_id_none;
        {
            inst_cal_type before = calt;
            int optional_skip = (calc & inst_calc_optional_flag) != 0;
            rv = bound->meter->calibrate(bound->meter, &calt, &calc, &idtype, id);
            /* inst.h:1031-1032: the driver clears completed calibration bits.
             * Not every driver does (below), so a call that returns inst_ok under
             * a condition the operator established has performed its work too.
             * Keep work done before a later optional skip, without calling the
             * skipped calibration performed. */
            if (!optional_skip && ((before & ~calt) || (rv == inst_ok && established)))
                performed = 1;
        }
        if (rv == inst_ok) {
            /* inst_ok ends the negotiation, whatever *calt still holds:
             * "calibrate() returns inst_ok when no more calibrations remain"
             * (inst.h:1032-1033), and upstream's loop returns on it
             * (instappsup.c:189-199). Two driver records follow it, and they
             * go stale differently:
             * - *calt, the negotiation's bookkeeping: spydX.c:990-1004 and
             *   spydX2.c never clear the black calibration they did,
             *   kleink10.c:2346 clears a condition bit instead of its type.
             *   It is not read.
             * - get_n_a_cals, the driver's fresh assessment, published as
             *   remaining_calibrations, is preserved even if it still reports
             *   required work. Our dtp92.c source repair clears its completed
             *   need flags; this common path never invents that result. */
            inst_cal_type remaining = inst_calt_none, unused;
            if ((rv = bound->meter->get_n_a_cals(bound->meter, &remaining, &unused)) != inst_ok)
                return fail("calibration_status", rv);
            start("calibration_ready");
            fprintf(wire, ",\"skipped\":%s", skipped ? "true" : "false");
            fprintf(wire, ",\"needed_calibrations\":%u,\"available_calibrations\":%u,"
                          "\"remaining_calibrations\":%u,\"performed\":%s,\"status_source\":\"driver\","
                          "\"initial_calibration_suppressed\":%s,\"reference_type\":%u,\"reference_id\":",
                    (unsigned)needed, (unsigned)available, (unsigned)remaining,
                    performed ? "true" : "false",
                    bound->initial_calibration_suppressed ? "true" : "false", (unsigned)idtype);
            string(id[0] ? id : NULL); fputs("}\n", wire);
            return 1;
        }
        if ((rv & inst_mask) != inst_cal_setup) {
            /* calc is kept: the driver checks the same condition again once the
             * operator has moved what it named (instappsup.c:186-233). */
            int moved = reposition(bound, rv, "calibrate");
            if (moved < 0) return 0;
            if (moved) {
                bound->calibration_setup_requested = 1;
                continue;
            }
            return native_fail(bound->meter, "calibrate", rv);
        }

        leveled = cm_display_level(calc, &level, &tries);
        if (leveled && tries > CM_CAL_GREY_TRIES)
            return fail("grey_level_limit", inst_internal_error);

        /* inst_calc_man_ref_whitek is NOT acknowledged. The operator clicks the
         * instrument down onto its tile and THAT triggers the calibration --
         * dtp22.c:722 says "Calibration only works when triggered by the read
         * switch" -- so upstream skips its own keypress for exactly this
         * condition (instappsup.c:403-405) and calls calibrate() straight back,
         * which blocks in the driver until the click arrives.
         *
         * The client is still TOLD, because it has to show the instruction; the
         * event is a notification rather than a question. Blocking for an answer
         * here would wait on a confirmation that cannot mean anything, and would
         * put the prompt before the driver had even armed the switch. */
        clicks = (calc & inst_calc_cond_mask) == inst_calc_man_ref_whitek;
        /* inst.h:640-669 names physical setup or a display reference. Unlike a
         * pure message (:670), these can leave the sample position or stimulus
         * changed, including a hardware click or an optional setup later skipped.
         * Calibration performed internally is not evidence of operator movement. */
        if ((calc & inst_calc_cond_mask) != inst_calc_message)
            bound->calibration_setup_requested = 1;
        if (clicks) {
            start("calibration_condition");
        } else {
            char request[40];
            snprintf(request, sizeof(request), "calibration-%d", round);
            ask("calibration_setup", request);
        }
        fprintf(wire, ",\"calibration_condition\":%u,\"reference_type\":%u,"
                      "\"needed_calibrations\":%u,\"remaining_calibrations\":%u,"
                      "\"skippable\":%s,\"acknowledged\":%s,\"display_level\":",
                (unsigned)(calc & inst_calc_cond_mask), (unsigned)idtype,
                (unsigned)needed, (unsigned)calt,
                (calc & inst_calc_optional_flag) ? "true" : "false",
                clicks ? "false" : "true");
        if (leveled) fprintf(wire, "%.17g", level); else fputs("null", wire);
        fputs(",\"reference_id\":", wire);
        string(id[0] ? id : NULL);
        fputs("}\n", wire);

        if (clicks) {
            /* Upstream clears the skip flag on both paths (instappsup.c:419-420),
             * so the driver sees the condition it asked for on the next call. */
            calc &= inst_calc_cond_mask;
            established = 1;
            continue;
        }

        if (!answer(action, argument)) return 0;
        if (!strcmp(action, "skip")) {
            if (!(calc & inst_calc_optional_flag))
                return fail("calibration_not_skippable", inst_wrong_config);
            /* Leave calc EXACTLY as the driver returned it, optional flag included:
             * that is how the driver is told the operator declined
             * (inst.h:1037-1038, instappsup.c:411-413). */
            skipped = 1;
            established = 0;
            continue;
        }
        if (strcmp(action, "acknowledge")) return fail("protocol", inst_internal_error);
        established = 1;
        /* Proceed: clear the optional flag and hand the condition itself back, so
         * the driver sees the setup it asked for (inst.h:1039-1040,
         * instappsup.c:421). calc keeps its condition bits into the next round. */
        calc &= inst_calc_cond_mask;
    }
    return fail("calibration_limit", inst_internal_error);
}

/* Some drivers learn calibration is needed only from a read response (e.g.
 * dtp41.c:523-524, dtp51.c:502-503). Keep that driver state alive, as spotread.c:
 * 2313-2323 and chartread.c:1625-1645 do. One retry, only when calibration was
 * requested; other errors, stored reads and table positioning never enter here.
 * Return 1 after calibration, -1 on failed recovery, 0 when no retry applies. */
static int recalibrate_read(cm_binding *bound, inst_code rv, const char *stage) {
    if ((rv & inst_mask) != inst_needs_cal || cancelled
     || !bound->calibration_allowed || bound->calibration_retries
     || (bound->confirmed_mode & inst_mode_illum_mask) == inst_mode_s_reflection)
        return 0;
    bound->calibration_retries++;
    start("calibration_retry");
    fprintf(wire, ",\"retry\":1,\"native_code\":%u,\"stage\":", (unsigned)rv);
    string(stage); fputs("}\n", wire);
    return cm_calibrate(bound) && verify_filter(bound) ? 1 : -1;
}

/* One patch, for any instrument. The result union is decided by the capability
 * word, not by the device: a colorimeter returns XYZ and no spectrum, and that
 * absence is reported as null. It is never an empty grid and never zeros --
 * models.py permits an XYZ-only reading and native_protocol.py refuses
 * zero-substitution by name.
 *
 * Validation is separate from publication so that a multi-patch read publishes
 * nothing until every patch has passed. A malformed patch in the middle of a
 * strip must not leave a client holding a shorter strip it believes is complete.
 */
static int patch_valid(const cm_binding *bound, const ipatch *patch) {
    int i;
    if (!patch->XYZ_v && !bound->spectral) return fail("empty_reading", inst_misread);
    if (patch->XYZ_v) for (i = 0; i < 3; i++)
        if (!isfinite(patch->XYZ[i])) return fail("invalid_XYZ", inst_misread);
    if (bound->spectral) {
        if (patch->sp.spec_n < 2 || patch->sp.spec_n > XSPECT_MAX_BANDS
            || !isfinite(patch->sp.spec_wl_short) || !isfinite(patch->sp.spec_wl_long)
            || patch->sp.spec_wl_short <= 0 || patch->sp.spec_wl_long <= patch->sp.spec_wl_short
            || !isfinite(patch->sp.norm) || patch->sp.norm <= 0)
            return fail("invalid_spectrum", inst_misread);
        for (i = 0; i < patch->sp.spec_n; i++)
            if (!isfinite(patch->sp.spec[i])) return fail("invalid_spectrum", inst_misread);
    } else if (patch->sp.spec_n != 0 && !IMODETST(bound->caps, inst_mode_spectral)) {
        /* An instrument that advertises no spectral bit and returns bands anyway
         * is not something to quietly publish. A spectrometer bound without the
         * bit (xyz_only) may still return one -- the SpectroScan table read
         * always does (ss.c:846-852), and its spot read and DTP20/22/41 do when
         * converting (ss.c:1288-1289, dtp20.c:555-557) -- and emit_patch below
         * publishes null for it, as the XYZ-only contract says. */
        return fail("unexpected_spectrum", inst_misread);
    }
    if (!isfinite(patch->duration) || patch->duration < 0)
        return fail("invalid_reading", inst_misread);
    return 1;
}

/* `index` < 0 is a lone reading; otherwise the patch's place in a strip or sheet
 * travels with it, so a client can never mistake an order it did not receive. */
static void emit_patch(const cm_binding *bound, const ipatch *patch,
                       const char *event, int index, int count) {
    int i;
    start(event);
    if (index >= 0) fprintf(wire, ",\"index\":%d,\"patch_count\":%d", index, count);
    fputs(",\"argyll_version\":\"3.5.0\",\"backend_reported\":{", wire);
    fprintf(wire, "\"mtype\":%d,\"mcond\":%d,\"XYZ_v\":%d,\"XYZ\":",
            patch->mtype, patch->mcond, patch->XYZ_v);
    if (patch->XYZ_v)
        fprintf(wire, "[%.17g,%.17g,%.17g]", patch->XYZ[0], patch->XYZ[1], patch->XYZ[2]);
    else fputs("null", wire);
    fprintf(wire, ",\"duration\":%.17g,\"spectrum\":", patch->duration);
    if (!bound->spectral) { fputs("null}}\n", wire); return; }
    fprintf(wire, "{\"spec_n\":%d,\"wavelength_start_nm\":%.17g,\"wavelength_end_nm\":%.17g,"
                  "\"norm\":%.17g,\"samples\":[",
            patch->sp.spec_n, patch->sp.spec_wl_short, patch->sp.spec_wl_long, patch->sp.norm);
    for (i = 0; i < patch->sp.spec_n; i++)
        fprintf(wire, "%s%.17g", i ? "," : "", patch->sp.spec[i]);
    fputs("]}}}\n", wire);   /* samples, spectrum, backend_reported, event */
}

/* A whole pass or sheet: validate every patch, then publish every patch. */
static int emit_patches(const cm_binding *bound, const ipatch *patches,
                        const char *event, int count) {
    int i;
    for (i = 0; i < count; i++)
        if (!patch_valid(bound, &patches[i])) return 0;
    for (i = 0; i < count; i++)
        emit_patch(bound, &patches[i], event, i, count);
    return 1;
}

/* State retrieval, not read_refrate or an ipatch. DTP92 may query device memory.
 * Without inst2_get_refresh_rate in the configured word nothing is called, so
 * the frame reports no native code rather than one no driver returned. */
static int cm_stored_refresh(cm_binding *bound) {
    double value = NAN;
    inst_code rv;
    const char *status = "unsupported";
    bound->meter->capabilities(bound->meter, &bound->caps, &bound->caps2, &bound->caps3);
    if (!(bound->caps2 & inst2_get_refresh_rate) || !bound->meter->get_refr_rate) {
        start("stored_refresh_rate");
        fputs(",\"native_method\":\"get_refr_rate\",\"native_code\":null,"
              "\"status\":\"not_advertised\",\"unit\":\"Hz\",\"value\":null}\n", wire);
        return 1;
    }
    rv = bound->meter->get_refr_rate(bound->meter, &value);
    if (rv == inst_ok) status = isfinite(value) && value > 0 ? "available" : "invalid";
    else if ((rv & inst_mask) == inst_needs_cal) status = "needs_calibration";
    else if ((rv & inst_mask) == inst_misread) status = "undetermined";
    else if ((rv & inst_mask) != inst_unsupported) status = "failed";
    start("stored_refresh_rate");
    fprintf(wire, ",\"native_method\":\"get_refr_rate\",\"native_code\":%u,\"status\":\"%s\",\"unit\":\"Hz\",\"value\":", (unsigned)rv, status);
    if (!strcmp(status, "available")) fprintf(wire, "%.17g", value);
    else fputs("null", wire);
    fputs("}\n", wire);
    if (!strcmp(status, "invalid")) return fail("invalid_stored_refresh_rate", rv);
    if (!strcmp(status, "failed")) return native_fail(bound->meter, "get_refresh_rate", rv);
    return 1;
}

int cm_read_refresh(cm_binding *bound) {
    inst2_capability current = inst2_none;
    inst_code rv;
    double hz = 0.0;
    /* inst.h's read_refrate contract returns a fresh Hz measurement, not an
     * ipatch or the last stored refresh rate. Recheck after configuration and
     * calibration; set_mode and set_disptype may change driver capabilities. */
    bound->meter->capabilities(bound->meter, NULL, &current, NULL);
    if (!(current & inst2_emis_refr_meas) || !bound->meter->read_refrate)
        return fail("refresh_frequency_unavailable", inst_unsupported);
    if (cancelled) return fail("cancelled", inst_user_abort);
    rv = bound->meter->read_refrate(bound->meter, &hz);
    cm_native_facts(bound->meter);
    if (cancelled && (rv & inst_mask) == inst_user_abort)
        return fail("cancelled", rv);
    /* No automatic second method call: a failed call, including needs_cal, is
     * retained as failure. The client may explicitly start a new operation. */
    if (rv != inst_ok) return native_fail(bound->meter, "read_refrate", rv);
    if (!isfinite(hz) || hz <= 0.0) {
        start("error");
        fprintf(wire, ",\"stage\":\"invalid_refresh_frequency\",\"native_code\":%u,"
                      "\"native_text\":\"read_refrate returned invalid frequency %.17g Hz\","
                      "\"native_text_truncated\":false}\n", (unsigned)rv, hz);
        return 0;
    }
    start("refresh_frequency");
    fprintf(wire, ",\"quantity\":\"refresh_frequency\",\"unit\":\"Hz\","
                  "\"native_method\":\"read_refrate\",\"native_code\":0,\"value\":%.17g}\n", hz);
    return 1;
}

/* One reading, for any instrument.
 *
 * `positioned` asks for the motorised-table form of a spot read
 * (spotread.c:2165-2277): the operator sights the point, the head is driven to it
 * to measure, and the sight is driven back afterwards. Without it a spot read on
 * a table measures wherever the head was left, which is why upstream gates the
 * whole branch on inst2_xy_locate AND inst2_xy_position rather than on identity.
 * A caller that asks for it from an instrument without a table is refused; a
 * caller that does not ask gets the plain read on any instrument.
 */
int cm_read_spot(cm_binding *bound, int positioned) {
    const int table = (bound->caps2 & (inst2_xy_locate | inst2_xy_position))
                    == (inst2_xy_locate | inst2_xy_position);
    const int holds = (bound->caps2 & inst2_xy_holdrel) != 0;
    double lx = 0.0, ly = 0.0;
    ipatch patch;
    inst_code rv, restored = inst_ok;
    int recovery;

    if (positioned == 2) positioned = table;

    memset(&patch, 0, sizeof(patch));
    if (cancelled) return fail("cancelled", inst_user_abort);
    if (positioned) {
        if (!table) return fail("positioning_unavailable", inst_unsupported);
        /* Hold the table before the head moves (spotread.c:1951-1963). */
        if (holds && (rv = bound->meter->xy_sheet_hold(bound->meter)) != inst_ok)
            return fail("xy_sheet_hold", rv);
        if ((rv = bound->meter->xy_locate_start(bound->meter)) != inst_ok)
            return fail("xy_locate_start", rv);
        if (!acknowledged("xy_locate", "locate-spot")) return 0;
        if ((rv = bound->meter->xy_get_location(bound->meter, &lx, &ly)) != inst_ok)
            return fail("xy_get_location", rv);
        if (!isfinite(lx) || !isfinite(ly)) return fail("invalid_location", inst_misread);
        if ((rv = bound->meter->xy_locate_end(bound->meter)) != inst_ok)
            return fail("xy_locate_end", rv);
        start("xy_location");
        fprintf(wire, ",\"index\":0,\"x\":%.17g,\"y\":%.17g}\n", lx, ly);
        /* measure=1 puts the head over the sighted point (spotread.c:2251). */
        if ((rv = bound->meter->xy_position(bound->meter, 1, lx, ly)) != inst_ok)
            return fail("xy_position", rv);
    }

    /* Preserve signed native XYZ; do not clamp a measured value to zero. */
    if (!native_controls(bound)) return 0;
    if (!read_allowance(bound->meter)) return 0;
    rv = bound->meter->read_sample(bound->meter, "SPOT", &patch, instNoClamp);
    /* The driver's facts for THIS attempt, before anything judges it and before a
     * later attempt could clear them -- a failed or warning-bearing read keeps its
     * native replies even though it publishes no measurement. */
    cm_native_facts(bound->meter);

    /* Restore the sight over the measured point BEFORE judging the reading.
     * spotread.c:2262-2276 runs its restore unconditionally after read_sample,
     * whatever that returned: a misread still leaves the head parked over the
     * patch, and leaving it there is a worse state to hand back than the misread
     * itself. Returning early on the read result would skip it. */
    if (positioned) {
        restored = bound->meter->xy_position(bound->meter, 0, lx, ly);
    }
    if (cancelled && (rv & inst_mask) == inst_user_abort)
        return fail("cancelled", inst_user_abort);   /* drivers OR a sub-code in */
    if (!positioned && (recovery = recalibrate_read(bound, rv, "read_sample"))) {
        if (recovery < 0 || !acknowledged("sample_position", "sample-retry")) return 0;
        return cm_read_spot(bound, 0);
    }
    if (!positioned && (recovery = reposition(bound, rv, "read_sample")))
        return recovery < 0 ? 0 : cm_read_spot(bound, 0);
    /* A button-triggered read is successful (spotread.c:2372-2374). */
    if (rv != inst_ok && (rv & inst_mask) != inst_user_trig)
        return native_fail(bound->meter, "read_sample", rv);
    if (!patch_valid(bound, &patch)) return 0;
    emit_patch(bound, &patch, "result", -1, 0);
    /* Retain the valid reading even if returning the sight failed. The session
     * still fails and does not claim that the instrument is ready. */
    if (restored != inst_ok) return fail("xy_restore", restored);
    return 1;
}

/* A sheet or a pass can be large -- a full chart page is hundreds of patches --
 * but it is bounded, and an unbounded count would be an allocation a client
 * chooses. */
#define CM_MAX_PATCHES 4096

/* One strip pass, for any strip-capable instrument.
 *
 * The scan tolerance is applied only when the instrument advertises it
 * (inst2_has_scan_toll), exactly as chartread.c does before its own strip loop;
 * a caller that asks for one the instrument cannot honour is refused rather than
 * quietly given the driver's default. Requiring that facility for every
 * strip would exclude the DTP41 and DTP51, which can use their defaults.
 */
static int strip_text(const char *text, size_t maximum) {
    if (!text || !*text || strlen(text) > maximum) return 0;
    for (; *text; text++) if ((unsigned char)*text < 32 || (unsigned char)*text > 126) return 0;
    return 1;
}

static int validate_strip(const cm_binding *bound, const cm_strip_request *r) {
    cm_strip_policy p = cm_strip_policy_for(bound->meter->dtype, bound->confirmed_mode);
    if (!p.max_patches) return fail("strip_operands_unassessed", inst_unsupported);
    if (r->patch_count < 1 || r->patch_count > p.max_patches)
        return fail("strip_patch_count", inst_bad_parameter);
    if (p.names && (!strip_text(r->strip_name, 7) || !strip_text(r->pass_name, 3)))
        return fail("strip_names", inst_bad_parameter);
    if (p.guide && (r->guide < 0 || r->guide > 99))
        return fail("strip_guide", inst_bad_parameter);
    if (p.widths && (!isfinite(r->patch_width_mm) || !isfinite(r->gap_width_mm)
        || r->patch_width_mm <= 0 || r->gap_width_mm < 0
        || (p.width_max && (r->patch_width_mm < 0.01
            || r->patch_width_mm > p.width_max || r->gap_width_mm > p.width_max))))
        return fail("strip_widths", inst_bad_parameter);
    if (p.trailer && (!isfinite(r->trailer_width_mm)
        || r->trailer_width_mm < 0 || r->trailer_width_mm > 9999))
        return fail("strip_trailer", inst_bad_parameter);
    return 1;
}

static int strip_position(const cm_binding *bound, const char *request_id) {
    char action[CM_ACTION_MAX + 1], argument[CM_ARGUMENT_MAX + 1];
    cm_strip_policy policy = cm_strip_policy_for(bound->meter->dtype, bound->confirmed_mode);
    ask("strip_position", request_id);
    fputs(",\"effective_trigger\":", wire);
    string(bound->trigger == inst_opt_trig_prog ? "programmatic"
           : bound->trigger == inst_opt_trig_switch ? "instrument_switch" : "user_switch");
    fputs(",\"trigger_guidance\":", wire);
    string(bound->trigger == inst_opt_trig_prog
        ? policy.program_instruction
        : policy.switch_instruction);
    fputs("}\n", wire);
    if (!answer(action, argument)) return 0;
    if (strcmp(action, "acknowledge")) return fail("protocol", inst_internal_error);
    return 1;
}

int cm_read_strip(cm_binding *bound, const cm_strip_request *request) {
    ipatch *patches;
    inst_code rv;
    int ok, recovery;

    if (request->patch_count < 1 || request->patch_count > CM_MAX_PATCHES)
        return fail("patch_count", inst_wrong_config);
    /* new_inst fills every unset vtable slot with a default that answers
     * inst_unsupported (inst.c:779-792), so a driver built through it always has
     * this method and a refusal arrives as a code. A wholly substituted
     * instrument is not built through it and may leave the slot empty. */
    if (!bound->meter->read_strip) return fail("read_strip_unavailable", inst_unsupported);
    if (request->scan_tolerance >= 0) {
        if (!(bound->caps2 & inst2_has_scan_toll))
            return fail("scan_tolerance_unavailable", inst_unsupported);
        if ((rv = bound->meter->get_set_opt(bound->meter, inst_opt_scan_toll,
                                            request->scan_tolerance)) != inst_ok)
            return fail("set_scan_tolerance", rv);
    }
    if (cancelled) return fail("cancelled", inst_user_abort);
    if (!(patches = (ipatch *)calloc((size_t)request->patch_count, sizeof(ipatch))))
        return fail("allocation", inst_internal_error);

    rv = bound->meter->read_strip(bound->meter, (char *)request->strip_name,
                                  request->patch_count, (char *)request->pass_name,
                                  request->guide, cm_strip_wire_length(request->patch_width_mm),
                                  cm_strip_wire_length(request->gap_width_mm),
                                  cm_strip_wire_length(request->trailer_width_mm), patches);
    /* inst_user_trig is a hint that a user command may be waiting, not a failure:
     * chartread.c:1551-1552 excludes it from the error test. Treating it as one
     * would discard a pass the instrument actually read. */
    if (rv != inst_ok && (rv & inst_mask) != inst_user_trig) {
        free(patches);
        if ((recovery = recalibrate_read(bound, rv, "read_strip"))) {
            if (recovery < 0 || !strip_position(bound, "strip-retry")) return 0;
            return cm_read_strip(bound, request);
        }
        if ((recovery = reposition(bound, rv, "read_strip")))
            return recovery < 0 ? 0 : cm_read_strip(bound, request);
        if (cancelled && (rv & inst_mask) == inst_user_abort) return fail("cancelled", rv);
        return native_fail(bound->meter, "read_strip", rv);
    }
    ok = emit_patches(bound, patches, "strip_patch", request->patch_count);
    free(patches);
    return ok;
}

/* One X-Y sheet, for any instrument with a motorised table.
 *
 * The sequence is chartread.c:1070-1360 with its console prompts turned into
 * protocol events: clear the table, place the sheet, hold it, let the operator
 * sight each fiducial, end location, read, release. Each vtable step is taken
 * only when the capability word offers it (inst2_xy_holdrel, inst2_xy_locate),
 * which is what makes this the SpectroScan's route and not the SpectroScan's
 * code -- ss.c:414-421 withdraws all three bits in transmission, and this
 * follows without naming the instrument.
 *
 * The navigation vectors come back from the caller. chartread.c:1254-1300 derives
 * them from the sighted points with algebra that branches on hexagonal versus
 * rectangular layout and on how many fiducials were taken; that is chart
 * geometry, and putting it here would make every future chart layout a C change.
 */
int cm_read_xy(cm_binding *bound, cm_xy_request *request) {
    char action[CM_ACTION_MAX + 1], argument[CM_ARGUMENT_MAX + 1];
    const int holds = (bound->caps2 & inst2_xy_holdrel) != 0;
    ipatch *patches;
    inst_code rv;
    int i, ok;

    if (request->patch_count < 1 || request->patch_count > CM_MAX_PATCHES
     || request->passes < 1 || request->steps < 1
     || request->passes > CM_MAX_PATCHES || request->steps > CM_MAX_PATCHES
     || request->patch_count > request->passes * request->steps
     || request->locations < 0 || request->locations > 3)
        return fail("sheet_geometry", inst_wrong_config);
    if (!bound->meter->read_xy) return fail("read_xy_unavailable", inst_unsupported);  /* inst.c:789-790 */
    if (request->locations && !(bound->caps2 & inst2_xy_locate))
        return fail("xy_locate_unavailable", inst_unsupported);

    /* Make sure the table is reachable before anything is placed on it
     * (chartread.c:1070-1073). */
    if (holds && (rv = bound->meter->xy_clear(bound->meter)) != inst_ok)
        return fail("xy_clear", rv);
    if (!acknowledged("xy_sheet", "sheet")) return 0;
    if (holds && (rv = bound->meter->xy_sheet_hold(bound->meter)) != inst_ok)
        return fail("xy_sheet_hold", rv);

    if (request->locations) {
        if ((rv = bound->meter->xy_locate_start(bound->meter)) != inst_ok)
            return fail("xy_locate_start", rv);
        for (i = 0; i < request->locations; i++) {
            char request_id[40];
            double x = 0.0, y = 0.0;
            snprintf(request_id, sizeof(request_id), "locate-%d", i);
            ask("xy_locate", request_id);
            fprintf(wire, ",\"location_index\":%d,\"location_count\":%d}\n",
                    i, request->locations);
            if (!answer(action, argument)) return 0;
            if (strcmp(action, "acknowledge")) return fail("protocol", inst_internal_error);
            if ((rv = bound->meter->xy_get_location(bound->meter, &x, &y)) != inst_ok)
                return fail("xy_get_location", rv);
            if (!isfinite(x) || !isfinite(y)) return fail("invalid_location", inst_misread);
            start("xy_location");
            fprintf(wire, ",\"index\":%d,\"x\":%.17g,\"y\":%.17g}\n", i, x, y);
        }
        if ((rv = bound->meter->xy_locate_end(bound->meter)) != inst_ok)
            return fail("xy_locate_end", rv);

        ask("xy_navigate", "navigate");
        fprintf(wire, ",\"location_count\":%d}\n", request->locations);
        if (!answer(action, argument)) return 0;
        if (strcmp(action, "navigate")) return fail("protocol", inst_internal_error);
        if (!cm_navigation(argument, request)) return fail("navigation", inst_wrong_config);
    }

    if (!(patches = (ipatch *)calloc((size_t)request->patch_count, sizeof(ipatch))))
        return fail("allocation", inst_internal_error);
    /* read_xy does not use the trigger mode (inst.h:935), so unlike a strip there
     * is no inst_user_trig to forgive here. */
    rv = bound->meter->read_xy(bound->meter, request->passes, request->steps,
                               request->patch_count, (char *)request->pass_name,
                               (char *)request->step_name,
                               request->origin[0], request->origin[1],
                               request->pass_increment[0], request->pass_increment[1],
                               request->pass_offset[0], request->pass_offset[1],
                               request->step_increment[0], request->step_increment[1],
                               patches);
    if (rv != inst_ok) {
        free(patches);
        if (cancelled && (rv & inst_mask) == inst_user_abort) return fail("cancelled", rv);
        return native_fail(bound->meter, "read_xy", rv);
    }
    ok = emit_patches(bound, patches, "xy_patch", request->patch_count);
    free(patches);
    if (!ok) return 0;

    /* Release the table and reset the head (chartread.c:1344-1353). Upstream
     * releases only after a successful sheet; an aborted one is left to del(). */
    if (holds && (rv = bound->meter->xy_clear(bound->meter)) != inst_ok)
        return fail("xy_release", rv);
    return 1;
}

/* A whole chart in one call, for an instrument that stored it.
 *
 * chartread.c:987-1043. There is no placement prompt and no trigger: the
 * instrument already holds the readings, which is what the saved illumination
 * bit means. The patch `loc` labels chartread fills in beforehand are its own
 * bookkeeping -- the only upstream implementation overwrites them
 * (dtp20.c:546) -- so a zeroed array is the correct input.
 */
int cm_read_chart(cm_binding *bound, const cm_chart_request *request) {
    int sheets[CM_MAX_SHEETS + 1], count = 0, total = 0, grid_patches, ok;
    ipatch *patches;
    inst_code rv;

    if (request->patch_count < 1 || request->patch_count > CM_MAX_PATCHES
     || request->passes < 1 || request->passes > CM_MAX_PATCHES
     || request->steps < 1 || request->steps > CM_MAX_PATCHES)
        return fail("chart_geometry", inst_wrong_config);
    /* DTP20 walks every pass and step, including padding in a short last pass.
     * Bound and allocate that entire grid even though only patch_count entries
     * are measurements the caller requested. */
    if (request->passes > CM_MAX_PATCHES / request->steps)
        return fail("chart_geometry", inst_wrong_config);
    grid_patches = request->passes * request->steps;
    if (request->patch_count > grid_patches)
        return fail("chart_geometry", inst_wrong_config);
    while (count < CM_MAX_SHEETS && request->sheets[count]) {
        if (request->sheets[count] < 1 || request->sheets[count] > request->passes - total)
            return fail("chart_geometry", inst_wrong_config);
        total += request->sheets[count];
        sheets[count] = request->sheets[count];
        count++;
    }
    /* The per-sheet pass counts must account for exactly the chart's passes;
     * otherwise the driver walks off the end of an array this call allocated. */
    if (!count || total != request->passes)
        return fail("chart_geometry", inst_wrong_config);
    sheets[count] = 0;   /* chartread.c:1061 terminates the list with a zero */
    if (!bound->meter->read_chart) return fail("read_chart_unavailable", inst_unsupported);
    if (cancelled) return fail("cancelled", inst_user_abort);

    if (!(patches = (ipatch *)calloc((size_t)grid_patches, sizeof(ipatch))))
        return fail("allocation", inst_internal_error);
    rv = bound->meter->read_chart(bound->meter, request->patch_count, request->passes,
                                  request->steps, sheets, request->chart_id, patches);
    if (rv != inst_ok) {
        free(patches);
        if (cancelled && (rv & inst_mask) == inst_user_abort) return fail("cancelled", rv);
        return native_fail(bound->meter, "read_chart", rv);
    }
    ok = emit_patches(bound, patches, "chart_patch", request->patch_count);
    free(patches);
    return ok;
}

/* Whether the instrument is holding readings, and of what kind.
 *
 * inst_stat_saved_readings is a RUNTIME STATUS, not a capability (inst.h:467-468):
 * an instrument that can store a chart may or may not have one stored right now.
 * chartread.c:900-931 asks it before preferring a saved route over a live one,
 * and nothing in the three capability words answers it. Published here so a
 * caller can make the same choice; the query itself is only made when the caller
 * asks, because an instrument that cannot store readings refuses it.
 */
int cm_report_saved_readings(cm_binding *bound, unsigned *stored) {
    inst_stat_savdrd saved = inst_stat_savdrd_none;
    inst_code rv = bound->meter->get_set_opt(bound->meter, inst_stat_saved_readings, &saved);
    if (rv != inst_ok) return fail("saved_readings", rv);
    if (stored) *stored = (unsigned)saved;
    start("saved_readings");
    fprintf(wire, ",\"stored\":%u}\n", (unsigned)saved);
    return 1;
}

/* Entry point. Every instrument-specific decision arrives as an argument: which
 * instrument to attach (consumed by the linked attach unit), the composed mode
 * word, the facilities the workflow needs, whether a missing spectral bit is a
 * fault, and the chart geometry of a strip or sheet. No mode route name and no
 * device name appears below; the option sentinels that do (inst_opt_filter_unknown,
 * xcalstd_none) are upstream's own "not requested" values.
 */
static void usage(void) {
    fputs("Invalid session arguments\n", stderr);
}

CM_PROCESS_MAIN(cm_session_main) {
    unsigned long want = 0, need2 = 0, spectral_required = 0, scan_events = 0, read = 0,
                  calibrate = 0, positioned = 0, native_debug_level = 0;
    cm_strip_request strip;
    cm_xy_request sheet;
    cm_configuration config;
    cm_chart_request chart;
    int strip_requested = 0, xy_requested = 0, chart_requested = 0, saved_readings = 0;
    int snapshot_requested = 0, stored_refresh = 0;
    unsigned stored = 0;
    int consumed, i, ok;
    long number;
    double widths[3];
    cm_binding bound;

    memset(&bound, 0, sizeof(bound));
    memset(&strip, 0, sizeof(strip));
    memset(&sheet, 0, sizeof(sheet));
    memset(&config, 0, sizeof(config));
    memset(&chart, 0, sizeof(chart));
    config.filter = inst_opt_filter_unknown;   /* inst.h:564 */
    config.xcalstd = xcalstd_none;             /* insttypes.h:141 */
    config.display_type = -1;
    config.correction_cbid = 1;    /* inst.h:1105, "needed 1 if unknown" */
    /* chartread.c:1551 names the pass "STRIP"; a driver that ignores names is
     * unaffected and the DTP51 gets something to print. */
    strip.strip_name = "STRIP";
    strip.pass_name = "1";
    strip.scan_tolerance = -1.0;     /* leave the driver's own tolerance alone */
    strip.guide = -1;               /* absent; a consumed operand must be supplied */
    strip.patch_width_mm = strip.gap_width_mm = strip.trailer_width_mm = -1.0;
    sheet.pass_name = " A";          /* inst.h:942-943 */
    sheet.step_name = " 1";
    sheet.pass_increment[0] = 1.0;   /* chartread.c:1086-1088 defaults */
    sheet.step_increment[1] = 1.0;

    /* The attach unit takes its own device selection off the front. A wholly
     * substituted instrument selects nothing and takes none. */
    if ((consumed = cm_configure(argc, argv)) < 0) { usage(); return 2; }

    for (i = 1 + consumed; i < argc; i += 2) {
        const char *flag = argv[i], *value;
        if (i + 1 >= argc) { usage(); return 2; }   /* every flag takes a value */
        value = argv[i + 1];
        if (!strcmp(flag, "--mode-word")) { if (!u32(value, &want)) { usage(); return 2; } }
        else if (!strcmp(flag, "--native-debug-level")) {
            if (!u32(value, &native_debug_level) || native_debug_level > 8) { usage(); return 2; }
        }
        else if (!strcmp(flag, "--facilities")) {
            if (!u32(value, &need2)) { usage(); return 2; }
        } else if (!strcmp(flag, "--spectral-required")) {
            if (!u32(value, &spectral_required) || spectral_required > 1) { usage(); return 2; }
        } else if (!strcmp(flag, "--scan-events")) {
            if (!u32(value, &scan_events) || scan_events > 1) { usage(); return 2; }
        } else if (!strcmp(flag, "--read")) {
            if (!u32(value, &read) || read > 1) { usage(); return 2; }
        } else if (!strcmp(flag, "--mode-snapshot") || !strcmp(flag, "--stored-refresh")) {
            unsigned long v;
            if (!u32(value, &v) || v > 1) { usage(); return 2; }
            if (!strcmp(flag, "--mode-snapshot")) snapshot_requested = (int)v;
            else stored_refresh = (int)v;
        } else if (!strcmp(flag, "--refresh-rate")) {
            if (config.refresh_rate_given || !doubles(value, &config.refresh_rate, 1)
                || !isfinite(config.refresh_rate)
                || (config.refresh_rate != 0.0
                    && (config.refresh_rate < 5.0 || config.refresh_rate > 150.0))) { usage(); return 2; }
            config.refresh_rate_given = 1;
        } else if (!strcmp(flag, "--read-refresh")) {
            unsigned long value32;
            if (!u32(value, &value32) || value32 > 1) { usage(); return 2; }
            config.refresh_frequency = (int)value32;
        } else if (!strcmp(flag, "--read-timing")) {
            unsigned long value32;
            if (!u32(value, &value32) || value32 > 1) { usage(); return 2; }
            config.display_timing = (int)value32;
        } else if (!strcmp(flag, "--read-allowance-s")) {
            if (!doubles(value, &read_allowance_s, 1) || !isfinite(read_allowance_s)
                || read_allowance_s <= 0) { usage(); return 2; }
        } else if (!strcmp(flag, "--controls-action")) {
            if (config.controls_action || (strcmp(value, "read") && strcmp(value, "apply"))) { usage(); return 2; }
            config.controls_action = value;
        } else if (!strcmp(flag, "--controls-fields")) {
            cm_control_fields parsed;
            if (config.controls_fields || !cm_control_fields_parse(value, &parsed)) { usage(); return 2; }
            config.controls_fields = value;
        } else if (!strcmp(flag, "--xyz-only")) {
            unsigned long value32;
            if (!u32(value, &value32) || value32 > 1) { usage(); return 2; }
            config.xyz_only = (int)value32;
        } else if (!strcmp(flag, "--highres")) {
            unsigned long value32;
            if (!u32(value, &value32) || value32 > 1) { usage(); return 2; }
            config.highres = (int)value32;
        } else if (!strcmp(flag, "--filter")) {
            unsigned long value32;
            if (!u32(value, &value32) || value32 > 0xFFFF) { usage(); return 2; }
            config.filter = (unsigned)value32;
        } else if (!strcmp(flag, "--averages")) {
            if (!i32(value, &number) || number < 1 || number > 1000) { usage(); return 2; }
            config.averages = (int)number;
        } else if (!strcmp(flag, "--xcalstd")) {
            if (!i32(value, &number) || number < xcalstd_none || number > xcalstd_xrga) {
                usage(); return 2;
            }
            config.xcalstd = (int)number;
        } else if (!strcmp(flag, "--display-catalogue-id")) {
            if (strlen(value)!=64 || strspn(value,"0123456789abcdef")!=64) { usage(); return 2; }
            config.display_catalogue_id=value;
        } else if (!strcmp(flag, "--display-choice-id")) {
            if (strlen(value)!=64 || strspn(value,"0123456789abcdef")!=64) { usage(); return 2; }
            config.display_choice_id=value;
        } else if (!strcmp(flag, "--display-type")) {
            if (!i32(value, &number) || number < 0 || number > 1000) { usage(); return 2; }
            config.display_type = (int)number;
        } else if (!strcmp(flag, "--correction-matrix")) {
            double flat[9];
            int row, column;
            /* One correction per session. A .ccmx and a .ccss are alternative
             * answers to the same question and the second would silently
             * overwrite the first, so asking for both is a caller error. */
            if (config.correction != CM_CORRECTION_NONE) { usage(); return 2; }
            if (!doubles(value, flat, 9)) { usage(); return 2; }
            for (row = 0; row < 3; row++)
                for (column = 0; column < 3; column++)
                    config.matrix[row][column] = flat[row * 3 + column];
            config.correction = CM_CORRECTION_MATRIX;
        } else if (!strcmp(flag, "--calibration-spectra")) {
            if (config.correction != CM_CORRECTION_NONE) { usage(); return 2; }
            if (!load_spectra(value, &config)) { usage(); return 2; }
            config.correction = CM_CORRECTION_SPECTRAL;
        } else if (!strcmp(flag, "--correction-dtech")) {
            /* disptech is an enumeration of explicit hex constants up to 0x5030
             * (disptechs.h); an unlisted value is disptech_unknown to a driver,
             * not an error, so only the width is checked here. */
            if (!i32(value, &number) || number < 0 || number > 0xFFFF) { usage(); return 2; }
            config.correction_dtech = (int)number;
        } else if (!strcmp(flag, "--correction-cbid")) {
            /* inst.h:1105: "Calibration display type base ID, needed 1 if
             * unknown". Zero is not a value a caller may send. */
            if (!i32(value, &number) || number < 1 || number > 255) { usage(); return 2; }
            config.correction_cbid = (int)number;
        } else if (!strcmp(flag, "--position")) {
            if (!u32(value, &positioned) || positioned > 2) { usage(); return 2; }
        } else if (!strcmp(flag, "--calibrate")) {
            if (!u32(value, &calibrate) || calibrate > 1) { usage(); return 2; }
        } else if (!strcmp(flag, "--read-strip")) {
            if (!i32(value, &number)) { usage(); return 2; }
            strip.patch_count = (int)number; strip_requested = 1;
        } else if (!strcmp(flag, "--strip-name")) { strip.strip_name = value; }
        else if (!strcmp(flag, "--strip-pass")) { strip.pass_name = value; }
        else if (!strcmp(flag, "--strip-guide")) {
            if (!i32(value, &number)) { usage(); return 2; }
            strip.guide = (int)number;
        } else if (!strcmp(flag, "--strip-widths")) {
            /* patch, gap and trailer widths in mm (inst.h:961-963) */
            if (!doubles(value, widths, 3)) { usage(); return 2; }
            strip.patch_width_mm = widths[0];
            strip.gap_width_mm = widths[1];
            strip.trailer_width_mm = widths[2];
        } else if (!strcmp(flag, "--scan-tolerance")) {
            if (!doubles(value, &strip.scan_tolerance, 1) || strip.scan_tolerance < 0) {
                usage(); return 2;
            }
        } else if (!strcmp(flag, "--read-xy")) {
            if (!i32(value, &number)) { usage(); return 2; }
            sheet.patch_count = (int)number; xy_requested = 1;
        } else if (!strcmp(flag, "--xy-passes")) {
            if (!i32(value, &number)) { usage(); return 2; }
            sheet.passes = (int)number;
        } else if (!strcmp(flag, "--xy-steps")) {
            if (!i32(value, &number)) { usage(); return 2; }
            sheet.steps = (int)number;
        } else if (!strcmp(flag, "--xy-pass-name")) { sheet.pass_name = value; }
        else if (!strcmp(flag, "--xy-step-name")) { sheet.step_name = value; }
        else if (!strcmp(flag, "--xy-locate")) {
            if (!i32(value, &number)) { usage(); return 2; }
            sheet.locations = (int)number;
        } else if (!strcmp(flag, "--read-chart")) {
            if (!i32(value, &number)) { usage(); return 2; }
            chart.patch_count = (int)number; chart_requested = 1;
        } else if (!strcmp(flag, "--chart-passes")) {
            if (!i32(value, &number)) { usage(); return 2; }
            chart.passes = (int)number;
        } else if (!strcmp(flag, "--chart-steps")) {
            if (!i32(value, &number)) { usage(); return 2; }
            chart.steps = (int)number;
        } else if (!strcmp(flag, "--chart-sheets")) {
            if (!ints(value, chart.sheets, CM_MAX_SHEETS)) { usage(); return 2; }
        } else if (!strcmp(flag, "--chart-id")) {
            if (!i32(value, &number) || number < 0) { usage(); return 2; }
            chart.chart_id = (int)number;
        } else if (!strcmp(flag, "--saved-readings")) {
            unsigned long value32;
            if (!u32(value, &value32) || value32 > 1) { usage(); return 2; }
            saved_readings = (int)value32;
        } else if (!strcmp(flag, "--xy-geometry")) {
            if (!cm_navigation(value, &sheet)) { usage(); return 2; }
        } else { usage(); return 2; }
    }
    /* A word of zero is no mode at all, and one workflow per session: a strip and
     * a sheet are different placements of the same chart and cannot both be read
     * from one binding. */
    /* One workflow per session: a strip, a sheet, a whole chart and a spot are
     * different placements of the same sample and cannot share one binding. */
    if (!want || strip_requested + xy_requested + chart_requested + (int)read
                + config.refresh_frequency + config.display_timing > 1
     || (positioned && !read)) {
        usage();
        return 2;
    }
    if ((config.controls_fields && !config.controls_action)
     || (config.controls_action && (!(read_allowance_s > 0) || (calibrate && !read) || strip_requested
        || xy_requested || chart_requested || snapshot_requested || stored_refresh
        || config.refresh_frequency || config.display_timing
        || (!strcmp(config.controls_action, "read") && (read
            || (config.controls_fields && config.controls_fields[0])))))) {
        usage(); return 2;
    }
    if (config.controls_action && !config.controls_fields) config.controls_fields = "";
    if ((config.refresh_frequency || config.display_timing)
        && !IMODETST((inst_mode)want, inst_mode_emis_spot)
        && !IMODETST((inst_mode)want, inst_mode_emis_tele)) {
        usage();
        return 2;
    }

    /* Keep stdout for the wire and send anything a driver prints to stderr, so a
     * chatty driver cannot corrupt the protocol. */
    wire = cm_open_wire();
    if (!wire) return 2;
    g_log->debug = (int)native_debug_level;
    if (native_debug_level)
        a1logd(g_log, 1, "CM native diagnostic logging level %d\n", g_log->debug);

    if (snapshot_requested && (calibrate || read || strip_requested || xy_requested
            || chart_requested || config.refresh_frequency || config.display_timing)) { usage(); return 2; }
    if (stored_refresh && (config.refresh_frequency || config.display_timing)) { usage(); return 2; }
    config.inspect_only = (config.controls_action && !read) || snapshot_requested || (stored_refresh && !read && !strip_requested
        && !xy_requested && !chart_requested);
    config.reads = strip_requested + xy_requested + chart_requested + (int)read > 0;
    config.reads = config.reads || config.refresh_frequency || config.display_timing;
    if (config.refresh_frequency) {
        need2 |= inst2_emis_refr_meas;
    }
    if (config.display_timing) need2 |= inst2_meas_disp_update;
    ok = cm_bind((inst_mode)want, (inst2_capability)need2, (int)spectral_required,
                 (int)scan_events, &config, &bound);
    bound.calibration_allowed = (int)calibrate;
    if (ok) cm_bind_report(&bound);
    if (ok && strip_requested) ok = validate_strip(&bound, &strip);
    if (positioned == 2) positioned = (bound.caps2 & (inst2_xy_locate | inst2_xy_position))
                                   == (inst2_xy_locate | inst2_xy_position);
    if (ok && calibrate) ok = cm_calibrate(&bound);
    if (ok) ok = verify_filter(&bound);
    if (ok && stored_refresh) ok = cm_stored_refresh(&bound);
    if (ok && bound.calibration_setup_requested && read && !positioned)
        ok = acknowledged("sample_position", "sample-ready");
    if (ok && config.refresh_frequency)
        ok = acknowledged("refresh_stimulus", "refresh-ready");
    if (ok && calibrate && strip_requested)
        ok = strip_position(&bound, "strip-ready");
    /* Every saved acquisition needs its matching runtime status before reading.
     * A stored chart does not establish the existence of a stored spot. */
    if (((inst_mode)want & inst_mode_illum_mask) == inst_mode_s_reflection
        && (read || strip_requested || chart_requested || xy_requested)) saved_readings = 1;
    if (ok && saved_readings) ok = cm_report_saved_readings(&bound, &stored);
    /* A saved route reads what the instrument is already holding. When it holds
     * nothing there is nothing to read, and asking anyway spends an operator's
     * time to arrive at an error from the driver instead of from the status that
     * already said so -- inst.h:467-468 makes this a RUNTIME status precisely so
     * it can be asked first, and chartread.c:900-931 asks it before choosing the
     * branch. The query on its own (no read requested) still reports zero
     * normally: "nothing is stored" is an answer, not a failure. */
    if (ok && saved_readings) {
        unsigned required = read ? inst_stat_savdrd_spot
            : strip_requested ? inst_stat_savdrd_strip
            : chart_requested ? inst_stat_savdrd_chart
            : xy_requested ? inst_stat_savdrd_xy : 0;
        if (required && !(stored & required)) ok = fail("no_saved_readings", inst_ok);
    }
    if (ok && config.controls_action && !read) ok = native_controls(&bound);
    if (ok && read) ok = cm_read_spot(&bound, (int)positioned);
    if (ok && config.refresh_frequency) ok = cm_read_refresh(&bound);
    if (ok && config.display_timing) ok = cm_read_timing(&bound);
    if (ok && strip_requested) ok = cm_read_strip(&bound, &strip);
    if (ok && chart_requested) ok = cm_read_chart(&bound, &chart);
    if (ok && xy_requested) ok = cm_read_xy(&bound, &sheet);
    cm_bind_release(&bound);
    /* After the release, never before -- defensively, and the distinction is
     * worth stating because it is not pinned by any test.
     *
     * Both drivers that accept a spectral set COPY it: i1d3_set_speccal callocs
     * its own array and struct-copies each sample (i1d3.c:2711-2717), and spyd2
     * does the same (spyd2.c:2342). So no driver in the pinned tree retains this
     * pointer, and freeing earlier would in fact be safe today.
     *
     * The order stays conservative anyway, because inst.h:1115-1121 documents
     * nothing about the array's lifetime, so a driver that referenced it instead
     * would be conforming. Moving the free above cm_bind_release survives the
     * check -- reading freed memory is undefined rather than reliably observable,
     * so this rule is documented rather than tested, and that is the honest
     * description of it. */
    free(config.samples);
    start("terminal");
    fprintf(wire, ",\"status\":%s,\"instrument_state\":\"unknown\",\"software_stopped\":true}\n",
            ok ? "\"completed\"" : "\"failed\"");
    if (fclose(wire)) return 2;
    return ok ? 0 : 1;
}
