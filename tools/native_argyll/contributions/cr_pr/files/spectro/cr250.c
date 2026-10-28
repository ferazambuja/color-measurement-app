/*
 * Argyll Color Management System
 *
 * Colorimetry Research CR family driver (CR-250, CR-100 and CR-300 registered)
 *
 * Author: Fernando Voltolini de Azambuja
 * Date:   24/9/2026
 *
 * Copyright 2026 Fernando Voltolini de Azambuja
 * All rights reserved.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 * This material is licenced under the GNU GENERAL PUBLIC LICENSE Version 2 or later :-
 * see the License2.txt file for licencing details.
 *
 * Written from the Colorimetry Research Remote Communication manual,
 * revision 1.36 (2018), sections 2.2, 4.1 to 4.3 and 4.5, and the CR app manual
 * 1.5.10 where the remote manual is silent. Model-specific facts live in the
 * models[] rows; the rest is protocol.
 */

/*
   The author(s) of this driver are responsible for its operation. Direct
   problems or queries about driving an instrument with it to the author(s)
   of this driver and of Argyll, not to Colorimetry Research or any other
   party.

   Before adding an instrument feature or command, contact Argyll's
   author(s). An incautious command sequence could damage an instrument or
   its calibration, and a manufacturer need not support developers it has
   not qualified. This driver sends RC/RS/RM queries, the measurement command
   M, "RM Spectrum", and "SM" writes of the exposure, sync and computation
   and user-calibration keys cr250.txt lists. It never writes the optics
   declarations, calibration data or a reset.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <math.h>
#include <float.h>
#include <errno.h>
#include <limits.h>
#include "cr250.h"

#define READ_TIMEOUT 3.0
/* "RM Spectrum" header and body, including its 200 ms pacing note (host policy) */
#define SPECTRUM_TIMEOUT 5.0

/* Spectrum self-check (host policy, to be refined at the bench). The bounds
 * refuse gross disagreement -- a wrong power of ten, per-bin rather than
 * per-nm values, a misread wavelength axis -- not instrument noise. Below the
 * minimum light level a comparison cannot tell those apart and is skipped. */
#define UNIT_RATIO_LOW   0.9
#define UNIT_RATIO_HIGH  1.1
#define UNIT_AXIS_NM     5.0
#define UNIT_CHROMA      0.05
#define UNIT_MIN_POWER_W 3e-4		/* W/(sr.m^2): about 0.2 cd/m^2 at 555 nm */
#define UNIT_MIN_Y       0.2		/* cd/m^2 */
#define PLANCK_HC        (6.62607015e-34 * 299792458.0)	/* J.m, exact SI constants */

/* How closely a capture must repeat a numeric setting (host policy). */
#define EXPOSURE_TOL_MS  0.05
#define EXPOSURE_TOL_REL 0.001
#define SYNC_TOL_HZ      0.011		/* RS/RM SyncFreq print two decimals (4.3.3.10) */

static int triplet(const char *s, double v[3]) {
	int i;
	for (i = 0; i < 3; i++) {
		if (!cr250_parse_number(&s, &v[i])) return 0;
		if (i < 2 && *s++ != ',') return 0;
	}
	return *s == 0;
}

static int milliseconds(const char *s, double *v) {
	return cr250_parse_number(&s, v) && strcmp(s, " msec") == 0 && *v > 0;
}

static int hertz(const char *s, double *v) {
	return cr250_parse_number(&s, v) && strcmp(s, " Hz") == 0 && *v >= 0;
}

/* A positive whole number: RS ExposureX (4.3.3.11), RC Min/MaxExposureX.
 * The host option's zero sentinel is handled without parsing a native value. */
static int whole(const char *s, int *v) {
	long long n;
	if (!cr250_parse_integer(s, &n) || n < 1 || n > INT_MAX) return 0;
	*v = (int)n;
	return 1;
}

static inst_code map(int ec) {
	switch (ec) {
		case CR250_SETUP: return inst_wrong_setup | ec;
		case CR250_CAPTURE_MISMATCH: return inst_misread | ec;
		case CR250_SPECTRUM_MISMATCH: return inst_misread | ec;
		case CR250_SPECTRUM_UNVERIFIED: return inst_misread | ec;
		case CR250_CAPTURE_WARNING: return inst_warning | ec;
		case CR250_FIRMWARE: return inst_unsupported | ec;
		default: return cr250_interp_code(ec);
	}
}

/* Keep which check refused, with both values, for interp_error. A reply's
 * value can outrun the buffer. On MSVC and NT, numsup.h makes vsnprintf and
 * snprintf _vsnprintf and _snprintf, which leave a full buffer unterminated,
 * so every text cut to size here is terminated explicitly. */
static int refuse(cr250 *p, int ec, const char *fmt, ...) {
	va_list args;
	va_start(args, fmt);
	vsnprintf(p->capture.problem, sizeof(p->capture.problem), fmt, args);
	va_end(args);
	p->capture.problem[sizeof(p->capture.problem) - 1] = '\000';
	return ec;
}

/* One read. Its complete reply, even a native refusal, stays in
 * engine.last_reply, which error_text reports. */
static int read_value(cr250 *p, const char *cmd, char value[CR250_MAX_VALUE]) {
	cr250_reply r;
	int ec = cr250_imp_command(&p->engine, cmd, &r, READ_TIMEOUT);
	if (ec == CR250_OK) strcpy(value, r.value);
	return ec;
}

/* The same required capability validation serves options and captures. A
 * complete but unusable value refuses the operation without poisoning framing. */
static int multiplier_limits(cr250 *p, int *lo, int *hi) {
	char value[CR250_MAX_VALUE];
	int ec;
	if ((ec = read_value(p, "RC MinExposureX", value)) != CR250_OK) return ec;
	if (!whole(value, lo)) return CR250_DATA_PARSE_ERROR;
	if ((ec = read_value(p, "RC MaxExposureX", value)) != CR250_OK) return ec;
	if (!whole(value, hi)) return CR250_DATA_PARSE_ERROR;
	return *lo <= *hi ? CR250_OK
	    : refuse(p, CR250_SETUP, "ExposureX limits %d to %d are reversed", *lo, *hi);
}

static int sync_limits(cr250 *p, double *lo, double *hi) {
	char value[CR250_MAX_VALUE];
	int ec;
	if ((ec = read_value(p, "RC MinSyncFreq", value)) != CR250_OK) return ec;
	if (!hertz(value, lo) || !(*lo > 0.0)) return CR250_DATA_PARSE_ERROR;
	if ((ec = read_value(p, "RC MaxSyncFreq", value)) != CR250_OK) return ec;
	if (!hertz(value, hi) || !(*hi > 0.0)) return CR250_DATA_PARSE_ERROR;
	return *lo <= *hi ? CR250_OK
	    : refuse(p, CR250_SETUP, "SyncFreq limits %.2f to %.2f Hz are reversed", *lo, *hi);
}

/* Validate the value SM will actually send, including its printed precision. */
static int refresh_value(double rate, char arg[32], double *value) {
	const char *s = arg;
	snprintf(arg, 32, "%.2f", rate);
	return cr250_parse_number(&s, value) && *s == 0 && *value > 0.0;
}

/* One "SM" write. */
static int write_value(cr250 *p, const char *key, const char *arg) {
	cr250_reply r;
	return cr250_imp_setting(&p->engine, key, arg, &r, READ_TIMEOUT);
}

/* A command this instrument does not implement: "ER:-500" (Invalid
 * command, manual 4.5) or the manual's "NA" value (4.3.1.3). */
static int not_available(int ec, const char *value) {
	return ec == CR250_NATIVE_ERROR_BASE + 500 || (ec == CR250_OK && strcmp(value, "NA") == 0);
}

/* CR-250 row. Manual 1.36 is shared by the family, so the row lists what the
 * CR-250 exposes to this contract. CMF and Speed are spectroradiometer-only
 * settings (4.2.1.19, 4.2.1.22); the aperture has its own inventory (4.3.2.6).
 * The CR app manual's CR-250 setup (1.5.10, 5.10) shows no Mode, Range or
 * Sync, so those are optional here: a reply that the command does not exist
 * records them as not applicable. Floor 1.26: SM/RS CMF and RM
 * MaxAutoExposure (4.3.1.10) appear at 1.26, and 4.2.1.9 gives RC MaxExposure
 * its Auto-ceiling meaning from there. RM Radiometric type 0 (4.3.1.36)
 * confirms the radiance quantity the Radiance accessory record promises.
 *
 * CR-100 row: a colorimeter, so none of the thirteen commands the manual
 * restricts to InstrumentType 2 (CMF, Speed, the 10-degree reads) and no
 * spectrum. The manual never says which type a CR-100 reports, and the CR
 * app manual heads it "Tristimulus Colorimeter/Photometer" (5.8), so 0 and 1
 * are accepted and the value is recorded; 2 is refused. That setup lists no
 * aperture, and manual exposure only in Range D, so Fixed exposure is not
 * offered. Floor 1.17 for RC InstrumentType (4.3.2.3); SM/RS Mode and
 * UserCalibMode need 1.16. RM MaxAutoExposure is read only from 1.26. */
