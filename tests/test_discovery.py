"""Turning one enumeration into a list a user interface can offer."""

import json
import platform
from pathlib import Path

import pytest
from conftest import native_display_unsupported

from color_measurement.argyll_capability import baseline_mode_words
from color_measurement.discovery import (
    DiscoveredInstrument,
    discovered_instruments,
    instrument_catalog,
)
from color_measurement.native_receipt import native_worker_name

# The build's own catalog shape, from driver_manifest.reconcile_linked_drivers.
# It carries the same three initialisation facts per TYPE that an enumeration
# carries per PATH, which is what lets a serial unit be judged once it says what
# it is. DTP22 and SpectroScan really do disclose; the i1Pro3 really is assessed.
CATALOG = json.dumps({"instrument_types": [
    {"enum": "instDTP22", "native_type": 1, "source_label": "Xrite DTP22",
     "initialization_assessed": True,
     "initialization_refusal": "initialization_resets_device",
     "generic_serial_identification": True},
    {"enum": "instSpectroScan", "native_type": 5, "source_label": "GretagMacbeth SpectroScan",
     "initialization_assessed": True,
     "initialization_refusal": "initialization_or_cleanup_moves_table",
     "generic_serial_identification": True},
    {"enum": "instI1Pro3", "native_type": 21, "source_label": "X-Rite i1 Pro 3",
     "initialization_assessed": True, "initialization_refusal": None},
    {"enum": "instI1Disp3", "native_type": 17, "source_label": "X-Rite i1 DisplayPro",
     "initialization_assessed": True,
     "initialization_refusal": "initialization_changes_indicator_leds"},
]})

# A catalog from before those facts were published. Nothing may read as harmless
# because of what it does not say.
SILENT_CATALOG = json.dumps({"instrument_types": [
    {"enum": "instI1Pro3", "native_type": 21, "source_label": "X-Rite i1 Pro 3"},
]})


def _candidates(*rows):
    return json.dumps({"candidates": [
        {"path": path, "dtype": dtype, "transport": transport,
         # Every identified row is one this build HAS assessed; the unassessed
         # case has its own test, because it is the one that used to read as safe.
         # An unidentified path (dtype 0) is reported as the worker reports it
         # (description.c:253): `instUnknown` is never assessed. Rows claiming
         # otherwise hid a gate on the port that refused every serial selection.
         "initialization_assessed": dtype != 0, "initialization_refusal": refusal}
        for path, dtype, transport, refusal in rows
    ]})


def test_an_identified_device_is_named_from_the_builds_own_catalog():
    """The ordinal is a build artifact, not a Python table: insttypes.h assigns
    the enum no explicit values, so an ordinal is a position upstream may reorder.
    """
    (found,) = discovered_instruments(
        _candidates(("usb:/bus0/dev3", 21, 0x02, None)), CATALOG)
    assert found == DiscoveredInstrument(
        index=1, native_type=21, symbol="instI1Pro3", name="X-Rite i1 Pro 3",
        path="usb:/bus0/dev3", transport=0x02, initialization_assessed=True,
        initialization_refusal=None)
    assert found.identified and found.attachable
    assert found.transports == ("usb",)


def test_an_unidentified_serial_path_is_kept_and_marked():
    """Dropping it would hide twelve instruments -- the SpectroScan among them --
    from a UI with no other way to learn they are attached. icompaths_add_serial
    never writes dtype (icoms.c:269-299) and this build does not probe."""
    (found,) = discovered_instruments(
        _candidates(("/dev/tty.usbserial-A1", 0, 0x01, None)), CATALOG)
    assert found.native_type == 0
    assert found.name is None and found.symbol is None
    assert not found.identified and not found.attachable
    assert found.transports == ("serial",)


def test_display_names_do_not_replace_the_native_binding_alias_or_path_identity():
    from color_measurement.argyll_capability import CAPABILITY2, mode_word
    from color_measurement.instrument import Instrument
    from color_measurement.instrument_frontend import InstrumentFrontend

    catalog = json.dumps({'instrument_types': [
        {'enum': 'instSpyder2024', 'native_type': 42, 'source_label': 'unused alias'}]})
    (found,) = discovered_instruments(_candidates(('hid:/spyder', 42, 0x04, None)), catalog)
    assert found.name == 'Datacolor Spyder 2024'
    assert found.symbol == 'instSpyder2024' and found.native_type == 42
    assert found.path == 'hid:/spyder'
    meter = Instrument('/never-started', found.name)
    assert meter.instrument == 'GretagMacbeth Spyder 2024'
    front = InstrumentFrontend(meter, mode_capabilities=mode_word('emissive_spot', spectral=False),
        trigger_capabilities=CAPABILITY2['prog_trig'], filter_capabilities=0,
        provenance='simulated', device_id=found.path)
    identity = json.loads(front.describe_json())['device']
    assert identity['model'] == found.name and identity['device_id'] == found.path


def test_a_device_the_catalog_knows_but_this_package_does_not_is_not_invented():
    """A build carrying a type absent from INSTRUMENTS must not get a made-up
    name; it is reported unidentified so a caller cannot attach by guesswork."""
    catalog = json.dumps({"instrument_types": [
        {"enum": "instSomethingNewer", "native_type": 40, "source_label": "?"}]})
    (found,) = discovered_instruments(_candidates(("usb:/x", 40, 0x02, None)), catalog)
    assert found.native_type == 40
    assert found.symbol is None and found.name is None
    assert not found.identified


def test_indices_are_positions_in_this_enumeration():
    found = discovered_instruments(_candidates(
        ("hid:/a", 17, 0x04, None),
        ("usb:/b", 21, 0x02, None),
        ("/dev/tty.c", 0, 0x01, None),
    ), CATALOG)
    assert [d.index for d in found] == [1, 2, 3]
    assert [d.name for d in found] == [
        "X-Rite i1 DisplayPro, ColorMunki Display", "X-Rite i1 Pro 3", None]
    # HID paths are added first (icoms.c:489-497), which is why an index is only
    # meaningful for the enumeration it came from.
    assert found[0].transports == ("hid",)


def test_a_refused_device_is_listed_but_not_attachable():
    (found,) = discovered_instruments(
        _candidates(("usb:/x", 21, 0x02, "firmware update required")), CATALOG)
    assert found.identified
    assert not found.attachable
    assert found.initialization_refusal == "firmware update required"


@pytest.mark.parametrize("bad,refusal", [
    ('{"candidates": "not a list"}', "Enumeration carries no candidates"),
    # Iterates as nothing: refused, never read as no instruments.
    ('{"candidates": ""}', "Enumeration carries no candidates"),
    ('{}', "Enumeration carries no candidates"),
    ('{"candidates": [{"dtype": "x"}]}', "Invalid candidate metadata"),
    ('{"candidates": [{"dtype": -1}]}', "Invalid candidate metadata"),
])
def test_a_malformed_enumeration_is_refused(bad, refusal):
    with pytest.raises(ValueError, match=refusal):
        discovered_instruments(bad, CATALOG)


@pytest.mark.parametrize("bad", [
    '{}', '{"instrument_types": []}', '{"instrument_types": [{"enum": "a"}]}',
    '{"instrument_types": [{"enum": "a", "native_type": 1}, '
    '{"enum": "b", "native_type": 1}]}',
])
def test_a_malformed_catalog_is_refused(bad):
    with pytest.raises(ValueError):
        instrument_catalog(bad)


def test_transport_labels_match_the_pinned_native_port_bits(argyll_archive):
    import re
    import zipfile

    with zipfile.ZipFile(argyll_archive) as archive:
        header = archive.read("Argyll_V3.5.0/spectro/icoms.h").decode()
    values = {name: int(value, 16) for name, value in re.findall(
        r"^\s*(icomt_\w+)\s*=\s*(0x[0-9a-fA-F]+)\b", header, re.M)}
    cases = [
        (("icomt_serial",), ("serial",)),
        (("icomt_usb",), ("usb",)),
        (("icomt_hid",), ("hid",)),
        (("icomt_bt",), ("bluetooth",)),
        (("icomt_serial", "icomt_usb", "icomt_fastserial"),
         ("serial", "usb", "fastserial")),
    ]
    for flags, labels in cases:
        mask = sum(values[name] for name in flags)
        (found,) = discovered_instruments(_candidates(("native:port", 0, mask, None)), CATALOG)
        assert found.transport == mask
        assert found.transports == labels


