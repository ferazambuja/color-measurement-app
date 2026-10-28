/*
 * Actual Argyll PR vtable and constructor tests over the existing engine actor.
 * No OS instrument transport is linked. Transcripts are constructed.
 * Copyright 2026 Fernando Voltolini de Azambuja.
 * SPDX-License-Identifier: GPL-2.0-or-later
 * This material is licensed under the GNU GENERAL PUBLIC LICENSE Version 2
 * or later.
 */
#define main pr655_engine_selftest_main
#include "pr655_test.c"
#undef main
#include "pr655.h"

static int opened, closed, deleted;
static char *owned_reply[64];
static int owned_count;
static char xyz_reply[256];
static const double fl_factor = 1.0 / (3.14159265358979323846 * 0.09290304);

static void reset_owned(void) {
	int i;
	for (i = 0; i < owned_count; i++) { free(owned_reply[i]); owned_reply[i] = NULL; }
	owned_count = 0;
}

/* Snapshot mutable generated replies, reusing the engine's one fake transport. */
static void add_owned(const char *command, const char *reply) {
	if (owned_count >= 64 || strlen(reply) > PR655_MAX_RESPONSE) abort();
	owned_reply[owned_count] = malloc(strlen(reply) + 1);
	if (!owned_reply[owned_count]) abort();
	strcpy(owned_reply[owned_count], reply);
	add(command, owned_reply[owned_count++]);
}

static void setup_steps(void) {
	add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
	add_owned("D112\r", "0000,1,3\r\n");
	add_owned("D116\r", "0000,8,Test lens,Primary,Luminance,Radiance\r\n");
}

static void full_setup(int units, int observer, int cycles, int sync, int frequency,
                       int exposure, int extended, int primary, int addon) {
	snprintf(numeric_setup, sizeof(numeric_setup),
	         "0000,%d,%d,-1,-1,2,%d,73,%d,87,%d,%d,0,%d,91,75.0\r\n",
	         primary, addon, units, exposure, cycles, observer, sync);
	snprintf(text_setup, sizeof(text_setup),
	         "0000,Test lens,%s,None,None,Half degree,%s,%s,%d msec,Native gain,%d cycles,%d deg,No Smart Dark,%s,%s,%.2f Hertz\r\n",
	         addon == -1 ? "None" : "Test addon", units ? "Metric" : "English",
	         exposure ? "Fixed" : "Adaptive", exposure, cycles, observer,
	         sync == 0 ? "No Sync" : sync == 1 ? "Auto Sync" : "User Sync",
	         extended ? "Extended Sensitivity" : "Standard Sensitivity", (double)frequency);
}

static double constructed_y(int model, double sample) {
	xspect *observer[3];
	double y = 0.0;
	int i, step = model == 655 ? 4 : 2;
	if (standardObserver(observer, icxOT_CIE_1931_2)) abort();
	for (i = 380; i <= 780; i += step) y += 683.0 * sample * step * value_xspect(observer[1], i);
	return y;
}

static void capture_steps(int model, int spectral, int units, int spectrum_code,
                         int xyz_code, double sample, double power_factor,
                         double xyz_factor, const char *late_error) {
	int i, used, count = model == 655 ? 101 : 201, step = model == 655 ? 4 : 2;
	double y = constructed_y(model, sample), native_scale = units ? 1.0 : 1.0 / fl_factor;
	used = snprintf(native_spectrum, sizeof(native_spectrum), "0000,%d,554,%.16g,4.125\r\n",
	                spectrum_code, sample * count * step / power_factor);
	for (i = 0; i < count; i++)
		used += snprintf(native_spectrum + used, sizeof(native_spectrum) - used,
		                 "%d,%.16g\r\n", 380 + i * step, sample);
	snprintf(xyz_reply, sizeof(xyz_reply), "0000,%d,%.16g,%.16g,%.16g\r\n",
	         xyz_code, 1.25 * y * native_scale * xyz_factor,
	         y * native_scale * xyz_factor, -0.125 * y * native_scale * xyz_factor);
	add_owned("B00\r", "Backlight set to 0 %\r\n");
	add_owned(spectral ? "M5\r" : "M2\r", spectral ? native_spectrum : xyz_reply);
	if (spectral) add_owned("D2\r", late_error && !strcmp(late_error, "D2") ? "0000,0,nan,1,2\r\n" : xyz_reply);
	add_owned("B100\r", late_error && !strcmp(late_error, "B100") ? "-1000\r\n" : "Backlight set to 100 %\r\n");
}

