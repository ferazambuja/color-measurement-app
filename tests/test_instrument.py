"""The device-agnostic instrument client: selection, chart geometry, workflows.

Two kinds of check. The geometry tests hold `navigation_vectors` to the model
chartread.c navigates by -- a patch at (pass i, step j) lies at
``origin + i*pass_increment + j*step_increment``, plus the odd-step offset on a
hexagonal chart -- by reconstructing a grid the test itself laid out. The session
tests drive a wholly substituted instrument through the same command line a real
one takes, and are skipped when no digest-verified worker is supplied.
"""

import dataclasses
import hashlib
import json
import math
import os
import subprocess
from pathlib import Path

import pytest
from conftest import (
    bind_cli_substitute,
    retained_build_report,
    supplied_stub,
)

from color_measurement.argyll_capability import INSTRUMENTS
from color_measurement.display_types import DisplayTypeSelection
from color_measurement.instrument import (
    MAX_PATCHES,
    WORKFLOW_FACILITIES,
    Configuration,
    Instrument,
    InstrumentError,
    Location,
    Measurement,
    navigation_vectors,
    sheet_layout,
    validate_chart_layout,
)
from color_measurement.native_protocol import ProtocolError
from color_measurement.native_receipt import native_worker_name

COLORIMETER = "X-Rite i1 DisplayPro, ColorMunki Display"


# --- selection -----------------------------------------------------------------


def test_an_instrument_is_selected_by_a_name_argyll_itself_matches():
    """The whole per-instrument surface is this string; a typo must fail here.

    Failing in Python rather than at attach matters because the alternative is a
    refusal in front of an operator holding the instrument, with nothing to say
    beyond "no device".
    """
    meter = Instrument(helper="/nonexistent", instrument="instSpectroScan",
                       expected_serial="SN-1")
    assert meter.instrument == "GretagMacbeth SpectroScan"
    # Any spelling upstream accepts resolves to the preferred one.
    assert Instrument(helper="/x", instrument="Xrite i1 Pro 3",
                      expected_serial="SN-1").instrument == "X-Rite i1 Pro 3"
    for rejected in ("i1Pro3", "SpectroScan", ""):
        with pytest.raises(KeyError):
            Instrument(helper="/x", instrument=rejected, expected_serial="SN-1")


def test_every_argyll_instrument_can_be_driven_without_new_code():
    """The cost of a further instrument is one row, and this is that claim.

    Asserting ``meter.instrument == names[0]`` alone would be tautological -- it is
    what ``instrument_binding_name`` returns. What is worth asserting is that every one of
    the 38 constructs, resolves to a spelling Argyll accepts, and reaches a helper
    command line carrying that spelling and nothing instrument-specific besides.
    The table's own correctness is held by the provenance tests in
    test_argyll_capability.py, which re-derive it from ``inst_enum``'s body.
    """
    from color_measurement.argyll_capability import ACCEPTED_INSTRUMENT_NAMES
    seen = set()
    for symbol in INSTRUMENTS:
        meter = Instrument(helper="/x", instrument=symbol, expected_serial="SN-1")
        assert meter.instrument in ACCEPTED_INSTRUMENT_NAMES
        argv = meter._argv("reflective_spot", True, (), {})
        assert argv[argv.index("--instrument") + 1] == meter.instrument
        # Nothing in the command line varies with the instrument except its name.
        seen.add(tuple(a for a in argv if a != meter.instrument))
    assert len(seen) == 1, "the command line must differ only by the instrument name"


def test_an_instrument_is_pinned_and_never_searched_for():
    # ProtocolError subclasses ValueError, so every refusal here is pinned by its
    # message: a bare type assertion would also accept a wire-protocol fault,
    # which is a different failure from a caller passing a bad argument.
    for serial in ("", "S" * 4097):
        with pytest.raises(ValueError, match="non-empty string, or None"):
            Instrument(helper="/x", instrument=COLORIMETER, expected_serial=serial)
    # No pin is legitimate: nine drivers inherit inst.c's default get_serial_no
    # returning "", so requiring one would make them unattachable.
    assert Instrument(helper="/x", instrument=COLORIMETER).expected_serial is None
    for index in (0, -1, 65):
        with pytest.raises(ValueError, match="chosen from an enumeration"):
            Instrument(helper="/x", instrument=COLORIMETER, expected_serial="SN-1",
                       path_index=index)


def test_a_mode_is_read_by_the_workflow_its_name_declares():
    """A strip is not a spot read, and the mode name already says which."""
    meter = Instrument(helper="/x", instrument=COLORIMETER, expected_serial="SN-1")
    with pytest.raises(ValueError, match="strip workflow"):
        meter.measure("reflective_strip")
    with pytest.raises(ValueError, match="spot workflow"):
        meter.measure_strip("reflective_spot", patch_count=4)
    with pytest.raises(ValueError, match="spot workflow"):
        meter.measure_sheet("reflective_spot", passes=2, steps=2)


# --- chart geometry ------------------------------------------------------------


def test_the_fiducials_a_sheet_needs_follow_its_shape():
    """chartread.c:1102-1107: fewer points when there is less to measure."""
    assert sheet_layout(passes=4, steps=5, patch_count=20) == (3, 5)
    # A MULTI-pass short sheet keeps its full first pass: upstream takes the
    # shortfall off the first pass only when paist == 1 (chartread.c:1093-1100).
    assert sheet_layout(passes=4, steps=5, patch_count=17) == (3, 5)
    assert sheet_layout(passes=2, steps=5, patch_count=6) == (3, 5)
    assert sheet_layout(passes=1, steps=5, patch_count=5) == (2, 5)
    assert sheet_layout(passes=1, steps=5, patch_count=1) == (1, 1)
    # A short final sheet takes the shortfall off the first pass (:1094-1098).
    assert sheet_layout(passes=1, steps=5, patch_count=3) == (2, 3)
    for bad in ((0, 5, 1), (4, 0, 1), (2, 2, 5), (2, 2, 0)):
        with pytest.raises(ValueError, match="at least one patch"):
            sheet_layout(*bad)


@pytest.mark.parametrize("passes,steps", [(4, 6), (2, 2), (7, 3)])
def test_a_rectangular_sheet_reconstructs_every_patch_it_was_built_from(passes, steps):
    """The vectors must navigate the grid the sighted points came from.

    A chart laid out at a known origin with known increments is sighted at its
    three fiducials; the derived vectors must place every patch back where the
    layout put it. This is the property read_xy relies on, stated directly.
    """
    origin = (12.5, -3.25)
    pass_step = (18.0, 0.75)      # one pass to the next
    patch_step = (-0.5, 9.0)      # one step along a pass

    def at(i, j):
        return Location(origin[0] + i * pass_step[0] + j * patch_step[0],
                        origin[1] + i * pass_step[1] + j * patch_step[1])

    fiducials, first_pass_steps = sheet_layout(passes, steps, passes * steps)
    assert fiducials == 3
    points = (at(0, 0), at(0, first_pass_steps - 1), at(passes - 1, 0))
    ox, oy, ax, ay, aax, aay, px, py = navigation_vectors(
        points, passes=passes, first_pass_steps=first_pass_steps,
    )
    assert (aax, aay) == (0.0, 0.0), "a rectangular chart has no odd-step offset"
    assert (ox, oy) == pytest.approx(origin)
    assert (ax, ay) == pytest.approx(pass_step)
    assert (px, py) == pytest.approx(patch_step)
    for i in range(passes):
        for j in range(steps):
            expected = at(i, j)
            assert (ox + i * ax + j * px, oy + i * ay + j * py) == pytest.approx(
                (expected.x, expected.y)
            ), f"patch ({i},{j}) misplaced"


@pytest.mark.parametrize("first_pass_steps", [4, 5, 6, 7])
@pytest.mark.parametrize("fiducials", [2, 3])
def test_a_hexagonal_sheet_reconstructs_the_grid_it_was_built_from(first_pass_steps, fiducials):
    """chartread.c:1257-1289, both parities and both fiducial counts.

    The chart is a regular hexagonal lattice, so its odd-step offset is both half a
    pass increment AND the step increment rotated 90 degrees and scaled by
    ``kk = sqrt(1/3)``. Upstream uses the first relation when three points were
    sighted (``:1266-1267``) and the second when only two were (``:1271-1272``),
    because with two points the pass direction was never measured. Building the
    grid so both hold is what lets one test hold both branches to full
    reconstruction.

    Parametrising ``first_pass_steps`` is the point: upstream branches on
    ``((fspipa-1) & 1) == 0`` -- whether the second sighted point lands on an even
    or an odd step -- and a single parity exercises only one side of it. The
    non-aligned case is a least-squares inversion (``:1283-1284``), so it recovers
    the step only if the algebra is right.
    """
    passes = 3
    origin = (5.0, -2.0)
    patch_step = (-0.75, 8.0)
    kk = math.sqrt(1 / 3)
    offset = (kk * patch_step[1], -kk * patch_step[0])   # rot90, scaled
    pass_step = (2 * offset[0], 2 * offset[1])           # odd steps sit half a pass over

    def at(i, j):
        odd = j % 2
        return Location(origin[0] + i * pass_step[0] + j * patch_step[0] + odd * offset[0],
                        origin[1] + i * pass_step[1] + j * patch_step[1] + odd * offset[1])

    points = [at(0, 0), at(0, first_pass_steps - 1)]
    if fiducials == 3:
        points.append(at(passes - 1, 0))
    ox, oy, ax, ay, aax, aay, px, py = navigation_vectors(
        tuple(points), passes=passes, first_pass_steps=first_pass_steps, hexagonal=True,
    )
    assert (ox, oy) == pytest.approx(origin)
    assert (px, py) == pytest.approx(patch_step), "the step increment must be recovered"
    assert (aax, aay) == pytest.approx(offset), "the odd-step offset must be recovered"
    if fiducials == 3:
        assert (ax, ay) == pytest.approx(pass_step), "the pass increment must be recovered"
        for i in range(passes):
            for j in range(first_pass_steps):
                expected = at(i, j)
                odd = j % 2
                assert (ox + i * ax + j * px + odd * aax,
                        oy + i * ay + j * py + odd * aay) == pytest.approx(
                    (expected.x, expected.y)
                ), f"patch ({i},{j}) misplaced"


def test_the_hexagonal_offset_is_a_rotation_of_the_step_increment():
    """chartread.c:1271-1272: ``aax, aay = kk * py, -kk * px``.

    With two sighted points the pass direction was never measured, so the offset is
    recovered from the step vector alone -- rotated 90 degrees and scaled by
    ``kk = sqrt(1/3)``. A sign transposition turns the rotation the wrong way, and
    a step vector with a non-zero x component is what makes that visible.
    """
    points = (Location(0.0, 0.0), Location(12.0, 16.0))    # four intervals, both axes
    _, _, _, _, aax, aay, px, py = navigation_vectors(
        points, passes=1, first_pass_steps=5, hexagonal=True,
    )
    kk = math.sqrt(1 / 3)
    assert (px, py) == pytest.approx((3.0, 4.0))
    # The exact rotation, not merely a perpendicular of the right length: the
    # opposite sign is also perpendicular and the same length.
    assert (aax, aay) == pytest.approx((kk * py, -kk * px))
    assert aax > 0 and aay < 0, "the rotation direction is part of the geometry"


def test_navigation_refuses_what_it_cannot_derive():
    """`navigation_vectors` is public, so it validates its own geometry.

    Each refusal below would otherwise be a bare ZeroDivisionError or a silently
    wrong vector: a point count it has no branch for, a pass or step interval of
    zero to divide by, or a non-finite sighting.
    """
    with pytest.raises(ValueError, match="one, two or three"):
        navigation_vectors((), passes=2, first_pass_steps=2)
    with pytest.raises(ValueError, match="one, two or three"):
        navigation_vectors((Location(0, 0),) * 4, passes=2, first_pass_steps=2)
    with pytest.raises(ValueError, match="finite"):
        navigation_vectors((Location(math.nan, 0.0),), passes=1, first_pass_steps=1)
    for passes, steps in ((0, 1), (1, 0)):
        with pytest.raises(ValueError, match="at least one pass"):
            navigation_vectors((Location(0, 0),), passes=passes, first_pass_steps=steps)
    # Two points measure a step interval; three also measure a pass interval. Each
    # needs something to divide by, and `passes - 1` is zero on a single-pass sheet.
    with pytest.raises(ValueError, match="at least two steps"):
        navigation_vectors((Location(0, 0), Location(1, 1)), passes=2, first_pass_steps=1)
    with pytest.raises(ValueError, match="at least two passes"):
        navigation_vectors((Location(0, 0),) * 3, passes=1, first_pass_steps=3)


def test_a_single_patch_sheet_keeps_upstreams_own_defaults():
    """One sighted point fixes the origin and nothing else (chartread.c:1086-1088)."""
    assert navigation_vectors((Location(4.0, 5.0),), passes=1, first_pass_steps=1) == (
        4.0, 5.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0
    )


# --- sessions over a wholly substituted instrument -----------------------------


def _meter(worker, instrument, serial, **kw):
    return Instrument(helper=worker, instrument=instrument, expected_serial=serial,
                      timeout=30.0, **kw,
               expected_helper_sha256=hashlib.sha256(
                   Path(worker).read_bytes()).hexdigest())


def test_a_colorimeter_reads_over_the_generic_helper():
    """No spectrum at all, and its absence stays an absence."""
    worker = supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")
    meter = _meter(worker, COLORIMETER, "CM-COLORIMETER-0001",
                   environment=dict(os.environ, CM_COLORIMETER_FIXTURE="success"))
    result = meter.measure("emissive_spot", spectral=False)
    assert result.provenance == "simulated"
    assert result.serial == "CM-COLORIMETER-0001"
    assert result.confirmed_routes == ("emis_spot",)
    assert result.workflow == "spot" and result.spectral is False
    assert len(result.patches) == 1
    assert result.patches[0].xyz == (21.5, 24.0, 9.75)
    assert result.patches[0].spectrum is None


def test_a_caller_needing_a_spectrum_is_refused_by_a_colorimeter():
    worker = supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")
    meter = _meter(worker, COLORIMETER, "CM-COLORIMETER-0001",
                   environment=dict(os.environ, CM_COLORIMETER_FIXTURE="success"))
    with pytest.raises(InstrumentError) as failure:
        meter.measure("emissive_spot", spectral=True)
    assert failure.value.stage == "spectral_unavailable"


def test_the_pinned_serial_travels_with_the_selection():
    """A substituted instrument takes the real command line, serial included."""
    worker = supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")
    meter = _meter(worker, COLORIMETER, "CM-NOT-THIS-ONE",
                   environment=dict(os.environ, CM_COLORIMETER_FIXTURE="success"))
    with pytest.raises(InstrumentError) as failure:
        meter.measure("emissive_spot", spectral=False)
    assert failure.value.stage == "serial_mismatch"


