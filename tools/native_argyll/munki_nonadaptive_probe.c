/* ColorMunki nonadaptive display integration-time selection, driven through the
 * authenticated Argyll 3.5.0 calibrate. The generated extracted.inc and
 * mode_state.inc retain Graeme W. Gill's GPL-2.0-or-later code.
 *
 * Transport is excluded by construction: the only stand-ins for instrument I/O
 * are the scripted munki_triggermeasure, munki_readmeasurement and
 * munki_getstatus below. No icoms, usbio or hidio object is compiled or linked.
 *
 * Upstream code exercised: add_munkiimp, munki_imp_init's mode-state block,
 * munki_imp_set_mode, munki_imp_get_n_a_cals, munki_imp_calibrate,
 * munki_imp_measure, munki_dark_measure{,_1,_2}, munki_whitemeasure,
 * munki_read_patches_1, munki_trigger_one_measure, munki_sens_to_raw,
 * munki_comp_{ru_,}nummeas, buf2ushort and numsup's vector allocators.
 *
 * Stand-ins, each documented at its definition: the three I/O primitives,
 * munki_sub_raw_to_absraw (returns a scripted maximum, records the dark bank),
 * munki_average_multimeas (tags the average with the triggered exposure),
 * munki_read_patches_2 (runs the pinned munki_sens_to_raw, then records the
 * exposure and dark bank), munki_conv2XYZ, munki_set_stdres, munki_heatLED,
 * munki_save_calibration, a1log* and msec_*. Everything else a path might call
 * is a tripwire that fails the case.
 *
 * a1logd is a format-only macro in extracted.inc: its variadic arguments
 * are not evaluated. This observes call reachability, not value formatting,
 * so the original failed-white log can be tested without reading its scale.
 *
 * Usage: probe main            defined with this observation, both texts
 *        probe repaired-only   first-probe failures, defined on the repaired text
 * stdout: one JSON object; stderr: every check and scripted acquisition trace. */
#include "extracted.inc"
#include "mode_state.inc"
#include <stdarg.h>

/* ------------------------------------------------------------------ */
/* Result accounting                                                   */

static const char *g_case = "";
static int g_checks, g_failures, g_tripwires;
static char g_failed[4096];

static void record(const char *what, int ok) {
	g_checks++;
	if (ok)
		return;
	g_failures++;
	if (strlen(g_failed) + strlen(g_case) + strlen(what) + 8 < sizeof g_failed) {
		if (*g_failed) strcat(g_failed, "\", \"");
		strcat(g_failed, g_case); strcat(g_failed, ": "); strcat(g_failed, what);
	}
}
static void chk_i(const char *what, long expected, long actual) {
	int ok = expected == actual;
	fprintf(stderr, "  [%s] %-62s expected %-10ld actual %-10ld %s\n",
	        g_case, what, expected, actual, ok ? "PASS" : "FAIL");
	record(what, ok);
}
static void chk_d(const char *what, double expected, double actual) {
	int ok = expected == actual;	/* exact: every value is dyadic */
	fprintf(stderr, "  [%s] %-62s expected %-10.7g actual %-10.7g %s\n",
	        g_case, what, expected, actual, ok ? "PASS" : "FAIL");
	record(what, ok);
}
static void note(const char *fmt, ...) {
	va_list a;
	va_start(a, fmt);
	fprintf(stderr, "  [%s] note    ", g_case);
	vfprintf(stderr, fmt, a);
	fputc('\n', stderr);
	va_end(a);
}

/* ------------------------------------------------------------------ */
/* Scripted acquisitions                                               */

typedef struct {
	const char *tag;
	int trig_rc;		/* munki_triggermeasure result */
	int read_rc;		/* munki_readmeasurement result */
	unsigned int raw;	/* raw level of the 128 spectral cells */
	double maxval;		/* maximum returned by munki_sub_raw_to_absraw */
	int incons;			/* munki_average_multimeas result */
} probe;

#define SATLIMIT 60000.0
#define OPTSVAL  40000.0
#define P_DARK          { "dark",    MUNKI_OK,        MUNKI_OK,        1500,  1.0,     0 }
#define P_OK(mx)        { "ok",      MUNKI_OK,        MUNKI_OK,        30000, (mx),    0 }
#define P_SAT           { "sat",     MUNKI_OK,        MUNKI_OK,        65000, 1.0,     0 }
#define P_TRIGFAIL      { "trigerr", MUNKI_COMS_FAIL, MUNKI_OK,        0,     0.0,     0 }
#define P_READFAIL      { "readerr", MUNKI_OK,        MUNKI_COMS_FAIL, 0,     0.0,     0 }
#define P_INCONS(mx)    { "incons",  MUNKI_OK,        MUNKI_OK,        30000, (mx),    1 }
/* targoscale 0.9 * OPTSVAL 40000 = 36000: maxval 20000 -> scale 1.8 (fits), */
/* maxval 45000 -> scale 0.8 (too bright, switch)                          */
#define BRIGHT 45000.0
#define FITS   20000.0

#define MAXP 32
static probe g_script[MAXP];
static int g_ns, g_si, g_probes;
static probe *g_cur;
static int g_unscripted, g_unexpected;
/* The last white probe's result as the stand-ins delivered it: nonzero when
 * munki_whitemeasure cannot have written its scale. */
static int g_probe_failed;
/* "Needed scale is" logged while the probe that should have written it failed. */
static int g_scale_logged_after_failure;