static void ambient_setup_steps(void) {
	add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
	add_owned("D112\r", "0000,1,3\r\n");
	add_owned("D116\r", "0000,8,Test receiver,Primary,Illuminance,Irradiance\r\n");
}

/* The same scripted exposure, with Table 9's ambient codes and English fc.
 * The source actor's spectrum and photometric integral are unchanged. */
static void ambient_capture_steps(int model, int spectral, int units, double sample,
                                  double power_factor, double xyz_factor) {
	static char ambient_xyz[256];
	double y = constructed_y(model, sample) * (units ? 1.0 : 0.09290304) * xyz_factor;
	int i, first = transport.count;
	capture_steps(model, spectral, units, 12, 112, sample, power_factor, xyz_factor, NULL);
	snprintf(ambient_xyz, sizeof(ambient_xyz), "0000,112,%.16g,%.16g,%.16g\r\n", 1.25 * y, y, -0.125 * y);
	for (i = first; i < transport.count; i++)
		if (!strcmp(transport.steps[i].command, spectral ? "D2\r" : "M2\r"))
			transport.steps[i].reply = ambient_xyz;
}

static icom_type selected_port_type(icoms *p) { (void)p; return icomt_serial; }
static int selected_open(icoms *p, flow_control fc, baud_rate br, parity pa, stop_bits sb, word_length wl) {
	if (fc != fc_None || br != baud_115200 || pa != parity_none || sb != stop_1 || wl != length_8) abort();
	p->is_open = 1; opened++; return ICOM_OK;
}
static void selected_close(icoms *p) { if (p->is_open) { p->is_open = 0; closed++; } }
static void selected_delete(icoms *p) {
	selected_close(p); deleted++; free(p); reset_owned();
#ifdef PR655_SPOTREAD_TEST
	fprintf(stderr, "Synthetic PR transport: opens=%d closes=%d deletes=%d\n", opened, closed, deleted);
	fprintf(stderr, "Synthetic PR wire:%s\n", transport.wire);
#endif
}

icoms *new_icoms(icompath *path, a1log *log) {
	icoms *p;
	int model = path->dtype == instPR655 ? 655 : path->dtype == instPR670 ? 670 : 0;
	if (!model || strcmp(path->name, "synthetic-only")) abort();
#ifdef PR655_SPOTREAD_TEST
	{
		const char *scenario = getenv("PR655_TEST_CASE");
		reset(model, 64); connect_steps(model); reset_owned();
		full_setup(1, 2, 1, 0, 75, 0, 0, 8, -1);
		setup_steps();
		capture_steps(model, 1, 1, 0, 0, 1e-4, scenario && !strcmp(scenario, "scale") ? 1000.0 : 1.0,
		              1.0, NULL);
		setup_steps(); add("Q", "");
	}
#endif
	p = calloc(1, sizeof(*p)); if (!p) abort();
	p->log = log; p->dtype = path->dtype; p->dctype = path->dctype;
	p->name = path->name; p->icntx = &transport;
	p->port_type = selected_port_type; p->set_ser_port = selected_open;
	p->read = fake_read; p->write = fake_write;
	p->close_port = selected_close; p->del = selected_delete;
	return p;
}

icompaths *new_icompaths(a1log *log) {
	(void)log; abort(); /* Explicit PR selection must never enter discovery. */
}
char *baud_rate_to_str(baud_rate br) { (void)br; return "synthetic"; }

#ifndef PR655_SPOTREAD_TEST
static inst *driver_with_setup(int model, int exposure_ms, int extended, int cycles) {
	icompath path;
	inst *p;
	reset(model, 7); connect_steps(model); reset_owned();
	full_setup(1, 2, cycles, 0, 75, exposure_ms, extended, 8, -1);
	opened = closed = deleted = 0;
	memset(&path, 0, sizeof(path));
	path.dtype = model == 655 ? instPR655 : instPR670;
	path.name = path.spath = "synthetic-only";
	path.dctype = icomt_serial | icomt_instrument;
	p = new_inst(&path, 1, g_log, NULL, NULL);
	CHECK(p != NULL && transport.writes == 0 && opened == 0);
	if (!p) abort();
	((pr655 *)p)->engine.now = test_now; ((pr655 *)p)->engine.sleep = test_sleep;
	CHECK(p->init_coms(p, baud_38400, fc_nc, 10.0) == inst_ok);
	CHECK(p->init_inst(p) == inst_ok);
	CHECK(p->get_set_opt(p, inst_opt_trig_prog) == inst_ok);
	CHECK(!strcmp(p->get_serial_no(p), "SYNTHETIC-P7"));
	return p;
}

