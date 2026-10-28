"""Decode ArgyllCMS instrument capability words into named modes and facilities.

The description helper already asks every instrument what it can do:
``meter->capabilities(meter, &cap, &cap2, &cap3)`` (``description.c:260``) runs on
the shared ``inst`` vtable and therefore works for every supported instrument, not
a chosen few. Until this module existed the three words were published as raw
integers and discarded unread, so measurement routes were assigned instead by
testing device identity (``driver_manifest.py``). This module decodes the reply.

Nothing here knows any instrument. It is a transcription of the bit tables in
``spectro/inst.h`` of the pinned Argyll 3.5.0 source, plus the two rules its
callers apply to them.

Bit layout (``inst.h:200-252``)
------------------------------
The primary word is two orthogonal axes plus independent modifiers:

* **illumination** -- how the sample is lit (``inst_mode_illum_mask`` 0x0000000f,
  ``:208``)
* **action** -- what physical motion the reading is (``inst_mode_sub_mask``
  0x000007f0, ``:226``)
* **modifiers** -- ``inst_mode_dep_extra_mask`` 0x00007800 (``:240``), which is
  part of ``inst_mode_measurement_mask``, and ``inst_mode_extra_mask``
  0x00070000 (``:249``), which is not, plus ``inst_mode_calibration``.

A *route* is a named illumination x action pairing. Argyll names 18 of the 28
possible pairings (``inst.h:258-297``); the other 10 are not modes. Emission names
5 of the 7 actions -- there is no emission X-Y and no emission chart -- so this
module transcribes the table rather than computing a product.

Membership test (``inst.h:307``)
-------------------------------
``#define IMODETST(mbits, mode) (((mbits) & (mode)) == (mode))``

A route is present when every bit of its word is set. Because a route is composed
of two bits, upstream immediately qualifies its own macro (``inst.h:305-306``):

    /* (This isn't foolproof - it->check_mode() is better for modes */
    /*  composed of more than one bit) */

**So decoding is advisory, never definitive.** ``capabilities()`` is documented as
advisory and ``set_mode`` may change it (``inst.h:191-193, 826-827``); the
definitive answer is ``check_mode()``.

The worked examples are ``i1pro.c:672-706`` and ``i1pro3.c:632-674``, whose
``convert_mode`` returns "no modes" for any word not matching one of its eight
``IMODETST`` branches -- including words every bit of which the capability word
advertises. ``i1d3_check_mode`` (``i1d3.c:3987-4011``) refuses more weakly than
once claimed here: its second test rejects any word containing none of
``emis_spot``/``emis_tele``/``emis_ambient``, so a bare advertised modifier such
as ``inst_mode_emis_refresh_ovd`` is refused -- but it refuses no *route* that
``cap1`` advertises, because all three are unconditionally present
(``i1d3.c:3902-3904``).

Callers of :func:`decode_modes` must treat the result as *candidates to confirm*,
and say so in whatever they publish.

Ranking (``chartread.c:752-790``)
---------------------------------
Applications do not pick a route, they rank the candidates and degrade:
chart (3) > X-Y (2) > strip (1) > spot (0), each guarded by ``check_mode()``. One
code path serves SpectroScan, DTP41, DTP20 and i1Pro alike. :data:`ACTION_RANK`
and :func:`rank_routes` carry that order so callers inherit it rather than
reinvent it.

Example
-------
An i1 Display 3 advertises emission spot/tele/ambient plus modifiers
(``i1d3.c:3895-3933``)::

    >>> word = 0x00000008 | 0x00000010 | 0x00000400 | 0x00000100 | 0x00010000
    >>> decode_modes(word)
    ('emis_ambient', 'emis_spot', 'emis_tele')
    >>> rank_routes(decode_modes(word))[0]
    'emis_spot'
    >>> 'spectral' in decode_mode_flags(word)
    False

Limitations
-----------
* Values are pinned to Argyll 3.5.0 and verified against the digest-checked source
  archive by ``tests/test_argyll_capability.py``. A different upstream release may
  move bits; the test fails rather than silently decoding wrongly.
* These words are bitmasks, not measured quantities. They carry no units and no
  calibration context, and decoding one says nothing about whether an instrument
  is present, initialised, or physically validated.
* An unknown bit is preserved and reported by :func:`undecoded_bits` rather than
  dropped, so a newer instrument advertising a capability this table lacks is
  visible instead of silently narrowed.
"""

from types import MappingProxyType

# ``inst_mode`` illumination axis -- inst.h:200-208.
ILLUMINATIONS = MappingProxyType({
    "reflection": 0x00000001,
    "s_reflection": 0x00000002,
    "transmission": 0x00000004,
    "emission": 0x00000008,
})
ILLUMINATION_MASK = 0x0000000F

# ``inst_mode`` action axis -- inst.h:210-226.
ACTIONS = MappingProxyType({
    "spot": 0x00000010,
    "strip": 0x00000020,
    "xy": 0x00000040,
    "chart": 0x00000080,
    "ambient": 0x00000100,
    "ambient_flash": 0x00000200,
    "tele": 0x00000400,
})
ACTION_MASK = 0x000007F0

# Acquisition preference order -- chartread.c:752-790 records rmode 3..0 and falls
# back down the list. Actions absent from that ranking are not chart strategies.
ACTION_RANK = MappingProxyType({"chart": 3, "xy": 2, "strip": 1, "spot": 0})

# Two groups, and the distinction is load-bearing. "Extra dependent modes"
# (inst.h:231-240, mask 0x7800) sit INSIDE inst_mode_measurement_mask
# (inst.h:299-301), so they are part of exclusive mode identity. "Extra
# independent modes" (inst.h:242-249, mask 0x70000) sit outside it.
DEPENDENT_FLAGS = ("emis_nonadaptive", "ref_uv", "emis_refresh_ovd", "emis_norefresh_ovd")
INDEPENDENT_FLAGS = ("colorimeter", "spectral", "highres")

# inst_mode_emis_norefresh_ovd (0x6000) CONTAINS inst_mode_emis_refresh_ovd
# (0x2000), so IMODETST reports both for a norefresh word. Upstream disambiguates
# by testing norefresh first, commented "Must test this first!" in every driver
# that does it: i1d3.c:3247, i1disp.c:2356, specbos.c:2426. decode_mode_flags
# applies that precedence; the raw bits stay in the table.
MODE_FLAGS = MappingProxyType({
    "emis_nonadaptive": 0x00000800,
    "ref_uv": 0x00001000,
    "emis_refresh_ovd": 0x00002000,
    "emis_norefresh_ovd": 0x00006000,
    "colorimeter": 0x00010000,
    "spectral": 0x00020000,
    "highres": 0x00040000,
    "calibration": 0x80000000,
})

# The 18 combinations Argyll names -- inst.h:258-297. Not every illumination x
# action pairing exists: there is no emission X-Y, no emission chart and no
# transmission telephoto mode.
MODE_ROUTES = MappingProxyType({
    name: ILLUMINATIONS[illumination] | ACTIONS[action]
    for name, illumination, action in (
        ("ref_spot", "reflection", "spot"),
        ("ref_strip", "reflection", "strip"),
        ("ref_xy", "reflection", "xy"),
        ("ref_chart", "reflection", "chart"),
        ("s_ref_spot", "s_reflection", "spot"),
        ("s_ref_strip", "s_reflection", "strip"),
        ("s_ref_xy", "s_reflection", "xy"),
        ("s_ref_chart", "s_reflection", "chart"),
        ("trans_spot", "transmission", "spot"),
        ("trans_spot_a", "transmission", "ambient"),
        ("trans_strip", "transmission", "strip"),
        ("trans_xy", "transmission", "xy"),
        ("trans_chart", "transmission", "chart"),
        ("emis_spot", "emission", "spot"),
        ("emis_tele", "emission", "tele"),
        ("emis_ambient", "emission", "ambient"),
        ("emis_ambient_flash", "emission", "ambient_flash"),
        ("emis_strip", "emission", "strip"),
    )
})

