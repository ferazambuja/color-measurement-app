"""Analytical native-XYZ comparisons; no instrument or display qualification."""

import json
import math
import sys
from dataclasses import FrozenInstanceError, asdict, replace
from fractions import Fraction

import pytest

from color_measurement.argyll_capability import quantity_contract
from color_measurement.models import XYZ
from color_measurement.processing import compare_xyz
from color_measurement.science import ColorDomainError


def native(values=(1, 1, 1), **context):
    return XYZ(tuple(values), **{
        "unit": "cd/m^2", "scale": "absolute; Y is luminance",
        "observer": "CIE1931_2", "illuminant": "none (emissive)", **context,
    })


@pytest.mark.parametrize("factor,expected", [(1, 0), (2, 100), (0.5, -50), (-1, -200)])
def test_signed_native_y_change_and_common_scaling(factor, expected):
    reference = native()
    sample = native((factor,) * 3)
    before = asdict(reference), asdict(sample)
    result = compare_xyz(reference, sample)
    assert result.y_percent == expected and result.uv_distance == 0
    assert result.y_reason is result.uv_reason is None
    assert (asdict(reference), asdict(sample)) == before
    assert (result.y_unit, result.uv_unit) == ("%", "1")
    json.dumps(asdict(result), allow_nan=False)
    with pytest.raises(FrozenInstanceError):
        result.y_percent = 12


def test_coordinate_distance_has_an_independent_rational_vector_and_swap_symmetry():
    # (u',v') are (0,3/5) and (1/4,9/16); the difference is (20,-3)/80.
    reference, sample = native((0, 1, 0)), native((1, 1, 0))
    expected = math.sqrt(409) / 80
    result = compare_xyz(reference, sample)
    assert result.uv_distance == pytest.approx(expected, rel=2e-15)
    assert result.y_percent == 0
    assert compare_xyz(sample, reference).uv_distance == result.uv_distance
    doubled = native((2, 2, 0))
    assert compare_xyz(reference, doubled).y_percent == 100
    assert compare_xyz(doubled, reference).y_percent == -50


def test_integer_y_difference_is_retained_before_float_division():
    reference = native((0, 2**60, 0))
    sample = native((0, 2**60 + 1, 0))
    assert compare_xyz(reference, sample).y_percent == float(Fraction(100, 2**60))


@pytest.mark.parametrize("values", [(0, 0, 0), (3, 0, -1)])
def test_undefined_chromaticity_does_not_erase_y_change(values):
    result = compare_xyz(native(), native(values))
    assert result.y_percent == -100 and result.y_reason is None
    assert result.uv_distance is None and "is zero" in result.uv_reason


@pytest.mark.parametrize("values", [(1, 0, 0), (-1, -1, -1)])
def test_nonpositive_reference_y_does_not_erase_coordinate_distance(values):
    result = compare_xyz(native(values), native(tuple(2 * v for v in values)))
    assert result.y_percent is None and "positive reference Y" in result.y_reason
    assert result.uv_distance == 0 and result.uv_reason is None


def test_tiny_positive_reference_has_no_cutoff():
    tiny = math.ulp(0.0)
    result = compare_xyz(native((0, tiny, 0)), native((0, 2 * tiny, 0)))
    assert result.y_percent == 100 and result.uv_distance == 0


def test_y_overflow_is_independent_of_coordinate_availability():
    result = compare_xyz(native((0, math.ulp(0.0), 0)), native((0, 1, 0)))
    assert result.y_percent is None and result.y_reason
    assert result.uv_distance == 0 and result.uv_reason is None
    json.dumps(asdict(result), allow_nan=False)


def test_coordinate_overflow_is_independent_of_y_availability():
    reference = native((0, sys.float_info.max, 0))
    result = compare_xyz(reference, reference)
    assert result.y_percent == 0 and result.y_reason is None
    assert result.uv_distance is None and result.uv_reason
    json.dumps(asdict(result), allow_nan=False)


