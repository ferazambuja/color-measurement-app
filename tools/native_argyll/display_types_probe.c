/* Inert proof of the production catalogue code. No transport is linked. */
#define CM_DISPLAY_IMPLEMENTATION
#include <stdio.h>
#include <math.h>
#include "numsup.h"
#include "cgats.h"
#include "xspect.h"
#include "insttypes.h"
#include "conv.h"
#include "icoms.h"
#include "inst.h"
#include "display_types.h"

/* The rejected limit+1 row is allocated too, so a removed limit check fails
 * the semantic assertion instead of passing a mutation through a sanitizer. */
static inst_disptypesel rows[CM_DISPLAY_LIMIT+1];
static int count=3, calls=0, failures=0;
static inst_code answer=inst_ok;
static inst_code get_rows(inst *p,int *n,inst_disptypesel **list,int all,int recreate) {
    (void)p; calls++;
    if (all!=1 || recreate!=1) failures++;
    *n=count; *list=rows; return answer;
}
static inst_code partial_readback(inst *p,disptech *technology,int *refresh,int *base) {
    (void)p; (void)refresh; *technology=disptech_unknown; *base=0; return inst_ok;
}
static void check(int good) { if (!good) failures++; }
/* Each edit of a copied row must move (or, for what selection itself writes,
 * keep) the identity of the unedited row. */
