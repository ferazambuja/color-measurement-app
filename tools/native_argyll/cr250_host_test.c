/* This application's read allowance and reply retention on the CR driver
 * (cr250_host.py), exercised on the transformed driver source over the series'
 * scripted instrument (cr250_fake.c).
 *
 * The allowance is only safe if capture.required_s really is the worst case from
 * M to the last capture read. required_after() restates that path, so the check
 * that matters runs every read with each reply arriving just inside its deadline
 * and requires the virtual time from M to stay within 100 ms below required_s
 * over the whole path, never above it. A read the driver adds after M, or one it
 * drops, fails here instead of letting the allowance fail open or refuse reads
 * it could complete. The bind floor is checked against the same driver: the
 * shortest path each model and mode can take must measure exactly
 * cr250_required_floor_s, and no read may need less.
 *
 * Linked with the persona's sources, as cr_floor.c is: no transport, nothing
 * that opens a port. Prints "cr250_host_test: <n> checks, <f> failures" and exits
 * non-zero on any failure. */
#include "session.h"
#if defined(CM_CONTRIBUTED_instCR250) || defined(CM_CONTRIBUTED_instCR100)
#include "cr250_fake.h"
#include "cr250_support.h"

#define F cr250_fake_state

FILE *wire;   /* session.h's; the persona sources reference none of it */

/* Minimal test envelope; the production support adapter writes its real DTO. */
void cm_event_start(const char *event) { fprintf(wire, "{\"event\":\"%s\"", event); }
void cm_json_string(const char *s) {
	if (!s) { fputs("null", wire); return; }
	fputc('"', wire);
	for (; *s; s++) {
		unsigned char c = (unsigned char)*s;
		if (c == '"' || c == '\\') { fputc('\\', wire); fputc(c, wire); }
		else if (c < 32) fprintf(wire, "\\u%04x", c);
		else fputc(c, wire);
	}
	fputc('"', wire);
}
void cm_cr250_native_facts(inst *meter);

static int checks, failures;
#define CHECK(cond) do { checks++; if (!(cond)) { failures++; \
	printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)

/* Slow mode: every reply after M arrives just inside its deadline. The spectrum
 * header leaves room for the 200 ms pacing note the engine then waits out. */
static int slow;
static unsigned int m_ms;					/* Virtual time M was written */
static int (*fake_read)(icoms *, char *, int, int *, char *, int, double);
static int (*fake_write)(icoms *, char *, int, double);

static int last_request_is(const char *cmd) {
	size_t n = strlen(cmd);
	return F.sent_n >= (int)n + 2 && F.sent[F.sent_n - 1] == '\n'
	    && F.sent[F.sent_n - (int)n - 2] == '\n'
	    && strncmp(F.sent + F.sent_n - (int)n - 1, cmd, n) == 0;
}

static int slow_read(icoms *p, char *buf, int size, int *count, char *tc, int ntc, double tout) {
	if (slow && F.measures > 0 && tout > 0.5) {
		unsigned int ms = (unsigned int)(tout * 1000.0 + 0.5) - 2;
		if (last_request_is("RM Spectrum")) ms -= CR250_SPECTRUM_GUARD_MS + 1;
		cr250_fake_sleep(ms);
	}
	return fake_read(p, buf, size, count, tc, ntc, tout);
}

static int slow_write(icoms *p, char *buf, int nch, double tout) {
	int measures = F.measures, rv = fake_write(p, buf, nch, tout);
	if (F.measures != measures) m_ms = F.clock_ms;
	return rv;
}

/* The persona's construction (cr250_persona.c), identified and ready to read. */
static inst *driver_of(instType dtype, const char *firmware) {
	icoms *icom = calloc(1, sizeof(*icom));
	cr250 *c;
	inst *p;
	if (!icom) abort();
	cr250_fake_reset();
	if (dtype == instCR100) { F.model = "CR-100"; F.type = "1"; }
	if (dtype == instCR300) { F.model = "CR-300"; F.type = "2"; }
	if (firmware) F.firmware = firmware;
	cr250_fake_attach(icom);
	fake_read = icom->read; icom->read = slow_read;
	fake_write = icom->write; icom->write = slow_write;
	if (!(c = new_cr250(icom, dtype))) abort();
	c->engine.now_ms = cr250_fake_now;
	c->engine.sleep_ms = cr250_fake_sleep;
	p = (inst *)c;
	CHECK(p->init_coms(p, baud_38400, fc_nc, 3) == inst_ok);
	CHECK(p->init_inst(p) == inst_ok);
	CHECK(p->get_set_opt(p, inst_opt_trig_prog) == inst_ok);
	slow = 0; m_ms = 0;
	return p;
}
static inst *driver(void) { return driver_of(instCR250, NULL); }

