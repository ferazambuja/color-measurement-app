/*
 * Synthetic tests for the PR remote protocol engine. No OS transport is linked
 * or called. Transcripts are constructed from documented field shapes; they
 * do not reproduce the manufacturer's manual examples.
 * Copyright 2026 Fernando Voltolini de Azambuja.
 * SPDX-License-Identifier: GPL-2.0-or-later
 * This material is licensed under the GNU GENERAL PUBLIC LICENSE Version 2
 * or later.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#ifndef SALONEINSTLIB
#include "copyright.h"
#include "aconfig.h"
#include "numlib.h"
#else
#include "sa_config.h"
#include "numsup.h"
#endif
#include "cgats.h"
#include "xspect.h"
#include "insttypes.h"
#include "conv.h"
#include "icoms.h"
#include "inst.h"
#include "pr655_imp.h"

typedef struct { const char *command, *reply; } step;
typedef struct {
	icoms icom;
	step steps[64];
	int count, next, fragment, writes, fail_write, command_bytes;
	char command[32], wire[4096];
	int wire_bytes;
	const char *reply;
	int read_delay_ms, purge_read_delay_ms;
	/* Input arriving before steps[late_step] is sent, after late_skip idle reads. */
	const char *late;
	int late_step, late_skip;
} fake;

static unsigned int ticks;
static int checks, failures;
static fake transport;
static pr655imp engine;
static pr655_capture capture;
static char native_spectrum[PR655_MAX_RESPONSE + 1];
static char grid_reply[256], numeric_setup[512], text_setup[1024];

#define CHECK(c) do { checks++; if (!(c)) { \
	fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); failures++; } } while (0)

static unsigned int test_now(void) { return ticks; }
static void test_sleep(unsigned int ms) { ticks += ms; }

static int fake_write(icoms *icom, char *b, int n, double timeout) {
	fake *p = (fake *)icom->icntx;
	const char *wanted;
	(void)timeout;
	if (n != 1 || p->next >= p->count) return ICOM_SYS;
	p->writes++;
	if (p->writes == p->fail_write) return ICOM_TO | ICOM_SERW;
	if (p->wire_bytes >= (int)sizeof(p->wire) - 1
	 || p->command_bytes >= (int)sizeof(p->command) - 1) return ICOM_SIZE;
	p->wire[p->wire_bytes++] = b[0]; p->wire[p->wire_bytes] = '\000';
	p->command[p->command_bytes++] = b[0]; p->command[p->command_bytes] = '\000';
	wanted = p->steps[p->next].command;
	if (strncmp(p->command, wanted, p->command_bytes) != 0) {
		fprintf(stderr, "Unexpected command '%s', wanted '%s'\n", p->command, wanted);
		return ICOM_SYS;
	}
	if ((size_t)p->command_bytes == strlen(wanted)) {
		p->reply = p->steps[p->next++].reply;
		p->command_bytes = 0;
	}
	return ICOM_OK;
}

static int fake_read(icoms *icom, char *b, int size, int *read_bytes,
                     char *terminators, int needed, double timeout) {
	fake *p = (fake *)icom->icntx;
	int n;
	(void)terminators; (void)needed;
	*read_bytes = 0; b[0] = '\000';
	if (p->late != NULL && p->next == p->late_step && (p->reply == NULL || *p->reply == '\000')) {
		if (p->late_skip > 0) p->late_skip--;
		else {
			n = (int)strlen(p->late);
			memcpy(b, p->late, n); b[n] = '\000';
			p->late = NULL; *read_bytes = n;
			return ICOM_OK;
		}
	}
	if (p->reply == NULL || *p->reply == '\000') {
		ticks += (unsigned int)ceil(timeout * 1000.0);
		return ICOM_TO;
	}
	if (timeout > 0.001 && p->read_delay_ms > 0) ticks += p->read_delay_ms;
	if (timeout <= 0.001 && p->purge_read_delay_ms > 0) ticks += p->purge_read_delay_ms;
	n = (int)strlen(p->reply);
	if (n > p->fragment) n = p->fragment;
	if (n > size - 1) n = size - 1;
	memcpy(b, p->reply, n); b[n] = '\000';
	p->reply += n;
	if (*p->reply == '\000') p->reply = NULL;
	*read_bytes = n;
	return ICOM_OK;
}

static void add(const char *command, const char *reply) {
	transport.steps[transport.count].command = command;
	transport.steps[transport.count++].reply = reply;
}

static void reset(int model, int fragment) {
	pr655_imp_dispose(&engine);
	pr655_capture_clear(&capture);
	memset(&transport, 0, sizeof(transport)); ticks = 0;
	transport.fragment = fragment;
	transport.icom.icntx = &transport;
	transport.icom.read = fake_read; transport.icom.write = fake_write;
	pr655_imp_init(&engine, &transport.icom, model);
	engine.now = test_now; engine.sleep = test_sleep;
}

