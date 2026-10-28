/* Inert allocation/plot/release proof of exact guarded caller fragments.
 * No instrument, transport, interpolation or optical claim is present. */
#include <stdio.h>
#include <string.h>

static double values[3][3], *matrix[3] = {values[0], values[1], values[2]};
static int live, allocations, releases, plots, failures;
static void expect(const char *name, int condition) {
    if (!condition) { printf("FAIL %s\n", name); failures++; }
}
static double **dmatrixz(int r0, int r1, int c0, int c1) {
    expect("allocation bounds", r0 == 0 && r1 == 2 && c0 == 0 && c1 == 2 && live == 0);
    memset(values, 0, sizeof values);
    live++; allocations++;
    return matrix;
}
static void free_dmatrix(double **p, int r0, int r1, int c0, int c1) {
    expect("release bounds", p == matrix && r0 == 0 && r1 == 2 && c0 == 0 && c1 == 2
           && live == 1 && plots == 1);
    live--; releases++;
}
static void plot_msg(const char *message) { (void)message; }
static int do_plot(double *x, double *before, double *after, double *unused, int count) {
    int i;
    expect("plot ownership", live == 1 && allocations == 1 && releases == 0
           && unused == NULL && count == 3);
    for (i = 0; i < count; i++)
        expect("plot data", x[i] == i && before[i] == 10.0 + i && after[i] == 10.0 + i);
    plots++;
    return 0;
}
#include "callers.inc"
int main(void) {
    double samples[3][51] = {{0}}, *rows[3] = {samples[0], samples[1], samples[2]};
    int i;
    for (i = 0; i < 3; i++) samples[i][50] = 10.0 + i;
    i1pro3_strip_refl_meas_plot(3, rows);
    expect("strip-reflective plot matrix retained", live == 0);
    live = allocations = releases = plots = 0;
    i1pro3_pol_strip_refl_meas_plot(3, rows);
    expect("polarized-strip-reflective plot matrix retained", live == 0);
    printf("%d failures\n", failures);
    return failures != 0;
}