/* RangeMode not applicable on either side: the CR-250's shortest range path. */
static void no_range(void) {
	F.override_cmd = "RS RangeMode"; F.override_value = "NA";
	F.override2_cmd = "RM RangeMode"; F.override2_value = "NA";
}
static void fixed_range(void) {
	F.override_cmd = "RS RangeMode"; F.override_value = "Fixed";
	F.override2_cmd = "RM RangeMode"; F.override2_value = "Fixed";
}

static int reply_index(cr250 *c, const char *context) {
	int i;
	for (i = 0; i < c->capture.count; i++)
		if (strcmp(c->capture.reply[i].context, context) == 0) return i;
	return -1;
}

/* One slow read must take within 100 ms below required_s over the whole path,
 * and never longer than required_s. */
static void worst_case(inst *p, const char *what) {
	cr250 *c = (cr250 *)p;
	ipatch out;
	double elapsed_ms;
	slow = 1;
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.valid);
	slow = 0;
	elapsed_ms = (double)(F.clock_ms - m_ms);
	if (!(elapsed_ms <= c->capture.required_s * 1000.0
	   && elapsed_ms >= c->capture.required_s * 1000.0 - 100.0))
		printf("FAIL worst case %s: %.0f ms from M, required %.3f s\n",
		       what, elapsed_ms, c->capture.required_s);
	CHECK(elapsed_ms <= c->capture.required_s * 1000.0);
	CHECK(elapsed_ms >= c->capture.required_s * 1000.0 - 100.0);
}

static int waits;	/* Polls of 100 ms before the user triggers */
static inst_code slow_trigger(void *ctx, inst_ui_purp purp) {
	(void)ctx;
	if (purp != inst_armed) return inst_ok;
	if (waits > 0) { waits--; return inst_ok; }
	return inst_user_trig;
}

/* Reopening while the user trigger has released the lock identifies the meter,
 * but init_coms deliberately leaves init_inst outstanding. The read must honor
 * its second readiness check before starting a capture. */
static int reconnect_writes;
static inst_code reconnect_trigger(void *ctx, inst_ui_purp purp) {
	inst *p = (inst *)ctx;
	if (purp != inst_armed) return inst_ok;
	CHECK(p->init_coms(p, baud_115200, fc_nc, 3) == inst_ok);
	reconnect_writes = F.writes;
	return inst_user_trig;
}

static void readiness_after_trigger(void) {
	inst *p = driver();
	cr250 *c = (cr250 *)p;
	ipatch out;
	reconnect_writes = -1;
	p->uicallback = reconnect_trigger; p->uic_cntx = p;
	CHECK(p->get_set_opt(p, inst_opt_trig_user) == inst_ok);
	memset(&out, 0x5a, sizeof(out));
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_no_init);
	CHECK(p->gotcoms && !p->inited && c->engine.identified);
	CHECK(reconnect_writes >= 0 && F.writes == reconnect_writes && F.measures == 0);
	CHECK(!out.XYZ_v && out.sp.spec_n == 0);
	CHECK(!c->capture.valid && !c->capture.parsed && c->capture.count == 0 && !c->capturing);
	p->del(p);
}