static const cr250_setting cr250_setup[] = {
	{ "CMF", "0", 0, NULL, 0, 1 }, { "UserCalibMode", NULL, 1, NULL, 0, 1 },
	{ "Mode", "Colorimeter", 0, NULL, 1, 0 }, { "ExposureMode", NULL, 1, NULL, 0, 1 },
	{ "ExposureX", NULL, 0, NULL, 0, 1 }, { "RangeMode", NULL, 1, NULL, 1, 0 },
	{ "SyncMode", NULL, 1, NULL, 1, 1 }, { "Speed", NULL, 1, NULL, 0, 0 },
	{ "Filter", NULL, 0, NULL, 0, 0 }, { "Accessory", NULL, 1, "Radiance", 0, 0 },
	{ "Aperture", NULL, 1, NULL, 0, 0 },
	{ NULL, NULL, 0, NULL, 0, 0 }
};
static const cr250_setting cr100_setup[] = {
	{ "UserCalibMode", NULL, 1, NULL, 0, 1 }, { "Mode", "Colorimeter", 0, NULL, 0, 0 },
	{ "ExposureMode", NULL, 1, NULL, 0, 1 }, { "ExposureX", NULL, 0, NULL, 0, 1 },
	{ "RangeMode", NULL, 1, NULL, 0, 0 }, { "SyncMode", NULL, 1, NULL, 0, 1 },
	{ "Filter", NULL, 0, NULL, 0, 0 }, { "Accessory", NULL, 1, "Radiance", 0, 0 },
	{ NULL, NULL, 0, NULL, 0, 0 }
};
static const cr250_model models[] = {
	{ instCR250, "CR-250", 1 << 2, 1, 26, cr250_setup, 0, 1, 1, 0 },
	{ instCR100, "CR-100", (1 << 0) | (1 << 1), 1, 17, cr100_setup, -1, 0, 0, 1 },
	/* CR-300 brochure (2023), pp. 1/4: spectroradiometer, fixed aperture,
	 * custom sync and exposure. Remote 1.36 section 4.3.2.3 defines type 2.
	 * Require that reported type; 1.26 is our command floor, not a claimed
	 * manufacturer firmware minimum. The reply supplies the spectral grid,
	 * never the brochure's optical bandwidth or detector pitch. */
	{ instCR300, "CR-300", 1 << 2, 1, 26, cr250_setup, 0, 1, 1, 0 }
};
#define NMODELS (sizeof(models)/sizeof(models[0]))

/* fast_ser_dev_type (inst.c) sends one "RC Model" (4.3.2.2) and passes the
 * reply line here. Only status 0 and a model with a row are accepted, so a
 * variant such as CR-250-RH, a warning or an error reply identifies nothing. */
instType cr250_model_reply(const char *line) {
	static const char prefix[] = "OK:0:RC Model:";
	size_t i, n;
	if (line == NULL || strncmp(line, prefix, sizeof(prefix) - 1) != 0)
		return instUnknown;
	line += sizeof(prefix) - 1;
	for (i = 0; i < NMODELS; i++) {
		n = strlen(models[i].model);
		if (strncmp(line, models[i].model, n) == 0 && strcmp(line + n, "\r\n") == 0)
			return models[i].dtype;
	}
	return instUnknown;
}

/* The engine accepts the same echo before any status line (read_status). */
int cr250_model_echo(const char *line) {
	return line != NULL && cr250_echo_line(line, "RC Model");
}

/* The owner supplies a NULL-terminated table. Permit at most CR250_MAX_SETUP
 * settings, plus its terminator; refuse longer policies before any request. */
static int setting_count(const cr250_model *model) {
	int n;
	if (!model || !model->setup) return -1;
	for (n = 0; n <= CR250_MAX_SETUP; n++)
		if (model->setup[n].key == NULL) return n;
	return -1;
}

static int setting_index(const cr250_model *model, const char *key) {
	int i, n = setting_count(model);
	for (i = 0; i < n; i++)
		if (strcmp(model->setup[i].key, key) == 0) return i;
	return -1;
}

/* nz once this connection has found the instrument without SyncMode (optional
 * on the CR-250 row): it cannot sync to a display's refresh. Before its first
 * read the driver cannot know, and a refresh request is refused at the read. */
static int no_sync(cr250 *p) {
	int i = setting_index(p->model, "SyncMode");
	return i >= 0 && p->presence[i] < 0;
}

/* This read's accepted RS value for key, or NULL when the row has no such
 * setting or this instrument does not implement it. */
static const char *setting(cr250 *p, const char *key) {
	int i = setting_index(p->model, key);
	return i < 0 || p->presence[i] < 0 ? NULL : p->capture.value[i];
}

static int unique_entry(cr250 *p, const char *key, const char *name, const char *type, cr250_record *record) {
	cr250_inventory inv = {0};
	int ec, i, found = -1;
	if ((ec = cr250_imp_inventory(&p->engine, key, &inv, READ_TIMEOUT)) != CR250_OK)
		goto done;
	for (i = 0; i < inv.count; i++)
		if (strcmp(inv.rec[i].name, name) == 0) {
			if (found >= 0) { ec = CR250_SETUP; goto done; }
			found = i;
		}
	if (found < 0 || (type != NULL && strcmp(inv.rec[found].type, type) != 0)) {
		ec = CR250_SETUP; goto done;
	}
	*record = inv.rec[found];
done:
	cr250_inventory_dispose(&inv);
	return ec;
}

static int setting_ok(cr250 *p, int i, const char *value) {
	const cr250_setting *s = &p->model->setup[i];
	if (strcmp(s->key, "Filter") == 0) {
		cr250_capture *c = &p->capture;
		cr250_inventory inv = {0};
		const char *start = value, *end;
		int slot, ec = CR250_OK, j, found, active = 0, inventoried = 0;
		c->active_filters[0] = 0;
		for (slot = 0; slot < 3; slot++) {
			char name[CR250_MAX_FIELD];
			size_t n;
			end = strchr(start, ',');
			n = end ? (size_t)(end - start) : strlen(start);
			if (n == 0 || n >= sizeof(name)) { ec = CR250_SETUP; goto filter_done; }
			memcpy(name, start, n); name[n] = 0;
			c->filters[slot].id = -1;
			if (strcmp(name, "None")) {
				if (!inventoried) {
					if ((ec = cr250_imp_inventory(&p->engine, "Filter", &inv, READ_TIMEOUT)) != CR250_OK) goto filter_done;
					inventoried = 1;
				}
				found = -1;
				for (j = 0; j < inv.count; j++) if (!strcmp(inv.rec[j].name, name)) {
					if (found >= 0) { ec = CR250_SETUP; goto filter_done; }
					found = j;
				}
				if (found < 0 || strcmp(inv.rec[found].type, "Radiance")) { ec = CR250_SETUP; goto filter_done; }
				/* The instrument refuses duplicate filter selection (Remote
				 * 1.36, 4.5, error -505). Check every declared slot before SM/M. */
				for (j = 0; j < slot; j++)
					if (c->filters[j].id == inv.rec[found].id) { ec = CR250_SETUP; goto filter_done; }
				c->filters[slot] = inv.rec[found];
				/* At most three bounded names: they fit CR250_MAX_VALUE. */
				if (active++) strcat(c->active_filters, ",");
				strcat(c->active_filters, name);
			}
			/* A single None is also a complete no-filter declaration. */
			if (end == NULL) {
				if (slot != 2 && !(slot == 0 && !strcmp(name, "None"))) { ec = CR250_SETUP; goto filter_done; }
				for (slot++; slot < 3; slot++) c->filters[slot].id = -1;
				break;
			}
			start = end + 1;
			if (slot == 2) { ec = CR250_SETUP; goto filter_done; }
		}
		if (!active) strcpy(c->active_filters, "None");
filter_done:
		cr250_inventory_dispose(&inv);
		return ec;
	}
	if (s->required == NULL || strcmp(value, s->required) == 0) return CR250_OK;
	if (strcmp(s->key, "Mode") == 0) {
		/* A model with a single mode names it (RC Mode, 4.3.2.7): accept that. */
		cr250_inventory inv = {0};
		int ec = cr250_imp_inventory(&p->engine, "Mode", &inv, READ_TIMEOUT);
		if (ec == CR250_OK && !(inv.count == 1 && strcmp(inv.rec[0].name, value) == 0)) ec = CR250_SETUP;
		cr250_inventory_dispose(&inv);
		return ec;
	}
	return CR250_SETUP;
}

/* The last capture must show the same accepted setting. */
static int capture_matches(cr250 *p, const cr250_setting *s, const char *before, const char *value) {
	if (strcmp(s->key, "Filter") == 0) {
		cr250_capture *c = &p->capture;
		const char *start = value, *end;
		int matched[3] = {0}, i, n = 0, found;
		strcpy(c->used_filters, value);
		if (!strcmp(c->active_filters, "None")) return !strcmp(value, "None");
		/* RM Filter reports used names, not slots or their ordering
		 * (Remote 4.3.1.5). Match the active declarations bijectively. */
		for (;;) {
			size_t len;
			end = strchr(start, ',');
			len = end ? (size_t)(end - start) : strlen(start);
			if (!len || ++n > 3) return 0;
			found = -1;
			for (i = 0; i < 3; i++)
				if (!matched[i] && c->filters[i].id >= 0 && strlen(c->filters[i].name) == len
				 && !strncmp(start, c->filters[i].name, len)) { found = i; break; }
			if (found < 0) return 0;
			matched[found] = 1;
			if (!end) break;
			start = end + 1;
		}
		for (i = 0; i < 3; i++) if (c->filters[i].id >= 0 && !matched[i]) return 0;
		return 1;
	}
	return strcmp(value, before) == 0;
}

