/*
 * Argyll Color Management System
 *
 * Synthetic-only inst and spotread tests for the CR driver. No OS
 * transport is linked; see cr250_driver_test.py.
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
 * Every transcript here is invented from the manual's documented shapes.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "cr250_fake.h"
#include "instappsup.h"

#define F cr250_fake_state
#define ALL_COMPARED (CR250_UNIT_POWER | CR250_UNIT_AXIS | CR250_UNIT_Y | CR250_UNIT_CHROMA)

/* These definitions replace the whole OS icoms implementation at link time.
 * new_inst and spotread themselves are the production source, unmodified. */
#ifdef CR250_SPOTREAD_TEST
static void report_transport(void) {
	fprintf(stderr, "Synthetic transport: M=%d opens=%d closes=%d\n", F.measures, F.opened, F.closes);
	fprintf(stderr, "Synthetic writes:%s\n", F.sm_log);
}
#endif
icoms *new_icoms(icompath *path, a1log *log) {
	icoms *p;
	if ((path->dtype != instCR250 && path->dtype != instCR100 && path->dtype != instCR300)
	 || strcmp(path->name, "synthetic-only")) abort();
#ifdef CR250_SPOTREAD_TEST
	{
		const char *scenario = getenv("CR250_TEST_CASE");
		F.on_delete = report_transport;
		cr250_fake_reset();
		if (path->dtype == instCR100) { F.model = "CR-100"; F.type = "1"; }
		if (path->dtype == instCR300) { F.model = "CR-300"; F.type = "2"; }
		if (scenario && !strcmp(scenario, "setup")) {
			F.override_cmd = "RS UserCalibMode"; F.override_value = "Match";
		} else if (scenario && !strcmp(scenario, "warning")) {
			F.native_cmd = "M"; F.native_status = "OK:100:M:Synchronization failed";
		} else if (scenario && !strcmp(scenario, "firmware")) {
			F.firmware = "1.25";
		} else if (scenario && !strcmp(scenario, "model")) {
			F.override_cmd = "RC Model"; F.override_value = "CR-300";
		} else if (scenario && !strcmp(scenario, "scale1000")) {
			F.sp_scale = 1000;
		} else if (scenario && !strcmp(scenario, "sync-auto")) {
			cr250_fake_set_setting("SyncMode", "Auto");
		} else if (scenario && !strcmp(scenario, "no-sync")) {	/* A CR-250 without SyncMode */
			F.override_cmd = "RS SyncMode"; F.override_value = "ER:-500:Invalid command:SyncMode";
			F.override2_cmd = "RM SyncMode"; F.override2_value = "NA";
		}
	}
#endif
	p = calloc(1, sizeof(*p)); if (!p) abort();
	p->log = log; p->dtype = path->dtype; p->name = path->name; p->dctype = path->dctype;
	cr250_fake_attach(p);
	return p;
}
static icompath path = { .dtype = instUnknown, .name = "synthetic-only", .dctype = icomt_serial | icomt_fastserial, .spath = "synthetic-only" };
static icompath *paths[] = { &path, NULL };
static icompath *get_path(icompaths *p, int n) { (void)p; return n == 1 ? &path : NULL; }
static void del_paths(icompaths *p) { free(p); }
icompaths *new_icompaths(a1log *log) {
#ifdef CR250_SPOTREAD_TEST
	abort(); /* Explicit CR selection must not discover ports. */
#endif
	icompaths *p = calloc(1, sizeof(*p)); if (!p) abort();
	p->log = log; p->paths = paths; p->npaths = 1; p->get_path = get_path; p->del = del_paths;
	return p;
}

/* Used by fast_ser_dev_type's debug log and legacy ser_inst_type in inst.c. */
char *baud_rate_to_str(baud_rate br) { (void)br; return "synthetic"; }

#ifndef CR250_SPOTREAD_TEST
static int checks, failures;
#define CHECK(x) do { checks++; if (!(x)) { failures++; fprintf(stderr,"line %d: %s\n",__LINE__,#x); } } while (0)
static inst *new_driver_of(instType dtype) {
	icompath typed = path;
	inst *p;
	typed.dtype = dtype;
	cr250_fake_reset();
	if (dtype == instCR100) { F.model = "CR-100"; F.type = "1"; }
	if (dtype == instCR300) { F.model = "CR-300"; F.type = "2"; }
	p = new_inst(&typed, 0, g_log, NULL, NULL);
	CHECK(p != NULL);
	((cr250 *)p)->engine.now_ms = cr250_fake_now;
	((cr250 *)p)->engine.sleep_ms = cr250_fake_sleep;
	return p;
}
static inst *new_driver(void) { return new_driver_of(instCR250); }
static inst *make_driver_of(instType dtype) {
	inst *p = new_driver_of(dtype);
	CHECK(p->init_coms(p, baud_38400, fc_nc, 3) == inst_ok);
	CHECK(p->init_inst(p) == inst_ok);
	CHECK(p->get_set_opt(p, inst_opt_trig_prog) == inst_ok);
	return p;
}
static inst *make_driver(void) { return make_driver_of(instCR250); }
/* Every entry point must release the command lock on every return path. */
static int unlocked(inst *p) {
	cr250 *c = (cr250 *)p;
	if (amutex_trylock(c->lock) != 0) return 0;
	amutex_unlock(c->lock);
	return 1;
}

static void rejected(const char *cmd, const char *value, int expected_m) {
	inst *p = make_driver();
	ipatch out;
	memset(&out, 0x7f, sizeof(out));
	F.override_cmd = cmd; F.override_value = value;
	CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok);
	CHECK(!out.XYZ_v && out.sp.spec_n == 0);
	CHECK(F.measures == expected_m);
	CHECK(unlocked(p));
	p->del(p); CHECK(F.deleted == 1 && F.closes == 1);
}
/* A spectral read that must be refused, keeping the link usable afterwards. */
static void spectral_refused(double scale, double shift, const char *fault,
                             inst_code mask, int ec, const char *text) {
	inst *p = make_driver();
	cr250 *c = (cr250 *)p;
	ipatch out;
	inst_code rv;
	F.sp_scale = scale; F.sp_shift_nm = shift; F.sp_fault = fault;
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	rv = p->read_sample(p, "", &out, instNoClamp);
	CHECK((rv & inst_mask) == mask && (ec == 0 || (rv & inst_imask) == ec));
	CHECK(!out.XYZ_v && out.sp.spec_n == 0 && F.measures == 1);
	if (text) CHECK(strstr(p->interp_error(p, rv), text) != NULL);
	CHECK(c->engine.identified && !c->engine.uncertain && unlocked(p));
	if (fault && !strcmp(fault, "tiny"))
		CHECK(c->capture.spectrum.count == 3 && c->capture.unit.verdict == CR250_UNIT_NOT_CHECKED);
	if (shift == 1000.0)
		CHECK(c->capture.spectrum.count == CR250_FAKE_SP_N && c->capture.unit.verdict == CR250_UNIT_NOT_CHECKED);
	F.sp_scale = 1.0; F.sp_shift_nm = 0; F.sp_fault = NULL;
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.sp.spec_n == CR250_FAKE_SP_N);
	p->del(p);
}
/* fast_ser_dev_type (inst.c, production source) over a scripted port that
 * answers "RC Model" only at 115200. Like an instrument given a foreign
 * query, it can answer JETI's "*idn?" with an error line; JETI's read stops
 * at the CR and leaves the LF, which the CR probe's flush-before-write must
 * discard. No other CR protocol command may be sent. */
static baud_rate probe_baud;
static char probe_input[256];
static int probe_input_n, probe_rc_model, probe_rc_elsewhere, probe_other_cr;
static const char *probe_reply, *probe_idn_reply;
static int probe_chunk_min;				/* nz: minimum bytes per read, even past a terminator */
static int probe_delay_ms, probe_reads;		/* "RC Model" answered late; direct reads */
static double probe_read_tout;				/* The last direct read's timeout */
static int probe_set_port(icoms *p, flow_control fc, baud_rate br, parity pa, stop_bits sb,
                          word_length wl, int delayms) {
	(void)p; (void)fc; (void)pa; (void)sb; (void)wl; (void)delayms;
	probe_baud = br; probe_input_n = 0;
	return ICOM_OK;
}
static void probe_queue(const char *s) {
	int n = (int)strlen(s);
	if (probe_input_n + n >= (int)sizeof(probe_input)) abort();
	memcpy(probe_input + probe_input_n, s, n); probe_input_n += n;
}
/* Accumulate OS chunks through the ntc'th terminator, or a byte count. */
static int probe_read(icoms *p, char *buf, int size, int *count, char *tc, int ntc, double tout) {
	int n = 0, seen = 0, se = ICOM_TO;
	(void)p; (void)tout;
	while (n < size - 1 && n < probe_input_n) {
		char ch = probe_input[n++];
		if (tc != NULL ? (ch != 0 && strchr(tc, ch) != NULL && ++seen >= ntc) : n >= ntc)
			se = ICOM_OK;
		/* Real icoms counts terminators after the OS chunk has arrived;
		 * the old mode splits each terminator to exercise CRLF boundaries. */
		if (se == ICOM_OK && (!probe_chunk_min || n >= probe_chunk_min))
			break;
	}
	memcpy(buf, probe_input, n); buf[n] = 0;
	memmove(probe_input, probe_input + n, probe_input_n - n); probe_input_n -= n;
	if (count) *count = n;
	return se;
}
static int probe_direct_read(icoms *p, char *buf, int size, int *count, char *tc, int ntc, double tout) {
	probe_reads++; probe_read_tout = tout;
	return probe_read(p, buf, size, count, tc, ntc, tout);
}
static int probe_write_read(icoms *p, char *wbuf, int nwch, char *rbuf, int bsize, int *bread,
                            char *tc, int ntc, double tout, int frbw) {
	int n = nwch ? nwch : (int)strlen(wbuf);
	if (frbw) probe_input_n = 0;
	if (n == 10 && !memcmp(wbuf, "RC Model\r\n", 10)) {
		if (probe_baud != baud_115200) probe_rc_elsewhere++;
		else {
			probe_rc_model++;
			if (probe_reply) probe_queue(probe_reply);
			if (probe_delay_ms) {
				unsigned int started = msec_time();
				/* Sleep may return before the requested number of coarse Windows
				 * ticks has elapsed. Delay on the clock the probe actually uses. */
				do { msec_sleep(1); }
				while (msec_time() - started < (unsigned int)probe_delay_ms);
			}
		}
	} else if ((n == 3 && !memcmp(wbuf, "M\r\n", 3))
	        || (n > 3 && (!memcmp(wbuf, "RC ", 3) || !memcmp(wbuf, "RS ", 3) || !memcmp(wbuf, "RM ", 3)
	                   || !memcmp(wbuf, "SM ", 3) || !memcmp(wbuf, "SC ", 3)))) {
		probe_other_cr++;
	} else if (probe_baud == baud_115200 && n == 6 && !memcmp(wbuf, "*idn?\r", 6) && probe_idn_reply) {
		probe_queue(probe_idn_reply);
	}
	return probe_read(p, rbuf, bsize, bread, tc, ntc, tout);
}
static instType probe(const char *reply, const char *idn, icom_type dctype) {
	icoms *p = calloc(1, sizeof(*p));
	instType rv;
	if (!p) abort();
	p->log = g_log; p->name = (char *)"synthetic-probe"; p->spath = (char *)"synthetic-probe";
	p->dctype = dctype; p->dtype = instUnknown;
	p->set_ser_port_ex = probe_set_port; p->write_read_ex = probe_write_read; p->read = probe_direct_read;
	probe_reply = reply; probe_idn_reply = idn;
	probe_rc_model = probe_rc_elsewhere = probe_other_cr = probe_input_n = probe_reads = 0;
	rv = fast_ser_dev_type(p, 0, NULL, NULL);
	free(p);
	return rv;
}

/* Setting writes sent since the fake was reset: each "SM <key> <arg>" */
static int sm_sent(void) {
	const char *s = F.sent;
	int n = 0;
	while ((s = strstr(s, "\nSM ")) != NULL) { n++; s += 4; }
	return n;
}

static inst_code trigger(void *ctx, inst_ui_purp purp) { (void)ctx; return purp == inst_armed ? inst_user_trig : inst_ok; }
static inst_code cancel(void *ctx, inst_ui_purp purp) { (void)ctx; (void)purp; return inst_user_abort; }

/* A failed capture must clear the same ipatch and previous comparison facts. */
static void test_errors_after_success(void) {
	int i;
	for (i = 0; i < 4; i++) {
		inst *p = make_driver();
		cr250 *c = (cr250 *)p;
		ipatch out;
		inst_code rv;
		CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
		CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
		CHECK(out.XYZ_v && out.sp.spec_n == CR250_FAKE_SP_N);
		CHECK(c->capture.unit.verdict == CR250_UNIT_CONSISTENT && c->capture.unit.computed != 0);
		if (i < 2) {
			F.native_cmd = "M";
			F.native_status = i == 0 ? "ER:-305:M:" : "ER:-305:M";
		} else if (i == 2) F.timeout_measure = 1;
		else F.sp_fault = "short";
		rv = p->read_sample(p, "", &out, instNoClamp);
		CHECK(!out.XYZ_v && out.sp.spec_n == 0 && F.measures == 2);
		CHECK(c->capture.spectrum.count == 0);
		CHECK(c->capture.unit.verdict == CR250_UNIT_NOT_CHECKED && c->capture.unit.computed == 0);
		CHECK(unlocked(p));
		if (i == 0) {
			CHECK((rv & inst_mask) == inst_misread);
			CHECK((rv & inst_imask) == CR250_NATIVE_ERROR_BASE + 305);
			CHECK(c->engine.last_reply.complete && c->engine.last_reply.code == -305);
			CHECK(!strcmp(c->engine.last_reply.context, "M") && !strcmp(c->engine.last_reply.value, ""));
			CHECK(!c->engine.uncertain && c->engine.identified && F.closes == 0);
		} else {
			CHECK((rv & inst_imask) == (i == 1 ? CR250_DATA_PARSE_ERROR : CR250_TIMEOUT));
			CHECK(c->engine.uncertain && !c->engine.identified && F.closes == 1);
			if (i != 3) CHECK(!c->engine.last_reply.complete);
			CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok);
			CHECK(F.measures == 2 && F.closes == 1 && !out.XYZ_v && out.sp.spec_n == 0);
		}
		p->del(p);
	}
}

