/* Source-bound high-resolution factor probe. Real factor calculation; raw-to-
 * wavelength and fine-tune object factories are inert substitutions. Driver
 * modes, error codes and scale constants are extracted from pinned source. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <float.h>
#include <math.h>

#define HIGH_RES
#undef FAKE_AMBIENT
#include "codes.inc"

typedef struct { int debug; } a1log;
static void a1loge(a1log *l, int lev, char *fmt, ...) { (void)l; (void)lev; (void)fmt; abort(); }
#define a1logd(...) ((void)0)
typedef struct { int n; } xspect;
enum { icxIT_Ptemp = 1 };
static int standardIlluminant(xspect *x, int t, double k) { (void)x; (void)t; (void)k; return 0; }
static double value_xspect(xspect *x, double w) { (void)x; (void)w; return 1.0; }
#define XSPECT_WL(s, l, n, i) ((s) + (i) * ((l) - (s)) / ((n) - 1.0))
#define XSPECT_DIX(s, l, n, w) (((n) - 1.0) * ((w) - (s)) / ((l) - (s)))
typedef struct { double p[1], v[1], w; } cow;
typedef struct { double p[1], v[1]; } co;
typedef double datai[1];
typedef double datao[1];
typedef struct _rspl {
	int (*fit_rspl_w)(struct _rspl *, int, cow *, int, datai, datai, int *, datao, datao, double, double *, double *);
	int (*interp)(struct _rspl *, co *);
	void (*del)(struct _rspl *);
} rspl;
#define RSPL_NOFLAGS 0
static int fit_rspl(rspl *r, int flags, cow *points, int count, datai low, datai high,
                    int *resolution, datao vlow, datao vhigh, double smooth,
                    double *average, double *weights) {
	(void)r; (void)flags; (void)points; (void)count; (void)low; (void)high;
	(void)resolution; (void)vlow; (void)vhigh; (void)smooth; (void)average; (void)weights;
	return 0;
}
static int interp_rspl(rspl *r, co *point) { (void)r; point->v[0] = 1.0; return 0; }
static void del_rspl(rspl *r) { (void)r; }
static rspl probe_rspl = { fit_rspl, interp_rspl, del_rspl };
static rspl *new_rspl(int f, int di, int fdi) {
	(void)f; (void)di; (void)fdi; return &probe_rspl;
}

static int nalloc, ncalloc, calloc_fail;
static double *dvectorz(int nl, int nh) { double *v = calloc(nh - nl + 1, sizeof(double)); nalloc++; return v - nl; }
static double *dvector(int nl, int nh) { return dvectorz(nl, nh); }
static void free_dvector(double *v, int nl, int nh) { (void)nh; free(v + nl); nalloc--; }
static void *probe_calloc(size_t n, size_t sz) {
	if (calloc_fail) return NULL;
	ncalloc++; return calloc(n, sz);
}
static void probe_free(void *v) { if (v != NULL) ncalloc--; free(v); }

typedef struct { int reflective, cal_valid; double *cal_factor[2]; double *white_data; } i1pro_state;
typedef struct {
	int nwav[2], nraw, hr_inited, emis_hr_cal, mmode;
	double wl_short[2], wl_long[2];
	double *emis_coef[2], *amb_coef[2], *white_ref[2];
	i1pro_state ms[i1p_no_modes];
} i1proimp;
typedef struct { a1log *log; i1proimp *m; } i1pro;
static double wav_lerp(i1proimp *m, int hr, double *v, double wl) {
	(void)m; (void)hr; (void)v; (void)wl; return 1.0;
}
static double wav_lerp_cv(i1proimp *m, int hr, double *v, double wl, double cv) { (void)m; (void)hr; (void)v; (void)wl; (void)cv; abort(); return 0.0; }
static void i1pro_absraw_to_abswav(i1pro *p, int hr, int refl, int n, double **abswav, double **absraw) {
	int j; (void)refl; (void)n;
	for (j = 0; j < p->m->nwav[hr]; j++)
		abswav[0][j] = absraw[0][j];
}

i1pro_code i1pro_compute_white_cal(i1pro *, double *, double *, double *, double *, double *, double *, int);
i1pro_code i1pro_create_hr_calfactors(i1pro *, int);
#define calloc probe_calloc
#define free probe_free
#include "white.inc"
#undef calloc
#undef free
#include "hr.inc"

#define N0 6
#define N1 8
#define NRAW 12
#define OLDF 0.25

static double ref0[N0], ref1[N1], ec0[N0], ec1[N1], ac0[N0], ac1[N1];

/* modes[k] >= 0: that mode holds a valid calibration with white value raw[k] (band 2 = b2[k]) */
static void setup(i1pro *p, i1proimp *m, a1log *lg, const int *modes, const double *raw, const double *b2, int nm) {
	int i, j, k;
	memset(m, 0, sizeof(*m));
	m->nwav[0] = N0; m->nwav[1] = N1; m->nraw = NRAW; m->hr_inited = 1;
	m->wl_short[0] = m->wl_short[1] = 380.0; m->wl_long[0] = m->wl_long[1] = 730.0;
	for (j = 0; j < N0; j++) { ref0[j] = 0.9; ec0[j] = 1.0; ac0[j] = 1.0; }
	for (j = 0; j < N1; j++) { ref1[j] = 0.9; ec1[j] = 1.0; ac1[j] = 1.0; }
	m->white_ref[0] = ref0; m->white_ref[1] = ref1;
	m->emis_coef[0] = ec0; m->emis_coef[1] = ec1; m->amb_coef[0] = ac0; m->amb_coef[1] = ac1;
	for (i = 0; i < i1p_no_modes; i++) {
		i1pro_state *s = &m->ms[i];
		s->cal_factor[0] = dvectorz(0, N0-1); s->cal_factor[1] = dvectorz(0, N1-1);
		s->white_data = dvectorz(-1, NRAW-1);
		for (j = 0; j < N0; j++) s->cal_factor[0][j] = OLDF;
		for (j = 0; j < N1; j++) s->cal_factor[1][j] = OLDF;
		s->reflective = (i == i1p_refl_spot || i == i1p_refl_scan);
		s->cal_valid = 0;
	}
	for (i = i1p_emiss_spot_na; i <= i1p_emiss_scan; i++) {	/* not yet generated, as at init */
		m->ms[i].cal_valid = 1;
		for (j = 0; j < N1; j++) m->ms[i].cal_factor[1][j] = 0.0;
	}
	for (k = 0; k < nm; k++) {
		i1pro_state *s = &m->ms[modes[k]];
		s->cal_valid = 1;
		for (j = -1; j < NRAW; j++) s->white_data[j] = raw[k];
		s->white_data[2] = b2[k];
	}
	p->log = lg; p->m = m;
}
static void teardown(i1proimp *m) {
	int i;
	for (i = 0; i < i1p_no_modes; i++) {
		free_dvector(m->ms[i].cal_factor[0], 0, N0-1); free_dvector(m->ms[i].cal_factor[1], 0, N1-1);
		free_dvector(m->ms[i].white_data, -1, NRAW-1);
	}
}

