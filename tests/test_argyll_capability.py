"""The capability decoder is a transcription; these tests hold it to the source.

Two kinds of check. The provenance tests re-derive every pinned bit value from the
digest-verified Argyll 3.5.0 archive and fail if the module has drifted from it.
The behaviour tests state what the decoder must mean, using the two instruments
the dissimilar-instrument gate mapped: a colorimeter that advertises more mode
bits than it has modes, and a motorised table whose positioning is a capability
rather than an identity.
"""

import re
import zipfile

import pytest

from color_measurement.argyll_capability import (
    _NAME_ROUTES,
    ACCEPTED_INSTRUMENT_NAMES,
    ACTION_MASK,
    ACTION_RANK,
    ACTIONS,
    CALIBRATION_CONDITIONS,
    CALIBRATION_REFERENCES,
    CALIBRATION_TYPES,
    CAPABILITY2,
    CAPABILITY3,
    CONDITION_ACTIONS,
    CONTRIBUTED_INSTRUMENTS,
    ILLUMINATION_MASK,
    ILLUMINATIONS,
    INSTRUMENT_CONFIGURATIONS,
    INSTRUMENTS,
    MEASUREMENT_CONDITIONS,
    MODE_FLAGS,
    MODE_ROUTES,
    WORKFLOW_KINDS,
    XY_POSITIONING_FLAGS,
    available_conditions,
    calibration_condition,
    calibration_reference,
    condition_detail,
    condition_is_optional,
    decode_calibration_types,
    decode_facilities,
    decode_filters,
    decode_mode_flags,
    decode_modes,
    deferrable_calibrations,
    expected_measurement_type,
    illumination_of,
    instrument_binding_name,
    instrument_display_name,
    mode_word,
    parse_mode_name,
    rank_routes,
    requires_positioning,
    route_action,
    route_illumination,
    supported_modes,
    undecoded_bits,
    workflow_kind,
)

PREFIX = "Argyll_V3.5.0/"
HEADER = PREFIX + "spectro/inst.h"


@pytest.fixture(scope="module")
def inst_h(argyll_archive):
    with zipfile.ZipFile(argyll_archive) as zipped:
        return zipped.read(HEADER).decode()


def _enum_values(text, pattern):
    return {name: int(value, 0) for name, value in re.findall(pattern, text)}


# --- provenance: every pinned value comes from the pinned source ----------------


def test_axis_and_modifier_values_match_the_pinned_header(inst_h):
    """Illumination, action and modifier bits are inst.h:200-252, not invention."""
    declared = _enum_values(inst_h, r"inst_mode_(\w+)\s*=\s*(0x[0-9A-Fa-f]+),")
    for table in (ILLUMINATIONS, ACTIONS, MODE_FLAGS):
        for name, bits in table.items():
            assert declared[name] == bits, f"inst_mode_{name} moved upstream"
    assert declared["illum_mask"] == ILLUMINATION_MASK
    assert declared["sub_mask"] == ACTION_MASK


def test_filter_selections_match_the_distinct_active_measurement_conditions(inst_h, argyll_archive):
    with zipfile.ZipFile(argyll_archive) as zipped:
        header = zipped.read(PREFIX + "xicc/xspect.h").decode()
    filters = _enum_values(inst_h, r"inst_opt_filter_(\w+)\s*=\s*(0x[0-9A-Fa-f]+)")
    conditions = _enum_values(header, r"inst_mrc_(\w+)\s*=\s*(\d+)")
    names = {"none": "none", "D50": "D50", "D65": "D65", "UVCut": "uvcut",
             "pol": "pol", "Custom": "custom"}
    expected = {filters[name]: conditions[condition] for name, condition in names.items()}
    assert dict(MEASUREMENT_CONDITIONS) == expected
    assert filters["unknown"] not in MEASUREMENT_CONDITIONS
    with pytest.raises(TypeError):
        MEASUREMENT_CONDITIONS[filters["none"]] = 99


def test_capability2_and_capability3_values_match_the_pinned_header(inst_h):
    declared2 = _enum_values(inst_h, r"(inst2_\w+)\s*=\s*(0x[0-9A-Fa-f]+)")
    declared3 = _enum_values(inst_h, r"(inst3_\w+)\s*=\s*(0x[0-9A-Fa-f]+)")
    for name, bits in CAPABILITY2.items():
        assert declared2[f"inst2_{name}"] == bits, f"inst2_{name} moved upstream"
    for name, bits in CAPABILITY3.items():
        assert declared3[f"inst3_{name}"] == bits, f"inst3_{name} moved upstream"


def test_the_named_routes_are_exactly_the_eighteen_upstream_names(inst_h):
    """Argyll names 18 of 28 possible pairings; a computed product would invent ten."""
    segment = inst_h[
        inst_h.index("/* Combined operating modes"): inst_h.index("inst_mode_measurement_mask")
    ]
    upstream = {
        name: ILLUMINATIONS[illumination] | ACTIONS[action]
        for name, action, illumination in re.findall(
            r"inst_mode_(\w+)\s*=\s*inst_mode_(\w+)\s*/\*.*?\*/\s*\|\s*inst_mode_(\w+)",
            segment,
            re.S,
        )
    }
    assert upstream == dict(MODE_ROUTES)
    assert len(MODE_ROUTES) == 18
    missing = {
        f"{illumination}_{action}"
        for illumination in ILLUMINATIONS
        for action in ACTIONS
    } - {f"{route_illumination(r)}_{route_action(r)}" for r in MODE_ROUTES}
    assert len(missing) == 10, "an unnamed pairing became a mode; re-read inst.h:258-297"


def test_every_route_decomposes_into_exactly_one_illumination_and_one_action():
    for route, word in MODE_ROUTES.items():
        assert ILLUMINATIONS[route_illumination(route)] | ACTIONS[route_action(route)] == word
        assert bin(word & ILLUMINATION_MASK).count("1") == 1
        assert bin(word & ACTION_MASK).count("1") == 1


# --- behaviour: the two instruments the gate mapped ------------------------------


I1DISPLAY3_PRIMARY = (
    ILLUMINATIONS["emission"]
    | ACTIONS["spot"]
    | ACTIONS["tele"]
    | ACTIONS["ambient"]
    | MODE_FLAGS["emis_refresh_ovd"]
    | MODE_FLAGS["emis_norefresh_ovd"]
    | MODE_FLAGS["emis_nonadaptive"]
    | MODE_FLAGS["colorimeter"]
)  # i1d3.c:3902-3908