typedef struct {
	const char *tag;
	char kind;			/* D dark, W white/calibration probe, R read, ? trigger failed */
	int mode;
	double inttime;		/* quantised exposure actually triggered */
	int bank_sub;		/* dark bank the white probe subtracted (-1 none/unknown) */
	int sensrv;			/* read path: munki_sens_to_raw result */
} acq;
static acq g_acq[64];
static int g_na;

static void script(int n, const probe *ps) {
	int i;
	g_ns = n;
	g_si = 0;
	for (i = 0; i < n; i++)
		g_script[i] = ps[i];
}

/* The three dark buffers allocated for a mode by the init block, by identity. */
static double *g_bank[mk_no_modes][3];

static int bank_of(munki *p, int mode, double *ptr) {
	int b;
	for (b = 0; b < 3; b++)
		if (g_bank[mode][b] == ptr)
			return b;
	return -1;
}

/* ------------------------------------------------------------------ */
/* Stand-ins for instrument I/O (the transport boundary)               */

/* Scripted stand-in for the USB trigger command. Consumes one probe. */
munki_code munki_triggermeasure(munki *p, int intclocks, int nummeas, int measmodeflags,
                                int holdtempduty) {
	munkiimp *m = (munkiimp *)p->m;
	acq *a = &g_acq[g_na < 63 ? g_na++ : 63];

	g_probes++;
	if (g_si >= g_ns) {
		g_unscripted++;
		g_cur = NULL;
		g_probe_failed = 1;
		a->tag = "UNSCRIPTED"; a->kind = '!'; a->mode = m->mmode;
		a->inttime = intclocks * m->intclkp; a->bank_sub = -1; a->sensrv = -1;
		return MUNKI_INTERNAL_ERROR;
	}
	g_cur = &g_script[g_si++];
	g_probe_failed = g_cur->trig_rc != MUNKI_OK || g_cur->read_rc != MUNKI_OK
	              || g_cur->raw >= SATLIMIT || g_cur->incons;
	a->tag = g_cur->tag;
	a->kind = '?';
	a->mode = m->mmode;
	a->inttime = intclocks * m->intclkp;
	a->bank_sub = -1;
	a->sensrv = -1;
	return g_cur->trig_rc;
}

/* Scripted stand-in for the USB bulk read. Fills 274-byte readings: four */
/* shielded cells at 1000, spectral cells at the probe's raw level.       */
static munki_code munki_readmeasurement(munki *p, int inummeas, int scanflag,
          unsigned char *buf, int bsize, int *nummeas, int calib_measure, int dark_measure) {
	munkiimp *m = (munkiimp *)p->m;
	int i, k;
	acq *a = &g_acq[g_na - 1];

	a->kind = dark_measure ? 'D' : calib_measure ? 'W' : 'R';
	if (g_cur == NULL)
		return MUNKI_INTERNAL_ERROR;
	if (g_cur->read_rc != MUNKI_OK)
		return g_cur->read_rc;
	if (bsize < inummeas * m->nsen * 2) {
		g_unexpected++;
		return MUNKI_INTERNAL_ERROR;
	}
	for (i = 0; i < inummeas; i++) {
		unsigned char *bp = buf + i * m->nsen * 2;
		for (k = 0; k < m->nsen; k++) {
			unsigned int v = k < 4 ? 1000 : g_cur->raw;
			bp[2 * k] = v & 0xff;
			bp[2 * k + 1] = (v >> 8) & 0xff;
		}
	}
	if (nummeas != NULL)
		*nummeas = inummeas;
	return MUNKI_OK;
}

static mk_spos g_spos = mk_spos_surf;

/* Scripted stand-in for the USB status query. */
munki_code munki_getstatus(munki *p, mk_spos *spos, mk_but *but) {
	if (spos != NULL)
		*spos = g_spos;
	if (but != NULL)
		*but = mk_but_switch_release;
	return MUNKI_OK;
}

/* ------------------------------------------------------------------ */
/* Stand-ins for processing the extracted functions call               */

/* Real code linearises and subtracts `sub`. The stand-in records which dark */
/* bank was subtracted and returns the scripted maximum, which fixes the    */
/* scale munki_whitemeasure computes (targoscale * optsval / maxval).       */
void munki_sub_raw_to_absraw(munki *p, int nummeas, double inttime, int gainmode,
          double **absraw, double *sub, double *trackmax, int ntrackmax, double *maxv) {
	munkiimp *m = (munkiimp *)p->m;
	g_acq[g_na - 1].bank_sub = bank_of(p, m->mmode, sub);
	if (maxv != NULL)
		*maxv = g_cur != NULL ? g_cur->maxval : 1.0;
}

/* Tags the average with the exposure the real trigger quantised (c_inttime) */
/* so each dark bank records the exposure it was measured at.              */
int munki_average_multimeas(munki *p, double *avg, double **multimeas, int nummeas,
                            double *poallavg, double darkthresh) {
	munkiimp *m = (munkiimp *)p->m;
	int j;
	for (j = -1; j < m->nraw; j++)
		avg[j] = 0.0;
	avg[-1] = 1000.0;
	avg[0] = m->c_inttime;
	avg[1] = (double)g_na;
	if (poallavg != NULL)
		*poallavg = 500.0;
	return g_cur != NULL ? g_cur->incons : 1;
}