#define IDENTITY(row, edit, moves) do { inst_disptypesel copy=(row); char a[65], b[65]; \
    if (!cm_display_identity(&(row),0,a)) { \
        check(0); fprintf(stderr,"Invalid identity baseline: %s\n",#row); break; } \
    edit; \
    if (!cm_display_identity(&copy,0,b) || (strcmp(a,b)!=0)!=(moves)) { \
        check(0); fprintf(stderr,"Display identity mismatch: %s\n",#edit); } } while (0)
static void every_identity_field(void) {
    xspect sets[2], changed[2]; inst_disptypesel matrix, samples, bank;
    memset(&matrix,0,sizeof(matrix)); memset(sets,0,sizeof(sets));
    strcpy(matrix.desc,"Installed"); matrix.flags=inst_dtflags_ccmx; matrix.cbid=0;
    matrix.dtech=disptech_lcd_ccfl; matrix.refr=0; matrix.ix=4; matrix.path="/one.ccmx";
    matrix.cc_cbid=1; for (int i=0; i<3; i++) matrix.mat[i][i]=1;
    IDENTITY(matrix, strcpy(copy.desc,"Installed 2"), 1);
    IDENTITY(matrix, copy.flags|=inst_dtflags_custom, 1);
    IDENTITY(matrix, copy.flags|=inst_dtflags_default, 1);
    IDENTITY(matrix, copy.flags|=inst_dtflags_ld, 0);
    IDENTITY(matrix, copy.cbid=2, 1);
    IDENTITY(matrix, copy.dtech=disptech_lcd_ccfl_ips, 1);
    IDENTITY(matrix, copy.refr=1, 1);
    IDENTITY(matrix, copy.ix=5, 1);
    IDENTITY(matrix, copy.path="/two.ccmx", 1);
    IDENTITY(matrix, copy.cc_cbid=2, 1);
    for (int i=0; i<3; i++) for (int j=0; j<3; j++)
        IDENTITY(matrix, copy.mat[i][j]+=1e-9, 1);
    /* spydX.c:1264-1266, spydX2.c:1449-1451 and kleink10.c:724-802 write an
     * instrument-bank row's matrix and ld flag. Applied identity must survive. */
    bank=matrix; bank.flags=inst_dtflags_mtx; bank.path=NULL;
    IDENTITY(bank, copy.flags|=inst_dtflags_ld, 0);
    for (int i=0; i<3; i++) for (int j=0; j<3; j++)
        IDENTITY(bank, copy.mat[i][j]+=1, 0);
    for (int k=0; k<2; k++) {
        sets[k].spec_n=3; sets[k].spec_wl_short=400; sets[k].spec_wl_long=600; sets[k].norm=1;
        for (int j=0; j<3; j++) sets[k].spec[j]=1+j+k;
    }
    samples=matrix; samples.flags=inst_dtflags_ccss; samples.path="/one.ccss";
    samples.sets=sets; samples.no_sets=2;
#define SETS(edit) (memcpy(changed,sets,sizeof(sets)), copy.sets=changed, edit)
    IDENTITY(samples, copy.no_sets=1, 1);
    for (int k=0; k<2; k++) {
        IDENTITY(samples, SETS(changed[k].spec_wl_short=401), 1);
        IDENTITY(samples, SETS(changed[k].spec_wl_long=601), 1);
        IDENTITY(samples, SETS((changed[k].spec_n=4, changed[k].spec[3]=0)), 1);
        IDENTITY(samples, SETS(changed[k].norm=2), 1);
        for (int j=0; j<sets[k].spec_n; j++)
            IDENTITY(samples, SETS(changed[k].spec[j]+=0.5), 1);
    }
#undef SETS
    {   /* Field boundaries: without the length prefix, index 1 named "2x" would
         * hash exactly as index 12 named "x". */
        inst_disptypesel first=matrix, second=matrix; char a[65], b[65];
        strcpy(first.desc,"2x"); strcpy(second.desc,"x");
        if (!cm_display_identity(&first,1,a) || !cm_display_identity(&second,12,b)
            || !strcmp(a,b)) {
            check(0); fprintf(stderr,"Display identity field boundary lost\n");
        }
    }
}
static void start_event(const char *event) { printf("{\"event\":\"%s\"",event); }
static void write_string(const char *text) {
    if (!text) fputs("null",stdout); else printf("\"%s\"",text);
}
int main(void) {
    inst meter; cm_display_catalogue catalogue;
    char baseline[65], changed[65], abc[65]; SHA256_CTX hash;
    xspect spectrum;
    memset(&meter,0,sizeof(meter)); memset(rows,0,sizeof(rows));
    memset(&spectrum,0,sizeof(spectrum));
    meter.dtype=instI1Disp3; meter.get_disptypesel=get_rows;
    meter.get_disptechi=partial_readback;
    for (int i=0; i<=CM_DISPLAY_LIMIT; i++) {
        strcpy(rows[i].desc,"Duplicate native name"); rows[i].cbid=1;
    }
    SHA256_Init(&hash); SHA256_Update(&hash,(const uint8_t *)"abc",3);
    cm_display_hash_finish(&hash,abc);
    check(!strcmp(abc,"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));
    cm_collect_display_types(&meter,inst2_none,&catalogue);
    check(calls==0 && catalogue.code==inst_unsupported);
    cm_collect_display_types(&meter,inst2_disptype,&catalogue);
    check(catalogue.code==inst_ok && catalogue.count==3 && calls==1);
    every_identity_field();
    {   /* The catalogue identity binds the instrument type and the entry order. */
        char first[65];
        rows[1].cbid=2; cm_collect_display_types(&meter,inst2_disptype,&catalogue);
        strcpy(first,catalogue.digest);
        meter.dtype=instSpyder5; cm_collect_display_types(&meter,inst2_disptype,&catalogue);
        check(catalogue.code==inst_ok && strcmp(first,catalogue.digest)!=0);
        meter.dtype=instI1Disp3; rows[0].cbid=2; rows[1].cbid=1;
        cm_collect_display_types(&meter,inst2_disptype,&catalogue);
        check(catalogue.code==inst_ok && strcmp(first,catalogue.digest)!=0);
        rows[0].cbid=1;
        /* Installed files come last (inst.c:950-1035); a change there must move it. */
        cm_collect_display_types(&meter,inst2_disptype,&catalogue);
        strcpy(first,catalogue.digest); rows[count-1].cbid=3;
        cm_collect_display_types(&meter,inst2_disptype,&catalogue);
        check(catalogue.code==inst_ok && strcmp(first,catalogue.digest)!=0);
        rows[count-1].cbid=1;
    }
    cm_display_identity(&rows[0],0,baseline); cm_display_identity(&rows[1],1,changed);
    check(strcmp(baseline,changed)!=0);
    rows[0].flags=inst_dtflags_ld;
    cm_display_identity(&rows[0],0,changed); check(!strcmp(baseline,changed));
    rows[0].flags=inst_dtflags_ccmx; rows[0].cc_cbid=1; rows[0].mat[0][0]=1;
    cm_display_identity(&rows[0],0,baseline);
    rows[0].mat[2][2]=1.00001;
    cm_display_identity(&rows[0],0,changed); check(strcmp(baseline,changed)!=0);
    rows[0].mat[2][2]=NAN; check(!cm_display_identity(&rows[0],0,changed));
    rows[0].flags=inst_dtflags_ccss; rows[0].sets=&spectrum; rows[0].no_sets=1;
    spectrum.spec_n=3; spectrum.spec_wl_short=400; spectrum.spec_wl_long=600;
    spectrum.norm=1; spectrum.spec[0]=1; spectrum.spec[1]=2; spectrum.spec[2]=3;
    cm_display_identity(&rows[0],0,baseline);
    spectrum.spec[2]=3.00001; cm_display_identity(&rows[0],0,changed);
    check(strcmp(baseline,changed)!=0);
    spectrum.norm=2; cm_display_identity(&rows[0],0,baseline);
    check(strcmp(baseline,changed)!=0);
    spectrum.spec_n=XSPECT_MAX_BANDS+1;
    check(!cm_display_identity(&rows[0],0,changed));
    rows[0].flags=0; rows[0].path="/first.ccmx";
    cm_display_identity(&rows[0],0,baseline); rows[0].path="/second.ccmx";
    cm_display_identity(&rows[0],0,changed); check(strcmp(baseline,changed)!=0);
    rows[0].path=NULL;
    strcpy(rows[0].desc,"\xc0\xaf");
    check(cm_display_identity(&rows[0],0,changed));
    check(cm_display_text_reason(&rows[0])!=NULL);
    strcpy(rows[0].desc,"Calibration caf\xc3\xa9");
    rows[0].path="/tmp/caf\xc3\xa9.ccmx";
    /* Bad text disables only its original row; exact native bytes still bind
     * every catalogue entry, so even a change to an unavailable row is stale. */
    rows[1].flags=inst_dtflags_ccmx;
    strcpy(rows[1].desc,"Invalid \xc0\xaf");
    count=3; cm_collect_display_types(&meter,inst2_disptype,&catalogue);
    check(catalogue.code==inst_ok && catalogue.count==3);
    strcpy(baseline,catalogue.digest);
    rows[1].desc[8]=(char)0xae;
    cm_collect_display_types(&meter,inst2_disptype,&catalogue);
    check(catalogue.code==inst_ok && strcmp(baseline,catalogue.digest)!=0);
    strcpy(rows[1].desc,"Duplicate native name");
    rows[1].path="/tmp/invalid\xff.ccmx";
    cm_collect_display_types(&meter,inst2_disptype,&catalogue);
    check(catalogue.code==inst_ok && catalogue.count==3);
    check(cm_display_text_reason(&rows[0])==NULL && cm_display_text_reason(&rows[1])!=NULL
          && cm_display_text_reason(&rows[2])==NULL);
    rows[1].flags=0; rows[1].path=NULL;
    count=CM_DISPLAY_LIMIT+1; cm_collect_display_types(&meter,inst2_disptype,&catalogue);
    check(catalogue.code!=inst_ok && catalogue.count==0);
    answer=inst_coms_fail; count=3;
    cm_collect_display_types(&meter,inst2_disptype,&catalogue);
    check(catalogue.code==inst_coms_fail && catalogue.count==0);
    answer=inst_ok; count=CM_DISPLAY_LIMIT;
    strcpy(rows[1].desc,"Invalid \xc0\xaf");
    cm_collect_display_types(&meter,inst2_disptype,&catalogue);
    check(catalogue.code==inst_ok && catalogue.count==CM_DISPLAY_LIMIT);
    /* A default marker is metadata, not a unique selected index. */
    rows[2].flags|=inst_dtflags_default;
    rows[count-1].flags|=inst_dtflags_default;
    cm_collect_display_types(&meter,inst2_disptype,&catalogue);
    cm_emit_display_types(stdout,&catalogue,start_event,write_string);
    start_event("summary");
    cm_print_display_types(stdout,&catalogue,&meter,-1,write_string);
    printf(",\"failures\":%d}\n",failures);
    return failures ? 1 : 0;
}
