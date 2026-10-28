/*
 * Argyll Color Management System
 *
 * A scripted Colorimetry Research instrument for synthetic tests
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
 * Every reply here is invented from the manual's documented shapes.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "cr250_fake.h"

cr250_fake cr250_fake_state;
#define F cr250_fake_state

static const int fragment = 7;			/* Bytes per read: replies arrive in pieces */
static char queued[16384];
static int queued_n;

/* Settings and modes reported by RS and RM. SM changes the first eight, the
 * writable settings, as it would on an instrument. Mode and RangeMode are only
 * reported: the driver never writes them, so an SM of either aborts. */
static const char *const setting_keys[] = { "ExposureMode", "ExposureX", "SyncMode", "SyncFreq",
	"UserCalibMode", "CMF", "Matrix", "Match", "Mode", "RangeMode", NULL };
#define WRITABLE_SETTINGS 8
static char setting[10][32];

static int writable_setting(const char *key) {
	int i;
	for (i = 0; i < WRITABLE_SETTINGS; i++) if (!strcmp(key, setting_keys[i])) return 1;
	return 0;
}

char *cr250_fake_setting(const char *key) {
	int i;
	for (i = 0; setting_keys[i]; i++) if (!strcmp(key, setting_keys[i])) return setting[i];
	return NULL;
}
void cr250_fake_set_setting(const char *key, const char *value) {
	char *slot = cr250_fake_setting(key);
	if (slot == NULL || value == NULL) abort();
	snprintf(slot, 32, "%s", value);
}

/* RC lists the instrument reports; SM of a listed key takes an ID from these. */
static const struct { const char *key, *list; } lists[] = {
	{ "ExposureMode", "2\r\n0,Auto\r\n1,Fixed" },
	{ "SyncMode", "6\r\n0,None\r\n1,Auto\r\n2,Manual\r\n3,NTSC\r\n4,PAL\r\n5,CINEMA" },
	{ "UserCalibMode", "3\r\n0,None\r\n1,Matrix\r\n2,Match" },
	{ "Matrix", "2\r\n0,Display-A\r\n7,Display-B" },
	{ "Match", "2\r\n0,Reference-A\r\n9,Reference-B" },
	{ "RangeMode", "2\r\n0,Auto\r\n1,Fixed" }, { "Range", "4\r\n0,A\r\n1,B\r\n2,C\r\n3,D" },
	{ "Mode", "3\r\n0,Colorimeter\r\n1,Flicker\r\n2,Response Time" },
	{ "Speed", "4\r\n0,Slow\r\n1,Normal\r\n2,Fast\r\n3,2x Fast" },
	{ "Accessory", "1\r\n0,Standard,Radiance" }, { "Aperture", "1\r\n0,1 deg" },
	{ "Filter", "3\r\n3,ND-100-1,Radiance\r\n4,ND-100-2,Radiance\r\n5,ND-100-3,Radiance" }
};
static const char *list_name(const char *key, const char *id) {
	static char name[32];
	const char *s = NULL, *e;
	char needle[16];
	size_t i;
	for (i = 0; i < sizeof(lists)/sizeof(lists[0]); i++) if (!strcmp(lists[i].key, key)) s = lists[i].list;
	if (!s) abort();
	snprintf(needle, sizeof(needle), "\n%s,", id);
	if ((s = strstr(s, needle)) == NULL) abort();
	s += strlen(needle);
	e = strchr(s, '\r'); if (!e) e = s + strlen(s);
	snprintf(name, sizeof(name), "%.*s", (int)(e - s), s);
	return name;
}

/* A synthetic spectrum, 380-780 nm at 5 nm in W/(sr.m^2.nm): three lines whose
 * amplitudes are solved so its CIE 1931 XYZ is the native XYZ reported, and
 * RM Radiometric is its own power and photon rate. So a correct driver sees
 * an instrument that agrees with itself. sp_level scales all of them together
 * (a dimmer light); sp_scale and sp_shift_nm corrupt only what RM Spectrum
 * sends; sp_line replaces it with one narrow line (the XYZ then disagrees,
 * but the conversion is visible in capture.unit).
 * Tails are cut at 1e-30: an instrument prints no subnormal values, which the
 * driver's parser refuses as underflow. */