/* Whether RS for setup[i] answered with a value. Only an optional setting may
 * be missing. read_sample restores prior presence if the read fails, so a
 * missing RS alone cannot withdraw a capability for the connection. */
static int presence(cr250 *p, int i, const char *cmd, int ec, const char *value) {
	const cr250_setting *s = &p->model->setup[i];
	int missing = not_available(ec, value), now = missing ? -1 : 1;
	if (!missing && ec != CR250_OK) return ec;
	if (missing && !s->optional)
		return refuse(p, ec != CR250_OK ? ec : CR250_SETUP,
		              "%s: this %s does not report %s", cmd, p->model->model, s->key);
	if (p->presence[i] != 0 && p->presence[i] != now)
		return refuse(p, CR250_SETUP, "%s: %s earlier on this connection, %s now", cmd,
		              p->presence[i] < 0 ? "not available" : "available",
		              now < 0 ? "not available" : "available");
	p->presence[i] = now;
	return CR250_OK;
}

/* The value this read needs for a key the driver sets, in its RS form, or
 * NULL to accept the instrument's own. ExposureMode follows the Argyll
 * route; ExposureX and sync follow the host's options; the computation
 * CMF is fixed by the contract; user calibration follows the selected
 * implicit display type. Called with the
 * lock held. */
static const char *target(cr250 *p, const char *key, char buf[32]) {
	if (strcmp(key, "UserCalibMode") == 0)
		return p->correction_mode == 1 ? "Matrix" : p->correction_mode == 2 ? "Match" : "None";
	if (strcmp(key, "CMF") == 0) return "0";
	if (strcmp(key, "ExposureMode") == 0)
		return IMODETST(p->mode, inst_mode_emis_nonadaptive) ? "Fixed" : "Auto";
	if (strcmp(key, "ExposureX") == 0) {
		if (p->averages <= 0) return NULL;
		snprintf(buf, 32, "%d", p->averages);
		return buf;
	}
	if (strcmp(key, "SyncMode") == 0) {
		if (IMODETST(p->mode, inst_mode_emis_norefresh_ovd)) return "None";	/* Test this first */
		if (p->refresh_hz > 0.0) return "Manual";
		if (IMODETST(p->mode, inst_mode_emis_refresh_ovd)) return "Auto";
	}
	return NULL;
}

/* SM takes an RC list ID for these keys and a number for CMF, ExposureX and
 * SyncFreq, while RS answers with the name (4.2.1, 4.3.3). */
static int takes_id(const char *key) {
	return strcmp(key, "CMF") != 0 && strcmp(key, "ExposureX") != 0 && strcmp(key, "SyncFreq") != 0;
}

/* Apply a preflighted setup argument and read it back into value. */
static int set_value(cr250 *p, const cr250_setting *s, char *value, const char *want, const char *arg) {
	char cmd[64];
	int ec;
	if ((ec = write_value(p, s->key, arg)) != CR250_OK) return ec;
	snprintf(cmd, sizeof(cmd), "RS %s", s->key);
	if ((ec = read_value(p, cmd, value)) != CR250_OK) return ec;
	if (strcmp(value, want) != 0)
		return refuse(p, CR250_SETUP, "SM %s %s left RS %s at %s, not %s", s->key, arg, s->key, value, want);
	return CR250_OK;
}

static const char *nearest_factor(double r) {
	static const struct { double f; const char *name; } factors[] = {
		{ 1.0, "1" }, { 1000.0, "1000" }, { 0.001, "1/1000" }, { 2.0, "2" }, { 0.5, "1/2" }
	};
	size_t i, best = 0;
	if (!(r > 0.0)) return "none: not positive";
	for (i = 1; i < sizeof(factors)/sizeof(factors[0]); i++)
		if (fabs(log10(r / factors[i].f)) < fabs(log10(r / factors[best].f))) best = i;
	return factors[best].name;
}

/* spectrum/native into *r, nz, when native is positive and the quotient
 * finite. Otherwise no ratio exists and *r is left alone. */
static int unit_ratio(double spectrum, double native, double *r) {
	double q;
	if (!(native > 0.0)) return 0;
	q = spectrum / native;
	if (!cr250_finite(q)) return 0;
	*r = q;
	return 1;
}

/* Append to the refusal text, which is cut at its size (see refuse()). */
static void problem_add(cr250 *p, const char *fmt, ...) {
	size_t n = strlen(p->capture.problem);
	va_list args;
	va_start(args, fmt);
	vsnprintf(p->capture.problem + n, sizeof(p->capture.problem) - n, fmt, args);
	va_end(args);
	p->capture.problem[sizeof(p->capture.problem) - 1] = '\000';
}

/* Name each comparison made, with its value or "no value", and the factor
 * nearest the power (else luminance) ratio. */
static int unit_refusal(cr250 *p) {
	const cr250_unit_check *u = &p->capture.unit;
	const int d = u->discriminating, v = u->computed;
	const char *sep = " ";
	p->capture.problem[0] = '\0';
	problem_add(p, "spectrum/instrument");
	if (d & CR250_UNIT_POWER) {
		if (v & CR250_UNIT_POWER) problem_add(p, "%spower %.4g", sep, u->power_ratio);
		else problem_add(p, "%spower no value", sep);
		sep = ", ";
	}
	if (d & CR250_UNIT_Y) {
		if (v & CR250_UNIT_Y) problem_add(p, "%sluminance %.4g", sep, u->y_ratio);
		else problem_add(p, "%sluminance no value", sep);
		sep = ", ";
	}
	if (v & (CR250_UNIT_POWER | CR250_UNIT_Y))
		problem_add(p, " (nearest factor %s)",
		            nearest_factor(v & CR250_UNIT_POWER ? u->power_ratio : u->y_ratio));
	if (d & CR250_UNIT_CHROMA) {
		if (v & CR250_UNIT_CHROMA) problem_add(p, "%schromaticity error %.3g", sep, u->chroma_error);
		else problem_add(p, "%schromaticity error no value", sep);
		sep = ", ";
	}
	if (d & CR250_UNIT_AXIS) {
		if ((v & CR250_UNIT_AXIS) && (v & CR250_UNIT_SPECTRUM_MEAN))
			problem_add(p, "%smean wavelength %.1f against %.1f nm", sep, u->spectrum_mean_nm, u->mean_nm);
		else problem_add(p, "%smean wavelength no value", sep);
	}
	return CR250_SPECTRUM_MISMATCH;
}

/* Compare the spectrum with the instrument's own totals for the same capture:
 * its summed radiant power and power-weighted mean wavelength against RM
 * Radiometric, then its CIE 1931 2 degree XYZ against the native XYZ (the
 * contract fixes CMF 0, so both use that observer). The instrument documents
 * no spectral unit; this is what backs publishing it as W/(sr.m^2.nm).
 * Power and luminance are compared when either side is above its floor.
 * Axis and chromaticity also need the native side above its floor. */
