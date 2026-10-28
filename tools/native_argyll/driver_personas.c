/* Wholly substituted instruments, one ROW per Argyll 3.5.0 type that no
 * hand-written persona covers. Never touches hardware; nothing here talks to a
 * device, a port or a driver.
 *
 * chart_stub.c, colorimeter_stub.c and cr250_persona.c are test doubles: each
 * asserts the ORDER and ARGUMENTS the ladder must use, and each fixture there is
 * a defect the ladder once had. This table is the other half. It transcribes
 * what each remaining driver advertises, which routes its check_mode serves,
 * what it asks to calibrate at a fresh start and under which conditions, its
 * display types, and what its reading holds -- so the ladder, the frontend, the
 * published fixtures and the demo meet every constructible Argyll type, and a
 * further one costs a row. Citations are to the pinned source, per field.
 *
 * Deliberately NOT modelled: the numbers (every reading is one of the twelve
 * illustrative shapes chart_stub.c draws), a driver's refusals after a late
 * device error, transport and timing, and anything that needs a particular
 * unit's stored state (the Klein's stored calibrations, a Spyder4/5's OEM
 * spyd4cal.bin, an i1Pro's EEPROM ambient bit -- each row says which it takes).
 *
 * Spyder1 and Spyder2 have no row: their init_inst downloads firmware
 * (spyd2.c:2998-3002), which session.c refuses before init for every caller.
 * The SpectroScanT is chart_stub.c's `spectroscant`, which already models the
 * capability withdrawal only that driver performs (ss.c:414-421).
 */
#include "session.h"

/* chart_stub.c: the synthetic reading on a driver's own grid, and the name the
 * caller selected the instrument by. */
void cm_synthetic_reading(ipatch *val, int k, int emitted, double first, double last,
                          int bands);
extern const char *cm_selected_instrument;

enum { CM_REFLECTIVE, CM_TRANSMISSIVE, CM_EMISSIVE, CM_AMBIENT };
/* How a driver takes inst_opt_noinitcalib (driver_policy.h): no handler, so
 * inst.c's default refuses it; the at-open want is waived and validity still
 * decides; or the need itself is masked (ss.c:1333-1349). */
enum { CM_NO_INITIAL_OPTION, CM_WAIVES_INITIAL, CM_MASKS_NEED };

typedef struct {
    inst_cal_type needed;          /* get_n_a_cals at a fresh start */
    inst_cal_cond steps[2];        /* the conditions calibrate() asks for, in order */
} cm_startup;

typedef struct {
    const char *persona;           /* CM_CHART_PERSONA */
    const char *name;              /* the spelling inst_enum() matches */
    instType dtype;
    inst_mode cap;
    inst2_capability cap2;
    inst2_capability emissive_cap2;   /* added while the mode is emissive */
    inst3_capability cap3;
    inst_mode routes[10];          /* check_mode serves a mode holding one of these */
    cm_startup startup[4];         /* by illumination: CM_REFLECTIVE ... CM_AMBIENT */
    inst_cal_type available;       /* also offered outside ambient, never needed at start */
    int integration_time;          /* a non-adaptive emissive spot or tele sets its time */
    int keeps_calt;                /* returns inst_ok without clearing *calt */
    int displays;                  /* get_disptypesel entries; entry 0 is the default */
    unsigned refresh;              /* bit i: selection uses refresh acquisition */
    int refresh_calibration;       /* a refresh display needs its rate measured */
    double first, last;            /* spectrum grid, nm */
    int bands;                     /* 0: XYZ only */
    int spectrum_always;           /* returned whether or not the mode asks for one */
    int unset_emission;            /* emissive readings carry inst_mrt_none */
    int ambient_by_matrix;         /* every reading typed by the matrix applied */
    int fixed_filter;              /* the filter reads back and cannot be set */
    int xcalstd;                   /* accepts inst_opt_set_xcalstd */
    const char *serial;            /* what get_serial_no returns after init */
    const char *reference;         /* id[] of a white-reference condition, or NULL */
    /* A read-reported need, cleared after successful calibration by the
     * dtp92.c repair in calibration_repairs.py. */
    inst_cal_type read_need;
    int initial_option;            /* CM_NO_INITIAL_OPTION, CM_WAIVES_INITIAL or CM_MASKS_NEED */
    int default_calibration;       /* no driver assessment; inst.c backfills the default */
    unsigned catalogue_refresh;    /* 0 uses refresh; otherwise published display flags */
} cm_row;

#define CM_NONE { inst_calt_none, { inst_calc_none, inst_calc_none } }
#define CM_NO_STARTUP { CM_NONE, CM_NONE, CM_NONE, CM_NONE }
#define CM_COLORIMETER_TRIGGERS (inst2_prog_trig | inst2_user_trig)
/* spyd2.c:3504-3540: every Spyder3/4/5 bit, the model differing only in ccss. */
#define CM_SPYDER_CAP (inst_mode_emis_spot | inst_mode_emis_refresh_ovd \
                       | inst_mode_emis_norefresh_ovd | inst_mode_colorimeter \
                       | inst_mode_emis_ambient)
#define CM_SPYDER_CAP2 (CM_COLORIMETER_TRIGGERS | inst2_ccmx | inst2_get_refresh_rate \
                        | inst2_set_refresh_rate | inst2_emis_refr_meas | inst2_disptype \
                        | inst2_has_leds | inst2_ambient_mono)
/* spydX.c:1099-1127 and spydX2.c:1187-1215, identical. */
#define CM_SPYDERX_CAP (inst_mode_emis_spot | inst_mode_colorimeter | inst_mode_emis_ambient)
#define CM_SPYDERX_CAP2 (CM_COLORIMETER_TRIGGERS | inst2_ccmx | inst2_disptype \
                         | inst2_ambient_mono)
/* i1disp.c:2288-2312 for btype 1. */
#define CM_I1D2_CAP (inst_mode_emis_spot | inst_mode_colorimeter | inst_mode_emis_ambient \
                     | inst_mode_emis_refresh_ovd | inst_mode_emis_norefresh_ovd)
