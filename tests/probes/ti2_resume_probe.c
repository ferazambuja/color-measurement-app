/* Inert target/resume proof. Tests insert exact pinned chartread.c XYZ type
 * checks and location join. Only CGATS parsing/support units are linked.
 * This is not chartread's main and cannot construct or enumerate instruments.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "numsup.h"
#include "cgats.h"

typedef struct { char *loc; double XYZ[3]; int rr; } patch;

static void hex(const char *s) {
    for (; *s; ++s) printf("%02x", (unsigned char)*s);
}

int main(int argc, char **argv) {
    cgats *icg, *rcg;
    patch *cols;
    int i, j, lix, si, nrpat, runpat, cieix[3], islab = 0;
    char *fname[2][3] = {{"XYZ_X", "XYZ_Y", "XYZ_Z"}, {"LAB_L", "LAB_A", "LAB_B"}};
    int channels[4], output_channels[4], nchan;
    const char *names[4] = {"RGB_R", "RGB_G", "RGB_B", NULL};
    const char *rep;
    const char *outname = argc == 3 ? argv[2] : "";
    if (argc != 3) return 2;
    icg = new_cgats(); rcg = new_cgats();
    icg->add_other(icg, "CTI2"); rcg->add_other(rcg, "CTI3");
    if (icg->read_name(icg, argv[1]) || rcg->read_name(rcg, argv[2])) return 3;
    if (icg->ntables != 1 || rcg->ntables != 1) return 4;
    runpat = icg->t[0].nsets;
    if (runpat < 1 || runpat > 140) return 5;
    si = icg->find_field(icg, 0, "SAMPLE_ID");
    i = icg->find_field(icg, 0, "SAMPLE_LOC");
    if (si < 0 || i < 0 || icg->t[0].ftype[si] != nqcs_t
        || icg->t[0].ftype[i] != cs_t) return 6;
    cols = calloc(runpat, sizeof(*cols));
    if (!cols) return 7;
    for (j = 0; j < runpat; j++) cols[j].loc = icg->t[0].fdata[j][i];
    j = icg->find_kword(icg, 0, "COLOR_REP");
    if (j < 0) return 8;
    rep = icg->t[0].kdata[j]; nchan = 3;
    {
        char expected[32];
        int index = rcg->find_kword(rcg, 0, "COLOR_REP");
        int kind = rcg->find_kword(rcg, 0, "DEVICE_CLASS");
        snprintf(expected, sizeof(expected), "%s_XYZ", rep);
        if (index < 0 || kind < 0 || strcmp(rcg->t[0].kdata[index], expected)
            || strcmp(rcg->t[0].kdata[kind], "OUTPUT")) return 14;
    }
    if (!strcmp(rep, "CMYK")) {
        names[0] = "CMYK_C"; names[1] = "CMYK_M";
        names[2] = "CMYK_Y"; names[3] = "CMYK_K"; nchan = 4;
    } else if (strcmp(rep, "RGB") && strcmp(rep, "iRGB")) return 9;
    for (j = 0; j < nchan; j++) {
        channels[j] = icg->find_field(icg, 0, names[j]);
        output_channels[j] = rcg->find_field(rcg, 0, names[j]);
        if (channels[j] < 0 || output_channels[j] < 0
            || icg->t[0].ftype[channels[j]] != r_t
            || rcg->t[0].ftype[output_channels[j]] != r_t) return 10;
    }
    /* Independently check the device channel/ID pairs the resume join ignores. */
    {
        int li = rcg->find_field(rcg, 0, "SAMPLE_LOC");
        int ri = rcg->find_field(rcg, 0, "SAMPLE_ID");
        if (li < 0 || ri < 0) return 11;
        for (i = 0; i < rcg->t[0].nsets; i++) {
            int k;
            for (k = 0; k < runpat; k++)
                if (!strcmp(cols[k].loc, rcg->t[0].fdata[i][li])) break;
            if (k == runpat || strcmp(icg->t[0].fdata[k][si], rcg->t[0].fdata[i][ri])) return 12;
            for (j = 0; j < nchan; j++)
                if (*((double *)icg->t[0].fdata[k][channels[j]])
                    != *((double *)rcg->t[0].fdata[i][output_channels[j]])) return 13;
        }
    }
    /* PINNED_XYZ_TYPES */
    /* PINNED_LOCATION_JOIN */
    for (i = 0; i < runpat; i++) {
        printf("%d %d %.17g %.17g %.17g ", i+1, cols[i].rr,
               cols[i].XYZ[0], cols[i].XYZ[1], cols[i].XYZ[2]);
        hex(icg->t[0].fdata[i][si]); printf(" "); hex(cols[i].loc);
        for (j = 0; j < nchan; j++) printf(" %.17g", *((double *)icg->t[0].fdata[i][channels[j]]));
        printf("\n");
    }
    free(cols); rcg->del(rcg); icg->del(icg);
    return 0;
}