I1DISPLAY3_SECONDARY = (
    CAPABILITY2["has_sensmode"]
    | CAPABILITY2["prog_trig"]
    | CAPABILITY2["user_trig"]
    | CAPABILITY2["has_leds"]
    | CAPABILITY2["disptype"]
    | CAPABILITY2["ccmx"]
    | CAPABILITY2["ccss"]
    | CAPABILITY2["get_min_int_time"]
    | CAPABILITY2["set_min_int_time"]
)  # i1d3.c:3911-3919


def test_a_colorimeter_decodes_to_emission_only_with_no_spectrum():
    """i1d3.c:3902-3908 carries no reflection, no transmission and no spectral bit."""
    routes = decode_modes(I1DISPLAY3_PRIMARY)
    assert routes == ("emis_ambient", "emis_spot", "emis_tele")
    assert {route_illumination(route) for route in routes} == {"emission"}
    assert "spectral" not in decode_mode_flags(I1DISPLAY3_PRIMARY)
    assert "colorimeter" in decode_mode_flags(I1DISPLAY3_PRIMARY)


def test_a_colorimeter_advertises_more_mode_bits_than_it_has_modes():
    """The advisory/definitive split, concretely.

    Seven mode-word bits beyond the illumination axis are set, yet only three are
    routes. The remaining four are modifiers, and ``i1d3_check_mode`` refuses some
    of what the word advertises. A decoded list is a set of candidates to confirm;
    ``inst.h:305-306`` says so of its own macro.
    """
    assert len(decode_modes(I1DISPLAY3_PRIMARY)) == 3
    # Four modifier bits are set, but norefresh_ovd (0x6000) contains
    # refresh_ovd (0x2000) and upstream tests norefresh first -- "Must test this
    # first!" at i1d3.c:3247, i1disp.c:2356 -- so three names come back.
    assert decode_mode_flags(I1DISPLAY3_PRIMARY) == (
        "colorimeter", "emis_nonadaptive", "emis_norefresh_ovd",
    )
    assert not requires_positioning(I1DISPLAY3_SECONDARY)


def test_the_refresh_override_precedence_upstream_documents_is_applied():
    """norefresh wins: its word contains refresh, so IMODETST alone reports both."""
    assert MODE_FLAGS["emis_norefresh_ovd"] & MODE_FLAGS["emis_refresh_ovd"] == (
        MODE_FLAGS["emis_refresh_ovd"]
    )
    assert decode_mode_flags(MODE_FLAGS["emis_norefresh_ovd"]) == ("emis_norefresh_ovd",)
    assert decode_mode_flags(MODE_FLAGS["emis_refresh_ovd"]) == ("emis_refresh_ovd",)


def test_adaptivity_is_refused_where_the_driver_would_silently_drop_it():
    """The class no downstream check catches, so the parser must.

    Both drivers read inst_mode_emis_nonadaptive only in the spot/tele branch
    (i1pro3.c:656-661); emis_strip, emis_ambient and emis_ambient_flash match on
    IMODETST and drop it. cap1 advertises the bit for the spot route, so
    ``m & ~cap`` passes, check_mode ACCEPTS the word, and set_mode silently
    reduces it. Neither the advisory test nor the definitive one sees this.
    """
    for silently_reduced in (
        "ambient_spot_nonadaptive", "ambient_flash_nonadaptive",
        "emissive_strip_nonadaptive", "emissive_strip_adaptive",
    ):
        with pytest.raises(ValueError, match="spot and telephoto"):
            parse_mode_name(silently_reduced)
    assert parse_mode_name("emissive_spot_nonadaptive") == (
        "emis_spot", ("emis_nonadaptive",), None,
    )


def test_positioning_is_a_capability_not_an_instrument_identity():
    """ss.c:703-721 gates its motorised teardown on cap2 & inst2_xy_position."""
    table = 0
    for flag in XY_POSITIONING_FLAGS:
        table |= CAPABILITY2[flag]
    assert requires_positioning(table)
    assert set(decode_facilities(table)) == set(XY_POSITIONING_FLAGS)
    assert not requires_positioning(table & ~CAPABILITY2["xy_position"])
    assert not requires_positioning(I1DISPLAY3_SECONDARY)


def test_a_reflective_table_offers_x_y_where_a_spot_instrument_does_not():
    """SpectroScan advertises ref_xy; the same decoder gives an i1Pro only spot/strip."""
    table = ILLUMINATIONS["reflection"] | ACTIONS["spot"] | ACTIONS["xy"]
    assert decode_modes(table) == ("ref_spot", "ref_xy")
    handheld = ILLUMINATIONS["reflection"] | ACTIONS["spot"] | ACTIONS["strip"]
    assert decode_modes(handheld) == ("ref_spot", "ref_strip")


# --- behaviour: ranking, unknown bits, input validation -------------------------


def test_routes_rank_chart_then_xy_then_strip_then_spot():
    """chartread.c:752-790 records rmode 3..0 and falls back down the list."""
    assert rank_routes(("ref_spot", "ref_strip", "ref_xy", "ref_chart")) == (
        "ref_chart", "ref_xy", "ref_strip", "ref_spot",
    )
    assert [ACTION_RANK[action] for action in ("chart", "xy", "strip", "spot")] == [3, 2, 1, 0]


def test_unranked_readings_sort_after_every_ranked_strategy():
    """Ambient and telephoto are readings, not chart-acquisition strategies."""
    ranked = rank_routes(("emis_ambient", "emis_tele", "emis_spot", "emis_strip"))
    assert ranked == ("emis_strip", "emis_spot", "emis_ambient", "emis_tele")


def test_per_patch_ranking_matches_chartread_and_transmission_spot_alias():
    """The pbypatch branch prefers either transmission spot enum first.

    ``trans_spot_a`` carries the upstream ambient action bit because it is the
    alternate transmission geometry, so ranking must use its route meaning.
    """
    assert rank_routes(("trans_chart", "trans_spot_a"), per_patch=True) == (
        "trans_spot_a", "trans_chart",
    )
    assert rank_routes(("ref_chart", "ref_spot"), per_patch=True) == (
        "ref_spot", "ref_chart",
    )


def test_ranking_is_total_and_reproducible_within_one_branch():
    """Ranked per branch, because saved and live cannot be ordered together."""
    for branch in ("s_reflection", "reflection", "transmission", "emission"):
        routes = tuple(r for r in MODE_ROUTES if route_illumination(r) == branch)
        assert rank_routes(routes) == rank_routes(tuple(reversed(routes)))
        assert sorted(rank_routes(routes)) == sorted(routes)