#define CM_I1D2_CAP2 (CM_COLORIMETER_TRIGGERS | inst2_disptype | inst2_ccmx \
                      | inst2_get_refresh_rate | inst2_set_refresh_rate \
                      | inst2_emis_refr_meas)
/* specbos.c:2275-2287: inst_mode_ambient is added for the 1201 and 1211 only,
 * and trans_spot_a carries the same bit for every model. */
#define CM_SPECBOS_CAP (inst_mode_emis_tele | inst_mode_trans_spot | inst_mode_trans_spot_a \
                        | inst_mode_colorimeter | inst_mode_spectral \
                        | inst_mode_emis_refresh_ovd | inst_mode_emis_norefresh_ovd)
#define CM_SPECBOS_CAP2 (CM_COLORIMETER_TRIGGERS | inst2_disptype | inst2_has_target)
#define CM_SPECBOS_REFRESH (inst2_emis_refr_meas | inst2_set_refresh_rate \
                            | inst2_get_refresh_rate)   /* :2296-2300, not the 1201 */
/* specbos.c:1774-1798, :1907-1914: a transmission reading needs a white. */
#define CM_SPECBOS_STARTUP { CM_NONE, \
    { inst_calt_trans_vwhite, { inst_calc_man_trans_white, inst_calc_none } }, \
    CM_NONE, CM_NONE }
/* i1pro.c:129-195 as a legacy (rev A-D) unit: no ref_uv, and cap3 is set from
 * the reflective state init leaves it in (:185-190) -- filter_none, because no
 * UV filter is fitted. Ambient needs the EEPROM bit (:162-166); this unit has it,
 * as the i1Pro 2 persona does. */
#define CM_I1PRO_EMIT (inst_mode_emis_spot | inst_mode_emis_tele | inst_mode_emis_strip \
                       | inst_mode_trans_spot | inst_mode_trans_strip \
                       | inst_mode_emis_nonadaptive | inst_mode_colorimeter \
                       | inst_mode_spectral | inst_mode_highres \
                       | inst_mode_emis_ambient | inst_mode_emis_ambient_flash)
#define CM_I1PRO_CAP2 (inst2_prog_trig | inst2_user_trig | inst2_user_switch_trig \
                       | inst2_bidi_scan | inst2_has_scan_toll | inst2_no_feedback)
/* i1pro_imp.c:1554-1659 needs these at every init (:1144-1377), asked in
 * i1pro_imp.c:2566-2620's order. With no wavelength LED (:1597-1601) nothing
 * needs the tile outside reflection, so the emissive dark is its own condition. */
#define CM_I1PRO_STARTUP_EMIT \
    { inst_calt_trans_dark | inst_calt_trans_vwhite, \
      { inst_calc_man_trans_dark, inst_calc_man_trans_white } }, \
    { inst_calt_em_dark, { inst_calc_man_em_dark, inst_calc_none } }, \
    { inst_calt_em_dark, { inst_calc_man_em_dark, inst_calc_none } }