static void settings(int cycles, int sync, int hz) {
	snprintf(numeric_setup, sizeof(numeric_setup),
	         "0000,8,-1,-1,-1,2,1,73,0,87,%d,2,0,%d,91,%.2f\r\n", cycles, sync, (double)hz);
	snprintf(text_setup, sizeof(text_setup),
	         "0000,Test lens,None,None,None,Half degree,Metric,Adaptive,0 msec,Native gain,%d cycles,2 deg,No Smart Dark,%s,Standard Sensitivity,%.2f Hertz\r\n",
	         cycles, sync == 0 ? "No Sync" : sync == 1 ? "Auto Sync" : "User Sync", (double)hz);
}

static void connect_steps(int model) {
	add("Q", ""); add("PHOTO", "PHOTO\r\nREMOTE MODE\r\n");
	add("D111\r", model == 655 ? "0000,PR-655\r\n" : "0000,PR-670\r\n");
	add("D110\r", "0000,SYNTHETIC-P7\r\n");
	add("D114\r", "0000,9.01-test\r\n");
	snprintf(grid_reply, sizeof(grid_reply), "0000,%d,4.75,380,780,%d,%d,9,%d\r\n",
	         model == 655 ? 101 : 201, model == 655 ? 4 : 2,
	         model == 655 ? 128 : 256, model == 655 ? 118 : 242);
	add("D120\r", grid_reply);
	settings(1, 0, 75);
	add("D601\r", numeric_setup); add("D602\r", text_setup);
	add("D112\r", "0000,1,3\r\n");
	add("D116\r", "0000,8,Test lens,Primary,Luminance,Radiance\r\n");
}

static void connect(int model, int fragment) {
	reset(model, fragment); connect_steps(model);
	CHECK(pr655_imp_open(&engine, 10.0) == PR655_OK);
	CHECK(pr655_imp_identity(&engine, 10.0) == PR655_OK);
	CHECK(pr655_imp_read_setup(&engine, 10.0) == PR655_OK);
	CHECK(engine.setup.exposure_mode == 73 && engine.setup.gain == 87 && engine.setup.capture_mode == 91);
	CHECK(engine.setup.adaptive_known && engine.setup.adaptive && !engine.setup.extended);
	CHECK(engine.setup.frequency_hz == 75.0 && engine.setup.shutter_mode == -1);
	CHECK(engine.accessories[0].id == 8);
}

static void make_spectrum(int model, const char *sample, int rows, const char *ending, int echo) {
	int i, used;
	used = snprintf(native_spectrum, sizeof(native_spectrum), "%s0000,0,552.0,0.0402,3.2%s",
	                echo ? "M5\r\n" : "", ending);
	for (i = 0; i < rows; i++)
		used += snprintf(native_spectrum + used, sizeof(native_spectrum) - used,
		                 "%d,%s%s", 380 + i * (model == 655 ? 4 : 2), sample, ending);
}

static int measurement_steps(int spectral, const char *brightness_before,
                             const char *spectrum_reply, const char *xyz_reply,
                             const char *brightness_after) {
	int exposure_step;
	add("B00\r", brightness_before);
	exposure_step = transport.count;
	add(spectral ? "M5\r" : "M2\r", spectral ? spectrum_reply : xyz_reply);
	if (spectral) add("D2\r", xyz_reply);
	add("B100\r", brightness_after);
	return exposure_step;
}

static void assert_quarantined(int writes) {
	CHECK(engine.state == pr655_quarantined);
	CHECK(pr655_imp_measure(&engine, 1, &capture, 10.0) == PR655_UNCERTAIN);
	CHECK(pr655_imp_close(&engine, 5.0) == PR655_UNCERTAIN);
	CHECK(transport.writes == writes);
}

static void success_tests(void) {
	int model, fragment, spectral, count;
	const char *ending;
	for (model = 655; model <= 670; model += 15) {
		for (fragment = 1; fragment <= 4096; fragment *= 8) {
			for (spectral = 0; spectral <= 1; spectral++) {
				ending = fragment == 1 ? "\r\n" : fragment == 8 ? "\r" : "\n";
				connect(model, fragment);
				count = model == 655 ? 101 : 201;
				make_spectrum(model, "-2.0e-4", count, ending, 1);
				measurement_steps(spectral, "Backlight set to 0 %\r\n", native_spectrum,
				                  "0000,111,12.5,13.0,-0.25\r\n", "Backlight set to 100 %\r\n");
				add("Q", "");
				CHECK(pr655_imp_measure(&engine, spectral, &capture, 300.0) == PR655_OK);
				CHECK(capture.exposure_started && capture.xyz_complete && capture.xyz_parsed);
				CHECK(capture.xyz[2] == -0.25 && capture.xyz_units == 111);
				CHECK(capture.spectrum_parsed == spectral);
				if (spectral) {
					CHECK(capture.count == count && capture.spectrum[0] == -2.0e-4);
					CHECK(strcmp(capture.spectrum_raw.bytes, native_spectrum) == 0);
					CHECK(strstr(transport.wire, "M5\rD2\r") != NULL);
				} else CHECK(strstr(transport.wire, "M2\r") != NULL && strstr(transport.wire, "D2\r") == NULL);
				CHECK(pr655_imp_close(&engine, 5.0) == PR655_OK);
				CHECK(transport.next == transport.count && engine.state == pr655_closed);
			}
		}
	}
}

