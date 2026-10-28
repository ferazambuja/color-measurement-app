/* Exact calibration and refresh methods, with inert status/black/read hooks.
 * Source enums are in codes.inc; method bodies are in functions.inc. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "codes.inc"
#define CALIDLEN 200
#define MAX_RD_SIZE 100
#define a1logd(...) ((void)0)
typedef struct state {
 int gotcoms, inited, bcal_done, noinitcalib, is2024, usehl, refrmode, refrvalid, rrset;
 double refrate; time_t bdate; void *log;
} state;
typedef state inst;
typedef state kleink10;
typedef state spydX;
typedef state spydX2;
typedef state spyd2;
typedef state dtp92;
static int black_calls, rate_calls, fail, failures, checks;
static inst_code status(inst *p, inst_cal_type *need, inst_cal_type *avail) {
 *need = *avail = p->refrmode ? inst_calt_ref_freq : inst_calt_emis_offset;
 return inst_ok;
}
static inst_code black(state *p) { (void)p; black_calls++; return fail ? inst_coms_fail : inst_ok; }
static inst_code rate(state *p) { p->refrvalid=1; p->refrate=60; rate_calls++; return inst_ok; }
static inst_code command(state *p, char *cmd, char *buf, int n, double t) {
 (void)p; (void)n; (void)t;
 if(strcmp(cmd,"10103RM\r")) abort();
 if(fail) return inst_coms_fail;
 strcpy(buf,"Hz 60.25 "); return inst_ok;
}
#define k10_get_n_a_cals status
#define spydX_get_n_a_cals status
#define spydX2_get_n_a_cals status
#define spyd2_get_n_a_cals status
#define k10_do_black_cal black
#define spydX_BlackCal black
#define spydX2_BlackCal black
#define spyd2_GetRefRate rate
#define dtp92_command command
#include "functions.inc"
#define CHECK(c,msg) do {checks++; if(!(c)) {failures++; printf("FAIL %s\n",msg);}} while(0)
int main(void) {
 inst_code (*cal[])(inst*,inst_cal_type*,inst_cal_cond*,inst_calc_id_type*,char*) = {
  k10_calibrate, spydX_calibrate, spydX2_calibrate };
 size_t i; int f;
 for(i=0;i<3;i++) for(f=0;f<2;f++) {
  state p={0}; inst_cal_type ct=inst_calt_emis_offset; inst_cal_cond cc=inst_calc_none;
  inst_calc_id_type idt=inst_calc_id_none; char id[CALIDLEN]={0}; inst_code rc;
  p.gotcoms=p.inited=1; fail=f; black_calls=0;
  rc=cal[i](&p,&ct,&cc,&idt,id);
  CHECK(rc==inst_cal_setup && ct==inst_calt_emis_offset && black_calls==0,"setup preserves mask");
  CHECK(cc==inst_calc_man_em_dark,"dark condition");
  rc=cal[i](&p,&ct,&cc,&idt,id);
  CHECK(black_calls==1,"one black hook");
  CHECK(rc==(f?inst_coms_fail:inst_ok),"black status preserved");
  CHECK(ct==(f?inst_calt_emis_offset:0),"completed offset removed; failed offset retained");
 }
 {
  state p={0}; inst_cal_type ct=inst_calt_ref_white; inst_cal_cond cc=inst_calc_none;
  inst_calc_id_type idt=inst_calc_id_none; char id[CALIDLEN]={0}; inst_code rc;
  p.gotcoms=p.inited=p.refrmode=1; rate_calls=0; fail=0;
  rc=spyd2_calibrate(&p,&ct,&cc,&idt,id);
  CHECK(rc==inst_unsupported && ct==inst_calt_ref_white && rate_calls==0,"unsupported calibration refused");
  ct=inst_calt_ref_freq; cc=inst_calc_emis_80pc;
  rc=spyd2_calibrate(&p,&ct,&cc,&idt,id);
  CHECK(rc==inst_ok && ct==0 && rate_calls==1,"supported refresh unchanged");
  CHECK(spyd2_set_refr_rate(&p,75)==inst_ok && p.refrvalid && p.refrate==75,"stored rate set");
  CHECK(spyd2_set_refr_rate(&p,0)==inst_ok && !p.refrvalid && p.rrset,"zero invalidates stored rate");
  CHECK(spyd2_set_refr_rate(&p,151)==inst_bad_parameter && !p.refrvalid,"invalid rate preserves state");
 }
 for(f=0;f<2;f++) {
  state p={0}; double hz=999; inst_code rc; p.gotcoms=p.inited=1; fail=f;
  rc=dtp92_get_refr_rate(&p,&hz);
  CHECK(rc==(f?inst_coms_fail:inst_ok),"stored retrieval status");
  CHECK(hz==(f?0:60.25),"stored retrieval writes successful rate");
  CHECK(dtp92_get_refr_rate(&p,NULL)==rc,"optional stored output");
 }
 printf("state: %d checks, %d failures\n",checks,failures);
 return failures != 0;
}