static void retention_and_required(void) {
	inst *p;
	cr250 *c;
	ipatch out;
	int n, writes;

	/* Each actual transport request contributes exactly one retained header;
	 * M at 120 s plus 21 later reads of 3 s. */
	p = driver(); c = (cr250 *)p;
	writes = F.writes;
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.valid);
	CHECK(c->capture.count == F.writes - writes && c->capture.budget_s == 120 && c->capture.required_s == 183);
	CHECK(!strcmp(c->capture.reply[0].context, "RS CMF"));
	CHECK(reply_index(c, "RC ExposureMode") > reply_index(c, "RS ExposureMode"));
	CHECK(reply_index(c, "RC RangeMode") > reply_index(c, "RS RangeMode"));
	CHECK(reply_index(c, "RC SyncMode") > reply_index(c, "RS SyncMode"));
	CHECK(reply_index(c, "RC Speed") > reply_index(c, "RS Speed"));
	CHECK(reply_index(c, "RC Accessory") > reply_index(c, "RS Accessory"));
	CHECK(reply_index(c, "RC Aperture") > reply_index(c, "RS Aperture"));
	CHECK(reply_index(c, "RC Range") > reply_index(c, "RM Range"));
	CHECK(reply_index(c, "RC Mode") == -1);
	CHECK(reply_index(c, "RC Accessory") >= 0 && c->capture.reply[reply_index(c, "RC Accessory")].complete);
	CHECK(reply_index(c, "RC Aperture") >= 0 && c->capture.reply[reply_index(c, "RC Aperture")].complete);
	CHECK(reply_index(c, "RC Range") >= 0 && c->capture.reply[reply_index(c, "RC Range")].complete);
	CHECK(!strcmp(c->capture.reply[c->capture.count - 1].context, "RM XYZ"));
	/* An option set between reads is not part of the read */
	n = c->capture.count;
	CHECK(p->get_set_opt(p, inst_opt_set_averages, 2) == inst_ok);
	CHECK(cr250_fake_sent("RC MaxExposureX") && c->capture.count == n && !c->capturing);
	p->del(p);

	/* Spectral: the spectrum header is the last reply, and its deadline counts */
	p = driver(); c = (cr250 *)p;
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	writes = F.writes;
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.sp.spec_n > 0);
	CHECK(c->capture.count == F.writes - writes
	   && !strcmp(c->capture.reply[c->capture.count - 1].context, "RM Spectrum"));
	CHECK(c->capture.required_s == 188);
	p->del(p);

	/* Two writes: their SM replies and second reads are retained */
	p = driver(); c = (cr250 *)p;
	cr250_fake_set_setting("CMF", "2"); cr250_fake_set_setting("UserCalibMode", "Match");
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.nwrites == 2);
	CHECK(c->capture.count == 47 && reply_index(c, "RC UserCalibMode") >= 0);
	p->del(p);

	/* Fixed exposure reads no RM MaxAutoExposure; sync adds RM SyncFreq; Fixed
	 * range reads RM Range alone */
	p = driver(); c = (cr250 *)p;
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_emis_nonadaptive) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.required_s == 180);
	p->del(p);
	p = driver(); c = (cr250 *)p;
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_emis_refresh_ovd) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.required_s == 186);
	p->del(p);
	p = driver(); c = (cr250 *)p; fixed_range();
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.required_s == 180);
	p->del(p);

	/* A native warning is retained even though the read stops there */
	p = driver(); c = (cr250 *)p;
	F.native_cmd = "RS CMF"; F.native_status = "OK:100:RS CMF:0";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_mask) == inst_warning && F.measures == 0);
	CHECK(c->capture.count == 1 && c->capture.reply[0].code == 100);
	p->del(p);
}

static void allowance(void) {
	inst *p;
	cr250 *c;
	ipatch out;
	int n;

	/* 150 s fits M but not the 21 reads after it: refused before M, keeping the
	 * budget and the requirement */
	p = driver(); c = (cr250 *)p; c->host_allowance_s = 150;
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_BUDGET_TOO_SHORT);
	CHECK(F.measures == 0 && !cr250_fake_sent("M") && cr250_fake_sent("RC MaxExposure"));
	CHECK(c->capture.budget_s == 120 && c->capture.required_s == 183 && c->capture.count > 0);
	CHECK(c->capture.remaining_s > 149 && c->capture.remaining_s <= 150);
	CHECK(!c->capture.valid && !out.XYZ_v);
	c->host_allowance_s = 1e6;
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && F.measures == 1);
	c->host_allowance_s = 0;
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && F.measures == 2);

	/* Time spent waiting for the user counts against it */
	c->host_allowance_s = 200; waits = 250;
	p->uicallback = slow_trigger; p->uic_cntx = NULL;
	CHECK(p->get_set_opt(p, inst_opt_trig_user) == inst_ok);
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_BUDGET_TOO_SHORT);
	CHECK(F.measures == 2 && waits == 0);
	CHECK(c->capture.remaining_s > 174 && c->capture.remaining_s < 176);
	CHECK(strstr(p->interp_error(p, CR250_BUDGET_TOO_SHORT),
	             "Read allowance remaining ") != NULL);
	CHECK(strstr(p->interp_error(p, CR250_BUDGET_TOO_SHORT), "s is below required 183.000 s; last reply ") != NULL);
	c->host_allowance_s = 200; waits = 100;
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_user_trig && F.measures == 3);
	p->uicallback = NULL;
	CHECK(p->get_set_opt(p, inst_opt_trig_prog) == inst_ok);

	/* A negative or non-finite allowance is refused before any command */
	n = F.writes;
	c->host_allowance_s = -1;
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_bad_parameter && F.writes == n);
	c->host_allowance_s = NAN;
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_bad_parameter && F.writes == n);
	c->host_allowance_s = INFINITY;
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_bad_parameter && F.writes == n);
	CHECK(F.measures == 3);
	p->del(p);

	/* A long budget the host cannot allow: 3 x 5 s x 50 + 5 s = 755 s */
	p = driver(); c = (cr250 *)p;
	cr250_fake_set_setting("ExposureX", "50");
	F.override_cmd = "RC MaxExposure"; F.override_value = "5000.0 msec";
	c->host_allowance_s = 600;
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_BUDGET_TOO_SHORT);
	CHECK(F.measures == 0 && c->capture.budget_s == 755 && c->capture.required_s > 600);
	p->del(p);
}

