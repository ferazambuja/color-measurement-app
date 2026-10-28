"""Independent analytical/domain proof for the inverse CIE 1976 u'v'Y map.

Primary definition: CIE S 017:2020, e-ILV 17-23-073. Exact rational XYZ and
its defining forward ratios supply the oracle independently of either kernel.
Numerical tolerances cover binary64 arithmetic, not physical uncertainty.
"""

import math
import sys
import unittest
from fractions import Fraction

from color_measurement.science import ColorDomainError, uvy_to_xyz, xyz_to_uvy


class InverseUVYTests(unittest.TestCase):
    def assert_triple_close(self, actual, expected, *, absolute=2e-14, relative=2e-14):
        self.assertIsInstance(actual, tuple)
        self.assertEqual(len(actual), 3)
        for value, target in zip(actual, expected):
            self.assertTrue(math.isclose(value, float(target), rel_tol=relative, abs_tol=absolute),
                            f"{actual!r} differs from {expected!r}")

    def test_analytical_xyz_and_explicit_y(self):
        examples = (((8 / 62, 27 / 62, 3), (2, 3, 5)),
                    ((0, 3 / 5, 2), (0, 2, 0)),
                    ((1 / 4, 9 / 16, 1), (1, 1, 0)),
                    ((1 / 4, 1 / 2, 1), (9 / 8, 1, 5 / 8)))
        for uvy, expected in examples:
            with self.subTest(uvy=uvy):
                result = uvy_to_xyz(uvy)
                self.assert_triple_close(result, expected)
                self.assertEqual(result[1], uvy[2])

    def test_exact_rational_equations_for_signed_and_positive_xyz(self):
        for x in (Fraction(-7, 3), Fraction(0), Fraction(1, 10), Fraction(5)):
            for y in (Fraction(-2), Fraction(1, 7), Fraction(3)):
                for z in (Fraction(-5, 2), Fraction(0), Fraction(7, 3)):
                    denominator = x + 15 * y + 3 * z
                    if not denominator:
                        continue
                    uvy = (4 * x / denominator, 9 * y / denominator, y)
                    with self.subTest(xyz=(x, y, z)):
                        self.assert_triple_close(uvy_to_xyz(uvy), (x, y, z))

    def test_independent_inverse_equation_vectors(self):
        for u in (Fraction(-1, 5), Fraction(0), Fraction(1, 5), Fraction(3, 4)):
            for v in (Fraction(-1, 3), Fraction(1, 10), Fraction(3, 5), Fraction(2)):
                for y in (Fraction(-2), Fraction(1, 8), Fraction(120)):
                    expected = (9 * u * y / (4 * v), y, (12 - 3 * u - 20 * v) * y / (4 * v))
                    with self.subTest(uvy=(u, v, y)):
                        self.assert_triple_close(uvy_to_xyz((u, v, y)), expected,
                                                 absolute=2e-13)

    def test_signed_components_are_not_clamped(self):
        for uvy in ((-0.2, 0.5, 1), (0.2, -0.5, 1), (0.2, 0.5, -1), (0.2, 0.8, 1)):
            with self.subTest(uvy=uvy):
                self.assertTrue(any(component < 0 for component in uvy_to_xyz(uvy)))

    def test_y_scaling_is_shared_without_normalization(self):
        base = uvy_to_xyz((0.25, 0.5, 1))
        for scale in (-120, -1, 0.01, 1, 100, 120):
            with self.subTest(scale=scale):
                result = uvy_to_xyz((0.25, 0.5, scale))
                self.assertEqual(result[1], scale)
                self.assert_triple_close(result, tuple(component * scale for component in base))

    def test_zero_v_is_refused_even_for_zero_y(self):
        for v in (0, -0.0):
            for y in (0, 1, -2):
                with self.subTest(v=v, y=y), self.assertRaisesRegex(ColorDomainError, "v' is zero"):
                    uvy_to_xyz((0.2, v, y))

    def test_black_loses_chromaticity_and_cannot_roundtrip(self):
        for uvy in ((0.2, 0.5, 0), (sys.float_info.max, sys.float_info.max, 0)):
            with self.subTest(uvy=uvy):
                self.assertEqual(uvy_to_xyz(uvy), (0.0, 0.0, 0.0))
                with self.assertRaises(ColorDomainError):
                    xyz_to_uvy(uvy_to_xyz(uvy))

    def test_nonzero_xyz_with_zero_y_has_no_inverse(self):
        xyz = (2, 0, 3)
        uvy = xyz_to_uvy(xyz)
        self.assertEqual(uvy[1:], (0.0, 0.0))
        with self.assertRaises(ColorDomainError):
            uvy_to_xyz(uvy)

    def test_no_arbitrary_cutoff_for_nonzero_v(self):
        for tiny in (1e-300, math.ulp(0.0)):
            with self.subTest(tiny=tiny):
                result = uvy_to_xyz((0.25, tiny, tiny))
                self.assertEqual(result[1], tiny)
                self.assert_triple_close(result, (9 / 16, tiny, 45 / 16))

    def test_xyz_roundtrip_only_on_information_preserving_domain(self):
        for xyz in ((2, 3, 5), (-1, 2, 3), (4, -2, 1), (-4, -2, -1),
                    (1e-100, 2e-100, 3e-100)):
            with self.subTest(xyz=xyz):
                self.assert_triple_close(uvy_to_xyz(xyz_to_uvy(xyz)), xyz,
                                         absolute=0, relative=2e-14)

    def test_uvy_roundtrip_on_valid_representations(self):
        for uvy in ((0.2, 0.5, 100), (-0.3, 0.4, 120), (0.2, -0.5, 1),
                    (0.75, 2, -2)):
            with self.subTest(uvy=uvy):
                self.assert_triple_close(xyz_to_uvy(uvy_to_xyz(uvy)), uvy)

    def test_invalid_inputs_have_typed_errors(self):
        invalid = (None, "123", b"123", (1, 2), (1, 2, 3, 4), (True, 0.5, 1),
                   (0.2, False, 1), (0.2, 0.5, True), (1j, 0.5, 1),
                   (math.inf, 0.5, 1), (0.2, math.nan, 1), (0.2, 0.5, -math.inf),
                   (10**1000, 0.5, 1))
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ColorDomainError):
                uvy_to_xyz(values)

    def test_nonfinite_required_arithmetic_is_refused(self):
        maximum = sys.float_info.max
        cases = ((0.2, math.ulp(0.0), 1),  # Y/v'
                 (maximum, 0.5, 1),       # u'Y/v'
                 (maximum / 2, 1, 1),     # X after finite u'Y/v'
                 (0.0, 1, maximum / 4),   # -5Y
                 (0.0, 0.25, maximum / 4)) # 3Y/v'
        for uvy in cases:
            with self.subTest(uvy=uvy), self.assertRaises(ColorDomainError):
                uvy_to_xyz(uvy)


if __name__ == "__main__":
    unittest.main()
