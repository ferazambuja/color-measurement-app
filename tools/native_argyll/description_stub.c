/* Substituted description transport. Native personas use production drivers
 * over the existing scripted links; no OS instrument transport is linked. */
#include "description.h"
#include "serial_policy.h"   /* cm_typed_serial: the types a typed copy may carry */
#include "description_identity.h"
inst *cm_chart_persona(const char *name, const char *scenario);
#if defined(CM_CONTRIBUTED_instCR250) || defined(CM_CONTRIBUTED_instCR100) || defined(CM_CONTRIBUTED_instCR300)
inst *cm_cr250_persona(const char *scenario);
#endif
#if defined(CM_CONTRIBUTED_instPR655) || defined(CM_CONTRIBUTED_instPR670)
inst *cm_pr655_persona(const char *scenario);
#endif

static const char *fixture;
/* Explicit discovery replay variants. Only the substituted transport changes;
 * description.c and the Python client still produce every protocol field. */
static char fixture_name[128];
static const char *variant;
static int second_unit;
static unsigned enumerations, constructions, communications, initializations, configurations, destructions, paths_deleted;
static unsigned calibrations, reads, options, modes;
static int initialized;
static int capabilities_queried;
static instType identity;
static icompath *listed;
static inst_code (*ui_callback)(void *, inst_ui_purp);
static void *ui_context;
static int is(const char *name) { return !strcmp(fixture, name); }
static int typed(void) { return !strncmp(fixture, "cr250_typed", 11); }
static instType native_fixture_type(void) {
#ifdef CM_CONTRIBUTED_instCR100
    if (is("cr100_native")) return instCR100;
#endif
#ifdef CM_CONTRIBUTED_instCR250
    if (is("cr250_native")) return instCR250;
#endif
#ifdef CM_CONTRIBUTED_instCR300
    if (is("cr300_native")) return instCR300;
#endif
#ifdef CM_CONTRIBUTED_instPR655
    if (is("pr655_native")) return instPR655;
#endif
#ifdef CM_CONTRIBUTED_instPR670
    if (is("pr670_native")) return instPR670;
#endif
    return instUnknown;
}
static void require(int condition) { if (!condition) abort(); }