def test_a_sheet_is_read_from_fiducials_the_operator_sighted():
    """The vectors the helper navigates by are derived here, from sighted points.

    This test used to pass ``geometry=`` and so took the OTHER path, skipping the
    sighting its own name claimed to cover. The stub required vectors 1..8, which
    the sighted points cannot derive, so nothing could have noticed. It now sights:
    the stub reports (10,20), (11,22), (12,24), and `navigation_vectors` turns
    those into (10, 20, 2, 4, 0, 0, 0.5, 1) -- which is what CM_CHART_XY_VECTORS
    makes the stub demand, so a wrong derivation aborts in read_xy.
    """
    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    seen = []
    meter = _meter(worker, "GretagMacbeth SpectroScan", "CM-CHART-0001",
                   environment=dict(os.environ, CM_CHART_PERSONA="spectroscan",
                                    CM_CHART_FIXTURE="success",
                                    CM_CHART_XY_VECTORS="10,20,2,4,0,0,0.5,1"))
    result = meter.measure_sheet(
        "reflective_xy", passes=2, steps=3, patch_count=6,
        acknowledge=lambda prompt: seen.append(prompt["instruction"]) or "acknowledge",
    )
    # One sheet placement, then one sighting per fiducial sheet_layout asks for.
    assert seen == ["xy_sheet", "xy_locate", "xy_locate", "xy_locate"]
    assert [(p.x, p.y) for p in result.locations] == [(10.0, 20.0), (11.0, 22.0),
                                                      (12.0, 24.0)]
    assert result.confirmed_routes == ("ref_xy",)
    assert result.workflow == "xy" and result.spectral is True
    assert [p.index for p in result.patches] == [0, 1, 2, 3, 4, 5]
    assert len(result.patches[0].spectrum.samples) == 36
    assert result.patches[0].spectrum.wavelengths_nm[0] == 380.0


def test_a_sheet_can_also_be_read_from_vectors_the_caller_already_knows():
    """`geometry=` skips the sighting entirely, for a sheet whose navigation is
    already established. Nothing is sighted, so the ladder asks for no fiducial."""
    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    seen = []
    meter = _meter(worker, "GretagMacbeth SpectroScan", "CM-CHART-0001",
                   environment=dict(os.environ, CM_CHART_PERSONA="spectroscan",
                                    CM_CHART_FIXTURE="success"))
    result = meter.measure_sheet(
        "reflective_xy", passes=2, steps=3, patch_count=6,
        geometry=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0),
        acknowledge=lambda prompt: seen.append(prompt["instruction"]) or "acknowledge",
    )
    assert seen == ["xy_sheet"] and result.locations == ()
    assert [p.index for p in result.patches] == [0, 1, 2, 3, 4, 5]


def test_a_strip_pass_is_published_whole_or_not_at_all():
    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    environment = dict(os.environ, CM_CHART_PERSONA="dtp41", CM_CHART_FIXTURE="success")
    meter = _meter(worker, "X-Rite DTP41", "CM-CHART-0001", environment=environment)
    strip = dict(patch_count=4, pass_name="B", guide=15, patch_width_mm=6.0,
                 gap_width_mm=1.5, trailer_width_mm=3.0)
    result = meter.measure_strip("reflective_strip", **strip)
    assert [p.index for p in result.patches] == [0, 1, 2, 3]
    assert all(p.spectrum is not None for p in result.patches)

    broken = _meter(worker, "X-Rite DTP41", "CM-CHART-0001",
                    environment=dict(environment, CM_CHART_FIXTURE="strip_malformed_last"))
    with pytest.raises(InstrumentError) as failure:
        broken.measure_strip("reflective_strip", **strip)
    assert failure.value.stage == "invalid_spectrum"


def test_a_scan_tolerance_is_refused_by_an_instrument_that_does_not_honour_it():
    """The DTP41 advertises no inst2_has_scan_toll; the ColorMunki does."""
    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    strip = dict(patch_count=4, pass_name="B", guide=15, patch_width_mm=6.0,
                 gap_width_mm=1.5, trailer_width_mm=3.0, scan_tolerance=0.75)
    dtp41 = _meter(worker, "X-Rite DTP41", "CM-CHART-0001",
                   environment=dict(os.environ, CM_CHART_PERSONA="dtp41"))
    with pytest.raises(InstrumentError) as failure:
        dtp41.measure_strip("reflective_strip", **strip)
    assert failure.value.stage == "scan_tolerance_unavailable"

    munki = _meter(worker, "X-Rite ColorMunki", "CM-CHART-0001",
                   environment=dict(os.environ, CM_CHART_PERSONA="munki"))
    assert len(munki.measure_strip("reflective_strip", **strip).patches) == 4


def test_skipping_a_condition_the_driver_did_not_offer_is_refused_locally():
    """A skip must not reach the helper unless the driver marked it optional."""
    worker = supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")
    meter = _meter(worker, COLORIMETER, "CM-COLORIMETER-0001",
                   environment=dict(os.environ, CM_COLORIMETER_FIXTURE="grey_ladder_calibration"))
    with pytest.raises(ProtocolError, match="did not offer to skip"):
        meter.measure("emissive_spot", spectral=False, calibrate=True,
                      acknowledge=lambda prompt: "skip")


def test_a_click_calibration_is_told_to_the_client_but_never_waited_on():
    """The DTP22 shape, and the first calibration here no i1Pro can raise.

    dtp22.c:712-720 asks for ``inst_calc_man_ref_whitek`` and dtp22.c:722 says the
    calibration "only works when triggered by the read switch": the operator
    clicks the instrument down on its tile and THAT is the trigger. Upstream
    therefore skips its own keypress for exactly this condition
    (instappsup.c:403-405).

    The proof is the callback's return value. "skip" is refused for a condition the
    driver did not mark optional, so if this prompt were being treated as a
    question the measurement would raise -- as it does for the colorimeter's
    non-skippable condition two tests above. It completes, because nothing is
    waiting for an answer.
    """
    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    seen = []
    meter = _meter(worker, "X-Rite DTP22", None,
                   environment=dict(os.environ, CM_CHART_PERSONA="dtp22",
                                    CM_CHART_FIXTURE="success"))

    def watch(prompt):
        seen.append(prompt)
        return "skip" if prompt.get("acknowledged") is False else "acknowledge"

    result = meter.measure("reflective_spot", calibrate=True, acknowledge=watch)
    assert len(result.patches) == 1
    assert [p["calibration_condition"] for p in seen if "calibration_condition" in p] == [0x20]
    assert seen[0]["acknowledged"] is False
    assert [p["event"] for p in seen] == ["calibration_condition", "instruction_required"]
    assert seen[1]["instruction"] == "sample_position"
    # The tile serial travels with it, and says that that is what it is.
    assert (seen[0]["reference_id"], seen[0]["reference_type"]) == ("22", 0x1)


def test_a_filter_change_names_the_filter_it_wants():
    """SpectroScan's ``inst_calc_change_filter`` puts the filter name in ``id[]``
    (ss.c), and ``inst_calc_id_filt_UV`` says the string is a filter rather than a
    reference serial. A client rendering both fields the same way would tell the
    operator to fit a calibration tile called UV cut Filter."""
    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    seen = []
    meter = _meter(worker, "GretagMacbeth SpectroScan", "CM-CHART-0001",
                   environment=dict(os.environ, CM_CHART_PERSONA="spectroscan",
                                    CM_CHART_FIXTURE="filter_after_calibration"))
    result = meter.measure("reflective_spot_m2", calibrate=True,
                           acknowledge=lambda prompt: seen.append(prompt) or "acknowledge")
    assert len(result.patches) == 1
    assert seen[0]["calibration_condition"] == 0x00010000
    assert (seen[0]["reference_id"], seen[0]["reference_type"]) == ("UV cut Filter", 0x00500000)
    assert seen[0]["acknowledged"] is True


def test_the_grey_ladder_tells_the_client_which_grey_to_display():
    """DTP92's cell-ratio calibration is a FEEDBACK LOOP, not one condition.

    The driver asks for a grey patch, measures it, then asks for a darker or
    lighter one. The level is the application's to compute -- dispsup.c:186-200
    starts at 0.6, multiplies by 0.7 or 1.4 clamped to 1.0, and gives up after
    four adjustments -- and no driver reads it back, so a client that is not told
    the level has to invent a grey. "A grey patch" is not a colour.
    """
    worker = supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")
    seen = []
    meter = _meter(worker, COLORIMETER, "CM-COLORIMETER-0001",
                   environment=dict(os.environ,
                                    CM_COLORIMETER_FIXTURE="grey_ladder_calibration"))
    result = meter.measure("emissive_spot", spectral=False, calibrate=True,
                           acknowledge=lambda prompt: seen.append(prompt) or "acknowledge")
    assert len(result.patches) == 1
    assert [p["calibration_condition"] for p in seen if "calibration_condition" in p] == [
        0x300, 0x400, 0x500]
    levels = [prompt["display_level"] for prompt in seen if "display_level" in prompt]
    assert levels[0] == pytest.approx(0.6)
    assert levels[1] == pytest.approx(0.6 * 0.7)
    assert levels[2] == pytest.approx(0.6 * 0.7 * 1.4)


# --- configuration -------------------------------------------------------------


def test_the_mode_name_already_names_its_measurement_condition():
    """M0-M3 are on the mode name, so the filter costs the caller nothing."""
    from color_measurement.argyll_capability import FILTERS, filter_for
    assert [filter_for(f"reflective_spot_m{n}") for n in range(4)] == [
        FILTERS["m0"], FILTERS["m1"], FILTERS["m2"], FILTERS["m3"],
    ]
    # A mode that states no condition leaves the instrument's filter alone; that
    # is upstream's own inst_opt_filter_unknown, not a made-up sentinel.
    assert filter_for("reflective_spot") == FILTERS["unknown"] == 0xFFFF
    assert filter_for("emissive_spot") == FILTERS["unknown"]
    assert Configuration().flags("reflective_spot_m2")["filter"] == FILTERS["m2"]
    # An explicit filter applies when the mode does not name a condition.
    assert Configuration(filter="d65").flags("reflective_spot")["filter"] == FILTERS["d65"]


def test_a_configuration_only_sends_what_was_asked_for():
    """Absent options must not become a request; the helper's defaults mean 'leave it'."""
    flags = Configuration().flags("emissive_spot")
    assert flags == {"highres": 0, "filter": 0xFFFF, "xcalstd": -2}
    assert "averages" not in flags and "display_type" not in flags
    full = Configuration(highres=True, averages=20, xcalstd="xrga",
                         display_type=DisplayTypeSelection(2, "a"*64, "b"*64))
    assert full.flags("reflective_spot_m1") == {
        "highres": 1, "filter": 0x0001, "xcalstd": 2, "averages": 20, "display_type": 2,
        "display_catalogue_id": "a"*64, "display_choice_id": "b"*64,
    }


def test_a_configuration_refuses_what_argyll_has_no_value_for():
    for bad, refusal in ((dict(xcalstd="m1"), "calibration standard"),
                         (dict(filter="xrga"), "ArgyllCMS filter"),
                         (dict(averages=-1), "bounded"),
                         (dict(averages=100000), "bounded"),
                         (dict(display_type=-1), "named DisplayTypeSelection")):
        with pytest.raises(ValueError, match=refusal):
            Configuration(**bad)


@pytest.mark.parametrize("standard", ["xrdi", "gmdi", "xrga"])
def test_calibration_standard_conversion_requires_spectrum_before_opening(standard):
    with pytest.raises(ValueError, match="conversion requires spectral output"):
        Configuration(xyz_only=True, xcalstd=standard)


def test_calibration_standards_apply_to_live_and_stored_reflection_only():
    for mode in ("reflective_spot", "saved_chart"):
        assert Configuration(xcalstd="xrga").flags(mode)["xcalstd"] == 2
        assert Configuration(xyz_only=True, xcalstd="native").flags(mode)["xcalstd"] == -1
    for mode in ("emissive_spot", "ambient_spot", "transmission_spot"):
        with pytest.raises(ValueError, match="only to reflective"):
            Configuration(xcalstd="xrga").flags(mode)


def test_a_measurement_condition_reaches_the_instrument_in_upstreams_order():
    """The filter precedes the calibration standard (spotread.c:1631, :1670).

    The substituted instrument aborts if the standard arrives first, so this
    fails loudly rather than reporting a reading taken in the wrong order.

    Driven on the ColorMunki because it ADVERTISES inst3_filter_UVCut. This ran
    on the DTP41 until the substituted instrument stopped accepting a condition
    its own capability word does not carry: dtp41.c:1145 sets
    `p->cap3 = inst3_none` and dtp41.c implements no filter option at all, so a
    real DTP41 refuses M2 and the passing case was an artefact of the stub being
    more permissive than any driver in the pinned tree.
    """
    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    meter = _meter(worker, "X-Rite ColorMunki", "CM-CHART-0001",
                   environment=dict(os.environ, CM_CHART_PERSONA="munki"))
    result = meter.measure_strip(
        "reflective_strip_m2", patch_count=4, pass_name="B", guide=15,
        patch_width_mm=6.0, gap_width_mm=1.5, trailer_width_mm=3.0,
        configuration=Configuration(xcalstd="xrga"),
    )
    assert len(result.patches) == 4


def test_a_condition_the_instrument_never_advertised_is_refused():
    """There is no capability pre-check on this path -- upstream sets the filter
    and lets the driver answer (spotread.c:1631-1637) -- so the driver's refusal
    is the only thing between a caller and a reading taken under a condition it
    did not ask for. A DTP41 carries no inst3 filter bit at all."""
    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    meter = _meter(worker, "X-Rite DTP41", "CM-CHART-0001",
                   environment=dict(os.environ, CM_CHART_PERSONA="dtp41"))
    with pytest.raises(InstrumentError) as failure:
        meter.measure_strip(
            "reflective_strip_m2", patch_count=4, pass_name="B", guide=15,
            patch_width_mm=6.0, gap_width_mm=1.5, trailer_width_mm=3.0,
        )
    assert failure.value.stage == "set_filter"


def test_high_resolution_is_deferred_to_a_second_set_mode():
    """inst_mode_highres is mode dependent; the stub offers it only after set_mode.

    Composing it into the first mode word would be refused by check_mode before
    the instrument ever advertised it, which is exactly why upstream defers it
    (spotread.c:1610-1628) and why a factored bind routine could not express it.
    """
    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    strip = dict(patch_count=4, pass_name="B", guide=15, patch_width_mm=6.0,
                 gap_width_mm=1.5, trailer_width_mm=3.0)
    munki = _meter(worker, "X-Rite ColorMunki", "CM-CHART-0001",
                   environment=dict(os.environ, CM_CHART_PERSONA="munki"))
    assert len(munki.measure_strip("reflective_strip", configuration=Configuration(highres=True),
                                   **strip).patches) == 4
    blind = _meter(worker, "X-Rite ColorMunki", "CM-CHART-0001",
                   environment=dict(os.environ, CM_CHART_PERSONA="munki",
                                    CM_CHART_FIXTURE="no_highres"))
    with pytest.raises(InstrumentError) as failure:
        blind.measure_strip("reflective_strip", configuration=Configuration(highres=True), **strip)
    assert failure.value.stage == "highres_unavailable"