static void invalid_spectra(void) {
	const char *bad[] = { "nan", "inf", "0x1p0", "1e999", "1e-999", "1,2", "", "-" };
	int i, used;
	for (i = 0; i < (int)(sizeof(bad) / sizeof(bad[0])); i++) {
		connect(655, 7); make_spectrum(655, bad[i], 101, "\r\n", 0);
		measurement_steps(1, "Backlight set to 0 %\r\n", native_spectrum,
		                  "0000,0,1,2,3\r\n", "Backlight set to 100 %\r\n");
		CHECK(pr655_imp_measure(&engine, 1, &capture, 300.0) == PR655_DATA_PARSE_ERROR);
		CHECK(!capture.spectrum_parsed && !capture.xyz_parsed);
		assert_quarantined(transport.writes);
	}
	connect(655, 4096); make_spectrum(655, "1e-5", 102, "\r\n", 0);
	measurement_steps(1, "Backlight set to 0 %\r\n", native_spectrum, "", "");
	CHECK(pr655_imp_measure(&engine, 1, &capture, 300.0) == PR655_STALE_INPUT);
	CHECK(capture.spectrum_complete && !capture.spectrum_parsed);
	assert_quarantined(transport.writes);
	connect(655, 1); make_spectrum(655, "1e-5", 100, "\r\n", 0);
	measurement_steps(1, "Backlight set to 0 %\r\n", native_spectrum, "", "");
	CHECK(pr655_imp_measure(&engine, 1, &capture, 3.0) == PR655_TIMEOUT);
	CHECK(!capture.spectrum_complete && capture.spectrum_raw.length > 0);
	assert_quarantined(transport.writes);
	connect(655, 3);
	used = snprintf(native_spectrum, sizeof(native_spectrum), "0000,0,552,0.01,1\r\n380.00000000000000001,2e-5\r\n");
	(void)used;
	measurement_steps(1, "Backlight set to 0 %\r\n", native_spectrum, "", "");
	CHECK(pr655_imp_measure(&engine, 1, &capture, 300.0) == PR655_DATA_PARSE_ERROR);
	assert_quarantined(transport.writes);
}

/* Input that arrives after a reply's surplus check and before the next
 * request belongs to that request's raw record, so a stale-input refusal
 * keeps the bytes that caused it and a late blank line is not lost. */
static void between_exchange_input_tests(void) {
	int model, writes;
	for (model = 655; model <= 670; model += 15) {
		connect(model, 4096);
		add("D601\r", numeric_setup); add("D602\r", text_setup); add("D112\r", "0000,0,0\r\n");
		transport.late = "ZZ\x80\r\n"; transport.late_step = transport.count - 1;
		transport.late_skip = 1; /* D602's own surplus check reads nothing */
		writes = transport.writes;
		CHECK(pr655_imp_read_setup(&engine, 10.0) == PR655_STALE_INPUT);
		CHECK(transport.late == NULL && transport.next == transport.count - 1);
		CHECK(engine.last_raw.length == 5 && memcmp(engine.last_raw.bytes, "ZZ\x80\r\n", 5) == 0);
		assert_quarantined(writes + 10); /* D601 and D602 only; no D112 byte */
		connect(model, 4096);
		add("D601\r", numeric_setup); add("D602\r", text_setup); add("D112\r", "0000,0,0\r\n");
		transport.late = "\r\n"; transport.late_step = transport.count - 1; transport.late_skip = 1;
		CHECK(pr655_imp_read_setup(&engine, 10.0) == PR655_OK && engine.setup_valid);
		CHECK(transport.late == NULL && transport.next == transport.count);
		CHECK(engine.last_raw.length == 12
		   && memcmp(engine.last_raw.bytes, "\r\n0000,0,0\r\n", 12) == 0);
		/* D114 follows D110 without a surplus check: text read with D110's
		 * reply stays in D110's record and stops D114 before any byte. */
		connect(model, 4096);
		add("D110\r", "0000,SYNTHETIC-P7\r\nJUNK\r\n"); add("D114\r", "0000,9.01-test\r\n");
		writes = transport.writes;
		CHECK(pr655_imp_identity(&engine, 10.0) == PR655_STALE_INPUT);
		CHECK(engine.last_raw.length == 25
		   && memcmp(engine.last_raw.bytes, "0000,SYNTHETIC-P7\r\nJUNK\r\n", 25) == 0);
		assert_quarantined(writes + 5);
	}
}