static const char *sync_basic_inventory = "3\r\n0,None\r\n1,Auto\r\n2,Manual";

static inst *make_sync_driver(const char *firmware) {
	inst *p = new_driver();
	F.firmware = firmware;
	CHECK(p->init_coms(p, baud_38400, fc_nc, 3) == inst_ok);
	CHECK(p->init_inst(p) == inst_ok);
	CHECK(p->get_set_opt(p, inst_opt_trig_prog) == inst_ok);
	return p;
}

static void check_sync_inventory(const char *firmware, const char *mode, const char *frequency,
                          double expected_hz, const char *inventory) {
	inst *p = make_sync_driver(firmware);
	cr250 *c = (cr250 *)p;
	ipatch out;
	inst_code rv;
	cr250_fake_set_setting("SyncMode", mode);
	cr250_fake_set_setting("SyncFreq", frequency);
	F.override_cmd = "RC SyncMode"; F.override_value = inventory;
	rv = p->read_sample(p, "", &out, instNoClamp);
	CHECK(rv == inst_ok && out.XYZ_v && F.measures == 1);
	CHECK(!strcmp(c->capture.value[6], mode));
	CHECK(c->capture.sync_hz == expected_hz);
	CHECK(cr250_fake_sent("RC SyncMode"));
	CHECK(cr250_fake_sent("RM SyncFreq") == (strcmp(mode, "None") != 0));
	CHECK(cr250_fake_sent("RS SyncFreq") == (strcmp(mode, "Manual") == 0));
	CHECK(!cr250_fake_sent_prefix("SM SyncMode "));
	CHECK(unlocked(p));
	p->del(p);
	CHECK(F.closes == 1);
}

static void test_sync_inventories(void) {
	inst *p;
	ipatch out;
	inst_code rv;
	static const char *new_inventory = "6\r\n0,None\r\n1,Auto\r\n2,Manual\r\n3,NTSC\r\n4,PAL\r\n5,CINEMA";
	check_sync_inventory("1.26", "None", "60.00 Hz", 0, sync_basic_inventory);
	check_sync_inventory("1.26", "Auto", "60.00 Hz", 60, sync_basic_inventory);
	check_sync_inventory("1.33", "Manual", "60.00 Hz", 60, sync_basic_inventory);
	check_sync_inventory("1.34", "NTSC", "60.00 Hz", 60, new_inventory);
	check_sync_inventory("1.34", "PAL", "50.00 Hz", 50, new_inventory);
	check_sync_inventory("1.34", "CINEMA", "48.00 Hz", 48, new_inventory);

	/* Unsupported setting name is refused before M, regardless of version text. */
	p = make_sync_driver("1.33");
	cr250_fake_set_setting("SyncMode", "NTSC");
	F.override_cmd = "RC SyncMode"; F.override_value = sync_basic_inventory;
	rv = p->read_sample(p, "", &out, instNoClamp);
	CHECK((rv & inst_imask) == CR250_SETUP && !out.XYZ_v && !F.measures);
	CHECK(!cr250_fake_sent_prefix("SM SyncMode "));
	p->del(p);

	/* The 1.34 version string does not force new modes to be present. */
	check_sync_inventory("1.34", "Auto", "60.00 Hz", 60, sync_basic_inventory);

	/* A requested Auto mode must exist before a write can be issued. */
	p = make_sync_driver("1.26");
	F.override_cmd = "RC SyncMode"; F.override_value = "2\r\n0,None\r\n2,Manual";
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_emis_refresh_ovd) == inst_ok);
	rv = p->read_sample(p, "", &out, instNoClamp);
	CHECK((rv & inst_imask) == CR250_SETUP && !out.XYZ_v && !F.measures);
	CHECK(!cr250_fake_sent_prefix("SM SyncMode "));
	p->del(p);

	/* Existing model floor still applies; dynamic inventory cannot bypass it. */
	p = new_driver(); F.firmware = "1.25";
	CHECK(p->init_coms(p, baud_38400, fc_nc, 3) == inst_ok);
	rv = p->init_inst(p);
	CHECK((rv & inst_imask) == CR250_FIRMWARE && !F.measures);
	p->del(p);
}

/* Options are promises for the next capture, not permission to write against
 * stale limits. Every required capability must be usable before any SM. */
static void test_numeric_preflight(void) {
	static const instType types[] = { instCR250, instCR100 };
	static const struct { const char *cmd, *value; } averages[] = {
		{ "RC MinExposureX", "0" }, { "RC MaxExposureX", "0" },
		{ "RC MinExposureX", "51" }, { "RC MaxExposureX", "10" },
		{ "RC MinExposureX", "1.5" }, { "RC MaxExposureX", "NA" },
		{ "RC MaxExposureX", "ER:-500:Invalid command:MaxExposureX" },
		{ "RS ExposureX", "0" }, { "RC MaxExposure", "0 msec" },
		{ "RC MaxExposure", "1e308 msec" }
	};
	static const struct { const char *cmd, *value; } sync[] = {
		{ "RC MinSyncFreq", "0 Hz" }, { "RC MaxSyncFreq", "0 Hz" },
		{ "RC MinSyncFreq", "10001 Hz" }, { "RC MaxSyncFreq", "50 Hz" },
		{ "RC MinSyncFreq", "NA" }, { "RC MaxSyncFreq", "ER:-500:Invalid command:MaxSyncFreq" },
		{ "RS SyncFreq", "nan Hz" }
	};
	static const struct { const char *cmd, *value; } exposure[] = {
		{ "RC MinExposure", "0 msec" }, { "RC MinExposure", "501 msec" },
		{ "RC MaxExposure", "50 msec" }, { "RS Exposure", "0.5 msec" },
		{ "RS Exposure", "NA" }
	};
	inst *p;
	cr250 *c;
	ipatch out;
	size_t t, i;
	for (t = 0; t < sizeof(types)/sizeof(types[0]); t++) {
		/* Unusable option capabilities cannot replace an accepted host value. */
		p = make_driver_of(types[t]); c = (cr250 *)p;
		CHECK(p->get_set_opt(p, inst_opt_set_averages, 2) == inst_ok);
		F.override_cmd = "RC MinExposureX"; F.override_value = "0";
		CHECK(p->get_set_opt(p, inst_opt_set_averages, 4) != inst_ok && c->averages == 2);
		F.override_cmd = NULL;
		CHECK(p->set_refr_rate(p, 60) == inst_ok);
		F.override_cmd = "RC MinSyncFreq"; F.override_value = "0 Hz";
		CHECK(p->set_refr_rate(p, 50) != inst_ok && c->refresh_hz == 60);
		F.override_cmd = "RC MaxSyncFreq"; F.override_value = "60.008 Hz";
		CHECK(p->set_refr_rate(p, 60.006) != inst_ok && c->refresh_hz == 60);
		CHECK(sm_sent() == 0 && unlocked(p)); p->del(p);
		/* A parsed zero from None/Auto may be replaced by an explicit valid
		 * Manual target. Positive readback is still required before M. */
		for (i = 0; i < 3; i++) {
			p = make_driver_of(types[t]);
			cr250_fake_set_setting("SyncMode", i == 0 ? "None" : "Auto");
			cr250_fake_set_setting("SyncFreq", "0 Hz");
			CHECK(p->set_refr_rate(p, 60) == inst_ok);
			if (i == 2) {
				cr250_fake_set_setting("SyncMode", "Manual"); F.sm_ignored = 1;
				CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok);
				CHECK(F.measures == 0 && !out.XYZ_v && cr250_fake_sent("SM SyncFreq 60.00"));
			} else {
				CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && F.measures == 1);
				CHECK(((cr250 *)p)->capture.sync_hz == 60 && cr250_fake_sent("SM SyncFreq 60.00"));
			}
			p->del(p);
		}
		/* A sent 60.01 is outside a 60.008 ceiling despite the host's 60.006.
		 * An unchanged 60.00 is also outside a fresh 60.005 floor, even when
		 * it is close enough to the host rate to skip a setter. */
		for (i = 0; i < 2; i++) {
			p = make_driver_of(types[t]);
			CHECK(p->set_refr_rate(p, 60.006) == inst_ok);
			cr250_fake_set_setting("UserCalibMode", "Match");
			if (i == 0) {
				cr250_fake_set_setting("SyncFreq", "50.00 Hz");
				F.override_cmd = "RC MaxSyncFreq"; F.override_value = "60.008 Hz";
			} else {
				F.override_cmd = "RC MinSyncFreq"; F.override_value = "60.005 Hz";
			}
			CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok);
			CHECK(sm_sent() == 0 && F.measures == 0 && !out.XYZ_v && unlocked(p));
			p->del(p);
		}

		for (i = 0; i < sizeof(averages)/sizeof(averages[0]); i++) {
			p = make_driver_of(types[t]);
			CHECK(p->get_set_opt(p, inst_opt_set_averages, 40) == inst_ok);
			cr250_fake_set_setting("UserCalibMode", "Match");
			F.override_cmd = averages[i].cmd; F.override_value = averages[i].value;
			CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok);
			CHECK(sm_sent() == 0 && F.measures == 0 && !out.XYZ_v && unlocked(p));
			F.override_cmd = NULL;
			CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && F.measures == 1);
			p->del(p);
		}
		for (i = 0; i < sizeof(sync)/sizeof(sync[0]); i++) {
			p = make_driver_of(types[t]);
			CHECK(p->set_refr_rate(p, 60) == inst_ok);
			cr250_fake_set_setting("UserCalibMode", "Match");
			F.override_cmd = sync[i].cmd; F.override_value = sync[i].value;
			CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok);
			CHECK(sm_sent() == 0 && F.measures == 0 && !out.XYZ_v && unlocked(p));
			F.override_cmd = NULL;
			CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && F.measures == 1);
			p->del(p);
		}
		/* Auto sync does not consume Manual-only limits. Zero RM frequency is
		 * a native no-sync observation, not an invented positive frequency. */
		p = make_driver_of(types[t]);
		cr250_fake_set_setting("SyncMode", "Auto");
		F.override_cmd = "RC MinSyncFreq"; F.override_value = "0 Hz";
		F.override2_cmd = "RM SyncFreq"; F.override2_value = "0 Hz";
		CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
		CHECK(!cr250_fake_sent("RC MinSyncFreq") && ((cr250 *)p)->capture.sync_hz == 0);
		p->del(p);
		/* Manual has no zero-frequency interpretation, even without a host override. */
		p = make_driver_of(types[t]);
		cr250_fake_set_setting("SyncMode", "Manual"); cr250_fake_set_setting("SyncFreq", "0 Hz");
		F.override_cmd = "RC MinSyncFreq"; F.override_value = "0 Hz";
		CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok);
		CHECK(sm_sent() == 0 && F.measures == 0 && !out.XYZ_v); p->del(p);
		p = make_driver_of(types[t]);
		cr250_fake_set_setting("SyncMode", "Manual"); cr250_fake_set_setting("SyncFreq", "0.001 Hz");
		F.override_cmd = "RC MinSyncFreq"; F.override_value = "0.001 Hz";
		F.override2_cmd = "RC MaxSyncFreq"; F.override2_value = "0.001 Hz";
		cr250_fake_set_setting("UserCalibMode", "Match");
		CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok);
		CHECK(sm_sent() == 0 && F.measures == 0 && !out.XYZ_v); p->del(p);
		/* A single permitted positive value remains a valid interval. */
		p = make_driver_of(types[t]);
		F.override_cmd = "RC MinExposureX"; F.override_value = "1";
		F.override2_cmd = "RC MaxExposureX"; F.override2_value = "1";
		CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok); p->del(p);
		p = make_driver_of(types[t]);
		F.override_cmd = "RC MinSyncFreq"; F.override_value = "60 Hz";
		F.override2_cmd = "RC MaxSyncFreq"; F.override2_value = "60 Hz";
		CHECK(p->set_refr_rate(p, 60) == inst_ok);
		CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok); p->del(p);
		/* Printed-value tolerance cannot turn a Manual RM zero into positive
		 * sync, or excuse an RM frequency outside the capability interval. */
		for (i = 0; i < 2; i++) {
			p = make_driver_of(types[t]);
			cr250_fake_set_setting("SyncMode", "Manual"); cr250_fake_set_setting("SyncFreq", "0.01 Hz");
			F.override_cmd = "RC MinSyncFreq"; F.override_value = "0.01 Hz";
			F.override2_cmd = "RC MaxSyncFreq"; F.override2_value = "0.01 Hz";
			F.override3_cmd = "RM SyncFreq"; F.override3_value = i == 0 ? "0 Hz" : "0.02 Hz";
			CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_CAPTURE_MISMATCH);
			CHECK(F.measures == 1 && !out.XYZ_v && unlocked(p)); p->del(p);
		}
	}
	for (i = 0; i < sizeof(exposure)/sizeof(exposure[0]); i++) {
		p = make_driver();
		CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_emis_nonadaptive) == inst_ok);
		cr250_fake_set_setting("UserCalibMode", "Match");
		F.override_cmd = exposure[i].cmd; F.override_value = exposure[i].value;
		CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok);
		CHECK(sm_sent() == 0 && F.measures == 0 && !out.XYZ_v && unlocked(p));
		F.override_cmd = NULL;
		CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && F.measures == 1);
		p->del(p);
	}
}