static inst *driver(int model) { return driver_with_setup(model, 0, 0, 1); }

static int unlocked(inst *p) {
	if (amutex_trylock(((pr655 *)p)->lock) != 0) return 0;
	amutex_unlock(((pr655 *)p)->lock); return 1;
}

static void finish(inst *p) {
	if (((pr655 *)p)->engine.state == pr655_ready) add("Q", "");
	CHECK(unlocked(p)); p->del(p);
	CHECK(opened == 1 && closed == 1 && deleted == 1);
}

static void success_and_units(void) {
	int model, spectral, units, code;
	inst *p;
	pr655 *c;
	ipatch out;
	double y;
	for (model = 655; model <= 670; model += 15) {
		for (spectral = 0; spectral <= 1; spectral++) {
			for (units = 0; units <= 1; units++) {
				for (code = 0; code <= 111; code += 111) {
					p = driver(model); c = (pr655 *)p;
					full_setup(units, 2, 1, 0, 75, 0, 0, 8, -1); setup_steps();
					capture_steps(model, spectral, units, code, code, 1e-4, 1.0, 1.0, NULL); setup_steps();
					CHECK(p->set_mode(p, inst_mode_emis_spot | (spectral ? inst_mode_spectral : inst_mode_colorimeter)) == inst_ok);
					CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
					y = constructed_y(model, 1e-4);
					CHECK(out.XYZ_v && fabs(out.XYZ[0] - 1.25 * y) < 1e-10 && fabs(out.XYZ[2] + 0.125 * y) < 1e-10);
					CHECK(fabs(out.XYZ[1] - y) < 1e-10 && c->capture.valid);
					CHECK(out.sp.spec_n == (spectral ? model == 655 ? 101 : 201 : 0));
					if (spectral) {
						CHECK(out.sp.norm == 1.0 && fabs(out.sp.spec[0] - 0.1) < 1e-12);
						CHECK(c->capture.unit.verdict == PR655_UNIT_CONSISTENT);
						CHECK(c->capture.unit.computed == (PR655_CHECK_POWER | PR655_CHECK_Y));
					}
					finish(p);
				}
			}
		}
	}
}

static void refused_units_and_dim(void) {
	static const double factors[] = {1000.0, 2.0, 4.0};
	static const char *names[] = {"1000", "2", "4"};
	inst *p;
	pr655 *c;
	ipatch out;
	inst_code result;
	int i;
	for (i = 0; i < 3; i++) {
		p = driver(i == 1 ? 670 : 655); c = (pr655 *)p; setup_steps();
		capture_steps(i == 1 ? 670 : 655, 1, 1, 0, 0, 1e-4, factors[i], 1.0, NULL); setup_steps();
		CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
		result = p->read_sample(p, "", &out, instNoClamp);
		CHECK((result & inst_imask) == PR655_UNIT_MISMATCH && !out.XYZ_v && !out.sp.spec_n);
		CHECK(c->capture.native.spectrum_parsed && c->capture.native.xyz_parsed && !c->capture.valid);
		CHECK(strstr(c->capture.problem, names[i]) != NULL && c->capture.unit.verdict == PR655_UNIT_REFUSED);
		finish(p);
	}
	p = driver(655); c = (pr655 *)p; setup_steps();
	capture_steps(655, 1, 1, 0, 0, 1e-4, 1.0, 1.0 / fl_factor, NULL); setup_steps();
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == PR655_UNIT_MISMATCH);
	CHECK(strstr(c->capture.problem, "fL-to-cd-m2") != NULL); finish(p);
	p = driver(655); c = (pr655 *)p; setup_steps();
	capture_steps(655, 1, 1, 0, 0, 1e-10, 1.0, 1.0, NULL); setup_steps();
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
	CHECK(c->capture.unit.verdict == PR655_UNIT_INCONCLUSIVE && out.sp.spec_n == 101); finish(p);
	/* Ambient's codes under radiance geometry, one wrong field at a time so
	 * that neither the M2/D2 nor the M5 check can stand in for the other. */
	for (i = 0; i < 3; i++) {
		static const int spectrum_codes[] = {12, 11, 0}, xyz_codes[] = {111, 112, 112};
		static const char *problems[] = {"M5 units code 12", "M2/D2 units code 112", "M2/D2 units code 112"};
		p = driver(655); c = (pr655 *)p; setup_steps();
		capture_steps(655, i < 2, 1, spectrum_codes[i], xyz_codes[i], 1e-4, 1.0, 1.0, NULL); setup_steps();
		CHECK(p->set_mode(p, inst_mode_emis_spot | (i < 2 ? inst_mode_spectral : inst_mode_colorimeter)) == inst_ok);
		CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == PR655_GEOMETRY_ERROR);
		CHECK(!out.XYZ_v && !out.sp.spec_n && strstr(c->capture.problem, problems[i]) != NULL);
		CHECK(c->capture.native.xyz_parsed && c->capture.native.spectrum_parsed == (i < 2)); finish(p);
	}
}

