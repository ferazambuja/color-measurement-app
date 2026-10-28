"""The manuals' operator setup, as the descriptor publishes it."""

import json
import time
from pathlib import Path
from zipfile import ZipFile

import pytest

from color_measurement import demo, operator_setup
from color_measurement.argyll_capability import (
    ACCEPTED_INSTRUMENT_NAMES,
    CALIBRATION_CONDITIONS,
    CAPABILITY2,
    CONTRIBUTED_INSTRUMENTS,
    INSTRUMENTS,
    MODE_ROUTES,
    instrument_display_name,
    parse_mode_name,
)
from color_measurement.argyll_source import ARCHIVE_VERSION


def test_every_actual_demo_mode_has_a_conservative_physical_chart_assessment(monkeypatch):
    from color_measurement import instrument

    monkeypatch.setattr(instrument, "HelperSession",
                        lambda *a, **kw: pytest.fail("Assessment attempted native I/O"))
    naming = {"cr100", "cr250", "klein_k10"}
    display = {"i1_display_3", "spyderx", "spyderx2", "spyder2024"}
    seen = set()
    kinds = set()
    for front in demo.demo_frontends():
        descriptor = json.loads(front.describe_json())
        persona = front._meter.persona
        seen.add(persona)
        for mode in descriptor["modes"]:
            route = mode["axes"]["route"]
            expected = ("reflective" if route in {"ref_spot", "ref_strip", "ref_xy", "ref_chart"}
                        else "naming_only" if persona in naming
                        and route in {"emis_spot", "emis_tele"}
                        else "display_only" if persona in display
                        and route in {"emis_spot", "emis_tele"} else "unknown")
            row = mode["physical_chart"]
            assert set(row) == {"kind", "assessment"}
            assert row["kind"] == expected, (persona, mode["mode"])
            kinds.add(row["kind"])
            if expected == "unknown":
                assert row["assessment"] is None
            else:
                source = row["assessment"]
                assert set(source) == {"text", "document", "location", "quote", "official"}
                assert source["text"] and source["document"]
                assert 0 < len(source["quote"].split()) <= 25
                assert source["location"].startswith(("https://", "PDF p. "))
                assert source["official"] is True
    assert len(seen) == 40
    assert {"cr300", "pr655", "pr670"} <= seen
    assert naming | display <= seen
    assert kinds == {"reflective", "naming_only", "display_only", "unknown"}


@pytest.mark.parametrize("persona", ["cr100", "cr250", "klein_k10"])
def test_actual_radiance_or_luminance_spot_is_conditional_naming_only(persona):
    front = next(f for f in demo.demo_frontends() if f._meter.persona == persona)
    modes = {m["mode"]: m for m in json.loads(front.describe_json())["modes"]}
    row = modes["emissive_spot"]["physical_chart"]
    assert row["kind"] == "naming_only"
    assert "illuminated patch fills the actual measurement field" in row["assessment"]["text"]
    assert "does not verify fitted optics" in row["assessment"]["text"]


def test_actual_i1display_tele_does_not_establish_physical_chart_optics():
    front = next(f for f in demo.demo_frontends() if f._meter.persona == "i1_display_3")
    modes = {m["mode"]: m for m in json.loads(front.describe_json())["modes"]}
    for name in ("emissive_spot", "emissive_tele"):
        assert modes[name]["physical_chart"]["kind"] == "display_only"
    tele = modes["emissive_tele"]["physical_chart"]["assessment"]
    assert tele["location"] == "PDF p. 31"
    assert "projector screen" in tele["quote"]


def test_physical_chart_assessment_never_inherits_another_model_or_route():
    for name in ("Unassessed instrument", "Klein K-10", "Colorimetry Research CR-250",
                 "Datacolor SpyderX"):
        for route in ("emis_ambient", "trans_spot", "s_ref_spot", "invented"):
            assert operator_setup.physical_chart_setup(name, route) == {
                "kind": "unknown", "assessment": None}
    assert operator_setup.physical_chart_setup("Unassessed instrument", "emis_tele") == {
        "kind": "unknown", "assessment": None}
    assert operator_setup.physical_chart_setup("Unassessed instrument", "ref_spot") == {
        "kind": "unknown", "assessment": None}