static const cm_row rows[] = {
    { "i1_display_1", "GretagMacbeth i1 Display 1", instI1Disp1,
      /* i1disp.c:2288-2296; btype 0 lacks the i1D2 bits (:2300-2312). */
      inst_mode_emis_spot | inst_mode_colorimeter,
      CM_COLORIMETER_TRIGGERS | inst2_disptype | inst2_ccmx, 0, inst3_none,
      { inst_mode_emis_spot },                           /* i1disp.c:2322-2344 */
      /* The offset calibration writes EEPROM (i1disp.c:1284-1320) and is never
       * needed (:2005-2008); a needs-driven session never asks for it. */
      CM_NO_STARTUP, inst_calt_emis_offset, 0, 0,
      2, 0x2, 0,                        /* "l", "c" (:2364-2392); btype 0: no rate */
      0, 0, 0, 0, 0, 0, 0, 0, "", NULL },               /* serial cleared, :1644 */
    { "i1_display_2", "GretagMacbeth i1 Display 2", instI1Disp2,
      CM_I1D2_CAP, CM_I1D2_CAP2, 0, inst3_none,
      { inst_mode_emis_spot, inst_mode_emis_ambient },
      CM_NO_STARTUP, inst_calt_none, 0, 0,
      /* A refresh display needs its rate until measured (i1disp.c:2009-2016). */
      2, 0x2, 1,
      0, 0, 0, 0, 0, 0, 0, 0, "", NULL },
    { "colormunki_smile", "ColorMunki Smile", instSmile,
      /* check_unlock runs a Smile as btype 1 (i1disp.c:1517-1523), and the
       * constructor's `btype == instSmile` (:2686) never makes it 2, so it
       * advertises what an i1D2 does. Its display list is its own (:2395-2432):
       * two non-refresh entries, so only a refresh override needs a rate. */
      CM_I1D2_CAP, CM_I1D2_CAP2, 0, inst3_none,
      { inst_mode_emis_spot, inst_mode_emis_ambient },
      CM_NO_STARTUP, inst_calt_none, 0, 0,
      2, 0x0, 1,
      0, 0, 0, 0, 0, 0, 0, 0, "", NULL },
    { "huey", "GretagMacbeth Huey", instHuey,
      inst_mode_emis_spot | inst_mode_emis_ambient | inst_mode_colorimeter,
      CM_COLORIMETER_TRIGGERS | inst2_has_leds | inst2_disptype | inst2_ambient_mono
      | inst2_ccmx, 0, inst3_none,                      /* huey.c:1436-1463 */
      { inst_mode_emis_spot, inst_mode_emis_ambient },  /* huey.c:1466-1488 */
      /* No calibration: inst.c:373-397 is its get_n_a_cals. A refresh display
       * only changes how it measures (huey.c:660-674). */
      CM_NO_STARTUP, inst_calt_none, 0, 0,
      2, 0x2, 0,                                        /* "l", "c" (:1503-1531) */
      /* huey.c:1246: an emissive reading's type is inst_mrt_none. */
      0, 0, 0, 0, 1, 0, 0, 0, "1000001", NULL,       /* decimal word, :942-945 */
      .default_calibration = 1 },
    { "colorhug", "Hughski ColorHug", instColorHug,
      inst_mode_emis_spot | inst_mode_colorimeter,
      CM_COLORIMETER_TRIGGERS | inst2_has_leds | inst2_disptype | inst2_ccmx, 0,
      inst3_none,                                       /* colorhug.c:860-885 */
      { inst_mode_emis_spot },                          /* colorhug.c:888-910 */
      CM_NO_STARTUP, inst_calt_none, 0, 0,
      6, 0x0, 0,                                        /* colorhug.c:926-990 */
      0, 0, 0, 0, 0, 0, 0, 0, "1000002", NULL,       /* "%u", :513-521 */
      .default_calibration = 1 },
    { "colorhug2", "Hughski ColorHug2", instColorHug2,
      inst_mode_emis_spot | inst_mode_colorimeter,
      CM_COLORIMETER_TRIGGERS | inst2_has_leds | inst2_disptype | inst2_ccmx, 0,
      inst3_none,                     /* differs only at init (colorhug.c:613-624) */
      { inst_mode_emis_spot },
      CM_NO_STARTUP, inst_calt_none, 0, 0,
      6, 0x0, 0,
      0, 0, 0, 0, 0, 0, 0, 0, "1000003", NULL, .default_calibration = 1 },
    { "hcfr", "Colorimtre HCFR", instHCFR,
      inst_mode_emis_spot | inst_mode_colorimeter,
      CM_COLORIMETER_TRIGGERS | inst2_disptype | inst2_ccmx, 0,
      inst3_none,                                       /* hcfr.c:647-671 */
      { inst_mode_emis_spot },                          /* hcfr.c:674-694 */
      CM_NO_STARTUP, inst_calt_none, 0, 0,
      3, 0x0, 0,                                        /* hcfr.c:706-743 */
      0, 0, 0, 0, 0, 0, 0, 0, "", NULL,               /* inst.c:99-101 */
      .default_calibration = 1 },
    { "dtp92", "X-Rite DTP92", instDTP92,
      inst_mode_emis_spot | inst_mode_colorimeter,
      /* dtp92.c:1046-1076: a rate but no inst2_disptype, though it lists one. */
      CM_COLORIMETER_TRIGGERS | inst2_ccmx | inst2_get_refresh_rate
      | inst2_emis_refr_meas, 0, inst3_none,
      { inst_mode_emis_spot },                          /* dtp92.c:1079-1099 */
      /* Needed only once a read reports it (dtp92.c:617-622, 759-780).
       * Our source repair clears completed work and its need flag. */
      CM_NO_STARTUP, inst_calt_emis_ratio | inst_calt_emis_offset, 0, 0,
      1, 0x1, 0,                                        /* "c" (:1111-1130) */
      0, 0, 0, 0, 0, 0, 0, 0, "", NULL, inst_calt_emis_offset },
    { "dtp94", "X-Rite DTP94", instDTP94,
      inst_mode_emis_spot | inst_mode_colorimeter,
      CM_COLORIMETER_TRIGGERS | inst2_ccmx | inst2_disptype, 0, inst3_none,
      { inst_mode_emis_spot },
      CM_NO_STARTUP, inst_calt_emis_offset, 0, 0,     /* dtp92.c drives both */
      3, 0x2, 0,                              /* "l", "c", "g" (dtp92.c:1132-1169) */
      0, 0, 0, 0, 0, 0, 0, 0, "", NULL, inst_calt_emis_offset },
    { "spyder3", "Datacolor Spyder3", instSpyder3,
      CM_SPYDER_CAP, CM_SPYDER_CAP2, 0, inst3_none,
      { inst_mode_emis_spot, inst_mode_emis_ambient },  /* spyd2.c:3558-3579 */
      CM_NO_STARTUP, inst_calt_none, 0, 0,
      /* "nl", "rc" (spyd2.c:3627-3655); a refresh display needs its rate
       * (:3241-3260), asked for explicitly since it is deferrable (inst.h:615). */
      2, 0x2, 1,
      0, 0, 0, 0, 0, 0, 0, 0, "00100004", NULL },      /* EEPROM bytes 8-15, spyd2.c:2470-2472 */
    { "spyder4", "Datacolor Spyder4", instSpyder4,
      CM_SPYDER_CAP, CM_SPYDER_CAP2 | inst2_ccss, 0, inst3_none,   /* :3545-3547 */
      { inst_mode_emis_spot, inst_mode_emis_ambient },
      CM_NO_STARTUP, inst_calt_none, 0, 0,
      /* Without the OEM spyd4cal.bin, "nl" and "rc" only (spyd2.c:3657-3685). */
      2, 0x2, 1,
      0, 0, 0, 0, 0, 0, 0, 0, "00100005", NULL },
    { "spyder5", "Datacolor Spyder5", instSpyder5,
      CM_SPYDER_CAP, CM_SPYDER_CAP2 | inst2_ccss, 0, inst3_none,
      { inst_mode_emis_spot, inst_mode_emis_ambient },
      CM_NO_STARTUP, inst_calt_none, 0, 0,
      2, 0x2, 1,
      0, 0, 0, 0, 0, 0, 0, 0, "00100006", NULL },
    { "spyderx", "Datacolor SpyderX", instSpyderX,
      CM_SPYDERX_CAP, CM_SPYDERX_CAP2, 0, inst3_none,
      { inst_mode_emis_spot, inst_mode_emis_ambient },  /* spydX.c:1129-1152 */
      /* A black calibration at every start outside ambient (spydX.c:911-938),
       * over a dark surface (:986-989). */
      { CM_NONE, CM_NONE, { inst_calt_emis_offset, { inst_calc_man_em_dark, inst_calc_none } },
        CM_NONE },
      inst_calt_none, 0,
      /* spydX.c:990-1004 returns inst_ok leaving the offset bit in *calt. */
      1,
      4, 0x0, 0,                /* spydX.c:1166-1213; refr is dead (:1390-1401) */
      0, 0, 0, 0, 0, 0, 0, 0, "", NULL,                 /* no get_serial_no, :1467-1481 */
      /* :917-925 needs the black unless it is done AND the want is waived; a
       * restored one is done (:755, :1578-1579). */
      /* spydX.c:1181, 1190, 1199 report three refresh catalogue entries. */
      .initial_option = CM_WAIVES_INITIAL, .catalogue_refresh = 0x0e },
    { "spyderx2", "Datacolor SpyderX2", instSpyderX2,
      CM_SPYDERX_CAP, CM_SPYDERX_CAP2, 0, inst3_none,
      { inst_mode_emis_spot, inst_mode_emis_ambient },
      /* ENABLE_BLACK_CAL is undefined (spydX2.c:68): offered, never needed
       * (:999-1005), and like the SpyderX never cleared (:1064-1083). */
      CM_NO_STARTUP, inst_calt_emis_offset, 0, 1,
      5, 0x0, 0,                                        /* spydX2.c:1254-1308 */
      0, 0, 0, 0, 0, 0, 0, 0, "", NULL,                 /* :1676-1690 */
      /* spydX2.c:1269-1296 reports four refresh catalogue entries. */
      .initial_option = CM_WAIVES_INITIAL, .catalogue_refresh = 0x1e },
    { "spyder2024", "GretagMacbeth Spyder 2024", instSpyder2024,
      CM_SPYDERX_CAP, CM_SPYDERX_CAP2, 0, inst3_none,
      { inst_mode_emis_spot, inst_mode_emis_ambient },
      CM_NO_STARTUP, inst_calt_emis_offset, 0, 1,
      7, 0x0, 0,                                        /* spydX2.c:1311-1383 */
      0, 0, 0, 0, 0, 0, 0, 0, "", NULL,
      /* spydX2.c:1326-1371 reports six refresh catalogue entries. */
      .initial_option = CM_WAIVES_INITIAL, .catalogue_refresh = 0x7e },
    { "klein_k10", "Klein K-10", instKleinK10,
      inst_mode_emis_tele | inst_mode_emis_spot | inst_mode_ambient
      | inst_mode_emis_nonadaptive | inst_mode_colorimeter,   /* kleink10.c:2495-2500 */
      CM_COLORIMETER_TRIGGERS | inst2_disptype | inst2_has_target | inst2_ccmx
      | inst2_emis_refr_meas | inst2_meas_disp_update, 0, inst3_none,
      { inst_mode_emis_spot, inst_mode_emis_tele, inst_mode_emis_ambient },  /* :2522-2543 */
      CM_NO_STARTUP, inst_calt_emis_offset, 0, 0,       /* kleink10.c:2271-2286 */
      /* The factory entry only (:2561-2570): stored ones are the unit's own. */
      1, 0x0, 0,
      /* kleink10.c:1422-1425: typed by the matrix, not the mode. */
      0, 0, 0, 0, 0, 1, 0, 0, "K10000007", NULL },     /* nine characters, :444-445 */
    { "specbos_1201", "JETI specbos 1201", instSpecbos1201,
      CM_SPECBOS_CAP | inst_mode_ambient, CM_SPECBOS_CAP2, 0, inst3_average,
      { inst_mode_emis_tele, inst_mode_trans_spot, inst_mode_trans_spot_a,
        inst_mode_emis_ambient },                       /* specbos.c:2399-2404 */
      CM_SPECBOS_STARTUP, inst_calt_none, 0, 0,
      /* An empty list, and set_disptype refuses the 1201 (:2483-2493, 2545). */
      0, 0x0, 0,
      /* The range is the instrument's (:836-861); 380-780 at 1 nm is the one the
       * driver itself falls back to (:1215-1242). */
      380.0, 780.0, 401, 0, 0, 0, 0, 0, "1000008", NULL },   /* "%u", :427-441 */
    { "specbos", "JETI specbos", instSpecbos,
      /* A JETI_SB idn that is not a 1201: the 1211 (specbos.c:340-365). */
      CM_SPECBOS_CAP | inst_mode_ambient, CM_SPECBOS_CAP2 | CM_SPECBOS_REFRESH, 0,
      inst3_average,
      { inst_mode_emis_tele, inst_mode_trans_spot, inst_mode_trans_spot_a,
        inst_mode_emis_ambient },
      CM_SPECBOS_STARTUP, inst_calt_none, 0, 0,
      2, 0x2, 1,                  /* "nl", "rc" (:2442-2470), a rate (:1848-1862) */
      380.0, 780.0, 401, 0, 0, 0, 0, 0, "1000009", NULL },
    { "specbos_2501", "JETI specbos 2501", instSpecbos2501,
      CM_SPECBOS_CAP, CM_SPECBOS_CAP2 | CM_SPECBOS_REFRESH, 0, inst3_average,
      /* specbos.c:2393 tests `p->mode == 2501` for `p->model`, so the 2501's
       * ambient route is served although :2284 withholds the bit. */
      { inst_mode_emis_tele, inst_mode_trans_spot, inst_mode_trans_spot_a,
        inst_mode_emis_ambient },
      CM_SPECBOS_STARTUP, inst_calt_none, 0, 0,
      2, 0x2, 1,
      380.0, 780.0, 401, 0, 0, 0, 0, 0, "1000010", NULL },
    { "spectraval", "JETI spectraval", instSpectraval,
      CM_SPECBOS_CAP, CM_SPECBOS_CAP2 | CM_SPECBOS_REFRESH, 0, inst3_average,
      /* A 1501 or 1511: no ambient route (specbos.c:2393-2396). */
      { inst_mode_emis_tele, inst_mode_trans_spot, inst_mode_trans_spot_a },
      CM_SPECBOS_STARTUP, inst_calt_none, 0, 0,
      2, 0x2, 1,
      380.0, 780.0, 401, 0, 0, 0, 0, 0, "1000011", NULL },
    { "ex1", "Image Engineering EX1", instEX1,
      inst_mode_emis_tele | inst_mode_colorimeter | inst_mode_spectral,
      CM_COLORIMETER_TRIGGERS, 0, inst3_none,           /* ex1.c:1068-1091 */
      { inst_mode_emis_tele },                          /* ex1.c:1126-1146 */
      /* A dark calibration at every start (ex1.c:688-714, 1261), in the dark
       * (:766-769). */
      { CM_NONE, CM_NONE, { inst_calt_em_dark, { inst_calc_man_em_dark, inst_calc_none } },
        CM_NONE },
      inst_calt_none, 0, 0,
      0, 0x0, 0,
      /* 2 nm from 350 to 800 at most (ex1.c:337-347), always (:663-670). */
      350.0, 800.0, 226, 1, 0, 0, 0, 0, "1000012", NULL,
      .initial_option = CM_WAIVES_INITIAL },            /* ex1.c:695-702, 1162-1171 */
    { "smcube", "SwatchMate Cube", instSMCube,
      inst_mode_ref_spot | inst_mode_colorimeter,
      inst2_prog_trig | inst2_user_trig | inst2_user_switch_trig | inst2_disptype
      | inst2_opt_calibs, 0, inst3_none,                /* smcube.c:873-899 */
      { inst_mode_ref_spot },                           /* smcube.c:903-923 */
      /* A white at every start (smcube.c:573-611, 662-666); the light trap is
       * offered and optional (:681-706), never needed. */
      { { inst_calt_ref_white, { inst_calc_man_ref_white, inst_calc_none } },
        CM_NONE, CM_NONE, CM_NONE },
      inst_calt_ref_dark, 0, 0,
      3, 0x0, 0,                          /* matte, gloss, native (:940-977) */
      0, 0, 0, 0, 0, 0, 0, 0, "", NULL,                 /* inst.c:99-101 */
      .initial_option = CM_WAIVES_INITIAL },            /* smcube.c:580-587, 1034-1043 */
    { "dtp22", "X-Rite DTP22", instDTP22,
      inst_mode_ref_spot | inst_mode_colorimeter | inst_mode_spectral,
      inst2_prog_trig | inst2_user_trig | inst2_user_switch_trig | inst2_cal_using_switch,
      0, inst3_none,                                    /* dtp22.c:967-991 */
      { inst_mode_ref_spot },                           /* dtp22.c:1024-1045 */
      /* Needed at every open (dtp22.c:640-656, 1191), completed by clicking the
       * instrument onto its plaque (:712-771). */
      { { inst_calt_ref_white, { inst_calc_man_ref_whitek, inst_calc_none } },
        CM_NONE, CM_NONE, CM_NONE },
      inst_calt_none, 0, 0,
      0, 0x0, 0,
      400.0, 700.0, 31, 0, 0, 0, 0, 1,                  /* :597-625; xcalstd :1078-1110 */
      "", "22" },                         /* no get_serial_no; plaque "%d", :714-715 */
    { "i1pro", "GretagMacbeth i1 Pro", instI1Pro,
      CM_I1PRO_EMIT | inst_mode_ref_spot | inst_mode_ref_strip,
      CM_I1PRO_CAP2, inst2_meas_disp_update | inst2_emis_refr_meas,   /* i1pro.c:176-183 */
      inst3_filter_none,
      { inst_mode_ref_spot, inst_mode_ref_strip, inst_mode_trans_spot, inst_mode_trans_strip,
        inst_mode_emis_spot, inst_mode_emis_tele, inst_mode_emis_strip,
        inst_mode_emis_ambient, inst_mode_emis_ambient_flash },   /* i1pro.c:676-720 */
      { { inst_calt_ref_dark | inst_calt_ref_white, { inst_calc_man_ref_white, inst_calc_none } },
        CM_I1PRO_STARTUP_EMIT },
      inst_calt_none, 1, 0,
      0, 0x0, 0,
      380.0, 730.0, 36, 0, 0, 0, 1, 0,                  /* i1pro_imp.c:711-723 */
      /* "%ud" appends a literal 'd' (i1pro_imp.c:648); the tile's id is "%d"
       * of the same number (:2567-2568). */
      "1000013d", "1000013", .initial_option = CM_WAIVES_INITIAL },   /* :1596-1630 */
    { "i1monitor", "GretagMacbeth i1 Monitor", instI1Monitor,
      CM_I1PRO_EMIT,                              /* reflective is I1Pro/Pro2 only (i1pro.c:146-151) */
      CM_I1PRO_CAP2, inst2_meas_disp_update | inst2_emis_refr_meas,
      inst3_filter_none,                          /* set from its init state (:185-190) */
      { inst_mode_trans_spot, inst_mode_trans_strip, inst_mode_emis_spot,
        inst_mode_emis_tele, inst_mode_emis_strip, inst_mode_emis_ambient,
        inst_mode_emis_ambient_flash },
      { CM_NONE, CM_I1PRO_STARTUP_EMIT },
      inst_calt_none, 1, 0,
      0, 0x0, 0,
      380.0, 730.0, 36, 0, 0, 0, 1, 0,
      "1000014d", "1000014", .initial_option = CM_WAIVES_INITIAL },
    { "spectrolino", "GretagMacbeth Spectrolino", instSpectrolino,
      inst_mode_ref_spot | inst_mode_emis_spot | inst_mode_emis_tele
      | inst_mode_colorimeter | inst_mode_spectral | inst_mode_trans_spot,  /* ss.c:386-396 */
      inst2_prog_trig | inst2_user_trig | inst2_user_switch_trig, 0,
      inst3_filter_none | inst3_filter_D65 | inst3_filter_UVCut | inst3_filter_pol
      | inst3_filter_Custom,                            /* ss.c:424-429 */
      { inst_mode_ref_spot, inst_mode_emis_spot, inst_mode_emis_tele,
        inst_mode_trans_spot },                         /* ss.c:1829-1833 */
      /* Every first calibration is a white on its tile (ss.c:1326-1352,
       * 1436-1445), emission's too (:1415-1416); emulated transmission then asks
       * for its white (:1549-1554). */
      { { inst_calt_ref_white, { inst_calc_man_ref_white, inst_calc_none } },
        { inst_calt_ref_white | inst_calt_trans_vwhite,
          { inst_calc_man_ref_white, inst_calc_man_trans_white } },
        { inst_calt_ref_white, { inst_calc_man_ref_white, inst_calc_none } },
        CM_NONE },
      inst_calt_none, 0, 0,
      0, 0x0, 0,
      380.0, 730.0, 36, 0, 0, 0, 0, 0,                  /* ss.c:1289-1311 */
      "1000015", "CM-WHITE-0015",         /* real serial (:524-533); white name :1432-1435 */
      .initial_option = CM_MASKS_NEED },                /* ss.c:1885-1905 */
};

