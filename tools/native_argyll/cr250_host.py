"""This application's read allowance and reply retention, composed onto the CR driver.

The CR contribution (contributions/cr_pr) holds only what an Argyll submission
needs. Four things this application needs from the driver have no place in
Argyll's instrument API, so they are composed here, after the series and only in
a build that composed it:

- A host read allowance. The worker's supervisor stops a read at its allowance
  (contract level 7), so ``read_sample`` refuses before M when the allowance
  left cannot cover ``capture.required_s``: the M budget plus every capture read
  after M at its deadline. ``cr250_required_floor_s`` gives the least
  ``required_s`` a model can have in a mode; ``cr_floor.c`` publishes it as the
  bind floor.
  Preflight also reserves both deadlines of each planned SM/RS transaction
  before the first setting write, and the final check uses actual remaining time.
- The capture's native replies, in order, for ``native_facts.h`` (cr250_support.c):
  every setting read, inventory header, write, M and spectrum header of the read.
  Configuration reads an option setter makes between reads are excluded.
- Which native triplets the read parsed (``capture.parsed``): RM XYZ, and RM
  Radiometric once its type matches the model. ``capture.valid`` says whether
  the reading was published; this says whether a value exists at all, so a
  refused read keeps its native XYZ and a zero or signed radiance is not taken
  for "not read".
- Copies the driver's checks use but do not keep, for ``native_facts.h``:
  ``capture.valid``, each setting write (``capture.write``: the value before,
  the target and the value after), the exposure and Auto limit used
  (``exposure_ms``, ``max_auto_exposure_ms``) and the RC record each listed
  setting matched (``matched``).
  A planned inventory record becomes a match only when RS already agrees or
  the setter's readback confirms it. SyncFreq retains its exact pre-write RS
  text across the other capability queries.

Typed operator controls in ``cr250_control.c`` extend the same closed engine
setter list. They retain an explicit exposure/synchronization intent, block
measurement after a failed settings write, and reuse the actual Argyll profile
selector under the instrument lock. Ordinary Argyll callers keep their defaults;
these application controls do not belong to the imported contribution.

``required_after`` restates which reads the driver makes after M. The anchors
below pin the code around it, and cr250_host_test.c runs every read with each
reply arriving just inside its deadline and requires the time from M to match
``required_s``, so a read added to or removed from that path fails the gate
rather than making the allowance fail open.

Every anchor must occur exactly once in the series text, and a text that already
carries these additions is refused. The driver is Fernando Voltolini de
Azambuja's GPL-2.0-or-later work; these additions carry the same terms.
"""
from __future__ import annotations

REPAIR_ID = 'app-policy:cr250-host'

HEADER = "spectro/cr250.h"
SOURCE = "spectro/cr250.c"

# One marker per file that only these additions introduce.
_MARKERS = {HEADER: "host_allowance_s", SOURCE: "host_allowance_s"}

_HEADER_EDITS = (
    (
        "#define CR250_MAX_SETUP         16\t/* Most settings one model row may check */\n",
        "#define CR250_MAX_SETUP         16\t/* Most settings one model row may check */\n"
        "#define CR250_MAX_WRITES        7\t/* CM: keys the driver may set, each at most once per"
        " read */\n"
        "/* CM: retained reply headers on the longest path: each setup slot may\n"
        " * read RS, RC inventory and RM (3 each). Each write may add RC, SM and\n"
        " * a confirming RS (3 each). Outside those loops, allow the existing\n"
        " * RC MaxExposure, RC MinExposure, RS Exposure, RC Min/MaxExposureX, RS\n"
        " * SyncFreq, SM SyncFreq, its second RS, RC Min/MaxSyncFreq, RC Mode,\n"
        " * RC Range, M, RM Model, ID, Warnings, Radiometric, SyncFreq, Exposure,\n"
        " * MaxAutoExposure, Range, Time,\n"
        " * XYZ and the RM Spectrum header, plus Filter and correction inventory,\n"
        " * profile preparation, writes and RS/RM readback (30). */\n"
        "#define CR250_FACTS_MAX         (3 * CR250_MAX_SETUP + 3 * CR250_MAX_WRITES + 30)\n"
        "/* CM: capture.parsed -- the native triplets this read parsed and validated,\n"
        " * zero and signed values included, kept when a later check refuses it. */\n"
        "#define CR250_PARSED_XYZ         0x01\t/* RM XYZ */\n"
        "#define CR250_PARSED_RADIOMETRIC 0x02\t/* RM Radiometric, of the model's type */\n",
    ),
    (
        "/* Whether the spectrum under the inferred density model and the instrument's\n",
        "/* CM: a setting the driver wrote during this read. Recorded before \"SM\" is\n"
        " * sent, so a failure afterwards still shows the attempt: an empty after with\n"
        " * confirmed 0 is an attempt whose delivery and outcome are unknown. */\n"
        "typedef struct {\n"
        "\tchar key[CR250_MAX_FIELD];\n"
        "\tchar before[CR250_MAX_VALUE];\t\t/* RS before the write */\n"
        "\tchar target[CR250_MAX_VALUE];\t\t/* Value set, in its RS form */\n"
        "\tchar after[CR250_MAX_VALUE];\t\t/* RS after the write, empty if not reached */\n"
        "\tint confirmed;\t\t\t\t\t\t/* nz: after equals target */\n"
        "} cr250_write;\n"
        "\n"
        "/* Whether the spectrum under the inferred density model and the instrument's\n",
    ),
    (
        "typedef struct {\n\tchar problem[192];",
        "typedef struct {\n"
        "\tint count;                        /* CM: replies retained this read */\n"
        "\tcr250_reply reply[CR250_FACTS_MAX]; /* CM: native preflight, writes, M and capture"
        " replies */\n"
        "\tint valid;                        /* CM: all checks passed, reading published */\n"
        "\tint published_spectrum_count;     /* CM: actual final ipatch spectral output,"
        " 0 otherwise */\n"
        "\tunsigned int parsed;              /* CM: CR250_PARSED_* this read, whatever valid"
        " says */\n"
        "\tchar problem[192];",
    ),
    (
        "\t                                     when not applicable on this instrument */\n",
        "\t                                     when not applicable on this instrument */\n"
        "\tint nwrites;                      /* CM */\n"
        "\tcr250_write write[CR250_MAX_WRITES]; /* CM */\n",
    ),
    (
        "\tdouble fixed_exposure_ms;",
        "\tdouble exposure_ms;               /* CM: RM Exposure, the exposure used */\n"
        "\tdouble fixed_exposure_ms;",
    ),
    (
        "\tint exposure_x;                   /* RS ExposureX */\n",
        "\tdouble max_auto_exposure_ms;      /* CM: RM MaxAutoExposure, 0 when not read or not"
        " reported */\n"
        "\tint exposure_x;                   /* RS ExposureX */\n",
    ),
    (
        "\tcr250_record range;",
        "\tcr250_record matched[CR250_MAX_SETUP]; /* CM: RC record named by setup[i] when"
        " setup[i].inventory */\n"
        "\tcr250_record range;",
    ),
    (
        "\tdouble budget_s;                  /* M deadline: the computed measurement budget */\n",
        "\tdouble budget_s;                  /* M deadline: the computed measurement budget */\n"
        "\tdouble remaining_s;               /* CM: host allowance remaining at the check before"
        " writes or M;\n"
        "\t                                     meaningful when required_s and host_allowance_s >"
        " 0 */\n"
        "\tdouble required_s;                /* CM: worst case from M to the last capture read:"
        " budget_s\n"
        "\t                                     plus every later read at its deadline */\n",
    ),
    (
        "\t                                     not applicable (optional settings only) */\n",
        "\t                                     not applicable (optional settings only) */\n"
        "\tdouble host_allowance_s;          /* CM: seconds a caller allows for the next"
        " read_sample,\n"
        "\t                                     counted from its start, or 0 (the default): no\n"
        "\t                                     limit. If what remains before M is less than\n"
        "\t                                     capture.required_s, read_sample refuses before M\n"
        "\t                                     with CR250_BUDGET_TOO_SHORT. */\n"
        "\tint capturing;                    /* CM: nz while read_sample runs its transaction, so\n"
        "\t                                     only its replies are retained */\n",
    ),
    (
        "instType cr250_model_reply(const char *line);\n",
        "instType cr250_model_reply(const char *line);\n"
        "\n"
        "/* CM: the least capture.required_s any read of dtype in mode can have: the\n"
        " * 120 s measurement floor plus the capture reads that path cannot skip, each\n"
        " * at its deadline. A host allowance at or below it is refused before M on\n"
        " * every read; above it is necessary, not sufficient, since the reads before\n"
        " * M, a user trigger, longer exposure settings and optional reads also spend\n"
        " * the allowance. -1 for a dtype without a row or a mode it refuses. */\n"
        "double cr250_required_floor_s(instType dtype, inst_mode mode);\n",
    ),
)

