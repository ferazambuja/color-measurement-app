
/*
 * Argyll Color Management System
 *
 * Synthetic checks for the CR-250 transport and transaction engine in
 * cr250_imp.c. Every transcript here is invented from the manual's
 * documented shapes; none was recorded from an instrument, and passing
 * says nothing about instrument behaviour.
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
 * The engine talks to an icoms object through read(), write() and
 * close_port() only. This file supplies those three as a scripted fake
 * with a virtual clock, so no port is opened and no time is really spent.
 * Build and run with cr250_test.ksh.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <stdarg.h>
#include <limits.h>
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
#include "cr250_imp.h"

/* ------------------------------------------------------------------- */
/* Scripted transport */

#define MAX_CHUNKS 96
#define MAX_WRITES 32

typedef struct {
	const char *data;			/* Bytes delivered by one read() */
	int len;					/* Byte count, so embedded shapes are explicit */
	unsigned int arrive_ms;		/* Virtual time the read spends before delivering */
	int after_writes;            /* Response becomes available only after this request */
} chunk;

typedef struct {
	chunk chunks[MAX_CHUNKS];
	int nchunks, ichunk;
	char writes[MAX_WRITES][CR250_MAX_COMMAND + 4];
	int nwrites;
	int closes;
	int fail_write;				/* nz: write() reports a transport error */
	int fail_read;				/* nz: read() reports a transport error */
	int script_request;          /* Optional explicit request number for prequeued replies */
	unsigned int write_delay_ms;
	double write_timeout;
	int read_overrun;			/* Deliver a line even after the caller timeout */
	int read_status;			/* Override the status reported with bytes */
	int zero_progress;			/* Return success without any bytes */
} fake_port;

static fake_port F;
static unsigned int vclock;		/* Virtual milliseconds */
static unsigned int vslept;		/* Virtual milliseconds spent in sleep_ms */
static icoms *port;
static a1log *tlog;

static unsigned int fake_now(void) {
	return vclock;
}

static void fake_sleep(unsigned int ms) {
	vclock += ms;
	vslept += ms;
}

static int fake_write(icoms *p, char *buf, int nch, double tout) {
	int n = nch > 0 ? nch : (int)strlen(buf);

	if (F.nwrites < MAX_WRITES && n < (int)sizeof(F.writes[0])) {
		memcpy(F.writes[F.nwrites], buf, n);
		F.writes[F.nwrites][n] = '\000';
	}
	F.nwrites++;
	F.write_timeout = tout;
	if (F.write_delay_ms >= (unsigned int)(tout * 1000.0 + 0.5)) {
		vclock += (unsigned int)(tout * 1000.0 + 0.5);
		return ICOM_TO;
	}
	vclock += F.write_delay_ms;
	return F.fail_write ? ICOM_SERR : ICOM_OK;
}

/* Deliver the next scripted chunk if it arrives within tout, else time out
   after the full wait. read_overrun and read_status model broken transports;
   a chunk larger than the caller's space is delivered in parts. */
static int fake_read(icoms *p, char *rbuf, int bsize, int *pbread, char *tc, int ntc, double tout) {
	unsigned int wait = (unsigned int)(tout * 1000.0 + 0.5);
	chunk *c;
	int n;

	*pbread = 0;
	rbuf[0] = '\000';
	if (F.fail_read && F.nwrites > 0)
		return ICOM_SERR;
	if (F.zero_progress && F.nwrites > 0)
		return ICOM_OK;
	if (F.ichunk >= F.nchunks || F.chunks[F.ichunk].after_writes > F.nwrites) {
		vclock += wait;
		return ICOM_TO;
	}
	c = &F.chunks[F.ichunk];
	if (c->arrive_ms > wait && !F.read_overrun) {
		c->arrive_ms -= wait;
		vclock += wait;
		return ICOM_TO;
	}
	vclock += c->arrive_ms;
	c->arrive_ms = 0;
	n = c->len;
	if (n > bsize - 1)
		n = bsize - 1;
	memcpy(rbuf, c->data, n);
	rbuf[n] = '\000';
	*pbread = n;
	if (n < c->len) {
		c->data += n;
		c->len -= n;
	} else {
		F.ichunk++;
	}
	return F.read_status;
}

static void fake_close(icoms *p) {
	F.closes++;
	p->is_open = 0;
}

static void reply(const char *text) {
	if (F.nchunks >= MAX_CHUNKS) {
		fprintf(stderr, "test script too long\n");
		exit(2);
	}
	F.chunks[F.nchunks].data = text;
	F.chunks[F.nchunks].len = (int)strlen(text);
	F.chunks[F.nchunks].arrive_ms = 0;
	F.chunks[F.nchunks].after_writes = F.script_request > 0
	                                   ? F.script_request : F.nwrites + 1;
	F.nchunks++;
}

static void reply_after(unsigned int ms, const char *text) {
	reply(text);
	F.chunks[F.nchunks - 1].arrive_ms = ms;
}

static void reply_bytes(const char *data, int len) {
	reply("");
	F.chunks[F.nchunks - 1].data = data;
	F.chunks[F.nchunks - 1].len = len;
}

static cr250_imp E;

static void fresh(void) {
	memset(&F, 0, sizeof(F));
	vclock = 1000;
	vslept = 0;
	port->is_open = 1;
	cr250_imp_dispose(&E);
	cr250_imp_init(&E, port, tlog);
	E.now_ms = fake_now;
	E.sleep_ms = fake_sleep;
}

static void script_identity(const char *model, const char *serial, const char *fw, const char *type) {
	static char b[4][CR250_MAX_LINE + 3];
	snprintf(b[0], sizeof(b[0]), "OK:0:RC Model:%s\r\n", model);
	snprintf(b[1], sizeof(b[1]), "OK:0:RC ID:%s\r\n", serial);
	snprintf(b[2], sizeof(b[2]), "OK:0:RC Firmware:%s\r\n", fw);
	F.script_request = F.nwrites + 1;
	reply(b[0]);
	F.script_request++;
	reply(b[1]);
	F.script_request++;
	reply(b[2]);
	if (type != NULL) {
		F.script_request++;
		snprintf(b[3], sizeof(b[3]), "OK:0:RC InstrumentType:%s\r\n", type);
		reply(b[3]);
	}
	F.script_request = 0;
}

/* ------------------------------------------------------------------- */
/* Checks */

static int failures, checks;

#define CHECK(cond) do { checks++; if (!(cond)) { failures++; \
	printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)

#define CHECK_STR(a, b) do { checks++; if (strcmp((a), (b)) != 0) { failures++; \
	printf("FAIL %s:%d: '%s' != '%s'\n", __FILE__, __LINE__, (a), (b)); } } while (0)

#define CHECK_EQ(a, b) do { long long _a = (long long)(a), _b = (long long)(b); checks++; if (_a != _b) { \
	failures++; printf("FAIL %s:%d: %s = %lld, expected %lld\n", __FILE__, __LINE__, #a, _a, _b); } } while (0)

/* ------------------------------------------------------------------- */

static void test_parse_status(void) {
	cr250_reply r;

	CHECK_EQ(cr250_parse_status("OK:0:RC Model:CR-250", &r), CR250_OK);
	CHECK(r.complete && !r.is_error && r.code == 0);
	CHECK_STR(r.context, "RC Model");
	CHECK_STR(r.value, "CR-250");

	CHECK_EQ(cr250_parse_status("ER:-500:Invalid command:Accessory1", &r), CR250_OK);
	CHECK(r.complete && r.is_error && r.code == -500);
	CHECK_STR(r.context, "Invalid command");
	CHECK_STR(r.value, "Accessory1");

	/* The fourth field keeps its own colons */
	CHECK_EQ(cr250_parse_status("OK:0:RC Time:12:34:56", &r), CR250_OK);
	CHECK_STR(r.value, "12:34:56");

	/* Empty value is a documented possibility of the shape */
	CHECK_EQ(cr250_parse_status("OK:0:RC ID:", &r), CR250_OK);
	CHECK_STR(r.value, "");

	CHECK_EQ(cr250_parse_status("OK:101:M:Cannot sync to constant light source", &r), CR250_OK);
	CHECK(r.code == 101 && !r.is_error);

	/* Sign and status must agree (manual 4.5) */
	CHECK_EQ(cr250_parse_status("OK:-5:x:y", &r), CR250_DATA_PARSE_ERROR);
	CHECK_EQ(cr250_parse_status("ER:5:x:y", &r), CR250_DATA_PARSE_ERROR);
	CHECK_EQ(cr250_parse_status("ER:0:x:y", &r), CR250_DATA_PARSE_ERROR);
	CHECK(!r.complete);

	/* Shape defects */
	CHECK_EQ(cr250_parse_status("OK:abc:x:y", &r), CR250_DATA_PARSE_ERROR);
	CHECK_EQ(cr250_parse_status("OK:1x:x:y", &r), CR250_DATA_PARSE_ERROR);
	CHECK_EQ(cr250_parse_status("OK: 1:x:y", &r), CR250_DATA_PARSE_ERROR);
	CHECK_EQ(cr250_parse_status("OK:0", &r), CR250_DATA_PARSE_ERROR);
	CHECK_EQ(cr250_parse_status("OK:0:ctx", &r), CR250_DATA_PARSE_ERROR);
	CHECK_EQ(cr250_parse_status("XX:0:a:b", &r), CR250_DATA_PARSE_ERROR);
	CHECK_EQ(cr250_parse_status("", &r), CR250_DATA_PARSE_ERROR);
	CHECK_EQ(cr250_parse_status("OK:0:a:b\x01", &r), CR250_DATA_PARSE_ERROR);

	/* Bounded fields */
	{
		char big[CR250_MAX_LINE + 16];
		memset(big, 'c', sizeof(big));
		memcpy(big, "OK:0:", 5);
		big[5 + CR250_MAX_FIELD] = ':';
		big[5 + CR250_MAX_FIELD + 2] = '\000';
		CHECK_EQ(cr250_parse_status(big, &r), CR250_DATA_PARSE_ERROR);
		memset(big, 'v', sizeof(big));
		memcpy(big, "OK:0:c:", 7);
		big[7 + CR250_MAX_VALUE] = '\000';
		CHECK_EQ(cr250_parse_status(big, &r), CR250_DATA_PARSE_ERROR);
		big[CR250_MAX_LINE] = '\000';
		CHECK_EQ(cr250_parse_status(big, &r), CR250_OK);
	}
}

static void test_native_code_roundtrip(void) {
	CHECK_EQ(cr250_native_code(CR250_NATIVE_WARNING_BASE + 101), 101);
	CHECK_EQ(cr250_native_code(CR250_NATIVE_ERROR_BASE + 305), -305);
	CHECK_EQ(cr250_native_code(CR250_OK), 0);
	CHECK_EQ(cr250_native_code(CR250_TIMEOUT), 0);
	CHECK_EQ(cr250_native_code(inst_misread | (CR250_NATIVE_ERROR_BASE + 305)), -305);
}

static void test_identity_with_mixed_terminators(void) {
	cr250_reply r;

	fresh();
	/* CR LF split across reads, then LF only, CR only, and CR LF */
	reply("OK:0:RC Model:CR-2");
	reply("50\r");
	reply("\n");
	F.script_request = 2;
	reply("OK:0:RC ID:A00102\n");
	F.script_request = 3;
	reply("OK:0:RC Firmware:1.36\r");
	F.script_request = 4;
	reply("OK:0:RC InstrumentType:2\r\n");
	F.script_request = 0;
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_OK);
	CHECK(E.identified);
	CHECK_STR(E.id.model, "CR-250");
	CHECK_STR(E.id.serial, "A00102");
	CHECK_EQ(E.id.nfw, 2);
	CHECK_EQ(E.id.fw[0], 1);
	CHECK_EQ(E.id.fw[1], 36);
	CHECK_EQ(E.id.instrument_type, 2);
	CHECK_EQ(F.nwrites, 4);
	CHECK_STR(F.writes[0], "RC Model\r\n");
	CHECK_STR(F.writes[1], "RC ID\r\n");
	CHECK_STR(F.writes[2], "RC Firmware\r\n");
	CHECK_STR(F.writes[3], "RC InstrumentType\r\n");
	CHECK(!cr250_imp_is_uncertain(&E));
	CHECK_EQ(F.closes, 0);
	CHECK_EQ(E.inlen, 0);

	/* A three-part firmware string is accepted and compared numerically */
	fresh();
	script_identity("CR-250", "B1", "1.9.7", "2");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_OK);
	CHECK_EQ(E.id.nfw, 3);
	CHECK(!cr250_firmware_at_least(&E.id, 1, 17));	/* 1.9 is below 1.17 */
	CHECK(cr250_firmware_at_least(&E.id, 1, 9));
	CHECK(cr250_firmware_at_least(&E.id, 0, 99));
	CHECK(!cr250_firmware_at_least(&E.id, 2, 0));
	CHECK_EQ(E.id.instrument_type, -1);
	CHECK_EQ(F.nwrites, 3);							/* 1.9 does not support RC InstrumentType */

	/* Echo on: each reply is preceded by the prompt and the request */
	fresh();
	reply(">RC Model\r\nOK:0:RC Model:CR-250\r\n");
	F.script_request = 2;
	reply(">RC ID\r\nOK:0:RC ID:X1\r\n");
	F.script_request = 3;
	reply(">RC Firmware\r\nOK:0:RC Firmware:1.17\r\n");
	F.script_request = 4;
	reply(">RC InstrumentType\r\nOK:0:RC InstrumentType:2\r\n");
	F.script_request = 0;
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_OK);
	CHECK_STR(E.id.serial, "X1");
	CHECK_EQ(E.id.instrument_type, 2);
	CHECK_EQ(F.nwrites, 4);
	CHECK_EQ(cr250_imp_command(&E, "RM Time", &r, 1.0), CR250_TIMEOUT);	/* Nothing left over */
}