@pytest.mark.parametrize("reference,sample", [(None, native()), (native(), None), (None, None)])
def test_missing_xyz_remains_unavailable(reference, sample):
    result = compare_xyz(reference, sample)
    assert result.y_percent is result.uv_distance is None
    assert result.y_reason and result.uv_reason


@pytest.mark.parametrize(
    "field,value", [("unit", "lux"), ("scale", "relative"),
                    ("observer", "CIE1964_10"), ("illuminant", "D50")],
)
def test_each_declared_context_mismatch_refuses_both_metrics(field, value):
    result = compare_xyz(native(), replace(native(), **{field: value}))
    assert result.y_percent is result.uv_distance is None
    assert "equal unit, scale, observer and illuminant" in result.y_reason
    assert result.uv_reason == result.y_reason


@pytest.mark.parametrize("types", [(1, 2), (1, 3), (2, 4), (5, 6)])
def test_authoritative_quantity_contexts_do_not_mix_even_with_equal_units(types):
    records = []
    for kind in types:
        contract = quantity_contract(kind)
        records.append(native(unit=contract["xyz_unit"], scale=contract["scale"],
                              illuminant=contract["illuminant"]))
    if types == (5, 6):
        assert records[0].unit == records[1].unit == "1"
    result = compare_xyz(*records)
    assert result.y_percent is result.uv_distance is None
    assert result.y_reason and result.uv_reason


@pytest.mark.parametrize("kind", [2, 3, 4, 5, 6])
def test_native_y_is_not_relabeled_as_luminance(kind):
    contract = quantity_contract(kind)
    reference = native(unit=contract["xyz_unit"], scale=contract["scale"],
                       illuminant=contract["illuminant"])
    assert compare_xyz(reference, replace(reference, values=(2, 2, 2))).y_percent == 100


@pytest.mark.parametrize("value", [False, 1, (1, 1, 1), {"values": [1, 1, 1]}, "XYZ"])
def test_comparator_requires_typed_records(value):
    with pytest.raises(ColorDomainError, match="XYZ records"):
        compare_xyz(value, native())
    with pytest.raises(ColorDomainError, match="XYZ records"):
        compare_xyz(native(), value)


@pytest.mark.parametrize("value", [True, math.nan, math.inf, -math.inf, 10**1000])
def test_invalid_components_cannot_enter_the_typed_comparator(value):
    with pytest.raises(ValueError, match="finite real numbers"):
        native((1, value, 1))


def test_full_color_independent_signed_breakdown_and_swap():
    from color_measurement.processing import compare_color

    def xyz(lightness, a, b):
        # Independent cube-branch inverse of the CIE Lab equations, white=(1,1,1).
        f = (lightness + 16) / 116
        return native(((f + a / 500) ** 3, f**3, (f - b / 200) ** 3))

    a, b, white = xyz(50, 3, 4), xyz(60, -4, 3), native()
    result = compare_color(a, b, reference_white=white)
    assert result.lab_reference == pytest.approx((50, 3, 4), abs=3e-13)
    assert result.lab_sample == pytest.approx((60, -4, 3), abs=3e-13)
    assert result.delta_lab == pytest.approx((10, -7, -1), abs=4e-13)
    assert result.delta_chroma == pytest.approx(0, abs=3e-13)
    assert result.delta_hue == pytest.approx(math.sqrt(50), abs=4e-13)
    assert result.delta_xyz == tuple(y - x for x, y in zip(a.values, b.values))
    assert result.delta_e2000 > 0 and result.delta_e2000_reason is None
    swapped = compare_color(b, a, reference_white=white)
    for field in ("delta_xyz", "delta_xy", "delta_uv", "delta_lab"):
        assert getattr(swapped, field) == pytest.approx(tuple(-v for v in getattr(result, field)))
    assert swapped.delta_hue == pytest.approx(-result.delta_hue)
    assert swapped.delta_chroma == pytest.approx(-result.delta_chroma)
    assert swapped.delta_e2000 == pytest.approx(result.delta_e2000)
    assert result.xyz_unit == a.unit
    json.dumps(asdict(result), allow_nan=False)