/* Upstream :4314-4326 converts the buffer with munki_sens_to_raw and returns */
/* its saturation result; :4331 then subtracts s->dark_data at `inttime`.     */
/* The stand-in performs that first step with the pinned helper and records  */
/* the exposure and dark bank the reading would use; spectral output is zero. */
munki_code munki_read_patches_2(munki *p, double *duration, double **specrd, int numpatches,
          double inttime, int gainmode, int ninvmeas, int nummeas, unsigned char *buf,
          unsigned int bsize) {
	munkiimp *m = (munkiimp *)p->m;
	munki_state *s = &m->ms[m->mmode];
	double **multimes, darkthresh;
	int rv, i, j;
	acq *a = &g_acq[g_na - 1];

	if (duration != NULL)
		*duration = 0.0;
	multimes = dmatrix(0, nummeas-1, -1, m->nraw-1);
	rv = munki_sens_to_raw(p, multimes, NULL, buf, ninvmeas, nummeas, m->satlimit, &darkthresh);
	free_dmatrix(multimes, 0, nummeas-1, -1, m->nraw-1);
	a->inttime = inttime;
	a->bank_sub = bank_of(p, m->mmode, s->dark_data);
	a->sensrv = rv;
	if (s->dark_data[0] != inttime)
		note("READ dark bank tag %.7g does not match read exposure %.7g", s->dark_data[0], inttime);
	if (rv != MUNKI_OK)
		return rv;
	for (i = 0; i < numpatches; i++)
		for (j = 0; j < m->nwav; j++)
			specrd[i][j] = 0.0;
	return MUNKI_OK;
}

munki_code munki_conv2XYZ(munki *p, ipatch *vals, int nvals, double **specrd, instClamping clamp) {
	return MUNKI_OK;
}

munki_code munki_set_stdres(munki *p) { return MUNKI_OK; }
munki_code munki_set_highres(munki *p) { g_unexpected++; return MUNKI_UNSUPPORTED; }

static int g_saves, g_heats;
munki_code munki_save_calibration(munki *p) { g_saves++; return MUNKI_OK; }
/* The reflective white calibration heats the LED first (:1513); a no-op here. */
munki_code munki_heatLED(munki *p, double htime) { g_heats++; return MUNKI_OK; }

static unsigned int g_msec;
unsigned int msec_time() { return g_msec += 10; }
void msec_sleep(unsigned int msec) { g_msec += msec; }

/* The macro in extracted.inc discards debug-value expressions before C
 * evaluation. Observe only which call site was reached, including original R1. */
static void cm_observe_debug(const char *fmt) {
	if (strncmp(fmt, "Needed scale is", 15) == 0 && g_probe_failed)
		g_scale_logged_after_failure++;
}
void a1logw(a1log *log, char *fmt, ...) { }
void a1loge(a1log *log, int ecode, char *fmt, ...) { }

int ret_null_on_malloc_fail = 0;
void error(char *fmt, ...) {
	fprintf(stderr, "numsup error(): %s\n", fmt);
	exit(3);
}

/* Tripwires: none of these is on a path the cases select. */
#define TRIP(name) (fprintf(stderr, "[%s] UNEXPECTED CALL %s\n", g_case, name), g_unexpected++)
munki_code munki_interp_dark(munki *p, double *result, double inttime, int gainmode) {
	TRIP("munki_interp_dark"); return MUNKI_INTERNAL_ERROR; }
void munki_prepare_idark(munki *p) { TRIP("munki_prepare_idark"); }
munki_code munki_optimise_sensor(munki *p, double *pnew_int_time, int *pnew_gain_mode,
          double cur_int_time, int cur_gain_mode, int permithg, int permitclip,
          double *targoscale, double scale, double deadtime) {
	TRIP("munki_optimise_sensor"); return MUNKI_INTERNAL_ERROR; }
munki_code munki_ledtemp_whitemeasure(munki *p, double *white, double **iwhite, double *reftemp,
          int nummeas, double inttime, int gainmode) {
	TRIP("munki_ledtemp_whitemeasure"); return MUNKI_INTERNAL_ERROR; }
munki_code munki_compute_wav_whitemeas(munki *p, double *abswav1, double *abswav2, double *absraw) {
	TRIP("munki_compute_wav_whitemeas"); return MUNKI_INTERNAL_ERROR; }
int munki_compute_white_cal(munki *p, double *cal_factor1, double *white_ref1, double *white_read1,
          double *cal_factor2, double *white_ref2, double *white_read2) {
	TRIP("munki_compute_white_cal"); return 0; }
munki_code munki_trialmeasure(munki *p, int *saturated, double *optscale, int nummeas,
          double *inttime, int gainmode, double targoscale) {
	TRIP("munki_trialmeasure"); return MUNKI_INTERNAL_ERROR; }
void issue_scan_ready(inst *p, int delay) { TRIP("issue_scan_ready"); }
void msec_beep(int delay, int freq, int msec) { TRIP("msec_beep"); }

/* ------------------------------------------------------------------ */
/* Instrument construction and observation                             */

static a1log h_log;

/* Mirrors what munki_imp_init establishes before its mode-state block. The
 * values stand in for EEPROM contents; none is claimed to be a real device's.
 * intclkp = 1/1024 makes every quantised exposure dyadic. */
