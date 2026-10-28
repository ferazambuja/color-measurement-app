/* Shared surface for the generic binding ladder. No instrument appears here. */
#ifndef CM_SESSION_H
#define CM_SESSION_H
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <math.h>
#include <errno.h>
#include <unistd.h>
#include <ctype.h>
#include "sa_config.h"
#include "numsup.h"
#include "cgats.h"
#include "xspect.h"
#include "insttypes.h"
#include "conv.h"
#include "icoms.h"
#include "inst.h"
#include "mode_checks.h"
#include "platform_io.h"
/* Upstream inst.c's backfilled default, not declared by inst.h. */
extern inst_code inst_get_n_a_cals(inst *, inst_cal_type *, inst_cal_type *);

#include "display_types.h"

typedef struct {
    /* The instrument travels with the binding, never in a file-static. Every step
     * above cm_bind -- a mode-dependent second round, set_disptype, calibration,
     * the read -- needs it, and if the only copy lives in this module then each of
     * those gets written here because that is where the pointer is. That is how a
     * 180-line ladder becomes a 479-line per-device helper, and it is the defect
     * that killed the previous attempt (1e80e3f). One line now; unpickable later. */
    inst *meter;
    inst_mode confirmed_mode;   /* what set_mode actually accepted */
    inst_mode caps;             /* re-read AFTER set_mode (inst.h:826-827) */
    cm_mode_checks baseline_mode_checks; /* initial state, before set_mode */
    inst2_capability caps2;
    inst3_capability caps3;
    int spectral;               /* instrument returns a spectrum at all */
    inst_opt_type trigger;      /* the trigger negotiated from caps2, not assumed */
    inst_opt_filter filter;     /* requested condition; verify after calibration */
    const char *serial;         /* driver-reported; may be NULL */
    /* CM_CORRECTION_*, as ACTUALLY APPLIED rather than as requested. set_disptype
     * "clears col_cor_mat() and col_cal_spec_set()" (inst.h:843-845) without
     * saying so, so "the caller passed a .ccmx" is not evidence the reading was
     * corrected. This is written only where the col_cor_mat/col_cal_spec_set call
     * returned inst_ok, so a reader downstream reports an applied correction only
     * when the driver accepted one. */
    int correction;
    int calibration_allowed;   /* explicit --calibrate, including read recovery */
    int calibration_retries;   /* at most one live spot/strip retry per binding */
    int calibration_setup_requested; /* operator setup may have changed placement or stimulus */
    int position_retried;      /* CM_RETRIED_*: one wrong-position retry per step */
    /* The driver was asked to keep a calibration it retained rather than ask for
     * its at-open one (cm_waives_initial_calibration); inst_ok from the option. */
    int initial_calibration_suppressed;
    cm_display_catalogue display_types;
    int display_selected;
    int refresh_rate_given;
    double refresh_rate;
    const char *controls_action, *controls_fields; /* borrowed validated CLI fields */
} cm_binding;

/* Supplied by the linked attach unit: the real one, or a substituted stub. A
 * matching basename does not authenticate a worker; the build digests it. */
inst *cm_attach(inst_code (*uicallback)(void *, inst_ui_purp), void *context);
/* Why the last cm_attach returned NULL, as a stage name, or NULL for "no reason
 * beyond the obvious". An attach can fail because nothing is at that index,
 * because a different instrument is, or because the path carries no identity at
 * all -- and those need different answers from the operator. */
const char *cm_attach_stage(void);
const char *cm_provenance(void);
const char *cm_expected_serial(void);   /* NULL when the caller pins no serial */
void cm_read_event(void *context, inst_event_type purpose);

/* Line-protocol token widths. The argument is wide because one command carries
 * the eight navigation doubles an X-Y sheet needs; the scanf formats below must
 * be kept one below each buffer. */
#define CM_ACTION_MAX    32
#define CM_ARGUMENT_MAX 256
#define CM_SCAN_ACTION  "%32s"
#define CM_SCAN_ARGUMENT "%255s"

