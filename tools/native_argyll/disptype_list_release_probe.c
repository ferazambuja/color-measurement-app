/* Original inert harness. Extracted driver code carries its original
 * GPL-2.0-or-later notices. No transport or physical acquisition is linked.
 *
 * One driver per build: -DCM_KLEINK10, -DCM_SPYDX or -DCM_SPYDX2. The list
 * helpers, tables, selectors, getter and destructor are the pinned text in
 * functions.inc; what the destructor leaves allocated is counted by the tracked
 * allocator below, which every allocation in the extracted unit goes through.
 * stdout: one JSON object; stderr: every failed check. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>
#include <float.h>
#define ORD64 uint64_t
#include <time.h>

/* ---- tracked allocation, defined with the real names before the macros ---- */
#define CM_BLOCKS 8192
static struct { void *p; size_t n; } blocks[CM_BLOCKS];
static long live_blocks, live_bytes, allocations, untracked_frees;

static void track_add(void *p, size_t n) {
	int i;
	if (p == NULL) return;
	for (i = 0; i < CM_BLOCKS; i++) {
		if (blocks[i].p == NULL) {
			blocks[i].p = p; blocks[i].n = n;
			live_blocks++; live_bytes += (long)n; allocations++;
			return;
		}
	}
	fprintf(stderr, "tracker full\n"); exit(2);
}
static void track_del(void *p) {
	int i;
	for (i = 0; i < CM_BLOCKS; i++) {
		if (blocks[i].p == p) {
			blocks[i].p = NULL; live_blocks--; live_bytes -= (long)blocks[i].n; blocks[i].n = 0;
			return;
		}
	}
	untracked_frees++;
}
static void *cm_malloc(size_t n) { void *p = malloc(n); track_add(p, n); return p; }
static void *cm_calloc(size_t a, size_t b) { void *p = calloc(a, b); track_add(p, a * b); return p; }
static void *cm_realloc(void *p, size_t n) {
	int slot = -1, i;
	void *q;
	/* Find the bookkeeping slot while p is still live. Successful realloc
	 * ends the old allocation's lifetime, even when the address is unchanged. */
	if (p != NULL) {
		for (i = 0; i < CM_BLOCKS; i++)
			if (blocks[i].p == p) { slot = i; break; }
		if (slot < 0) { fprintf(stderr, "untracked realloc\n"); exit(2); }
	}
	if (n == 0) {
		if (p != NULL) track_del(p);
		free(p);
		return NULL;
	}
	q = realloc(p, n);
	if (q != NULL) {
		if (slot >= 0) {
			blocks[slot].p = NULL;
			live_blocks--; live_bytes -= (long)blocks[slot].n; blocks[slot].n = 0;
		}
		track_add(q, n);
	}
	return q;
}
static void cm_free(void *p) { if (p != NULL) track_del(p); free(p); }
static char *cm_strdup(const char *s) {
	size_t n = strlen(s) + 1; char *d = cm_malloc(n);
	if (d != NULL) memcpy(d, s, n);
	return d;
}
#define malloc cm_malloc
#define calloc cm_calloc
#define realloc cm_realloc
#define free cm_free
#define strdup cm_strdup

/* ---- reduced common base and inert stand-ins for what the drivers touch ---- */
#define ORD8 uint8_t
#define ORD16 uint16_t
#define ORD32 uint32_t
typedef struct { int debug; } a1log;
typedef struct _xspect xspect;          /* referenced by pointer only */
typedef int instType;
typedef struct _icoms icoms;
struct _icoms { void (*del)(icoms *p); };
typedef int amutex;
static long icom_dels, mutex_dels, vdels, touches, commands;
#define amutex_del(m) (mutex_dels++)
#define a1logd(...) ((void)0)
#define a1loge(...) ((void)0)
#define a1logw(...) ((void)0)
#define ENABLE_NONVCAL                  /* as spydX.c and spydX2.c define it */
/* The base object's layout is not in question; the members the extracted
 * selection, correction and destructor code reads are. */
#define INST_OBJ_BASE a1log *log; icoms *icom; instType dtype; int gotcoms; int inited; \
                      int (*vdel)(void *pp);
#include "declarations.h"