def test_the_real_catalog_covers_every_instrument_this_package_names(argyll_archive):
    """The two tables must agree: 38 instruments, every ordinal resolving to a
    name inst_enum() accepts."""
    import importlib.util
    import sys
    import zipfile

    from color_measurement.argyll_capability import INSTRUMENTS

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "tools"))
    spec = importlib.util.spec_from_file_location(
        "cm_driver_manifest_discovery", root / "tools/native_argyll/driver_manifest.py")
    driver_manifest = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = driver_manifest
    spec.loader.exec_module(driver_manifest)

    with zipfile.ZipFile(argyll_archive) as archive:
        header = archive.read("Argyll_V3.5.0/spectro/insttypes.h").decode()
    rows = driver_manifest.parse_instrument_types(header)
    catalog = instrument_catalog(json.dumps({"instrument_types": rows}))
    assert len(catalog) == 38
    assert set(catalog.values()) == set(INSTRUMENTS)


def test_an_unassessed_initialization_is_not_reported_attachable():
    """The last fail-open in the per-instrument tables.

    `attachable` used to be `identified and initialization_refusal is None`, so an
    instrument whose driver nobody had read passed on the strength of a silence --
    indistinguishable from one read and found harmless. The absence of a refusal
    is now only an assurance when the assessment happened.

    It is not a refusal: the instrument is reported unassessed and the caller
    decides. Refusing outright would make every future instrument unusable until
    someone read its driver.
    """
    (found,) = discovered_instruments(
        _candidates(("usb:/bus0/dev3", 21, 0x02, None)), CATALOG)
    assert found.attachable is True and found.initialization_assessed is True

    unassessed = json.loads(_candidates(("usb:/bus0/dev3", 21, 0x02, None)))
    unassessed["candidates"][0]["initialization_assessed"] = False
    (found,) = discovered_instruments(json.dumps(unassessed), CATALOG)
    assert found.identified is True and found.initialization_refusal is None
    assert found.initialization_assessed is False
    assert found.attachable is False, (
        "an unread driver must not read as safe merely because nothing was said")


# --- the composed entry point --------------------------------------------------
#
# A UI needs one call for "Refresh devices" and a second, per unit, for "open
# this one". These drive `InstrumentBuild` through a recording double rather than
# a worker: the wire itself is checked by the native description checks, and what
# is worth holding here is the POLICY -- what opens, what refuses, and what a
# refusal costs a caller who wants to proceed anyway.


def _envelope(data=None, error=None):
    """The shape every NativeDescriptionClient method actually returns.

    The double answers in envelopes rather than in the payload the parser wants,
    because the first version of it did the latter and hid a real defect: the
    composed call read `data` straight off the envelope and saw no candidates at
    all. A double shaped like the caller's assumption cannot contradict it.
    """
    return json.dumps({
        "schema_version": 1, "operation_id": "fixture", "provenance": "simulated",
        "backend": {"version": "3.5.0", "helper_sha256": "1" * 64,
                    "build_report_sha256": None}, "events": [],
        "status": "failed" if error is not None else "completed",
        "data": data, "error": error,
    })


class RecordingClient:
    """A description client that answers from fixtures and records what was asked."""

    def __init__(self, candidates, *, catalog=CATALOG, describe=None, enumeration_error=None):
        self._candidates = candidates
        self._catalog = catalog
        self._enumeration_error = enumeration_error
        self._describe = describe or {
            "initialized_dtype": 21, "serial": "SN-1",
            "capabilities": {"primary": 0x21, "secondary": 0x30001, "tertiary": 0},
        }
        self._helper_sha256 = "1" * 64
        self._report_sha256 = None
        self._describe.setdefault("native_identity", None)
        # These policy tests do not implement check_mode; report it unavailable.
        primary = self._describe["capabilities"]["primary"]
        self._describe.setdefault("baseline_mode_checks", {
            "scope": "initialized_before_mode_configuration", "mode_capabilities": primary,
            "checks": [{"mode_word": word, "native_code": None}
                       for word in baseline_mode_words(primary)]})
        self._describe.setdefault("strip_policies", {
            name: {"max_patches": 0, "requirement": None, "layout_fields": []}
            for name in ("reflection", "transmission", "emission")})
        self._describe.setdefault("display_types", native_display_unsupported() | {"entries": []})
        self.enumerations = 0
        self.describes = []
        self.last_events = ()

    def list_candidates_json(self):
        self.enumerations += 1
        if self._enumeration_error is not None:
            return _envelope(error=self._enumeration_error)
        return _envelope(json.loads(self._candidates))

    def catalog_json(self):
        return self._catalog

    def describe_json(self, candidate_json, **kwargs):
        self.describes.append((json.loads(candidate_json), kwargs))
        return _envelope(self._describe)


def _build(candidates, **kwargs):
    from color_measurement.discovery import InstrumentBuild

    client = RecordingClient(candidates, **kwargs)
    return InstrumentBuild(client, Path("/nonexistent/generic-worker-never-run"),
                           expected_helper_sha256="0" * 64), client


def test_build_bound_frontend_registers_in_desktop_without_additional_device_access(monkeypatch):
    from color_measurement.demo import is_demo_frontend
    from color_measurement.desktop_session import DesktopSession
    from color_measurement.instrument import Instrument

    monkeypatch.setattr(
        Instrument, "_open_session", lambda *args, **kwargs: pytest.fail("Native transport"),
    )
    build, client = _build(_candidates(("usb:/recording-only", 21, 0x02, None)))
    client._describe["provenance"] = "simulated"
    chosen, = build.attached()
    frontend = build.bind(build.describe(chosen))
    session = DesktopSession([])
    token = session.register_frontend(frontend)
    settings = session.settings(token)
    assert settings["descriptor"] == json.loads(frontend.describe_json())
    assert settings["selection_id"] == token
    assert settings["descriptor"]["device_id"] == chosen.path
    assert settings["descriptor"]["provenance"] == "simulated"
    assert not is_demo_frontend(frontend)
    assert client.enumerations == 1 and len(client.describes) == 1
    assert session.snapshot()["batch"] is None
    assert session.dataset()["dataset_revision"] == "empty"


def test_static_build_catalogue_does_not_enumerate_or_describe_devices():
    build, client = _build(_candidates(("usb:/a", 21, 0x02, None)))
    assert build.catalog_json() == CATALOG
    reply = json.loads(build.catalog_json())
    reply["instrument_types"].clear()
    assert build.catalog_json() == CATALOG
    assert client.enumerations == 0 and client.describes == []


def test_refreshing_the_device_list_opens_nothing():
    """The whole of a Refresh action. Enumeration reads the IORegistry on every
    macOS transport -- usbio_ox.c:34-98, hidio.c:288-297 and its own comment,
    icoms_ux.c for serial -- so it cannot disturb a unit another process holds."""
    build, client = _build(_candidates(
        ("hid:/a", 17, 0x04, None), ("/dev/tty.b", 0, 0x01, None)))
    found = build.attached()
    assert [d.index for d in found] == [1, 2]
    assert client.enumerations == 1
    assert client.describes == [], "listing devices must not open any of them"


def test_a_disclosed_initialisation_is_refused_until_it_is_accepted_by_name():
    """i1d3.c:3079 flashes the LED on init. The operator should be told before it
    happens, not after, so the disclosure is operative rather than advisory."""
    build, client = _build(_candidates(
        ("hid:/a", 17, 0x04, "initialization_changes_indicator_leds")))
    (found,) = build.attached()
    assert not found.attachable
    with pytest.raises(ValueError, match="initialization_changes_indicator_leds"):
        build.describe(found)
    assert client.describes == [], "the refusal must come before the instrument is opened"
    described = build.describe(found, accept_disclosed_initialization=True)
    assert described.mode_capabilities == 0x21
    assert len(client.describes) == 1


def test_accepting_a_known_effect_does_not_accept_an_unknown_one():
    """The two acknowledgements are separate on purpose. A caller who agrees that
    an LED may flash has not thereby agreed to open a driver nobody has read --
    one flag covering both would be the same fail-open `attachable` used to have.
    """
    rows = json.loads(_candidates(("hid:/a", 17, 0x04, None)))
    rows["candidates"][0]["initialization_assessed"] = False
    build, client = _build(json.dumps(rows))
    (found,) = build.attached()
    assert found.identified and found.initialization_refusal is None
    with pytest.raises(ValueError, match="unassessed"):
        build.describe(found, accept_disclosed_initialization=True)
    assert client.describes == []
    assert build.describe(found, accept_unassessed_initialization=True).serial == "SN-1"