# ``inst2_capability`` -- inst.h:322-... . The three ``xy_*`` flags gate the seven
# optional X-Y methods that live on the shared vtable (inst.h:893-931, 327-329);
# they are a capability group, not a SpectroScan interface.
CAPABILITY2 = MappingProxyType({
    "xy_holdrel": 0x00000001,
    "xy_locate": 0x00000002,
    "xy_position": 0x00000004,
    "meas_disp_update": 0x00000010,
    "get_refresh_rate": 0x00000020,
    "set_refresh_rate": 0x00000040,
    "emis_refr_meas": 0x00000080,
    "prog_trig": 0x00000100,
    "user_trig": 0x00000200,
    "switch_trig": 0x00000400,
    "user_switch_trig": 0x00000800,
    "bidi_scan": 0x00001000,
    "cal_using_switch": 0x00002000,
    "has_scan_toll": 0x00004000,
    "no_feedback": 0x00008000,
    "opt_calibs": 0x00010000,
    "has_leds": 0x00200000,
    "has_target": 0x00400000,
    "has_sensmode": 0x00800000,
    "has_battery": 0x01000000,
    "disptype": 0x02000000,
    "ccmx": 0x04000000,
    "ccss": 0x08000000,
    "ambient_mono": 0x10000000,
    "ambient_cardioid": 0x20000000,
    "get_min_int_time": 0x40000000,
    "set_min_int_time": 0x80000000,
})

# ``inst3_capability`` -- measurement averaging and the filter group.
CAPABILITY3 = MappingProxyType({
    "average": 0x00000001,
    "filter_none": 0x00000010,
    "filter_D50": 0x00000020,
    "filter_D65": 0x00000040,
    "filter_UVCut": 0x00000080,
    "filter_pol": 0x00000100,
    "filter_Custom": 0x00000200,
})

# The X-Y method group: present together or not at all, per inst.h:327-329.
XY_POSITIONING_FLAGS = ("xy_holdrel", "xy_locate", "xy_position")


def _present(word: int, table) -> tuple[str, ...]:
    """Names in ``table`` whose every bit is set in ``word`` (``IMODETST``)."""
    _check_word(word)
    return tuple(sorted(name for name, bits in table.items() if word & bits == bits))


def _check_word(word: int) -> None:
    if type(word) is not int or not 0 <= word <= 0xFFFFFFFF:
        raise ValueError(f"Capability word must be a 32-bit unsigned integer, got {word!r}")


def decode_modes(primary: int) -> tuple[str, ...]:
    """Named routes advertised by a primary capability word, sorted by name.

    Advisory only. Confirm every returned route with ``check_mode()`` before
    offering it. ``inst.h:305-306`` warns that bit matching is not definitive
    for modes composed of more than one bit.
    """
    return _present(primary, MODE_ROUTES)


def decode_mode_flags(primary: int) -> tuple[str, ...]:
    """Mode modifiers set in a primary word, with upstream's one precedence rule.

    ``inst_mode_emis_norefresh_ovd`` contains ``inst_mode_emis_refresh_ovd``, so a
    bare ``IMODETST`` sweep reports both. Upstream resolves this by testing
    norefresh first -- ``i1d3.c:3247``, ``i1disp.c:2356``, ``specbos.c:2426``, each
    commented "Must test this first!" -- so norefresh wins and refresh is dropped.

    Harmless on a capability word, where an instrument may genuinely advertise
    both. Wrong on a *mode* word, which is what both session helpers publish as
    ``mode``, and which is where this function is most likely to be pointed.
    """
    present = _present(primary, MODE_FLAGS)
    if "emis_norefresh_ovd" in present:
        present = tuple(name for name in present if name != "emis_refresh_ovd")
    return present


def decode_facilities(secondary: int) -> tuple[str, ...]:
    """Named ``inst2`` facilities, including the optional method groups."""
    return _present(secondary, CAPABILITY2)


def decode_filters(tertiary: int) -> tuple[str, ...]:
    """Named ``inst3`` facilities: measurement averaging and the filter group."""
    return _present(tertiary, CAPABILITY3)


def undecoded_bits(word: int, table) -> int:
    """Bits set in ``word`` that ``table`` does not name.

    A newer instrument may advertise a capability this pinned table lacks. That is
    reportable, not droppable: narrowing it silently would make an unknown
    capability indistinguishable from an absent one.
    """
    _check_word(word)
    known = 0
    for bits in table.values():
        known |= bits
    return word & ~known


def route_illumination(route: str) -> str:
    """The illumination axis of a named route."""
    bits = MODE_ROUTES[route] & ILLUMINATION_MASK
    return next(name for name, value in ILLUMINATIONS.items() if value == bits)


def route_action(route: str) -> str:
    """The action axis of a named route."""
    bits = MODE_ROUTES[route] & ACTION_MASK
    return next(name for name, value in ACTIONS.items() if value == bits)


def _route_strategy(route: str) -> str:
    """Return the chartread strategy represented by a named route.

    ``inst_mode_trans_spot_a`` is the one deliberately odd upstream enum: its
    action bit is ``inst_mode_ambient`` even though it is an alternate
    transmission *spot* geometry (``inst.h:278-279``).  Keep the raw action
    decoder truthful while giving route ranking the semantic strategy.
    """
    return "spot" if route == "trans_spot_a" else route_action(route)


def rank_routes(routes, *, per_patch: bool = False) -> tuple[str, ...]:
    """Order candidates using the relevant ``chartread.c`` fallback branch.

    For a chart-style read, upstream tries chart, X-Y, strip, then spot
    (``chartread.c:752-790`` and ``:900-950``).  With ``pbypatch`` it tries the
    spot route first, then falls back to that order; pass ``per_patch=True`` to
    represent that explicit request.  The helper only ranks candidates in one
    requested illumination family; it does not call ``check_mode()`` or claim
    that a capability bit is a confirmed mode. Ambient, ambient-flash and
    telephoto routes are intentionally left outside the chart strategies.

    **Saved-reading routes cannot be ranked here, and mixing them raises.**
    ``chartread.c:900-931`` tries every ``s_ref_*`` route *before* any live one,
    but gates each on ``sv & inst_stat_savdrd_*`` -- a runtime status obtained
    from ``get_set_opt(inst_stat_saved_readings, &sv)`` (``inst.h:467-468``,
    values ``:575-579``), which is not in any capability word. So a
    capability-only ranking cannot express that branch: ordering saved routes
    first without ``sv`` would offer a route with no readings behind it, and
    ordering them by action -- as this function did -- silently inverts upstream.

    This is not hypothetical. The DTP20 advertises live and saved routes in one
    word (``dtp20.c:1456-1461``: ``ref_spot | ref_strip | s_ref_spot |
    s_ref_chart``), so a decoded capability list really does arrive mixed. For
    that instrument upstream picks ``s_ref_spot``; ranking by action picks
    ``ref_strip``. The caller must query saved-reading status and choose the
    branch before ranking within it.
    """
    saved = {route for route in routes if route_illumination(route) == "s_reflection"}
    if saved and set(routes) - saved:
        raise ValueError(
            "Saved and live routes cannot be ranked together: upstream selects the "
            "branch from inst_stat_saved_readings, not from capability "
            f"(chartread.c:900-931). Saved routes present: {sorted(saved)}"
        )

    def key(route: str):
        strategy = _route_strategy(route)
        rank = 4 if per_patch and strategy == "spot" else ACTION_RANK.get(strategy, -1)
        return -rank, route

    return tuple(sorted(routes, key=key))


def requires_positioning(secondary: int) -> bool:
    """Whether the instrument carries the X-Y positioning method group.

    ``ss_xy_clear`` (``ss.c:703-721``) gates raising the sensor, releasing the
    paper and moving home on ``cap2 & inst2_xy_position`` -- a capability, not a
    device identity. Any instrument carrying the flag has a motorised teardown, so
    cleanup cost follows the flag rather than a global constant.
    """
    _check_word(secondary)
    return bool(secondary & CAPABILITY2["xy_position"])