static double gauss(double wl, double c, double sd) {
	double g = exp(-0.5 * (wl - c) * (wl - c) / (sd * sd));
	return g < 1e-30 ? 0.0 : g;
}
static void spectrum_xyz(const double *w, double xyz[3]) {
	xspect sp;
	xsp2cie *conv;
	int i;
	memset(&sp, 0, sizeof(sp));
	sp.spec_n = CR250_FAKE_SP_N; sp.spec_wl_short = 380; sp.spec_wl_long = 780; sp.norm = 1.0;
	for (i = 0; i < CR250_FAKE_SP_N; i++) sp.spec[i] = 1000.0 * w[i];
	conv = new_xsp2cie(icxIT_none, 0.0, NULL, icxOT_CIE_1931_2, NULL, icSigXYZData, icxNoClamp);
	if (!conv) abort();
	conv->set_int_steps(conv, 1.0, 380, 780);
	conv->convert(conv, xyz, &sp);
	conv->del(conv);
}
static double det3(double m[3][3]) {
	return m[0][0]*(m[1][1]*m[2][2]-m[1][2]*m[2][1]) - m[0][1]*(m[1][0]*m[2][2]-m[1][2]*m[2][0])
	     + m[0][2]*(m[1][0]*m[2][1]-m[1][1]*m[2][0]);
}
void cr250_fake_spectrum(void) {
	static const double centre[3] = { 450, 540, 610 }, target[3] = { 12.5, 25, 6.25 };
	double basis[3][CR250_FAKE_SP_N], m[3][3], det, power = 0, photons = 0;
	int i, k;
	for (k = 0; k < 3; k++) {
		double xyz[3];
		for (i = 0; i < CR250_FAKE_SP_N; i++) basis[k][i] = gauss(380 + 5 * i, centre[k], 15);
		spectrum_xyz(basis[k], xyz);
		for (i = 0; i < 3; i++) m[i][k] = xyz[i];
	}
	det = det3(m);
	for (k = 0; k < 3; k++) {				/* Cramer's rule */
		double a[3][3];
		memcpy(a, m, sizeof(a));
		for (i = 0; i < 3; i++) a[i][k] = target[i];
		F.amplitudes[k] = det3(a) / det;
	}
	for (i = 0; i < CR250_FAKE_SP_N; i++) {
		double wl = 380 + 5 * i;
		F.sp_values[i] = F.sp_level * (F.amplitudes[0]*basis[0][i] + F.amplitudes[1]*basis[1][i]
		                             + F.amplitudes[2]*basis[2][i]);
		if (F.sp_line > 0) F.sp_values[i] = 0.05 * gauss(wl, F.sp_line, 5);
		power += F.sp_values[i] * 5;
		photons += F.sp_values[i] * 5 * wl * 1e-9 / (6.62607015e-34 * 299792458.0);
	}
	snprintf(F.xyz, sizeof(F.xyz), "%.6g,%.6g,%.6g", 12.5 * F.sp_level, 25 * F.sp_level, 6.25 * F.sp_level);
	snprintf(F.radiometric, sizeof(F.radiometric), "0,%.6e,%.6e", power, photons);
}