static const cm_row *row;
static int initialized, coms, mode_sets, trigger_set, display, rate_set, calibrated, timed;
static int reads_needing, read_needed;
/* A start whose driver restored a calibration still within its limits, and the
 * option as the row's driver took it. Nothing is restored for CM_MASKS_NEED:
 * ss.c keeps no file. */
static int retained_start, waived, masked;
static int step, rounds;
static inst_mode active = inst_mode_none;
static inst_meas_cond condition = inst_mrc_none;
static inst_opt_filter selected_filter = inst_opt_filter_unknown;
static double matrix_scale = 1.0;

static void require(int value) { if (!value) abort(); }

static int illumination(inst_mode m) {
    if ((m & inst_mode_illum_mask) == inst_mode_reflection) return CM_REFLECTIVE;
    if ((m & inst_mode_illum_mask) == inst_mode_transmission) return CM_TRANSMISSIVE;
    if (IMODETST(m, inst_mode_emis_ambient) || IMODETST(m, inst_mode_emis_ambient_flash))
        return CM_AMBIENT;
    return CM_EMISSIVE;
}

/* The effective refresh mode, the mode's override first and norefresh before
 * refresh because its bits contain the other's (i1disp.c:2482-2495,
 * spyd2.c:3156-3169, specbos.c:2422-2437). */