def test_full_color_coordinate_golden_and_no_white():
    from color_measurement.processing import compare_color

    result = compare_color(native((0, 1, 0)), native((1, 1, 0)), reference_white=None)
    assert result.delta_xyz == (1, 0, 0)
    assert result.delta_xy == (0.5, -0.5)
    assert result.delta_uv == pytest.approx((0.25, -3 / 80), abs=2e-16)
    assert result.lab_reference is result.delta_lab is None
    assert result.lab_reason and result.hue_reason


def test_full_color_black_coordinates_and_neutral_hue_are_independent():
    from color_measurement.processing import compare_color

    result = compare_color(native((0, 0, 0)), native((0.125,) * 3), reference_white=native())
    assert result.delta_xyz == (0.125,) * 3
    assert result.delta_xy is result.delta_uv is None
    assert result.xy_reason and result.uv_reason
    assert result.lab_reference == (0, 0, 0)
    for coordinates in (result.lab_sample, result.delta_lab):
        # libm cbrt(0.125) can round one ulp below 0.5; L* is then just below 42.
        assert coordinates[0] == pytest.approx(42, rel=0, abs=1e-13)
        assert coordinates[1:] == (0, 0)
    assert result.delta_chroma == 0 and result.delta_hue is None and result.hue_reason
    assert result.delta_e2000 > 0


@pytest.mark.parametrize(
    "field,value",
    [("unit", "1"), ("scale", "relative"), ("observer", "CIE1964_10"), ("illuminant", "D50")],
)
def test_full_color_pair_and_retained_white_context_fences(field, value):
    from color_measurement.processing import compare_color

    result = compare_color(native(), replace(native(), **{field: value}), reference_white=native())
    assert all(getattr(result, name) is None for name in ("delta_xyz", "delta_xy", "lab_sample"))
    assert result.xyz_reason and result.lab_reason
    result = compare_color(native(), native(), reference_white=replace(native(), **{field: value}))
    assert result.delta_xyz == (0, 0, 0)
    assert result.lab_sample is None and result.lab_reason


def test_full_color_theoretical_white_and_input_preservation():
    from color_measurement.processing import compare_color
    from color_measurement.science import standard_reference_white

    white = standard_reference_white(
        "D50",
        observer="CIE1931_2",
        reference_y=100,
        unit="cd/m^2",
        scale="absolute; Y is luminance",
    )
    pair = native(white.xyz)
    before = asdict(pair), asdict(white)
    result = compare_color(pair, pair, reference_white=white)
    assert result.lab_reference == (100, 0, 0) and result.delta_e2000 == 0
    assert result.delta_hue is None
    assert (asdict(pair), asdict(white)) == before
    with pytest.raises(FrozenInstanceError):
        result.delta_xyz = (1, 2, 3)


def test_full_color_independent_lab_failure_keeps_reference_and_native_difference():
    from color_measurement.processing import compare_color

    tiny = math.ulp(0)
    result = compare_color(
        native((tiny,) * 3), native((1,) * 3), reference_white=native((tiny,) * 3)
    )
    assert result.lab_reference == (100, 0, 0) and result.lab_reference_reason is None
    assert result.lab_sample is None and result.lab_sample_reason
    assert result.delta_xyz == (1, 1, 1) and result.delta_lab is None
    json.dumps(asdict(result), allow_nan=False)


def test_full_color_hue_wrap_and_undefined_argument_refusal():
    from color_measurement.processing import compare_color

    def xyz(h):
        a, b = 5 * math.cos(math.radians(h)), 5 * math.sin(math.radians(h))
        f = 66 / 116
        return native(((f + a / 500) ** 3, f**3, (f - b / 200) ** 3))

    result = compare_color(xyz(170), xyz(-170), reference_white=native())
    assert result.delta_hue == pytest.approx(10 * math.sin(math.radians(10)), abs=4e-13)
    assert compare_color(None, native(), reference_white=None).xyz_reason
    for args in ((False, native()), (native(), None)):
        if args[0] is False:
            with pytest.raises(ColorDomainError):
                compare_color(*args, reference_white=None)
    with pytest.raises(ColorDomainError):
        compare_color(native(), native(), reference_white=(1, 1, 1))
