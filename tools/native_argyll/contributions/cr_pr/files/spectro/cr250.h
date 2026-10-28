#ifndef CR250_H
#define CR250_H
/*
 * Argyll Color Management System
 *
 * Colorimetry Research CR family driver defines
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
 * Protocol: Remote Communication manual 1.36 (2018). See cr250.txt for the
 * implemented contract, the model policy and physical-validation limits.
 */
#ifndef SALONEINSTLIB
#include "aconfig.h"
#else
#include "sa_config.h"
#endif
#include "numsup.h"
#include "cgats.h"
#include "xspect.h"
#include "conv.h"
#include "insttypes.h"
#include "cr250_imp.h"

#ifdef __cplusplus
	extern "C" {
#endif

#define CR250_SETUP             0xff20
#define CR250_CAPTURE_MISMATCH  0xff21
#define CR250_CAPTURE_WARNING   0xff22
#define CR250_FIRMWARE          0xff23
#define CR250_SPECTRUM_MISMATCH 0xff24	/* Spectrum disagrees with the instrument's own totals */
#define CR250_SPECTRUM_UNVERIFIED 0xff25 /* This capture cannot support the inferred spectral unit */
#define CR250_MAX_SETUP         16	/* Most settings one model row may check */
#define CR250_MAX_DISPTYPES     (1 + 2 * CR250_MAX_RECORDS)

/* One setting checked around M. "RS <key>" before M must satisfy the rule
 * and "RM <key>" after M must repeat the accepted value. */
typedef struct {
	const char *key;
	const char *required;		/* Exact RS value, or NULL: any value, repeated by the capture.
								   "Filter" is special: validate declared slots against RC Filter. "Mode" also
								   accepts the only entry of a one-entry RC Mode list. */
	int inventory;				/* nz: the value must name exactly one "RC <key>" record */
	const char *record_type;	/* Required type of that record, or NULL for any */
	int optional;				/* nz: the model may lack it. RS and RM both answering
								   "ER:-500" (Invalid command) or "NA" mark it not
								   applicable, and the answer must hold for the connection */
	int driver_set;				/* nz: the driver writes the value this read needs
								   (cr250.c target()) when RS shows another */
} cr250_setting;

/* What differs between Colorimetry Research models under the shared remote
 * protocol (manual 1.36 covers the family): the exact identity, the type
 * codes accepted, the firmware floor for the commands issued, the settings
 * the model exposes, whether RM Radiometric applies, and which Argyll modes
 * the model offers. Rows exist only for implemented, exact models; variants
 * and aliases are refused by exact "RC Model" text. */
typedef struct {
	instType dtype;
	const char *model;			/* Exact "RC Model" text (manual 4.3.2.2) */
	int instrument_types;		/* Bit t: "RC InstrumentType" t (4.3.2.3) accepted from firmware 1.17 */
	int fw_major, fw_minor;		/* Floor: every command this row always issues is documented here */
	const cr250_setting *setup;	/* Settings checked around M, terminated by a NULL key */
	int radiometric_type;		/* Required "RM Radiometric" type (4.3.1.36), or -1: not read */
	int spectral;				/* nz: offers inst_mode_spectral ("RM Spectrum", 4.3.1.37) */
	int nonadaptive;			/* nz: offers inst_mode_emis_nonadaptive (Fixed exposure) */
	int matrix;					/* nz: colorimeter model may select an accessory-specific Matrix */
} cr250_model;

/* Whether the spectrum under the inferred density model and the instrument's
 * own totals for the same capture agree. Power and luminance are compared when either side is
 * bright enough. Axis and chromaticity also require the native side above
 * its floor. */
#define CR250_UNIT_NOT_CHECKED   0
#define CR250_UNIT_CONSISTENT    1		/* At least one comparison discriminated; all passed.
									   Supports an inferred model, not manufacturer-confirmed units. */
#define CR250_UNIT_INCONCLUSIVE  2		/* Both sides too dim for any comparison to discriminate;
									   requested spectral publication refused. */
#define CR250_UNIT_MISMATCH      3		/* A comparison made failed or had no finite value, or
									   the spectrum overflowed once scaled, summed or integrated;
									   read refused */

/* Bits of cr250_unit_check: the comparisons made (discriminating) and the
 * values that hold a result (computed). A value whose bit is clear was not
 * calculated or has no finite value -- a ratio to a total that is not
 * positive, the mean of a spectrum summing to zero -- and is 0, which then
 * means nothing. */
#define CR250_UNIT_POWER         0x01	/* Radiant power: power_ratio */
#define CR250_UNIT_AXIS          0x02	/* Wavelength axis: mean_nm */
#define CR250_UNIT_Y             0x04	/* Luminance: y_ratio */
#define CR250_UNIT_CHROMA        0x08	/* Chromaticity: chroma_error */
#define CR250_UNIT_SPECTRUM      0x10	/* Computed only: spectrum_w and xyz */
#define CR250_UNIT_SPECTRUM_MEAN 0x20	/* Computed only: spectrum_mean_nm */
typedef struct {
	int verdict;
	int discriminating;				/* CR250_UNIT_* comparisons made */
	int computed;					/* CR250_UNIT_* values below that hold a result */
	double spectrum_w;				/* Sum of the spectrum times its step, as sent */
	double power_ratio;				/* spectrum_w / RM Radiometric power */
	double mean_nm;					/* h c photons / power from RM Radiometric */
	double spectrum_mean_nm;		/* Power-weighted mean wavelength of the spectrum */
	double xyz[3];					/* Diagnostic CIE 1931 2 degree XYZ under the inferred density, cd/m^2 */
	double y_ratio;					/* xyz[1] / native Y */
	double chroma_error;			/* max(|dX|, |dZ|) / native X+Y+Z, compared only when
									   native Y is above the floor */
} cr250_unit_check;

typedef struct {
	char problem[192];                /* The check that refused this read, with both values */
	char value[CR250_MAX_SETUP][CR250_MAX_VALUE]; /* Accepted RS value of setup[i]; empty
	                                     when not applicable on this instrument */
	double xyz[3];                    /* Native XYZ, before optional Argyll clamping */
	double radiometric[3];            /* RM Radiometric type, W/(sr.m^2), photons/(sr.m^2.s) */
	double fixed_exposure_ms;         /* RS Exposure when ExposureMode is Fixed, else 0 */
	int exposure_x;                   /* RS ExposureX */
	double sync_hz;                   /* RS SyncFreq in Manual sync, RM SyncFreq in another
	                                     sync mode, 0 when not synchronized */
	double budget_s;                  /* M deadline: the computed measurement budget */
	cr250_record range;               /* RC Range record named by RM Range */
	cr250_record filters[3];          /* RC Filter records for each RS slot; id -1 means None */
	char active_filters[CR250_MAX_VALUE]; /* RM Filter form: active names in slot order, or None */
	char used_filters[CR250_MAX_VALUE]; /* Native RM Filter names, with no assumed ordering */
	int correction_mode;             /* 0 None, 1 Matrix, 2 Match: checked RS/RM UserCalibMode */
	cr250_record correction;         /* Checked RC Matrix/Match ID and name, id -1 for None */
	cr250_spectrum spectrum;          /* RM Spectrum as sent; count 0 when not read */
	cr250_unit_check unit;            /* Spectrum against the instrument's totals */
} cr250_capture;

typedef struct {
	INST_OBJ_BASE
	amutex lock;                      /* Command lock, held around port and state access */
	const cr250_model *model;         /* Row selected by dtype; applied at init_coms */
	cr250_imp engine;
	inst_opt_type trigger;
	inst_mode mode;                   /* Accepted by set_mode */
	int averages;                     /* inst_opt_set_averages: ExposureX to set, 0: as found */
	double refresh_hz;                /* set_refr_rate: Manual sync at this rate, 0: none */
	int presence[CR250_MAX_SETUP];    /* This connection: 0 unknown, 1 present, -1 setup[i]
	                                     not applicable (optional settings only) */
	inst_disptypesel *dtlist;         /* Argyll owns selector allocation; this instance owns its lifetime */
	int ndtlist;
	int *correction_modes;           /* Owned modes behind the current implicit selectors */
	cr250_record *corrections;       /* Owned records; one per selector */
	char matrix_accessory[CR250_MAX_VALUE]; /* Accessory for which the current Matrix list was read */
	int correction_mode;             /* Requested implicit device calibration, initially None */
	cr250_record correction;
	char correction_accessory[CR250_MAX_VALUE]; /* Matrix selection is invalid after accessory changes */
	cr250_capture capture;
	char error_message[CR250_MAX_LINE + 256];
} cr250;

/* Ownership of icom transfers on success. NULL for a dtype without a row. */
cr250 *new_cr250(icoms *icom, instType dtype);

/* For fast_ser_dev_type (inst.c): the registered type whose exact "RC Model"
 * text one reply line names, or instUnknown. The line must be exactly
 * "OK:0:RC Model:<model>\r\n" for a model with a row in cr250.c. */
instType cr250_model_reply(const char *line);

/* For fast_ser_dev_type: nz if the line is "RC Model" echoed back, as it is
 * when the instrument's echo is on (manual 4.1). The reply follows it. */
int cr250_model_echo(const char *line);

#ifdef __cplusplus
	}
#endif

#endif
