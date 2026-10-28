/* Actual Argyll attachment. Build/link only in offline checks; NEVER execute.
 * The build must disable automatic serial probes in path enumeration. */
#include "description.h"
#include "description_identity.h"

int cm_description_backend_options(int *argc, char ***argv) {
    (void)argc; (void)argv; return 1;
}
const char *cm_description_provenance(void) { return "physical"; }
icompaths *cm_description_paths(void) {
    /* The pinned integration patches suppress fast-serial auto-identification.
     * This obtains OS path metadata; it does not initialize every candidate. */
    return new_icompaths_sel(NULL, icomt_instrument | icomt_portattr_all);
}
inst *cm_description_construct(icompath *path, int identify_serial, instType expected_type,
    inst_code (*callback)(void *, inst_ui_purp), void *context) {
    /* nocoms=0 is permitted only for the explicitly selected unknown serial path. */
    return cm_new_inst_checked(path, !identify_serial, NULL, callback, context, expected_type);
}
void cm_description_backend_finished(void) { }

void cm_description_native_identity(FILE *out, inst *meter) {
    cm_write_initialized_identity(out, meter);
}
