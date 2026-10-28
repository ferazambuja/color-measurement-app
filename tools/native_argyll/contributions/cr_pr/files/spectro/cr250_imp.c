
/*
 * Argyll Color Management System
 *
 * Colorimetry Research CR-250 remote-protocol transport and transaction
 * engine. See cr250_imp.h for the contract this file implements.
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
 * Written from the manufacturer's "Remote Communication" manual, revision
 * 1.36 (2018). Section numbers in comments refer to that document. No
 * instrument has been driven by this code; every behaviour is a reading
 * of the manual exercised only with synthetic transcripts.
 */

/*
   The author(s) of this driver are responsible for its operation. Direct
   problems or queries about driving an instrument with it to the author(s)
   of this driver and of Argyll, not to Colorimetry Research or any other
   party.

   Before adding an instrument feature or command, contact Argyll's
   author(s). An incautious command sequence could damage an instrument or
   its calibration, and a manufacturer need not support developers it has
   not qualified. This driver sends read-only RC/RS/RM queries, the single
   measurement command M, and SM only for the settings cr250.txt lists, each
   read back once written.
 */

#include <stdio.h>
#include <stdlib.h>
#include <ctype.h>
#include <string.h>
#include <math.h>
#include <stdarg.h>
#include <errno.h>
#include <float.h>
#include <limits.h>
#ifndef SALONEINSTLIB
#include "copyright.h"
#include "aconfig.h"
#include "numlib.h"
#else	/* !SALONEINSTLIB */
#include "sa_config.h"
#include "numsup.h"
#endif /* !SALONEINSTLIB */
#include "cgats.h"
#include "xspect.h"
#include "insttypes.h"
#include "conv.h"
#include "icoms.h"
#include "inst.h"
#include "cr250_imp.h"

/* Longest transaction any caller may ask for, in seconds. Bounds the
   millisecond deadline arithmetic. */
#define CR250_MAX_TIMEOUT_S (24.0 * 3600.0)

void cr250_raw_dispose(cr250_raw *raw) {
	if (raw == NULL) return;
	free(raw->bytes);
	memset(raw, 0, sizeof(*raw));
}

static void raw_reset(cr250_raw *raw) {
	raw->length = 0;
	if (raw->bytes != NULL) raw->bytes[0] = '\000';
}

static int raw_append(cr250_raw *raw, const char *bytes, int count) {
	int needed, capacity, overflow = 0;
	char *grown;
	if (count < 0) return CR250_REPLY_TOO_LONG;
	if (count > CR250_MAX_RESPONSE - raw->length) {
		count = CR250_MAX_RESPONSE - raw->length;
		overflow = 1;
	}
	needed = raw->length + count + 1;
	if (needed > raw->capacity) {
		capacity = raw->capacity ? raw->capacity : 256;
		while (capacity < needed && capacity < CR250_MAX_RESPONSE + 1)
			capacity = capacity > (CR250_MAX_RESPONSE + 1) / 2
			         ? CR250_MAX_RESPONSE + 1 : capacity * 2;
		grown = (char *)realloc(raw->bytes, capacity);
		if (grown == NULL) return CR250_INTERNAL_ERROR;
		raw->bytes = grown; raw->capacity = capacity;
	}
	if (count) memcpy(raw->bytes + raw->length, bytes, count);
	raw->length += count; raw->bytes[raw->length] = '\000';
	return overflow ? CR250_REPLY_TOO_LONG : CR250_OK;
}

int cr250_raw_copy(cr250_raw *dest, const cr250_raw *source) {
	char *bytes = NULL;
	if (dest == NULL || source == NULL) return CR250_INTERNAL_ERROR;
	if (dest == source) return CR250_OK;
	if (source->length < 0 || source->length > CR250_MAX_RESPONSE
	 || (source->length && source->bytes == NULL)) return CR250_INTERNAL_ERROR;
	if (source->length) {
		bytes = (char *)malloc(source->length + 1);
		if (bytes == NULL) return CR250_INTERNAL_ERROR;
		memcpy(bytes, source->bytes, source->length); bytes[source->length] = '\000';
	}
	cr250_raw_dispose(dest);
	dest->bytes = bytes; dest->length = source->length;
	dest->capacity = source->length ? source->length + 1 : 0;
	return CR250_OK;
}

void cr250_inventory_dispose(cr250_inventory *inventory) {
	if (inventory == NULL) return;
	free(inventory->rec);
	memset(inventory, 0, sizeof(*inventory));
}

void cr250_imp_dispose(cr250_imp *p) {
	if (p != NULL) cr250_raw_dispose(&p->last_raw);
}

/* ------------------------------------------------------------------- */
/* Small helpers */

static unsigned int default_now(void) {
	return msec_time();
}

static void default_sleep(unsigned int ms) {
	msec_sleep(ms);
}

/* Printable rendering of a reply for debug logs. Static buffer, as for
   icoms_fix(); not for concurrent use. */
static char *cr250_fix(const char *s) {
	static char buf[CR250_MAX_LINE + 64];
	char *d = buf, *end = buf + sizeof(buf) - 5;

	for (; *s != '\000' && d < end; s++) {
		if (*s == '\r') {
			*d++ = '\\'; *d++ = 'r';
		} else if (*s == '\n') {
			*d++ = '\\'; *d++ = 'n';
		} else if (*s < 0x20 || *s > 0x7e) {
			*d++ = '?';
		} else {
			*d++ = *s;
		}
	}
	if (*s != '\000') {
		*d++ = '.'; *d++ = '.'; *d++ = '.';
	}
	*d = '\000';
	return buf;
}

/* Exact original-token integer semantics, independent of host long width. */
int cr250_parse_integer(const char *text, long long *value) {
	const char *p = text;
	char digits[CR250_MAX_LINE + 1];
	int n = 0, fraction = 0, exponent = 0, exponent_sign = 1;
	int negative = 0, first = 0, last, power, i;
	long long v = 0, limit = 9007199254740991LL;
	if (p == NULL || value == NULL) return 0;
	if (*p == '+' || *p == '-') { negative = *p == '-'; p++; }
	while (*p >= '0' && *p <= '9') {
		if (n == CR250_MAX_LINE) return 0;
		digits[n++] = *p++;
	}
	if (*p == '.') {
		p++;
		while (*p >= '0' && *p <= '9') {
			if (n == CR250_MAX_LINE) return 0;
			digits[n++] = *p++; fraction++;
		}
	}
	if (!n) return 0;
	if (*p == 'e' || *p == 'E') {
		p++;
		if (*p == '+' || *p == '-') { exponent_sign = *p == '-' ? -1 : 1; p++; }
		if (*p < '0' || *p > '9') return 0;
		while (*p >= '0' && *p <= '9') {
			if (exponent < 100000) exponent = exponent * 10 + *p - '0';
			p++;
		}
	}
	if (*p) return 0;
	while (first < n && digits[first] == '0') first++;
	if (first == n) { *value = 0; return 1; }
	last = n;
	while (last > first && digits[last - 1] == '0') last--;
	power = exponent_sign * exponent - fraction + n - last;
	if (power < 0 || last - first + power > 16) return 0;
	for (i = first; i < last; i++) {
		int digit = digits[i] - '0';
		if (v > (limit - digit) / 10) return 0;
		v = v * 10 + digit;
	}
	for (i = 0; i < power; i++) {
		if (v > limit / 10) return 0;
		v *= 10;
	}
	*value = negative ? -v : v;
	return 1;
}