static int refresh_mode(void) {
    if (IMODETST(active, inst_mode_emis_norefresh_ovd)) return 0;
    if (IMODETST(active, inst_mode_emis_refresh_ovd)) return 1;
    return (row->refresh >> display) & 1;
}

/* A non-adaptive emissive spot or tele also chooses its integration time
 * (i1pro_imp.c:1631-1636). */
static int integration_time(void) {
    return row->integration_time && illumination(active) == CM_EMISSIVE
        && (active & (inst_mode_spot | inst_mode_tele))
        && IMODETST(active, inst_mode_emis_nonadaptive);
}

static void capabilities(inst *p, inst_mode *cap, inst2_capability *cap2,
                         inst3_capability *cap3) {
    (void)p; require(initialized);
    if (cap) *cap = row->cap;
    if (cap2) *cap2 = row->cap2
        | ((active & inst_mode_illum_mask) == inst_mode_emission ? row->emissive_cap2 : 0);
    if (cap3) *cap3 = row->cap3;
}

/* The simple test every driver opens with, then the routes its own semantic test
 * serves. A mode word can carry every bit of a route the driver refuses: the
 * specbos advertises emis_spot's two bits and serves no emissive spot. */
static inst_code check_mode(inst *p, inst_mode m) {
    (void)p; require(initialized);
    if (m & ~row->cap) return inst_unsupported;
    for (int i = 0; row->routes[i]; i++)
        if (IMODETST(m, row->routes[i])) return inst_ok;
    return inst_unsupported;
}

