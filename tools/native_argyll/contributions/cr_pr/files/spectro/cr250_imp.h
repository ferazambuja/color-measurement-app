#ifndef CR250_IMP_H
#define CR250_IMP_H

/*
 * Argyll Color Management System
 *
 * Colorimetry Research CR-250 remote-protocol transport and transaction
 * engine: framing, identity and bounded transactions over an icoms port.
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
 * 1.36 (2018). Section numbers in comments refer to that document. Nothing
 * here has been exercised against an instrument; every behaviour claim is a
 * reading of the manual, checked only with synthetic transcripts.
 */

/*
 * Scope of this layer:
 *
 *   - frames requests as "<command>\r\n" and accepts CR, LF or CR LF reply
 *     terminators, including a CR LF pair split across reads (manual 2.2);
 *   - parses "OK:code:context:value" and "ER:code:description:message"
 *     status lines (manual 2.2.1) and keeps the native code, context and
 *     value verbatim;
 *   - classifies native codes: 0 success, positive warning, negative error
 *     (manual 4.5);
 *   - reads count-prefixed inventories such as "RC Accessory" (manual
 *     4.3.2.4 to 4.3.2.11, 4.3.2.13 to 4.3.2.16 and 4.3.2.26)
 *     under one deadline;
 *   - verifies the exact identity its owner configured (expected_model and
 *     expected_types) from "RC Model", "RC ID", "RC Firmware" and, from
 *     firmware 1.17, "RC InstrumentType";
 *   - issues at most one "M" per request, never retries, and turns a
 *     deadline expiry or transport failure into a closed port plus an
 *     explicit "uncertain" state that needs deliberate recovery;
 *   - reads the header-counted "RM Spectrum" body (manual 4.3.1.37) and
 *     observes its 200 ms pacing note before checking for leftover input;
 *   - writes only the eight measurement-setup keys cr250.c sets ("SM", manual
 *     4.2.1): ExposureMode, ExposureX, SyncMode, SyncFreq, CMF,
 *     UserCalibMode and the stored calibration selections Matrix and Match,
 *     one allow-listed key and numeric argument at a time;
 *   - refuses the echo toggle, the reserved measurement commands, SM Reset,
 *     the optics declarations (accessory, filters, aperture), calibration
 *     data and all other computation or calibration writes.
 *
 * It does not interpret spectra or XYZ values, and it advertises no
 * measurement capability. The registered Argyll "inst" object in cr250.c
 * uses this engine and supplies the measurement contract.
 */

#include "numsup.h"
#include "icoms.h"
#include "inst.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Host bounds for the supported ASCII response shapes, not manufacturer
   maxima. Strings reserve one byte for NUL. Temporal bodies and
   calibration-factor tables are outside this layer's implemented readers. */
#define CR250_MAX_COMMAND     64	/* Longest request accepted, without terminator */
#define CR250_MAX_LINE        4096	/* Longest reply line accepted, without terminator */
#define CR250_MAX_FIELD       (CR250_MAX_LINE + 1)
#define CR250_MAX_VALUE       (CR250_MAX_LINE + 1)
#define CR250_MAX_RECORDS     256	/* Host inventory bound, not an instrument maximum */
#define CR250_MAX_RESPONSE    (1024 * 1024)
#define CR250_MAX_INV_BYTES   CR250_MAX_RESPONSE
#define CR250_MAX_PRELINES    1		/* Echo lines tolerated before a status line */
#define CR250_INBUF           (CR250_MAX_LINE + 2048)
#define CR250_MAX_SPECTRUM_LINES 10000
#define CR250_MAX_SPECTRUM_BYTES CR250_MAX_RESPONSE

/* Pacing note after "RM Spectrum" (manual 4.3.1.37): no further command for
   200 ms. cr250_imp_spectrum waits it out before its trailing-input check. */
#define CR250_SPECTRUM_GUARD_MS 200

/* Grid of a published spectrum (host policy, applied by cr250.c). The engine
   retains any finite header-counted grid whose wavelengths increase; only the
   driver's publication is bounded here. The manual's example is 380 to 780 nm
   at 2 nm (4.3.1.37). These bounds admit any ultraviolet to near infrared
   array grid, keep its points at least 0.1 nm apart so none collapse, and
   limit a 1 nm integration over the band to 900 steps. */
#define CR250_SPECTRUM_MIN_NM      200.0
#define CR250_SPECTRUM_MAX_NM      1100.0
#define CR250_SPECTRUM_MIN_STEP_NM 0.1

/* Measurement deadline policy (host policy, see cr250_measure_budget). */
#define CR250_MEAS_FLOOR_S     120.0
#define CR250_MEAS_FACTOR      3.0
#define CR250_MEAS_ALLOWANCE_S 5.0