static munki *new_instrument(void) {
	munki *p = (munki *)calloc(1, sizeof(munki));
	munkiimp *m;
	int i, j;

	p->log = &h_log;
	if (add_munkiimp(p) != MUNKI_OK)
		exit(4);
	m = (munkiimp *)p->m;
	m->nsen = 137;
	m->nraw = 128;
	m->nwav1 = 36;
	m->nwav2 = 106;
	m->nwav = m->nwav1;
	m->emis_coef1 = dvectorz(0, m->nwav1-1);
	m->proj_coef1 = dvectorz(0, m->nwav1-1);
	m->amb_coef1  = dvectorz(0, m->nwav1-1);
	for (j = 0; j < (int)m->nwav1; j++)
		m->emis_coef1[j] = m->proj_coef1[j] = m->amb_coef1[j] = 1.0;
	m->intclkp = 1.0/1024.0;
	m->cal_int_time = 0.018208;
	m->min_int_time = 0.007168;
	m->max_int_time = 4.5;
	m->satlimit = SATLIMIT;
	m->optsval = OPTSVAL;
	m->minsval = 20000.0;
	m->maxsval = 50000.0;
	m->ledpreheattime = 1.0;
	m->trig = inst_opt_trig_prog;

	cm_upstream_mode_state_init(p);

	for (i = 0; i < mk_no_modes; i++) {
		g_bank[i][0] = m->ms[i].dark_data;
		g_bank[i][1] = m->ms[i].dark_data2;
		g_bank[i][2] = m->ms[i].dark_data3;
	}
	return p;
}

/* Release only the allocations made by this inert constructor and its extracted
 * mode-state initializer. Calibration permutes each mode's three dark pointers;
 * they still own three distinct vectors. cal_factor is an alias, not an owner.
 * The full instrument destructor also owns transport and cache state absent here. */
static void delete_instrument(munki *p) {
	munkiimp *m = (munkiimp *)p->m;
	int i;
	for (i = 0; i < mk_no_modes; i++) {
		munki_state *s = &m->ms[i];
		free_dvector(s->dark_data, -1, m->nraw-1);
		free_dvector(s->dark_data2, -1, m->nraw-1);
		free_dvector(s->dark_data3, -1, m->nraw-1);
		free_dvector(s->cal_factor1, 0, m->nwav1-1);
		free_dvector(s->cal_factor2, 0, m->nwav2-1);
		free_dvector(s->white_data, -1, m->nraw-1);
		free_dmatrix(s->iwhite_data, 0, 1, -1, m->nraw-1);
		free_dmatrix(s->idark_data, 0, 3, -1, m->nraw-1);
	}
	free_dvector(m->emis_coef1, 0, m->nwav1-1);
	free_dvector(m->proj_coef1, 0, m->nwav1-1);
	free_dvector(m->amb_coef1, 0, m->nwav1-1);
	free(m);
	free(p);
}

static double Q(double t) {		/* the real trigger's quantisation, :4861-4863 */
	return floor(t/(1.0/1024.0) + 0.5) * (1.0/1024.0);
}

static inst_cal_type needed(munki *p) {
	inst_cal_type n = inst_calt_none;
	munki_imp_get_n_a_cals(p, &n, NULL);
	return n;
}

static void dump_acq(int from) {
	int i;
	for (i = from; i < g_na; i++)
		note("acq %d: %c %-8s mode %d exposure %.10g dark-bank-subtracted %d%s",
		     i, g_acq[i].kind, g_acq[i].tag, g_acq[i].mode, g_acq[i].inttime, g_acq[i].bank_sub,
		     g_acq[i].sensrv < 0 ? "" : g_acq[i].sensrv == MUNKI_OK ? " sens=OK" : " sens=SATURATED");
}

/* Upstream-supported sequence: set the mode, then complete the needed */
/* (dark) calibration exactly as inst_handle_calibrate drives it.       */
static munki *calibrated_display_mode(void) {
	munki *p = new_instrument();
	munkiimp *m = (munkiimp *)p->m;
	munki_state *s;
	inst_cal_type calt = inst_calt_needed;
	inst_cal_cond calc = inst_calc_none;
	inst_calc_id_type idt;
	char id[CALIDLEN];
	int rv;
	probe darks[3] = { P_DARK, P_DARK, P_DARK };

	chk_i("set_mode(mk_emiss_spot_na) returns OK", MUNKI_OK,
	      munki_imp_set_mode(p, mk_emiss_spot_na, inst_mode_emis_spot));
	s = &m->ms[mk_emiss_spot_na];
	g_spos = mk_spos_calib;
	rv = munki_imp_calibrate(p, &calt, &calc, &idt, id);
	chk_i("calibrate(needed, none) asks for setup", MUNKI_CAL_SETUP, rv);
	chk_i("  ...with calc man_cal_smode", inst_calc_man_cal_smode, calc);
	script(3, darks);
	rv = munki_imp_calibrate(p, &calt, &calc, &idt, id);
	chk_i("calibrate(em_dark, man_cal_smode) returns OK", MUNKI_OK, rv);
	chk_i("  ...three dark probes consumed", 3, g_si);
	chk_i("  ...dark_valid", 1, s->dark_valid);
	chk_d("  ...bank 0 measured at inttime", s->inttime, s->dark_data[0]);
	chk_d("  ...bank 1 measured at dark_int_time2", s->dark_int_time2, s->dark_data2[0]);
	chk_d("  ...bank 2 measured at dark_int_time3", s->dark_int_time3, s->dark_data3[0]);
	chk_i("  ...get_n_a_cals: int-time selection needed",
	      inst_calt_emis_int_time, needed(p) & inst_calt_emis_int_time);
	g_spos = mk_spos_surf;
	return p;
}