# Our published mode vocabulary against Argyll's. The retired per-device frontends
# published 23 mode names between them (i1pro 8, i1pro3 15) that resolved to 8 of
# the routes above plus two orthogonal modifier axes; this table keeps the names.
# The names are ours; the structure underneath is Argyll's, which is why one
# acquisition path can serve every family and why a third device needs a table
# row rather than a driver.
#
# (prefix, suffix) -> (illumination, action). The pairing is not the obvious one:
# "ambient_spot" is emission x ambient (inst_mode_emis_ambient), not an action of
# spot, and "ambient_flash" is emission x ambient_flash.
_NAME_ROUTES = MappingProxyType({
    # Emission.
    ("emissive", "spot"): ("emission", "spot"),
    ("emissive", "strip"): ("emission", "strip"),
    ("emissive", "tele"): ("emission", "tele"),
    ("ambient", "spot"): ("emission", "ambient"),
    ("ambient", "flash"): ("emission", "ambient_flash"),
    # Reflection, live.
    ("reflective", "spot"): ("reflection", "spot"),
    ("reflective", "strip"): ("reflection", "strip"),
    ("reflective", "xy"): ("reflection", "xy"),
    ("reflective", "chart"): ("reflection", "chart"),
    # Reflection, read back from the instrument's own stored readings. A separate
    # illumination in Argyll (inst_mode_s_reflection), not a flag on reflection,
    # and selectable only when inst_stat_saved_readings says data exists.
    ("saved", "spot"): ("s_reflection", "spot"),
    ("saved", "strip"): ("s_reflection", "strip"),
    ("saved", "xy"): ("s_reflection", "xy"),
    ("saved", "chart"): ("s_reflection", "chart"),
    # Transmission. "transmission_ambient" is upstream's inst_mode_trans_spot_a:
    # an alternate 90/diffuse spot geometry that reuses the ambient action bit
    # (inst.h:278-279), which is why it is named for its axes and not for "spot".
    ("transmission", "spot"): ("transmission", "spot"),
    ("transmission", "ambient"): ("transmission", "ambient"),
    ("transmission", "strip"): ("transmission", "strip"),
    ("transmission", "xy"): ("transmission", "xy"),
    ("transmission", "chart"): ("transmission", "chart"),
})

# Trailing qualifiers. "adaptive" is the absence of the non-adaptive bit, not a bit
# of its own. M0/M1/M2/M3 are reflective measurement conditions -- a separate axis
# carried by inst_opt_filter and the xspect condition enums, never by the mode word.
# FILTERS keeps the filter option and measurement-condition values separate.
_ADAPTIVITY = frozenset({"adaptive", "nonadaptive"})
# The only routes whose driver branch reads the bit (i1pro3.c:656-661).
_ADAPTIVE_ROUTES = frozenset({"emis_spot", "emis_tele"})

_NAME_MODIFIERS = MappingProxyType({
    "adaptive": (None, None),
    "nonadaptive": ("emis_nonadaptive", None),
    "m0": (None, "m0"), "m1": (None, "m1"), "m2": (None, "m2"), "m3": (None, "m3"),
})


def parse_mode_name(mode: str) -> tuple[str, tuple[str, ...], str | None]:
    """Resolve one published mode name to ``(route, mode_flags, condition)``.

    The shared request builder composes ``route | spectral | colorimeter |
    [emis_nonadaptive]`` from these components. Route and modifier meaning belong
    to the common Argyll vocabulary, independent of the selected instrument.

    ``condition`` is returned separately and never folded into the mode word: a
    reflective measurement condition is a different axis from a measurement mode,
    and merging them would lose which one an instrument actually refused.

    Raises ``ValueError`` for a name outside the grammar rather than guessing.
    """
    if not isinstance(mode, str) or not mode:
        raise ValueError("Mode name must be a non-empty string")
    parts = mode.split("_")
    if len(parts) < 2:
        raise ValueError(f"Mode name {mode!r} is not <illumination>_<action>[_<modifier>]")
    key = (parts[0], parts[1])
    if key not in _NAME_ROUTES:
        raise ValueError(f"Mode name {mode!r} has no Argyll route")
    illumination, action = _NAME_ROUTES[key]
    route = next(
        name for name, word in MODE_ROUTES.items()
        if word == ILLUMINATIONS[illumination] | ACTIONS[action]
    )
    flags: tuple[str, ...] = ()
    condition = None
    adaptivity_seen = False
    condition_seen = False
    for part in parts[2:]:
        if part not in _NAME_MODIFIERS:
            raise ValueError(f"Mode name {mode!r} carries unknown qualifier {part!r}")
        flag, found = _NAME_MODIFIERS[part]
        # Adaptivity is inst_mode_emis_nonadaptive (inst.h:231-232), and it is
        # action-dependent, not merely emission-dependent. Both drivers honour the
        # bit ONLY in the spot/tele branch -- i1pro3.c:656-661 and i1pro.c
        # convert_mode match `IMODETST(im, emis_spot) || IMODETST(im, emis_tele)`
        # and test nonadaptive inside it; the emis_strip, emis_ambient and
        # emis_ambient_flash branches match on IMODETST and drop the bit.
        #
        # That matters because nothing downstream catches it. cap1 advertises
        # emis_nonadaptive for the spot route, so `m & ~cap` passes, check_mode
        # ACCEPTS a word like emis_ambient_flash|emis_nonadaptive, and set_mode
        # silently reduces it to plain ambient flash. The advisory/definitive
        # safety net does not see this class at all -- so the parser must.
        if part in _ADAPTIVITY and route not in _ADAPTIVE_ROUTES:
            raise ValueError(
                f"Mode name {mode!r}: {part!r} applies only to spot and telephoto "
                f"emission, not to {route}"
            )
        # M0/M1/M2/M3 are reflective measurement conditions, carried by
        # inst_opt_filter and the xspect condition enums, not by any mode bit.
        if found is not None and illumination != "reflection":
            raise ValueError(f"Mode name {mode!r}: {part!r} is a reflective condition")
        if part in _ADAPTIVITY and adaptivity_seen:
            raise ValueError(f"Mode name {mode!r} repeats an adaptivity qualifier")
        if found is not None and condition_seen:
            raise ValueError(f"Mode name {mode!r} repeats a measurement condition")
        if part in _ADAPTIVITY:
            adaptivity_seen = True
        if found is not None:
            condition_seen = True
        if flag is not None:
            flags += (flag,)
        if found is not None:
            condition = found
    return route, flags, condition


def mode_word(mode: str, *, spectral: bool = True, colorimeter: bool = True) -> int:
    """Compose the ``inst_mode`` word a session helper would set for ``mode``.

    Composes the word consumed by the shared native session:
    ``route | inst_mode_spectral | inst_mode_colorimeter | [inst_mode_emis_nonadaptive]``
    A colorimeter such as the i1 Display 3 carries no spectral bit, hence the
    keywords -- they are the instrument's capability, not a constant.

    Advisory: the word must still be confirmed with ``check_mode()``.
    """
    route, flags, _ = parse_mode_name(mode)
    word = MODE_ROUTES[route]
    if spectral:
        word |= MODE_FLAGS["spectral"]
    if colorimeter:
        word |= MODE_FLAGS["colorimeter"]
    for flag in flags:
        word |= MODE_FLAGS[flag]
    return word


def baseline_mode_candidates() -> tuple[int, ...]:
    """Named routes and supported adaptivity variants, without output flags.

    Used to generate the native query table from the same vocabulary as requests.
    Reflective conditions are options, not mode bits; high resolution is negotiated
    after set_mode and cannot be checked at the initial description boundary.
    """
    return tuple(sorted({*MODE_ROUTES.values(), *(
        MODE_ROUTES[route] | MODE_FLAGS["emis_nonadaptive"]
        for route in _ADAPTIVE_ROUTES
    )}))


