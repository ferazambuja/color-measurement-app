"""Pinned source/build reconciliation; no instrument constructors are executed."""

from __future__ import annotations

import hashlib
import json
import platform
import re
from pathlib import Path

REPAIR_ID = 'recipe:driver-manifest'

GROUPS = ("SER_INSTS", "USB_INSTS", "FAST_SER_INSTS", "SER_USB_INSTS")
UTF16_INCLUDE = '\n#include "../icc/cm_utf16_compat.h"'


def include_standalone_utf16(text: str, marker: str = '#include "inst.h"') -> str:
    """Include the standalone converter at the authenticated header boundary."""
    if text.count(marker) != 1 or UTF16_INCLUDE in text:
        raise ValueError("Pinned USB source header boundary changed")
    return text.replace(marker, marker + UTF16_INCLUDE)


def previous_standalone_utf16(text: str) -> str:
    """Undo only the optional converter include before full-file authentication."""
    if text.count(UTF16_INCLUDE) > 1:
        raise ValueError("Ambiguous standalone UTF16 include")
    return text.replace(UTF16_INCLUDE, "")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def instrument_sources(source: Path) -> tuple[str, ...]:
    """Read the public Jamfile groups, excluding optional absent/private devices."""
    jam = (source / "spectro/Jamfile").read_text()
    names = ["spectro/rspec.c"]
    for group in GROUPS:
        matches = re.findall(r"^" + group + r"\s*=\s*([^;]+);", jam, re.M)
        if len(matches) != 1:
            raise ValueError(f"Missing or ambiguous instrument source group: {group}")
        for name in matches[0].split():
            if not re.fullmatch(r"[A-Za-z0-9_]+\.c", name):
                raise ValueError(f"Unexpected instrument source: {name}")
            relative = "spectro/" + name
            if not (source / relative).is_file():
                raise ValueError(f"Missing instrument source: {relative}")
            names.append(relative)
    return tuple(dict.fromkeys(names))


# Exact ArgyllCMS 3.5.0 blocks. Kept once for forward patches and A/B recovery.
LINUX_SERIAL_OPEN_PROBE = (
    '\t\t\t/* See if the (not fast) serial port is real */\n'
    '\t\t\tif (strncmp(de->d_name, "ttyUSB", 6) != 0\n'
    '\t\t\t && strncmp(de->d_name, "ttyHS", 5) != 0\n'
    '\t\t\t && strncmp(de->d_name, "rfcomm", 6) != 0) {\n'
    '\n'
    '\t\t\t\t/* Hmm. This is probably a bad idea - it can upset other'
    ' */\n'
    '\t\t\t\t/* programs that use the serial ports ? */\n'
    '\t\t\t\tif ((fd = open(dpath, O_RDONLY | O_NOCTTY | O_NONBLOCK))'
    ' < 0) {\n'
    '\t\t\t\t\ta1logd(p->log, 8, "serial_get_paths: failed to open ser'
    'ial \\"%s\\" r/o - not real\\n",dpath);\n'
    '\t\t\t\t\tfree(dpath);\n'
    '\t\t\t\t\tmsec_sleep(100);\t\t/* Improves reliability of USB<->Seri'
    'al converters */\n'
    '\t\t\t\t\tcontinue;\n'
    '\t\t\t\t}\n'
    '\t\t\t\t/* On linux we could do a \n'
    '\t\t\t\t\tstruct serial_struct serinfo;\n'
    '\t\n'
    '\t\t\t\t\tserinfo.reserved_char[0] = 0;\n'
    '\t\n'
    '\t\t\t\t\tif (ioctl(fd, TIOCGSERIAL, &serinfo) < 0\n'
    '\t\t\t\t\t\t|| serinfo.type == PORT_UNKNOWN) {\n'
    '\t\t\t\t\t\tfree(dpath);\n'
    '\t\t\t\t\t\tcontinue;\n'
    '\t\t\t\t\t}\n'
    '\t\n'
    '\t\t\t\t */\n'
    '\t\t\t\tclose(fd);\n'
    '\t\t\t\tmsec_sleep(100);\t\t/* Improves reliability of USB<->Seria'
    'l converters */\n'
    '\t\t\t\ta1logd(p->log, 8, "serial_get_paths: open\'d serial \\"%s\\'
    '" r/o - assume real\\n",dpath);\n'
    '\t\t\t}\n'
)
SERIAL_PASSIVE_MARKER = '\t\t\t/* CM: list serial metadata without opening candidates. */\n'

