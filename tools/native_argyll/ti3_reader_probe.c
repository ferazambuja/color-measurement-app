/* Inert CTI3 reader proof. The Python test inserts exact pinned spec2cie
 * dispatch, spectral-grid/type/norm and output-scale blocks at the markers.
 * Only CGATS, spectral mathematics and support units are linked. No Argyll
 * tool main, instrument constructor, enumeration or acquisition is present.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "numsup.h"
#include "cgats.h"
#include "xspect.h"

int main(int argc, char **argv) {
    cgats *icg;
    xspect sp;
    xsp2cie *converter;
    int ii, ti, j, i, spi[XSPECT_MAX_BANDS];
    int isemis = 0, isdisp = 0, isdnormed = -1, fwacomp = 0;
    int oXi = 0, oYi = 1, oZi = 2;
    icxIllumeType illum = icxIT_none, tillum = icxIT_none;
    cgats_set_elem elems[3];
    double XYZ[3], flat_y = 0.0;
    char buf[80];
    if ((argc != 2 && argc != 3) || (icg = new_cgats()) == NULL) return 2;
    icg->add_other(icg, "CTI3");
    if (icg->read_name(icg, argv[1]) != 0) return 3;
    if (icg->ntables != 1 || icg->t[0].nsets < 1) return 4;
    memset(&sp, 0, sizeof(sp));
    /* PINNED_DISPATCH */
    /* PINNED_SPECTRAL_READER */
    if (sp.spec_n < 2 || sp.spec_n > XSPECT_MAX_BANDS) return 5;
    converter = new_xsp2cie(isemis ? tillum : icxIT_D50, 0, NULL,
                            icxOT_CIE_1931_2, NULL, icSigXYZData, icxNoClamp);
    if (converter == NULL) return 6;
    /* Independent flat-density integral over the pinned 1 nm observer table.
     * This does not call the conversion routine or use its input norm. */
    if (converter->observer[1].spec_n != 471 || converter->spec_bw != 1.0) return 7;
    for (j = 0; j < 471; j++) flat_y += converter->observer[1].spec[j];
    flat_y *= 0.683002 / converter->observer[1].norm;
    printf("{\"rows\":%d,\"bands\":%d,\"norm\":%.17g,\"emissive\":%d,"
           "\"flat_mw_y\":%.17g,\"values\":[", icg->t[0].nsets, sp.spec_n,
           sp.norm, isemis, flat_y);
    for (i = 0; i < icg->t[0].nsets; i++) {
        for (j = 0; j < sp.spec_n; j++)
            sp.spec[j] = *((double *)icg->t[0].fdata[i][spi[j]]);
        converter->convert(converter, XYZ, &sp);
        /* PINNED_OUTPUT_SCALE */
        if (i) putchar(',');
        printf("[%.17g,%.17g,%.17g]", elems[0].d, elems[1].d, elems[2].d);
    }
    printf("],\"poly\":[");
    for (i = 0; i < icg->t[0].nsets; i++) {
        double tail = argc == 3 ? strtod(argv[2], NULL) : 0.0;
        for (j = 0; j < XSPECT_MAX_BANDS; j++)
            sp.spec[j] = j < sp.spec_n ? *((double *)icg->t[0].fdata[i][spi[j]]) : tail;
        if (i) putchar(',');
        putchar('[');
        for (j = 0; j < 5; j++) {
            double wl = sp.spec_wl_short + j * (sp.spec_wl_long - sp.spec_wl_short)/4.0;
            if (j) putchar(',');
            printf("%.17g", value_xspect_poly(&sp, wl));
        }
        putchar(']');
    }
    printf("]}\n");
    converter->del(converter);
    icg->del(icg);
    return 0;
}