def test_physical_chart_reflection_quotes_match_pinned_native_definitions(argyll_archive):
    with ZipFile(argyll_archive) as archive:
        source = archive.read(f"Argyll_V{ARCHIVE_VERSION}/spectro/inst.h").decode()
    for route in ("ref_spot", "ref_strip", "ref_xy", "ref_chart"):
        row = operator_setup.physical_chart_setup("X-Rite i1 Pro 3", route)
        assert row["kind"] == "reflective"
        assert row["assessment"]["quote"] in source
        assert row["assessment"]["document"] == "ArgyllCMS 3.5.0, spectro/inst.h"


def test_published_fixture_descriptors_carry_the_current_physical_chart_assessment():
    root = Path(__file__).parent / "fixtures"
    count = 0

    def check(value):
        nonlocal count
        if isinstance(value, dict):
            if value.get("contract") == "argyll_generic_instrument_v1":
                count += 1
                for mode in value["modes"]:
                    assert mode["physical_chart"] == operator_setup.physical_chart_setup(
                        value["device"]["model"], mode["axes"]["route"])
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    for folder in ("generic_frontend", "discovery"):
        for path in (root / folder).rglob("*.json"):
            check(json.loads(path.read_text()))
    assert count >= 37


def test_physical_chart_scope_is_bound_by_the_capability_id(monkeypatch):
    from color_measurement import instrument_frontend

    front = next(f for f in demo.demo_frontends() if f._meter.persona == "cr250")
    before = json.loads(front.describe_json())
    monkeypatch.setattr(instrument_frontend, "physical_chart_setup",
                        lambda *args: {"kind": "unknown", "assessment": None})
    after = json.loads(front.describe_json())
    assert after["capability_id"] != before["capability_id"]
    request = {"schema_version": 1, "device_id": before["device_id"],
               "capability_id": before["capability_id"], "mode": "emissive_spot",
               "workflow": "spot", "patch_ids": ["opaque-cell"], "options": {}, "layout": {}}
    with pytest.raises(ValueError, match="[Ss]tale"):
        front.prepare(json.dumps(request))


def test_every_entry_is_keyed_to_a_real_instrument_route_and_condition():
    """A misspelt name, route or condition would never be looked up, so its entry
    would sit here unpublished while the prompt card showed nothing. Every key must
    be a name the frontend can bind, a route upstream defines, and a calibration
    condition upstream names (or None, a calibration with no operator condition)."""
    for instrument, routes in operator_setup._SETUP.items():
        assert instrument in ACCEPTED_INSTRUMENT_NAMES, instrument
        for route in routes:
            assert route in MODE_ROUTES, (instrument, route)
    for instrument, conditions in operator_setup._CALIBRATION.items():
        assert instrument in ACCEPTED_INSTRUMENT_NAMES, instrument
        for condition in conditions:
            # "none" and "unknown" are values, not conditions a prompt asks for,
            # and the descriptor's vocabulary leaves them out.
            assert condition is None or (condition in CALIBRATION_CONDITIONS
                                         and condition not in ("none", "unknown")), (
                instrument, condition)


def test_every_published_setup_names_a_condition_its_descriptor_publishes():
    """A client decodes calibration_setup against the SAME descriptor's
    calibration.conditions and refuses anything else (MATLAB's genericDescriptor
    does). A note keyed to a name that list omits would pass every check here and
    make the whole descriptor unreadable there."""
    for front in demo.demo_frontends():
        descriptor = json.loads(front.describe_json())
        published = {c["condition"] for c in descriptor["calibration"]["conditions"]}
        named = [entry["condition"] for entry in descriptor["calibration_setup"]]
        assert len(named) == len(set(named)), front._meter.persona
        assert set(named) <= published | {None}, front._meter.persona


def test_every_entry_is_short_and_backed_by_a_quote():
    """One line for a prompt card, backed by at most 25 words quoted from a named
    document and a page or URL -- never an instruction without its source."""
    for instrument, routes in (*operator_setup._SETUP.items(),
                               *operator_setup._CALIBRATION.items()):
        for route, entry in routes.items():
            where = (instrument, route)
            assert entry.text and len(entry.text) <= 160, where
            assert entry.document and entry.quote, where
            assert 0 < len(entry.quote.split()) <= 25, where
            assert entry.location.startswith(("PDF p. ", "https://")), where


