/* Current initialized CR identity, independent of acquisition event emitters.
 * The caller owns the meter lifetime. This getter copies facts already verified
 * by that instance; it issues no instrument command and reads no prior attempt.
 */
/* Copyright 2026 Fernando Voltolini de Azambuja.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef CM_CR250_SUPPORT_H
#define CM_CR250_SUPPORT_H
#include <string.h>
#if defined(CM_CONTRIBUTED_instCR250) || defined(CM_CONTRIBUTED_instCR100) || defined(CM_CONTRIBUTED_instCR300)
#include "cr250.h"

static int cm_cr250_identity_snapshot(inst *meter, cr250_identity *identity) {
    cr250 *p;
    int family = 0, verified;
    if (identity == NULL) return 0;
    memset(identity, 0, sizeof(*identity));
    if (meter == NULL) return 0;
#ifdef CM_CONTRIBUTED_instCR250
    if (meter->dtype == instCR250) family = 1;
#endif
#ifdef CM_CONTRIBUTED_instCR100
    if (meter->dtype == instCR100) family = 1;
#endif
#ifdef CM_CONTRIBUTED_instCR300
    if (meter->dtype == instCR300) family = 1;
#endif
    if (!family) return 0;
    p = (cr250 *)meter;
    amutex_lock(p->lock);
    verified = p->gotcoms && p->inited && p->engine.identified
            && !p->engine.uncertain;
    if (verified) *identity = p->engine.id;
    amutex_unlock(p->lock);
    return verified;
}
#endif
#endif
