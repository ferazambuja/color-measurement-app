/* Actual pinned-driver attachment for the generic ladder. Software checks BUILD
 * and LINK this; they never run it, because running it enumerates devices.
 *
 * This file is the whole per-instrument surface of the generic path, and it names
 * no instrument. The caller supplies the instrument as one of the strings Argyll
 * itself matches, and `inst_enum()` (insttypes.c) turns it into an `instType`.
 * That is the same division the mode word uses: Python owns the vocabulary --
 * `argyll_capability.INSTRUMENTS` is the accepted-spelling table, re-derived from
 * `inst_enum`'s own body by its provenance test -- and C owns the vtable. This
 * shared attach unit introduces no per-device selection branch.
 *
 * A name rather than an enum ordinal because insttypes.h:29-69 gives the enum no
 * explicit values: an ordinal is a position in a list upstream reorders.
 */
#include "session.h"
#include "serial_policy.h"

/* Attributed pinned-source extension: guard the detected type before dispatching
 * a constructor, including constructors that themselves communicate. */
inst *cm_new_inst_checked(icompath *, int, a1log *,
    inst_code (*)(void *, inst_ui_purp), void *, instType);

static int selected_index;
static instType expected_type = instUnknown;
static const char *expected_serial;
static int identify_serial;
static const char *expected_path;

int cm_configure(int argc, char **argv) {
    cm_selection selected;
    int consumed = cm_parse_selection(argc, argv, &selected);
    if (consumed < 0) return -1;
    /* Argyll's own name table. instUnknown is what it answers for anything it does
     * not match, and attaching "whatever turns up" is not a selection. */
    expected_type = inst_enum((char *)selected.instrument);
    if (expected_type == instUnknown) return -1;
    selected_index = selected.index;
    expected_serial = selected.serial;
    identify_serial = selected.identify_serial;
    expected_path = selected.path;
    return consumed;
}

const char *cm_provenance(void) { return "physical"; }
/* NULL, not "", when the caller pinned nothing: cm_bind tests the pointer.
 * Nine drivers inherit inst.c:99-101's default get_serial_no returning "",
 * so a mandatory pin would make them unattachable. */
const char *cm_expected_serial(void) {
    return expected_serial && expected_serial[0] ? expected_serial : NULL;
}

/* Scan events reach the wire as they arrive, so a supervised strip can report
 * progress without the ladder polling for it. */
void cm_read_event(void *context, inst_event_type purpose) {
    (void)context;
    fprintf(wire, "{\"protocol_version\":1,\"provenance\":\"physical\","
                  "\"event\":\"instrument_event\",\"event_type\":%u}\n", (unsigned)purpose);
}

static const char *attach_stage;

const char *cm_attach_stage(void) { return attach_stage; }

/* A serial instrument presents a path with NO identity, and this build does not
 * ask it for one.
 *
 * icompaths_add_serial (icoms.c:269-299) never writes dtype, and an icompath is
 * callocd (icoms.c:214), so dtype stays devUnknown == instUnknown
 * (insttypes.h:28,:87). Only icompaths_set_serial_itype (icoms.c:308) could fill
 * it, and it is reached solely from icoms_ux.c:169 and :349 behind
 * `itype = fast_ser_dev_type(...)` -- which THIS PROJECT deliberately rewrites to
 * `instUnknown` (driver_manifest.py `_passive_serial`), so no path is identified
 * by opening it. new_inst would identify one itself at inst.c:637,:645, but only
 * when nocoms is 0, and this attach passes 1 unless the caller explicitly asks
 * otherwise (below).
 *
 * So every SER_INSTS, FAST_SER_INSTS and SER_USB_INSTS family (Jamfile: dtp22,
 * dtp41, dtp51, dtp92/94, ss = Spectrolino/SpectroScan/SpectroScanT, specbos,
 * spectraval, kleink10, smcube) reaches a path whose dtype can never equal the
 * selected type. That is a deliberate consequence of not probing serial ports,
 * not an oversight -- but it must be SAID, because an operator holding a
 * SpectroScan otherwise sees only "attach failed".
 *
 * A type in serial_policy.h's cm_typed_serial() is the exception: the caller's
 * type selects its driver on a copy of the named path, nocoms stays 1 and no
 * family is identified, so --allow-serial-identification does not apply to it.
 *
 * Otherwise `--allow-serial-identification 1` lifts it for ONE attach, and nothing else
 * changes: enumeration stays passive (the _passive_serial patch is unconditional),
 * and only the single path the caller already named is opened. new_inst does the
 * identifying, at inst.c:636-650, which is why nocoms becomes 0 -- inst.c:637,:645
 * gate identification on it. The checked constructor then refuses a path that
 * turns out to be a different family before any constructor side effect
 * (driver_manifest.checked_constructor_patch inserts that test at inst.c:653,
 * after identification and before dispatch), so a wrong guess costs an open and a
 * query, never a session against the wrong instrument.
 *
 * serial_policy.h shares the eligible serial-driver families with description.c.
 * Refuse an unsupported expected family before opening a port or sending an
 * identification request. */