def test_the_descriptor_publishes_the_manual_setup_and_null_where_it_is_silent():
    """What a client shows: the i1Pro 2's positioning target for a reflective spot,
    its plate for the white-tile condition, and null for transmission, which its
    manual does not describe; a CR-250, whose X-Rite-style entries do not exist,
    publishes nulls and no calibration setup."""
    fronts = {f._meter.persona: f for f in demo.demo_frontends()}
    descriptor = json.loads(fronts["i1pro2"].describe_json())
    by_route = {parse_mode_name(m["mode"])[0]: m["operator_setup"] for m in descriptor["modes"]}
    assert "positioning target" in by_route["ref_spot"]["text"]
    assert by_route["ref_spot"]["location"] == "PDF p. 25"
    assert by_route["trans_spot"] is None
    (plate,) = descriptor["calibration_setup"]
    assert plate["condition"] == "man_ref_white" and "calibration plate" in plate["text"]
    cr250 = json.loads(fronts["cr250"].describe_json())
    assert cr250["calibration_setup"] == []
    assert all(m["operator_setup"] is None for m in cr250["modes"])


def _calibration_prompts(front, mode):
    snapshot = json.loads(front.describe_json())
    op = front.prepare(json.dumps({"schema_version": 1, "device_id": snapshot["device_id"],
        "capability_id": snapshot["capability_id"], "mode": mode, "workflow": "spot",
        "patch_ids": ["P1"], "options": {}, "layout": {}}))
    op.dispatch()
    prompts = []
    try:
        while not op.done:
            text = op.poll_event_json()
            if not text:
                time.sleep(0.002)
                continue
            event = json.loads(text)
            if event["kind"] == "instruction_required":
                if event["payload"].get("calibration"):
                    prompts.append(event["payload"]["calibration"])
                op.respond(op.operation_id, event["payload"]["request_id"], "accept")
        assert json.loads(op.result_json())["status"] == "completed"
        return prompts
    finally:
        op.close()


def test_a_calibration_prompt_carries_the_setup_for_its_own_condition_only():
    """The ColorMunki's note -- turn the dial to its calibration position -- is right
    for man_cal_smode and wrong for the transmission white that follows, which needs
    the dial back at surface (munki_imp.c:1071-1073). Published per instrument, a
    client showed it on both, and an operator following it produced the very wrong
    position the driver then refused. Each prompt now carries its own condition's."""
    fronts = {f._meter.persona: f for f in demo.demo_frontends()}
    dial, white = _calibration_prompts(fronts["colormunki"], "transmission_spot")
    assert dial["condition"] == "man_cal_smode" and "calibration position" in dial["setup"]["text"]
    assert white["condition"] == "man_trans_white" and white["setup"] is None
    (tile,) = _calibration_prompts(fronts["i1pro3"], "reflective_spot")
    assert tile["condition"] == "man_ref_white" and "calibration plate" in tile["setup"]["text"]


def test_placement_notes_do_not_request_another_calibration_or_device_trigger():
    """These notes prepare the next driver call, not a standalone manual workflow.
    DTP20 sends CR after man_ref_white (dtp20.c:1187); ColorMunki measures its dark
    after checking the dial (munki_imp.c:1067-1073,1124). Neither needs a button.
    Ambient sample placement follows calibration, so it must not request it again.
    A user-operated DTP41 calibration still needs its physical button."""
    fronts = {f._meter.persona: f for f in demo.demo_frontends()}
    ambient_checked = set()
    for persona, front in fronts.items():
        descriptor = json.loads(front.describe_json())
        for mode in descriptor["modes"]:
            if mode["mode"] != "ambient_spot":
                continue
            setup = mode["operator_setup"]
            assert setup == operator_setup.mode_setup(
                descriptor["device"]["model"], "emis_ambient"), persona
            # Only instruments with a sourced ambient-head procedure carry a note
            # (operator_setup.py); mode_setup returns None for the rest, and a
            # persona with no note has nothing to request.
            if setup is None:
                continue
            # Choosing the Klein's named Lux calibration is configuration, not
            # another calibration acquisition. Other ambient notes only fit an
            # adapter or move the dial.
            if persona == "klein_k10":
                assert "diffuser" in setup["text"] and "named Lux calibration" in setup["text"]
                assert setup["location"] == "https://www.kleininstruments.com/faq"
            else:
                assert "calibrat" not in setup["text"].lower(), persona
            ambient_checked.add(persona)
    # The personas whose manuals state an ambient-head procedure; a note that
    # disappears, or a new one, changes this set deliberately.
    assert ambient_checked == {"colormunki", "i1_display_3", "i1pro2", "i1pro3", "klein_k10"}
    for instrument, condition in (("X-Rite ColorMunki", "man_cal_smode"),
                                   ("X-Rite DTP20", "man_ref_white")):
        assert "button" not in operator_setup.calibration_setup(
            instrument, condition)["text"].lower()
    assert "button" not in operator_setup.mode_setup("X-Rite DTP20", "ref_spot")["text"].lower()
    assert "button" in operator_setup.calibration_setup(
        "X-Rite DTP41", "uop_ref_white")["text"]