def baseline_mode_words(primary: int) -> tuple[int, ...]:
    """Exact baseline query words for the advertised candidates, not acceptance.

    As in session.c, colorimeter is always requested. A spectral instrument is
    queried both with its default spectrum and with XYZ alone. Native check_mode
    supplies the answer; this function only determines the finite query set.
    """
    _check_word(primary)
    outputs = (0, MODE_FLAGS["spectral"]) if primary & MODE_FLAGS["spectral"] else (0,)
    return tuple(sorted(
        base | MODE_FLAGS["colorimeter"] | output
        for base in baseline_mode_candidates() if base & primary == base
        for output in outputs
    ))


# The four acquisition strategies chartread ranks (chartread.c:752-790). Any other
# action is a single placement and a single reading, which is the spot workflow
# even where the geometry differs: telephoto aims rather than touches, ambient
# uses a diffuser, ambient-flash waits for a trigger. The workflow shape is what
# this names, not the optics.
WORKFLOW_KINDS = ("chart", "xy", "strip", "spot")


def workflow_kind(mode: str) -> str:
    """The acquisition workflow a published mode name requires.

    Derived from the route's action axis, so a new instrument's modes classify
    themselves. This replaces per-device membership tuples such as
    ``mode in STRIP_MODES``, which cannot classify a mode the device module does
    not already list -- the reason a third instrument previously needed its own
    frontend to answer a question the mode name already answers.
    """
    route, _, _ = parse_mode_name(mode)
    action = route_action(route)
    return action if action in ACTION_RANK else "spot"


def illumination_of(mode: str) -> str:
    """``reflection`` / ``s_reflection`` / ``transmission`` / ``emission`` for a mode.

    Replaces per-device membership tuples such as ``mode in TRANSMISSION_MODES``.
    Saved reflection is reported as itself, never folded into reflection: it is a
    separate illumination bit (``inst.h:202``) whose selection also depends on
    ``inst_stat_saved_readings``.
    """
    route, _, _ = parse_mode_name(mode)
    return route_illumination(route)



# --- reflective measurement conditions ------------------------------------------
#
# M0-M3 are the ISO 13655 reflective measurement conditions, and Argyll expresses
# each as a filter selection (`inst.h:564-570`): M0 unfiltered, M1 a D50
# illuminant filter, M2 a U.V. cut filter, M3 a polarising filter. The published
# mode names already carry the condition -- `parse_mode_name` returns it -- so a
# caller that names `reflective_spot_m2` has already said which filter to fit.
#
# D65 and Custom have no ISO condition name and no mode-name qualifier; they are
# listed because `inst_opt_set_filter` accepts them and an instrument may
# advertise them (`ss.c:437-441` offers both).
FILTERS = MappingProxyType({
    "unknown": 0xFFFF,   # inst_opt_filter_unknown: not requested
    "m0": 0x0000,        # inst_opt_filter_none
    "m1": 0x0001,        # inst_opt_filter_D50
    "d65": 0x0002,       # inst_opt_filter_D65
    "m2": 0x0004,        # inst_opt_filter_UVCut
    "m3": 0x0008,        # inst_opt_filter_pol
    "custom": 0x0010,    # inst_opt_filter_Custom
})

#: Map inst_opt_filter (inst.h:564-570) to the distinct inst_meas_cond values
#: in the active xicc/xspect.h:57-63 enum. Unknown means no requested condition.
MEASUREMENT_CONDITIONS = MappingProxyType({
    FILTERS["m0"]: 0,
    FILTERS["m1"]: 1,
    FILTERS["d65"]: 2,
    FILTERS["m2"]: 3,
    FILTERS["m3"]: 4,
    FILTERS["custom"]: 5,
})


def filter_for(mode: str) -> int:
    """The ``inst_opt_filter_*`` value a published mode name asks for.

    ``FILTERS["unknown"]`` when the name states no condition, which is upstream's
    own "leave the filter alone" value (``inst.h:564``). This is the whole reason
    a mode name carries ``_m2``: the condition is part of what was requested, and
    a reading taken through the wrong filter is a different measurement.
    """
    _, _, condition = parse_mode_name(mode)
    return FILTERS["unknown"] if condition is None else FILTERS[condition]


#: X-Rite's reflective calibration standards (`insttypes.h:141-146`). Which one an
#: instrument was calibrated against changes the numbers it reports, so it travels
#: with the request rather than being assumed. `none` means "leave it alone".
XCALIBRATION_STANDARDS = MappingProxyType({
    "none": -2, "native": -1, "xrdi": 0, "gmdi": 1, "xrga": 2,
})


# --- instrument identity -------------------------------------------------------
#
# Every accepted spelling `inst_enum()` matches (`insttypes.c`), keyed by the
# `instType` symbol and ordered with `inst_name()`'s own spelling first where
# upstream accepts it. This is the *whole* instrument table: a session helper
# names the instrument with one of these strings and Argyll's own `inst_enum()`
# resolves it, so supporting a further instrument costs a string, not a branch.
#
# Names rather than enum ordinals, because `insttypes.h:29-69` assigns no explicit
# values -- an ordinal is a position in a list upstream reorders, a name is not.
#
# One upstream inconsistency is transcribed rather than corrected: `inst_name()`
# returns "Datacolor Spyder 2024" for `instSpyder2024`, but `inst_enum()` matches
# only "GretagMacbeth Spyder 2024" (a copy of the Huey branch below it). The
# accepted spelling is what a helper must send, so that is what is recorded, and
# `test_argyll_capability.py` fails if upstream ever agrees with itself.
INSTRUMENTS = MappingProxyType({
    "instDTP22": ("X-Rite DTP22", "Xrite DTP22"),
    "instDTP41": ("X-Rite DTP41", "Xrite DTP41"),
    "instDTP51": ("X-Rite DTP51", "Xrite DTP51"),
    "instSpectrolino": ("GretagMacbeth Spectrolino",),
    "instSpectroScan": ("GretagMacbeth SpectroScan",),
    "instSpectroScanT": ("GretagMacbeth SpectroScanT",),
    # Upstream matches this name but CANNOT construct it: inst.c comments the
    # constructor out -- `/* NYI  else if (itype == instSpectrocam) p = (inst
    # *)new_spc(icom, itype); */`. Kept because this table transcribes inst_enum
    # and nothing else; a caller naming it gets a clean attach failure, and
    # test_argyll_capability.py records the exception so it is not mistaken for
    # a gap on our side.
    "instSpectrocam": ("Spectrocam",),
    "instSpecbos1201": ("JETI specbos 1201",),
    "instSpecbos2501": ("JETI specbos 2501",),
    "instSpecbos": ("JETI specbos",),
    "instSpectraval": ("JETI spectraval",),
    "instKleinK10": ("Klein K-10",),
    "instSMCube": ("SwatchMate Cube",),
    "instDTP20": ("X-Rite DTP20", "Xrite DTP20"),
    "instDTP92": ("X-Rite DTP92", "Xrite DTP92"),
    "instDTP94": ("X-Rite DTP94", "Xrite DTP94"),
    "instI1Disp1": ("GretagMacbeth i1 Display 1",),
    "instI1Disp2": ("GretagMacbeth i1 Display 2", "GretagMacbeth i1 Display",
                    "Xrite i1 Display", "X-Rite i1 Display"),
    "instI1Disp3": ("X-Rite i1 DisplayPro, ColorMunki Display", "Xrite i1 DisplayPro",
                    "X-Rite i1 DisplayPro", "ColorMunki Display",
                    "Xrite i1 DisplayPro, ColorMunki Display"),
    "instI1Monitor": ("GretagMacbeth i1 Monitor",),
    "instI1Pro": ("GretagMacbeth i1 Pro", "Xrite i1 Pro", "X-Rite i1 Pro"),
    "instI1Pro2": ("X-Rite i1 Pro 2", "Xrite i1 Pro 2"),
    "instI1Pro3": ("X-Rite i1 Pro 3", "Xrite i1 Pro 3"),
    "instColorMunki": ("X-Rite ColorMunki", "XRite ColorMunki"),
    "instHCFR": ("Colorimtre HCFR",),
    "instSpyder1": ("ColorVision Spyder1",),
    "instSpyder2": ("ColorVision Spyder2",),
    "instSpyder3": ("Datacolor Spyder3",),
    "instSpyder4": ("Datacolor Spyder4",),
    "instSpyder5": ("Datacolor Spyder5",),
    "instSpyderX": ("Datacolor SpyderX",),
    "instSpyderX2": ("Datacolor SpyderX2",),
    "instSpyder2024": ("GretagMacbeth Spyder 2024",),
    "instHuey": ("GretagMacbeth Huey",),
    "instSmile": ("ColorMunki Smile",),
    "instEX1": ("Image Engineering EX1",),
    "instColorHug": ("Hughski ColorHug",),
    "instColorHug2": ("Hughski ColorHug2",),
})

