/* Initialized PR identity copied without instrument I/O or acquisition history.
 * Copyright 2026 Fernando Voltolini de Azambuja. GPL-2.0-or-later.
 */
#ifndef CM_PR655_SUPPORT_H
#define CM_PR655_SUPPORT_H
#include <string.h>
#if defined(CM_CONTRIBUTED_instPR655) || defined(CM_CONTRIBUTED_instPR670)
#include "pr655.h"
typedef struct {
    int model;
    char serial[PR655_MAX_TEXT], firmware[PR655_MAX_TEXT];
    pr655_grid grid;
} cm_pr655_identity;

static int cm_pr655_identity_snapshot(inst *meter, cm_pr655_identity *identity) {
    pr655 *p;
    int expected = 0, verified;
    if (!identity) return 0;
    memset(identity, 0, sizeof(*identity));
    if (!meter) return 0;
#ifdef CM_CONTRIBUTED_instPR655
    if (meter->dtype == instPR655) expected = 655;
#endif
#ifdef CM_CONTRIBUTED_instPR670
    if (meter->dtype == instPR670) expected = 670;
#endif
    if (!expected) return 0;
    p = (pr655 *)meter;
    amutex_lock(p->lock);
    verified = p->gotcoms && p->inited && p->engine.identity_valid
            && p->engine.state == pr655_ready && p->engine.model == expected;
    if (verified) {
        identity->model = p->engine.model;
        strcpy(identity->serial, p->engine.serial);
        strcpy(identity->firmware, p->engine.version);
        identity->grid = p->engine.grid;
    }
    amutex_unlock(p->lock);
    return verified;
}
#endif
#endif