def test_a_unit_says_what_opening_it_needs_and_a_firmware_download_needs_what_nothing_gives():
    """A picker labels and enables a unit from `opening_requires`. Spyder1/2 used
    to read like an LED unit -- a disclosure and not attachable -- and describe
    asked for the one acknowledgement describe_json then refuses."""
    rows = json.loads(_candidates(
        ("hid:/a", 17, 0x04, None), ("hid:/b", 17, 0x04, "initialization_changes_indicator_leds"),
        ("hid:/c", 17, 0x04, None), ("/dev/tty.d", 0, 0x01, None),
        ("usb:/e", 17, 0x02, "initialization_downloads_pld")))
    rows["candidates"][2]["initialization_assessed"] = False
    build, client = _build(json.dumps(rows))
    found = build.attached()
    assert [unit.opening_requires for unit in found] == [
        None, "accept_disclosed_initialization", "accept_unassessed_initialization",
        "expect", "not_permitted"]
    for flags in ({}, {"accept_disclosed_initialization": True},
                  {"accept_disclosed_initialization": True,
                   "accept_unassessed_initialization": True}):
        with pytest.raises(ValueError, match="nothing opens it") as refused:
            build.describe(found[4], **flags)
        assert "Pass" not in str(refused.value)
    assert client.describes == [], "refused before the instrument is opened"


def test_an_unidentified_path_needs_both_an_expected_type_and_the_permission():
    """A serial port carries no identity until it is opened (icoms.c:269-299), so
    naming what it should be and permitting the open are two separate facts."""
    build, _ = _build(_candidates(("/dev/tty.a", 0, 0x01, None)))
    (found,) = build.attached()
    with pytest.raises(ValueError, match="expect="):
        build.describe(found)
    with pytest.raises(ValueError, match="allow_serial_identification"):
        build.describe(found, expect="GretagMacbeth SpectroScan",
                       accept_disclosed_initialization=True)
    with pytest.raises(ValueError, match="carries no instrument named"):
        build.describe(found, expect="X-Rite DTP41", allow_serial_identification=True)


def test_a_serial_unit_that_identifies_itself_carries_the_answer():
    build, client = _build(
        _candidates(("/dev/tty.a", 0, 0x01, None)),
        describe={"initialized_dtype": 5, "serial": None,
                  "capabilities": {"primary": 0x21, "secondary": 0x30001, "tertiary": 0}},
    )
    (found,) = build.attached()
    assert found.name is None
    described = build.describe(
        found, expect="GretagMacbeth SpectroScan", allow_serial_identification=True,
        accept_disclosed_initialization=True)
    assert described.name == "GretagMacbeth SpectroScan"
    assert described.discovered.native_type == 5
    assert client.describes[0][1]["expected_initialized_dtype"] == 5
    assert client.describes[0][1]["allow_serial_identification"] is True


def test_known_model_mismatch_refuses_both_selection_and_description_before_access():
    catalog = (Path(__file__).parent / "fixtures/discovery/catalog.json").read_text()
    build, client = _build(_candidates(("usb:/selected-i1pro3", 23, 0x02, None)),
                           catalog=catalog)
    found, = build.attached()
    assert found.symbol == "instI1Pro3"
    for action in (build.validate_selection, build.describe):
        with pytest.raises(ValueError, match="known selected candidate"):
            action(found, expect="instCR250")
    assert client.enumerations == 1 and client.describes == []


def test_selected_i1pro2_can_request_its_source_backed_i1pro_refinement_before_access():
    catalog = (Path(__file__).parent / "fixtures/discovery/catalog.json").read_text()
    build, client = _build(_candidates((
        "usb:/selected-extra-pipe", 22, 0x02, "initialization_resets_device",
    )), catalog=catalog)
    found, = build.attached()
    with pytest.raises(ValueError, match="accept_disclosed_initialization"):
        build.validate_selection(found, expect="instI1Pro")
    _, arguments = build.validate_selection(
        found, expect="instI1Pro", accept_disclosed_initialization=True,
    )
    assert arguments["expected_initialized_dtype"] == 21
    assert arguments["accepted_initialization_reason"] == "initialization_resets_device"
    assert client.describes == []


# A composed build's catalog. The CR-250 series appends instCR250 after the
# pinned 38; its row says the type is selected on a serial path without
# identification, which the build derives from the compiled serial_policy.h.
TYPED_CATALOG = json.dumps({"instrument_types": [
    *json.loads(CATALOG)["instrument_types"],
    {"enum": "instCR250", "native_type": 39, "source_label": "Colorimetry Research CR-250",
     "initialization_assessed": True, "initialization_refusal": None,
     "typed_serial_selection": True},
]})
_SERIAL_PORT = 0x01 | 0x100 | 0x400  # icomt_serial | icomt_fastserial | icomt_seriallike


def test_direct_binding_checks_highest_published_mode_floor_before_frontend(monkeypatch):
    rows = json.loads(TYPED_CATALOG)["instrument_types"]
    rows[-1]["read_allowance_floor_s"] = 174.0
    rows[-1]["read_allowance_floors_s"] = {"65560": 174.0, "196632": 200.0}
    build, _ = _build(
        _candidates(("serial:/CR", 0, _SERIAL_PORT, None)),
        catalog=json.dumps({"instrument_types": rows}),
        describe={"initialized_dtype": 39, "serial": "CR-1",
                  "capabilities": {"primary": 66584, "secondary": 768, "tertiary": 0}},
    )
    description = build.describe(build.attached()[0], expect="instCR250")
    from color_measurement.instrument_frontend import InstrumentFrontend

    monkeypatch.setattr(InstrumentFrontend, "__init__",
                        lambda *a, **k: pytest.fail("Bound too early"))
    with pytest.raises(ValueError, match="allows 175 s"):
        build.bind(description, timeout=175)


def test_a_typed_serial_path_is_selected_by_its_type_without_identification():
    """expect= alone selects a typed serial driver; generic identification never
    runs for it, so a request for that permission is refused before any open."""
    build, client = _build(
        _candidates(("/dev/cu.usbmodem1", 0, _SERIAL_PORT, None)), catalog=TYPED_CATALOG,
        describe={"initialized_dtype": 39, "serial": "CR-0001",
                  "capabilities": {"primary": 0x21, "secondary": 0x30001, "tertiary": 0}},
    )
    (found,) = build.attached()
    assert found.name is None
    with pytest.raises(ValueError, match="does not apply"):
        build.describe(found, expect="Colorimetry Research CR-250",
                       allow_serial_identification=True)
    assert client.describes == [], "the refusal must come before the port is opened"
    described = build.describe(found, expect="instCR250")
    kwargs = client.describes[0][1]
    assert kwargs["expected_initialized_dtype"] == 39
    assert kwargs["allow_serial_identification"] is False
    assert described.name == "Colorimetry Research CR-250"
    assert (described.discovered.native_type, described.discovered.path) == (
        39, "/dev/cu.usbmodem1")
    assert described.serial == "CR-0001"
    # The session worker reopens exactly this path and pins its serial.
    meter = build.bind(described)._meter
    assert (meter.instrument, meter.expected_path, meter.expected_serial) == (
        "Colorimetry Research CR-250", "/dev/cu.usbmodem1", "CR-0001")
    assert meter.allow_serial_identification is False
    # The same port named as a generically identified family still needs it.
    with pytest.raises(ValueError, match="allow_serial_identification=True"):
        build.describe(found, expect="GretagMacbeth SpectroScan",
                       accept_disclosed_initialization=True)


def test_a_catalog_silent_on_typed_selection_asks_for_identification():
    """Absent the fact, describe asks for generic identification. The worker then
    refuses that for a typed type rather than probing, so silence fails closed."""
    rows = json.loads(TYPED_CATALOG)["instrument_types"]
    del rows[-1]["typed_serial_selection"]
    build, client = _build(_candidates(("/dev/cu.usbmodem1", 0, _SERIAL_PORT, None)),
                           catalog=json.dumps({"instrument_types": rows}))
    (found,) = build.attached()
    with pytest.raises(ValueError, match="allow_serial_identification=True"):
        build.describe(found, expect="Colorimetry Research CR-250")
    assert client.describes == []


