/*
 * Argyll Color Management System
 * Photo Research PR-655/PR-670 remote protocol engine.
 * Copyright 2026 Fernando Voltolini de Azambuja.
 * SPDX-License-Identifier: GPL-2.0-or-later
 * This material is licensed under the GNU GENERAL PUBLIC LICENSE Version 2
 * or later.
 * See License2.txt for licensing details.
 *
 * Original implementation from protocol facts in Photo Research manual
 * 9903-0006-65 Rev. B (2009), printed pp. 102-122. Protocol facts are
 * paraphrased; no manual example blocks or MATLAB source expression copied.
 * The existing CR engine informed the icoms and Argyll clock conventions.
 * No physical instrument qualification has been performed.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <errno.h>
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
#include "pr655_imp.h"

typedef struct {
	unsigned int start, milliseconds;
} pr655_deadline;

void pr655_raw_dispose(pr655_raw *raw) {
	if (raw == NULL) return;
	free(raw->bytes);
	memset(raw, 0, sizeof(*raw));
}

static void raw_reset(pr655_raw *raw) {
	raw->length = 0;
	if (raw->bytes != NULL) raw->bytes[0] = '\000';
}

static int raw_append(pr655_raw *raw, const char *bytes, int count) {
	int needed, capacity;
	char *grown;
	if (count < 0 || count > PR655_MAX_RESPONSE - raw->length) return PR655_REPLY_TOO_LONG;
	needed = raw->length + count + 1;
	if (needed > raw->capacity) {
		capacity = raw->capacity ? raw->capacity : 256;
		while (capacity < needed && capacity < PR655_MAX_RESPONSE + 1) {
			capacity = capacity > (PR655_MAX_RESPONSE + 1) / 2
			         ? PR655_MAX_RESPONSE + 1 : capacity * 2;
		}
		grown = (char *)realloc(raw->bytes, capacity);
		if (grown == NULL) return PR655_INTERNAL_ERROR;
		raw->bytes = grown; raw->capacity = capacity;
	}
	if (count) memcpy(raw->bytes + raw->length, bytes, count);
	raw->length += count; raw->bytes[raw->length] = '\000';
	return PR655_OK;
}

int pr655_raw_copy(pr655_raw *dest, const pr655_raw *source) {
	char *bytes = NULL;
	if (dest == source) return PR655_OK;
	if (source->length < 0 || source->length > PR655_MAX_RESPONSE) return PR655_INTERNAL_ERROR;
	if (source->length) {
		bytes = (char *)malloc(source->length + 1);
		if (bytes == NULL) return PR655_INTERNAL_ERROR;
		memcpy(bytes, source->bytes, source->length); bytes[source->length] = '\000';
	}
	pr655_raw_dispose(dest);
	dest->bytes = bytes; dest->length = source->length;
	dest->capacity = source->length ? source->length + 1 : 0;
	return PR655_OK;
}

static int accessory_strings(pr655_accessory *row, const char *name, const char *type,
                             const char *photometry, const char *radiometry) {
	size_t a = strlen(name) + 1, b = strlen(type) + 1;
	size_t c = strlen(photometry) + 1, d = strlen(radiometry) + 1;
	char *text = (char *)malloc(a + b + c + d);
	if (text == NULL) return PR655_INTERNAL_ERROR;
	row->name = text; row->type = text + a;
	row->photometry = text + a + b; row->radiometry = text + a + b + c;
	memcpy(row->name, name, a); memcpy(row->type, type, b);
	memcpy(row->photometry, photometry, c); memcpy(row->radiometry, radiometry, d);
	return PR655_OK;
}

void pr655_accessories_dispose(pr655_accessory *rows, int count) {
	int i;
	if (rows == NULL) return;
	for (i = 0; i < count; i++) free(rows[i].name);
	free(rows);
}

int pr655_accessories_copy(pr655_accessory **dest, const pr655_accessory *rows, int count) {
	pr655_accessory *copy = NULL;
	int i, ec;
	if (count < 0 || count > PR655_MAX_ACCESSORIES || (count && rows == NULL)) return PR655_INTERNAL_ERROR;
	if (count) {
		copy = (pr655_accessory *)calloc(count, sizeof(*copy));
		if (copy == NULL) return PR655_INTERNAL_ERROR;
		for (i = 0; i < count; i++) {
			copy[i].id = rows[i].id;
			if ((ec = accessory_strings(&copy[i], rows[i].name, rows[i].type,
			                           rows[i].photometry, rows[i].radiometry)) != PR655_OK) {
				pr655_accessories_dispose(copy, count); return ec;
			}
		}
	}
	*dest = copy;
	return PR655_OK;
}

void pr655_capture_clear(pr655_capture *capture) {
	if (capture == NULL) return;
	pr655_raw_dispose(&capture->spectrum_raw); pr655_raw_dispose(&capture->xyz_raw);
	memset(capture, 0, sizeof(*capture));
}

void pr655_imp_dispose(pr655imp *p) {
	if (p == NULL) return;
	pr655_raw_dispose(&p->last_raw);
	pr655_accessories_dispose(p->accessories, p->accessory_count);
	p->accessories = NULL; p->accessory_count = p->aperture_count = 0;
	p->inventory_counts_valid = p->accessory_list_valid = 0;
	p->reported_accessory_count = p->inventory_counts_error = p->accessory_list_error = 0;
}

static int quarantine(pr655imp *p, int code) {
	p->state = pr655_quarantined;
	return code;
}

static int deadline(pr655imp *p, pr655_deadline *d, double seconds) {
	if (!isfinite(seconds) || seconds <= 0.0 || seconds > 86400.0)
		return PR655_INTERNAL_ERROR;
	d->start = p->now();
	d->milliseconds = (unsigned int)ceil(seconds * 1000.0);
	return PR655_OK;
}

static unsigned int remaining(pr655imp *p, pr655_deadline *d) {
	unsigned int elapsed = p->now() - d->start;
	return elapsed >= d->milliseconds ? 0 : d->milliseconds - elapsed;
}

/* During initialization only, discard bytes left before or after Q. Count this
   input separately; bound the drain by both the open deadline and byte limit.
   PHOTO and every subsequent exchange still require their exact replies. */