/* Engine result codes. These occupy the instrument-specific low 16 bits of
   an inst_code. Native device codes are carried inside that space too, so a
   caller can always recover the number the instrument actually sent. */
#define CR250_OK                  0x0000
#define CR250_INTERNAL_ERROR      0xff01	/* Programming error, bad argument */
#define CR250_TIMEOUT             0xff02	/* Deadline expired; link now uncertain */
#define CR250_COMS_FAIL           0xff03	/* Transport failure; link now uncertain */
#define CR250_UNKNOWN_MODEL       0xff04	/* RC Model or InstrumentType is not expected */
#define CR250_DATA_PARSE_ERROR    0xff05	/* Reply shape not understood */
#define CR250_BAD_ECHO            0xff06	/* Unexpected text before the status line */
#define CR250_UNCERTAIN           0xff07	/* Link is in the uncertain state; recover first */
#define CR250_REPLY_TOO_LONG      0xff08	/* Line or body exceeded a bound; link now uncertain */
#define CR250_INVENTORY_SHAPE     0xff09	/* Inventory count or record shape invalid */
#define CR250_REFUSED_COMMAND     0xff0a	/* Command is outside the implemented contract */
#define CR250_STALE_INPUT         0xff0b	/* Unexpected input outside a reply */
#define CR250_NOT_IDENTIFIED      0xff0c	/* Identity not verified on this link */
#define CR250_BUDGET_TOO_SHORT    0xff0d	/* Caller allowed less than the computed budget */
#define CR250_BAD_BUDGET_INPUT    0xff0e	/* Budget input missing, non-finite, below floor or above 24 h */
#define CR250_SPECTRUM_SHAPE      0xff10	/* Spectrum shape unusable; count above the body limit closes the link */

/* Native device codes (manual 4.5). Warnings are positive, errors negative. */
#define CR250_NATIVE_WARNING_BASE 0x2000	/* + warning code, 1..0x0fff */
#define CR250_NATIVE_ERROR_BASE   0x1000	/* + (-error code), 1..0x0fff */
#define CR250_NATIVE_MAX          0x0fff

/* Decode the low 16 bits of a result. Returns the signed native code the
   instrument sent, or 0 if the result is not a native code. */
int cr250_native_code(int ec);

/* Parsed status line. Fields are copies of the instrument's text. */
typedef struct {
	int complete;					/* nz: a status line was parsed */
	int is_error;					/* nz: "ER", z: "OK" */
	long code;						/* Native code, sign as sent */
	char context[CR250_MAX_FIELD];	/* Third field: command name or error description */
	char value[CR250_MAX_VALUE];	/* Remainder of the line after the third field */
} cr250_reply;

/* Parse one status line. Returns CR250_OK or CR250_DATA_PARSE_ERROR. */
int cr250_parse_status(const char *line, cr250_reply *r);

/* nz if line is the instrument echoing cmd (manual 4.1): cmd exactly, after
   an optional '>' prompt, ending the line or followed only by its CRLF. */
int cr250_echo_line(const char *line, const char *cmd);

/* nz for a finite double. C89 has no isfinite(), and the VC++ releases
   doc/Compiling.html lists lack it; numsup.h's isFinite() also rejects zero
   and negative values. */
int cr250_finite(double x);

/* One decimal ASCII number at *src: optional sign, digits with an optional
   fraction, optional exponent. Keep finite nonzero subnormals and signed zero;
   refuse hex, NaN, infinity, overflow and nonzero values lost to zero.
   On success *src points past the number. */
int cr250_parse_number(const char **src, double *value);

/* Full original decimal token, checked exactly before binary64 conversion.
   Integral decimal/exponent spellings are accepted; magnitude is <=2^53-1.
   Callers enforce the appropriate sign, count and native-status bounds. */
int cr250_parse_integer(const char *text, long long *value);

/* One record of a count-prefixed inventory: "id,name" or "id,name,type". */
typedef struct {
	long long id;
	char name[CR250_MAX_FIELD];
	char type[CR250_MAX_FIELD];		/* Empty when the record has two fields */
} cr250_record;

typedef struct {
	char key[CR250_MAX_FIELD];		/* Inventory key, for example "Accessory" */
	int fields;						/* 2 or 3, uniform across the inventory */
	int count;						/* Records read */
	cr250_record *rec;              /* Owned allocation, count records on success */
} cr250_inventory;

/* Initialize owners to zero before first use; dispose performs no I/O.
   Inventory calls replace their output and preserve no partial parsed rows.
   Original received bytes remain independently available in last_raw. */
void cr250_inventory_dispose(cr250_inventory *inventory);

typedef struct {
	int length, capacity;
	char *bytes;                    /* Exact received bytes; length includes NUL bytes */
} cr250_raw;
void cr250_raw_dispose(cr250_raw *raw);
int cr250_raw_copy(cr250_raw *dest, const cr250_raw *source);