static void test_identity_refusals(void) {
	cr250_reply r;

	/* Not exactly CR-250: stop after the first read, link stays usable */
	fresh();
	reply("OK:0:RC Model:CR-250-IR\r\n");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_UNKNOWN_MODEL);
	CHECK_EQ(F.nwrites, 1);
	CHECK(!E.identified);
	CHECK(!cr250_imp_is_uncertain(&E));
	reply("OK:0:RM Time:1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RM Time", &r, 1.0), CR250_OK);

	fresh();
	reply("OK:0:RC Model:CR-100\r\n");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_UNKNOWN_MODEL);

	fresh();
	reply("OK:0:RC Model:\r\n");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_UNKNOWN_MODEL);

	/* Missing serial */
	fresh();
	reply("OK:0:RC Model:CR-250\r\n");
	F.script_request = 2;
	reply("OK:0:RC ID:\r\n");
	F.script_request = 0;
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_DATA_PARSE_ERROR);
	CHECK(!E.identified);

	/* Malformed firmware */
	fresh();
	script_identity("CR-250", "A1", "1", NULL);
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_DATA_PARSE_ERROR);
	fresh();
	script_identity("CR-250", "A1", "1.36b", NULL);
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_DATA_PARSE_ERROR);
	fresh();
	script_identity("CR-250", "A1", "1..36", NULL);
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_DATA_PARSE_ERROR);
	fresh();
	script_identity("CR-250", "A1", "1.2.3.4", NULL);
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_DATA_PARSE_ERROR);

	/* Firmware below 1.17: instrument type is not queried, not guessed */
	fresh();
	script_identity("CR-250", "A1", "1.16", NULL);
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_OK);
	CHECK_EQ(F.nwrites, 3);
	CHECK_EQ(E.id.instrument_type, -1);
	CHECK(E.identified);

	/* Firmware 1.17 queries it; an undocumented value is refused */
	fresh();
	script_identity("CR-250", "A1", "1.17", "5");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_DATA_PARSE_ERROR);
	CHECK_EQ(F.nwrites, 4);
	CHECK(!E.identified);

	/* A native error on a configuration read is returned as that code */
	fresh();
	reply("ER:-500:Invalid command:RC Model\r\n");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_NATIVE_ERROR_BASE + 500);
	CHECK(!E.identified);
	CHECK(!cr250_imp_is_uncertain(&E));

	/* A warning on a configuration read is not accepted as identity */
	fresh();
	reply("OK:100:RC Model:CR-250\r\n");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_NATIVE_WARNING_BASE + 100);
	CHECK(!E.identified);

	/* Identity is re-established, not remembered, after a failed attempt */
	fresh();
	script_identity("CR-250", "A1", "1.36", "2");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_OK);
	reply("OK:0:RC Model:CR-300\r\n");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_UNKNOWN_MODEL);
	CHECK(!E.identified);
}

static void test_echo_handling(void) {
	cr250_reply r;

	/* Exact echo, with and without the prompt, is skipped */
	fresh();
	reply("RC ID\r\nOK:0:RC ID:A1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_OK);
	CHECK_STR(r.value, "A1");
	reply(">RC ID\r\nOK:0:RC ID:A1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_OK);

	CHECK(!cr250_imp_is_uncertain(&E));

	/* A blank line is not documented and is not an echo */
	fresh();
	reply("\r\nOK:0:RC ID:A1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_BAD_ECHO);
	CHECK(cr250_imp_is_uncertain(&E));

	/* Mismatched echo */
	fresh();
	reply("RC Idx\r\nOK:0:RC ID:A1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_BAD_ECHO);
	fresh();
	reply("RC IDx\r\nOK:0:RC ID:A1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_BAD_ECHO);
	CHECK(cr250_imp_is_uncertain(&E));
	CHECK_EQ(F.closes, 1);
	CHECK(!r.complete);

	/* More than one echo line */
	fresh();
	reply("RC ID\r\nRC ID\r\nOK:0:RC ID:A1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_BAD_ECHO);
	CHECK(cr250_imp_is_uncertain(&E));

	/* Any other text before the status line */
	fresh();
	reply("hello\r\nOK:0:RC ID:A1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_BAD_ECHO);
}

static void test_stale_input(void) {
	cr250_reply r;
	cr250_raw previous = {0};
	static const char first_raw[] = "OK:0:RC ID:A1\r";
	static const char next_raw[] = "\nOK:0:RC Model:CR-250\r\n";

	/* Extra bytes invalidate the current exchange before success is returned. */
	fresh();
	reply("OK:0:RC ID:A1\r\nEXTRA\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_STALE_INPUT);
	CHECK_EQ(cr250_imp_command(&E, "RC Model", &r, 1.0), CR250_UNCERTAIN);
	CHECK_EQ(F.nwrites, 1);
	CHECK(cr250_imp_is_uncertain(&E));
	CHECK_EQ(F.closes, 1);

	/* Extra terminators after a reply are stale too; only the LF half
	   of a split CR LF is expected */
	fresh();
	reply("OK:0:RC ID:A1\r\n\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_STALE_INPUT);
	CHECK_EQ(cr250_imp_command(&E, "RC Model", &r, 1.0), CR250_UNCERTAIN);
	CHECK_EQ(F.nwrites, 1);
	fresh();
	reply(first_raw);
	reply_after(2, "\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_OK);
	CHECK(E.last_raw.length == sizeof(first_raw) - 1
	   && !memcmp(E.last_raw.bytes, first_raw, sizeof(first_raw) - 1));
	CHECK_EQ(cr250_raw_copy(&previous, &E.last_raw), CR250_OK);
	/* The LF arrives during the next preflight. Attribute it exactly once
	   to that attempt rather than dropping it after the earlier snapshot. */
	reply("OK:0:RC Model:CR-250\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC Model", &r, 1.0), CR250_OK);
	CHECK(E.last_raw.length == sizeof(next_raw) - 1
	   && !memcmp(E.last_raw.bytes, next_raw, sizeof(next_raw) - 1));
	CHECK(previous.length == sizeof(first_raw) - 1
	   && !memcmp(previous.bytes, first_raw, sizeof(first_raw) - 1));
	CHECK_EQ(F.nwrites, 2);
	CHECK(!cr250_imp_is_uncertain(&E));
	cr250_raw_dispose(&previous);
}

static void test_framing_limits_and_failures(void) {
	cr250_reply r;
	static char big[CR250_MAX_LINE + 100];

	/* Data without a terminator until the deadline */
	fresh();
	reply("OK:0:RC ID:A00");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_TIMEOUT);
	CHECK(cr250_imp_is_uncertain(&E));
	CHECK_EQ(F.closes, 1);
	CHECK_EQ(vclock, 2000);				/* Waited the whole second, no more */
	CHECK(!r.complete);

	/* Silence */
	fresh();
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 0.5), CR250_TIMEOUT);
	CHECK_EQ(vclock, 1500);

	/* Reply arriving after the deadline is not waited for */
	fresh();
	reply_after(1500, "OK:0:RC ID:A1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_TIMEOUT);
	CHECK_EQ(vclock, 2000);

	/* Reply arriving inside the deadline is */
	fresh();
	reply_after(900, "OK:0:RC ID:A1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_OK);
	CHECK_EQ(vclock, 1902);

	/* Overlong line */
	fresh();
	memset(big, 'A', sizeof(big));
	big[sizeof(big) - 3] = '\r';
	big[sizeof(big) - 2] = '\n';
	big[sizeof(big) - 1] = '\000';
	reply(big);
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_REPLY_TOO_LONG);
	CHECK(cr250_imp_is_uncertain(&E));

	/* Overlong without any terminator, delivered in pieces */
	fresh();
	memset(big, 'B', sizeof(big) - 1);
	big[sizeof(big) - 1] = '\000';
	reply_bytes(big, CR250_MAX_LINE / 2 + 1);
	reply_bytes(big, CR250_MAX_LINE / 2 + 1);
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_REPLY_TOO_LONG);

	/* Transport write failure */
	fresh();
	F.fail_write = 1;
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_COMS_FAIL);
	CHECK(cr250_imp_is_uncertain(&E));
	CHECK_EQ(F.closes, 1);

	/* Transport read failure */
	fresh();
	F.fail_read = 1;
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_COMS_FAIL);
	CHECK(cr250_imp_is_uncertain(&E));
	CHECK_EQ(F.closes, 1);

	/* Public calls on an uncertain link send nothing and do not close again. */
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_UNCERTAIN);
	CHECK_EQ(cr250_imp_measure(&E, &r, 200.0, 120.0), CR250_UNCERTAIN);
	CHECK_EQ(F.nwrites, 1);
	CHECK_EQ(F.closes, 1);

	/* Recovery needs an open port, then identity again */
	CHECK_EQ(cr250_imp_recover(&E), CR250_COMS_FAIL);
	CHECK(cr250_imp_is_uncertain(&E));
	port->is_open = 1;
	F.fail_read = 0;
	CHECK_EQ(cr250_imp_recover(&E), CR250_OK);
	CHECK(!cr250_imp_is_uncertain(&E));
	CHECK_EQ(cr250_imp_measure(&E, &r, 200.0, 120.0), CR250_NOT_IDENTIFIED);
	CHECK_EQ(F.nwrites, 1);
}

static void test_request_validation_and_refusals(void) {
	cr250_reply r;
	static const char *refused[] = {
		"E", "M", "MT", "MA", "MF", "SM Reset", "SM Accessory 0", "SH Time 0",
		"SC CMF 1", "CC Matrix 0", "CC Match 0", "RM Spectrum", "RM Temporal", NULL
	};
	static const char *malformed[] = {
		"", " RC ID", "RC ID ", "RC\rID", "RC\nID", "RC\tID", NULL
	};
	char longcmd[CR250_MAX_COMMAND + 2];
	int i;

	fresh();
	CHECK_EQ(cr250_imp_command(&E, "RC Time", &r, 1.0), CR250_REFUSED_COMMAND);	/* only a syntax example in manual 2.2 */
	for (i = 0; refused[i] != NULL; i++) {
		CHECK_EQ(cr250_imp_command(&E, refused[i], &r, 1.0), CR250_REFUSED_COMMAND);
	}
	for (i = 0; malformed[i] != NULL; i++) {
		CHECK_EQ(cr250_imp_command(&E, malformed[i], &r, 1.0), CR250_INTERNAL_ERROR);
	}
	memset(longcmd, 'R', sizeof(longcmd) - 1);
	longcmd[sizeof(longcmd) - 1] = '\000';
	CHECK_EQ(cr250_imp_command(&E, longcmd, &r, 1.0), CR250_INTERNAL_ERROR);
	CHECK_EQ(cr250_imp_command(&E, NULL, &r, 1.0), CR250_INTERNAL_ERROR);
	CHECK_EQ(cr250_imp_command(&E, "RC ID", NULL, 1.0), CR250_INTERNAL_ERROR);
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 0.0), CR250_INTERNAL_ERROR);
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, -1.0), CR250_INTERNAL_ERROR);
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, NAN), CR250_INTERNAL_ERROR);
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1e9), CR250_INTERNAL_ERROR);
	CHECK_EQ(cr250_imp_inventory(&E, "Bad Key", NULL, 1.0), CR250_INTERNAL_ERROR);
	{
		cr250_inventory inv = {0};
		CHECK_EQ(cr250_imp_inventory(&E, "Bad Key", &inv, 1.0), CR250_INTERNAL_ERROR);
		CHECK_EQ(cr250_imp_inventory(&E, "", &inv, 1.0), CR250_INTERNAL_ERROR);
		cr250_inventory_dispose(&inv);
	}
	CHECK_EQ(F.nwrites, 0);
	CHECK(!cr250_imp_is_uncertain(&E));

	/* Allowed reads go out with CR LF */
	reply("OK:0:RS CMF:0\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RS CMF", &r, 1.0), CR250_OK);
	CHECK_STR(F.writes[0], "RS CMF\r\n");
	reply("OK:0:RM Warnings:0\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RM Warnings", &r, 1.0), CR250_OK);
	CHECK_STR(r.value, "0");
}

