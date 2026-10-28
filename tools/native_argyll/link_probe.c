/* Original offline link check. This executable is built but NEVER run.
 * Force-loading the archive verifies all compiled upstream dependencies resolve.
 * Argyll declarations retain their upstream licensing and attribution. */
#ifndef SALONEINSTLIB
#error "This probe requires the pinned standalone instrument-library configuration"
#endif
#include <stdio.h>
#include "sa_config.h"
#include "numsup.h"
#include "cgats.h"
#include "xspect.h"
#include "insttypes.h"
#include "conv.h"
#include "icoms.h"
#include "inst.h"
int main(void) { return sizeof(ipatch) == 0; }