/* Verified identity (manual 4.3.2.1 to 4.3.2.3 and 4.3.2.12). */
typedef struct {
	char model[CR250_MAX_FIELD];	/* Exactly the expected model string */
	char serial[CR250_MAX_FIELD];	/* RC ID, non-empty */
	char firmware[CR250_MAX_FIELD]; /* Original version, including an optional build suffix */
	int fw[3];						/* Firmware components, unset components are 0 */
	int nfw;						/* Number of firmware components parsed (2 or 3) */
	int instrument_type;			/* 0 photometer, 1 colorimeter, 2 spectroradiometer,
									   -1 not queried (firmware before 1.17) */
} cr250_identity;

/* nz if the parsed firmware is at least major.minor */
int cr250_firmware_at_least(const cr250_identity *id, int major, int minor);

/* The engine. One per port. The synthetic harness inspects its state;
   cr250.c also sets identity policy and reads last_reply and uncertain. */
typedef struct _cr250_imp {
	icoms *icom;					/* Borrowed transport, one port for the life of the engine */
	a1log *log;
	const char *expected_model;		/* Exact "RC Model" text; "CR-250" until the owner sets it */
	int expected_types;				/* Bit t set: "RC InstrumentType" t is accepted from
									   firmware 1.17; 1 << 2 (spectroradiometer) until
									   the owner sets it */

	/* Time source and sleep, replaceable for deterministic tests */
	unsigned int (*now_ms)(void);
	void (*sleep_ms)(unsigned int ms);

	/* Link state */
	int uncertain;					/* nz: exchange incomplete; port closed; recovery needed */
	int identified;					/* nz: cr250_imp_identify succeeded on this link */

	/* Pending input not yet consumed as lines */
	char inbuf[CR250_INBUF];
	int inlen;
	int drop_lf;					/* nz: a CR ended the buffer; drop one following LF */

	cr250_identity id;
	cr250_reply last_reply;      /* Last parsed status, including identity/inventory failures */
	/* Owned bytes read during the latest attempted exchange, including preflight.
	   A delayed LF belongs to the attempt that reads it, even if it completes
	   the previous reply. Copy these bytes before the next attempt to retain them. */
	cr250_raw last_raw;
} cr250_imp;

/* Bind an engine to an open transport. The engine does not open the port
   and does not delete the icoms object; it may close the port on failure. */
void cr250_imp_init(cr250_imp *p, icoms *icom, a1log *log);
void cr250_imp_dispose(cr250_imp *p); /* Free owned bytes without closing the port */

/* nz if the last exchange left the instrument state unknown. */
int cr250_imp_is_uncertain(const cr250_imp *p);

/* Leave the uncertain state. The caller must have re-opened the port first.
   Identity must then be verified again before any measurement. Returns
   CR250_OK or CR250_COMS_FAIL if the port is not open. */
int cr250_imp_recover(cr250_imp *p);

/* One exact documented single-line read, within timeout_s seconds from API
   entry, including preflight, write, read and trailing-input check.
   Timeouts have millisecond resolution and must be at least 1 ms.
   Inventories, spectra, temporal bodies, calibration-factor tables, reads
   needing unimplemented pacing and every write are refused before dispatch. The reply is
   filled whenever a complete status line was parsed, including "ER" lines
   and warnings. Return value: CR250_OK for "OK:0", a native warning or
   error code for other complete replies, or an engine code. OK/warning context
   must exactly match the request; ER descriptions need not echo it.
   Strict ASCII framing refuses blank lines, embedded NUL and control bytes.
   A bounded 1 ms read checks transport-queued input before and after an
   exchange; buffered leftovers invalidate that exchange immediately. This
   cannot distinguish an arbitrarily delayed identical reply: the wire
   protocol has no transaction ID. Failure closes without flushing or retry. */
int cr250_imp_command(cr250_imp *p, const char *cmd, cr250_reply *r, double timeout_s);

/* "RC <key>" inventory: key-specific id/name[/type] schema. Accessory/Filter
   require three fields; Aperture/Mode/ExposureMode/RangeMode/Range/SyncMode/
   MatrixMode/UserCalibMode/Matrix/Match/Speed require two. Only Matrix/Match
   accept the documented empty "None" response; numeric counts are 1..256.
   A bare zero count is refused as ill-shaped but leaves the link usable.
   The entire exchange shares timeout_s from API entry. Partial/extra bodies
   never publish records. A fully consumed malformed record leaves the link
   usable. Native header diagnostics remain in p->last_reply. A warning with a
   usable count is fully drained and retained, then returned as a warning;
   an unknown or oversized count leaves the link uncertain. Dispose the owned
   output on every return, including warnings and failures. */