ACCEPTED_INSTRUMENT_NAMES = frozenset(
    name for names in INSTRUMENTS.values() for name in names
)

# Spellings an authenticated contribution series adds to `inst_enum()`, kept
# apart from the stock transcription above. They resolve only in a build that
# composed the series; a stock build's `inst_enum()` answers `instUnknown` for
# them, and its catalog carries no such type. `test_argyll_capability.py`
# re-derives this from the series' own `insttypes.c` patch.
CONTRIBUTED_INSTRUMENTS = MappingProxyType({
    "instCR250": ("Colorimetry Research CR-250",),
    "instCR100": ("Colorimetry Research CR-100",),
    "instCR300": ("Colorimetry Research CR-300",),
    "instPR655": ("Photo Research PR-655",),
    "instPR670": ("Photo Research PR-670",),
})


# inst_name()'s canonical spelling differs from every accepted inst_enum() alias
# for this one stock type (insttypes.c:264, :378). Keep that source fact here,
# rather than teaching discovery or a client about a specific instrument.
_DISPLAY_NAMES = MappingProxyType({"instSpyder2024": "Datacolor Spyder 2024"})


def _instrument_identity(instrument: str) -> tuple[str, tuple[str, ...]]:
    for table in (INSTRUMENTS, CONTRIBUTED_INSTRUMENTS):
        if instrument in table:
            return instrument, table[instrument]
        for symbol, names in table.items():
            if (instrument in names or symbol in _DISPLAY_NAMES
                    and instrument == _DISPLAY_NAMES[symbol]):
                return symbol, names
    raise KeyError(f"Not an ArgyllCMS instrument name: {instrument!r}")


def instrument_binding_name(instrument: str) -> str:
    """Resolve a symbol, display name or alias to a spelling inst_enum accepts.

    Canonical names are suitable for presentation, but upstream's inst_name and
    inst_enum do not always round-trip. Only this resolved alias is sent to the
    native worker. A contributed name still requires a build carrying its driver.
    """
    return _instrument_identity(instrument)[1][0]


def instrument_display_name(instrument: str) -> str:
    """The canonical inst_name label, independently of the native binding alias."""
    symbol, names = _instrument_identity(instrument)
    return _DISPLAY_NAMES.get(symbol, names[0])


# Display technology strings, transcribed from `disptech_info_array[]`
# (`spectro/disptechs.c:63`). The key is the entry's `strid` -- the first string
# of each row (`disptechs.h:115-118` gives the field order: dtech, strid, desc)
# -- because `strid` is exactly what a `.ccmx` or `.ccss` file carries in its
# TECHNOLOGY keyword, and what `disptech_get_strid()` (`disptechs.c:438`) matches.
#
# The value is the `disptech` enumeration, which IS an explicit hex constant
# (`disptechs.h:46-...`), unlike `instType`; it is passed to `col_cor_mat()` and
# `col_cal_spec_set()` (`inst.h:1102`, `:1115`) and so must be the number.
#
# `disptech_end` is a terminator and has no row. `disptech_unknown` is the
# fallback `disptech_get_strid()` returns when nothing matches, so an unrecognised
# TECHNOLOGY is not an error -- see `display_technology()`.
DISPLAY_TECHNOLOGIES = MappingProxyType({
    "None": 0x0001,
    "CRT": 0x1000,
    "Plasma": 0x2000,
    "LCD": 0x3000,
    "LCD CCFL": 0x3100,
    "LCD CCFL IPS": 0x3110,
    "LCD CCFL PVA": 0x3120,
    "LCD CCFL TFT": 0x3130,
    "LCD CCFL Wide Gamut": 0x3200,
    "LCD CCFL Wide Gamut IPS": 0x3210,
    "LCD CCFL Wide Gamut PVA": 0x3220,
    "LCD CCFL Wide Gamut TFT": 0x3230,
    "LCD White LED": 0x3300,
    "LCD White LED IPS": 0x3310,
    "LCD White LED PVA": 0x3320,
    "LCD White LED TFT": 0x3330,
    "LCD RGB LED": 0x3400,
    "LCD RGB LED IPS": 0x3410,
    "LCD RGB LED PVA": 0x3420,
    "LCD RGB LED TFT": 0x3430,
    "LCD RG Phosphor": 0x3500,
    "LCD RG Phosphor IPS": 0x3510,
    "LCD RG Phosphor PVA": 0x3520,
    "LCD RG Phosphor TFT": 0x3530,
    "LCD PFS Phosphor": 0x3600,
    "LCD PFS Phosphor IPS": 0x3610,
    "LCD PFS Phosphor PVA": 0x3620,
    "LCD PFS Phosphor TFT": 0x3630,
    "LCD GB-R Phosphor": 0x3700,
    "LCD GB-R Phosphor IPS": 0x3710,
    "LCD GB-R Phosphor PVA": 0x3720,
    "LCD GB-R Phosphor TFT": 0x3730,
    "LED OLED": 0x4000,
    "LED AMOLED": 0x4010,
    "LED WOLED": 0x4020,
    "Projector": 0x5000,
    "Projector RGB Filter Wheel": 0x5010,
    "Projector RGBW Filter Wheel": 0x5020,
    "Projector RGBCMY Filter Wheel": 0x5030,
    "Unknown": 0x0000,
})

DISPTECH_UNKNOWN = 0x0000  # disptechs.h:48


def display_technology(technology: str | None) -> int:
    """The ``disptech`` enumeration for a TECHNOLOGY string.

    Returns ``DISPTECH_UNKNOWN`` for an unrecognised or absent string rather than
    raising, because that is what upstream does: ``disptech_get_strid()`` is
    documented "Return the disptech_unknown entry if not matched"
    (``disptechs.c:437``). A correction file naming a technology this pinned
    release does not know is still a usable correction file.

    The ``" VPA"`` rewrite is upstream's, not an improvement on it:
    ``disptech_get_strid()`` opens with a "Backwards compatibility hack - replace
    ``" VPA"`` with ``" PVA"``" before it looks anything up, so older files
    spelling the panel type that way still resolve. It edits the first occurrence
    only (it is a single ``strstr``), which is why ``count=1`` here.
    """
    if technology is None:
        return DISPTECH_UNKNOWN
    return DISPLAY_TECHNOLOGIES.get(technology.replace(" VPA", " PVA", 1), DISPTECH_UNKNOWN)


