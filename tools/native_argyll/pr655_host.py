"""Strict application-only PR controls, raw retention and enclosing allowance.

The reviewed contribution remains an Argyll inst/icoms driver. This overlay
adds only the application's typed controls and retained-fact seam. Every source
anchor must occur once; already overlaid or changed source is refused.
Copyright 2026 Fernando Voltolini de Azambuja. GPL-2.0-or-later.
"""
from __future__ import annotations

REPAIR_ID = 'app-policy:pr655-host'

_ENGINE_HELPERS = r'''
/* Application-only typed controls, composed after the contribution import. */
int cm_pr655_engine_finish(pr655imp *p, int code) {
    int tail = PR655_OK;
    if (p->host_pending && p->state != pr655_quarantined) tail = clean_input(p);
    if (p->host_pending) {
        int observed = p->device_error ? p->device_error : code;
        p->host_pending = 0;
        if (p->host_observe && p->host_observe(p->host_context, p->host_command,
                &p->last_raw, observed, 0) != PR655_OK) tail = CM_PR655_TRACE_LIMIT;
    }
    /* A host retention failure is secondary to an existing operation error.
     * Framing/transport failures still keep their own precedence. */
    return tail == PR655_OK || (tail == CM_PR655_TRACE_LIMIT && code != PR655_OK) ? code : tail;
}

int cm_pr655_engine_set(pr655imp *p, int field, long long value, int *acknowledged,
                        double timeout_s) {
    static const char *prefix[CM_PR655_FIELDS] = {
        "SE", "SN", "SS", "SK", "SP", "SA", "SB", "SC", "SF", "SH", "SD", "SU", "SO", "SZ", "SG"
    };
    pr655_deadline d;
    int code;
    *acknowledged = 0;
    if (field < 0 || field >= CM_PR655_FIELDS) return PR655_REFUSED_COMMAND;
    if (field == CM_PR655_SPEED && (p->model != 670 || value < 0 || value > 3))
        return PR655_REFUSED_COMMAND;
    if ((code = ready(p)) != PR655_OK) return code;
    if ((code = deadline(p, &d, timeout_s)) != PR655_OK) return code;
    if (field != CM_PR655_SHUTTER) p->setup_valid = 0;
    if ((code = set_value(p, prefix[field], value, &d)) != PR655_OK) return code;
    *acknowledged = 1;
    if (field == CM_PR655_SHUTTER) return PR655_OK; /* SZ has no report. */
    if (!remaining(p, &d)) return quarantine(p, PR655_TIMEOUT);
    return pr655_imp_read_setup(p, remaining(p, &d) / 1000.0);
}

int cm_pr655_engine_apertures(pr655imp *p, cm_pr655_aperture *rows, double timeout_s) {
    pr655_deadline d;
    char out[PR655_MAX_LINE + 1], *f[5];
    int code, n, i, j;
    if ((code = ready(p)) != PR655_OK) return code;
    if (!p->setup_valid || p->aperture_count < 0 || p->aperture_count > CM_PR655_MAX_APERTURES)
        return PR655_BAD_SETUP;
    if (!p->aperture_count) return PR655_OK;
    if ((code = deadline(p, &d, timeout_s)) != PR655_OK) return code;
    if ((code = begin(p, "D117", &d)) != PR655_OK) return code;
    for (i = 0; i < p->aperture_count; i++) {
        code = i ? line(p, out, &d) : first_line(p, "D117", out, &d);
        if (code != PR655_OK) return code;
        memset(f, 0, sizeof(f)); n = fields(out, f, 5);
        if ((code = native_status(p, f, n)) != PR655_OK) {
            if (i) return quarantine(p, code);
            if (p->state == pr655_ready) {
                int tail = clean_input(p);
                if (tail != PR655_OK) return tail;
            }
            return code;
        }
        if (n != 4 || !optical_integer(f[1], &rows[i].id) || rows[i].id < 0
         || !valid_text(f[2]) || !decimal(f[3], &rows[i].bandwidth_nm)
         || rows[i].bandwidth_nm < 0) return quarantine(p, PR655_BAD_SETUP);
        rows[i].name = malloc(strlen(f[2]) + 1);
        if (!rows[i].name) return quarantine(p, PR655_INTERNAL_ERROR);
        strcpy(rows[i].name, f[2]);
        for (j = 0; j < i; j++) if (rows[j].id == rows[i].id)
            return quarantine(p, PR655_BAD_SETUP);
    }
    return clean_input(p);
}

int cm_pr655_engine_metadata(pr655imp *p, int code, cm_pr655_metadata *m, double timeout_s) {
    pr655_deadline d;
    char out[PR655_MAX_LINE + 1], command[8], *f[4];
    int status, n;
    memset(m, 0, sizeof(*m)); m->queried = 1;
    if (code != 13 && code != 14) return m->error = PR655_REFUSED_COMMAND;
    if ((status = ready(p)) != PR655_OK) return m->error = status;
    if ((status = deadline(p, &d, timeout_s)) != PR655_OK) return m->error = status;
    snprintf(command, sizeof(command), "D%d", code);
    if ((status = response(p, command, out, f, 4, &n, &d)) != PR655_OK) {
        m->complete = p->state == pr655_ready;
        return m->error = status;
    }
    if (n != 3 || !copy_text(m->description, f[1])
     || !labelled_decimal(f[2], code == 13 ? "msec" : "Hertz", &m->value)
     || m->value < 0) return m->error = quarantine(p, PR655_DATA_PARSE_ERROR);
    if ((status = clean_input(p)) != PR655_OK) return m->error = status;
    m->complete = m->parsed = 1;
    return PR655_OK;
}
'''