#if defined(CM_KLEINK10)
typedef kleink10 cm_inst;
#define CM_NAME "kleink10"
#define CM_DEL k10_del
#define CM_GET k10_get_disptypesel
#define CM_SET k10_set_disptype
#define CM_COLCOR k10_col_cor_mat
#define CM_TABLE k10_disptypesel
#elif defined(CM_SPYDX)
typedef spydX cm_inst;
#define CM_NAME "spydX"
#define CM_DEL spydX_del
#define CM_GET spydX_get_disptypesel
#define CM_SET spydX_set_disptype
#define CM_COLCOR spydX_col_cor_mat
#define CM_TABLE spydX_disptypesel
#elif defined(CM_SPYDX2)
typedef spydX2 cm_inst;
#define CM_NAME "spydX2"
#define CM_DEL spydX2_del
#define CM_GET spydX2_get_disptypesel
#define CM_SET spydX2_set_disptype
#define CM_COLCOR spydX2_col_cor_mat
#define CM_TABLE spydX2_disptypesel
#else
#error "one of CM_KLEINK10, CM_SPYDX, CM_SPYDX2 selects the driver"
#endif
typedef cm_inst inst;

/* Installed correction and spectral-set lists as inst.h declares them: only the
 * members inst_creat_disptype_list reads. */
typedef struct {
	char *path; char *desc; disptech dtech; int cc_cbid; int refr; char *sel; int oem;
	double mat[3][3];
} iccmx;
typedef struct {
	char *path; char *desc; disptech dtech; int refr; char *sel; int oem;
	xspect *sets; int no_sets;
} iccss;
static iccmx *list_iccmx(instType itype, int *no);
static void free_iccmx(iccmx *list);
static iccss *list_iccss(int *no);
static void free_iccss(iccss *list);

/* Driver-internal helpers the extracted text calls: prototypes as the drivers
 * declare them, stand-ins defined below. */
static inst_code set_disp_type(cm_inst *p, inst_disptypesel *dentry);
static inst_code set_default_disp_type(cm_inst *p);
#if defined(CM_KLEINK10)
static inst_code k10_read_cal_matrix(kleink10 *p, inst_disptypesel *m, int ix);
#elif defined(CM_SPYDX)
static inst_code set_base_disp_type(spydX *p, int cbid);
static int spydX_touch_calibration(spydX *p);
static inst_code spydX_getCalibration(spydX *p, double mat[3][3], int *pv1, int *pv2,
                                      int *pv3, int cix);
#else
static inst_code set_base_disp_type(spydX2 *p, int cbid);
static int spydX2_touch_calibration(spydX2 *p);
static int spydX2_command(spydX2 *p, int cmd, unsigned char *send, unsigned int s_size,
                          unsigned char *reply, unsigned int r_size, int chsum, double to);
static inst_code spydX2_interp_code(inst *pp, int ec) {
	(void)pp;
	return (inst_code)(inst_protocol_error | ec);
}
static int icoms2spydX2_err(int se) { return se; }
#endif
#include "functions.inc"

/* ---- stand-ins ---- */
static void icoms_del(icoms *p) { icom_dels++; free(p); }
static icoms *new_icoms(void) { icoms *p = calloc(1, sizeof *p); p->del = icoms_del; return p; }
static int virtual_del(void *pp) { (void)pp; vdels++; return 0; }

