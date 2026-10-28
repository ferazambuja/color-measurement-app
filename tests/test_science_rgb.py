"""Primary-source numerical reference and ideal-reference geometry tests.

Both matrices follow pinned CSS Color 4 primary/D65 conventions. Reference
transfer values use independent 70-digit Decimal arithmetic, not a display.
"""

import math
import sys
import unittest
from dataclasses import FrozenInstanceError
from fractions import Fraction

from color_measurement.science import (
    ColorDomainError,
    _display_encode,
    rgb_gamut_status,
    rgb_reference_geometry,
    xyz_to_lab,
    xyz_to_rgb,
)

# Published forward coefficients, independent of the production inverse.
SRGB_PRIMARY_XYZ = (
    (506752 / 1228815, 87098 / 409605, 7918 / 409605),
    (87881 / 245763, 175762 / 245763, 87881 / 737289),
    (12673 / 70218, 12673 / 175545, 1001167 / 1053270),
)

# Independent RGB-to-XYZ coefficients in W3C CSS Color 4 section 19.
P3_PRIMARY_XYZ = (
    (608311 / 1250200, 35783 / 156275, 0.0),
    (189793 / 714400, 247089 / 357200, 32229 / 714400),
    (198249 / 1000160, 198249 / 2500400, 5220557 / 5000800),
)
COLOR_SPACES = ("srgb", "display-p3")