def test_serial_identification_is_refused_for_a_path_that_already_has_an_identity():
    build, _ = _build(_candidates(("hid:/a", 17, 0x04, None)))
    (found,) = build.attached()
    with pytest.raises(ValueError, match="only to an unidentified path"):
        build.describe(found, expect="X-Rite i1 DisplayPro", allow_serial_identification=True)


def test_two_units_of_one_model_bind_to_records_that_can_be_told_apart():
    """The case a model name cannot serve. Two i1Pro3s produce one model string,
    so the default device_id is the enumerated path instead."""
    build, _ = _build(_candidates(
        ("usb:/bus0/dev3", 21, 0x02, None), ("usb:/bus0/dev4", 21, 0x02, None)))
    first, second = build.attached()
    bound = [build.bind(build.describe(unit)) for unit in (first, second)]
    identities = [json.loads(frontend.describe_json())["device_id"] for frontend in bound]
    assert identities == ["usb:/bus0/dev3", "usb:/bus0/dev4"]
    assert len(set(identities)) == 2
    assert build.bind(build.describe(first), device_id="bench left")


def test_the_drivers_matrix_threshold_reaches_the_bound_descriptor():
    """The threshold the native gate compares against travels describe -> bind
    -> descriptor, as ambient_ccmx does. Dropped on the way, an ambient mode
    would be published as readable without a matrix, and refused only once
    Measure had opened the instrument."""
    from color_measurement.argyll_capability import CAPABILITY2, MODE_ROUTES

    words = {"primary": MODE_ROUTES["emis_spot"] | MODE_ROUTES["emis_ambient"],
             "secondary": CAPABILITY2["ccmx"], "tertiary": 0}
    build, _ = _build(_candidates(("usb:/bus0/dev3", 21, 0x02, None)), describe={
        "initialized_dtype": 21, "serial": "SN-1", "capabilities": words,
        "ambient_ccmx": True, "ambient_matrix_threshold": 5.0})
    (found,) = build.attached()
    described = build.describe(found)
    assert described.ambient_matrix_threshold == 5.0
    modes = {entry["mode"]: entry
             for entry in json.loads(build.bind(described).describe_json())["modes"]}
    assert modes["ambient_spot"]["correction_required"] is True
    assert modes["ambient_spot"]["correction_matrix_diagonal_mean"] == {"above": 5.0}


def test_baseline_native_mode_answers_survive_description_and_binding():
    """The actual per-word answer must reach the descriptor, not just its mask."""
    primary = 0x30018
    checks = {"scope": "initialized_before_mode_configuration", "mode_capabilities": primary,
              "checks": [{"mode_word": 0x10018, "native_code": 0x50001},
                         {"mode_word": 0x30018, "native_code": 0}]}
    build, _ = _build(_candidates(("usb:/bus0/dev3", 21, 0x02, None)), describe={
        "initialized_dtype": 21, "serial": "SN-1",
        "capabilities": {"primary": primary, "secondary": 1, "tertiary": 0},
        "baseline_mode_checks": checks})
    (found,) = build.attached()
    described = build.describe(found)
    snapshot = json.loads(build.bind(described).describe_json())
    mode = next(entry for entry in snapshot["modes"] if entry["mode"] == "emissive_spot")
    assert described.baseline_mode_checks == snapshot["baseline_mode_checks"] == checks
    assert mode["baseline_native_mode"]["default"]["status"] == "confirmed"
    assert mode["baseline_native_mode"]["xyz_only"]["status"] == "rejected"


# --- binding both workers to one build -----------------------------------------


def _report(tmp_path, *, generic=b"generic worker", link=None, write_generic=True,
            override=None):
    """A retained build report with both workers beside it, as a real run leaves.

    ``override`` replaces top-level fields, or merges into a nested object, so a
    test can break exactly one clause of the report predicate at a time.
    """
    import hashlib

    from color_measurement import argyll_description as api

    description = tmp_path / native_worker_name("argyll-description-worker-never-run")
    description.write_bytes(b"description worker")
    if write_generic:
        (tmp_path / native_worker_name("generic-worker-never-run")).write_bytes(generic)
    report = {
        "status": "passed", "argyll_version": "3.5.0",
        "archive_sha256": api.ARCHIVE_SHA256, "driver_scope": "all_public",
        "build_scope": "all_public", "build_system": platform.system(),
        "description_worker_build_link": {
            "status": "passed", "executed": False,
            "executable_sha256": hashlib.sha256(description.read_bytes()).hexdigest()},
        "generic_worker_build_link": link if link is not None else {
            "status": "passed", "executed": False,
            "executable_sha256": hashlib.sha256(generic).hexdigest()},
        "driver_manifest": {"archive_sha256": api.ARCHIVE_SHA256,
                            "build_scope": "all_public", "instrument_types": []},
    }
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(report.get(key), dict):
            report[key] = {**report[key], **value}
        else:
            report[key] = value
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    return path


@pytest.mark.parametrize("system", ["Darwin", "Linux", "Windows"])
def test_both_workers_are_authenticated_against_one_report(tmp_path, monkeypatch, system):
    """Enumeration and acquisition are different processes, and an index only
    carries between them if both enumerate the same set. usb_get_paths skips what
    inst_usb_match calls instUnknown (usbio_ox.c:99) and that filter is compiled
    from the linked drivers, so a narrow and a broad worker see different devices.
    One report for both is what makes the pairing checkable."""
    from color_measurement import argyll_description as api
    from color_measurement.discovery import InstrumentBuild

    monkeypatch.setattr(api, "HelperSession", lambda *a, **k: pytest.fail("Unexpected execution"))
    monkeypatch.setattr(platform, "system", lambda: system)
    build = InstrumentBuild.from_report(_report(tmp_path))
    suffix = ".exe" if system == "Windows" else ""
    assert build._helper.name == "generic-worker-never-run" + suffix
    assert build._client._helper.name == "argyll-description-worker-never-run" + suffix


@pytest.mark.parametrize("broken, match", [
    ({"generic": b"one build", "link": {"status": "passed", "executed": False,
                                        "executable_sha256": "0" * 64}}, "does not match"),
    ({"link": {"status": "failed", "executed": False, "executable_sha256": "0" * 64}},
     "no passed generic worker"),
    ({"link": {"status": "passed", "executed": True, "executable_sha256": "0" * 64}},
     "was executed"),
    ({"write_generic": False}, "No generic worker beside"),
])
def test_a_worker_that_is_not_this_builds_is_refused(tmp_path, monkeypatch, broken, match):
    """A matching basename authenticates nothing; the digest in the report does."""
    from color_measurement import argyll_description as api
    from color_measurement.discovery import InstrumentBuild

    monkeypatch.setattr(api, "HelperSession", lambda *a, **k: pytest.fail("Unexpected execution"))
    with pytest.raises(ValueError, match=match):
        InstrumentBuild.from_report(_report(tmp_path, **broken))


@pytest.mark.parametrize("override", [
    pytest.param({"status": "failed"}, id="status"),
    pytest.param({"argyll_version": "3.4.0"}, id="argyll_version"),
    pytest.param({"archive_sha256": "0" * 64}, id="archive_sha256"),
    pytest.param({"driver_scope": "selected_i1pro"}, id="driver_scope"),
    pytest.param({"driver_scope": "all_public_macos"}, id="retired_scope"),
    pytest.param({"build_scope": "i1pro"}, id="build_scope"),
    pytest.param({"description_worker_build_link": {"status": "failed"}}, id="link_status"),
    pytest.param({"description_worker_build_link": {"executed": True}}, id="link_executed"),
    pytest.param({"description_worker_build_link": "passed"}, id="link_not_an_object"),
    pytest.param({"driver_manifest": {"archive_sha256": "0" * 64}}, id="manifest_archive"),
    pytest.param({"driver_manifest": {"build_scope": "i1pro"}}, id="manifest_scope"),
    pytest.param({"driver_manifest": {"instrument_types": "all"}}, id="manifest_types"),
    pytest.param({"driver_manifest": []}, id="manifest_not_an_object"),
])
def test_each_clause_of_the_report_predicate_refuses_on_its_own(tmp_path, monkeypatch, override):
    """One field wrong at a time. The `backend_version` every CLI record carries
    (cli.py) is this predicate's `argyll_version`/`archive_sha256` restated, so a
    clause that can disappear unnoticed leaves that provenance unsupported. The
    helper-digest clause is covered separately above.
    """
    from color_measurement import argyll_description as api
    from color_measurement.discovery import InstrumentBuild

    monkeypatch.setattr(api, "HelperSession", lambda *a, **k: pytest.fail("Unexpected execution"))
    with pytest.raises(ValueError, match="A passed broad-driver build report is required"):
        InstrumentBuild.from_report(_report(tmp_path, override=override))
    # The same report with the field restored binds: the refusal was that field's.
    InstrumentBuild.from_report(_report(tmp_path))