/* A strip pass. Every field is chart geometry or chart navigation, which is the
 * caller's to compute -- upstream derives all of them in chartread.c from the
 * .ti2 layout (:1534 for the guide, :1551 for the widths) and none of them from
 * the instrument. Drivers that do not use a field ignore it: the widths are for
 * DTP20/41/41T, the names and guide for the DTP51. */
typedef struct {
    int patch_count;             /* patches in the pass, including any padding */
    const char *strip_name;      /* up to 7 chars (DTP51) */
    const char *pass_name;       /* up to 3 chars (DTP51) */
    int guide;                   /* DTP51 guide number, sent unchanged; -1 absent */
    double patch_width_mm;
    double gap_width_mm;
    double trailer_width_mm;
    double scan_tolerance;       /* < 0 leaves the driver's own tolerance alone */
} cm_strip_request;

/* An X-Y sheet. `locations` is how many fiducials the operator will sight before
 * the read: chartread.c:1102-1107 asks for three, or two for a single-pass sheet,
 * or one when that pass holds a single patch. Zero means the caller already knows
 * the geometry and supplies the vectors below.
 *
 * The vectors are NOT computed here. chartread.c:1254-1300 turns located points
 * into them with chart algebra that differs for hexagonal and rectangular layouts
 * -- a property of the chart, not of the instrument -- so the ladder publishes the
 * points it measured and takes the vectors back. Supporting a further layout is
 * then a Python change and no C at all. */
typedef struct {
    int passes;                  /* paist: passes in the sheet (letters) */
    int steps;                   /* stipa: steps in a pass (numbers) */
    int patch_count;             /* npatch: patches to read, excluding padding */
    const char *pass_name;       /* starting pass name, ' A' to 'ZZ' */
    const char *step_name;       /* starting step name, ' 1' to '99' */
    int locations;               /* fiducials to sight, 0 to 3 */
    double origin[2];            /* ox, oy */
    double pass_increment[2];    /* ax, ay */
    double pass_offset[2];       /* aax, aay -- odd-step offset for hex layouts */
    double step_increment[2];    /* px, py */
} cm_xy_request;

/* Which colorimeter correction the caller supplied, if any. A colorimeter reads
 * through three fixed filters, so its XYZ error depends on the spectrum it is
 * shown: uncorrected, the same instrument returns different XYZ for the same
 * visual colour on a CCFL and an RGB-LED display. Upstream carries two remedies
 * and gates each on its own capability bit (inst.h:362-363). */
#define CM_CORRECTION_NONE     0
#define CM_CORRECTION_MATRIX   1   /* .ccmx -> col_cor_mat, inst2_ccmx */
#define CM_CORRECTION_SPECTRAL 2   /* .ccss -> col_cal_spec_set, inst2_ccss */

/* Bound on the samples a .ccss may carry, so a malformed file cannot ask for an
 * unbounded allocation. Real sets hold a handful: upstream's own reader refuses
 * fewer than three and the shipped MacBookProRetina2016.ccss has four. */
#define CM_MAX_CAL_SAMPLES 1000

/* What the caller asked of the instrument beyond the mode itself. Every field is
 * capability-gated at bind time, and none of them names an instrument.
 * `inst_opt_filter_unknown` and `xcalstd_none` are upstream's own "not requested"
 * values (inst.h:564, insttypes.h:141).
 *
 * The correction fields are the one non-scalar entry, and deliberately so: the
 * matrix and the spectral samples are MEASUREMENTS, parsed from a CGATS .ccmx or
 * .ccss by src/color_measurement/colorimeter_correction.py. Nothing in this file
 * parses CGATS -- the samples arrive as a plain numeric file (see load_spectra)
 * because one .ccss can hold 601 bands x N samples, which exceeds the 128 KiB
 * Linux allows in a single argv element (MAX_ARG_STRLEN). */