WINDOWS_USB_DEBUG_WRITE = (
    '\t\t/* Set kernel message debug */\n'
    '\t\tif (p->log->debug >= 6) {\n'
    '\t\t\treq.debug.level = LIBUSB_DEBUG_MAX;\n'
    '\t\t\treq.timeout = LIBUSBW1_DEFAULT_TIMEOUT;\n'
    '\t\t\tif (do_sync_io(handle, LIBUSB_IOCTL_SET_DEBUG_LEVEL,\n'
    '\t\t\t\t&req, sizeof(libusb_request), NULL, 0, NULL)) {\n'
    '\t\t\t\ta1logd(p->log, 1, "usb_get_paths_w0 failed to set driver'
    ' log leve\\n");\n'
    '\t\t\t} else {\n'
    '\t\t\t\ta1logd(p->log, 1, "usb_get_paths_w0 turned on kernel deb'
    'ug messages\\n");\n'
    '\t\t\t}\n'
    '\t\t}\n'
)
USB_PASSIVE_MARKER = '\t\t/* CM: enumeration does not change kernel driver logging. */\n'


def _passive_serial(source: str, *, windows: bool) -> str:
    # Do not open every serial candidate to identify it. new_inst still supports
    # explicit identification of the single path selected by the caller.
    typename, count = ("devType", 1) if windows else ("instType", 2)
    original = f"{typename} itype = fast_ser_dev_type(icom, 0, NULL, NULL);"
    if source.count(original) != count:
        raise ValueError("Pinned automatic serial identification calls changed")
    if not windows:
        # Argyll 3.5.0 icoms_ux.c:284-312 opens ordinary tty candidates simply
        # to judge whether they exist. Listing metadata must not seize a port.
        if source.count(LINUX_SERIAL_OPEN_PROBE) != 1:
            raise ValueError("Pinned Linux serial candidate-open block changed")
        source = source.replace(LINUX_SERIAL_OPEN_PROBE, SERIAL_PASSIVE_MARKER)
    return source.replace(original, f"{typename} itype = instUnknown; /* CM: passive catalog */")


def passive_usb_patch(source: str) -> str:
    """Descriptor listing may query the driver, but must not change its logging."""
    if source.count(WINDOWS_USB_DEBUG_WRITE) != 1:
        raise ValueError("Pinned Windows USB enumeration debug block changed")
    return source.replace(WINDOWS_USB_DEBUG_WRITE, USB_PASSIVE_MARKER)


def passive_serial_patches() -> dict:
    return {
        "spectro/icoms_ux.c": lambda source: _passive_serial(source, windows=False),
        "spectro/icoms_nt.c": lambda source: _passive_serial(source, windows=True),
        "spectro/usbio_w0.c": passive_usb_patch,
        "spectro/inst.c": checked_constructor_patch,
    }


def _constructor_code(source: str) -> str:
    """Remove C comments and literals so examples/log messages are not calls."""
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*.*?\*/|//[^\n]*',
                  " ", source, flags=re.S)


def _require_constructor_guard(body: str) -> str:
    """Check the pinned guard shape and ordering before device constructors.

    new_icoms allocates the transport needed for identification before the guard.
    Every other new_* call in this function must follow the refusing guard.
    This also runs on compiler-selected source after all build patches compose.
    """
    code = _constructor_code(body)
    guards = list(re.finditer(
        r"if\s*\(\s*expected_type\s*!=\s*(?:instUnknown|devUnknown)\s*&&\s*"
        r"itype\s*!=\s*expected_type\s*\)\s*\{\s*"
        r"icom\s*->\s*del\s*\(\s*icom\s*\)\s*;\s*return\s+"
        r"(?:NULL|0|\(\s*\(\s*void\s*\*\s*\)\s*0\s*\))\s*;\s*\}",
        code,
    ))
    if len(guards) != 1:
        raise ValueError("Expected-type constructor guard is missing or changed")
    for call in re.finditer(r"\b(new_\w+)\s*\(", code[:guards[0].end()]):
        if call[1] != "new_icoms":
            raise ValueError(f"Instrument constructor {call[1]} appears before expected-type guard")
    return code