static int unit_check(cr250 *p) {
	const cr250_spectrum *s = &p->capture.spectrum;
	cr250_unit_check *u = &p->capture.unit;
	const double *native = p->capture.xyz;
	double sum = 0.0, moment = 0.0, power = p->capture.radiometric[1];
	double photons = p->capture.radiometric[2], xyz[3], spectrum_w, mean_nm;
	xspect sp;
	xsp2cie *conv;
	int i, bad = 0;

	memset(&sp, 0, sizeof(sp));
	sp.spec_n = s->count;
	sp.spec_wl_short = s->start_nm;
	sp.spec_wl_long = s->end_nm;
	sp.norm = 1.0;
	for (i = 0; i < s->count; i++) {
		sp.spec[i] = 1000.0 * s->value[i];	/* W to mW */
		sum += s->value[i];
		moment += s->value[i] * (s->start_nm + i * s->step_nm);
	}
	spectrum_w = sum * s->step_nm;
	if ((conv = new_xsp2cie(icxIT_none, 0.0, NULL, icxOT_CIE_1931_2, NULL,
	                        icSigXYZData, icxNoClamp)) == NULL)
		return CR250_INTERNAL_ERROR;
	/* Integrate over the band sent, not the observer's wider range. The
	 * driver's publication grid bounds keep this to at most 900 one-nm steps. */
	conv->set_int_steps(conv, 1.0, s->start_nm, s->end_nm);
	conv->convert(conv, xyz, &sp);
	conv->del(conv);
	/* Values the parser accepted as finite can still overflow once scaled to
	 * mW, summed or integrated. Nothing derived from them is compared or
	 * published; the values as sent stay in capture.spectrum. The mean
	 * wavelength feeds only a comparison that fails without it, so it is
	 * left without a value instead. With every sample finite in mW and
	 * count * step below 1310 nm, power overflow requires large same-sign
	 * samples. No tested spectrum reaches the power check alone; this does
	 * not prove it redundant for every admissible grid. Keep it as an
	 * independent guard on spectrum_w. */
	for (i = 0; i < s->count && cr250_finite(sp.spec[i]); i++)
		;
	if (i < s->count || !cr250_finite(spectrum_w)
	 || !cr250_finite(xyz[0]) || !cr250_finite(xyz[1]) || !cr250_finite(xyz[2])) {
		u->verdict = CR250_UNIT_MISMATCH;
		return refuse(p, CR250_SPECTRUM_MISMATCH,
		              "spectrum is not finite once scaled to mW, summed or integrated");
	}
	u->spectrum_w = spectrum_w;
	for (i = 0; i < 3; i++) u->xyz[i] = xyz[i];
	u->computed |= CR250_UNIT_SPECTRUM;
	/* No mean wavelength for a spectrum summing to zero, or one that overflows */
	if (sum != 0.0 && cr250_finite(mean_nm = moment / sum)) {
		u->spectrum_mean_nm = mean_nm;
		u->computed |= CR250_UNIT_SPECTRUM_MEAN;
	}

	if (p->model->radiometric_type >= 0
	 && (power >= UNIT_MIN_POWER_W || spectrum_w >= UNIT_MIN_POWER_W)) {
		u->discriminating |= CR250_UNIT_POWER;
		if (unit_ratio(spectrum_w, power, &u->power_ratio)) u->computed |= CR250_UNIT_POWER;
		bad |= !((u->computed & CR250_UNIT_POWER)
		         && u->power_ratio >= UNIT_RATIO_LOW && u->power_ratio <= UNIT_RATIO_HIGH);
		/* The native mean wavelength means something only for native light */
		if (power >= UNIT_MIN_POWER_W && photons > 0.0) {
			u->discriminating |= CR250_UNIT_AXIS;
			if (cr250_finite(mean_nm = PLANCK_HC * photons / power * 1e9)) {
				u->mean_nm = mean_nm;
				u->computed |= CR250_UNIT_AXIS;
			}
			bad |= !((u->computed & CR250_UNIT_AXIS) && (u->computed & CR250_UNIT_SPECTRUM_MEAN)
			         && fabs(u->spectrum_mean_nm - u->mean_nm) <= UNIT_AXIS_NM);
		}
	}
	/* Match can alter reported XYZ (CRI App 1.5.10 pp. 79, 83-85).
	 * The manuals do not promise equal corrections for every RM quantity.
	 * Keep independent radiometric/axis checks, but do not test corrected
	 * XYZ against an assumed uncorrected spectrum. Diagnostic integration
	 * remains available; no correction is computed by the host. */
	if (p->capture.correction_mode == 0 && (native[1] >= UNIT_MIN_Y || xyz[1] >= UNIT_MIN_Y)) {
		u->discriminating |= CR250_UNIT_Y;
		if (unit_ratio(xyz[1], native[1], &u->y_ratio)) u->computed |= CR250_UNIT_Y;
		bad |= !((u->computed & CR250_UNIT_Y)
		         && u->y_ratio >= UNIT_RATIO_LOW && u->y_ratio <= UNIT_RATIO_HIGH);
		/* Chromaticity, as a fraction of the native total, needs native light.
		 * Every term is quartered before it is summed or differenced (exact
		 * but for subnormal values, which only round), so no finite values
		 * overflow: an infinite total would make any disagreement look like
		 * none. */
		if (native[1] >= UNIT_MIN_Y) {
			double total = native[0] / 4 + native[1] / 4 + native[2] / 4;
			double dx = fabs(xyz[0] / 4 - native[0] / 4), dz = fabs(xyz[2] / 4 - native[2] / 4);
			u->discriminating |= CR250_UNIT_CHROMA;
			if (total > 0.0 && cr250_finite(dx = (dz > dx ? dz : dx) / total)) {
				u->chroma_error = dx;
				u->computed |= CR250_UNIT_CHROMA;
			}
			bad |= !((u->computed & CR250_UNIT_CHROMA) && u->chroma_error <= UNIT_CHROMA);
		}
	}
	u->verdict = bad ? CR250_UNIT_MISMATCH
	           : u->discriminating ? CR250_UNIT_CONSISTENT : CR250_UNIT_INCONCLUSIVE;
	return bad ? unit_refusal(p) : CR250_OK;
}

static inst_code init_coms(inst *pp, baud_rate br, flow_control fc, double tout) {
	cr250 *p = (cr250 *)pp;
	inst_code rv = inst_ok;
	int ec;
	(void)br;
	amutex_lock(p->lock);
	p->gotcoms = p->inited = 0;
	memset(&p->capture, 0, sizeof(p->capture));
	/* A new connection may be a different instrument: forget which optional
	 * settings the last one lacked. */
	memset(p->presence, 0, sizeof(p->presence));
	inst_del_disptype_list(p->dtlist, p->ndtlist);
	p->dtlist = NULL; p->ndtlist = 0;
	p->correction_mode = 0;
	memset(&p->correction, 0, sizeof(p->correction)); p->correction.id = -1;
	p->correction_accessory[0] = 0;
	/* Recovery must be deliberate: re-open then re-identify, never retry M.
	 * 115200 8N1/no flow is the host serial policy, pending physical validation. */
	p->engine.identified = 0;
	memset(&p->engine.id, 0, sizeof(p->engine.id));
	p->engine.id.instrument_type = -1;
	p->engine.expected_model = p->model->model;
	p->engine.expected_types = p->model->instrument_types;
	if (!(p->icom->port_type(p->icom) & icomt_serial)
	 || (fc != fc_nc && fc != fc_None))
		rv = inst_unsupported;
	else if (!cr250_finite(tout) || tout <= 0)
		rv = inst_bad_parameter;
	else if (p->icom->set_ser_port(p->icom, fc_None, baud_115200,
	                              parity_none, stop_1, length_8) != ICOM_OK)
		rv = map(CR250_COMS_FAIL);
	else if ((ec = cr250_imp_recover(&p->engine)) != CR250_OK)
		rv = map(ec);
	else if ((ec = cr250_imp_identify(&p->engine, tout)) != CR250_OK)
		rv = map(ec);
	else
		p->gotcoms = 1;
	amutex_unlock(p->lock);
	return rv;
}

/* Changes nothing on the instrument: settings are written only by a read. */
static inst_code init_inst(inst *pp) {
	cr250 *p = (cr250 *)pp;
	inst_code rv = inst_ok;
	amutex_lock(p->lock);
	p->inited = 0;
	if (!p->gotcoms)
		rv = inst_no_coms;
	else if (!p->engine.identified)
		rv = map(CR250_NOT_IDENTIFIED);
	else if (!cr250_firmware_at_least(&p->engine.id, p->model->fw_major, p->model->fw_minor))
		rv = map(CR250_FIRMWARE);
	else
		p->inited = 1;
	amutex_unlock(p->lock);
	return rv;
}

static char *serial_no(inst *pp) {
	return ((cr250 *)pp)->engine.id.serial;
}

static inst_mode row_modes(const cr250_model *m) {
	inst_mode modes = inst_mode_emis_spot | inst_mode_emis_tele | inst_mode_colorimeter
	                | inst_mode_emis_refresh_ovd | inst_mode_emis_norefresh_ovd;
	if (m->spectral) modes |= inst_mode_spectral;
	if (m->nonadaptive) modes |= inst_mode_emis_nonadaptive;
	return modes;
}

/* Spot or telephoto emission, plus the qualifiers the row offers. */
static int mode_ok(const cr250_model *m, inst_mode mode) {
	inst_mode ovd = mode & inst_mode_emis_norefresh_ovd, base;
	if (ovd != 0 && ovd != inst_mode_emis_refresh_ovd && ovd != inst_mode_emis_norefresh_ovd)
		return 0;
	base = mode & ~(inst_mode_colorimeter | inst_mode_emis_norefresh_ovd);
	if (m->spectral) base &= ~inst_mode_spectral;
	if (m->nonadaptive) base &= ~inst_mode_emis_nonadaptive;
	return base == inst_mode_emis_spot || base == inst_mode_emis_tele;
}

static void capabilities(inst *pp, inst_mode *m, inst2_capability *c2, inst3_capability *c3) {
	cr250 *p = (cr250 *)pp;
	if (m) *m = row_modes(p->model);
	if (c2) *c2 = inst2_prog_trig | inst2_user_trig | inst2_disptype
	           | (no_sync(p) ? 0 : inst2_set_refresh_rate);
	if (c3) *c3 = inst3_average;
}

static inst_code check_mode(inst *pp, inst_mode m) {
	cr250 *p = (cr250 *)pp;
	if (!pp->gotcoms) return inst_no_coms;
	if (!pp->inited) return inst_no_init;
	if (!mode_ok(p->model, m)) return inst_unsupported;
	/* The mode bits cannot withdraw refresh alone: no-refresh contains its bit */
	if ((m & inst_mode_emis_norefresh_ovd) == inst_mode_emis_refresh_ovd && no_sync(p))
		return inst_unsupported;
	return inst_ok;
}

static inst_code set_mode(inst *pp, inst_mode m) {
	cr250 *p = (cr250 *)pp;
	inst_code rv;
	if ((rv = check_mode(pp, m)) != inst_ok) return rv;
	amutex_lock(p->lock);
	p->mode = m;
	/* The latest request wins: no refresh sync cancels an earlier rate. */
	if (IMODETST(m, inst_mode_emis_norefresh_ovd)) p->refresh_hz = 0.0;
	amutex_unlock(p->lock);
	return inst_ok;
}

/* Why the link cannot take a request now. Called with the lock held. */
static inst_code link_ready(cr250 *p) {
	if (!p->gotcoms) return inst_no_coms;
	if (!p->inited) return inst_no_init;
	if (p->engine.uncertain) return map(CR250_UNCERTAIN);
	if (!p->engine.identified) return map(CR250_NOT_IDENTIFIED);
	return inst_ok;
}