/* One display integration-time calibration call as dispsup.c:576-590 makes it. */
static int select_inttime(munki *p, const char *label, int n, const probe *ps,
                          inst_cal_type *pcalt) {
	inst_cal_type calt = inst_calt_emis_int_time;
	inst_cal_cond calc = inst_calc_emis_white;
	inst_calc_id_type idt;
	char id[CALIDLEN];
	int rv, from;

	g_na = 0;			/* acquisition log restarts at each selection call */
	from = g_na;
	script(n, ps);
	rv = munki_imp_calibrate(p, &calt, &calc, &idt, id);
	note("%s: calibrate(emis_int_time, emis_white) -> 0x%x, calt 0x%x, probes consumed %d of %d",
	     label, rv, calt, g_si, n);
	dump_acq(from);
	if (pcalt != NULL)
		*pcalt = calt;
	return rv;
}

/* The next supported read (munki_read_sample -> munki_imp_measure). */
static int read_once(munki *p, int n, const probe *ps) {
	ipatch val;
	int rv, from = g_na;
	memset(&val, 0, sizeof(val));
	script(n, ps);
	rv = munki_imp_measure(p, &val, 1, instNoClamp);
	note("read: munki_imp_measure -> 0x%x, probes consumed %d of %d", rv, g_si, n);
	dump_acq(from);
	return rv;
}

static void begin(const char *name, const char *what) {
	g_case = name;
	g_unscripted = g_unexpected = 0;
	g_na = 0;
	fprintf(stderr, "\nCASE %s: %s\n", name, what);
}

static void end_case(void) {
	g_tripwires += g_unscripted + g_unexpected;
	chk_i("no unscripted acquisition, no tripwire", 0, g_unscripted + g_unexpected);
}

/* Checks on the selection state of the tested mode. */
static void expect_sel(munki *p, int bank, int done) {
	munkiimp *m = (munkiimp *)p->m;
	munki_state *s = &m->ms[mk_emiss_spot_na];
	double t[3];
	t[0] = Q(DISP_INTT); t[1] = Q(DISP_INTT2); t[2] = Q(DISP_INTT3);
	chk_i("selected dispswap", bank, s->dispswap);
	chk_i("current dark bank (by allocation identity)", bank, bank_of(p, mk_emiss_spot_na, s->dark_data));
	chk_d("current exposure s->inttime", t[bank], s->inttime);
	chk_d("exposure matches the dark bank's measured exposure", s->inttime, s->dark_data[0]);
	chk_i("done_dintsel", done, (long)s->done_dintsel);
	chk_i("get_n_a_cals reports int-time selection needed",
	      done ? 0 : inst_calt_emis_int_time, needed(p) & inst_calt_emis_int_time);
	chk_i("current mode restored", mk_emiss_spot_na, m->mmode);
}

static void expect_read(munki *p, int rv, int bank) {
	double t[3];
	acq *a = &g_acq[g_na - 1];
	t[0] = Q(DISP_INTT); t[1] = Q(DISP_INTT2); t[2] = Q(DISP_INTT3);
	chk_i("read admitted and completes", MUNKI_OK, rv);
	chk_i("read used dark bank", bank, a->bank_sub);
	chk_d("read exposure", t[bank], a->inttime);
}

/* ------------------------------------------------------------------ */
/* Cases                                                               */

/* Prove that debug arguments are discarded before evaluation, not merely
 * ignored inside a variadic function. This must run for both source variants. */
static void case_logging_observer(void) {
	int evaluated = 0;
	begin("L1", "debug observer does not evaluate variadic arguments");
	a1logd(NULL, 3, "%d", ++evaluated);
	chk_i("debug argument side effect not evaluated", 0, evaluated);
	end_case();
}

/* munki_whitemeasure's outputs on the six covered return paths: the scale is
 * written on success only, which is what the selection must respect. */
static void case_helper_outputs(void) {
	munki *p;
	munkiimp *m;
	munki_state *s;
	double *absraw, optscale, inttime;
	int rv, j;
	struct { const char *name; probe pr; int nummeas; int rc; int w_scale; int w_absraw; int w_inttime; } w[] = {
		{ "W1 success",               P_OK(FITS),     1, MUNKI_OK,                 1, 1, 1 },
		{ "W2 trigger failure",       P_TRIGFAIL,     1, MUNKI_COMS_FAIL,          0, 0, 1 },
		{ "W3 read failure",          P_READFAIL,     1, MUNKI_COMS_FAIL,          0, 0, 1 },
		{ "W4 saturated",             P_SAT,          1, MUNKI_RD_SENSORSATURATED, 0, 0, 1 },
		{ "W5 inconsistent average",  P_INCONS(FITS), 1, MUNKI_RD_WHITEREADINCONS, 0, 1, 1 },
		{ "W6 zero measurements",     P_OK(FITS),     0, MUNKI_INT_ZEROMEASURES,   0, 0, 0 },
	};
	int i;

	begin("W0", "setup for direct munki_whitemeasure output matrix");
	p = calibrated_display_mode();
	m = (munkiimp *)p->m;
	s = &m->ms[mk_emiss_spot_na];
	end_case();

	absraw = dvectorz(-1, m->nraw-1);
	for (i = 0; i < (int)(sizeof(w)/sizeof(w[0])); i++) {
		begin(w[i].name, "munki_whitemeasure(p, absraw, &optscale, n, &inttime, 0, 0.9) outputs");
		for (j = -1; j < m->nraw; j++)
			absraw[j] = -555.0;
		optscale = -777.0;			/* harness canaries, not production variables */
		inttime = 0.7;				/* Q(0.7) != 0.7, so a write is visible */
		script(1, &w[i].pr);
		rv = munki_whitemeasure(p, absraw, &optscale, w[i].nummeas, &inttime, 0, s->targoscale);
		chk_i("return code", w[i].rc, rv);
		chk_i("optscale written", w[i].w_scale, optscale != -777.0);
		if (w[i].w_scale)
			chk_d("optscale value = 0.9 * optsval / maxval", 0.9 * OPTSVAL / FITS, optscale);
		chk_i("absraw written", w[i].w_absraw, absraw[0] != -555.0);
		chk_i("*inttime written (quantised by the real trigger)", w[i].w_inttime, inttime != 0.7);
		chk_i("acquisitions triggered", w[i].nummeas > 0 ? 1 : 0, g_si);
		end_case();
	}
	free_dvector(absraw, -1, m->nraw-1);
}