static void test_maximum_profiles(void) {
	inst *p;
	ipatch out;
	inst_code rv;
	/* Both documented inventory bounds must fit the ordinary Argyll selector
	 * owner. Every calibration remains selectable, including the last one. */
	{
		char matrix[CR250_MAX_RECORDS * 32 + 16], match[CR250_MAX_RECORDS * 32 + 16];
		inst_disptypesel *full_list = NULL;
		int full_count = 0;
		int i, j, a = snprintf(matrix, sizeof(matrix), "%d", CR250_MAX_RECORDS);
		int b = snprintf(match, sizeof(match), "%d", CR250_MAX_RECORDS);
		for (i = 0; i < CR250_MAX_RECORDS; i++) {
			a += snprintf(matrix + a, sizeof(matrix) - a, "\r\n%d,Matrix-%d", i, i);
			b += snprintf(match + b, sizeof(match) - b, "\r\n%d,Match-%d", i, i);
		}
		p = make_driver_of(instCR100);
		F.override_cmd = "RC Matrix"; F.override_value = matrix;
		F.override2_cmd = "RC Match"; F.override2_value = match;
		rv = p->get_disptypesel(p, &full_count, &full_list, 0, 0);
		CHECK(rv == inst_ok && full_count == CR250_MAX_DISPTYPES);
		if (rv == inst_ok) {
			for (i = 0; i < full_count; i++) {
				int selector = full_list[i].sel[0];
				if (!selector) continue; /* Indexed rows survive character-selector exhaustion. */
				CHECK(!(full_list[i].sel[0] == '_' && full_list[i].sel[1] == 0));
				for (j = 0; j < i; j++) if (full_list[j].sel[0]) CHECK(strcmp(full_list[i].sel, full_list[j].sel) != 0);
				if (selector == '_') selector = selector << 8 | full_list[i].sel[1];
				CHECK(inst_get_disptype_index(p, selector, 0) == i);
			}
			CHECK(!strcmp(full_list[61].sel, "_1") && !strcmp(full_list[121].sel, "_Z"));
			CHECK(!strcmp(full_list[122].sel, "_0") && !strcmp(full_list[128].sel, "_="));
			CHECK(p->set_disptype(p, full_count - 1) == inst_ok);
			CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
			CHECK(((cr250 *)p)->capture.correction_mode == 2
			      && ((cr250 *)p)->capture.correction.id == CR250_MAX_RECORDS - 1);
		}
		CHECK(unlocked(p)); p->del(p);
	}
	/* Selector exhaustion keeps every indexed calibration. Assigned character
	 * selectors must stay unique; remaining rows have no character selector. */
	{
		inst_disptypesel base[141], *list = NULL;
		int i, j, unassigned = 0, count = 0;
		memset(base, 0, sizeof(base));
		for (i = 0; i < 140; i++) {
			base[i].dtech = disptech_unknown;
			snprintf(base[i].desc, sizeof(base[i].desc), "Calibration-%d", i);
		}
		base[140].flags = inst_dtflags_end;
		p = make_driver();
		CHECK(inst_creat_disptype_list(p, &count, &list, base, 0, 0) == inst_ok);
		CHECK(count == 140 && list != NULL);
		if (count == 140 && list != NULL) {
			for (i = 0; i < count; i++) {
				CHECK(!strcmp(list[i].desc, base[i].desc));
				if (!list[i].sel[0]) { unassigned++; continue; }
				CHECK(!(list[i].sel[0] == '_' && list[i].sel[1] == 0));
				for (j = 0; j < i; j++) if (list[j].sel[0])
					CHECK(strcmp(list[i].sel, list[j].sel) != 0);
			}
			CHECK(unassigned > 0);
		}
		free(list); p->del(p);
	}
}

/* Device-applied profiles use Argyll calibration selectors. The current
 * inventory and capture must match the selected ID/name; no host transform
 * or persistent setter is hidden in list/selection calls. */
static void test_full_width_profile_ids(void) {
	instType types[] = { instCR100, instCR250, instCR300 };
	int i, count;
	for (i = 0; i < 3; i++) {
		inst *p = make_driver_of(types[i]);
		cr250 *c = (cr250 *)p;
		inst_disptypesel *list = NULL;
		ipatch out;
		F.override_cmd = "RC Match"; F.override_value = "1\r\n9007199254740991,Full-width profile";
		CHECK(p->get_disptypesel(p, &count, &list, 0, 1) == inst_ok && count >= 2);
		CHECK(p->set_disptype(p, count - 1) == inst_ok);
		CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && F.measures == 1);
		CHECK(c->capture.correction.id == 9007199254740991LL);
		CHECK(!strcmp(c->capture.correction.name, "Full-width profile"));
		CHECK(cr250_fake_sent("SM Match 9007199254740991") && unlocked(p));
		p->del(p);
	}
}

static void test_corrections_and_cr300(void) {
	inst *p;
	cr250 *c;
	ipatch out;
	inst_disptypesel *list;
	inst_mode modes;
	inst2_capability caps;
	int count, n;
	inst_code rv;
	p = make_driver(); c = (cr250 *)p;
	p->capabilities(p, &modes, &caps, NULL);
	CHECK(caps & inst2_disptype);
	CHECK(p->get_disptypesel(p, &count, &list, 0, 0) == inst_ok && count == 3);
	CHECK(!strcmp(list[0].desc, "No user calibration") && (list[0].flags & inst_dtflags_default));
	CHECK(!(list[1].flags & inst_dtflags_mtx) && list[1].cbid == 0 && list[1].dtech == disptech_unknown);
	CHECK(!cr250_fake_sent("RC Matrix") && cr250_fake_sent("RC Match"));
	CHECK(p->set_disptype(p, -1) == inst_unsupported && p->set_disptype(p, count) == inst_unsupported);
	n = F.writes;
	CHECK(p->set_disptype(p, 2) == inst_ok && F.writes == n && sm_sent() == 0);
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	/* Match may change native XYZ without an equal spectral correction.
	 * Do not compare the two as uncorrected XYZ; retain power/axis checks. */
	F.override_cmd = "RM XYZ"; F.override_value = "25,50,12.5";
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.XYZ[1] == 50 && F.measures == 1);
	CHECK(cr250_fake_sent("SM Match 9") && cr250_fake_sent("SM UserCalibMode 2"));
	CHECK(c->capture.correction_mode == 2 && c->capture.correction.id == 9
	      && !strcmp(c->capture.correction.name, "Reference-B"));
	CHECK(c->capture.unit.discriminating == (CR250_UNIT_POWER | CR250_UNIT_AXIS));
	CHECK(c->capture.unit.verdict == CR250_UNIT_CONSISTENT && c->capture.unit.xyz[1] > 24.9);
	n = sm_sent();
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && sm_sent() == n);
	F.sp_scale = 1000;
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_mask) == inst_misread && !out.XYZ_v);
	F.sp_scale = 1; F.override_cmd = "RM Match"; F.override_value = "0";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_mask) == inst_misread && !out.XYZ_v);
	F.override_cmd = "RC Match"; F.override_value = "1\r\n9,Changed-Reference";
	n = F.measures;
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_mask) == inst_wrong_setup && F.measures == n);
	CHECK(strstr(p->interp_error(p, CR250_SETUP), "no longer exists") != NULL);
	F.override_cmd = NULL;
	CHECK(p->set_disptype(p, 0) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.correction.id == -1);
	CHECK(cr250_fake_sent("SM UserCalibMode 0") && c->capture.unit.discriminating == ALL_COMPARED);
	CHECK(unlocked(p)); p->del(p);

	/* Empty profile inventories leave the explicit None choice; recreating
	 * a failed list must not discard an existing usable list. */
	p = make_driver();
	F.override_cmd = "RC Match"; F.override_value = "None";
	CHECK(p->get_disptypesel(p, &count, &list, 0, 1) == inst_ok && count == 1);
	F.override_value = "2\r\n9,A\r\n9,B";
	CHECK(p->get_disptypesel(p, &count, &list, 0, 1) != inst_ok);
	CHECK(p->get_disptypesel(p, &count, &list, 0, 0) == inst_ok && count == 1);
	CHECK(unlocked(p)); p->del(p);

	p = make_driver_of(instCR100); c = (cr250 *)p;
	CHECK(p->get_disptypesel(p, &count, &list, 0, 0) == inst_ok && count == 5);
	CHECK(strstr(list[2].desc, "Matrix 7:") != NULL);
	CHECK(p->set_disptype(p, 2) == inst_ok && sm_sent() == 0);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.sp.spec_n == 0);
	CHECK(c->capture.correction_mode == 1 && c->capture.correction.id == 7);
	CHECK(cr250_fake_sent("SM Matrix 7") && cr250_fake_sent("RM Matrix") && !cr250_fake_sent("RM Match"));
	F.override_cmd = "RS Accessory"; F.override_value = "Other";
	F.override2_cmd = "RC Accessory"; F.override2_value = "2\r\n0,Standard,Radiance\r\n1,Other,Radiance";
	n = sm_sent();
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_mask) == inst_wrong_setup && sm_sent() == n);
	CHECK(strstr(p->interp_error(p, CR250_SETUP), "Matrix accessory changed") != NULL);
	CHECK(unlocked(p)); p->del(p);
	/* An exact CR-100 reporting Photometer type 0 cannot select a Matrix. */
	p = new_driver_of(instCR100); F.type = "0";
	CHECK(p->init_coms(p, baud_38400, fc_nc, 3) == inst_ok && p->init_inst(p) == inst_ok);
	CHECK(p->get_disptypesel(p, &count, &list, 0, 0) == inst_ok && count == 3);
	CHECK(!cr250_fake_sent("RC Matrix")); p->del(p);

	/* A queued profile cannot bypass numeric preflight or ignore readback. */
	p = make_driver();
	CHECK(p->get_disptypesel(p, &count, &list, 0, 0) == inst_ok && p->set_disptype(p, 2) == inst_ok);
	F.override_cmd = "RC MaxExposure"; F.override_value = "0 msec";
	CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok && sm_sent() == 0 && F.measures == 0);
	F.override_cmd = NULL; F.sm_ignored = 1;
	CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok && F.measures == 0);
	CHECK(unlocked(p)); p->del(p);

	p = make_driver_of(instCR300); c = (cr250 *)p;
	CHECK(!strcmp(c->engine.id.model, "CR-300") && c->engine.id.instrument_type == 2);
	CHECK(inst_enum(inst_name(instCR300)) == instCR300 && !strcmp(inst_sname(instCR300), "CR-300"));
	p->capabilities(p, &modes, &caps, NULL);
	CHECK((modes & inst_mode_spectral) && (modes & inst_mode_emis_nonadaptive));
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.sp.spec_n == CR250_FAKE_SP_N);
	CHECK(cr250_fake_sent("RM Radiometric") && cr250_fake_sent("RM Spectrum") && F.measures == 1);
	/* The grid is the actual reply: this synthetic 5 nm case is accepted. */
	CHECK(out.sp.spec_wl_short == 380 && out.sp.spec_wl_long == 780);
	CHECK(unlocked(p)); p->del(p);
	p = new_driver_of(instCR300); F.type = "1";
	CHECK(p->init_coms(p, baud_38400, fc_nc, 3) != inst_ok && F.measures == 0);
	p->del(p);
	p = new_driver_of(instCR300); F.firmware = "1.25";
	CHECK(p->init_coms(p, baud_38400, fc_nc, 3) == inst_ok);
	rv = p->init_inst(p);
	CHECK((rv & inst_mask) == inst_unsupported && F.measures == 0); p->del(p);
}

/* A trigger callback can run another read while the outer transaction has
 * released its lock. Its retained facts must not survive the outer failure. */
typedef struct {
	inst *instrument;
	int fail_ready, nested_ok;
} interleaved_read;

static inst_code interleave_trigger(void *context, inst_ui_purp purpose) {
	interleaved_read *state = (interleaved_read *)context;
	inst *p = state->instrument;
	ipatch inner;
	if (purpose != inst_armed) return inst_ok;
	p->set_uicallback(p, NULL, NULL);
	p->get_set_opt(p, inst_opt_trig_prog);
	state->nested_ok = p->read_sample(p, "nested", &inner, instNoClamp) == inst_ok
	                && inner.XYZ_v && inner.sp.spec_n != 0;
	if (state->fail_ready) p->gotcoms = 0;
	else {
		F.override_cmd = "RS CMF";
		F.override_value = "ER:-500:Invalid command:CMF";
	}
	p->set_uicallback(p, interleave_trigger, context);
	return inst_user_trig;
}

static void test_interleaved_capture_reset(void) {
	int i;
	for (i = 0; i < 2; i++) {
		inst *p = make_driver();
		cr250 *c = (cr250 *)p;
		interleaved_read state;
		inst_disptypesel *list;
		int count;
		ipatch out;
		state.instrument = p; state.fail_ready = i; state.nested_ok = 0;
		CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
		CHECK(p->get_disptypesel(p, &count, &list, 0, 0) == inst_ok && p->set_disptype(p, 2) == inst_ok);
		p->set_uicallback(p, interleave_trigger, &state);
		p->get_set_opt(p, inst_opt_trig_user);
		CHECK(p->read_sample(p, "outer", &out, instNoClamp) != inst_ok);
		CHECK(state.nested_ok && F.measures == 1 && !out.XYZ_v && out.sp.spec_n == 0);
		CHECK(c->capture.xyz[0] == 0 && c->capture.xyz[1] == 0 && c->capture.xyz[2] == 0);
		CHECK(c->capture.spectrum.count == 0 && c->capture.unit.verdict == CR250_UNIT_NOT_CHECKED);
		CHECK(c->capture.correction_mode == 0 && c->capture.correction.name[0] == 0);
		CHECK(unlocked(p)); p->del(p);
	}
}