/* RC Matrix is scoped to the current accessory (Remote 1.36, 4.3.2.15).
 * RC Match is instrument-wide (4.3.2.16). These are implicit device-applied
 * calibrations, not matrices downloaded for host multiplication. Argyll's
 * common selector builder supplies unique selectors, including two-character
 * selectors for long lists; no display technology is guessed from a name. */
static inst_code display_list(cr250 *p) {
	inst_disptypesel *base = NULL, *list = NULL;
	cr250_record *records = NULL;
	int *modes = NULL, n = 1, nlist = 0, mode, i, ec;
	cr250_inventory available = {0}, profiles = {0};
	char accessory[CR250_MAX_VALUE] = {0};
	inst_code rv;
	if ((ec = cr250_imp_inventory(&p->engine, "UserCalibMode", &available, READ_TIMEOUT)) != CR250_OK) {
		rv = map(ec); goto done;
	}
	for (i = 0, ec = 0; i < available.count; i++) if (!strcmp(available.rec[i].name, "None")) ec++;
	if (ec != 1) { rv = map(CR250_SETUP); goto done; }
	base = calloc(CR250_MAX_DISPTYPES + 1, sizeof(*base));
	records = calloc(CR250_MAX_DISPTYPES, sizeof(*records));
	modes = calloc(CR250_MAX_DISPTYPES, sizeof(*modes));
	if (base == NULL || records == NULL || modes == NULL) { rv = inst_internal_error; goto done; }
	records[0].id = -1;
	base[0].flags = inst_dtflags_default;
	strcpy(base[0].sel, "n"); strcpy(base[0].desc, "No user calibration");
	base[0].dtech = disptech_unknown;
	for (mode = 1; mode <= 2; mode++) {
		const char *key = mode == 1 ? "Matrix" : "Match";
		int present = 0;
		if (mode == 1 && (!p->model->matrix || p->engine.id.instrument_type != 1)) continue;
		for (i = 0; i < available.count; i++)
			if (!strcmp(available.rec[i].name, key)) present++;
		if (present == 0) continue;
		if (present != 1) { rv = map(CR250_SETUP); goto done; }
		if (mode == 1 && (ec = read_value(p, "RS Accessory", accessory)) != CR250_OK) {
			rv = map(ec); goto done;
		}
		if ((ec = cr250_imp_inventory(&p->engine, key, &profiles, READ_TIMEOUT)) != CR250_OK) {
			rv = map(ec); goto done;
		}
		for (i = 0; i < profiles.count; i++, n++) {
			modes[n] = mode; records[n] = profiles.rec[i];
			base[n].ix = n; base[n].dtech = disptech_unknown;
			snprintf(base[n].desc, sizeof(base[n].desc), "%s %lld: %.65s", key,
			         profiles.rec[i].id, profiles.rec[i].name);
			base[n].desc[sizeof(base[n].desc) - 1] = 0;
		}
	}
	base[n].flags = inst_dtflags_end;
	rv = inst_creat_disptype_list((inst *)p, &nlist, &list, base, 0, 0);
	if (rv != inst_ok) goto done;
	inst_del_disptype_list(p->dtlist, p->ndtlist);
	free(p->correction_modes); free(p->corrections);
	p->dtlist = list; p->ndtlist = nlist;
	p->correction_modes = modes; p->corrections = records;
	list = NULL; modes = NULL; records = NULL;
	strcpy(p->matrix_accessory, accessory);
done:
	cr250_inventory_dispose(&available); cr250_inventory_dispose(&profiles);
	inst_del_disptype_list(list, nlist);
	free(base); free(modes); free(records);
	return rv;
}

static inst_code get_disptypesel(inst *pp, int *count, inst_disptypesel **list,
                                int allconfig, int recreate) {
	cr250 *p = (cr250 *)pp;
	inst_code rv;
	(void)allconfig; /* Stored Matrix profiles exist only for the current accessory. */
	amutex_lock(p->lock);
	rv = link_ready(p);
	if (rv == inst_ok && (p->dtlist == NULL || recreate)) rv = display_list(p);
	if (rv == inst_ok) {
		if (count) *count = p->ndtlist;
		if (list) *list = p->dtlist;
	}
	amutex_unlock(p->lock);
	return rv;
}

/* Queue the selection. Its current inventory identity and full measurement
 * setup are revalidated at read_sample before any persistent SM write. */
static inst_code set_disptype(inst *pp, int index) {
	cr250 *p = (cr250 *)pp;
	inst_code rv;
	amutex_lock(p->lock);
	rv = link_ready(p);
	if (rv == inst_ok && p->dtlist == NULL) rv = display_list(p);
	if (rv == inst_ok) {
		if (index < 0 || index >= p->ndtlist) rv = inst_unsupported;
		else {
			int ix = p->dtlist[index].ix;
			p->correction_mode = p->correction_modes[ix];
			p->correction = p->corrections[ix];
			strcpy(p->correction_accessory, p->correction_mode == 1 ? p->matrix_accessory : "");
		}
	}
	amutex_unlock(p->lock);
	return rv;
}

static int profile_id(const char *value, long long *id) {
	return cr250_parse_integer(value, id) && *id >= 0;
}

/* Resolve the queued profile in a fresh inventory, preserving its ID/name.
 * RS Matrix/Match is queried only while the corresponding mode is active,
 * as Remote 1.36 sections 4.3.3.14/15 require. */
static int correction_prepare(cr250 *p, char arg[32]) {
	cr250_capture *c = &p->capture;
	const char *key = p->correction_mode == 1 ? "Matrix" : "Match";
	cr250_inventory inv = {0};
	char value[CR250_MAX_VALUE], cmd[64];
	long long id;
	int i, ec, found = 0;
	c->correction_mode = p->correction_mode;
	c->correction.id = -1;
	if (!p->correction_mode) return CR250_OK;
	if (p->correction_mode == 1) {
		if (!p->model->matrix || p->engine.id.instrument_type != 1) return CR250_SETUP;
		if (strcmp(setting(p, "Accessory"), p->correction_accessory))
			return refuse(p, CR250_SETUP, "Matrix accessory changed; recreate and reselect its display type");
	}
	if ((ec = cr250_imp_inventory(&p->engine, key, &inv, READ_TIMEOUT)) != CR250_OK) {
		cr250_inventory_dispose(&inv); return ec;
	}
	for (i = 0; i < inv.count; i++)
		if (inv.rec[i].id == p->correction.id && !strcmp(inv.rec[i].name, p->correction.name)) found++;
	cr250_inventory_dispose(&inv);
	if (found != 1) return refuse(p, CR250_SETUP, "%s %lld (%s) no longer exists; recreate and reselect its display type",
	                             key, p->correction.id, p->correction.name);
	c->correction = p->correction;
	if (!strcmp(setting(p, "UserCalibMode"), key)) {
		snprintf(cmd, sizeof(cmd), "RS %s", key);
		if ((ec = read_value(p, cmd, value)) != CR250_OK) return ec;
		if (!profile_id(value, &id)) return CR250_DATA_PARSE_ERROR;
		if (id == p->correction.id) return CR250_OK;
	}
	snprintf(arg, 32, "%lld", p->correction.id);
	return CR250_OK;
}

static int correction_readback(cr250 *p, const char *prefix) {
	char cmd[64], value[CR250_MAX_VALUE];
	long long id;
	int ec;
	if (!p->correction_mode) return CR250_OK;
	snprintf(cmd, sizeof(cmd), "%s %s", prefix, p->correction_mode == 1 ? "Matrix" : "Match");
	if ((ec = read_value(p, cmd, value)) != CR250_OK) return ec;
	if (!profile_id(value, &id)) return CR250_DATA_PARSE_ERROR;
	if (id != p->correction.id)
		return refuse(p, !strcmp(prefix, "RM") ? CR250_CAPTURE_MISMATCH : CR250_SETUP,
		              "%s is %lld, not selected profile %lld", cmd, id, p->correction.id);
	return CR250_OK;
}

/* Check an option against the instrument's limits when it is set, so a bad
 * value fails the option, not every later read. */
static inst_code options(inst *pp, inst_opt_type opt, ...) {
	cr250 *p = (cr250 *)pp;
	va_list args;
	inst_code rv;
	if (opt == inst_opt_trig_prog || opt == inst_opt_trig_user) {
		amutex_lock(p->lock);
		p->trigger = opt;
		amutex_unlock(p->lock);
		return inst_ok;
	}
	if (opt == inst_opt_set_averages) {
		int n, lo, hi, ec;
		va_start(args, opt);
		n = va_arg(args, int);
		va_end(args);
		if (n < 0) return inst_bad_parameter;
		amutex_lock(p->lock);
		if (n == 0) {
			p->averages = 0;					/* Argyll default: ExposureX as found */
			rv = inst_ok;
		} else if ((rv = link_ready(p)) == inst_ok) {
			if ((ec = multiplier_limits(p, &lo, &hi)) != CR250_OK) rv = map(ec);
			else if (n < lo || n > hi) rv = inst_bad_parameter;
			else p->averages = n;
		}
		amutex_unlock(p->lock);
		return rv;
	}
	va_start(args, opt);
	rv = inst_get_set_opt_def(pp, opt, args);
	va_end(args);
	return rv;
}