def test_an_unknown_capability_bit_is_reported_rather_than_dropped():
    """A newer instrument's unknown flag must not read as an absent one."""
    unknown = 0x00100000  # unassigned in inst2 at 3.5.0
    assert unknown not in CAPABILITY2.values()
    word = CAPABILITY2["prog_trig"] | unknown
    assert decode_facilities(word) == ("prog_trig",)
    assert undecoded_bits(word, CAPABILITY2) == unknown
    assert undecoded_bits(CAPABILITY2["prog_trig"], CAPABILITY2) == 0


def test_capability_words_must_be_thirty_two_bit_unsigned_integers():
    rejected: tuple[object, ...] = (-1, 0x1_0000_0000, 1.0, True, None, "0x10")
    for bad in rejected:
        with pytest.raises(ValueError, match="32-bit unsigned"):
            decode_modes(bad)


def test_positioning_query_rejects_non_capability_words():
    for bad in (-1, 0x1_0000_0000, 1.0, True, None):
        with pytest.raises(ValueError, match="32-bit unsigned"):
            requires_positioning(bad)


def test_empty_words_decode_to_nothing_rather_than_failing():
    """inst_mode_none is a real state: ss.c:2164 leaves cap unknown until initialised."""
    assert decode_modes(0) == ()
    assert decode_mode_flags(0) == ()
    assert decode_facilities(0) == ()
    assert decode_filters(0) == ()
    assert not requires_positioning(0)


@pytest.mark.parametrize("filters, expected", [
    ((), {"unknown"}),
    (("average",), {"unknown"}),
    (("filter_none",), {"unknown", "m0"}),
    (("filter_UVCut",), {"unknown", "m2"}),
    (("filter_D50",), {"unknown", "m1"}),
    (("filter_Custom",), {"unknown", "custom"}),
    (("filter_none", "filter_D50", "filter_D65", "filter_UVCut", "filter_pol",
      "filter_Custom"), {"unknown", "m0", "m1", "d65", "m2", "m3", "custom"}),
])
def test_explicit_reflective_conditions_require_their_advertised_filter(filters, expected):
    tertiary = sum(CAPABILITY3[name] for name in filters)
    assert set(available_conditions(tertiary)) == expected
    modes = supported_modes(MODE_ROUTES["ref_spot"], tertiary)
    assert "reflective_spot" in modes
    assert {name.removeprefix("reflective_spot_") for name in modes
            if name.startswith("reflective_spot_m")} == expected & {"m0", "m1", "m2", "m3"}


# --- the shared vocabulary: 38 caller-facing names resolve to Argyll routes ---


def _published_modes():
    """Every mode name this project publishes.

    This used to read the two per-device modules' MODES tuples and reconcile them
    against the grammar. There is one vocabulary now -- the grammar IS the
    published set -- so what is left to check is that the grammar is closed: every
    name it generates resolves to an Argyll route, and nothing outside it parses.
    """
    from color_measurement.argyll_capability import _ALL_MODE_NAMES
    return _ALL_MODE_NAMES


def test_every_published_mode_resolves_to_an_argyll_route():
    """The claim the whole collapse rested on, now the only vocabulary there is.

    Three tests used to live here comparing the two per-device MODES tuples --
    eight names against fifteen, which routes each family had, how they differed.
    That comparison was the ARGUMENT for building one path; with the per-device
    modules gone it has no second side, and what remains is the property that made
    the argument true: every name the grammar publishes is an Argyll route, and
    many names share one.
    """
    published = _published_modes()
    routes = set()
    for mode in published:
        route, _, _ = parse_mode_name(mode)
        assert route in MODE_ROUTES, mode
        routes.add(route)
    # Thirty-eight caller-facing names over far fewer routes: the names carry a
    # measurement condition and an adaptivity modifier, neither of which is a
    # route bit, which is exactly why one ladder serves all of them.
    assert len(published) == 38
    assert len(routes) < len(published)
    assert {"emis_spot", "ref_spot", "trans_spot", "emis_ambient_flash"} <= routes


def test_a_measurement_condition_never_enters_the_mode_word():
    """M0..M3 is a different axis; folding it in would lose which was refused."""
    conditions = ("m0", "m1", "m2", "m3")
    assert len({mode_word(f"reflective_spot_{c}") for c in conditions}) == 1
    assert len({mode_word(f"reflective_strip_{c}") for c in conditions}) == 1
    # The action axis still separates them, so the word is not simply constant.
    assert mode_word("reflective_spot_m0") != mode_word("reflective_strip_m0")
    assert parse_mode_name("reflective_spot_m2")[2] == "m2"


def test_mode_word_reproduces_the_hand_written_session_composition():
    """route | spectral | colorimeter | [emis_nonadaptive], per both configure_meter."""
    base = MODE_FLAGS["spectral"] | MODE_FLAGS["colorimeter"]
    assert mode_word("emissive_spot_adaptive") == MODE_ROUTES["emis_spot"] | base
    assert mode_word("emissive_spot_nonadaptive") == (
        MODE_ROUTES["emis_spot"] | base | MODE_FLAGS["emis_nonadaptive"]
    )
    assert mode_word("ambient_spot") == MODE_ROUTES["emis_ambient"] | base
    assert mode_word("ambient_flash") == MODE_ROUTES["emis_ambient_flash"] | base
    assert mode_word("transmission_strip") == MODE_ROUTES["trans_strip"] | base
    assert mode_word("reflective_strip_m2") == MODE_ROUTES["ref_strip"] | base


def test_a_colorimeter_composes_no_spectral_bit():
    """i1d3 carries no inst_mode_spectral, so the bit is capability, not a constant."""
    word = mode_word("emissive_spot_adaptive", spectral=False)
    assert "spectral" not in decode_mode_flags(word)
    assert decode_modes(word) == ("emis_spot",)


def test_a_name_outside_argylls_own_grammar_is_refused_rather_than_guessed():
    """Every rejection here is derivable from upstream, not from our families.

    ``adaptive``/``nonadaptive`` is ``inst_mode_emis_nonadaptive`` (inst.h:231-232),
    an emission modifier by its own name and bit. M0..M3 are reflective measurement
    conditions carried by ``inst_opt_filter``, never by a mode bit. An axis holds
    one value. The rest have no named route at all.
    """
    # Each name trips its own grammar rule; both validators share parse_mode_name.
    for bad, refusal in (
        ("", "must be a non-empty string"),
        ("x", "is not <illumination>_<action>"),
        ("spot", "is not <illumination>_<action>"),
        ("chart_spot", "has no Argyll route"),
        ("emissive_chart", "has no Argyll route"),
        ("emissive_spot_m9", "carries unknown qualifier"),
        ("transmission_spot_m0", "is a reflective condition"),
        ("reflective_strip_adaptive", "adaptiv"),
        ("reflective_spot_m0_m1", "repeats a measurement condition"),
        ("emissive_spot_adaptive_adaptive", "repeats an adaptivity"),
        ("emissive_spot_adaptive_nonadaptive", "repeats an adaptivity"),
        ("emissive_spot_nonadaptive_adaptive", "repeats an adaptivity"),
        (None, "must be a non-empty string"),
        (42, "must be a non-empty string"),
        (False, "must be a non-empty string"),
        ([], "must be a non-empty string"),
        ({}, "must be a non-empty string"),
    ):
        for validator in (parse_mode_name, expected_measurement_type):
            with pytest.raises(ValueError, match=refusal):
                validator(bad)