# What a reading's numbers MEAN, keyed on the measurement type the driver
# reported with it (`inst_meas_type`, `xicc/xspect.h:40-50`), whose comments give
# the units directly. The older copy in inst.h is disabled by #ifdef NEVER:
#
#     inst_mrt_emission       = 1,  /* cd/m^2,    mW/(m^2.sr.nm) */
#     inst_mrt_ambient        = 2,  /* Lux        mW/(m^2.nm) */
#     inst_mrt_emission_flash = 3,  /* cd.s/m^2,  mW.s/(m^2.sr.nm) */
#     inst_mrt_ambient_flash  = 4,  /* Lux.s      mW.s/(m^2.nm) */
#     inst_mrt_reflective     = 5,  /* %,         %/nm */
#     inst_mrt_transmissive   = 6,  /* %,         %/nm */
#     inst_mrt_sensitivity    = 7,  /* %,         %/nm (i.e. CMF) */
#     inst_mrt_frequency      = 8   /* Hz */
#
# Keyed on the REPORTED type rather than on the requested mode or the device,
# for two reasons. It is what the instrument says it actually measured, so a
# driver that answers a request with a different measurement cannot be labelled
# as the request; and it is one table for 38 instruments instead of a contract
# per device (the retired per-device path derived the same labels from its own
# mode list, correct for one family and unavailable to the rest).
#
# The D50 in the reflective and transmissive rows is upstream's own default, and
# is a computation rather than an illumination that was present: `inst.h:92-94`
# -- "reflectance is measured under the given conditions, but by default XYZ is
# computed as if the spectral reflectance was illuminated by a non-fluorescent
# stimulating D50 illuminant". The measurement condition that WAS present is
# reported separately as `mcond` (`inst_meas_cond`, `xicc/xspect.h:57-64`).
MEASUREMENT_TYPES = MappingProxyType({
    1: "emission",
    2: "ambient",
    3: "emission_flash",
    4: "ambient_flash",
    5: "reflective",
    6: "transmissive",
    7: "sensitivity",
    8: "frequency",
})

_QUANTITIES = MappingProxyType({
    1: MappingProxyType(dict(
        xyz_unit="cd/m^2", scale="absolute; Y is luminance",
        illuminant="none (emissive)", quantity="spectral_radiance",
        spectrum_unit="mW/(m^2 sr nm)")),
    2: MappingProxyType(dict(
        xyz_unit="lux", scale="absolute; Y is illuminance",
        illuminant="none (ambient)", quantity="spectral_irradiance",
        spectrum_unit="mW/(m^2 nm)")),
    3: MappingProxyType(dict(
        xyz_unit="cd s/m^2", scale="absolute; Y is time-integrated luminance",
        illuminant="none (emissive flash)", quantity="time_integrated_spectral_radiance",
        spectrum_unit="mW s/(m^2 sr nm)")),
    4: MappingProxyType(dict(
        xyz_unit="lux s", scale="absolute; Y is luminous exposure",
        illuminant="none (ambient flash)", quantity="spectral_radiant_exposure",
        spectrum_unit="mW s/(m^2 nm)")),
    5: MappingProxyType(dict(
        xyz_unit="1", scale="relative; perfect-diffuser Y=100",
        illuminant="D50", quantity="reflectance", spectrum_unit="%")),
    6: MappingProxyType(dict(
        xyz_unit="1", scale="relative; perfect-transmission Y=100",
        illuminant="D50", quantity="transmittance", spectrum_unit="%")),
})


def quantity_contract(mtype: int) -> dict[str, str]:
    """What a reading of this measurement type means, in units and scale.

    ``mtype`` is the driver's own ``inst_meas_type`` as published with the patch,
    not a mode name. Nothing here converts: these are labels for quantities the
    instrument already produced.

    Raises ``KeyError`` for ``inst_mrt_none`` (0), ``inst_mrt_sensitivity`` (7),
    ``inst_mrt_frequency`` (8, a refresh rate rather than a colour reading), and
    for any value upstream does not define. These types have no colour-reading
    contract and must not be published with borrowed units.
    """
    if mtype not in _QUANTITIES:
        raise KeyError(
            f"No quantity contract for inst_meas_type {mtype!r}"
            f"{' (' + MEASUREMENT_TYPES[mtype] + ')' if mtype in MEASUREMENT_TYPES else ''}"
        )
    return dict(_QUANTITIES[mtype])


#: Which ``inst_meas_type`` each (illumination, action) pair is EXPECTED to
#: produce. Derived from the route's own illumination, which is what upstream's
#: drivers key on when they set ``mtype`` on a reading.
#:
#: Expected, never authoritative: the driver stamps each reading with its own
#: ``inst_meas_type`` and ``observation_from_patch`` labels the units from THAT.
#: This exists so a user interface can say what a mode will return before anything
#: has been measured -- axis captions, unit labels, a plot's scale -- without
#: having to take a reading first.
#:
#: ``inst_mrt_emission_flash`` (3) is absent on purpose: no name in this grammar
#: routes to it. Upstream reaches it through the emissive-flash path, which this
#: project does not name as a mode.
_EXPECTED_MEASUREMENT_TYPE = MappingProxyType({
    ("emission", "spot"): 1,
    ("emission", "strip"): 1,
    ("emission", "tele"): 1,
    ("emission", "ambient"): 2,
    ("emission", "ambient_flash"): 4,
    ("reflection", "spot"): 5,
    ("reflection", "strip"): 5,
    ("reflection", "xy"): 5,
    ("reflection", "chart"): 5,
    ("s_reflection", "spot"): 5,
    ("s_reflection", "strip"): 5,
    ("s_reflection", "xy"): 5,
    ("s_reflection", "chart"): 5,
    ("transmission", "spot"): 6,
    ("transmission", "ambient"): 6,
    ("transmission", "strip"): 6,
    ("transmission", "xy"): 6,
    ("transmission", "chart"): 6,
})


def expected_measurement_type(mode: str) -> int:
    """The ``inst_meas_type`` a published mode name is expected to return.

    Raises ``ValueError`` for a name outside the grammar and ``KeyError`` for a
    route this project cannot predict a type for. Neither is a fallback: a caller
    that cannot be told what a mode returns must say so rather than guess.
    """
    parse_mode_name(mode)
    parts = mode.split("_")
    return _EXPECTED_MEASUREMENT_TYPE[_NAME_ROUTES[(parts[0], parts[1])]]


def _parses(candidate: str) -> bool:
    try:
        parse_mode_name(candidate)
    except ValueError:
        return False
    return True


# Caller-facing mode names, built once from the name grammar rather than listed.
# `decode_modes` answers in Argyll's ROUTE keys (`emis_spot`); a caller names a
# mode (`emissive_spot_nonadaptive`), and one route carries several names.
_ALL_MODE_NAMES = tuple(sorted({
    candidate
    for illumination, action in _NAME_ROUTES
    for candidate in (
        f"{illumination}_{action}",
        *(f"{illumination}_{action}_{modifier}" for modifier in _NAME_MODIFIERS),
    )
    if _parses(candidate)
}))

#: Which `inst_opt_filter` each reflective condition needs, as a capability-3 name.
_FILTER_CAPABILITY = MappingProxyType({
    "m0": "filter_none", "m1": "filter_D50", "m2": "filter_UVCut",
    "m3": "filter_pol", "d65": "filter_D65", "custom": "filter_Custom",
})


def available_conditions(tertiary: int) -> tuple[str, ...]:
    """Reflective measurement conditions an instrument's tertiary word allows.

    Every explicit filter needs its corresponding ``inst3_filter_*`` bit,
    including M0 (``filter_none``) and Custom (``filter_Custom``). A fixed UV-cut
    instrument cannot remove its filter. Only ``unknown``, meaning "leave the
    filter wherever it is", is always present.
    """
    _check_word(tertiary)
    present = set(decode_filters(tertiary))
    return tuple(
        name for name in FILTERS
        if name == "unknown" or _FILTER_CAPABILITY.get(name) in present
    )


