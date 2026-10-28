/* Inert control-flow harness. ArgyllCMS 3.5.0 fragments are extracted from the
 * authenticated accepted source; the reflective path is unchanged. They retain
 * Graeme W. Gill's GPL-2.0-or-later terms, not new measurement algorithms.
 * No instrument, USB, cache, EEPROM, spectral or white-plausibility runtime links. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <math.h>
#include "sa_config.h"
#include "numsup.h"
#include "rspl1.h"
#include "cgats.h"
#include "xspect.h"
#include "insttypes.h"
#include "conv.h"
#include "icoms.h"
#include "inst.h"
#include "i1pro.h"
#include "i1pro_imp.h"
#include "definitions.inc"

static int dark_calls, white_calls, white_checks, factors, saves, optimizer_calls;
static double scale_input;
static int saturation;
static char call_order[64];
static void unexpected(const char *why) {
    fprintf(stderr,"Unexpected path or failed assertion: %s\n",why); abort();
}
static void require(int value,const char *why) { if(!value) unexpected(why); }
static void record(char code) {
    size_t n=strlen(call_order); require(n+1<sizeof(call_order),"trace overflow");
    call_order[n]=code; call_order[n+1]=0;
}
static i1pro_state *state(i1pro *p) {
    i1proimp *m=(i1proimp *)p->m;
    require(m->mmode==i1p_refl_scan,"current mode");
    return &m->ms[m->mmode];
}
static int dark_probe(i1pro *p,double *data,int count,double *integration,int gain) {
    i1pro_state *s=state(p);
    require(data==s->dark_data && count==50 && integration==&s->inttime
            && *integration==.01 && gain==0,"dark arguments");
    ++dark_calls; record('D'); return I1PRO_OK;
}
static int white_probe(i1pro *p,double *a,double *b,double *data,double *scale,
                       int count,double *integration,int gain,double target,int lamp) {
    i1pro_state *s=state(p);
    require(a==s->cal_factor[0] && b==s->cal_factor[1] && data==s->white_data
            && count==250 && integration==&s->inttime && *integration==.01
            && gain==0 && target==1.0 && lamp==0 && white_calls==0,"white arguments");
    ++white_calls; record('W');
    if(saturation) return I1PRO_RD_SENSORSATURATED; /* scale deliberately unwritten */
    *scale=scale_input;
    for(int i=0;i<36;++i) a[i]=b[i]=2000.0;
    return I1PRO_OK;
}
static int white_check(i1pro *p,double *white) {
    require(white==state(p)->cal_factor[0],"white check arguments");
    ++white_checks; record('C'); return I1PRO_OK;
}
static int factor_probe(i1pro *p,double *a,double *r0,double *w0,
                        double *b,double *r1,double *w1,int highres_fine_tune) {
    i1proimp *m=(i1proimp *)p->m; i1pro_state *s=state(p);
    require(a==s->cal_factor[0] && a==w0 && r0==m->white_ref[0]
            && b==s->cal_factor[1] && b==w1 && r1==m->white_ref[1]
            && highres_fine_tune==0,"factor arguments");
    ++factors; record('F'); return I1PRO_OK;
}
static int cache_probe(i1pro *p) {
    require(state(p)->cal_valid && state(p)->dark_valid,"cache boundary state");
    ++saves; record('S'); return I1PRO_OK;
}
#define a1logd(...) ((void)0)
#define time(...) ((time_t)1234567)
#define msec_time(...) 0
#define ENABLE_NONVCAL
#define HIGH_RES
#undef USE_HIGH_GAIN_MODE
#define i1pro_whitemeasure(...) white_probe(__VA_ARGS__)
#define i1pro_dark_measure(...) dark_probe(__VA_ARGS__)
#define i1pro_check_white_reference1(...) white_check(__VA_ARGS__)
#define i1pro_compute_white_cal(...) factor_probe(__VA_ARGS__)
#define i1pro_save_calibration(...) cache_probe(__VA_ARGS__)
#define i1pro_optimise_sensor(...) (++optimizer_calls, unexpected("optimizer"), I1PRO_INTERNAL_ERROR)
#define i1pro_update_log(...) (unexpected("EEPROM log"), I1PRO_INTERNAL_ERROR)
#define i1pro2_wl_measure(...) (unexpected("wavelength measurement"), I1PRO_INTERNAL_ERROR)
#define i1pro2_match_wl_meas(...) (unexpected("wavelength matching"), I1PRO_INTERNAL_ERROR)
#define i1pro_compute_wav_filters(...) (unexpected("wavelength filters"), I1PRO_INTERNAL_ERROR)
#define i1pro_create_hr(...) (unexpected("high resolution"), I1PRO_INTERNAL_ERROR)
#define i1pro_interp_dark(...) (unexpected("interpolated dark"), I1PRO_INTERNAL_ERROR)
#define i1pro_prepare_idark(...) unexpected("adaptive dark preparation")
#define dvectorz(...) (unexpected("unexpected buffer allocation"), (double *)NULL)
#define free_dvector(...) unexpected("unexpected buffer free")
#define plot_raw2(...) unexpected("plot")
#include "status.inc"
#include "measurement_count.inc"
#include "calibrate.inc"