static void case_valid_controls(void) {
	munki *p;
	int rv;
	inst_cal_type calt;

	begin("V1", "first selection fits at the default exposure");
	p = calibrated_display_mode();
	{ probe ps[] = { P_OK(FITS) };
	  rv = select_inttime(p, "V1", 1, ps, &calt); }
	chk_i("returns OK", MUNKI_OK, rv);
	chk_i("calt remaining", 0, calt);
	expect_sel(p, 0, 1);
	{ probe ps[] = { P_OK(FITS) };
	  rv = read_once(p, 1, ps); }
	expect_read(p, rv, 0);
	end_case();

	begin("V2", "first selection: too bright, first alternate fits");
	p = calibrated_display_mode();
	{ probe ps[] = { P_OK(BRIGHT), P_OK(FITS) };
	  rv = select_inttime(p, "V2", 2, ps, &calt); }
	chk_i("returns OK", MUNKI_OK, rv);
	chk_i("second probe measured at first alternate (bank 1)", 1, g_acq[1].bank_sub);
	expect_sel(p, 1, 1);
	{ probe ps[] = { P_OK(FITS) };
	  rv = read_once(p, 1, ps); }
	expect_read(p, rv, 1);
	end_case();

	begin("V3", "first selection: too bright twice, second alternate");
	p = calibrated_display_mode();
	{ probe ps[] = { P_OK(BRIGHT), P_OK(BRIGHT) };
	  rv = select_inttime(p, "V3", 2, ps, &calt); }
	chk_i("returns OK", MUNKI_OK, rv);
	expect_sel(p, 2, 1);
	end_case();

	begin("V4", "intended saturation fallback: saturated, first alternate fits");
	p = calibrated_display_mode();
	{ probe ps[] = { P_SAT, P_OK(FITS) };
	  rv = select_inttime(p, "V4", 2, ps, &calt); }
	chk_i("returns OK", MUNKI_OK, rv);
	expect_sel(p, 1, 1);
	end_case();

	begin("V5", "read-time saturation fallback after a base selection (munki_imp_measure :2361-2397)");
	p = calibrated_display_mode();
	{ probe ps[] = { P_OK(FITS) };
	  rv = select_inttime(p, "V5", 1, ps, &calt); }
	{ probe ps[] = { P_SAT, P_OK(FITS) };
	  rv = read_once(p, 2, ps); }
	chk_i("read OK after its own switch", MUNKI_OK, rv);
	chk_i("first read attempt at bank 0, saturated", MUNKI_RD_SENSORSATURATED, g_acq[g_na-2].sensrv);
	expect_read(p, rv, 1);
	chk_i("read leaves dispswap 1", 1, ((munkiimp *)p->m)->ms[mk_emiss_spot_na].dispswap);
	end_case();
}

/* Saturating twice returns saturated with the last bank swapped in and no
 * completion. Upstream leaves it so; the repair does not decide that policy. */
static void case_saturation_edge(void) {
	munki *p;
	int rv;
	inst_cal_type calt;

	begin("S1", "first selection saturates at default and at first alternate");
	p = calibrated_display_mode();
	{ probe ps[] = { P_SAT, P_SAT };
	  rv = select_inttime(p, "S1", 2, ps, &calt); }
	chk_i("returns SENSORSATURATED with bank 2 swapped in", MUNKI_RD_SENSORSATURATED, rv);
	chk_i("calt still carries emis_int_time", inst_calt_emis_int_time, calt & inst_calt_emis_int_time);
	expect_sel(p, 2, 0);
	{ probe ps[] = { P_OK(FITS) };
	  rv = read_once(p, 1, ps); }
	expect_read(p, rv, 2);
	end_case();
}

/* Bright at the default exposure, then the probe at the first alternate fails:
 * the error is returned, and the selection stays at the alternate the valid
 * scale chose. The original consults that stale scale again and swaps to the
 * shortest exposure, which the next read then uses. */