static void later_failure_tests(void) {
	int writes, model, spectral;
	for (model = 655; model <= 670; model += 15) {
		for (spectral = 0; spectral <= 1; spectral++) {
			connect(model, 3);
			add("B00\r", "-1000\r\n");
			CHECK(pr655_imp_measure(&engine, spectral, &capture, 300.0) == PR655_NATIVE_ERROR_BASE + 1000);
			CHECK(capture.backlight_before_error == PR655_NATIVE_ERROR_BASE + 1000);
			CHECK(!capture.exposure_started && !capture.spectrum_parsed && !capture.xyz_parsed);
			CHECK(strcmp(capture.backlight_before_raw, "-1000\r\n") == 0);
			CHECK(engine.state == pr655_ready && transport.next == transport.count);
			CHECK(strstr(transport.wire, "M5") == NULL && strstr(transport.wire, "M2") == NULL);
			CHECK(strstr(transport.wire, "B100") == NULL);
		}
	}
	connect(655, 3); make_spectrum(655, "2e-5", 101, "\r\n", 0);
	measurement_steps(1, "Backlight set to 0 %\r\n", native_spectrum, "0000,0,1,2,3\r\n", "-1000\r\n");
	CHECK(pr655_imp_measure(&engine, 1, &capture, 300.0) == PR655_NATIVE_ERROR_BASE + 1000);
	CHECK(capture.backlight_before_error == PR655_OK && capture.backlight_after_error == PR655_NATIVE_ERROR_BASE + 1000);
	CHECK(capture.spectrum_parsed && capture.xyz_parsed && engine.state == pr655_ready);
	connect(655, 1); make_spectrum(655, "2e-5", 101, "\r\n", 0);
	measurement_steps(1, "Backlight set to 0 %\r\n", native_spectrum, "0000,0,nan,2,3\r\n", "");
	CHECK(pr655_imp_measure(&engine, 1, &capture, 300.0) == PR655_DATA_PARSE_ERROR);
	CHECK(capture.spectrum_parsed && !capture.xyz_parsed);
	CHECK(strcmp(capture.spectrum_raw.bytes, native_spectrum) == 0);
	assert_quarantined(transport.writes);
	connect(655, 7); make_spectrum(655, "2e-5", 101, "\r\n", 0);
	measurement_steps(1, "Backlight set to 0 %\r\n", native_spectrum, "", "");
	CHECK(pr655_imp_measure(&engine, 1, &capture, 300.0) == PR655_TIMEOUT);
	CHECK(capture.spectrum_parsed && !capture.xyz_complete);
	assert_quarantined(transport.writes);
	connect(655, 9); make_spectrum(655, "2e-5", 101, "\r\n", 0);
	measurement_steps(1, "Backlight set to 0 %\r\n", native_spectrum, "0000,0,1,2,3\r\n", "");
	/* B00(4), M5(3), D2(3), then the second B100 character fails. */
	transport.fail_write = transport.writes + 12;
	CHECK(pr655_imp_measure(&engine, 1, &capture, 300.0) == PR655_TIMEOUT);
	CHECK(capture.spectrum_parsed && capture.xyz_parsed);
	writes = transport.writes; assert_quarantined(writes);
	connect(655, 7);
	add("B00\r", "Backlight set to 0 %\r\n");
	add("M5\r", "-3\r\n"); add("B100\r", "Backlight set to 100 %\r\n");
	CHECK(pr655_imp_measure(&engine, 1, &capture, 300.0) == PR655_NATIVE_ERROR_BASE + 3);
	CHECK(engine.device_error == -3 && !capture.spectrum_parsed);
	CHECK(engine.state == pr655_ready && transport.next == transport.count);
	connect(655, 7);
	measurement_steps(1, "Backlight set to 0 %\r\n", "-3,ignored\r\n", "", "");
	CHECK(pr655_imp_measure(&engine, 1, &capture, 300.0) == PR655_DATA_PARSE_ERROR);
	assert_quarantined(transport.writes);
	connect(655, 7);
	measurement_steps(1, "unexpected brightness reply\r\n", "", "", "");
	CHECK(pr655_imp_measure(&engine, 1, &capture, 300.0) == PR655_DATA_PARSE_ERROR);
	CHECK(!capture.exposure_started && strstr(transport.wire, "M5") == NULL);
	assert_quarantined(transport.writes);
}

static void framed_error_cleanup_tests(void) {
	int model, scenario, cleanup;
	for (model = 655; model <= 670; model += 15) {
		for (scenario = 0; scenario < 3; scenario++) {
			for (cleanup = 0; cleanup < 3; cleanup++) {
				connect(model, 7);
				add("B00\r", "Backlight set to 0 %\r\n");
				if (scenario == 2) {
					make_spectrum(model, "2e-5", model == 655 ? 101 : 201, "\r\n", 0);
					add("M5\r", native_spectrum); add("D2\r", "-2000\r\n");
				} else add(scenario ? "M5\r" : "M2\r", "-3\r\n");
				add("B100\r", cleanup == 0 ? "Backlight set to 100 %\r\n"
				    : cleanup == 1 ? "-1000\r\n" : "malformed\r\n");
				CHECK(pr655_imp_measure(&engine, scenario != 0, &capture, 300.0)
				      == PR655_NATIVE_ERROR_BASE + (scenario == 2 ? 2000 : 3));
				CHECK(engine.device_error == (scenario == 2 ? -2000 : -3));
				CHECK(capture.exposure_started && !capture.xyz_parsed);
				CHECK(capture.spectrum_parsed == (scenario == 2));
				CHECK(capture.backlight_after_error == (cleanup == 0 ? PR655_OK
				      : cleanup == 1 ? PR655_NATIVE_ERROR_BASE + 1000 : PR655_DATA_PARSE_ERROR));
				CHECK(strstr(capture.backlight_after_raw, cleanup == 0 ? "100 %"
				      : cleanup == 1 ? "-1000" : "malformed") != NULL);
				CHECK(transport.next == transport.count);
				CHECK(engine.state == (cleanup == 2 ? pr655_quarantined : pr655_ready));
			}
		}
	}
}

