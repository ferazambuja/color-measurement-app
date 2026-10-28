/* Original inert harness. Extracted driver code carries its original
 * GPL-2.0-or-later notices. No transport or physical acquisition is linked. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>
#include <time.h>

#define ORD8 uint8_t
#define ORD16 uint16_t
#define ORD32 uint32_t
typedef struct { int debug; } a1log;
typedef struct _xspect xspect;          /* referenced by pointer only */
/* The base object's layout is not in question; what follows it is. The public
 * selectors read the two readiness flags (spydX2.c:1497-1500). */
#define INST_OBJ_BASE a1log *log; int gotcoms; int inited;
#define a1logd(...) ((void)0)
#define a1loge(...) ((void)0)
#include "declarations.h"
typedef spydX2 inst;

static int checks, failures, commands, executed, skipped;
static const char *scenario;
static char failed[256];
static void note_failure(void) {
    char token[40];
    snprintf(token, sizeof token, ",%s,", scenario);
    char listed[260];
    snprintf(listed, sizeof listed, ",%s,", failed);
    if (strstr(listed, token)) return;
    if (*failed) strcat(failed, ",");
    strcat(failed, scenario);
}
#define CHECK(expression) do { checks++; if (!(expression)) { failures++; note_failure(); \
    fprintf(stderr, "%s:%d: %s\n", scenario, __LINE__, #expression); } } while (0)

/* The USB stand-in answers the calibration request with a
 * reply whose every field depends on the index asked for, so a slot holding
 * another index's calibration, or none, is visible. These are synthetic command
 * values, exactly representable in binary32, not an instrument's calibration.
 * The common base, logging and error helpers are also inert scaffolding; this
 * probe does not establish the production object size or its full ABI. */
static int spydX2_command(spydX2 *p, int cmd, unsigned char *send, unsigned int s_size,
                          unsigned char *reply, unsigned int r_size, int chsum, double to);
static inst_code spydX2_interp_code(inst *pp, int ec) {
    (void)pp;
    return (inst_code)(inst_protocol_error | ec);
}
static int icoms2spydX2_err(int se) { return se; }
#ifdef SPYDX2_PATH_PROBE
/* Every path scenario supplies the list itself, so the driver's list creation
 * must never be reached. */
static inst_code inst_creat_disptype_list(inst *pp, int *pndtlist, inst_disptypesel **pdtlist,
                                          inst_disptypesel *sdtlist, int doccss, int doccmx) {
    (void)pp; (void)pndtlist; (void)pdtlist; (void)sdtlist; (void)doccss; (void)doccmx;
    CHECK(!"inst_creat_disptype_list reached");
    return inst_internal_error;
}
#endif
#include "functions.inc"

static double matrix_value(int ix, int k) { return (ix + 1) + k / 8.0; }
static double extra_value(int ix, int k) { return -(ix + 1) - k / 4.0; }
static int fail_next;   /* nz: the next command fails at the transport (path family) */

static int spydX2_command(spydX2 *p, int cmd, unsigned char *send, unsigned int s_size,
                          unsigned char *reply, unsigned int r_size, int chsum, double to) {
    int ix, k;
    (void)p; (void)to;
    commands++;
    CHECK(cmd == 0xF6 && s_size == 1 && r_size == 0x6C && chsum == 1);
    if (fail_next) { fail_next = 0; return 1; }
    ix = send[0];
    memset(reply, 0, r_size);
    write_ORD8(reply + 0, ix);
    write_ORD8(reply + 1, 3);
    write_ORD16_be(reply + 2, 700 + ix);
    for (k = 0; k < 6; k++) write_ORD8(reply + 4 + k, 10 * ix + k);
    for (k = 0; k < 18; k++)
        write_ORD32_le(reply + 10 + 4 * k, doubletoIEEE754(matrix_value(ix, k)));
    for (k = 0; k < 6; k++)
        write_ORD32_le(reply + 82 + 4 * k, doubletoIEEE754(extra_value(ix, k)));
    write_ORD8(reply + 106, 40 + ix);
    return SPYDX2_OK;
}

#define COUNT(a) ((int)(sizeof(a) / sizeof((a)[0])))
/* The guard follows the object, so a write past it lands where it can be seen. */
typedef struct { spydX2 obj; unsigned char guard[1024]; } boxed;
/* The slots this object has, from its type alone (never evaluated). */
#define SLOTS COUNT(((spydX2 *)0)->cinfo)
static a1log quiet;

static boxed *prepare(int is2024, int forcell) {
    int i;
    boxed *b = malloc(sizeof *b);
    if (!b) { fputs("out of memory\n", stderr); exit(2); }
    memset(b, 0, sizeof *b);
    memset(b->guard, 0xA5, sizeof b->guard);
    b->obj.log = &quiet;
    b->obj.is2024 = is2024;
    b->obj.forcell = forcell;
    if (is2024) {
        /* A high-level unit offering every display number (spydX2_getInstInfo). */
        b->obj.hlavail = 1;
        b->obj.mxdnp1 = SPYD2024_NOCALIBS;
        b->obj.dnomask = (1 << SPYD2024_NOCALIBS) - 1;
    }
    /* State after the array that no selection may touch. */
    b->obj.bcal_done = 0x5A5A;
    for (i = 0; i < 6; i++) b->obj.bcal[i] = 1000 + i;
    b->obj.bdate = (time_t)123456;
    b->obj.noinitcalib = 77;
    b->obj.lo_secs = 88;
    return b;
}
static int intact(const boxed *b) {
    int i, ok = b->obj.bcal_done == 0x5A5A && b->obj.bdate == (time_t)123456
        && b->obj.noinitcalib == 77 && b->obj.lo_secs == 88;
    for (i = 0; i < 6; i++) ok = ok && b->obj.bcal[i] == 1000 + i;
    for (i = 0; i < (int)sizeof b->guard; i++) ok = ok && b->guard[i] == 0xA5;
    return ok;
}
static int empty_slot(SpX2calinfo slot, int ix) {
    static const SpX2calinfo zero;
    CHECK(slot.ix == ix);          /* set_disp_type records the index first */
    slot.ix = 0;
    return memcmp(&slot, &zero, sizeof zero) == 0;
}
static int holds_calibration(const SpX2calinfo *c, int ix) {
    int i, j, ok = c->ix == ix && c->v1 == 3 && c->v2 == 700 + ix && c->v3 == 40 + ix;
    for (i = 0; i < 6; i++) ok = ok && c->v4[i] == 10 * ix + i;
    for (i = 0; i < 3; i++)
        for (j = 0; j < 6; j++) ok = ok && c->mat[i][j] == matrix_value(ix, j * 3 + i);
    for (j = 0; j < 3; j++)
        ok = ok && c->gain[j] == extra_value(ix, 2 * j) && c->off[j] == extra_value(ix, 2 * j + 1);
    return ok;
}
static int unity(double m[3][3]) {
    int i, j, ok = 1;
    for (i = 0; i < 3; i++)
        for (j = 0; j < 3; j++) ok = ok && m[i][j] == (i == j ? 1.0 : 0.0);
    return ok;
}

#ifndef SPYDX2_PATH_PROBE
/* ---- Calibration-slot family (spyder_calibration_slots.py) ---- */

/* Every built-in calibration the driver advertises has its own slot. */
static void slots(const char *model, inst_disptypesel *table) {
    int e, defaults = 0;
    scenario = "slots";
    for (e = 0; !(table[e].flags & inst_dtflags_end); e++) {
        if (!(table[e].flags & inst_dtflags_mtx)) continue;
        if (table[e].flags & inst_dtflags_default) defaults++;
        if (table[e].ix < 0 || table[e].ix >= SLOTS)
            fprintf(stderr, "%s \"%s\" native ix %d has no slot (%d slots)\n",
                    model, table[e].desc, table[e].ix, SLOTS);
        CHECK(table[e].ix >= 0 && table[e].ix < SLOTS);
    }
    CHECK(defaults == 1);
}

/* Each advertised selection on a fresh object, through the actual set_disp_type. */
static void select_each(const char *name, inst_disptypesel *table, int is2024, int forcell) {
    int e, i, highlevel = is2024 && !forcell;
    for (e = 0; !(table[e].flags & inst_dtflags_end); e++) {
        inst_disptypesel dentry = table[e];
        int ix = dentry.ix;
        boxed *b;
        if (!(dentry.flags & inst_dtflags_mtx)) continue;
        if (ix < 0 || ix >= SLOTS) { skipped++; continue; }  /* never run UB */
        scenario = name;
        b = prepare(is2024, forcell);
        commands = 0;
        CHECK(set_disp_type(&b->obj, &dentry) == inst_ok);
        executed++;
        CHECK(b->obj.ix == ix);
        CHECK(b->obj.usehl == highlevel);
        CHECK(commands == (highlevel ? 0 : 1));
        CHECK((dentry.flags & inst_dtflags_ld) != 0);
        CHECK(b->obj.cbid == dentry.cbid && b->obj.ucbid == dentry.cbid);
        CHECK(b->obj.dtech == dentry.dtech);
        CHECK(unity(b->obj.ccmat));
        if (highlevel)
            CHECK(empty_slot(b->obj.cinfo[ix], ix));
        else
            CHECK(holds_calibration(&b->obj.cinfo[ix], ix));
        for (i = 0; i < COUNT(b->obj.cinfo); i++)
            if (i != ix) CHECK(empty_slot(b->obj.cinfo[i], 0));
        CHECK(intact(b));
        free(b);
    }
}

/* One object, OLED and Mini-LED among others, each reselected: a loaded entry
 * is not read again, and every index keeps its own calibration. */
static void repeat(void) {
    static const int order[] = {6, 0, 5, 6, 0};
    inst_disptypesel table[SPYD2024_NOCALIBS + 1];
    int k, e;
    boxed *b;
    scenario = "repeat";
    for (k = 0; k < COUNT(order); k++)
        if (order[k] >= SLOTS) { skipped++; return; }
    memcpy(table, spyd2024_disptypesel, sizeof table);
    b = prepare(1, 1);
    commands = 0;
    for (k = 0; k < COUNT(order); k++) {
        for (e = 0; e < SPYD2024_NOCALIBS; e++)
            if ((table[e].flags & inst_dtflags_mtx) && table[e].ix == order[k]) break;
        CHECK(e < SPYD2024_NOCALIBS);
        if (e == SPYD2024_NOCALIBS) break;
        CHECK(set_disp_type(&b->obj, &table[e]) == inst_ok);
        executed++;
        CHECK(b->obj.ix == order[k]);
    }
    CHECK(commands == 3);
    for (k = 0; k < 3; k++)   /* 6, 0 and 5, each read once */
        CHECK(holds_calibration(&b->obj.cinfo[order[k]], order[k]));
    CHECK(intact(b));
    free(b);
}

int main(void) {
    slots("SpyderX2", spydX2_disptypesel);
    slots("Spyder2024", spyd2024_disptypesel);
    select_each("x2", spydX2_disptypesel, 0, 0);
    select_each("low_level", spyd2024_disptypesel, 1, 1);
    select_each("high_level", spyd2024_disptypesel, 1, 0);
    repeat();
    printf("{\"checks\": %d, \"failures\": %d, \"failed_scenarios\": \"%s\", "
           "\"skipped\": %d, \"executed\": %d, \"slots\": %d, \"object_bytes\": %lu}\n",
           checks, failures, failed, skipped, executed, SLOTS,
           (unsigned long)sizeof(spydX2));
    return failures != 0;
}

#else
/* ---- Selection-path family (spyder_selection_path.py): which measurement path
 * each selection leaves in force, through the public selectors. ---- */

static const double correction[3][3] = {{1.5, 0.25, 0.0}, {0.0, 0.75, 0.125}, {0.5, 0.0, 2.0}};
static int same3x3(double a[3][3], const double b[3][3]) {
    int i, j, ok = 1;
    for (i = 0; i < 3; i++)
        for (j = 0; j < 3; j++) ok = ok && a[i][j] == b[i][j];
    return ok;
}

/* A unit whose firmware supports the high-level command for the display numbers
 * in `mask` below `mxdnp1` (spydX2_getInstInfo, spydX2.c:271-278), with the list
 * the driver would build: the table and, when asked, one CCMX entry over cbid 1
 * (inst_creat_disptype_list, inst.c:1019-1035). Ready for the public selectors. */
static boxed *unit(int is2024, int forcell, int mxdnp1, int mask, int with_ccmx, int cc_cbid) {
    const inst_disptypesel *table = is2024 ? spyd2024_disptypesel : spydX2_disptypesel;
    inst_disptypesel *list;
    boxed *b = prepare(is2024, forcell);
    int n, e;
    b->obj.mxdnp1 = mxdnp1;
    b->obj.dnomask = mask;
    for (n = 0; !(table[n].flags & inst_dtflags_end); n++) ;
    list = calloc(n + 2, sizeof *list);
    if (!list) { fputs("out of memory\n", stderr); exit(2); }
    memcpy(list, table, n * sizeof *list);
    if (with_ccmx) {
        for (e = 0; e < n && list[e].cbid != 1; e++) ;
        CHECK(e < n);                                   /* the base with cbid 1 */
        list[n].flags = inst_dtflags_ccmx | inst_dtflags_ld | inst_dtflags_wr;
        strcpy(list[n].sel, "c");
        strcpy(list[n].desc, "Correction");
        list[n].dtech = disptech_lcd;
        list[n].ix = list[e].ix;                        /* inst.c:1031 */
        list[n].cc_cbid = cc_cbid;
        memcpy(list[n].mat, correction, sizeof correction);
        n++;
    }
    list[n].flags = inst_dtflags_end;
    b->obj.dtlist = list;
    b->obj.ndtlist = n;
    b->obj.gotcoms = b->obj.inited = 1;
    return b;
}
static void done(boxed *b) {
    CHECK(intact(b));
    free(b->obj.dtlist);
    free(b);
}
/* Select list entry e through the public selector; the path it leaves in force. */
static int select_entry(boxed *b, int e) {
    CHECK(spydX2_set_disptype(&b->obj, e) == inst_ok);
    executed++;
    CHECK(b->obj.ix == b->obj.dtlist[e].ix);
    return b->obj.usehl;
}
static int entry_of(const boxed *b, int ix) {
    int e;
    for (e = 0; e < b->obj.ndtlist; e++)
        if ((b->obj.dtlist[e].flags & inst_dtflags_mtx) && b->obj.dtlist[e].ix == ix) return e;
    CHECK(!"native index not listed");
    return 0;
}

/* first, second, first again under a mask that puts them on different paths:
 * the cached reselection must leave the path its own first selection chose. */
static void mixed(const char *name, int mask, int first, int second) {
    int high1 = (mask >> first) & 1, high2 = (mask >> second) & 1;
    boxed *b;
    scenario = name;
    b = unit(1, 0, SPYD2024_NOCALIBS, mask, 0, 0);
    commands = 0;
    CHECK(select_entry(b, entry_of(b, first)) == high1);
    CHECK(select_entry(b, entry_of(b, second)) == high2);
    CHECK(commands == !high1 + !high2);                 /* each low-level index read once */
    CHECK(select_entry(b, entry_of(b, first)) == high1);
    CHECK(commands == !high1 + !high2);                 /* the cached entry is not reread */
    CHECK(high1 ? empty_slot(b->obj.cinfo[first], first)
                : holds_calibration(&b->obj.cinfo[first], first));
    CHECK(high2 ? empty_slot(b->obj.cinfo[second], second)
                : holds_calibration(&b->obj.cinfo[second], second));
    done(b);
}

/* The firmware's display-number limit: indices at or past it go low level even
 * when the mask bit is set (spydX2.c:1437), and index 0 stays high level after. */
static void boundary(void) {
    boxed *b;
    scenario = "max_display_boundary";
    b = unit(1, 0, 5, (1 << SPYD2024_NOCALIBS) - 1, 0, 0);
    commands = 0;
    CHECK(select_entry(b, entry_of(b, 0)) == 1);
    CHECK(select_entry(b, entry_of(b, 5)) == 0);        /* OLED */
    CHECK(commands == 1 && holds_calibration(&b->obj.cinfo[5], 5));
    CHECK(select_entry(b, entry_of(b, 0)) == 1);
    CHECK(select_entry(b, entry_of(b, 6)) == 0);        /* Mini-LED */
    CHECK(commands == 2 && holds_calibration(&b->obj.cinfo[6], 6));
    done(b);
}

/* A CCMX list entry over base 0, chosen after index 1 took the low-level path. */
static void ccmx_list(void) {
    boxed *b;
    int c;
    scenario = "ccmx_list";
    b = unit(1, 0, SPYD2024_NOCALIBS, 1 << 0, 1, 1);
    c = b->obj.ndtlist - 1;
    commands = 0;
    CHECK(set_default_disp_type(&b->obj) == inst_ok);
    CHECK(b->obj.ix == 0 && b->obj.usehl == 1 && b->obj.cbid == 1);
    CHECK(select_entry(b, entry_of(b, 1)) == 0);
    CHECK(select_entry(b, c) == 1);
    CHECK(b->obj.cbid == 0 && b->obj.dtech == disptech_lcd);
    CHECK(same3x3(b->obj.ccmat, correction));
    CHECK(commands == 1);
    done(b);
}

/* The application's chain: the default at init, an explicit display type, then
 * spydX2_col_cor_mat over base 1, which reselects the cached entry 0. */
static void correction_matrix(void) {
    double m[3][3];
    boxed *b;
    scenario = "correction_matrix";
    memcpy(m, correction, sizeof m);
    b = unit(1, 0, SPYD2024_NOCALIBS, 1 << 0, 0, 0);
    commands = 0;
    CHECK(set_default_disp_type(&b->obj) == inst_ok);
    CHECK(b->obj.usehl == 1);
    CHECK(select_entry(b, entry_of(b, 1)) == 0);
    CHECK(spydX2_col_cor_mat(&b->obj, disptech_lcd, 1, m) == inst_ok);
    CHECK(b->obj.ix == 0 && b->obj.usehl == 1);
    CHECK(b->obj.cbid == 0 && b->obj.dtech == disptech_lcd && same3x3(b->obj.ccmat, correction));
    CHECK(spydX2_col_cor_mat(&b->obj, disptech_unknown, 1, NULL) == inst_ok);
    CHECK(b->obj.ix == 0 && b->obj.usehl == 1 && unity(b->obj.ccmat));
    CHECK(commands == 1);
    done(b);
}

/* One path throughout: every selection, cached or not, leaves the same path. */
static void uniform(const char *name, int is2024, int forcell) {
    int high = is2024 && !forcell;
    boxed *b;
    scenario = name;
    b = unit(is2024, forcell, SPYD2024_NOCALIBS, (1 << SPYD2024_NOCALIBS) - 1, 0, 0);
    commands = 0;
    CHECK(select_entry(b, entry_of(b, 0)) == high);
    CHECK(select_entry(b, entry_of(b, 1)) == high);
    CHECK(select_entry(b, entry_of(b, 0)) == high);
    CHECK(commands == (high ? 0 : 2));
    CHECK(high ? empty_slot(b->obj.cinfo[0], 0) : holds_calibration(&b->obj.cinfo[0], 0));
    CHECK(high ? empty_slot(b->obj.cinfo[1], 1) : holds_calibration(&b->obj.cinfo[1], 1));
    done(b);
}

/* A low-level read that fails at the transport: the selection fails, the entry
 * stays unloaded and is read on retry; index 0 is then cached high level. */
static void failed_load(void) {
    boxed *b;
    int e1;
    scenario = "failed_load";
    b = unit(1, 0, SPYD2024_NOCALIBS, 1 << 0, 0, 0);
    e1 = entry_of(b, 1);
    commands = 0;
    CHECK(select_entry(b, entry_of(b, 0)) == 1);
    fail_next = 1;
    CHECK(spydX2_set_disptype(&b->obj, e1) != inst_ok);
    CHECK(commands == 1 && !(b->obj.dtlist[e1].flags & inst_dtflags_ld));
    CHECK(b->obj.ix == 1 && b->obj.usehl == 0);         /* as upstream leaves it */
    CHECK(empty_slot(b->obj.cinfo[1], 1));
    CHECK(select_entry(b, e1) == 0);
    CHECK(commands == 2 && (b->obj.dtlist[e1].flags & inst_dtflags_ld));
    CHECK(holds_calibration(&b->obj.cinfo[1], 1));
    CHECK(select_entry(b, entry_of(b, 0)) == 1);
    CHECK(commands == 2);
    done(b);
}

/* A CCMX entry over another base is refused without touching the selection. */
static void wrong_cbid(void) {
    boxed *b;
    scenario = "wrong_cbid";
    b = unit(1, 0, SPYD2024_NOCALIBS, 1 << 0, 1, 2);
    commands = 0;
    CHECK(set_default_disp_type(&b->obj) == inst_ok);
    CHECK(select_entry(b, entry_of(b, 1)) == 0);
    CHECK(spydX2_set_disptype(&b->obj, b->obj.ndtlist - 1) == inst_wrong_setup);
    CHECK(b->obj.ix == 1 && b->obj.usehl == 0 && b->obj.cbid == 0 && unity(b->obj.ccmat));
    CHECK(commands == 1);
    done(b);
}

int main(void) {
    mixed("mixed_high_low_high", 1 << 0, 0, 1);
    mixed("mixed_low_high_low", 1 << 1, 0, 1);
    boundary();
    ccmx_list();
    correction_matrix();
    uniform("uniform_high", 1, 0);
    uniform("forced_low", 1, 1);
    uniform("x2", 0, 0);
    failed_load();
    wrong_cbid();
    printf("{\"checks\": %d, \"failures\": %d, \"failed_scenarios\": \"%s\", "
           "\"selections\": %d, \"slots\": %d}\n",
           checks, failures, failed, executed, SLOTS);
    return failures != 0;
}
#endif