_SOURCE_EDITS = (
    (
        "/* SM takes an RC list ID for these keys",
        "/* CM: record a write before it is sent, so a failure afterwards still shows it. */\n"
        "static cr250_write *record_write(cr250 *p, const char *key, const char *before, const char"
        " *want) {\n"
        "\tcr250_write *w;\n"
        "\tif (p->capture.nwrites >= CR250_MAX_WRITES) return NULL;\n"
        "\tw = &p->capture.write[p->capture.nwrites++];\n"
        "\tsnprintf(w->key, sizeof(w->key), \"%s\", key);\n"
        "\tsnprintf(w->before, sizeof(w->before), \"%s\", before);\n"
        "\tsnprintf(w->target, sizeof(w->target), \"%s\", want);\n"
        "\treturn w;\n"
        "}\n"
        "\n"
        "/* SM takes an RC list ID for these keys",
    ),
    (
        "static int set_value(cr250 *p, const cr250_setting *s, char *value, const char *want,"
        " const char *arg) {\n"
        "\tchar cmd[64];\n",
        "static int set_value(cr250 *p, const cr250_setting *s, char *value, const char *want,"
        " const char *arg) {\n"
        "\tcr250_write *w;\t\t\t\t\t\t/* CM */\n"
        "\tchar cmd[64];\n",
    ),
    (
        "\tif ((ec = write_value(p, s->key, arg)) != CR250_OK) return ec;\n"
        "\tsnprintf(cmd, sizeof(cmd), \"RS %s\", s->key);\n"
        "\tif ((ec = read_value(p, cmd, value)) != CR250_OK) return ec;\n"
        "\tif (strcmp(value, want) != 0)\n"
        "\t\treturn refuse(p, CR250_SETUP, \"SM %s %s left RS %s at %s, not %s\", s->key, arg,"
        " s->key, value, want);\n"
        "\treturn CR250_OK;\n",
        "\tif ((w = record_write(p, s->key, value, want)) == NULL) return CR250_INTERNAL_ERROR;\n"
        "\tif ((ec = write_value(p, s->key, arg)) != CR250_OK) return ec;\n"
        "\tsnprintf(cmd, sizeof(cmd), \"RS %s\", s->key);\n"
        "\tif ((ec = read_value(p, cmd, value)) != CR250_OK) return ec;\n"
        "\tsnprintf(w->after, sizeof(w->after), \"%s\", value);\n"
        "\tif (strcmp(value, want) != 0)\n"
        "\t\treturn refuse(p, CR250_SETUP, \"SM %s %s left RS %s at %s, not %s\", s->key, arg,"
        " s->key, value, want);\n"
        "\tw->confirmed = 1;\n"
        "\treturn CR250_OK;\n",
    ),
    (
        "\tif (frequency_argument[0]) {\n"
        "\t\tif ((ec = write_value(p, \"SyncFreq\", frequency_argument))"
        " != CR250_OK) return map(ec);\n"
        "\t\tif ((ec = read_value(p, \"RS SyncFreq\", value)) != CR250_OK) return map(ec);\n",
        "\tif (frequency_argument[0]) {\n"
        "\t\tcr250_write *w;\t\t\t\t\t/* CM */\n"
        "\t\tif ((w = record_write(p, \"SyncFreq\", frequency_before, frequency_argument))"
        " == NULL) return"
        " map(CR250_INTERNAL_ERROR);\n"
        "\t\tif ((ec = write_value(p, \"SyncFreq\", frequency_argument))"
        " != CR250_OK) return map(ec);\n"
        "\t\tif ((ec = read_value(p, \"RS SyncFreq\", value)) != CR250_OK) return map(ec);\n"
        "\t\tsnprintf(w->after, sizeof(w->after), \"%s\", value);\n",
    ),
    (
        "\t\t\treturn map(refuse(p, CR250_SETUP, \"SM SyncFreq %s left RS SyncFreq at %s\","
        " frequency_argument, value));\n",
        "\t\t\treturn map(refuse(p, CR250_SETUP, \"SM SyncFreq %s left RS SyncFreq at %s\","
        " frequency_argument, value));\n"
        "\t\tw->confirmed = 1;\n",
    ),
    (
        "\tcr250_record record;\n",
        "\tcr250_record record;\n"
        "\tcr250_record planned_records[CR250_MAX_SETUP] = {{0}}; /* CM */\n",
    ),
    (
        "\t\tif (want == NULL || strcmp(v, want) == 0) continue;\n",
        "\t\tif (setup[i].inventory) { /* CM: planned is not an applied RS match yet */\n"
        "\t\t\tplanned_records[i] = record;\n"
        "\t\t\tif (want == NULL || strcmp(v, want) == 0) c->matched[i] = record;\n"
        "\t\t}\n"
        "\t\tif (want == NULL || strcmp(v, want) == 0) continue;\n",
    ),
    (
        "\t\tif ((ec = set_value(p, &setup[i], c->value[i], want, arguments[i]))"
        " != CR250_OK) return map(ec);\n",
        "\t\tif ((ec = set_value(p, &setup[i], c->value[i], want, arguments[i]))"
        " != CR250_OK) return map(ec);\n"
        "\t\tif (setup[i].inventory) c->matched[i] = planned_records[i]; /* CM */\n",
    ),
    (
        "\tchar frequency_argument[32] = {0};\n",
        "\tchar frequency_argument[32] = {0};\n"
        "\tchar frequency_before[CR250_MAX_VALUE] = {0}; /* CM: exact pre-write RS */\n",
    ),
    (
        "\t\tif ((ec = read_value(p, \"RS SyncFreq\", value)) != CR250_OK) return map(ec);\n"
        "\t\tif (!hertz(value, &c->sync_hz)) return map(CR250_DATA_PARSE_ERROR);\n",
        "\t\tif ((ec = read_value(p, \"RS SyncFreq\", value)) != CR250_OK) return map(ec);\n"
        "\t\tstrcpy(frequency_before, value); /* CM */\n"
        "\t\tif (!hertz(value, &c->sync_hz)) return map(CR250_DATA_PARSE_ERROR);\n",
    ),
    (
        "\tif (!milliseconds(value, &exp)) return map(CR250_DATA_PARSE_ERROR);\n",
        "\tif (!milliseconds(value, &exp)) return map(CR250_DATA_PARSE_ERROR);\n"
        "\tc->exposure_ms = exp;\t\t\t\t\t\t/* CM */\n",
    ),
    (
        "\"RM Exposure %.3f ms, RM MaxAutoExposure %s, RC MaxExposure %.3f ms\", exp, value,"
        " maxexp));\n",
        "\"RM Exposure %.3f ms, RM MaxAutoExposure %s, RC MaxExposure %.3f ms\", exp, value,"
        " maxexp));\n"
        "\t\t\tc->max_auto_exposure_ms = autoexp;\t/* CM */\n",
    ),
    (
        "\tout->mcond = inst_mrc_none;\n\treturn inst_ok;\n",
        "\tout->mcond = inst_mrc_none;\n"
        "\tc->published_spectrum_count = out->sp.spec_n; /* CM: exact mapping, XYZ-only is 0 */\n"
        "\tc->valid = 1;\t\t\t\t\t\t\t/* CM */\n\treturn inst_ok;\n",
    ),
    (
        "static int read_value(cr250 *p, const char *cmd, char value[CR250_MAX_VALUE]) {\n"
        "\tcr250_reply r;\n"
        "\tint ec = cr250_imp_command(&p->engine, cmd, &r, READ_TIMEOUT);\n"
        "\tif (ec == CR250_OK) strcpy(value, r.value);\n"
        "\treturn ec;\n"
        "}\n",
        "static int read_value(cr250 *p, const char *cmd, char value[CR250_MAX_VALUE]) {\n"
        "\tcr250_reply r;\n"
        "\tint ec;\n"
        "\t/* CM: never publish a measurement whose diagnostic replies were dropped. */\n"
        "\tif (p->capturing && p->capture.count >= CR250_FACTS_MAX) return CR250_INTERNAL_ERROR;\n"
        "\tec = cr250_imp_command(&p->engine, cmd, &r, READ_TIMEOUT);\n"
        "\tif (p->capturing && r.complete)\n"
        "\t\tp->capture.reply[p->capture.count++] = r;\n"
        "\tif (ec == CR250_OK) strcpy(value, r.value);\n"
        "\treturn ec;\n"
        "}\n",
    ),
    (
        "static int write_value(cr250 *p, const char *key, const char *arg) {\n"
        "\tcr250_reply r;\n"
        "\treturn cr250_imp_setting(&p->engine, key, arg, &r, READ_TIMEOUT);\n"
        "}\n",
        "static int write_value(cr250 *p, const char *key, const char *arg) {\n"
        "\tcr250_reply r;\n"
        "\tint ec;\n"
        "\tif (p->capturing && p->capture.count >= CR250_FACTS_MAX) return CR250_INTERNAL_ERROR;\n"
        "\tec = cr250_imp_setting(&p->engine, key, arg, &r, READ_TIMEOUT);\n"
        "\tif (p->capturing && r.complete)\n"
        "\t\tp->capture.reply[p->capture.count++] = r;\n"
        "\treturn ec;\n"
        "}\n",
    ),
    (
        "static int unique_entry(cr250 *p, const char *key, const char *name, "
        "const char *type, cr250_record *record) {\n",
        "/* CM: retain the status header of a completed inventory, including a refusal.\n"
        " * Clear the previous status first: an inventory rejected before send must not\n"
        " * duplicate a different command's reply. Its body remains in inv. */\n"
        "static int inventory_value(cr250 *p, const char *key, cr250_inventory *inv) {\n"
        "\tint ec;\n"
        "\tif (p->capturing && p->capture.count >= CR250_FACTS_MAX) return CR250_INTERNAL_ERROR;\n"
        "\tmemset(&p->engine.last_reply, 0, sizeof(p->engine.last_reply));\n"
        "\tec = cr250_imp_inventory(&p->engine, key, inv, READ_TIMEOUT);\n"
        "\tif (p->capturing && p->engine.last_reply.complete)\n"
        "\t\tp->capture.reply[p->capture.count++] = p->engine.last_reply;\n"
        "\treturn ec;\n"
        "}\n"
        "\n"
        "static int unique_entry(cr250 *p, const char *key, const char *name, "
        "const char *type, cr250_record *record) {\n",
    ),
    (
        "\tif ((ec = cr250_imp_inventory(&p->engine, key, &inv, READ_TIMEOUT)) != CR250_OK)\n"
        "\t\tgoto done;\n",
        "\tif ((ec = inventory_value(p, key, &inv)) != CR250_OK)\n"
        "\t\tgoto done;\n",
    ),
    (
        '\tif ((ec = cr250_imp_inventory(&p->engine, '
        'key, &inv, READ_TIMEOUT)) != CR250_OK) {\n',
        "\tif ((ec = inventory_value(p, key, &inv)) != CR250_OK) {\n",
    ),
    (
        "cr250_imp_inventory(&p->engine, \"Filter\", &inv, READ_TIMEOUT)",
        "inventory_value(p, \"Filter\", &inv)",
    ),
    (
        "cr250_imp_inventory(&p->engine, \"Mode\", &inv, READ_TIMEOUT)",
        "inventory_value(p, \"Mode\", &inv)",
    ),
    (
        "static const char *nearest_factor(double r) {\n",
        "/* CM: seconds from M to the last capture read, each at its deadline: the\n"
        " * measurement budget, every setting's RM, the fixed RM Model, ID, Warnings,\n"
        " * Exposure, Time and XYZ, and what this read's settings add. */\n"
        "static double required_after(const cr250_model *m, int spectral, double budget, int"
        " nsetup,\n"
        '                             int auto_limit, int '
        'range_reads, int sync_read, int correction_read) {\n'
        "\tdouble t = budget + READ_TIMEOUT * (nsetup + 6 + (m->radiometric_type >= 0 ? 1 : 0)\n"
        '\t                                    + '
        'auto_limit + range_reads + sync_read + correction_read);\n'
        "\treturn spectral ? t + SPECTRUM_TIMEOUT : t;\n"
        "}\n"
        "\n"
        "static const char *nearest_factor(double r) {\n",
    ),
    (
        "\tif ((*nsetup = setting_count(p->model)) < 0) return map(CR250_INTERNAL_ERROR);\n"
        "\treturn link_ready(p);\n",
        "\tif ((*nsetup = setting_count(p->model)) < 0) return map(CR250_INTERNAL_ERROR);\n"
        "\tif (p->host_allowance_s != 0.0\n"
        "\t && !(cr250_finite(p->host_allowance_s) && p->host_allowance_s > 0.0))\n"
        "\t\treturn inst_bad_parameter;\n"
        "\treturn link_ready(p);\n",
    ),
    (
        "static inst_code capture_reading(cr250 *p, int nsetup, ipatch *out, instClamping clamp)"
        " {\n",
        "static inst_code capture_reading(cr250 *p, int nsetup, unsigned int start_ms,\n"
        "                                 ipatch *out, instClamping clamp) {\n",
    ),
    (
        "\tc->budget_s = budget;\n",
        "\tc->budget_s = budget;\n"
        "\tc->required_s = required_after(m, spectral, budget, nsetup, auto_limit, range_reads,"
        " sync_read, p->correction_mode != 0);\n"
        "\t/* CM: reserve each planned SM and its RS readback before any setting changes. */\n"
        "\tif (p->host_allowance_s > 0.0) {\n"
        "\t\tdouble setting_s = (frequency_argument[0] ? 2.0 * READ_TIMEOUT : 0.0)\n"
        "\t\t                 + (correction_argument[0] ? READ_TIMEOUT : 0.0)\n"
        "\t\t                 + (p->correction_mode ? READ_TIMEOUT : 0.0);\n"
        "\t\tfor (i = 0; i < nsetup; i++)\n"
        "\t\t\tif (arguments[i][0]) setting_s += 2.0 * READ_TIMEOUT;\n"
        "\t\tc->remaining_s = p->host_allowance_s - (p->engine.now_ms() - start_ms) / 1000.0;\n"
        "\t\tif (c->remaining_s < c->required_s + setting_s) {\n"
        "\t\t\tif (setting_s > 0.0)\n"
        "\t\t\t\treturn map(refuse(p, CR250_BUDGET_TOO_SHORT,\n"
        "\t\t\t\t    \"Read allowance remaining %.3f s is below required %.3f s"
        " plus %.3f s for setting writes/readbacks\",\n"
        "\t\t\t\t    c->remaining_s, c->required_s, setting_s));\n"
        "\t\t\treturn map(CR250_BUDGET_TOO_SHORT);\n"
        "\t\t}\n"
        "\t}\n",
    ),
    (
        "\tif ((ec = cr250_imp_measure(&p->engine, &r, budget, budget)) != CR250_OK) return"
        " map(ec);\n",
        "\t/* CM: a caller that would stop before the last read is refused before M, not\n"
        "\t * left with a completed exposure whose metadata it never reads. */\n"
        "\tif (p->host_allowance_s > 0.0) {\n"
        "\t\tc->remaining_s = p->host_allowance_s - (p->engine.now_ms() - start_ms) / 1000.0;\n"
        "\t\tif (c->remaining_s < c->required_s)\n"
        "\t\t\treturn map(CR250_BUDGET_TOO_SHORT);\n"
        "\t}\n"
        "\t/* CM: bounded like every other retained reply, and refused before M. */\n"
        "\tif (c->count >= CR250_FACTS_MAX) return map(CR250_INTERNAL_ERROR);\n"
        "\tec = cr250_imp_measure(&p->engine, &r, budget, budget);\n"
        "\tif (r.complete) c->reply[c->count++] = r;\n"
        "\tif (ec != CR250_OK) return map(ec);\n",
    ),
    (
        "\t\tif ((ec = cr250_imp_spectrum(&p->engine, &c->spectrum, &r, SPECTRUM_TIMEOUT)) !="
        " CR250_OK) {\n",
        "\t\tif (c->count >= CR250_FACTS_MAX) return map(CR250_INTERNAL_ERROR);\n"
        "\t\tec = cr250_imp_spectrum(&p->engine, &c->spectrum, &r, SPECTRUM_TIMEOUT);\n"
        "\t\tif (r.complete) c->reply[c->count++] = r;\n"
        "\t\tif (ec != CR250_OK) {\n",
    ),
    (
        "\t\t\treturn map(refuse(p, CR250_CAPTURE_MISMATCH, \"RM Radiometric type is %g, not"
        " %d\",\n"
        "\t\t\t                  c->radiometric[0], m->radiometric_type));\n"
        "\t}\n",
        "\t\t\treturn map(refuse(p, CR250_CAPTURE_MISMATCH, \"RM Radiometric type is %g, not"
        " %d\",\n"
        "\t\t\t                  c->radiometric[0], m->radiometric_type));\n"
        "\t\tc->parsed |= CR250_PARSED_RADIOMETRIC;\t/* CM */\n"
        "\t}\n",
    ),
    (
        "\tfor (i = 0; i < 3; i++) c->xyz[i] = xyz[i];\n",
        "\tfor (i = 0; i < 3; i++) c->xyz[i] = xyz[i];\n"
        "\tc->parsed |= CR250_PARSED_XYZ;\t\t/* CM */\n",
    ),
    (
        "\tint nsetup, user = 0;\n"
        "\tint prior_presence[CR250_MAX_SETUP];\n"
        "\t(void)name;\n",
        "\tint nsetup, user = 0;\n"
        "\tint prior_presence[CR250_MAX_SETUP];\n"
        "\tunsigned int start_ms = p->engine.now_ms();\t\t/* CM: host_allowance_s runs from here"
        " */\n"
        "\t(void)name;\n",
    ),
    (
        "\tif ((rv = ready(p, &nsetup)) == inst_ok) {\n"
        "\t\tmemcpy(prior_presence, p->presence, sizeof(prior_presence));\n"
        "\t\trv = capture_reading(p, nsetup, out, clamp);\n"
        "\t\tif (rv != inst_ok)\n"
        "\t\t\tmemcpy(p->presence, prior_presence, sizeof(prior_presence));\n"
        "\t}\n",
        "\tif ((rv = ready(p, &nsetup)) == inst_ok) {\n"
        "\t\tmemcpy(prior_presence, p->presence, sizeof(prior_presence));\n"
        "\t\tp->capturing = 1;\n"
        "\t\trv = capture_reading(p, nsetup, start_ms, out, clamp);\n"
        "\t\tp->capturing = 0;\n"
        "\t\tif (rv != inst_ok)\n"
        "\t\t\tmemcpy(p->presence, prior_presence, sizeof(prior_presence));\n"
        "\t}\n",
    ),
    (
        "\tswitch (ec & inst_imask) {\n\t\tcase CR250_SETUP:\n",
        "\tswitch (ec & inst_imask) {\n"
        "\t\tcase CR250_BUDGET_TOO_SHORT:\n"
        "\t\t\tif (p->capture.problem[0]) snprintf(text, sizeof(text),"
        " \"%s\", p->capture.problem);\n"
        "\t\t\telse snprintf(text, sizeof(text), \"Read allowance remaining %.3f s"
        " is below required"
        " %.3f s\",\n"
        "\t\t\t         p->capture.remaining_s, p->capture.required_s);\n"
        "\t\t\tbreak;\n"
        "\t\tcase CR250_SETUP:\n",
    ),
    (
        "cr250 *new_cr250(icoms *icom, instType dtype) {\n",
        "/* CM: the shortest capture path: the 120 s budget floor, every setting's RM (an\n"
        " * optional one still answers), the fixed reads, RM MaxAutoExposure in Auto\n"
        " * when the row's floor guarantees firmware 1.26, and RM Range when the row\n"
        " * requires RangeMode (Fixed range reads only RM Range). No sync read, since\n"
        " * SyncMode None needs none. */\n"
        "double cr250_required_floor_s(instType dtype, inst_mode mode) {\n"
        "\tconst cr250_model *m = model_for(dtype);\n"
        "\tint i, auto_limit, range_reads;\n"
        "\tif (m == NULL || !mode_ok(m, mode)) return -1.0;\n"
        "\ti = setting_index(m, \"RangeMode\");\n"
        "\trange_reads = i < 0 || m->setup[i].optional ? 0 : 1;\n"
        "\tauto_limit = !(m->nonadaptive && (mode & inst_mode_emis_nonadaptive))\n"
        "\t          && (m->fw_major > 1 || (m->fw_major == 1 && m->fw_minor >= 26));\n"
        "\treturn required_after(m, m->spectral && (mode & inst_mode_spectral),"
        " CR250_MEAS_FLOOR_S,\n"
        "\t                      setting_count(m), auto_limit, range_reads, 0, 0);\n"
        "}\n"
        "\n"
        "cr250 *new_cr250(icoms *icom, instType dtype) {\n",
    ),
)


