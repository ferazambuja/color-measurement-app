/* Shared display-timing exchange over inst.meas_delay/white_change.
 * Argyll 3.5.0 inst.h:1053-1074 and dispsup.c:414-440,497-545 require
 * acquisition on the owning thread and a second thread for the white marker.
 * No driver identity, transport, colour calculation or optical-onset claim.
 * Include after session wire/command helpers, before the UI callback.
 */
#ifndef CM_DISPLAY_TIMING_H
#define CM_DISPLAY_TIMING_H
#ifndef _WIN32
#include <pthread.h>
#endif

static struct {
    inst *meter;
    int done, abort, white, error;
    inst_code marker_code;
    double started, marker_started, marker_finished;
#ifdef _WIN32
    CRITICAL_SECTION mutex;
#else
    pthread_mutex_t mutex;
#endif
} cm_timing;
static int cm_timing_active;

static void cm_timing_lock(void) {
#ifdef _WIN32
    EnterCriticalSection(&cm_timing.mutex);
#else
    if (pthread_mutex_lock(&cm_timing.mutex)) _Exit(2);
#endif
}
static void cm_timing_unlock(void) {
#ifdef _WIN32
    LeaveCriticalSection(&cm_timing.mutex);
#else
    if (pthread_mutex_unlock(&cm_timing.mutex)) _Exit(2);
#endif
}
static int cm_timing_aborted(void) {
    int abort;
    cm_timing_lock(); abort = cm_timing.abort; cm_timing_unlock();
    return abort;
}

/* Sole stdin reader while meas_delay blocks. It never writes wire events.
 * A successful marker is still rejected if its bracket falls outside the
 * method window after the owning thread joins this reader.
 */