static int parse_long(const char *s, long *value) {
	const char *p = s;
	long long v;
	if (p == NULL) return 0;
	if (*p == '-' || *p == '+') p++;
	if (*p < '0' || *p > '9') return 0;
	for (; *p; p++) if (*p < '0' || *p > '9') return 0;
	if (!cr250_parse_integer(s, &v) || v < LONG_MIN || v > LONG_MAX) return 0;
	*value = (long)v;
	return 1;
}

int cr250_finite(double x) {
	return x >= -DBL_MAX && x <= DBL_MAX;	/* A NaN fails both comparisons */
}

/* Decimal ASCII only. Keep representable subnormals and signed zero; reject
   overflow, nonzero values lost to zero and anything beyond the grammar. */
int cr250_parse_number(const char **src, double *value) {
	const char *s = *src, *a = s;
	char *end;
	int digits = 0, nonzero = 0;
	if (*s == '+' || *s == '-') s++;
	while (*s >= '0' && *s <= '9') { nonzero |= *s != '0'; s++; digits++; }
	if (*s == '.') {
		s++;
		while (*s >= '0' && *s <= '9') { nonzero |= *s != '0'; s++; digits++; }
	}
	if (!digits) return 0;
	if (*s == 'e' || *s == 'E') {
		s++;
		if (*s == '+' || *s == '-') s++;
		if (*s < '0' || *s > '9') return 0;
		while (*s >= '0' && *s <= '9') s++;
	}
	errno = 0;
	*value = strtod(a, &end);
	if ((errno && errno != ERANGE) || end != s || !cr250_finite(*value)
	 || (*value == 0.0 && nonzero)) return 0;
	*src = s;
	return 1;
}

/* Copy a bounded field. nz on success, z if it does not fit. */
static int copy_field(char *dst, int dsize, const char *src, int len) {
	if (len < 0 || len >= dsize)
		return 0;
	memcpy(dst, src, len);
	dst[len] = '\000';
	return 1;
}

/* nz if every byte is printable ASCII */
static int all_printable(const char *s) {
	for (; *s != '\000'; s++) {
		if (*s < 0x20 || *s > 0x7e)
			return 0;
	}
	return 1;
}

int cr250_native_code(int ec) {
	ec &= inst_imask;
	if (ec > CR250_NATIVE_WARNING_BASE && ec <= CR250_NATIVE_WARNING_BASE + CR250_NATIVE_MAX)
		return ec - CR250_NATIVE_WARNING_BASE;
	if (ec > CR250_NATIVE_ERROR_BASE && ec <= CR250_NATIVE_ERROR_BASE + CR250_NATIVE_MAX)
		return -(ec - CR250_NATIVE_ERROR_BASE);
	return 0;
}

/* The one echo rule, for read_status and inst.c's fast-serial discovery probe. */
int cr250_echo_line(const char *line, const char *cmd) {
	size_t n = strlen(cmd);
	if (*line == '>')
		line++;
	return strncmp(line, cmd, n) == 0 && (line[n] == '\000' || strcmp(line + n, "\r\n") == 0);
}

/* ------------------------------------------------------------------- */
/* Status line (manual 2.2.1):
     OK:code:context:value
     ER:code:description:message
   The first three fields contain no ':'; the fourth is the rest of the
   line and may contain anything, so a time value keeps its colons. */

int cr250_parse_status(const char *line, cr250_reply *r) {
	const char *f1, *f2, *f3, *f4;
	long code;
	int is_error;

	if (line == NULL || r == NULL)
		return CR250_INTERNAL_ERROR;
	memset(r, 0, sizeof(*r));
	if (strlen(line) > CR250_MAX_LINE) return CR250_DATA_PARSE_ERROR;

	if (strncmp(line, "OK:", 3) == 0)
		is_error = 0;
	else if (strncmp(line, "ER:", 3) == 0)
		is_error = 1;
	else
		return CR250_DATA_PARSE_ERROR;

	f1 = line + 3;
	if ((f2 = strchr(f1, ':')) == NULL)
		return CR250_DATA_PARSE_ERROR;
	if ((f3 = strchr(f2 + 1, ':')) == NULL)
		return CR250_DATA_PARSE_ERROR;
	f4 = f3 + 1;

	{
		char cbuf[16];
		if (!copy_field(cbuf, sizeof(cbuf), f1, (int)(f2 - f1)))
			return CR250_DATA_PARSE_ERROR;
		if (!parse_long(cbuf, &code))
			return CR250_DATA_PARSE_ERROR;
	}
	/* Manual 4.5: negative numbers are errors, positive warnings, 0 no error. */
	if (is_error && code >= 0)
		return CR250_DATA_PARSE_ERROR;
	if (!is_error && code < 0)
		return CR250_DATA_PARSE_ERROR;

	if (!copy_field(r->context, sizeof(r->context), f2 + 1, (int)(f3 - f2 - 1)))
		return CR250_DATA_PARSE_ERROR;
	if (r->context[0] == '\0')
		return CR250_DATA_PARSE_ERROR;
	if (!copy_field(r->value, sizeof(r->value), f4, (int)strlen(f4)))
		return CR250_DATA_PARSE_ERROR;
	if (!all_printable(r->context) || !all_printable(r->value))
		return CR250_DATA_PARSE_ERROR;

	r->is_error = is_error;
	r->code = code;
	r->complete = 1;
	return CR250_OK;
}

/* Result for a complete, parsed reply */
static int reply_result(const cr250_reply *r) {
	if (r->code == 0)
		return CR250_OK;
	if (r->code > 0) {
		if (r->code > CR250_NATIVE_MAX)
			return CR250_DATA_PARSE_ERROR;
		return CR250_NATIVE_WARNING_BASE + (int)r->code;
	}
	if (r->code < -CR250_NATIVE_MAX)
		return CR250_DATA_PARSE_ERROR;
	return CR250_NATIVE_ERROR_BASE + (int)(-r->code);
}

/* ------------------------------------------------------------------- */
/* Link state */

void cr250_imp_init(cr250_imp *p, icoms *icom, a1log *log) {
	memset(p, 0, sizeof(*p));
	p->icom = icom;
	p->log = log;
	p->expected_model = "CR-250";
	p->expected_types = 1 << 2;
	p->now_ms = default_now;
	p->sleep_ms = default_sleep;
	p->id.instrument_type = -1;
}

int cr250_imp_is_uncertain(const cr250_imp *p) {
	return p->uncertain;
}

/* Abandon the exchange: the instrument's state is no longer known. Close
   the port once, forget identity and pending input, and report ec. */
static int fail_uncertain(cr250_imp *p, int ec, const char *why) {
	a1logd(p->log, 1, "cr250: %s; link is now uncertain (0x%x)\n", why, ec);
	p->uncertain = 1;
	p->identified = 0;
	memset(&p->id, 0, sizeof(p->id));
	p->id.instrument_type = -1;
	p->inlen = 0;
	p->drop_lf = 0;
	/* icoms close_port clears is_open (icoms.c:557-570), so a later failure
	 * cannot close the port twice. */
	if (p->icom != NULL && p->icom->is_open && p->icom->close_port != NULL)
		p->icom->close_port(p->icom);
	return ec;
}

