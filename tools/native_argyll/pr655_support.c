/* PR retained facts from the live owned driver, without parsing or more I/O.
 * Copyright 2026 Fernando Voltolini de Azambuja. GPL-2.0-or-later.
 * Raw bytes use bounded hex chunks, including malformed/non-ASCII/NUL input.
 */
#include "session.h"
#if defined(CM_CONTRIBUTED_instPR655) || defined(CM_CONTRIBUTED_instPR670)
#include "pr655.h"
#define PR_RAW_CHUNK 1024
#define PR_SPECTRUM_CHUNK 100

static void text(const char *key, const char *s) {
    fprintf(wire, ",\"%s\":", key); cm_json_string(s);
}
static void boolean(const char *key, int yes) {
    fprintf(wire, ",\"%s\":%s", key, yes ? "true" : "false");
}
static void number(const char *key, int have, double n) {
    fprintf(wire, ",\"%s\":", key);
    if (have && isfinite(n)) fprintf(wire, "%.17g", n); else fputs("null", wire);
}
static void setup(const pr655_setup *s, int valid) {
    int i;
    if (!valid) { fputs("null", wire); return; }
    fprintf(wire, "{\"primary\":%lld,\"addons\":[%lld,%lld,%lld],\"aperture\":%lld,"
        "\"units\":%d,\"exposure_mode\":%d,\"exposure_ms\":%d,\"gain\":%d,\"cycles\":%d,"
        "\"observer\":%d,\"dark_mode\":%d,\"sync_mode\":%d,\"capture_mode\":%d",
        s->primary, s->addon[0], s->addon[1], s->addon[2], s->aperture, s->units,
        s->exposure_mode, s->exposure_ms, s->gain, s->cycles, s->observer,
        s->dark_mode, s->sync_mode, s->capture_mode);
    number("sync_value", 1, s->sync_value); number("frequency_hz", 1, s->frequency_hz);
    boolean("adaptive_known", s->adaptive_known); boolean("adaptive", s->adaptive);
    boolean("sensitivity_known", s->sensitivity_known); boolean("extended", s->extended);
    fputs(",\"descriptions\":[", wire);
    for (i = 0; i < 15; i++) { if (i) fputc(',', wire); cm_json_string(s->text[i]); }
    fputs("]}", wire);
}
static void metadata(const cm_pr655_metadata *m) {
    fprintf(wire, "{\"queried\":%s,\"complete\":%s,\"parsed\":%s,\"error\":%d",
        m->queried ? "true" : "false", m->complete ? "true" : "false", m->parsed ? "true" : "false", m->error);
    text("description", m->parsed ? m->description : NULL);
    number("value", m->parsed, m->value); fputc('}', wire);
}
static void values(const cm_pr655_control_request *v) {
    fprintf(wire, "{\"present\":%u", v->present);
#define FIELD(name, field) number(#name, v->present & CM_PR655_BIT(field), v->name)
    FIELD(exposureMilliseconds, CM_PR655_EXPOSURE); FIELD(cycles, CM_PR655_CYCLES);
    FIELD(syncMode, CM_PR655_SYNC); FIELD(syncFrequencyHz, CM_PR655_FREQUENCY);
    FIELD(primaryAccessoryId, CM_PR655_PRIMARY); FIELD(addon1Id, CM_PR655_ADDON1);
    FIELD(addon2Id, CM_PR655_ADDON2); FIELD(addon3Id, CM_PR655_ADDON3);
    FIELD(apertureId, CM_PR655_APERTURE); FIELD(sensitivityMode, CM_PR655_SENSITIVITY);
    FIELD(smartDark, CM_PR655_DARK); FIELD(photometricUnits, CM_PR655_UNITS);
    FIELD(instrumentObserver, CM_PR655_OBSERVER); FIELD(shutterMode, CM_PR655_SHUTTER);
    FIELD(speedModeCode, CM_PR655_SPEED);
#undef FIELD
    fputc('}', wire);
}
static void raw_facts(const pr655 *p) {
    int i, offset;
    for (i = 0; i < p->host.exchange_count; i++) {
        const cm_pr655_exchange *e = &p->host.exchanges[i];
        for (offset = 0; offset < e->raw.length || offset == 0; offset += PR_RAW_CHUNK) {
            int k, n = e->raw.length - offset;
            if (n > PR_RAW_CHUNK) n = PR_RAW_CHUNK;
            cm_event_start("native_raw"); text("driver", "pr655"); text("command", e->command);
            fprintf(wire, ",\"exchange_index\":%d,\"exchange_count\":%d,\"complete\":%s,\"error\":%d,"
                "\"byte_offset\":%d,\"byte_total\":%d,\"raw_hex\":\"", i, p->host.exchange_count,
                e->complete ? "true" : "false", e->error, offset, e->raw.length);
            for (k = 0; k < n; k++) fprintf(wire, "%02x", (unsigned char)e->raw.bytes[offset + k]);
            fputs("\"}\n", wire);
            if (!e->raw.length) break;
        }
    }
}
void cm_pr655_control_report_event(const cm_pr655_control_report *r) {
    cm_event_start("native_control"); text("driver", "pr655");
    fprintf(wire, ",\"status_code\":%d,\"changed\":%u,\"attempted\":%u,\"acknowledged\":%u,\"confirmed\":%u,\"blocked\":%u",
        r->status_code, r->changed, r->attempted, r->acknowledged, r->confirmed, r->blocked);
    fprintf(wire, ",\"accessory_count\":%d,\"aperture_count\":%d,\"raw_exchange_count\":%d",
        r->accessory_count, r->aperture_list_valid ? r->aperture_count : 0, r->raw_exchange_count);
    boolean("failed", r->failed); boolean("setup_valid", r->setup_valid);
    boolean("inventory_counts_valid", r->inventory_counts_valid);
    boolean("accessory_list_valid", r->accessory_list_valid);
    fprintf(wire, ",\"reported_accessory_count\":%d,\"reported_aperture_count\":%d,\"inventory_counts_error\":%d,\"accessory_list_error\":%d,\"aperture_list_error\":%d",
        r->reported_accessory_count, r->aperture_count, r->inventory_counts_error, r->accessory_list_error, r->aperture_list_error);
    boolean("aperture_list_valid", r->aperture_list_valid); text("problem", r->problem[0] ? r->problem : NULL);
    boolean("setup_before_valid", r->setup_before_valid);
    fputs(",\"setup_before\":", wire); setup(&r->setup_before, r->setup_before_valid);
    fputs(",\"setup\":", wire); setup(&r->setup, r->setup_valid);
    fputs(",\"before\":", wire); values(&r->before);
    fputs(",\"values\":", wire); values(&r->values); fputs(",\"requested\":", wire); values(&r->requested);
    fputs("}\n", wire);
}
void cm_pr655_control_facts(inst *meter) {
    const pr655 *p = (const pr655 *)meter;
    const cm_pr655_control_report *r = &p->host.control;
    int i;
    cm_pr655_control_report_event(r);
    for (i = 0; i < r->accessory_count; i++) {
        const pr655_accessory *a = &r->accessories[i];
        cm_event_start("native_accessory"); text("driver", "pr655");
        fprintf(wire, ",\"accessory_index\":%d,\"accessory_count\":%d,\"id\":%lld", i, r->accessory_count, a->id);
        text("name", a->name); text("type", a->type); text("photometry", a->photometry);
        text("radiometry", a->radiometry); fputs("}\n", wire);
    }
    if (r->aperture_list_valid) for (i = 0; i < r->aperture_count; i++) {
        const cm_pr655_aperture *a = &r->apertures[i];
        cm_event_start("native_aperture"); text("driver", "pr655");
        fprintf(wire, ",\"aperture_index\":%d,\"aperture_count\":%d,\"id\":%lld", i, r->aperture_count, a->id);
        text("name", a->name); number("bandwidth", 1, a->bandwidth_nm); fputs("}\n", wire);
    }
    raw_facts(p);
}

