/* Serial paths selected by the caller's type instead of identified by probing.
 *
 * An enumerated serial path carries no identity (session_attach.c explains why),
 * and Argyll's own route to one, fast_ser_dev_type()/ser_inst_type() under
 * nocoms == 0 (inst.c:636-650), probes the port with several families' commands
 * at several rates. A driver listed in cm_typed_serial() is instead constructed
 * from the caller's type on a local copy of the one selected path with
 * nocoms == 1, so those probes are unreachable. Its init_coms then opens only
 * that port, and the driver itself must refuse any other instrument there.
 *
 * A type belongs here only when its constructor performs no I/O and its selected
 * initialization verifies the exact expected identity with documented commands.
 * PR initialization enters remote mode explicitly; CR initialization uses reads.
 * Neither initializes unselected ports or acquires/calibrates a sample. A type
 * must not also be one of cm_serial_driver_type()'s
 * families, whose route is generic identification.
 *
 * Enums added by an authenticated contribution series exist only in a build
 * that composed it; argyll_native_check.py defines CM_CONTRIBUTED_<enum> for
 * exactly the enums the series manifest declares, so a stock build compiles
 * none of them and this route stays closed there. */
#ifndef CM_SERIAL_POLICY_H
#define CM_SERIAL_POLICY_H

/* Families whose constructors Argyll can reach by identifying a serial port.
 * Keep description and acquisition on the same policy: a USB-only expected type
 * must never cause the acquisition helper to probe an unidentified port. */
static int cm_serial_driver_type(instType dtype) {
    switch (dtype) {
        case instDTP22: case instDTP41: case instDTP51:
        case instDTP92: case instDTP94:
        case instSpectrolino: case instSpectroScan: case instSpectroScanT:
        case instSpecbos1201: case instSpecbos2501: case instSpecbos: case instSpectraval:
        case instKleinK10: case instSMCube: return 1;
        default: return 0;
    }
}

static int cm_typed_serial(instType dtype) {
    switch (dtype) {
#ifdef CM_CONTRIBUTED_instCR250
        /* cr250.c: new_cr250 allocates and performs no I/O; init_coms sets
         * 115200 8N1 on the given port, and cr250_imp_identify requires the
         * exact RC Model and, from firmware 1.17, RC InstrumentType, recording
         * RC ID and RC Firmware; init_inst checks the cached firmware floor. */
        case instCR250:
            return 1;
#endif
#ifdef CM_CONTRIBUTED_instCR100
        /* The same cr250.c, on its CR-100 row: identical construction, port
         * policy and identity reads; only the exact model text differs. */
        case instCR100:
            return 1;
#endif
#ifdef CM_CONTRIBUTED_instCR300
        /* Same CR engine and exact model/type validation on its CR-300 row. */
        case instCR300:
            return 1;
#endif
#ifdef CM_CONTRIBUTED_instPR655
        /* Inert constructor; init_coms opens this one port at 115200 8N1 and
         * sends documented Q/PHOTO/D111. init_inst reads identity/grid/setup.
         * Every model report must match the explicitly selected PR-655. */
        case instPR655:
            return 1;
#endif
#ifdef CM_CONTRIBUTED_instPR670
        /* Same PR engine; exact PR-670 identity and its 201-bin grid. */
        case instPR670:
            return 1;
#endif
        default:
            return 0;
    }
}

/* icompaths_add_serial (icoms.c:269-299) never writes dtype, so an enumerated
 * serial path stays instUnknown in this build (session_attach.c). */
static int cm_unidentified_serial(const icompath *path) {
    return path->dtype == instUnknown
        && (path->dctype & (icomt_serial | icomt_fastserial | icomt_seriallike)) != 0;
}

/* Declared by each caller, as inst.c:68 and icoms.c:45 do; insttypes.h:101
 * leaves the prototype commented out. */
icom_type dev_category(instType itype);

/* The requested type goes on a copy; the enumeration's own entry stays as it
 * was listed. Type and category are what icompaths_set_serial_itype
 * (icoms.c:302-308) would write after an identification. name (the display
 * text) and spath (the device node new_icoms opens) are carried exactly as
 * enumerated, and icom_copy_path_to_icom duplicates both, so the copy need
 * outlive only construction. */
static icompath cm_typed_serial_path(const icompath *selected, instType requested) {
    icompath typed = *selected;
    typed.dtype = requested;
    typed.dctype = (typed.dctype & ~icomt_cat_mask) | dev_category(requested);
    return typed;
}
#endif