static void admission_and_retention(void) {
	inst *p;
	pr655 *c;
	ipatch out;
	int scenario, before;
	for (scenario = 0; scenario < 4; scenario++) {
		p = driver(655); c = (pr655 *)p;
		full_setup(1, scenario == 0 ? 10 : 2, 1, 0, 75, 0, 0,
		           scenario == 1 ? 42 : 8, scenario == 3 ? 9 : -1);
		add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
		add_owned("D112\r", scenario == 3 ? "0000,2,3\r\n" : "0000,1,3\r\n");
		add_owned("D116\r", scenario == 2 ? "0000,8,Test lens,Primary,Illuminance,Irradiance\r\n"
		                  : scenario == 3 ? "0000,8,Test lens,Primary,Luminance,Radiance\r\n0000,9,Test addon,Addon,Illuminance,Irradiance\r\n"
		                  : "0000,8,Test lens,Primary,Luminance,Radiance\r\n");
		before = transport.writes;
		CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == PR655_GEOMETRY_ERROR);
		CHECK(!out.XYZ_v && !c->capture.native.exposure_started && transport.writes > before);
		CHECK(strstr(transport.wire, "B00") == NULL && strstr(transport.wire, "M2") == NULL); finish(p);
	}
	for (scenario = 0; scenario < 2; scenario++) {
		p = driver(655); c = (pr655 *)p; setup_steps();
		capture_steps(655, 1, 1, 0, 0, 1e-4, 1.0, 1.0, scenario ? "B100" : "D2");
		CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
		CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok);
		CHECK(!out.XYZ_v && !out.sp.spec_n && c->capture.native.spectrum_parsed);
		CHECK(c->capture.native.spectrum_raw.length > 0 && !c->capture.valid);
		CHECK(c->capture.native.xyz_parsed == scenario); finish(p);
	}
}

static void options_and_preflight(void) {
	inst *p = driver(670);
	pr655 *c = (pr655 *)p;
	ipatch out;
	inst_mode capabilities;
	inst2_capability c2;
	inst3_capability c3;
	int before = transport.writes;
	p->capabilities(p, &capabilities, &c2, &c3);
	CHECK((capabilities & inst_mode_spectral) && (c2 & inst2_set_refresh_rate) && (c3 & inst3_average));
	CHECK(p->check_mode(p, inst_mode_ref_spot) == inst_unsupported);
	CHECK(p->get_set_opt(p, inst_opt_set_averages, 0) == inst_ok && c->averages == 0);
	CHECK(p->get_set_opt(p, inst_opt_set_averages, -1) == inst_bad_parameter);
	CHECK(p->get_set_opt(p, inst_opt_set_averages, 100) == inst_bad_parameter);
	CHECK(p->set_refr_rate(p, 0.0) == inst_ok && c->refresh_hz == 0.0);
	CHECK(p->set_refr_rate(p, 59.94) == inst_bad_parameter);
	CHECK(p->set_refr_rate(p, 19.0) == inst_bad_parameter);
	CHECK(p->set_refr_rate(p, 151.0) == inst_bad_parameter);
	CHECK(transport.writes == before);
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_emis_nonadaptive) == inst_wrong_setup);
	CHECK(transport.writes == before);
	CHECK(strstr(transport.wire, "SN") == NULL && strstr(transport.wire, "M2") == NULL); finish(p);
	p = driver_with_setup(670, 30000, 1, 15); c = (pr655 *)p;
	full_setup(1, 2, 15, 3, 75, 30000, 1, 8, -1); setup_steps();
	capture_steps(670, 0, 1, 0, 0, 1e-4, 1.0, 1.0, NULL); setup_steps();
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_emis_nonadaptive) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
	CHECK(c->capture.budget_s >= 900.0 && c->capture.before.exposure_ms == 30000);
	CHECK(strstr(transport.wire, "SE") == NULL && strstr(transport.wire, "SN0") == NULL && strstr(transport.wire, "SK0") == NULL);
	finish(p);
	/* Adaptive (no nonadaptive bit) refuses a reported Fixed exposure before any
	 * setter or exposure, and does not rewrite the operator's SE setting. */
	p = driver_with_setup(655, 200, 0, 1); c = (pr655 *)p;
	setup_steps();
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == PR655_BAD_SETUP);
	CHECK(strstr(c->capture.problem, "adaptive mode requires a reported Adaptive exposure") != NULL);
	CHECK(!out.XYZ_v && c->engine.state == pr655_ready);
	CHECK(strstr(transport.wire, "M5") == NULL && strstr(transport.wire, "SE") == NULL
	   && strstr(transport.wire, "B00") == NULL);
	finish(p);
}

