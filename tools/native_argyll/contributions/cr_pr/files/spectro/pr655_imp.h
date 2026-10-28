#ifndef PR655_IMP_H
#define PR655_IMP_H

/*
 * Argyll Color Management System
 * Photo Research PR-655/PR-670 bounded remote protocol engine.
 * Copyright 2026 Fernando Voltolini de Azambuja.
 * SPDX-License-Identifier: GPL-2.0-or-later
 * This material is licensed under the GNU GENERAL PUBLIC LICENSE Version 2
 * or later.
 * See License2.txt for licensing details.
 *
 * Original implementation from protocol facts in Photo Research manual
 * 9903-0006-65 Rev. B (2009), printed pp. 102-122. The manual is not bundled.
 * The current MATLAB driver was inspected as a behavior reference; its source
 * expression and third-party MATLAB algorithms are not copied here.
 * This code has only synthetic validation, not physical qualification.
 */

#include "numsup.h"
#include "icoms.h"
#include "inst.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Host resource limits, not instrument limits. */
#define PR655_MAX_LINE       4096
#define PR655_MAX_RESPONSE  (1024 * 1024)
#define PR655_MAX_ACCESSORIES 1024
#define PR655_MAX_SAMPLES     201
#define PR655_MAX_TEXT       (PR655_MAX_LINE + 1)
#define PR655_INBUF          (PR655_MAX_LINE + 2048)

#define PR655_OK                0x0000
#define PR655_INTERNAL_ERROR    0xff01
#define PR655_TIMEOUT           0xff02
#define PR655_COMS_FAIL         0xff03
#define PR655_UNKNOWN_MODEL     0xff04
#define PR655_DATA_PARSE_ERROR  0xff05
#define PR655_UNCERTAIN         0xff06
#define PR655_REPLY_TOO_LONG    0xff07
#define PR655_STALE_INPUT       0xff08
#define PR655_NOT_IDENTIFIED    0xff09
#define PR655_REFUSED_COMMAND   0xff0a
#define PR655_BAD_GRID          0xff0b
#define PR655_BAD_SETUP         0xff0c
#define PR655_READBACK_ERROR    0xff0d
#define PR655_NATIVE_ERROR_BASE 0x1000 /* plus absolute device error, <=4095 */

typedef enum {
	pr655_closed, pr655_opening, pr655_ready, pr655_quarantined
} pr655_state;

typedef struct {
	int count, start_nm, end_nm, step_nm;
	int pixels, first_pixel, last_pixel;
	double bandwidth_nm;
} pr655_grid;

typedef struct {
	long long primary, addon[3], aperture; /* exact optical IDs, <=2^53-1 */
	int units;                 /* D601: 0 English, 1 Metric */
	int exposure_mode, exposure_ms, gain, cycles, observer;
	int dark_mode, sync_mode, capture_mode;
	/* D601 ExposureMode/Gain/CaptureMode codes are retained, not inferred
	   from setter codes. Recognized states come from D602's text labels. */
	int adaptive_known, adaptive, sensitivity_known, extended;
	double sync_value;         /* D601 bare value, no inferred unit */
	double frequency_hz;       /* D602 explicitly labelled Hertz */
	int shutter_mode;          /* -1: SZ has no implemented readback */
	char text[15][PR655_MAX_TEXT]; /* D602 fields, preserved independently */
} pr655_setup;

typedef struct {
	long long id;
	/* One owned allocation starts at name; the other strings point inside it. */
	char *name, *type, *photometry, *radiometry;
} pr655_accessory;

typedef struct {
	int length, capacity;
	char *bytes;
} pr655_raw;

typedef struct {
	int exposure_started;
	int spectrum_complete, spectrum_parsed, xyz_complete, xyz_parsed;
	int spectrum_units, xyz_units, count;
	double peak_nm, integrated, photon_integrated;
	double spectrum[PR655_MAX_SAMPLES], xyz[3];
	pr655_raw spectrum_raw, xyz_raw;
	int backlight_before_error, backlight_after_error;
	char backlight_before_raw[256], backlight_after_raw[256];
} pr655_capture;

typedef struct {
	icoms *icom;
	pr655_state state;
	int expected_model, model, remote_entered;
	int device_error, transport_error;
	int identity_valid, setup_valid;
	char serial[PR655_MAX_TEXT], version[PR655_MAX_TEXT];
	pr655_grid grid;
	pr655_setup setup;
	int accessory_count, aperture_count;
	int inventory_counts_valid, accessory_list_valid;
	int reported_accessory_count, inventory_counts_error, accessory_list_error;
	pr655_accessory *accessories;
	pr655_raw last_raw;
	char inbuf[PR655_INBUF];
	int inlen;
	int discarded_bytes; /* input purged around open's Q, counted not parsed */
	/* Default to Argyll's monotonic msec_time/msec_sleep. Synthetic tests
	   substitute only the clock; all transport still goes through icoms. */
	unsigned int (*now)(void);
	void (*sleep)(unsigned int);
} pr655imp;

/* init is inert; open assumes the adapter already opened its selected port.
   open sends only Q, PHOTO and D111, and accepts the exact requested model.
   Input left by an earlier session is discarded before and after Q, and
   counted in discarded_bytes; PHOTO must then reply exactly.
   Every timeout is finite, positive and at most 24 hours. */
void pr655_imp_init(pr655imp *p, icoms *icom, int expected_model);
/* Initialize owners to zero before first use. dispose/clear performs no I/O.
   Copying an owner struct by value does not transfer or duplicate ownership. */
void pr655_raw_dispose(pr655_raw *raw);
int pr655_raw_copy(pr655_raw *dest, const pr655_raw *source);
void pr655_accessories_dispose(pr655_accessory *rows, int count);
int pr655_accessories_copy(pr655_accessory **dest, const pr655_accessory *rows, int count);
void pr655_capture_clear(pr655_capture *capture);
void pr655_imp_dispose(pr655imp *p);
int pr655_imp_open(pr655imp *p, double timeout_s);
int pr655_imp_identity(pr655imp *p, double timeout_s); /* D110/D114/D120 */
int pr655_imp_read_setup(pr655imp *p, double timeout_s); /* D601/602/112/116 */

/* These controls validate the entire value before a write and check one
   readback. SN accepts 1..99; SS accepts 0/1/3; SK accepts integer20..400Hz.
   Host zero sentinels and Argyll's narrower refresh range belong to adapter.
   SS is the established command spelling; the manual's SQ line is inconsistent. */
int pr655_imp_set_averages(pr655imp *p, int cycles, double timeout_s);
int pr655_imp_set_sync(pr655imp *p, int mode, double timeout_s);
int pr655_imp_set_frequency(pr655imp *p, int hz, double timeout_s);

/* One exposure: M5+D2 for spectra, M2 for XYZ only. B00 precedes acquisition;
   B100 requests100% afterward, and is not restoration of original brightness.
   Any B00 rejection is retained and prevents the exposure. A complete device
   refusal stays synchronized; it does not authorize an M command or B100.
   Partial/malformed exchanges quarantine the link. No retries or uncertain
   cleanup are sent. Complete parsed native data survives later failures.
   This engine does not decide geometry, observer, physical units or ipatch
   publication. The adapter must admit current setup before calling measure. */
int pr655_imp_measure(pr655imp *p, int spectral, pr655_capture *capture,
                     double timeout_s);

/* Q is sent only from a clean identified connection. The caller owns port
   closure. On quarantine, close sends no bytes and returns UNCERTAIN. */
int pr655_imp_close(pr655imp *p, double timeout_s);

#ifdef __cplusplus
}
#endif
#endif