static int spectrum_open;
static unsigned int spectrum_done_ms, late_at;
static int fault(const char *name) { return F.sp_fault && !strcmp(F.sp_fault, name); }
static void queue_spectrum(void) {
	int i, n = CR250_FAKE_SP_N, extra = 0;
	if (fault("NA")) { queued_n = snprintf(queued, sizeof(queued), "OK:0:RM Spectrum:NA\r\n"); return; }
	if (fault("ER")) {
		queued_n = snprintf(queued, sizeof(queued), "ER:-305:Light intensity too low or unmeasurable:RM Spectrum\r\n");
		return;
	}
	queued_n = snprintf(queued, sizeof(queued), "OK:0:RM Spectrum:%.1f,%.1f,5.0,%d\r\n",
	                    380 + F.sp_shift_nm, 780 + F.sp_shift_nm,
	                    fault("grid") ? CR250_FAKE_SP_N - 1 : CR250_FAKE_SP_N);
	if (fault("grid") || fault("short")) n = CR250_FAKE_SP_N - 1;
	if (fault("tiny")) {		/* A consistent grid, too short to integrate */
		queued_n = snprintf(queued, sizeof(queued), "OK:0:RM Spectrum:380.0,390.0,5.0,3\r\n");
		n = 3;
	}
	/* Two values that cancel in the sum and leave the moment and XYZ finite,
	 * but each overflows once scaled to mW */
	if (fault("cancel")) {
		queued_n = snprintf(queued, sizeof(queued), "OK:0:RM Spectrum:200.0,201.9,0.1,20\r\n");
		for (i = 0; i < 20; i++)
			queued_n += snprintf(queued + queued_n, sizeof(queued) - queued_n, "%s\r\n",
			                     i == 5 ? "2.000000e+305" : i == 6 ? "-2.000000e+305" : "0.000000e+00");
		spectrum_open = 1;
		return;
	}
	if (fault("extra")) extra = 1;
	for (i = 0; i < n + extra; i++) {
		if (fault("bad") && i == 7)
			queued_n += snprintf(queued + queued_n, sizeof(queued) - queued_n, "1.234e-03x\r\n");
		else if (fault("huge"))		/* Finite as sent; overflows once scaled to mW */
			queued_n += snprintf(queued + queued_n, sizeof(queued) - queued_n, "1.000000e+307\r\n");
		else if (fault("large"))	/* Finite in mW, summed and weighted; overflows once integrated */
			queued_n += snprintf(queued + queued_n, sizeof(queued) - queued_n, "3.000000e+303\r\n");
		else
			queued_n += snprintf(queued + queued_n, sizeof(queued) - queued_n, "%.6e\r\n",
			                     F.sp_scale * F.sp_values[i < CR250_FAKE_SP_N ? i : 0]);
	}
	spectrum_open = 1;
}

void cr250_fake_reset(void) {
	void (*on_delete)(void) = F.on_delete;
	memset(&F, 0, sizeof(F));
	F.on_delete = on_delete;
	F.model = "CR-250"; F.type = "2"; F.serial = "SYNTHETIC-250"; F.firmware = "1.34.0-S28";
	F.sp_level = F.sp_scale = 1.0;
	queued_n = 0; spectrum_open = 0; spectrum_done_ms = late_at = 0;
	cr250_fake_set_setting("ExposureMode", "Auto"); cr250_fake_set_setting("ExposureX", "1");
	cr250_fake_set_setting("SyncMode", "None"); cr250_fake_set_setting("SyncFreq", "60.00 Hz");
	cr250_fake_set_setting("UserCalibMode", "None"); cr250_fake_set_setting("CMF", "0");
	cr250_fake_set_setting("Matrix", "0"); cr250_fake_set_setting("Match", "0");
	cr250_fake_set_setting("Mode", "Colorimeter"); cr250_fake_set_setting("RangeMode", "Auto");
	cr250_fake_spectrum();
}

unsigned int cr250_fake_now(void) { return F.clock_ms; }
void cr250_fake_sleep(unsigned int ms) { F.clock_ms += ms; }

int cr250_fake_sent(const char *cmd) {
	char needle[80];
	snprintf(needle, sizeof(needle), "\n%s\n", cmd);
	return strstr(F.sent, needle) != NULL;
}
int cr250_fake_sent_prefix(const char *prefix) {
	char needle[80];
	snprintf(needle, sizeof(needle), "\n%s", prefix);
	return strstr(F.sent, needle) != NULL;
}