# --- a chart the instrument already holds ---------------------------------------


def test_a_chart_is_refused_unless_its_sheets_account_for_its_passes():
    """The driver walks the per-sheet list; a short one walks off the array.

    chartread.c:1061 terminates that list with a zero. The helper reserves the
    full grid, including unused slots in its final row. The sheets must account
    for every pass before entering the driver.
    """
    meter = Instrument(helper="/x", instrument="X-Rite DTP20", expected_serial="SN-1")
    good = dict(patch_count=12, passes=4, steps=3, sheets=(2, 2))
    for bad, refusal in (({"sheets": (2, 3)}, "account for"), ({"sheets": (1,)}, "account for"),
                         ({"sheets": ()}, "account for"), ({"sheets": (4, 0)}, "account for"),
                         ({"patch_count": 13}, "at least one patch"),
                         ({"patch_count": 0}, "at least one patch")):
        with pytest.raises(ValueError, match=refusal):
            meter.measure_chart("saved_chart", **{**good, **bad})
    with pytest.raises(ValueError, match="spot workflow"):
        meter.measure_chart("saved_spot", **good)

    # A safe partial final row remains valid, including its unused native slot.
    validate_chart_layout(5, 2, 3, (2,))
    steps = MAX_PATCHES // 2 + 1
    # read_xy shares sheet_layout but does not use the chart driver's full-grid buffer.
    sheet_layout(2, steps, MAX_PATCHES)
    with pytest.raises(ValueError, match="at most 4096"):
        meter.measure_chart("saved_chart", patch_count=MAX_PATCHES, passes=2,
                            steps=steps, sheets=(2,))


def test_a_stored_chart_reads_with_its_runtime_status():
    """The status is queried and published, never inferred from a capability word."""
    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    meter = _meter(worker, "X-Rite DTP20", "CM-CHART-0001",
                   environment=dict(os.environ, CM_CHART_PERSONA="dtp20"))
    result = meter.measure_chart("saved_chart", patch_count=12, passes=4, steps=3,
                                 sheets=(2, 2), chart_id=7)
    assert result.confirmed_routes == ("s_ref_chart",)
    assert result.workflow == "chart"
    assert [p.index for p in result.patches] == list(range(12))
    assert result.stored is not None and result.stored != 0
    # Nothing stored must read as nothing, not as an empty chart.
    empty = _meter(worker, "X-Rite DTP20", "CM-CHART-0001",
                   environment=dict(os.environ, CM_CHART_PERSONA="dtp20",
                                    CM_CHART_FIXTURE="nothing_stored"))
    assert empty.stored_readings("saved_spot") == 0


def test_every_argyll_route_reaches_an_acquisition_method():
    """The goal, as a test: no route is left without a way to acquire it.

    Asserting against `WORKFLOW_FACILITIES` alone would be circular -- it is part
    of what is under test, and its keys are trivially all of `ACTIONS`. What must
    hold is that every one of the eighteen routes reaches a *method* that accepts
    it: each verb refuses a mode belonging to another workflow by name, so getting
    past that guard is what proves the route is served.
    """
    from color_measurement.argyll_capability import (
        _NAME_ROUTES,
        MODE_ROUTES,
        parse_mode_name,
        workflow_kind,
    )
    published = {f"{illum}_{action}" for illum, action in _NAME_ROUTES}
    routes = {parse_mode_name(name)[0] for name in published}
    assert routes == set(MODE_ROUTES), "every Argyll route must have a published name"

    meter = Instrument(helper="/x", instrument=COLORIMETER, expected_serial="SN-1")
    extra = {
        "spot": {},
        "strip": {"patch_count": 1},
        "xy": {"passes": 1, "steps": 1},
        "chart": {"patch_count": 1, "passes": 1, "steps": 1, "sheets": (1,)},
    }
    verbs = {"spot": meter.measure, "strip": meter.measure_strip,
             "xy": meter.measure_sheet, "chart": meter.measure_chart}
    for name in sorted(published):
        workflow = workflow_kind(name)
        try:
            verbs[workflow](name, **extra[workflow])
        except ValueError as refusal:
            assert "workflow" not in str(refusal), f"{name} reached no acquisition verb"
        except OSError:
            pass    # past every guard; only the helper binary is missing
    # And each verb really does refuse the others, or the loop above proves nothing.
    for name, workflow in (("reflective_strip", "spot"), ("emissive_spot", "strip"),
                           ("emissive_spot", "xy"), ("emissive_spot", "chart")):
        with pytest.raises(ValueError, match="workflow"):
            verbs[workflow](name, **extra[workflow])


# --- absence stays absence ------------------------------------------------------


def _reported(**native):
    base = {"mtype": 5, "mcond": 0, "XYZ_v": 1, "XYZ": [1.0, 2.0, 3.0],
            "duration": 0.0, "spectrum": None}
    return {"event": "result", "backend_reported": {**base, **native}}


def test_an_unmeasured_quantity_is_never_turned_into_a_zero():
    """An unavailable quantity must remain distinct from a measured zero.

    A patch whose XYZ the instrument did not produce must come back as ``None``.
    Substituting ``(0, 0, 0)`` publishes a measurement that says "perfectly black"
    for a reading that was never taken, and nothing downstream can tell the two
    apart. The substituted instruments all report valid XYZ, so this path has no
    other coverage.
    """
    from color_measurement.instrument import _patch
    spectrum = {"spec_n": 3, "wavelength_start_nm": 400.0, "wavelength_end_nm": 700.0,
                "norm": 100.0, "samples": [1.0, 2.0, 3.0]}

    absent = _patch(_reported(XYZ_v=0, XYZ=None, spectrum=spectrum), True)
    assert absent.xyz is None, "an unmeasured XYZ must stay absent"
    assert absent.spectrum is not None and absent.spectrum.samples == (1.0, 2.0, 3.0)

    # Invalid XYZ carrying values anyway is a contradiction, not something to publish.
    with pytest.raises(ProtocolError, match="never converted to zeros"):
        _patch(_reported(XYZ_v=0, XYZ=[0.0, 0.0, 0.0], spectrum=spectrum), True)

    # A patch with neither quantity is not a reading.
    with pytest.raises(ProtocolError, match="no quantities"):
        _patch(_reported(XYZ_v=0, XYZ=None, spectrum=None), False)

    # A spectral instrument that published no spectrum is a fault, not an XYZ-only
    # reading; a colorimeter that published one is the opposite fault.
    with pytest.raises(ProtocolError, match="no spectrum"):
        _patch(_reported(), True)
    with pytest.raises(ProtocolError, match="advertising none"):
        _patch(_reported(spectrum=spectrum), False)


def test_a_patch_refuses_an_unusable_spectral_grid():
    """Bands that cannot describe a measurement must not be published as one."""
    from color_measurement.instrument import _patch
    def grid(**over):
        return {**{"spec_n": 3, "wavelength_start_nm": 400.0, "wavelength_end_nm": 700.0,
                   "norm": 100.0, "samples": [1.0, 2.0, 3.0]}, **over}
    for broken, refusal in ((grid(spec_n=1, samples=[1.0]), "spectral count"),
                            (grid(spec_n=2), "spectral count"),
                            (grid(norm=0.0), "grid or normalization"),
                            (grid(wavelength_start_nm=0.0), "grid or normalization"),
                            (grid(wavelength_end_nm=400.0), "grid or normalization"),
                            (grid(samples=[1.0, 2.0, float("nan")]), "finite")):
        with pytest.raises(ProtocolError, match=refusal):
            _patch(_reported(spectrum=broken), True)
    with pytest.raises(ProtocolError, match="negative time"):
        _patch(_reported(duration=-1.0), False)


# --- the request that reaches the helper ----------------------------------------


def test_no_workflow_demands_a_trigger_the_instrument_may_not_have():
    """dtp51.c:809 advertises inst2_switch_trig and nothing else.

    `session.c` applies `--facilities` as a hard conjunction, so any trigger bit
    named here excludes every instrument lacking it. Upstream never requires one --
    chartread.c:1412-1432 walks the bits and picks. A sheet's two positioning bits
    are different: without them there is no way to locate the chart at all.
    """
    from color_measurement.argyll_capability import CAPABILITY2
    meter = Instrument(helper="/x", instrument=COLORIMETER, expected_serial="SN-1")
    triggers = {"prog_trig", "user_trig", "switch_trig", "user_switch_trig"}
    for workflow, facilities in WORKFLOW_FACILITIES.items():
        assert not (set(facilities) & triggers), f"{workflow} demands a trigger"
    assert WORKFLOW_FACILITIES["xy"] == ("xy_locate", "xy_position")
    # xy_holdrel is deliberately absent: upstream guards every use of it
    # (chartread.c:1071, :1142, :1344) rather than requiring it.
    assert "xy_holdrel" not in WORKFLOW_FACILITIES["xy"]

    def facilities_of(mode, workflow):
        argv = meter._argv(mode, True, WORKFLOW_FACILITIES[workflow], {})
        return int(argv[argv.index("--facilities") + 1])

    assert facilities_of("emissive_spot", "spot") == 0
    assert facilities_of("reflective_strip", "strip") == 0
    assert facilities_of("saved_chart", "chart") == 0
    assert facilities_of("reflective_xy", "xy") == (
        CAPABILITY2["xy_locate"] | CAPABILITY2["xy_position"]
    )


def test_the_serial_pin_is_optional_on_the_command_line():
    """Nine drivers report no serial; an empty pin is how the helper is told."""
    meter = Instrument(helper="/x", instrument="X-Rite DTP41")
    argv = meter._argv("reflective_strip", True, (), {})
    assert argv[argv.index("--expected-serial") + 1] == ""
    pinned = Instrument(helper="/x", instrument="X-Rite DTP41", expected_serial="A1")
    argv = pinned._argv("reflective_strip", True, (), {})
    assert argv[argv.index("--expected-serial") + 1] == "A1"


def test_a_sheet_refuses_to_navigate_by_points_nobody_sighted():
    """chartread.c:1192 loops exactly nloc times; this cannot diverge either.

    Deriving the branch from however many points happened to arrive would navigate
    a real chart by a geometry nobody established -- silently, because
    `navigation_vectors` accepts one, two or three points by design.
    """
    meter = Instrument(helper="/x", instrument="GretagMacbeth SpectroScan",
                       expected_serial="SN-1")
    prompt = {"instruction": "xy_navigate", "request_id": "navigate"}
    sighted = (Location(0.0, 0.0), Location(0.0, 10.0), Location(20.0, 0.0))
    sheet = (3, 5, False, 3)
    action, argument = meter._reply(prompt, list(sighted), sheet, None)
    assert action == "navigate" and len(argument.split(",")) == 8
    for short in (sighted[:2], sighted[:1], ()):
        with pytest.raises(ProtocolError, match="sighted where"):
            meter._reply(prompt, list(short), sheet, None)


def test_an_unanswered_saved_readings_status_is_not_reported_as_nothing_stored():
    """"Nobody answered" and "nothing is stored" are different facts.

    Returning 0 for an absent status is the same substitution `_patch` exists to
    prevent, one field over.
    """
    meter = Instrument(helper="/x", instrument="X-Rite DTP20", expected_serial="SN-1")
    answered = Measurement(instrument="X-Rite DTP20", serial=None, mode="saved_spot",
                           workflow="spot", provenance="simulated", spectral=True,
                           confirmed_routes=("s_ref_spot",), stored=3)
    silent = dataclasses.replace(answered, stored=None)
    def answering(result):
        # Instrument is frozen, so substitute the bound method on the class for
        # the duration rather than on the instance.
        return lambda self, *a, **k: result

    original = Instrument._run
    try:
        Instrument._run = answering(answered)
        assert meter.stored_readings("saved_spot") == 3
        Instrument._run = answering(dataclasses.replace(answered, stored=0))
        assert meter.stored_readings("saved_spot") == 0, "zero is a real answer"
        Instrument._run = answering(silent)
        with pytest.raises(ProtocolError, match="no saved-readings status"):
            meter.stored_readings("saved_spot")
    finally:
        Instrument._run = original


def test_a_sheet_that_cannot_be_navigated_is_refused_before_anything_is_spawned(
    tmp_path, monkeypatch,
):
    """Three passes of one step asks for three fiducials it cannot use.

    `sheet_layout` correctly returns three (chartread.c:1102-1107 only reduces the
    count for a single-pass sheet), but there is no step interval to divide by.
    Discovering that inside `_reply` would fail on a real table after the operator
    had already sighted every point; discovering it here costs them nothing.
    """
    import color_measurement.instrument as module

    helper = tmp_path / "inert-worker"
    helper.write_bytes(b"Authenticated inert geometry-test input")
    launches = []

    def intercept(argv, **kwargs):
        launches.append(argv)
        raise OSError("inert session constructor intercepted")

    monkeypatch.setattr(module, "HelperSession", intercept)
    meter = Instrument(
        helper=helper, instrument="GretagMacbeth SpectroScan",
        expected_helper_sha256=hashlib.sha256(helper.read_bytes()).hexdigest(),
    )
    assert sheet_layout(passes=3, steps=1, patch_count=3) == (3, 1)
    for hexagonal in (True, False):
        with pytest.raises(ValueError, match="at least two steps"):
            meter.measure_sheet("reflective_xy", passes=3, steps=1, hexagonal=hexagonal)
    assert launches == [], "Invalid geometry reached the session constructor"
    # Explicit geometry passes validation and authentication, without a process.
    with pytest.raises(OSError, match="inert session constructor intercepted"):
        meter.measure_sheet("reflective_xy", passes=3, steps=1,
                            geometry=(0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0))
    assert len(launches) == 1
    argv = launches[0]
    assert argv[0] == str(helper.resolve())
    assert argv[argv.index("--xy-geometry") + 1] == "0.0,0.0,1.0,0.0,0.0,0.0,0.0,1.0"


# -- colorimeter corrections -------------------------------------------------

CCMX_TEXT = (
    'CCMX\n\nINSTRUMENT "Xrite DTP94"\nDISPLAY "Dell 2408WFP"\nCOLOR_REP "XYZ"\n'
    'TECHNOLOGY "LCD RG Phosphor IPS"\n\nNUMBER_OF_FIELDS 3\nBEGIN_DATA_FORMAT\n'
    "XYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\n\nNUMBER_OF_SETS 3\nBEGIN_DATA\n"
    "0.5 -0.25 0.125\n1.5 2.25 -3.375\n-0.0625 4.5 6.75\nEND_DATA\n"
)
CCSS_TEXT = (
    'CCSS\n\nDISPLAY "Color LCD"\nTECHNOLOGY "LCD RG Phosphor IPS"\n'
    'SPECTRAL_BANDS "4"\nSPECTRAL_START_NM "400.0"\nSPECTRAL_END_NM "700.0"\n'
    'SPECTRAL_NORM "100.0"\n\nNUMBER_OF_FIELDS 4\nBEGIN_DATA_FORMAT\n'
    "SPEC_400 SPEC_500 SPEC_600 SPEC_700\nEND_DATA_FORMAT\n\nNUMBER_OF_SETS 3\n"
    "BEGIN_DATA\n0.0 0.25 0.5 0.75\n1.0 1.25 1.5 1.75\n2.0 2.25 2.5 2.75\nEND_DATA\n"
)