static inst_code set_mode(inst *p, inst_mode m) {
    inst_code result = check_mode(p, m);
    int before;
    if (result != inst_ok) return result;
    require(mode_sets < 2);   /* one round, plus one mode-dependent round */
    before = refresh_mode();
    ++mode_sets; active = m;
    if (refresh_mode() != before) rate_set = 0;
    return inst_ok;
}

static inst_code init_coms(inst *p, baud_rate br, flow_control fc, double tout) {
    (void)p; (void)br; (void)fc;
    require(!initialized && !coms && tout >= 15.0);
    coms = 1;
    return inst_ok;
}

static inst_code init_inst(inst *p) {
    (void)p; require(coms && !initialized);
    initialized = 1;
    return inst_ok;
}

static char *get_serial_no(inst *p) { (void)p; require(initialized); return (char *)row->serial; }

static inst_code options(inst *p, inst_opt_type option, ...) {
    va_list args;
    (void)p; require(initialized);
    switch (option) {
        case inst_opt_noinitcalib: {
            int losecs;
            /* After init, before the mode: where spotread -N asks (spotread.c:1538). */
            require(!mode_sets);
            va_start(args, option); losecs = va_arg(args, int); va_end(args);
            require(losecs == 0);
            if (row->initial_option == CM_WAIVES_INITIAL) waived = 1;
            else if (row->initial_option == CM_MASKS_NEED) masked = 1;
            else return inst_unsupported;
            return inst_ok;
        }
        case inst_opt_trig_prog:
            return row->cap2 & inst2_prog_trig ? (trigger_set = 1, inst_ok) : inst_unsupported;
        case inst_opt_trig_user:
            return row->cap2 & inst2_user_trig ? (trigger_set = 1, inst_ok) : inst_unsupported;
        case inst_opt_trig_user_switch:
            return row->cap2 & inst2_user_switch_trig ? (trigger_set = 1, inst_ok)
                                                      : inst_unsupported;
        case inst_opt_set_filter: {
            int selected;
            static const struct { int filter; inst3_capability bit; inst_meas_cond cond; } known[] = {
                { inst_opt_filter_none, inst3_filter_none, inst_mrc_none },
                { inst_opt_filter_D50, inst3_filter_D50, inst_mrc_D50 },
                { inst_opt_filter_D65, inst3_filter_D65, inst_mrc_D65 },
                { inst_opt_filter_UVCut, inst3_filter_UVCut, inst_mrc_uvcut },
                { inst_opt_filter_pol, inst3_filter_pol, inst_mrc_pol },
                { inst_opt_filter_Custom, inst3_filter_Custom, inst_mrc_custom },
            };
            /* i1pro.c:803-874 reads its filter back and has no setter. */
            if (row->fixed_filter) return inst_unsupported;
            va_start(args, option); selected = va_arg(args, int); va_end(args);
            for (size_t i = 0; i < sizeof(known) / sizeof(known[0]); i++)
                if (known[i].filter == selected && (row->cap3 & known[i].bit)) {
                    /* ss.c:1908-1938 records the choice; this operator then fits
                     * the filter the calibration checks for (:1470-1490). */
                    selected_filter = (inst_opt_filter)selected;
                    condition = known[i].cond;
                    return inst_ok;
                }
            return inst_unsupported;
        }
        case inst_stat_get_filter: {
            inst_opt_filter *out;
            if (!row->fixed_filter && selected_filter == inst_opt_filter_unknown)
                return inst_unsupported;
            va_start(args, option); out = va_arg(args, inst_opt_filter *); va_end(args);
            *out = row->fixed_filter ? inst_opt_filter_none : selected_filter;
            return inst_ok;
        }
        case inst_opt_set_averages:
            return row->cap3 & inst3_average ? inst_ok : inst_unsupported;
        case inst_opt_set_ccss_obs:
            return row->cap2 & inst2_ccss ? inst_ok : inst_unsupported;
        case inst_opt_scan_toll:
            return row->cap2 & inst2_has_scan_toll ? inst_ok : inst_unsupported;
        case inst_opt_set_xcalstd:
            return row->xcalstd ? inst_ok : inst_unsupported;
        default:
            return inst_unsupported;
    }
}