class RGBEncodingTests(unittest.TestCase):
    def assert_close(self, actual, expected, *, tolerance=2e-15):
        self.assertEqual(len(actual), 3)
        for value, target in zip(actual, expected):
            self.assertAlmostEqual(value, target, delta=tolerance)

    def test_black_has_zero_rgb_and_no_clipping(self):
        for color_space in COLOR_SPACES:
            with self.subTest(color_space=color_space):
                result = xyz_to_rgb((0, 0, 0), color_space=color_space, reference_luminance=100)
                self.assertEqual(result.color_space, color_space)
                self.assertEqual(result.linear_rgb, (0.0, 0.0, 0.0))
                self.assertEqual(result.display_rgb, (0.0, 0.0, 0.0))
                self.assertEqual(result.clipped_channels, (False, False, False))

    def test_analytical_basis_vectors_preserve_actual_matrix_columns(self):
        columns = ((12831 / 3959, -851781 / 878810, 705 / 12673),
                   (-329 / 214, 1648619 / 878810, -2585 / 12673),
                   (-1974 / 3959, 36519 / 878810, 705 / 667))
        flags = ((True, True, False), (True, True, True), (True, False, True))
        for index, (column, clipped) in enumerate(zip(columns, flags)):
            xyz = tuple(float(index == axis) for axis in range(3))
            with self.subTest(axis=index):
                result = xyz_to_rgb(xyz, color_space="srgb", reference_luminance=1)
                self.assertEqual(result.linear_rgb, column)
                self.assertEqual(result.clipped_channels, clipped)
                self.assertTrue(all(0 <= value <= 1 for value in result.display_rgb))

    def test_independent_inverse_primary_vectors(self):
        for color_space, primaries in (("srgb", SRGB_PRIMARY_XYZ), ("display-p3", P3_PRIMARY_XYZ)):
            for index, xyz in enumerate(primaries):
                expected = tuple(float(index == channel) for channel in range(3))
                with self.subTest(color_space=color_space, primary=index):
                    result = xyz_to_rgb(xyz, color_space=color_space, reference_luminance=1)
                    self.assert_close(result.linear_rgb, expected, tolerance=3e-16)
                    self.assert_close(result.display_rgb, expected, tolerance=2e-15)
                    # Rounded fixture/matrix arithmetic may cross an exact boundary.
                    self.assertEqual(result.clipped_channels,
                                     tuple(v < 0 or v > 1 for v in result.linear_rgb))

    def test_nominal_primaries_and_d65_white_in_both_spaces(self):
        definitions = (
            ("srgb", SRGB_PRIMARY_XYZ, ((0.640, 0.330), (0.300, 0.600), (0.150, 0.060))),
            ("display-p3", P3_PRIMARY_XYZ, ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060))),
        )
        nominal_white = (0.3127 / 0.3290, 1.0, 0.3583 / 0.3290)
        for color_space, primaries, chromaticities in definitions:
            for xyz, xy in zip(primaries, chromaticities):
                with self.subTest(color_space=color_space, xy=xy):
                    total = math.fsum(xyz)
                    self.assertAlmostEqual(xyz[0] / total, xy[0], delta=1e-16)
                    self.assertAlmostEqual(xyz[1] / total, xy[1], delta=1e-16)
            result = xyz_to_rgb(nominal_white, color_space=color_space, reference_luminance=1)
            self.assert_close(result.linear_rgb, (1, 1, 1), tolerance=1e-15)
            self.assert_close(result.display_rgb, (1, 1, 1), tolerance=1e-15)
            # Nominal reference definitions do not snap binary64 results to 1.
            self.assertEqual(result.clipped_channels,
                             tuple(c < 0 or c > 1 for c in result.linear_rgb))

    def test_p3_color_can_be_inside_p3_but_outside_srgb(self):
        linear_p3 = (0.95, 0.1, 0.05)
        xyz = tuple(math.fsum(primary[i] * channel
                              for primary, channel in zip(P3_PRIMARY_XYZ, linear_p3))
                    for i in range(3))
        p3 = xyz_to_rgb(xyz, color_space="display-p3", reference_luminance=1)
        srgb = xyz_to_rgb(xyz, color_space="srgb", reference_luminance=1)
        self.assert_close(p3.linear_rgb, linear_p3, tolerance=3e-16)
        self.assertEqual(p3.clipped_channels, (False, False, False))
        self.assertGreater(srgb.linear_rgb[0], 1)
        self.assertEqual(srgb.clipped_channels, (True, False, False))
        self.assertNotEqual(p3.display_rgb, srgb.display_rgb)

    def test_independent_neutral_white_and_midgray(self):
        for color_space, primaries in (("srgb", SRGB_PRIMARY_XYZ), ("display-p3", P3_PRIMARY_XYZ)):
            with self.subTest(color_space=color_space):
                white = tuple(math.fsum(primary[i] for primary in primaries) for i in range(3))
                result = xyz_to_rgb(white, color_space=color_space, reference_luminance=1)
                self.assert_close(result.linear_rgb, (1, 1, 1), tolerance=6e-16)
                self.assert_close(result.display_rgb, (1, 1, 1), tolerance=5e-16)
                gray = xyz_to_rgb(tuple(v * 0.18 for v in white), color_space=color_space,
                                  reference_luminance=1)
                self.assert_close(gray.linear_rgb, (0.18, 0.18, 0.18), tolerance=2e-16)
                self.assert_close(gray.display_rgb, (0.461356129500441650,) * 3, tolerance=3e-16)
                self.assertEqual(gray.clipped_channels, (False, False, False))

    def test_transfer_reference_values_and_declared_breakpoint(self):
        for linear, encoded in ((0, 0), (0.001, 0.01292),
                                (0.0031308, 0.040449936),
                                (0.003130800001, 0.040449907495393229),
                                (0.18, 0.461356129500441650),
                                (0.5, 0.735356983052449491), (1, 1)):
            with self.subTest(linear=linear):
                self.assertAlmostEqual(_display_encode(linear), encoded, delta=2e-16)
        # A.8's rounded boundary has a small discontinuity; do not smooth it.
        self.assertLess(_display_encode(math.nextafter(0.0031308, math.inf)),
                        _display_encode(0.0031308))

    def test_independent_p3_worked_example(self):
        # Exact rational matrix evaluation, then 70-digit Decimal transfer (5/12).
        result = xyz_to_rgb((20, 30, 40), color_space="display-p3", reference_luminance=100)
        self.assert_close(result.linear_rgb,
                         (0.0581999832322611295867, 0.3723512985199664898073,
                          0.3670712588714192757593), tolerance=2e-16)
        self.assert_close(result.display_rgb,
                         (0.2675796634229519227472, 0.6440090897839318407672,
                          0.6398618236777619700147), tolerance=3e-16)
        self.assertEqual(result.clipped_channels, (False, False, False))

    def test_exact_clipping_flag_has_no_epsilon_or_hidden_normalization(self):
        xyz = [0.2, -0.3, 0.4]
        result = xyz_to_rgb(xyz, color_space="srgb", reference_luminance=1)
        self.assertEqual(result.clipped_channels,
                         tuple(v < 0 or v > 1 for v in result.linear_rgb))
        self.assertTrue(any(result.clipped_channels))
        self.assertEqual(xyz, [0.2, -0.3, 0.4])
        self.assertTrue(all(0 <= v <= 1 for v in result.display_rgb))
        brighter = xyz_to_rgb((0.2, 0.3, 0.4), color_space="srgb", reference_luminance=0.1)
        darker = xyz_to_rgb((0.2, 0.3, 0.4), color_space="srgb", reference_luminance=1)
        self.assertNotEqual(brighter.display_rgb, darker.display_rgb)
        with self.assertRaises(FrozenInstanceError):
            result.linear_rgb = (0, 0, 0)

    def test_common_scale_invariance(self):
        for color_space in COLOR_SPACES:
            baseline = xyz_to_rgb((0.2, 0.3, 0.4), color_space=color_space, reference_luminance=1)
            for factor in (0.001, 100, 1e200):
                with self.subTest(color_space=color_space, factor=factor):
                    scaled = xyz_to_rgb(tuple(v * factor for v in (0.2, 0.3, 0.4)),
                                        color_space=color_space, reference_luminance=factor)
                    self.assert_close(scaled.linear_rgb, baseline.linear_rgb, tolerance=3e-16)
                    self.assert_close(scaled.display_rgb, baseline.display_rgb, tolerance=3e-16)
                    self.assertEqual(scaled.clipped_channels, baseline.clipped_channels)

    def test_supported_real_inputs_and_extreme_positive_scale(self):
        for color_space in COLOR_SPACES:
            with self.subTest(color_space=color_space):
                result = xyz_to_rgb((Fraction(1, 5), Fraction(3, 10), Fraction(2, 5)),
                                    color_space=color_space, reference_luminance=Fraction(1))
                self.assertEqual(result, xyz_to_rgb((0.2, 0.3, 0.4), color_space=color_space,
                                                    reference_luminance=1))
                result = xyz_to_rgb((sys.float_info.max,) * 3, color_space=color_space,
                                    reference_luminance=sys.float_info.max)
                self.assertTrue(all(math.isfinite(v) for v in result.linear_rgb))
                self.assertEqual(xyz_to_rgb((0, 0, 0), color_space=color_space,
                                            reference_luminance=math.ulp(0.0)).display_rgb,
                                 (0.0, 0.0, 0.0))

    def test_invalid_xyz_is_typed_error(self):
        for color_space in COLOR_SPACES:
            for xyz in (None, '123', (1, 2), (1, 2, 3, 4), (True, 0, 0),
                        (1j, 0, 0), (math.nan, 0, 0), (math.inf, 0, 0), (10**400, 0, 0)):
                with (self.subTest(color_space=color_space, xyz=xyz),
                      self.assertRaises(ColorDomainError)):
                    xyz_to_rgb(xyz, color_space=color_space, reference_luminance=100)

    def test_missing_or_invalid_reference_luminance(self):
        with self.assertRaises(TypeError):
            xyz_to_rgb((0, 0, 0), color_space="srgb")
        for color_space in COLOR_SPACES:
            for reference in (None, '100', True, 1j, (100,), 0, -0.0, -100,
                              math.nan, math.inf, -math.inf, 10**400):
                with (self.subTest(color_space=color_space, reference=reference),
                      self.assertRaises(ColorDomainError)):
                    xyz_to_rgb((0, 0, 0), color_space=color_space, reference_luminance=reference)

    def test_required_exact_supported_color_space(self):
        with self.assertRaises(TypeError):
            xyz_to_rgb((0, 0, 0), reference_luminance=100)
        for color_space in (None, True, 1, 1.5, {}, [], "", "SRGB", "Display-P3", "dci-p3",
                            "srgb-linear", "display-p3-linear", "srgb ", " srgb"):
            with self.subTest(color_space=color_space), self.assertRaises(ColorDomainError):
                xyz_to_rgb((0, 0, 0), color_space=color_space, reference_luminance=100)

    def test_nonrepresentable_required_arithmetic_is_typed_error(self):
        for color_space in COLOR_SPACES:
            for xyz, reference in (((1, 0, 0), math.ulp(0.0)),
                                   ((sys.float_info.max, 0, 0), 1),
                                   ((sys.float_info.max,) * 3, 1)):
                with (self.subTest(color_space=color_space, xyz=xyz),
                      self.assertRaises(ColorDomainError)):
                    xyz_to_rgb(xyz, color_space=color_space, reference_luminance=reference)