def _mode_notes(instrument, mode, secondary=0):
    return {entry["condition"]: entry for entry in
            operator_setup.mode_calibration_setups(instrument, mode, secondary)}


def test_mode_preparation_never_mixes_reflective_and_transmission_references():
    reflection = _mode_notes("X-Rite DTP41", "reflective_spot")
    transmission = _mode_notes("X-Rite DTP41", "transmission_spot")
    assert set(reflection) == {"uop_ref_white"}
    assert "Color Reflection Reference" in reflection["uop_ref_white"]["text"]
    assert set(transmission) == {"uop_trans_white"}
    assert "transmissive white" in transmission["uop_trans_white"]["text"]
    munki = _mode_notes("X-Rite ColorMunki", "transmission_spot")
    assert set(munki) == {"man_cal_smode", "man_trans_white"}
    assert "calibration position" in munki["man_cal_smode"]["text"]
    assert "surface position" in munki["man_trans_white"]["text"]
    fronts = {f._meter.persona: f for f in demo.demo_frontends()}
    descriptor = json.loads(fronts["dtp41"].describe_json())
    published = {mode["mode"]: mode["calibration_setup"] for mode in descriptor["modes"]}
    assert [row["condition"] for row in published["reflective_spot"]] == ["uop_ref_white"]
    # This DTP41 persona advertises reflection only; previewing the driver's
    # transmission branch must not invent a transmission mode in its descriptor.
    assert "transmission_spot" not in published
    descriptor = json.loads(fronts["colormunki"].describe_json())
    published = {mode["mode"]: mode["calibration_setup"] for mode in descriptor["modes"]}
    assert [row["condition"] for row in published["transmission_spot"]] == [
        "man_cal_smode", "man_trans_white"]


def test_wavelength_and_high_resolution_references_follow_the_actual_driver():
    i1pro2 = _mode_notes("X-Rite i1 Pro 2", "ambient_spot")
    assert set(i1pro2) == {"man_ref_white", "man_am_dark", "man_em_dark"}
    assert "High-resolution" in i1pro2["man_ref_white"]["text"]
    assert "ambient adapter" in i1pro2["man_am_dark"]["text"]
    assert "cap fitted" in i1pro2["man_am_dark"]["text"]
    for mode in ("emissive_spot", "ambient_spot", "transmission_spot"):
        i1pro3 = _mode_notes("X-Rite i1 Pro 3", mode)
        assert "calibration plate" in i1pro3["man_ref_white"]["text"]
        assert "man_am_dark" not in i1pro3
    monitor = _mode_notes("GretagMacbeth i1 Monitor", "emissive_spot")
    assert set(monitor) == {"man_em_dark"}
    pro = _mode_notes("GretagMacbeth i1 Pro", "emissive_spot")
    assert "High-resolution" in pro["man_ref_white"]["text"]


