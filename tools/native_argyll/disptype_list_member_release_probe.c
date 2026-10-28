/* Display-list member-release probe: the exact list constructor, growth,
 * destructor and selector assignment (functions.inc, Graeme W. Gill's
 * GPL-2.0-or-later text), original or repaired, over a tracked allocator. The
 * CCSS and CCMX listings are scripted stand-ins that allocate what the readers
 * allocate for the members the constructor takes, and may fail as the readers
 * fail. Every attempt of a case is recorded; a handed-back list is released as
 * every destructor releases it, and what stays allocated is counted. No file
 * system, transport or instrument runtime.
 *
 * Usage: probe <case>   or   probe --list
 * stdout: one JSON object; stderr: nothing unless a check fails. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* ---- tracked allocation, defined with the real names before the macros ---- */
#define CM_BLOCKS 8192
static struct { void *p; size_t n; } blocks[CM_BLOCKS];
static long live_blocks, allocations, untracked_frees;
static int g_fail_realloc_at, g_reallocs;      /* the Nth realloc fails, 0 = none */

static void track_add(void *p, size_t n) {
	int i;
	if (p == NULL) return;
	for (i = 0; i < CM_BLOCKS; i++) {
		if (blocks[i].p == NULL) {
			blocks[i].p = p; blocks[i].n = n;
			live_blocks++; allocations++;
			return;
		}
	}
	fprintf(stderr, "tracker full\n"); exit(2);
}
static void track_del(void *p) {
	int i;
	for (i = 0; i < CM_BLOCKS; i++) {
		if (blocks[i].p == p) {
			blocks[i].p = NULL; blocks[i].n = 0; live_blocks--;
			return;
		}
	}
	untracked_frees++;
}
/* Fresh bytes are poisoned so that what the constructor leaves uninitialised is
 * observed, not inherited from zero pages (the selector bytes). */
