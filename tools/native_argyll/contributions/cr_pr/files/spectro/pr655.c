/*
 * Argyll Color Management System
 * Photo Research PR-655 and PR-670 instrument interface.
 * Copyright 2026 Fernando Voltolini de Azambuja.
 * SPDX-License-Identifier: GPL-2.0-or-later
 * This material is licensed under the GNU GENERAL PUBLIC LICENSE Version 2
 * or later; see License2.txt.
 * Written from the manufacturer's documented protocol facts. The Argyll
 * interface and integration use the existing inst/icoms/xspect owners.
 * See pr655.txt for sources, unit assumptions and software-only validation.
 */
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <math.h>
#include <float.h>
#include "pr655.h"

/* Host comparison policy, not manufacturer accuracy specifications. */
#define UNIT_MIN_POWER_W 3e-4
#define UNIT_MIN_Y 0.2
#define UNIT_MIN_RATIO 0.9
#define UNIT_MAX_RATIO 1.1
#define FOOT_LAMBERT_TO_CD_M2 (1.0 / (3.14159265358979323846 * 0.09290304))
#define FOOT_CANDLE_TO_LUX (1.0 / 0.09290304)
#define RESPONSE_SECONDS 10.0
#define MEASUREMENT_SECONDS 300.0

static int finite_number(double value) {
	return value == value && value <= DBL_MAX && value >= -DBL_MAX;
}

static int refuse(pr655 *p, int code, const char *format, ...) {
	va_list args;
	va_start(args, format);
	vsnprintf(p->capture.problem, sizeof(p->capture.problem), format, args);
	va_end(args);
	p->capture.problem[sizeof(p->capture.problem) - 1] = '\000';
	return code;
}

static inst_code map(int code) {
	switch (code) {
		case PR655_OK: return inst_ok;
		case PR655_INTERNAL_ERROR: return inst_internal_error | code;
		case PR655_TIMEOUT: case PR655_COMS_FAIL:
			return inst_coms_fail | code;
		case PR655_UNKNOWN_MODEL: return inst_unknown_model | code;
		case PR655_NOT_IDENTIFIED: return inst_no_init | code;
		case PR655_GEOMETRY_ERROR: case PR655_BAD_SETUP:
		case PR655_BUDGET_ERROR: case PR655_ALREADY_OPEN: return inst_wrong_setup | code;
		case PR655_UNIT_MISMATCH: case PR655_CAPTURE_MISMATCH:
			return inst_misread | code;
		default:
			if (code >= PR655_NATIVE_ERROR_BASE && code < 0x2000)
				return inst_misread | code;
			return inst_protocol_error | code;
	}
}

static inst_code ready(pr655 *p) {
	if (!p->gotcoms) return inst_no_coms;
	if (!p->inited) return inst_no_init;
	if (p->engine.state != pr655_ready) return map(PR655_UNCERTAIN);
	return inst_ok;
}

static int ambient(const pr655 *p) {
	return IMODETST(p->mode, inst_mode_emis_ambient);
}

/* An active optical declaration must match the requested measurement geometry.
 * The configured IDs do not prove which physical optics are fitted. */
static int optical_row(pr655 *p, long long id, const char *kind) {
	int i, found = 0;
	const char *photometry = ambient(p) ? "Illuminance" : "Luminance";
	const char *radiometry = ambient(p) ? "Irradiance" : "Radiance";
	for (i = 0; i < p->engine.accessory_count; i++) {
		const pr655_accessory *row = &p->engine.accessories[i];
		if (row->id != id) continue;
		found++;
		if (strcmp(row->type, kind) || strcmp(row->photometry, photometry)
		 || strcmp(row->radiometry, radiometry)) return 0;
	}
	return found == 1;
}

