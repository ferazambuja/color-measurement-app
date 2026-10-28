/* CR-250 support adapter: the driver's retained facts, onto the wire.
 *
 * Linked into every build of session.c that composed the CR-250 contribution, and
 * compiled to nothing otherwise. native_facts.h calls it only for a meter whose
 * dtype is instCR250, instCR100 or instCR300; all use new_cr250.
 *
 * What it publishes is what cr250.c already retained, as the driver holds it. It
 * parses no text, re-queries nothing and re-derives nothing: the replies keep the
 * instrument's own status, code, context and value; the identity is published
 * only when the engine verified it; the budget is labelled as HOST policy, not a
 * device fact. Two things this protocol cannot establish are said rather than
 * implied: the factory calibration's validity is unknown, and XYZ is the native
 * RM XYZ requested unclamped (session.c passes instNoClamp).
 *
 * Replies, settings and writes go out one per event. A capture retains up to
 * CR250_FACTS_MAX replies, each up to CR250_MAX_VALUE bytes before escaping, and
 * a setting or write carries values as long; together they can pass the 64 KiB
 * line the Python client accepts (native_protocol.py). One event each keeps every
 * line bounded without truncating a native fact. */
#include "session.h"
#if defined(CM_CONTRIBUTED_instCR250) || defined(CM_CONTRIBUTED_instCR100) || defined(CM_CONTRIBUTED_instCR300)
#include "cr250.h"
#include "cr250_control.h"

#define CM_SPECTRUM_CHUNK 100   /* spectrum values per native_spectrum event */
#define CM_RAW_CHUNK 1024

static void boolean(const char *key, int value) {
    fprintf(wire, ",\"%s\":%s", key, value ? "true" : "false");
}

static void text(const char *key, const char *value) {
    fprintf(wire, ",\"%s\":", key);
    cm_json_string(value);
}

/* A positive finite number, or null: the driver's 0 means "not read". */
static void positive(const char *key, double value) {
    fprintf(wire, ",\"%s\":", key);
    if (value > 0 && isfinite(value)) fprintf(wire, "%.17g", value);
    else fputs("null", wire);
}

/* Any finite number, or null. */
static void number(const char *key, double value) {
    fprintf(wire, ",\"%s\":", key);
    if (isfinite(value)) fprintf(wire, "%.17g", value);
    else fputs("null", wire);
}

/* A unit-check value: null unless the driver computed it (cr250.h CR250_UNIT_*),
 * whose 0 otherwise means nothing. */
static void computed(const char *key, int have, double value) {
    if (have) number(key, value);
    else fprintf(wire, ",\"%s\":null", key);
}

/* Empty text is the driver's "not reached" or "not applicable": null. */
static void optional_text(const char *key, const char *value) {
    text(key, value && value[0] ? value : NULL);
}

static void reply_fields(const cr250_reply *r) {
    text("status", r->is_error ? "ER" : "OK");
    fprintf(wire, ",\"code\":%ld", r->code);
    /* For an OK reply the third field is the command; for ER it is the
     * instrument's own description (cr250_imp.h). Kept exactly as sent. */
    text("context", r->context);
    text("value", r->value);
}

static void identity(const cr250_identity *id, int verified) {
    int i;
    fprintf(wire, "{\"verified\":%s", verified ? "true" : "false");
    text("model", verified ? id->model : NULL);
    text("serial", verified ? id->serial : NULL);
    text("firmware", verified ? id->firmware : NULL);
    fputs(",\"firmware_components\":", wire);
    if (verified && id->nfw > 0 && id->nfw <= 3) {
        fputc('[', wire);
        for (i = 0; i < id->nfw; i++) fprintf(wire, "%s%d", i ? "," : "", id->fw[i]);
        fputc(']', wire);
    } else fputs("null", wire);
    fputs(",\"instrument_type\":", wire);
    if (verified && id->instrument_type >= 0) fprintf(wire, "%d", id->instrument_type);
    else fputs("null", wire);
    fputc('}', wire);
}

