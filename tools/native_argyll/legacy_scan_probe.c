/* Synthetic raw arrays exercise the authenticated Argyll 3.5.0 recognizer.
 * The generated recognizer.inc retains Graeme W. Gill's GPL-2.0-or-later code.
 * No instrument runtime or acquisition hooks are linked. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <fenv.h>
#include "sa_config.h"
#include "numsup.h"
#include "rspl1.h"
#include "cgats.h"
#include "xspect.h"
#include "insttypes.h"
#include "conv.h"
#include "icoms.h"
#include "usbio.h"
#include "inst.h"
#include "i1pro.h"
#include "i1pro_imp.h"
#define PATCH_CONS_THR 0.1
#include "recognizer.inc"

static const char *scenario;
static int is(const char *s) { return strcmp(scenario, s)==0; }
static double plateau(int segment, int count) {
    if (segment==0) return is("zero_leaders")||is("zero_leader") ? 0.0 : 1000.0;
    if (segment==count+1) return is("leader_mismatch") ? 400.0
        : count==1 ? 900.0
        : is("zero_leaders")||is("zero_trailer") ? 0.0 : 1000.0;
    int patch = is("reverse3") ? count-segment : segment-1;
    if (count==32) return patch%2 ? 600.0+patch : 200.0+patch;
    const double values[]={200.0,600.0,350.0};
    return values[patch];
}
static void number(double x) {
    if(isnan(x)) printf("\"NaN\"");
    else if(!isfinite(x)) printf(x>0 ? "\"Infinity\"" : "\"-Infinity\"");
    else printf("%.17g",x);
}
int main(int argc, char **argv) {
    if(argc<2 || argc>3) return 2;
    scenario=argv[1];
    int count=is("valid32") ? 32 : is("valid1") ? 1 : is("valid2") ? 2 : 3;
    int requested=is("count_low") ? 2 : is("count_high") ? 4 : count;
    int interior=!strncmp(scenario,"sparse_interior",15) ? atoi(scenario+15) : 0;
    int width=interior ? 100 : !strncmp(scenario,"sparse",6) ? atoi(scenario+6) : 100;
    int ramp=width<100 ? 2 : 20;
    if(width<4) return 3;
    if(interior) ramp=2;
    int rows=interior ? 2*width+count*interior : (count+2)*width;
    i1pro meter={0}; i1proimp implementation={0};
    meter.m=&implementation; meter.log=g_log;
    if(argc==3) meter.log->debug=atoi(argv[2]);
    implementation.nraw=128; implementation.scan_toll_ratio=1.0;
    implementation.mmode=i1p_emiss_scan;
    implementation.ms[i1p_emiss_scan].emiss=1;
    implementation.ms[i1p_emiss_scan].scan=1;
    implementation.ms[i1p_emiss_scan].adaptive=1;
    double **input=dmatrixz(0,rows-1,-1,127);
    double **out=dmatrix(0,requested-1,-1,127);
    for(int p=0;p<requested;p++) for(int j=-1;j<128;j++) out[p][j]=-1234567.0;
    int nonfinite_input=0;
    for(int i=0;i<rows;i++) for(int j=0;j<128;j++) {
        int segment=i/width, within=i%width;
        int segment_width=width;
        if(interior && i>=width) {
            int offset=i-width;
            if(offset<count*interior){segment=1+offset/interior;within=offset%interior;segment_width=interior;}
            else {segment=count+1;within=offset-count*interior;}
        }
        double v=plateau(segment,count);
        if(segment<count+1 && within>=segment_width-ramp)
            v+=(plateau(segment+1,count)-v)*(within-segment_width+ramp)/(double)ramp;
        if(v!=0.0) v += .1*(1000.0-v)*sin(j*.1);
        /* Constant per-plateau spectra; transition ramps are not patch truth. */
        if(is("flat")) v=300.0;
        if(is("zero")) v=0.0;
        if(is("negative")) v=-v;
        if((is("sat9")||is("sat10")) && j>=1 && j<=(is("sat9")?9:10)) v=12000.0;
        if((is("nan0")&&j==0)||(is("nan20")&&j==20)||(is("nan50")&&j==50)) v=NAN;
        if(is("inf20")&&j==20) v=INFINITY;
        if(is("neginf20")&&j==20) v=-INFINITY;
        if(is("nan_leader")&&segment==0) v=NAN;
        if(is("inf_leader")&&segment==0) v=INFINITY;
        if(is("nan_all")) v=NAN;
        if(is("inf_all")) v=INFINITY;
        input[i][j]=v; nonfinite_input+=!isfinite(v);
    }
    int flags=-999;
    double highest=-1234567.0;
    feclearexcept(FE_ALL_EXCEPT);
    int code=i1pro_extract_patches_multimeas(&meter,&flags,out,requested,input,rows,&highest,10000.0,.01);
    int fp=fetestexcept(FE_INVALID|FE_DIVBYZERO|FE_OVERFLOW);
    int nonfinite_output=0, changed=0;
    for(int p=0;p<requested;p++) for(int j=0;j<128;j++) {
        nonfinite_output+=!isfinite(out[p][j]); changed+=out[p][j]!=-1234567.0;
    }
    printf("{\"case\":\"%s\",\"code\":%d,\"flags\":%d,\"requested\":%d,\"rows\":%d,"
           "\"nonfinite_input\":%d,\"nonfinite_output\":%d,\"changed_values\":%d,"
           "\"fp_exception_mask\":%d,\"highest\":",scenario,code,flags,requested,rows,
           nonfinite_input,nonfinite_output,changed,fp);
    number(highest); printf(",\"channel50\":[");
    for(int p=0;p<requested;p++){if(p)putchar(',');number(out[p][50]);}
    printf("],\"raw_patch_averages\":[");
    for(int p=0;p<requested;p++){if(p)putchar(',');putchar('[');for(int j=0;j<128;j++){if(j)putchar(',');number(out[p][j]);}putchar(']');}
    printf("]}\n");
    free_dmatrix(input,0,rows-1,-1,127); free_dmatrix(out,0,requested-1,-1,127);
    return 0;
}