static int geometry(pr655 *p) {
	const pr655_setup *s = &p->engine.setup;
	int i, j;
	if (s->observer != 2 || !optical_row(p, s->primary, "Primary"))
		return refuse(p, PR655_GEOMETRY_ERROR,
		              "require reported 2-degree observer and %s/%s primary optics",
		              ambient(p) ? "Illuminance" : "Luminance", ambient(p) ? "Irradiance" : "Radiance");
	for (i = 0; i < 3; i++) {
		if (s->addon[i] == -1) continue;
		if (!optical_row(p, s->addon[i], "Addon"))
			return refuse(p, PR655_GEOMETRY_ERROR,
			              "active addon %lld does not declare %s/%s geometry", s->addon[i],
			              ambient(p) ? "Illuminance" : "Luminance", ambient(p) ? "Irradiance" : "Radiance");
		for (j = 0; j < i; j++)
			if (s->addon[i] == s->addon[j]) return PR655_BAD_SETUP;
	}
	if (s->units != 0 && s->units != 1) return PR655_BAD_SETUP;
	return PR655_OK;
}

static int same_setup(const pr655_setup *a, const pr655_setup *b) {
	int i;
	if (a->primary != b->primary || a->aperture != b->aperture
	 || a->units != b->units || a->observer != b->observer
	 || a->exposure_ms != b->exposure_ms || a->cycles != b->cycles
	 || a->dark_mode != b->dark_mode || a->sync_mode != b->sync_mode
	 || a->gain != b->gain || strcmp(a->text[8], b->text[8]))
		return 0;
	for (i = 0; i < 3; i++) if (a->addon[i] != b->addon[i]) return 0;
	if (a->sync_mode == 3 && a->frequency_hz != b->frequency_hz) return 0;
	/* D602 owns the named exposure and sensitivity modes. D601 numeric
	 * codes and the bare SyncPeriod do not establish those semantics. */
	return a->adaptive_known == b->adaptive_known && a->adaptive == b->adaptive
	    && a->sensitivity_known == b->sensitivity_known && a->extended == b->extended;
}

/* SN/SS/SK may change only their requested settings. A collateral readback
 * change invalidates the remaining plan before any further setter or M. */
static int unchanged_context(pr655 *p, const pr655_setup *expected) {
	if (!same_setup(expected, &p->engine.setup)
	 || expected->frequency_hz != p->engine.setup.frequency_hz)
		return refuse(p, PR655_CAPTURE_MISMATCH, "a setter changed an unrequested publication condition");
	return geometry(p);
}

static const char *nearest_factor(double ratio) {
	static const double factors[] = { 1.0, 1000.0, 0.001, 2.0, 0.5,
	                                 4.0, 0.25, FOOT_LAMBERT_TO_CD_M2,
	                                 1.0 / FOOT_LAMBERT_TO_CD_M2,
	                                 FOOT_CANDLE_TO_LUX, 1.0 / FOOT_CANDLE_TO_LUX };
	static const char *names[] = { "1", "1000", "1/1000", "2", "1/2",
	                              "4", "1/4", "fL-to-cd-m2", "cd-m2-to-fL", "fc-to-lux", "lux-to-fc" };
	double best = DBL_MAX;
	int i, at = 0;
	if (!(ratio > 0.0) || !finite_number(ratio)) return "unformable";
	for (i = 0; i < (int)(sizeof(factors) / sizeof(factors[0])); i++) {
		double distance = fabs(log(ratio) - log(factors[i]));
		if (distance < best) { best = distance; at = i; }
	}
	return names[at];
}

static int ratio_check(pr655 *p, int bit, double derived, double native, double floor_value,
                       double *ratio) {
	pr655_unit_check *u = &p->capture.unit;
	if (fabs(derived) < floor_value && fabs(native) < floor_value) return PR655_OK;
	u->discriminating |= bit;
	if (!(native > 0.0) || !finite_number(*ratio = derived / native)
	 || *ratio < UNIT_MIN_RATIO || *ratio > UNIT_MAX_RATIO)
		return refuse(p, PR655_UNIT_MISMATCH,
		              "%s spectrum/native comparison: %.9g against %.9g (nearest factor %s)",
		              bit == PR655_CHECK_POWER ? "power" : ambient(p) ? "illuminance" : "luminance", derived, native,
		              native > 0.0 ? nearest_factor(derived / native) : "unformable");
	u->computed |= bit;
	return PR655_OK;
}