/* Sync to a refresh rate: SyncMode Manual at SyncFreq (4.2.1.12-13). */
static inst_code set_refresh(inst *pp, double rate) {
	cr250 *p = (cr250 *)pp;
	char arg[32];
	double lo, hi, sent;
	inst_code rv;
	int ec;
	if (rate != 0.0 && !(cr250_finite(rate) && rate >= 5.0 && rate <= 150.0))
		return inst_bad_parameter;			/* Argyll's range (inst.h) */
	amutex_lock(p->lock);
	if (rate == 0.0) {
		p->refresh_hz = 0.0;
		rv = inst_ok;
	} else if (no_sync(p))
		rv = inst_unsupported;
	else if (IMODETST(p->mode, inst_mode_emis_norefresh_ovd))
		rv = inst_wrong_setup;
	else if ((rv = link_ready(p)) == inst_ok) {
		if ((ec = sync_limits(p, &lo, &hi)) != CR250_OK) rv = map(ec);
		else if (!refresh_value(rate, arg, &sent)) rv = map(CR250_DATA_PARSE_ERROR);
		else if (rate < lo || rate > hi) rv = inst_bad_parameter;
		else if (sent < lo || sent > hi) rv = inst_bad_parameter;
		else p->refresh_hz = rate;
	}
	amutex_unlock(p->lock);
	return rv;
}

static inst_code calibrations(inst *pp, inst_cal_type *needed, inst_cal_type *available) {
	(void)pp;
	/* No host calibration operation exists here. This does not certify the
	 * instrument's factory calibration, whose status this protocol cannot read. */
	if (needed) *needed = inst_calt_none;
	if (available) *available = inst_calt_none;
	return inst_ok;
}

/* Why a transaction cannot start now. Called with the lock held. */
static inst_code ready(cr250 *p, int *nsetup) {
	if ((*nsetup = setting_count(p->model)) < 0) return map(CR250_INTERNAL_ERROR);
	return link_ready(p);
}

/* The checked transaction: settings (written where this read needs another
 * value), exposure, sync and range preflight, one M, capture metadata, and in
 * spectral mode the spectrum. Called with the lock held; every return leaves
 * it to the caller. */