/* The fine-tune writes shared emissive coefficients. A bad later band must
 * leave every old coefficient intact; a valid set must replace the whole set. */
static int fine_tune_case(const char *name, int bad_band, int overflow) {
	i1proimp mi; i1pro p; a1log lg = { 0 };
	double read0[N0], read1[N1], cal0[N0], cal1[N1], prior[N1];
	int i, ev, failed = 0;
	setup(&p, &mi, &lg, NULL, NULL, NULL, 0);
	mi.mmode = i1p_refl_spot;
	for (i = 0; i < N0; i++) read0[i] = ref0[i];
	for (i = 0; i < N1; i++) {
		read1[i] = ref1[i];
		ec1[i] = prior[i] = 0.25;
	}
	if (bad_band) {
		if (overflow) { ref1[3] = 1e308; read1[3] = 1e-300; }
		else if (bad_band == 2) { ref1[3] = 1e-300; read1[3] = 1e100; }
		else if (bad_band == 3) read1[3] = 0.0;
		else read1[3] = -ref1[3];
	}
	ncalloc = 0;
	ev = i1pro_compute_white_cal(&p, cal0, ref0, read0, cal1, ref1, read1, 1);
	if (bad_band) {
		if (ev != I1PRO_RD_WHITEREFERROR || memcmp(ec1, prior, sizeof(prior)) != 0
		    || mi.emis_hr_cal != 0) failed = 1;
	} else {
		if (ev != I1PRO_OK || mi.emis_hr_cal != 1) failed = 1;
		for (i = 0; i < N1; i++) if (ec1[i] != 1.0) failed = 1;
	}
	if (ncalloc != 0) failed = 1;
	teardown(&mi);
	if (nalloc != 0) failed = 1;
	printf("%s: code 0x%x, prior-preserved %d, allocations %d, %s\n", name, ev,
	       memcmp(ec1, prior, sizeof(prior)) == 0, ncalloc,
	       failed ? "failed" : "passed");
	if (failed) printf("FAIL(compiled): %s\n", name);
	return failed;
}

typedef i1pro_code (*hrfn)(i1pro *, int);
static const struct { const char *tag; hrfn fn; int checked; } V[] = {
    { "compiled", i1pro_create_hr_calfactors, 1 } };
