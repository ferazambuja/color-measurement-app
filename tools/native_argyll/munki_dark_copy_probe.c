/* Shared-dark regression helpers; appended to the existing inert nonadaptive probe. */
#define obs(...) ((void)0)
static int dark_via_needed(munki *p, mk_mode mode, const char *label) {
	inst_cal_type calt;
	inst_cal_cond calc = inst_calc_none;
	inst_calc_id_type idt;
	char id[CALIDLEN];
	int rv;
	chk_i("set_mode returns OK", MUNKI_OK, munki_imp_set_mode(p, mode, inst_mode_emis_spot));
	calt = needed(p);
	chk_i("needed includes em_dark", inst_calt_em_dark, calt & inst_calt_em_dark);
	g_spos = mk_spos_surf;
	rv = munki_imp_calibrate(p, &calt, &calc, &idt, id);
	chk_i("first request is man_cal_smode", inst_calc_man_cal_smode, calc & inst_calc_cond_mask);
	g_spos = mk_spos_calib;
	g_na = 0;
	{ probe ps[] = { P_DARK, P_DARK, P_DARK }; script(3, ps); }
	rv = munki_imp_calibrate(p, &calt, &calc, &idt, id);
	chk_i("three dark probes consumed", 3, g_si);
	obs("\"%s: dark in mode %d -> rv 0x%x calc 0x%x\"", label, (int)mode, rv, (unsigned)calc);
	g_spos = mk_spos_surf;
	return rv;
}

static void expect_pairing(munki *p, mk_mode mode) {
	munkiimp *m = (munkiimp *)p->m;
	munki_state *s = &m->ms[mode];
	chk_d("current bank recorded at the current exposure", Q(s->inttime), s->dark_data[0]);
	chk_d("bank 2 recorded at dark_int_time2", s->dark_int_time2, s->dark_data2[0]);
	chk_d("bank 3 recorded at dark_int_time3", s->dark_int_time3, s->dark_data3[0]);
}

/* Synthetic transitions through the real ColorMunki calibration state machine.
 * Bank zero records the exposure used by the inert dark acquisition. */
static void age_darks(munki *p) {
    munkiimp *m = (munkiimp *)p->m; int i;
    for (i = 0; i < mk_no_modes; i++) m->ms[i].ddate -= 2 * DCALTOUT;
}
static void select_tier(munki *p, mk_mode mode, int tier) {
    inst_cal_type c; int rv;
    probe ps[2] = { P_OK(FITS), P_OK(FITS) };
    munki_imp_set_mode(p, mode, mode == mk_tele_spot_na ? inst_mode_emis_tele : inst_mode_emis_spot);
    if (tier) ps[0] = (probe)P_OK(BRIGHT);
    if (tier == 2) ps[1] = (probe)P_OK(BRIGHT);
    rv = select_inttime(p, "tier", tier ? 2 : 1, ps, &c);
    chk_i("selection succeeds", MUNKI_OK, rv);
    chk_i("selected tier", tier, ((munkiimp *)p->m)->ms[mode].dispswap);
}
static void pairing_matrix(void) {
    int source, target, rv;
    for (source = 0; source < 3; source++) for (target = 0; target < 3; target++) {
        munki *p; munkiimp *m; munki_state *ss; time_t before;
        char name[40];
        snprintf(name,sizeof(name),"source%d-target%d",source,target);
        begin(name,"selected banks are never overwritten by an unpermuted sibling copy");
        p=new_instrument();m=(munkiimp *)p->m;ss=&m->ms[mk_tele_spot_na];
        dark_via_needed(p,mk_emiss_spot_na,"source first");
        age_darks(p);dark_via_needed(p,mk_tele_spot_na,"target first");
        select_tier(p,mk_tele_spot_na,source);
        age_darks(p);dark_via_needed(p,mk_tele_spot_na,"target selected dark");
        select_tier(p,mk_tele_spot_na,target);
        select_tier(p,mk_emiss_spot_na,source);
        age_darks(p);before=ss->ddate;
        dark_via_needed(p,mk_emiss_spot_na,"source selected dark");
        chk_i("copy only between canonical bank orders",source==0 && target==0,ss->ddate!=before);
        expect_pairing(p,mk_tele_spot_na);
        chk_d("canonical third exposure preserved",Q(DISP_INTT3),
              ss->dispswap==2 ? ss->inttime : ss->dark_int_time3);
        munki_imp_set_mode(p,mk_tele_spot_na,inst_mode_emis_tele);
        chk_i("unshared destination still needs its own expired dark",
              source!=0 || target!=0,!!(needed(p)&inst_calt_em_dark));
        if (needed(p)&inst_calt_em_dark) dark_via_needed(p,mk_tele_spot_na,"target own renewal");
        expect_pairing(p,mk_tele_spot_na);
        chk_d("canonical third exposure preserved",Q(DISP_INTT3),
              ss->dispswap==2 ? ss->inttime : ss->dark_int_time3);
        g_spos=mk_spos_proj;g_na=0;
        {probe ps[]={P_OK(FITS)};rv=read_once(p,1,ps);}
        chk_i("read after required calibration succeeds",MUNKI_OK,rv);
        chk_d("read exposure matches measured dark",ss->dark_data[0],g_acq[g_na-1].inttime);
        end_case();
        delete_instrument(p);
    }
}
static void cold_shared_dark(double tick) {
    munki *p; munkiimp *m; munki_state *ss; int rv;
    begin("cold-sharing", "quantized equal exposures share all three matching banks");
    p=new_instrument();m=(munkiimp *)p->m;m->intclkp=tick;ss=&m->ms[mk_tele_spot_na];
    dark_via_needed(p,mk_emiss_spot_na,"cold source");
    chk_i("cold sibling gets a valid dark",1,ss->dark_valid);
    chk_d("third dark keeps its own exposure",floor(DISP_INTT3/tick+0.5)*tick,ss->dark_int_time3);
    select_tier(p,mk_tele_spot_na,2);
    g_spos=mk_spos_proj;g_na=0;
    {probe ps[]={P_OK(FITS)};rv=read_once(p,1,ps);}
    chk_i("shared cold calibration admits the read",MUNKI_OK,rv);
    if (rv==MUNKI_OK && g_na) chk_d("cold read matches its bank",ss->dark_data[0],g_acq[g_na-1].inttime);
    end_case();
    delete_instrument(p);
}
int main(void) {
    munki *p; munkiimp *m;
    pairing_matrix();
    cold_shared_dark(1.0/1024.0);
    cold_shared_dark(1.0e-6);
    begin("different-clock-count", "nearby exposures remain different hardware integrations");
    p=new_instrument();m=(munkiimp *)p->m;
    m->ms[mk_tele_spot_na].inttime=0.705;
    m->ms[mk_tele_spot_na].dark_int_time=0.705;
    dark_via_needed(p,mk_emiss_spot_na,"different-clock-count");
    chk_i("nearby but different exposure is not shared",0,m->ms[mk_tele_spot_na].dark_valid);
    end_case();
    delete_instrument(p);
    printf("{\"checks\":%d,\"failures\":%d,\"tripwires\":%d}\n",g_checks,g_failures,g_unexpected+g_unscripted);
    return g_failures ? 1 : 0;
}