static void test_warning_and_error_replies(void) {
	cr250_reply r;

	/* A complete "ER" line is a finished exchange; the link stays usable */
	fresh();
	reply("ER:-506:Accessory:Index doesn't select an Accessory\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RS Accessory", &r, 1.0), CR250_NATIVE_ERROR_BASE + 506);
	CHECK(r.complete && r.is_error);
	CHECK_EQ(r.code, -506);
	CHECK(!cr250_imp_is_uncertain(&E));
	reply("OK:0:RC ID:A1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_OK);

	/* A positive code is a warning, kept apart from success */
	reply("OK:102:RM Time:1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RM Time", &r, 1.0), CR250_NATIVE_WARNING_BASE + 102);
	CHECK(r.complete && !r.is_error && r.code == 102);

	/* Codes outside the representable native range are not mapped, but
	   the exchange did complete and the number is kept */
	reply("OK:5000:RM Time:1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RM Time", &r, 1.0), CR250_DATA_PARSE_ERROR);
	CHECK(r.complete && r.code == 5000);
	CHECK(!cr250_imp_is_uncertain(&E));
	reply("ER:-5000:RM Time:1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RM Time", &r, 1.0), CR250_DATA_PARSE_ERROR);
	CHECK(r.complete && r.code == -5000);
	CHECK(!cr250_imp_is_uncertain(&E));
}

static void test_inventory(void) {
	cr250_inventory inv = {0};
	cr250_reply r;

	/* Three-field records, shapes from manual 4.3.2.4 */
	fresh();
	reply("OK:0:RC Accessory:3\r\n0,Standard,Radiance\r\n1,IR-100,Irradiance\r\n2,IS-101,Rad. Flux\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Accessory", &inv, 1.0), CR250_OK);
	CHECK_STR(F.writes[0], "RC Accessory\r\n");
	CHECK_STR(inv.key, "Accessory");
	CHECK_EQ(inv.count, 3);
	CHECK_EQ(inv.fields, 3);
	CHECK_EQ(inv.rec[0].id, 0);
	CHECK_STR(inv.rec[0].name, "Standard");
	CHECK_STR(inv.rec[0].type, "Radiance");
	CHECK_EQ(inv.rec[2].id, 2);
	CHECK_STR(inv.rec[2].name, "IS-101");
	CHECK_STR(inv.rec[2].type, "Rad. Flux");
	CHECK(!cr250_imp_is_uncertain(&E));
	CHECK_EQ(E.inlen, 0);

	/* Two-field aperture records, non-sequential ids, spaces, fragmented */
	fresh();
	reply("OK:0:RC Aper");
	reply("ture:2\r");
	reply("\n3,ND-100");
	reply("-1\r\n7,ND 0.7\r");
	reply("\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Aperture", &inv, 1.0), CR250_OK);
	CHECK_EQ(inv.count, 2);
	CHECK_EQ(inv.fields, 2);
	CHECK_EQ(inv.rec[0].id, 3);
	CHECK_STR(inv.rec[0].name, "ND-100-1");
	CHECK_EQ(inv.rec[1].id, 7);
	CHECK_STR(inv.rec[1].name, "ND 0.7");
	CHECK_STR(inv.rec[1].type, "");

	/* CR LF split between two records */
	fresh();
	reply("OK:0:RC Aperture:2\r\n3,ND-100-1\r");
	reply("\n7,ND 0.7\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Aperture", &inv, 1.0), CR250_OK);
	CHECK_EQ(inv.count, 2);
	CHECK_EQ(inv.rec[1].id, 7);

	/* Native error instead of a body */
	fresh();
	reply("ER:-500:Invalid command:RC Mode\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Mode", &inv, 1.0), CR250_NATIVE_ERROR_BASE + 500);
	CHECK_EQ(inv.count, 0);
	CHECK(!cr250_imp_is_uncertain(&E));

	/* Truncated body: fewer records than announced */
	fresh();
	reply("OK:0:RC Mode:3\r\n0,Colorimeter\r\n1,Flicker\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Mode", &inv, 1.0), CR250_TIMEOUT);
	CHECK(cr250_imp_is_uncertain(&E));
	CHECK_EQ(F.closes, 1);
	CHECK_EQ(inv.count, 0);

	/* One deadline for the whole body, not one per line */
	fresh();
	reply_after(400, "OK:0:RC Mode:3\r\n");
	reply_after(400, "0,Colorimeter\r\n");
	reply_after(400, "1,Flicker\r\n");
	reply_after(400, "2,Response Time\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Mode", &inv, 1.0), CR250_TIMEOUT);
	CHECK_EQ(vclock, 2000);
	fresh();
	reply_after(400, "OK:0:RC Mode:3\r\n");
	reply_after(400, "0,Colorimeter\r\n");
	reply_after(400, "1,Flicker\r\n");
	reply_after(400, "2,Response Time\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Mode", &inv, 2.0), CR250_OK);
	CHECK_EQ(inv.count, 3);
	CHECK_STR(inv.rec[2].name, "Response Time");

	/* An extra record invalidates this inventory, not just the next request. */
	fresh();
	reply("OK:0:RC Range:1\r\n0,A\r\n1,B\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Range", &inv, 1.0), CR250_STALE_INPUT);
	CHECK_EQ(inv.count, 0);
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_UNCERTAIN);
	CHECK(cr250_imp_is_uncertain(&E));

	/* Counts that leave an unknown body in flight */
	fresh();
	reply("OK:0:RC Range:-1\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Range", &inv, 1.0), CR250_INVENTORY_SHAPE);
	CHECK(cr250_imp_is_uncertain(&E));
	fresh();
	reply("OK:0:RC Range:abc\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Range", &inv, 1.0), CR250_INVENTORY_SHAPE);
	CHECK(cr250_imp_is_uncertain(&E));
	fresh();
	reply("OK:0:RC Range:257\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Range", &inv, 1.0), CR250_INVENTORY_SHAPE);
	CHECK(cr250_imp_is_uncertain(&E));
	CHECK_EQ(F.closes, 1);

	/* Ill-shaped but complete bodies: consumed, reported, link usable */
	fresh();
	reply("OK:0:RC Range:2\r\n0,A\r\n0,B\r\n");			/* duplicate id */
	CHECK_EQ(cr250_imp_inventory(&E, "Range", &inv, 1.0), CR250_INVENTORY_SHAPE);
	CHECK(!cr250_imp_is_uncertain(&E));
	CHECK_EQ(inv.count, 0);
	reply("OK:0:RC ID:A1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_OK);

	fresh();
	reply("OK:0:RC Accessory:2\r\n0,Standard,Radiance\r\n1,IR-100\r\n");	/* mixed field counts */
	CHECK_EQ(cr250_imp_inventory(&E, "Accessory", &inv, 1.0), CR250_INVENTORY_SHAPE);
	CHECK(!cr250_imp_is_uncertain(&E));
	reply("OK:0:RC ID:A1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_OK);

	fresh();
	reply("OK:0:RC Accessory:1\r\n0,Standard,Radiance,Extra\r\n");	/* four fields */
	CHECK_EQ(cr250_imp_inventory(&E, "Accessory", &inv, 1.0), CR250_INVENTORY_SHAPE);
	CHECK(!cr250_imp_is_uncertain(&E));

	fresh();
	reply("OK:0:RC Range:1\r\n-1,A\r\n");					/* negative id */
	CHECK_EQ(cr250_imp_inventory(&E, "Range", &inv, 1.0), CR250_INVENTORY_SHAPE);
	fresh();
	reply("OK:0:RC Range:1\r\n0,\r\n");						/* empty name */
	CHECK_EQ(cr250_imp_inventory(&E, "Range", &inv, 1.0), CR250_INVENTORY_SHAPE);
	fresh();
	reply("OK:0:RC Accessory:1\r\n0,A,\r\n");				/* empty type */
	CHECK_EQ(cr250_imp_inventory(&E, "Accessory", &inv, 1.0), CR250_INVENTORY_SHAPE);
	fresh();
	reply("OK:0:RC Range:1\r\nA\r\n");						/* no comma */
	CHECK_EQ(cr250_imp_inventory(&E, "Range", &inv, 1.0), CR250_INVENTORY_SHAPE);
	fresh();
	reply("OK:0:RC Range:2\r\n0,A\r\nzz\r\n1,B\r\n");		/* bad middle record, then stale */
	CHECK_EQ(cr250_imp_inventory(&E, "Range", &inv, 1.0), CR250_STALE_INPUT);
	CHECK(cr250_imp_is_uncertain(&E));
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_UNCERTAIN);

		/* Larger complete inventories replace the former 4096-byte body bound. */
	{
		static char body[CR250_MAX_RECORDS * 128 + 64];
		char *d = body;
		int i;
		fresh();
		d += snprintf(d, 64, "OK:0:RC Filter:%d\r\n", CR250_MAX_RECORDS);
		for (i = 0; i < CR250_MAX_RECORDS; i++) {
			d += snprintf(d, 8, "%d,", i);
			memset(d, 'n', 80);
			d += 80;
			d += snprintf(d, 16, ",Radiance\r\n");
		}
		reply(body);
		CHECK_EQ(cr250_imp_inventory(&E, "Filter", &inv, 1.0), CR250_OK);
		CHECK(inv.count == CR250_MAX_RECORDS && E.last_raw.length > 4096);
		CHECK(!cr250_imp_is_uncertain(&E));
	}
	cr250_inventory_dispose(&inv);
}