/* Table9, the selected geometry and M5 integral establish the declared density
 * interpretation. Check it against independent native XYZ and the
 * native integrated radiometric value for this same M5 exposure. Reuse
 * Argyll's observer tables and xsp2cie; no host XYZ replaces native D2. */
static int unit_check(pr655 *p, xspect *spectrum, double xyz[3]) {
	const pr655_capture *native = &p->capture.native;
	pr655_unit_check *u = &p->capture.unit;
	xsp2cie *conversion;
	double scale = p->capture.before.units == 0
	             ? ambient(p) ? FOOT_CANDLE_TO_LUX : FOOT_LAMBERT_TO_CD_M2 : 1.0;
	double step = p->engine.grid.step_nm, power = 0.0;
	int i, code;
	u->xyz_scale = scale;
	if (!native->xyz_parsed || (native->xyz_units != 0 && native->xyz_units != (ambient(p) ? 112 : 111)))
		return refuse(p, PR655_GEOMETRY_ERROR, "M2/D2 units code %d is not a %s declaration",
		              native->xyz_units, ambient(p) ? "illuminance" : "luminance");
	for (i = 0; i < 3; i++) {
		xyz[i] = native->xyz[i] * scale;
		if (!finite_number(xyz[i])) return PR655_UNIT_MISMATCH;
	}
	if (!native->spectrum_parsed) return PR655_OK;
	if (ambient(p) ? native->spectrum_units != 12
	              : native->spectrum_units != 0 && native->spectrum_units != 11 && native->spectrum_units != 111)
		return refuse(p, PR655_GEOMETRY_ERROR, "M5 units code %d contradicts %s geometry",
		              native->spectrum_units, ambient(p) ? "irradiance" : "radiance");
	memset(spectrum, 0, sizeof(*spectrum));
	spectrum->spec_n = native->count;
	spectrum->spec_wl_short = 380.0;
	spectrum->spec_wl_long = 780.0;
	spectrum->norm = 1.0;
	for (i = 0; i < native->count; i++) {
		spectrum->spec[i] = 1000.0 * native->spectrum[i];
		power += step * native->spectrum[i];
		if (!finite_number(spectrum->spec[i]) || !finite_number(power)) return PR655_UNIT_MISMATCH;
	}
	u->power_w = power;
	conversion = new_xsp2cie(icxIT_none, 0.0, NULL, icxOT_CIE_1931_2, NULL,
	                        icSigXYZData, icxNoClamp);
	if (conversion == NULL) return PR655_INTERNAL_ERROR;
	/* The check uses the actual native grid and inclusive rectangle weights,
	 * as the M5 integral definition does. Spectra remain unchanged. */
	conversion->set_int_steps(conversion, step, 380.0, 780.0);
	conversion->convert(conversion, u->xyz, spectrum);
	conversion->del(conversion);
	for (i = 0; i < 3; i++) if (!finite_number(u->xyz[i])) return PR655_UNIT_MISMATCH;
	code = ratio_check(p, PR655_CHECK_POWER, power, native->integrated,
	                   UNIT_MIN_POWER_W, &u->power_ratio);
	if (code == PR655_OK)
		code = ratio_check(p, PR655_CHECK_Y, u->xyz[1], xyz[1], UNIT_MIN_Y, &u->y_ratio);
	/* M5's integral is defined from the same reported samples, so agreement
	 * with it guards parsing but cannot establish their scale. Only native D2
	 * photometric Y can, so a power-only comparison stays inconclusive. */
	u->verdict = code == PR655_OK
	           ? (u->discriminating & PR655_CHECK_Y ? PR655_UNIT_CONSISTENT : PR655_UNIT_INCONCLUSIVE)
	           : PR655_UNIT_REFUSED;
	return code;
}