def test_a_refused_enumeration_is_not_an_empty_device_list():
    """The one answer a picker must never be given. `status` has to be read before
    `data`, or a helper that failed to enumerate reads as "nothing is plugged in"
    -- and an operator holding an instrument is told it is not there."""
    from color_measurement.discovery import NativeRefusal

    build, _ = _build(_candidates(("usb:/a", 21, 0x02, None)), enumeration_error={
        "stage": "enumeration_failed", "native_code": 0, "refusal_reason": None})
    with pytest.raises(NativeRefusal) as raised:
        build.attached()
    assert raised.value.stage == "enumeration_failed"
    assert "enumeration refused at enumeration_failed" in str(raised.value)


def test_nothing_attached_is_an_empty_list_and_not_a_refusal():
    build, _ = _build(json.dumps({"candidates": []}))
    assert build.attached() == ()


# --- the published captures a client pins against -------------------------------

FIXTURES = Path(__file__).resolve().parents[1] / "tests/fixtures/discovery"


def _capture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_the_published_enumeration_captures_still_say_what_a_picker_needs():
    """These are what a client pins instead of hand-writing JSON, so a contract
    change here has to show up there as a fixture diff rather than a divergence."""
    listed = {row["capture"] for row in _capture("index")["enumerations"]}
    catalogs = {"catalog", Path(_capture("index")["catalog_without_contribution"]).stem}
    assert listed == {path.stem for path in FIXTURES.glob("*.json")} - {"index", *catalogs}

    same_model = _capture("two_of_one_model")["units"]
    assert len({unit["name"] for unit in same_model}) == 1
    assert len({unit["path"] for unit in same_model}) == 2, (
        "the case a model name cannot identify: two units, one model, distinct paths")
    assert all(unit["attachable"] for unit in same_model)

    for name in ("discloses_led_flash", "discloses_led_flash_colorhug",
                 "discloses_led_flash_huey"):
        capture = _capture(name)
        assert all(unit["identified"] for unit in capture["units"]), (
            "a disclosed unit is LISTED; hiding it leaves an operator with no row")
        assert not any(unit["attachable"] for unit in capture["units"])
        assert {row["requires"] for row in capture["opening"]} == {
            "accept_disclosed_initialization"}

    assert _capture("nothing_attached")["units"] == []
    assert _capture("nothing_attached")["refused"] is None, (
        "nothing attached is an answer; only a failed enumeration is a refusal")
    failed = _capture("enumeration_failed")
    assert failed["units"] is None and failed["refused"]["error"] == "NativeRefusal"


def test_the_published_catalog_is_the_builds_own_and_not_a_table_here():
    catalog = instrument_catalog(json.dumps(_capture("catalog")))
    from color_measurement.argyll_capability import CONTRIBUTED_INSTRUMENTS, INSTRUMENTS
    # Published from the application's composed build: the pinned 38 plus exactly
    # the contribution's declared types, and nothing hand-added here.
    assert len(catalog) == len(INSTRUMENTS) + len(CONTRIBUTED_INSTRUMENTS) == 43
    assert set(catalog.values()) == set(INSTRUMENTS) | set(CONTRIBUTED_INSTRUMENTS)


def test_a_serial_model_is_judged_by_what_it_will_be_not_by_the_bare_port():
    """The defect MATLAB's review found.

    An unidentified path carries the facts for `instUnknown`, which are about the
    PORT. The instrument that actually gets initialised is the expected one, and
    `bind` then initialises it AGAIN on the session worker. Judging the port and
    calling that a decision about the model is the same fail-open as reading an
    absent refusal as an assurance.

    description.c:271 does this check itself before constructing, so the native
    side was never at risk; what was wrong was the answer this module gave, and
    the refusal a client saw for it.
    """
    build, client = _build(_candidates(("/dev/tty.a", 0, 0x01, None)))
    (found,) = build.attached()
    assert found.initialization_refusal is None, "the PORT discloses nothing; the model does"

    with pytest.raises(ValueError, match="initialization_or_cleanup_moves_table"):
        build.describe(found, expect="GretagMacbeth SpectroScan",
                       allow_serial_identification=True)
    assert client.describes == [], "judged before the port is opened, as the C does"


def test_an_identified_serial_unit_carries_its_own_disclosure_onward():
    """What `bind` will initialise is the identified model, so the record a client
    holds -- and shows -- has to be that model's, not the blank path's."""
    build, _ = _build(
        _candidates(("/dev/tty.a", 0, 0x01, None)),
        describe={"initialized_dtype": 5, "serial": "SS-9",
                  "capabilities": {"primary": 0x21, "secondary": 0x30001, "tertiary": 0}},
    )
    (found,) = build.attached()
    described = build.describe(
        found, expect="GretagMacbeth SpectroScan", allow_serial_identification=True,
        accept_disclosed_initialization=True)
    assert described.discovered.name == "GretagMacbeth SpectroScan"
    assert described.discovered.initialization_refusal == "initialization_or_cleanup_moves_table"
    assert described.discovered.initialization_assessed is True
    assert described.discovered.attachable is False, (
        "a disclosed model stays disclosed after it identifies itself")


def test_a_catalog_that_says_nothing_about_a_type_reads_as_unassessed():
    """Absence of a statement is not an assurance -- the rule that made
    `initialization_assessed` exist in the first place."""
    build, client = _build(
        _candidates(("/dev/tty.a", 0, 0x01, None)), catalog=SILENT_CATALOG)
    (found,) = build.attached()
    with pytest.raises(ValueError, match="unassessed"):
        build.describe(found, expect="X-Rite i1 Pro 3", allow_serial_identification=True)
    assert client.describes == []
    with pytest.raises(ValueError, match="not eligible"):
        build.describe(found, expect="X-Rite i1 Pro 3", allow_serial_identification=True,
                       accept_unassessed_initialization=True)
    assert client.describes == []


def test_the_report_may_come_from_the_environment_and_is_never_searched_for(
    tmp_path, monkeypatch,
):
    """A client with no better way to find a build reads one variable, so several
    clients on one machine agree. Nothing is searched for: a report authenticates
    the workers it sits beside, so a guessed location would authenticate nothing.
    """
    from color_measurement import argyll_description as api
    from color_measurement.discovery import REPORT_VARIABLE, InstrumentBuild

    monkeypatch.setattr(api, "HelperSession", lambda *a, **k: pytest.fail("Unexpected execution"))
    monkeypatch.delenv(REPORT_VARIABLE, raising=False)
    with pytest.raises(ValueError, match=REPORT_VARIABLE):
        InstrumentBuild.from_report()
    monkeypatch.setenv(REPORT_VARIABLE, str(_report(tmp_path)))
    helper = InstrumentBuild.from_report()._helper
    assert helper.name == native_worker_name("generic-worker-never-run")