static void test_measure_budget(void) {
	double b;

	CHECK_EQ(cr250_measure_budget(500.0, 1, &b), CR250_OK);
	CHECK(b == CR250_MEAS_FLOOR_S);						/* 6.5 s raised to the floor */
	CHECK_EQ(cr250_measure_budget(100000.0, 1, &b), CR250_OK);
	CHECK(fabs(b - 305.0) < 1e-9);
	CHECK_EQ(cr250_measure_budget(100000.0, 2, &b), CR250_OK);
	CHECK(fabs(b - 605.0) < 1e-9);
	CHECK_EQ(cr250_measure_budget(0.0, 1, &b), CR250_BAD_BUDGET_INPUT);
	CHECK_EQ(cr250_measure_budget(-5.0, 1, &b), CR250_BAD_BUDGET_INPUT);
	CHECK_EQ(cr250_measure_budget(NAN, 1, &b), CR250_BAD_BUDGET_INPUT);
	CHECK_EQ(cr250_measure_budget(INFINITY, 1, &b), CR250_BAD_BUDGET_INPUT);
	CHECK_EQ(cr250_measure_budget(500.0, 0, &b), CR250_BAD_BUDGET_INPUT);
	CHECK_EQ(cr250_measure_budget(500.0, -1, &b), CR250_BAD_BUDGET_INPUT);
	CHECK_EQ(cr250_measure_budget(1e12, 50, &b), CR250_BAD_BUDGET_INPUT);
	CHECK(b == 0.0);
	CHECK_EQ(cr250_measure_budget(500.0, 1, NULL), CR250_INTERNAL_ERROR);
}

static void test_measure_transaction(void) {
	cr250_reply r;

	/* Identity first, then exactly one "M" */
	fresh();
	script_identity("CR-250", "A1", "1.36", "2");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_OK);
	reply("OK:0:M:No errors\r\n");
	CHECK_EQ(cr250_imp_measure(&E, &r, 120.0, 120.0), CR250_OK);
	CHECK_EQ(F.nwrites, 5);
	CHECK_STR(F.writes[4], "M\r\n");
	CHECK(r.complete && r.code == 0);
	CHECK_STR(r.context, "M");
	CHECK_STR(r.value, "No errors");

	/* Warning-bearing completion is reported as the native warning */
	reply("OK:101:M:Cannot sync to constant light source\r\n");
	CHECK_EQ(cr250_imp_measure(&E, &r, 120.0, 120.0), CR250_NATIVE_WARNING_BASE + 101);
	CHECK(r.complete && r.code == 101);
	CHECK(!cr250_imp_is_uncertain(&E));

	/* Measurement error: complete exchange, no retry, link usable */
	reply("ER:-305:M:Light intensity too low or unmeasurable\r\n");
	CHECK_EQ(cr250_imp_measure(&E, &r, 120.0, 120.0), CR250_NATIVE_ERROR_BASE + 305);
	CHECK_EQ(F.nwrites, 7);
	CHECK(r.complete && r.is_error && r.code == -305);
	CHECK(!cr250_imp_is_uncertain(&E));
	CHECK(E.identified);

	/* Caller allows less than the budget: nothing is sent */
	CHECK_EQ(cr250_imp_measure(&E, &r, 100.0, 120.0), CR250_BUDGET_TOO_SHORT);
	CHECK_EQ(cr250_imp_measure(&E, &r, 0.0, 120.0), CR250_BAD_BUDGET_INPUT);
	CHECK_EQ(cr250_imp_measure(&E, &r, 120.0, NAN), CR250_BAD_BUDGET_INPUT);
	CHECK_EQ(cr250_imp_measure(&E, &r, 120.0, 0.0), CR250_BAD_BUDGET_INPUT);
	CHECK_EQ(cr250_imp_measure(&E, NULL, 120.0, 120.0), CR250_INTERNAL_ERROR);
	CHECK_EQ(F.nwrites, 7);

	/* A longer allowance than the budget is used as the deadline */
	reply_after(150000, "OK:0:M:No errors\r\n");
	CHECK_EQ(cr250_imp_measure(&E, &r, 200.0, 120.0), CR250_OK);

	/* Deadline expiry during exposure: one "M", port closed, link
	   uncertain, identity forgotten, and no second "M" ever */
	{
		unsigned int before = vclock;
		CHECK_EQ(cr250_imp_measure(&E, &r, 120.0, 120.0), CR250_TIMEOUT);
		CHECK_EQ(vclock - before, 120000);
	}
	CHECK_EQ(F.nwrites, 9);
	CHECK(cr250_imp_is_uncertain(&E));
	CHECK_EQ(F.closes, 1);
	CHECK(!E.identified);
	CHECK(!r.complete);
	CHECK_EQ(cr250_imp_measure(&E, &r, 120.0, 120.0), CR250_UNCERTAIN);
	CHECK_EQ(F.nwrites, 9);

	/* Recovery, then identity, then measurement again */
	port->is_open = 1;
	CHECK_EQ(cr250_imp_recover(&E), CR250_OK);
	CHECK_EQ(cr250_imp_measure(&E, &r, 120.0, 120.0), CR250_NOT_IDENTIFIED);
	script_identity("CR-250", "A1", "1.36", "2");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_OK);
	reply("OK:0:M:No errors\r\n");
	CHECK_EQ(cr250_imp_measure(&E, &r, 120.0, 120.0), CR250_OK);
	CHECK_EQ(F.nwrites, 14);

	/* Without identity nothing is sent at all */
	fresh();
	CHECK_EQ(cr250_imp_measure(&E, &r, 120.0, 120.0), CR250_NOT_IDENTIFIED);
	CHECK_EQ(F.nwrites, 0);
}