def test_adaptive_modes_do_not_prescribe_nonadaptive_exposure_selection():
    for instrument in ("GretagMacbeth i1 Monitor", "GretagMacbeth i1 Pro",
                       "X-Rite i1 Pro 2", "X-Rite i1 Pro 3", "X-Rite ColorMunki"):
        for mode in ("emissive_spot", "emissive_spot_adaptive", "emissive_tele",
                     "ambient_spot", "ambient_flash", "emissive_strip"):
            assert "emis_white" not in _mode_notes(instrument, mode), (instrument, mode)
        for mode in ("emissive_spot_nonadaptive", "emissive_tele_nonadaptive"):
            assert "emis_white" in _mode_notes(instrument, mode), (instrument, mode)


def test_refresh_preparation_uses_observed_variant_facilities_and_stays_conditional():
    refresh = CAPABILITY2["emis_refr_meas"]
    for instrument in ("GretagMacbeth i1 Display 2", "ColorMunki Smile",
                       "X-Rite i1 DisplayPro, ColorMunki Display", "Datacolor Spyder3",
                       "Datacolor Spyder4", "Datacolor Spyder5", "JETI specbos"):
        for mode in ("emissive_spot", "ambient_spot"):
            assert "emis_80pc" not in _mode_notes(instrument, mode)
            row = _mode_notes(instrument, mode, refresh)["emis_80pc"]
            assert "If the selected display calibration uses refresh" in row["text"]
            assert "no rate is set" in row["text"]
    transmission = _mode_notes("JETI specbos", "transmission_spot", refresh)
    assert set(transmission) == {"emis_80pc", "man_trans_white"}


def test_dark_and_grey_preparation_does_not_promote_optional_calibration():
    assert "man_em_dark" in _mode_notes("Datacolor SpyderX", "emissive_spot")
    assert _mode_notes("Datacolor SpyderX", "ambient_spot") == {}
    for instrument in ("Datacolor SpyderX2", "Datacolor Spyder 2024"):
        row = _mode_notes(instrument, "emissive_spot")[None]
        assert "Black calibration is optional" in row["text"]
        assert "do not request a black reference" in row["text"]
        assert _mode_notes(instrument, "ambient_spot") == {}
    assert set(_mode_notes("X-Rite DTP92", "emissive_spot")) == {"man_em_dark", "emis_grey"}
    assert set(_mode_notes("X-Rite DTP94", "emissive_spot")) == {"man_em_dark"}
    assert "blocking the optical input" in _mode_notes(
        "Image Engineering EX1", "emissive_spot")["man_em_dark"]["text"]
    for instrument in ("GretagMacbeth i1 Display 1", "Klein K-10"):
        row = _mode_notes(instrument, "emissive_spot")[None]
        assert "optional" in row["text"]
        assert any(word in row["text"] for word in
                   ("does not request", "do not request", "not requested"))
        assert "man_em_dark" not in _mode_notes(instrument, "emissive_spot")
    cube = _mode_notes("SwatchMate Cube", "reflective_spot")
    assert set(cube) == {"man_ref_white", None}
    assert "optional" in cube[None]["text"]


def test_table_preview_does_not_ask_for_a_handheld_reference_or_transmission_source():
    for instrument in ("GretagMacbeth SpectroScan", "GretagMacbeth SpectroScanT"):
        for mode in ("reflective_spot", "emissive_spot", "transmission_spot"):
            notes = _mode_notes(instrument, mode)
            assert set(notes) == {None, "change_filter"}
            assert "automatically" in notes[None]["text"]
            assert ("transmission aperture" in notes[None]["text"]) == (
                mode == "transmission_spot")
    lino = _mode_notes("GretagMacbeth Spectrolino", "transmission_spot")
    assert set(lino) == {"man_ref_white", "man_trans_white", "change_filter"}