int cr250_imp_recover(cr250_imp *p) {
	if (p == NULL)
		return CR250_INTERNAL_ERROR;
	if (p->icom == NULL || !p->icom->is_open) {
		a1logd(p->log, 1, "cr250: recovery refused, port is not open\n");
		return CR250_COMS_FAIL;
	}
	p->uncertain = 0;
	p->identified = 0;
	p->inlen = 0;
	p->drop_lf = 0;
	memset(&p->last_reply, 0, sizeof(p->last_reply));
	memset(&p->id, 0, sizeof(p->id));
	p->id.instrument_type = -1;
	return CR250_OK;
}

/* ------------------------------------------------------------------- */
/* Framing (manual 2.2): a reply line ends in CR, LF or CR LF. The pair
   may be split across reads, so a trailing CR remembers to drop one
   following LF. */

/* Drop a pending LF that completes an earlier CR, if it has arrived. */
static void settle_split_crlf(cr250_imp *p) {
	if (p->drop_lf && p->inlen > 0) {
		if (p->inbuf[0] == '\n') {
			memmove(p->inbuf, p->inbuf + 1, p->inlen - 1);
			p->inlen--;
		}
		p->drop_lf = 0;
	}
}

/* Take one terminated line out of the pending buffer.
   Returns 1 with the line, 0 if no complete line is pending, or -1 if
   the pending text already exceeds the line bound, -2 for a byte outside
   printable ASCII. */
static int take_line(cr250_imp *p, char *line, int lsize) {
	int i;

	settle_split_crlf(p);

	for (i = 0; i < p->inlen; i++) {
		if (p->inbuf[i] == '\r' || p->inbuf[i] == '\n') {
			int consume = i + 1;
			int j;

			if (i >= lsize || i > CR250_MAX_LINE)
				return -1;
			for (j = 0; j < i; j++)
				if ((unsigned char)p->inbuf[j] < 0x20 || (unsigned char)p->inbuf[j] > 0x7e)
					return -2;
			memcpy(line, p->inbuf, i);
			line[i] = '\000';
			if (p->inbuf[i] == '\r') {
				if (i + 1 < p->inlen) {
					if (p->inbuf[i + 1] == '\n')
						consume++;
				} else {
					p->drop_lf = 1;
				}
			}
			memmove(p->inbuf, p->inbuf + consume, p->inlen - consume);
			p->inlen -= consume;
			return 1;
		}
	}
	if (p->inlen > CR250_MAX_LINE)
		return -1;
	return 0;
}

/* Read one line, waiting no later than the absolute deadline. */
static int read_line(cr250_imp *p, char *line, int lsize, unsigned int deadline_ms) {
	for (;;) {
		int rv, se, ec, space, bread = 0;
		unsigned int now;
		double tout;
		char tmp[CR250_INBUF];

		now = p->now_ms();
		if ((int)(deadline_ms - now) <= 0)
			return CR250_TIMEOUT;
		if ((rv = take_line(p, line, lsize)) > 0)
			return CR250_OK;
		if (rv == -2)
			return CR250_DATA_PARSE_ERROR;
		if (rv < 0)
			return CR250_REPLY_TOO_LONG;

		now = p->now_ms();
		if ((int)(deadline_ms - now) <= 0)
			return CR250_TIMEOUT;
		tout = (deadline_ms - now) / 1000.0;

		space = CR250_INBUF - p->inlen;
		if (space < 3)
			return CR250_REPLY_TOO_LONG;

		/* The transport returns when it has seen a terminator, when the
		   buffer fills, or at the timeout, whichever comes first. It may
		   return more or less than one line, so the buffer above does
		   the line splitting. */
		se = p->icom->read(p->icom, tmp, space, &bread, "\r\n", 1, tout);
		if (bread < 0 || bread > space - 1)
			return CR250_COMS_FAIL;
		if ((ec = raw_append(&p->last_raw, tmp, bread)) != CR250_OK) return ec;
		if (bread > 0) {
			memcpy(p->inbuf + p->inlen, tmp, bread);
			p->inlen += bread;
		}
		a1logd(p->log, 8, "cr250: read %d bytes, status 0x%x\n", bread, se);
		if (se == ICOM_TO) {
			if (bread == 0)
				return CR250_TIMEOUT;	/* The wait covered the whole remaining time */
			continue;					/* Data arrived; look for a line, then re-check the deadline */
		}
		if (se != ICOM_OK)
			return CR250_COMS_FAIL;
		if (bread == 0)
			return CR250_COMS_FAIL; /* No progress is not a successful read. */
	}
}

/* ------------------------------------------------------------------- */
/* Requests */

/* Request text checks: printable ASCII, bounded, no terminator inside. */
static int request_well_formed(const char *cmd) {
	size_t n;

	if (cmd == NULL || (n = strlen(cmd)) == 0 || n > CR250_MAX_COMMAND)
		return 0;
	if (!all_printable(cmd))
		return 0;
	if (cmd[0] == ' ' || cmd[n - 1] == ' ')
		return 0;
	return 1;
}

/* Commands outside this layer's contract. Only read commands ("R" root,
   manual 4.3) go through cr250_imp_command; "M" goes through
   cr250_imp_measure alone so one request is one exposure.
   Excluded on purpose: "E" toggles echo blind (4.1); "MT", "MA" and "MF"
   are reserved (4.3.4.2 to 4.3.4.4); every "S" and "C" root command
   writes setup, computation or calibration state (4.2, 4.4) and no
   single-line read may write; the setup keys this contract sets go through
   cr250_imp_setting. "RM Spectrum" answers with a data body and goes through
   cr250_imp_spectrum; "RM Temporal" answers with one this layer has no reader
   for (4.3.1.38), so it is refused rather than left half-consumed. */
static int request_refused(const char *cmd) {
	/* Exact documented single-line commands only. TemporalY has a body too;
	   RM SamplingRate requires temporal pacing (manual 4.3.1.40), which this
	   layer does not provide. RC Min/MaxSamplingRate and RS SamplingRate are
	   ordinary single-line reads.
	   Inventory and calibration-factor reads must not use this entry point. */
	static const char *reads[] = {
		"RC ID", "RC Model", "RC Firmware", "RC InstrumentType",
		"RC MinExposure", "RC MaxExposure", "RC MinSyncFreq", "RC MaxSyncFreq",
		"RC MinExposureX", "RC MaxExposureX", "RC MinSamplingRate", "RC MaxSamplingRate",
		"RS Accessory", "RS Filter", "RS Aperture", "RS Mode", "RS RangeMode",
		"RS Range", "RS ExposureMode", "RS Exposure", "RS SyncMode", "RS SyncFreq",
		"RS ExposureX", "RS MatrixMode", "RS UserCalibMode", "RS Matrix", "RS Match",
		"RS Speed", "RS SamplingRate", "RS MaxFreqFlickerSearch", "RS CMF",
		"RM ID", "RM Model", "RM Time", "RM Accessory", "RM Filter", "RM Aperture",
		"RM Mode", "RM ExposureMode", "RM Exposure", "RM MaxAutoExposure",
		"RM RangeMode", "RM Range", "RM SyncMode", "RM SyncFreq", "RM ExposureX",
		"RM MatrixMode", "RM UserCalibMode", "RM Matrix", "RM Match", "RM Speed",
		"RM X", "RM X10", "RM Y", "RM Y10", "RM Z", "RM Z10", "RM XYZ", "RM XYZ10",
		"RM xy", "RM xy10", "RM uv", "RM upvp", "RM CCT", "RM Warnings",
		"RM Yv", "RM Radiometric", "RM CMF", NULL
	};
	int i;
	for (i = 0; reads[i] != NULL; i++)
		if (strcmp(cmd, reads[i]) == 0)
			return 0;
	return 1;
}