static void record(const char *key, const cr250_record *r) {
    fputs("{\"key\":", wire); cm_json_string(key);
    fprintf(wire, ",\"id\":%lld", r->id);
    text("name", r->name);
    text("type", r->type[0] ? r->type : NULL);
    fputc('}', wire);
}

static const char *control_names[CM_CR250_FIELDS] = {
    "mode", "exposureMode", "exposureMs", "rangeMode", "range", "exposureMultiplier",
    "syncMode", "syncFrequencyHz", "speed", "accessory", "filter1", "filter2", "filter3",
    "userCalibrationMode", "matrixId", "matchId", "maxAutoExposureMs"
};
static void control_values(const cm_cr250_control_request *v) {
    fprintf(wire, "{\"present\":%u", v->present);
#define FIELD(name, field) do { \
    if (v->present & CM_CR250_BIT(field)) number(#name, v->name); \
    else fprintf(wire, ",\"%s\":null", #name); \
} while (0)
    FIELD(mode, CM_CR250_MODE); FIELD(exposureMode, CM_CR250_EXPOSURE_MODE);
    FIELD(exposureMs, CM_CR250_EXPOSURE); FIELD(rangeMode, CM_CR250_RANGE_MODE);
    FIELD(range, CM_CR250_RANGE); FIELD(exposureMultiplier, CM_CR250_MULTIPLIER);
    FIELD(syncMode, CM_CR250_SYNC_MODE); FIELD(syncFrequencyHz, CM_CR250_FREQUENCY);
    FIELD(speed, CM_CR250_SPEED); FIELD(accessory, CM_CR250_ACCESSORY);
    FIELD(filter1, CM_CR250_FILTER1); FIELD(filter2, CM_CR250_FILTER2); FIELD(filter3, CM_CR250_FILTER3);
    FIELD(userCalibrationMode, CM_CR250_CORRECTION_MODE); FIELD(matrixId, CM_CR250_MATRIX);
    FIELD(matchId, CM_CR250_MATCH); FIELD(maxAutoExposureMs, CM_CR250_MAX_AUTO_EXPOSURE);
#undef FIELD
    fputc('}', wire);
}

static void raw_facts(const cm_cr250_exchange *exchanges, int count) {
    int i, offset;
    for (i = 0; i < count; i++) {
        const cm_cr250_exchange *e = &exchanges[i];
        for (offset = 0; offset < e->raw.length || offset == 0; offset += CM_RAW_CHUNK) {
            int k, n = e->raw.length - offset;
            if (n > CM_RAW_CHUNK) n = CM_RAW_CHUNK;
            cm_event_start("native_raw"); text("driver", "cr250"); text("command", e->command);
            fprintf(wire, ",\"exchange_index\":%d,\"exchange_count\":%d,\"complete\":%s,\"error\":%d,"
                "\"byte_offset\":%d,\"byte_total\":%d,\"raw_hex\":\"", i, count,
                e->complete ? "true" : "false", e->error, offset, e->raw.length);
            for (k = 0; k < n; k++) fprintf(wire, "%02x", (unsigned char)e->raw.bytes[offset + k]);
            fputs("\"}\n", wire);
            if (!e->raw.length) break;
        }
    }
}

static void native_record(int index, int count, const char *role, int slot,
                          const char *key, const cr250_record *r) {
    cm_event_start("native_record"); text("driver", "cr250");
    fprintf(wire, ",\"record_index\":%d,\"record_count\":%d,\"slot\":%d", index, count, slot);
    text("role", role); fputs(",\"record\":", wire); record(key, r); fputs("}\n", wire);
}

/* One bounded event per row, choice and status. Native inventories remain
 * distinguishable from semantic values; NA never becomes a fabricated ID. */
void cm_cr250_control_report_event(const cm_cr250_control_report *r) {
    int field, i;
    cm_event_start("native_control"); text("driver", "cr250");
    fprintf(wire, ",\"status_code\":%d,\"changed\":%u,\"attempted\":%u,\"acknowledged\":%u,"
                  "\"confirmed\":%u,\"blocked\":%u,\"control_count\":%d,\"reply_count\":%d",
            r->status_code, r->changed, r->attempted, r->acknowledged, r->confirmed,
            r->blocked, CM_CR250_FIELDS, r->reply_count);
    boolean("failed", r->failed); optional_text("aperture", r->aperture); optional_text("problem", r->problem);
    fprintf(wire, ",\"raw_exchange_count\":%d", r->raw_exchange_count);
    boolean("trace_overflow", r->trace_overflow);
    fputs(",\"before\":", wire); control_values(&r->before);
    fputs(",\"values\":", wire); control_values(&r->values);
    fputs(",\"requested\":", wire); control_values(&r->requested); fputs("}\n", wire);
    for (field = 0; field < CM_CR250_FIELDS; field++) {
        const cm_cr250_control_row *row = &r->controls[field];
        cm_event_start("native_control_row"); text("driver", "cr250");
        fprintf(wire, ",\"control_index\":%d,\"control_count\":%d,\"kind\":%d,\"choice_count\":%d",
                field, CM_CR250_FIELDS, row->kind, row->choice_count);
        text("id", control_names[field]); boolean("available", row->available); boolean("read_only", row->read_only);
        boolean("limits_known", row->limits_known);
        computed("minimum", row->limits_known, row->minimum); computed("maximum", row->limits_known, row->maximum);
        optional_text("current_native", row->current_native); optional_text("problem", row->problem); fputs("}\n", wire);
        for (i = 0; i < row->choice_count; i++) {
            cm_event_start("native_control_choice"); text("driver", "cr250");
            fprintf(wire, ",\"control_index\":%d,\"choice_index\":%d,\"choice_count\":%d,\"choice\":",
                    field, i, row->choice_count);
            record(control_names[field], &row->choices[i]); fputs("}\n", wire);
        }
    }
    for (i = 0; i < r->reply_count; i++) {
        const cr250_reply *reply = &r->replies[i].reply;
        cm_event_start("native_control_reply"); text("driver", "cr250");
        fprintf(wire, ",\"reply_index\":%d,\"reply_count\":%d,\"phase\":%d,\"error\":%d",
                i, r->reply_count, r->replies[i].phase, r->replies[i].error);
        text("command", r->replies[i].command); boolean("complete", reply->complete);
        if (reply->complete) reply_fields(reply);
        fputs("}\n", wire);
    }
    raw_facts(r->raw_exchanges, r->raw_exchange_count);
}

/* The host's remaining allowance for the next read_sample, counted by cr250.c from
 * that call's start, as cr250_host.py composes it onto the driver. Before setting
 * writes it reserves each planned SM/RS transaction and capture.required_s, the
 * budget plus the worst case of the capture reads after M. Just before M it
 * checks the actual remaining time again, keeping both allowance facts instead
 * of being cut off mid-exposure or before the capture's metadata is read. */
void cm_cr250_read_allowance(inst *meter, double seconds) {
    ((cr250 *)meter)->host_allowance_s = seconds;
}

void cm_cr250_native_facts(inst *meter) {
    const cr250 *p = (const cr250 *)meter;
    const cr250_identity *id = &p->engine.id;
    const cr250_capture *c = &p->capture;
    const int verified = p->engine.identified;
    int i, count = c->count, nrecords = 0, nsettings = 0;
    /* Native values survive a refused Argyll mapping. This parsed observation
     * may exceed xspect's publication capacity; each event stays bounded. */
    const int nvalues = c->spectrum.count > 0
                      ? (c->spectrum.count > CR250_MAX_SPECTRUM_LINES ? CR250_MAX_SPECTRUM_LINES
                                                              : c->spectrum.count) : 0;
    const int nchunks = (nvalues + CM_SPECTRUM_CHUNK - 1) / CM_SPECTRUM_CHUNK;
    const int nwrites = c->nwrites < 0 ? 0
                      : c->nwrites > CR250_MAX_WRITES ? CR250_MAX_WRITES : c->nwrites;

    if (count < 0) count = 0;
    if (count > CR250_FACTS_MAX) count = CR250_FACTS_MAX;
    if (p->model && p->model->setup)
        while (nsettings < CR250_MAX_SETUP && p->model->setup[nsettings].key) nsettings++;
    for (i = 0; i < nsettings; i++)
        if (p->model->setup[i].inventory && c->matched[i].name[0]) nrecords++;
    if (c->range.name[0]) nrecords++;
    cm_event_start("native_facts");
    text("driver", "cr250");
    text("model_policy", p->model ? p->model->model : NULL);
    fputs(",\"identity\":", wire); identity(id, verified);

    fputs(",\"capture\":{", wire);
    fprintf(wire, "\"valid\":%s,\"reply_count\":%d", c->valid ? "true" : "false", count);
    fprintf(wire, ",\"raw_exchange_count\":%d,\"record_count\":%d", p->raw_count, nrecords);
    fputs(",\"identity\":", wire); identity(&c->identity, c->identity_verified);
    /* The native RM XYZ whenever the read parsed it (cr250_host.py's
     * capture.parsed), so a later refusal keeps it; valid says whether it was
     * published. */
    fputs(",\"xyz\":", wire);
    if ((c->parsed & CR250_PARSED_XYZ)
        && isfinite(c->xyz[0]) && isfinite(c->xyz[1]) && isfinite(c->xyz[2]))
        fprintf(wire, "[%.17g,%.17g,%.17g]", c->xyz[0], c->xyz[1], c->xyz[2]);
    else fputs("null", wire);
    fputs(",\"measurement_budget_s\":", wire);
    if (c->budget_s > 0 && isfinite(c->budget_s)) fprintf(wire, "%.17g", c->budget_s);
    else fputs("null", wire);
    /* What the read needed from M to its last capture read, and what the host
     * allowed for the whole read -- the two numbers a refusal compares. */
    /* What the read needed from M to its last capture read, what the host allowed
     * for the whole read, and what remained at the latest check before writes or
     * M. Pre-write refusals also reserve the planned setting transactions.
     * remaining_s is null when no allowance was set or no comparison was reached. */
    positive("required_s", c->required_s);
    positive("host_allowance_s", p->host_allowance_s);
    fputs(",\"remaining_s\":", wire);
    if (p->host_allowance_s > 0 && c->required_s > 0 && isfinite(c->remaining_s))
        fprintf(wire, "%.17g", c->remaining_s);
    else fputs("null", wire);
    /* cr250_measure_budget's constants: host policy, not an instrument property. */
    fprintf(wire, ",\"budget_policy\":{\"source\":\"host\",\"floor_s\":%.17g,"
                  "\"max_exposure_factor\":%.17g,\"allowance_s\":%.17g}",
            CR250_MEAS_FLOOR_S, CR250_MEAS_FACTOR, CR250_MEAS_ALLOWANCE_S);
    optional_text("problem", c->problem);
    /* The exposure and sync the capture ran with, as the driver read them. */
    positive("exposure_ms", c->exposure_ms);
    positive("fixed_exposure_ms", c->fixed_exposure_ms);
    positive("max_auto_exposure_ms", c->max_auto_exposure_ms);
    fputs(",\"exposure_x\":", wire);
    if (c->exposure_x > 0) fprintf(wire, "%d", c->exposure_x);
    else fputs("null", wire);
    positive("sync_hz", c->sync_hz);
    fputs(",\"radiometric\":", wire);
    /* Parsed and of the model's type: a zero or signed dark reading is a value,
     * and a half-parsed triplet is not. */
    if ((c->parsed & CR250_PARSED_RADIOMETRIC)
        && isfinite(c->radiometric[1]) && isfinite(c->radiometric[2]))
        fprintf(wire, "{\"type\":%.17g,\"radiance_w_sr_m2\":%.17g,"
                      "\"photon_radiance_sr_m2_s\":%.17g}",
                c->radiometric[0], c->radiometric[1], c->radiometric[2]);
    else fputs("null", wire);
    /* The row's settings and this read's writes go out one per event after the
     * replies, like them: each value may be as long as a reply's. */
    fprintf(wire, ",\"setting_count\":%d,\"write_count\":%d,\"spectrum_chunk_count\":%d",
            nsettings, nwrites, nchunks);
    /* The original density unit is unknown. Same-capture consistency supports
     * only the driver's inferred model. Publication additionally requires the
     * exact nonzero final ipatch spectral count, never valid alone. Original bins
     * follow independently in native_spectrum chunks, including on refusal. */
    fputs(",\"spectrum\":", wire);
    if (c->spectrum.count > 0) {
        const int inferred = c->unit.verdict == CR250_UNIT_CONSISTENT;
        const int published = inferred && c->valid && c->published_spectrum_count > 0
                           && c->published_spectrum_count == c->spectrum.count;
        fprintf(wire, "{\"start_nm\":%.17g,\"end_nm\":%.17g,\"step_nm\":%.17g,"
                      "\"count\":%d,\"unit\":null",
                c->spectrum.start_nm, c->spectrum.end_nm, c->spectrum.step_nm,
                c->spectrum.count);
        text("inferred_unit", inferred ? "W/(sr.m^2.nm)" : NULL);
        text("published_unit", published ? "mW/(m^2.sr.nm)" : NULL);
        fprintf(wire, ",\"published_scale\":%s", published ? "1000" : "null");
        text("unit_basis", inferred ? "same_capture_consistency_under_driver_model" : "unverified");
        fputc('}', wire);
    } else fputs("null", wire);
    /* The spectrum against the instrument's own totals for the same capture. */
    {
        const cr250_unit_check *u = &c->unit;
        static const char *const verdicts[] = {
            "not_checked", "consistent", "inconclusive", "mismatch" };
        const int known = u->verdict >= 0 && u->verdict <= 3;
        fputs(",\"unit_check\":{", wire);
        fputs("\"verdict\":", wire);
        cm_json_string(known ? verdicts[u->verdict] : NULL);
        /* The comparisons made, then each number or null when it was not
         * computed: skipped, not formable, or its spectrum overflowed. */
        fputs(",\"discriminating\":{", wire);
        fprintf(wire, "\"power\":%s", u->discriminating & CR250_UNIT_POWER ? "true" : "false");
        boolean("axis", u->discriminating & CR250_UNIT_AXIS);
        boolean("luminance", u->discriminating & CR250_UNIT_Y);
        boolean("chromaticity", u->discriminating & CR250_UNIT_CHROMA);
        fputc('}', wire);
        if (u->verdict != CR250_UNIT_NOT_CHECKED) {
            computed("spectrum_w", u->computed & CR250_UNIT_SPECTRUM, u->spectrum_w);
            computed("power_ratio", u->computed & CR250_UNIT_POWER, u->power_ratio);
            computed("mean_nm", u->computed & CR250_UNIT_AXIS, u->mean_nm);
            computed("spectrum_mean_nm", u->computed & CR250_UNIT_SPECTRUM_MEAN, u->spectrum_mean_nm);
            fputs(",\"xyz\":", wire);
            if ((u->computed & CR250_UNIT_SPECTRUM)
                && isfinite(u->xyz[0]) && isfinite(u->xyz[1]) && isfinite(u->xyz[2]))
                fprintf(wire, "[%.17g,%.17g,%.17g]", u->xyz[0], u->xyz[1], u->xyz[2]);
            else fputs("null", wire);
            computed("y_ratio", u->computed & CR250_UNIT_Y, u->y_ratio);
            computed("chroma_error", u->computed & CR250_UNIT_CHROMA, u->chroma_error);
        }
        fputc('}', wire);
    }
    fputc('}', wire);

    /* What the host asked of the driver for this read. */
    fprintf(wire, ",\"requested\":{\"mode\":%u,\"averages\":", (unsigned)p->mode);
    if (p->averages > 0) fprintf(wire, "%d", p->averages); else fputs("null", wire);
    positive("refresh_hz", p->refresh_hz);
    fputc('}', wire);
    boolean("link_uncertain", p->engine.uncertain);
    boolean("trace_overflow", p->raw_overflow);
    fputs(",\"last_reply\":", wire);
    if (p->engine.last_reply.complete) {
        fputc('{', wire);
        fputs("\"complete\":true", wire);
        reply_fields(&p->engine.last_reply);
        fputc('}', wire);
    } else fputs("null", wire);
    text("factory_calibration", "unknown");
    text("xyz_origin", "native RM XYZ, requested unclamped");
    fputs("}\n", wire);

    raw_facts(p->raw_exchanges, p->raw_count);
    {
        int index = 0;
        for (i = 0; i < nsettings; i++)
            if (p->model->setup[i].inventory && c->matched[i].name[0])
                native_record(index++, nrecords, "matched", i, p->model->setup[i].key, &c->matched[i]);
        if (c->range.name[0]) native_record(index, nrecords, "range", 0, "Range", &c->range);
    }

    for (i = 0; i < count; i++) {
        cm_event_start("native_reply");
        text("driver", "cr250");
        fprintf(wire, ",\"reply_index\":%d,\"reply_count\":%d", i, count);
        reply_fields(&c->reply[i]);
        fputs("}\n", wire);
    }
    /* Every setting the row checks: the value accepted from RS, or null when the
     * model lacks it on this connection, and whether the driver sets it. */
    for (i = 0; i < nsettings; i++) {
        const cr250_setting *setting = &p->model->setup[i];
        cm_event_start("native_setting");
        text("driver", "cr250");
        fprintf(wire, ",\"setting_index\":%d,\"setting_count\":%d", i, nsettings);
        text("key", setting->key);
        optional_text("value", c->value[i]);
        text("presence", p->presence[i] > 0 ? "present"
                       : p->presence[i] < 0 ? "not_applicable" : "unknown");
        boolean("driver_set", setting->driver_set);
        fputs("}\n", wire);
    }
    /* What the driver wrote this read. The instrument keeps a written value until
     * powered down; nothing is restored. An empty after with confirmed false is a
     * write sent whose outcome is unknown. */
    for (i = 0; i < nwrites; i++) {
        const cr250_write *w = &c->write[i];
        cm_event_start("native_write");
        text("driver", "cr250");
        fprintf(wire, ",\"write_index\":%d,\"write_count\":%d", i, nwrites);
        text("key", w->key);
        text("before", w->before);
        text("target", w->target);
        optional_text("after", w->after);
        boolean("confirmed", w->confirmed);
        fputs("}\n", wire);
    }
    for (i = 0; i < nchunks; i++) {
        const int offset = i * CM_SPECTRUM_CHUNK;
        const int n = nvalues - offset < CM_SPECTRUM_CHUNK ? nvalues - offset
                                                           : CM_SPECTRUM_CHUNK;
        int k;
        cm_event_start("native_spectrum");
        text("driver", "cr250");
        fprintf(wire, ",\"spectrum_chunk_index\":%d,\"spectrum_chunk_count\":%d,"
                      "\"offset\":%d,\"values\":[", i, nchunks, offset);
        for (k = 0; k < n; k++) {
            const double v = c->spectrum.value[offset + k];
            if (k) fputc(',', wire);
            if (isfinite(v)) fprintf(wire, "%.17g", v); else fputs("null", wire);
        }
        fputs("]}\n", wire);
    }
}
#endif