static void test_duplicate_filters(void) {
	inst *p = make_driver();
	ipatch out;
	/* Duplicate RS declarations are invalid even if RM repeats both names.
	 * Pending CMF/correction changes must not be written before refusal. */
	cr250_fake_set_setting("CMF", "2"); cr250_fake_set_setting("UserCalibMode", "Match");
	F.override_cmd = "RS Filter"; F.override_value = "ND-100-1,None,ND-100-1";
	F.override2_cmd = "RM Filter"; F.override2_value = "ND-100-1,ND-100-1";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_mask) == inst_wrong_setup);
	CHECK(!out.XYZ_v && F.measures == 0 && sm_sent() == 0);
	CHECK(strstr(p->interp_error(p, CR250_SETUP), "distinct Radiance inventory records") != NULL);
	CHECK(strstr(p->interp_error(p, CR250_SETUP), "None in every slot") == NULL);
	CHECK(unlocked(p)); p->del(p);
}

static void test_active_filters(void) {
	inst *p = make_driver();
	cr250 *c = (cr250 *)p;
	ipatch out;
	int n;
	F.override_cmd = "RS Filter"; F.override_value = "ND-100-1,None,ND-100-3";
	F.override2_cmd = "RM Filter"; F.override2_value = "ND-100-1,ND-100-3";
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.XYZ_v);
	CHECK(c->capture.filters[0].id == 3 && c->capture.filters[1].id == -1 && c->capture.filters[2].id == 5);
	CHECK(!strcmp(c->capture.filters[2].type, "Radiance") && sm_sent() == 0);
	F.override2_value = "ND-100-3,ND-100-1";
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.XYZ_v);
	CHECK(!strcmp(c->capture.used_filters, "ND-100-3,ND-100-1"));
	F.override2_value = "ND-100-1";
	CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok && !out.XYZ_v);
	F.override2_value = "ND-100-1,ND-100-1";
	CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok && !out.XYZ_v);
	F.override3_cmd = "RC Filter"; F.override3_value = "2\r\n3,ND-100-1,Irradiance\r\n5,ND-100-3,Radiance";
	n = F.measures;
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_mask) == inst_wrong_setup && F.measures == n);
	F.override3_value = "3\r\n3,ND-100-1,Radiance\r\n4,ND-100-1,Radiance\r\n5,ND-100-3,Radiance";
	CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok && F.measures == n);
	CHECK(unlocked(p)); p->del(p);
}

/* Only a discriminating same-capture check may support the driver's density
 * model. Dark/ambiguous spectra retain native observations without publishing
 * an ipatch or silently changing the requested mode to XYZ-only. */
static void test_unit_publication(void) {
	static const instType models[] = { instCR250, instCR300 };
	static const struct {
		double level, scale;
		const char *fault;
		inst_code category;
		int error, verdict;
	} cases[] = {
		{ 1, 1, NULL, inst_ok, 0, CR250_UNIT_CONSISTENT },
		{ 0.001, 1, NULL, inst_misread, CR250_SPECTRUM_UNVERIFIED, CR250_UNIT_INCONCLUSIVE },
		{ 0, 1, NULL, inst_misread, CR250_SPECTRUM_UNVERIFIED, CR250_UNIT_INCONCLUSIVE },
		{ 1e-8, 1000, NULL, inst_misread, CR250_SPECTRUM_UNVERIFIED, CR250_UNIT_INCONCLUSIVE },
		{ 1, 1000, NULL, inst_misread, CR250_SPECTRUM_MISMATCH, CR250_UNIT_MISMATCH },
		{ 1, 1, "NA", inst_protocol_error, CR250_SPECTRUM_SHAPE, CR250_UNIT_NOT_CHECKED }
	};
	int model, n, i;
	for (model = 0; model < 2; model++) {
		for (n = 0; n < (int)(sizeof(cases) / sizeof(cases[0])); n++) {
			inst *p = make_driver_of(models[model]);
			cr250 *c = (cr250 *)p;
			ipatch out;
			inst_code rv;
			double type, power, photons;
			F.sp_level = cases[n].level; cr250_fake_spectrum();
			F.sp_scale = cases[n].scale; F.sp_fault = cases[n].fault;
			CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
			memset(&out, 0x7f, sizeof(out));
			rv = p->read_sample(p, "", &out, instNoClamp);
			CHECK((rv & inst_mask) == cases[n].category && (rv & inst_imask) == cases[n].error);
			CHECK(c->capture.unit.verdict == cases[n].verdict);
			CHECK(F.measures == 1 && cr250_fake_sent("RM Spectrum"));
			CHECK(c->capture.xyz[0] == 12.5 * cases[n].level
			   && c->capture.xyz[1] == 25 * cases[n].level
			   && c->capture.xyz[2] == 6.25 * cases[n].level);
			CHECK(sscanf(F.radiometric, "%lf,%lf,%lf", &type, &power, &photons) == 3);
			CHECK(c->capture.radiometric[0] == type && c->capture.radiometric[1] == power
			   && c->capture.radiometric[2] == photons);
			if (cases[n].fault == NULL) {
				CHECK(c->capture.spectrum.count == CR250_FAKE_SP_N);
				CHECK(c->capture.spectrum.start_nm == 380 && c->capture.spectrum.end_nm == 780
				   && c->capture.spectrum.step_nm == 5);
				for (i = 0; i < CR250_FAKE_SP_N; i++) {
					double sent = F.sp_values[i] * cases[n].scale;
					CHECK(fabs(c->capture.spectrum.value[i] - sent) <= 1e-6 * fabs(sent));
				}
			} else CHECK(c->capture.spectrum.count == 0);
			if (cases[n].category == inst_ok) {
				CHECK(out.XYZ_v && out.sp.spec_n == CR250_FAKE_SP_N && out.sp.norm == 1);
				CHECK(out.sp.spec[40] == 1000 * c->capture.spectrum.value[40]);
			} else CHECK(!out.XYZ_v && out.sp.spec_n == 0);
			if (cases[n].error == CR250_SPECTRUM_UNVERIFIED) {
				CHECK(c->capture.unit.discriminating == 0);
				CHECK(strstr(p->interp_error(p, rv), "request XYZ-only") != NULL);
				CHECK(c->capture.problem[0] != 0);
			}
			if (cases[n].category == inst_ok) {
				/* The preceding bright success cannot qualify a later dark capture. */
				F.sp_level = 0.001; cr250_fake_spectrum();
				rv = p->read_sample(p, "", &out, instNoClamp);
				CHECK((rv & inst_imask) == CR250_SPECTRUM_UNVERIFIED);
				CHECK(!out.XYZ_v && out.sp.spec_n == 0 && F.measures == 2);
				CHECK(c->capture.unit.verdict == CR250_UNIT_INCONCLUSIVE);
				CHECK(c->capture.xyz[1] == 0.025 && c->capture.spectrum.count == CR250_FAKE_SP_N);
			}
			CHECK(c->engine.identified && !c->engine.uncertain && unlocked(p));
			p->del(p); CHECK(F.deleted == 1 && F.closes == 1);
		}
	}
	/* Explicit XYZ-only requests stay available for dark captures on all models,
	 * including CR-100, which never reads or publishes a spectrum. */
	for (model = 0; model < 3; model++) {
		inst *p = make_driver_of(model == 2 ? instCR100 : models[model]);
		cr250 *c = (cr250 *)p;
		ipatch out;
		F.sp_level = 0; cr250_fake_spectrum();
		CHECK(p->set_mode(p, inst_mode_emis_spot) == inst_ok);
		CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
		CHECK(out.XYZ_v && out.XYZ[0] == 0 && out.XYZ[1] == 0 && out.XYZ[2] == 0);
		CHECK(out.sp.spec_n == 0 && !cr250_fake_sent("RM Spectrum"));
		CHECK(c->capture.unit.verdict == CR250_UNIT_NOT_CHECKED && unlocked(p));
		p->del(p); CHECK(F.deleted == 1 && F.closes == 1);
	}
}