typedef struct {
    int highres;            /* request the high-resolution spectral mode */
    int xyz_only;           /* bind without inst_mode_spectral even if advertised */
    unsigned filter;        /* inst_opt_filter_*; inst_opt_filter_unknown leaves it */
    int averages;           /* readings averaged into one; 0 leaves the driver's own */
    int xcalstd;            /* xcalstd_*; xcalstd_none leaves it */
    const char *display_catalogue_id;
    const char *display_choice_id;
    int display_type;       /* index into get_disptypesel(); < 0 leaves it */
    int correction;         /* CM_CORRECTION_*; CM_CORRECTION_NONE leaves it */
    int correction_dtech;   /* disptech enum (disptechs.h:46); 0 is disptech_unknown */
    int correction_cbid;    /* cbid; inst.h:1105 wants 1 when the file did not say */
    double matrix[3][3];    /* CM_CORRECTION_MATRIX only */
    xspect *samples;        /* CM_CORRECTION_SPECTRAL only; owned, freed by main */
    int sample_count;
    /* The operation reads. Only then may a driver's retained calibration serve:
     * a calibration on its own is the operator asking for a fresh one. */
    int reads;
    int refresh_frequency;  /* read_refrate, which does not use the trigger mode */
    int display_timing;     /* meas_delay, with a separate controlled presentation exchange */
    int inspect_only;       /* no acquisition or trigger setup */
    const char *controls_action, *controls_fields;
    int refresh_rate_given;
    double refresh_rate;    /* 0 invalidates; otherwise 5..150 Hz, inst.h:1087-1093 */
} cm_configuration;

/* A whole chart, read in one go. Only the DTP20 implements read_chart upstream
 * (dtp20.c:440), and it reads the chart it stored rather than one presented to it,
 * so there is no operator placement step and no trigger (inst.h:877-878). */
#define CM_MAX_SHEETS 64

typedef struct {
    int patch_count;             /* npatch: total values in the chart */
    int passes;                  /* pich: passes (rows) in the chart */
    int steps;                   /* sip: steps in each pass */
    int sheets[CM_MAX_SHEETS];   /* pis: passes in each sheet, zero terminated */
    int chart_id;                /* chid */
} cm_chart_request;

extern FILE *wire;

/* Wire helpers for a linked driver support adapter (native_facts.h). An adapter
 * writes each event whole: cm_event_start opens the shared envelope, the adapter
 * adds its fields with cm_json_string for text, and closes it with "}\n". */
void cm_event_start(const char *event);
void cm_json_string(const char *value);

/* Which instrument the caller selected. The name is one of the spellings
 * `inst_enum()` matches (insttypes.c); the index is a position in an enumeration
 * the caller already has, never something to search for. */
typedef struct {
    int index;
    const char *serial;
    const char *instrument;
    /* 1 permits OPENING the selected serial port and asking the device what it
     * is. Off by default and never implied: a serial path carries no identity of
     * its own, so the only way to learn one is to talk to the device. */
    int identify_serial;
    const char *path;
} cm_selection;

/* Parse the three leading selection arguments, shared by every attach unit so a
 * substituted instrument takes exactly the command line a real one takes. Returns
 * the number of arguments consumed, or -1. */
int cm_parse_selection(int argc, char **argv, cm_selection *out);

/* Supplied by the linked attach unit: consume the leading device-selection
 * arguments and return how many were taken, or -1 when the selection is
 * malformed. */
int cm_configure(int argc, char **argv);

int cm_bind(inst_mode want, inst2_capability need2, int spectral_required,
            int wants_scan_events, const cm_configuration *config, cm_binding *out);
void cm_bind_report(const cm_binding *bound);
void cm_bind_release(cm_binding *bound);
int cm_read_spot(cm_binding *bound, int positioned);
int cm_read_refresh(cm_binding *bound);
int cm_read_strip(cm_binding *bound, const cm_strip_request *request);
int cm_read_xy(cm_binding *bound, cm_xy_request *request);
int cm_read_chart(cm_binding *bound, const cm_chart_request *request);
int cm_report_saved_readings(cm_binding *bound, unsigned *stored);
int cm_calibrate(cm_binding *bound);
#endif