/* Each floor is what the transformed driver measures on that model's
 * shortest path in that mode, not a restated table. */
static void floor_path(instType dtype, const char *firmware, void (*range)(void),
                       inst_mode mode, const char *what) {
	inst *p = driver_of(dtype, firmware);
	cr250 *c = (cr250 *)p;
	double floor_s = cr250_required_floor_s(dtype, mode);
	range();
	CHECK(p->set_mode(p, mode) == inst_ok);
	worst_case(p, what);
	if (c->capture.required_s != floor_s)
		printf("FAIL floor %s: shortest read requires %.3f s, floor %.3f s\n",
		       what, c->capture.required_s, floor_s);
	CHECK(floor_s > 0 && c->capture.required_s == floor_s);
	p->del(p);
}

static void floors(void) {
	floor_path(instCR250, NULL, no_range, inst_mode_emis_spot, "CR-250 spot");
	floor_path(instCR250, NULL, no_range, inst_mode_emis_tele, "CR-250 telephoto");
	floor_path(instCR250, NULL, no_range, inst_mode_emis_spot | inst_mode_spectral,
	           "CR-250 spectral");
	floor_path(instCR250, NULL, no_range, inst_mode_emis_spot | inst_mode_emis_nonadaptive,
	           "CR-250 Fixed");
	floor_path(instCR250, NULL, no_range,
	           inst_mode_emis_spot | inst_mode_emis_nonadaptive | inst_mode_spectral,
	           "CR-250 Fixed spectral");
	/* The CR-100 row's floor firmware (1.17) precedes RM MaxAutoExposure (1.26),
	 * and its RangeMode is required: Fixed range is its shortest path */
	floor_path(instCR100, "1.20", fixed_range, inst_mode_emis_spot, "CR-100 spot");
	floor_path(instCR100, "1.20", fixed_range, inst_mode_emis_tele, "CR-100 telephoto");
	/* Modes a row refuses, and types without a row, have no floor */
	CHECK(cr250_required_floor_s(instCR100, inst_mode_emis_spot | inst_mode_spectral) == -1);
	CHECK(cr250_required_floor_s(instCR100, inst_mode_emis_spot | inst_mode_emis_nonadaptive) == -1);
	CHECK(cr250_required_floor_s(instKleinK10, inst_mode_emis_spot) == -1);
}