/* A short icoms read is needed: a zero timeout in icoms_ser_read does not
   poll the OS queue. Check buffered and already-arriving bytes without
   silently flushing them. A delayed, indistinguishable reply cannot be ruled
   out by this check: the protocol has no transaction identifier. */
static int check_idle_input(cr250_imp *p, unsigned int deadline_ms) {
	int pass;
	for (pass = 0; pass < 2; pass++) {
		char buf[CR250_INBUF];
		int bread = 0, se, ec;
		settle_split_crlf(p);
		if (p->inlen != 0)
			return fail_uncertain(p, CR250_STALE_INPUT, "unexpected input outside reply");
		if ((int)(deadline_ms - p->now_ms()) <= 0)
			return fail_uncertain(p, CR250_TIMEOUT, "deadline before input check");
		se = p->icom->read(p->icom, buf, sizeof(buf), &bread, NULL, 1, 0.001);
		if (bread < 0 || bread >= (int)sizeof(buf))
			return fail_uncertain(p, CR250_COMS_FAIL, "input check failed");
		if ((ec = raw_append(&p->last_raw, buf, bread)) != CR250_OK)
			return fail_uncertain(p, ec, "input check exceeded response bound");
		if (se != ICOM_OK && se != ICOM_TO)
			return fail_uncertain(p, CR250_COMS_FAIL, "input check failed");
		if ((int)(deadline_ms - p->now_ms()) < 0)
			return fail_uncertain(p, CR250_TIMEOUT, "input check exceeded deadline");
		if (bread == 0)
			return se == ICOM_TO ? CR250_OK
			       : fail_uncertain(p, CR250_COMS_FAIL, "input check made no progress");
		memcpy(p->inbuf, buf, bread);
		p->inlen = bread;
		settle_split_crlf(p);
		if (p->inlen != 0)
			return fail_uncertain(p, CR250_STALE_INPUT, "extra or stale transport bytes");
		/* Only one LF can be consumed; any second nonempty read is stale. */
	}
	return CR250_OK;
}

/* Send "<cmd>\r\n". Any pending input means the previous exchange was not
   fully understood, and the link becomes uncertain. The only tolerated
   leftover is the LF half of a split CR LF, which settle_split_crlf()
   removes. */
static int send_request(cr250_imp *p, const char *cmd, unsigned int deadline_ms) {
	char buf[CR250_MAX_COMMAND + 3];
	int se, ec;
	unsigned int now;
	raw_reset(&p->last_raw);
	memset(&p->last_reply, 0, sizeof(p->last_reply));
	if (p->icom == NULL || !p->icom->is_open || p->icom->read == NULL
	 || p->icom->write == NULL || p->icom->close_port == NULL)
		return fail_uncertain(p, CR250_COMS_FAIL, "transport is not ready");

	settle_split_crlf(p);
	if (p->inlen > 0)
		return fail_uncertain(p, CR250_STALE_INPUT, "unconsumed input before request");

	if ((ec = check_idle_input(p, deadline_ms)) != CR250_OK)
		return ec;
	now = p->now_ms();
	if ((int)(deadline_ms - now) <= 0)
		return fail_uncertain(p, CR250_TIMEOUT, "deadline before write");

	snprintf(buf, sizeof(buf), "%s\r\n", cmd);
	a1logd(p->log, 4, "cr250: sending '%s'\n", cr250_fix(buf));
	if ((se = p->icom->write(p->icom, buf, 0, (deadline_ms - now) / 1000.0)) != ICOM_OK)
		return fail_uncertain(p, se == ICOM_TO ? CR250_TIMEOUT : CR250_COMS_FAIL, "write failed");
	if ((int)(deadline_ms - p->now_ms()) <= 0)
		return fail_uncertain(p, CR250_TIMEOUT, "write exhausted deadline");
	return CR250_OK;
}

/* Read up to the status line of one reply. Before it, at most
   CR250_MAX_PRELINES lines are tolerated and each must be an exact echo
   of the request, with or without the '>' prompt (manual 4.1). The manual
   documents no other text before a status line, so none is accepted.
   An OK or warning context must equal cmd when contexts is NULL; otherwise
   it must be in the supplied NULL-terminated context list. */
static int read_status(cr250_imp *p, const char *cmd, const char *const *contexts,
                       cr250_reply *r, unsigned int deadline_ms) {
	char line[CR250_MAX_LINE + 1];
	int pre = 0;

	for (;;) {
		int ec;

		if ((ec = read_line(p, line, sizeof(line), deadline_ms)) != CR250_OK)
			return fail_uncertain(p, ec, "no complete status line");

		a1logd(p->log, 4, "cr250: line '%s'\n", cr250_fix(line));

		if (strncmp(line, "OK:", 3) == 0 || strncmp(line, "ER:", 3) == 0) {
			if ((ec = cr250_parse_status(line, r)) != CR250_OK)
				return fail_uncertain(p, ec, "status line not understood");
			p->last_reply = *r;
			/* ER fields describe the error, not necessarily the command.
			   OK and warning replies do carry the command context. */
			if (!r->is_error) {
				int i, known = contexts == NULL && strcmp(r->context, cmd) == 0;
				for (i = 0; contexts != NULL && contexts[i] != NULL; i++)
					known |= strcmp(r->context, contexts[i]) == 0;
				if (!known)
					return fail_uncertain(p, CR250_DATA_PARSE_ERROR, "reply belongs to another command");
			}
			return reply_result(r);
		}

		if (++pre > CR250_MAX_PRELINES)
			return fail_uncertain(p, CR250_BAD_ECHO, "too many lines before the status line");
		if (cr250_echo_line(line, cmd))
			continue;
		return fail_uncertain(p, CR250_BAD_ECHO, "unexpected text before the status line");
	}
}

static int deadline_from(cr250_imp *p, double timeout_s, unsigned int *deadline_ms) {
	if (!(timeout_s > 0.0) || !(timeout_s <= CR250_MAX_TIMEOUT_S))	/* Also rejects NaN */
		return CR250_INTERNAL_ERROR;
	if (timeout_s < 0.001)
		return CR250_INTERNAL_ERROR;
	*deadline_ms = p->now_ms() + (unsigned int)(timeout_s * 1000.0 + 0.5);
	return CR250_OK;
}