@pytest.mark.parametrize("floor", [180.0, "180", True, float("inf")])
def test_a_driver_allowance_floor_refuses_a_binding_that_cannot_fit_one_read(floor):
    """The build asks the real CR-250 driver the smallest allowance it accepts for
    one read (its 120 s budget floor plus its reserve for the reads after M) and
    publishes it; a binding allowing no more than that is certain to be refused by
    the driver, so it is refused before any worker starts. A floor that is not a
    finite positive number is refused, never read as none."""
    rows = json.loads(TYPED_CATALOG)["instrument_types"]
    rows[-1]["read_allowance_floor_s"] = floor
    build, client = _build(
        _candidates(("/dev/cu.usbmodem1", 0, _SERIAL_PORT, None)),
        catalog=json.dumps({"instrument_types": rows}),
        # The CR-250's own words, as its persona reports them: emissive spot and
        # telephoto on a colorimeter, programmatic and user triggers.
        describe={"initialized_dtype": 39, "serial": "CR-0001",
                  "capabilities": {"primary": 66584, "secondary": 768, "tertiary": 0}},
    )
    described = build.describe(build.attached()[0], expect="instCR250")
    if floor != 180.0:
        with pytest.raises(ValueError, match="Malformed read-allowance floor"):
            build.bind(described, timeout=300)
        return
    default = build.bind(described)
    assert default._meter.timeout == 300
    assert json.loads(default.describe_json())["limits"]["binding_policy"][
        "default_binding_timeout_seconds"] == 300
    # Exactly the floor cannot fit: the driver gets what remains when the read starts.
    with pytest.raises(ValueError, match="allows 180 s"):
        build.bind(described, timeout=180)
    front = build.bind(described, timeout=300)
    assert front._meter.timeout == 300
    # The floor travels with the frontend: published to the client, and enforced on
    # the OPERATION timeout too, which start() once replaced with a fixed 30 s --
    # silently undoing this check for every client driving through the frontend.
    snapshot = json.loads(front.describe_json())
    assert snapshot["limits"]["read_allowance_floor_seconds"] == 180.0
    offered = next(entry for entry in snapshot["modes"] if entry["workflow"] == "spot")
    request = json.dumps({"schema_version": 1, "device_id": snapshot["device_id"],
                          "capability_id": snapshot["capability_id"], "mode": offered["mode"],
                          "workflow": "spot", "patch_ids": ["A1"], "options": {},
                          "layout": {}})
    with pytest.raises(ValueError, match=r"more than 180 s.*allows 30 s"):
        front.prepare(request, timeout=30)
    assert front._read_allowance_floor == 180.0
    unfloored = _build(_candidates(("hid:/a", 17, 0x04, None)))[0]
    plain = unfloored.bind(unfloored.describe(unfloored.attached()[0],
                                              accept_disclosed_initialization=True))
    assert json.loads(plain.describe_json())["limits"]["read_allowance_floor_seconds"] is None


def test_a_client_reads_typed_selection_and_the_floor_before_opening_anything():
    """Both facts were readable only through private methods, so a client could
    neither grey out the identification option nor pick a bind timeout without
    reaching into internals or hard-coding 180 s. They are public, from the same
    catalog describe() and bind() use, and reading them opens nothing."""
    rows = json.loads(TYPED_CATALOG)["instrument_types"]
    rows[-1]["read_allowance_floor_s"] = 180.0
    catalog = json.dumps({"instrument_types": rows})
    build, client = _build(_candidates(("/dev/cu.usbmodem1", 0, _SERIAL_PORT, None)),
                           catalog=catalog)
    assert build.typed_serial_selection("Colorimetry Research CR-250") is True
    assert build.typed_serial_selection("GretagMacbeth SpectroScan") is False
    assert build.read_allowance_floor("instCR250") == 180.0
    assert build.read_allowance_floor("GretagMacbeth SpectroScan") is None
    assert client.enumerations == 0 and client.describes == []


def test_canonical_discovery_replays_preserve_identity_and_wire_shapes():
    """Real producer captures replace the client's hand-built protocol messages.

    This checks scenario intent, including identities which used to be invented
    by the MATLAB stub. The supplied-worker fixture regeneration check separately
    proves these committed bytes came from the current native source.
    """
    root = Path(__file__).parent / "fixtures/discovery"
    cases = json.loads((root / "index.json").read_text())["native_messages"]

    def read(case, key="listing", unit=0):
        row = cases[case]
        path = row[key] if key == "listing" else row["descriptions"][unit][key]
        return json.loads((root / path).read_text())

    first = read("two_of_one_model")["data"]["candidates"]
    moved = read("two_of_one_model_reordered")["data"]["candidates"]
    assert moved == list(reversed(first))
    serials = [read("two_of_one_model", "response", i)["data"]["serial"] for i in (0, 1)]
    assert serials == ["SUBSTITUTED-DISPLAY-001", "SUBSTITUTED-DISPLAY-002"]
    for i in (0, 1):
        assert read("two_of_one_model_reordered", "response", i)["data"]["serial"] == (
            serials[1 - i])
        assert read("two_of_one_model_shared_serial", "response", i)["data"]["serial"] == (
            serials[0])
        assert read("two_of_one_model_no_serial", "response", i)["data"]["serial"] is None
    description = read("two_of_one_model", "response")
    assert description["error"] is None
    assert description["backend"]["build_report_sha256"] is None
    assert len(description["data"]["baseline_mode_checks"]["checks"]) == 1
    assert isinstance(description["data"]["baseline_mode_checks"]["checks"], list)
    assert description["data"] == description["events"][1]
    assert read("nothing_attached")["data"]["candidates"] == []
    assert read("enumeration_failed")["data"] is None
    assert read("enumeration_failed")["error"]["stage"] == "enumeration_failed"
    assert read("same_path_twice", "response")["error"]["stage"] == "ambiguous_path"
    assert read("refuses_firmware_download", "response")["error"]["refusal_reason"] == (
        "initialization_downloads_pld")
    for name in ("discloses_led_flash", "discloses_led_flash_colorhug",
                 "discloses_led_flash_huey"):
        assert read(name, "without_acknowledgement")["error"]["stage"] == (
            "initialization_refused")
        assert read(name, "response")["status"] == "completed"
    typed = read("typed_cr250")["data"]["candidates"]
    assert len(typed) == 1 and typed[0]["dtype"] == 0
    catalog = json.loads((root / "catalog.json").read_text())["instrument_types"]
    cr = next(row for row in catalog if row["enum"] == "instCR250")
    assert read("typed_cr250", "response")["data"]["initialized_dtype"] == cr["native_type"]


def test_canonical_discovery_replays_bind_through_real_instrument_build():
    """Every published scenario goes through the real InstrumentBuild.

    The replay answers as the worker answered the request the capture made: a
    disclosed effect not accepted by its own name gets the captured refusal, and
    the expected type must be the one captured. Serving the accepted response
    whatever was asked could not notice the acknowledgement going missing, and
    three of the 21 scenarios were all that ever ran.
    """
    import functools

    from color_measurement.discovery import InstrumentBuild, NativeRefusal

    root = Path(__file__).parent / "fixtures/discovery"
    index = json.loads((root / "index.json").read_text())
    catalog = (root / "catalog.json").read_text()

    class Replay:
        def __init__(self, case):
            self.case = case
            response = (
                case["descriptions"][0]["response"] if case["descriptions"] else case["listing"]
            )
            answer = json.loads((root / response).read_text())
            self._helper_sha256 = answer["backend"]["helper_sha256"]
            self._report_sha256 = answer["backend"]["build_report_sha256"]

        def catalog_json(self):
            return catalog

        def list_candidates_json(self):
            return (root / self.case["listing"]).read_text()

        def describe_json(self, candidate, **kwargs):
            chosen = json.loads(candidate)
            row = next(row for row in self.case["descriptions"] if row["path"] == chosen["path"])
            assert kwargs["expected_initialized_dtype"] == self.case.get(
                "expected_initialized_dtype")
            accepted = kwargs["accepted_initialization_reason"] == chosen["initialization_refusal"]
            refused = "without_acknowledgement" in row and not accepted
            return (root / row["without_acknowledgement" if refused else "response"]).read_text()

    for name, case in index["native_messages"].items():
        build = InstrumentBuild(Replay(case), "must-not-run", timeout=300,
                                expected_helper_sha256="0" * 64)
        if json.loads((root / case["listing"]).read_text())["status"] != "completed":
            with pytest.raises(NativeRefusal):
                build.attached()
            continue
        found = build.attached()
        assert [unit.path for unit in found] == [row["path"] for row in case["descriptions"]]
        expect = "Colorimetry Research CR-250" if "expected_initialized_dtype" in case else None
        for unit, row in zip(found, case["descriptions"]):
            answer = json.loads((root / row["response"]).read_text())
            describe = functools.partial(
                build.describe, unit, expect=expect,
                accept_disclosed_initialization=(
                    unit.opening_requires == "accept_disclosed_initialization"))
            if unit.opening_requires == "not_permitted":
                with pytest.raises(ValueError, match="nothing opens it"):
                    describe()
            elif answer["status"] != "completed":
                with pytest.raises(NativeRefusal, match=answer["error"]["stage"]):
                    describe()
            else:
                assert "native_identity" in answer["data"], name
                described = describe()
                assert described.serial == answer["data"]["serial"], name
                if name == "typed_cr250":
                    # This generic enum placeholder is not a CR driver instance.
                    # It cannot supply initialized identity for native controls.
                    assert described.native_identity is None
                    with pytest.raises(ValueError, match="no matching current native identity"):
                        build.bind(described)
                    continue
                snapshot = json.loads(build.bind(described).describe_json())
                assert snapshot["device_id"] == unit.path, name