/* Bounds-checked as every driver does (i1d3.c:4160-4161); a type change clears
 * any correction (inst.h:843-845). */
static inst_code get_disptypesel(inst *p, int *count, inst_disptypesel **list,
                                  int allconfig, int recreate) {
    static inst_disptypesel entries[100];
    (void)p; (void)allconfig; (void)recreate;
    memset(entries,0,sizeof(entries));
    *count=row->displays; *list=entries;
    for (int i=0; i<*count; i++) {
        sprintf(entries[i].desc,"Simulated display calibration %d",i);
        entries[i].cbid=i+1; entries[i].ix=i;
        entries[i].refr=((row->catalogue_refresh ? row->catalogue_refresh : row->refresh) >> i)&1; entries[i].dtech=disptech_unknown;
    }
    return inst_ok;
}
static inst_code get_disptechi(inst *p, disptech *technology, int *refresh, int *base) {
    (void)p; *technology=disptech_unknown; *refresh=refresh_mode(); *base=1;
    return inst_ok;
}

static inst_code set_disptype(inst *p, int index) {
    int before;
    (void)p; require(initialized && mode_sets >= 1 && !trigger_set);
    if (index < 0 || index >= row->displays) return inst_unsupported;
    before = refresh_mode();
    display = index;
    matrix_scale = 1.0;
    if (refresh_mode() != before) rate_set = 0;
    return inst_ok;
}

static inst_code col_cor_mat(inst *p, disptech dtech, int cbid, double mtx[3][3]) {
    (void)p; (void)dtech; (void)cbid;
    require(initialized && mode_sets >= 1 && !trigger_set);
    if (!(row->cap2 & inst2_ccmx)) return inst_unsupported;
    /* kleink10.c:1423: the diagonal's mean decides the Klein's units. */
    matrix_scale = (mtx[0][0] + mtx[1][1] + mtx[2][2]) / 3.0;
    return inst_ok;
}

static inst_code col_cal_spec_set(inst *p, disptech dtech, xspect *sets, int no_sets) {
    (void)p; (void)dtech; (void)sets; (void)no_sets;
    require(initialized && mode_sets >= 1 && !trigger_set);
    return row->cap2 & inst2_ccss ? inst_ok : inst_unsupported;
}

static inst_code get_n_a_cals(inst *p, inst_cal_type *needed, inst_cal_type *available) {
    const int illum = illumination(active);
    inst_cal_type need = calibrated || (retained_start && waived) ? inst_calt_none
                       : masked ? row->startup[illum].needed
                                  & ~(inst_calt_ref_white | inst_calt_trans_white)
                       : row->startup[illum].needed;
    inst_cal_type offer = row->startup[illum].needed
                        | (illum == CM_AMBIENT ? inst_calt_none : row->available);
    (void)p; require(initialized);
    if (integration_time()) {
        offer |= inst_calt_emis_int_time;
        if (!timed) need |= inst_calt_emis_int_time;
    }
    if (row->refresh_calibration && refresh_mode()) {
        offer |= inst_calt_ref_freq;
        if (!rate_set) need |= inst_calt_ref_freq;
    }
    if (read_needed) need |= row->read_need;
    if (needed) *needed = need;
    if (available) *available = offer;
    return inst_ok;
}

/* The conditions for what *calt asks, in the driver's order: its startup steps,
 * then the integration time (i1pro_imp.c:2614-2620), then a refresh rate on an
 * 80 % patch (i1disp.c:2089-2112, spyd2.c:3310-3313, specbos.c:1848-1862). The
 * condition handed back completes its step (instappsup.c:186-189). */
static inst_code calibrate(inst *p, inst_cal_type *calt, inst_cal_cond *calc,
                           inst_calc_id_type *idtype, char id[CALIDLEN]) {
    const cm_startup *startup = &row->startup[illumination(active)];
    inst_cal_cond conds[4];
    int n = 0;
    (void)p;
    require(initialized && calt && calc && idtype && id);
    require(++rounds <= 8);
    if (*calt & startup->needed)
        for (int i = 0; i < 2 && startup->steps[i] != inst_calc_none; i++)
            /* With the restored calibration standing, what is asked again is only
             * the transmission white session.c requests for every setup. */
            if (!(retained_start && waived) || startup->steps[i] == inst_calc_man_trans_white)
                conds[n++] = startup->steps[i];
    if (*calt & inst_calt_emis_int_time) conds[n++] = inst_calc_emis_white;
    if (*calt & inst_calt_ref_freq) conds[n++] = inst_calc_emis_80pc;
    /* A read-reported dark offset takes the dark cover (dtp92.c:835-838). */
    if (*calt & row->read_need & inst_calt_emis_offset) conds[n++] = inst_calc_man_em_dark;
    while (step < n && (*calc & inst_calc_cond_mask) == conds[step]) step++;
    if (step < n) {
        const int white = conds[step] == inst_calc_man_ref_white
                       || conds[step] == inst_calc_man_ref_whitek;
        *calc = conds[step];
        *idtype = white && row->reference ? inst_calc_id_ref_sn : inst_calc_id_none;
        snprintf(id, CALIDLEN, "%s", *idtype == inst_calc_id_ref_sn ? row->reference : "");
        return inst_cal_setup;
    }
    if (*calt & startup->needed) calibrated = 1;
    if (*calt & inst_calt_emis_int_time) timed = 1;
    if (*calt & inst_calt_ref_freq) rate_set = 1;
    if (*calt & row->read_need) read_needed = 0;
    if (!row->keeps_calt) *calt = inst_calt_none;
    step = 0;
    return inst_ok;
}