static void setter_and_context_tests(void) {
	inst *p = driver(655);
	pr655 *c = (pr655 *)p;
	ipatch out;
	setup_steps();
	add_owned("SN3\r", "0000\r\n");
	full_setup(1, 2, 3, 0, 75, 0, 0, 8, -1);
	add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
	add_owned("SK120\r", "0000\r\n");
	full_setup(1, 2, 3, 0, 120, 0, 0, 8, -1);
	add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
	add_owned("SS3\r", "0000\r\n");
	full_setup(1, 2, 3, 3, 120, 0, 0, 8, -1);
	add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
	capture_steps(655, 0, 1, 0, 0, 1e-4, 1.0, 1.0, NULL); setup_steps();
	CHECK(p->get_set_opt(p, inst_opt_set_averages, 3) == inst_ok);
	CHECK(p->set_refr_rate(p, 120.0) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
	CHECK(c->capture.before.cycles == 3 && c->capture.before.sync_mode == 3 && c->capture.before.frequency_hz == 120.0);
	CHECK(strstr(transport.wire, "SN3\r") && strstr(transport.wire, "SK120\r") && strstr(transport.wire, "SS3\r")); finish(p);
	/* Admission must be checked again after setter readback, before M. */
	p = driver(655); setup_steps(); add_owned("SN3\r", "0000\r\n");
	full_setup(1, 10, 3, 0, 75, 0, 0, 8, -1);
	add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
	CHECK(p->get_set_opt(p, inst_opt_set_averages, 3) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == PR655_CAPTURE_MISMATCH);
	CHECK(strstr(transport.wire, "B00") == NULL && strstr(transport.wire, "M2") == NULL); finish(p);
	/* A setter acknowledgment does not replace requested-value readback. */
	p = driver(655); setup_steps(); add_owned("SN3\r", "0000\r\n");
	add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
	CHECK(p->get_set_opt(p, inst_opt_set_averages, 3) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == PR655_READBACK_ERROR);
	CHECK(strstr(transport.wire, "M2") == NULL); finish(p);
	/* Explicit native frequency is material context even at equal sync mode. */
	p = driver(655);
	full_setup(1, 2, 1, 3, 75, 0, 0, 8, -1); setup_steps();
	capture_steps(655, 0, 1, 0, 0, 1e-4, 1.0, 1.0, NULL);
	full_setup(1, 2, 1, 3, 120, 0, 0, 8, -1); setup_steps();
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == PR655_CAPTURE_MISMATCH);
	CHECK(((pr655 *)p)->capture.native.xyz_parsed && !out.XYZ_v); finish(p);
}

static void collateral_setter_tests(void) {
	inst *p;
	ipatch out;
	int scenario;
	for (scenario = 0; scenario < 4; scenario++) {
		p = driver(655); setup_steps();
		if (scenario < 2) {
			CHECK(p->get_set_opt(p, inst_opt_set_averages, 3) == inst_ok);
			CHECK(p->set_refr_rate(p, 120.0) == inst_ok);
			add_owned("SN3\r", "0000\r\n");
			full_setup(1, 2, 3, scenario == 0 ? 1 : 0, scenario == 1 ? 120 : 75, 0, 0, 8, -1);
		} else if (scenario == 2) {
			CHECK(p->set_refr_rate(p, 120.0) == inst_ok);
			add_owned("SK120\r", "0000\r\n");
			full_setup(1, 2, 3, 0, 120, 0, 0, 8, -1);
		} else {
			CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_emis_refresh_ovd) == inst_ok);
			add_owned("SS1\r", "0000\r\n");
			full_setup(1, 2, 1, 1, 120, 0, 0, 8, -1);
		}
		add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
		CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == PR655_CAPTURE_MISMATCH);
		CHECK(!out.XYZ_v && !((pr655 *)p)->capture.native.exposure_started);
		CHECK(strstr(transport.wire, "B00") == NULL && strstr(transport.wire, "M2") == NULL);
		if (scenario < 2) CHECK(strstr(transport.wire, "SK120") == NULL && strstr(transport.wire, "SS") == NULL);
		if (scenario == 2) CHECK(strstr(transport.wire, "SS3") == NULL);
		finish(p);
	}
}

