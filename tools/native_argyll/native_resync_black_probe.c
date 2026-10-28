/* Inert curve capture around the source-extracted i1pro_sub_absraw routine.
 * Values are normalized raw sensor values, not optical measurements. The
 * drift fixture's independent rational adjusted curve is 3219/323,3292/323;
 * the unchanged quadratic linearisation is checked separately. */
#include <stdio.h>
#include <string.h>
#include <math.h>
#include "config.inc"
#define instI1Pro2 1
#define a1logd(...) ((void)0)
typedef struct { double highgain, *lin0, *lin1; int nlin0, nlin1, nraw; } i1proimp;
typedef struct { i1proimp *m; int dtype; void *log; } i1pro;
static int calls, failures, adjusted_errors, drifting;
static int equal(double a, double b) { return isfinite(a) && fabs(a-b) <= 1e-12; }
static void plot_msg(const char *text) { (void)text; }
static int do_plot6(double *x, double *in, double *dark, double *adjusted,
                    double *result, double *y5, double *y6, int count) {
    int j;
    double expected[2][2] = {{20,40},{30,50}};
    double golden[2] = {3219.0/323.0,3292.0/323.0};
    (void)y5; (void)y6;
    if (calls >= 2 || count != 2) { failures++; return 0; }
    for (j = 0; j < count; j++) {
        double want = drifting ? golden[j] : j+1;
        if (!equal(x[j],j) || !equal(in[j],expected[calls][j])
            || !equal(dark[j],j+1) || !equal(result[j],expected[calls][j]-want)) failures++;
        if (!equal(adjusted[j],want)) { adjusted_errors++; failures++; }
        if (drifting && equal(adjusted[j],dark[j])) failures++;
    }
    calls++;
    return 0;
}
#include "caller.inc"
int main(int argc, char **argv) {
    double poly[3] = {1,2,0.1}, samples[2][3] = {{10,20,40},{10,30,50}}, dark[3] = {10,1,2};
    double neutral[2][2] = {{110.7,365.3},{226.7,557.3}};
    double drift[2][2] = {{40.70456632384093,237.8200624946084},
                         {120.84078923405764,397.05226063702327}};
    double *rows[2] = {samples[0]+1,samples[1]+1};
    int i,j;
    i1proimp state;
    i1pro meter;
    if (argc != 2) return 2;
    if (!strcmp(argv[1],"drift")) { drifting = 1; samples[0][0] = samples[1][0] = 12; }
    else if (strcmp(argv[1],"neutral")) return 2;
    memset(&state,0,sizeof state);
    state.highgain = 4; state.lin0 = state.lin1 = poly;
    state.nlin0 = state.nlin1 = 3; state.nraw = 2;
    meter.m = &state; meter.dtype = instI1Pro2; meter.log = NULL;
    i1pro_sub_absraw(&meter,2,0.5,1,rows,dark+1);
    for (i = 0; i < 2; i++)
        for (j = 0; j < 2; j++)
            if (!equal(rows[i][j],drifting ? drift[i][j] : neutral[i][j])) failures++;
#ifdef PLOT_BLACK_SUBTRACT
    if (calls != 2) failures++;
#else
    if (calls != 0) failures++;
#endif
    printf("{\"plot_calls\":%d,\"failures\":%d,\"adjusted_errors\":%d,"
           "\"first\":%.17g,\"last\":%.17g}\n",
           calls,failures,adjusted_errors,samples[0][1],samples[1][2]);
    return failures ? 1 : 0;
}