static void worst_cases(void) {
	inst *p;
	/* The ordinary paths each take exactly what they reserve */
	p = driver(); worst_case(p, "CR-250 default"); p->del(p);
	p = driver(); CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	worst_case(p, "CR-250 spectral"); p->del(p);
	p = driver(); cr250_fake_set_setting("UserCalibMode", "Match");
	worst_case(p, "CR-250 with a write"); p->del(p);
	p = driver(); CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_emis_refresh_ovd) == inst_ok);
	worst_case(p, "CR-250 Auto sync"); p->del(p);
	p = driver(); CHECK(p->set_refr_rate(p, 59.94) == inst_ok);
	worst_case(p, "CR-250 Manual sync"); p->del(p);
	p = driver(); fixed_range(); worst_case(p, "CR-250 Fixed range"); p->del(p);
	p = driver_of(instCR100, NULL); worst_case(p, "CR-100 default"); p->del(p);

	/* The longest path: every setting slot filled, both computation keys and
	 * the multiplier written, Manual sync with its frequency written, Fixed
	 * range, a single-entry RC Mode, Fixed exposure and spectral. */
	{
		cr250_setting settings[CR250_MAX_SETUP + 1];
		cr250_model model;
		cr250 *c;
		int i, k = 0;
		p = driver(); c = (cr250 *)p; model = *c->model;
		for (i = 0; c->model->setup[i].key; i++) settings[k++] = c->model->setup[i];
		while (k < CR250_MAX_SETUP) settings[k++] = (cr250_setting){ "Speed", NULL, 1, NULL, 0, 0 };
		settings[k] = (cr250_setting){ NULL, NULL, 0, NULL, 0, 0 };
		model.setup = settings; c->model = &model;
		cr250_fake_set_setting("CMF", "1"); cr250_fake_set_setting("UserCalibMode", "Match");
		cr250_fake_set_setting("RangeMode", "Fixed");
		cr250_fake_set_setting("Mode", "Flicker");
		F.override_cmd = "RC Mode"; F.override_value = "1\r\n0,Flicker";
		CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral | inst_mode_emis_nonadaptive) == inst_ok);
		CHECK(p->get_set_opt(p, inst_opt_set_averages, 5) == inst_ok && p->set_refr_rate(p, 59.94) == inst_ok);
		worst_case(p, "longest path");
		CHECK(c->capture.nwrites == 6 && c->capture.count <= CR250_FACTS_MAX);
		CHECK(reply_index(c, "RC Mode") > reply_index(c, "RS Mode"));
		CHECK(reply_index(c, "RC Mode") >= 0
		      && c->capture.reply[reply_index(c, "RC Mode")].complete);
		CHECK(!strcmp(c->capture.reply[c->capture.count - 1].context, "RM Spectrum"));
		CHECK(c->capture.required_s >= cr250_required_floor_s(instCR250,
		      inst_mode_emis_spot | inst_mode_spectral | inst_mode_emis_nonadaptive));
		p->del(p);
	}
}

/* capture.parsed: which native triplets exist, whatever valid says. What each
 * check catches: a zero or signed radiance taken for "not read"; a malformed or
 * wrong-type triplet, which triplet() may have half written, taken for a value;
 * a refused read's native XYZ lost; one read's flags surviving into the next. */
static void parsed(void) {
	const char *body[] = { "0,0,0", "0,-0.01,-2", "0,0,broken", "1,0,0" };
	ipatch out;
	inst *p;
	cr250 *c;
	int j;
	for (j = 0; j < 4; j++) {
		p = driver(); c = (cr250 *)p;
		F.override_cmd = "RM Radiometric"; F.override_value = body[j];
		if (j < 2) {
			CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.valid);
			CHECK(c->capture.parsed == (CR250_PARSED_XYZ | CR250_PARSED_RADIOMETRIC));
			CHECK(c->capture.radiometric[1] == (j ? -0.01 : 0)
			      && c->capture.radiometric[2] == (j ? -2 : 0));
		} else {
			CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask)
			      == (j == 2 ? CR250_DATA_PARSE_ERROR : CR250_CAPTURE_MISMATCH));
			CHECK(c->capture.parsed == 0 && !c->capture.valid);
		}
		p->del(p);
	}
	/* Native X and Z whose plain sum overflows: the spectrum is refused after
	 * RM XYZ was parsed, which stays; a next read that cannot parse RM XYZ
	 * starts with no flags from this one. */
	p = driver(); c = (cr250 *)p;
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	F.override_cmd = "RM XYZ"; F.override_value = "1e308,25,1e308";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_MISMATCH);
	CHECK(!c->capture.valid && c->capture.xyz[0] == 1e308
	      && c->capture.parsed == (CR250_PARSED_XYZ | CR250_PARSED_RADIOMETRIC));
	F.override_value = "0,0,broken";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_DATA_PARSE_ERROR);
	CHECK(!c->capture.valid && c->capture.parsed == CR250_PARSED_RADIOMETRIC);
	p->del(p);
	/* The CR-100 row reads no RM Radiometric. */
	p = driver_of(instCR100, NULL); c = (cr250 *)p;
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok
	      && c->capture.parsed == CR250_PARSED_XYZ);
	p->del(p);
}

/* The copies cr250_host.py adds for native_facts.h, which the driver's own checks
 * use without keeping: each write with its value before, target and after, the
 * exposure and Auto limit used, the RC record each listed setting matched, and
 * whether the reading was published. */