static void repeated_initialization_test(void) {
	inst *p = driver(655);
	pr655 *c = (pr655 *)p;
	int writes = transport.writes;
	CHECK((p->init_coms(p, baud_115200, fc_nc, 10.0) & inst_imask) == PR655_ALREADY_OPEN);
	CHECK(strstr(p->interp_error(p, PR655_ALREADY_OPEN), "already open") != NULL);
	/* spotread passes the whole inst_code, not only its driver bits. */
	CHECK(strstr(p->interp_error(p, inst_wrong_setup | PR655_ALREADY_OPEN), "already open") != NULL);
	CHECK(opened == 1 && transport.writes == writes && p->gotcoms && p->inited);
	add_owned("D110\r", "0000\r\n");
	CHECK(p->init_inst(p) != inst_ok && !c->engine.identity_valid);
	CHECK(!strcmp(p->get_serial_no(p), ""));
	writes = transport.writes;
	CHECK((p->init_coms(p, baud_115200, fc_nc, 10.0) & inst_imask) == PR655_UNCERTAIN);
	CHECK(opened == 1 && transport.writes == writes);
	finish(p);
}

static void padded_backlight_tests(void) {
	inst *p;
	ipatch out;
	int i, first;
	for (i = 0; i < 2; i++) {
		p = driver(655); setup_steps(); first = transport.count;
		capture_steps(655, 0, 1, 0, 0, 1e-4, 1.0, 1.0, NULL); setup_steps();
		transport.steps[first + (i ? 2 : 0)].reply = i ? "Backlight set to 0100 %\r\n" : "Backlight set to 00 %\r\n";
		CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
		CHECK(out.XYZ_v && ((pr655 *)p)->capture.native.xyz_parsed);
		CHECK(strstr(i ? ((pr655 *)p)->capture.native.backlight_after_raw
		              : ((pr655 *)p)->capture.native.backlight_before_raw,
		              i ? "0100 %" : "00 %") != NULL);
		finish(p);
	}
}

typedef struct { inst *driver; int nested; } callback_read;
static inst_code nested_read_callback(void *context, inst_ui_purp purpose) {
	callback_read *state = (callback_read *)context;
	ipatch out;
	if (purpose != inst_armed || state->nested) return inst_ok;
	state->nested = 1;
	CHECK(state->driver->get_set_opt(state->driver, inst_opt_trig_prog) == inst_ok);
	CHECK(state->driver->read_sample(state->driver, "", &out, instNoClamp) == inst_ok && out.XYZ_v);
	return inst_user_trig;
}

static void callback_capture_test(void) {
	inst *p = driver(655);
	callback_read callback = {p, 0};
	ipatch out;
	setup_steps(); capture_steps(655, 0, 1, 0, 0, 1e-4, 1.0, 1.0, NULL); setup_steps();
	full_setup(1, 10, 1, 0, 75, 0, 0, 8, -1); setup_steps();
	p->uicallback = nested_read_callback; p->uic_cntx = &callback;
	CHECK(p->get_set_opt(p, inst_opt_trig_user) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == PR655_GEOMETRY_ERROR);
	CHECK(callback.nested && !out.XYZ_v && !((pr655 *)p)->capture.valid);
	CHECK(!((pr655 *)p)->capture.native.xyz_parsed && ((pr655 *)p)->capture.unit.computed == 0);
	finish(p);
}

/* A complete weak-light reply refuses that reading only. The same instance
 * reads again and still closes with Q. */
