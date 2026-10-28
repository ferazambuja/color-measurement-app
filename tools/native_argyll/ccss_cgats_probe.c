/* Read one .ccss file through ccss.read_ccss and print what the reader kept.
 *
 * Compiled by native_argyll/ccss_cgats.py against the pinned ArgyllCMS 3.5.0
 * reader, pristine and repaired. The installed-file search is not under test:
 * xdg_bds and xdg_free are stand-ins that list nothing, so no directory is read
 * and list_iccss has nothing to open. Nothing here can reach a port or device.
 */
#include <stdio.h>
#include <stdlib.h>
#include "numsup.h"
#include "cgats.h"
#include "xspect.h"
#include "disptechs.h"
#include "ccss.h"
#include "xdg_bds.h"

int xdg_bds(xdg_error *er, char ***paths, xdg_storage_type st, xdg_op_type op,
            xdg_scope sc, xdg_options opt, char *spath) {
    (void)st; (void)op; (void)sc; (void)opt; (void)spath;
    if (er != NULL) *er = xdg_ok;
    *paths = NULL;
    return 0;
}

void xdg_free(char **paths, int nopaths) {
    (void)paths; (void)nopaths;
}

/* JSON string body for a message the reader composed; the probe's own inputs
 * keep it printable, so only quote and backslash need escaping. */
static void string(const char *text) {
    putchar('"');
    for (; *text; text++) {
        if (*text == '"' || *text == '\\') putchar('\\');
        putchar(*text);
    }
    putchar('"');
}

/* A nonfinite value is a JSON string naming it, never a bare token. */
static void number(double value) {
    if (value - value == 0.0) printf("%.17g", value);
    else printf("\"%s\"", value != value ? "nan" : value > 0 ? "inf" : "-inf");
}

int main(int argc, char **argv) {
    ccss *cs;
    int rv, j;
    if (argc != 2 || (cs = new_ccss()) == NULL) return 2;
    rv = cs->read_ccss(cs, argv[1]);
    printf("{\"rc\":%d,\"message\":", rv);
    string(rv ? cs->e.m : "");
    if (rv == 0) {
        printf(",\"samples\":%d,\"bands\":%d,\"norm\":", cs->no_samp, cs->samples[0].spec_n);
        number(cs->samples[0].norm);
        fputs(",\"first\":[", stdout);
        for (j = 0; j < cs->samples[0].spec_n; j++) {
            if (j) putchar(',');
            number(cs->samples[0].spec[j]);
        }
        putchar(']');
    }
    printf("}\n");
    cs->del(cs);
    return 0;
}