/* Fixed capture preflight counts a dark for every cycle because SZ has no
 * independent readback, even if Smart Dark may reuse one. Auto uses a model
 * ceiling for host policy only; firmware timing remains unqualified. */
static int budget(pr655 *p, int cycles, int sync, double hz, double *seconds) {
	const pr655_setup *s = &p->engine.setup;
	double exposure, period = 0.0;
	if (s->adaptive_known && s->adaptive)
		exposure = p->engine.model == 670 && (!s->sensitivity_known || s->extended) ? 30.0 : 6.0;
	else if (s->adaptive_known && !s->adaptive && s->exposure_ms > 0)
		exposure = s->exposure_ms / 1000.0;
	else return PR655_BAD_SETUP;
	if (!s->adaptive && s->exposure_ms > 6000 && (!s->sensitivity_known || !s->extended))
		return PR655_BAD_SETUP;
	if (sync == 1) period = 1.0 / 20.0;
	if (sync == 3) {
		if (!(hz >= 20.0 && hz <= 400.0)) return PR655_BAD_SETUP;
		period = 1.0 / hz;
	}
	*seconds = cycles * (2.0 * exposure + period) + 5.0;
	if (!finite_number(*seconds)) return PR655_BUDGET_ERROR;
	if (*seconds < MEASUREMENT_SECONDS) *seconds = MEASUREMENT_SECONDS;
	return PR655_OK;
}

static inst_code init_coms(inst *pp, baud_rate br, flow_control fc, double timeout) {
	pr655 *p = (pr655 *)pp;
	int code;
	(void)br;
	amutex_lock(p->lock);
	/* Reinitialization must not reconfigure a live or uncertain host port. */
	if (p->engine.state != pr655_closed) {
		code = p->engine.state == pr655_ready ? PR655_ALREADY_OPEN : PR655_UNCERTAIN;
		amutex_unlock(p->lock);
		return map(code);
	}
	p->gotcoms = p->inited = 0;
	if (!finite_number(timeout) || timeout <= 0.0) code = PR655_BAD_SETUP;
	else if (!(p->icom->port_type(p->icom) & icomt_serial)
	      || (fc != fc_nc && fc != fc_None)) code = PR655_BAD_SETUP;
	else if (p->icom->set_ser_port(p->icom, fc_None, baud_115200,
	                              parity_none, stop_1, length_8) != ICOM_OK) code = PR655_COMS_FAIL;
	else code = pr655_imp_open(&p->engine, timeout);
	if (code == PR655_OK) p->gotcoms = 1;
	amutex_unlock(p->lock);
	return map(code);
}

static inst_code init_inst(inst *pp) {
	pr655 *p = (pr655 *)pp;
	int code;
	amutex_lock(p->lock);
	p->inited = 0;
	code = p->gotcoms ? pr655_imp_identity(&p->engine, RESPONSE_SECONDS) : PR655_NOT_IDENTIFIED;
	if (code == PR655_OK) code = pr655_imp_read_setup(&p->engine, RESPONSE_SECONDS);
	if (code == PR655_OK) p->inited = 1;
	amutex_unlock(p->lock);
	return map(code);
}

static char *serial_no(inst *pp) {
	pr655 *p = (pr655 *)pp;
	char *serial;
	amutex_lock(p->lock);
	serial = p->engine.identity_valid && p->engine.state == pr655_ready ? p->engine.serial : "";
	amutex_unlock(p->lock);
	return serial;
}

static void capabilities(inst *pp, inst_mode *m, inst2_capability *c2, inst3_capability *c3) {
	(void)pp;
	if (m) *m = inst_mode_emis_spot | inst_mode_emis_tele | inst_mode_emis_ambient | inst_mode_colorimeter
	          | inst_mode_spectral | inst_mode_emis_nonadaptive
	          | inst_mode_emis_refresh_ovd | inst_mode_emis_norefresh_ovd;
	if (c2) *c2 = inst2_prog_trig | inst2_user_trig | inst2_set_refresh_rate;
	if (c3) *c3 = inst3_average;
}