def _correction(tmp_path, name, text):
    from color_measurement.colorimeter_correction import read_correction
    path = tmp_path / name
    path.write_text(text)
    return read_correction(path)


def test_a_configuration_takes_only_a_parsed_correction():
    """A path or a raw string would mean parsing CGATS somewhere other than the
    one module that does it, and a silently ignored correction is uncorrected XYZ."""
    for bad in ("/some/file.ccmx", 3, [[1, 0, 0], [0, 1, 0], [0, 0, 1]]):
        with pytest.raises(ValueError, match="CorrectionMatrix or a CalibrationSpectralSet"):
            Configuration(correction=bad)
    assert Configuration().correction is None


def test_a_matrix_correction_travels_on_the_command_line(tmp_path):
    from color_measurement.instrument import _correction_arguments
    matrix = _correction(tmp_path, "a.ccmx", CCMX_TEXT)
    with _correction_arguments(matrix) as flags:
        assert flags["correction_matrix"] == (
            "0.5,-0.25,0.125,1.5,2.25,-3.375,-0.0625,4.5,6.75"
        )
        # The technology string, resolved to the disptech enum the vtable takes.
        assert flags["correction_dtech"] == 0x3510
        # inst.h:1105 wants 1 when the file declared no base id.
        assert flags["correction_cbid"] == 1
        assert "calibration_spectra" not in flags


def test_a_spectral_correction_travels_as_a_file_that_outlives_argument_building(tmp_path):
    """One .ccss may hold 601 bands x N samples, past the 128 KiB Linux allows in a
    single argv element (MAX_ARG_STRLEN), so it is a file the helper opens."""
    from pathlib import Path

    from color_measurement.instrument import _correction_arguments
    spectra = _correction(tmp_path, "a.ccss", CCSS_TEXT)
    with _correction_arguments(spectra) as flags:
        written = Path(flags["calibration_spectra"])
        assert flags["correction_dtech"] == 0x3510
        assert "correction_matrix" not in flags
        lines = written.read_text().splitlines()
        # The layout load_spectra() reads: grid header, then one line per sample.
        assert lines[0] == "4 400.0 700.0 100.0 3"
        assert [line.split() for line in lines[1:]] == [
            ["0.0", "0.25", "0.5", "0.75"],
            ["1.0", "1.25", "1.5", "1.75"],
            ["2.0", "2.25", "2.5", "2.75"],
        ]
    # Released with the session, not left behind in the temporary directory.
    assert not written.exists()


@pytest.mark.parametrize("header", [
    "1 550 550 1 3", "4 700 400 1 3", "4 400 400 1 3",
    "4 0 300 1 3",
])
def test_native_correction_input_refuses_unusable_grids_before_connection(tmp_path, header):
    worker = supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")
    path = tmp_path / "spectra.txt"
    bands = int(header.split()[0])
    path.write_text(header + "\n" + (" ".join(["1"] * bands) + "\n") * 3)
    meter = Instrument(worker, "instI1Disp3",
                expected_helper_sha256=hashlib.sha256(
                    Path(worker).read_bytes()).hexdigest())
    command = meter._argv("emissive_spot", False, [],
                          {"calibration_spectra": str(path), "mode_snapshot": 1})
    run = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert run.returncode == 2, run.stdout + run.stderr
    assert not run.stdout, "Invalid correction must fail before a connected event"


@pytest.mark.parametrize("header", ["2 400 700 1 3", "3 400 700 1 3"])
def test_native_correction_input_accepts_coarse_two_and_three_band_grids(tmp_path, header):
    """The composed cubic helper interpolates them linearly (xspect.c fallback),
    so the worker no longer refuses coarse short grids before connecting."""
    worker = supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")
    path = tmp_path / "spectra.txt"
    bands = int(header.split()[0])
    path.write_text(header + "\n" + (" ".join(["1"] * bands) + "\n") * 3)
    meter = Instrument(worker, "instI1Disp3",
                expected_helper_sha256=hashlib.sha256(
                    Path(worker).read_bytes()).hexdigest())
    command = meter._argv("emissive_spot", False, [],
                          {"calibration_spectra": str(path), "mode_snapshot": 1})
    run = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stdout + run.stderr
    assert '"event":"connected"' in run.stdout


def test_no_correction_adds_no_flags():
    from color_measurement.instrument import _correction_arguments
    with _correction_arguments(None) as flags:
        assert flags == {}


def test_serial_identification_is_off_unless_asked_for():
    """A serial path carries no identity (icoms.c:269-299), so learning one means
    opening the port. It is a permission, and the default is not to have it."""
    meter = Instrument(helper="/x", instrument="GretagMacbeth SpectroScan")
    argv = meter._argv("reflective_xy", True, (), {})
    assert argv[argv.index("--allow-serial-identification") + 1] == "0"
    # Positional: cm_parse_selection reads this pair immediately after the three
    # mandatory ones, so it must sit directly after --instrument.
    assert argv.index("--allow-serial-identification") == argv.index("--instrument") + 2

    allowed = Instrument(helper="/x", instrument="GretagMacbeth SpectroScan",
                         allow_serial_identification=True)
    argv = allowed._argv("reflective_xy", True, (), {})
    assert argv[argv.index("--allow-serial-identification") + 1] == "1"


CCMX_ALT_TEXT = (
    'CCMX\n\nINSTRUMENT "Xrite DTP94"\nDISPLAY "OLED panel"\nCOLOR_REP "XYZ"\n'
    'TECHNOLOGY "LED AMOLED"\nDISPLAY_TYPE_BASE_ID "3"\n\nNUMBER_OF_FIELDS 3\n'
    "BEGIN_DATA_FORMAT\nXYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\n\nNUMBER_OF_SETS 3\n"
    "BEGIN_DATA\n1.25 0.0625 -0.5\n-2.75 0.375 8.0\n0.03125 -1.5 0.25\nEND_DATA\n"
)
CCSS_ROUNDTRIP_TEXT = (
    'CCSS\n\nDISPLAY "Color LCD"\nTECHNOLOGY "LCD RG Phosphor IPS"\n'
    'SPECTRAL_BANDS "4"\nSPECTRAL_START_NM "400.0"\nSPECTRAL_END_NM "700.0"\n'
    'SPECTRAL_NORM "2.5"\n\nNUMBER_OF_FIELDS 4\nBEGIN_DATA_FORMAT\n'
    "SPEC_400 SPEC_500 SPEC_600 SPEC_700\nEND_DATA_FORMAT\n\nNUMBER_OF_SETS 5\n"
    "BEGIN_DATA\n0.0 0.25 0.5 0.75\n1.0 1.25 1.5 1.75\n2.0 2.25 2.5 2.75\n"
    "3.0 3.25 3.5 3.75\n4.0 4.25 4.5 4.75\nEND_DATA\n"
)


@pytest.mark.parametrize("name,text,fixture", [
    ("a.ccmx", CCMX_TEXT, "matrix_roundtrip"),
    ("b.ccmx", CCMX_ALT_TEXT, "matrix_roundtrip_alt"),
    ("c.ccss", CCSS_ROUNDTRIP_TEXT, "spectra_roundtrip"),
])
def test_a_correction_reaches_the_instrument_through_a_configuration(
    tmp_path, name, text, fixture
):
    """The wiring, end to end -- the one thing no other test covered.

    Deleting `_correction_arguments` from `_run` entirely left the whole suite
    green: every correction test called that helper DIRECTLY, the session check
    builds its own argv without going through `Instrument`, and the stub-gated
    session tests passed no Configuration. A client could have asked for a
    correction, been told the session completed, and received uncorrected XYZ.

    These fixtures assert the values the helper received AND that one arrived at
    all, so the stub aborts if the correction is dropped anywhere between
    `Configuration` and `col_cor_mat`.
    """
    worker = supplied_stub("COLOR_MEASUREMENT_GENERIC_STUB")
    meter = _meter(worker, COLORIMETER, "CM-COLORIMETER-0001",
                   environment=dict(os.environ, CM_COLORIMETER_FIXTURE=fixture))
    result = meter.measure(
        "emissive_spot", spectral=False,
        configuration=Configuration(correction=_correction(tmp_path, name, text)),
    )
    assert len(result.patches) == 1
    assert result.patches[0].xyz == (21.5, 24.0, 9.75)


def test_a_declared_base_id_reaches_the_command_line(tmp_path):
    """`calibration_base_id` exists to turn the file's "not known" 0 into the 1
    inst.h:1105 wants. Only files WITHOUT a declared id were ever passed through
    the flags, so hardcoding 1 at this boundary survived -- and cbid selects the
    base calibration the correction is applied over."""
    from color_measurement.instrument import _correction_arguments
    with _correction_arguments(_correction(tmp_path, "b.ccmx", CCMX_ALT_TEXT)) as flags:
        assert flags["correction_cbid"] == 3
        assert flags["correction_dtech"] == 0x4010


def test_a_spectral_correction_is_released_when_the_session_cannot_start(
    tmp_path, monkeypatch,
):
    """Authentication passes; the intercepted session constructor then fails.

    The staged CCSS file exists at that failure and is released afterwards.
    No helper or device process is started.
    """
    from pathlib import Path

    import color_measurement.instrument as module

    correction = _correction(tmp_path, "c.ccss", CCSS_ROUNDTRIP_TEXT)
    seen: list = []

    class Boom(RuntimeError):
        pass

    helper = tmp_path / "inert-worker"
    helper.write_bytes(b"Authenticated inert correction-test input")

    def explode(argv, **_kw):
        assert argv[0] == str(helper.resolve())
        staged = Path(argv[argv.index("--calibration-spectra") + 1])
        assert staged.is_file(), "The session received no staged spectral correction"
        seen.append(staged)
        raise Boom("helper could not start")

    monkeypatch.setattr(module, "HelperSession", explode)
    meter = Instrument(
        helper=helper, instrument=COLORIMETER, expected_serial="SN-1",
        expected_helper_sha256=hashlib.sha256(helper.read_bytes()).hexdigest(),
    )
    with pytest.raises(Boom, match="helper could not start"):
        meter.measure("emissive_spot", spectral=False,
                      configuration=Configuration(correction=correction))
    assert len(seen) == 1 and not seen[0].exists(), (
        "the spectral file outlived the failed session")
    assert (tmp_path / "c.ccss").is_file(), "The supplied correction must be preserved"


# -- results integration --------------------------------------------------------

def _measurement(**kw):
    from color_measurement.instrument import Measurement
    defaults = dict(
        instrument="X-Rite i1 Pro 3", serial="SN-1", serial_encoding="utf8",
        mode="reflective_spot_m0",
        workflow="spot", provenance="physical", spectral=True,
        confirmed_routes=("ref_spot",), patches=(), locations=(),
        calibration=None, stored=None, events=(),
    )
    if kw.get("serial", "SN-1") is None and "serial_encoding" not in kw:
        kw["serial_encoding"] = None
    return Measurement(**{**defaults, **kw})


def _device(**kw):
    """The identity the BINDING holds, which is what a reading is stamped with."""
    from color_measurement.models import DeviceIdentity
    defaults = dict(device_id="X-Rite i1 Pro 3", model="X-Rite i1 Pro 3",
                    backend="argyll-native", backend_version="3.5.0", serial="SN-1",
                    serial_encoding="utf8")
    if kw.get("serial", "SN-1") is None and "serial_encoding" not in kw:
        kw["serial_encoding"] = None
    return DeviceIdentity(**{**defaults, **kw})


def _patch(mtype=5, xyz=(21.5, 24.0, 9.75), spectrum=None, mcond=0, duration=0.0):
    from color_measurement.instrument import Patch
    return Patch(mtype=mtype, mcond=mcond, xyz=xyz, spectrum=spectrum,
                 duration_seconds=duration, index=0)


def test_a_reading_becomes_an_observation_carrying_its_units():
    """The gap that blocked everything: a generic reading had numbers and no
    units, so it could not become an Observation and could not be saved."""
    from color_measurement.instrument import observation_from_patch

    observation = observation_from_patch(
        _patch(), _measurement(), device=_device(), operation_id="op-1", sample_id="A1",
        acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
    )
    assert observation.backend_reported.xyz.values == (21.5, 24.0, 9.75)
    assert observation.backend_reported.xyz.unit == "1"
    assert observation.backend_reported.xyz.illuminant == "D50"
    assert observation.backend_reported.xyz.observer == "CIE1931_2"
    assert observation.device.backend == "argyll-native"
    assert observation.device.serial == "SN-1"
    facts = dict(observation.configuration)
    # One vocabulary. `processing.reflectance_fraction` and `spectral_exports`
    # both gate on exactly these keys, so the longer spelling this used to publish
    # meant a generic reflective reading could not be converted or exported at all.
    assert facts["native_mtype"] == "5"
    assert facts["native_mcond"] == "0"
    assert facts["correction_applied"] == "none"


def test_an_emissive_reading_is_labelled_absolute_not_relative():
    """Keyed on the reported type: the same call with mtype 1 must not say D50."""
    from color_measurement.instrument import observation_from_patch

    observation = observation_from_patch(
        _patch(mtype=1), _measurement(mode="emissive_spot"), device=_device(),
        operation_id="op-1",
        sample_id="A1", acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
    )
    assert observation.backend_reported.xyz.unit == "cd/m^2"
    assert observation.backend_reported.xyz.illuminant == "none (emissive)"


def test_absent_quantities_stay_absent():
    """A colorimeter reports no spectrum and an invalid XYZ is not zeroed."""
    from color_measurement.instrument import observation_from_patch

    colorimeter = observation_from_patch(
        _patch(mtype=1, spectrum=None), _measurement(mode="emissive_spot", spectral=False),
        device=_device(), operation_id="op-1", sample_id="A1",
        acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
    )
    assert colorimeter.backend_reported.spectrum is None
    # An invalid XYZ stays absent rather than becoming zeros, provided the reading
    # still carries something that WAS measured.
    from color_measurement.native_protocol import NativeSpectrum
    spectral_only = observation_from_patch(
        _patch(xyz=None, spectrum=NativeSpectrum(
            start_nm=400.0, end_nm=500.0, norm=100.0, samples=(1.0, 2.0, 3.0))),
        _measurement(), device=_device(), operation_id="op-1", sample_id="A1",
        acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
    )
    assert spectral_only.backend_reported.xyz is None
    assert spectral_only.backend_reported.spectrum.values == (1.0, 2.0, 3.0)
    # And nothing is derived here; xyY and Lab belong to the science layer.
    assert spectral_only.project_computed.xyy is None
    assert spectral_only.project_computed.lab is None

    # Neither quantity at all is not an observation, and says so.
    with pytest.raises(ProtocolError, match="neither a valid XYZ nor a spectrum"):
        observation_from_patch(
            _patch(xyz=None), _measurement(), device=_device(),
            operation_id="op-1", sample_id="A1",
            acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
        )