_IMP_HEADER = (
    ('\tpr655_raw last_raw;\n', '\tpr655_raw last_raw;\n'
     '\t/* Application-only observer and one enclosing operation deadline. */\n'
     '\tint (*host_observe)(void *, const char *, const pr655_raw *, int, int);\n'
     '\tvoid *host_context;\n\tchar host_command[32];\n'
     '\tint host_pending, host_requested, host_error, host_active;\n'
     '\tunsigned int host_started_ms, host_milliseconds;\n'),
)
_IMP_SOURCE = (
    ('#include "pr655_imp.h"\n', '#include "pr655_imp.h"\n#include "pr655_control.h"\n'),
    ('static int quarantine(pr655imp *p, int code) {\n\tp->state = pr655_quarantined;',
     'static int quarantine(pr655imp *p, int code) {\n'
     '\tif (p->host_pending) {\n\t\tp->host_pending = 0;\n'
     '\t\tif (p->host_observe) p->host_observe(p->host_context, p->host_command,\n'
     '\t\t    &p->last_raw, p->device_error ? p->device_error : code, 0);\n\t}\n'
     '\tp->state = pr655_quarantined;'),
    ('\treturn elapsed >= d->milliseconds ? 0 : d->milliseconds - elapsed;\n',
     '\tunsigned int local = elapsed >= d->milliseconds ? 0 : d->milliseconds - elapsed;\n'
     '\tif (p->host_active) {\n\t\tunsigned int spent = p->now() - p->host_started_ms;\n'
     '\t\tunsigned int host = spent >= p->host_milliseconds ? 0 : p->host_milliseconds - spent;\n'
     '\t\tif (host < local) local = host;\n\t}\n\treturn local;\n'),
    ('\tp->device_error = status;\n', '\tp->device_error = status;\n\tp->host_error = status;\n'),
    # The exchange is complete and its input clean: a full host trace refuses
    # the operation it cannot retain, but the link stays synchronized, so
    # cleanup (B100) and the closing Q still follow.
    ('/* Inspect already queued data',
     '/* CM: complete the open exchange with the bytes it retained. */\n'
     'static int cm_pr655_exchange_complete(pr655imp *p) {\n'
     '\tp->host_pending = 0;\n'
     '\tif (p->host_observe && p->host_observe(p->host_context, p->host_command,\n'
     '\t    &p->last_raw, p->host_error, 1) != PR655_OK)\n'
     '\t\treturn p->host_error ? PR655_NATIVE_ERROR_BASE - p->host_error : CM_PR655_TRACE_LIMIT;\n'
     '\treturn PR655_OK;\n}\n\n'
     '/* Inspect already queued data'),
    ('\t\tif (n == 0) return PR655_OK;\n',
     '\t\tif (n == 0)\n\t\t\treturn p->host_pending && p->host_requested\n'
     '\t\t\t       ? cm_pr655_exchange_complete(p) : PR655_OK;\n'),
    # A back-to-back request (D601 then D602) completes the previous exchange.
    # The new one is armed before its pre-request input check, so a stale-input
    # refusal records this command with the bytes that caused it; only the
    # surplus check after the sent request completes it.
    ('\traw_reset(&p->last_raw);\n\tp->device_error = p->transport_error = 0;\n',
     '\tif (p->host_pending && (ec = cm_pr655_exchange_complete(p)) != PR655_OK) return ec;\n'
     '\traw_reset(&p->last_raw);\n\tp->device_error = p->transport_error = 0;\n'
     '\tp->host_error = 0;\n\tp->host_pending = 1;\n\tp->host_requested = 0;\n'
     '\tsnprintf(p->host_command, sizeof(p->host_command), "%s", command);\n'),
    ('\tif (attempt != NULL) *attempt = 1;\n',
     '\tp->host_requested = 1;\n\tif (attempt != NULL) *attempt = 1;\n'),
    # Numeric originals are already validated when this clean-input check runs.
    # Host retention alone must not erase them; the operation still fails.
    ('\tc->spectrum_complete = 1;\n'
     '\tif ((ec = clean_input(p)) != PR655_OK) return ec;\n'
     '\tc->spectrum_parsed = 1;\n\treturn PR655_OK;\n',
     '\tc->spectrum_complete = 1;\n\tec = clean_input(p);\n'
     '\tif (ec == PR655_OK || (ec == CM_PR655_TRACE_LIMIT && p->state == pr655_ready))\n'
     '\t\tc->spectrum_parsed = 1;\n\treturn ec;\n'),
    ('\tc->xyz_complete = 1;\n'
     '\tif ((ec = clean_input(p)) != PR655_OK) return ec;\n'
     '\tc->xyz_parsed = 1;\n\treturn PR655_OK;\n',
     '\tc->xyz_complete = 1;\n\tec = clean_input(p);\n'
     '\tif (ec == PR655_OK || (ec == CM_PR655_TRACE_LIMIT && p->state == pr655_ready))\n'
     '\t\tc->xyz_parsed = 1;\n\treturn ec;\n'),
)
_HEADER = (
    ('#include "pr655_imp.h"\n', '#include "pr655_imp.h"\n#include "pr655_control.h"\n'),
    ('\tpr655_capture_result capture;\n',
     '\tpr655_capture_result capture;\n\tcm_pr655_host host;\n'),
    ('\tint valid, setup_before_valid, setup_after_valid;\n',
     '\tint valid, setup_before_valid, setup_after_valid;\n'
     '\tint geometry_valid; /* CM: independent qualification of native XYZ scale */\n'
     '\tint xyz_publication_valid;\n'
     '\tdouble published_xyz[3], native_to_published_xyz_scale;\n'),
)
_SOURCE = (
    ('\t(void)pr655_imp_close(&p->engine, RESPONSE_SECONDS);\n',
     '\t(void)pr655_imp_close(&p->engine, RESPONSE_SECONDS);\n'
     '\tcm_pr655_host_dispose(pp);\n'),
    ('\tp->mode = mode;\n',
     '\tp->mode = mode;\n\tp->host.explicit_sync = 0; /* latest ordinary mode intent */\n'),
    ('\tp->refresh_hz = hz;\n',
     '\tp->refresh_hz = hz;\n\tp->host.explicit_sync = 0; /* latest ordinary refresh intent */\n'),
    ('\tint code, cycles, sync, i;\n',
     '\tint code, cycles, sync, i, geometry_before;\n'),
    ('\tc->before = *s; c->setup_before_valid = 1;\n'
     '\tif ((code = geometry(p)) != PR655_OK) return map(code);\n',
     '\tc->before = *s; c->setup_before_valid = 1;\n'
     '\t/* CM: retain native observations before judging their Argyll mapping. */\n'
     '\tc->geometry_valid = 0;\n'
     '\tgeometry_before = geometry(p);\n'),
    ('\treturn geometry(p);\n',
     '\treturn PR655_OK; /* CM: geometry governs publication after capture. */\n'),
    ('\tif ((code = geometry(p)) != PR655_OK) return map(code);\n'
     '\tif ((code = budget(p, cycles, sync, frequency, &c->budget_s)) != PR655_OK) '
     'return map(code);\n',
     '\tif ((code = budget(p, cycles, sync, frequency, &c->budget_s)) != PR655_OK) '
     'return map(code);\n'),
    ('\tif (IMODETST(p->mode, inst_mode_emis_norefresh_ovd)) sync = 0;\n'
     '\telse if (p->refresh_hz > 0.0) { sync = 3; frequency = p->refresh_hz; }\n'
     '\telse if (IMODETST(p->mode, inst_mode_emis_refresh_ovd)) sync = 1;\n',
     '\tif (!p->host.explicit_sync) {\n'
     '\t\tif (IMODETST(p->mode, inst_mode_emis_norefresh_ovd)) sync = 0;\n'
     '\t\telse if (p->refresh_hz > 0.0) { sync = 3; frequency = p->refresh_hz; }\n'
     '\t\telse if (IMODETST(p->mode, inst_mode_emis_refresh_ovd)) sync = 1;\n'
     '\t}\n'),
    ('\t\treturn map(refuse(p, PR655_CAPTURE_MISMATCH, '
     '"material setup changed across the capture"));\n'
     '\tmemset(&spectrum, 0, sizeof(spectrum));\n',
     '\t\treturn map(refuse(p, PR655_CAPTURE_MISMATCH, '
     '"material setup changed across the capture"));\n'
     '\tif (geometry_before != PR655_OK) return map(geometry_before);\n'
     '\tc->geometry_valid = 1; /* CM: native scale qualified by stable reported geometry. */\n'
     '\tmemset(&spectrum, 0, sizeof(spectrum));\n'),
    ('\t\tcase PR655_BUDGET_ERROR: case PR655_ALREADY_OPEN:',
     '\t\tcase CM_PR655_ALLOWANCE: case PR655_BUDGET_ERROR: case PR655_ALREADY_OPEN:'),
    ('\tc->valid = 1;\n\treturn inst_ok;\n',
     '\tc->valid = 1;\n'
     '\tmemcpy(c->published_xyz, out->XYZ, sizeof(c->published_xyz));\n'
     '\tc->native_to_published_xyz_scale = c->unit.xyz_scale;\n'
     '\tc->xyz_publication_valid = 1;\n\treturn inst_ok;\n'),
    ('\t/* All intent and the known timing policy validated before any setter. */\n',
     '\t/* All intent and the known timing policy validated before any setter. */\n'
     '\tif ((code = cm_pr655_preflight((inst *)p, c->budget_s,\n'
     '\t        (cycles != s->cycles) + (sync == 3 && frequency != s->frequency_hz)\n'
     '\t        + (sync != s->sync_mode))) != PR655_OK) return map(code);\n'),
    ('\tc->before = *s;\n\tcode = pr655_imp_measure',
     '\tc->before = *s;\n'
     '\tif (geometry_before == PR655_OK) geometry_before = geometry(p);\n'
     '\tif ((code = cm_pr655_preflight((inst *)p, c->budget_s, 0)) != PR655_OK) return map(code);\n'
     '\tcode = pr655_imp_measure'),
    ('\ttrigger = p->trigger;\n', '\tcm_pr655_capture_reset(pp);\n\ttrigger = p->trigger;\n'),
    ('\tint user = 0;\n', '\tint user = 0;\n\tunsigned int started_ms = p->engine.now();\n'),
    ('\tif ((rv = ready(p)) == inst_ok) rv = capture_reading(p, out, clamp);\n',
     '\t{ int admission = cm_pr655_capture_begin(pp, started_ms);\n'
     '\t  if (admission != PR655_OK) rv = map(admission);\n'
     '\t  else if ((rv = ready(p)) == inst_ok) rv = capture_reading(p, out, clamp); }\n'
     '\t{ int code = cm_pr655_capture_end(pp, rv & inst_imask);\n'
     '\t  if (code != PR655_OK && rv == inst_ok) {\n'
     '\t      memset(out, 0, sizeof(*out)); p->capture.valid = 0; rv = map(code);\n'
     '\t  } }\n'),
    ('\tpr655_imp_init(&p->engine, icom, model);\n',
     '\tpr655_imp_init(&p->engine, icom, model);\n'
     '\tp->engine.host_observe = cm_pr655_observe; p->engine.host_context = p;\n'),
    ('\tif (code == PR655_OK) return "No error";\n',
     '\tif (code == PR655_OK) return "No error";\n'
     '\tif (code == CM_PR655_ALLOWANCE && p->capture.problem[0]) return p->capture.problem;\n'),
)