def supported_modes(primary: int, tertiary: int = 0) -> tuple[str, ...]:
    """Caller-facing mode names an instrument's capability words advertise.

    The frontend reads offered modes from the instrument instead of maintaining
    per-device mode lists. Argyll applications use the same vtable and capability
    queries, with device-specific exceptions such as DTP51 chart alignment.
    Shared acquisition retains bounded, source-backed policies for genuine
    device differences.

    Three gates, all from the words themselves:

    * the mode's ROUTE must be present in the primary word (``decode_modes``);
    * a name carrying a mode modifier -- ``nonadaptive`` -- needs that flag in the
      primary word too (``decode_mode_flags``), since upstream advertises it
      separately from the route;
    * a name carrying any reflective condition needs the matching filter in the
      tertiary word (``decode_filters``), including M0's ``filter_none``. An
      unqualified reflective name leaves the existing filter unchanged.

    ADVISORY, like everything derived from a capability word: ``inst.h:305-306``
    says the macro behind it is not definitive and ``check_mode()`` is. A route
    returned here is one the instrument claims; the ladder still confirms it.
    """
    _check_word(primary)
    _check_word(tertiary)
    routes = set(decode_modes(primary))
    flags = set(decode_mode_flags(primary))
    filters = set(decode_filters(tertiary))
    offered = []
    for name in _ALL_MODE_NAMES:
        route, modifiers, condition = parse_mode_name(name)
        if route not in routes:
            continue
        if any(modifier not in flags for modifier in modifiers):
            continue
        needed = _FILTER_CAPABILITY.get(condition or "")
        if needed is not None and needed not in filters:
            continue
        offered.append(name)
    return tuple(offered)


# ---------------------------------------------------------------------------
# Calibration vocabulary
# ---------------------------------------------------------------------------
#
# Three enumerations travel on every calibration prompt, and until this section
# existed all three crossed the wire as bare integers. A client could show
# "condition 32" and nothing else.
#
# The i1Pro family raises 7 of the 19 conditions (inst_calc_man_ref_white,
# man_em_dark, man_am_dark, man_trans_white, man_trans_dark, emis_white and
# message, per i1pro_imp.c / i1pro3_imp.c). The other 12 belong to instruments
# this project had never named: the DTP41/DTP51 `uop_*` group, the DTP22 click,
# the light trap, smcube's gloss black, ColorMunki's sensor dial, the colorimeter
# 80% patch, the DTP92 grey ladder, and SpectroScan's filter change. Publishing
# an integer relocated that knowledge into every client rather than removing it.

#: ``inst_cal_type`` -- inst.h:586-621. A BITFIELD of specific calibrations, with
#: an enumeration of three "pseudo types" sharing its low nibble.
CALIBRATION_TYPES = MappingProxyType({
    "wavelength": 0x00000010,
    "ref_white": 0x00000020,
    "ref_dark": 0x00000040,
    "ref_dark_gl": 0x00000080,
    "emis_offset": 0x00000100,
    "emis_ratio": 0x00000200,
    "em_dark": 0x00000400,
    "trans_white": 0x00000800,
    "trans_vwhite": 0x00001000,
    "trans_dark": 0x00002000,
    # inst.h:613-617 -- "Calibrations that might be deferred until measurement".
    "emis_int_time": 0x00100000,
    "ref_freq": 0x00200000,
})

#: The three values that share the low nibble (inst.h:591-597). They are
#: REQUESTS, not a report: a caller passes one to ``calibrate()`` to mean "do all
#: required", and ``get_n_a_cals`` answers in specific bits instead.
CALIBRATION_REQUESTS = MappingProxyType({"all": 0x1, "needed": 0x2, "available": 0x3})

CALIBRATION_TYPE_MASK = 0x00F0FFF0    # inst_calt_all_mask, inst.h:619
CALIBRATION_DEFERRABLE_MASK = 0x00F00000  # inst_calt_dfrble_mask, inst.h:617
CALIBRATION_REQUEST_MASK = 0x0000000F
CALIBRATION_ALL_POSSIBLE_FLAG = 0x80000000  # inst_calt_ap_flag, inst.h:621

#: ``inst_cal_cond`` -- inst.h:636-676. **Not a bitfield.** The values are packed
#: into nibble groups (``uop`` 0x0000000F, ``man`` 0x000000F0, ``emis``
#: 0x00000F00) and upstream reads them with ``switch (calc & inst_calc_cond_mask)``
#: (instappsup.c:240), so exactly one condition is in force at a time. Decoding
#: these by bit would report ``man_ref_white`` and ``man_ref_whitek`` together for
#: the value 0x30, which is neither of them -- it is ``man_ref_dark``.
CALIBRATION_CONDITIONS = MappingProxyType({
    "none": 0x00000000,
    "uop_ref_white": 0x00000001,
    "uop_trans_white": 0x00000002,
    "uop_trans_dark": 0x00000003,
    "man_ref_white": 0x00000010,
    "man_ref_whitek": 0x00000020,
    "man_ref_dark": 0x00000030,
    "man_dark_gloss": 0x00000040,
    "man_em_dark": 0x00000050,
    "man_am_dark": 0x00000060,
    "man_cal_smode": 0x00000070,
    "man_trans_white": 0x00000080,
    "man_trans_dark": 0x00000090,
    "emis_white": 0x00000100,
    "emis_80pc": 0x00000200,
    "emis_grey": 0x00000300,
    "emis_grey_darker": 0x00000400,
    "emis_grey_ligher": 0x00000500,
    "change_filter": 0x00010000,
    "message": 0x00020000,
    "unknown": 0xFFFFFFFF,
})

CONDITION_MASK = 0x0FFFFFFF        # inst_calc_cond_mask, inst.h:672
CONDITION_OPTIONAL_FLAG = 0x80000000   # inst_calc_optional_flag, inst.h:674

_CONDITION_BY_VALUE = MappingProxyType({
    value & CONDITION_MASK: name for name, value in CALIBRATION_CONDITIONS.items()
})

#: ``inst_calc_id_type`` -- inst.h:680-693. What the ``id[]`` string accompanying a
#: condition MEANS, so a client renders a reference identifier, a filter request and
#: a measurement warning differently instead of printing all three the same way.
#: Upstream never decodes this (instappsup.c:195 notes only that one "could create
#: our own message text based on value of idtype"), so the names are transcribed
#: from the header and the meanings are its comments.
CALIBRATION_REFERENCES = MappingProxyType({
    "none": 0x00000000,
    "ref_sn": 0x00000001,
    "trans_low": 0x00010000,
    "trans_wl": 0x00020000,
    "filt_unkn": 0x00100000,
    "filt_none": 0x00200000,
    "filt_D50": 0x00300000,
    "filt_D65": 0x00400000,
    "filt_UV": 0x00500000,
    "filt_pol": 0x00600000,
    "filt_cust": 0x00700000,
})

#: The two ``inst_calc_id_*`` values that are WARNINGS about the reading rather
#: than identifiers of a thing to present (inst.h:684-685). They are separate bits
#: and can therefore accompany another value, which is why they are decoded apart
#: from the filter group.
REFERENCE_WARNINGS = MappingProxyType({
    "trans_low": ("The transmission reference light is too low for accuracy",
                  0x00010000),
    "trans_wl": ("The transmission reference light is low at some wavelengths",
                 0x00020000),
})
_REFERENCE_FILTER_MASK = 0x00F00000

