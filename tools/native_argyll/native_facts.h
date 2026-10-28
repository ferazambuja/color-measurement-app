/* Retained native facts a driver keeps beyond what the inst vtable returns.
 *
 * The vtable hands back an ipatch and an inst_code. A driver may know far more
 * about the attempt it just made -- the exact identity it verified, every native
 * reply, the settings it checked -- and the only way to keep those is to read the
 * driver's own structure while the instrument is alive, before the next attempt
 * clears it. That needs a cast, so it is done by one small support adapter per
 * driver, never by a fork-wide vtable method or an arbitrary-command interface.
 *
 * Contributed drivers only, and only in a build that composed them: an adapter is
 * compiled under CM_CONTRIBUTED_<enum>, which argyll_native_check.py defines from
 * the series manifest. A stock build compiles no adapter and this is a no-op.
 *
 * The type test comes before any cast. meter->dtype is what new_cr250 wrote, and
 * the checked constructor dispatches instCR250 and instCR100 to new_cr250 alone,
 * so a meter carrying either type is a cr250. The adapter itself then refuses to
 * publish an identity the driver did not verify. */
#ifndef CM_NATIVE_FACTS_H
#define CM_NATIVE_FACTS_H

#if defined(CM_CONTRIBUTED_instCR250) || defined(CM_CONTRIBUTED_instCR100) || defined(CM_CONTRIBUTED_instCR300)
#define CM_CR_ADAPTER 1
void cm_cr250_native_facts(inst *meter);   /* cr250_support.c */
void cm_cr250_read_allowance(inst *meter, double seconds);
#endif
#if defined(CM_CONTRIBUTED_instPR655) || defined(CM_CONTRIBUTED_instPR670)
#define CM_PR_ADAPTER 1
void cm_pr655_native_facts(inst *meter);
void cm_pr655_read_allowance(inst *meter, double seconds);
#endif

/* Whether this meter is one of cr250.c's Colorimetry Research models. */
static int cm_cr_family(const inst *meter) {
    if (!meter) return 0;
#ifdef CM_CONTRIBUTED_instCR250
    if (meter->dtype == instCR250) return 1;
#endif
#ifdef CM_CONTRIBUTED_instCR100
    if (meter->dtype == instCR100) return 1;
#endif
#ifdef CM_CONTRIBUTED_instCR300
    if (meter->dtype == instCR300) return 1;
#endif
    return 0;
}

static int cm_pr_family(const inst *meter) {
    if (!meter) return 0;
#ifdef CM_CONTRIBUTED_instPR655
    if (meter->dtype == instPR655) return 1;
#endif
#ifdef CM_CONTRIBUTED_instPR670
    if (meter->dtype == instPR670) return 1;
#endif
    return 0;
}

/* Whether this driver takes a host allowance at all. The worker asks the client
 * for its remaining deadline only then: a driver without an adapter would ignore
 * the answer, so the exchange would be a round trip per read for nothing. */
static int cm_native_wants_allowance(const inst *meter) {
    return cm_cr_family(meter) || cm_pr_family(meter);
}

/* Hand the driver what remains of the host's allowance before a read, so a driver
 * whose budget will not fit can refuse BEFORE measuring rather than be cut off by
 * the host mid-exposure. A no-op for a driver without an adapter. */
static void cm_native_read_allowance(inst *meter, double seconds) {
#ifdef CM_CR_ADAPTER
    if (cm_cr_family(meter)) {
        cm_cr250_read_allowance(meter, seconds);
        return;
    }
#endif
#ifdef CM_PR_ADAPTER
    if (cm_pr_family(meter)) {
        cm_pr655_read_allowance(meter, seconds);
        return;
    }
#endif
    (void)meter; (void)seconds;
}

static void cm_native_facts(inst *meter) {
#ifdef CM_CR_ADAPTER
    if (cm_cr_family(meter)) {
        cm_cr250_native_facts(meter);
        return;
    }
#endif
#ifdef CM_PR_ADAPTER
    if (cm_pr_family(meter)) {
        cm_pr655_native_facts(meter);
        return;
    }
#endif
    (void)meter;
}
#endif
