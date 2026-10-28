#ifndef CR250_FAKE_H
#define CR250_FAKE_H
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
 * The remote protocol as manual 1.36 describes it, answered from invented
 * values over an icoms that has no OS transport: identity, RC lists, RS/RM
 * setup that SM writes change, M, RM Radiometric and an RM Spectrum that
 * agrees with the native XYZ, plus the faults a test selects. One instance,
 * cr250_fake_state, shared by every user in a process. It opens no port,
 * enumerates nothing and is not part of the Jam build.
 */
#include "cr250.h"

#define CR250_FAKE_SP_N 81		/* 380-780 nm at 5 nm */

typedef struct {
	/* Scenario: set by the test after cr250_fake_reset() */
	const char *model, *type, *serial, *firmware;	/* Identity reported */
	const char *override_cmd, *override_value;		/* Up to three non-SM replies replaced; */
	const char *override2_cmd, *override2_value;	/* a value starting "ER:" is sent */
	const char *override3_cmd, *override3_value;	/* as a whole status line */
	/* RM Spectrum uses override_cmd only. */
	const char *native_cmd, *native_status;			/* A whole status line for one command */
	const char *timeout_cmd;						/* One request gets no reply */
	int timeout_measure;							/* M is never answered */
	int sm_ignored;									/* SM accepted, value kept */
	int sm_short_context;							/* SM answers "<key>", not "SM <key>" */
	double sp_level;		/* Scales spectrum, RM Radiometric and RM XYZ together */
	double sp_scale;		/* Scales only what RM Spectrum sends */
	double sp_shift_nm;		/* Shifts only the RM Spectrum header */
	double sp_line;			/* nz: one narrow line at this wavelength instead */
	const char *sp_fault;	/* NA, ER, bad, grid, tiny, extra, short, late, huge, large, cancel */
	void (*on_delete)(void);	/* Called when the icoms is deleted */

	/* Observations */
	unsigned int clock_ms;	/* Virtual clock: advances on sleeps and read timeouts */
	int writes, measures, closes, deleted, opened;
	int guard_checks;		/* Check the first request after a spectrum body */
	int guard_violations;	/* Requests sooner than 200 ms after a spectrum */
	char sent[65536];		/* "\n<cmd>\n..." for every request written */
	int sent_n;
	char sm_log[1024];		/* " SM <key> <arg>;" for every SM write the fake applied */
	double sp_values[CR250_FAKE_SP_N];	/* The spectrum, W/(sr.m^2.nm) */
	double amplitudes[3];	/* Of its three lines */
	char xyz[96], radiometric[96];		/* RM XYZ and RM Radiometric values */
} cr250_fake;

extern cr250_fake cr250_fake_state;

/* Default scenario, cleared observations, setup state and spectrum. */
void cr250_fake_reset(void);

/* Rebuild the spectrum and its RM XYZ/Radiometric after sp_level or sp_line. */
void cr250_fake_spectrum(void);

/* Serve p: port type, open, write, read, close and delete. */
void cr250_fake_attach(icoms *p);

/* The virtual clock, for cr250_imp.now_ms and sleep_ms. */
unsigned int cr250_fake_now(void);
void cr250_fake_sleep(unsigned int ms);

/* nz if cmd was sent, or a request starting with prefix was. */
int cr250_fake_sent(const char *cmd);
int cr250_fake_sent_prefix(const char *prefix);

/* The current value of a setting the driver may write, and setting it as an
 * operator would. NULL for other keys. */
char *cr250_fake_setting(const char *key);
void cr250_fake_set_setting(const char *key, const char *value);
#endif