#ifdef _WIN32
static DWORD WINAPI cm_timing_reader(LPVOID context) {
#else
static void *cm_timing_reader(void *context) {
#endif
    char action[CM_ACTION_MAX+1], argument[CM_ARGUMENT_MAX+1];
    int done;
    double now;
    (void)context;
    for (;;) {
        cm_timing_lock(); done = cm_timing.done; cm_timing_unlock();
        if (done) break;
        if (!cm_monotonic_checked(&now)) {
            cm_timing_lock(); cm_timing.error = cm_timing.abort = 1; cm_timing_unlock();
            break;
        }
        cm_timing_lock();
        if (!cm_timing.white && now > cm_timing.started + 2.0)
            cm_timing.error = 2, cm_timing.abort = 1;
        done = cm_timing.abort;
        cm_timing_unlock();
        if (done) break;
        if (command(action, argument, 0)) {
            cm_timing_lock();
            if (strcmp(action, "presented") || strcmp(argument, "timing-white")
                || cm_timing.white || cm_timing.done
                || !cm_monotonic_checked(&cm_timing.marker_started)
                || cm_timing.marker_started < cm_timing.started + 0.2
                || cm_timing.marker_started > cm_timing.started + 2.0) {
                cm_timing.error = cm_timing.abort = 1;
            } else {
                cm_timing.marker_code = cm_timing.meter->white_change(cm_timing.meter, 0);
                if (!cm_monotonic_checked(&cm_timing.marker_finished)
                    || cm_timing.marker_finished > cm_timing.started + 2.0)
                    cm_timing.error = cm_timing.abort = 1;
                cm_timing.white = 1;
                if (cm_timing.marker_code != inst_ok) cm_timing.abort = 1;
            }
            cm_timing_unlock();
        }
        if (cancelled || bad_input) {
            cm_timing_lock(); cm_timing.abort = 1;
            if (bad_input) cm_timing.error = 1;
            cm_timing_unlock();
        }
        cm_sleep_ms(1);
    }
    return 0;
}

static void cm_timing_request(const char *phase, const char *id, int minimum_delay_ms,
                              double window_start) {
    start("presentation_required");
    fputs(",\"request_id\":", wire); string(id);
    fputs(",\"phase\":", wire); string(phase);
    fprintf(wire, ",\"minimum_delay_ms\":%d,\"deadline_ms\":2000,"
                  "\"native_clock_id\":\"worker_monotonic\","
                  "\"method_window_started_seconds\":%.17g}\n",
            minimum_delay_ms, window_start);
}

static int cm_read_timing(cm_binding *bound) {
    char action[CM_ACTION_MAX+1], argument[CM_ARGUMENT_MAX+1];
    inst2_capability current;
    inst_code rv;
    int delay = INT_MIN, reaction = INT_MIN;
    double finished, phase_start, now;
#ifdef _WIN32
    HANDLE thread;
#else
    pthread_t thread;
#endif
    bound->meter->capabilities(bound->meter, NULL, &current, NULL);
    if (!(current & inst2_meas_disp_update) || !bound->meter->meas_delay
        || !bound->meter->white_change)
        return fail("display_timing_unavailable", inst_unsupported);
    if (cancelled) return fail("cancelled", inst_user_abort);
    if (!cm_monotonic_checked(&phase_start)) return fail("timing_clock", inst_internal_error);
    cm_timing_request("black", "timing-black", 0, 0.0);
    while (!command(action, argument, 0)) {
        if (cancelled || bad_input)
            return fail(cancelled ? "cancelled" : "timing_protocol", inst_user_abort);
        if (!cm_monotonic_checked(&now)) return fail("timing_clock", inst_internal_error);
        if (now > phase_start + 2.0)
            return fail("timing_presentation_timeout", inst_user_abort);
        cm_sleep_ms(1);
    }
    if (!cm_monotonic_checked(&now)) return fail("timing_clock", inst_internal_error);
    if (now > phase_start + 2.0)
        return fail("timing_presentation_timeout", inst_user_abort);
    if (strcmp(action, "presented") || strcmp(argument, "timing-black"))
        return fail("timing_protocol", inst_internal_error);
    /* Receiving the accepted stamp is later than client draw completion;
     * start the full settle here without subtracting different clock epochs. */
    if (!cm_monotonic_checked(&phase_start)) return fail("timing_clock", inst_internal_error);
    do {
        if (command(action, argument, 0) || bad_input)
            return fail("timing_protocol", inst_internal_error);
        if (cancelled) return fail("cancelled", inst_user_abort);
        if (!cm_monotonic_checked(&now)) return fail("timing_clock", inst_internal_error);
        cm_sleep_ms(1);
    } while (now < phase_start + 0.6);
    if ((rv = bound->meter->white_change(bound->meter, 1)) != inst_ok)
        return native_fail(bound->meter, "white_change_init", rv);
    memset(&cm_timing, 0, sizeof(cm_timing));
    cm_timing.meter = bound->meter;
    if (!cm_monotonic_checked(&cm_timing.started))
        return fail("timing_clock", inst_internal_error);
#ifdef _WIN32
    if (!InitializeCriticalSectionAndSpinCount(&cm_timing.mutex, 0))
        return fail("timing_thread", inst_internal_error);
#else
    if (pthread_mutex_init(&cm_timing.mutex, NULL))
        return fail("timing_thread", inst_internal_error);
#endif
    /* This timestamp brackets notification/thread setup plus the native call;
     * it is a wrapper bound, never a claim of first optical sampling. */
    cm_timing_request("white", "timing-white", 200, cm_timing.started);
    cm_timing_active = 1;
#ifdef _WIN32
    thread = CreateThread(NULL, 0, cm_timing_reader, NULL, 0, NULL);
    if (!thread) {
        cm_timing_active = 0; DeleteCriticalSection(&cm_timing.mutex);
        return fail("timing_thread", inst_internal_error);
    }
#else
    if (pthread_create(&thread, NULL, cm_timing_reader, NULL)) {
        cm_timing_active = 0; pthread_mutex_destroy(&cm_timing.mutex);
        return fail("timing_thread", inst_internal_error);
    }
#endif
    rv = bound->meter->meas_delay(bound->meter, &delay, &reaction);
    if (!cm_monotonic_checked(&finished)) finished = -1.0;
    cm_timing_lock(); cm_timing.done = 1; cm_timing_unlock();
#ifdef _WIN32
    if (WaitForSingleObject(thread, INFINITE) != WAIT_OBJECT_0) _Exit(2);
    CloseHandle(thread); DeleteCriticalSection(&cm_timing.mutex);
#else
    if (pthread_join(thread, NULL)) _Exit(2);
    if (pthread_mutex_destroy(&cm_timing.mutex)) _Exit(2);
#endif
    cm_timing_active = 0;
    if (cm_timing.error == 2) return fail("timing_presentation_timeout", inst_user_abort);
    if (cm_timing.error || bad_input) return fail("timing_protocol", inst_internal_error);
    if (cm_timing.white && cm_timing.marker_code != inst_ok)
        return native_fail(bound->meter, "white_change", cm_timing.marker_code);
    if (rv != inst_ok) return native_fail(bound->meter,
        cancelled && (rv & inst_mask) == inst_user_abort ? "cancelled" : "meas_delay", rv);
    if (!cm_timing.white || cm_timing.marker_started < cm_timing.started+0.2
        || cm_timing.marker_finished < cm_timing.marker_started
        || finished < cm_timing.marker_finished)
        return fail("timing_transition", inst_misread);
    if (delay < 0 || reaction == INT_MIN)
        return fail("invalid_display_timing", inst_misread);
    start("display_timing");
    fprintf(wire, ",\"native_code\":0,\"unit\":\"ms\",\"native_method\":\"meas_delay\","
                  "\"time_reference\":\"white_change_call\",\"display_update_delay_ms\":%d,"
                  "\"instrument_reaction_ms\":%d,\"native_clock_id\":\"worker_monotonic\","
                  "\"method_window_started_seconds\":%.17g,"
                  "\"marker_call_started_seconds\":%.17g,"
                  "\"marker_call_finished_seconds\":%.17g,"
                  "\"method_window_finished_seconds\":%.17g}\n",
            delay, reaction, cm_timing.started, cm_timing.marker_started,
            cm_timing.marker_finished, finished);
    return 1;
}
#endif