int main(int argc, char **argv) {
	inst *p;
	cr250 *c;
	ipatch out;
	inst_mode modes; inst2_capability caps; inst3_capability caps3;
	inst_cal_type needed, available;
	inst_code rv;
	int n;
	if (argc == 2 && !strcmp(argv[1], "--units-only")) {
		test_unit_publication();
		printf("cr250 unit publication test: %d checks, %d failures\n", checks, failures);
		return failures ? 1 : 0;
	}
	if (argc == 2 && !strcmp(argv[1], "--selectors-only")) {
		test_maximum_profiles();
		printf("cr250 selector test: %d checks, %d failures\n", checks, failures);
		return failures ? 1 : 0;
	}
	p = make_driver(); c = (cr250 *)p;
	CHECK(inst_enum(inst_name(instCR250)) == instCR250 && inst_enum(inst_name(instCR100)) == instCR100);
	/* Identification probe: one read-only "RC Model", at 115200 only. */
	{
		const char *idn = "ER:-1:*idn?:Unknown command\r\n";
		icom_type fast = icomt_serial | icomt_fastserial | icomt_seriallike;
		CHECK(probe("OK:0:RC Model:CR-250\r\n", idn, fast) == instCR250);
		CHECK(probe_rc_model == 1 && !probe_rc_elsewhere && !probe_other_cr);
		CHECK(probe("OK:0:RC Model:CR-100\r\n", idn, fast) == instCR100);
		CHECK(probe("OK:0:RC Model:CR-250-RH\r\n", idn, fast) == instUnknown && probe_rc_model == 1);
		CHECK(probe("OK:0:RC Model:CR-300\r\n", NULL, fast) == instCR300);
		CHECK(probe("OK:100:RC Model:CR-250\r\n", NULL, fast) == instUnknown);
		CHECK(probe("ER:-100:RC Model:Unknown command\r\n", NULL, fast) == instUnknown);
		CHECK(probe("OK:0:RC Model:CR-250", NULL, fast) == instUnknown);
		CHECK(probe(NULL, NULL, fast) == instUnknown);
		CHECK(probe_rc_model == 1 && !probe_rc_elsewhere && !probe_other_cr);
		CHECK(probe("OK:0:RC Model:CR-250\r\n", NULL, fast | icomt_btserial) == instUnknown);
		CHECK(probe_rc_model == 0 && !probe_other_cr);
		CHECK(cr250_model_reply("OK:0:RC Model:CR-250\r\n") == instCR250);
		CHECK(cr250_model_reply("OK:0:RC Model:CR-100\r\n") == instCR100);
		CHECK(cr250_model_reply("OK:0:RC Model:CR-250\n") == instUnknown);
		CHECK(cr250_model_reply(NULL) == instUnknown);
		/* Echo on (manual 4.1): the query comes back first, with or without
		 * the prompt, then two reads take its split LF and reply. Only one exact echo. */
		CHECK(probe(">RC Model\r\nOK:0:RC Model:CR-250\r\n", idn, fast) == instCR250);
		CHECK(probe_rc_model == 1 && probe_reads == 2 && !probe_other_cr);
		CHECK(probe("RC Model\r\nOK:0:RC Model:CR-100\r\n", idn, fast) == instCR100 && probe_reads == 2);
		CHECK(probe(">RC Model\r\n", idn, fast) == instUnknown && probe_reads == 2);
		CHECK(probe(">RC Model\r\nOK:100:RC Model:CR-250\r\n", idn, fast) == instUnknown && probe_reads == 2);
		CHECK(probe(">RC Model\r\nER:-100:RC Model:Unknown command\r\n", idn, fast) == instUnknown);
		CHECK(probe(">RC Model\r\nOK:0:RC Model:CR-300\r\n", idn, fast) == instCR300 && probe_rc_model == 1);
		CHECK(probe(">RC Model\r\n>RC Model\r\nOK:0:RC Model:CR-250\r\n", idn, fast) == instUnknown);
		CHECK(probe(">RC Modelx\r\nOK:0:RC Model:CR-250\r\n", idn, fast) == instUnknown && !probe_reads);
		CHECK(probe(">>RC Model\r\nOK:0:RC Model:CR-250\r\n", idn, fast) == instUnknown && !probe_reads);
		CHECK(probe("RC Model \r\nOK:0:RC Model:CR-250\r\n", idn, fast) == instUnknown && !probe_reads);
		/* The reply's read gets what is left of the probe's 0.1 s, not
		 * another 0.1 s; with nothing left there is no read. */
		probe_delay_ms = 40;
		CHECK(probe(">RC Model\r\nOK:0:RC Model:CR-250\r\n", idn, fast) == instCR250);
		CHECK(probe_reads == 2 && probe_read_tout > 0.0 && probe_read_tout < 0.062);
		probe_delay_ms = 120;
		CHECK(probe(">RC Model\r\nOK:0:RC Model:CR-250\r\n", idn, fast) == instUnknown && !probe_reads);
		probe_delay_ms = 0;
		CHECK(cr250_model_echo(">RC Model\r\n") && cr250_model_echo("RC Model\r\n") && cr250_model_echo("RC Model"));
		CHECK(!cr250_model_echo("RC Model\n") && !cr250_model_echo(" RC Model\r\n") && !cr250_model_echo(""));
		CHECK(!cr250_model_echo("RC Model\r\nOK") && !cr250_model_echo(NULL));
		/* OS reads may coalesce the echo and status, or leave part of the
		 * status in the first chunk. Both registered models must be found. */
		probe_chunk_min = 256;
		CHECK(probe(">RC Model\r\nOK:0:RC Model:CR-250\r\n", idn, fast) == instCR250 && !probe_reads);
		CHECK(probe("RC Model\r\nOK:0:RC Model:CR-100\r\n", idn, fast) == instCR100 && !probe_reads);
		CHECK(probe(">RC Model\rOK:0:RC Model:CR-250\r", idn, fast) == instCR250 && !probe_reads);
		CHECK(probe("RC Model\nOK:0:RC Model:CR-100\n", idn, fast) == instCR100 && !probe_reads);
		CHECK(probe("OK:0:RC Model:CR-250\r", idn, fast) == instCR250 && !probe_reads);
		CHECK(probe("OK:0:RC Model:CR-100\n", idn, fast) == instCR100 && !probe_reads);
		CHECK(probe(">RC Model\r\nOK:0:RC Model:CR-250\r\nextra\r\n", idn, fast) == instUnknown);
		/* Prompt placement is not specified; do not accept a buffered prompt
		 * after the reply as if the command/reply exchange were complete. */
		CHECK(probe("RC Model\r\nOK:0:RC Model:CR-250\r\n>", idn, fast) == instUnknown);
		CHECK(probe(">RC Model\r\n>RC Model\r\nOK:0:RC Model:CR-250\r\n", idn, fast) == instUnknown);
		CHECK(probe(">RC Modelx\r\nOK:0:RC Model:CR-250\r\n", idn, fast) == instUnknown);
		CHECK(probe(">RC Model\r\nOK:100:RC Model:CR-250\r\n", idn, fast) == instUnknown);
		CHECK(probe(">RC Model\r\nER:-100:RC Model:Unknown command\r\n", idn, fast) == instUnknown);
		CHECK(probe(">RC Model\r\nOK:0:RM Model:CR-250\r\n", idn, fast) == instUnknown);
		CHECK(probe(">RC Model\r\nOK:0:RC Model:CR-300\r\n", idn, fast) == instCR300);
		probe_chunk_min = 22;	/* Echo plus "OK:0:RC Mod" in the first chunk */
		CHECK(probe(">RC Model\r\nOK:0:RC Model:CR-250\r\n", idn, fast) == instCR250 && probe_reads == 1);
		CHECK(probe("RC Model\r\nOK:0:RC Model:CR-100\r\n", idn, fast) == instCR100 && probe_reads == 1);
		probe_delay_ms = 40;
		CHECK(probe(">RC Model\r\nOK:0:RC Model:CR-250\r\n", idn, fast) == instCR250);
		CHECK(probe_reads == 1 && probe_read_tout > 0.0 && probe_read_tout < 0.062);
		probe_delay_ms = 120;
		CHECK(probe(">RC Model\r\nOK:0:RC Model:CR-250\r\n", idn, fast) == instUnknown && !probe_reads);
		probe_delay_ms = 0;
		probe_chunk_min = 10;	/* The first chunk ends at the echo's CR */
		CHECK(probe(">RC Model\r\nOK:0:RC Model:CR-250\r\n", idn, fast) == instCR250);
		probe_chunk_min = 0;
	}
	CHECK(!strcmp(inst_sname(instCR250), "CR-250") && !strcmp(inst_sname(instCR100), "CR-100"));
	CHECK(instColorHug2 == 38 && instCR250 == 39 && instCR100 == 40 && instFakeDisp == 9998);
	/* The fixture's spectrum is three positive lines agreeing with its XYZ. */
	CHECK(F.amplitudes[0] > 0 && F.amplitudes[1] > 0 && F.amplitudes[2] > 0);

	/* CR-250 modes: spectra, Fixed exposure, the refresh overrides; averaging
	 * and a refresh rate as options. */
	p->capabilities(p, &modes, &caps, &caps3);
	CHECK((modes & inst_mode_spectral) && (modes & inst_mode_emis_nonadaptive));
	CHECK(IMODETST(modes, inst_mode_emis_norefresh_ovd) && (caps & inst2_prog_trig));
	CHECK((caps & inst2_set_refresh_rate) && !(caps & inst2_get_refresh_rate) && (caps3 & inst3_average));
	CHECK(p->check_mode(p, inst_mode_emis_spot | inst_mode_colorimeter) == inst_ok);
	CHECK(p->check_mode(p, inst_mode_emis_tele) == inst_ok);
	CHECK(p->check_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK(p->check_mode(p, inst_mode_emis_spot | inst_mode_emis_nonadaptive) == inst_ok);
	CHECK(p->check_mode(p, inst_mode_emis_spot | inst_mode_emis_refresh_ovd) == inst_ok);
	CHECK(p->check_mode(p, inst_mode_emis_tele | inst_mode_emis_norefresh_ovd) == inst_ok);
	CHECK(p->check_mode(p, inst_mode_emis_spot | 0x4000) == inst_unsupported);
	CHECK(p->check_mode(p, inst_mode_emis_refresh_ovd) == inst_unsupported);
	CHECK(p->check_mode(p, inst_mode_ref_spot) == inst_unsupported);
	CHECK(p->get_n_a_cals(p, &needed, &available) == inst_ok && !needed && !available);
	CHECK(!strcmp(p->get_serial_no(p), "SYNTHETIC-250"));

	/* Without a set_mode the read is XYZ only and the instrument, already in
	 * the contract's state, is not written. */
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
	CHECK(F.measures == 1 && out.XYZ_v && out.XYZ[0] == 12.5 && out.XYZ[1] == 25 && out.XYZ[2] == 6.25);
	CHECK(out.sp.spec_n == 0 && out.mtype == inst_mrt_emission);
	CHECK(c->capture.budget_s == 120);
	CHECK(!cr250_fake_sent_prefix("SM ") && !cr250_fake_sent("RM Spectrum"));
	CHECK(!strcmp(c->capture.range.name, "A") && c->capture.exposure_x == 1 && c->capture.sync_hz == 0);
	CHECK(cr250_fake_sent("RC Accessory") && cr250_fake_sent("RC Aperture") && cr250_fake_sent("RC Speed"));
	CHECK(cr250_fake_sent("RM Exposure") && cr250_fake_sent("RM MaxAutoExposure"));
	CHECK(cr250_fake_sent("RS CMF") && cr250_fake_sent("RS Speed") && cr250_fake_sent("RS Aperture") && cr250_fake_sent("RM Radiometric"));
	CHECK(!cr250_fake_sent("RM SyncFreq") && !cr250_fake_sent("RS Exposure") && !cr250_fake_sent("RS Range"));
	CHECK(c->capture.radiometric[0] == 0 && c->capture.radiometric[1] > 0);
	F.override_cmd = "RM XYZ"; F.override_value = "-1,2,-3";
	CHECK(p->read_sample(p, "", &out, instClamp) == inst_ok);
	CHECK(out.XYZ[0] == 0 && c->capture.xyz[0] == -1 && c->capture.xyz[2] == -3);
	p->set_uicallback(p, trigger, NULL); p->get_set_opt(p, inst_opt_trig_user);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_user_trig);
	CHECK(out.XYZ[0] == -1 && unlocked(p));
	p->set_uicallback(p, cancel, NULL); n = F.writes;
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_user_abort && F.writes == n && !out.XYZ_v);
	CHECK(unlocked(p));
	/* A single "None" is also no filter; the RS and RM forms need not match */
	p->set_uicallback(p, NULL, NULL); p->get_set_opt(p, inst_opt_trig_prog);
	F.override_cmd = "RS Filter"; F.override_value = "None";
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.XYZ_v);
	F.override_cmd = NULL;

	/* Spectral mode: RM Spectrum after the XYZ, published in Argyll's mW
	 * units with norm 1, the 200 ms note kept, and the spectrum agreeing
	 * with the instrument's power, photon rate and XYZ. */
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.XYZ_v && out.XYZ[1] == 25);
	CHECK(out.sp.spec_n == CR250_FAKE_SP_N && out.sp.spec_wl_short == 380 && out.sp.spec_wl_long == 780);
	CHECK(out.sp.norm == 1.0 && fabs(out.sp.spec[40] - 1000.0 * c->capture.spectrum.value[40]) < 1e-12);
	CHECK(fabs(c->capture.spectrum.value[40] - F.sp_values[40]) <= 1e-6 * fabs(F.sp_values[40]));
	CHECK(c->capture.unit.verdict == CR250_UNIT_CONSISTENT && c->capture.unit.discriminating == ALL_COMPARED);
	CHECK(c->capture.unit.computed == (ALL_COMPARED | CR250_UNIT_SPECTRUM | CR250_UNIT_SPECTRUM_MEAN));
	CHECK(fabs(c->capture.unit.power_ratio - 1) < 1e-4 && fabs(c->capture.unit.y_ratio - 1) < 1e-4);
	CHECK(fabs(c->capture.unit.spectrum_mean_nm - c->capture.unit.mean_nm) < 0.01);
	CHECK(c->capture.unit.chroma_error < 1e-4 && cr250_fake_sent("RM Spectrum"));
	CHECK(unlocked(p));
	/* Back to XYZ only: no spectrum is read */
	CHECK(p->set_mode(p, inst_mode_emis_spot) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.sp.spec_n == 0);
	CHECK(F.guard_checks == 0 && F.guard_violations == 0);	/* The next command waited 200 ms */
	p->del(p); CHECK(F.deleted == 1 && F.closes == 1);

	/* A spectrum that disagrees with the instrument is refused, the factor
	 * named, and the link kept. */
	spectral_refused(1000, 0, NULL, inst_misread, CR250_SPECTRUM_MISMATCH, "nearest factor 1000");
	spectral_refused(0.001, 0, NULL, inst_misread, CR250_SPECTRUM_MISMATCH, "nearest factor 1/1000");
	spectral_refused(2, 0, NULL, inst_misread, CR250_SPECTRUM_MISMATCH, "nearest factor 2");
	spectral_refused(0.5, 0, NULL, inst_misread, CR250_SPECTRUM_MISMATCH, "nearest factor 1/2");
	spectral_refused(1, 20, NULL, inst_misread, CR250_SPECTRUM_MISMATCH, "mean wavelength");
	/* Unusable spectrum replies keep the link */
	spectral_refused(1, 0, "NA", inst_protocol_error, CR250_SPECTRUM_SHAPE, "RM Spectrum:NA");
	spectral_refused(1, 0, "bad", inst_protocol_error, CR250_SPECTRUM_SHAPE, NULL);
	spectral_refused(1, 0, "grid", inst_protocol_error, CR250_SPECTRUM_SHAPE, NULL);
	spectral_refused(1, 0, "ER", inst_misread, 0, "Light intensity too low");
	spectral_refused(1, 0, "tiny", inst_protocol_error, CR250_SPECTRUM_SHAPE, NULL);
	/* Each comparison on its own: the others agree in these */
	{
		char radiometric[96];
		double power, photons;
		static const struct { double power, photons; const char *xyz, *text; } cases[] = {
			{ 2.0, 2.0, NULL, "power 0.5, " },			/* Radiant power alone */
			{ 1 / 1.11, 1 / 1.11, NULL, "power 1.11, " },	/* Just above the 1.1 bound */
			{ 1 / 0.89, 1 / 0.89, NULL, "power 0.89, " },	/* Just below the 0.9 bound */
			{ 1.0, 1.05, NULL, "mean wavelength" },		/* Power-weighted mean wavelength alone */
			{ 1.0, 1.0, "20,25,6.25", "chromaticity error 0.146" },	/* Chromaticity alone */
			/* Luminance alone: native XYZ / 1.15 keeps chromaticity within bounds */
			{ 1.0, 1.0, "10.869565,21.739130,5.434783", "luminance 1.15 " }
		};
		for (n = 0; n < 6; n++) {
			p = make_driver(); c = (cr250 *)p;
			sscanf(F.radiometric, "0,%lf,%lf", &power, &photons);
			snprintf(radiometric, sizeof(radiometric), "0,%.6e,%.6e",
			         power * cases[n].power, photons * cases[n].photons);
			F.override_cmd = "RM Radiometric"; F.override_value = radiometric;
			if (cases[n].xyz) { F.override2_cmd = "RM XYZ"; F.override2_value = cases[n].xyz; }
			CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
			rv = p->read_sample(p, "", &out, instNoClamp);
			CHECK((rv & inst_imask) == CR250_SPECTRUM_MISMATCH && !out.XYZ_v);
			CHECK(strstr(p->interp_error(p, rv), cases[n].text) != NULL);
			CHECK(c->capture.unit.verdict == CR250_UNIT_MISMATCH);
			p->del(p);
		}
		/* A finite native mean of ~1e283 nm prints hundreds of digits: the text
		 * is cut to its size and stays a string (see refuse() in cr250.c) */
		p = make_driver(); c = (cr250 *)p;
		sscanf(F.radiometric, "0,%lf,%lf", &power, &photons);
		snprintf(radiometric, sizeof(radiometric), "0,%.6e,%.6e", power, photons * 1e280);
		F.override_cmd = "RM Radiometric"; F.override_value = radiometric;
		CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
		CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_MISMATCH);
		CHECK(c->capture.unit.mean_nm > 1e280);
		CHECK(memchr(c->capture.problem, '\000', sizeof(c->capture.problem)) != NULL);
		CHECK(strlen(c->capture.problem) == sizeof(c->capture.problem) - 1);
		CHECK(strstr(p->interp_error(p, CR250_SPECTRUM_MISMATCH), "mean wavelength") != NULL);
		p->del(p);
	}
	/* Framing failures do not */
	{
		/* "late": a line arriving during the 200 ms note must fail RM Spectrum
		 * itself, not the next request */
		static const char *faults[] = { "extra", "short", "late" };
		for (n = 0; n < 3; n++) {
			p = make_driver(); c = (cr250 *)p; F.sp_fault = faults[n];
			CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
			CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_mask) != inst_ok);
			CHECK(c->engine.uncertain && !c->engine.identified && F.closes == 1 && !out.XYZ_v);
			CHECK(c->capture.spectrum.count == 0 && unlocked(p));
			p->del(p);
		}
	}
	/* A grid outside the wavelengths a spectroradiometer reports */
	spectral_refused(1.0, 1000.0, NULL, inst_protocol_error, 0, "publication size or integration grid limits");
	/* Too dim to tell: refused and recorded as inconclusive. Native bins stay. */
	p = make_driver(); c = (cr250 *)p; F.sp_level = 0.001; cr250_fake_spectrum();
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_UNVERIFIED);
	CHECK(!out.XYZ_v && out.sp.spec_n == 0 && c->capture.spectrum.count == CR250_FAKE_SP_N);
	CHECK(c->capture.unit.verdict == CR250_UNIT_INCONCLUSIVE && c->capture.unit.discriminating == 0);
	p->del(p);
	/* A dim reading does not excuse a bright spectrum: the spectrum's own
	 * totals are above the floor, so the comparison is made and a spectrum
	 * sent in mW where the totals are in W is refused */
	p = make_driver(); c = (cr250 *)p; F.sp_level = 0.001; cr250_fake_spectrum(); F.sp_scale = 1000;
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_MISMATCH);
	CHECK(c->capture.unit.verdict == CR250_UNIT_MISMATCH && (c->capture.unit.discriminating & 5) == 5);
	CHECK(fabs(c->capture.unit.power_ratio / 1000 - 1) < 1e-3 && fabs(c->capture.unit.y_ratio / 1000 - 1) < 1e-3);
	CHECK(strstr(p->interp_error(p, CR250_SPECTRUM_MISMATCH), "nearest factor 1000)") != NULL);
	CHECK(!out.XYZ_v && out.sp.spec_n == 0 && c->capture.spectrum.count == CR250_FAKE_SP_N);
	CHECK(F.measures == 1 && c->engine.identified && !c->engine.uncertain && unlocked(p));
	p->del(p);
	/* An instrument total of zero has no ratio: a bright spectrum against it
	 * is refused, not taken as agreeing */
	p = make_driver(); c = (cr250 *)p;
	F.override_cmd = "RM XYZ"; F.override_value = "0,0,0";
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_MISMATCH);
	CHECK((c->capture.unit.discriminating & CR250_UNIT_Y) && !(c->capture.unit.computed & CR250_UNIT_Y));
	CHECK(c->capture.unit.y_ratio == 0 && !out.XYZ_v);
	CHECK(strstr(p->interp_error(p, CR250_SPECTRUM_MISMATCH), "luminance no value") != NULL);
	p->del(p);
	/* Nor has a total so small the ratio overflows: no value, not infinity */
	p = make_driver(); c = (cr250 *)p;
	F.override_cmd = "RM XYZ"; F.override_value = "12.5,1e-307,6.25";
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_MISMATCH);
	CHECK((c->capture.unit.discriminating & CR250_UNIT_Y) && !(c->capture.unit.computed & CR250_UNIT_Y));
	CHECK(c->capture.unit.y_ratio == 0 && !out.XYZ_v);
	p->del(p);
	/* Nor has a negative total, as a signed dark reading can give: no value,
	 * never a negative factor. Zero alone is caught by the finite check too */
	p = make_driver(); c = (cr250 *)p;
	F.override_cmd = "RM XYZ"; F.override_value = "12.5,-5,6.25";
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_MISMATCH);
	CHECK((c->capture.unit.discriminating & CR250_UNIT_Y) && !(c->capture.unit.computed & CR250_UNIT_Y));
	CHECK(c->capture.unit.y_ratio == 0 && !out.XYZ_v);
	CHECK(strstr(p->interp_error(p, CR250_SPECTRUM_MISMATCH), "luminance no value") != NULL);
	p->del(p);
	p = make_driver(); c = (cr250 *)p;
	F.override_cmd = "RM Radiometric"; F.override_value = "0,-0.5,1e18";
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_MISMATCH);
	CHECK((c->capture.unit.discriminating & CR250_UNIT_POWER) && !(c->capture.unit.computed & CR250_UNIT_POWER));
	CHECK(c->capture.unit.power_ratio == 0 && !(c->capture.unit.discriminating & CR250_UNIT_AXIS));
	CHECK(strstr(p->interp_error(p, CR250_SPECTRUM_MISMATCH), "power no value") != NULL);
	p->del(p);
	/* Near the floor, the wavelength axis and chromaticity are compared only
	 * when the instrument's own side is above it: a native mean wavelength or
	 * chromaticity from light below the floor is noise, not a reference */
	{
		double power1, photons1, level;
		cr250_fake_reset();			/* The default radiometric reply, not the last case's */
		CHECK(sscanf(F.radiometric, "0,%lf,%lf", &power1, &photons1) == 2);
		level = 3.1e-4 / power1;				/* Spectrum power just above its floor */
		p = make_driver(); c = (cr250 *)p; F.sp_level = level; cr250_fake_spectrum();
		{
			static char rad[96];				/* Native power just below it, photons 10% off */
			snprintf(rad, sizeof(rad), "0,%.6e,%.6e", 2.9e-4, photons1 * level * (2.9 / 3.1) * 1.1);
			F.override_cmd = "RM Radiometric"; F.override_value = rad;
		}
		CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
		CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.sp.spec_n == CR250_FAKE_SP_N);
		CHECK(c->capture.unit.verdict == CR250_UNIT_CONSISTENT && (c->capture.unit.discriminating & 3) == 1);
		p->del(p);
		/* Spectrum Y 0.205 against a native 0.19 whose chromaticity is off */
		p = make_driver(); c = (cr250 *)p; F.sp_level = 0.0082; cr250_fake_spectrum();
		F.override_cmd = "RM XYZ"; F.override_value = "0.2,0.19,0.03";
		CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
		CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.sp.spec_n == CR250_FAKE_SP_N);
		CHECK(c->capture.unit.verdict == CR250_UNIT_CONSISTENT && (c->capture.unit.computed & CR250_UNIT_Y));
		CHECK(fabs(c->capture.unit.y_ratio - 0.205 / 0.19) < 1e-3);
		/* Chromaticity was not compared, so it has no value, not a zero error */
		CHECK(!((c->capture.unit.discriminating | c->capture.unit.computed) & CR250_UNIT_CHROMA));
		p->del(p);
	}
	/* The one case left inconclusive: both sides dim, so a spectrum a
	 * thousand times too small cannot be told from a dark one and is refused. */
	p = make_driver(); c = (cr250 *)p; F.sp_level = 0.001; cr250_fake_spectrum(); F.sp_scale = 0.001;
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_UNVERIFIED);
	CHECK(!out.XYZ_v && out.sp.spec_n == 0 && c->capture.spectrum.count == CR250_FAKE_SP_N);
	CHECK(c->capture.unit.verdict == CR250_UNIT_INCONCLUSIVE && c->capture.unit.discriminating == 0);
	p->del(p);
	/* Finite values whose scaling or integration overflows are refused, dim
	 * or bright, never published as infinite; the values as sent are kept */
	{
		double level[2] = { 0.001, 1.0 };
		const char *fault[2] = { "huge", "large" };
		int k, j;
		for (k = 0; k < 4; k++) {
			p = make_driver(); c = (cr250 *)p; F.sp_level = level[k % 2]; cr250_fake_spectrum();
			F.sp_fault = fault[k / 2];
			CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
			CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_MISMATCH);
			CHECK(strstr(p->interp_error(p, CR250_SPECTRUM_MISMATCH), "not finite") != NULL);
			CHECK(c->capture.unit.verdict == CR250_UNIT_MISMATCH && !out.XYZ_v && out.sp.spec_n == 0);
			CHECK(c->capture.unit.computed == 0 && c->capture.unit.discriminating == 0);
			for (j = 0; j < c->capture.spectrum.count; j++) CHECK(cr250_finite(c->capture.spectrum.value[j]));
			CHECK(c->capture.spectrum.count == CR250_FAKE_SP_N && !c->engine.uncertain && unlocked(p));
			p->del(p);
		}
	}
	/* Signed values that cancel in the sum and fall between the converter's
	 * points: only the per-value check sees that each overflows in mW, so
	 * without it a dim read would publish two infinite values */
	p = make_driver(); c = (cr250 *)p; F.sp_level = 0.001; cr250_fake_spectrum(); F.sp_fault = "cancel";
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_MISMATCH);
	CHECK(strstr(p->interp_error(p, CR250_SPECTRUM_MISMATCH), "not finite") != NULL);
	CHECK(!out.XYZ_v && out.sp.spec_n == 0 && c->capture.unit.computed == 0);
	CHECK(c->capture.spectrum.count == 20 && c->capture.spectrum.value[5] == 2e305);
	p->del(p);
	/* Native XYZ whose sum overflows: the chromaticity error is still formed,
	 * about 1/2 here, not an infinite total that makes any error vanish */
	p = make_driver(); c = (cr250 *)p;
	F.override_cmd = "RM XYZ"; F.override_value = "1e308,25,1e308";
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_MISMATCH);
	CHECK(c->capture.unit.verdict == CR250_UNIT_MISMATCH && (c->capture.unit.computed & CR250_UNIT_CHROMA));
	CHECK(fabs(c->capture.unit.chroma_error - 0.5) < 1e-9 && c->capture.xyz[0] == 1e308 && !out.XYZ_v);
	CHECK(strstr(p->interp_error(p, CR250_SPECTRUM_MISMATCH), "chromaticity error 0.5") != NULL);
	p->del(p);
	/* Bright native Y whose X+Y+Z is not positive: no chromaticity error can
	 * be formed, and that refuses the read rather than passing as zero */
	p = make_driver(); c = (cr250 *)p;
	F.override_cmd = "RM XYZ"; F.override_value = "-20,25,-10";
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_MISMATCH);
	CHECK((c->capture.unit.discriminating & CR250_UNIT_CHROMA) && !(c->capture.unit.computed & CR250_UNIT_CHROMA));
	CHECK(strstr(p->interp_error(p, CR250_SPECTRUM_MISMATCH), "chromaticity error no value") != NULL);
	p->del(p);
	/* A spectrum summing to zero has no mean wavelength: bright native light
	 * compares the axis and refuses it; dim, the mean is simply absent */
	p = make_driver(); c = (cr250 *)p; F.sp_scale = 0;
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_MISMATCH);
	CHECK((c->capture.unit.discriminating & CR250_UNIT_AXIS) && !(c->capture.unit.computed & CR250_UNIT_SPECTRUM_MEAN));
	CHECK(strstr(p->interp_error(p, CR250_SPECTRUM_MISMATCH), "mean wavelength no value") != NULL);
	p->del(p);
	p = make_driver(); c = (cr250 *)p; F.sp_level = 0.001; cr250_fake_spectrum(); F.sp_scale = 0;
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_UNVERIFIED);
	CHECK(!out.XYZ_v && out.sp.spec_n == 0 && c->capture.spectrum.count == CR250_FAKE_SP_N);
	CHECK(c->capture.unit.verdict == CR250_UNIT_INCONCLUSIVE && c->capture.unit.computed == CR250_UNIT_SPECTRUM);
	p->del(p);
	/* The conversion itself, against physics rather than the fixture: a
	 * narrow line at 555 nm, where ybar is 1, carries 683 lm/W, so its Y is
	 * 683 times its power less a sliver for the line's width. */
	p = make_driver(); c = (cr250 *)p; F.sp_line = 555; cr250_fake_spectrum();
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_MISMATCH);
	CHECK(c->capture.unit.xyz[1] > 0.98 * 683.002 * c->capture.unit.spectrum_w);
	CHECK(c->capture.unit.xyz[1] < 1.0 * 683.002 * c->capture.unit.spectrum_w);
	p->del(p);

	/* Settings the contract fixes are written when found otherwise, read
	 * back. */
	p = make_driver(); c = (cr250 *)p; cr250_fake_set_setting("UserCalibMode", "Match"); cr250_fake_set_setting("CMF", "2");
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.XYZ_v);
	CHECK(cr250_fake_sent("SM CMF 0") && cr250_fake_sent("SM UserCalibMode 0") && sm_sent() == 2);
	CHECK(!strcmp(cr250_fake_setting("CMF"), "0") && !strcmp(cr250_fake_setting("UserCalibMode"), "None"));
	n = sm_sent();								/* Now in place: nothing more is written */
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && sm_sent() == n);
	/* Either documented OK context form is accepted */
	F.sm_short_context = 1; cr250_fake_set_setting("UserCalibMode", "Matrix");
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && !strcmp(cr250_fake_setting("UserCalibMode"), "None"));
	/* An instrument that keeps its value is refused before M */
	F.sm_ignored = 1; cr250_fake_set_setting("UserCalibMode", "Match"); n = F.measures;
	rv = p->read_sample(p, "", &out, instNoClamp);
	CHECK((rv & inst_mask) == inst_wrong_setup && F.measures == n && !out.XYZ_v);
	CHECK(cr250_fake_sent("SM UserCalibMode 0") && !strcmp(cr250_fake_setting("UserCalibMode"), "Match"));
	CHECK(strstr(p->interp_error(p, rv), "left RS UserCalibMode at Match, not None") != NULL);
	/* A key without a fixed contract value is still checked against what was set */
	F.sm_ignored = 1; cr250_fake_set_setting("UserCalibMode", "None"); n = F.measures;
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_emis_refresh_ovd) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SETUP && F.measures == n);
	CHECK(p->set_mode(p, inst_mode_emis_spot) == inst_ok);
	/* A write the instrument refuses fails the read with its native code */
	F.sm_ignored = 0; cr250_fake_set_setting("UserCalibMode", "Match");
	F.native_cmd = "SM UserCalibMode 0"; F.native_status = "ER:-560:Invalid User Calibration Mode:0";
	rv = p->read_sample(p, "", &out, instNoClamp);
	CHECK((rv & inst_mask) == inst_bad_parameter && F.measures == n);
	CHECK(cr250_fake_sent("SM UserCalibMode 0") && !strcmp(cr250_fake_setting("UserCalibMode"), "Match"));
	F.native_cmd = NULL; CHECK(unlocked(p));
	p->del(p);
	/* The engine writes only the contract's keys; optics declarations and a
	 * reset are refused before anything is sent. */
	{
		cr250_reply r;
		p = make_driver(); c = (cr250 *)p; n = F.writes;
		CHECK(cr250_imp_setting(&c->engine, "Accessory", "0", &r, 3) == CR250_REFUSED_COMMAND);
		CHECK(cr250_imp_setting(&c->engine, "Aperture", "0", &r, 3) == CR250_REFUSED_COMMAND);
		CHECK(cr250_imp_setting(&c->engine, "Filter1", "0", &r, 3) == CR250_REFUSED_COMMAND);
		CHECK(cr250_imp_setting(&c->engine, "Reset", "0", &r, 3) == CR250_REFUSED_COMMAND);
		CHECK(cr250_imp_setting(&c->engine, "Mode", "0", &r, 3) == CR250_REFUSED_COMMAND);
		CHECK(cr250_imp_setting(&c->engine, "ExposureX", "-1", &r, 3) == CR250_INTERNAL_ERROR);
		CHECK(cr250_imp_setting(&c->engine, "ExposureX", "1e3", &r, 3) == CR250_INTERNAL_ERROR);
		CHECK(cr250_imp_setting(&c->engine, "SyncFreq", "1.2.3", &r, 3) == CR250_INTERNAL_ERROR);
		CHECK(cr250_imp_command(&c->engine, "SM CMF 0", &r, 3) == CR250_REFUSED_COMMAND);
		CHECK(cr250_imp_command(&c->engine, "RM Spectrum", &r, 3) == CR250_REFUSED_COMMAND);
		CHECK(F.writes == n);
		p->del(p);
	}

	/* The route sets the exposure mode: non-adaptive is Fixed at the
	 * operator's exposure, adaptive is Auto. */
	p = make_driver(); c = (cr250 *)p;
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_emis_nonadaptive) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && cr250_fake_sent("SM ExposureMode 1"));
	CHECK(c->capture.fixed_exposure_ms == 100 && c->capture.budget_s == 120);
	CHECK(cr250_fake_sent("RC MinExposure") && cr250_fake_sent("RS Exposure"));
	CHECK(!cr250_fake_sent("RM MaxAutoExposure"));	/* Not read in Fixed */
	F.override_cmd = "RM Exposure"; F.override_value = "100.040 msec";		/* Within 0.1 ms (0.1% of 100 ms) */
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
	/* Below 50 ms, the absolute 0.05 ms tolerance is decisive. */
	F.override2_cmd = "RS Exposure"; F.override2_value = "10.000 msec";
	F.override_value = "10.040 msec";
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
	F.override_value = "10.060 msec";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_CAPTURE_MISMATCH);
	F.override2_cmd = NULL;
	F.override_value = "90.000 msec";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_CAPTURE_MISMATCH);
	F.override_cmd = "RS Exposure"; F.override_value = "0.5 msec";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SETUP);
	CHECK(strstr(p->interp_error(p, CR250_SETUP), "Fixed exposure 0.500 ms is outside 1.000 to 500.000 ms") != NULL);
	F.override_cmd = NULL;
	/* A long Fixed exposure is budgeted from itself: 3 x 40 s + 5, not the 60 s ceiling */
	F.override_cmd = "RS Exposure"; F.override_value = "40000.0 msec";
	F.override2_cmd = "RC MaxExposure"; F.override2_value = "60000.0 msec";
	F.override3_cmd = "RM Exposure"; F.override3_value = "40000.000 msec";
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.budget_s == 125);
	F.override_cmd = F.override2_cmd = F.override3_cmd = NULL;
	CHECK(p->set_mode(p, inst_mode_emis_spot) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && cr250_fake_sent("SM ExposureMode 0"));
	p->del(p);

	/* Averaging: checked against the instrument's limits when set, written
	 * at the read, and multiplying the budget. */
	p = make_driver(); c = (cr250 *)p;
	CHECK(p->get_set_opt(p, inst_opt_set_averages, 100) == inst_bad_parameter && c->averages == 0);
	CHECK(p->get_set_opt(p, inst_opt_set_averages, -1) == inst_bad_parameter);
	CHECK(p->get_set_opt(p, inst_opt_set_averages, 40) == inst_ok && c->averages == 40);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && cr250_fake_sent("SM ExposureX 40"));
	CHECK(c->capture.exposure_x == 40 && c->capture.budget_s == 120);	/* 65 s, under the floor */
	CHECK(p->get_set_opt(p, inst_opt_set_averages, 0) == inst_ok && c->averages == 0);
	/* An operator's multiplier is accepted and budgeted; one outside the
	 * instrument's range is refused before M */
	cr250_fake_set_setting("ExposureX", "2");
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.exposure_x == 2);
	n = F.measures; cr250_fake_set_setting("ExposureX", "51");
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SETUP && F.measures == n);
	/* The multiplier scales M's deadline: 3 * 5 s * 50 + 5 s */
	cr250_fake_set_setting("ExposureX", "50"); F.override_cmd = "RC MaxExposure"; F.override_value = "5000.0 msec";
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && F.measures == n + 1);
	CHECK(c->capture.budget_s == 755 && c->capture.exposure_x == 50);
	F.override_cmd = NULL;
	p->del(p);

	/* Sync. The refresh overrides select Auto or None; a refresh rate
	 * selects Manual at that frequency. */
	p = make_driver(); c = (cr250 *)p;
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_emis_refresh_ovd) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && cr250_fake_sent("SM SyncMode 1"));
	CHECK(cr250_fake_sent("RM SyncFreq") && c->capture.sync_hz == 60);
	CHECK(p->set_refr_rate(p, 59.94) == inst_ok && c->refresh_hz == 59.94);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
	CHECK(cr250_fake_sent("SM SyncMode 2") && cr250_fake_sent("SM SyncFreq 59.94") && c->capture.sync_hz > 59.93);
	CHECK(!strcmp(cr250_fake_setting("SyncFreq"), "59.94 Hz"));
	F.override_cmd = "RM SyncFreq"; F.override_value = "59.95 Hz";		/* Within the printed step */
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
	F.override_cmd = NULL;
	CHECK(p->set_refr_rate(p, 200) == inst_bad_parameter && p->set_refr_rate(p, NAN) == inst_bad_parameter);
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_emis_norefresh_ovd) == inst_ok && c->refresh_hz == 0);
	CHECK(p->set_refr_rate(p, 50) == inst_wrong_setup);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && cr250_fake_sent("SM SyncMode 0"));
	CHECK(!strcmp(cr250_fake_setting("SyncMode"), "None") && c->capture.sync_hz == 0);
	/* Fixed exposure under sync may round up to one period (20 ms at 50 Hz) */
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_emis_nonadaptive) == inst_ok);
	CHECK(p->set_refr_rate(p, 50) == inst_ok);
	F.override_cmd = "RM Exposure"; F.override_value = "115.000 msec";
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.sync_hz == 50);
	F.override_value = "125.000 msec";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_CAPTURE_MISMATCH);
	F.override_cmd = NULL;
	/* An operator's Manual sync outside the instrument's range is refused */
	CHECK(p->set_refr_rate(p, 0) == inst_ok && p->set_mode(p, inst_mode_emis_spot) == inst_ok);
	cr250_fake_set_setting("SyncMode", "Manual"); cr250_fake_set_setting("SyncFreq", "5.00 Hz"); n = F.measures;
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SETUP && F.measures == n);
	/* RM SyncFreq must repeat a Manual frequency */
	cr250_fake_set_setting("SyncFreq", "60.00 Hz"); F.override_cmd = "RM SyncFreq"; F.override_value = "50.00 Hz";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_CAPTURE_MISMATCH);
	F.override_cmd = NULL; CHECK(unlocked(p));
	/* A reported sync period longer than the M budget is not one the capture
	 * used, and would otherwise excuse any exposure (0.001 Hz), or overflow
	 * the allowance to infinity (1e-306 Hz) */
	{
		static const char *slow[] = { "0.001 Hz", "1e-306 Hz" };
		cr250_fake_set_setting("SyncMode", "Auto");
		for (n = 0; n < 2; n++) {
			F.override_cmd = "RM SyncFreq"; F.override_value = slow[n];
			F.override2_cmd = "RM Exposure"; F.override2_value = "600.000 msec";
			CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_CAPTURE_MISMATCH);
			CHECK(strstr(p->interp_error(p, CR250_CAPTURE_MISMATCH), "period longer than") != NULL);
			CHECK(!out.XYZ_v);
		}
		F.override_cmd = F.override2_cmd = NULL;
	}
	p->del(p);

	/* Settings a CR-250 may lack. Only an explicit "does not exist" answer on
	 * both RS and RM marks one not applicable, and it must stay so. */
	p = make_driver(); c = (cr250 *)p;
	F.override_cmd = "RS SyncMode"; F.override_value = "ER:-500:Invalid command:SyncMode";
	F.override2_cmd = "RM SyncMode"; F.override2_value = "NA";
	p->capabilities(p, &modes, &caps, &caps3);
	CHECK(caps & inst2_set_refresh_rate);				/* Not known before a read */
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_emis_refresh_ovd) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SETUP && !F.measures);
	CHECK(strstr(p->interp_error(p, CR250_SETUP), "has no SyncMode setting to set to Auto") != NULL);
	CHECK(c->presence[6] == 0 && c->capture.value[6][0] == 0 && !cr250_fake_sent("RM SyncFreq"));
	/* A missing RS alone cannot withdraw connection capabilities. Complete
	 * the same no-sync read without a refresh request, so RM confirms absence. */
	p->capabilities(p, &modes, &caps, &caps3);
	CHECK(caps & inst2_set_refresh_rate);
	CHECK(p->check_mode(p, inst_mode_emis_spot | inst_mode_emis_refresh_ovd) == inst_ok);
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_emis_norefresh_ovd) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && !cr250_fake_sent_prefix("SM "));
	CHECK(c->presence[6] == -1);
	/* Once both replies agree, refresh sync and the rate setter are withdrawn. */
	p->capabilities(p, &modes, &caps, &caps3);
	CHECK(!(caps & inst2_set_refresh_rate) && (caps3 & inst3_average));
	CHECK((modes & inst_mode_emis_norefresh_ovd) == inst_mode_emis_norefresh_ovd);
	CHECK(p->check_mode(p, inst_mode_emis_spot | inst_mode_emis_refresh_ovd) == inst_unsupported);
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_emis_refresh_ovd) == inst_unsupported);
	CHECK(p->set_refr_rate(p, 50) == inst_unsupported && p->set_refr_rate(p, 0) == inst_ok);
	CHECK(p->set_mode(p, inst_mode_emis_spot) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
	n = F.measures;
	F.override_cmd = F.override2_cmd = NULL;	/* Now it answers: not the same instrument state */
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SETUP && F.measures == n);
	p->del(p);
	p = make_driver(); c = (cr250 *)p;		/* RS answers, RM says absent */
	F.override_cmd = "RM SyncMode"; F.override_value = "NA";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_CAPTURE_MISMATCH && F.measures == 1);
	CHECK(strstr(p->interp_error(p, CR250_CAPTURE_MISMATCH), "RM SyncMode is not available but RS SyncMode was available") != NULL);
	p->del(p);
	p = make_driver(); c = (cr250 *)p;		/* RS says absent, RM answers */
	F.override_cmd = "RS RangeMode"; F.override_value = "NA";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_CAPTURE_MISMATCH && F.measures == 1);
	p->del(p);
	p = make_driver(); c = (cr250 *)p;		/* Not applicable on both sides: no Range reads */
	F.override_cmd = "RS RangeMode"; F.override_value = "NA"; F.override2_cmd = "RM RangeMode"; F.override2_value = "NA";
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && !cr250_fake_sent("RM Range"));
	CHECK(out.XYZ_v && !cr250_fake_sent("RS Range"));
	p->del(p);
	/* Only "does not exist" marks a setting not applicable; another error refuses */
	rejected("RS SyncMode", "ER:-521:Invalid Sync Mode:SyncMode", 0);
	/* A required setting cannot be missing */
	rejected("RS Speed", "ER:-500:Invalid command:Speed", 0);
	rejected("RS Accessory", "NA", 0);
	/* Fixed range: RS Range must be listed and repeated by RM Range */
	p = make_driver(); c = (cr250 *)p;
	F.override_cmd = "RS RangeMode"; F.override_value = "Fixed"; F.override2_cmd = "RM RangeMode"; F.override2_value = "Fixed";
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && cr250_fake_sent("RS Range"));
	CHECK(!strcmp(c->capture.range.name, "A") && cr250_fake_sent("RM Range"));
	p->del(p);
	/* A single-mode instrument names its own mode; a mode that is merely
	 * listed first among several is not accepted */
	p = make_driver(); c = (cr250 *)p;
	F.override_cmd = "RS Mode"; F.override_value = "Spectroradiometer";
	F.override2_cmd = "RM Mode"; F.override2_value = "Spectroradiometer";
	F.override3_cmd = "RC Mode"; F.override3_value = "1\r\n0,Spectroradiometer";
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.XYZ_v);
	F.override3_value = "2\r\n0,Spectroradiometer\r\n1,Flicker";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SETUP);
	F.override3_cmd = NULL;					/* The fixture's three-mode list */
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SETUP);
	p->del(p);
	/* A Mode inventory request that fails is not a complete wrong setup. */
	p = make_driver();
	F.override_cmd = "RS Mode"; F.override_value = "Spectroradiometer";
	F.native_cmd = "RC Mode";
	F.native_status = "ER:-560:Invalid Mode:Mode";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask)
	      == CR250_NATIVE_ERROR_BASE + 560 && F.measures == 0);
	p->del(p);
	p = make_driver();
	F.override_cmd = "RS Mode"; F.override_value = "Spectroradiometer";
	F.timeout_cmd = "RC Mode";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask)
	      == CR250_TIMEOUT && F.measures == 0);
	p->del(p);

	rejected("RS UserCalibMode", "Match", 0);	/* Written, but the fixture keeps it */
	rejected("RS SyncMode", "Bogus", 0); rejected("RS RangeMode", "Bogus", 0);
	rejected("RS ExposureX", "2.5", 0); rejected("RS Speed", "Turbo", 0);
	rejected("RS Filter", "ND-100-1", 0); /* RS requires all three slots for an active filter. */
	rejected("RS Filter", "None,", 0); rejected("RS Filter", "", 0);
	rejected("RM Filter", "ND-100-1", 1); rejected("RM Filter", "None,ND-100-3", 1);
	rejected("RM Filter", "None,", 1); rejected("RM Filter", "NoneOther", 1);
	rejected("RS Mode", "Flicker", 0);
	rejected("RC Accessory", "1\r\n0,Standard,Irradiance", 0);
	rejected("RC Accessory", "2\r\n0,Standard,Radiance\r\n1,Standard,Radiance", 0);
	rejected("RC Aperture", "1\r\n0,2 deg", 0);
	rejected("RC MaxExposure", "500 seconds", 0); rejected("RC MaxExposure", "nan msec", 0);
	rejected("RC MaxExposureX", "0x32", 0);
	rejected("RM CMF", "1", 1); rejected("RM Accessory", "Other", 1); rejected("RM Speed", "Fast", 1);
	rejected("RM ID", "DIFFERENT", 1); rejected("RM Model", "CR-300", 1);
	rejected("RM Warnings", "100,101", 1); rejected("RM Radiometric", "1,2,3", 1);
	rejected("RM Exposure", "501 msec", 1); rejected("RM MaxAutoExposure", "501 msec", 1);
	rejected("RM MaxAutoExposure", "1 msec", 1); rejected("RM Range", "Z", 1);
	rejected("RM XYZ", "1,2", 1); rejected("RM XYZ", "1,2,3,4", 1);
	rejected("RM XYZ", "1,NaN,3", 1); rejected("RM XYZ", "1,2,3junk", 1);
	rejected("RM XYZ", "0x1p0,2,3", 1); rejected("RM XYZ", "1e999,2,3", 1);
	p = make_driver(); c = (cr250 *)p;		/* A model that does not report its Auto limit */
	F.override_cmd = "RM MaxAutoExposure"; F.override_value = "ER:-500:Invalid command:MaxAutoExposure";
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.XYZ_v);
	p->del(p);
	p = make_driver(); F.native_cmd = "RS CMF"; F.native_status = "OK:100:RS CMF:0";
	CHECK((p->read_sample(p,"",&out,instNoClamp) & inst_mask) == inst_warning);
	CHECK(((cr250 *)p)->engine.last_reply.code == 100 && F.measures == 0);
	p->del(p);
	p = make_driver(); F.native_cmd = "M"; F.native_status = "OK:101:M:Cannot sync to constant light source";
	CHECK((p->read_sample(p,"",&out,instNoClamp) & inst_mask) == inst_warning && F.measures == 1 && !out.XYZ_v);
	CHECK(strstr(p->interp_error(p, CR250_NATIVE_WARNING_BASE + 101), "101:M:Cannot sync to constant light source") != NULL);
	CHECK(unlocked(p));
	p->del(p);
	p = make_driver(); F.timeout_measure = 1;
	CHECK((p->read_sample(p,"",&out,instNoClamp) & inst_mask) == inst_coms_fail);
	CHECK(F.measures == 1 && F.closes == 1 && !((cr250 *)p)->engine.identified);
	n = F.writes;
	CHECK(p->read_sample(p,"",&out,instNoClamp) != inst_ok && F.writes == n && unlocked(p));
	F.timeout_measure = 0;
	CHECK(p->init_coms(p,baud_38400,fc_nc,3) == inst_ok && p->init_inst(p) == inst_ok);
	CHECK(p->read_sample(p,"",&out,instNoClamp) == inst_ok && F.measures == 2);
	p->del(p); CHECK(F.closes == 2 && F.deleted == 1);
	p = make_driver(); F.override_cmd = "RC Firmware"; F.override_value = "1.25";
	CHECK(p->init_coms(p,baud_38400,fc_nc,3) == inst_ok);
	CHECK((p->init_inst(p) & inst_mask) == inst_unsupported);
	CHECK(strstr(p->interp_error(p, CR250_FIRMWARE), "CR-250 measurement requires firmware 1.26") != NULL);
	CHECK(p->read_sample(p,"",&out,instNoClamp) == inst_no_init && F.measures == 0);
	CHECK(p->init_coms(p,baud_38400,fc_XonXOff,3) == inst_unsupported);
	CHECK(p->init_coms(p,baud_38400,fc_nc,INFINITY) == inst_bad_parameter);
	CHECK(!((cr250 *)p)->engine.identified && ((cr250 *)p)->engine.id.instrument_type == -1);
	CHECK(unlocked(p));
	p->del(p);
	/* Initialisation writes nothing: settings change only when a read runs */
	p = make_driver(); CHECK(!cr250_fake_sent_prefix("SM ") && !cr250_fake_sent_prefix("RS ")); p->del(p);

	/* CR-100 row: a colorimeter. It offers no spectrum and no Fixed route,
	 * sends none of the spectroradiometer-only commands, requires RangeMode,
	 * and accepts InstrumentType 0 or 1. */
	p = make_driver_of(instCR100); c = (cr250 *)p;
	CHECK(!strcmp(c->engine.id.model, "CR-100") && c->engine.id.instrument_type == 1);
	p->capabilities(p, &modes, &caps, &caps3);
	CHECK(!(modes & inst_mode_spectral) && !(modes & inst_mode_emis_nonadaptive));
	CHECK(p->check_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_unsupported);
	CHECK(p->check_mode(p, inst_mode_emis_spot | inst_mode_emis_nonadaptive) == inst_unsupported);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.XYZ_v && out.XYZ[1] == 25);
	CHECK(out.sp.spec_n == 0);
	CHECK(!cr250_fake_sent_prefix("RS CMF") && !cr250_fake_sent_prefix("RM CMF") && !cr250_fake_sent_prefix("RS Speed") && !cr250_fake_sent_prefix("RC Speed"));
	CHECK(!cr250_fake_sent_prefix("RS Aperture") && !cr250_fake_sent_prefix("RC Aperture") && !cr250_fake_sent_prefix("RM Radiometric"));
	CHECK(!cr250_fake_sent_prefix("RM Spectrum") && !cr250_fake_sent_prefix("RM XYZ10") && !cr250_fake_sent_prefix("SM CMF"));
	CHECK(cr250_fake_sent("RS UserCalibMode") && cr250_fake_sent("RC Accessory") && cr250_fake_sent("RM MaxAutoExposure"));
	cr250_fake_set_setting("UserCalibMode", "Match");	/* It writes the contract's keys too */
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && cr250_fake_sent("SM UserCalibMode 0"));
	F.override_cmd = "RS Accessory"; F.override_value = "Other";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_mask) == inst_wrong_setup && !out.XYZ_v);
	CHECK(strstr(p->interp_error(p, CR250_SETUP), "CR-100: RS Accessory Other names no single RC Accessory record of type Radiance") != NULL);
	F.override_cmd = "RS RangeMode"; F.override_value = "NA";	/* Required on this row */
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_mask) == inst_wrong_setup);
	/* A 300 character value outruns the 192 byte refusal text, and native
	 * text beside it outruns interp_error's 256. Those results stay bounded
	 * strings (see refuse() in cr250.c). A 127 byte context and 370 byte value
	 * now fit the enlarged final message and must remain complete. */
	{
		static char longval[301];
		const char *msg;
		memset(longval, 'x', 300); longval[300] = '\000';
		F.override_cmd = "RS Accessory"; F.override_value = longval;
		CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_mask) == inst_wrong_setup && !out.XYZ_v);
		CHECK(memchr(c->capture.problem, '\000', sizeof(c->capture.problem)) != NULL);
		CHECK(strlen(c->capture.problem) == sizeof(c->capture.problem) - 1);
		CHECK(strstr(p->interp_error(p, CR250_SETUP), "CR-100: RS Accessory xxx") != NULL);
		msg = p->interp_error(p, CR250_NATIVE_WARNING_BASE + 100);
		CHECK(strstr(msg, "Light intensity too low for automatic sync (RS Accessory xxx") != NULL);
		CHECK(strstr(msg, "; last reply ") != NULL);
		c->engine.last_reply.complete = 1; c->engine.last_reply.code = -500;
		memset(c->engine.last_reply.context, 'c', 127); c->engine.last_reply.context[127] = '\000';
		memset(c->engine.last_reply.value, 'v', 370); c->engine.last_reply.value[370] = '\000';
		msg = p->interp_error(p, CR250_NATIVE_WARNING_BASE + 100);
		CHECK(memchr(msg, '\000', sizeof(c->error_message)) != NULL);
		CHECK(strlen(msg) < sizeof(c->error_message) && strstr(msg, "; last reply -500:ccc") != NULL);
		CHECK(strlen(msg) >= 370 && !strcmp(msg + strlen(msg) - 370, c->engine.last_reply.value));
		/* Stress both declared internal field capacities independently. Their
		 * combination exceeds a legal wire line, but the diagnostic must still
		 * terminate safely when its final output is cut to the host buffer. */
		memset(c->engine.last_reply.context, 'c', sizeof(c->engine.last_reply.context) - 1);
		c->engine.last_reply.context[sizeof(c->engine.last_reply.context) - 1] = '\000';
		memset(c->engine.last_reply.value, 'v', sizeof(c->engine.last_reply.value) - 1);
		c->engine.last_reply.value[sizeof(c->engine.last_reply.value) - 1] = '\000';
		msg = p->interp_error(p, CR250_NATIVE_WARNING_BASE + 100);
		CHECK(memchr(msg, '\000', sizeof(c->error_message)) != NULL);
		CHECK(strlen(msg) == sizeof(c->error_message) - 1 && strstr(msg, "; last reply -500:ccc") != NULL);
	}
	F.override_cmd = NULL;
	F.type = "0";							/* A photometer answer is accepted */
	CHECK(p->init_coms(p, baud_38400, fc_nc, 3) == inst_ok && c->engine.id.instrument_type == 0);
	F.type = "2";							/* A spectroradiometer answer is not */
	CHECK((p->init_coms(p, baud_38400, fc_nc, 3) & inst_mask) == inst_unknown_model);
	CHECK(!c->engine.identified && p->init_inst(p) == inst_no_coms);
	p->del(p); CHECK(F.deleted == 1);
	p = new_driver_of(instCR100); F.override_cmd = "RC Firmware"; F.override_value = "1.16";
	CHECK(p->init_coms(p, baud_38400, fc_nc, 3) == inst_ok && (p->init_inst(p) & inst_mask) == inst_unsupported);
	CHECK(strstr(p->interp_error(p, CR250_FIRMWARE), "CR-100 measurement requires firmware 1.17") != NULL);
	p->del(p);
	p = make_driver_of(instCR100); c = (cr250 *)p;	/* Below 1.26: no Auto-limit read */
	F.override_cmd = "RC Firmware"; F.override_value = "1.20";
	CHECK(p->init_coms(p, baud_38400, fc_nc, 3) == inst_ok && p->init_inst(p) == inst_ok);
	F.override_cmd = NULL; F.sent_n = 0; F.sent[0] = 0;
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && !cr250_fake_sent("RM MaxAutoExposure"));
	p->del(p);
	/* The registered CR-250 row is exact: a CR-100 answering it is refused */
	p = new_driver(); F.model = "CR-100"; F.type = "1";
	CHECK((p->init_coms(p, baud_38400, fc_nc, 3) & inst_mask) == inst_unknown_model);
	CHECK(strstr(p->interp_error(p, CR250_UNKNOWN_MODEL), "RC Model:CR-100") != NULL);
	p->del(p);

	/* A dtype without a row gets no driver, and the constructor keeps the
	 * transport for the caller to release */
	{
		icompath typed = path;
		icoms *icom;
		typed.dtype = instCR250;
		icom = new_icoms(&typed, g_log);
		CHECK(new_cr250(icom, instKleinK10) == NULL && new_cr250(NULL, instCR250) == NULL);
		F.deleted = 0; icom->del(icom); CHECK(F.deleted == 1);
	}
	/* The longest path this driver can take keeps every reply, down to the
	 * spectrum header: 16 settings, every key written, Fixed exposure,
	 * Manual sync with its frequency written, Fixed range, spectral mode. */
	{
		cr250_setting settings[CR250_MAX_SETUP + 1];
		cr250_model model;
		int i, k = 0;
		p = make_driver(); c = (cr250 *)p; model = *c->model;
		for (i = 0; c->model->setup[i].key; i++) settings[k++] = c->model->setup[i];
		while (k < CR250_MAX_SETUP) settings[k++] = (cr250_setting){ "Speed", NULL, 1, NULL, 0, 0 };
		settings[k] = (cr250_setting){ NULL, NULL, 0, NULL, 0, 0 };
		model.setup = settings; c->model = &model;
		cr250_fake_set_setting("CMF", "1"); cr250_fake_set_setting("UserCalibMode", "Match");
		F.override_cmd = "RS RangeMode"; F.override_value = "Fixed"; F.override2_cmd = "RM RangeMode"; F.override2_value = "Fixed";
		CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral | inst_mode_emis_nonadaptive) == inst_ok);
		CHECK(p->get_set_opt(p, inst_opt_set_averages, 5) == inst_ok && p->set_refr_rate(p, 59.94) == inst_ok);
		n = sm_sent();
		CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.XYZ_v && out.sp.spec_n == CR250_FAKE_SP_N);
		CHECK(sm_sent() == n + 6 && cr250_fake_sent("RM Spectrum"));
		CHECK(c->capture.unit.verdict == CR250_UNIT_CONSISTENT && c->capture.sync_hz == 59.94);
		p->del(p);
	}
	/* An oversized table is refused before any read or M, not truncated. */
	{
		cr250_setting settings[CR250_MAX_SETUP + 2];
		cr250_model model;
		int i, prior_writes;
		p = make_driver(); c = (cr250 *)p; model = *c->model;
		for (i = 0; i <= CR250_MAX_SETUP; i++)
			settings[i] = (cr250_setting){ "Mode", "Colorimeter", 0, NULL, 0, 0 };
		settings[i] = (cr250_setting){ NULL, NULL, 0, NULL, 0, 0 };
		model.setup = settings; c->model = &model; prior_writes = F.writes;
		CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_mask) == inst_internal_error);
		CHECK(!out.XYZ_v && F.measures == 0 && F.writes == prior_writes);
		p->del(p);
	}
	test_numeric_preflight();
	test_sync_inventories();
	test_errors_after_success();
	test_corrections_and_cr300();
	test_full_width_profile_ids();
	test_maximum_profiles();
	test_active_filters();
	test_duplicate_filters();
	test_interleaved_capture_reset();
	test_unit_publication();
	printf("cr250_driver_test: %d checks, %d failures\n", checks, failures);
	return failures ? 1 : 0;
}
#endif