static void settings_tests(void) {
	int before;
	connect(655, 7); before = transport.writes;
	CHECK(pr655_imp_set_averages(&engine, 0, 5.0) == PR655_REFUSED_COMMAND);
	CHECK(pr655_imp_set_averages(&engine, 100, 5.0) == PR655_REFUSED_COMMAND);
	CHECK(pr655_imp_set_sync(&engine, 2, 5.0) == PR655_REFUSED_COMMAND);
	CHECK(pr655_imp_set_frequency(&engine, 19, 5.0) == PR655_REFUSED_COMMAND);
	CHECK(pr655_imp_set_frequency(&engine, 401, 5.0) == PR655_REFUSED_COMMAND);
	CHECK(transport.writes == before);
	settings(3, 0, 75); add("SN3\r", "0000\r\n");
	add("D601\r", numeric_setup); add("D602\r", text_setup);
	CHECK(pr655_imp_set_averages(&engine, 3, 5.0) == PR655_OK && engine.setup.cycles == 3);
	settings(3, 3, 120); add("SS3\r", "0000\r\n");
	add("D601\r", numeric_setup); add("D602\r", text_setup);
	CHECK(pr655_imp_set_sync(&engine, 3, 5.0) == PR655_OK && engine.setup.sync_mode == 3);
	add("SK120\r", "0000\r\n"); add("D601\r", numeric_setup); add("D602\r", text_setup);
	CHECK(pr655_imp_set_frequency(&engine, 120, 5.0) == PR655_OK);
	connect(655, 7); add("SN3\r", "0000\r\n");
	add("D601\r", numeric_setup); add("D602\r", text_setup);
	CHECK(pr655_imp_set_averages(&engine, 3, 5.0) == PR655_READBACK_ERROR);
	assert_quarantined(transport.writes);
	reset(655, 7); connect_steps(655);
	transport.steps[8].reply = "0000,1.00000000000000001,3\r\n";
	CHECK(pr655_imp_open(&engine, 10.0) == PR655_OK);
	CHECK(pr655_imp_identity(&engine, 10.0) == PR655_OK);
	CHECK(pr655_imp_read_setup(&engine, 10.0) == PR655_BAD_SETUP);
	CHECK(strstr(transport.wire, "D116") == NULL);
	assert_quarantined(transport.writes);
}

static void identity_and_bounds_tests(void) {
	int before;
	char long_reply[PR655_MAX_LINE + 8];
	reset(655, 1);
	CHECK(transport.writes == 0 && engine.state == pr655_closed);
	CHECK(pr655_imp_open(&engine, NAN) == PR655_INTERNAL_ERROR && transport.writes == 0);
	add("Q", ""); add("PHOTO", "REMOTE MODE\r\n"); add("D111\r", "0000,PR-670\r\n");
	CHECK(pr655_imp_open(&engine, 10.0) == PR655_UNKNOWN_MODEL);
	assert_quarantined(transport.writes);
	reset(655, 7); add("Q", ""); add("PHOTO", "PHOTO\r\nPHOTO\r\nREMOTE MODE\r\n");
	CHECK(pr655_imp_open(&engine, 10.0) == PR655_DATA_PARSE_ERROR);
	assert_quarantined(transport.writes);
	/* An earlier session's bytes before or after Q are discarded and counted,
	   as the MATLAB and direct drivers purge them; PHOTO must still match. */
	{
		static const char before_q[] = "0000,380.0,1.0e-4\r\n", after_q[] = "M5 tail\r\n";
		static char endless[PR655_MAX_RESPONSE + 2];
		reset(655, 7); connect_steps(655);
		transport.reply = before_q; transport.steps[0].reply = after_q;
		CHECK(pr655_imp_open(&engine, 10.0) == PR655_OK && engine.state == pr655_ready);
		CHECK(engine.discarded_bytes == (int)(strlen(before_q) + strlen(after_q)));
		CHECK(strstr(transport.wire, "Q") == transport.wire);
		reset(655, 4096); connect_steps(655);
		memset(endless, 'x', sizeof(endless) - 1); endless[sizeof(endless) - 1] = '\000';
		transport.steps[0].reply = endless;
		CHECK(pr655_imp_open(&engine, 10.0) == PR655_REPLY_TOO_LONG);
		assert_quarantined(transport.writes);
		/* Short reads must respect the one open deadline, before Q and after Q.
		 * An expired purge must never enter remote mode by sending PHOTO. */
		reset(655, 1); connect_steps(655);
		transport.reply = before_q; transport.purge_read_delay_ms = 100;
		CHECK(pr655_imp_open(&engine, 0.05) == PR655_TIMEOUT);
		CHECK(engine.discarded_bytes == 1 && !transport.writes && !engine.remote_entered);
		assert_quarantined(transport.writes);
		reset(655, 1); connect_steps(655);
		transport.steps[0].reply = after_q; transport.purge_read_delay_ms = 100;
		CHECK(pr655_imp_open(&engine, 0.8) == PR655_TIMEOUT);
		CHECK(engine.discarded_bytes > 0 && ticks < 1000);
		CHECK(!strcmp(transport.wire, "Q") && !engine.remote_entered);
		assert_quarantined(transport.writes);
	}
	reset(655, 7); connect_steps(655);
	transport.steps[5].reply = "0000,101.00000000000000001,4,380,780,4,128,9,118\r\n";
	CHECK(pr655_imp_open(&engine, 10.0) == PR655_OK);
	CHECK(pr655_imp_identity(&engine, 10.0) == PR655_DATA_PARSE_ERROR);
	assert_quarantined(transport.writes);
	connect(655, 4096);
	memset(long_reply, '9', sizeof(long_reply) - 3);
	long_reply[sizeof(long_reply) - 3] = '\r'; long_reply[sizeof(long_reply) - 2] = '\n';
	long_reply[sizeof(long_reply) - 1] = '\000';
	measurement_steps(0, "Backlight set to 0 %\r\n", "", long_reply, "");
	CHECK(pr655_imp_measure(&engine, 0, &capture, 10.0) == PR655_REPLY_TOO_LONG);
	assert_quarantined(transport.writes);
	connect(655, 1); before = transport.writes;
	CHECK(pr655_imp_measure(&engine, 1, &capture, INFINITY) == PR655_INTERNAL_ERROR);
	CHECK(pr655_imp_measure(&engine, 1, &capture, 86401.0) == PR655_INTERNAL_ERROR);
	CHECK(transport.writes == before);
	measurement_steps(0, "Backlight set to 0 %\r\n", "", "0000,0,1,2,3\r\n", "");
	CHECK(pr655_imp_measure(&engine, 0, &capture, 0.1) == PR655_TIMEOUT);
	CHECK(!capture.exposure_started);
	assert_quarantined(transport.writes);
}