static int purge_input(pr655imp *p, pr655_deadline *d) {
	char b[PR655_INBUF];
	int n, se;
	p->discarded_bytes += p->inlen;
	p->inlen = 0;
	do {
		if (!remaining(p, d)) return quarantine(p, PR655_TIMEOUT);
		n = 0;
		se = p->icom->read(p->icom, b, sizeof(b), &n, NULL, 1, 0.001);
		if (n < 0 || n >= (int)sizeof(b) || (se != ICOM_OK && se != ICOM_TO)) {
			p->transport_error = se;
			return quarantine(p, PR655_COMS_FAIL);
		}
		if (n > PR655_MAX_RESPONSE - p->discarded_bytes)
			return quarantine(p, PR655_REPLY_TOO_LONG);
		p->discarded_bytes += n;
	} while (n > 0);
	return remaining(p, d) ? PR655_OK : quarantine(p, PR655_TIMEOUT);
}

/* The short metadata/brightness allowance never extends the enclosing read. */
static int short_deadline(pr655imp *p, pr655_deadline *outer, pr655_deadline *d) {
	unsigned int left = remaining(p, outer);
	if (left == 0)
		return quarantine(p, PR655_TIMEOUT);
	d->start = p->now();
	d->milliseconds = left > 5000 ? 5000 : left;
	return PR655_OK;
}

static int ready(pr655imp *p) {
	if (p == NULL || p->icom == NULL || p->icom->read == NULL || p->icom->write == NULL
	 || p->now == NULL || p->sleep == NULL)
		return PR655_INTERNAL_ERROR;
	if (p->state == pr655_quarantined)
		return PR655_UNCERTAIN;
	return p->state == pr655_ready ? PR655_OK : PR655_NOT_IDENTIFIED;
}

static char *trim(char *s) {
	char *end;
	while (*s == ' ' || *s == '\t') s++;
	end = s + strlen(s);
	while (end > s && (end[-1] == ' ' || end[-1] == '\t')) *--end = '\000';
	return s;
}

/* Parse the original decimal spelling exactly, without binary64 rounding.
   Integral 5.000/5e0 are admitted; nearby fractional values cannot become IDs.
   Optical IDs retain the shared product's exact binary64 integer envelope. */
