/* Inert baseline/corrected calibration probe. Extracted ArgyllCMS 3.5.0
 * functions retain Graeme W. Gill's GPL-2.0-or-later terms. No driver IO. */
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
static const char *scenario;
static int dark_calls,white_calls,factors,saves,optimizer_calls;
static char call_order[64];
static void unexpected(const char *why) { fprintf(stderr,"Unexpected: %s\n",why); abort(); }
static void require(int v,const char *why) { if(!v) unexpected(why); }
static int is(const char *name) {
    const char *value=!strncmp(scenario,"cached_",7) ? scenario+7 : scenario;
    return !strcmp(value,name);
}
static void record(char c) { size_t n=strlen(call_order); require(n+1<64,"trace"); call_order[n]=c; call_order[n+1]=0; }
static i1pro_state *state(i1pro *p) { i1proimp *m=p->m; require(m->mmode==i1p_trans_scan,"mode"); return &m->ms[m->mmode]; }
static int dark_probe(i1pro *p,double *data,int count,double *integration,int gain) {
    i1pro_state *s=state(p); require(data==s->dark_data && count==50
       && integration==&s->inttime && *integration==.01 && gain==0,"dark args");
    ++dark_calls; record('D'); return I1PRO_OK;
}
static int white_probe(i1pro *p,double *a,double *b,double *data,double *scale,
    int count,double *integration,int gain,double target,int lamp) {
    i1pro_state *s=state(p); require(a==s->cal_factor[0] && b==s->cal_factor[1]
       && data==s->white_data && count==100 && integration==&s->inttime
       && *integration==.01 && gain==0 && target==1 && lamp==0 && !white_calls,"white args");
    ++white_calls; record('W');
    if(is("saturated")) return I1PRO_RD_SENSORSATURATED;
    if(is("misread")) return I1PRO_RD_WHITEREADINCONS;
    *scale=1;
    for(int j=0;j<36;++j) a[j]=b[j]=2000;
    a[0]=a[1]=300;
    for(int j=22;j<=27;++j) a[j]=2900;
    return I1PRO_OK;
}
static int cache_probe(i1pro *p) {
    require(state(p)->cal_valid && state(p)->dark_valid,"cache state");
    ++saves; record('S'); return I1PRO_OK;
}
#define a1logd(...) ((void)0)
#define a1loge(...) unexpected("unexpected logged high-resolution error")
#define time(...) ((time_t)1234567)
#define msec_time(...) 0
#define ENABLE_NONVCAL
#define HIGH_RES
#undef USE_HIGH_GAIN_MODE
/* High-resolution dependencies are compiled but must remain unreachable. */
#define standardIlluminant(...) (unexpected("hires illuminant"),1)
#define value_xspect(...) (unexpected("hires spectrum"),0.0)
#define new_rspl(...) (unexpected("hires spline"),(rspl *)NULL)
#define wav_lerp(...) (unexpected("hires interpolation"),0.0)
#define i1pro_create_hr_calfactors(...) (unexpected("hires factors"),I1PRO_INTERNAL_ERROR)
#include "white_factor.inc"
static int factor_probe(i1pro *p,double *a,double *r0,double *w0,
    double *b,double *r1,double *w1,int fine_tune) {
    i1proimp *m=p->m; i1pro_state *s=state(p);
    require(a==s->cal_factor[0] && a==w0 && r0==NULL
       && b==s->cal_factor[1] && b==w1 && r1==NULL
       && fine_tune==0 && m->hr_inited==0,"factor args");
    ++factors; record('F');
    return i1pro_compute_white_cal(p,a,r0,w0,b,r1,w1,fine_tune);
}
#define i1pro_whitemeasure(...) white_probe(__VA_ARGS__)
#define i1pro_dark_measure(...) dark_probe(__VA_ARGS__)
#define i1pro_check_white_reference1(...) (unexpected("reflective white check"),I1PRO_INTERNAL_ERROR)
#define i1pro_compute_white_cal(...) factor_probe(__VA_ARGS__)
#define i1pro_save_calibration(...) cache_probe(__VA_ARGS__)
#define i1pro_optimise_sensor(...) (++optimizer_calls,unexpected("optimizer"),I1PRO_INTERNAL_ERROR)
#define i1pro_update_log(...) (unexpected("EEPROM"),I1PRO_INTERNAL_ERROR)
#define i1pro2_wl_measure(...) (unexpected("wavelength"),I1PRO_INTERNAL_ERROR)
#define i1pro2_match_wl_meas(...) (unexpected("wavelength fit"),I1PRO_INTERNAL_ERROR)
#define i1pro_compute_wav_filters(...) (unexpected("wavelength filters"),I1PRO_INTERNAL_ERROR)
#define i1pro_create_hr(...) (unexpected("hires"),I1PRO_INTERNAL_ERROR)
#define i1pro_interp_dark(...) (unexpected("dark interpolation"),I1PRO_INTERNAL_ERROR)
#define i1pro_prepare_idark(...) unexpected("adaptive dark")
#define dvectorz(...) (unexpected("unexpected extra allocation"),(double *)NULL)
#define plot_raw2(...) unexpected("plot")
#include "status.inc"
#include "measurement_count.inc"
#include "calibrate.inc"
int main(int argc,char **argv) {
    require(argc==3,"usage model case"); scenario=argv[2];
    int cached=!strncmp(scenario,"cached_",7);
    require(!strcmp(argv[1],"i1pro") || !strcmp(argv[1],"i1pro2"),"model");
    require(is("validwhite") || is("saturated") || is("misread"),"case");
    i1pro meter={0}; i1proimp m={0};
    double dark[i1p_no_modes][129]={{0}}, white[129]={0}, factor[2][36]={{0}};
    meter.m=&m; meter.dtype=!strcmp(argv[1],"i1pro") ? instI1Pro : instI1Pro2;
    m.mmode=i1p_trans_scan; m.nraw=128; m.nwav[0]=m.nwav[1]=36;
    m.min_int_time=.01; m.highgain=8; m.serno=-12345;
    for(int i=0;i<i1p_no_modes;++i) {
        m.ms[i].mode=i; m.ms[i].dark_data=dark[i]+1; m.ms[i].dark_int_time=.5;
    }
    i1pro_state *s=&m.ms[i1p_trans_scan];
    s->trans=s->scan=1; s->adaptive=0; s->inttime=s->dark_int_time=.01;
    s->dcaltime=s->wcaltime=1; s->targoscale=.25;
    s->want_calib=!cached; s->cal_valid=s->dark_valid=1;
    s->ddate=1234567; s->cfdate=1234555; s->white_data=white+1;
    s->cal_factor[0]=factor[0]; s->cal_factor[1]=factor[1];
    for(int j=0;j<36;++j) factor[0][j]=factor[1][j]=100;
    inst_cal_type needed,available,remaining; inst_cal_cond condition=inst_calc_none;
    inst_calc_id_type idtype=inst_calc_id_none; char id[CALIDLEN]={0};
    require(i1pro_imp_get_n_a_cals(&meter,&needed,&available)==0
            && needed==(cached ? 0 : 0x1000) && available==0x3000,"initial masks");
    remaining=needed | inst_calt_trans_vwhite;
    int setup=i1pro_imp_calibrate(&meter,&remaining,&condition,&idtype,id);
    require(setup==I1PRO_CAL_SETUP && remaining==0x1000 && condition==0x80
            && idtype==0 && !id[0] && !call_order[0],"setup");
    int code=i1pro_imp_calibrate(&meter,&remaining,&condition,&idtype,id);
    require(i1pro_imp_get_n_a_cals(&meter,&needed,&available)==0,"post query");
    require(white_calls==1 && !dark_calls && !optimizer_calls,"call counts");
    if(is("misread")) require(code==I1PRO_RD_WHITEREADINCONS && remaining==0x1000
            && needed==((CM_PATCHED || !cached) ? 0x1000 : 0) && !factors && !saves && factor[0][0]==100,"misread refusal");
    else if(CM_PATCHED && is("saturated")) require(code==I1PRO_RD_SENSORSATURATED
            && remaining==0x1000 && needed==((CM_PATCHED || !cached) ? 0x1000 : 0) && !factors && !saves
            && factor[0][0]==100 && !strcmp(call_order,"W"),"saturation refused");
    else require(code==0 && !remaining && !needed && factors==1 && saves==1
            && s->cal_valid && !s->want_calib && !strcmp(call_order,"WFS"),"accepted white");
    require(m.mmode==i1p_trans_scan,"restored mode");
    if(code!=I1PRO_OK) {
        require(s->cfdate==1234555 && s->cal_valid==!CM_PATCHED && s->want_calib==!cached,
                "failed reference preserves prior state");
        for(int j=0;j<36;++j) require(factor[0][j]==100,"all old factors unchanged");
    } else require(s->cfdate==1234567,"successful calibration date");
    if(!CM_PATCHED && is("saturated")) for(int j=0;j<36;++j)
        require(isfinite(factor[0][j]) && factor[0][j]==.01,"old factors reciprocated");
    printf("{\"model\":\"%s\",\"case\":\"%s\",\"code\":%d,\"remaining\":%u,"
           "\"needed\":%u,\"cal_valid\":%d,\"want_calib\":%d,\"calls\":\"%s\","
           "\"factor0\":%.17g,\"cache_hooks\":%d,\"calibration_date\":%lld,\"final_mode\":%d}\n",
           argv[1],scenario,code,(unsigned)remaining,(unsigned)needed,
           s->cal_valid,s->want_calib,call_order,factor[0][0],saves,(long long)s->cfdate,m.mmode);
}