@pytest.mark.parametrize("field,value", [
    ("path", None), ("path", ""), ("path", 42), ("path", "x" * 1025),
    ("path", "nul\0path"), ("dtype", True), ("dtype", 0x80000000),
    ("transport", "8"), ("transport", 8.9), ("transport", True),
    ("transport", 0), ("transport", 0x100), ("transport", 0x10000),
    ("initialization_assessed", "yes"), ("initialization_refusal", 3),
])
def test_direct_candidate_json_uses_native_validation(field, value):
    from color_measurement.native_protocol import ProtocolError

    payload = json.loads(_candidates(("usb:/unit", 21, 8, None)))
    payload["candidates"][0][field] = value
    with pytest.raises(ProtocolError, match="Invalid candidate metadata"):
        discovered_instruments(json.dumps(payload), CATALOG)


@pytest.mark.parametrize("field", [
    "path", "dtype", "transport", "initialization_assessed", "initialization_refusal",
])
def test_direct_candidate_json_refuses_missing_fields(field):
    from color_measurement.native_protocol import ProtocolError

    payload = json.loads(_candidates(("usb:/unit", 21, 8, None)))
    del payload["candidates"][0][field]
    with pytest.raises(ProtocolError, match="Invalid candidate metadata"):
        discovered_instruments(json.dumps(payload), CATALOG)


@pytest.mark.parametrize("transport", [0x101, 0x108, 0xff08])
def test_direct_candidates_preserve_transport_flags_and_unknown_serial(transport):
    (found,) = discovered_instruments(_candidates(("/dev/serial", 0, transport, None)), CATALOG)
    assert found.transport == transport and found.native_type == 0
    assert not found.identified and found.path == "/dev/serial"