static void report_and_deadline_tests(void) {
	const char *status[] = { "0.00000000000000001,0,1,2,3\r\n",
	                         "0e0,0.00000000000000001,1,2,3\r\n", "-4096\r\n", "380,1\r\n" };
	int i;
	for (i = 0; i < (int)(sizeof(status) / sizeof(status[0])); i++) {
		connect(655, 7);
		measurement_steps(0, "Backlight set to 0 %\r\n", "", status[i], "");
		CHECK(pr655_imp_measure(&engine, 0, &capture, 300.0) == PR655_DATA_PARSE_ERROR);
		assert_quarantined(transport.writes);
	}
	reset(655, 7); connect_steps(655);
	transport.steps[7].reply = "0000,Test lens,None,None,None,Half degree,English,Adaptive,0 msec,Native gain,1 cycles,2 deg,No Smart Dark,No Sync,Standard Sensitivity,75.00 Hertz\r\n";
	CHECK(pr655_imp_open(&engine, 10.0) == PR655_OK);
	CHECK(pr655_imp_identity(&engine, 10.0) == PR655_OK);
	CHECK(pr655_imp_read_setup(&engine, 10.0) == PR655_BAD_SETUP);
	CHECK(strstr(transport.wire, "D112") == NULL && !engine.setup_valid);
	assert_quarantined(transport.writes);
	reset(655, 7); connect_steps(655);
	transport.steps[8].reply = "0000,2,3\r\n";
	transport.steps[9].reply = "0000,8,Test lens,Primary,Luminance,Radiance\r\n0000,8,Other lens,Primary,Luminance,Radiance\r\n";
	CHECK(pr655_imp_open(&engine, 10.0) == PR655_OK);
	CHECK(pr655_imp_identity(&engine, 10.0) == PR655_OK);
	CHECK(pr655_imp_read_setup(&engine, 10.0) == PR655_BAD_SETUP);
	assert_quarantined(transport.writes);
	connect(655, 4096); make_spectrum(655, "2e-5", 101, "\r\n", 0);
	measurement_steps(1, "Backlight set to 0 %\r\n", native_spectrum,
	                  "0000,0,1,2,3\r\n", "Backlight set to 100 %\r\n");
	/* Pacing B00+M5 consumes350ms; each reply read consumes100ms. The M5
	   is complete when the enclosing500ms allowance expires. D2 cannot
	   acquire a fresh five-second allowance after that deadline. */
	transport.read_delay_ms = 100;
	CHECK(pr655_imp_measure(&engine, 1, &capture, 0.5) == PR655_TIMEOUT);
	CHECK(capture.spectrum_parsed && !capture.xyz_parsed);
	CHECK(strstr(transport.wire, "D2\r") == NULL && strstr(transport.wire, "B100\r") == NULL);
	assert_quarantined(transport.writes);
	connect(670, 7); ticks = ~0u - 200;
	make_spectrum(670, "0", 201, "\r\n", 0);
	measurement_steps(1, "Backlight set to 0 %\r\n", native_spectrum,
	                  "0000,0,0,0,0\r\n", "Backlight set to 100 %\r\n");
	CHECK(pr655_imp_measure(&engine, 1, &capture, 300.0) == PR655_OK);
	CHECK(capture.spectrum_parsed && capture.xyz_parsed && capture.spectrum[200] == 0.0);
}