void cm_pr655_native_facts(inst *meter) {
    const pr655 *p = (const pr655 *)meter;
    const pr655_capture_result *c = &p->capture;
    const pr655_capture *n = &c->native;
    const int ambient = IMODETST(p->mode, inst_mode_emis_ambient);
    const int complete = n->xyz_parsed && (!(p->mode & inst_mode_spectral) || n->spectrum_parsed);
    int i, chunk, chunks = (n->count + PR_SPECTRUM_CHUNK - 1) / PR_SPECTRUM_CHUNK;
    cm_event_start("native_facts"); text("driver", "pr655");
    fputs(",\"identity\":{\"verified\":", wire); fputs(p->engine.identity_valid ? "true" : "false", wire);
    text("model", p->engine.identity_valid ? p->engine.model == 655 ? "PR-655" : "PR-670" : NULL);
    text("serial", p->engine.identity_valid ? p->engine.serial : NULL);
    text("firmware", p->engine.identity_valid ? p->engine.version : NULL); fputc('}', wire);
    fputs(",\"capture\":{\"valid\":", wire); fputs(c->valid ? "true" : "false", wire);
    boolean("native_complete", complete); boolean("exposure_started", n->exposure_started);
    boolean("geometry_valid", c->geometry_valid);
    boolean("inventory_counts_valid", p->engine.inventory_counts_valid);
    boolean("accessory_list_valid", p->engine.accessory_list_valid);
    fprintf(wire, ",\"reported_accessory_count\":%d,\"reported_aperture_count\":%d,\"inventory_counts_error\":%d,\"accessory_list_error\":%d",
        p->engine.reported_accessory_count, p->engine.aperture_count, p->engine.inventory_counts_error, p->engine.accessory_list_error);
    boolean("spectrum_complete", n->spectrum_complete); boolean("spectrum_parsed", n->spectrum_parsed);
    boolean("xyz_complete", n->xyz_complete); boolean("xyz_parsed", n->xyz_parsed);
    fputs(",\"xyz\":", wire);
    if (n->xyz_parsed) fprintf(wire, "[%.17g,%.17g,%.17g]", n->xyz[0], n->xyz[1], n->xyz[2]); else fputs("null", wire);
    fputs(",\"xyz_publication\":", wire);
    if (c->valid && c->geometry_valid && c->xyz_publication_valid) {
        fprintf(wire, "{\"xyz\":[%.17g,%.17g,%.17g],\"unit\":\"%s\",\"observer\":2,"
                      "\"native_to_published_scale\":%.17g}", c->published_xyz[0], c->published_xyz[1],
                c->published_xyz[2], ambient ? "lux" : "cd/m2", c->native_to_published_xyz_scale);
    } else fputs("null", wire);
    fprintf(wire, ",\"xyz_units\":%d,\"spectrum_units\":%d,\"spectrum_count\":%d,\"reply_count\":%d",
        n->xyz_units, n->spectrum_units, n->spectrum_parsed ? n->count : 0, p->host.exchange_count);
    fprintf(wire, ",\"spectrum_chunk_count\":%d,\"spectrum\":", n->spectrum_parsed ? chunks : 0);
    if (n->spectrum_parsed) {
        fprintf(wire, "{\"start_nm\":%d,\"end_nm\":%d,\"step_nm\":%d,\"count\":%d,\"unit\":null",
            p->engine.grid.start_nm, p->engine.grid.end_nm, p->engine.grid.step_nm, n->count);
        text("inferred_unit", c->unit.verdict == PR655_UNIT_CONSISTENT
            ? ambient ? "W/(m^2.nm)" : "W/(sr.m^2.nm)" : NULL);
        text("published_unit", c->valid ? ambient ? "mW/(m^2.nm)" : "mW/(m^2.sr.nm)" : NULL);
        number("published_scale", c->valid, 1000.0);
        /* The basis names the definition of the retained M5 body. A units code
         * that contradicts the geometry, which pr655.c unit_check refuses, has
         * none: an irradiance body is never labelled radiance or the reverse. */
        {
            const int agrees = ambient ? n->spectrum_units == 12
                : n->spectrum_units == 0 || n->spectrum_units == 11 || n->spectrum_units == 111;
            text("unit_basis", !agrees ? NULL : ambient
                ? "manual M5 integral and irradiance definition; per-capture consistency verdict retained separately"
                : "manual M5 integral and radiance definition; per-capture consistency verdict retained separately");
        }
        fputc('}', wire);
    } else fputs("null", wire);
    text("xyz_scale", n->xyz_parsed && c->geometry_valid
        && (n->xyz_units == 0 || n->xyz_units == (ambient ? 112 : 111)) && c->setup_before_valid
        ? c->before.units == 0 ? ambient ? "foot-candle" : "foot-lambert" : ambient ? "lux" : "cd/m2" : NULL);
    number("measurement_budget_s", c->budget_s > 0, c->budget_s);
    number("required_s", p->host.required_s > 0, p->host.required_s);
    number("host_allowance_s", p->host.allowance_s > 0, p->host.allowance_s);
    number("remaining_s", p->host.allowance_s > 0 && p->host.required_s > 0, p->host.remaining_s);
    number("native_integrated", n->spectrum_parsed, n->integrated);
    number("native_photon_integrated", n->spectrum_parsed, n->photon_integrated);
    number("peak_nm", n->spectrum_parsed, n->peak_nm); text("problem", c->problem[0] ? c->problem : NULL);
    fputs(",\"setup_before\":", wire); setup(&c->before, c->setup_before_valid);
    fputs(",\"setup_after\":", wire); setup(&c->after, c->setup_after_valid);
    fputs(",\"budget_policy\":{\"source\":\"host\",\"floor_s\":300,\"dark_per_cycle\":true}}", wire);
    fprintf(wire, ",\"unit_check\":{\"verdict\":%d,\"discriminating\":%d,\"computed\":%d",
        c->unit.verdict, c->unit.discriminating, c->unit.computed);
    number("power_ratio", c->unit.computed & PR655_CHECK_POWER, c->unit.power_ratio);
    number("y_ratio", c->unit.computed & PR655_CHECK_Y, c->unit.y_ratio); fputc('}', wire);
    fputs(",\"metadata\":{\"exposure\":", wire); metadata(&p->host.exposure);
    fputs(",\"stored_synchronization\":", wire); metadata(&p->host.synchronization); fputc('}', wire);
    boolean("link_uncertain", p->engine.state == pr655_quarantined); boolean("trace_overflow", p->host.overflow);
    text("factory_calibration", "unknown"); text("xyz_origin", "native M2/D2, requested unclamped"); fputs("}\n", wire);
    raw_facts(p);
    if (n->spectrum_parsed) for (chunk = 0; chunk < chunks; chunk++) {
        int offset = chunk * PR_SPECTRUM_CHUNK, count = n->count - offset;
        if (count > PR_SPECTRUM_CHUNK) count = PR_SPECTRUM_CHUNK;
        cm_event_start("native_spectrum"); text("driver", "pr655");
        fprintf(wire, ",\"spectrum_chunk_index\":%d,\"spectrum_chunk_count\":%d,\"offset\":%d,\"values\":[", chunk, chunks, offset);
        for (i = 0; i < count; i++) fprintf(wire, "%s%.17g", i ? "," : "", n->spectrum[offset + i]);
        fputs("]}\n", wire);
    }
}
#endif