int cr250_imp_command(cr250_imp *p, const char *cmd, cr250_reply *r, double timeout_s) {
	unsigned int deadline;
	int ec;

	if (p == NULL || r == NULL)
		return CR250_INTERNAL_ERROR;
	memset(r, 0, sizeof(*r));
	if (!request_well_formed(cmd))
		return CR250_INTERNAL_ERROR;
	if ((ec = deadline_from(p, timeout_s, &deadline)) != CR250_OK)
		return ec;
	if (p->uncertain)
		return CR250_UNCERTAIN;
	if (request_refused(cmd)) {
		a1logd(p->log, 1, "cr250: refusing '%s', outside the implemented contract\n", cmd);
		return CR250_REFUSED_COMMAND;
	}
	if ((ec = send_request(p, cmd, deadline)) != CR250_OK)
		return ec;
	ec = read_status(p, cmd, NULL, r, deadline);
	if (!p->uncertain) {
		int tail = check_idle_input(p, deadline);
		if (tail != CR250_OK)
			return tail;
	}
	return ec;
}

/* ------------------------------------------------------------------- */
/* Measurement setup writes (manual 4.2.1). The keys are the ones cr250.c
   sets: exposure mode, multiplier, sync, CMF and stored calibration selections.
   The optics declarations are never written: they tell
   the instrument what is fitted, and a host that set them would falsify the
   optical path. SM Reset (4.2.1.23) would discard the operator's setup. */

static int setting_key_allowed(const char *key) {
	static const char *keys[] = { "ExposureMode", "ExposureX", "SyncMode", "SyncFreq",
		"UserCalibMode", "CMF", "Matrix", "Match", NULL };
	int i;
	for (i = 0; keys[i] != NULL; i++)
		if (strcmp(key, keys[i]) == 0)
			return 1;
	return 0;
}

/* An inventory ID, a multiplier or a frequency: unsigned decimal, at most
   one point, no sign, exponent or spaces. */
static int setting_arg_ok(const char *arg) {
	int digits = 0, points = 0;
	if (arg == NULL || strlen(arg) > 16)
		return 0;
	for (; *arg != '\000'; arg++) {
		if (*arg >= '0' && *arg <= '9') digits++;
		else if (*arg == '.' && points++ == 0) continue;
		else return 0;
	}
	return digits > 0;
}

int cr250_imp_setting(cr250_imp *p, const char *key, const char *arg,
                      cr250_reply *r, double timeout_s) {
	char cmd[CR250_MAX_COMMAND + 1], short_form[CR250_MAX_COMMAND + 1];
	const char *contexts[4];
	unsigned int deadline;
	int ec;

	if (p == NULL || r == NULL || key == NULL)
		return CR250_INTERNAL_ERROR;
	memset(r, 0, sizeof(*r));
	if (!setting_key_allowed(key)) {
		a1logd(p->log, 1, "cr250: refusing SM %s, outside the implemented contract\n", key);
		return CR250_REFUSED_COMMAND;
	}
	if (!setting_arg_ok(arg))
		return CR250_INTERNAL_ERROR;
	if ((!strcmp(key, "Matrix") || !strcmp(key, "Match")) && strchr(arg, '.') != NULL)
		return CR250_INTERNAL_ERROR; /* Stored profile IDs are whole numbers. */
	snprintf(cmd, sizeof(cmd), "SM %s %s", key, arg);
	snprintf(short_form, sizeof(short_form), "SM %s", key);
	contexts[0] = key; contexts[1] = short_form; contexts[2] = cmd; contexts[3] = NULL;
	if (!request_well_formed(cmd))
		return CR250_INTERNAL_ERROR;
	if ((ec = deadline_from(p, timeout_s, &deadline)) != CR250_OK)
		return ec;
	if (p->uncertain)
		return CR250_UNCERTAIN;
	if ((ec = send_request(p, cmd, deadline)) != CR250_OK)
		return ec;
	ec = read_status(p, cmd, contexts, r, deadline);
	if (!p->uncertain) {
		int tail = check_idle_input(p, deadline);
		if (tail != CR250_OK)
			return tail;
	}
	return ec;
}

/* ------------------------------------------------------------------- */
/* Count-prefixed inventories (manual 4.3.2.4 to 4.3.2.11,
   4.3.2.13 to 4.3.2.16 and 4.3.2.26):
     OK:0:RC <key>:<count>
     <id>,<name>[,<type>]      count times
   The whole reply shares one deadline. Framing failures make the link
   uncertain; content that is complete but ill-shaped does not. */

static int parse_record(const char *line, cr250_record *rec, int *fields) {
	const char *c1, *c2;
	char idbuf[CR250_MAX_LINE + 1];
	long long id;

	memset(rec, 0, sizeof(*rec));
	if ((c1 = strchr(line, ',')) == NULL)
		return 0;
	if (!copy_field(idbuf, sizeof(idbuf), line, (int)(c1 - line)))
		return 0;
	if (!cr250_parse_integer(idbuf, &id) || id < 0)
		return 0;
	rec->id = id;

	if ((c2 = strchr(c1 + 1, ',')) == NULL) {
		if (!copy_field(rec->name, sizeof(rec->name), c1 + 1, (int)strlen(c1 + 1)))
			return 0;
		*fields = 2;
	} else {
		if (strchr(c2 + 1, ',') != NULL)
			return 0;					/* Four or more fields */
		if (!copy_field(rec->name, sizeof(rec->name), c1 + 1, (int)(c2 - c1 - 1)))
			return 0;
		if (!copy_field(rec->type, sizeof(rec->type), c2 + 1, (int)strlen(c2 + 1)))
			return 0;
		if (rec->type[0] == '\000')
			return 0;
		*fields = 3;
	}
	if (rec->name[0] == '\000')
		return 0;
	return 1;
}

/* Forget any partially read records */
static void clear_records(cr250_inventory *inv) {
	free(inv->rec);
	inv->rec = NULL;
	inv->count = 0;
	inv->fields = 0;
}

/* Documented id/name[/type] inventory schemas. Calibration-factor tables
   are different shapes and are deliberately not accepted here. */
static int inventory_fields(const char *key) {
	static const char *two[] = { "Aperture", "Mode", "ExposureMode", "RangeMode",
		"Range", "SyncMode", "MatrixMode", "UserCalibMode", "Matrix", "Match",
		"Speed", NULL };
	int i;
	if (strcmp(key, "Accessory") == 0 || strcmp(key, "Filter") == 0)
		return 3;
	for (i = 0; two[i] != NULL; i++)
		if (strcmp(key, two[i]) == 0)
			return 2;
	return 0;
}