# Operator controls belong to the application. The contribution keeps its
# ordinary Argyll defaults and does not offer optics declaration setters.
IMPL = "spectro/cr250_imp.c"
IMP_HEADER = "spectro/cr250_imp.h"
_MARKERS[IMPL] = "CM: closed operator control keys"
_MARKERS[IMP_HEADER] = "host_observe"
_HEADER_EDITS += (
    ('\tcr250_record correction;         /* Checked '
     'RC Matrix/Match ID and name, id -1 for None */\n',
     '\tcr250_record correction;         /* Checked '
     'RC Matrix/Match ID and name, id -1 for None */\n'
     '\tchar correction_before[CR250_MAX_VALUE]; /* '
     'CM: RS profile when its prior mode was active */\n'
     "\tchar correction_after[CR250_MAX_VALUE]; /* CM: actual RS profile after its write */\n"),
    ("\tdouble host_allowance_s;",
     '\tint control_exposure_mode;       /* CM: 0 '
     'Argyll recipe, 1 explicit Auto, 2 explicit Fixed */\n'
     '\tchar control_sync_name[CR250_MAX_FIELD]; /* '
     'CM: explicit reported native synchronization */\n'
     "\tunsigned int control_blocked;    /* CM: attempted fields a successful apply must cover */\n"
     "\tint (*control_observe)(void *, const char *, const cr250_reply *, int);\n"
     '\tvoid *control_context;          /* CM: '
     'bounded receipt sink while the same lock is held */\n'
     "\tdouble host_allowance_s;"),
)
_SOURCE_EDITS += (
    ("\tif (ec == CR250_OK) strcpy(value, r.value);\n\treturn ec;\n}\n",
     "\tif (p->control_observe) ec = p->control_observe(p->control_context, cmd, &r, ec);\n"
     "\tif (ec == CR250_OK) strcpy(value, r.value);\n\treturn ec;\n}\n"),
    ("\t\tp->capture.reply[p->capture.count++] = p->engine.last_reply;\n\treturn ec;\n",
     "\t\tp->capture.reply[p->capture.count++] = p->engine.last_reply;\n"
     "\tif (p->control_observe) {\n"
     "\t\tchar cmd[CR250_MAX_COMMAND + 1];\n"
     "\t\tsnprintf(cmd, sizeof(cmd), \"RC %s\", key);\n"
     "\t\tec = p->control_observe(p->control_context, cmd, &p->engine.last_reply, ec);\n"
     "\t}\n\treturn ec;\n"),
    ('cr250_imp_inventory(&p->engine, "UserCalibMode", &available, READ_TIMEOUT)',
     'inventory_value(p, "UserCalibMode", &available)'),
    ('cr250_imp_inventory(&p->engine, key, &profiles, READ_TIMEOUT)',
     'inventory_value(p, key, &profiles)'),
    ("static inst_code set_disptype(inst *pp, int index) {\n"
     "\tcr250 *p = (cr250 *)pp;\n\tinst_code rv;\n\tamutex_lock(p->lock);\n",
     "static inst_code select_index(cr250 *p, int index) {\n\tinst_code rv;\n"),
    ("\tamutex_unlock(p->lock);\n\treturn rv;\n}\n\nstatic int profile_id",
     "\treturn rv;\n}\n\n"
     "static inst_code set_disptype(inst *pp, int index) {\n"
     "\tcr250 *p = (cr250 *)pp;\n\tinst_code rv;\n"
     "\tamutex_lock(p->lock);\n\trv = select_index(p, index);\n"
     "\tamutex_unlock(p->lock);\n\treturn rv;\n}\n\n"
     "/* CM: reuse the actual Argyll selector owner without opening a lock gap. */\n"
     "inst_code cm_cr250_select_profile_locked(cr250 *p, int mode, long long id, "
     "const char *name) {\n"
     "\tint i, selected = -1;\n\tinst_code rv = display_list(p);\n"
     "\tif (rv != inst_ok) return rv;\n"
     "\tfor (i = 0; i < p->ndtlist; i++) {\n"
     "\t\tint ix = p->dtlist[i].ix;\n"
     "\t\tif (ix < 0 || ix >= CR250_MAX_DISPTYPES) return inst_internal_error;\n"
     "\t\tif (p->correction_modes[ix] != mode) continue;\n"
     '\t\tif (mode && (p->corrections[ix].id != id || !name '
     '|| strcmp(p->corrections[ix].name, name))) continue;\n'
     "\t\tif (selected >= 0) return map(CR250_SETUP);\n\t\tselected = i;\n\t}\n"
     "\treturn selected < 0 ? map(CR250_SETUP) : select_index(p, selected);\n}\n\n"
     "static int profile_id"),
    ("\t/* Read the setup first. Required capabilities, planned settings and the\n",
     "\tif (p->control_blocked)\n"
     '\t\treturn map(refuse(p, CR250_SETUP, "A failed settings '
     'application requires explicit recovery before measurement"));\n\n'
     "\t/* Read the setup first. Required capabilities, planned settings and the\n"),
    ("\tif (strcmp(key, \"ExposureMode\") == 0)\n"
     "\t\treturn IMODETST(p->mode, inst_mode_emis_nonadaptive) ? \"Fixed\" : \"Auto\";\n",
     "\tif (strcmp(key, \"ExposureMode\") == 0) {\n"
     '\t\tif (p->control_exposure_mode) return '
     'p->control_exposure_mode == 2 ? "Fixed" : "Auto";\n'
     "\t\treturn IMODETST(p->mode, inst_mode_emis_nonadaptive) ? \"Fixed\" : \"Auto\";\n"
     "\t}\n"),
    ("\tif (strcmp(key, \"SyncMode\") == 0) {\n",
     "\tif (strcmp(key, \"SyncMode\") == 0) {\n"
     "\t\tif (p->control_sync_name[0]) return p->control_sync_name; /* CM */\n"),
    ("\tp->mode = m;\n",
     "\tp->mode = m;\n"
     '\tp->control_exposure_mode = 0; '
     'p->control_sync_name[0] = 0; /* CM: latest mode request wins */\n'),
    ("\t\tif (!profile_id(value, &id)) return CR250_DATA_PARSE_ERROR;\n",
     "\t\tif (!profile_id(value, &id)) return CR250_DATA_PARSE_ERROR;\n"
     "\t\tstrcpy(c->correction_before, value); /* CM: active RS profile, not a guessed ID */\n"),
    ("\tif (id != p->correction.id)\n",
     "\tif (!strcmp(prefix, \"RS\")) { /* CM: retain even a disagreeing readback */\n"
     "\t\tint i;\n"
     "\t\tstrcpy(p->capture.correction_after, value);\n"
     "\t\tfor (i = 0; i < p->capture.nwrites; i++)\n"
     '\t\t\tif (!strcmp(p->capture.write[i].key, '
     'p->correction_mode == 1 ? "Matrix" : "Match"))\n'
     "\t\t\t\tstrcpy(p->capture.write[i].after, value);\n"
     "\t}\n"
     "\tif (id != p->correction.id)\n"),
    ("\tif (correction_argument[0]\n"
     '\t && (ec = write_value(p, p->correction_mode == 1 ? '
     '"Matrix" : "Match", correction_argument)) != CR250_OK)\n'
     "\t\treturn map(ec);\n",
     '\tif (correction_argument[0]) { /* CM: retain '
     'the profile write independently of the mode */\n'
     "\t\tif (!record_write(p, p->correction_mode == 1 ? \"Matrix\" : \"Match\",\n"
     '\t\t                  c->correction_before, '
     'correction_argument)) return map(CR250_INTERNAL_ERROR);\n'
     '\t\tif ((ec = write_value(p, p->correction_mode == 1 ? '
     '"Matrix" : "Match", correction_argument)) != CR250_OK)\n'
     "\t\t\treturn map(ec);\n"
     "\t}\n"),
    ("\tif ((ec = correction_readback(p, \"RS\")) != CR250_OK) return map(ec);\n",
     "\tif ((ec = correction_readback(p, \"RS\")) != CR250_OK) return map(ec);\n"
     "\tfor (i = 0; i < c->nwrites; i++)\n"
     "\t\tif (!strcmp(c->write[i].key, \"Matrix\") || !strcmp(c->write[i].key, \"Match\")) {\n"
     "\t\t\tstrcpy(c->write[i].after, c->correction_after); c->write[i].confirmed = 1;\n"
     "\t\t}\n"),
    ("\t\tp->refresh_hz = 0.0;\n\t\trv = inst_ok;\n",
     "\t\tp->refresh_hz = 0.0; p->control_sync_name[0] = 0; /* CM */\n\t\trv = inst_ok;\n"),
    ("\t\telse p->refresh_hz = rate;\n",
     '\t\telse { p->refresh_hz = rate; '
     'p->control_sync_name[0] = 0; } /* CM: latest refresh request wins */\n'),
    ("\tauto_limit = !fixed && cr250_firmware_at_least(&p->engine.id, 1, 26);\n",
     "\t/* CM: the documented CR-100 manual recipe is Fixed Range D. Recheck\n"
     "\t * current native state before any setting write or M, including after an\n"
     "\t * operator or another client changed the setup. */\n"
     "\tif (p->dtype == instCR100 && fixed && (!range_mode || strcmp(range_mode, \"Fixed\")\n"
     "\t || strcmp(c->range.name, \"D\")))\n"
     "\t\treturn map(refuse(p, CR250_SETUP, \"CR-100 Fixed exposure requires Fixed Range D\"));\n"
     "\tauto_limit = !fixed && cr250_firmware_at_least(&p->engine.id, 1, 26);\n"),
)
_IMPL_EDITS = (
    ('\t\t"UserCalibMode", "CMF", "Matrix", "Match", NULL };\n',
     '\t\t"UserCalibMode", "CMF", "Matrix", "Match",\n'
     '\t\t/* CM: closed operator control keys, validated by cr250_control.c. */\n'
     '\t\t"Mode", "Accessory", "Filter1", "Filter2", "Filter3", "RangeMode",\n'
     '\t\t"Range", "Exposure", "Speed", "MaxAutoExposure", NULL };\n'),
)