static void native_rejection_keeps_link(void) {
	inst *p = driver(655);
	pr655 *c = (pr655 *)p;
	ipatch out;
	inst_code result;
	setup_steps();
	add_owned("B00\r", "Backlight set to 0 %\r\n"); add_owned("M5\r", "-8\r\n");
	add_owned("B100\r", "Backlight set to 100 %\r\n");
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	result = p->read_sample(p, "", &out, instNoClamp);
	CHECK((result & inst_mask) == inst_misread && (result & inst_imask) == PR655_NATIVE_ERROR_BASE + 8);
	CHECK(strstr(p->interp_error(p, result & inst_imask), "native error -8") != NULL);
	CHECK(strstr(p->interp_error(p, result), "native error -8") != NULL);
	CHECK(strstr(p->interp_error(p, inst_coms_fail | PR655_TIMEOUT), "deadline expired") != NULL);
	CHECK(!out.XYZ_v && !c->capture.valid && c->engine.state == pr655_ready);
	CHECK(c->capture.native.backlight_after_error == PR655_OK);
	setup_steps(); capture_steps(655, 1, 1, 0, 0, 1e-4, 1.0, 1.0, NULL); setup_steps();
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.XYZ_v && c->capture.valid);
	CHECK(transport.next == transport.count);
	finish(p);
}

/* Only native D2 luminance can disclose the spectral scale. A reading whose
 * power clears its floor but whose luminance does not stays inconclusive,
 * while scale errors that keep M5 self-consistent are caught through D2. */
static void scale_needs_luminance(void) {
	static const double factors[] = {0.25, 0.5, 0.001};
	static const char *names[] = {"4", "2", "1000"};
	inst *p;
	pr655 *c;
	ipatch out;
	int i;
	p = driver(655); c = (pr655 *)p; setup_steps();
	capture_steps(655, 1, 1, 0, 0, 1e-6, 1.0, 1.0, NULL); setup_steps();
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.sp.spec_n == 101);
	CHECK(c->capture.unit.discriminating == PR655_CHECK_POWER);
	CHECK(c->capture.unit.computed == PR655_CHECK_POWER);
	CHECK(c->capture.unit.verdict == PR655_UNIT_INCONCLUSIVE); finish(p);
	for (i = 0; i < 3; i++) {
		p = driver(i == 1 ? 670 : 655); c = (pr655 *)p; setup_steps();
		capture_steps(i == 1 ? 670 : 655, 1, 1, 0, 0, 1e-4, 1.0, factors[i], NULL); setup_steps();
		CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
		CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == PR655_UNIT_MISMATCH);
		CHECK(c->capture.unit.computed == PR655_CHECK_POWER && fabs(c->capture.unit.power_ratio - 1.0) < 1e-12);
		CHECK(strstr(c->capture.problem, "luminance") != NULL && strstr(c->capture.problem, names[i]) != NULL);
		CHECK(!out.XYZ_v && !out.sp.spec_n); finish(p);
	}
}