#: What the operator is being asked to DO, with upstream's own wording.
#:
#: ``action`` groups the 19 conditions into the 7 genuinely different screens a
#: client needs; ``instruction`` is transcribed from ``instappsup.c:240-385``;
#: ``reference_id`` names what the accompanying ``id[]`` string holds, and
#: ``acknowledged`` records the one condition upstream does NOT wait for.
_CONDITION_DETAIL = MappingProxyType({
    "uop_ref_white": {
        "action": "operate_instrument",
        "instruction": "Do a reflective white calibration on the instrument itself",
    },
    "uop_trans_white": {
        "action": "operate_instrument",
        "instruction": "Do a transmissive white calibration on the instrument itself",
    },
    "uop_trans_dark": {
        "action": "operate_instrument",
        "instruction": "Do a transmissive dark calibration on the instrument itself",
    },
    "man_ref_white": {
        "action": "present_reference",
        "instruction": "Place the instrument on its reflective white reference",
        "reference_id": "The expected white reference identifier, held by the driver, "
                        "not read from the reference placed",
    },
    "man_ref_whitek": {
        "action": "click_reference",
        "instruction": "Click the instrument down on its reflective white reference",
        "reference_id": "The expected white reference identifier, held by the driver, "
                        "not read from the reference placed",
        # instappsup.c:403-405 skips its own keypress for exactly this condition,
        # and dtp22.c:722 says why: "Calibration only works when triggered by the
        # read switch". The instrument's own click is the trigger.
        "acknowledged": False,
    },
    "man_ref_dark": {
        "action": "present_reference",
        "instruction": "Place the instrument on a light trap, or in the dark and "
                       "distant from any surface",
    },
    "man_dark_gloss": {
        "action": "present_reference",
        "instruction": "Place the instrument on its black gloss reference",
    },
    "man_em_dark": {
        "action": "present_reference",
        "instruction": "Place the cap on the instrument, or place it on a dark "
                       "surface or on the calibration reference",
    },
    "man_am_dark": {
        "action": "present_reference",
        "instruction": "Place the ambient adapter and cap on the instrument, or "
                       "place it on the calibration reference",
    },
    "man_cal_smode": {
        "action": "set_sensor_position",
        "instruction": "Set the instrument sensor to its calibration position",
    },
    "man_trans_white": {
        "action": "present_reference",
        "instruction": "Place the instrument on its transmissive white source",
    },
    "man_trans_dark": {
        "action": "present_reference",
        "instruction": "Block the transmission path with the appropriate "
                       "transmissive blocking",
    },
    "emis_white": {
        "action": "display_patch",
        "instruction": "Display a 100% white test patch for the instrument",
    },
    "emis_80pc": {
        "action": "display_patch",
        "instruction": "Display an 80% white test patch for the instrument",
    },
    "emis_grey": {
        "action": "display_patch",
        "instruction": "Display a grey test patch for the instrument",
    },
    "emis_grey_darker": {
        "action": "display_patch",
        "instruction": "Display a darker grey test patch for the instrument",
    },
    "emis_grey_ligher": {
        "action": "display_patch",
        "instruction": "Display a lighter grey test patch for the instrument",
    },
    "change_filter": {
        "action": "change_filter",
        "instruction": "Change the filter on the instrument",
        "reference_id": "The filter to fit",
    },
    "message": {
        "action": "message",
        "instruction": "The driver has a message for the operator",
        "reference_id": "The message text",
    },
})

#: The seven operator actions, so a client can enumerate the screens it must build
#: without walking all 19 conditions.
CONDITION_ACTIONS = tuple(sorted({
    detail["action"] for detail in _CONDITION_DETAIL.values()
}))


def calibration_condition(value: int) -> str:
    """Name the condition a driver is asking for.

    ``value`` is the raw ``calc`` word. The optional flag is stripped first
    (inst.h:674) and the rest matched EXACTLY, because this enumeration is not a
    bitfield -- see :data:`CALIBRATION_CONDITIONS`.

    A value upstream does not name returns ``"unknown"`` rather than raising:
    ``inst_calc_unknown`` is itself a value a driver may return (inst.h:638), and
    a newer driver inventing a condition should leave the operator with a prompt
    they can refuse, not crash the session.
    """
    _check_word(value)
    return _CONDITION_BY_VALUE.get(value & CONDITION_MASK, "unknown")


def condition_is_optional(value: int) -> bool:
    """Whether the driver marked this condition skippable (inst.h:674)."""
    _check_word(value)
    return bool(value & CONDITION_OPTIONAL_FLAG)


def condition_detail(name: str) -> dict:
    """What the operator must do, for one condition name.

    Returns ``action``, ``instruction``, ``acknowledged`` and -- where the
    condition carries one -- ``reference_id``, saying what the accompanying
    identifier string holds.

    ``acknowledged`` is False for exactly one condition, ``man_ref_whitek``: the
    instrument's own click triggers that calibration, so nothing should wait for
    an operator confirmation that will never mean anything.
    """
    if name not in _CONDITION_DETAIL:
        if name in ("none", "unknown"):
            return {"action": "message", "acknowledged": True,
                    "instruction": "The driver reported no usable calibration setup"}
        raise KeyError(f"Not a calibration condition: {name!r}")
    detail: dict[str, object] = dict(_CONDITION_DETAIL[name])
    detail.setdefault("acknowledged", True)
    return detail


def decode_calibration_types(word: int) -> tuple[str, ...]:
    """Specific calibrations named in an ``inst_cal_type`` word.

    A BITFIELD, unlike the conditions: ``get_n_a_cals`` answers with every
    calibration that is needed or available at once, so more than one name is
    normal. The low-nibble request values are not decoded here -- they are
    something a caller SENDS, not something an instrument reports.
    """
    _check_word(word)
    return tuple(name for name, bit in CALIBRATION_TYPES.items() if word & bit)


def deferrable_calibrations(word: int) -> tuple[str, ...]:
    """Those of :func:`decode_calibration_types` upstream may defer to measurement.

    inst.h:613-617. A client showing "calibration outstanding" should not treat
    these as a failure to calibrate: the driver is entitled to do them when it
    next measures.
    """
    return tuple(name for name in decode_calibration_types(word)
                 if CALIBRATION_TYPES[name] & CALIBRATION_DEFERRABLE_MASK)


def calibration_reference(idtype: int) -> dict:
    """What the ``id[]`` string accompanying a condition means.

    The filter group (0x00100000-0x00700000) is a VALUE in its own nibble field,
    while ``trans_low`` and ``trans_wl`` are independent bits that may accompany
    it, so the two are decoded differently rather than uniformly.
    """
    _check_word(idtype)
    reported = tuple(sorted(
        name for name, (_, bit) in REFERENCE_WARNINGS.items() if idtype & bit
    ))
    group = idtype & _REFERENCE_FILTER_MASK
    if not group:
        # Everything outside the filter nibble except the two warning bits, so a
        # `ref_sn` accompanied by `trans_low` still names the reference identifier.
        group = idtype & ~_REFERENCE_FILTER_MASK & ~sum(
            bit for _, bit in REFERENCE_WARNINGS.values())
    name = "none" if not group else next(
        (key for key, value in CALIBRATION_REFERENCES.items() if value == group),
        "unknown",
    )
    return {
        "reference": name,
        "warnings": reported,
        "warning_detail": [REFERENCE_WARNINGS[key][0] for key in reported],
    }


#: ``inst_config`` -- inst.h:717-726. The position a driver's ``config_enum``
#: names for an ``inst_wrong_config`` it returned (inst.h:1166-1167): where the
#: sensor, dial, diffuser or adapter must be for what it was doing. Only munki.c,
#: i1d3.c and i1pro3.c fill that slot; every other driver inherits inst.c's
#: default (:588-589, installed at :825-826), which names nothing.
INSTRUMENT_CONFIGURATIONS = MappingProxyType({
    "unknown": 0,
    "projector": 1,
    "surface": 2,
    "surface_polarized": 3,
    "emission": 4,
    "calibration": 5,
    "polarized_calibration": 6,
    "ambient": 7,
})
_CONFIGURATION_BY_VALUE = MappingProxyType({
    value: name for name, value in INSTRUMENT_CONFIGURATIONS.items()
})


def instrument_configuration(value: int) -> str:
    """Name the position a driver needs, from its ``config_enum`` answer.

    A value upstream does not define returns ``"unknown"``, as
    :func:`calibration_condition` does: the operator is still told the position
    is wrong, and a newer driver's position should not crash the session.
    """
    _check_word(value)
    return _CONFIGURATION_BY_VALUE.get(value, "unknown")