int main(int argc,char **argv) {
    require(argc==3,"usage: probe i1pro|i1pro2 ordinary|saturation|below6|at6|above6");
    instType dtype=!strcmp(argv[1],"i1pro") ? instI1Pro : instI1Pro2;
    require(!strcmp(argv[1],"i1pro") || !strcmp(argv[1],"i1pro2"),"model name");
    const char *name=argv[2]; int warning=!strcmp(name,"above6");
    saturation=!strcmp(name,"saturation");
    require(saturation || warning || !strcmp(name,"ordinary") || !strcmp(name,"below6")
            || !strcmp(name,"at6"),"case name");
    scale_input=warning ? 6.1 : !strcmp(name,"below6") ? 5.9 : !strcmp(name,"at6") ? 6.0 : 1.0;
    i1pro meter={0}; i1proimp implementation={0};
    double dark[i1p_no_modes][129]={{0}}, white[129]={0}, factor[2][36]={{0}}, reference_white[2][36]={{0}};
    meter.m=&implementation; meter.dtype=dtype;
    implementation.mmode=i1p_refl_scan; implementation.nraw=128;
    implementation.nwav[0]=implementation.nwav[1]=36;
    implementation.min_int_time=.01; implementation.highgain=8.0;
    implementation.serno=-12345; /* synthetic signed serial, no observed device */
    for(int i=0;i<i1p_no_modes;++i) {
        implementation.ms[i].mode=i;
        implementation.ms[i].dark_data=dark[i]+1;
        implementation.ms[i].dark_int_time=.5; /* no coincident other-mode dark state */
    }
    for(int r=0;r<2;++r) {
        implementation.white_ref[r]=reference_white[r];
        for(int j=0;j<36;++j) reference_white[r][j]=.9;
    }
    i1pro_state *s=&implementation.ms[i1p_refl_scan];
    s->reflective=s->scan=s->adaptive=1; s->inttime=s->dark_int_time=.01;
    s->lamptime=.5; s->dcaltime=.5; s->wcaltime=2.5; s->targoscale=.25;
    s->want_calib=s->want_dcalib=1; s->white_data=white+1;
    s->cal_factor[0]=factor[0]; s->cal_factor[1]=factor[1];
    inst_cal_type needed,available,remaining;
    inst_cal_cond condition=inst_calc_none; inst_calc_id_type idtype=inst_calc_id_none;
    char id[CALIDLEN]={0}, setup_reference[CALIDLEN]={0};
    require(i1pro_imp_get_n_a_cals(&meter,&needed,&available)==I1PRO_OK
            && needed==0x60 && available==0x60,"initial masks");
    remaining=needed;
    int setup=i1pro_imp_calibrate(&meter,&remaining,&condition,&idtype,id);
    require(setup==I1PRO_CAL_SETUP && remaining==0x60 && condition==0x10
            && idtype==1 && !strcmp(id,"-12345") && !call_order[0],"setup result");
    strcpy(setup_reference,id);
    int code=i1pro_imp_calibrate(&meter,&remaining,&condition,&idtype,id);
    require(i1pro_imp_get_n_a_cals(&meter,&needed,&available)==I1PRO_OK,"post status");
    require(implementation.mmode==i1p_refl_scan && s->gainmode==0 && s->inttime==.01
            && optimizer_calls==0 && white_calls==1 && s->dark_valid && !s->want_dcalib
            && implementation.transwarn==0 && available==0x60,"common outcome");
    if(saturation) {
        require(code==I1PRO_RD_SENSORSATURATED && remaining==0x20 && needed==0x20
                && !s->cal_valid && s->want_calib && dark_calls==1 && !factors
                && !white_checks && !saves && !strcmp(call_order,"DW")
                && condition==0x10 && idtype==1 && !strcmp(id,"-12345"),"saturation outcome");
    } else {
        require(code==I1PRO_OK && !remaining && !needed && s->cal_valid && !s->want_calib
                && dark_calls==2 && white_checks==1 && factors==1 && saves==1
                && !strcmp(call_order,"DWDCFS"),"successful call sequence/masks");
        if(warning) require(condition==0x20000 && idtype==0x10000
                && !strcmp(id,"Warning: Transmission light source is too low for accuracy!"),"warning metadata");
        else require(condition==0x10 && idtype==1 && !strcmp(id,"-12345"),"ordinary reference retained");
    }
    printf("{\"model\":\"%s\",\"dtype\":%d,\"case\":\"%s\",\"scale_supplied\":",argv[1],dtype,name);
    if(saturation) printf("null"); else printf("%.17g",scale_input);
    printf(",\"setup_code\":%d,\"setup_remaining\":96,\"setup_condition\":16,\"setup_idtype\":1,"
           "\"setup_reference\":\"%s\",\"return_code\":%d,\"remaining\":%u,\"queried_needed\":%u,"
           "\"queried_available\":%u,\"condition\":%u,\"idtype\":%u,\"reference_or_message\":\"%s\","
           "\"call_order\":\"%s\",\"white_calls\":%d,\"dark_calls\":%d,\"white_checks\":%d,"
           "\"factor_hooks\":%d,\"inert_cache_save_hooks\":%d,\"optimizer_calls\":%d,"
           "\"final_mode\":%d,\"gain\":%d,\"integration_seconds\":%.17g,\"dark_valid\":%d,"
           "\"cal_valid\":%d,\"want_dark\":%d,\"want_white\":%d,\"transwarn\":%d}\n",
           setup,setup_reference,code,(unsigned)remaining,(unsigned)needed,(unsigned)available,
           (unsigned)condition,(unsigned)idtype,id,call_order,white_calls,dark_calls,white_checks,
           factors,saves,optimizer_calls,implementation.mmode,s->gainmode,s->inttime,s->dark_valid,
           s->cal_valid,s->want_dcalib,s->want_calib,implementation.transwarn);
    return 0;
}