# Raw transport bytes have one owner in the contribution engine. The application
# copies each completed or failed exchange once, before the next command resets
# that buffer. Parsed statuses remain separate facts.
_TRACE_TYPES = r'''
#define CM_CR250_RAW_EXCHANGES 256
#define CM_CR250_RAW_BYTES (4 * 1024 * 1024)
typedef struct {
    char command[CR250_MAX_COMMAND + 1];
    int complete, error;
    cr250_raw raw;
} cm_cr250_exchange;

'''
_TRACE_FUNCTIONS = r'''
/* CM: actual-size snapshots, bounded across the complete operation. */
void cm_cr250_trace_reset(cr250 *p) {
    int i;
    for (i = 0; i < p->raw_count; i++) cr250_raw_dispose(&p->raw_exchanges[i].raw);
    memset(p->raw_exchanges, 0, sizeof(p->raw_exchanges));
    p->raw_count = p->raw_bytes = p->raw_overflow = p->raw_tracing = 0;
}
void cm_cr250_trace_begin(cr250 *p) {
    cm_cr250_trace_reset(p);
    p->raw_tracing = 1;
}
static int cm_cr250_trace_observe(void *context, const char *command,
                                const cr250_raw *raw, int error, int complete) {
    cr250 *p = (cr250 *)context;
    cm_cr250_exchange *e;
    if (!p->raw_tracing) return CR250_OK;
    if (p->raw_count >= CM_CR250_RAW_EXCHANGES
     || raw->length > CM_CR250_RAW_BYTES - p->raw_bytes) {
        p->raw_overflow = 1;
        return CR250_REPLY_TOO_LONG;
    }
    e = &p->raw_exchanges[p->raw_count];
    if (cr250_raw_copy(&e->raw, raw) != CR250_OK) {
        p->raw_overflow = 1;
        return CR250_INTERNAL_ERROR;
    }
    snprintf(e->command, sizeof(e->command), "%s", command);
    e->complete = complete; e->error = error;
    p->raw_count++; p->raw_bytes += raw->length;
    return CR250_OK;
}

'''
_HEADER_EDITS += (
    ("typedef struct {\n\tint count;                        /* CM: replies retained this read */",
     _TRACE_TYPES + "typedef struct {\n"
     "\tint count;                        /* CM: replies retained this read */"),
    ("\tint capturing;", "\tint raw_count, raw_bytes, raw_overflow, raw_tracing; /* CM */\n"
     "\tcm_cr250_exchange raw_exchanges[CM_CR250_RAW_EXCHANGES];\n\tint capturing;"),
    ("\tint valid;                        /* CM: all checks passed, reading published */\n",
     "\tint valid;                        /* CM: all checks passed, reading published */\n"
     "\tcr250_identity identity;          /* CM: "
     "verified capture-start identity, not current link state */\n"
     "\tint identity_verified;\n"),
    ("instType cr250_model_reply(const char *line);\n",
     "instType cr250_model_reply(const char *line);\n"
     "void cm_cr250_trace_reset(cr250 *); /* CM: frees snapshots without I/O */\n"
     "void cm_cr250_trace_begin(cr250 *);\n"),
)
_SOURCE_EDITS += (
    ("static int read_value(cr250 *p, const char *cmd, char value[CR250_MAX_VALUE]) {\n",
     _TRACE_FUNCTIONS + "static int read_value(cr250 *p, const char *cmd, "
     "char value[CR250_MAX_VALUE]) {\n"),
    ("\tcr250_imp_init(&p->engine, icom, p->log);\n",
     "\tcr250_imp_init(&p->engine, icom, p->log);\n"
     "\tp->engine.host_observe = cm_cr250_trace_observe; p->engine.host_context = p;\n"),
    ("static void destroy(inst *pp) {\n\tcr250 *p = (cr250 *)pp;\n",
     "static void destroy(inst *pp) {\n\tcr250 *p = (cr250 *)pp;\n\tcm_cr250_trace_reset(p);\n"),
    ("\t(void)name;\n\tamutex_lock(p->lock);\n\tmemset(&p->capture, 0, sizeof(p->capture));\n",
     "\t(void)name;\n\tamutex_lock(p->lock);\n\tcm_cr250_trace_reset(p);\n"
     "\tmemset(&p->capture, 0, sizeof(p->capture));\n"),
    ("\t * Reset under this transaction's lock, including when readiness fails. */\n"
     "\tmemset(&p->capture, 0, sizeof(p->capture));\n",
     "\t * Reset under this transaction's lock, including when readiness fails. */\n"
     "\tcm_cr250_trace_begin(p);\n\tmemset(&p->capture, 0, sizeof(p->capture));\n"),
    ("\t\t\tmemcpy(p->presence, prior_presence, sizeof(prior_presence));\n"
     "\t}\n\tamutex_unlock(p->lock);\n",
     "\t\t\tmemcpy(p->presence, prior_presence, sizeof(prior_presence));\n\t}\n"
     "\tp->raw_tracing = 0;\n\tamutex_unlock(p->lock);\n"),
    ("\t\tp->capturing = 1;\n",
     "\t\tp->capturing = 1;\n"
     "\t\tp->capture.identity = p->engine.id;\n"
     "\t\tp->capture.identity_verified = p->engine.identified;\n"),
)
_IMP_HEADER_EDITS = (
    ("\tcr250_raw last_raw;",
     "\tint (*host_observe)(void *, const char *, const cr250_raw *, int, int);\n"
     "\tvoid *host_context;\n\tint host_pending;\n\tcr250_raw last_raw;"),
)
_IMPL_EDITS += (
    ("\tmemset(&p->last_reply, 0, sizeof(p->last_reply));\n\tif (p->icom == NULL",
     "\tmemset(&p->last_reply, 0, sizeof(p->last_reply));\n"
     "\tp->host_pending = 1;\n\tif (p->icom == NULL"),
)