static inst_code check_mode(inst *pp, inst_mode mode) {
	pr655 *p = (pr655 *)pp;
	inst_code rv = ready(p);
	inst_mode qualifier = mode & inst_mode_emis_norefresh_ovd;
	inst_mode base = mode & ~(inst_mode_colorimeter | inst_mode_spectral
	                       | inst_mode_emis_nonadaptive | inst_mode_emis_norefresh_ovd);
	if (rv != inst_ok) return rv;
	if (qualifier != 0 && qualifier != inst_mode_emis_refresh_ovd
	 && qualifier != inst_mode_emis_norefresh_ovd) return inst_unsupported;
	if (base != inst_mode_emis_spot && base != inst_mode_emis_tele
	 && base != inst_mode_emis_ambient) return inst_unsupported;
	if (base == inst_mode_emis_ambient && IMODETST(mode, inst_mode_emis_nonadaptive))
		return inst_unsupported;
	if (IMODETST(mode, inst_mode_emis_nonadaptive)
	 && (!p->engine.setup.adaptive_known || p->engine.setup.adaptive)) return inst_wrong_setup;
	return ready(p);
}

static inst_code set_mode(inst *pp, inst_mode mode) {
	pr655 *p = (pr655 *)pp;
	inst_code rv = check_mode(pp, mode);
	if (rv != inst_ok) return rv;
	amutex_lock(p->lock);
	p->mode = mode;
	if (IMODETST(mode, inst_mode_emis_norefresh_ovd)) p->refresh_hz = 0.0;
	amutex_unlock(p->lock);
	return inst_ok;
}

static inst_code options(inst *pp, inst_opt_type option, ...) {
	pr655 *p = (pr655 *)pp;
	va_list args;
	inst_code rv;
	if (option == inst_opt_trig_prog || option == inst_opt_trig_user) {
		amutex_lock(p->lock); p->trigger = option; amutex_unlock(p->lock);
		return inst_ok;
	}
	if (option == inst_opt_set_averages) {
		int n;
		va_start(args, option); n = va_arg(args, int); va_end(args);
		if (n < 0 || n > 99) return inst_bad_parameter;
		amutex_lock(p->lock); p->averages = n; amutex_unlock(p->lock);
		return inst_ok;
	}
	va_start(args, option); rv = inst_get_set_opt_def(pp, option, args); va_end(args);
	return rv;
}

static inst_code set_refresh(inst *pp, double hz) {
	pr655 *p = (pr655 *)pp;
	if (!finite_number(hz) || (hz != 0.0 && (hz < 20.0 || hz > 150.0 || hz != floor(hz))))
		return inst_bad_parameter;
	amutex_lock(p->lock);
	if (hz != 0.0 && IMODETST(p->mode, inst_mode_emis_norefresh_ovd)) {
		amutex_unlock(p->lock); return inst_wrong_setup;
	}
	p->refresh_hz = hz;
	amutex_unlock(p->lock);
	return inst_ok;
}

static inst_code calibrations(inst *pp, inst_cal_type *needed, inst_cal_type *available) {
	(void)pp;
	if (needed) *needed = inst_calt_none;
	if (available) *available = inst_calt_none;
	return inst_ok;
}