class RGBGamutStatusTests(unittest.TestCase):
    white = (0.3127 / 0.3290, 1.0, 0.3583 / 0.3290)

    def test_black_keeps_cube_status_and_unavailable_triangles_separate(self):
        for space in COLOR_SPACES:
            status = rgb_gamut_status((0, 0, 0), color_space=space, reference_luminance=1)
            self.assertTrue(status.within_rgb_cube)
            self.assertFalse(status.positive_stimulus_eligible)
            self.assertEqual(status.below_zero_channels, (False,) * 3)
            self.assertEqual(status.above_one_channels, (False,) * 3)
            for projection in (status.xy, status.uv):
                self.assertIsNone(projection.coordinates)
                self.assertIsNone(projection.in_triangle)
                self.assertIn("undefined", projection.unavailable_reason)

    def test_neutral_over_scale_is_inside_triangles_but_outside_cube(self):
        for space in COLOR_SPACES:
            status = rgb_gamut_status(tuple(2 * v for v in self.white),
                                      color_space=space, reference_luminance=1)
            self.assertEqual(status.above_one_channels, (True,) * 3)
            self.assertEqual(status.below_zero_channels, (False,) * 3)
            self.assertTrue(status.positive_stimulus_eligible)
            self.assertFalse(status.within_rgb_cube)
            self.assertTrue(status.xy.in_triangle)
            self.assertTrue(status.uv.in_triangle)

    def test_signed_algebraic_coordinates_are_not_positive_stimulus_membership(self):
        for space in COLOR_SPACES:
            for xyz in (tuple(-v for v in self.white), (1, 0, 1), (1, 1, -1)):
                with self.subTest(space=space, xyz=xyz):
                    status = rgb_gamut_status(xyz, color_space=space, reference_luminance=1)
                    self.assertFalse(status.positive_stimulus_eligible)
                    for projection in (status.xy, status.uv):
                        self.assertIsNotNone(projection.coordinates)
                        self.assertIsNone(projection.in_triangle)
                        self.assertIn("nonnegative XYZ and positive Y",
                                      projection.unavailable_reason)

    def test_each_projection_can_be_unavailable_independently(self):
        cases = (((1, -1, 0), "xy"), ((1, 1, -16 / 3), "uv"))
        for xyz, unavailable in cases:
            status = rgb_gamut_status(xyz, color_space="srgb", reference_luminance=1)
            self.assertIsNone(getattr(status, unavailable).coordinates)
            available = status.uv if unavailable == "xy" else status.xy
            self.assertIsNotNone(available.coordinates)
            self.assertIsNone(available.in_triangle)

    def test_wider_triangle_and_volume_are_independent(self):
        xyz = (0.2, 0.3, 0.4)
        small = rgb_gamut_status(xyz, color_space="srgb", reference_luminance=1)
        wide = rgb_gamut_status(xyz, color_space="display-p3", reference_luminance=1)
        self.assertEqual(small.below_zero_channels, (True, False, False))
        self.assertEqual(small.above_one_channels, (False,) * 3)
        self.assertFalse(small.xy.in_triangle)
        self.assertFalse(small.uv.in_triangle)
        self.assertTrue(wide.xy.in_triangle)
        self.assertTrue(wide.uv.in_triangle)
        self.assertTrue(wide.within_rgb_cube)
        brighter = rgb_gamut_status(xyz, color_space="display-p3", reference_luminance=0.1)
        self.assertTrue(brighter.xy.in_triangle)
        self.assertTrue(brighter.uv.in_triangle)
        self.assertFalse(brighter.within_rgb_cube)

    def test_exact_channel_flags_and_native_preservation(self):
        xyz = list(self.white)
        for space in COLOR_SPACES:
            result = rgb_gamut_status(xyz, color_space=space, reference_luminance=1)
            channels = result.encoding.linear_rgb
            self.assertEqual(result.below_zero_channels, tuple(v < 0 for v in channels))
            self.assertEqual(result.above_one_channels, tuple(v > 1 for v in channels))
            self.assertEqual(result.within_rgb_cube, all(0 <= v <= 1 for v in channels))
            self.assertEqual(result.encoding.clipped_channels,
                             tuple(lo or hi for lo, hi in zip(result.below_zero_channels,
                                                            result.above_one_channels)))
            self.assertEqual(result.algorithm, "ideal-rgb-gamut-status")
            with self.assertRaises(FrozenInstanceError):
                result.within_rgb_cube = True
        self.assertEqual(xyz, list(self.white))

    def test_huge_and_subnormal_values_have_scale_free_chromaticity(self):
        for value in (math.ulp(0.0), sys.float_info.max):
            for space in COLOR_SPACES:
                result = rgb_gamut_status((value,) * 3, color_space=space,
                                          reference_luminance=value)
                self.assertAlmostEqual(result.xy.coordinates[0], 1 / 3, delta=1e-16)
                self.assertAlmostEqual(result.uv.coordinates[0], 4 / 19, delta=1e-16)
                self.assertAlmostEqual(result.uv.coordinates[1], 9 / 19, delta=1e-16)