static void test_setting_writes(void) {
	static const char *refused[] = { "Accessory", "Aperture", "Filter1", "Filter", "Reset",
		"Mode", "Range", "RangeMode", "Speed", "Exposure", "", NULL };
	static const char *bad_args[] = { "", "-1", "+1", "1e3", "1 2", "1.2.3", ".", "0x10",
		"12345678901234567", NULL };
	cr250_reply r;
	int i;

	/* Both OK context forms the manual shows, and the whole request */
	fresh();
	reply("OK:0:SM ExposureMode:No errors\r\n");
	CHECK_EQ(cr250_imp_setting(&E, "ExposureMode", "1", &r, 1.0), CR250_OK);
	CHECK_STR(F.writes[0], "SM ExposureMode 1\r\n");
	CHECK(r.complete && r.code == 0);
	CHECK_STR(r.context, "SM ExposureMode");
	reply("OK:0:CMF:No errors\r\n");
	CHECK_EQ(cr250_imp_setting(&E, "CMF", "0", &r, 1.0), CR250_OK);
	CHECK_STR(r.context, "CMF");
	reply("OK:0:SM SyncFreq 59.94:No errors\r\n");
	CHECK_EQ(cr250_imp_setting(&E, "SyncFreq", "59.94", &r, 1.0), CR250_OK);
	CHECK_STR(F.writes[2], "SM SyncFreq 59.94\r\n");
	reply("OK:0:SM ExposureX:No errors\r\n");
	CHECK_EQ(cr250_imp_setting(&E, "ExposureX", "1234567890123456", &r, 1.0), CR250_OK);
	CHECK(!cr250_imp_is_uncertain(&E) && E.inlen == 0);
	reply("OK:0:Matrix:No errors\r\n");
	CHECK_EQ(cr250_imp_setting(&E, "Matrix", "7", &r, 1.0), CR250_OK);
	reply("OK:0:SM Match:No errors\r\n");
	CHECK_EQ(cr250_imp_setting(&E, "Match", "9", &r, 1.0), CR250_OK);
	CHECK_EQ(cr250_imp_setting(&E, "Matrix", "7.5", &r, 1.0), CR250_INTERNAL_ERROR);
	CHECK_EQ(cr250_imp_setting(&E, "Match", "9.0", &r, 1.0), CR250_INTERNAL_ERROR);

	/* A native refusal keeps the link */
	reply("ER:-521:Invalid Sync Mode:SyncMode\r\n");
	CHECK_EQ(cr250_imp_setting(&E, "SyncMode", "9", &r, 1.0), CR250_NATIVE_ERROR_BASE + 521);
	CHECK(r.complete && r.is_error && r.code == -521);
	CHECK(!cr250_imp_is_uncertain(&E));

	/* Optics declarations, SM Reset and every other key are refused
	   before anything is sent, as are arguments that are not a plain
	   unsigned decimal of at most 16 characters */
	for (i = 0; refused[i] != NULL; i++)
		CHECK_EQ(cr250_imp_setting(&E, refused[i], "0", &r, 1.0), CR250_REFUSED_COMMAND);
	for (i = 0; bad_args[i] != NULL; i++)
		CHECK_EQ(cr250_imp_setting(&E, "ExposureX", bad_args[i], &r, 1.0), CR250_INTERNAL_ERROR);
	CHECK_EQ(cr250_imp_setting(&E, "ExposureX", NULL, &r, 1.0), CR250_INTERNAL_ERROR);
	CHECK_EQ(cr250_imp_setting(&E, NULL, "1", &r, 1.0), CR250_INTERNAL_ERROR);
	CHECK_EQ(cr250_imp_setting(&E, "ExposureX", "1", NULL, 1.0), CR250_INTERNAL_ERROR);
	CHECK_EQ(cr250_imp_setting(&E, "ExposureX", "1", &r, 0.0), CR250_INTERNAL_ERROR);
	/* The read entry point cannot be used to write */
	CHECK_EQ(cr250_imp_command(&E, "SM CMF 0", &r, 1.0), CR250_REFUSED_COMMAND);
	CHECK_EQ(cr250_imp_command(&E, "SM Reset", &r, 1.0), CR250_REFUSED_COMMAND);
	CHECK_EQ(F.nwrites, 7);

	/* An OK for another command is not taken as this write's */
	reply("OK:0:SM SyncMode:No errors\r\n");
	CHECK_EQ(cr250_imp_setting(&E, "ExposureMode", "0", &r, 1.0), CR250_DATA_PARSE_ERROR);
	CHECK(cr250_imp_is_uncertain(&E));
	CHECK_EQ(cr250_imp_setting(&E, "ExposureMode", "0", &r, 1.0), CR250_UNCERTAIN);
	CHECK_EQ(F.nwrites, 8);

	/* Bytes after the status line: they would be taken as the next reply */
	fresh();
	reply("OK:0:SM ExposureMode:No errors\r\nOK:0:SM ExposureMode:No errors\r\n");
	CHECK_EQ(cr250_imp_setting(&E, "ExposureMode", "0", &r, 1.0), CR250_STALE_INPUT);
	CHECK(cr250_imp_is_uncertain(&E) && r.complete && r.code == 0);

	/* No answer: the outcome is unknown and the link uncertain */
	fresh();
	CHECK_EQ(cr250_imp_setting(&E, "UserCalibMode", "0", &r, 1.0), CR250_TIMEOUT);
	CHECK(cr250_imp_is_uncertain(&E) && !r.complete);
	CHECK_EQ(F.closes, 1);
}

/* A scripted "RM Spectrum" reply: header, then the body in one chunk. */
static void script_spectrum(const char *header, const char *body) {
	static char h[CR250_MAX_LINE];
	snprintf(h, sizeof(h), "OK:0:RM Spectrum:%s\r\n", header);
	reply(h);
	if (body != NULL) {
		reply(body);
	}
}

static void test_spectrum_transaction(void) {
	static char big[4000 * 22 + 1];
	cr250_spectrum sp;
	cr250_reply r;
	unsigned int before;
	int i;

	/* Header, count values (signed kept, unscaled), then the pacing note */
	fresh();
	script_spectrum("380,780,100,5", "1.5e-03\r\n2\r\n-2.5e-05\r\n0\r\n4.25E+01\r\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_OK);
	CHECK_STR(F.writes[0], "RM Spectrum\r\n");
	CHECK(r.complete && r.code == 0);
	CHECK_STR(r.context, "RM Spectrum");
	CHECK(sp.count == 5 && sp.start_nm == 380 && sp.end_nm == 780 && sp.step_nm == 100);
	CHECK(sp.value[0] == 1.5e-03 && sp.value[1] == 2 && sp.value[2] == -2.5e-05);
	CHECK(sp.value[4] == 42.5);
	CHECK_EQ(vslept, CR250_SPECTRUM_GUARD_MS);
	CHECK(!cr250_imp_is_uncertain(&E) && E.inlen == 0);
	/* Fragmented across reads, with an exact declared grid. */
	fresh();
	reply("OK:0:RM Spec");
	reply("trum:380.0,395.0,5,4\r\n1\r");
	reply("\n2\r\n3\r\n4\r\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_OK);
	CHECK(sp.count == 4 && sp.value[3] == 4);
	/* Not a general command */
	CHECK_EQ(cr250_imp_command(&E, "RM Spectrum", &r, 1.0), CR250_REFUSED_COMMAND);

	/* Complete but unusable replies keep the link and still wait the pacing
	   note out. Finite bodies retain their native header and bins. */
	fresh();
	script_spectrum("NA", NULL);
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
	CHECK(sp.count == 0 && !cr250_imp_is_uncertain(&E));
	CHECK_EQ(vslept, CR250_SPECTRUM_GUARD_MS);
	reply("ER:-500:Invalid command:RM Spectrum\r\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_NATIVE_ERROR_BASE + 500);
	CHECK(sp.count == 0 && r.is_error && !cr250_imp_is_uncertain(&E));
	script_spectrum("380,780,100,5", "1\r\n2\r\n1.234e-03x\r\n4\r\n5\r\n");	/* Bad line */
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
	CHECK(sp.count == 0 && !cr250_imp_is_uncertain(&E) && E.inlen == 0);
	script_spectrum("380,380,5,1", "1\r\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
	CHECK(sp.count == 1 && sp.start_nm == 380 && sp.end_nm == 380
	   && sp.step_nm == 5 && sp.value[0] == 1);
	/* One bin forms no axis even when its end lies within the endpoint
	   tolerance of its start, so the count guard alone must refuse it. */
	script_spectrum("380,380.000001,5,1", "1\r\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
	CHECK(sp.count == 1 && sp.end_nm == 380.000001 && sp.value[0] == 1);
	script_spectrum("380,780,5,5", "1\r\n2\r\n3\r\n4\r\n5\r\n");			/* Grid */
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
	CHECK(sp.count == 5 && sp.start_nm == 380 && sp.end_nm == 780
	   && sp.step_nm == 5 && sp.value[4] == 5);
	script_spectrum("380,397,5,4", "1\r\n2\r\n3\r\n4\r\n");				/* End 2 nm off the grid */
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
	script_spectrum("380,780,200,3", "1\r\n2\r\n3\r\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_OK);
	CHECK(sp.count == 3 && sp.value[2] == 3);
	script_spectrum("780,380,-100,5", "1\r\n2\r\n3\r\n4\r\n5\r\n");			/* Descending */
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
	script_spectrum("380,380,0,5", "1\r\n2\r\n3\r\n4\r\n5\r\n");			/* Zero step */
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
	script_spectrum("0,400,100,5", "1\r\n2\r\n3\r\n4\r\n5\r\n");			/* Start below 200 nm */
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
	script_spectrum("380,780,100", NULL);									/* Three fields */
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
	/* Structural grid defects refuse wavelength-axis use while retaining the
	   complete native header and bins. A wide but consistent native grid remains
	   available for the driver's separate publication decision. */
	script_spectrum("1e308,1e308,1,4", "1\r\n2\r\n3\r\n4\r\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
	script_spectrum("380,1000000000380,250000000000,5", "1\r\n2\r\n3\r\n4\r\n5\r\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_OK);
	script_spectrum("380,380,1e-14,4", "1\r\n2\r\n3\r\n4\r\n");		/* Points coincide */
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
	script_spectrum("380,380.03,0.01,4", "1\r\n2\r\n3\r\n4\r\n");	/* Step below 0.1 nm */
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_OK);
	script_spectrum("190,205,5,4", "1\r\n2\r\n3\r\n4\r\n");			/* Starts below 200 nm */
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_OK);
	script_spectrum("1000,1105,35,4", "1\r\n2\r\n3\r\n4\r\n");		/* Ends above 1100 nm */
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_OK);
	CHECK(sp.count == 4 && !cr250_imp_is_uncertain(&E) && E.inlen == 0);
	/* The bounds themselves, and the finest step, are accepted */
	script_spectrum("200,1100,300,4", "1\r\n2\r\n3\r\n4\r\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_OK);
	script_spectrum("380,380.3,0.1,4", "1\r\n2\r\n3\r\n4\r\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_OK);
	/* The manual's own example grid (4.3.1.37): 380 to 780 nm at 2 nm */
	for (i = 0; i < 201; i++) memcpy(big + 11 * i, "2.119e-24\r\n", 11);
	big[11 * i] = '\000';
	script_spectrum("380.0,780.0,2.0,201", big);
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_OK);
	CHECK(sp.count == 201 && sp.value[200] == 2.119e-24);
	/* Native bins beyond Argyll publication capacity remain parsed originals. */
	for (i = 0; i <= XSPECT_MAX_BANDS; i++) memcpy(big + 3 * i, "0\r\n", 3);
	big[3 * i] = '\000';
	{
		char h[64];
		snprintf(h, sizeof(h), "200,%d,1,%d",
		         200 + XSPECT_MAX_BANDS, XSPECT_MAX_BANDS + 1);
		script_spectrum(h, big);
	}
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_OK);
	CHECK(sp.count == XSPECT_MAX_BANDS + 1 && !cr250_imp_is_uncertain(&E) && E.inlen == 0);
	CHECK_EQ(F.closes, 0);
	/* The most native bins are parsed, independently of the physical range. */
	for (i = 0; i < CR250_MAX_SPECTRUM_LINES; i++) memcpy(big + 3 * i, "0\r\n", 3);
	big[3 * i] = '\000';
	script_spectrum("200,1199.9,0.1,10000", big);
	CHECK_EQ(CR250_MAX_SPECTRUM_LINES, 10000);
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_OK);
	CHECK(sp.count == 10000 && !cr250_imp_is_uncertain(&E) && E.inlen == 0);
	CHECK_EQ(F.closes, 0);

	/* A parseable count beyond the line bound declares a body that cannot be
	   drained. Close immediately, even if its first line arrives later. */
	fresh();
	script_spectrum("380,10380,1,10001", NULL);
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
	CHECK(cr250_imp_is_uncertain(&E) && F.closes == 1);
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_UNCERTAIN);
	CHECK_EQ(F.nwrites, 1);
	fresh();
	reply("OK:0:RM Spectrum:380,10380,1,10001\r\n");
	reply_after(500, "1\r\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
	CHECK(cr250_imp_is_uncertain(&E) && F.closes == 1 && F.ichunk == 1);
	CHECK_EQ(cr250_imp_command(&E, "RM Time", &r, 1.0), CR250_UNCERTAIN);
	CHECK_EQ(F.nwrites, 1);

	/* Framing failures leave the link uncertain and publish nothing */
	fresh();
	script_spectrum("380,780,100,5", "1\r\n2\r\n3\r\n");					/* Short */
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_TIMEOUT);
	CHECK(sp.count == 0 && cr250_imp_is_uncertain(&E));
	CHECK_EQ(F.closes, 1);
	fresh();
	script_spectrum("380,780,100,5", "1\r\n2\r\n3\r\n4\r\n5\r\n6\r\n");		/* An extra line */
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_STALE_INPUT);
	CHECK(sp.count == 0 && cr250_imp_is_uncertain(&E));
	fresh();
	script_spectrum("380,780,100,5", "1\r\n2\r\n3\r\n4\r\n5\r\n");
	F.chunks[0].data = "OK:101:RM Spectrum:380,780,100,5\r\n";
	F.chunks[0].len = (int)strlen(F.chunks[0].data);							/* Warning header */
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_NATIVE_WARNING_BASE + 101);
	CHECK(sp.count == 5 && !cr250_imp_is_uncertain(&E));
	fresh(); /* The former 64 KiB body bound no longer discards a complete response. */
	for (i = 0; i < 4000; i++) memcpy(big + 21 * i, "0.00000000000000001\r\n", 21);
	big[21 * i] = '\000';
	script_spectrum("380,4379,1,4000", big);
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_OK);
	CHECK(sp.count == 4000 && !cr250_imp_is_uncertain(&E) && E.last_raw.length > 65536);
	/* No time left for the pacing note after a slow body */
	fresh();
	script_spectrum("380,780,100,5", NULL);
	reply_after(2900, "1\r\n2\r\n3\r\n4\r\n5\r\n");
	before = vclock;
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_TIMEOUT);
	CHECK(sp.count == 0 && cr250_imp_is_uncertain(&E) && vslept == 0);
	CHECK(vclock - before < 3000);
	/* Refused before anything is sent */
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_UNCERTAIN);
	fresh();
	CHECK_EQ(cr250_imp_spectrum(&E, NULL, &r, 3.0), CR250_INTERNAL_ERROR);
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, NULL, 3.0), CR250_INTERNAL_ERROR);
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 0.0), CR250_INTERNAL_ERROR);
	CHECK_EQ(F.nwrites, 0);
}