int cr250_imp_inventory(cr250_imp *p, const char *key, cr250_inventory *inv, double timeout_s);

/* "SM <key> <arg>" (manual 4.2.1) for the keys cr250.c sets: ExposureMode,
   ExposureX, SyncMode, SyncFreq, UserCalibMode, CMF, Matrix and Match. Others, including
   SM Reset and the optics declarations Accessory, Filter1-3 and Aperture,
   are refused before dispatch. arg is an unsigned decimal of at most 16
   characters (an inventory ID, a multiplier or a frequency); Matrix/Match
   IDs must be whole numbers. The manual's
   examples answer with either "SM <key>" or "<key>" as the OK context
   (4.2.1.1 against 4.2.1.6); both are accepted, as is the whole request.
   Otherwise as cr250_imp_command: r holds any complete status line. */
int cr250_imp_setting(cr250_imp *p, const char *key, const char *arg,
                      cr250_reply *r, double timeout_s);

/* A spectrum as the instrument sent it (manual 4.3.1.37): the header's
   start, end and step in nm, then one value per line, unscaled. */
typedef struct {
	double start_nm, end_nm, step_nm;
	int count;
	double value[CR250_MAX_SPECTRUM_LINES]; /* Original bins, independent of ipatch capacity */
} cr250_spectrum;

/* "RM Spectrum": the header "start,end,step,count" then count value lines,
   under one deadline, then the 200 ms pacing note, then a trailing-input
   check. r holds the header status line. CR250_OK needs four fields,
   2 <= count <= CR250_MAX_SPECTRUM_LINES, a finite positive non-collapsing
   grid consistent with its declared end, and every line a finite number.
   Publication limits and physical/unit qualification belong to the driver.
   A header that is not four fields (the manual's "NA", for example),
   a complete but unusable body, or an ER header return CR250_SPECTRUM_SHAPE
   or the native code and keep the link; a short, slow or oversized body, a
   warning with no usable body count or input left after the pacing note makes
   it uncertain. With 1 <= count <= CR250_MAX_SPECTRUM_LINES, a complete finite
   header and body retain their declared count and original bins even when
   count, domain, end consistency or binary64 monotonicity prevents a usable
   wavelength axis; return CR250_SPECTRUM_SHAPE.
   Malformed or incomplete numeric bodies leave count zero. A complete parsed
   warning body with a usable axis retains its bins and warning. */
int cr250_imp_spectrum(cr250_imp *p, cr250_spectrum *s, cr250_reply *r, double timeout_s);

/* Verify exact identity from configuration reads only. Each read must
   complete with code 0 within timeout_s. On success the engine is marked
   identified and p->id is filled. From firmware 1.17 the reported
   InstrumentType must be one expected_types accepts (manual 4.3.2.3). Firmware
   retains its complete version/build text separately from numeric
   components used for command gating. Failed identification or
   uncertain transport clears the previous identity; last_reply keeps the
   most recent parsed status, including a native refusal or warning. */
int cr250_imp_identify(cr250_imp *p, double timeout_s);

/* Measurement transaction budget in seconds:
   max(CR250_MEAS_FLOOR_S, CR250_MEAS_FACTOR * max_exposure_ms/1000 * multiplier
       + CR250_MEAS_ALLOWANCE_S).
   max_exposure_ms is the longest single exposure the settings allow: in Auto,
   "RC MaxExposure", which manual 4.2.1.9 establishes from firmware 1.26 as the
   per-exposure ceiling (the configured Auto limit has no RS readback); in
   Fixed, "RS Exposure". multiplier comes from "RS ExposureX". Neither is a
   guaranteed duration for a complete capture.
   The floor/factor/allowance are host policy. Returns CR250_OK,
   CR250_BAD_BUDGET_INPUT for an out-of-policy value, or
   CR250_INTERNAL_ERROR for a NULL output pointer. */
int cr250_measure_budget(double max_exposure_ms, int multiplier, double *budget_s);

/* Issue exactly one "M" (manual 4.3.4.1) and wait for its completion line.
   allowed_s is the caller's permitted duration and must be at least
   budget_s, otherwise nothing is sent. The caller must compute budget_s with
   cr250_measure_budget from checked inputs; values below the floor are refused.
   allowed_s covers the whole exchange from API entry. Requires a verified identity on
   this link. Never retries. A deadline expiry closes the port and leaves
   the link uncertain because the instrument may still be exposing. */
int cr250_imp_measure(cr250_imp *p, cr250_reply *r, double allowed_s, double budget_s);

/* Map an engine or native result to an Argyll inst_code class while
   keeping the low 16 bits. */
inst_code cr250_interp_code(int ec);

/* Human-readable text for an engine or native result. */
const char *cr250_interp_text(int ec);

#ifdef __cplusplus
}
#endif

#endif /* CR250_IMP_H */