def checked_constructor_patch(source: str) -> str:
    """Refuse an unexpected identified serial family before its constructor runs."""
    start = source.index("extern inst *new_inst(")
    end = source.index("\n/* --------------------------------------------------- */", start)
    body = source[start:end]
    signature_end = body.index(") {")
    signature = body[:signature_end]
    if not signature.endswith("\n") or "void *cntx" not in signature:
        raise ValueError("Pinned constructor signature changed")
    # The context declaration carries a source comment: add a comma before it.
    signature = signature.replace("extern inst *new_inst(", "inst *cm_new_inst_checked(")
    signature = signature.replace("void *cntx\t", "void *cntx,\t")
    signature += "instType expected_type\n"
    marker = "#ifdef ENABLE_SERIAL\n\tif (itype == instDTP22)"
    if body.count(marker) != 1:
        raise ValueError("Pinned constructor dispatch boundary changed")
    body = signature + body[signature_end:]
    body = body.replace(marker,
        "/* CM: reject a different family before any constructor side effects. */\n"
        "\tif (expected_type != instUnknown && itype != expected_type) {\n"
        "\t\ticom->del(icom);\n\t\treturn NULL;\n\t}\n\n" + marker)
    _require_constructor_guard(body)
    wrapper = (
        "\n/* CM: preserve the original upstream entry point for existing callers. */\n"
        "inst *new_inst(icompath *path, int nocoms, a1log *log,\n"
        "    inst_code (*uicallback)(void *, inst_ui_purp), void *cntx) {\n"
        "    return cm_new_inst_checked(path, nocoms, log, uicallback, cntx, instUnknown);\n"
        "}\n"
    )
    return source[:start] + body + wrapper + source[end:]


def parse_dispatch(preprocessed: str) -> dict[str, str]:
    """Reconcile active compiler-selected constructor branches, not comments."""
    signature = re.search(r"inst \*cm_new_inst_checked\([^{}]*\)\s*\{", preprocessed)
    if signature is None:
        raise ValueError("Preprocessed new_inst definition is missing")
    body = preprocessed[signature.end():].split("p->vdel = virtual_del;", 1)[0]
    body = _require_constructor_guard(body)
    result = {}
    for condition, constructor in re.findall(
        r"if\s*\(([^{};]*?)\)\s*p\s*=\s*\(inst\s*\*\)\s*(new_\w+)\(icom,\s*itype\);",
        body, re.S,
    ):
        names = re.findall(r"itype\s*==\s*(inst\w+)", condition)
        if not names:
            raise ValueError("Constructor branch has no recognized instrument type")
        for name in names:
            if name in result:
                raise ValueError(f"Duplicate active constructor for {name}")
            result[name] = constructor
    if not result:
        raise ValueError("No active upstream constructors found")
    return result


def _between(source: str, opening: str, closing: str) -> str:
    """The text of one function, comments removed, refusing a moved boundary.

    Comments come out here because the case labels fall through to a shared
    return, and a comment between two of them is not a break in the fall-through
    -- but it IS a break in `\\s*`.
    """
    if opening not in source or closing not in source.split(opening, 1)[1]:
        raise ValueError(f"description.c boundary not found: {opening!r} .. {closing!r}")
    body = source.split(opening, 1)[1].split(closing, 1)[0]
    return re.sub(r"/\*.*?\*/", " ", body, flags=re.S)


def parse_init_refusals(description_source: str) -> dict[str, str]:
    """``instType`` symbol -> the reason ``init_refusal`` gives for it.

    Comments come out first. The labels fall through to a shared ``return``, and a
    comment sitting between two of them is not a break in the fall-through -- but
    it IS a break in ``\\s*``, so matching the raw text drops the earlier labels
    and publishes those instruments as carrying no refusal. That is how
    ``instColorHug`` and ``instColorHug2`` were published as unrefused while
    ``description.c`` refused them, in the fail-open direction: a client would have
    opened them believing nothing was disclosed.

    Every case label must reach a reason. Checking only for a symbol the parser
    invented catches the harmless direction; this catches the one it loses.
    """
    body = _between(description_source, "static const char *init_refusal(",
                    "static int init_harmless")
    refusals: dict[str, str] = {}
    for cases, reason in re.findall(
        r'((?:case\s+inst\w+:\s*)+)return\s+"([a-z_]+)";', body,
    ):
        for enum in re.findall(r"case\s+(inst\w+):", cases):
            refusals[enum] = reason
    labelled = set(re.findall(r"case\s+(inst\w+):", body))
    if not refusals or labelled != refusals.keys():
        raise ValueError(
            "init_refusal case labels did not all reach a reason: "
            f"{sorted(labelled - refusals.keys())}")
    return refusals