static void test_parity_envelope(void) {
	static const char *integers[] = { "+5", "5.000", "5e0", "50e-1", "2147483648", "9007199254740991", "0e-9999" };
	static const long long expected[] = {5, 5, 5, 5, 2147483648LL, 9007199254740991LL, 0};
	static const char *fractional[] = { "5.0000000000000000001", "9007199254740990.5", "9007199254740992", "1e9999" };
	static const char malformed[] = "OK:0:RC ID:A\000\377tail\r\n";
	static const char partial[] = "OK:0:RC Aperture:2\n0,First\n1,Partial";
	cr250_inventory inv = {0};
	cr250_raw copy = {0};
	cr250_reply r;
	cr250_spectrum sp;
	long long integer;
	double value;
	const char *number;
	char *body;
	char header[128], raw[256];
	int i, extra, used;
	for (i = 0; i < (int)(sizeof(integers) / sizeof(integers[0])); i++) {
		CHECK(cr250_parse_integer(integers[i], &integer)); CHECK_EQ(integer, expected[i]);
	}
	for (i = 0; i < (int)(sizeof(fractional) / sizeof(fractional[0])); i++)
		CHECK(!cr250_parse_integer(fractional[i], &integer));
	CHECK_EQ(cr250_parse_status("OK:0e0:RC ID:A", &r), CR250_DATA_PARSE_ERROR);
	CHECK_EQ(cr250_parse_status("OK:0.0:RC ID:A", &r), CR250_DATA_PARSE_ERROR);
	number = "1e-310"; CHECK(cr250_parse_number(&number, &value) && value > 0 && !*number);
	number = "-1e-310"; CHECK(cr250_parse_number(&number, &value) && value < 0 && !*number);
	number = "-0e-9999"; CHECK(cr250_parse_number(&number, &value) && value == 0 && signbit(value));
	number = "1e-9999"; CHECK(!cr250_parse_number(&number, &value));
	number = "1e9999"; CHECK(!cr250_parse_number(&number, &value));
	fresh(); reply("OK:0:RC Aperture:1.000\n9007199254740991,Full identifier\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Aperture", &inv, 1.0), CR250_OK);
	CHECK_EQ(inv.rec[0].id, 9007199254740991LL);
	cr250_inventory_dispose(&inv);
	fresh(); script_spectrum("380,780,400,2e0", "1e-310\n-1e-310\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_OK);
	CHECK(sp.count == 2 && sp.value[0] > 0 && sp.value[1] < 0);
	/* This first step advances, but a third binary64 point repeats 1 nm. */
	snprintf(header, sizeof(header), "%.17g,1,%.17g,2", nextafter(1.0, 0.0), ldexp(1.0, -54));
	fresh(); script_spectrum(header, "1\r\n2\r\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_OK);
	CHECK(sp.count == 2 && sp.start_nm == nextafter(1.0, 0.0)
	   && sp.end_nm == 1.0 && sp.step_nm == ldexp(1.0, -54));
	CHECK(sp.value[0] == 1 && sp.value[1] == 2);
	snprintf(header, sizeof(header), "%.17g,1,%.17g,3", nextafter(1.0, 0.0), ldexp(1.0, -54));
	snprintf(raw, sizeof(raw), "OK:0:RM Spectrum:%s\r\n1\r\n2\r\n3\r\n", header);
	fresh(); script_spectrum(header, "1\r\n2\r\n3\r\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
	CHECK(sp.count == 3 && sp.start_nm == nextafter(1.0, 0.0)
	   && sp.end_nm == 1.0 && sp.step_nm == ldexp(1.0, -54));
	CHECK(sp.value[0] == 1 && sp.value[1] == 2 && sp.value[2] == 3);
	CHECK(E.last_raw.length == strlen(raw) && !memcmp(E.last_raw.bytes, raw, strlen(raw)));
	CHECK(F.ichunk == F.nchunks && E.inlen == 0 && !E.uncertain && F.closes == 0);
	/* Every point is start + i * step rounded twice, as consumers compute it.
	   A fused multiply-add (one rounding) would accept both grids. The first
	   computed end, 720.3, lies just beyond 1e-8 * end of the declared end
	   (the fused 720.3000000000001 lies just within); the second grid's third
	   and fourth points both round to 500.0000000000001. */
	{
		static char ones[401 * 3 + 1];
		for (i = 0; i < 401; i++) memcpy(ones + 3 * i, "1\r\n", 3);
		ones[3 * i] = '\000';
		fresh(); script_spectrum("200.3,720.3000072030001,1.3,401", ones);
		CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
		CHECK(sp.count == 401 && sp.step_nm == 1.3 && !E.uncertain);
		fresh(); script_spectrum("500,500.00000000000017,4.7369515717340014e-14,4", ones + 397 * 3);
		CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
		CHECK(sp.count == 4 && sp.step_nm == 4.7369515717340014e-14 && !E.uncertain);
	}
	reply("OK:0:RM Time:1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RM Time", &r, 1.0), CR250_OK);
	CHECK(F.nwrites == 2 && !E.uncertain && F.closes == 0);
	fresh(); reply_bytes(malformed, sizeof(malformed) - 1);
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_DATA_PARSE_ERROR);
	CHECK(E.last_raw.length == sizeof(malformed) - 1 && !memcmp(E.last_raw.bytes, malformed, sizeof(malformed) - 1));
	CHECK_EQ(cr250_raw_copy(&copy, &E.last_raw), CR250_OK);
	fresh(); reply("OK:0:RC ID:New\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_OK);
	CHECK(copy.bytes != E.last_raw.bytes && !memcmp(copy.bytes, malformed, sizeof(malformed) - 1));
	cr250_raw_dispose(&copy);
	fresh(); reply(partial);
	CHECK_EQ(cr250_imp_inventory(&E, "Aperture", &inv, 1.0), CR250_TIMEOUT);
	CHECK(!inv.rec && !inv.count && E.uncertain);
	CHECK(E.last_raw.length == strlen(partial) && !memcmp(E.last_raw.bytes, partial, strlen(partial)));
	fresh(); reply("OK:101:RC Aperture:unknown\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Aperture", &inv, 1.0), CR250_INVENTORY_SHAPE);
	CHECK(E.uncertain);
	fresh(); reply("OK:101:RM Spectrum:unknown\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 1.0), CR250_NATIVE_WARNING_BASE + 101);
	CHECK(E.uncertain && !sp.count);
	/* Actual LF-only bytes own the cap; estimated CRLF sizes must not reject it. */
	body = malloc(CR250_MAX_RESPONSE + 2); if (!body) abort();
	for (extra = 0; extra < 2; extra++) {
		int target = CR250_MAX_RESPONSE + extra;
		used = snprintf(body, CR250_MAX_RESPONSE + 2, "OK:0:RC Aperture:%d\n", CR250_MAX_RECORDS);
		for (i = 0; i < CR250_MAX_RECORDS; i++) {
			int prefix = snprintf(body + used, target + 1 - used, "%d,", i);
			int line = i + 1 == CR250_MAX_RECORDS ? target - used - 1 : CR250_MAX_LINE;
			used += prefix; memset(body + used, 'x', line - prefix); used += line - prefix;
			body[used++] = '\n';
		}
		body[used] = 0; CHECK(used == target);
		fresh(); reply_bytes(body, used);
		CHECK_EQ(cr250_imp_inventory(&E, "Aperture", &inv, 3.0), extra ? CR250_REPLY_TOO_LONG : CR250_OK);
		CHECK(E.last_raw.length == CR250_MAX_RESPONSE && !memcmp(E.last_raw.bytes, body, CR250_MAX_RESPONSE));
		CHECK(E.uncertain == extra);
		if (!extra) CHECK(inv.count == CR250_MAX_RECORDS && strlen(inv.rec[0].name) > 4000);
		cr250_inventory_dispose(&inv);
	}
	free(body);
}

static void test_interp(void) {
	CHECK_EQ(cr250_interp_code(CR250_OK), inst_ok);
	CHECK_EQ(cr250_interp_code(CR250_NATIVE_WARNING_BASE + 101) & inst_mask, inst_warning);
	CHECK_EQ(cr250_interp_code(CR250_NATIVE_ERROR_BASE + 303) & inst_mask, inst_misread);
	CHECK_EQ(cr250_interp_code(CR250_NATIVE_ERROR_BASE + 306) & inst_mask, inst_misread);
	CHECK_EQ(cr250_interp_code(CR250_NATIVE_ERROR_BASE + 300) & inst_mask, inst_wrong_setup);
	CHECK_EQ(cr250_interp_code(CR250_NATIVE_ERROR_BASE + 302) & inst_mask, inst_wrong_setup);
	CHECK_EQ(cr250_interp_code(CR250_NATIVE_ERROR_BASE + 332) & inst_mask, inst_wrong_setup);
	CHECK_EQ(cr250_interp_code(CR250_NATIVE_ERROR_BASE + 336) & inst_mask, inst_wrong_setup);
	CHECK_EQ(cr250_interp_code(CR250_NATIVE_ERROR_BASE + 331) & inst_mask, inst_hardware_fail);
	CHECK_EQ(cr250_interp_code(CR250_NATIVE_ERROR_BASE + 500) & inst_mask, inst_protocol_error);
	CHECK_EQ(cr250_interp_code(CR250_NATIVE_ERROR_BASE + 519) & inst_mask, inst_bad_parameter);
	CHECK_EQ(cr250_interp_code(CR250_NATIVE_ERROR_BASE + 560) & inst_mask, inst_bad_parameter);
	CHECK_EQ(cr250_interp_code(CR250_NATIVE_ERROR_BASE + 556) & inst_mask, inst_hardware_fail);
	CHECK_EQ(cr250_interp_code(CR250_NATIVE_ERROR_BASE + 307) & inst_mask, inst_other_error);	/* reserved */
	CHECK_EQ(cr250_interp_code(CR250_NATIVE_ERROR_BASE + 554) & inst_mask, inst_other_error);	/* example only, absent from 4.5 table */
	CHECK_EQ(cr250_interp_code(CR250_NATIVE_ERROR_BASE + 999) & inst_mask, inst_other_error);
	CHECK_EQ(cr250_interp_code(CR250_TIMEOUT) & inst_mask, inst_coms_fail);
	CHECK_EQ(cr250_interp_code(CR250_COMS_FAIL) & inst_mask, inst_coms_fail);
	CHECK_EQ(cr250_interp_code(CR250_UNCERTAIN) & inst_mask, inst_coms_fail);
	CHECK_EQ(cr250_interp_code(CR250_UNKNOWN_MODEL) & inst_mask, inst_unknown_model);
	CHECK_EQ(cr250_interp_code(CR250_DATA_PARSE_ERROR) & inst_mask, inst_protocol_error);
	CHECK_EQ(cr250_interp_code(CR250_BAD_ECHO) & inst_mask, inst_protocol_error);
	CHECK_EQ(cr250_interp_code(CR250_STALE_INPUT) & inst_mask, inst_protocol_error);
	CHECK_EQ(cr250_interp_code(CR250_INVENTORY_SHAPE) & inst_mask, inst_protocol_error);
	CHECK_EQ(cr250_interp_code(CR250_REFUSED_COMMAND) & inst_mask, inst_unsupported);
	CHECK_EQ(cr250_interp_code(CR250_NOT_IDENTIFIED) & inst_mask, inst_no_init);
	CHECK_EQ(cr250_interp_code(CR250_BUDGET_TOO_SHORT) & inst_mask, inst_bad_parameter);
	CHECK_EQ(cr250_interp_code(CR250_INTERNAL_ERROR) & inst_mask, inst_internal_error);

	/* Low bits, and so the native number, survive the mapping */
	CHECK_EQ(cr250_interp_code(CR250_NATIVE_ERROR_BASE + 305) & inst_imask, CR250_NATIVE_ERROR_BASE + 305);
	CHECK_EQ(cr250_native_code(cr250_interp_code(CR250_NATIVE_ERROR_BASE + 305)), -305);

	CHECK(strstr(cr250_interp_text(CR250_NATIVE_ERROR_BASE + 305), "-305") != NULL);
	CHECK(strstr(cr250_interp_text(CR250_NATIVE_ERROR_BASE + 305), "unmeasurable") != NULL);
	CHECK(strstr(cr250_interp_text(CR250_NATIVE_WARNING_BASE + 101), "warning 101") != NULL);
	CHECK(strstr(cr250_interp_text(CR250_NATIVE_ERROR_BASE + 554), "undocumented") != NULL);
	CHECK(strstr(cr250_interp_text(CR250_TIMEOUT), "uncertain") != NULL);
	CHECK_STR(cr250_interp_text(0xff11 | inst_other_error), "Unknown CR engine code");
}

static void test_review_regressions(void) {
	cr250_reply r;
	cr250_inventory inv = {0};
	char serial[CR250_MAX_VALUE];
	static const char nul_reply[] = "OK:0:RC Model:CR-250\0IGNORED\r\n";

	/* Success belongs to the requested command, not merely any OK line. */
	fresh();
	reply("OK:0:RC Model:wrong-command\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_DATA_PARSE_ERROR);
	CHECK(E.uncertain);
	fresh();
	E.identified = 1;
	reply("OK:0:RC ID:old-result\r\n");
	CHECK_EQ(cr250_imp_measure(&E, &r, 120.0, 120.0), CR250_DATA_PARSE_ERROR);
	CHECK(E.uncertain);

	/* A byte-counted transport must not hide embedded NUL or control bytes. */
	fresh();
	reply_bytes(nul_reply, sizeof(nul_reply) - 1);
	CHECK_EQ(cr250_imp_command(&E, "RC Model", &r, 1.0), CR250_DATA_PARSE_ERROR);
	fresh();
	reply("OK:0:RC Aperture:1\r\n0,Five\001degrees\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Aperture", &inv, 1.0), CR250_DATA_PARSE_ERROR);

	/* Only implemented response shapes are dispatched. */
	fresh();
	fresh();
	CHECK_EQ(cr250_imp_command(&E, "RNotDocumented", &r, 1.0), CR250_REFUSED_COMMAND);

	/* Inventories have key-specific schemas and documented empty values. */
	fresh();
	reply("OK:0:RC Accessory:1\r\n0,Standard\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Accessory", &inv, 1.0), CR250_INVENTORY_SHAPE);
	fresh();
	reply("OK:0:RC Matrix:None\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Matrix", &inv, 1.0), CR250_OK);
	CHECK_EQ(inv.count, 0);

	/* A serial report beyond the complete line envelope is refused. */
	fresh();
	memset(serial, 'A', sizeof(serial) - 1);
	serial[sizeof(serial) - 1] = '\0';
	script_identity("CR-250", serial, "1.36", "2");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_REPLY_TOO_LONG);
	CHECK(!E.identified);
	cr250_inventory_dispose(&inv);
}