#if defined(CM_KLEINK10)
/* k10_read_cal_matrix: the instrument's matrix for one calibration. */
static inst_code k10_read_cal_matrix(kleink10 *p, inst_disptypesel *m, int ix) {
	int i, j;
	(void)p; commands++;
	for (i = 0; i < 3; i++) for (j = 0; j < 3; j++) m->mat[i][j] = (i == j) ? 1.0 : 0.0;
	m->mat[0][0] = 1.0 + ix / 100.0;
	m->flags |= inst_dtflags_ld;
	return inst_ok;
}
#elif defined(CM_SPYDX)
static int spydX_touch_calibration(spydX *p) { (void)p; touches++; return 0; }
static inst_code spydX_getCalibration(spydX *p, double mat[3][3], int *pv1, int *pv2,
                                      int *pv3, int cix) {
	int i, j;
	(void)p; commands++;
	if (cix < 0 || cix >= SPYDX_NOCALIBS) return inst_wrong_setup;
	for (i = 0; i < 3; i++) for (j = 0; j < 3; j++) mat[i][j] = (i == j) ? 1.0 + cix / 8.0 : 0.0;
	*pv1 = 3; *pv2 = 700 + cix; *pv3 = 40 + cix;
	return inst_ok;
}
#else
static int spydX2_touch_calibration(spydX2 *p) { (void)p; touches++; return 0; }
/* The slot proof's scripted calibration reply: every field depends on the index. */
static int spydX2_command(spydX2 *p, int cmd, unsigned char *send, unsigned int s_size,
                          unsigned char *reply, unsigned int r_size, int chsum, double to) {
	int ix, k;
	(void)p; (void)to; (void)cmd; (void)s_size; (void)chsum;
	commands++;
	ix = send[0];
	memset(reply, 0, r_size);
	write_ORD8(reply + 0, ix);
	write_ORD8(reply + 1, 3);
	write_ORD16_be(reply + 2, 700 + ix);
	for (k = 0; k < 6; k++) write_ORD8(reply + 4 + k, 10 * ix + k);
	for (k = 0; k < 18; k++)
		write_ORD32_le(reply + 10 + 4 * k, doubletoIEEE754((ix + 1) + k / 8.0));
	for (k = 0; k < 6; k++)
		write_ORD32_le(reply + 82 + 4 * k, doubletoIEEE754(-(ix + 1) - k / 4.0));
	write_ORD8(reply + 106, 40 + ix);
	return SPYDX2_OK;
}
#endif

/* Installed CCMX files: seven match base 1, three name a base no table has and
 * must be skipped (inst.c:1003-1013). Nothing is read from disk. */
static int ccmx_files;                   /* how many the scripted directory holds */
static const int ccmx_cbid[10] = {1, 1, 2, 1, 1, 2, 1, 1, 2, 1};
static iccmx *list_iccmx(instType itype, int *no) {
	int i;
	iccmx *list = calloc(ccmx_files + 1, sizeof *list);
	(void)itype;
	for (i = 0; i < ccmx_files; i++) {
		char text[64];
		snprintf(text, sizeof text, "/scripted/corrections/matrix-%02d.ccmx", i);
		list[i].path = strdup(text);
		snprintf(text, sizeof text, "Scripted %02d", i);
		list[i].desc = strdup(text);
		list[i].dtech = disptech_lcd;
		list[i].cc_cbid = ccmx_cbid[i];
		list[i].refr = 0;
		list[i].sel = NULL;
		list[i].oem = (i & 1);
		list[i].mat[0][0] = list[i].mat[1][1] = list[i].mat[2][2] = 1.0 + i / 16.0;
	}
	if (no != NULL) *no = ccmx_files;
	return list;
}
static void free_iccmx(iccmx *list) {
	int i;
	for (i = 0; list[i].path != NULL || list[i].desc != NULL; i++) {
		if (list[i].path != NULL) free(list[i].path);
		if (list[i].desc != NULL) free(list[i].desc);
	}
	free(list);
}
static iccss *list_iccss(int *no) { if (no != NULL) *no = 0; return NULL; }
static void free_iccss(iccss *list) { (void)list; }

/* ---- checks ---- */
static int checks, failures;
static const char *scenario;
static char failed[256];
static long left[8];
static const char *left_names[8];
static int nleft;
static char state[2048];