static inst_code capture_reading(cr250 *p, int nsetup, ipatch *out, instClamping clamp) {
	const cr250_model *m = p->model;
	const cr250_setting *setup = m->setup;
	char value[CR250_MAX_VALUE], cmd[64], buf[32];
	char arguments[CR250_MAX_SETUP][32] = {{0}};
	char frequency_argument[32] = {0};
	char correction_argument[32] = {0};
	const char *exposure_mode, *range_mode, *sync_mode, *want;
	cr250_capture *c = &p->capture;
	cr250_record record;
	cr250_reply r;
	double maxexp, minexp, base, budget, exp, autoexp, tol = 0.0, xyz[3], period_ms = 0.0;
	double sync_lo = 0.0, sync_hi = 0.0, manual_hz = 0.0;
	int ec, i, lo, hi, fixed, auto_limit, range_reads = 0, sync_read, spectral;

	/* Read the setup first. Required capabilities, planned settings and the
	 * measurement budget must all validate before the first persistent SM. */
	for (i = 0; i < nsetup; i++) {
		char *v = c->value[i];
		snprintf(cmd, sizeof(cmd), "RS %s", setup[i].key);
		ec = read_value(p, cmd, v);
		if ((ec = presence(p, i, cmd, ec, v)) != CR250_OK) return map(ec);
		want = setup[i].driver_set ? target(p, setup[i].key, buf) : NULL;
		if (p->presence[i] < 0) {
			v[0] = '\0';
			/* Without SyncMode it never syncs, which is what None asks for */
			if (want != NULL && !(strcmp(setup[i].key, "SyncMode") == 0 && strcmp(want, "None") == 0))
				return map(refuse(p, CR250_SETUP, "this %s has no %s setting to set to %s",
				                  m->model, setup[i].key, want));
			continue;
		}
	}
	for (i = 0; i < nsetup; i++) {
		const char *v = c->value[i], *planned;
		if (p->presence[i] < 0) continue;
		want = setup[i].driver_set ? target(p, setup[i].key, buf) : NULL;
		planned = want != NULL ? want : v;
		ec = setting_ok(p, i, planned);
		if (ec != CR250_OK) {
			if (ec == CR250_SETUP && !strcmp(setup[i].key, "Filter"))
				return map(refuse(p, ec, "RS Filter %s must declare three slots with distinct Radiance inventory records, or None", planned));
			return map(ec != CR250_SETUP ? ec
			    : refuse(p, ec, "RS %s is %s, not %s", setup[i].key, planned,
			             setup[i].required ? setup[i].required : "None in every slot"));
		}
		if (setup[i].inventory
		 && (ec = unique_entry(p, setup[i].key, planned, setup[i].record_type, &record)) != CR250_OK)
			return map(ec != CR250_SETUP ? ec
			    : refuse(p, ec, "RS %s %s names no single RC %s record%s%s", setup[i].key,
			             planned, setup[i].key, setup[i].record_type ? " of type " : "",
			             setup[i].record_type ? setup[i].record_type : ""));
		if (want == NULL || strcmp(v, want) == 0) continue;
		if (takes_id(setup[i].key)) {
			if (!setup[i].inventory
			 && (ec = unique_entry(p, setup[i].key, want, NULL, &record)) != CR250_OK)
				return map(ec != CR250_SETUP ? ec
				    : refuse(p, CR250_SETUP, "RC %s lists no single %s to set", setup[i].key, want));
				snprintf(arguments[i], sizeof(arguments[i]), "%lld", record.id);
		} else
			snprintf(arguments[i], sizeof(arguments[i]), "%s", want);
	}
	if ((ec = correction_prepare(p, correction_argument)) != CR250_OK) return map(ec);

	/* Exposure: Auto up to RC MaxExposure, or Fixed at RS Exposure (4.2.1.7-9). */
	exposure_mode = target(p, "ExposureMode", buf);
	if (exposure_mode == NULL || (strcmp(exposure_mode, "Auto") && strcmp(exposure_mode, "Fixed")))
		return map(refuse(p, CR250_SETUP, "ExposureMode %s is neither Auto nor Fixed",
		                  exposure_mode ? exposure_mode : "not reported"));
	fixed = strcmp(exposure_mode, "Fixed") == 0;
	if ((ec = read_value(p, "RC MaxExposure", value)) != CR250_OK) return map(ec);
	if (!milliseconds(value, &maxexp)) return map(CR250_DATA_PARSE_ERROR);
	base = maxexp;
	if (fixed) {
		if ((ec = read_value(p, "RC MinExposure", value)) != CR250_OK) return map(ec);
		if (!milliseconds(value, &minexp)) return map(CR250_DATA_PARSE_ERROR);
		if (minexp > maxexp)
			return map(refuse(p, CR250_SETUP, "Exposure limits %.3f to %.3f ms are reversed", minexp, maxexp));
		if ((ec = read_value(p, "RS Exposure", value)) != CR250_OK) return map(ec);
		if (!milliseconds(value, &c->fixed_exposure_ms)) return map(CR250_DATA_PARSE_ERROR);
		if (c->fixed_exposure_ms < minexp || c->fixed_exposure_ms > maxexp)
			return map(refuse(p, CR250_SETUP, "Fixed exposure %.3f ms is outside %.3f to %.3f ms",
			                  c->fixed_exposure_ms, minexp, maxexp));
		base = c->fixed_exposure_ms;
		tol = EXPOSURE_TOL_REL * base > EXPOSURE_TOL_MS ? EXPOSURE_TOL_REL * base : EXPOSURE_TOL_MS;
	}
	if (!whole(setting(p, "ExposureX"), &c->exposure_x)) return map(CR250_DATA_PARSE_ERROR);
	if ((ec = multiplier_limits(p, &lo, &hi)) != CR250_OK) return map(ec);
	if (p->averages > 0) c->exposure_x = p->averages;
	if (c->exposure_x < lo || c->exposure_x > hi)
		return map(refuse(p, CR250_SETUP, "ExposureX %d is outside %d to %d", c->exposure_x, lo, hi));

	/* Sync (4.2.1.12-13): Manual runs at RS SyncFreq, set from the host's
	 * refresh rate when it gave one; other modes report what they used. */
	sync_mode = target(p, "SyncMode", buf);
	if (sync_mode == NULL) sync_mode = setting(p, "SyncMode");
	if (no_sync(p)) sync_mode = NULL;
	sync_read = sync_mode != NULL && strcmp(sync_mode, "None") != 0;
	if (sync_mode != NULL && strcmp(sync_mode, "Manual") == 0) {
		double planned;
		if ((ec = sync_limits(p, &sync_lo, &sync_hi)) != CR250_OK) return map(ec);
		if ((ec = read_value(p, "RS SyncFreq", value)) != CR250_OK) return map(ec);
		if (!hertz(value, &c->sync_hz)) return map(CR250_DATA_PARSE_ERROR);
		/* RS can hold zero while sync is None/Auto. A valid explicit target
		 * replaces it; keeping a Manual zero has no positive-frequency meaning. */
		if (!(c->sync_hz > 0.0) && !(p->refresh_hz > 0.0)) return map(CR250_DATA_PARSE_ERROR);
		if (p->refresh_hz > 0.0 && (p->refresh_hz < sync_lo || p->refresh_hz > sync_hi))
			return map(refuse(p, CR250_SETUP, "SyncFreq %.2f Hz is outside %.2f to %.2f Hz",
			                  p->refresh_hz, sync_lo, sync_hi));
		planned = c->sync_hz;
		if (p->refresh_hz > 0.0 && fabs(c->sync_hz - p->refresh_hz) > SYNC_TOL_HZ
		 && !refresh_value(p->refresh_hz, frequency_argument, &planned))
			return map(CR250_DATA_PARSE_ERROR);
		if (planned < sync_lo || planned > sync_hi)
			return map(refuse(p, CR250_SETUP, "SyncFreq %.2f Hz is outside %.2f to %.2f Hz",
			                  planned, sync_lo, sync_hi));
		manual_hz = planned;
	}

	/* Range (4.2.1.10-11): Fixed at RS Range; Auto reports the one used. */
	range_mode = setting(p, "RangeMode");
	if (range_mode != NULL) {
		if (strcmp(range_mode, "Fixed") == 0) {
			if ((ec = read_value(p, "RS Range", value)) != CR250_OK) return map(ec);
			if ((ec = unique_entry(p, "Range", value, NULL, &c->range)) != CR250_OK)
				return map(ec != CR250_SETUP ? ec
				    : refuse(p, ec, "RS Range %s names no single RC Range record", value));
			range_reads = 1;					/* RM Range */
		} else if (strcmp(range_mode, "Auto") == 0)
			range_reads = 2;					/* RM Range and the RC Range list */
		else
			return map(refuse(p, CR250_SETUP, "RangeMode %s is neither Auto nor Fixed", range_mode));
	}

	auto_limit = !fixed && cr250_firmware_at_least(&p->engine.id, 1, 26);
	spectral = m->spectral && (p->mode & inst_mode_spectral);
	if ((ec = cr250_measure_budget(base, c->exposure_x, &budget)) != CR250_OK) return map(ec);
	c->budget_s = budget;
	if (manual_hz > 0.0 && !(1000.0 / manual_hz <= 1000.0 * budget))
		return map(refuse(p, CR250_SETUP, "SyncFreq %.2f Hz is a period longer than the %.0f s measurement budget",
		                  manual_hz, budget));
	/* Capability preflight is complete. Apply only the planned changes, using
	 * the authenticated list IDs above, and require their existing readbacks. */
	if (correction_argument[0]
	 && (ec = write_value(p, p->correction_mode == 1 ? "Matrix" : "Match", correction_argument)) != CR250_OK)
		return map(ec);
	for (i = 0; i < nsetup; i++) {
		if (!arguments[i][0]) continue;
		want = target(p, setup[i].key, buf);
		if ((ec = set_value(p, &setup[i], c->value[i], want, arguments[i])) != CR250_OK) return map(ec);
	}
	if (frequency_argument[0]) {
		if ((ec = write_value(p, "SyncFreq", frequency_argument)) != CR250_OK) return map(ec);
		if ((ec = read_value(p, "RS SyncFreq", value)) != CR250_OK) return map(ec);
		if (!hertz(value, &c->sync_hz) || !(c->sync_hz > 0.0)) return map(CR250_DATA_PARSE_ERROR);
		if (c->sync_hz < sync_lo || c->sync_hz > sync_hi)
			return map(refuse(p, CR250_SETUP, "SyncFreq %.2f Hz is outside %.2f to %.2f Hz",
			                  c->sync_hz, sync_lo, sync_hi));
		if (fabs(c->sync_hz - p->refresh_hz) > SYNC_TOL_HZ)
			return map(refuse(p, CR250_SETUP, "SM SyncFreq %s left RS SyncFreq at %s", frequency_argument, value));
	}
	if ((ec = correction_readback(p, "RS")) != CR250_OK) return map(ec);
	if ((ec = cr250_imp_measure(&p->engine, &r, budget, budget)) != CR250_OK) return map(ec);

	for (i = 0; i < nsetup; i++) {
		int missing;
		snprintf(cmd, sizeof(cmd), "RM %s", setup[i].key);
		ec = read_value(p, cmd, value);
		missing = not_available(ec, value);
		if (p->presence[i] < 0 && missing) continue;
		if (p->presence[i] < 0 || missing)
			return map(refuse(p, CR250_CAPTURE_MISMATCH, "%s is %s but RS %s was %s", cmd,
			                  missing ? "not available" : value, setup[i].key,
			                  missing ? "available" : "not available"));
		if (ec != CR250_OK) return map(ec);
		if (!capture_matches(p, &setup[i], c->value[i], value))
			return map(refuse(p, CR250_CAPTURE_MISMATCH, "%s is %s but RS %s was %s", cmd, value,
			                  setup[i].key, c->value[i]));
	}
	if ((ec = correction_readback(p, "RM")) != CR250_OK) return map(ec);
	if ((ec = read_value(p, "RM Model", value)) != CR250_OK) return map(ec);
	if (strcmp(value, p->engine.id.model))
		return map(refuse(p, CR250_CAPTURE_MISMATCH, "RM Model is %s, not %s", value, p->engine.id.model));
	if ((ec = read_value(p, "RM ID", value)) != CR250_OK) return map(ec);
	if (strcmp(value, p->engine.id.serial))
		return map(refuse(p, CR250_CAPTURE_MISMATCH, "RM ID is %s, not %s", value, p->engine.id.serial));
	if ((ec = read_value(p, "RM Warnings", value)) != CR250_OK) return map(ec);
	/* The manual specifies no list grammar beyond the zero example. Preserve
	 * every non-zero body as native text and refuse to publish a clean read. */
	if (strcmp(value, "0")) return map(CR250_CAPTURE_WARNING);
	if (m->radiometric_type >= 0) {
		if ((ec = read_value(p, "RM Radiometric", value)) != CR250_OK) return map(ec);
		if (!triplet(value, c->radiometric)) return map(CR250_DATA_PARSE_ERROR);
		if (c->radiometric[0] != m->radiometric_type)
			return map(refuse(p, CR250_CAPTURE_MISMATCH, "RM Radiometric type is %g, not %d",
			                  c->radiometric[0], m->radiometric_type));
	}
	if (sync_read) {
		double hz;
		if ((ec = read_value(p, "RM SyncFreq", value)) != CR250_OK) return map(ec);
		if (!hertz(value, &hz)) return map(CR250_DATA_PARSE_ERROR);
		if (strcmp(sync_mode, "Manual") == 0) {
			if (!(hz > 0.0) || hz < sync_lo || hz > sync_hi)
				return map(refuse(p, CR250_CAPTURE_MISMATCH, "RM SyncFreq %s is outside positive %.2f to %.2f Hz",
				                  value, sync_lo, sync_hi));
			if (fabs(hz - c->sync_hz) > SYNC_TOL_HZ)
				return map(refuse(p, CR250_CAPTURE_MISMATCH, "RM SyncFreq is %s but RS was %.2f Hz",
				                  value, c->sync_hz));
		} else
			c->sync_hz = hz;
		/* A synchronized capture integrates whole periods and finished within
		 * its M budget, so a longer period is not one it used. This also
		 * keeps the one-period allowance below finite. */
		if (c->sync_hz > 0.0 && !((period_ms = 1000.0 / c->sync_hz) <= 1000.0 * c->budget_s))
			return map(refuse(p, CR250_CAPTURE_MISMATCH,
			                  "RM SyncFreq %s is a period longer than the %.0f s measurement budget",
			                  value, c->budget_s));
	}
	/* A synchronized exposure may round up to whole periods. */
	if ((ec = read_value(p, "RM Exposure", value)) != CR250_OK) return map(ec);
	if (!milliseconds(value, &exp)) return map(CR250_DATA_PARSE_ERROR);
	if (exp > maxexp + period_ms)
		return map(refuse(p, CR250_CAPTURE_MISMATCH, "RM Exposure %.3f ms exceeds RC MaxExposure %.3f ms",
		                  exp, maxexp));
	if (fixed && (exp < c->fixed_exposure_ms - tol || exp > c->fixed_exposure_ms + tol + period_ms))
		return map(refuse(p, CR250_CAPTURE_MISMATCH, "RM Exposure %.3f ms but RS Exposure was %.3f ms",
		                  exp, c->fixed_exposure_ms));
	if (auto_limit) {
		/* Only a consistency check, so a model that does not report it is accepted. */
		ec = read_value(p, "RM MaxAutoExposure", value);
		if (!not_available(ec, value)) {
			if (ec != CR250_OK) return map(ec);
			if (!milliseconds(value, &autoexp) || exp > autoexp + period_ms || autoexp > maxexp)
				return map(refuse(p, CR250_CAPTURE_MISMATCH,
				    "RM Exposure %.3f ms, RM MaxAutoExposure %s, RC MaxExposure %.3f ms", exp, value, maxexp));
		}
	}
	if (range_mode != NULL) {
		if ((ec = read_value(p, "RM Range", value)) != CR250_OK) return map(ec);
		if (range_reads == 1) {
			if (strcmp(value, c->range.name))
				return map(refuse(p, CR250_CAPTURE_MISMATCH, "RM Range is %s but RS Range was %s",
				                  value, c->range.name));
		} else if ((ec = unique_entry(p, "Range", value, NULL, &c->range)) != CR250_OK)
			return map(ec != CR250_SETUP ? ec
			    : refuse(p, CR250_CAPTURE_MISMATCH, "RM Range %s names no single RC Range record", value));
	}
	if ((ec = read_value(p, "RM Time", value)) != CR250_OK) return map(ec);
	if ((ec = read_value(p, "RM XYZ", value)) != CR250_OK) return map(ec);
	if (!triplet(value, xyz)) return map(CR250_DATA_PARSE_ERROR);
	/* RM XYZ is native 2-degree XYZ. The Radiance accessory record, and the
	 * capture type when the row reads it, establish luminance Y in cd/m^2. */
	for (i = 0; i < 3; i++) c->xyz[i] = xyz[i];
	if (spectral) {
		if ((ec = cr250_imp_spectrum(&p->engine, &c->spectrum, &r, SPECTRUM_TIMEOUT)) != CR250_OK) {
			if (ec == CR250_SPECTRUM_SHAPE && c->spectrum.count > 0)
				return map(refuse(p, ec,
				    "complete native spectrum header and bins retained; grid cannot form "
				    "at least two positive finite strictly increasing binary64 wavelengths "
				    "consistent with its declared end"));
			return map(ec);
		}
		/* Original bins survive a publication refusal. Argyll's fixed xspect
		 * capacity and this driver's bounded integration policy stay separate
		 * from the native report's structural grid. */
		if (c->spectrum.count < 4 || c->spectrum.count > XSPECT_MAX_BANDS
		 || c->spectrum.start_nm < CR250_SPECTRUM_MIN_NM
		 || c->spectrum.end_nm > CR250_SPECTRUM_MAX_NM
		 || c->spectrum.step_nm < CR250_SPECTRUM_MIN_STEP_NM)
			return map(refuse(p, CR250_SPECTRUM_SHAPE,
			                  "native spectrum exceeds Argyll publication size or integration grid limits"));
		if ((ec = unit_check(p)) != CR250_OK) return map(ec);
		/* The remote manual does not establish the density unit. Only this
		 * capture's discriminating agreement can support our inferred model;
		 * a dark or unchecked spectrum cannot establish its scaling. */
		if (c->unit.verdict != CR250_UNIT_CONSISTENT)
			return map(refuse(p, CR250_SPECTRUM_UNVERIFIED,
			                  "spectral density unit is unverified for this capture; request XYZ-only"));
		/* Argyll's emission unit is mW/(m^2.sr.nm) with norm 1 (xspect.h). */
		out->sp.spec_n = c->spectrum.count;
		out->sp.spec_wl_short = c->spectrum.start_nm;
		out->sp.spec_wl_long = c->spectrum.end_nm;
		out->sp.norm = 1.0;
		for (i = 0; i < c->spectrum.count; i++)
			out->sp.spec[i] = 1000.0 * c->spectrum.value[i];
	}
	for (i = 0; i < 3; i++)
		out->XYZ[i] = clamp == instClamp && xyz[i] < 0 ? 0 : xyz[i];
	out->XYZ_v = 1;
	out->mtype = inst_mrt_emission;
	out->mcond = inst_mrc_none;
	return inst_ok;
}