static void test_transport_boundaries(void) {
	cr250_reply r;
	cr250_inventory inv = {0};
	unsigned int before;

	/* Already queued bytes must block the write, not become its reply. */
	fresh();
	reply("OK:0:RC ID:old\r\n");
	F.chunks[0].after_writes = 0;
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_STALE_INPUT);
	CHECK_EQ(F.nwrites, 0);
	CHECK_EQ(F.closes, 1);

	/* Extra transport chunks are checked even when the engine buffer is empty. */
	fresh();
	reply("OK:0:RC ID:A1\r\n");
	reply("UNEXPECTED\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_STALE_INPUT);
	CHECK_EQ(F.nwrites, 1);
	fresh();
	reply("OK:0:RC Aperture:1\r\n0,Five degrees\r\n");
	reply("1,Extra\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Aperture", &inv, 1.0), CR250_STALE_INPUT);
	CHECK_EQ(inv.count, 0);

	/* A late complete line must not override a deadline or transport error. */
	fresh();
	F.read_overrun = 1;
	reply_after(1001, "OK:0:RC ID:A1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_TIMEOUT);
	CHECK(!r.complete);
	CHECK(E.uncertain);
	fresh();
	F.read_status = ICOM_TO;
	reply_after(999, "OK:0:RC ID:A1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_TIMEOUT);
	CHECK(!r.complete);
	fresh();
	F.read_status = ICOM_SERR;
	reply("OK:0:RC ID:A1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_COMS_FAIL);
	CHECK(!r.complete);
	fresh();
	F.zero_progress = 1;
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_COMS_FAIL);
	CHECK_EQ(F.closes, 1);

	/* A slow successful write and its reply share the caller's deadline. */
	fresh();
	F.write_delay_ms = 800;
	reply_after(250, "OK:0:RC ID:A1\r\n");
	before = vclock;
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_TIMEOUT);
	CHECK(F.write_timeout > 0.998 && F.write_timeout <= 1.0);
	CHECK_EQ(vclock - before, 1000);
	CHECK_EQ(F.nwrites, 1);
	CHECK(!r.complete && E.uncertain && F.closes == 1);

	/* Wrapped millisecond clocks preserve a bounded deadline. */
	fresh();
	vclock = UINT_MAX - 50;
	before = vclock;
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_TIMEOUT);
	CHECK_EQ(vclock - before, 1000);

	/* Closed and missing transports never dispatch a request. */
	fresh();
	port->is_open = 0;
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_COMS_FAIL);
	CHECK_EQ(F.nwrites, 0);
	fresh();
	E.icom = NULL;
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_COMS_FAIL);
	CHECK_EQ(F.nwrites, 0);
	cr250_inventory_dispose(&inv);
}