def parse_init_harmless(description_source: str) -> set[str]:
    """The ``instType`` symbols ``init_harmless`` names.

    Published alongside the refusals because "no refusal" says two different
    things -- assessed and harmless, or nobody looked -- and only one of them is a
    claim this project can make. A client resolving an instrument by type needs
    the same three facts the enumeration already carries per path.
    """
    body = _between(description_source, "static int init_harmless(", "static int init_assessed")
    return set(re.findall(r"case\s+(inst\w+):", body))


def parse_typed_serial(description_source: str) -> set[str]:
    """The ``instType`` symbols ``serial_policy.h``'s ``cm_typed_serial`` admits.

    Those are selected by the caller's type on an unidentified serial path, with
    no generic identification. A client must know which, because it must not ask
    for ``--allow-serial-identification`` there and must ask for it elsewhere.
    """
    body = _between(description_source, "static int cm_typed_serial(",
                    "static int cm_unidentified_serial(")
    return set(re.findall(r"case\s+(inst\w+):", body))


def guarded_typed_serial(policy_source: str) -> set[str]:
    """Types ``cm_typed_serial`` admits under their own ``CM_CONTRIBUTED_<enum>``.

    Read from the RAW header, so it names what the policy would admit in a build
    that composed each series, whatever this build defined. A block whose macro
    names a different enum than its case label is refused rather than trusted.
    """
    body = policy_source[policy_source.index("static int cm_typed_serial("):]
    body = body[:body.index("static int cm_unidentified_serial(")]
    found = set()
    for macro, block in re.findall(r"#ifdef CM_CONTRIBUTED_(inst\w+)(.*?)#endif", body, re.S):
        labels = set(re.findall(r"case\s+(inst\w+):", block))
        if labels != {macro}:
            raise ValueError(f"serial_policy.h guard names {macro} but admits {sorted(labels)}")
        found.add(macro)
    return found


def parse_serial_driver_types(description_source: str) -> set[str]:
    """Families admitted by the compiled shared ``cm_serial_driver_type`` policy."""
    body = _between(description_source, "static int cm_serial_driver_type(",
                    "static int cm_typed_serial(")
    return set(re.findall(r"case\s+(inst\w+):", body))


#: A driver's own lower bound on one read, where it declares one: the header and
#: macro it lives in, read from the COMPOSED source so the host cannot be told
#: less. It is host measurement policy in the driver, not an instrument property.
READ_BUDGET_FLOORS = {enum: ("spectro/cr250_imp.h", "CR250_MEAS_FLOOR_S")
                      for enum in ("instCR250", "instCR100", "instCR300")}
READ_BUDGET_FLOORS.update({enum: ("spectro/pr655.c", "MEASUREMENT_SECONDS")
                          for enum in ("instPR655", "instPR670")})


def read_budget_floor(source: Path, enum: str) -> float | None:
    """The declared floor for ``enum`` in this composed tree, or None.

    None where no driver declares one, or where the declaring header is absent
    because the build did not compose that driver's series. A present header
    whose macro moved is refused rather than read as "no floor".
    """
    declared = READ_BUDGET_FLOORS.get(enum)
    if declared is None or not (source / declared[0]).is_file():
        return None
    text = (source / declared[0]).read_text(encoding="utf-8")
    found = re.findall(rf"^#define\s+{declared[1]}\s+([0-9]+(?:\.[0-9]+)?)\b", text, re.M)
    if len(found) != 1:
        raise ValueError(f"{declared[1]} is not one plain #define in {declared[0]}")
    return float(found[0])


