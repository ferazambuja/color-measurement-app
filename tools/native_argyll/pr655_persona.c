/* Real PR-655/PR-670 driver over its existing scripted production-test actor.
 * The common session owns initialization, controls, acquisition and release.
 * Only serial transport and time are substituted. No OS instrument transport.
 * Copyright 2026 Fernando Voltolini de Azambuja. GPL-2.0-or-later.
 */
#define new_inst cm_source_new_inst
#include "session.h"
#if defined(CM_CONTRIBUTED_instPR655) || defined(CM_CONTRIBUTED_instPR670)
#define new_icoms cm_pr655_fake_icoms
#define new_icompaths cm_pr655_fake_paths
#define baud_rate_to_str cm_pr655_fake_baud
#define uimain cm_pr655_unused_driver_suite
#include "pr655_driver_test.c"
#undef uimain
#undef baud_rate_to_str
#undef new_icompaths
#undef new_icoms

static void persona_callback(inst *p, inst_code (*callback)(void *, inst_ui_purp), void *context) {
    p->uicallback = callback; p->uic_cntx = context;
}
static void persona_release(void) {
    if (opened != 1 || closed != 1 || deleted != 1 || failures
     || transport.next != transport.count) abort();
}
static void control_inventory(void) {
    setup_steps();
    add_owned("D117\r", "0000,2,Half degree,0.0\r\n0000,9,Quarter degree,0.0\r\n0000,15,Eighth degree,0.0\r\n");
}
inst *cm_pr655_persona(const char *scenario) {
    int model = !strncmp(scenario, "pr655", 5) ? 655 : 670, i;
    const char *kind = scenario + 5;
    icompath path = {0};
    pr655 *p;
    reset(model, 7);
    full_setup(1, 2, 1, 0, 75, 0, 0, 8, -1);
    connect_steps(model); reset_owned();
    /* connect_steps uses mutable generated replies. Freeze the startup before
     * scripting later settings, through the actor's existing snapshot storage. */
    for (i = 0; i < transport.count; i++) {
        owned_reply[owned_count] = malloc(strlen(transport.steps[i].reply) + 1);
        if (!owned_reply[owned_count]) abort();
        strcpy(owned_reply[owned_count], transport.steps[i].reply);
        transport.steps[i].reply = owned_reply[owned_count++];
    }
    opened = closed = deleted = 0;
    if (!strcmp(kind, "_ambient")) {
        ambient_setup_steps(); ambient_capture_steps(model, 1, 1, 1e-4, 1, 1); ambient_setup_steps();
    } else if (!strcmp(kind, "_controls_read")) control_inventory();
    else if (!strcmp(kind, "_controls_apply") || !strcmp(kind, "_control_capture")) {
        control_inventory(); add_owned("SN3\r", "0000\r\n");
        full_setup(1, 2, 3, 0, 75, 0, 0, 8, -1); setup_steps(); control_inventory();
    } else if (!strcmp(kind, "_control_failed")) {
        control_inventory(); add_owned("SN3\r", "-35\r\n");
    } else if (*kind && strcmp(kind, "_d2_failure") && strcmp(kind, "_geometry_changed")
               && strcmp(kind, "_identity") && strcmp(kind, "_xyz_only")
               && strcmp(kind, "_nonadaptive_refused") && strcmp(kind, "_cancel")) abort();
    if (!*kind || !strcmp(kind, "_control_capture") || !strcmp(kind, "_d2_failure")
     || !strcmp(kind, "_geometry_changed") || !strcmp(kind, "_xyz_only")) {
        if (!strcmp(kind, "_geometry_changed")) {
            add_owned("D601\r", numeric_setup); add_owned("D602\r", text_setup);
            add_owned("D112\r", "0000,1,3\r\n");
            add_owned("D116\r", "0000,8,Test lens,Primary,Illuminance,Irradiance\r\n");
        } else setup_steps();
        capture_steps(model, strcmp(kind, "_xyz_only") != 0, 1, 0, 0, 1e-4, 1, 1,
                      !strcmp(kind, "_d2_failure") ? "D2" : NULL);
        if (!strcmp(kind, "_d2_failure")) transport.count--; /* quarantine forbids B100 */
        if (strcmp(kind, "_d2_failure")) setup_steps();
    }
    /* A native error status leaves the connection usable; malformed streams
     * quarantine it. These scripted cases all expect the documented Q close. */
    if (strcmp(kind, "_d2_failure")) add("Q", "");
    path.dtype = model == 655 ? instPR655 : instPR670;
    path.name = path.spath = "synthetic-only";
    path.dctype = icomt_serial | icomt_instrument;
    p = new_pr655(cm_pr655_fake_icoms(&path, g_log), path.dtype);
    if (!p || transport.writes || opened) abort();
    p->engine.now = test_now; p->engine.sleep = test_sleep;
    p->set_uicallback = persona_callback;
    atexit(persona_release);
    return (inst *)p;
}
#endif
#undef new_inst
