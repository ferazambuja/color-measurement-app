/* Source-extracted ColorMunki scaler and each caller's post-scale tail.
 * Prior processing/allocation is excluded; signed, zero and second-row
 * overflow values exercise the real scaling and publication guard only. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "codes.inc"
typedef int munki_code;
typedef struct { double *cal_factor; } munki_state;
typedef struct { int mmode, nwav; munki_state ms[1]; } munkiimp;
typedef struct { void *m; } munki;
#include "functions.inc"
static int failures;
static void expect(const char *name, int condition) {
    if (!condition) { printf("FAIL %s\n",name); failures++; }
}
static void one(const char *name) {
    double bands[2][2] = {{1,2},{3,4}}, factors[2] = {2,2};
    double *rows[2] = {bands[0],bands[1]};
    munkiimp state;
    munki meter;
    int rc, caller, bad = strstr(name,"overflow") != NULL;
    int row = strstr(name,"second") ? 1 : 0;
    int count = row ? 2 : 1;
    if (strstr(name,"signed")) bands[0][0] = -2;
    if (strstr(name,"zero")) bands[0][0] = 0;
    if (bad) bands[row][0] = 1e308;
    memset(&state,0,sizeof state);
    state.nwav = 2; state.ms[0].cal_factor = factors; meter.m = &state;
    caller = strstr(name,"caller2a") != NULL;
    rc = caller ? source_caller2a(&meter,rows,count) : source_caller2(&meter,rows,count);
    if (bad) expect(name,rc == MUNKI_RD_READINCONS && isinf(bands[row][0]));
    else {
        expect(name,rc == MUNKI_OK && bands[0][0] == (strstr(name,"signed") ? -4 :
                   (strstr(name,"zero") ? 0 : 2)) && bands[0][1] == 4);
        printf("CONTROL %s %a %a\n",name,bands[0][0],bands[0][1]);
    }
}
int main(void) {
    static const char *cases[] = {"caller2-normal","caller2-signed","caller2-zero",
        "caller2a-normal","caller2a-signed","caller2a-zero",
        "caller2-overflow","caller2-overflow-second",
        "caller2a-overflow","caller2a-overflow-second"};
    size_t i;
    for (i = 0; i < sizeof cases / sizeof cases[0]; i++) one(cases[i]);
    printf("%d failures\n",failures);
    return failures != 0;
}