static void retained_copies(void) {
	inst *p = driver();
	cr250 *c = (cr250 *)p;
	ipatch out;
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.valid && c->capture.nwrites == 0);
	CHECK(c->capture.matched[9].id == 0 && !strcmp(c->capture.matched[9].type, "Radiance"));
	CHECK(!strcmp(c->capture.matched[10].name, "1 deg") && !strcmp(c->capture.matched[7].name, "Normal"));
	CHECK(c->capture.exposure_ms > 111.6 && c->capture.exposure_ms < 111.7);
	CHECK(c->capture.max_auto_exposure_ms > 449.9);
	/* Written, read back and confirmed; then in place, so none */
	cr250_fake_set_setting("UserCalibMode", "Match"); cr250_fake_set_setting("CMF", "2");
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.nwrites == 2);
	CHECK(!strcmp(c->capture.write[0].key, "CMF") && !strcmp(c->capture.write[0].before, "2"));
	CHECK(!strcmp(c->capture.write[1].before, "Match") && !strcmp(c->capture.write[1].target, "None"));
	CHECK(!strcmp(c->capture.write[1].after, "None") && c->capture.write[1].confirmed);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.nwrites == 0);
	/* Kept by the instrument: after shows the value it kept */
	F.sm_ignored = 1; cr250_fake_set_setting("UserCalibMode", "Match");
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_mask) == inst_wrong_setup && !c->capture.valid);
	CHECK(c->capture.nwrites == 1 && !c->capture.write[0].confirmed && !strcmp(c->capture.write[0].after, "Match"));
	/* Refused: the attempt stays on record with no after */
	F.sm_ignored = 0; cr250_fake_set_setting("UserCalibMode", "Match");
	F.native_cmd = "SM UserCalibMode 0"; F.native_status = "ER:-560:Invalid User Calibration Mode:0";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_mask) == inst_bad_parameter);
	CHECK(c->capture.nwrites == 1 && c->capture.write[0].after[0] == 0 && !c->capture.write[0].confirmed);
	F.native_cmd = NULL; cr250_fake_set_setting("UserCalibMode", "None");
	/* A refresh rate writes SyncMode, then SyncFreq */
	CHECK(p->set_refr_rate(p, 59.94) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.nwrites == 2);
	CHECK(!strcmp(c->capture.write[1].key, "SyncFreq") && c->capture.write[1].confirmed);
	CHECK(!strcmp(c->capture.write[1].before, "60.00 Hz") && !strcmp(c->capture.write[1].after, "59.94 Hz"));
	p->del(p);
	/* Fixed exposure: the exposure used, and no Auto limit read */
	p = driver(); c = (cr250 *)p;
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_emis_nonadaptive) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
	CHECK(c->capture.exposure_ms == 100 && c->capture.max_auto_exposure_ms == 0);
	p->del(p);
	/* A model that does not report its Auto limit */
	p = driver(); c = (cr250 *)p;
	F.override_cmd = "RM MaxAutoExposure"; F.override_value = "ER:-500:Invalid command:MaxAutoExposure";
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.max_auto_exposure_ms == 0);
	F.override_cmd = NULL;
	p->del(p);
	/* A rejected inventory keeps its header, but never exposes a wrong-type or
	 * duplicate record as a successful match. */
	p = driver(); c = (cr250 *)p;
	F.override_cmd = "RC Accessory";
	F.override_value = "1\r\n0,Standard,Irradiance";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_mask) == inst_wrong_setup);
	CHECK(c->capture.matched[9].name[0] == '\0' && reply_index(c, "RC Accessory") >= 0);
	F.override_value = "2\r\n0,Standard,Radiance\r\n1,Standard,Radiance";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_mask) == inst_wrong_setup);
	CHECK(c->capture.matched[9].name[0] == '\0' && reply_index(c, "RC Accessory") >= 0);
	p->del(p);
}

/* Composition must preserve refusal facts without reporting a planned record
 * as a match or an unattempted setting as a write. */