static inst_code capture_reading(pr655 *p, ipatch *out, instClamping clamp) {
	pr655_capture_result *c = &p->capture;
	pr655_setup *s = &p->engine.setup;
	pr655_setup expected;
	xspect spectrum;
	double xyz[3], frequency;
	int code, cycles, sync, i;
	if ((code = pr655_imp_read_setup(&p->engine, RESPONSE_SECONDS)) != PR655_OK) return map(code);
	c->before = *s; c->setup_before_valid = 1;
	if ((code = geometry(p)) != PR655_OK) return map(code);
	if (IMODETST(p->mode, inst_mode_emis_nonadaptive) && (!s->adaptive_known || s->adaptive))
		return map(refuse(p, PR655_BAD_SETUP, "nonadaptive mode requires a reported fixed exposure"));
	/* Spot/tele without the nonadaptive bit asks for adaptive integration. Measuring
	 * a reported Fixed exposure would mislabel the reading; writing SE0 would undo
	 * the operator's setting. Refuse, before any setter or exposure. */
	if (!ambient(p) && !IMODETST(p->mode, inst_mode_emis_nonadaptive) && s->adaptive_known && !s->adaptive)
		return map(refuse(p, PR655_BAD_SETUP,
		                  "adaptive mode requires a reported Adaptive exposure; use nonadaptive mode for Fixed"));
	cycles = p->averages > 0 ? p->averages : s->cycles;
	sync = s->sync_mode;
	frequency = s->frequency_hz;
	if (IMODETST(p->mode, inst_mode_emis_norefresh_ovd)) sync = 0;
	else if (p->refresh_hz > 0.0) { sync = 3; frequency = p->refresh_hz; }
	else if (IMODETST(p->mode, inst_mode_emis_refresh_ovd)) sync = 1;
	if ((code = budget(p, cycles, sync, frequency, &c->budget_s)) != PR655_OK) return map(code);
	/* All intent and the known timing policy validated before any setter. */
	if (cycles != s->cycles) {
		expected = *s; expected.cycles = cycles;
		if ((code = pr655_imp_set_averages(&p->engine, cycles, RESPONSE_SECONDS)) != PR655_OK) return map(code);
		if ((code = unchanged_context(p, &expected)) != PR655_OK) return map(code);
	}
	if (sync == 3 && frequency != s->frequency_hz) {
		expected = *s; expected.frequency_hz = frequency;
		if ((code = pr655_imp_set_frequency(&p->engine, (int)frequency, RESPONSE_SECONDS)) != PR655_OK) return map(code);
		if ((code = unchanged_context(p, &expected)) != PR655_OK) return map(code);
	}
	if (sync != s->sync_mode) {
		expected = *s; expected.sync_mode = sync;
		if ((code = pr655_imp_set_sync(&p->engine, sync, RESPONSE_SECONDS)) != PR655_OK) return map(code);
		if ((code = unchanged_context(p, &expected)) != PR655_OK) return map(code);
	}
	if (s->cycles != cycles || s->sync_mode != sync || (sync == 3 && s->frequency_hz != frequency))
		return map(PR655_READBACK_ERROR);
	if ((code = geometry(p)) != PR655_OK) return map(code);
	if ((code = budget(p, cycles, sync, frequency, &c->budget_s)) != PR655_OK) return map(code);
	c->before = *s;
	code = pr655_imp_measure(&p->engine, (p->mode & inst_mode_spectral) != 0, &c->native, c->budget_s);
	if (code != PR655_OK) return map(code);
	/* Current setup is documentary, not an atomic exposure snapshot. Refuse
	 * changes to material publication conditions within our owned transaction. */
	if ((code = pr655_imp_read_setup(&p->engine, RESPONSE_SECONDS)) != PR655_OK) return map(code);
	c->after = *s; c->setup_after_valid = 1;
	if ((code = geometry(p)) != PR655_OK) return map(code);
	if (!same_setup(&c->before, &c->after))
		return map(refuse(p, PR655_CAPTURE_MISMATCH, "material setup changed across the capture"));
	memset(&spectrum, 0, sizeof(spectrum));
	if ((code = unit_check(p, &spectrum, xyz)) != PR655_OK) {
		c->unit.verdict = PR655_UNIT_REFUSED;
		return map(code);
	}
	for (i = 0; i < 3; i++) out->XYZ[i] = xyz[i];
	if (clamp) icmClamp3(out->XYZ, out->XYZ);
	out->XYZ_v = 1;
	out->sp = spectrum;
	out->mtype = ambient(p) ? inst_mrt_ambient : inst_mrt_emission;
	out->mcond = inst_mrc_none;
	c->valid = 1;
	return inst_ok;
}

