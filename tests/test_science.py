"""Fresh analytical vectors for software numerical behavior, without hardware.

Expected values below are obtained from elementary ratios, perfect cubes, and
linear slopes, ICC equations independently evaluated with Decimal, and original
CIE table rows/metadata, not another color library or captured instrument result.
Absolute tolerances cover binary64 arithmetic; they are not optical tolerances.
"""

import math
import sys
import unittest
from dataclasses import FrozenInstanceError, asdict
from decimal import Decimal, localcontext
from fractions import Fraction
from importlib import resources

from color_measurement.models import Spectrum
from color_measurement.science import (
    ColorDomainError,
    adapt_xyz,
    lab_to_xyz,
    spectral_xyz,
    xyy_to_xyz,
    xyz_to_lab,
    xyz_to_xyy,
)


class ColorScienceTests(unittest.TestCase):
    def assert_triple_close(self, actual, expected, *, absolute=2e-12, relative=2e-14):
        self.assertIsInstance(actual, tuple)
        self.assertEqual(len(actual), 3)
        for component, target in zip(actual, expected):
            self.assertTrue(
                math.isclose(component, target, rel_tol=relative, abs_tol=absolute),
                f"{actual!r} differs from {expected!r}",
            )

    def test_xyz_to_xyy_analytical(self):
        self.assertEqual(xyz_to_xyy((2, 3, 5)), (0.2, 0.3, 3.0))
        self.assertEqual(xyz_to_xyy((0, 2, 0)), (0.0, 1.0, 2.0))
        self.assertEqual(xyz_to_xyy((-1, 2, 3)), (-0.25, 0.5, 2.0))

    def test_xyy_to_xyz_analytical(self):
        self.assertEqual(xyy_to_xyz((0.2, 0.3, 3)), (2.0, 3.0, 5.0))
        self.assertEqual(xyy_to_xyz((0.0, 1.0, 2)), (0.0, 2.0, 0.0))
        self.assertEqual(xyy_to_xyz((-0.25, 0.5, 2)), (-1.0, 2.0, 3.0))
        self.assert_triple_close(xyy_to_xyz((0.2, -0.4, 2)), (-1, 2, -6))

    def test_chromaticity_scale_invariance_preserves_y(self):
        for scale in (0.01, 1.0, 100.0, -2.0):
            with self.subTest(scale=scale):
                self.assert_triple_close(
                    xyz_to_xyy((2 * scale, 3 * scale, 5 * scale)),
                    (0.2, 0.3, 3 * scale),
                )
                self.assert_triple_close(
                    xyy_to_xyz((0.2, 0.3, 3 * scale)),
                    (2 * scale, 3 * scale, 5 * scale),
                )

    def test_chromaticity_round_trip_signed_values(self):
        for xyz in ((2, 3, 5), (-1, 2, 3), (-1, -2, -3), (4, -2, 1)):
            with self.subTest(xyz=xyz):
                self.assert_triple_close(xyy_to_xyz(xyz_to_xyy(xyz)), xyz)

    def test_xyz_sum_uses_stable_summation(self):
        self.assertEqual(xyz_to_xyy((1e16, 1, -1e16)), (1e16, 1.0, 1.0))

    def test_chromaticity_undefined_for_zero_sum(self):
        for xyz in ((0, 0, 0), (1, 1, -2), (-1, 1, 0)):
            with self.subTest(xyz=xyz), self.assertRaisesRegex(ColorDomainError, "sum is zero"):
                xyz_to_xyy(xyz)

    def test_xyy_y_zero_is_always_undefined(self):
        for xyy in ((0.3, 0, 5), (0.3, 0, 0), (0, -0.0, -1)):
            with self.subTest(xyy=xyy), self.assertRaisesRegex(ColorDomainError, "y is zero"):
                xyy_to_xyz(xyy)

    def test_xyy_black_loses_chromaticity(self):
        xyz = xyy_to_xyz((0.3, 0.4, 0))
        self.assertEqual(xyz, (0.0, 0.0, 0.0))
        with self.assertRaises(ColorDomainError):
            xyz_to_xyy(xyz)

    def test_zero_luminance_nonzero_xyz_has_no_inverse(self):
        xyy = xyz_to_xyy((2, 0, 3))
        self.assertEqual(xyy, (0.4, 0.0, 0.0))
        with self.assertRaises(ColorDomainError):
            xyy_to_xyz(xyy)

    def test_nonzero_small_denominators_have_no_arbitrary_cutoff(self):
        self.assertEqual(xyz_to_xyy((1e-300, 0, 0)), (1.0, 0.0, 0.0))
        self.assertEqual(xyy_to_xyz((0.25, 1e-300, 1e-300)), (0.25, 1e-300, 0.75))
        tiny = math.ulp(0.0)
        self.assertEqual(xyz_to_lab((tiny,) * 3, (tiny,) * 3), (100.0, 0.0, 0.0))
        self.assertEqual(lab_to_xyz((100, 0, 0), (tiny,) * 3), (tiny,) * 3)

    def test_lab_black_and_white(self):
        for white in ((1, 1, 1), (95, 100, 109), (0.1, 0.2, 0.3)):
            with self.subTest(white=white):
                self.assertEqual(xyz_to_lab((0, 0, 0), white), (0.0, 0.0, 0.0))
                self.assertEqual(xyz_to_lab(white, white), (100.0, 0.0, 0.0))
                self.assertEqual(lab_to_xyz((0, 0, 0), white), (0.0, 0.0, 0.0))
                self.assertEqual(lab_to_xyz((100, 0, 0), white), white)

    def test_lab_independent_perfect_cube_vector(self):
        # XYZ/white = (1/8, 1/27, 1/64), so f = (1/2, 1/3, 1/4).
        lab = (68 / 3, 250 / 3, 50 / 3)
        self.assert_triple_close(xyz_to_lab((1, 1, 1), (8, 27, 64)), lab)
        self.assert_triple_close(lab_to_xyz(lab, (8, 27, 64)), (1, 1, 1))

    def test_lab_independent_unequal_channels_and_white(self):
        # Cube-root ratios are (0.6, 0.5, 0.4) relative to this synthetic white.
        white = (80, 100, 120)
        xyz = (17.28, 12.5, 7.68)
        self.assert_triple_close(xyz_to_lab(xyz, white), (42, 50, 20), absolute=1e-10, relative=0)
        self.assert_triple_close(lab_to_xyz((42, 50, 20), white), xyz, absolute=1e-10, relative=0)

    def test_lab_independent_mixed_branches(self):
        epsilon = 216 / 24389
        for xyz, lab in (
            ((0, epsilon, 1), (8, -1000 / 29, -4600 / 29)),
            ((-epsilon, 0, epsilon), (0, -1000 / 29, -400 / 29)),
        ):
            with self.subTest(xyz=xyz):
                self.assert_triple_close(xyz_to_lab(xyz, (1,) * 3), lab, absolute=1e-10, relative=0)
                self.assert_triple_close(lab_to_xyz(lab, (1,) * 3), xyz, absolute=1e-10, relative=0)

    def test_lab_near_boundary_uses_distinct_branches(self):
        # These distances distinguish branches; adjacent floats cannot do so.
        epsilon = 216 / 24389
        for ratio, lightness in (
            (0.999 * epsilon, 7.992),
            (1.001 * epsilon, 8.007997334813827884),
        ):
            with self.subTest(ratio=ratio):
                self.assert_triple_close(
                    xyz_to_lab((ratio,) * 3, (1,) * 3),
                    (lightness, 0, 0),
                    absolute=1e-10,
                    relative=0,
                )
                self.assert_triple_close(
                    lab_to_xyz((lightness, 0, 0), (1,) * 3),
                    (ratio,) * 3,
                    absolute=1e-10,
                    relative=0,
                )

    def test_lab_independent_neutral_midpoint(self):
        # f(1/8) = 1/2 gives L* = 42 exactly, a* = b* = 0.
        self.assert_triple_close(xyz_to_lab((0.125,) * 3, (1,) * 3), (42, 0, 0))
        self.assert_triple_close(lab_to_xyz((42, 0, 0), (1,) * 3), (0.125,) * 3)

    def test_lab_linear_branch_independent_vector(self):
        # Ratios = k * 108/24389 produce L* = 4*k for a neutral sample.
        for k in (0.25, 1.0, -1.0):
            ratio = k * 108 / 24389
            self.assert_triple_close(xyz_to_lab((ratio,) * 3, (1,) * 3), (4 * k, 0, 0))
            self.assert_triple_close(lab_to_xyz((4 * k, 0, 0), (1,) * 3), (ratio,) * 3)

    def test_lab_signed_extension_independent_vector(self):
        # f values = (-1+4/29, -2+4/29, 4/29), yielding exact integer Lab.
        xyz = (-108 / 841, -216 / 841, 0)
        lab = (-232, 500, -400)
        self.assert_triple_close(xyz_to_lab(xyz, (1,) * 3), lab)
        self.assert_triple_close(lab_to_xyz(lab, (1,) * 3), xyz)

    def test_lab_common_scale_invariance(self):
        lab = (68 / 3, 250 / 3, 50 / 3)
        for scale in (0.001, 1, 1000):
            xyz, white = (scale,) * 3, tuple(scale * x for x in (8, 27, 64))
            with self.subTest(scale=scale):
                self.assert_triple_close(xyz_to_lab(xyz, white), lab)
                self.assert_triple_close(lab_to_xyz(lab, white), xyz)

    def test_lab_white_is_required_and_changes_result(self):
        self.assert_triple_close(xyz_to_lab((1,) * 3, (8,) * 3), (42, 0, 0))
        with self.assertRaises(TypeError):
            xyz_to_lab((1,) * 3)
        with self.assertRaises(TypeError):
            lab_to_xyz((100, 0, 0))

    def test_lab_forward_branch_boundary_and_neighbors(self):
        boundary = (6 / 29) ** 3
        for ratio in (
            math.nextafter(boundary, -math.inf),
            boundary,
            math.nextafter(boundary, math.inf),
        ):
            with self.subTest(ratio=ratio):
                lab = xyz_to_lab((ratio,) * 3, (1,) * 3)
                self.assert_triple_close(lab, (8, 0, 0), absolute=2e-14, relative=0)
                self.assert_triple_close(
                    lab_to_xyz(lab, (1,) * 3), (ratio,) * 3, absolute=2e-17, relative=0
                )

    def test_lab_inverse_branch_boundary_and_neighbors(self):
        boundary = 6 / 29
        for value in (
            math.nextafter(boundary, -math.inf),
            boundary,
            math.nextafter(boundary, math.inf),
        ):
            lightness = 116 * value - 16
            with self.subTest(value=value):
                xyz = lab_to_xyz((lightness, 0, 0), (1,) * 3)
                self.assert_triple_close(xyz, ((6 / 29) ** 3,) * 3, absolute=2e-17, relative=0)
                self.assert_triple_close(
                    xyz_to_lab(xyz, (1,) * 3), (lightness, 0, 0), absolute=2e-14, relative=0
                )

    def test_lab_round_trip_mixed_branches_and_signs(self):
        white = (3, 5, 7)
        for xyz in ((0.001, 1, 5), (-0.1, 0.2, -0.3), (30, 50, 70)):
            with self.subTest(xyz=xyz):
                self.assert_triple_close(lab_to_xyz(xyz_to_lab(xyz, white), white), xyz)
        for lab in ((-10, 25, -50), (0, 0, 0), (200, -300, 400)):
            with self.subTest(lab=lab):
                self.assert_triple_close(xyz_to_lab(lab_to_xyz(lab, white), white), lab)

    def test_real_sequences_are_converted_to_floats(self):
        self.assertEqual(xyz_to_xyy([Fraction(1, 2), 1, Fraction(3, 2)]), (1 / 6, 1 / 3, 1.0))

    def test_invalid_shapes_and_components_are_rejected(self):
        invalid = (
            None,
            1,
            "123",
            b"123",
            (),
            (1,),
            (1, 2),
            (1, 2, 3, 4),
            {1, 2, 3},
            {0: 1, 1: 2, 2: 3},
            (1, "2", 3),
            (1, True, 3),
            (False, 2, 3),
            (1, 2, complex(3, 0)),
            ((1,), 2, 3),
            (10**1000, 2, 3),
        )
        functions = (
            xyz_to_xyy,
            xyy_to_xyz,
            lambda values: xyz_to_lab(values, (1,) * 3),
            lambda values: lab_to_xyz(values, (1,) * 3),
        )
        for function in functions:
            for value in invalid:
                with (
                    self.subTest(function=function, value=value),
                    self.assertRaises(ColorDomainError),
                ):
                    function(value)

    def test_nonfinite_components_are_rejected(self):
        functions = (
            xyz_to_xyy,
            xyy_to_xyz,
            lambda values: xyz_to_lab(values, (1,) * 3),
            lambda values: lab_to_xyz(values, (1,) * 3),
        )
        for function in functions:
            for nonfinite in (math.nan, math.inf, -math.inf):
                for index in range(3):
                    values = [1, 1, 1]
                    values[index] = nonfinite
                    with (
                        self.subTest(function=function, nonfinite=nonfinite, index=index),
                        self.assertRaises(ColorDomainError),
                    ):
                        function(values)

    def test_invalid_reference_whites_are_rejected(self):
        whites = [None, (), (1, 2), (1, 2, 3, 4), (True, 1, 1), (1, "1", 1)]
        for invalid in (0, -1, math.nan, math.inf, -math.inf):
            for index in range(3):
                white = [1, 1, 1]
                white[index] = invalid
                whites.append(white)
        for function in (xyz_to_lab, lab_to_xyz):
            for white in whites:
                with (
                    self.subTest(function=function, white=white),
                    self.assertRaises(ColorDomainError),
                ):
                    function((1, 1, 1), white)

    def test_overflow_is_a_domain_error(self):
        maximum = sys.float_info.max
        tiny = math.ulp(0.0)
        operations = (
            lambda: xyz_to_xyy((maximum, maximum, 0)),
            lambda: xyz_to_xyy((maximum, -maximum, tiny)),
            lambda: xyy_to_xyz((1, tiny, maximum)),
            lambda: xyy_to_xyz((-maximum, -maximum, 1)),
            lambda: xyy_to_xyz((maximum, 1, maximum)),
            lambda: xyz_to_lab((1, 1, 1), (tiny,) * 3),
            lambda: xyz_to_lab((-maximum, 0, 0), (1,) * 3),
            lambda: xyz_to_lab((-maximum / 100, 0, 0), (1,) * 3),
            lambda: lab_to_xyz((maximum, 0, 0), (1,) * 3),
            lambda: lab_to_xyz((216, 0, 0), (maximum,) * 3),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(ColorDomainError):
                operation()

    def test_large_finite_values_supported_when_arithmetic_is_finite(self):
        maximum = sys.float_info.max
        self.assertEqual(xyz_to_lab((maximum,) * 3, (maximum,) * 3), (100.0, 0.0, 0.0))
        self.assertEqual(lab_to_xyz((100, 0, 0), (maximum,) * 3), (maximum,) * 3)


def decimal_bradford(xyz, source, destination):
    """Independent 60-digit equation evaluation using Cramer's rule.

    Matrix decimals are from ICC.1:2004-10 E.1, not the production constants.
    Cramer's rule deliberately differs from the production rational inverse.
    """
    with localcontext() as context:
        context.prec = 60
        matrix = [[Decimal(value) for value in row] for row in (
            ("0.8951", "0.2664", "-0.1614"),
            ("-0.7502", "1.7135", "0.0367"),
            ("0.0389", "-0.0685", "1.0296"),
        )]

        def product(vector):
            return [sum(a * b for a, b in zip(row, vector)) for row in matrix]

        def determinant(rows):
            a, b, c = rows
            return (a[0] * (b[1] * c[2] - b[2] * c[1])
                    - a[1] * (b[0] * c[2] - b[2] * c[0])
                    + a[2] * (b[0] * c[1] - b[1] * c[0]))

        sample = [Decimal.from_float(float(value)) for value in xyz]
        whites = [[Decimal.from_float(float(value)) for value in white]
                  for white in (source, destination)]
        cones = [product([value / white[1] for value in white]) for white in whites]
        target = [value * b / a for value, a, b in zip(product(sample), *cones)]
        divisor = determinant(matrix)
        return tuple(float(determinant([
            [target[i] if j == column else value for j, value in enumerate(row)]
            for i, row in enumerate(matrix)
        ]) / divisor) for column in range(3))


class BradfordTests(unittest.TestCase):
    # Reuse the assertion helper without repeating the original coordinate tests.
    assert_triple_close = ColorScienceTests.assert_triple_close
    def test_bradford_independent_decimal_vectors(self):
        whites = ((0.9642, 1, 0.8251), (0.9505, 1, 1.0890), (1, 1, 1))
        for source in whites:
            for destination in whites:
                for xyz in ((0, 0, 0), (0.25, 0.5, 0.75), (-5, 3, 7), (40, -20, 5)):
                    with self.subTest(source=source, destination=destination, xyz=xyz):
                        self.assert_triple_close(
                            adapt_xyz(xyz, source, destination),
                            decimal_bradford(xyz, source, destination),
                        )

    def test_bradford_identity_is_exact_even_for_large_signed_samples(self):
        sample = (sys.float_info.max, -sys.float_info.max, 0)
        self.assertEqual(adapt_xyz(sample, (1, 1, 1), (100, 100, 100)), sample)

    def test_bradford_white_mapping_ignores_destination_brightness(self):
        source = (0.9505 * 203, 203, 1.0890 * 203)
        destination = (96.42, 100, 82.51)
        self.assert_triple_close(
            adapt_xyz(source, source, destination), (0.9642 * 203, 203, 0.8251 * 203),
        )

    def test_bradford_preserves_sample_scaling_and_signed_channels(self):
        source, destination = (0.9642, 1, 0.8251), (0.9505, 1, 1.0890)
        sample = (-0.25, 0.5, 0.75)
        adapted = adapt_xyz(sample, source, destination)
        self.assertLess(adapted[0], 0)
        for factor in (0.001, -2, 1000):
            self.assert_triple_close(
                adapt_xyz(tuple(value * factor for value in sample), source, destination),
                tuple(value * factor for value in adapted),
            )
        self.assert_triple_close(adapt_xyz(adapted, destination, source), sample)

    def test_bradford_invalid_inputs_and_cone_whites_refuse(self):
        for value in (None, (), (1, 2), (True, 1, 1), (1, math.nan, 1)):
            for position in range(3):
                inputs = [(1, 1, 1)] * 3
                inputs[position] = value
                with (self.subTest(value=value, position=position),
                      self.assertRaises(ColorDomainError)):
                    adapt_xyz(*inputs)
        for white in ((0, 1, 1), (-1, 1, 1), (1, 1, 100), (100, 1, 1)):
            with self.subTest(white=white), self.assertRaises(ColorDomainError):
                adapt_xyz((1, 1, 1), white, white)

    def test_bradford_nonfinite_arithmetic_refuses(self):
        tiny, maximum = math.ulp(0), sys.float_info.max
        for args in (((1, 1, 1), (1, tiny, 1), (1, 1, 1)),
                     ((maximum, maximum, maximum), (1, 1, 1), (0.9642, 1, 0.8251))):
            with self.subTest(args=args), self.assertRaises(ColorDomainError):
                adapt_xyz(*args)


class SpectralIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.observer_csv = resources.files("color_measurement").joinpath(
            "desktop_assets", "resources", "CIE_xyz_1931_2deg.csv"
        ).read_bytes()

    def integrate(self, spectrum, **kwargs):
        arguments = dict(observer="CIE1931_2", observer_csv=self.observer_csv,
                         wavelength_convention="standard-air-nm")
        arguments.update(kwargs)
        return spectral_xyz(spectrum, **arguments)

    def test_radiance_known_rows_and_inclusive_endpoint_weights(self):
        # Original official 555 and 556 nm rows: no production parser oracle.
        spectrum = Spectrum((555, 556), (1, 2), "spectral_radiance", "W/(m^2 sr nm)")
        expected = tuple(683.002 * (a + 2 * b) for a, b in zip(
            (0.5120501, 1.0, 0.005749999), (0.5282959, 0.9998567, 0.0053036)
        ))
        result = self.integrate(spectrum)
        for actual, target in zip(result.values, expected):
            self.assertAlmostEqual(actual, target, delta=2e-12)
        self.assertEqual((result.unit, result.scale), ("cd/m^2", "absolute; Y is luminance"))
        self.assertEqual(result.photometric_factor, 683.002)
        self.assertEqual(result.wavelength_step_nm, 1)
        self.assertTrue(result.limited_range)
        self.assertIsNone(result.reference_white)
        self.assertIs(result.spectrum, spectrum)

    def test_milliwatts_and_irradiance_keep_distinct_quantity_units(self):
        grid = (540, 550, 560)
        expected = self.integrate(Spectrum(grid, (1, -0.5, 2), "spectral_radiance",
                                           "W/(m^2 sr nm)")).values
        for quantity, unit, output in (("spectral_radiance", "mW/(m^2 sr nm)", "cd/m^2"),
                                       ("spectral_irradiance", "mW/(m^2 nm)", "lux")):
            result = self.integrate(Spectrum(grid, (1000, -500, 2000), quantity, unit))
            self.assertEqual(result.values, expected)
            self.assertEqual(result.unit, output)
        dark = self.integrate(Spectrum(grid, (-1, -1, -1), "spectral_irradiance", "W/(m^2 nm)"))
        self.assertTrue(all(value < 0 for value in dark.values))

    def test_fractional_grid_linear_cmf_interpolation(self):
        spectrum = Spectrum((555.25, 555.75), (1, 1), "spectral_radiance", "W/(m^2 sr nm)")
        result = self.integrate(spectrum)
        # Two symmetric interpolated points, each weighted by delta_lambda=0.5.
        expected = tuple(683.002 * (a + b) / 2 for a, b in zip(
            (0.5120501, 1.0, 0.005749999), (0.5282959, 0.9998567, 0.0053036)
        ))
        for actual, target in zip(result.values, expected):
            self.assertAlmostEqual(actual, target, delta=1e-12)
        self.assertEqual(result.cmf_interpolation, "linear; no extrapolation")

    def test_full_official_table_equal_energy_column_sums(self):
        spectrum = Spectrum(tuple(range(360, 831)), (1,) * 471,
                            "spectral_radiance", "W/(m^2 sr nm)")
        result = self.integrate(spectrum)
        # Official metadata's independent sumOfColumns validation values.
        expected = tuple(683.002 * value for value in
                         (106.865469489595, 106.8569171011719, 106.892251278636))
        for actual, target in zip(result.values, expected):
            self.assertAlmostEqual(actual, target, delta=1e-8)
        self.assertFalse(result.limited_range)
        self.assertEqual(result.observer_sha256,
                         "fa663e3535a7e0763a745993a1f0a192eb0275ac46ad2d1befd7626841e713c1")

    def test_reflectance_percent_fraction_scale_and_diffuser_white(self):
        grid = (450, 550, 650)
        spd = Spectrum(grid, (10, 20, 5), "relative_spectral_power", "1")
        white = self.integrate(Spectrum(grid, (1, 1, 1), "reflectance", "1"),
                               illuminant=spd, illuminant_id="explicit synthetic SPD")
        self.assertAlmostEqual(white.values[1], 100, delta=2e-14)
        self.assertEqual(white.values, white.reference_white)
        half = self.integrate(Spectrum(grid, (50, 50, 50), "reflectance", "%"),
                              illuminant=spd, illuminant_id="explicit synthetic SPD")
        self.assertEqual(half.values, tuple(value / 2 for value in white.values))
        self.assertEqual((half.unit, half.scale), ("1", "relative; perfect-diffuser Y=100"))
        self.assertIsNone(half.photometric_factor)
        self.assertIs(half.illuminant, spd)
        scaled = self.integrate(Spectrum(grid, (0.5, 0.5, 0.5), "reflectance", "1"),
                                illuminant=Spectrum(grid, (1e300, 2e300, 5e299),
                                                    "relative_spectral_power", "1"),
                                illuminant_id="same synthetic shape")
        for actual, target in zip(scaled.values, half.values):
            self.assertAlmostEqual(actual, target, delta=2e-14)
        signed = self.integrate(Spectrum(grid, (-0.5, -0.5, -0.5), "reflectance", "1"),
                                illuminant=spd, illuminant_id="synthetic")
        self.assertEqual(signed.values, tuple(-value for value in half.values))
        above = self.integrate(Spectrum(grid, (1.5, 1.5, 1.5), "reflectance", "1"),
                               illuminant=spd, illuminant_id="synthetic")
        self.assertAlmostEqual(above.values[1], 150, delta=3e-14)

    def test_reflectance_independent_official_rows(self):
        spectrum = Spectrum((555, 556), (-0.25, 1.5), "reflectance", "1")
        spd = Spectrum((555, 556), (2, 1), "relative_spectral_power", "1")
        result = self.integrate(spectrum, illuminant=spd, illuminant_id="synthetic")
        denominator = 2 + 0.9998567
        expected = tuple(100 * (-0.5 * a + 1.5 * b) / denominator for a, b in zip(
            (0.5120501, 1.0, 0.005749999), (0.5282959, 0.9998567, 0.0053036)
        ))
        for actual, target in zip(result.values, expected):
            self.assertAlmostEqual(actual, target, delta=2e-14)

    def test_missing_wrong_observer_bytes_and_wavelength_conventions_refuse(self):
        spectrum = Spectrum((555, 556), (1, 1), "spectral_radiance", "W/(m^2 sr nm)")
        for replacement in (b"", self.observer_csv + b"\n", self.observer_csv.decode("ascii")):
            with self.subTest(replacement=type(replacement)), self.assertRaises(ColorDomainError):
                self.integrate(spectrum, observer_csv=replacement)
        for arguments in ({"observer": "CIE1964_10"}, {"wavelength_convention": "vacuum-nm"}):
            with self.subTest(arguments=arguments), self.assertRaises(ColorDomainError):
                self.integrate(spectrum, **arguments)

    def test_grid_domain_and_unsupported_quantities_refuse(self):
        for grid in ((555,), (359, 360), (830, 831), (400, 410, 425),
                     (500, 500 + math.ulp(500), 500 + 3 * math.ulp(500))):
            spectrum = Spectrum(grid, (1,) * len(grid), "spectral_radiance", "W/(m^2 sr nm)")
            with self.subTest(grid=grid), self.assertRaises(ColorDomainError):
                self.integrate(spectrum)
        for quantity, unit in (("unknown", "W/(m^2 sr nm)"), ("spectral_radiance", "counts"),
                               ("reflectance", "W/(m^2 nm)"), ("transmittance", "%")):
            with self.subTest(quantity=quantity, unit=unit), self.assertRaises(ColorDomainError):
                self.integrate(Spectrum((555, 556), (1, 1), quantity, unit))
        with self.assertRaises(ColorDomainError):
            self.integrate(None)

    def test_reflectance_requires_exact_spd_grid_quantity_units_and_identity(self):
        spectrum = Spectrum((555, 556), (1, 1), "reflectance", "1")
        valid = Spectrum((555, 556), (1, 1), "relative_spectral_power", "1")
        for illuminant in (None,
                           Spectrum((554, 555), (1, 1), "relative_spectral_power", "1"),
                           Spectrum((555, 556), (-1, 1), "relative_spectral_power", "1"),
                           Spectrum((555, 556), (0, 0), "relative_spectral_power", "1"),
                           Spectrum((555, 556), (1, 1), "spectral_irradiance", "W/(m^2 nm)"),
                           Spectrum((555, 556), (1, 1), "relative_spectral_power", "%")):
            with self.subTest(illuminant=illuminant), self.assertRaises(ColorDomainError):
                self.integrate(spectrum, illuminant=illuminant, illuminant_id="synthetic")
        for identity in (None, "", "  ", True):
            with self.subTest(identity=identity), self.assertRaises(ColorDomainError):
                self.integrate(spectrum, illuminant=valid, illuminant_id=identity)
        with self.assertRaises(ColorDomainError):
            self.integrate(Spectrum((555, 556), (1, 1), "spectral_radiance", "W/(m^2 sr nm)"),
                           illuminant=valid, illuminant_id="synthetic")

    def test_overflow_refuses_and_result_preserves_original_inputs(self):
        spectrum = Spectrum((555, 556), (sys.float_info.max,) * 2,
                            "spectral_radiance", "W/(m^2 sr nm)")
        with self.assertRaises(ColorDomainError):
            self.integrate(spectrum)
        spectrum = Spectrum((555, 556), (-1, 2), "reflectance", "1")
        illuminant = Spectrum((555, 556), (1, 2), "relative_spectral_power", "1")
        before = asdict(spectrum), asdict(illuminant)
        result = self.integrate(spectrum, illuminant=illuminant, illuminant_id="synthetic")
        self.assertEqual((asdict(spectrum), asdict(illuminant)), before)
        with self.assertRaises(FrozenInstanceError):
            result.values = (0, 0, 0)


if __name__ == "__main__":
    unittest.main()
