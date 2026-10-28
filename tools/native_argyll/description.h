/* Original selected-device description boundary. Upstream types remain Argyll's. */
#ifndef CM_DESCRIPTION_H
#define CM_DESCRIPTION_H
#ifndef SALONEINSTLIB
#error "The description boundary requires the standalone instrument configuration"
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "sa_config.h"
#include "numsup.h"
#include "cgats.h"
#include "xspect.h"
#include "insttypes.h"
#include "conv.h"
#include "icoms.h"
#include "inst.h"
#include "native_text.h"

#define CM_DESCRIPTION_PATH_LIMIT 1024
#define CM_DESCRIPTION_SERIAL_LIMIT CM_NATIVE_IDENTITY_TEXT_LIMIT
#define CM_DESCRIPTION_CANDIDATE_LIMIT 512

/* The real implementation never accepts --fixture. The substituted backend
 * consumes that leading pair before common argument validation. */
int cm_description_backend_options(int *argc, char ***argv);
const char *cm_description_provenance(void);
icompaths *cm_description_paths(void);
inst *cm_description_construct(icompath *path, int identify_serial, instType expected_type,
    inst_code (*callback)(void *, inst_ui_purp), void *context);
/* Attributed pinned-source extension: guard detected type before dispatching
 * a constructor, including constructors that themselves communicate. */
inst *cm_new_inst_checked(icompath *, int, a1log *,
    inst_code (*)(void *, inst_ui_purp), void *, instType);
void cm_description_backend_finished(void);
/* Print identity copied from this initialized instance, or null when unavailable.
 * No instrument I/O, acquisition facts or source from an earlier attempt. */
void cm_description_native_identity(FILE *out, inst *meter);
#endif