def test_the_grammar_admits_exactly_the_published_modes_plus_the_colorimeters():
    """Exhaustive, so it cannot drift: every name the grammar can express.

    The published set and this independently-derived sweep must be EQUAL. They
    used to differ by twenty-one names -- everything the grammar could express
    that the two per-device modules did not publish -- and that gap was the
    vocabulary a new instrument would have blocked on. There is no gap now: what
    the grammar names is what an instrument may be offered, gated only by its own
    capability words.
    """
    from itertools import product

    published = set(_published_modes())
    accepted = set()
    qualifiers = ("adaptive", "nonadaptive", "m0", "m1", "m2", "m3")
    # Derived from the grammar itself, so extending it cannot silently narrow
    # this sweep -- which is exactly what happened when the five missing routes
    # were added and this test kept passing without covering them.
    prefixes = sorted({prefix for prefix, _ in _NAME_ROUTES})
    suffixes = sorted({suffix for _, suffix in _NAME_ROUTES})
    for count in (0, 1, 2):
        for prefix, suffix, extra in product(
            prefixes, suffixes, product(qualifiers, repeat=count),
        ):
            name = "_".join((prefix, suffix, *extra))
            try:
                parse_mode_name(name)
            except ValueError:
                continue
            accepted.add(name)
    assert published == accepted, sorted(published ^ accepted)


def test_the_shared_parser_accepts_a_colorimeters_unqualified_emission_spot():
    """Regression: the i1 Display 3 has no adaptive/nonadaptive qualifier."""
    assert parse_mode_name("emissive_spot") == ("emis_spot", (), None)
    assert decode_modes(mode_word("emissive_spot", spectral=False)) == ("emis_spot",)


def test_saved_and_live_routes_cannot_be_ranked_together():
    """The DTP20 case: one capability word carries both, and action alone inverts it.

    ``dtp20.c:1456-1461`` advertises ``ref_spot | ref_strip | s_ref_spot |
    s_ref_chart``, so a decoded list really does arrive mixed.
    ``chartread.c:900-931`` tries every saved route first, each gated on
    ``sv & inst_stat_savdrd_*`` -- a runtime status (``inst.h:467-468``), not a
    capability. Ranking by action would put ``ref_strip`` ahead of ``s_ref_spot``
    where upstream picks ``s_ref_spot``; ranking saved first without ``sv`` would
    offer a route with no readings behind it. Neither is derivable here.
    """
    dtp20 = ("ref_spot", "ref_strip", "s_ref_spot", "s_ref_chart")
    with pytest.raises(ValueError, match="inst_stat_saved_readings"):
        rank_routes(dtp20)
    # Either branch alone ranks normally.
    assert rank_routes(("ref_spot", "ref_strip")) == ("ref_strip", "ref_spot")
    assert rank_routes(("s_ref_spot", "s_ref_chart")) == ("s_ref_chart", "s_ref_spot")


def test_the_dtp20_capability_word_really_does_decode_mixed():
    """Guards the premise of the test above rather than asserting it in prose."""
    word = (
        MODE_ROUTES["ref_spot"] | MODE_ROUTES["ref_strip"]
        | MODE_ROUTES["s_ref_spot"] | MODE_ROUTES["s_ref_chart"]
        | MODE_FLAGS["colorimeter"] | MODE_FLAGS["spectral"]
    )
    routes = decode_modes(word)
    families = {route_illumination(route) for route in routes}
    assert families == {"reflection", "s_reflection"}


def test_every_argyll_route_has_a_name_in_our_vocabulary():
    """The goal-level guarantee: no instrument can block on vocabulary.

    Extracted from the pinned source, every ``inst_mode_*`` symbol any driver ORs
    into a capability word decodes to one of these 18 routes, so naming all 18
    means a new instrument never needs a new mode name -- only the workflow that
    runs it. ``emis_tele`` alone is advertised by ex1, i1d3, i1pro, kleink10,
    specbos and ss; ``ref_xy`` by ss; the saved pair by dtp20; ``trans_spot_a``
    by specbos.
    """
    nameable = {parse_mode_name(f"{prefix}_{suffix}")[0] for prefix, suffix in _NAME_ROUTES}
    assert nameable == set(MODE_ROUTES)
    assert len(nameable) == 18


def test_the_five_routes_that_were_unreachable_are_now_nameable():
    assert parse_mode_name("emissive_tele") == ("emis_tele", (), None)
    assert parse_mode_name("reflective_xy") == ("ref_xy", (), None)
    assert parse_mode_name("saved_spot") == ("s_ref_spot", (), None)
    assert parse_mode_name("saved_chart") == ("s_ref_chart", (), None)
    assert parse_mode_name("transmission_ambient") == ("trans_spot_a", (), None)


def test_adaptivity_reaches_telephoto_now_that_it_can_be_named():
    """i1pro3.c:656-661 tests emis_spot OR emis_tele, so both take the qualifier."""
    assert parse_mode_name("emissive_tele_nonadaptive") == (
        "emis_tele", ("emis_nonadaptive",), None,
    )
    with pytest.raises(ValueError, match="spot and telephoto"):
        parse_mode_name("reflective_xy_nonadaptive")


def test_a_saved_route_is_a_separate_illumination_not_a_flag():
    """inst_mode_s_reflection is its own illumination bit, not reflection+flag."""
    assert route_illumination("s_ref_spot") == "s_reflection"
    assert MODE_ROUTES["s_ref_spot"] != MODE_ROUTES["ref_spot"]
    with pytest.raises(ValueError, match="inst_stat_saved_readings"):
        rank_routes(("ref_spot", "s_ref_spot"))


# --- workflow classification: what replaces the per-device membership tuples ---


def test_workflow_kind_classifies_every_published_mode_of_both_devices():
    """Derived from the mode name, so a new instrument's modes classify themselves."""
    for mode in _published_modes():
        assert workflow_kind(mode) in WORKFLOW_KINDS