static void close_fake(icoms *p) { if (p->is_open) F.closes++; p->is_open = 0; }
static void delete_fake(icoms *p) {
	close_fake(p); F.deleted++; free(p);
	if (F.on_delete) F.on_delete();
}
static icom_type port_type(icoms *p) { (void)p; return icomt_serial; }
static int open_fake(icoms *p, flow_control fc, baud_rate br, parity pa, stop_bits sb, word_length wl) {
	if (fc != fc_None || br != baud_115200 || pa != parity_none || sb != stop_1 || wl != length_8) abort();
	F.opened++; p->is_open = 1; queued_n = 0; return ICOM_OK;
}
static const struct { const char *cmd, *value; } answers[] = {
	{ "RC MaxExposure", "500.0 msec" }, { "RC MinExposure", "1.0 msec" },
	{ "RC MinExposureX", "1" }, { "RC MaxExposureX", "50" },
	{ "RC MinSyncFreq", "10.00 Hz" }, { "RC MaxSyncFreq", "10000.00 Hz" },
	{ "Speed", "Normal" },
	/* Remote 1.36 sections 4.3.3.2 and 4.3.1.5 have different reply shapes.
	 * RS adapts the three-slot example to no installed filters; RM uses its
	 * documented no-filter example. Keep these command-specific fixtures. */
	{ "RS Filter", "None,None,None" }, { "RM Filter", "None" },
	{ "Accessory", "Standard" }, { "Aperture", "1 deg" }, { "RS Exposure", "100.000 msec" },
	{ "RS Range", "A" }, { "RM Warnings", "0" }, { "RM MaxAutoExposure", "449.999 msec" },
	{ "RM Range", "A" }, { "RM Time", "NA" }, { "M", "No errors" }
};
static int write_fake(icoms *p, char *buf, int n, double tout) {
	char cmd[64];
	const char *value = NULL;
	char *st;
	size_t i;
	(void)p; (void)tout;
	if (!n) n = (int)strlen(buf);
	if (n < 3 || n >= (int)sizeof(cmd) || buf[n-2] != '\r' || buf[n-1] != '\n' || queued_n) abort();
	memcpy(cmd, buf, n-2); cmd[n-2] = 0;
	F.writes++;
	if (F.guard_checks) {			/* The first request after a spectrum body */
		if (F.clock_ms - spectrum_done_ms < CR250_SPECTRUM_GUARD_MS) F.guard_violations++;
		F.guard_checks = 0;
	}
	if (F.sent_n + n + 2 >= (int)sizeof(F.sent)) abort();
	if (!F.sent_n) F.sent[F.sent_n++] = '\n';
	F.sent_n += snprintf(F.sent + F.sent_n, sizeof(F.sent) - F.sent_n, "%s\n", cmd);
	if (!strcmp(cmd, "M")) {
		F.measures++;
		if (F.timeout_measure) return ICOM_OK;
	}
	if (F.timeout_cmd && !strcmp(cmd, F.timeout_cmd)) return ICOM_OK;
	if (F.native_cmd && !strcmp(cmd, F.native_cmd)) {
		queued_n = snprintf(queued, sizeof(queued), "%s\r\n", F.native_status);
		return ICOM_OK;
	}
	if (!strncmp(cmd, "SM ", 3)) {
		char key[32], arg[32], name[32];
		if (sscanf(cmd + 3, "%31s %31s", key, arg) != 2 || !writable_setting(key)
		 || (st = cr250_fake_setting(key)) == NULL) abort();
		if (!strcmp(key, "ExposureX") || !strcmp(key, "CMF") || !strcmp(key, "Matrix") || !strcmp(key, "Match"))
			snprintf(name, sizeof(name), "%s", arg);
		else if (!strcmp(key, "SyncFreq")) snprintf(name, sizeof(name), "%.2f Hz", atof(arg));
		else snprintf(name, sizeof(name), "%s", list_name(key, arg));
		if (!F.sm_ignored) strcpy(st, name);
		snprintf(F.sm_log + strlen(F.sm_log), sizeof(F.sm_log) - strlen(F.sm_log), " %s;", cmd);
		queued_n = snprintf(queued, sizeof(queued), "OK:0:%s%s:No errors\r\n", F.sm_short_context ? "" : "SM ", key);
		return ICOM_OK;
	}
	if (!strcmp(cmd, "RM Spectrum") && !(F.override_cmd && !strcmp(cmd, F.override_cmd))) {
		queue_spectrum();
		return ICOM_OK;
	}
	if (F.override_cmd && !strcmp(cmd, F.override_cmd)) value = F.override_value;
	else if (F.override2_cmd && !strcmp(cmd, F.override2_cmd)) value = F.override2_value;
	else if (F.override3_cmd && !strcmp(cmd, F.override3_cmd)) value = F.override3_value;
	else if (!strcmp(cmd, "RC Model") || !strcmp(cmd, "RM Model")) value = F.model;
	else if (!strcmp(cmd, "RC ID") || !strcmp(cmd, "RM ID")) value = F.serial;
	else if (!strcmp(cmd, "RC Firmware")) value = F.firmware;
	else if (!strcmp(cmd, "RC InstrumentType")) value = F.type;
	else if (!strcmp(cmd, "RM XYZ")) value = F.xyz;
	else if (!strcmp(cmd, "RM Radiometric")) value = F.radiometric;
	else if (!strcmp(cmd, "RM Exposure"))
		value = !strcmp(cr250_fake_setting("ExposureMode"), "Fixed") ? "100.000 msec" : "111.622 msec";
	else if (!strncmp(cmd, "RC ", 3)) {
		for (i = 0; i < sizeof(lists)/sizeof(lists[0]); i++)
			if (!strcmp(cmd + 3, lists[i].key)) value = lists[i].list;
	}
	if (!value && (!strncmp(cmd, "RS ", 3) || !strncmp(cmd, "RM ", 3)) && (st = cr250_fake_setting(cmd + 3)) != NULL)
		value = st;
	for (i = 0; !value && i < sizeof(answers)/sizeof(answers[0]); i++)
		if (!strcmp(cmd, answers[i].cmd)
		 || ((!strncmp(cmd, "RS ", 3) || !strncmp(cmd, "RM ", 3)) && !strcmp(cmd+3, answers[i].cmd)))
			value = answers[i].value;
	if (!value) { fprintf(stderr, "Unexpected command: %s\n", cmd); abort(); }
	/* An ER line stands for itself (a command the model lacks, for example). */
	if (!strncmp(value, "ER:", 3)) queued_n = snprintf(queued, sizeof(queued), "%s\r\n", value);
	else queued_n = snprintf(queued, sizeof(queued), "OK:0:%s:%s\r\n", cmd, value);
	return ICOM_OK;
}
static int read_fake(icoms *p, char *buf, int size, int *count, char *tc, int ntc, double tout) {
	int n;
	(void)p; (void)tc; (void)ntc;
	if (late_at && F.clock_ms >= late_at) {	/* An uncounted line, 100 ms after the body */
		queued_n += snprintf(queued + queued_n, sizeof(queued) - queued_n, "1.0e-3\r\n");
		late_at = 0;
	}
	n = queued_n;
	if (n > size-1) n = size-1;
	if (n > fragment) n = fragment;
	*count = n;
	if (!n) { F.clock_ms += (unsigned int)ceil(tout * 1000); buf[0] = 0; return ICOM_TO; }
	memcpy(buf, queued, n); buf[n] = 0;
	memmove(queued, queued+n, queued_n-n); queued_n -= n;
	if (spectrum_open && !queued_n) {	/* The body has been taken */
		spectrum_open = 0; spectrum_done_ms = F.clock_ms; F.guard_checks = 1;
		if (fault("late")) late_at = F.clock_ms + 100;
	}
	return ICOM_OK;
}

void cr250_fake_attach(icoms *p) {
	p->port_type = port_type; p->set_ser_port = open_fake;
	p->write = write_fake; p->read = read_fake; p->close_port = close_fake; p->del = delete_fake;
}