static void subnormal_tests(void) {
	int model;
	for (model = 655; model <= 670; model += 15) {
		connect(model, 7);
		make_spectrum(model, "1e-310", model == 655 ? 101 : 201, "\r\n", 0);
		measurement_steps(1, "Backlight set to 0 %\r\n", native_spectrum,
		                  "0000,0,1e-310,-1e-310,0e-9999\r\n", "Backlight set to 100 %\r\n");
		CHECK(pr655_imp_measure(&engine, 1, &capture, 300.0) == PR655_OK);
		CHECK(capture.spectrum_parsed && capture.xyz_parsed);
		CHECK(capture.spectrum[0] > 0.0 && capture.xyz[0] > 0.0 && capture.xyz[1] < 0.0);
		CHECK(capture.xyz[2] == 0.0 && strstr(capture.spectrum_raw.bytes, "1e-310") != NULL);
		connect(model, 7);
		measurement_steps(0, "Backlight set to 0 %\r\n", "",
		                  "0000,0,1e-9999,2,3\r\n", "");
		CHECK(pr655_imp_measure(&engine, 0, &capture, 300.0) == PR655_DATA_PARSE_ERROR);
		assert_quarantined(transport.writes);
	}
}

static char *inventory_reply(int count) {
	char *reply = malloc(PR655_MAX_RESPONSE + 1);
	int i, used = 0;
	if (!reply) abort();
	for (i = 0; i < count; i++) {
		const char *suffix = ",Primary,Luminance,Radiance\r\n";
		int prefix = snprintf(reply + used, PR655_MAX_RESPONSE + 1 - used, "0000,%d,", i);
		int name_n = (i == 0 ? PR655_MAX_LINE + 2 : 1000) - prefix - (int)strlen(suffix);
		used += prefix;
		memset(reply + used, 'A', name_n); used += name_n;
		strcpy(reply + used, suffix); used += (int)strlen(suffix);
	}
	return reply;
}

static void inventory_resource_tests(void) {
	const char *unavailable[] = { "-1000\r\n", "-1035\r\n", "-2000\r\n" };
	int model, kind, i;
	for (model = 655; model <= 670; model += 15) {
		char *reply = inventory_reply(PR655_MAX_ACCESSORIES);
		char counts[64];
		pr655_accessory *copy = NULL;
		connect(model, 2048);
		add("D601\r", numeric_setup); add("D602\r", text_setup);
		snprintf(counts, sizeof(counts), "0000,%d,%d\r\n", PR655_MAX_ACCESSORIES, PR655_MAX_ACCESSORIES);
		add("D112\r", counts); add("D116\r", reply);
		CHECK(pr655_imp_read_setup(&engine, 30.0) == PR655_OK);
		CHECK(engine.accessory_count == 1024 && engine.aperture_count == 1024);
		CHECK(engine.inventory_counts_valid && engine.accessory_list_valid && engine.last_raw.length > 32768);
		CHECK(strlen(engine.accessories[0].name) > 4000 && engine.accessories[1023].id == 1023);
		CHECK(pr655_accessories_copy(&copy, engine.accessories, engine.accessory_count) == PR655_OK);
		CHECK(copy != engine.accessories && copy[0].name != engine.accessories[0].name);
		add("D601\r", numeric_setup); add("D602\r", text_setup); add("D112\r", "0000,0,0\r\n");
		CHECK(pr655_imp_read_setup(&engine, 10.0) == PR655_OK && engine.setup_valid);
		CHECK(!engine.accessories && !engine.accessory_count && engine.inventory_counts_valid && engine.accessory_list_valid);
		CHECK(copy[1023].id == 1023 && strlen(copy[0].name) > 4000);
		pr655_accessories_dispose(copy, 1024); free(reply);
		for (kind = 0; kind < 2; kind++) for (i = 0; i < 3; i++) {
			connect(model, 7);
			add("D601\r", numeric_setup); add("D602\r", text_setup);
			add("D112\r", kind ? "0000,1,3\r\n" : unavailable[i]);
			if (kind) add("D116\r", unavailable[i]);
			CHECK(pr655_imp_read_setup(&engine, 10.0) == PR655_OK && engine.setup_valid && engine.state == pr655_ready);
			CHECK(!engine.accessories && !engine.accessory_count && !engine.accessory_list_valid);
			CHECK(engine.inventory_counts_valid == kind && engine.aperture_count == (kind ? 3 : 0));
			CHECK(engine.reported_accessory_count == kind);
			CHECK((kind ? engine.accessory_list_error : engine.inventory_counts_error) == -atoi(unavailable[i] + 1));
			measurement_steps(0, "Backlight set to 0 %\r\n", "", "0000,0,1,2,3\r\n", "Backlight set to 100 %\r\n");
			CHECK(pr655_imp_measure(&engine, 0, &capture, 300.0) == PR655_OK && capture.xyz_parsed);
		}
		/* Any other complete first-row refusal fails this setup read only.
		 * The link stays synchronized and a later complete report recovers. */
		connect(model, 7);
		add("D601\r", numeric_setup); add("D602\r", text_setup);
		add("D112\r", "0000,1,3\r\n"); add("D116\r", "-1001\r\n");
		CHECK(pr655_imp_read_setup(&engine, 10.0) == PR655_NATIVE_ERROR_BASE + 1001);
		CHECK(engine.state == pr655_ready && !engine.setup_valid && engine.device_error == -1001);
		CHECK(!engine.accessories && !engine.accessory_count && !engine.inventory_counts_valid);
		add("D601\r", numeric_setup); add("D602\r", text_setup);
		add("D112\r", "0000,1,3\r\n"); add("D116\r", "0000,8,Test lens,Primary,Luminance,Radiance\r\n");
		CHECK(pr655_imp_read_setup(&engine, 10.0) == PR655_OK && engine.setup_valid);
		CHECK(engine.accessory_list_valid && engine.accessory_count == 1 && engine.accessories[0].id == 8);
		connect(model, 7);
		add("D601\r", numeric_setup); add("D602\r", text_setup); add("D112\r", "0000,1025,0\r\n");
		CHECK(pr655_imp_read_setup(&engine, 10.0) == PR655_BAD_SETUP && engine.state == pr655_quarantined);
		connect(model, 2048);
		add("D601\r", numeric_setup); add("D602\r", text_setup);
		add("D112\r", "0000,0,0\r\n0000,unexpected\r\n");
		CHECK(pr655_imp_read_setup(&engine, 10.0) == PR655_STALE_INPUT);
		CHECK(!engine.setup_valid && !engine.inventory_counts_valid && !engine.accessory_list_valid);
		CHECK(!engine.accessories && !engine.accessory_count && !engine.aperture_count);
		for (i = 0; i < 2; i++) {
			const char *row = "0000,8,Test lens,Primary,Luminance,Radiance\r\n";
			int n = PR655_MAX_RESPONSE + i, start = (int)strlen(row), at;
			char *bounded = malloc(n + 1);
			if (!bounded) abort();
			strcpy(bounded, row);
			for (at = start; at < n; at++) bounded[at] = (at - start) % 2 ? '\n' : '\r';
			bounded[n] = '\000';
			connect(model, 2048);
			add("D601\r", numeric_setup); add("D602\r", text_setup);
			add("D112\r", "0000,1,3\r\n"); add("D116\r", bounded);
			CHECK(pr655_imp_read_setup(&engine, 10.0) == (i ? PR655_REPLY_TOO_LONG : PR655_OK));
			if (!i) CHECK(engine.last_raw.length == PR655_MAX_RESPONSE);
			else CHECK(engine.state == pr655_quarantined && !engine.setup_valid);
			free(bounded);
		}
	}
}

