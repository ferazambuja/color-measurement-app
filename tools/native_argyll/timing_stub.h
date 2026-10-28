/* Synthetic timing vtable for table/chart personas. No transport or optics.
 * The common session must reset the marker, call acquisition on its owning
 * thread, and deliver white from its sole command-reader thread. These fixed
 * outputs test that exchange only; they do not model an instrument's response.
 */
#ifndef CM_TIMING_STUB_H
#define CM_TIMING_STUB_H
static inst_code (*timing_callback)(void *, inst_ui_purp);
static void *timing_context;
static int timing_ready;
static inst_code stub_white_change(inst *p, int init) {
    (void)p;
    if (init) timing_ready = 1;
    else require(timing_ready);
    return inst_ok;
}
static inst_code stub_meas_delay(inst *p, int *delay, int *reaction) {
    double started, now;
    (void)p;
    require(timing_ready && timing_callback);
    require(cm_monotonic_checked(&started));
    /* Mirror the two-second native white window, including a small margin. */
    do {
        if (timing_callback(timing_context, inst_armed) == inst_user_abort)
            return inst_user_abort;
        cm_sleep_ms(1);
        require(cm_monotonic_checked(&now));
    } while (now < started + 2.1);
    *delay = 37; *reaction = -230;
    return inst_ok;
}
#endif