def _apply(path: str, text: str, edits, marker: str, suffix: str = "") -> str:
    if marker in text:
        raise ValueError(f"{path} already carries PR application additions")
    for old, new in edits:
        if text.count(old) != 1:
            raise ValueError(f"{path}: anchor missing or ambiguous: {old.splitlines()[0]!r}")
        text = text.replace(old, new)
    return text + suffix


def pr655_host_header_patch(text: str) -> str:
    return _apply("spectro/pr655.h", text, _HEADER, "cm_pr655_host")


def pr655_host_source_patch(text: str) -> str:
    return _apply("spectro/pr655.c", text, _SOURCE, "cm_pr655_preflight")


def pr655_host_imp_header_patch(text: str) -> str:
    return _apply("spectro/pr655_imp.h", text, _IMP_HEADER, "host_observe")


def pr655_host_imp_source_patch(text: str) -> str:
    return _apply("spectro/pr655_imp.c", text, _IMP_SOURCE, "pr655_control.h", _ENGINE_HELPERS)


PATCHES = (("spectro/pr655.h", pr655_host_header_patch),
           ("spectro/pr655.c", pr655_host_source_patch),
           ("spectro/pr655_imp.h", pr655_host_imp_header_patch),
           ("spectro/pr655_imp.c", pr655_host_imp_source_patch))