int cr250_imp_inventory(cr250_imp *p, const char *key, cr250_inventory *inv, double timeout_s) {
	char cmd[CR250_MAX_COMMAND + 1];
	char line[CR250_MAX_LINE + 1];
	cr250_reply r;
	unsigned int deadline;
	long long count;
	int ec, header_ec, i, shape_ok = 1, nfields;
	size_t kl;

	if (p == NULL || inv == NULL || key == NULL)
		return CR250_INTERNAL_ERROR;
	cr250_inventory_dispose(inv);
	kl = strlen(key);
	if (kl == 0 || kl + 3 > CR250_MAX_COMMAND || kl >= sizeof(inv->key))
		return CR250_INTERNAL_ERROR;
	for (i = 0; key[i] != '\000'; i++) {
		if (!isalnum((unsigned char)key[i]))
			return CR250_INTERNAL_ERROR;
	}
	strcpy(inv->key, key);
	snprintf(cmd, sizeof(cmd), "RC %s", key);
	if ((nfields = inventory_fields(key)) == 0)
		return CR250_REFUSED_COMMAND;

	if ((ec = deadline_from(p, timeout_s, &deadline)) != CR250_OK)
		return ec;
	if (p->uncertain)
		return CR250_UNCERTAIN;
	if ((ec = send_request(p, cmd, deadline)) != CR250_OK)
		return ec;
	header_ec = ec = read_status(p, cmd, NULL, &r, deadline);
	if (ec != CR250_OK) {
		if (p->uncertain)
			return ec;
		if (!r.complete || r.is_error) {
			int tail = check_idle_input(p, deadline);
			return tail != CR250_OK ? tail : ec;
		}
		/* A warning with a usable count still declares a bounded body.
		   Drain and retain it without converting the warning into success. */
	}

	/* A count that cannot be read, or that exceeds the bound, leaves an
	   unknown number of lines in flight. */
	if (strcmp(r.value, "None") == 0
	 && (strcmp(key, "Matrix") == 0 || strcmp(key, "Match") == 0))
		count = 0; /* Documented empty form, manual 4.3.2.15-16. */
	else if (!cr250_parse_integer(r.value, &count) || count < 0 || count > CR250_MAX_RECORDS)
		return fail_uncertain(p, CR250_INVENTORY_SHAPE, "inventory count not usable");
	else if (count == 0) {
		int tail;
		if (header_ec != CR250_OK)
			return fail_uncertain(p, CR250_INVENTORY_SHAPE, "inventory warning has no usable body count");
		/* No documented inventory answers with a bare zero, so it is not
		   accepted, but no body can follow it and the link stays usable. */
		tail = check_idle_input(p, deadline);
		return tail != CR250_OK ? tail : CR250_INVENTORY_SHAPE;
	}
	if (count && (inv->rec = (cr250_record *)calloc((size_t)count, sizeof(*inv->rec))) == NULL)
		return fail_uncertain(p, CR250_INTERNAL_ERROR, "inventory allocation failed before body");

	for (i = 0; i < count; i++) {
		int fields = 0;

		if ((ec = read_line(p, line, sizeof(line), deadline)) != CR250_OK) {
			clear_records(inv);
			return fail_uncertain(p, ec, "inventory body incomplete");
		}
		a1logd(p->log, 4, "cr250: record '%s'\n", cr250_fix(line));

		/* Keep consuming the body even after a bad record, so the
		   exchange completes and the link stays usable. */
		if (!shape_ok)
			continue;
		if (!parse_record(line, &inv->rec[i], &fields)) {
			shape_ok = 0;
			continue;
		}
		if (fields != nfields) {
			shape_ok = 0;
			continue;
		}
	}

	for (i = 1; shape_ok && i < count; i++) {
		int j;
		for (j = 0; j < i; j++) {
			if (inv->rec[j].id == inv->rec[i].id)
				shape_ok = 0;
		}
	}
	if ((ec = check_idle_input(p, deadline)) != CR250_OK) {
		clear_records(inv);
		return ec;
	}
	if (!shape_ok) {
		clear_records(inv);
		return CR250_INVENTORY_SHAPE;
	}
	inv->count = (int)count;
	inv->fields = nfields;
	return header_ec;
}

/* ------------------------------------------------------------------- */
/* Spectrum (manual 4.3.1.37):
     OK:0:RM Spectrum:<start>,<end>,<step>,<count>
     <value>                   count times
   then no further command for 200 ms. As for inventories, framing failures
   make the link uncertain; content that is complete but unusable does not. */

static int parse_spectrum_header(const char *s, cr250_spectrum *sp, long long *count) {
	double v[3];
	int i;
	for (i = 0; i < 3; i++) {
		if (!cr250_parse_number(&s, &v[i]) || *s++ != ',')
			return 0;
	}
	if (!cr250_parse_integer(s, count))
		return 0;
	sp->start_nm = v[0];
	sp->end_nm = v[1];
	sp->step_nm = v[2];
	return 1;
}

int cr250_imp_spectrum(cr250_imp *p, cr250_spectrum *s, cr250_reply *r, double timeout_s) {
	static const char cmd[] = "RM Spectrum";
	char line[CR250_MAX_LINE + 1];
	unsigned int deadline, now;
	long long count = 0;
	int ec, header_ec, i, shape_ok = 1;

	if (p == NULL || s == NULL || r == NULL)
		return CR250_INTERNAL_ERROR;
	memset(s, 0, sizeof(*s));
	memset(r, 0, sizeof(*r));
	if ((ec = deadline_from(p, timeout_s, &deadline)) != CR250_OK)
		return ec;
	if (p->uncertain)
		return CR250_UNCERTAIN;
	if ((ec = send_request(p, cmd, deadline)) != CR250_OK)
		return ec;
	header_ec = ec = read_status(p, cmd, NULL, r, deadline);
	if (ec != CR250_OK && (p->uncertain || !r->complete)) return ec;
	if (ec != CR250_OK && r->is_error) {
		if (p->uncertain)
			return ec;
		shape_ok = 0;			/* An ER header has no body */
	} else if (!parse_spectrum_header(r->value, s, &count) || count < 1) {
		if (header_ec != CR250_OK)
			return fail_uncertain(p, header_ec, "spectrum warning has no usable body count");
		/* "NA" or an unreadable header: no body is known to follow. The
		   check after the pacing note turns any that arrives into an
		   uncertain link rather than a misread next reply. */
		shape_ok = 0;
		count = 0;
		ec = CR250_SPECTRUM_SHAPE;
	} else if (count > CR250_MAX_SPECTRUM_LINES) {
		/* A parseable count declares a body. Refuse an unbounded one without
		   leaving late lines to corrupt the next exchange. */
		memset(s, 0, sizeof(*s));
		return fail_uncertain(p, CR250_SPECTRUM_SHAPE, "spectrum count exceeds bound");
	}

	for (i = 0; i < count; i++) {
		const char *v = line;
		double x;
		if ((ec = read_line(p, line, sizeof(line), deadline)) != CR250_OK) {
			memset(s, 0, sizeof(*s));
			return fail_uncertain(p, ec, "spectrum body incomplete");
		}
		/* Keep consuming after a bad line, so the exchange completes. */
		if (!shape_ok
		 || !cr250_parse_number(&v, &x) || *v != '\000') {
			shape_ok = 0;
			continue;
		}
		s->value[i] = x;
	}

	/* Wait the pacing note out, then look for anything left over: an
	   uncounted line would otherwise surface as stale input on the next
	   request and be blamed on it. */
	now = p->now_ms();
	if ((int)(deadline - now) <= CR250_SPECTRUM_GUARD_MS) {
		memset(s, 0, sizeof(*s));
		return fail_uncertain(p, CR250_TIMEOUT, "no time left for the spectrum pacing note");
	}
	p->sleep_ms(CR250_SPECTRUM_GUARD_MS);
	if ((i = check_idle_input(p, deadline)) != CR250_OK) {
		memset(s, 0, sizeof(*s));
		return i;
	}
	if (!shape_ok) {
		memset(s, 0, sizeof(*s));
		return ec != CR250_OK ? ec : CR250_SPECTRUM_SHAPE;
	}
	/* Preserve the complete finite native header and bins independently of
	   whether the declared grid can form a usable wavelength axis. The driver
	   qualifies physical range, integration and publication later. */
	s->count = (int)count;
	/* Each wavelength is start + i * step with both operations rounded, as
	   the consumers compute it. The volatile product keeps a compiler from
	   contracting the expression into one fused multiply-add, whose single
	   rounding would accept or refuse a different set of endpoint grids. */
	if (count < 2 || !(s->start_nm > 0.0)
	 || !(s->end_nm > s->start_nm) || !(s->step_nm > 0.0))
		return CR250_SPECTRUM_SHAPE;
	{
		volatile double span_nm = (double)(count - 1) * s->step_nm;
		double end_nm = s->start_nm + span_nm;
		if (!cr250_finite(end_nm)
		 || fabs(end_nm - s->end_nm) > 1e-8 * (s->end_nm > 1.0 ? s->end_nm : 1.0))
			return CR250_SPECTRUM_SHAPE;
	}
	/* A later rounded point can coincide even when the first step advances.
	   Retain the complete native header and bins if its derived axis fails. */
	{
		double previous_nm = 0.0;
		for (i = 0; i < count; i++) {
			volatile double offset_nm = (double)i * s->step_nm;
			double wavelength_nm = s->start_nm + offset_nm;
			if (!(wavelength_nm > 0.0) || !cr250_finite(wavelength_nm)
			 || (i > 0 && !(wavelength_nm > previous_nm)))
				return CR250_SPECTRUM_SHAPE;
			previous_nm = wavelength_nm;
		}
	}
	return header_ec;
}