static void *cm_malloc(size_t n) {
	void *p = malloc(n);
	if (p != NULL) memset(p, 0x7f, n);
	track_add(p, n); return p;
}
static void *cm_calloc(size_t a, size_t b) { void *p = calloc(a, b); track_add(p, a * b); return p; }
static void *cm_realloc(void *p, size_t n) {
	int slot = -1, i;
	void *q;
	if (p != NULL) {
		for (i = 0; i < CM_BLOCKS; i++)
			if (blocks[i].p == p) { slot = i; break; }
		if (slot < 0) { fprintf(stderr, "untracked realloc\n"); exit(2); }
	}
	if (++g_reallocs == g_fail_realloc_at)
		return NULL;                                /* the old block stays live and owned */
	{
		size_t old = slot >= 0 ? blocks[slot].n : 0;
		q = realloc(p, n);
		if (q != NULL && n > old) memset((char *)q + old, 0x7f, n - old);
	}
	if (q != NULL) {
		if (slot >= 0) { blocks[slot].p = NULL; blocks[slot].n = 0; live_blocks--; }
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

/* ---- reduced common base: only what the extracted text reads ---- */
typedef struct { int debug; } a1log;
typedef struct _xspect xspect;              /* referenced by pointer only */
typedef int instType;
#define a1logd(...) ((void)0)
#define a1loge(...) ((void)0)
#define a1logw(...) ((void)0)
#include "declarations.h"
typedef struct _inst { a1log *log; instType dtype; } inst;

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
#include "functions.inc"

/* ---- scripted listings ---- */
static int g_ccss_files, g_ccmx_files, g_fail_ccss, g_fail_ccmx;

/* Each CCSS entry owns a path, a description and a sample-set block, as
 * list_iccss leaves them (inst.c:1366, :1354, :1385-1390); the constructor
 * takes the path and the sets (inst.c:981, :983). */
static iccss *list_iccss(int *no) {
	int i;
	iccss *list;
	if (g_fail_ccss) { if (no != NULL) *no = 0; return NULL; }
	list = calloc(g_ccss_files + 1, sizeof *list);
	for (i = 0; i < g_ccss_files; i++) {
		char text[64];
		snprintf(text, sizeof text, "/scripted/spectra/set-%02d.ccss", i);
		list[i].path = strdup(text);
		snprintf(text, sizeof text, "Scripted spectra %02d", i);
		list[i].desc = strdup(text);
		list[i].dtech = disptech_lcd;
		list[i].refr = 0;
		list[i].sel = NULL;
		list[i].oem = (i & 1);
		list[i].sets = (xspect *)malloc(3 * 64);    /* opaque: three sample spectra */
		list[i].no_sets = 3;
	}
	if (no != NULL) *no = g_ccss_files;
	return list;
}
static void free_iccss(iccss *list) {
	int i;
	for (i = 0; list[i].path != NULL || list[i].desc != NULL; i++) {
		if (list[i].path != NULL) free(list[i].path);
		if (list[i].desc != NULL) free(list[i].desc);
		if (list[i].sets != NULL) free(list[i].sets);
	}
	free(list);
}
/* Every CCMX entry names base 1, which the static table has (inst.c:1003-1013). */
static iccmx *list_iccmx(instType itype, int *no) {
	int i;
	iccmx *list;
	(void)itype;
	if (g_fail_ccmx) { if (no != NULL) *no = 0; return NULL; }
	list = calloc(g_ccmx_files + 1, sizeof *list);
	for (i = 0; i < g_ccmx_files; i++) {
		char text[64];
		snprintf(text, sizeof text, "/scripted/corrections/matrix-%03d.ccmx", i);
		list[i].path = strdup(text);
		snprintf(text, sizeof text, "Scripted %03d", i);
		list[i].desc = strdup(text);
		list[i].dtech = disptech_lcd;
		list[i].cc_cbid = 1;
		list[i].refr = 0;
		list[i].sel = NULL;
		list[i].oem = (i & 1);
		list[i].mat[0][0] = list[i].mat[1][1] = list[i].mat[2][2] = 1.0 + i / 16.0;
	}
	if (no != NULL) *no = g_ccmx_files;
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

/* ---- cases ---- */
typedef struct {
	const char *name;
	int repeat;         /* attempts in one process */
	int doccss;         /* ask for CCSS files */
	int ccss_files;     /* how many the scripted directory holds */
	int ccmx_files;
	int fail_ccss;      /* the CCSS listing fails */
	int fail_ccmx;      /* the CCMX listing fails */
	int fail_realloc;   /* the Nth growth fails, 0 = none */
} spec;

static const spec cases[] = {
	{"static_ccmx_fails",         1, 0, 0,   0, 0, 1, 0},
	{"ccss2_ccmx_fails",          1, 1, 2,   0, 0, 1, 0},
	{"ccss2_ccmx_fails_repeat5",  5, 1, 2,   0, 0, 1, 0},
	{"ccss2_ccmx3",               1, 1, 2,   3, 0, 0, 0},
	{"ccss_fails",                1, 1, 0,   0, 1, 0, 0},
	/* 2 static + 2 CCSS + 6 CCMX = 10 entries: the second growth (at 9) is inside the CCMX loop */
	{"ccss2_ccmx6_grow_fails",    1, 1, 2,   6, 0, 0, 2},
	{"ccmx130",                   1, 0, 0, 130, 0, 0, 0},
};
#define NCASES ((int)(sizeof(cases) / sizeof(cases[0])))
#define MAXREP 8

/* Two built-in calibrations with the shape the drivers' tables have. */
static inst_disptypesel table[] = {
	{ inst_dtflags_default, 1, "nl", "Non-refresh display", 0, disptech_lcd, 0 },
	{ inst_dtflags_none,    2, "rc", "Refresh display",     1, disptech_crt, 1 },
	{ inst_dtflags_end }
};

int main(int argc, char **argv) {
	const spec *c = NULL;
	int i, it;
	int codes[MAXREP], handed[MAXREP], entries[MAXREP], unassigned[MAXREP];
	a1log quiet = {0};
	inst instrument;

	if (argc == 2 && strcmp(argv[1], "--list") == 0) {
		for (i = 0; i < NCASES; i++) puts(cases[i].name);
		return 0;
	}
	if (argc != 2) return 2;
	for (i = 0; i < NCASES; i++)
		if (strcmp(argv[1], cases[i].name) == 0) c = &cases[i];
	if (c == NULL || c->repeat > MAXREP) return 3;
	instrument.log = &quiet;
	instrument.dtype = 0;
	g_ccss_files = c->ccss_files;
	g_ccmx_files = c->ccmx_files;
	g_fail_ccss = c->fail_ccss;
	g_fail_ccmx = c->fail_ccmx;

	for (it = 0; it < c->repeat; it++) {
		inst_disptypesel *list = NULL;
		int n = 0, k;
		g_reallocs = 0;
		g_fail_realloc_at = c->fail_realloc;
		codes[it] = inst_creat_disptype_list(&instrument, &n, &list, table, c->doccss, 1);
		handed[it] = list != NULL;
		entries[it] = n;
		/* A two-letter selector whose second byte the constructor never wrote:
		 * bounded reads, since such a selector need not be NUL-terminated. */
		unassigned[it] = 0;
		for (k = 0; list != NULL && k < n; k++)
			if (list[k].sel[0] == '_' && (unsigned char)list[k].sel[1] == 0x7f) unassigned[it]++;
		/* Release what was handed back, as every destructor does. */
		inst_del_disptype_list(list, n);
	}

	printf("{\"case\":\"%s\",\"iterations\":%d,\"codes\":[", c->name, c->repeat);
	for (it = 0; it < c->repeat; it++) printf("%s%d", it ? "," : "", codes[it]);
	printf("],\"handed\":[");
	for (it = 0; it < c->repeat; it++) printf("%s%d", it ? "," : "", handed[it]);
	printf("],\"entries\":[");
	for (it = 0; it < c->repeat; it++) printf("%s%d", it ? "," : "", entries[it]);
	printf("],\"unassigned\":[");
	for (it = 0; it < c->repeat; it++) printf("%s%d", it ? "," : "", unassigned[it]);
	printf("],\"allocations\":%ld,\"left\":%ld,\"untracked_frees\":%ld}\n",
	       allocations, live_blocks, untracked_frees);
	return 0;
}