def test_workflow_and_illumination_partition_the_whole_vocabulary():
    """Both used to be checked against hand-written per-device tuples --
    STRIP_MODES and TRANSMISSION_MODES -- which is how you verify a replacement
    while the thing it replaces still exists. It does not, so what is checked now
    is that the derivation is total: every published name classifies, and the
    classifications agree with the name's own axes.
    """
    for mode in _published_modes():
        kind, illumination = workflow_kind(mode), illumination_of(mode)
        assert kind in WORKFLOW_KINDS
        assert (kind == "strip") == mode.split("_")[1].startswith("strip")
        assert (illumination == "transmission") == mode.startswith("transmission_")


def test_the_unreachable_routes_classify_without_any_device_module():
    """A SpectroScan X-Y read and a DTP20 saved chart classify before either exists."""
    assert workflow_kind("reflective_xy") == "xy"
    assert workflow_kind("saved_chart") == "chart"
    assert workflow_kind("emissive_tele") == "spot"
    assert workflow_kind("transmission_ambient") == "spot"
    assert illumination_of("saved_spot") == "s_reflection"
    assert illumination_of("transmission_ambient") == "transmission"


def test_the_native_mode_words_sent_to_the_instrument_are_unchanged():
    """The eight literals i1pro.py:62-70 used to spell out, kept as the oracle.

    These words go to `set_mode` on a real instrument, so the shared vocabulary
    must never silently change one. Derivation replaced the table; this pins the
    values the table held, independently of how they are now composed.
    """
    assert {
        "emissive_spot_adaptive": 0x30018,
        "emissive_spot_nonadaptive": 0x30818,
        "ambient_spot": 0x30108,
        "reflective_spot": 0x30011,
        "transmission_spot": 0x30014,
        "emissive_strip": 0x30028,
        "reflective_strip": 0x30021,
        "transmission_strip": 0x30024,
    } == {name: mode_word(name) for name in (
        "emissive_spot_adaptive", "emissive_spot_nonadaptive", "ambient_spot",
        "reflective_spot", "transmission_spot", "emissive_strip",
        "reflective_strip", "transmission_strip",
    )}


def test_every_published_mode_still_composes_a_word():
    """No published mode may lose its native word when the vocabulary changes."""
    published = _published_modes()
    assert {mode: mode_word(mode) for mode in published}.keys() == set(published)
    assert all(mode_word(mode) for mode in published)


# --- provenance: the instrument table is Argyll's own, not ours ----------------


@pytest.fixture(scope="module")
def insttypes_c(argyll_archive):
    with zipfile.ZipFile(argyll_archive) as zipped:
        return zipped.read(PREFIX + "spectro/insttypes.c").decode()


def _accepted_names(insttypes_c):
    """Every (instType, name) pair `inst_enum()` matches, read from its own body."""
    body = re.search(r"instType inst_enum\(char \*name\)\s*\{.*?\n\}", insttypes_c, re.S)
    assert body, "inst_enum moved or changed shape upstream"
    accepted: dict[str, set[str]] = {}
    for branch in re.finditer(
        r"((?:strcmp\(name,\s*\"[^\"]*\"\)\s*==\s*0\s*(?:\|\|)?\s*)+)\)?\s*\n?\s*return\s+(inst\w+);",
        body.group(0),
    ):
        accepted.setdefault(branch.group(2), set()).update(
            re.findall(r'"([^"]*)"', branch.group(1))
        )
    return accepted


def test_every_instrument_binding_name_is_one_inst_enum_accepts(insttypes_c):
    """A name the helper sends must be one `inst_enum()` matches, or the attach fails.

    The table is the entire instrument-support surface: a helper names the
    instrument and Argyll resolves it. A single stale spelling here is a device
    that cannot be attached at all, which is why it is re-derived rather than
    trusted.
    """
    accepted = _accepted_names(insttypes_c)
    # The regex could silently miss a branch upstream reformatted, and a stale
    # table missing the same symbol would keep the set comparison below true. So
    # count what inst_enum actually returns and account for every one of them.
    body = re.search(r"instType inst_enum\(char \*name\)\s*\{.*?\n\}", insttypes_c, re.S)
    returned = set(re.findall(r"return\s+(\w+);", body.group(0)))
    # instUnknown is the fall-through; devRadiance and devPrisma are the two
    # ENABLE_VTPGLUT video-LUT branches, which are not measurement instruments.
    assert returned - set(accepted) == {"instUnknown", "devRadiance", "devPrisma"}, (
        "inst_enum returns a type the parser did not see"
    )
    assert set(INSTRUMENTS) == set(accepted), "instType coverage drifted from inst_enum"
    for symbol, names in INSTRUMENTS.items():
        assert set(names) == accepted[symbol], f"{symbol}: accepted spellings drifted"
    assert ACCEPTED_INSTRUMENT_NAMES == {n for names in accepted.values() for n in names}


def test_the_preferred_spelling_is_inst_names_own_where_upstream_accepts_it(insttypes_c):
    """`inst_name()` is the spelling a chart or report carries; prefer it.

    Upstream does not round-trip for one instrument: `inst_name(instSpyder2024)`
    returns "Datacolor Spyder 2024" while `inst_enum` matches only "GretagMacbeth
    Spyder 2024" (a copy of the Huey branch below it). Sending the returned name
    would attach nothing, so the accepted spelling is preferred there instead.
    This test states the exception explicitly: when upstream fixes it, it fails.
    """
    body = re.search(r"char \*inst_name\(instType itype\)\s*\{.*?\n\}", insttypes_c, re.S)
    assert body, "inst_name moved or changed shape upstream"
    published = dict(
        re.findall(r"case\s+(inst\w+):\s*\n\s*return\s+\"([^\"]*)\";", body.group(0))
    )
    accepted = _accepted_names(insttypes_c)
    mismatched = {
        symbol: name for symbol, name in published.items()
        if symbol in accepted and name not in accepted[symbol]
    }
    assert mismatched == {"instSpyder2024": "Datacolor Spyder 2024"}
    assert set(INSTRUMENTS) <= set(published)
    for symbol in INSTRUMENTS:
        assert instrument_display_name(symbol) == published[symbol]
        assert instrument_binding_name(published[symbol]) in accepted[symbol]
    for symbol, name in published.items():
        if symbol in INSTRUMENTS and symbol not in mismatched:
            assert INSTRUMENTS[symbol][0] == name, f"{symbol}: not inst_name's spelling"