def parse_instrument_types(header: str, added: tuple[str, ...] = ()) -> list[dict]:
    """Read pinned contiguous public measurement enums and their source labels.

    The stock header pins 38 enums ending in ``instColorHug2``. An authenticated
    contribution series may declare enums it appends; they must follow the
    pinned 38 in the declared order, so earlier values never move.
    """
    boundary = re.search(r"typedef\s+enum\s*\{(.*?)\binstFakeDisp\s*=\s*9998\s*,",
                         header, re.S)
    if boundary is None:
        raise ValueError("Pinned instrument enum boundary changed")
    before = boundary[1]
    result = []
    for match in re.finditer(r"^\s*(inst\w+)\s*,\s*/\*\s*(.*?)\s*\*/", before, re.M):
        result.append({"enum": match[1], "source_label": match[2], "native_type": len(result) + 1})
    pinned = 38
    names = [row["enum"] for row in result]
    # Pin the complete ordered 3.5.0 enum identity, independently of labels.
    # Parse every token too: an explicit assignment or an unlabelled enum must
    # not change C's numeric values while escaping the label extractor above.
    code = re.sub(r"\s+", "", re.sub(r"/\*.*?\*/|//[^\n]*", "", before, flags=re.S))
    expected = "devUnknown=0," + ",".join(names) + ","
    stock_identity = "f0abb1690358ff4b37fc2f37b8a4214f44f73f12c3a5da9993c65caebea401ae"
    if (code != expected or len(result) < pinned
            or sha256(",".join(names[:pinned]).encode()) != stock_identity):
        raise ValueError("Pinned public instrument enum set changed")
    if (names[pinned:] != list(added) or len(set(names)) != len(names)
            or "instFakeDisp" in added):
        raise ValueError("Instrument enums after the pinned set differ from the declared series")
    return result