static void prewrite(void) {
	static const instType types[] = { instCR250, instCR100 };
	inst *p;
	cr250 *c;
	ipatch out;
	size_t t;
	int i;
	for (t = 0; t < sizeof(types)/sizeof(types[0]); t++) {
		p = driver_of(types[t], NULL); c = (cr250 *)p;
		CHECK(p->set_refr_rate(p, 60) == inst_ok);
		cr250_fake_set_setting("UserCalibMode", "Match");
		F.override_cmd = "RC MinExposureX"; F.override_value = "0";
		CHECK(p->read_sample(p, "", &out, instNoClamp) != inst_ok);
		CHECK(!cr250_fake_sent_prefix("SM ") && F.measures == 0 && c->capture.nwrites == 0);
		CHECK(reply_index(c, "RC MinExposureX") >= 0 && !c->capture.valid && !out.XYZ_v);
		for (i = 0; c->model->setup[i].key; i++)
			if (!strcmp(c->model->setup[i].key, "SyncMode"))
				CHECK(c->capture.matched[i].name[0] == 0 && !strcmp(c->capture.value[i], "None"));
		F.override_cmd = NULL;
		CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && F.measures == 1);
		p->del(p);

		p = driver_of(types[t], NULL); c = (cr250 *)p;
		cr250_fake_set_setting("SyncFreq", "0 Hz");
		CHECK(p->set_refr_rate(p, 60) == inst_ok);
		CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.nwrites == 2);
		CHECK(!strcmp(c->capture.write[1].before, "0 Hz") && c->capture.write[1].confirmed);
		CHECK(!strcmp(c->capture.write[1].target, "60.00") && !strcmp(c->capture.write[1].after, "60.00 Hz"));
		p->del(p);
	}
	/* 190 s covers the 183 s capture path, but cannot reserve the two planned
	 * SM/RS transactions (12 s). Refuse before either persistent setting write. */
	p = driver(); c = (cr250 *)p;
	cr250_fake_set_setting("CMF", "2"); cr250_fake_set_setting("UserCalibMode", "Match");
	c->host_allowance_s = 190;
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_BUDGET_TOO_SHORT);
	CHECK(!cr250_fake_sent_prefix("SM ") && F.measures == 0 && c->capture.nwrites == 0);
	CHECK(c->capture.required_s == 183 && c->capture.remaining_s > 189 && c->capture.remaining_s <= 190);
	CHECK(strstr(p->interp_error(p, CR250_BUDGET_TOO_SHORT), "plus 12.000 s for setting writes/readbacks") != NULL);
	c->host_allowance_s = 200;
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && c->capture.nwrites == 2);
	p->del(p);
}

/* The current-instance getter copies verified state without a device command.
 * A stale identity string is never enough to qualify another initialized state. */
static void current_identity(void) {
    const instType types[] = {instCR250, instCR100, instCR300};
    int i;
    for (i = 0; i < 3; i++) {
        inst *p = driver_of(types[i], NULL);
        cr250 *c = (cr250 *)p;
        cr250_identity id;
        int sent = F.sent_n;
        CHECK(cm_cr250_identity_snapshot(p, &id));
        CHECK(!strcmp(id.model, i == 0 ? "CR-250" : i == 1 ? "CR-100" : "CR-300"));
        CHECK(id.instrument_type == (i == 1 ? 1 : 2));
        CHECK(id.instrument_type != (int)p->dtype && F.sent_n == sent);
        id.model[0] = 'X';
        CHECK(cm_cr250_identity_snapshot(p, &id) && id.model[0] == 'C');
        p->inited = 0;
        CHECK(!cm_cr250_identity_snapshot(p, &id) && id.model[0] == 0 && id.serial[0] == 0);
        p->inited = 1; p->gotcoms = 0;
        CHECK(!cm_cr250_identity_snapshot(p, &id) && id.model[0] == 0);
        p->gotcoms = 1; c->engine.identified = 0;
        CHECK(!cm_cr250_identity_snapshot(p, &id) && id.model[0] == 0);
        c->engine.identified = 1; c->engine.uncertain = 1;
        CHECK(!cm_cr250_identity_snapshot(p, &id) && id.model[0] == 0);
        c->engine.uncertain = 0;
        CHECK(cm_cr250_identity_snapshot(p, &id) && F.sent_n == sent);
        CHECK(amutex_trylock(c->lock) == 0); amutex_unlock(c->lock);
        CHECK(!cm_cr250_identity_snapshot(p, NULL));
        CHECK(!cm_cr250_identity_snapshot(NULL, &id) && id.model[0] == 0);
        p->del(p);
    }
    {
        inst foreign;
        cr250_identity id;
        memset(&foreign, 0, sizeof(foreign)); foreign.dtype = instFakeDisp;
        memset(&id, 0x7f, sizeof(id));
        CHECK(!cm_cr250_identity_snapshot(&foreign, &id) && id.model[0] == 0);
    }
}