def test_instrument_binding_name_resolves_symbols_and_aliases_and_refuses_anything_else():
    """A typo must fail in Python, not as a failed attach against real hardware."""
    assert instrument_binding_name("instSpectroScan") == "GretagMacbeth SpectroScan"
    assert instrument_binding_name("Xrite i1 Pro 3") == "X-Rite i1 Pro 3"
    assert (instrument_binding_name("ColorMunki Display")
            == "X-Rite i1 DisplayPro, ColorMunki Display")
    # Every accepted spelling resolves, and resolves to something itself accepted.
    for name in ACCEPTED_INSTRUMENT_NAMES:
        assert instrument_binding_name(name) in ACCEPTED_INSTRUMENT_NAMES
    assert instrument_binding_name("Datacolor Spyder 2024") == "GretagMacbeth Spyder 2024"
    assert instrument_display_name("GretagMacbeth Spyder 2024") == "Datacolor Spyder 2024"
    for rejected in ("", "i1Pro3", "X-Rite i1 Pro3", "instUnknown", None, 1):
        with pytest.raises(KeyError):
            instrument_binding_name(rejected)
        with pytest.raises(KeyError):
            instrument_display_name(rejected)


def test_contributed_names_are_what_the_series_adds_to_inst_enum(insttypes_c):
    """A contributed spelling must be one the COMPOSED `inst_enum()` matches.

    The series' own authenticated `insttypes.c` patch is applied to the pinned
    file, exactly as the native build composes it. The table must equal the
    composed accepted names minus the stock ones -- so a spelling the series
    does not add, or one it adds that is missing here, fails -- and must name
    only the enums the series manifest declares.
    """
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "series_contribution", root / "tools/native_argyll/contribution.py")
    contribution = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = contribution  # its dataclasses resolve their module
    spec.loader.exec_module(contribution)
    series = contribution.load_series(root / "tools/native_argyll/contributions/cr_pr")
    entry = next(e for e in series.entries if e.path == "spectro/insttypes.c")
    patch = (series.directory / entry.source).read_bytes()
    assert contribution.sha256(patch) == entry.source_sha256
    assert contribution.sha256(insttypes_c.encode()) == entry.pre_sha256
    composed = contribution.apply_unified_diff(insttypes_c, patch.decode(), entry.path)
    assert contribution.sha256(composed.encode()) == entry.post_sha256

    stock = _accepted_names(insttypes_c)
    added = {symbol: names for symbol, names in _accepted_names(composed).items()
             if symbol not in stock}
    assert {symbol: set(names) for symbol, names in CONTRIBUTED_INSTRUMENTS.items()} == added
    assert set(CONTRIBUTED_INSTRUMENTS) == set(series.instrument_types_added)
    assert not set(CONTRIBUTED_INSTRUMENTS) & set(INSTRUMENTS)
    # The preferred spelling is the composed inst_name()'s own.
    body = re.search(r"char \*inst_name\(instType itype\)\s*\{.*?\n\}", composed, re.S)
    published = dict(re.findall(r"case\s+(inst\w+):\s*\n\s*return\s+\"([^\"]*)\";",
                                body.group(0)))
    for symbol, names in CONTRIBUTED_INSTRUMENTS.items():
        assert names[0] == published[symbol]
        assert instrument_binding_name(symbol) == names[0] == instrument_binding_name(names[0])
        assert instrument_display_name(symbol) == published[symbol]


def test_one_named_instrument_has_no_upstream_constructor(argyll_archive):
    """`inst_enum` matches the Spectrocam; `new_inst` cannot build one.

    The table transcribes `inst_enum` and nothing else, so the name belongs in it.
    But upstream comments the constructor out, which means naming it can only ever
    produce an attach failure. Recording that here keeps it from being read later
    as a gap on our side -- and fails if upstream ever finishes the driver.
    """
    with zipfile.ZipFile(argyll_archive) as zipped:
        inst_c = zipped.read(PREFIX + "spectro/inst.c").decode()
    # Strip C comments FIRST: the Spectrocam branch is inside one, and matching
    # raw text would find the very line whose absence is the point.
    live = re.sub(r"/\*.*?\*/", "", inst_c, flags=re.S)
    constructors = dict(re.findall(
        r"if\s*\(itype\s*==\s*(inst\w+)\)\s*\n\s*p\s*=\s*\(inst\s*\*\)\s*(new_\w+)\(", live
    ))
    assert len(constructors) > 15, "the constructor dispatch moved or changed shape"
    assert "Spectrocam" in INSTRUMENTS["instSpectrocam"]
    assert "instSpectrocam" not in constructors, "upstream now builds a Spectrocam"
    commented = re.search(r"/\*\s*NYI\s*\n\s*else if \(itype == instSpectrocam\)", inst_c)
    assert commented, "the NYI comment around new_spc moved or changed shape"


def test_the_vpa_spelling_resolves_the_way_upstream_resolves_it():
    """`disptech_get_strid` (disptechs.c:438) opens with a documented
    "Backwards compatibility hack - replace " VPA" with " PVA"" before lookup, so
    an older correction file spelling the panel type that way still resolves.
    Without it the technology read as disptech_unknown and the correction was
    applied with a dtech nobody stated.

    It is a single strstr upstream, so only the first occurrence is rewritten.
    """
    from color_measurement.argyll_capability import DISPTECH_UNKNOWN, display_technology

    for spelled, canonical in (
        ("LCD CCFL VPA", "LCD CCFL PVA"),
        ("LCD White LED VPA", "LCD White LED PVA"),
        ("LCD RG Phosphor VPA", "LCD RG Phosphor PVA"),
    ):
        assert display_technology(spelled) == display_technology(canonical)
        assert display_technology(spelled) != DISPTECH_UNKNOWN
    # "VPA" without the leading space is not the pinned pattern and is not rewritten.
    assert display_technology("LCDVPA") == DISPTECH_UNKNOWN


@pytest.fixture(scope="module")
def disptechs(argyll_archive):
    with zipfile.ZipFile(argyll_archive) as zipped:
        return (zipped.read(PREFIX + "spectro/disptechs.h").decode(),
                zipped.read(PREFIX + "spectro/disptechs.c").decode())