#include "timing_stub.h"
static void set_uicallback(inst *p, inst_code (*cb)(void *, inst_ui_purp), void *ctx) {
    (void)p;
    timing_callback = cb; timing_context = ctx;
    require(cb && !coms);
}

/* One patch, as this driver shapes it. */
static void fill(ipatch *val, int index) {
    const int illum = illumination(active);
    const int emitted = illum == CM_EMISSIVE || illum == CM_AMBIENT;
    int k = 0;
    memset(val, 0, sizeof(*val));
    for (const char *c = row->persona; *c; c++) k += *c;
    k = (k + index) % 12;
    if (row->bands) cm_synthetic_reading(val, k, emitted, row->first, row->last, row->bands);
    else cm_synthetic_reading(val, k, emitted, 380.0, 730.0, 36);
    /* A colorimeter measures tristimulus and nothing else, and a spectrometer
     * returns its spectrum only when the mode asks (i1pro_imp.c:10512-10514),
     * unless it always does (ex1.c:663-670). */
    if (!row->bands || !(IMODETST(active, inst_mode_spectral) || row->spectrum_always))
        memset(&val->sp, 0, sizeof(val->sp));
    val->mtype = illum == CM_REFLECTIVE ? inst_mrt_reflective
               : illum == CM_TRANSMISSIVE ? inst_mrt_transmissive
               : illum == CM_AMBIENT ? (IMODETST(active, inst_mode_emis_ambient_flash)
                                        ? inst_mrt_ambient_flash : inst_mrt_ambient)
               : inst_mrt_emission;
    /* kleink10.c:1422-1425 types every reading by the matrix, whatever the
     * mode; session.c refuses a route the matrix disagrees with. */
    if (row->ambient_by_matrix)
        val->mtype = matrix_scale > 5.0 ? inst_mrt_ambient : inst_mrt_emission;
    if (illum == CM_EMISSIVE && row->unset_emission) val->mtype = inst_mrt_none;
    val->mcond = illum == CM_REFLECTIVE ? condition : inst_mrc_none;
    val->XYZ_v = 1;
    val->duration = 0.0;
}

static inst_code read_sample(inst *p, char *name, ipatch *val, instClamping clamp) {
    (void)p; (void)name; (void)clamp;
    require(initialized && mode_sets >= 1 && trigger_set);
    /* A read reports the need (dtp92.c:617-622). Calibration clears it in our
     * repaired source. The fixture says how many reads do: one, and the
     * read after the calibration succeeds; two, and it fails again. */
    if (reads_needing > 0) {
        reads_needing--;
        read_needed = 1;
        return inst_needs_cal;
    }
    fill(val, 0);
    return inst_ok;
}

/* i1pro.c:232-255 ignores the pass geometry and returns what it scanned. */
static inst_code read_strip(inst *p, char *name, int npatch, char *pname, int sguide,
                            double pwid, double gwid, double twid, ipatch *vals) {
    (void)p; (void)name; (void)pname; (void)sguide; (void)pwid; (void)gwid; (void)twid;
    require(initialized && mode_sets >= 1 && trigger_set && npatch >= 1);
    for (int i = 0; i < npatch; i++) fill(&vals[i], i);
    return inst_ok;
}

static int deleted;
static void del(inst *p) { require(!deleted); ++deleted; free(p); }
static void must_release(void) { require(deleted == 1); }

inst *cm_table_persona(const char *name, int needing, int retained) {
    inst *p;
    for (size_t i = 0; i < sizeof(rows) / sizeof(rows[0]) && !row; i++)
        if (!strcmp(rows[i].persona, name)) row = &rows[i];
    require(row != NULL);
    require(!needing || row->read_need != inst_calt_none);
    reads_needing = needing;
    retained_start = retained;
    /* Selected by the name inst_enum() would resolve to this type, as a real
     * attach is (session_attach.c). */
    require(!strcmp(cm_selected_instrument, row->name));
    if (!(p = calloc(1, sizeof(inst)))) return NULL;
    atexit(must_release);
    p->dtype = row->dtype;
    p->capabilities = capabilities;
    p->check_mode = check_mode;
    p->set_mode = set_mode;
    p->init_coms = init_coms;
    p->init_inst = init_inst;
    p->get_serial_no = get_serial_no;
    p->get_set_opt = options;
    p->set_disptype = set_disptype;
    p->get_disptypesel = get_disptypesel;
    p->get_disptechi = get_disptechi;
    p->col_cor_mat = col_cor_mat;
    p->col_cal_spec_set = col_cal_spec_set;
    /* Huey, HCFR and both ColorHugs leave this slot unset in their constructors.
     * new_inst installs inst_get_n_a_cals (inst.c:795-800), whose identity is
     * how session.c distinguishes unknown readiness from a driver's assessment.
     * A wrapper returning the same zero masks would falsely claim the latter. */
    p->get_n_a_cals = row->default_calibration ? inst_get_n_a_cals : get_n_a_cals;
    p->calibrate = calibrate;
    p->set_uicallback = set_uicallback;
    p->meas_delay = stub_meas_delay;
    p->white_change = stub_white_change;
    p->read_sample = read_sample;
    for (int i = 0; row->routes[i]; i++)
        if (row->routes[i] & inst_mode_strip) p->read_strip = read_strip;
    p->del = del;
    return p;
}