def reconcile_linked_drivers(source, output, report, cc, flags, library, command) -> dict:
    """Use actual preprocessor output and defined archive symbols as link evidence."""
    preprocessed = command([cc, *flags, "-E", "-P", str(source / "spectro/inst.c")], output, report)
    (output / "instrument-dispatch.preprocessed.c").write_text(preprocessed)
    dispatch = parse_dispatch(preprocessed)
    symbol_flags = ["-gU"] if platform.system() == "Darwin" else ["-g", "--defined-only"]
    symbols = command(["nm", *symbol_flags, str(library)], output, report)
    defined = set(re.findall(r"\bT\s+_?(new_\w+)\s*$", symbols, re.M))
    missing = set(dispatch.values()) - defined
    if missing:
        raise ValueError(f"Constructor symbols absent from linked archive: {sorted(missing)}")
    header = (source / "spectro/insttypes.h").read_text()
    added = tuple(report.get("contribution", {}).get("instrument_types_added", ()))
    catalog = parse_instrument_types(header, added) if added else parse_instrument_types(header)
    omitted = {row["enum"] for row in catalog} - dispatch.keys()
    if omitted != {"instSpectrocam"}:
        raise ValueError(f"Unexpected public driver exclusions: {sorted(omitted)}")
    if dispatch.keys() - {row["enum"] for row in catalog}:
        raise ValueError("Active constructor outside public measurement catalog")
    description_path = Path(__file__).parent / "description.c"
    description_source = description_path.read_text()
    serial_policy = description_path.with_name("serial_policy.h").read_bytes()
    # Parse the policy this build compiles. A contributed enum appears only under
    # CM_CONTRIBUTED_<enum>, so the raw text names types a stock build lacks.
    active = command([cc, *flags, "-E", "-P", str(description_path)], output, report)
    refusals = parse_init_refusals(active)
    harmless = parse_init_harmless(active)
    typed = parse_typed_serial(active)
    identified = parse_serial_driver_types(active)
    if (refusals.keys() - dispatch.keys() or harmless - dispatch.keys()
            or typed - dispatch.keys() or identified - dispatch.keys()):
        raise ValueError("Unreconciled description initialization refusals")
    if refusals.keys() & harmless:
        raise ValueError(
            f"Instruments both refused and called harmless: {sorted(refusals.keys() & harmless)}")
    # The compiled policy can only drop a guarded type silently: lose the
    # -DCM_CONTRIBUTED_<enum> flag and the composed build compiles as stock, the
    # catalog carries no typed rows and every typed check has nothing to run. So a
    # type the manifest declares, which serial_policy.h guards for its route, must
    # be in the compiled set.
    guarded = guarded_typed_serial(serial_policy.decode())
    declared = set(report.get("contribution", {}).get("instrument_types_added", ()))
    if (guarded & declared) - typed:
        raise ValueError(
            "Declared contribution types lost their typed serial route (is "
            f"-DCM_CONTRIBUTED_<enum> missing?): {sorted((guarded & declared) - typed)}")
    if typed & identified or typed - (harmless | refusals.keys()):
        # Typed selection skips generic probing. Its exact identity startup must
        # be assessed; disclosed effects still require explicit acceptance.
        raise ValueError(f"Typed serial selection outside its policy: {sorted(typed)}")
    for row in catalog:
        constructor = dispatch.get(row["enum"])
        row.update(
            constructor=constructor,
            linked=constructor is not None,
            application_measurement_routes=[],
            physical_tested=False,
            exclusion_reason=(None if constructor else "Upstream constructor marked NYI in inst.c"),
            description_route=(
                "not_linked" if constructor is None else "initialization_refused"
                if row["enum"] in refusals else "selected_initialization"
            ),
            initialization_refusal=refusals.get(row["enum"]),
            # `init_assessed` in description.c: a refusal or an entry in the
            # harmless list. Absence of a refusal alone is NOT an assurance.
            initialization_assessed=(
                row["enum"] in refusals or row["enum"] in harmless),
            typed_serial_selection=row["enum"] in typed,
            generic_serial_identification=row["enum"] in identified,
            measurement_budget_floor_s=read_budget_floor(source, row["enum"]),
        )
        if row["linked"] and row["initialization_refusal"] != "initialization_downloads_pld":
            # The shared application can attempt capability-confirmed workflows
            # for this linked type. Firmware-loading initialization remains
            # prohibited; linkage must not turn that into an acquisition claim.
            # This field is an implementation route, not physical validation.
            #
            # The modes are deliberately NOT enumerated. They come from the
            # instrument's own capability words at bind time (inst.h:305-306 --
            # the capability macro is advisory and check_mode() is definitive),
            # and this manifest is a static source catalog with no device
            # discovery. Listing modes here would be asserting statically what
            # only an attached instrument can answer.
            row["application_measurement_routes"] = ["generic_ladder:capability_derived"]
    manifest = {
        "schema_version": 1,
        "argyll_version": report["argyll_version"],
        "archive_sha256": report["archive_sha256"],
        "platform": report["platform"],
        "scope": "static source catalog reconciled with this macOS build; no device discovery",
        "build_scope": report["build_scope"],
        "coverage_summary": {
            "linked_instrument_type_count": sum(row["linked"] for row in catalog),
            "application_measurement_instrument_type_count": sum(
                bool(row["application_measurement_routes"]) for row in catalog
            ),
            "application_measurement_mode_route_count": sum(
                len(row["application_measurement_routes"]) for row in catalog
            ),
            "hardware_tested": False,
        },
        "driver_groups": list(GROUPS),
        "instrument_types": catalog,
        "linked_constructor_count": len(set(dispatch.values())),
        "linked_instrument_type_count": len(dispatch),
        "library_sha256": sha256(library.read_bytes()),
        "preprocessed_dispatch_sha256": sha256(preprocessed.encode()),
        "defined_symbols_sha256": sha256(symbols.encode()),
        "description_policy_sha256": sha256(description_source.encode()),
        "serial_policy_sha256": sha256(serial_policy),
        "excluded_optional_sources": [
            {"path": "spectro/d123.c", "reason": "Absent from pinned public archive"},
            {"path": "spectro/demoinst.c", "reason": "Absent optional synthetic driver"},
            {"path": "spectro/vtpglut.c", "reason": "Not a measurement-instrument driver"},
            {"path": "spectro/printers.c", "reason": "Not a measurement-instrument driver"},
            {"path": "spectro/colvis.c", "reason": "Absent optional source"},
            {"path": "spectro/ledcool.c", "reason": "Absent optional source"},
            {"path": "spectro/fastserio.c", "reason": "Absent optional source"},
        ],
        "physical_tested": False,
        "real_helper_executed": False,
    }
    (output / "driver-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