static inst_code read_sample(inst *pp, char *name, ipatch *out, instClamping clamp) {
	cr250 *p = (cr250 *)pp;
	inst_opt_type trigger;
	inst_code rv;
	int nsetup, user = 0;
	int prior_presence[CR250_MAX_SETUP];
	(void)name;
	amutex_lock(p->lock);
	memset(&p->capture, 0, sizeof(p->capture));
	if (out == NULL) {
		rv = inst_bad_parameter;
	} else {
		/* Failed requests must never leave a previous reading marked valid. */
		memset(out, 0, sizeof(*out));
		rv = ready(p, &nsetup);
	}
	trigger = p->trigger;
	amutex_unlock(p->lock);
	if (rv != inst_ok) return rv;

	/* Wait for the user without the lock, as kleink10.c's read_sample does. */
	if (trigger == inst_opt_trig_user) {
		if (!p->uicallback) return inst_unsupported;
		for (;;) {
			rv = p->uicallback(p->uic_cntx, inst_armed);
			if (rv == inst_user_trig) { user = 1; break; }
			if (rv != inst_ok) return rv;
			p->engine.sleep_ms(100);
		}
		p->uicallback(p->uic_cntx, inst_triggered);
	} else if (p->uicallback &&
	           p->uicallback(p->uic_cntx, inst_armed) == inst_user_abort)
		return inst_user_abort;

	amutex_lock(p->lock);
	/* The port or identity may have changed while the lock was released. */
	/* Another read may also have populated capture during the trigger wait.
	 * Reset under this transaction's lock, including when readiness fails. */
	memset(&p->capture, 0, sizeof(p->capture));
	if ((rv = ready(p, &nsetup)) == inst_ok) {
		memcpy(prior_presence, p->presence, sizeof(prior_presence));
		rv = capture_reading(p, nsetup, out, clamp);
		if (rv != inst_ok)
			memcpy(p->presence, prior_presence, sizeof(prior_presence));
	}
	amutex_unlock(p->lock);
	return rv == inst_ok && user ? inst_user_trig : rv;
}

static char *error_text(inst *pp, int ec) {
	cr250 *p = (cr250 *)pp;
	char text[256];
	amutex_lock(p->lock);
	switch (ec & inst_imask) {
		case CR250_SETUP:
			if (p->capture.problem[0]) snprintf(text, sizeof(text), "%s: %s", p->model->model, p->capture.problem);
			else snprintf(text, sizeof(text), "%s setting/accessory is outside the measurement contract", p->model->model);
			break;
		case CR250_CAPTURE_MISMATCH:
			if (p->capture.problem[0]) snprintf(text, sizeof(text), "%s: %s", p->model->model, p->capture.problem);
			else snprintf(text, sizeof(text), "%s capture metadata does not match the checked settings", p->model->model);
			break;
		case CR250_SPECTRUM_MISMATCH:
			if (p->capture.problem[0]) snprintf(text, sizeof(text), "%s: %s", p->model->model, p->capture.problem);
			else snprintf(text, sizeof(text), "%s spectrum disagrees with the instrument's totals", p->model->model);
			break;
		case CR250_SPECTRUM_UNVERIFIED:
			if (p->capture.problem[0]) snprintf(text, sizeof(text), "%s: %s", p->model->model, p->capture.problem);
			else snprintf(text, sizeof(text), "%s spectral density unit is unverified for this capture; request XYZ-only", p->model->model);
			break;
		case CR250_CAPTURE_WARNING:
			snprintf(text, sizeof(text), "%s last capture reports warnings", p->model->model);
			break;
		case CR250_FIRMWARE:
			snprintf(text, sizeof(text), "%s measurement requires firmware %d.%d or later",
			         p->model->model, p->model->fw_major, p->model->fw_minor);
			break;
		default:
			if (p->capture.problem[0])
				snprintf(text, sizeof(text), "%s (%s)", cr250_interp_text(ec & inst_imask), p->capture.problem);
			else
				snprintf(text, sizeof(text), "%s", cr250_interp_text(ec & inst_imask));
			break;
	}
	text[sizeof(text) - 1] = '\000';				/* See refuse() */
	if (p->engine.last_reply.complete)
		snprintf(p->error_message, sizeof(p->error_message), "%s; last reply %ld:%s:%s",
		         text, p->engine.last_reply.code, p->engine.last_reply.context, p->engine.last_reply.value);
	else
		snprintf(p->error_message, sizeof(p->error_message), "%s", text);
	p->error_message[sizeof(p->error_message) - 1] = '\000';
	amutex_unlock(p->lock);
	return p->error_message;
}

static void destroy(inst *pp) {
	cr250 *p = (cr250 *)pp;
	inst_del_disptype_list(p->dtlist, p->ndtlist);
	free(p->correction_modes); free(p->corrections);
	cr250_imp_dispose(&p->engine);
	if (p->icom) p->icom->del(p->icom);
	amutex_del(p->lock);
	if (p->vdel) p->vdel(pp);
	del_a1log(p->log);
	free(p);
}

/* A row's setup table must fit the per-request buffers; a longer table is a
 * programming error refused here rather than an overflow in read_sample. */
static const cr250_model *model_for(instType dtype) {
	size_t i;
	for (i = 0; i < NMODELS; i++) {
		if (models[i].dtype != dtype) continue;
		if (setting_count(&models[i]) < 0) return NULL;
		return &models[i];
	}
	return NULL;
}

cr250 *new_cr250(icoms *icom, instType dtype) {
	cr250 *p;
	const cr250_model *model = model_for(dtype);
	if (!icom || !model) return NULL;
	if ((p = (cr250 *)calloc(1, sizeof(*p))) == NULL) return NULL;
	amutex_init(p->lock);
	p->log = new_a1log_d(icom->log);
	p->icom = icom;
	p->dtype = dtype;
	p->model = model;
	cr250_imp_init(&p->engine, icom, p->log);
	p->trigger = inst_opt_trig_user;
	p->init_coms = init_coms;
	p->init_inst = init_inst;
	p->get_serial_no = serial_no;
	p->capabilities = capabilities;
	p->get_disptypesel = get_disptypesel;
	p->set_disptype = set_disptype;
	p->check_mode = check_mode;
	p->set_mode = set_mode;
	p->get_set_opt = options;
	p->set_refr_rate = set_refresh;
	p->get_n_a_cals = calibrations;
	p->read_sample = read_sample;
	p->interp_error = error_text;
	p->del = destroy;
	return p;
}