_ENGINE_WRAPPERS = r'''
/* CM: observe one whole exchange, including failures before a status or midway
 * through a counted body. A refused command that never dispatches adds no entry. */
static void cm_cr250_exchange_begin(cr250_imp *p) {
    if (p) { p->host_pending = 0; p->last_raw.length = 0; }
}
static int cm_cr250_exchange_end(cr250_imp *p, const char *command, int code) {
    int observed;
    if (!p || !p->host_pending) return code;
    p->host_pending = 0;
    if (!p->host_observe) return code;
    observed = p->host_observe(p->host_context, command, &p->last_raw, code, !p->uncertain);
    return observed == CR250_OK ? code : observed;
}
int cr250_imp_command(cr250_imp *p, const char *cmd, cr250_reply *r, double timeout_s) {
    int code;
    cm_cr250_exchange_begin(p);
    code = cm_cr250_imp_command(p, cmd, r, timeout_s);
    return cm_cr250_exchange_end(p, cmd, code);
}
int cr250_imp_setting(cr250_imp *p, const char *key, const char *arg,
                      cr250_reply *r, double timeout_s) {
    char cmd[CR250_MAX_COMMAND + 1];
    int code;
    cm_cr250_exchange_begin(p);
    code = cm_cr250_imp_setting(p, key, arg, r, timeout_s);
    snprintf(cmd, sizeof(cmd), "SM %s %s", key ? key : "", arg ? arg : "");
    return cm_cr250_exchange_end(p, cmd, code);
}
int cr250_imp_inventory(cr250_imp *p, const char *key, cr250_inventory *inv, double timeout_s) {
    char cmd[CR250_MAX_COMMAND + 1];
    int code;
    cm_cr250_exchange_begin(p);
    code = cm_cr250_imp_inventory(p, key, inv, timeout_s);
    snprintf(cmd, sizeof(cmd), "RC %s", key ? key : "");
    return cm_cr250_exchange_end(p, cmd, code);
}
int cr250_imp_spectrum(cr250_imp *p, cr250_spectrum *s, cr250_reply *r, double timeout_s) {
    int code;
    cm_cr250_exchange_begin(p);
    code = cm_cr250_imp_spectrum(p, s, r, timeout_s);
    return cm_cr250_exchange_end(p, "RM Spectrum", code);
}
int cr250_imp_measure(cr250_imp *p, cr250_reply *r, double allowed_s, double budget_s) {
    int code;
    cm_cr250_exchange_begin(p);
    code = cm_cr250_imp_measure(p, r, allowed_s, budget_s);
    return cm_cr250_exchange_end(p, "M", code);
}
'''
for _name in ("command", "setting", "inventory", "spectrum", "measure"):
    _IMPL_EDITS += ((f"int cr250_imp_{_name}(cr250_imp *p,",
                    f"static int cm_cr250_imp_{_name}(cr250_imp *p,"),)