inst *cm_attach(inst_code (*callback)(void *, inst_ui_purp), void *context) {
    /* The caller names one already-enumerated path index; nothing here probes.
     * icomt_instrument keeps the enumeration to instrument paths, and the pinned
     * build suppresses fast-serial auto-identification (driver_manifest.py).
     *
     * icomt_portattr_all, not a hand-picked set of port bits. HID paths are
     * produced ONLY by hid_get_paths, which icoms.c:489-493 runs only when the
     * mask carries icomt_hid (0x04, icoms.h:91) -- and a mask of
     * `icomt_usb | icomt_serial` does not. Every display colorimeter that
     * presents as HID would then have no path to be indexed by, and the failure
     * would look like an absent instrument. icomt_portattr_all is
     * icomt_port_mask | icomt_attr_mask (icoms.h:105-107), which is what upstream
     * itself scans with (icoms.c:577, :713) and what description_attach.c
     * already used. */
    icompaths *paths = new_icompaths_sel(g_log, icomt_instrument | icomt_portattr_all);
    icompath *path;
    inst *meter = NULL;
    if (!paths) return NULL;
    path = paths->get_path(paths, selected_index);
    /* nocoms=1 everywhere except an explicitly permitted serial identification:
     * do not open a port merely to discover what is on it. The checked constructor
     * refuses a path whose detected type is not the selected one before any
     * constructor side effect. */
    attach_stage = NULL;
    if (!path) {
        attach_stage = "no_path_at_index";
    } else if (expected_path && (!path->name || strcmp(path->name, expected_path))) {
        attach_stage = "selected_path_changed";
    } else if (cm_unidentified_serial(path) && cm_typed_serial(expected_type)) {
        /* An index alone can move between enumerations; the typed route opens a
         * port only when the caller also pinned its OS path, checked above. */
        if (identify_serial) {
            attach_stage = "serial_identification_not_applicable";
        } else if (!expected_path) {
            attach_stage = "typed_serial_requires_path";
        } else {
            icompath typed = cm_typed_serial_path(path, expected_type);
            meter = cm_new_inst_checked(&typed, 1, g_log, callback, context, expected_type);
            if (!meter) attach_stage = "typed_serial_construction_failed";
        }
    } else if (cm_unidentified_serial(path)) {
        if (!identify_serial) {
            attach_stage = "serial_identification_required";
        } else if (!cm_serial_driver_type(expected_type)) {
            attach_stage = "expected_type_has_no_serial_driver";
        } else {
            /* nocoms = 0, and ONLY here: open this one path and let new_inst ask
             * it what it is. Every other route keeps nocoms = 1. */
            meter = cm_new_inst_checked(path, 0, g_log, callback, context, expected_type);
            if (!meter) attach_stage = "serial_identification_failed";
        }
    } else if (path->dtype != expected_type) {
        attach_stage = "different_instrument_at_index";
    } else {
        meter = cm_new_inst_checked(path, 1, g_log, callback, context, expected_type);
    }
    paths->del(paths);
    return meter;
}