static void failure_after_probe(const char *name, const char *what, probe second, int rc) {
	munki *p;
	int rv;
	inst_cal_type calt;

	begin(name, what);
	p = calibrated_display_mode();
	{ probe ps[2];
	  ps[0] = (probe)P_OK(BRIGHT); ps[1] = second;
	  rv = select_inttime(p, name, 2, ps, &calt); }
	chk_i("error from the second probe is returned", rc, rv);
	chk_i("calt still carries emis_int_time", inst_calt_emis_int_time, calt & inst_calt_emis_int_time);
	chk_i("only probe 1 (bank 0) produced a scale", 0, g_acq[0].bank_sub);
	expect_sel(p, 1, 0);
	{ probe ps[] = { P_OK(FITS) };
	  rv = read_once(p, 1, ps); }
	expect_read(p, rv, 1);
	end_case();
}

/* A forced re-selection of a completed selection. On failure it must not stay
 * marked complete, and it must not move past the bank a valid scale chose. */
static void forced_reselect(const char *name, const char *what, int prior_bank,
                            int n, const probe *ps, int rc, int bank_after) {
	munki *p;
	int rv;
	inst_cal_type calt;
	probe pv1[] = { P_OK(FITS) };
	probe pv2[] = { P_OK(BRIGHT), P_OK(FITS) };
	probe pv3[] = { P_OK(BRIGHT), P_OK(BRIGHT) };

	begin(name, what);
	p = calibrated_display_mode();
	if (prior_bank == 0) rv = select_inttime(p, "prior", 1, pv1, NULL);
	else if (prior_bank == 1) rv = select_inttime(p, "prior", 2, pv2, NULL);
	else rv = select_inttime(p, "prior", 2, pv3, NULL);
	chk_i("prior selection succeeded", MUNKI_OK, rv);
	expect_sel(p, prior_bank, 1);
	g_na = 0;
	rv = select_inttime(p, "forced", n, ps, &calt);
	chk_i("forced re-selection return code", rc, rv);
	if (rc == MUNKI_OK) {
		chk_i("calt remaining", 0, calt);
		expect_sel(p, bank_after, 1);
	} else {
		chk_i("calt still carries emis_int_time", inst_calt_emis_int_time,
		      calt & inst_calt_emis_int_time);
		expect_sel(p, bank_after, 0);
		{ probe pr[] = { P_OK(FITS) };
		  rv = read_once(p, 1, pr); }
		expect_read(p, rv, bank_after);
	}
	end_case();
}

static void case_failures(void) {
	failure_after_probe("F2", "first selection: bright at default, trigger fails at first alternate",
	                    (probe)P_TRIGFAIL, MUNKI_COMS_FAIL);
	failure_after_probe("F2r", "first selection: bright at default, read fails at first alternate",
	                    (probe)P_READFAIL, MUNKI_COMS_FAIL);
	failure_after_probe("F2i", "first selection: bright at default, inconsistent white at first alternate",
	                    (probe)P_INCONS(FITS), MUNKI_RD_WHITEREADINCONS);

	{ probe ps[] = { P_OK(BRIGHT), P_OK(FITS) };
	  forced_reselect("F3c", "forced re-selection of a valid bank-1 selection, succeeds (control)",
	                  1, 2, ps, MUNKI_OK, 1); }
	{ probe ps[] = { P_OK(BRIGHT), P_TRIGFAIL };
	  forced_reselect("F3", "forced re-selection of a valid bank-1 selection, fails after one probe",
	                  1, 2, ps, MUNKI_COMS_FAIL, 1); }
	{ probe ps[] = { P_OK(BRIGHT), P_TRIGFAIL };
	  forced_reselect("F4", "forced re-selection of a valid bank-0 selection, fails after one probe",
	                  0, 2, ps, MUNKI_COMS_FAIL, 1); }
	{ probe ps[] = { P_SAT, P_SAT };
	  forced_reselect("F5", "forced re-selection of a valid bank-1 selection, saturates twice",
	                  1, 2, ps, MUNKI_RD_SENSORSATURATED, 2); }
	{ probe ps[] = { P_OK(BRIGHT), P_TRIGFAIL };
	  forced_reselect("F6", "forced re-selection of a valid bank-2 selection, fails after one probe",
	                  2, 2, ps, MUNKI_COMS_FAIL, 1); }
}

/* inst_calt_all with the display-white condition while the current mode (an */
/* adaptive emissive mode) needs a dark calibration: the ap-flag loop visits  */
/* both non-adaptive display modes (:1086-1712) and must restore m->mmode.   */
static void case_all_modes(void) {
	munki *p;
	munkiimp *m;
	int rv;
	inst_cal_type calt;
	inst_cal_cond calc;
	inst_calc_id_type idt;
	char id[CALIDLEN];

	begin("A1", "all-mode pass, both display modes succeed");
	p = new_instrument();
	m = (munkiimp *)p->m;
	munki_imp_set_mode(p, mk_emiss_spot, inst_mode_emis_spot);
	calt = inst_calt_all; calc = inst_calc_emis_white;
	{ probe ps[] = { P_OK(FITS), P_OK(FITS) }; script(2, ps); }
	rv = munki_imp_calibrate(p, &calt, &calc, &idt, id);
	note("calibrate(all, emis_white) -> 0x%x calt 0x%x calc 0x%x", rv, calt, calc);
	dump_acq(0);
	chk_i("returns CAL_SETUP for the remaining dark", MUNKI_CAL_SETUP, rv);
	chk_i("current mode restored to mk_emiss_spot", mk_emiss_spot, m->mmode);
	chk_i("mk_emiss_spot_na done_dintsel", 1, (long)m->ms[mk_emiss_spot_na].done_dintsel);
	chk_i("mk_tele_spot_na done_dintsel", 1, (long)m->ms[mk_tele_spot_na].done_dintsel);
	end_case();

	begin("A2", "all-mode pass, second display mode fails after one probe");
	p = new_instrument();
	m = (munkiimp *)p->m;
	munki_imp_set_mode(p, mk_emiss_spot, inst_mode_emis_spot);
	calt = inst_calt_all; calc = inst_calc_emis_white;
	{ probe ps[] = { P_OK(FITS), P_OK(BRIGHT), P_TRIGFAIL }; script(3, ps); }
	rv = munki_imp_calibrate(p, &calt, &calc, &idt, id);
	note("calibrate(all, emis_white) -> 0x%x calt 0x%x", rv, calt);
	dump_acq(0);
	chk_i("returns the second mode's error", MUNKI_COMS_FAIL, rv);
	chk_i("current mode restored to mk_emiss_spot", mk_emiss_spot, m->mmode);
	chk_i("mk_emiss_spot_na keeps its completed selection", 1, (long)m->ms[mk_emiss_spot_na].done_dintsel);
	chk_i("mk_tele_spot_na not marked done", 0, (long)m->ms[mk_tele_spot_na].done_dintsel);
	chk_i("mk_tele_spot_na stays at the bank its valid scale chose", 1, m->ms[mk_tele_spot_na].dispswap);
	end_case();
}