def test_all_demo_modes_publish_scoped_notes_without_changing_live_prompt_catalogues():
    seen = set()
    for front in demo.demo_frontends():
        descriptor = json.loads(front.describe_json())
        instrument = descriptor["device"]["model"]
        seen.add(instrument)
        assert instrument in operator_setup._DRIVER_FOR_INSTRUMENT
        assert descriptor["calibration_setup"] == operator_setup.calibration_setups(instrument)
        vocabulary = {c["condition"] for c in descriptor["calibration"]["conditions"]} | {None}
        for mode in descriptor["modes"]:
            notes = mode["calibration_setup"]
            conditions = [row["condition"] for row in notes]
            assert len(conditions) == len(set(conditions)), (instrument, mode["mode"])
            assert set(conditions) <= vocabulary, (instrument, mode["mode"])
            if mode["stored"]:
                assert notes == [], (instrument, mode["mode"])
            for row in notes:
                assert set(row) == {"condition", "text", "document", "location",
                                    "quote", "official"}
                assert row["text"] and len(row["text"]) <= 260
                assert 0 < len(row["quote"].split()) <= 25
                assert row["location"].startswith(("PDF p. ", "https://", "Bundled source: "))
    assert len(seen) == 40
    display_names = {instrument_display_name(symbol) for symbol in
                     (*INSTRUMENTS, *CONTRIBUTED_INSTRUMENTS)}
    assert all(name in display_names
               for name in operator_setup._DRIVER_FOR_INSTRUMENT)
    assert operator_setup.mode_calibration_setups("Spectrocam", "reflective_spot", 0) == []


def test_contributed_preview_cites_its_driver_without_claiming_physical_chart_assessment():
    for instrument, family, driver in (
            ("Colorimetry Research CR-100", "CR", "cr250.c"),
            ("Colorimetry Research CR-250", "CR", "cr250.c"),
            ("Colorimetry Research CR-300", "CR", "cr250.c"),
            ("Photo Research PR-655", "PR", "pr655.c"),
            ("Photo Research PR-670", "PR", "pr655.c")):
        row = _mode_notes(instrument, "emissive_spot")[None]
        assert row["document"] == f"Color Measurement {family} contribution, spectro/{driver}"
        assert row["location"] == (
            "Bundled source: tools/native_argyll/contributions/cr_pr/files/spectro/" + driver)
        assert row["quote"] == "if (needed) *needed = inst_calt_none;"
        if instrument.endswith(("CR-300", "PR-655", "PR-670")):
            assert operator_setup.physical_chart_setup(instrument, "emis_spot") == {
                "kind": "unknown", "assessment": None}
    row = _mode_notes("Colorimtre HCFR", "emissive_spot")[None]
    assert row["document"] == "ArgyllCMS 3.5.0, spectro/inst.c"
    assert row["location"] == "https://www.argyllcms.com/Argyll_V3.5.0_src.zip"


def test_native_mode_preparation_quotes_match_the_cited_pinned_sources(argyll_archive):
    """Bind each published native quotation to its file, not just its word count.

    The archive fixture checks the upstream digest. The CR/PR contribution has a
    separate document and source path; it must not be attributed to that archive.
    Manufacturer references retain their existing independent provenance.
    """
    quotes = set()
    root = Path(__file__).resolve().parents[1]
    with ZipFile(argyll_archive) as archive:
        for front in demo.demo_frontends():
            descriptor = json.loads(front.describe_json())
            for mode in descriptor["modes"]:
                for row in mode["calibration_setup"]:
                    if row["document"].startswith("ArgyllCMS "):
                        prefix = f"ArgyllCMS {ARCHIVE_VERSION}, spectro/"
                        assert row["document"].startswith(prefix)
                        name = row["document"].removeprefix(prefix)
                        assert row["location"] == (
                            f"https://www.argyllcms.com/Argyll_V{ARCHIVE_VERSION}_src.zip")
                        members = [member for member in archive.namelist()
                                   if member.endswith("/spectro/" + name)]
                        assert len(members) == 1, row["document"]
                        source = archive.read(members[0]).decode()
                    elif row["location"].startswith("Bundled source: "):
                        driver, family = {
                            "Colorimetry Research": ("cr250.c", "CR"),
                            "Photo Research": ("pr655.c", "PR"),
                        }[descriptor["device"]["model"].rsplit(" ", 1)[0]]
                        name = "tools/native_argyll/contributions/cr_pr/files/spectro/" + driver
                        assert row["document"] == (
                            f"Color Measurement {family} contribution, spectro/{driver}")
                        assert row["location"] == "Bundled source: " + name
                        assert not any(member.endswith("/spectro/" + driver)
                                       for member in archive.namelist())
                        source = (root / name).read_text()
                    else:
                        continue
                    assert row["quote"] in source, (name, row["quote"])
                    quotes.add((name, row["quote"]))
    assert len(quotes) == 34