@pytest.mark.parametrize("mtype", [7, 8, 1])
def test_a_reading_that_cannot_be_labelled_is_refused(mtype):
    """inst_mrt_sensitivity and inst_mrt_frequency (a refresh rate) are not colour
    readings, and an emissive type on a reflective route is another route's.
    Publishing any of them with this route's units invents a quantity."""
    from color_measurement.instrument import observation_from_patch

    with pytest.raises(ProtocolError, match="measurement type"):
        observation_from_patch(
            _patch(mtype=mtype), _measurement(), device=_device(),
            operation_id="op-1", sample_id="A1",
            acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
        )


@pytest.mark.parametrize("mode,unit,quantity", [
    # huey.c:1246 stamps every emissive reading inst_mrt_none.
    ("emissive_spot", "cd/m^2", None),
    # ss.c:829 stamps every X-Y reading inst_mrt_none.
    ("reflective_xy", "1", "reflectance"),
])
def test_a_reading_whose_driver_left_its_type_unset_is_labelled_by_its_route(
        mode, unit, quantity):
    """inst_mrt_none is "not stated" upstream (spotread.c:1923-1935), which labels
    such a reading by the mode it asked for. Refusing it made every Huey emissive
    reading and every SpectroScan X-Y reading unpublishable. The record keeps the
    driver's 0 and says where its units came from."""
    from color_measurement.instrument import observation_from_patch
    from color_measurement.native_protocol import NativeSpectrum

    spectrum = None if quantity is None else NativeSpectrum(
        start_nm=380.0, end_nm=730.0, norm=100.0, samples=(50.0,) * 36)
    observation = observation_from_patch(
        _patch(mtype=0, spectrum=spectrum), _measurement(mode=mode), device=_device(),
        operation_id="op-1", sample_id="A1", acquired_at="2026-09-22T12:00:00Z",
        raw_backend_output="{}",
    )
    assert observation.backend_reported.xyz.unit == unit
    if quantity is not None:
        assert observation.backend_reported.spectrum.quantity == quantity
    facts = dict(observation.configuration)
    assert facts["native_mtype"] == "0"
    assert facts["units_source"].startswith("requested mode")


def test_a_stated_type_carries_no_units_source():
    """The fact is there only when the route supplied the units."""
    from color_measurement.instrument import observation_from_patch

    observation = observation_from_patch(
        _patch(), _measurement(), device=_device(), operation_id="op-1", sample_id="A1",
        acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
    )
    assert "units_source" not in dict(observation.configuration)


def test_an_observation_reaches_the_existing_export_machinery():
    """The claim results integration rests on: once a reading is an Observation,
    the save/export layer the per-device stack already uses works unchanged."""
    import json as _json

    from color_measurement.instrument import observation_from_patch
    from color_measurement.operation_records import (
        OperationResult,
        PatchResult,
        result_from_json,
        result_to_json,
    )

    observation = observation_from_patch(
        _patch(), _measurement(), device=_device(), operation_id="op-1", sample_id="A1",
        acquired_at="2026-09-22T12:00:00Z", raw_backend_output='{"event":"result"}',
    )
    result = OperationResult(
        schema_version=1, operation_id="op-1", device_id="X-Rite i1 Pro 3",
        device=observation.device, provenance="physical", mode="reflective_spot_m0",
        workflow="spot", status="completed", expected_patch_ids=("A1",),
        patches=(PatchResult(index=0, patch_id="A1", observation=observation),),
        request_json=_json.dumps({"device_id": "X-Rite i1 Pro 3",
                                  "mode": "reflective_spot_m0", "workflow": "spot",
                                  "patch_ids": ["A1"]}),
        configuration=(),
        calibration=observation.calibration, error=None,
        software_stopped=True, instrument_state="unknown",
    )
    restored = result_from_json(result_to_json(result))
    assert restored.patches[0].observation.backend_reported.xyz.values == (21.5, 24.0, 9.75)
    assert restored.patches[0].observation.backend_reported.xyz.unit == "1"
    assert restored.provenance == "physical"


def test_a_generic_reflective_reading_reaches_the_reflectance_and_spect_layers():
    """The proof that the key names were a BUG and not a preference.

    `processing.reflectance_fraction` and `spectral_exports` both gate on
    `native_mtype`, `native_mcond` and `spectrum_norm`. While the generic path
    published `native_measurement_type` and no norm at all, a generic reflective
    reading could not be converted to a fraction or exported as SPECT -- the two
    things a reflective measurement exists to be used for. The per-device
    adapters wrote the short names, so this worked for an i1Pro and not for the
    ladder that was supposed to replace it.
    """
    from color_measurement.instrument import observation_from_patch
    from color_measurement.native_protocol import NativeSpectrum
    from color_measurement.processing import reflectance_fraction

    observation = observation_from_patch(
        _patch(mtype=5, mcond=1, spectrum=NativeSpectrum(
            start_nm=400.0, end_nm=410.0, norm=100.0, samples=(50.0, 25.0, 12.5))),
        _measurement(mode="reflective_spot_m1"), device=_device(),
        operation_id="op-1", sample_id="A1",
        acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
    )
    facts = dict(observation.configuration)
    assert (facts["native_mtype"], facts["native_mcond"], facts["spectrum_norm"]) == (
        "5", "1", "100.0")
    # Percent to fraction, using the norm the reading now carries.
    assert reflectance_fraction(observation).values == (0.5, 0.25, 0.125)


def test_a_reading_says_which_correction_the_driver_applied():
    """Not which one was requested. `set_disptype` clears col_cor_mat() and
    col_cal_spec_set() (inst.h:843-845) without saying so, so the two differ."""
    from color_measurement.instrument import observation_from_patch

    for applied in ("none", "matrix", "spectral"):
        observation = observation_from_patch(
            _patch(), _measurement(correction=applied), device=_device(),
            operation_id="op-1", sample_id="A1",
            acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
        )
        assert dict(observation.configuration)["correction_applied"] == applied


def test_an_unpinned_instrument_may_name_itself():
    """A binding that pinned no serial still learns which unit answered.

    Discarding it would lose a measured fact about provenance; requiring the
    reading to match a serial nobody supplied refused every patch from any
    instrument that volunteers one, which is most of them.
    """
    from color_measurement.instrument import observation_from_patch

    unpinned = observation_from_patch(
        _patch(), _measurement(serial="SN-REPORTED"), device=_device(serial=None),
        operation_id="op-1", sample_id="A1",
        acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
    )
    assert unpinned.device.serial == "SN-REPORTED"
    # A pinned serial is NOT overwritten: the helper verifies that one, and a
    # disagreement there is its refusal to make, not this function's to paper over.
    pinned = observation_from_patch(
        _patch(), _measurement(serial="SN-REPORTED"), device=_device(serial="SN-1"),
        operation_id="op-1", sample_id="A1",
        acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
    )
    assert pinned.device.serial == "SN-1"


def test_two_identical_instruments_produce_distinguishable_records():
    """device_id came from the MODEL NAME, so two of the same instrument wrote
    records nothing could tell apart."""
    from color_measurement.instrument import observation_from_patch

    readings = [
        observation_from_patch(
            _patch(), _measurement(), device=_device(device_id=label, serial=serial),
            operation_id="op-1", sample_id="A1",
            acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
        )
        for label, serial in (("bench-left", "SN-1"), ("bench-right", "SN-2"))
    ]
    assert {r.device.device_id for r in readings} == {"bench-left", "bench-right"}
    assert {r.device.model for r in readings} == {"X-Rite i1 Pro 3"}


def test_native_byte_serial_pair_survives_observation_refinement():
    from color_measurement.instrument import observation_from_patch
    serial = "í\u00a0\u0080"
    observation = observation_from_patch(
        _patch(), _measurement(serial=serial, serial_encoding="bytes"),
        device=_device(serial=None), operation_id="op-1", sample_id="A1",
        acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}")
    assert (observation.device.serial, observation.device.serial_encoding) == (serial, "bytes")


@pytest.mark.parametrize("encoding", ["bytes", "utf8"])
def test_a_serial_pin_requires_utf8_text_without_a_substituted_worker(monkeypatch, encoding):
    """The connected-event pin (instrument.py) accepts only the pinned text in
    UTF-8: the same text under the `bytes` encoding is a different unit. The
    lifecycle module checks this through the substituted worker and skips in the
    gate's default form, where dropping the encoding clause went unseen.
    This scripted session has no such gate.
    """
    from conftest import native_display_unsupported

    from color_measurement.argyll_capability import baseline_mode_words, mode_word
    from color_measurement.mode_acceptance import BASELINE_SCOPE

    serial = "í \u0080"
    primary = mode_word("emissive_spot", spectral=False)
    envelope = {"protocol_version": 1, "provenance": "simulated"}
    events = [
        dict(envelope, event="connected", serial=serial, serial_encoding=encoding,
             spectral=False, mode=primary, display_types=native_display_unsupported(),
             baseline_mode_checks={
                 "scope": BASELINE_SCOPE, "mode_capabilities": primary,
                 "checks": [{"mode_word": word, "native_code": 0}
                            for word in baseline_mode_words(primary)]}),
        dict(envelope, event="result", backend_reported={
            "mtype": 1, "mcond": 0, "XYZ_v": 1, "XYZ": [1, 2, 3], "spectrum": None,
            "duration": 0}),
        dict(envelope, event="terminal", status="completed"),
    ]

    class Session:
        stopped = "exited"

        def receive_message(self, **kwargs):
            return events.pop(0)

        def send(self, *args):
            pass

        def finish_messages(self, *, expected_returncode):
            assert expected_returncode == 0

        def close(self):
            pass

    monkeypatch.setattr(Instrument, "_open_session", lambda *args: Session())
    pinned = Instrument("/never-started", "instI1Disp3", expected_serial=serial)
    if encoding == "bytes":
        with pytest.raises(ProtocolError, match="Confirmed serial differs"):
            pinned.measure("emissive_spot", spectral=False)
        assert events[0]["event"] == "result", "refused at the connected event, before any reading"
    else:
        reading = pinned.measure("emissive_spot", spectral=False)
        assert (reading.serial, reading.serial_encoding) == (serial, "utf8")


def test_a_correction_can_be_named_in_a_request(tmp_path):
    """It could not be, and that was the whole defect: Configuration takes a
    parsed object while a JSON request carries a dict, so a colorimeter driven
    through the generic frontend reported UNCORRECTED XYZ with no way to ask
    otherwise."""
    from color_measurement.instrument import Configuration

    ccmx = tmp_path / "correction.ccmx"
    ccmx.write_text(
        'CCMX\n\nDESCRIPTOR "test"\nORIGINATOR "test"\nINSTRUMENT "X-Rite i1 DisplayPro"\n'
        'DISPLAY "LCD"\nKEYWORD "COLOR_REP"\nCOLOR_REP "XYZ"\n'
        "NUMBER_OF_FIELDS 3\nBEGIN_DATA_FORMAT\nXYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\n"
        "NUMBER_OF_SETS 3\nBEGIN_DATA\n1.0 0.0 0.0\n0.0 1.0 0.0\n0.0 0.0 1.0\nEND_DATA\n"
    )
    # By path, the way `spotread -X file` takes one...
    by_path = Configuration.from_options({"correction": {"path": str(ccmx)}})
    assert by_path.correction.apply((1.0, 2.0, 3.0)) == (1.0, 2.0, 3.0)
    # ...and by content, for a client that has bytes rather than a local file.
    by_text = Configuration.from_options({"correction": {"text": ccmx.read_text()}})
    assert by_text.correction == by_path.correction
    # The kind is never stated by the caller; it is the file's own identifier.
    assert by_path.correction.instrument == "X-Rite i1 DisplayPro"

    for bad in ({"correction": {}}, {"correction": {"path": str(ccmx), "text": "x"}},
                {"correction": "a-path"}):
        with pytest.raises(ValueError, match="correction"):
            Configuration.from_options(bad)
    with pytest.raises(ValueError, match="not a usable|Not a usable"):
        Configuration.from_options({"correction": {"text": "not a correction"}})


@pytest.mark.parametrize("mtype,unit", [(4, "lux s")])
def test_a_flash_reading_carries_what_its_duration_means(mtype, unit):
    """Duration is a measured quantity only for a flash, and the one field a
    caller is most likely to misuse: the values it accompanies are ALREADY
    integrated over it (xicc/xspect.h:40-50), so multiplying integrates twice.

    The per-device path warned about this and the generic path published the
    number bare, which is the worse of the two -- a plausible figure with its
    meaning left off.
    """
    from color_measurement.instrument import observation_from_patch

    measured = observation_from_patch(
        _patch(mtype=mtype, duration=0.004), _measurement(mode="ambient_flash"),
        device=_device(), operation_id="op-1", sample_id="A1",
        acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
    )
    assert dict(measured.configuration)["native_duration_seconds"] == "0.004"
    assert any("must not be multiplied" in warning for warning in measured.warnings)
    assert any(unit in warning for warning in measured.warnings)

    # Zero is the field's initialised state, so it cannot mean "no exposure".
    unreported = observation_from_patch(
        _patch(mtype=mtype, duration=0.0), _measurement(mode="ambient_flash"),
        device=_device(), operation_id="op-1", sample_id="A1",
        acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
    )
    assert dict(unreported.configuration)["native_duration_seconds"] == "0.0"
    assert any("unreported rather than a zero-length" in w for w in unreported.warnings)

    # A non-flash reading says nothing about duration it cannot support: the field
    # is documented for flash only, so it is reported and labelled, never treated
    # as an exposure.
    ordinary = observation_from_patch(
        _patch(mtype=5, duration=0.004), _measurement(), device=_device(),
        operation_id="op-1", sample_id="A1",
        acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
    )
    assert not any("must not be multiplied" in w for w in ordinary.warnings)
    assert any("not an exposure or integration time" in w for w in ordinary.warnings)