static void ambient_readings(void) {
	int model, units, spectral, scenario;
	for (model = 655; model <= 670; model += 15) {
		for (units = 0; units <= 1; units++) for (spectral = 0; spectral <= 1; spectral++) {
			inst *p = driver(model); pr655 *c = (pr655 *)p;
			ipatch out; inst_mode caps = 0;
			p->capabilities(p, &caps, NULL, NULL);
			CHECK(IMODETST(caps, inst_mode_emis_ambient));
			full_setup(units, 2, 1, 0, 75, 0, 0, 8, -1); ambient_setup_steps();
			ambient_capture_steps(model, spectral, units, 1e-4, 1, 1); ambient_setup_steps();
			CHECK(p->set_mode(p, inst_mode_emis_ambient | (spectral ? inst_mode_spectral : inst_mode_colorimeter)) == inst_ok);
			CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.XYZ_v && out.mtype == inst_mrt_ambient);
			CHECK(fabs(out.XYZ[1] - constructed_y(model, 1e-4)) < 1e-11);
			CHECK(fabs(c->capture.unit.xyz_scale - (units ? 1 : 1.0 / 0.09290304)) < 1e-12);
			CHECK(out.XYZ[2] < 0 && c->capture.native.xyz[2] < 0);
			CHECK(out.sp.spec_n == (spectral ? model == 655 ? 101 : 201 : 0));
			if (spectral) {
				CHECK(out.sp.spec[0] == 0.1 && c->capture.native.spectrum[0] == 1e-4);
				CHECK(c->capture.unit.verdict == PR655_UNIT_CONSISTENT);
			}
			CHECK(transport.next == transport.count);
			CHECK((strstr(transport.wire, "M2\r") != NULL) == !spectral);
			CHECK((strstr(transport.wire, "M5\r") != NULL) == spectral);
			finish(p);
		}
		/* Unit contradictions and self-consistent radiometric scale mistakes
		 * keep their original vectors but cannot become an ambient ipatch. */
		for (scenario = 0; scenario < 4; scenario++) {
			inst *p = driver(model); pr655 *c = (pr655 *)p; ipatch out; int i;
			full_setup(1, 2, 1, 0, 75, 0, 0, 8, -1); ambient_setup_steps();
			ambient_capture_steps(model, 1, 1, 1e-4, scenario == 3 ? 1000 : 1, scenario == 2 ? 1000 : 1);
			for (i = 0; i < transport.count; i++) {
				if (scenario == 0 && !strcmp(transport.steps[i].command, "M5\r")) {
					char *code = strchr((char *)transport.steps[i].reply, ',');
					if (!code || strncmp(code, ",12,", 4)) abort();
					code[2] = '1';
				}
				if (scenario == 1 && !strcmp(transport.steps[i].command, "D2\r"))
					transport.steps[i].reply = "0000,111,1,1,1\r\n";
			}
			ambient_setup_steps();
			CHECK(p->set_mode(p, inst_mode_emis_ambient | inst_mode_spectral) == inst_ok);
			CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask)
			      == (scenario < 2 ? PR655_GEOMETRY_ERROR : PR655_UNIT_MISMATCH));
			CHECK(!out.XYZ_v && !out.sp.spec_n && c->capture.native.spectrum_parsed && c->capture.native.xyz_parsed);
			finish(p);
		}
		{
			inst *p = driver(model); pr655 *c = (pr655 *)p; ipatch out;
			full_setup(1, 2, 1, 0, 75, 0, 0, 8, -1); ambient_setup_steps();
			ambient_capture_steps(model, 1, 1, 1e-12, 1, 1); ambient_setup_steps();
			CHECK(p->set_mode(p, inst_mode_emis_ambient | inst_mode_spectral) == inst_ok);
			CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.mtype == inst_mrt_ambient);
			CHECK(c->capture.unit.verdict == PR655_UNIT_INCONCLUSIVE && c->capture.native.spectrum[0] == 1e-12);
			finish(p);
		}
		{
			inst *p = driver(model); pr655 *c = (pr655 *)p; ipatch out;
			full_setup(1, 2, 1, 0, 75, 200, 0, 8, -1); ambient_setup_steps();
			ambient_capture_steps(model, 1, 1, 1e-4, 1, 1); ambient_setup_steps();
			CHECK(p->set_mode(p, inst_mode_emis_ambient | inst_mode_spectral) == inst_ok);
			CHECK(p->check_mode(p, inst_mode_emis_ambient | inst_mode_emis_nonadaptive) == inst_unsupported);
			CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.mtype == inst_mrt_ambient);
			CHECK(c->capture.before.exposure_ms == 200 && !c->capture.before.adaptive);
			CHECK(c->capture.budget_s >= 300 && strstr(transport.wire, "SE") == NULL);
			finish(p);
		}
	}
	for (scenario = 0; scenario < 3; scenario++) {
		inst *p = driver(655); pr655 *c = (pr655 *)p; ipatch out;
		full_setup(1, scenario == 0 ? 10 : 2, 1, 0, 75, 0, 0, 8, scenario == 2 ? 9 : -1);
		if (scenario == 1) setup_steps();
		else if (scenario == 2) {
			add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
			add_owned("D112\r", "0000,2,3\r\n");
			add_owned("D116\r", "0000,8,Test receiver,Primary,Illuminance,Irradiance\r\n0000,9,Wrong addon,Addon,Luminance,Radiance\r\n");
		} else ambient_setup_steps();
		CHECK(p->set_mode(p, inst_mode_emis_ambient | inst_mode_spectral) == inst_ok);
		CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == PR655_GEOMETRY_ERROR);
		CHECK(!out.XYZ_v && !c->capture.native.exposure_started && strstr(transport.wire, "M5\r") == NULL);
		finish(p);
	}
}

int uimain(void) {
	success_and_units(); refused_units_and_dim(); admission_and_retention(); options_and_preflight();
	setter_and_context_tests();
	collateral_setter_tests(); repeated_initialization_test();
	padded_backlight_tests();
	callback_capture_test();
	native_rejection_keeps_link(); scale_needs_luminance();
	ambient_readings();
	printf("PR adapter: %d checks, %d failures (actual inst/vtable, synthetic icoms)\n", checks, failures);
	return failures ? 1 : 0;
}
#endif