/* Bind the producer's publication claim to the final ipatch, while retaining
 * native bins and an inferred model independently of final publication. */
static void unit_publication(void) {
	inst *p = driver();
	cr250 *c = (cr250 *)p;
	ipatch out;
	CHECK(p->set_mode(p, inst_mode_emis_spot | inst_mode_spectral) == inst_ok);
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok);
	CHECK(c->capture.valid && c->capture.unit.verdict == CR250_UNIT_CONSISTENT);
	CHECK(c->capture.published_spectrum_count == out.sp.spec_n
	   && out.sp.spec_n == c->capture.spectrum.count && out.sp.spec_n > 0);
	CHECK(out.sp.spec[5] == 1000.0 * c->capture.spectrum.value[5]);
	cm_cr250_native_facts(p); /* consistent, published */
	c->capture.valid = 0;
	cm_cr250_native_facts(p); /* inference survives a later publication failure */
	c->capture.valid = 1; c->capture.published_spectrum_count = 0;
	cm_cr250_native_facts(p); /* valid alone cannot establish spectral output */
	c->capture.published_spectrum_count = c->capture.spectrum.count - 1;
	cm_cr250_native_facts(p); /* a different output count cannot qualify publication */
	F.sp_level = 0.001; cr250_fake_spectrum();
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_UNVERIFIED);
	CHECK(!out.XYZ_v && !out.sp.spec_n && !c->capture.valid
	   && !c->capture.published_spectrum_count);
	CHECK(c->capture.unit.verdict == CR250_UNIT_INCONCLUSIVE
	   && c->capture.spectrum.count == CR250_FAKE_SP_N);
	cm_cr250_native_facts(p); /* bright-to-dark freshness and original dark bins */
	F.sp_level = 1; F.sp_scale = 1000; cr250_fake_spectrum();
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_imask) == CR250_SPECTRUM_MISMATCH);
	CHECK(c->capture.unit.verdict == CR250_UNIT_MISMATCH
	   && c->capture.spectrum.count == CR250_FAKE_SP_N && !c->capture.published_spectrum_count);
	cm_cr250_native_facts(p);
	F.sp_scale = 1; F.sp_fault = "cancel";
	CHECK((p->read_sample(p, "", &out, instNoClamp) & inst_mask) != inst_ok);
	CHECK(c->capture.spectrum.count == 20 && c->capture.unit.verdict == CR250_UNIT_MISMATCH);
	cm_cr250_native_facts(p); /* original signed bins remain available after overflow refusal */
	c->capture.unit.verdict = CR250_UNIT_NOT_CHECKED;
	cm_cr250_native_facts(p); /* an unchecked retained observation cannot claim a unit */
	p->del(p);
	p = driver(); c = (cr250 *)p;
	F.sp_level = 0; cr250_fake_spectrum();
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.XYZ_v && out.XYZ[1] == 0);
	CHECK(!out.sp.spec_n && !c->capture.published_spectrum_count);
	cm_cr250_native_facts(p); /* explicit zero XYZ-only remains valid */
	p->del(p);
	p = driver_of(instCR100, NULL); c = (cr250 *)p;
	CHECK(p->read_sample(p, "", &out, instNoClamp) == inst_ok && out.XYZ_v);
	CHECK(!out.sp.spec_n && !c->capture.spectrum.count && !c->capture.published_spectrum_count);
	cm_cr250_native_facts(p);
	p->del(p);
}

static void host_checks(void) {
	current_identity();
	unit_publication();
	setvbuf(stdout, NULL, _IONBF, 0);
	retention_and_required();
	retained_copies();
	prewrite();
	parsed();
	readiness_after_trigger();
	allowance();
	floors();
	worst_cases();
}

static FILE *cr250_facts_output(void) {
	const char *output = getenv("CR250_FACTS_OUTPUT");
	FILE *result = output ? fopen(output, "w") : tmpfile();
	if (!result) perror("CR host-test facts output");
	return result;
}

int main(int argc, char **argv) {
	(void)argc; (void)argv;
	wire = cr250_facts_output();
	if (!wire) return 2;
	host_checks();
	fclose(wire);
	printf("cr250_host_test: %d checks, %d failures\n", checks, failures);
	return failures != 0;
}
#else
int main(void) { return 2; }
#endif