static void note_failure(void) {
	char token[40], listed[260];
	snprintf(token, sizeof token, ",%s,", scenario);
	snprintf(listed, sizeof listed, ",%s,", failed);
	if (strstr(listed, token)) return;
	if (*failed) strcat(failed, ",");
	strcat(failed, scenario);
}
#define CHECK(expression) do { checks++; if (!(expression)) { failures++; note_failure(); \
	fprintf(stderr, "%s:%d: %s\n", scenario, __LINE__, #expression); } } while (0)

static a1log quiet;
static cm_inst *create(void) {
	cm_inst *p = calloc(1, sizeof *p);
	p->log = &quiet;
	p->icom = new_icoms();
	p->vdel = virtual_del;
	p->gotcoms = 1;
	p->inited = 1;
	p->dtype = 0;
	return p;
}
static int table_entries(void) {
	int n = 0;
	while (!(CM_TABLE[n].flags & inst_dtflags_end)) n++;
	return n;
}
/* Delete through the driver, then record what stays allocated: the list is the
 * only thing the object owned besides itself and its transport. */
static void delete_and_record(cm_inst *p) {
	long before_icoms = icom_dels, before_vdels = vdels;
	CM_DEL((inst *)p);
	CHECK(icom_dels == before_icoms + 1);
	CHECK(vdels == before_vdels + 1);
	CHECK(untracked_frees == 0);
	CHECK(live_blocks == 0);
	left_names[nleft] = scenario; left[nleft] = live_blocks; nleft++;
	/* Release anything the original left, so scenarios stay independent. */
	{ int i; for (i = 0; i < CM_BLOCKS; i++) if (blocks[i].p != NULL) { void *q = blocks[i].p; cm_free(q); } }
}
static void append_state(const char *tag, cm_inst *p, inst_disptypesel *list, int n) {
	int i;
	size_t at = strlen(state);
	snprintf(state + at, sizeof state - at, "%s:n=%d,cbid=%d,ucbid=%d,dtech=%d,ccmat=%.3f/%.3f/%.3f,sels=",
	         tag, n, p->cbid, p->ucbid, (int)p->dtech, p->ccmat[0][0], p->ccmat[1][1], p->ccmat[2][2]);
	for (i = 0; i < n; i++) {
		at = strlen(state);
		snprintf(state + at, sizeof state - at, "%s%s", list[i].sel, i + 1 < n ? "+" : ";");
	}
}

static void del_only(void) {
	cm_inst *p;
	scenario = "del_only";
	p = create();
	CHECK(p->dtlist == NULL && p->ndtlist == 0);
	delete_and_record(p);
}
static void init_del(void) {
	cm_inst *p;
	scenario = "init_del";
	p = create();
	CHECK(set_default_disp_type(p) == inst_ok);          /* as init_inst does */
	CHECK(p->dtlist != NULL && p->ndtlist == table_entries());
	CHECK(p->cbid == CM_TABLE[0].cbid);
	append_state("init", p, p->dtlist, p->ndtlist);
	delete_and_record(p);
}
static void get_del(void) {
	cm_inst *p;
	inst_disptypesel *sels = NULL;
	int n = 0;
	scenario = "get_del";
	p = create();
	CHECK(CM_GET((inst *)p, &n, &sels, 0, 0) == inst_ok);
	CHECK(sels != NULL && sels == p->dtlist && n == table_entries());
	CHECK(live_blocks == 3);                               /* object, transport, list */
	delete_and_record(p);
}
static void repeat(void) {
	cm_inst *p;
	inst_disptypesel *first = NULL, *again = NULL;
	int n = 0, i;
	long blocks_after_first, allocations_after_first;
	scenario = "repeat";
	p = create();
	CHECK(CM_GET((inst *)p, &n, &first, 0, 0) == inst_ok);
	blocks_after_first = live_blocks; allocations_after_first = allocations;
	for (i = 0; i < 5; i++) {
		CHECK(CM_GET((inst *)p, &n, &again, 0, 0) == inst_ok);
		CHECK(again == first);                             /* the same list, no allocation */
	}
	CHECK(live_blocks == blocks_after_first && allocations == allocations_after_first);
	delete_and_record(p);
}
static void recreate(void) {
	cm_inst *p;
	inst_disptypesel *sels = NULL;
	int n = 0, i;
	long blocks_after_first;
	scenario = "recreate";
	p = create();
	CHECK(CM_GET((inst *)p, &n, &sels, 0, 0) == inst_ok);
	blocks_after_first = live_blocks;
	for (i = 0; i < 5; i++) {
		CHECK(CM_GET((inst *)p, &n, &sels, 0, 1) == inst_ok);
		CHECK(live_blocks == blocks_after_first);          /* the old list is freed each time */
		CHECK(sels == p->dtlist && n == table_entries());
	}
	delete_and_record(p);
}
static void ccmx(void) {
	cm_inst *p;
	inst_disptypesel *sels = NULL;
	int n = 0, i, first_ccmx = -1, matching = 0;
	double mtx[3][3] = {{0.9, 0.0, 0.1}, {0.0, 1.1, 0.0}, {0.0, 0.0, 0.8}};
	scenario = "ccmx";
	ccmx_files = 10;
	for (i = 0; i < ccmx_files; i++) matching += (ccmx_cbid[i] == 1);
	p = create();
	CHECK(set_default_disp_type(p) == inst_ok);
	CHECK(CM_GET((inst *)p, &n, &sels, 0, 1) == inst_ok);  /* re-check installed files */
	CHECK(n == table_entries() + matching);
	for (i = 0; i < n; i++)
		if ((sels[i].flags & inst_dtflags_ccmx) && first_ccmx < 0) first_ccmx = i;
	CHECK(first_ccmx >= 0 && sels[first_ccmx].path != NULL && sels[first_ccmx].cc_cbid == 1);
	CHECK(live_blocks == 3 + matching);                    /* object, transport, list, paths */
	/* A correction entry selected by index, then a correction matrix applied
	 * and removed: the driver's own correction handling over the list. */
	CHECK(CM_SET((inst *)p, first_ccmx) == inst_ok);
	CHECK(p->cbid == 0);
	CHECK(CM_COLCOR((inst *)p, disptech_lcd_ccfl, 1, mtx) == inst_ok);
	CHECK(p->cbid == 0 && p->ccmat[1][1] == 1.1);
	CHECK(CM_COLCOR((inst *)p, disptech_unknown, 1, NULL) == inst_ok);
	CHECK(p->ccmat[1][1] == 1.0 && p->ccmat[0][2] == 0.0);
	CHECK(live_blocks == 3 + matching);                    /* selection allocates nothing */
	append_state("ccmx", p, p->dtlist, p->ndtlist);
	delete_and_record(p);
	ccmx_files = 0;
}
#if defined(CM_KLEINK10)
/* The instrument's programmed calibrations extend the static table in place, as
 * k10_read_cal_list does at init (kleink10.c:862-927): forty here, so the list
 * grows through expand_dlist (8, then 22, then 50 entries). */
static void programmed(void) {
	cm_inst *p;
	inst_disptypesel *sels = NULL;
	int n = 0, i;
	static const char *letters = "123456789ABCDEGHIJKLMNOPQRSTUVWXYZabcdefghijklmnop";
	scenario = "programmed";
	for (i = 1; i <= 40; i++) {
		inst_disptypesel *e = &k10_disptypesel[i];
		memset(e, 0, sizeof *e);
		e->flags = inst_dtflags_mtx;
		e->cbid = 0;
		e->sel[0] = letters[i - 1]; e->sel[1] = '\000';
		snprintf(e->desc, sizeof e->desc, "Programmed %d", i);
		e->dtech = disptech_unknown;
		e->ix = i;
	}
	memset(&k10_disptypesel[41], 0, sizeof k10_disptypesel[41]);
	k10_disptypesel[41].flags = inst_dtflags_end;
	p = create();
	CHECK(CM_GET((inst *)p, &n, &sels, 0, 0) == inst_ok);
	CHECK(n == 41 && sels == p->dtlist);
	CHECK(CM_SET((inst *)p, 40) == inst_ok && p->cbid == 0);
	CHECK(live_blocks == 3);
	delete_and_record(p);
	/* Restore the two-entry table for any later scenario. */
	memset(&k10_disptypesel[1], 0, sizeof k10_disptypesel[1]);
	k10_disptypesel[1].flags = inst_dtflags_end;
}
#endif

int main(void) {
	int i;
	del_only();
	init_del();
	get_del();
	repeat();
	recreate();
	ccmx();
#if defined(CM_KLEINK10)
	programmed();
#endif
	printf("{\"driver\": \"%s\", \"checks\": %d, \"failures\": %d, \"failed_scenarios\": \"%s\", "
	       "\"untracked_frees\": %ld, \"commands\": %ld, \"left\": {",
	       CM_NAME, checks, failures, failed, untracked_frees, commands);
	for (i = 0; i < nleft; i++)
		printf("%s\"%s\": %ld", i ? ", " : "", left_names[i], left[i]);
	printf("}, \"state\": \"%s\"}\n", state);
	return failures != 0;
}