def test_every_reading_says_how_it_was_taken():
    """Three facts that belong to the READING and not to any instrument, and that
    the per-device modules each stated in their own words.

    Spot readings request instNoClamp and retain signed native XYZ. Other
    workflows record driver-defined clamping. The record identifies which
    policy applies; absent measurements are never replaced with zeros.
    """
    from color_measurement.instrument import observation_from_patch

    physical = observation_from_patch(
        _patch(), _measurement(), device=_device(), operation_id="op-1", sample_id="A1",
        acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
    )
    assert dict(physical.configuration)["native_clamping"] == "instNoClamp"
    assert not any("did not come from a physical measurement" in w for w in physical.warnings)
    # The other branch of the same policy: a strip reading keeps the driver's
    # clamping and says so, rather than inheriting the spot request.
    strip = observation_from_patch(
        _patch(), _measurement(workflow="strip", mode="reflective_strip_m0"), device=_device(),
        operation_id="op-1", sample_id="A1",
        acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
    )
    assert dict(strip.configuration)["native_clamping"] == "driver-defined"

    simulated = observation_from_patch(
        _patch(), _measurement(provenance="simulated"), device=_device(),
        operation_id="op-1", sample_id="A1",
        acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
    )
    assert any("SIMULATED" in w and "did not come from a physical measurement" in w
               for w in simulated.warnings)

    # A driver's own calibration warning travels in its own words.
    warned = observation_from_patch(
        _patch(), _measurement(calibration={
            "warning": "white tile is dusty", "remaining_calibrations": 0}),
        device=_device(), operation_id="op-1", sample_id="A1",
        acquired_at="2026-09-22T12:00:00Z", raw_backend_output="{}",
    )
    assert any("white tile is dusty" in w for w in warned.warnings)


# --- the command line ----------------------------------------------------------