def test_direct_build_requires_session_authority_and_preserves_report_pairing(tmp_path):
    import hashlib

    from color_measurement.argyll_description import NativeDescriptionClient
    from color_measurement.discovery import InstrumentBuild

    report = _report(tmp_path)
    client = NativeDescriptionClient.from_build(report)
    helper = report.with_name(native_worker_name("generic-worker-never-run"))
    with pytest.raises(TypeError, match="expected_helper_sha256"):
        InstrumentBuild(client, helper)
    with pytest.raises(ValueError, match="SHA-256 is required"):
        InstrumentBuild(client, helper, expected_helper_sha256=None)
    other = tmp_path / "another-worker"
    other.write_bytes(b"Another build")
    with pytest.raises(ValueError, match="not beside the retained build report"):
        InstrumentBuild(client, other,
                        expected_helper_sha256=hashlib.sha256(other.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="does not match"):
        InstrumentBuild(client, helper, expected_helper_sha256="0" * 64)


def test_report_replacement_cannot_mix_bindings_or_published_provenance(tmp_path, monkeypatch):
    import hashlib

    from color_measurement import argyll_description as api
    from color_measurement.discovery import InstrumentBuild

    path = _report(tmp_path).resolve()
    first = path.read_bytes()
    second = json.loads(first)
    second["generic_worker_build_link"]["executable_sha256"] = "0" * 64
    second["description_worker_build_link"]["executable_sha256"] = "f" * 64
    replaced = json.dumps(second).encode()
    read_bytes = Path.read_bytes
    report_reads = []

    def replace_after_read(self):
        if self == path:
            report_reads.append(self)
            path.write_bytes(replaced)
            return first if len(report_reads) == 1 else replaced
        return read_bytes(self)

    events = json.loads((Path(__file__).parent /
        "fixtures/discovery/native/nothing_attached/list.json").read_text())["events"]

    class Session:
        def __init__(self, command, **kwargs):
            operation_id = command[command.index("--operation-id") + 1]
            self.messages = iter([{**event, "operation_id": operation_id,
                                   "provenance": "physical"} for event in events])

        def receive_message(self):
            return next(self.messages)

        def finish_messages(self, *, expected_returncode=0):
            assert expected_returncode == 0

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    monkeypatch.setattr(api, "HelperSession", Session)
    build = InstrumentBuild.from_report(path)
    first_report = json.loads(first)
    assert build._client._helper_sha256 == (
        first_report["description_worker_build_link"]["executable_sha256"])
    assert build._helper_sha256 == first_report["generic_worker_build_link"]["executable_sha256"]
    assert build._client._report_sha256 == hashlib.sha256(first).hexdigest()
    emitted = json.loads(build._client.list_candidates_json())
    assert emitted["backend"]["build_report_sha256"] == hashlib.sha256(first).hexdigest()
    assert emitted["backend"]["helper_sha256"] == build._client._helper_sha256
    replay, _ = _build(_candidates(("usb:/unit", 21, 8, None)))
    described = replay.describe(replay.attached()[0])
    with pytest.raises(ValueError, match="authenticated backend receipt"):
        build.bind(described, catalog_json=CATALOG)
    # A description from a different build must not acquire this report's
    # session-worker identity, even if its capabilities happen to match.
    assert len(report_reads) == 1
    assert build._helper_sha256 == (
        first_report["generic_worker_build_link"]["executable_sha256"])
    assert build._helper == path.with_name(native_worker_name("generic-worker-never-run"))


def current_cr_identity():
    return dict(driver="cr250", identity=dict(
        verified=True, model="CR-250", serial="CR-0001", firmware="1.26",
        firmware_components=[1, 26], instrument_type=2,
    ))


def current_cr_build():
    catalogue = json.loads(TYPED_CATALOG)
    next(row for row in catalogue["instrument_types"]
         if row["native_type"] == 39)["native_facts"] = True
    return _build(
        _candidates(("selected-cr-path", 39, _SERIAL_PORT, None)),
        catalog=json.dumps(catalogue), describe=dict(
            initialized_dtype=39, serial="CR-0001", native_identity=current_cr_identity(),
            provenance="simulated",
            capabilities=dict(primary=0x21, secondary=0x30001, tertiary=0),
        ),
    )


@pytest.mark.parametrize("value", [True, 0, -1, 39.0, "39"])
def test_initialized_description_requires_a_positive_integer_type_at_construction(value):
    from dataclasses import replace

    build, _ = current_cr_build()
    description = build.describe(build.attached()[0])
    with pytest.raises(ValueError, match="observed positive native enum"):
        replace(description, initialized_type=value)


def test_initialized_description_owns_caller_identity_and_receipt_snapshots():
    from copy import deepcopy
    from dataclasses import replace

    build, _ = current_cr_build()
    description = build.describe(build.attached()[0])
    identity, receipt = deepcopy(description.native_identity), deepcopy(description.backend_receipt)
    held = replace(description, native_identity=identity, backend_receipt=receipt)
    identity["identity"]["firmware"] = "caller changed identity"
    receipt["helper_sha256"] = "f" * 64
    assert held.native_identity == description.native_identity
    assert held.backend_receipt == description.backend_receipt


@pytest.mark.parametrize("case", [
    "extra_key", "version", "helper_hex", "report_hex", "other_report",
])
def test_description_receipt_checks_shape_and_exact_report_even_for_identical_helpers(case):
    build, client = current_cr_build()
    candidate = build.attached()[0]
    original = client.describe_json
    client._report_sha256 = "a" * 64

    def response(*args, **kwargs):
        envelope = json.loads(original(*args, **kwargs))
        envelope["backend"]["build_report_sha256"] = client._report_sha256
        return json.dumps(envelope)

    client.describe_json = response
    assert build.describe(candidate).backend_receipt["build_report_sha256"] == "a" * 64

    def altered(*args, **kwargs):
        envelope = json.loads(response(*args, **kwargs))
        receipt = envelope["backend"]
        if case == "extra_key":
            receipt["unknown"] = True
        elif case == "version":
            receipt["version"] = "other version"
        elif case == "helper_hex":
            receipt["helper_sha256"] = client._helper_sha256 = "g" * 64
        elif case == "report_hex":
            receipt["build_report_sha256"] = client._report_sha256 = "g" * 64
        else:
            receipt["build_report_sha256"] = "b" * 64
        return json.dumps(envelope)

    client.describe_json = altered
    with pytest.raises(ValueError, match="authenticated backend receipt"):
        build.describe(candidate)


@pytest.mark.parametrize("case", ["omitted_availability", "uncompiled_adapter"])
def test_description_identity_requires_explicit_availability_and_compiled_adapter(case):
    build, client = current_cr_build()
    if case == "omitted_availability":
        del client._describe["native_identity"]
    else:
        catalogue = json.loads(client._catalog)
        next(row for row in catalogue["instrument_types"]
             if row["native_type"] == 39)["native_facts"] = False
        client._catalog = json.dumps(catalogue)
    with pytest.raises(ValueError, match="native identity"):
        build.describe(build.attached()[0])


@pytest.mark.parametrize("case", [
    "extra_packet_key", "invalid_native_identity", "backend_version", "session_helper",
    "description_hex", "no_description_helper", "no_session_helper",
])
def test_frontend_current_packet_requires_complete_correlated_worker_provenance(case):
    from dataclasses import replace

    from color_measurement.instrument_frontend import InstrumentFrontend
    from color_measurement.native_protocol import ProtocolError

    build, _ = current_cr_build()
    description = build.describe(build.attached()[0])
    front = build.bind(description)
    packet = json.loads(front.describe_json())["current_binding_identity"]
    receipt = packet["provenance"]
    meter = front._meter
    if case == "extra_packet_key":
        packet["unknown"] = True
    elif case == "invalid_native_identity":
        packet["native_identity"]["driver"] = "other adapter"
    elif case == "backend_version":
        receipt["backend_version"] = "other version"
    elif case == "session_helper":
        receipt["session_helper_sha256"] = "f" * 64
    elif case == "description_hex":
        receipt["description_helper_sha256"] = "g" * 64
    elif case == "no_description_helper":
        receipt["description_helper_sha256"] = None
    else:
        receipt["session_helper_sha256"] = None
        meter = replace(meter, expected_helper_sha256=None)
    with pytest.raises((ValueError, ProtocolError)):
        InstrumentFrontend(
            meter, mode_capabilities=description.mode_capabilities,
            trigger_capabilities=description.trigger_capabilities,
            filter_capabilities=description.filter_capabilities, device_id="selected-cr-path",
            serial=description.serial, current_binding_identity=packet,
        )


def test_frontend_owns_its_current_packet_before_returning_serialized_descriptions():
    from copy import deepcopy

    from color_measurement.instrument_frontend import InstrumentFrontend

    build, _ = current_cr_build()
    description = build.describe(build.attached()[0])
    front = build.bind(description)
    packet = json.loads(front.describe_json())["current_binding_identity"]
    original = deepcopy(packet)
    held = InstrumentFrontend(
        front._meter, mode_capabilities=description.mode_capabilities,
        trigger_capabilities=description.trigger_capabilities,
        filter_capabilities=description.filter_capabilities, device_id="selected-cr-path",
        serial=description.serial, current_binding_identity=packet,
    )
    packet["native_identity"]["identity"]["firmware"] = "caller changed identity"
    assert json.loads(held.describe_json())["current_binding_identity"] == original


def test_current_identity_and_paired_receipts_reach_binding_without_additional_access(monkeypatch):
    from color_measurement.instrument import Instrument

    monkeypatch.setattr(Instrument, "_open_session", lambda *args: pytest.fail("Native transport"))
    build, client = current_cr_build()
    description = build.describe(build.attached()[0])
    front = build.bind(description)
    packet = json.loads(front.describe_json())["current_binding_identity"]
    assert packet == dict(
        device_id="selected-cr-path", initialized_type=39,
        native_identity=current_cr_identity(), provenance=dict(
            backend_version="3.5.0", description_helper_sha256="1" * 64,
            session_helper_sha256="0" * 64, build_report_sha256=None,
        ),
    )
    # Reported kind 2 is not the Argyll initialized enum 39.
    assert packet["native_identity"]["identity"]["instrument_type"] == 2
    client._describe["native_identity"]["identity"]["firmware"] = "mutated caller data"
    assert description.native_identity["identity"]["firmware"] == "1.26"
    packet["native_identity"]["identity"]["firmware"] = "mutated returned data"
    assert json.loads(front.describe_json())["current_binding_identity"]["native_identity"] == \
        current_cr_identity()
    assert client.enumerations == 1 and len(client.describes) == 1


def test_capability_identity_excludes_only_receipt_provenance_without_mutating_descriptor():
    from copy import deepcopy

    from color_measurement.instrument_frontend import capability_identity

    build, _ = current_cr_build()
    descriptor = json.loads(build.bind(build.describe(build.attached()[0])).describe_json())
    content = {key: value for key, value in descriptor.items() if key != "capability_id"}
    original = deepcopy(content)
    changed = deepcopy(content)
    for key in ("description_helper_sha256", "session_helper_sha256", "build_report_sha256"):
        changed["current_binding_identity"]["provenance"][key] = "f" * 64
    assert capability_identity(changed) == descriptor["capability_id"]
    assert content == original
    for key, value in (("device_id", "other-path"), ("initialized_type", 40),
                       ("native_identity", None)):
        changed = deepcopy(content)
        changed["current_binding_identity"][key] = value
        assert capability_identity(changed) != descriptor["capability_id"]
    changed = deepcopy(content)
    changed["current_binding_identity"]["native_identity"]["identity"]["firmware"] = "1.27"
    assert capability_identity(changed) != descriptor["capability_id"]


@pytest.mark.parametrize("field,value,reason", [
    ("model", "CR-100", "differs from the observed initialized type"),
    ("model", "other model", "absent from the binding catalogue"),
    ("serial", "OTHER-UNIT", "reported serial"),
    ("instrument_type", 39, "instrument kind"),
    ("firmware_components", [True, 26], "firmware components"),
])
def test_initialized_identity_refuses_wrong_model_serial_or_instrument_kind(field, value, reason):
    build, client = current_cr_build()
    client._describe["native_identity"]["identity"][field] = value
    with pytest.raises(ValueError, match=reason):
        build.describe(build.attached()[0])


def test_bind_rechecks_mutated_identity_and_origin_before_publishing_current_facts():
    build, _ = current_cr_build()
    description = build.describe(build.attached()[0])
    description.native_identity["identity"]["model"] = "CR-100"
    with pytest.raises(ValueError, match="differs from the observed initialized type"):
        build.bind(description)
    description.native_identity["identity"]["model"] = "CR-250"
    description.backend_receipt["helper_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="authenticated backend receipt"):
        build.bind(description)


def test_bind_refuses_shape_valid_native_identity_changes_without_additional_access(monkeypatch):
    from color_measurement.instrument import Instrument

    monkeypatch.setattr(Instrument, "_open_session", lambda *args: pytest.fail("Native transport"))
    build, client = current_cr_build()
    description = build.describe(build.attached()[0])
    identity = description.native_identity["identity"]
    identity.update(firmware="1.27", firmware_components=[1, 27])
    with pytest.raises(ValueError, match="differs from the initialized observation"):
        build.bind(description)
    identity.update(firmware="1.26", firmware_components=[1, 26])
    packet = json.loads(build.bind(description).describe_json())["current_binding_identity"]
    assert packet["native_identity"] == current_cr_identity()
    assert client.enumerations == 1 and len(client.describes) == 1


def test_no_adapter_remains_explicitly_unknown_in_current_binding():
    build, _ = _build(_candidates(("usb:/unit", 21, 8, None)))
    description = build.describe(build.attached()[0])
    packet = json.loads(build.bind(description).describe_json())["current_binding_identity"]
    assert packet["native_identity"] is None and packet["initialized_type"] == 21


@pytest.mark.parametrize("field,value", [
    ("device_id", "other-unit"), ("initialized_type", True),
    ("provenance", {}),
])
def test_frontend_refuses_a_current_identity_packet_that_disagrees_with_its_binding(field, value):
    from color_measurement.instrument_frontend import InstrumentFrontend

    build, _ = current_cr_build()
    description = build.describe(build.attached()[0])
    front = build.bind(description)
    packet = json.loads(front.describe_json())["current_binding_identity"]
    packet[field] = value
    with pytest.raises(ValueError, match="Current binding identity"):
        InstrumentFrontend(
            front._meter, mode_capabilities=description.mode_capabilities,
            trigger_capabilities=description.trigger_capabilities,
            filter_capabilities=description.filter_capabilities, device_id="selected-cr-path",
            serial=description.serial, current_binding_identity=packet,
        )