static int exact_integer(const char *s, long long *value) {
	const char *q = s;
	char digits[PR655_MAX_LINE + 1];
	int count = 0, fractional = 0, exponent = 0, exponent_sign = 1;
	int negative = 0, first = 0, last, power, i;
	long long v = 0, limit = 9007199254740991LL;
	if (*q == '-' || *q == '+') { negative = *q == '-'; q++; }
	while (*q >= '0' && *q <= '9') {
		if (count == PR655_MAX_LINE) return 0;
		digits[count++] = *q++;
	}
	if (*q == '.') {
		q++;
		while (*q >= '0' && *q <= '9') {
			if (count == PR655_MAX_LINE) return 0;
			digits[count++] = *q++; fractional++;
		}
	}
	if (count == 0) return 0;
	if (*q == 'e' || *q == 'E') {
		q++;
		if (*q == '-' || *q == '+') { exponent_sign = *q == '-' ? -1 : 1; q++; }
		if (*q < '0' || *q > '9') return 0;
		while (*q >= '0' && *q <= '9') {
			/* Saturation is sufficient: a nonzero token cannot offset such an
			   exponent with at most MAX_LINE mantissa digits. Zero stays zero. */
			if (exponent < 100000) exponent = exponent * 10 + *q - '0';
			q++;
		}
	}
	if (*q != '\000') return 0;
	while (first < count && digits[first] == '0') first++;
	if (first == count) { *value = 0; return 1; }
	last = count;
	while (last > first && digits[last - 1] == '0') last--;
	power = exponent_sign * exponent - fractional + count - last;
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

static int integer(const char *s, int *value) {
	long long v;
	if (!exact_integer(s, &v) || v < INT_MIN || v > INT_MAX) return 0;
	*value = (int)v;
	return 1;
}

static int optical_integer(const char *s, long long *value) {
	return exact_integer(s, value);
}

/* Decimal grammar excludes hex, NaN/Inf and locale punctuation. Preserve
   finite nonzero subnormals; refuse overflow and nonzero values lost to zero. */
static int decimal(const char *s, double *value) {
	const char *q = s;
	char *end;
	int digits = 0, nonzero = 0;
	double v;
	if (*q == '-' || *q == '+') q++;
	while (*q >= '0' && *q <= '9') { nonzero |= *q != '0'; q++; digits++; }
	if (*q == '.') {
		q++;
		while (*q >= '0' && *q <= '9') { nonzero |= *q != '0'; q++; digits++; }
	}
	if (digits == 0) return 0;
	if (*q == 'e' || *q == 'E') {
		q++;
		if (*q == '-' || *q == '+') q++;
		if (*q < '0' || *q > '9') return 0;
		while (*q >= '0' && *q <= '9') q++;
	}
	if (*q != '\000') return 0;
	errno = 0;
	v = strtod(s, &end);
	if ((errno != 0 && errno != ERANGE) || *end != '\000' || !isfinite(v)
	 || (v == 0.0 && nonzero)) return 0;
	*value = v;
	return 1;
}

static int fields(char *line, char **f, int maximum) {
	char *q;
	int n = 1;
	f[0] = line;
	for (q = line; *q != '\000'; q++) {
		if (*q == ',') {
			if (n == maximum) return -1;
			*q = '\000';
			f[n++] = q + 1;
		}
	}
	for (n = 0; n < maximum && f[n] != NULL; n++) f[n] = trim(f[n]);
	return n;
}

static int native_status(pr655imp *p, char **f, int n) {
	int status;
	if (n < 1 || !integer(f[0], &status))
		return quarantine(p, PR655_DATA_PARSE_ERROR);
	if (status == 0) return PR655_OK;
	if (status >= 0 || status < -4095 || n != 1)
		return quarantine(p, PR655_DATA_PARSE_ERROR);
	p->device_error = status;
	return PR655_NATIVE_ERROR_BASE - status;
}

static int valid_text(const char *s) {
	const char *q;
	if (*s == '\000' || strlen(s) >= PR655_MAX_TEXT) return 0;
	for (q = s; *q; q++)
		if ((unsigned char)*q < 32 || (unsigned char)*q > 126) return 0;
	return 1;
}

static int copy_text(char *dest, const char *s) {
	if (!valid_text(s)) return 0;
	strcpy(dest, s);
	return 1;
}

static int blank_bytes(const char *s, int count) {
	int i;
	for (i = 0; i < count; i++)
		if (s[i] != '\r' && s[i] != '\n' && s[i] != ' ' && s[i] != '\t') return 0;
	return 1;
}

/* Inspect already queued data using the established icoms short read and
   retain every byte read in the current raw record: the surplus of the reply
   just read, or input found before a request (begin_attempt).
   This is a bounded surplus/stale-input check, not an idle-gap terminator. */
static int clean_input(pr655imp *p) {
	char b[PR655_INBUF];
	int n, se, ec, total = 0;
	if (!blank_bytes(p->inbuf, p->inlen)) return quarantine(p, PR655_STALE_INPUT);
	p->inlen = 0;
	for (;;) {
		n = 0;
		se = p->icom->read(p->icom, b, sizeof(b), &n, NULL, 1, 0.001);
		if (n < 0 || n >= (int)sizeof(b) || (se != ICOM_OK && se != ICOM_TO)) {
			p->transport_error = se;
			return quarantine(p, PR655_COMS_FAIL);
		}
		if ((ec = raw_append(&p->last_raw, b, n)) != PR655_OK) return quarantine(p, ec);
		if (!blank_bytes(b, n)) return quarantine(p, PR655_STALE_INPUT);
		total += n;
		if (total > PR655_MAX_RESPONSE) return quarantine(p, PR655_REPLY_TOO_LONG);
		if (n == 0) return PR655_OK;
	}
}

static int send_command(pr655imp *p, const char *command, int cr, pr655_deadline *d) {
	char b[2];
	int i, count, se;
	unsigned int left;
	count = (int)strlen(command) + cr;
	if (count < 1 || count > 32) return PR655_REFUSED_COMMAND;
	for (i = 0; i < count; i++) {
		left = remaining(p, d);
		if (left == 0) return quarantine(p, PR655_TIMEOUT);
		b[0] = command[i] == '\000' ? '\r' : command[i];
		b[1] = '\000';
		se = p->icom->write(p->icom, b, 1, left / 1000.0);
		if (se != ICOM_OK) {
			p->transport_error = se;
			return quarantine(p, (se & ICOM_TO) ? PR655_TIMEOUT : PR655_COMS_FAIL);
		}
		left = remaining(p, d);
		if (left < 50) return quarantine(p, PR655_TIMEOUT);
		p->sleep(50);
	}
	return PR655_OK;
}

static int begin_attempt(pr655imp *p, const char *command, pr655_deadline *d, int *attempt) {
	int ec;
	if (remaining(p, d) == 0) return quarantine(p, PR655_TIMEOUT);
	/* Text read with the previous reply but not consumed is that reply's
	   surplus, already in its raw record. Input still queued belongs to this
	   attempt's record, so a stale-input refusal retains the bytes that caused
	   it. */
	if (!blank_bytes(p->inbuf, p->inlen)) return quarantine(p, PR655_STALE_INPUT);
	raw_reset(&p->last_raw);
	p->device_error = p->transport_error = 0;
	if ((ec = clean_input(p)) != PR655_OK) return ec;
	if (attempt != NULL) *attempt = 1;
	return send_command(p, command, 1, d);
}

static int begin(pr655imp *p, const char *command, pr655_deadline *d) {
	return begin_attempt(p, command, d, NULL);
}

static int line(pr655imp *p, char *out, pr655_deadline *d) {
	char b[PR655_INBUF];
	int i, n, se, ec;
	unsigned int left;
	for (;;) {
		for (i = 0; i < p->inlen; i++) {
			if (p->inbuf[i] == '\r' || p->inbuf[i] == '\n') {
				if (i > PR655_MAX_LINE) return quarantine(p, PR655_REPLY_TOO_LONG);
				memcpy(out, p->inbuf, i);
				out[i] = '\000';
				memmove(p->inbuf, p->inbuf + i + 1, p->inlen - i - 1);
				p->inlen -= i + 1;
				if (*trim(out) == '\000') { i = -1; break; }
				return PR655_OK;
			}
		}
		if (i == -1) continue;
		if (p->inlen > PR655_MAX_LINE) return quarantine(p, PR655_REPLY_TOO_LONG);
		left = remaining(p, d);
		if (left == 0) return quarantine(p, PR655_TIMEOUT);
		n = 0;
		se = p->icom->read(p->icom, b, sizeof(b) - p->inlen, &n, "\r\n", 1,
		                   left / 1000.0);
		if (n < 0 || n >= (int)sizeof(b) - p->inlen) return quarantine(p, PR655_COMS_FAIL);
		if (se != ICOM_OK && se != ICOM_TO) {
			p->transport_error = se;
			return quarantine(p, PR655_COMS_FAIL);
		}
		if ((ec = raw_append(&p->last_raw, b, n)) != PR655_OK) return quarantine(p, ec);
		for (i = 0; i < n; i++) {
			unsigned char c = (unsigned char)b[i];
			if (c != '\r' && c != '\n' && c != '\t' && (c < 32 || c > 126))
				return quarantine(p, PR655_DATA_PARSE_ERROR);
		}
		memcpy(p->inbuf + p->inlen, b, n);
		p->inlen += n;
		if (n == 0 && se == ICOM_OK) p->sleep(1);
	}
}

static int first_line(pr655imp *p, const char *command, char *out, pr655_deadline *d) {
	int ec;
	if ((ec = line(p, out, d)) != PR655_OK) return ec;
	if (strcmp(trim(out), command) == 0) return line(p, out, d);
	return PR655_OK;
}

static int response(pr655imp *p, const char *command, char *out, char **f,
                    int maximum, int *count, pr655_deadline *d) {
	int ec;
	memset(f, 0, maximum * sizeof(*f));
	if ((ec = begin(p, command, d)) != PR655_OK) return ec;
	if ((ec = first_line(p, command, out, d)) != PR655_OK) return ec;
	*count = fields(out, f, maximum);
	ec = native_status(p, f, *count);
	if (ec != PR655_OK && p->state != pr655_quarantined) {
		int tail = clean_input(p);
		if (tail != PR655_OK) return tail;
	}
	return ec;
}

void pr655_imp_init(pr655imp *p, icoms *icom, int expected_model) {
	memset(p, 0, sizeof(*p));
	p->icom = icom;
	p->expected_model = expected_model;
	p->state = pr655_closed;
	p->now = msec_time;
	p->sleep = msec_sleep;
	p->setup.shutter_mode = -1;
}

int pr655_imp_open(pr655imp *p, double timeout_s) {
	pr655_deadline d;
	char out[PR655_MAX_LINE + 1], *f[3];
	int ec, n;
	if (p == NULL || p->icom == NULL || p->icom->read == NULL || p->icom->write == NULL
	 || p->now == NULL || p->sleep == NULL
	 || (p->expected_model != 655 && p->expected_model != 670)) return PR655_INTERNAL_ERROR;
	if (p->state != pr655_closed) return PR655_UNCERTAIN;
	if ((ec = deadline(p, &d, timeout_s)) != PR655_OK) return ec;
	p->state = pr655_opening;
	p->discarded_bytes = 0;
	if ((ec = purge_input(p, &d)) != PR655_OK) return ec;
	if ((ec = send_command(p, "Q", 0, &d)) != PR655_OK) return ec;
	if (remaining(p, &d) < 500) return quarantine(p, PR655_TIMEOUT);
	p->sleep(500);
	if ((ec = purge_input(p, &d)) != PR655_OK) return ec;
	raw_reset(&p->last_raw);
	p->remote_entered = 1; /* Even a missing acknowledgement cannot prove exit. */
	if ((ec = send_command(p, "PHOTO", 0, &d)) != PR655_OK) return ec;
	if ((ec = first_line(p, "PHOTO", out, &d)) != PR655_OK) return ec;
	if (strcmp(trim(out), "REMOTE MODE") != 0) return quarantine(p, PR655_DATA_PARSE_ERROR);
	if ((ec = clean_input(p)) != PR655_OK) return ec;
	if ((ec = response(p, "D111", out, f, 3, &n, &d)) != PR655_OK) return quarantine(p, ec);
	if (n != 2) return quarantine(p, PR655_DATA_PARSE_ERROR);
	if (strcmp(f[1], p->expected_model == 655 ? "PR-655" : "PR-670") != 0)
		return quarantine(p, PR655_UNKNOWN_MODEL);
	if ((ec = clean_input(p)) != PR655_OK) return ec;
	p->model = p->expected_model;
	p->state = pr655_ready;
	return PR655_OK;
}

int pr655_imp_identity(pr655imp *p, double timeout_s) {
	pr655_deadline d;
	pr655_grid g;
	char out[PR655_MAX_LINE + 1], *f[10];
	char serial[PR655_MAX_TEXT], version[PR655_MAX_TEXT];
	int ec, n;
	if ((ec = ready(p)) != PR655_OK) return ec;
	if ((ec = deadline(p, &d, timeout_s)) != PR655_OK) return ec;
	p->identity_valid = 0;
	if ((ec = response(p, "D110", out, f, 10, &n, &d)) != PR655_OK) return ec;
	if (n != 2 || !copy_text(serial, f[1])) return quarantine(p, PR655_DATA_PARSE_ERROR);
	if ((ec = response(p, "D114", out, f, 10, &n, &d)) != PR655_OK) return ec;
	if (n != 2 || !copy_text(version, f[1])) return quarantine(p, PR655_DATA_PARSE_ERROR);
	if ((ec = response(p, "D120", out, f, 10, &n, &d)) != PR655_OK) return ec;
	memset(&g, 0, sizeof(g));
	if (n != 9 || !integer(f[1], &g.count) || !decimal(f[2], &g.bandwidth_nm)
	 || !integer(f[3], &g.start_nm) || !integer(f[4], &g.end_nm)
	 || !integer(f[5], &g.step_nm) || !integer(f[6], &g.pixels)
	 || !integer(f[7], &g.first_pixel) || !integer(f[8], &g.last_pixel))
		return quarantine(p, PR655_DATA_PARSE_ERROR);
	if (g.count != (p->model == 655 ? 101 : 201) || g.start_nm != 380
	 || g.end_nm != 780 || g.step_nm != (p->model == 655 ? 4 : 2)
	 || g.pixels != (p->model == 655 ? 128 : 256) || g.bandwidth_nm < 0.0
	 || g.first_pixel < 0 || g.last_pixel < g.first_pixel || g.last_pixel >= g.pixels)
		return quarantine(p, PR655_BAD_GRID);
	if ((ec = clean_input(p)) != PR655_OK) return ec;
	strcpy(p->serial, serial);
	strcpy(p->version, version);
	p->grid = g;
	p->identity_valid = 1;
	return PR655_OK;
}

static int parse_setup(pr655imp *p, char **f, int n, pr655_setup *s) {
	int v[14], i;
	long long optics[5];
	if (n != 16) return quarantine(p, PR655_BAD_SETUP);
	for (i = 0; i < 5; i++)
		if (!optical_integer(f[i + 1], &optics[i])) return quarantine(p, PR655_BAD_SETUP);
	for (i = 5; i < 14; i++)
		if (!integer(f[i + 1], &v[i])) return quarantine(p, PR655_BAD_SETUP);
	if (!decimal(f[15], &s->sync_value)) return quarantine(p, PR655_BAD_SETUP);
	if (optics[0] < 0 || optics[1] < -1 || optics[2] < -1 || optics[3] < -1 || optics[4] < 0
	 || (v[5] != 0 && v[5] != 1) || v[7] < 0
	 || v[7] > (p->model == 670 ? 30000 : 6000)
	 || (v[7] != 0 && v[7] < (p->model == 655 ? 3 : 6))
	 || v[9] < 1 || v[9] > 99
	 || (v[10] != 2 && v[10] != 10) || (v[11] != 0 && v[11] != 1)
	 || (v[12] != 0 && v[12] != 1 && v[12] != 3)
	 || s->sync_value < 0.0) return quarantine(p, PR655_BAD_SETUP);
	s->primary = optics[0];
	for (i = 0; i < 3; i++) s->addon[i] = optics[i + 1];
	s->aperture = optics[4]; s->units = v[5]; s->exposure_mode = v[6];
	s->exposure_ms = v[7]; s->gain = v[8]; s->cycles = v[9];
	s->observer = v[10]; s->dark_mode = v[11]; s->sync_mode = v[12];
	s->capture_mode = v[13]; s->shutter_mode = -1;
	return PR655_OK;
}

static int labelled_decimal(const char *s, const char *label, double *v) {
	char number[PR655_MAX_TEXT];
	const char *space = strchr(s, ' ');
	size_t n;
	if (space == NULL || strcmp(space + 1, label) != 0) return 0;
	n = (size_t)(space - s);
	if (n == 0 || n >= sizeof(number)) return 0;
	memcpy(number, s, n); number[n] = '\000';
	return decimal(number, v);
}

static int labelled_integer(const char *s, const char *label, int *v) {
	char number[PR655_MAX_TEXT];
	const char *space = strchr(s, ' ');
	size_t n;
	if (space == NULL || strcmp(space + 1, label) != 0) return 0;
	n = (size_t)(space - s);
	if (n == 0 || n >= sizeof(number)) return 0;
	memcpy(number, s, n); number[n] = '\000';
	return integer(number, v);
}

static int setup_reports(pr655imp *p, pr655_setup *s, pr655_deadline *d) {
	char out[PR655_MAX_LINE + 1], *f[17];
	int ec, n, i, reported;
	if ((ec = response(p, "D601", out, f, 17, &n, d)) != PR655_OK) return ec;
	if ((ec = parse_setup(p, f, n, s)) != PR655_OK) return ec;
	if ((ec = response(p, "D602", out, f, 17, &n, d)) != PR655_OK) return ec;
	if (n != 16) return quarantine(p, PR655_BAD_SETUP);
	for (i = 0; i < 15; i++)
		if (!copy_text(s->text[i], f[i + 1])) return quarantine(p, PR655_BAD_SETUP);
	if (!labelled_decimal(f[15], "Hertz", &s->frequency_hz) || s->frequency_hz < 0.0)
		return quarantine(p, PR655_BAD_SETUP);
	if (strcmp(f[6], s->units == 0 ? "English" : "Metric") != 0
	 || !labelled_integer(f[8], "msec", &reported) || reported != s->exposure_ms
	 || !labelled_integer(f[10], "cycles", &reported) || reported != s->cycles
	 || !labelled_integer(f[11], "deg", &reported) || reported != s->observer)
		return quarantine(p, PR655_BAD_SETUP);
	s->adaptive_known = strcmp(f[7], "Adaptive") == 0 || strcmp(f[7], "Fixed") == 0;
	s->adaptive = strcmp(f[7], "Adaptive") == 0;
	s->sensitivity_known = strcmp(f[14], "Standard Sensitivity") == 0
	                    || strcmp(f[14], "Extended Sensitivity") == 0;
	s->extended = strcmp(f[14], "Extended Sensitivity") == 0;
	if ((s->adaptive_known && s->adaptive != (s->exposure_ms == 0))
	 || (s->sensitivity_known && !s->extended && s->exposure_ms > 6000)
	 || (p->model == 655 && s->extended)) return quarantine(p, PR655_BAD_SETUP);
	/* A text report is independent of the bare numeric SyncPeriod field. */
	return clean_input(p);
}

int pr655_imp_read_setup(pr655imp *p, double timeout_s) {
	pr655_deadline d;
	pr655_setup s;
	pr655_accessory *rows = NULL;
	char out[PR655_MAX_LINE + 1], *f[7];
	int ec, n, i, j, count = 0, apertures = 0, counts_valid = 0, list_valid = 0;
	int reported_count = 0, counts_error = 0, list_error = 0;
	if ((ec = ready(p)) != PR655_OK) return ec;
	if ((ec = deadline(p, &d, timeout_s)) != PR655_OK) return ec;
	p->setup_valid = 0;
	pr655_accessories_dispose(p->accessories, p->accessory_count);
	p->accessories = NULL; p->accessory_count = p->aperture_count = 0;
	p->inventory_counts_valid = p->accessory_list_valid = 0;
	p->reported_accessory_count = p->inventory_counts_error = p->accessory_list_error = 0;
	memset(&s, 0, sizeof(s));
	if ((ec = setup_reports(p, &s, &d)) != PR655_OK) return ec;
	ec = response(p, "D112", out, f, 7, &n, &d);
	if (ec != PR655_OK) {
		if (p->state == pr655_ready && (p->device_error == -1000
		 || p->device_error == -1035 || p->device_error == -2000)) {
			counts_error = p->device_error; goto accept;
		}
		return ec;
	}
	if (n != 3 || !integer(f[1], &count) || !integer(f[2], &apertures)
	 || count < 0 || count > PR655_MAX_ACCESSORIES
	 || apertures < 0 || apertures > PR655_MAX_ACCESSORIES)
		return quarantine(p, PR655_BAD_SETUP);
	counts_valid = 1;
	reported_count = count;
	if (!count) { list_valid = 1; goto accept; }
	rows = (pr655_accessory *)calloc(count, sizeof(*rows));
	if (rows == NULL) return PR655_INTERNAL_ERROR;
	if ((ec = begin(p, "D116", &d)) != PR655_OK) goto failed;
	for (i = 0; i < count; i++) {
		ec = i == 0 ? first_line(p, "D116", out, &d) : line(p, out, &d);
		if (ec != PR655_OK) goto failed;
		memset(f, 0, sizeof(f));
		n = fields(out, f, 7);
		if ((ec = native_status(p, f, n)) != PR655_OK) {
			if (i == 0 && p->state == pr655_ready) {
				int status = p->device_error;
				if ((ec = clean_input(p)) != PR655_OK) goto failed;
				if (status == -1000 || status == -1035 || status == -2000) {
					list_error = status;
					pr655_accessories_dispose(rows, count); rows = NULL; count = 0;
					goto accept;
				}
				ec = PR655_NATIVE_ERROR_BASE - status;
			} else if (i != 0) ec = quarantine(p, ec);
			goto failed;
		}
		if (n != 6 || !optical_integer(f[1], &rows[i].id) || rows[i].id < 0
		 || !valid_text(f[2]) || !valid_text(f[3])
		 || (strcmp(f[3], "Primary") != 0 && strcmp(f[3], "Addon") != 0)
		 || !valid_text(f[4]) || !valid_text(f[5])) {
			ec = quarantine(p, PR655_BAD_SETUP); goto failed;
		}
		if ((ec = accessory_strings(&rows[i], f[2], f[3], f[4], f[5])) != PR655_OK) {
			ec = quarantine(p, ec); goto failed;
		}
		for (j = 0; j < i; j++)
			if (rows[j].id == rows[i].id) { ec = quarantine(p, PR655_BAD_SETUP); goto failed; }
	}
	if ((ec = clean_input(p)) != PR655_OK) goto failed;
	list_valid = 1;
accept:
	if ((ec = clean_input(p)) != PR655_OK) goto failed;
	p->setup = s;
	pr655_accessories_dispose(p->accessories, p->accessory_count);
	p->accessories = rows;
	p->accessory_count = count; p->aperture_count = apertures;
	p->inventory_counts_valid = counts_valid; p->accessory_list_valid = list_valid;
	p->reported_accessory_count = reported_count;
	p->inventory_counts_error = counts_error; p->accessory_list_error = list_error;
	p->setup_valid = 1;
	return PR655_OK;
failed:
	pr655_accessories_dispose(rows, count);
	return ec;
}

static int set_value(pr655imp *p, const char *prefix, long long value, pr655_deadline *d) {
	char command[32], out[PR655_MAX_LINE + 1], *f[2];
	int ec, n;
	snprintf(command, sizeof(command), "%s%lld", prefix, value);
	if ((ec = response(p, command, out, f, 2, &n, d)) != PR655_OK) return ec;
	if (n != 1) return quarantine(p, PR655_DATA_PARSE_ERROR);
	return clean_input(p);
}

static int set_readback(pr655imp *p, const char *prefix, int value, double timeout_s) {
	pr655_deadline d;
	pr655_setup s;
	int ec;
	if ((ec = ready(p)) != PR655_OK) return ec;
	if ((ec = deadline(p, &d, timeout_s)) != PR655_OK) return ec;
	p->setup_valid = 0;
	if ((ec = set_value(p, prefix, value, &d)) != PR655_OK) return ec;
	memset(&s, 0, sizeof(s));
	if ((ec = setup_reports(p, &s, &d)) != PR655_OK) return ec;
	if ((strcmp(prefix, "SN") == 0 && s.cycles != value)
	 || (strcmp(prefix, "SS") == 0 && s.sync_mode != value)
	 || (strcmp(prefix, "SK") == 0 && s.frequency_hz != (double)value))
		return quarantine(p, PR655_READBACK_ERROR);
	p->setup = s;
	/* An earlier inventory remains valid; setters cannot change accessories. */
	p->setup_valid = 1;
	return PR655_OK;
}

int pr655_imp_set_averages(pr655imp *p, int cycles, double timeout_s) {
	if (cycles < 1 || cycles > 99) return PR655_REFUSED_COMMAND;
	return set_readback(p, "SN", cycles, timeout_s);
}

int pr655_imp_set_sync(pr655imp *p, int mode, double timeout_s) {
	if (mode != 0 && mode != 1 && mode != 3) return PR655_REFUSED_COMMAND;
	return set_readback(p, "SS", mode, timeout_s);
}

int pr655_imp_set_frequency(pr655imp *p, int hz, double timeout_s) {
	if (hz < 20 || hz > 400) return PR655_REFUSED_COMMAND;
	return set_readback(p, "SK", hz, timeout_s);
}

static int backlight(pr655imp *p, int percent, pr655_deadline *d) {
	const char *prefix = "Backlight set to ";
	char command[8], out[PR655_MAX_LINE + 1], number[32], *f[2], *q, *end;
	int ec, n, reported, accepted = 0;
	snprintf(command, sizeof(command), "B%d", percent);
	if (percent == 0) strcpy(command, "B00");
	if ((ec = begin(p, command, d)) != PR655_OK) return ec;
	if ((ec = first_line(p, command, out, d)) != PR655_OK) return ec;
	q = trim(out);
	if (strncmp(q, prefix, strlen(prefix)) == 0) {
		q += strlen(prefix);
		end = q;
		while (*end >= '0' && *end <= '9') end++;
		n = (int)(end - q);
		if (n > 0 && n < (int)sizeof(number) && strcmp(end, " %") == 0) {
			memcpy(number, q, n); number[n] = '\000';
			accepted = integer(number, &reported) && reported == percent;
		}
	}
	if (!accepted) {
		memset(f, 0, sizeof(f)); n = fields(out, f, 2);
		ec = native_status(p, f, n);
		if (ec == PR655_OK) return quarantine(p, PR655_DATA_PARSE_ERROR);
		if (p->state == pr655_quarantined) return ec;
		if ((n = clean_input(p)) != PR655_OK) return n;
		return ec;
	}
	return clean_input(p);
}

static void small_raw(char *out, const pr655_raw *raw) {
	int n = raw->length > 255 ? 255 : raw->length;
	if (n) memcpy(out, raw->bytes, n);
	out[n] = '\000';
}

static int spectrum(pr655imp *p, pr655_capture *c, pr655_deadline *d) {
	char out[PR655_MAX_LINE + 1], *f[6];
	int ec, n, i, wavelength;
	if ((ec = first_line(p, "M5", out, d)) != PR655_OK) return ec;
	memset(f, 0, sizeof(f)); n = fields(out, f, 6);
	if ((ec = native_status(p, f, n)) != PR655_OK) {
		int tail = p->state == pr655_quarantined ? ec : clean_input(p);
		/* A complete standalone native error is the command's final result.
		   Incomplete or surplus input still quarantines the connection. */
		return tail == PR655_OK ? ec : tail;
	}
	if (n != 5 || !integer(f[1], &c->spectrum_units) || c->spectrum_units < 0
	 || !decimal(f[2], &c->peak_nm) || !decimal(f[3], &c->integrated)
	 || !decimal(f[4], &c->photon_integrated)) return quarantine(p, PR655_DATA_PARSE_ERROR);
	c->count = p->grid.count;
	for (i = 0; i < c->count; i++) {
		if ((ec = line(p, out, d)) != PR655_OK) return ec;
		memset(f, 0, sizeof(f)); n = fields(out, f, 6);
		if (n != 2 || !integer(f[0], &wavelength)
		 || wavelength != p->grid.start_nm + i * p->grid.step_nm
		 || !decimal(f[1], &c->spectrum[i])) return quarantine(p, PR655_DATA_PARSE_ERROR);
	}
	c->spectrum_complete = 1;
	if ((ec = clean_input(p)) != PR655_OK) return ec;
	c->spectrum_parsed = 1;
	return PR655_OK;
}

static int xyz(pr655imp *p, const char *command, pr655_capture *c, pr655_deadline *d) {
	char out[PR655_MAX_LINE + 1], *f[6];
	int ec, n, i;
	if ((ec = first_line(p, command, out, d)) != PR655_OK) return ec;
	memset(f, 0, sizeof(f)); n = fields(out, f, 6);
	if ((ec = native_status(p, f, n)) != PR655_OK) {
		int tail = p->state == pr655_quarantined ? ec : clean_input(p);
		return tail == PR655_OK ? ec : tail;
	}
	if (n != 5 || !integer(f[1], &c->xyz_units) || c->xyz_units < 0)
		return quarantine(p, PR655_DATA_PARSE_ERROR);
	for (i = 0; i < 3; i++)
		if (!decimal(f[i + 2], &c->xyz[i])) return quarantine(p, PR655_DATA_PARSE_ERROR);
	c->xyz_complete = 1;
	if ((ec = clean_input(p)) != PR655_OK) return ec;
	c->xyz_parsed = 1;
	return PR655_OK;
}

int pr655_imp_measure(pr655imp *p, int spectral, pr655_capture *c, double timeout_s) {
	pr655_deadline d, short_d;
	int ec, original_device_error;
	if ((ec = ready(p)) != PR655_OK) return ec;
	if (c == NULL || (spectral != 0 && spectral != 1)) return PR655_INTERNAL_ERROR;
	if (!p->identity_valid || !p->setup_valid) return PR655_NOT_IDENTIFIED;
	if ((ec = deadline(p, &d, timeout_s)) != PR655_OK) return ec;
	pr655_capture_clear(c);
	if ((ec = short_deadline(p, &d, &short_d)) != PR655_OK) return ec;
	c->backlight_before_error = backlight(p, 0, &short_d);
	small_raw(c->backlight_before_raw, &p->last_raw);
	if (c->backlight_before_error != PR655_OK)
		return c->backlight_before_error;
	/* The requested backlight-off prerequisite must succeed before exposure.
	   A complete device refusal keeps the link synchronized, but starts no M
	   command and sends no B100 request. */
	if ((ec = begin_attempt(p, spectral ? "M5" : "M2", &d, &c->exposure_started)) != PR655_OK) return ec;
	if (spectral) {
		ec = spectrum(p, c, &d);
		if (pr655_raw_copy(&c->spectrum_raw, &p->last_raw) != PR655_OK)
			return quarantine(p, PR655_INTERNAL_ERROR);
		if (ec != PR655_OK) goto backlight_after;
		if ((ec = short_deadline(p, &d, &short_d)) != PR655_OK) return ec;
		if ((ec = begin(p, "D2", &short_d)) != PR655_OK) return ec;
		ec = xyz(p, "D2", c, &short_d);
	} else {
		ec = xyz(p, "M2", c, &d);
	}
	if (pr655_raw_copy(&c->xyz_raw, &p->last_raw) != PR655_OK)
		return quarantine(p, PR655_INTERNAL_ERROR);
backlight_after:
	/* The manual's framed measurement/report errors are final responses.
	   Request brightness once while synchronized; preserve the original error
	   even if this cleanup fails. No cleanup follows uncertain framing. */
	if (p->state == pr655_quarantined) return ec;
	original_device_error = p->device_error;
	c->backlight_after_error = short_deadline(p, &d, &short_d);
	if (c->backlight_after_error == PR655_OK) {
		c->backlight_after_error = backlight(p, 100, &short_d);
		small_raw(c->backlight_after_raw, &p->last_raw);
	}
	if (ec != PR655_OK) {
		p->device_error = original_device_error;
		return ec;
	}
	return c->backlight_after_error;
}

int pr655_imp_close(pr655imp *p, double timeout_s) {
	pr655_deadline d;
	int ec;
	if ((ec = ready(p)) != PR655_OK) return ec;
	if ((ec = deadline(p, &d, timeout_s)) != PR655_OK) return ec;
	if ((ec = clean_input(p)) != PR655_OK) return ec;
	if ((ec = send_command(p, "Q", 0, &d)) != PR655_OK) return ec;
	p->state = pr655_closed;
	p->remote_entered = p->identity_valid = p->setup_valid = 0;
	return PR655_OK;
}