/* ------------------------------------------------------------------- */
/* Identity (manual 4.3.2.1 RC ID, 4.3.2.2 RC Model, 4.3.2.3
   RC InstrumentType from firmware 1.17, 4.3.2.12 RC Firmware). These are
   configuration reads; last-capture "RM" data is never consulted. */

/* Identity callers require code 0; preserve a warning's native code here. */
static int config_read(cr250_imp *p, const char *cmd, cr250_reply *r, double timeout_s) {
	/* Warnings refuse identification without discarding the native code. */
	return cr250_imp_command(p, cmd, r, timeout_s);
}

/* Numeric major.minor[.patch], with optional opaque '-build' suffix. */
static int parse_firmware(const char *s, cr250_identity *id) {
	int n = 0;

	id->nfw = 0;
	id->fw[0] = id->fw[1] = id->fw[2] = 0;
	for (;;) {
		long v;
		const char *e = s;
		char buf[8];

		while (isdigit((unsigned char)*e))
			e++;
		if (e == s || e - s > 4 || n >= 3)
			return 0;
		if (!copy_field(buf, sizeof(buf), s, (int)(e - s)) || !parse_long(buf, &v))
			return 0;
		id->fw[n++] = (int)v;
		if (*e == '-' && n >= 2) {
			/* Preserve a nonempty ASCII build identifier; numeric feature
			   comparisons use the version components, never a float. */
			const char *suffix = e + 1;
			if (!isalnum((unsigned char)*suffix))
				return 0;
			for (; *suffix; suffix++)
				if (!isalnum((unsigned char)*suffix) && *suffix != '-' && *suffix != '_')
					return 0;
			break;
		}
		if (*e == '\000')
			break;
		if (*e != '.')
			return 0;
		s = e + 1;
	}
	if (n < 2)
		return 0;
	id->nfw = n;
	return 1;
}

int cr250_firmware_at_least(const cr250_identity *id, int major, int minor) {
	if (id->nfw < 2)
		return 0;
	if (id->fw[0] != major)
		return id->fw[0] > major;
	return id->fw[1] >= minor;
}

int cr250_imp_identify(cr250_imp *p, double timeout_s) {
	cr250_reply r;
	cr250_identity id;
	int ec;

	if (p == NULL)
		return CR250_INTERNAL_ERROR;
	p->identified = 0;
	memset(&p->id, 0, sizeof(p->id));
	p->id.instrument_type = -1;
	memset(&id, 0, sizeof(id));
	id.instrument_type = -1;

	if ((ec = config_read(p, "RC Model", &r, timeout_s)) != CR250_OK)
		return ec;
	if (r.value[0] == '\000' || strcmp(r.value, p->expected_model) != 0) {
		a1logd(p->log, 1, "cr250: model '%s' is not '%s'\n", cr250_fix(r.value), p->expected_model);
		return CR250_UNKNOWN_MODEL;
	}
	if (!copy_field(id.model, sizeof(id.model), r.value, (int)strlen(r.value)))
		return CR250_DATA_PARSE_ERROR;

	if ((ec = config_read(p, "RC ID", &r, timeout_s)) != CR250_OK)
		return ec;
	if (r.value[0] == '\000' || strchr(r.value, ' ') != NULL)
		return CR250_DATA_PARSE_ERROR;
	if (!copy_field(id.serial, sizeof(id.serial), r.value, (int)strlen(r.value)))
		return CR250_DATA_PARSE_ERROR;

	if ((ec = config_read(p, "RC Firmware", &r, timeout_s)) != CR250_OK)
		return ec;
	if (!copy_field(id.firmware, sizeof(id.firmware), r.value, (int)strlen(r.value))
	 || !parse_firmware(r.value, &id))
		return CR250_DATA_PARSE_ERROR;

	if (cr250_firmware_at_least(&id, 1, 17)) {
		long t;
		if ((ec = config_read(p, "RC InstrumentType", &r, timeout_s)) != CR250_OK)
			return ec;
		if (!parse_long(r.value, &t) || t < 0 || t > 2)
			return CR250_DATA_PARSE_ERROR;
		if (!(p->expected_types & (1 << t))) {
			/* Manual 4.3.2.3: 0 photometer, 1 colorimeter, 2 spectroradiometer */
			a1logd(p->log, 1, "cr250: instrument type %ld is not accepted (mask 0x%x)\n",
			       t, p->expected_types);
			return CR250_UNKNOWN_MODEL;
		}
		id.instrument_type = (int)t;
	}

	p->id = id;
	p->identified = 1;
	a1logd(p->log, 2, "cr250: identified %s serial %s firmware %d.%d type %d\n",
	       id.model, id.serial, id.fw[0], id.fw[1], id.instrument_type);
	return CR250_OK;
}

/* ------------------------------------------------------------------- */
/* Measurement (manual 4.3.4.1 "M", timing guidance in 2.2.2) */

int cr250_measure_budget(double max_exposure_ms, int multiplier, double *budget_s) {
	double t;

	if (budget_s == NULL)
		return CR250_INTERNAL_ERROR;
	*budget_s = 0.0;
	if (!(max_exposure_ms > 0.0) || multiplier <= 0)	/* Also rejects NaN */
		return CR250_BAD_BUDGET_INPUT;
	t = CR250_MEAS_FACTOR * (max_exposure_ms / 1000.0) * (double)multiplier + CR250_MEAS_ALLOWANCE_S;
	if (!(t <= CR250_MAX_TIMEOUT_S))					/* Also rejects infinity and NaN */
		return CR250_BAD_BUDGET_INPUT;
	if (t < CR250_MEAS_FLOOR_S)
		t = CR250_MEAS_FLOOR_S;
	*budget_s = t;
	return CR250_OK;
}