static inst_code read_sample(inst *pp, char *name, ipatch *out, instClamping clamp) {
	pr655 *p = (pr655 *)pp;
	inst_code rv;
	inst_opt_type trigger;
	int user = 0;
	(void)name;
	amutex_lock(p->lock);
	pr655_capture_clear(&p->capture.native);
	memset(&p->capture, 0, sizeof(p->capture));
	if (out == NULL) rv = inst_bad_parameter;
	else { memset(out, 0, sizeof(*out)); rv = ready(p); }
	trigger = p->trigger;
	amutex_unlock(p->lock);
	if (rv != inst_ok) return rv;
	if (trigger == inst_opt_trig_user) {
		if (!p->uicallback) return inst_unsupported;
		for (;;) {
			rv = p->uicallback(p->uic_cntx, inst_armed);
			if (rv == inst_user_trig) { user = 1; break; }
			if (rv != inst_ok) return rv;
			p->engine.sleep(100);
		}
		p->uicallback(p->uic_cntx, inst_triggered);
	} else if (p->uicallback && p->uicallback(p->uic_cntx, inst_armed) == inst_user_abort)
		return inst_user_abort;
	amutex_lock(p->lock);
	/* Recheck after the user callback and clear again while owning the
	 * transaction lock. A failed read cannot inherit another read's facts. */
	pr655_capture_clear(&p->capture.native);
	memset(&p->capture, 0, sizeof(p->capture));
	if ((rv = ready(p)) == inst_ok) rv = capture_reading(p, out, clamp);
	amutex_unlock(p->lock);
	return rv == inst_ok && user ? inst_user_trig : rv;
}

static char *error_text(inst *pp, int code) {
	pr655 *p = (pr655 *)pp;
	const char *text = "PR protocol or publication refusal";
	code &= inst_imask;	/* Callers such as spotread pass the whole inst_code. */
	if (code == PR655_OK) return "No error";
	if (code == PR655_ALREADY_OPEN) return "PR connection is already open; repeated initialization was refused";
	if (code == PR655_TIMEOUT) text = "PR response deadline expired; reconnect after confirming exposure is idle";
	else if (code == PR655_UNCERTAIN) text = "PR link is uncertain; no further commands permitted";
	else if (code == PR655_UNKNOWN_MODEL) text = "Selected port reported a different PR model";
	else if (code == PR655_COMS_FAIL) text = "PR serial transfer failed";
	if (p->capture.problem[0]) text = p->capture.problem;
	snprintf(p->error_message, sizeof(p->error_message), "%s (native error %d)", text, p->engine.device_error);
	p->error_message[sizeof(p->error_message) - 1] = '\000';
	return p->error_message;
}

static void destroy(inst *pp) {
	pr655 *p = (pr655 *)pp;
	amutex_lock(p->lock);
	(void)pr655_imp_close(&p->engine, RESPONSE_SECONDS);
	pr655_capture_clear(&p->capture.native);
	pr655_imp_dispose(&p->engine);
	p->icom->del(p->icom);
	amutex_unlock(p->lock);
	amutex_del(p->lock);
	del_a1log(p->log);
	free(p);
}

pr655 *new_pr655(icoms *icom, instType dtype) {
	pr655 *p;
	int model = dtype == instPR655 ? 655 : dtype == instPR670 ? 670 : 0;
	if (icom == NULL || model == 0) return NULL;
	if ((p = (pr655 *)calloc(1, sizeof(*p))) == NULL) return NULL;
	amutex_init(p->lock);
	p->log = new_a1log_d(icom->log);
	p->icom = icom; p->dtype = dtype;
	pr655_imp_init(&p->engine, icom, model);
	p->trigger = inst_opt_trig_user;
	p->mode = inst_mode_emis_tele;
	p->init_coms = init_coms; p->init_inst = init_inst;
	p->get_serial_no = serial_no;
	p->capabilities = capabilities; p->check_mode = check_mode; p->set_mode = set_mode;
	p->get_set_opt = options; p->set_refr_rate = set_refresh;
	p->get_n_a_cals = calibrations; p->read_sample = read_sample;
	p->interp_error = error_text; p->del = destroy;
	return p;
}
