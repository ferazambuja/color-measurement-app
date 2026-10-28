/* Facts absent from Argyll 3.5.0's public capability words. These describe the
 * pinned implementations, not hardware validation. Keep them at the native
 * boundary and publish them; clients must not maintain a second identity table. */
#ifndef CM_DRIVER_POLICY_H
#define CM_DRIVER_POLICY_H
static int cm_ambient_ccmx(const inst *meter) {
    /* kleink10.c:1408 applies CCMX unconditionally; :1423-1427 chooses ambient
     * units from its scale. i1disp.c:1274, huey.c:839-863, spyd2.c:3105-3127,
     * i1d3.c:2336-2360, spydX.c:829-840 and spydX2.c:901-917 bypass it in
     * ambient. ColorHug has no advertised ambient route (colorhug.c:868-871). */
    return meter->dtype == instKleinK10;
}
/* kleink10.c:1422-1425 labels EVERY reading by the matrix it applied, whatever
 * the mode: ambient when the diagonal's mean exceeds 5.0 -- an illuminance-scaled
 * matrix -- and emission otherwise. A Klein reading is therefore of its route
 * only when the matrix agrees with it. The threshold is published with the
 * binding (cm_print_matrix_threshold), so a client states and checks the
 * requirement before an instrument is opened, from this one definition. */
#define CM_AMBIENT_MATRIX_MEAN 5.0
static int cm_matrix_sets_type(const inst *meter) {
    return meter->dtype == instKleinK10;
}
static int cm_matrix_labels_ambient(const double matrix[3][3]) {
    return (matrix[0][0] + matrix[1][1] + matrix[2][2]) / 3.0 > CM_AMBIENT_MATRIX_MEAN;
}
static void cm_print_matrix_threshold(FILE *wire, const inst *meter) {
    if (cm_matrix_sets_type(meter))
        fprintf(wire, ",\"ambient_matrix_threshold\":%.17g", CM_AMBIENT_MATRIX_MEAN);
    else
        fputs(",\"ambient_matrix_threshold\":null", wire);
}
/* Drivers whose inst_opt_noinitcalib waives ONLY the calibration they ask for at
 * every open, so a calibration they restored from their own file serves until
 * their own checks expire it. Each ORs the waived want with a validity test that
 * the flag does not touch: i1pro_imp.c:1596-1630, i1pro3_imp.c:1083-1125 (after
 * i1pro3_check_calib's time and temperature limits, :1023-1057), munki_imp.c:
 * 957-985, ex1.c:695-702, smcube.c:580-587, spydX.c:917-925. With losecs 0 --
 * spotread -N's value, spotread.c:1538-1540 -- each setter sets the flag
 * unconditionally (i1pro3_imp.c:4916-4924). The types are those inst.c constructs
 * with these six handlers (inst.c:696-704, :717-718, :722-723, :727-728).
 *
 * NOT ss.c, whose handler also accepts losecs 0: there the flag masks the need
 * itself, with no validity term (ss.c:1333-1349), and nothing is restored, so the
 * white would simply never be measured. NOT spydX2.c (SpyderX2, Spyder 2024)
 * either: it reads the flag only under ENABLE_BLACK_CAL, which it leaves
 * undefined (:68, :1001-1004), so asking would waive nothing and the record would
 * claim a waiver. Every other public driver leaves the option to inst.c's
 * default, inst_unsupported. */
static int cm_waives_initial_calibration(const inst *meter) {
    switch (meter->dtype) {
        case instI1Monitor: case instI1Pro: case instI1Pro2: case instI1Pro3:
        case instColorMunki: case instEX1: case instSMCube: case instSpyderX:
            return 1;
        default:
            return 0;
    }
}
#endif