int cr250_imp_measure(cr250_imp *p, cr250_reply *r, double allowed_s, double budget_s) {
	unsigned int deadline;
	int ec;

	if (p == NULL || r == NULL)
		return CR250_INTERNAL_ERROR;
	memset(r, 0, sizeof(*r));
	if (!(allowed_s > 0.0) || !(allowed_s <= CR250_MAX_TIMEOUT_S)
	 || !(budget_s >= CR250_MEAS_FLOOR_S) || !(budget_s <= CR250_MAX_TIMEOUT_S))
		return CR250_BAD_BUDGET_INPUT;
	if (allowed_s < budget_s)
		return CR250_BUDGET_TOO_SHORT;
	if (p->uncertain)
		return CR250_UNCERTAIN;
	if (!p->identified)
		return CR250_NOT_IDENTIFIED;
	if ((ec = deadline_from(p, allowed_s, &deadline)) != CR250_OK)
		return ec;

	/* One request, one exposure. A deadline expiry below leaves the link
	   uncertain because the instrument may still be exposing, and this
	   layer never sends a second "M" on its own. */
	if ((ec = send_request(p, "M", deadline)) != CR250_OK)
		return ec;
	ec = read_status(p, "M", NULL, r, deadline);
	if (!p->uncertain) {
		int tail = check_idle_input(p, deadline);
		if (tail != CR250_OK)
			return tail;
	}
	return ec;
}

/* ------------------------------------------------------------------- */
/* Argyll classes and text */

/* Documented "-5xx" values from manual 4.5 that describe a rejected
   request parameter. Other values are handled above, reserved, or absent
   from that table (-554 appears in the manual's 4.2.1.5 example). */
static int documented_parameter_error(int n) {
	switch (n) {
		case -505: case -506: case -507: case -508: case -509: case -510:
		case -511: case -512: case -513: case -514: case -515: case -518:
		case -519: case -521: case -522: case -552: case -553: case -555:
		case -557: case -558: case -560:
			return 1;
	}
	return 0;
}

inst_code cr250_interp_code(int ec) {
	int n;

	ec &= inst_imask;
	if (ec == CR250_OK)
		return inst_ok;

	if ((n = cr250_native_code(ec)) != 0) {
		if (n > 0)
			return inst_warning | ec;
		if (n <= -303 && n >= -306)
			return inst_misread | ec;
		if ((n <= -300 && n >= -302) || (n <= -332 && n >= -336))
			return inst_wrong_setup | ec;
		if (n == -331 || n == -556 || n == -559)
			return inst_hardware_fail | ec;
		if (n == -500)
			return inst_protocol_error | ec;
		if (documented_parameter_error(n))
			return inst_bad_parameter | ec;
		return inst_other_error | ec;		/* Reserved or undocumented native code */
	}

	switch (ec) {
		case CR250_INTERNAL_ERROR:
			return inst_internal_error | ec;
		case CR250_TIMEOUT:
		case CR250_COMS_FAIL:
		case CR250_UNCERTAIN:
			return inst_coms_fail | ec;
		case CR250_UNKNOWN_MODEL:
			return inst_unknown_model | ec;
		case CR250_DATA_PARSE_ERROR:
		case CR250_BAD_ECHO:
		case CR250_REPLY_TOO_LONG:
		case CR250_INVENTORY_SHAPE:
		case CR250_STALE_INPUT:
		case CR250_SPECTRUM_SHAPE:
			return inst_protocol_error | ec;
		case CR250_REFUSED_COMMAND:
			return inst_unsupported | ec;
		case CR250_NOT_IDENTIFIED:
			return inst_no_init | ec;
		case CR250_BUDGET_TOO_SHORT:
		case CR250_BAD_BUDGET_INPUT:
			return inst_bad_parameter | ec;
	}
	return inst_other_error | ec;
}

/* Manual 4.5 descriptions for documented native codes. */
static const char *native_text(int n) {
	switch (n) {
		case 100: return "Light intensity too low for automatic sync";
		case 101: return "Cannot sync to constant light source";
		case 102: return "Cannot find sync, max limit selected";
		case 103: return "Sync level too low for reliable sync";
		case -300: return "Invalid Sync mode";
		case -301: return "Invalid Sync period";
		case -302: return "Can not sync to light";
		case -303: return "Light intensity is fluctuating";
		case -304: return "Light intensity too low for range";
		case -305: return "Light intensity too low or unmeasurable";
		case -306: return "Light intensity too high for range";
		case -331: return "Hardware malfunction";
		case -332: return "Matrix version mismatch";
		case -333: return "Invalid matrix index";
		case -334: return "Uninitialized CIE tables";
		case -335: return "Uninitialized CMF tables";
		case -336: return "No Matrix exists for given ID";
		case -500: return "Invalid command";
		case -505: return "Duplicate Filter selection";
		case -506: return "Index doesn't select an Accessory";
		case -507: return "Index doesn't select a Filter";
		case -508: return "Index not valid for Accessory";
		case -509: case -510: case -511: return "Index not valid for Filter";
		case -512: return "Invalid Range mode";
		case -513: return "Invalid Range index";
		case -514: return "Invalid Exposure Multiplier";
		case -515: return "Index doesn't select an Aperture";
		case -518: return "Invalid Exposure Mode";
		case -519: return "Invalid Exposure value";
		case -521: return "Invalid Sync Mode";
		case -522: return "Invalid User Sync Frequency";
		case -552: return "Invalid Matrix Mode";
		case -553: return "Invalid Matrix ID";
		case -555: return "Invalid Matrix name/description";
		case -556: return "Error saving Matrix to FLASH";
		case -557: return "Invalid Match ID";
		case -558: return "Invalid Match name/description";
		case -559: return "Error saving Match to FLASH";
		case -560: return "Invalid User Calibration Mode";
	}
	return NULL;
}

const char *cr250_interp_text(int ec) {
	static char buf[96];
	int n;

	ec &= inst_imask;
	if ((n = cr250_native_code(ec)) != 0) {
		const char *t = native_text(n);
		snprintf(buf, sizeof(buf), "CR native %s %d: %s", n > 0 ? "warning" : "error", n,
		        t != NULL ? t : "reserved or undocumented code");
		return buf;
	}
	switch (ec) {
		case CR250_OK:                return "No error";
		case CR250_INTERNAL_ERROR:    return "Internal error or bad argument";
		case CR250_TIMEOUT:           return "Deadline expired; instrument state uncertain, port closed";
		case CR250_COMS_FAIL:         return "Communication failure; instrument state uncertain, port closed";
		case CR250_UNKNOWN_MODEL:     return "Instrument is not the expected model or instrument type";
		case CR250_DATA_PARSE_ERROR:  return "Reply not understood";
		case CR250_BAD_ECHO:          return "Unexpected text before the status line";
		case CR250_UNCERTAIN:         return "Link is uncertain; recovery is required";
		case CR250_REPLY_TOO_LONG:    return "Reply exceeded its bound";
		case CR250_INVENTORY_SHAPE:   return "Inventory count or record shape invalid";
		case CR250_REFUSED_COMMAND:   return "Command is outside the implemented contract";
		case CR250_STALE_INPUT:       return "Unexpected input outside a reply";
		case CR250_NOT_IDENTIFIED:    return "Identity not verified on this link";
		case CR250_BUDGET_TOO_SHORT:  return "Allowed duration is below the measurement budget";
		case CR250_BAD_BUDGET_INPUT:  return "Measurement budget input missing or out of range";
		case CR250_SPECTRUM_SHAPE:    return "Spectrum header or body not usable";
	}
	return "Unknown CR engine code";
}