static void exact_integer_tests(void) {
	int model;
	const char *identifiers[] = {"5.000", "5e0", "50e-1", "9007199254740991"};
	int i;
	char setup_ids[512], row[512];
	for (model = 655; model <= 670; model += 15) {
		for (i = 0; i < 4; i++) {
			reset(model, 7); connect_steps(model);
			snprintf(setup_ids, sizeof(setup_ids),
			         "0e-9999,%s,-1,-1,-1,2.0,1e0,73,0,87,10e-1,2,0,0,91,75.0\r\n", identifiers[i]);
			snprintf(row, sizeof(row), "0e0,%s,Test lens,Primary,Luminance,Radiance\r\n", identifiers[i]);
			transport.steps[6].reply = setup_ids;
			transport.steps[8].reply = "0.000,1.000,30e-1\r\n";
			transport.steps[9].reply = row;
			CHECK(pr655_imp_open(&engine, 10.0) == PR655_OK);
			CHECK(pr655_imp_identity(&engine, 10.0) == PR655_OK);
			CHECK(pr655_imp_read_setup(&engine, 10.0) == PR655_OK);
			CHECK(engine.setup.primary == (i == 3 ? 9007199254740991LL : 5));
			CHECK(engine.accessories[0].id == engine.setup.primary);
		}
		reset(model, 7); connect_steps(model);
		transport.steps[9].reply = "0000,9007199254740992,Test lens,Primary,Luminance,Radiance\r\n";
		CHECK(pr655_imp_open(&engine, 10.0) == PR655_OK);
		CHECK(pr655_imp_identity(&engine, 10.0) == PR655_OK);
		CHECK(pr655_imp_read_setup(&engine, 10.0) == PR655_BAD_SETUP);
		assert_quarantined(transport.writes);
	}
}

int main(void) {
	success_tests(); invalid_spectra(); later_failure_tests(); settings_tests(); identity_and_bounds_tests(); subnormal_tests(); framed_error_cleanup_tests(); exact_integer_tests();
	report_and_deadline_tests();
	inventory_resource_tests();
	between_exchange_input_tests();
	pr655_imp_dispose(&engine); pr655_capture_clear(&capture);
	printf("PR engine: %d checks, %d failures (synthetic icoms only)\n", checks, failures);
	return failures ? 1 : 0;
}