def test_every_display_technology_is_transcribed_from_upstream(disptechs):
    """Re-derive the whole table, because only one of its 40 rows was exercised.

    A correction file's TECHNOLOGY resolves through this table into the disptech
    enumeration handed to col_cor_mat() and col_cal_spec_set(). Every test used
    "LCD RG Phosphor IPS", so a typo in any of the other 39 strids, or a wrong
    enum value, was invisible: the lookup would return disptech_unknown and the
    correction would be applied with a technology nobody stated.

    This asserts the transcription against disptech_info_array itself, the way
    INSTRUMENTS is re-derived from inst_enum's body.
    """
    from color_measurement.argyll_capability import DISPLAY_TECHNOLOGIES

    header, body = disptechs
    values = {name: int(value, 16) for name, value in re.findall(
        r"(disptech_[A-Za-z0-9_]+)\s*=\s*(0x[0-9A-Fa-f]+)", header)}
    # Comments first: an entry commented out must not be read as a live row.
    array = re.sub(r"/\*.*?\*/", "", body, flags=re.S).split("disptech_info_array[] = {", 1)[1]
    # {dtech, strid, desc, ...} -- disptechs.h:115-118 gives strid as the FIRST string.
    rows = re.findall(r"\{\s*(disptech_[A-Za-z0-9_]+)\s*,\s*\"([^\"]*)\"\s*,\s*\"[^\"]*\"", array)
    assert len(rows) == 40, f"upstream array changed shape: {len(rows)} rows"
    assert dict(DISPLAY_TECHNOLOGIES) == {strid: values[name] for name, strid in rows}
    # disptech_end terminates the array and has no row of its own.
    assert "disptech_end" in values
    assert not any(name == "disptech_end" for name, _ in rows)


def test_the_measurement_type_table_is_transcribed_from_upstream(argyll_archive):
    """Use live xspect.h; inst.h's obsolete enum is disabled by #ifdef NEVER."""
    from color_measurement.argyll_capability import MEASUREMENT_TYPES

    with zipfile.ZipFile(argyll_archive) as zipped:
        source = zipped.read(PREFIX + "xicc/xspect.h").decode()
    block = re.search(r"inst_mrt_none\s*=\s*0,.*?\}\s*inst_meas_type;", source, re.S)
    assert block, "inst_meas_type moved or changed shape upstream"
    found = {int(value): name for name, value in
             re.findall(r"inst_mrt_([a-z_]+)\s*=\s*(\d+)", block.group(0))}
    assert found == {0: "none", **dict(MEASUREMENT_TYPES)}


@pytest.mark.parametrize("mtype,unit,quantity", [
    (1, "cd/m^2", "spectral_radiance"),
    (2, "lux", "spectral_irradiance"),
    (3, "cd s/m^2", "time_integrated_spectral_radiance"),
    (4, "lux s", "spectral_radiant_exposure"),
    (5, "1", "reflectance"),
    (6, "1", "transmittance"),
])
def test_each_colorimetric_type_carries_its_upstream_units(mtype, unit, quantity):
    from color_measurement.argyll_capability import quantity_contract

    contract = quantity_contract(mtype)
    assert contract["xyz_unit"] == unit
    assert contract["quantity"] == quantity
    if mtype in (3, 4):
        # Directional luminance integrated over time retains the sr basis;
        # incident illuminance integrated over time is luminous exposure.
        assert contract["scale"] == {
            3: "absolute; Y is time-integrated luminance",
            4: "absolute; Y is luminous exposure",
        }[mtype]
    # Only the two relative types compute XYZ under an assumed illuminant.
    assert (contract["illuminant"] == "D50") == (mtype in (5, 6))


@pytest.mark.parametrize("mtype", [0, 7, 8, -1, 99])
def test_a_reading_whose_type_is_unknown_or_not_colorimetric_is_refused(mtype):
    """Unset, sensitivity and frequency types have no colour-reading contract.

    Publishing them with borrowed units would attach a quantity nobody measured.
    """
    from color_measurement.argyll_capability import quantity_contract

    with pytest.raises(KeyError, match="No quantity contract"):
        quantity_contract(mtype)


def test_the_expected_type_of_every_mode_has_a_quantity_contract():
    """One vocabulary, checked end to end.

    This used to compare the generic table against i1pro3.py's own mode -> mtype
    expression, because the same instrument could be labelled two ways depending
    on which stack read it. There is one stack now, so the check is that the chain
    is total and consistent: every published mode forecasts a measurement type,
    and every forecast type has a quantity contract whose units a UI can show
    before the first reading.
    """
    from color_measurement.argyll_capability import expected_measurement_type, quantity_contract

    for mode in _published_modes():
        contract = quantity_contract(expected_measurement_type(mode))
        assert contract["xyz_unit"] and contract["quantity"]
        # Reflective and transmissive are the RELATIVE types, computed as if
        # under a non-fluorescent D50 (inst.h:92-94). An emissive or ambient
        # reading is absolute and must never claim an illuminant it did not have.
        assert (contract["illuminant"] == "D50") == (
            mode.split("_")[0] in ("reflective", "saved", "transmission"))


# --- provenance: the calibration vocabulary ------------------------------------


@pytest.fixture(scope="module")
def instappsup_c(argyll_archive):
    with zipfile.ZipFile(argyll_archive) as zipped:
        return zipped.read(PREFIX + "spectro/instappsup.c").decode()


def test_the_calibration_vocabulary_matches_the_pinned_header(inst_h):
    """Three enumerations, transcribed rather than remembered.

    Completeness matters as much as correctness here: a condition upstream names
    and this table omits reaches a client as an unrenderable integer, which is the
    state every non-i1Pro instrument was in before these tables existed.
    """
    for prefix, table in (
        ("inst_calt_", CALIBRATION_TYPES),
        ("inst_calc_id_", CALIBRATION_REFERENCES),
    ):
        declared = _enum_values(inst_h, rf"({prefix}\w+)\s*=\s*(0x[0-9A-Fa-f]+)")
        for name, value in table.items():
            assert declared[f"{prefix}{name}"] == value, f"{prefix}{name} moved upstream"
    declared = _enum_values(inst_h, r"(inst_calc_\w+)\s*=\s*(0x[0-9A-Fa-f]+)")
    for name, value in CALIBRATION_CONDITIONS.items():
        assert declared[f"inst_calc_{name}"] == value, f"inst_calc_{name} moved upstream"
    # Every condition upstream declares that is not a mask or a flag is named here.
    upstream = {
        name for name in declared
        if not name.endswith(("_mask", "_flag")) and not name.startswith("inst_calc_id_")
    }
    assert upstream == {f"inst_calc_{name}" for name in CALIBRATION_CONDITIONS}


def test_the_sensor_positions_match_the_pinned_header(inst_h):
    """inst_config assigns no values (inst.h:717-726): each is its place in the
    list, so the order IS the transcription. Two names transposed -- surface and
    emission, say -- would tell an operator to move the wrong thing, and nothing
    that only checks the names are present would notice."""
    body = inst_h.split("/* Instrument configuration/sensor position*/", 1)[1]
    body = body.split("} inst_config;", 1)[0]
    assert "=" not in body, "upstream now numbers inst_config explicitly"
    names = re.findall(r"\binst_conf_(\w+)", body)
    assert names == list(INSTRUMENT_CONFIGURATIONS)
    assert list(INSTRUMENT_CONFIGURATIONS.values()) == list(range(len(names)))