class RGBNumericalBoundaryTests(unittest.TestCase):
    white = (0.3127 / 0.3290, 1.0, 0.3583 / 0.3290)

    def test_nominal_white_and_primary_boundaries_at_declared_scales(self):
        for space, primaries in (("srgb", SRGB_PRIMARY_XYZ), ("display-p3", P3_PRIMARY_XYZ)):
            for scale in (1, 100, 120):
                for relative in (self.white, *primaries):
                    with self.subTest(space=space, scale=scale, relative=relative):
                        xyz = tuple(value * scale for value in relative)
                        status = rgb_gamut_status(xyz, color_space=space, reference_luminance=scale)
                        self.assertEqual(status.numerical_state, "on-boundary")
                        expected = "on-boundary" if relative in primaries else "inside"
                        self.assertEqual(status.xy.numerical_state, expected)
                        self.assertEqual(status.uv.numerical_state, status.xy.numerical_state)
                        raw = status.encoding.linear_rgb
                        self.assertEqual(status.below_zero_channels, tuple(c < 0 for c in raw))
                        self.assertEqual(status.above_one_channels, tuple(c > 1 for c in raw))
                        self.assertEqual(status.max_lower_excess, max(0, *(-c for c in raw)))
                        self.assertEqual(status.max_upper_excess, max(0, *(c - 1 for c in raw)))
                        self.assertTrue(all(math.isfinite(e) and e > 0
                                            for e in status.numerical_error_bounds))

    def test_near_boundary_substantive_outside_and_inside_stay_distinct(self):
        for space, primaries in (("srgb", SRGB_PRIMARY_XYZ), ("display-p3", P3_PRIMARY_XYZ)):
            for red, expected in ((-1e-16, "on-boundary"), (-1e-10, "outside"), (0.2, "inside")):
                rgb = (red, 0.4, 0.6)
                xyz = tuple(math.fsum(c * primary[i] for c, primary in zip(rgb, primaries))
                            for i in range(3))
                with self.subTest(space=space, red=red):
                    status = rgb_gamut_status(xyz, color_space=space, reference_luminance=1)
                    self.assertEqual(status.numerical_state, expected)
                    self.assertEqual(status.xy.numerical_state, expected)
                    self.assertEqual(status.uv.numerical_state, expected)
                    self.assertEqual(status.below_zero_channels,
                                     tuple(c < 0 for c in status.encoding.linear_rgb))

    def test_boundary_band_remains_tight_against_independent_channel_excursions(self):
        for space, primaries in (("srgb", SRGB_PRIMARY_XYZ), ("display-p3", P3_PRIMARY_XYZ)):
            for red in (-1e-14, 0.2, 1.0 + 1e-14):
                rgb = (red, 0.4, 0.6)
                xyz = tuple(math.fsum(c * primary[i] for c, primary in zip(rgb, primaries))
                            for i in range(3))
                status = rgb_gamut_status(xyz, color_space=space, reference_luminance=1)
                self.assertLess(max(status.numerical_error_bounds), 1e-14)
                self.assertEqual(status.numerical_state, "inside" if red == 0.2 else "outside")
                if red < 0:
                    self.assertLess(status.encoding.linear_rgb[0], -5e-15)
                elif red > 1:
                    self.assertGreater(status.encoding.linear_rgb[0] - 1, 5e-15)

    def test_boundary_budget_contains_independent_exact_rational_roundoff(self):
        inverse = {
            "srgb": ((Fraction(12831, 3959), Fraction(-329, 214), Fraction(-1974, 3959)),
                     (Fraction(-851781, 878810), Fraction(1648619, 878810),
                      Fraction(36519, 878810)),
                     (Fraction(705, 12673), Fraction(-2585, 12673), Fraction(705, 667))),
            "display-p3": ((Fraction(446124, 178915), Fraction(-333277, 357830),
                            Fraction(-72051, 178915)),
                           (Fraction(-14852, 17905), Fraction(63121, 35810),
                            Fraction(423, 17905)),
                           (Fraction(11844, 330415), Fraction(-50337, 660830),
                            Fraction(316169, 330415))),
        }
        for space, matrix in inverse.items():
            for scale in (1.0, 100.0, 120.0):
                for index in range(41):
                    xyz = ((index - 20) * 0.17 * scale,
                           (index % 7 - 2) * 0.13 * scale, (index % 11) * 0.23 * scale)
                    status = rgb_gamut_status(xyz, color_space=space, reference_luminance=scale)
                    exact_xyz = tuple(Fraction.from_float(v) / Fraction.from_float(scale)
                                      for v in xyz)
                    for row, calculated, error_band in zip(matrix, status.encoding.linear_rgb,
                                                          status.numerical_error_bounds):
                        exact = sum(a * b for a, b in zip(row, exact_xyz))
                        error = abs(Fraction.from_float(calculated) - exact)
                        self.assertLessEqual(error, Fraction.from_float(error_band))

    def test_positive_channel_fit_uses_rgb_maximum_rather_than_y(self):
        for space, primaries in (("srgb", SRGB_PRIMARY_XYZ), ("display-p3", P3_PRIMARY_XYZ)):
            rgb = (0.1, 0.1, 1.6)
            xyz = tuple(100 * math.fsum(c * primary[i] for c, primary in zip(rgb, primaries))
                        for i in range(3))
            status = rgb_gamut_status(xyz, color_space=space, reference_luminance=100)
            self.assertLess(xyz[1], 100)
            self.assertAlmostEqual(status.required_positive_rgb_scale, 160, delta=1e-12)
            self.assertGreater(status.fit_positive_rgb_scale, status.required_positive_rgb_scale)
            fitted = rgb_gamut_status(xyz, color_space=space,
                                      reference_luminance=status.fit_positive_rgb_scale)
            self.assertIn(fitted.numerical_state, ("inside", "on-boundary"))
            self.assertEqual(fitted.above_one_channels, (False,) * 3)
            self.assertIsNone(status.fit_scale_reason)

    def test_fit_does_not_repair_signed_lower_channels(self):
        xyz = (20, 30, 40)
        status = rgb_gamut_status(xyz, color_space="srgb", reference_luminance=100)
        fitted = rgb_gamut_status(xyz, color_space="srgb",
                                  reference_luminance=status.fit_positive_rgb_scale)
        self.assertTrue(fitted.below_zero_channels[0])
        self.assertEqual(fitted.numerical_state, "outside")
        self.assertEqual(fitted.xy.numerical_state, "outside")
        self.assertEqual(xyz, (20, 30, 40))

    def test_black_signed_and_nonrepresentable_fit_are_explicit(self):
        black = rgb_gamut_status((0, 0, 0), color_space="srgb", reference_luminance=100)
        self.assertEqual(black.numerical_state, "on-boundary")
        self.assertIsNone(black.xy.numerical_state)
        self.assertEqual(black.required_positive_rgb_scale, 0)
        self.assertIsNone(black.fit_positive_rgb_scale)
        signed = rgb_gamut_status(tuple(-v for v in self.white), color_space="srgb",
                                  reference_luminance=1)
        self.assertEqual(signed.numerical_state, "outside")
        self.assertIsNone(signed.xy.numerical_state)
        huge = rgb_gamut_status((1e308, 0, 0), color_space="srgb", reference_luminance=1e308)
        self.assertIsNone(huge.required_positive_rgb_scale)
        self.assertIsNone(huge.fit_positive_rgb_scale)
        self.assertIn("not representable", huge.fit_scale_reason)
        self.assertTrue(all(math.isfinite(v) for v in huge.encoding.linear_rgb))