def _apply(path: str, text: str, edits: tuple[tuple[str, str], ...]) -> str:
    if _MARKERS[path] in text:
        raise ValueError(f"{path} already carries the host allowance additions")
    for old, new in edits:
        if text.count(old) != 1:
            raise ValueError(f"{path}: anchor missing or ambiguous: {old.splitlines()[0]!r}")
        text = text.replace(old, new)
    return text


def cr250_host_header_patch(text: str) -> str:
    """Declare the retained replies, the allowance fields and the floor."""
    return _apply(HEADER, text, _HEADER_EDITS)


def cr250_host_source_patch(text: str) -> str:
    """Retain the capture's replies and refuse a read the allowance cannot cover."""
    return _apply(SOURCE, text, _SOURCE_EDITS)


def cr250_host_impl_patch(text: str) -> str:
    """Extend the existing engine's closed setter list for typed operator controls."""
    return _apply(IMPL, text, _IMPL_EDITS) + _ENGINE_WRAPPERS


def cr250_host_impl_header_patch(text: str) -> str:
    return _apply(IMP_HEADER, text, _IMP_HEADER_EDITS)


PATCHES = ((HEADER, cr250_host_header_patch), (SOURCE, cr250_host_source_patch),
           (IMPL, cr250_host_impl_patch), (IMP_HEADER, cr250_host_impl_header_patch))