/* The reflective white calibration (:1500-1560) logs the scale its probe
 * returned. After a failed probe nothing wrote it; the log call must come
 * after the failure is checked, not before. */
static void case_white_log(void) {
	munki *p;
	inst_cal_type calt = inst_calt_ref_white;
	inst_cal_cond calc = inst_calc_man_cal_smode;
	inst_calc_id_type idt;
	char id[CALIDLEN];
	int rv;
	probe ps[] = { P_TRIGFAIL };

	begin("R1", "reflective white calibration: the probe's trigger fails");
	p = new_instrument();
	chk_i("set_mode(mk_refl_spot) returns OK", MUNKI_OK,
	      munki_imp_set_mode(p, mk_refl_spot, inst_mode_ref_spot));
	g_spos = mk_spos_calib;
	g_scale_logged_after_failure = 0;
	script(1, ps);
	rv = munki_imp_calibrate(p, &calt, &calc, &idt, id);
	g_spos = mk_spos_surf;
	chk_i("the probe's error is returned", MUNKI_COMS_FAIL, rv);
	chk_i("one probe consumed after the LED heat", 1, g_si);
	chk_i("LED heated once", 1, g_heats);
	chk_i("scale not logged after the failed probe", 0, g_scale_logged_after_failure);
	chk_i("current mode restored", mk_refl_spot, ((munkiimp *)p->m)->mmode);
	end_case();
}

/* First-probe failures. In the original these consult a scale nothing wrote,
 * so their outcome is not defined; on the repaired text they are. */
static void case_first_probe_failures(void) {
	munki *p;
	int rv;
	inst_cal_type calt;
	probe pv2[] = { P_OK(BRIGHT), P_OK(FITS) };

	begin("IR1", "first selection: first probe's trigger fails");
	p = calibrated_display_mode();
	{ probe ps[] = { P_TRIGFAIL, P_OK(FITS) };
	  rv = select_inttime(p, "IR1", 2, ps, &calt); }
	chk_i("the error is returned", MUNKI_COMS_FAIL, rv);
	chk_i("no second probe", 1, g_si);
	expect_sel(p, 0, 0);
	end_case();

	begin("IR2", "first selection: saturated, then the second probe's trigger fails");
	p = calibrated_display_mode();
	{ probe ps[] = { P_SAT, P_TRIGFAIL };
	  rv = select_inttime(p, "IR2", 2, ps, &calt); }
	chk_i("the error is returned", MUNKI_COMS_FAIL, rv);
	expect_sel(p, 1, 0);		/* the saturation fallback happened; nothing further */
	end_case();

	begin("IR3", "forced re-selection of a valid bank-1 selection: first probe fails");
	p = calibrated_display_mode();
	select_inttime(p, "prior", 2, pv2, NULL);
	{ probe ps[] = { P_TRIGFAIL, P_OK(FITS) };
	  rv = select_inttime(p, "IR3", 2, ps, &calt); }
	chk_i("the error is returned", MUNKI_COMS_FAIL, rv);
	chk_i("no second probe", 1, g_si);
	/* The swap is undone before the first probe, as upstream orders it, so a
	 * failed re-selection stands at the default exposure and reports itself
	 * incomplete, rather than at the previous valid bank. */
	expect_sel(p, 0, 0);
	end_case();
}

int main(int argc, char *argv[]) {
	const char *run = argc > 1 ? argv[1] : "main";

	if (strcmp(run, "main") == 0) {
		case_logging_observer();
		case_helper_outputs();
		case_valid_controls();
		case_saturation_edge();
		case_failures();
		case_all_modes();
		case_white_log();
	} else if (strcmp(run, "repaired-only") == 0) {
		case_first_probe_failures();
	} else {
		return 2;
	}
	printf("{\"checks\": %d, \"failures\": %d, \"failed\": [%s%s%s], \"probes\": %d, "
	       "\"tripwires\": %d, \"saves\": %d}\n",
	       g_checks, g_failures, *g_failed ? "\"" : "", g_failed, *g_failed ? "\"" : "",
	       g_probes, g_tripwires, g_saves);
	return g_failures != 0;
}