def test_a_calibration_condition_is_a_value_and_not_a_bitfield():
    """The whole table would decode wrongly if this were got backwards.

    ``inst_calc_man_ref_white`` is 0x10 and ``man_ref_whitek`` 0x20, so a bitwise
    decoder reports BOTH for 0x30 -- a reading that is neither of them. 0x30 is
    ``man_ref_dark``, its own value in the ``man`` nibble (inst.h:649-659), and
    upstream reads the group with ``switch (calc & inst_calc_cond_mask)``.
    """
    assert calibration_condition(0x30) == "man_ref_dark"
    assert calibration_condition(0x70) == "man_cal_smode"
    # The optional flag is stripped before matching, never folded into the value.
    assert calibration_condition(0x80000020) == "man_ref_whitek"
    assert condition_is_optional(0x80000020) and not condition_is_optional(0x20)
    # A value upstream does not name leaves the operator a prompt to refuse.
    assert calibration_condition(0x7) == "unknown"


def test_every_condition_says_what_the_operator_must_do():
    """A client builds screens from `action`; an unnamed condition builds none."""
    for name in CALIBRATION_CONDITIONS:
        if name in ("none", "unknown"):
            continue
        detail = condition_detail(name)
        assert detail["action"] in CONDITION_ACTIONS
        assert detail["instruction"] and isinstance(detail["instruction"], str)
    # Seven genuinely different screens for nineteen conditions -- placing the
    # instrument on a reference, clicking it down, operating its own panel, moving
    # its sensor, displaying a patch, changing a filter, and reading a message.
    assert len(CONDITION_ACTIONS) == 7


def test_only_the_click_condition_goes_unacknowledged(instappsup_c):
    """Upstream's own rule, re-read from the pinned source rather than recalled.

    ``inst_calc_man_ref_whitek`` is the one condition upstream does not wait on,
    because the instrument's own click triggers the calibration. A ladder that
    waited would prompt for a press that means nothing and would do it before the
    driver had armed the switch.
    """
    assert re.search(
        r"if \(!doimmediately\s*\n\s*&& \(calc & inst_calc_cond_mask\) "
        r"!= inst_calc_man_ref_whitek\)",
        instappsup_c,
    ), "upstream no longer exempts the click condition from its keypress"
    unacknowledged = [
        name for name in CALIBRATION_CONDITIONS
        if name not in ("none", "unknown") and not condition_detail(name)["acknowledged"]
    ]
    assert unacknowledged == ["man_ref_whitek"]


def test_calibration_types_decode_as_a_bitfield_and_name_the_deferrable_ones():
    """Unlike the conditions, ``inst_cal_type`` IS a bitfield: ``get_n_a_cals``
    answers with every calibration needed at once (inst.h:586-621). The deferrable
    two are not an outstanding failure -- the driver may do them at measurement."""
    word = CALIBRATION_TYPES["ref_white"] | CALIBRATION_TYPES["ref_freq"]
    assert decode_calibration_types(word) == ("ref_white", "ref_freq")
    assert deferrable_calibrations(word) == ("ref_freq",)
    assert decode_calibration_types(0) == ()


def test_a_reference_identifier_separates_warnings_from_what_to_present():
    """``trans_low`` and ``trans_wl`` are independent bits that may ACCOMPANY a
    value from the filter nibble or a tile serial (inst.h:680-693), so decoding
    them uniformly would lose whichever arrived second."""
    assert calibration_reference(0x00300000)["reference"] == "filt_D50"
    both = calibration_reference(0x00010001)
    assert both["reference"] == "ref_sn" and both["warnings"] == ("trans_low",)
    assert "too low for accuracy" in both["warning_detail"][0]
    assert calibration_reference(0)["reference"] == "none"


def test_the_three_refresh_capabilities_are_not_one_capability(argyll_archive):
    """Measuring a refresh rate, retrieving a stored one and writing one are
    separate upstream capabilities, held by different drivers.

    ``read_refrate`` measures. ``get_refr_rate`` returns "the last calibrated
    refresh rate" (``inst.h:1076-1085``) -- a value a caller may itself have
    written through ``set_refr_rate``, where 0.0 marks it invalid and the range
    is 5.0 to 150.0 Hz. Publishing a retrieved rate as a measurement would
    republish an input as a reading.

    The failure this must catch is treating "refresh" as one capability. Gating
    measurement on the stored-value bit drops the four drivers that only measure;
    gating retrieval on the measurement bit invents a stored value for them.
    Asserting the bits merely exist could not catch either, so this asserts they
    are advertised by strictly different driver sets, which no conflation
    satisfies. Advertisement is `cap2 |= ...`, which some drivers chain across
    lines; testing a bit in an application is not advertising it.
    """
    bits = ("inst2_emis_refr_meas", "inst2_get_refresh_rate", "inst2_set_refresh_rate")
    measures, stored, settable = set(), set(), set()
    with zipfile.ZipFile(argyll_archive) as zipped:
        contract = zipped.read(HEADER).decode()
        for entry in zipped.namelist():
            name = entry[len(PREFIX + "spectro/"):].removesuffix(".c")
            if not entry.startswith(PREFIX + "spectro/") or not entry.endswith(".c"):
                continue
            source = zipped.read(entry).decode("utf-8", "replace")
            # Count each advertisement independently. Requiring read_refrate
            # here would hide a future getter/setter-only driver, assuming the
            # very nesting this census is supposed to verify. Default methods
            # in inst.c advertise no bits. Comments are not advertisements.
            source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
            advertised = " ".join(re.findall(r"\bcap2\s*\|=\s*(.*?);", source, re.S))
            for bit, group in zip(bits, (measures, stored, settable)):
                if re.search(rf"\b{bit}\b", advertised):
                    group.add(name)

    assert "last calibrated refresh rate" in contract, "get_refr_rate contract moved"
    assert measures == {"dtp92", "i1d3", "i1disp", "i1pro", "i1pro3",
                        "kleink10", "munki", "specbos", "spyd2"}
    # Strict nesting: a driver that measures need not store, and one that stores
    # need not let anyone write. DTP92 is upstream's own named example of the
    # second gap (set_refr_rate's comment); the first covers four drivers.
    assert settable < stored < measures
    assert measures - stored == {"i1pro", "i1pro3", "kleink10", "munki"}
    assert stored - settable == {"dtp92"}