int cm_description_backend_options(int *argc, char ***argv) {
    static const char *fixtures[]={"display_types", "display_types_failure", "success", "unknown_serial", "empty_serial", "serial_mismatch",
        "configuration_unavailable", "configuration_missing", "calibration_configuration", "initialization_failure",
        "mode_rejected", "mode_inconclusive", "mode_missing", "mode_composite",
        "communications_failure", "construction_failure", "enumeration_failure", "identity_mismatch",
        "identity_refinement", "identity_cross_family", "identity_comms_cross_family", "callback_refusal", "callback_ignored",
        "duplicate_path", "no_candidates", "invalid_path", "invalid_utf8_path", "invalid_utf8_serial", "long_serial",
        "quoted_serial", "utf8_serial", "spyder1", "spyder3", "spyder4", "spyder5", "spyderx", "dtp20", "dtp22", "dtp41", "dtp51", "dtp92", "dtp94", "spectrolino", "spectroscan", "smcube",
        "kleink10", "specbos1201", "specbos2501", "specbos", "spectraval", "i1monitor", "i1pro", "i1pro2", "i1pro3", "i1d3_flash", "colorhug_flash", "huey_flash", "candidate_payload_limit",
        "cr250_typed", "cr250_typed_mismatch", "cr250_typed_wrong_model", "cr250_typed_old_firmware",
#ifdef CM_CONTRIBUTED_instCR100
        "cr100_native",
#endif
#ifdef CM_CONTRIBUTED_instCR250
        "cr250_native",
#endif
#ifdef CM_CONTRIBUTED_instCR300
        "cr300_native",
#endif
#ifdef CM_CONTRIBUTED_instPR655
        "pr655_native",
#endif
#ifdef CM_CONTRIBUTED_instPR670
        "pr670_native",
#endif
        NULL};
    if (*argc<3 || strcmp((*argv)[1], "--fixture")) return 0;
    if (strlen((*argv)[2])>=sizeof(fixture_name)) return 0;
    strcpy(fixture_name, (*argv)[2]);
    char *separator=strstr(fixture_name, "__");
    if (separator) {
        *separator=0; variant=separator+2;
        if (strcmp(variant, "reordered") && strcmp(variant, "shared_serial")
            && strcmp(variant, "no_serial") && strcmp(variant, "single")) return 0;
    }
    fixture=fixture_name;
    if (native_fixture_type()!=instUnknown && variant && strcmp(variant, "single")) return 0;
    for (int i=0; fixtures[i]; i++) if (is(fixtures[i])) {
        (*argv)[2]=(*argv)[0]; *argv+=2; *argc-=2; return 1;
    }
    return 0;
}
const char *cm_description_provenance(void) { return "simulated"; }
void cm_description_native_identity(FILE *out, inst *meter) {
    if (native_fixture_type()!=instUnknown) {
        cm_write_initialized_identity(out, meter);
        return;
    }
    /* This substituted constructor is not a CR instance and cannot be cast to
     * one. It reports unavailable instead of inventing initialized driver data. */
    (void)meter;
    fputs("null", out);
}
static void paths_del(icompaths *paths) {
    require(!constructions || destructions || is("construction_failure"));
    if (is("spyder3") || is("spyder4") || is("spyder5")
        || is("i1monitor") || is("i1pro") || is("i1pro2") || is("identity_refinement"))
        require(constructions==0 && communications==0 && initializations==0);
    /* The typed route types a copy; the listed entry is never rewritten. */
    if (typed() || native_fixture_type()!=instUnknown) require(listed->dtype==instUnknown);
    paths_deleted++;
    for (int i=0; i<paths->npaths; i++) {
#if defined(ENABLE_SERIAL) || defined(ENABLE_FAST_SERIAL)
        free(paths->paths[i]->spath);
#endif
        free(paths->paths[i]->name); free(paths->paths[i]);
    }
    free(paths->paths); free(paths);
}
icompaths *cm_description_paths(void) {
    icompaths *paths;
    enumerations++;
    if (is("enumeration_failure")) return NULL;
    paths=calloc(1, sizeof(*paths)); require(paths!=NULL);
    paths->npaths=is("no_candidates") ? 0 : (is("candidate_payload_limit") ? 100 : 2);
    if (native_fixture_type()!=instUnknown || is("i1pro3")) paths->npaths=1;
    if (variant && !strcmp(variant, "single")) paths->npaths=1;
    paths->paths=calloc(paths->npaths ? (size_t)paths->npaths : 1, sizeof(*paths->paths)); require(paths->paths!=NULL);
    paths->del=paths_del;
    for (int i=0; i<paths->npaths; i++) {
        icompath *path=calloc(1, sizeof(*path)); require(path!=NULL);
        paths->paths[i]=path;
        int unit=variant && !strcmp(variant, "reordered") ? 1-i : i;
        path->name=strdup(unit && !is("duplicate_path") ? "fixture:display:2" : "fixture:display:1");
        /* The default persona must be an instrument that can actually be
         * described, which means one whose init_inst is refusal-free. It was the
         * i1Display3 until the provenance test showed i1d3.c:3079 flashes its LED
         * on init; the i1Display2 is the same class on the same transport and is
         * genuinely harmless (i1disp.c:1802). `i1d3_flash` has its own scenario. */
        path->dtype=instI1Disp2; path->dctype=icomt_instrument | icomt_hid;
        if (is("i1d3_flash") || is("utf8_serial")) path->dtype=instI1Disp3;
        if (is("colorhug_flash")) path->dtype=instColorHug;
        if (is("huey_flash")) path->dtype=instHuey;
        if (is("identity_refinement")) { path->dtype=instI1Pro2; path->dctype=icomt_usb; }
        if (is("i1monitor")) { path->dtype=instI1Monitor; path->dctype=icomt_usb; }
        if (is("i1pro")) { path->dtype=instI1Pro; path->dctype=icomt_usb; }
        if (is("i1pro2")) { path->dtype=instI1Pro2; path->dctype=icomt_usb; }
        if (is("i1pro3")) { path->dtype=instI1Pro3; path->dctype=icomt_usb; }
        if (is("kleink10") || is("specbos1201") || is("specbos2501") || is("specbos") || is("spectraval")) {
            path->dtype=instUnknown; path->dctype=icomt_serial | icomt_fastserial;
        }
        if (is("spyder1")) path->dtype=instSpyder1;
        if (is("spyder3")) { path->dtype=instSpyder3; path->dctype=icomt_usb; }
        if (is("spyder4")) { path->dtype=instSpyder4; path->dctype=icomt_usb; }
        if (is("spyder5")) { path->dtype=instSpyder5; path->dctype=icomt_usb; }
        if (is("spyderx")) path->dtype=instSpyderX;
        if (is("dtp22")) path->dtype=instDTP22;
        if (is("dtp20")) { path->dtype=instDTP20; path->dctype=icomt_usb; }
        if (is("dtp41")) path->dtype=instDTP41;
        if (is("dtp51")) path->dtype=instDTP51;
        if (is("dtp92")) path->dtype=instDTP92;
        if (is("dtp94")) path->dtype=instDTP94;
        if (is("spectroscan")) path->dtype=instSpectroScan;
        if (is("spectrolino")) path->dtype=instSpectrolino;
        if (is("smcube")) path->dtype=instSMCube;
        if (typed() || native_fixture_type()!=instUnknown) {
            path->dtype=instUnknown;
            path->dctype=icomt_serial | icomt_fastserial | icomt_seriallike;
#if defined(ENABLE_SERIAL) || defined(ENABLE_FAST_SERIAL)
            path->spath=strdup("fixture:serial-node:1"); require(path->spath!=NULL);
#endif
        }
        if (!i) listed=path;
        if (is("invalid_path")) { free(path->name); path->name=NULL; }
        if (is("invalid_utf8_path")) { free(path->name); path->name=strdup("\xc0\xaf"); }
    }
    return paths;
}
static instType get_itype(inst *p) {
    (void)p;
    if (initialized && is("identity_refinement")) return instI1Pro;
    if ((initialized && is("identity_cross_family")) || (communications && is("identity_comms_cross_family"))) return instI1Pro3;
    return identity;
}
static inst_code init_coms(inst *p, baud_rate rate, flow_control flow, double timeout) {
    (void)p; require(rate==baud_nc && flow==fc_nc && timeout==5.0); communications++;
    require(ui_callback(ui_context, inst_negcoms)==inst_ok);
    /* The driver, not the selection, refuses another model on the port. */
    if (is("cr250_typed_wrong_model")) return inst_unknown_model;
    return is("communications_failure") ? inst_coms_fail : inst_ok;
}
static inst_code init_inst(inst *p) {
    (void)p; require(communications==1); initializations++;
    if (is("initialization_failure")) return inst_internal_error;
    if (is("cr250_typed_old_firmware")) return inst_unsupported;
    if (is("callback_refusal") || is("callback_ignored")) {
        require(ui_callback(ui_context, inst_armed)==inst_user_abort);
        if (is("callback_refusal")) return inst_user_abort;
    }
    initialized=1; return inst_ok;
}
static char *get_serial(inst *p) {
    static char long_serial[CM_DESCRIPTION_SERIAL_LIMIT+2];
    (void)p; require(initialized);
    if (is("unknown_serial")) return NULL;
    if (variant && !strcmp(variant, "no_serial")) return NULL;
    if (is("empty_serial")) return "";
    if (is("serial_mismatch")) return "OTHER-UNIT";
    if (is("invalid_utf8_serial")) return "\xed\xa0\x80";
    if (is("long_serial")) { memset(long_serial, 'x', sizeof(long_serial)-1); return long_serial; }
    if (is("quoted_serial")) return "Q\"\\\n\t\xc3\xa9";
    if (is("utf8_serial")) return "CM-\xc3\xb1-1";
    return second_unit && !(variant && !strcmp(variant, "shared_serial"))
        ? "SUBSTITUTED-DISPLAY-002" : "SUBSTITUTED-DISPLAY-001";
}
static void capabilities(inst *p, inst_mode *cap, inst2_capability *cap2, inst3_capability *cap3) {
    (void)p; require(initialized);
    capabilities_queried=1;
    *cap=inst_mode_emis_spot | inst_mode_colorimeter; *cap2=inst2_none; *cap3=inst3_none;
    if (is("display_types") || is("display_types_failure")) *cap2=inst2_disptype;
    if (is("mode_composite")) *cap=inst_mode_emis_tele | inst_mode_trans_spot
        | inst_mode_trans_spot_a | inst_mode_colorimeter | inst_mode_spectral;
}
static inst_code check_mode(inst *p, inst_mode mode) {
    (void)p; require(initialized && capabilities_queried
                    && configurations==(is("configuration_missing") ? 0u : 1u));
    if (is("mode_rejected")) return inst_unsupported;
    if (is("mode_inconclusive")) return inst_no_coms;
    /* specbos_check_mode's semantic gate, after its composite capability mask. */
    if (is("mode_composite") && !IMODETST(mode, inst_mode_emis_tele)
        && !IMODETST(mode, inst_mode_trans_spot) && !IMODETST(mode, inst_mode_trans_spot_a)
        && !IMODETST(mode, inst_mode_emis_ambient)) return inst_unsupported;
    return inst_ok;
}
static inst_code meas_config(inst *p, inst_mode *available, inst_cal_cond *conditions, int *index) {
    (void)p; require(initialized && *index==-1); configurations++;
    if (is("configuration_unavailable")) { *available=~0u; *conditions=~0u; *index=999; return inst_unsupported; }
    *available=is("calibration_configuration") ? inst_mode_calibration : inst_mode_emis_spot;
    *conditions=is("calibration_configuration") ? inst_calc_man_ref_white : inst_calc_unknown;
    *index=is("calibration_configuration") ? 1 : 0; return inst_ok;
}
static inst_code display_types(inst *p,int *count,inst_disptypesel **list,int all,int recreate) {
    static inst_disptypesel rows[3];
    (void)p; require(initialized && capabilities_queried && all==1 && recreate==1);
    memset(rows,0,sizeof(rows));
    *count=3; *list=rows;
    for (int i=0; i<3; i++) {
        strcpy(rows[i].desc,"Duplicate native name"); rows[i].cbid=i+1;
    }
    rows[0].flags=inst_dtflags_default;
    rows[2].flags=inst_dtflags_ccmx; rows[2].path="/simulated/catalogue.ccmx";
    rows[2].cc_cbid=1; rows[2].mat[0][0]=rows[2].mat[1][1]=rows[2].mat[2][2]=1;
    return is("display_types_failure") ? inst_coms_fail : inst_ok;
}
static inst_code display_readback(inst *p,disptech *technology,int *refresh,int *base) {
    (void)p; (void)refresh; *technology=disptech_unknown; *base=0; return inst_ok;
}
static inst_code forbidden_options(inst *p, inst_opt_type type, ...) { (void)p; (void)type; options++; abort(); }
static inst_code forbidden_mode(inst *p, inst_mode mode) { (void)p; (void)mode; modes++; abort(); }
static inst_code forbidden_read(inst *p, char *name, ipatch *result, instClamping clamp) { (void)p; (void)name; (void)result; (void)clamp; reads++; abort(); }
static inst_code forbidden_calibrate(inst *p, inst_cal_type *type, inst_cal_cond *condition, inst_calc_id_type *id, char name[CALIDLEN]) {
    (void)p; (void)type; (void)condition; (void)id; (void)name; calibrations++; abort();
}
static void destroy(inst *p) { destructions++; free(p); }
/* Argyll inst.c's default get_itype returns the constructed dtype. These direct
 * production constructors need that default installed, as new_inst would do. */