static void test_identity_and_inventory_contracts(void) {
	cr250_inventory inv = {0};
	cr250_reply r;
	char serial[CR250_MAX_FIELD + 1];
	int i, serial_limit = CR250_MAX_LINE - (int)strlen("OK:0:RC ID:");
	static const char *refused[] = {
		"RM TemporalY", "RM SamplingRate", "RM Spectrum 0", "RM  Spectrum",
		"RC Accessory", "RC Matrix", "RC MatrixCalibration", "RC MatrixCalib",
		"RC MatchCalib", "R", "RSomething", "RS CMF 0", NULL
	};
	static const char *not_inventories[] = {
		"Model", "Firmware", "MatrixCalibration", "MatrixCalib", "MatchCalib", NULL
	};
	fresh();
	for (i = 0; refused[i]; i++)
		CHECK_EQ(cr250_imp_command(&E, refused[i], &r, 1.0), CR250_REFUSED_COMMAND);
	for (i = 0; not_inventories[i]; i++)
		CHECK_EQ(cr250_imp_inventory(&E, not_inventories[i], &inv, 1.0), CR250_REFUSED_COMMAND);
	CHECK_EQ(F.nwrites, 0);

	/* Identity admits the full serial that fits the complete ASCII line. */
	fresh();
	memset(serial, 'S', serial_limit);
	serial[serial_limit] = '\0';
	script_identity("CR-250", serial, "1.34.0-S28", "2");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_OK);
	CHECK_STR(E.id.serial, serial);
	CHECK_STR(E.id.firmware, "1.34.0-S28");
	CHECK(cr250_firmware_at_least(&E.id, 1, 26));
	CHECK_EQ(E.id.fw[2], 0);
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_TIMEOUT);
	CHECK(!E.identified);
	CHECK_STR(E.id.serial, "");
	CHECK_STR(E.id.firmware, "");
	fresh();
	serial[serial_limit] = 'S';
	serial[serial_limit + 1] = '\0';
	script_identity("CR-250", serial, "1.36", "2");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_REPLY_TOO_LONG);
	CHECK(!E.identified);
	fresh();
	script_identity("CR-250", "A1", "1.36", "1");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_UNKNOWN_MODEL);	/* colorimeter, not a spectroradiometer */
	CHECK(!E.identified);
	CHECK(!cr250_imp_is_uncertain(&E));
	fresh();
	script_identity("CR-250", "A1", "1.36", "0");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_UNKNOWN_MODEL);
	CHECK(!E.identified);
	/* The owner's expected type is what is enforced, not a fixed 2 */
	fresh();
	E.expected_types = 1 << 1;
	script_identity("CR-250", "A1", "1.36", "1");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_OK);
	CHECK(E.identified);
	CHECK_EQ(E.id.instrument_type, 1);
	fresh();
	E.expected_types = 1 << 1;
	script_identity("CR-250", "A1", "1.36", "2");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_UNKNOWN_MODEL);
	CHECK(!E.identified);
	/* A set of accepted types: each member passes, a non-member is refused */
	fresh();
	E.expected_types = (1 << 0) | (1 << 1);
	script_identity("CR-250", "A1", "1.36", "0");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_OK);
	CHECK_EQ(E.id.instrument_type, 0);
	fresh();
	E.expected_types = (1 << 0) | (1 << 1);
	script_identity("CR-250", "A1", "1.36", "1");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_OK);
	CHECK_EQ(E.id.instrument_type, 1);
	fresh();
	E.expected_types = (1 << 0) | (1 << 1);
	script_identity("CR-250", "A1", "1.36", "2");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_UNKNOWN_MODEL);
	fresh();
	E.expected_model = "CR-100";
	E.expected_types = 1 << 1;
	script_identity("CR-100", "A1", "1.36", "1");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_OK);
	CHECK_STR(E.id.model, "CR-100");
	fresh();
	script_identity("CR-250", "A1", "1.36-", NULL);
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_DATA_PARSE_ERROR);

	/* Native configuration and inventory diagnostics remain inspectable. */
	fresh();
	reply("OK:101:RC Model:CR-250\r\n");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_NATIVE_WARNING_BASE + 101);
	CHECK_EQ(E.last_reply.code, 101);
	CHECK_STR(E.last_reply.context, "RC Model");
	CHECK(!E.identified);
	fresh();
	reply("ER:-500:Invalid command:RC Aperture\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Aperture", &inv, 1.0), CR250_NATIVE_ERROR_BASE + 500);
	CHECK_STR(E.last_reply.value, "RC Aperture");
	CHECK(!E.uncertain);
	fresh();
	reply("OK:101:RC Aperture:1\r\n0,Five degrees\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Aperture", &inv, 1.0), CR250_NATIVE_WARNING_BASE + 101);
	CHECK_EQ(E.last_reply.code, 101);
	CHECK_EQ(inv.count, 1);
	CHECK(!E.uncertain);

	fresh();
	reply("OK:0:RC Aperture:0\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Aperture", &inv, 1.0), CR250_INVENTORY_SHAPE);
	CHECK(!cr250_imp_is_uncertain(&E));		/* nothing can follow a zero count */
	CHECK_EQ(F.closes, 0);
	reply("OK:0:RC ID:A1\r\n");
	CHECK_EQ(cr250_imp_command(&E, "RC ID", &r, 1.0), CR250_OK);
	fresh();
	reply("OK:0:RC Match:None\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Match", &inv, 1.0), CR250_OK);
	CHECK_EQ(inv.count, 0);
	fresh();
	reply("OK:0:RC Filter:1\r\n0,Filter name\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Filter", &inv, 1.0), CR250_INVENTORY_SHAPE);
	fresh();
	reply("OK:0:RC Aperture:1\r\n0,A,Unexpected type\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Aperture", &inv, 1.0), CR250_INVENTORY_SHAPE);
	fresh();
	reply("OK:0:RC Filter:1\r\n3,ND-100-1,Radiance\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Filter", &inv, 1.0), CR250_OK);
	CHECK_EQ(inv.fields, 3);
	CHECK_STR(inv.rec[0].type, "Radiance");
	fresh();
	reply("OK:0:RC Range:1\r\n+1,A\r\n");
	CHECK_EQ(cr250_imp_inventory(&E, "Range", &inv, 1.0), CR250_OK);
	CHECK_EQ(cr250_parse_status("OK:+0:RC ID:A1", &r), CR250_OK);
	CHECK_EQ(cr250_parse_status("OK:0::A1", &r), CR250_DATA_PARSE_ERROR);
	fresh();
	E.identified = 1;
	CHECK_EQ(cr250_imp_measure(&E, &r, 1.0, 1.0), CR250_BAD_BUDGET_INPUT);
	CHECK_EQ(F.nwrites, 0);
	cr250_inventory_dispose(&inv);
}

/* ------------------------------------------------------------------- */


/* Complete native errors and incomplete data must not reuse a prior result. */
static void test_empty_errors_and_delayed_spectrum(void) {
	cr250_reply r;
	cr250_spectrum sp;
	unsigned int before;
	int i;
	const char *bad[] = { "ER:-305:M\r\n", "ER:-305:M:" };
	int expected[] = { CR250_DATA_PARSE_ERROR, CR250_TIMEOUT };

	fresh();
	script_identity("CR-250", "A1", "1.36", "2");
	CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_OK);
	reply("OK:0:M:No errors\r\n");
	CHECK_EQ(cr250_imp_measure(&E, &r, 120.0, 120.0), CR250_OK);
	reply("ER:-305:M:\r\n");
	CHECK_EQ(cr250_imp_measure(&E, &r, 120.0, 120.0), CR250_NATIVE_ERROR_BASE + 305);
	CHECK(r.complete && r.is_error && r.code == -305);
	CHECK_STR(r.context, "M"); CHECK_STR(r.value, "");
	CHECK(E.last_reply.complete && E.last_reply.code == -305);
	CHECK_STR(E.last_reply.context, "M"); CHECK_STR(E.last_reply.value, "");
	CHECK_EQ(F.nwrites, 6);
	CHECK(E.identified && !E.uncertain && F.closes == 0);

	for (i = 0; i < 2; i++) {
		fresh();
		script_identity("CR-250", "A1", "1.36", "2");
		CHECK_EQ(cr250_imp_identify(&E, 2.0), CR250_OK);
		reply("OK:0:M:No errors\r\n");
		CHECK_EQ(cr250_imp_measure(&E, &r, 120.0, 120.0), CR250_OK);
		reply(bad[i]); before = vclock;
		CHECK_EQ(cr250_imp_measure(&E, &r, 120.0, 120.0), expected[i]);
		CHECK(!r.complete && !E.last_reply.complete);
		CHECK(E.uncertain && !E.identified && F.closes == 1);
		CHECK_EQ(F.nwrites, 6);
		if (i == 1) CHECK_EQ(vclock - before, 120000);
		CHECK_EQ(cr250_imp_measure(&E, &r, 120.0, 120.0), CR250_UNCERTAIN);
		CHECK_EQ(F.nwrites, 6); CHECK_EQ(F.closes, 1);
	}

	/* No model-specific zero-end fallback, even after a successful spectrum. */
	fresh();
	script_spectrum("380,395,5,4", "1\r\n2\r\n3\r\n4\r\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_OK);
	CHECK_EQ(sp.count, 4);
	script_spectrum("380,0,5,4", "1\r\n2\r\n3\r\n4\r\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_SPECTRUM_SHAPE);
	CHECK(sp.count == 4 && sp.start_nm == 380 && sp.end_nm == 0
	   && sp.step_nm == 5 && sp.value[0] == 1 && sp.value[3] == 4);
	CHECK(!E.uncertain && F.closes == 0 && E.inlen == 0);
	CHECK_EQ(F.nwrites, 2);

	/* Delayed fragments may split an exponent and the CRLF pair. */
	fresh(); before = vclock;
	reply("OK:0:RM Spectrum:380,395,5,4\r\n");
	reply_after(500, "1.2e"); reply_after(500, "-3\r");
	reply_after(500, "\n2\r\n3\r\n"); reply_after(500, "4\r\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_OK);
	CHECK(sp.count == 4 && sp.value[0] == 0.0012 && sp.value[3] == 4);
	CHECK(vclock - before >= 2200 && vclock - before < 3000);
	CHECK_EQ(vslept, CR250_SPECTRUM_GUARD_MS);
	CHECK_EQ(F.nwrites, 1);

	/* Individual chunks fit; their total cannot restart the one deadline. */
	fresh(); before = vclock;
	reply("OK:0:RM Spectrum:380,395,5,4\r\n");
	reply_after(800, "1.2e"); reply_after(800, "-3\r");
	reply_after(800, "\n2\r\n3\r\n"); reply_after(800, "4\r\n");
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_TIMEOUT);
	CHECK(sp.count == 0 && E.uncertain && F.closes == 1);
	CHECK_EQ(vclock - before, 3000);
	CHECK_EQ(F.nwrites, 1); CHECK_EQ(vslept, 0);

	/* All declared numbers arrived, but the final sample has no terminator. */
	fresh();
	script_spectrum("380,395,5,4", "1\r\n2\r\n3\r\n4");
	before = vclock;
	CHECK_EQ(cr250_imp_spectrum(&E, &sp, &r, 3.0), CR250_TIMEOUT);
	CHECK(sp.count == 0 && E.uncertain && F.closes == 1);
	CHECK_EQ(vclock - before, 3000);
	CHECK_EQ(F.nwrites, 1);
}

int main(int argc, char *argv[]) {
	int debug = argc > 1 && strcmp(argv[1], "-D") == 0;
	setvbuf(stdout, NULL, _IONBF, 0);

	tlog = new_a1log(NULL, 0, debug ? 8 : 0, NULL, NULL, NULL, NULL);
	if ((port = (icoms *)calloc(1, sizeof(icoms))) == NULL) {
		fprintf(stderr, "calloc failed\n");
		return 2;
	}
	port->dctype = icomt_fastserial;
	port->log = tlog;
	port->read = fake_read;
	port->write = fake_write;
	port->close_port = fake_close;

	test_empty_errors_and_delayed_spectrum();
	test_parse_status();
	test_native_code_roundtrip();
	test_identity_with_mixed_terminators();
	test_identity_refusals();
	test_echo_handling();
	test_stale_input();
	test_framing_limits_and_failures();
	test_request_validation_and_refusals();
	test_warning_and_error_replies();
	test_inventory();
	test_measure_budget();
	test_measure_transaction();
	test_setting_writes();
	test_spectrum_transaction();
	test_interp();
	test_review_regressions();
	test_transport_boundaries();
	test_identity_and_inventory_contracts();
	test_parity_envelope();

	printf("cr250_test: %d checks, %d failures\n", checks, failures);
	cr250_imp_dispose(&E);
	free(port);
	return failures == 0 ? 0 : 1;
}