def test_the_cli_reads_an_instrument_through_the_one_generic_path(tmp_path, capsys, monkeypatch):
    import io
    import sys
    monkeypatch.setattr(sys, "stdin", io.StringIO("accept\n" * 8))
    """The CLI offered only the tristimulus simulator, so the whole acquisition
    stack had no command-line entry at all.

    Nothing here names an instrument: `--device` is resolved by the same
    `instrument_binding_name` table the library uses, so a further instrument costs this
    command nothing.
    """
    from color_measurement.cli import main

    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    output = tmp_path / "reading.json"
    previous = {key: os.environ.get(key) for key in ("CM_CHART_PERSONA", "CM_CHART_FIXTURE")}
    os.environ.update(CM_CHART_PERSONA="dtp41", CM_CHART_FIXTURE="success")
    try:
        report = retained_build_report(tmp_path / "build")
        loader_calls = bind_cli_substitute(monkeypatch, worker, report)
        code = main(["acquire", "--device", "X-Rite DTP41", "--build-report", str(report),
                     "--serial", "CM-CHART-0001", "--mode", "reflective_spot",
                     "--sample-id", "patch-1", "--output", str(output)])
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    assert code == 0
    assert loader_calls == [report]
    record = json.loads(output.read_text())
    assert record["device"]["model"] == "X-Rite DTP41"
    assert record["device"]["serial"] == "CM-CHART-0001"
    assert record["sample_id"] == "patch-1"
    # A substituted instrument is SIMULATED, and the record has to keep saying so.
    assert record["provenance"] == "simulated"
    assert any("SIMULATED" in note for note in record["warnings"])
    raw = json.loads(record["raw_backend_output"])
    assert raw["build_report_sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()
    assert raw["helper_sha256"] == hashlib.sha256(Path(worker).read_bytes()).hexdigest()


def test_the_cli_refuses_an_instrument_without_a_report(capsys, monkeypatch):
    """Naming an instrument must never make this command go looking for a helper
    to run: finding one would open whatever device answered."""
    from color_measurement.cli import main

    monkeypatch.delenv("CM_ARGYLL_BUILD_REPORT", raising=False)
    assert main(["acquire", "--device", "X-Rite DTP41"]) == 1
    assert "No build report given" in capsys.readouterr().err


def test_the_cli_lists_display_names_without_enumerating_anything(capsys):
    """The full name table includes contributions without claiming construction
    or attachment. InstSpectrocam is known by name but cannot be constructed."""
    from color_measurement.argyll_capability import CONTRIBUTED_INSTRUMENTS, INSTRUMENTS
    from color_measurement.cli import main

    assert main(["devices"]) == 0
    result = json.loads(capsys.readouterr().out)
    rows = result['instruments']
    assert {row['name'] for row in rows} == INSTRUMENTS.keys() | CONTRIBUTED_INSTRUMENTS.keys()
    assert {row['name'] for row in rows if row['requires_contribution']} == (
        CONTRIBUTED_INSTRUMENTS.keys())
    assert all(type(row['requires_contribution']) is bool for row in rows)
    assert result['instruments_are'] == (
        'static name table; native construction and attachment are not queried')
    assert next(row for row in rows if row['name'] == 'instSpyder2024')['instrument'] == (
        'Datacolor Spyder 2024')


def test_reading_a_stored_chart_that_is_not_stored_is_refused_before_the_read():
    """`inst_stat_saved_readings` is a RUNTIME status (inst.h:467-468), made one so
    it can be asked before the branch is chosen -- which is what chartread.c:900-931
    does. Asking anyway spends an operator's wait to reach a driver error instead
    of the status that already answered, and the refusal names its own stage so a
    client can say why rather than showing a bare failure.

    The query on its own still answers zero normally: nothing stored is an answer.
    """
    worker = supplied_stub("COLOR_MEASUREMENT_CHART_STUB")
    meter = _meter(worker, "X-Rite DTP20", "CM-CHART-0001",
                   environment=dict(os.environ, CM_CHART_PERSONA="dtp20",
                                    CM_CHART_FIXTURE="nothing_stored"))
    assert meter.stored_readings("saved_spot") == 0
    with pytest.raises(InstrumentError) as raised:
        meter.measure_chart("saved_chart", patch_count=12, passes=4, steps=3,
                            sheets=(2, 2), chart_id=7)
    assert raised.value.stage == "no_saved_readings"


@pytest.mark.parametrize("options, refusal", [
    # The one that got through: accepted here, then `int("yes")` inside the
    # operation, after `started`, as a failed result carrying an interpreter
    # message instead of a refusal in this validator's words.
    ({"highres": "yes"}, "highres is a boolean"),
    # bool IS an int in Python, so this used to mean "average one reading". A
    # client that wired a checkbox to the wrong control was obeyed, not told.
    ({"averages": True}, "averages is an integer"),
    # These were refused, but by accident and in the wrong words -- the option is
    # known; its TYPE is wrong, and the message named the wrong fault.
    ({"averages": "5"}, "averages is an integer"),
    ({"display_type": "2"}, "display_type is a named choice identity"),
    ({"xcalstd": ["none"]}, "xcalstd is one of the calibration standard names"),
    ({"filter": 2}, "filter is one of the filter names"),
    # A boolean option is a boolean, not a truthy number: the descriptor types it.
    ({"highres": 1}, "highres is a boolean"),
    # Membership, which already worked, stated here so the type checks cannot
    # displace it.
    ({"xcalstd": "bogus"}, "Not an X-Rite calibration standard"),
    ({"filter": "bogus"}, "Not an ArgyllCMS filter"),
])
def test_an_option_of_the_wrong_type_is_refused_in_this_validators_words(options, refusal):
    """The descriptor types every option; this is where that type is enforced.

    It lives on `Configuration` rather than in the frontend's `_validate_request`
    because that is the one validator acquisition itself uses -- a second copy in
    the frontend would leave a direct `Instrument` caller unprotected and could
    drift from this one.
    """
    with pytest.raises(ValueError, match=refusal):
        Configuration.from_options(options)


def test_a_well_formed_configuration_still_passes():
    configuration = Configuration.from_options(
        {"highres": True, "averages": 3, "xcalstd": "none"})
    assert configuration.flags("reflective_spot")["highres"] == 1
    assert Configuration.from_options({}).averages == 0


def test_native_replies_join_their_facts_whole_or_not_at_all():
    """A support adapter sends one ``native_facts`` event and then each native
    reply on its own line. A reply missing, reordered, or counted differently is a
    fact dropped, so it must refuse rather than hand back a shorter list."""
    from color_measurement.instrument import native_facts
    from color_measurement.native_protocol import ProtocolError

    def reply(index, count=2, **change):
        return {"event": "native_reply", "driver": "cr250", "reply_index": index,
                "reply_count": count, "status": "OK", "code": 0, "context": f"RC {index}",
                "value": "v", **change}

    head = {"event": "native_facts", "protocol_version": 1, "provenance": "simulated",
            "driver": "cr250", "identity": {"verified": True}, "trace_overflow": False,
            "capture": {"reply_count": 2, "raw_exchange_count": 0, "record_count": 0}}
    whole = native_facts([head, reply(0), reply(1)])
    assert [r["context"] for r in whole["replies"]] == ["RC 0", "RC 1"]
    assert "event" not in whole and "provenance" not in whole
    assert native_facts([{"event": "result"}]) is None
    for broken in ([head, reply(0)],                       # one dropped
                   [head, reply(1), reply(0)],             # reordered
                   [head, reply(0), reply(1, count=3)],    # count disagrees
                   [head, reply(0), reply(1, status="MAYBE")],
                   [head, reply(0), reply(1, driver="other")],  # another driver's
                   [reply(0)]):                            # no facts at all
        with pytest.raises(ProtocolError):
            native_facts(broken)


def test_native_settings_and_writes_join_like_replies():
    """Settings and writes follow the replies, one per event, each counted in the
    head. A dropped, reordered, miscounted or uncounted item is refused, and a
    value the driver gives as null stays null rather than becoming empty text."""
    from color_measurement.instrument import native_facts
    from color_measurement.native_protocol import ProtocolError

    def setting(index, count=2, **change):
        return {"event": "native_setting", "driver": "cr250", "setting_index": index,
                "setting_count": count, "key": f"K{index}", "value": None,
                "presence": "not_applicable", "driver_set": False, **change}

    def write(index, count=1, **change):
        return {"event": "native_write", "driver": "cr250", "write_index": index,
                "write_count": count, "key": "CMF", "before": "1", "target": "0",
                "after": None, "confirmed": False, **change}

    head = {"event": "native_facts", "driver": "cr250", "identity": {}, "trace_overflow": False,
            "capture": {"reply_count": 0, "setting_count": 2, "write_count": 1,
                        "raw_exchange_count": 0, "record_count": 0}}
    whole = native_facts([head, setting(0), setting(1, value="Auto", presence="present"),
                          write(0)])
    assert [s["key"] for s in whole["settings"]] == ["K0", "K1"]
    assert whole["settings"][0]["value"] is None and whole["settings"][1]["value"] == "Auto"
    assert whole["writes"] == [{"key": "CMF", "before": "1", "target": "0",
                                "after": None, "confirmed": False}]
    uncounted = {**head, "capture": {"reply_count": 0}}
    for broken in ([head, setting(0), write(0)],                          # one dropped
                   [head, setting(1), setting(0), write(0)],              # reordered
                   [head, setting(0), setting(1, count=3), write(0)],     # miscounted
                   [head, setting(0), setting(1, presence="maybe"), write(0)],
                   [head, setting(0), setting(1), write(0, confirmed=1)],  # not a bool
                   [uncounted, write(0, count=1)],                        # uncounted
                   [setting(0)]):                                         # no head
        with pytest.raises(ProtocolError):
            native_facts(broken)


@pytest.mark.parametrize("text", ["x" * 4097, "λ\n"])
def test_generic_native_strings_keep_json_text_while_cr_uses_its_wire_contract(text):
    from color_measurement.instrument import native_facts

    head = dict(event="native_facts", driver="scripted", identity={},
                capture=dict(reply_count=0, setting_count=1))
    setting = dict(event="native_setting", driver="scripted", setting_index=0,
                   setting_count=1, key="opaque", value=text,
                   presence="present", driver_set=False)
    assert native_facts([head, setting])["settings"][0]["value"] == text
    head.update(driver="cr250", trace_overflow=False)
    head["capture"].update(raw_exchange_count=0, record_count=0)
    setting["driver"] = "cr250"
    with pytest.raises(ProtocolError):
        native_facts([head, setting])


def test_a_refused_spectrum_joins_whole_or_not_at_all():
    """A refused read's spectrum arrives in chunks; joined, it is the grid's whole
    count in order. A gap, overlap, non-finite value or short total is refused."""
    from color_measurement.instrument import native_facts
    from color_measurement.native_protocol import ProtocolError

    def chunk(index, offset, values, count=2):
        return {"event": "native_spectrum", "driver": "scripted", "spectrum_chunk_index": index,
                "spectrum_chunk_count": count, "offset": offset, "values": values}

    head = {"event": "native_facts", "driver": "scripted", "identity": {},
            "capture": {"reply_count": 0, "spectrum_chunk_count": 2,
                        "spectrum": {"start_nm": 380, "end_nm": 390, "step_nm": 5, "count": 3}}}
    whole = native_facts([head, chunk(0, 0, [1.0, 2.0]), chunk(1, 2, [3.0])])
    assert whole["capture"]["spectrum"]["values"] == [1.0, 2.0, 3.0]
    assert "spectrum_chunks" not in whole
    # A dropped chunk also shortens the total, so each case names its refusal; one chunk
    # carrying every value breaks the declared chunk count alone.
    for broken, refusal in (
        ([head, chunk(0, 0, [1.0, 2.0])],                        # chunk dropped
         "spectrum_chunks are incomplete"),
        ([head, chunk(0, 0, [1.0, 2.0, 3.0])],                   # all in one of two
         "spectrum_chunks are incomplete"),
        ([head, chunk(0, 0, [1.0, 2.0]), chunk(1, 1, [3.0])],    # overlap
         "spectrum chunks are malformed or out of order"),
        ([head, chunk(0, 0, [1.0, None]), chunk(1, 2, [3.0])],   # not finite
         "spectrum chunks are malformed or out of order"),
        ([head, chunk(0, 0, [1.0]), chunk(1, 1, [3.0])],         # short total
         "spectrum is incomplete"),
        ([dict(head, capture=dict(head["capture"], spectrum=dict(  # over one chunk's 100
            head["capture"]["spectrum"], count=102))),
          chunk(0, 0, [1.0] * 101), chunk(1, 101, [3.0])],
         "spectrum chunks are malformed or out of order"),
    ):
        with pytest.raises(ProtocolError, match=refusal):
            native_facts(broken)


def test_completed_calibration_does_not_overrule_a_remaining_driver_requirement():
    from color_measurement.argyll_capability import CALIBRATION_TYPES
    from color_measurement.instrument import calibration_context

    outcome = {"status_source": "driver", "performed": True,
               "remaining_calibrations": CALIBRATION_TYPES["emis_offset"]}
    context = calibration_context(outcome)
    assert context.status == "required"
    assert context.detail.startswith(
        "Calibration negotiation completed; the driver's subsequent assessment")
    assert outcome["remaining_calibrations"] == CALIBRATION_TYPES["emis_offset"]


def test_unbound_acquisition_refuses_before_launch(tmp_path, monkeypatch):
    from color_measurement import instrument as api

    helper = tmp_path / "unbound-worker"
    helper.write_bytes(b"Never executable")
    monkeypatch.setattr(api, "HelperSession",
                        lambda *a, **k: pytest.fail("An unbound helper was launched"))
    with pytest.raises(ProtocolError, match="authenticated helper SHA-256"):
        Instrument(helper, COLORIMETER).measure("emissive_spot", spectral=False)


def test_acquisition_authenticates_the_actual_launch_bytes(tmp_path, monkeypatch):
    from color_measurement import instrument as api

    helper = tmp_path / "bound-worker"
    helper.write_bytes(b"Bound launch input")
    digest = hashlib.sha256(helper.read_bytes()).hexdigest()
    calls = []
    monkeypatch.setattr(api, "HelperSession", lambda *a, **k: calls.append((a, k)))
    meter = Instrument(helper, COLORIMETER, expected_helper_sha256=digest)
    meter._open_session("emissive_spot", False, [], {})
    assert len(calls) == 1 and calls[0][0][0][0] == str(helper)
    helper.write_bytes(b"Changed launch input")
    with pytest.raises(ProtocolError, match="changed since build binding"):
        meter._open_session("emissive_spot", False, [], {})
    assert len(calls) == 1


def test_cli_refuses_a_report_worker_mismatch_before_launch(tmp_path, monkeypatch, capsys):
    from color_measurement import argyll_description as api
    from color_measurement.cli import main

    report = retained_build_report(tmp_path / "build")
    report.with_name(native_worker_name("generic-worker-never-run")).write_bytes(b"Other worker")
    monkeypatch.setattr(api, "HelperSession",
                        lambda *a, **k: pytest.fail("Invalid report launched a worker"))
    assert main(["acquire", "--device", "instDTP41", "--build-report", str(report)]) == 1
    captured = capsys.readouterr()
    assert "does not match the retained build report" in captured.err
    assert captured.out == ""


def test_instrument_cli_applies_explicit_white_through_shared_processing(tmp_path,
                                                                       monkeypatch, capsys):
    from color_measurement.cli import main

    report = retained_build_report(tmp_path / "build")
    acquired = []

    def measure(self, mode, **kwargs):
        acquired.append((self, mode, kwargs))
        return _measurement(instrument=self.instrument, serial="SN-1", mode=mode,
                            provenance="simulated", spectral=False,
                            patches=(_patch(xyz=(21.5, 24.0, 9.75)),))

    monkeypatch.setattr(Instrument, "measure", measure)
    assert main(["acquire", "--device", "instDTP41", "--mode", "reflective_spot",
                 "--xyz-only", "--build-report", str(report),
                 "--white", "21.5", "24", "9.75"]) == 0
    (meter, mode, options), = acquired
    assert meter.expected_helper_sha256 == hashlib.sha256(
        report.with_name(native_worker_name("generic-worker-never-run")).read_bytes()).hexdigest()
    assert mode == "reflective_spot" and options["spectral"] is False
    record = json.loads(capsys.readouterr().out)
    assert record["provenance"] == "simulated"
    assert record["backend_reported"]["xyz"]["values"] == [21.5, 24.0, 9.75]
    lab = record["project_computed"]["lab"]
    assert lab["algorithm"] == "xyz_to_lab" and lab["status"] == "ok"
    assert lab["reference_white"] == [21.5, 24.0, 9.75]
    assert lab["values"] == pytest.approx([100, 0, 0])


@pytest.mark.parametrize("component", ["0", "-1", "nan", "inf"])
def test_invalid_instrument_cli_white_refuses_before_binding(component, monkeypatch, capsys):
    from color_measurement.cli import main
    from color_measurement.discovery import InstrumentBuild

    monkeypatch.setattr(InstrumentBuild, "from_report",
                        lambda *a, **k: pytest.fail("Invalid white reached acquisition binding"))
    assert main(["acquire", "--device", "instDTP41", "--mode", "reflective_spot",
                 "--white", component, "100", "120"]) == 1
    captured = capsys.readouterr()
    assert "Reference white components must be finite and positive" in captured.err
    assert captured.out == ""


def test_instrument_cli_white_preserves_spectrum_only_stdout_and_save(tmp_path,
                                                                    monkeypatch, capsys):
    from color_measurement.cli import main
    from color_measurement.native_protocol import NativeSpectrum

    report = retained_build_report(tmp_path / "build")
    output = tmp_path / "spectrum-only.json"
    events = ({"kind": "fixture", "note": "intercepted spectrum-only acquisition"},)

    def measure(self, mode, **kwargs):
        assert mode == "reflective_spot_m1" and kwargs["spectral"] is True
        return _measurement(
            instrument=self.instrument, serial="SN-1", mode=mode, provenance="simulated",
            patches=(_patch(xyz=None, mcond=1, spectrum=NativeSpectrum(
                start_nm=400.0, end_nm=410.0, norm=100.0, samples=(50.0, 25.0, 12.5))),),
            events=events,
        )

    monkeypatch.setattr(Instrument, "measure", measure)
    assert main([
        "acquire", "--device", "instI1Pro3", "--mode", "reflective_spot_m1",
        "--build-report", str(report), "--white", "96.42", "100", "82.49",
        "--output", str(output),
    ]) == 0
    captured = capsys.readouterr()
    record = json.loads(captured.out)
    assert json.loads(output.read_text()) == record
    assert record["provenance"] == "simulated"
    assert record["backend_reported"]["xyz"] is None
    assert record["backend_reported"]["spectrum"] == {
        "wavelengths_nm": [400.0, 405.0, 410.0],
        "values": [50.0, 25.0, 12.5], "quantity": "reflectance", "unit": "%",
    }
    assert record["project_computed"] == {"xyy": None, "lab": None}
    warning = (
        "Lab unavailable: the instrument returned no native XYZ; "
        "this acquisition does not infer missing native XYZ from a spectrum"
    )
    assert warning in record["warnings"] and warning in captured.err
    raw = json.loads(record["raw_backend_output"])
    assert raw["events"] == list(events)
    assert raw["build_report_sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()
    assert raw["helper_sha256"] == hashlib.sha256(
        report.with_name(native_worker_name("generic-worker-never-run")).read_bytes()).hexdigest()


def cr_qualified_facts(*, verdict="consistent", published=True, serial="SN-1"):
    consistent = verdict == "consistent"
    return dict(driver="cr250", identity=dict(verified=True, model="CR-250", serial=serial),
                trace_overflow=False,
                capture=dict(valid=published, reply_count=0, raw_exchange_count=0, record_count=0,
                             xyz=[1, -2, 3],
                             unit_check=dict(verdict=verdict), spectrum=dict(
                                 start_nm=380, end_nm=410, step_nm=10, count=4,
                                 values=[0.1, -0.2, 0, 0.4], unit=None,
                                 inferred_unit="W/(sr.m^2.nm)" if consistent else None,
                                 published_unit="mW/(m^2.sr.nm)" if published else None,
                                 published_scale=1000 if published else None,
                                 unit_basis="same_capture_consistency_under_driver_model"
                                 if consistent else "unverified")))


@pytest.mark.parametrize("case", [
    "missing_unit", "missing_inferred_unit", "missing_published_unit", "missing_published_scale",
    "wrong_published_unit", "invalid_publication", "nonboolean_valid", "short_count",
    "long_count", "nonpositive_start", "nonfinite_sample",
])
def test_cr_native_spectrum_refuses_incomplete_or_contradictory_capture_contract(case):
    from color_measurement.instrument import native_facts

    facts = cr_qualified_facts()
    capture = facts["capture"]
    grid = capture["spectrum"]
    if case.startswith("missing_"):
        facts = cr_qualified_facts(verdict="not_checked", published=False)
        del facts["capture"]["spectrum"][case.removeprefix("missing_")]
    elif case == "wrong_published_unit":
        grid["published_unit"] = "W/(sr.m^2.nm)"
    elif case == "invalid_publication":
        capture["valid"] = False
    elif case == "nonboolean_valid":
        capture["valid"] = 1
    elif case in {"short_count", "long_count"}:
        count = 3 if case == "short_count" else 602
        grid.update(count=count, values=[0.1] * count, step_nm=1, end_nm=380 + count - 1)
    elif case == "nonpositive_start":
        grid.update(start_nm=0, end_nm=30)
    else:
        grid["values"][1] = float("nan")
    with pytest.raises(ProtocolError):
        native_facts([dict(facts, event="native_facts")])


@pytest.mark.parametrize("case", ["unknown_model", "noncr_model", "empty_serial"])
def test_cr_publication_refuses_foreign_or_unattributed_native_capture(case):
    from color_measurement.instrument import observation_from_patch
    from color_measurement.native_protocol import NativeSpectrum

    facts = cr_qualified_facts()
    model = "Colorimetry Research CR-250"
    if case == "unknown_model":
        model = "Unknown instrument"
    elif case == "noncr_model":
        model = "X-Rite i1 Pro"
    else:
        facts["identity"]["serial"] = ""
    reason = "no reported serial" if case == "empty_serial" else "different model binding"
    with pytest.raises(ProtocolError, match="Retained native capture .*" + reason):
        observation_from_patch(
            _patch(mtype=1, spectrum=NativeSpectrum(380, 410, 1, (100, -200, 0, 400))),
            _measurement(mode="emissive_spot", native_facts=facts,
                         serial=None if case == "empty_serial" else "SN-1"),
            device=_device(model=model, serial=None), operation_id="op-foreign",
            sample_id="raw", acquired_at="2026-10-14T00:00:00Z", raw_backend_output="{}",
        )


@pytest.mark.parametrize("case", ["norm", "start", "end"])
def test_cr_publication_requires_the_original_norm_and_reported_endpoints(case):
    from color_measurement.instrument import observation_from_patch
    from color_measurement.native_protocol import NativeSpectrum

    start, end, norm, samples = 380, 410, 1, (100, -200, 0, 400)
    if case == "norm":
        norm = 2
    elif case == "start":
        start = 381
    else:
        end = 411
    with pytest.raises(ProtocolError):
        observation_from_patch(
            _patch(mtype=1, spectrum=NativeSpectrum(start, end, norm, samples)),
            _measurement(mode="emissive_spot", native_facts=cr_qualified_facts()),
            device=_device(model="Colorimetry Research CR-250"), operation_id="op-grid",
            sample_id="raw", acquired_at="2026-10-14T00:00:00Z", raw_backend_output="{}",
        )


def test_cr_refused_capture_requires_an_original_xyz_triple_before_retaining_it():
    from color_measurement.instrument import observation_from_native_capture

    facts = cr_qualified_facts(published=False)
    facts["capture"]["xyz"] = [1, 2]
    with pytest.raises(ProtocolError, match="XYZ"):
        observation_from_native_capture(
            facts, device=_device(model="Colorimetry Research CR-250"), operation_id="op-shape",
            sample_id="raw", acquired_at="2026-10-14T00:00:00Z", mode="emissive_spot",
            provenance="simulated", raw_backend_output="{}",
        )


@pytest.mark.parametrize("verdict", ["consistent", "not_checked", "inconclusive", "mismatch"])
def test_cr_native_bins_remain_unknown_when_argyll_publication_is_refused(verdict):
    from color_measurement.instrument import observation_from_native_capture
    from color_measurement.models import ComputedValues

    facts = cr_qualified_facts(verdict=verdict, published=False)
    before = json.dumps(facts, sort_keys=True)
    observation = observation_from_native_capture(
        facts, device=_device(model="Colorimetry Research CR-250"), operation_id="op-raw",
        sample_id="raw", acquired_at="2026-10-14T00:00:00Z", mode="emissive_spot",
        provenance="simulated", raw_backend_output=before,
    )
    assert observation.backend_reported.xyz.values == (1, -2, 3)
    assert observation.backend_reported.xyz.unit == "unverified native XYZ units"
    assert observation.backend_reported.spectrum.values == (0.1, -0.2, 0, 0.4)
    assert observation.backend_reported.spectrum.unit == "unverified native spectral density units"
    assert observation.calibration.status == "unknown"
    assert observation.project_computed == ComputedValues()
    assert json.dumps(facts, sort_keys=True) == before


@pytest.mark.parametrize("count", [2, 10000])
def test_cr_raw_grid_bounds_keep_native_bins_without_qualifying_publication(count):
    from color_measurement.instrument import native_facts, observation_from_native_capture

    facts = cr_qualified_facts(verdict="not_checked", published=False)
    spectrum = facts["capture"]["spectrum"]
    spectrum.update(count=count, start_nm=100, step_nm=0.05,
                    end_nm=100 + (count - 1) * 0.05, values=[-0.1] * count)
    values = spectrum.pop("values")
    nchunks = (count + 99) // 100
    facts["capture"]["spectrum_chunk_count"] = nchunks
    events = [dict(facts, event="native_facts"), *(dict(
        event="native_spectrum", driver="cr250", spectrum_chunk_index=index,
        spectrum_chunk_count=nchunks, offset=index * 100,
        values=values[index * 100:(index + 1) * 100],
    ) for index in range(nchunks))]
    joined = native_facts(events)
    observation = observation_from_native_capture(
        joined, device=_device(model="Colorimetry Research CR-250"), operation_id="op-raw",
        sample_id="raw", acquired_at="2026-10-14T00:00:00Z", mode="emissive_spot",
        provenance="simulated", raw_backend_output="{}",
    )
    assert observation.backend_reported.spectrum.values == tuple(values)
    assert observation.backend_reported.spectrum.unit == "unverified native spectral density units"
    assert joined["capture"]["spectrum"]["published_scale"] is None
    assert joined["capture"]["spectrum"]["inferred_unit"] is None
    events[-1]["values"].append(-0.1)
    with pytest.raises(ProtocolError):
        native_facts(events)


@pytest.mark.parametrize("change", [
    None, "serial", "model", "verified", "firmware", "missing_field",
])
def test_cr_quarantined_capture_snapshot_attributes_only_refused_originals(change):
    from color_measurement.instrument import observation_from_native_capture, observation_from_patch

    facts = cr_qualified_facts(verdict="not_checked", published=False)
    facts["capture"]["identity"] = dict(
        verified=True, model="CR-250", serial="SN-1", firmware="1.34",
        firmware_components=[1, 34], instrument_type=2,
    )
    facts["identity"] = dict(verified=False, model=None, serial=None)
    facts["link_uncertain"] = True
    snapshot = facts["capture"]["identity"]
    if change == "missing_field":
        del snapshot["instrument_type"]
    elif change is not None:
        snapshot[change] = {"serial": "other", "model": "CR-300", "verified": False,
                            "firmware": "\x00bad"}[change]
    arguments = dict(
        device=_device(model="Colorimetry Research CR-250"), operation_id="op-raw",
        sample_id="raw", acquired_at="2026-10-14T00:00:00Z", raw_backend_output="{}",
    )
    if change is None:
        observation = observation_from_native_capture(
            facts, mode="emissive_spot", provenance="simulated", **arguments,
        )
        assert observation.backend_reported.xyz.values == (1, -2, 3)
        assert facts["identity"]["verified"] is False and facts["link_uncertain"] is True
    else:
        with pytest.raises(ProtocolError):
            observation_from_native_capture(
                facts, mode="emissive_spot", provenance="simulated", **arguments,
            )
    with pytest.raises(ProtocolError, match="identity was not verified"):
        observation_from_patch(
            _patch(mtype=1), _measurement(mode="emissive_spot", spectral=False, native_facts=facts),
            **arguments,
        )


@pytest.mark.parametrize("changes", [
    dict(count=2, end_nm=390), dict(count=602, step_nm=1, end_nm=981),
    dict(start_nm=100, end_nm=130), dict(start_nm=1080, end_nm=1110),
    dict(step_nm=0.05, end_nm=380.15),
])
def test_cr_wider_raw_domain_cannot_claim_inferred_or_published_units(changes):
    from color_measurement.instrument import native_facts

    facts = cr_qualified_facts()
    facts["capture"]["spectrum"].update(changes)
    facts["capture"]["spectrum"]["values"] = [0.1] * facts["capture"]["spectrum"]["count"]
    with pytest.raises(ProtocolError, match="publication domain"):
        native_facts([dict(facts, event="native_facts")])


@pytest.mark.parametrize("case", [
    "relative_tolerance", "end_subtraction", "start_collapse", "late_collapse",
    "overflow", "one_bin",
])
def test_cr_declared_raw_grid_survives_refused_mapping_without_publication(case):
    from color_measurement.instrument import native_facts, observation_from_native_capture

    facts = cr_qualified_facts(verdict="not_checked", published=False)
    grid = facts["capture"]["spectrum"]
    cases = {
        "relative_tolerance": dict(start_nm=500, end_nm=500.000002, step_nm=1e-6, count=2),
        "end_subtraction": dict(start_nm=math.nextafter(1, 0), end_nm=1, step_nm=2**-54, count=2),
        "start_collapse": dict(
            start_nm=1, end_nm=math.nextafter(1, math.inf), step_nm=2**-54, count=2),
        "late_collapse": dict(start_nm=500, end_nm=500 + 3 * 0.75 * math.ulp(500),
                              step_nm=0.75 * math.ulp(500), count=4),
        "overflow": dict(start_nm=1e308, end_nm=1.5e308, step_nm=1e308, count=3),
        "one_bin": dict(start_nm=500, end_nm=500, step_nm=1, count=1),
    }
    grid.update(cases[case])
    grid["values"] = [1, -1] if grid["count"] == 2 else [1] * grid["count"]
    joined = native_facts([dict(facts, event="native_facts")])
    observation = observation_from_native_capture(
        joined, device=_device(model="Colorimetry Research CR-250"), operation_id="op-raw",
        sample_id="raw", acquired_at="2026-10-14T00:00:00Z", mode="emissive_spot",
        provenance="simulated", raw_backend_output="{}",
    )
    if case in {"relative_tolerance", "end_subtraction"}:
        assert observation.backend_reported.spectrum.values == (1, -1)
    else:
        assert observation.backend_reported.spectrum is None
        assert (dict(observation.configuration)["native_spectrum_mapping_refusal"]
                in observation.warnings)
        assert json.loads(dict(observation.configuration)["native_facts_json"])[
            "capture"]["spectrum"]["values"] == grid["values"]
    assert observation.backend_reported.xyz.values == (1, -2, 3)
    assert joined["capture"]["spectrum"]["end_nm"] == cases[case]["end_nm"]
    assert joined["capture"]["spectrum"]["published_unit"] is None


def test_cr_published_density_is_model_inferred_and_derived_science_keeps_that_basis():
    from dataclasses import asdict

    from color_measurement.instrument import observation_from_patch
    from color_measurement.native_protocol import NativeSpectrum
    from color_measurement.qualified_science import derive_readings
    from color_measurement.reading_dataset import reading_key

    facts = cr_qualified_facts()
    observation = observation_from_patch(
        _patch(mtype=1, spectrum=NativeSpectrum(380, 410, 1, (100, -200, 0, 400))),
        _measurement(mode="emissive_spot", native_facts=facts),
        device=_device(model="Colorimetry Research CR-250"), operation_id="op-inferred",
        sample_id="inferred", acquired_at="2026-10-14T00:00:00Z", raw_backend_output="{}",
    )
    assert observation.backend_reported.spectrum.unit == "mW/(m^2 sr nm)"
    basis = dict(observation.configuration)["units_source"]
    assert "inferred" in basis and "not manufacturer-confirmed" in basis
    assert basis in observation.warnings
    identity = dict(operation_id=observation.operation_id,
                    observation_id=observation.observation_id)
    key = reading_key(identity)
    row = dict(saved=True, observation=asdict(observation), reading_ref=identity)
    derived = derive_readings({key: row}, [key], dict(method="spectral_xyz", context=dict(
        observer="CIE1931_2", wavelength_convention="standard-air-nm", illuminant=None,
        illuminant_id=None, nonfluorescent_reflectance=False,
    )))["by_source"][key]
    assert derived["reason"] is None
    assert derived["result"]["input_quantity_basis"] == basis
    assert facts["capture"]["spectrum"]["values"] == [0.1, -0.2, 0, 0.4]


@pytest.mark.parametrize("field,value", [
    ("unit", "W/(sr.m^2.nm)"), ("published_scale", 1), ("inferred_unit", None),
    ("unit_basis", "vendor_confirmed"), ("step_nm", 11), ("values", [0.1]),
])
def test_cr_current_spectral_dto_refuses_contradictory_unit_or_native_source(field, value):
    from color_measurement.instrument import native_facts

    facts = cr_qualified_facts()
    facts["capture"]["spectrum"][field] = value
    with pytest.raises(ProtocolError):
        native_facts([dict(facts, event="native_facts")])


@pytest.mark.parametrize("end_nm,accepted", [
    (410.000001, True), (410.00001, False), (412.5, False), (407.5, False),
])
def test_cr_mapping_uses_current_tolerance_and_keeps_raw_originals(end_nm, accepted):
    from color_measurement.instrument import native_facts, observation_from_native_capture

    facts = cr_qualified_facts(verdict="not_checked", published=False)
    facts["capture"]["spectrum"]["end_nm"] = end_nm
    assert (native_facts([dict(facts, event="native_facts")])["capture"]["spectrum"]["end_nm"]
            == end_nm)
    observation = observation_from_native_capture(
        facts, device=_device(model="Colorimetry Research CR-250"), operation_id="op-raw",
        sample_id="raw", acquired_at="2026-10-14T00:00:00Z", mode="emissive_spot",
        provenance="simulated", raw_backend_output="{}",
    )
    if accepted:
        assert observation.backend_reported.spectrum.values == (0.1, -0.2, 0, 0.4)
    else:
        assert observation.backend_reported.spectrum is None
        assert ("grid is inconsistent"
                in dict(observation.configuration)["native_spectrum_mapping_refusal"])
    assert observation.backend_reported.xyz.values == (1, -2, 3)
    assert json.loads(dict(observation.configuration)["native_facts_json"])[
        "capture"]["spectrum"]["end_nm"] == end_nm


@pytest.mark.parametrize("change", ["unverified", "wrong_serial", "wrong_model"])
def test_cr_retention_attributes_only_originals_it_would_keep(change):
    from color_measurement.instrument import observation_from_native_capture

    facts = cr_qualified_facts(published=False)
    if change == "unverified":
        facts["identity"]["verified"] = False
    elif change == "wrong_serial":
        facts["identity"]["serial"] = "SN-OTHER"
    else:
        facts["identity"]["model"] = "CR-100"
    retain = dict(device=_device(model="Colorimetry Research CR-250", serial="SN-1"),
                  operation_id="op-raw", sample_id="raw", acquired_at="2026-10-14T00:00:00Z",
                  mode="emissive_spot", provenance="simulated", raw_backend_output="{}")
    reason = {"unverified": "identity was not verified",
              "wrong_serial": "different reported serial",
              "wrong_model": "different model binding"}[change]
    with pytest.raises(ProtocolError, match="Retained native capture .*" + reason):
        observation_from_native_capture(facts, **retain)
    # A failure before any capture leaves nothing to retain, so nothing is attributed.
    facts["capture"].update(xyz=None, spectrum=None)
    assert observation_from_native_capture(facts, **retain) is None


@pytest.mark.parametrize("samples", [(100, -200, 0, 401), (0.1, -0.2, 0, 0.4)])
def test_cr_ipatch_cannot_relabel_or_rescale_a_different_native_spectrum(samples):
    from color_measurement.instrument import observation_from_patch
    from color_measurement.native_protocol import NativeSpectrum

    with pytest.raises(ProtocolError, match="retained native source"):
        observation_from_patch(
            _patch(mtype=1, spectrum=NativeSpectrum(380, 410, 1, samples)),
            _measurement(mode="emissive_spot", native_facts=cr_qualified_facts()),
            device=_device(model="Colorimetry Research CR-250"), operation_id="op",
            sample_id="raw", acquired_at="2026-10-14T00:00:00Z", raw_backend_output="{}",
        )


@pytest.mark.parametrize("verdict", ["not_checked", "inconclusive", "mismatch"])
def test_cr_unknown_or_failed_basis_cannot_claim_successful_density_publication(verdict):
    from color_measurement.instrument import native_facts

    facts = cr_qualified_facts(verdict=verdict, published=True)
    with pytest.raises(ProtocolError, match="density and inferred publication"):
        native_facts([dict(facts, event="native_facts")])


def test_cr_model_demo_keeps_synthetic_backend_separate_from_native_capture_admission():
    from dataclasses import replace

    from color_measurement.instrument import observation_from_patch
    from color_measurement.native_protocol import NativeSpectrum

    patch = _patch(mtype=1, spectrum=NativeSpectrum(380, 410, 1, (100, 200, 300, 400)))
    measurement = replace(_measurement(mode="emissive_spot"), provenance="simulated")
    native_device = _device(model="Colorimetry Research CR-250")
    arguments = dict(operation_id="synthetic-op", sample_id="palette",
                     acquired_at="2026-10-14T00:00:00Z",
                     raw_backend_output="{}")
    with pytest.raises(ProtocolError, match="no current native identity/basis"):
        observation_from_patch(patch, measurement, device=native_device, **arguments)
    demo_device = replace(native_device, backend="argyll-demo")
    observation = observation_from_patch(patch, measurement, device=demo_device, **arguments)
    assert observation.device.backend == "argyll-demo"
    assert observation.backend_reported.spectrum.values == patch.spectrum.samples
    assert any("SIMULATED" in warning for warning in observation.warnings)
    with pytest.raises(ProtocolError, match="explicit simulated provenance"):
        observation_from_patch(
            patch, replace(measurement, provenance="physical"), device=demo_device, **arguments,
        )
    with pytest.raises(ProtocolError, match="cannot claim native CR capture facts"):
        observation_from_patch(
            patch, replace(_measurement(mode="emissive_spot", native_facts=cr_qualified_facts()),
                           provenance="simulated"),
            device=demo_device, **arguments,
        )


def test_cr100_native_xyz_only_refusal_preserves_xyz_without_fabricating_spectrum():
    from color_measurement.instrument import observation_from_native_capture

    facts = cr_qualified_facts(published=False)
    facts["identity"]["model"] = "CR-100"
    facts["capture"]["spectrum"] = None
    observation = observation_from_native_capture(
        facts, device=_device(model="Colorimetry Research CR-100"), operation_id="op-raw",
        sample_id="raw", acquired_at="2026-10-14T00:00:00Z", mode="emissive_spot",
        provenance="simulated", raw_backend_output="{}",
    )
    assert observation.backend_reported.xyz.values == (1, -2, 3)
    assert observation.backend_reported.spectrum is None


@pytest.mark.parametrize("case", ["missing_spectrum", "missing_facts", "missing_grid",
                                 "raw_only", "unverified", "wrong_model", "wrong_serial"])
def test_cr_publication_requires_matching_bound_identity_and_both_publication_directions(case):
    from color_measurement.instrument import observation_from_patch
    from color_measurement.native_protocol import NativeSpectrum

    facts = cr_qualified_facts()
    spectrum = NativeSpectrum(380, 410, 1, (100, -200, 0, 400))
    if case == "missing_spectrum":
        spectrum = None
    elif case == "missing_facts":
        facts = None
    elif case == "missing_grid":
        facts["capture"]["spectrum"] = None
    elif case == "raw_only":
        facts["capture"]["spectrum"].update(published_unit=None, published_scale=None)
    elif case == "unverified":
        facts["identity"]["verified"] = False
    elif case == "wrong_model":
        facts["identity"]["model"] = "CR-100"
    else:
        facts["identity"]["serial"] = "OTHER-DEVICE"
    with pytest.raises(ProtocolError):
        observation_from_patch(
            _patch(mtype=1, spectrum=spectrum),
            _measurement(instrument="Colorimetry Research CR-250", mode="emissive_spot",
                         native_facts=facts),
            device=_device(model="Colorimetry Research CR-250"), operation_id="op",
            sample_id="raw", acquired_at="2026-10-14T00:00:00Z", raw_backend_output="{}",
        )


def test_cr_consistent_raw_capture_can_keep_inference_without_claiming_spectral_publication():
    from color_measurement.instrument import native_facts, observation_from_patch

    facts = cr_qualified_facts()
    facts["capture"]["spectrum"].update(published_unit=None, published_scale=None)
    checked = native_facts([dict(facts, event="native_facts")])
    assert checked["capture"]["spectrum"]["inferred_unit"] == "W/(sr.m^2.nm)"
    assert checked["capture"]["spectrum"]["published_scale"] is None
    observation = observation_from_patch(
        _patch(mtype=1), _measurement(instrument="Colorimetry Research CR-250",
                                    mode="emissive_spot", spectral=False, native_facts=facts),
        device=_device(model="Colorimetry Research CR-250"), operation_id="op",
        sample_id="raw", acquired_at="2026-10-14T00:00:00Z", raw_backend_output="{}",
    )
    assert observation.backend_reported.spectrum is None
    assert observation.backend_reported.xyz.unit == "cd/m^2"


def test_cr100_explicit_xyz_only_publication_keeps_normal_native_xyz_contract():
    from color_measurement.instrument import observation_from_patch

    facts = cr_qualified_facts()
    facts["identity"]["model"] = "CR-100"
    facts["capture"]["spectrum"] = None
    observation = observation_from_patch(
        _patch(mtype=1), _measurement(instrument="Colorimetry Research CR-100",
                                    mode="emissive_spot", spectral=False, native_facts=facts),
        device=_device(model="Colorimetry Research CR-100"), operation_id="op",
        sample_id="raw", acquired_at="2026-10-14T00:00:00Z", raw_backend_output="{}",
    )
    assert observation.backend_reported.spectrum is None
    assert observation.backend_reported.xyz.unit == "cd/m^2"