static instType native_get_itype(inst *p) { return p ? p->dtype : instUnknown; }
static inst_code (*native_init_coms)(inst *, baud_rate, flow_control, double);
static inst_code (*native_init_inst)(inst *);
static void (*native_del)(inst *);
static inst_code counted_native_coms(inst *p, baud_rate rate, flow_control flow, double timeout) {
    communications++;
    return native_init_coms(p, rate, flow, timeout);
}
static inst_code counted_native_init(inst *p) {
    initializations++;
    return native_init_inst(p);
}
static void counted_native_del(inst *p) {
    destructions++;
    native_del(p);
}
inst *cm_description_construct(icompath *path, int identify_serial, instType expected_type,
    inst_code (*callback)(void *, inst_ui_purp), void *context) {
    inst *p;
    constructions++; ui_callback=callback; ui_context=context;
    second_unit=!strcmp(path->name, "fixture:display:2");
    require(second_unit || !strcmp(path->name, "fixture:display:1"));
    if (is("construction_failure")) return NULL;
    require(!identify_serial && expected_type==path->dtype);
    if (native_fixture_type()!=instUnknown || is("i1pro3")) {
        if (is("i1pro3")) {
            require(path==listed && path->dtype==instI1Pro3 && path->dctype==icomt_usb);
            p=cm_chart_persona("i1pro3", "startup");
        } else {
            require(path!=listed && path->dtype==native_fixture_type()
                    && cm_typed_serial(path->dtype) && path->name==listed->name
                    && (path->dctype & icomt_serial)
#if defined(ENABLE_SERIAL) || defined(ENABLE_FAST_SERIAL)
                    && path->spath==listed->spath
#endif
                    );
            p=NULL;
#if defined(CM_CONTRIBUTED_instCR250) || defined(CM_CONTRIBUTED_instCR100) || defined(CM_CONTRIBUTED_instCR300)
            if (is("cr100_native")) p=cm_cr250_persona("cr100");
            if (is("cr250_native")) p=cm_cr250_persona("cr250");
            if (is("cr300_native")) p=cm_cr250_persona("cr300");
#endif
#if defined(CM_CONTRIBUTED_instPR655) || defined(CM_CONTRIBUTED_instPR670)
            if (is("pr655_native")) p=cm_pr655_persona("pr655_identity");
            if (is("pr670_native")) p=cm_pr655_persona("pr670_identity");
#endif
        }
        require(p && p->dtype==path->dtype && p->set_uicallback);
        p->set_uicallback(p, callback, context);
        p->get_itype=native_get_itype;
        native_init_coms=p->init_coms; native_init_inst=p->init_inst; native_del=p->del;
        require(native_init_coms && native_init_inst && native_del);
        p->init_coms=counted_native_coms; p->init_inst=counted_native_init;
        p->del=counted_native_del;
        return p;
    }
#if defined(CM_CONTRIBUTED_instCR250) || defined(CM_CONTRIBUTED_instCR100)
    /* A copy of the listed path carrying the caller's type -- one of the CR
     * family's rows -- with the display name and device node exactly as
     * enumerated. */
    if (typed())
        require(path!=listed && cm_typed_serial(path->dtype) && path->name==listed->name
                && (path->dctype & icomt_serial)
#if defined(ENABLE_SERIAL) || defined(ENABLE_FAST_SERIAL)
                && path->spath==listed->spath
#endif
                );
#else
    require(!typed());
#endif
    identity=is("identity_mismatch") || is("cr250_typed_mismatch") ? instI1Pro3 : path->dtype;
    p=calloc(1, sizeof(*p)); require(p!=NULL);
    p->dtype=identity;
    p->get_itype=get_itype; p->init_coms=init_coms; p->init_inst=init_inst;
    p->get_serial_no=get_serial; p->capabilities=capabilities;
    p->check_mode=is("mode_missing") ? NULL : check_mode;
    p->meas_config=is("configuration_missing") ? NULL : meas_config;
    p->get_disptypesel=display_types; p->get_disptechi=display_readback;
    p->get_set_opt=forbidden_options; p->set_mode=forbidden_mode;
    p->calibrate=forbidden_calibrate; p->read_sample=forbidden_read; p->del=destroy;
    return p;
}
void cm_description_backend_finished(void) {
    fprintf(stderr, "{\"enumerations\":%u,\"constructions\":%u,\"communications\":%u,\"initializations\":%u,\"configurations\":%u,\"destructions\":%u,\"paths_deleted\":%u,\"reads\":%u,\"calibrations\":%u,\"options\":%u,\"modes\":%u}\n",
        enumerations, constructions, communications, initializations, configurations,
        destructions, paths_deleted, reads, calibrations, options, modes);
}