class RGBReferenceGeometryTests(unittest.TestCase):
    white = (100 * 0.3127 / 0.3290, 100.0, 100 * 0.3583 / 0.3290)

    def geometry(self, space="srgb", n=3, white=None, luminance=100):
        return rgb_reference_geometry(color_space=space, reference_luminance=luminance,
                                      reference_white=white, samples_per_edge=n)

    def test_nominal_reference_primaries_white_and_uv(self):
        for space, chromaticities in (
            ("srgb", ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06))),
            ("display-p3", ((0.68, 0.32), (0.265, 0.69), (0.15, 0.06))),
        ):
            result = self.geometry(space)
            for xy, uv, expected in zip(result.primaries_xy, result.primaries_uv, chromaticities):
                self.assertAlmostEqual(xy[0], expected[0], delta=1e-16)
                self.assertAlmostEqual(xy[1], expected[1], delta=1e-16)
                denominator = -2 * expected[0] + 12 * expected[1] + 3
                self.assertAlmostEqual(uv[0], 4 * expected[0] / denominator, delta=2e-16)
                self.assertAlmostEqual(uv[1], 9 * expected[1] / denominator, delta=2e-16)
            self.assertAlmostEqual(result.white_xy[0], 0.3127, delta=1e-16)
            self.assertAlmostEqual(result.white_xy[1], 0.3290, delta=1e-16)
            self.assertEqual(result.reference_luminance, 100)
            self.assertEqual(result.algorithm, "ideal-rgb-cube-surface")
            self.assertEqual(result.algorithm_version, "1")

    def test_unique_shell_count_topology_and_black_white(self):
        for n in (2, 3, 5):
            result = self.geometry(n=n)
            self.assertEqual(len(result.surface), n**3 - (n - 2)**3)
            self.assertEqual(len(result.triangles), 12 * (n - 1)**2)
            self.assertEqual(len(result.lines), 12 * (n - 1)**2)
            points = [p.linear_rgb for p in result.surface]
            self.assertEqual(len(set(points)), len(points))
            self.assertEqual(points, sorted(points))
            self.assertTrue(all(0 in p or 1 in p for p in points))
            black, white = result.surface[0], result.surface[-1]
            self.assertEqual(black.xyz, (0, 0, 0))
            self.assertIsNone(black.xy)
            self.assertIsNone(black.uv)
            self.assertEqual(white.xyz, result.white_xyz)
            self.assertIsNone(white.lab)
            for i, primary in enumerate(result.primaries_xyz):
                rgb = tuple(float(i == j) for j in range(3))
                self.assertEqual(result.surface[points.index(rgb)].xyz, primary)

    def test_mesh_is_closed_outward_and_lines_follow_grid(self):
        from collections import Counter
        for space in COLOR_SPACES:
            result = self.geometry(space, n=4)
            edges = Counter()
            for triangle in result.triangles:
                self.assertEqual(len(set(triangle)), 3)
                a, b, c = (result.surface[index].linear_rgb for index in triangle)
                ab, ac = tuple(v - u for u, v in zip(a, b)), tuple(v - u for u, v in zip(a, c))
                cross = (ab[1] * ac[2] - ab[2] * ac[1],
                         ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0])
                self.assertGreater(math.fsum(v * (u - 0.5) for u, v in zip(a, cross)), 0)
                for i, j in ((triangle[0], triangle[1]), (triangle[1], triangle[2]),
                             (triangle[2], triangle[0])):
                    edges[tuple(sorted((i, j)))] += 1
            self.assertTrue(all(count == 2 for count in edges.values()))
            self.assertEqual(len(set(result.lines)), len(result.lines))
            for i, j in result.lines:
                diffs = [abs(a - b) for a, b in zip(result.surface[i].linear_rgb,
                                                  result.surface[j].linear_rgb)]
                self.assertEqual(sum(d > 0 for d in diffs), 1)
                self.assertAlmostEqual(sum(diffs), 1 / 3, delta=1e-16)

    def test_lab_projection_uses_only_explicit_white(self):
        for space in COLOR_SPACES:
            no_white = self.geometry(space)
            result = self.geometry(space, white=self.white)
            self.assertIsNone(no_white.reference_white)
            self.assertTrue(all(point.lab is None for point in no_white.surface))
            self.assertEqual(result.reference_white, self.white)
            for point in result.surface:
                self.assertEqual(point.lab, xyz_to_lab(point.xyz, self.white))
            self.assertEqual(result.surface[0].lab, (0, 0, 0))
            other_white = self.geometry(space, white=(100, 100, 100))
            self.assertNotEqual(result.surface[-1].lab, other_white.surface[-1].lab)
            self.assertEqual(result.surface[-1].xyz, other_white.surface[-1].xyz)
            with self.assertRaises(FrozenInstanceError):
                result.reference_white = None

    def test_scale_changes_xyz_but_not_chromaticity_or_topology(self):
        small, large = self.geometry(luminance=1), self.geometry(luminance=100)
        self.assertEqual(small.primaries_xy, large.primaries_xy)
        self.assertEqual(small.triangles, large.triangles)
        self.assertEqual(small.lines, large.lines)
        for a, b in zip(small.surface, large.surface):
            self.assertEqual(tuple(100 * v for v in a.xyz), b.xyz)
            self.assertEqual(a.xy, b.xy)
            self.assertEqual(a.uv, b.uv)

    def test_invalid_geometry_domains_are_refused(self):
        for n in (None, True, 1, 0, -1, 66, 2.0, "3"):
            with self.subTest(n=n), self.assertRaises(ColorDomainError):
                self.geometry(n=n)
        for white in ((0, 1, 1), (1, -1, 1), (1, 2), (True, 1, 1), (math.inf, 1, 1)):
            with self.subTest(white=white), self.assertRaises(ColorDomainError):
                self.geometry(white=white)
        for scale in (0, -1, True, "100", math.nan, math.inf):
            with self.subTest(scale=scale), self.assertRaises(ColorDomainError):
                self.geometry(luminance=scale)
        with self.assertRaises(ColorDomainError):
            self.geometry(space="dci-p3")
        with self.assertRaises(ColorDomainError):
            self.geometry(luminance=sys.float_info.max)


if __name__ == '__main__':
    unittest.main()