#define NV ((int)(sizeof(V)/sizeof(V[0])))
static int fails[NV], checks[NV];
#define CHK(k, c, ...) do { checks[k]++; if (!(c)) { fails[k]++; printf("      FAIL(%s): ", V[k].tag); printf(__VA_ARGS__); printf("\n"); } } while (0)

/* expect_ev: returned code expected for fix-04 (the first refusal); refusing[]: modes that must end invalid */
static void run(const char *name, const int *modes, const double *raw, const double *b2, int nm,
                int fail_fine_tune, int expect_ev, const int *refusing, int nref) {
	int k, j, r;
	printf("%s\n", name);
	for (k = 0; k < NV; k++) {
		i1proimp mi; i1pro p; a1log lg = { 0 };
		int ev, emis_ok = 1;
		setup(&p, &mi, &lg, modes, raw, b2, nm);
		calloc_fail = fail_fine_tune; ncalloc = 0;
		ev = V[k].fn(&p, 0);
		calloc_fail = 0;
		for (j = 0; j < N1; j++)
			if (mi.ms[i1p_emiss_spot_na].cal_factor[1][j] != 1.0 || mi.ms[i1p_emiss_scan].cal_factor[1][j] != 1.0) emis_ok = 0;
		printf("   %-18s ev 0x%02x  refl_spot valid %d cf0 %-7.4g refl_scan valid %d cf0 %-7.4g trans_spot valid %d  emissive hi-res %s  calloc bal %d\n",
		       V[k].tag, ev, mi.ms[0].cal_valid, mi.ms[0].cal_factor[0][0], mi.ms[1].cal_valid, mi.ms[1].cal_factor[0][0],
		       mi.ms[7].cal_valid, emis_ok ? "installed" : "NOT INSTALLED", ncalloc);
		if (V[k].checked) {
			CHK(k, ev == expect_ev, "returned 0x%x, expected 0x%x", ev, expect_ev);
			for (r = 0; r < nref; r++)
				CHK(k, mi.ms[refusing[r]].cal_valid == 0, "refusing mode %d left valid", refusing[r]);
			CHK(k, emis_ok, "emissive hi-res factors not installed");
			CHK(k, ncalloc == 0, "fine-tune allocations unbalanced (%d)", ncalloc);
		}
		teardown(&mi);
	}
}

int main(int argc, char **argv) {
	static const int m1[] = { i1p_refl_scan }, m7[] = { i1p_trans_spot }, m01[] = { i1p_refl_spot, i1p_refl_scan };
	static const double w5000[] = { 5000.0, 5000.0 }, w1000[] = { 1000.0 }, w0[] = { 0.0 }, wsub[] = { 1e-310 };
	double nanv = strtod("nan", NULL), b2nan[2];
	static const int r1[] = { i1p_refl_scan }, r7[] = { i1p_trans_spot }, r01[] = { i1p_refl_spot, i1p_refl_scan };
	int k, tot;
	if (argc == 2 && !strcmp(argv[1], "fine-tune-zero")) {
		checks[0] = 1;
		tot = fine_tune_case("fine-tune zero divisor preserves state", 3, 0);
		printf("hr: %d checks, %d failures\n", checks[0], tot);
		return tot != 0;
	}
	b2nan[0] = 5000.0; b2nan[1] = nanv;
	run("C0 control: reflective scan, white 5000", m1, w5000, w5000, 1, 0, I1PRO_OK, r1, 0);
	run("C1 control: transmission spot, white 1000", m7, w1000, w1000, 1, 0, I1PRO_OK, r7, 0);
	{ double b[1]; b[0] = nanv;
	run("C2 reflective scan, NaN in one white band", m1, w5000, b, 1, 0, I1PRO_RD_WHITEREFERROR, r1, 1); }
	run("C3 transmission spot, all-zero white", m7, w0, w0, 1, 0, I1PRO_RD_WHITEREFERROR, r7, 1);
	run("C4 transmission spot, subnormal white", m7, wsub, wsub, 1, 0, I1PRO_RD_WHITEREFERROR, r7, 1);
	run("C5 two reflective modes refuse: refl_spot fine-tune allocation fails (first), refl_scan NaN band (second)",
	    m01, w5000, b2nan, 2, 1, I1PRO_INT_MALLOC, r01, 2);
    tot = fails[0] + (nalloc != 0);
	/* Each direct fine-tune case is one additional outcome check. */
	checks[0] += 5;
	tot += fine_tune_case("fine-tune normal commit", 0, 0);
	tot += fine_tune_case("fine-tune negative band preserves state", 1, 0);
	tot += fine_tune_case("fine-tune underflowed factor preserves state", 2, 0);
	tot += fine_tune_case("fine-tune zero divisor preserves state", 3, 0);
	tot += fine_tune_case("fine-tune overflow preserves state", 1, 1);
    printf("hr: %d checks, %d failures\n", checks[0], tot);
    return tot != 0;
}
