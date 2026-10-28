"""Primary CIEDE2000 data and analytical UCS/spectral software checks.

The published four-decimal ΔE00 data has 5e-5 absolute rounding tolerance.
The separate unequal-chroma value is from an earlier independent 85-digit
paper-equation evaluation, with 1e-10 absolute tolerance. No physical data,
MATLAB implementation, or second production colour library supplies an oracle.
"""

import hashlib
import inspect
import math
import sys
import unittest
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

from color_measurement import science
from color_measurement.models import Spectrum
from color_measurement.processing import SpectralResidual, compare_spectra
from color_measurement.science import ColorDomainError, delta_e_2000, xyz_to_uvy

DATA = Path(__file__).with_name("data") / "ciede2000.txt"


def published_pairs():
    return [tuple(map(float, line.split())) for line in DATA.read_text().splitlines()]


class CIEDE2000Tests(unittest.TestCase):
    def test_retained_published_fixture_identity(self):
        self.assertEqual(
            hashlib.sha256(DATA.read_bytes()).hexdigest(),
            "db3ff1756a6fe3da5e185897f73c83fa6be5a6d530b06f1e5979f02d6d80c85d",
        )
        self.assertEqual(len(published_pairs()), 34)
        self.assertTrue(all(len(row) == 7 for row in published_pairs()))

    def test_all_published_pairs_and_symmetry(self):
        for index, row in enumerate(published_pairs(), 1):
            a, b, expected = row[:3], row[3:6], row[6]
            with self.subTest(pair=index):
                result = delta_e_2000(a, b)
                self.assertAlmostEqual(result, expected, delta=5e-5)
                self.assertAlmostEqual(delta_e_2000(b, a), expected, delta=5e-5)
                self.assertAlmostEqual(result, delta_e_2000(b, a), delta=1e-12)

    def test_unequal_chroma_above_turn_mean_hue(self):
        a, b = (50, 20, -20), (50, 80, 80)
        self.assertAlmostEqual(delta_e_2000(a, b), 39.01817399392473, delta=1e-10)
        self.assertAlmostEqual(delta_e_2000(b, a), 39.01817399392473, delta=1e-10)

    def test_extra_oracle_rejects_wrong_above_turn_branch(self):
        # Mutate only the implemented above-turn branch in an isolated namespace.
        # This is a refusal proof, not a second maintained colour implementation.
        source = inspect.getsource(delta_e_2000)
        correct = "h_mean = (hue_sum - 360.0) / 2.0"
        wrong = "h_mean = (hue_sum + 360.0) / 2.0"
        self.assertEqual(source.count(correct), 1)
        namespace = vars(science).copy()
        exec(compile(source.replace(correct, wrong), "<wrong-mean-hue>", "exec"), namespace)
        mutant = namespace["delta_e_2000"]
        # The published vectors alone do not detect this particular defect.
        for row in published_pairs():
            self.assertAlmostEqual(mutant(row[:3], row[3:6]), row[6], delta=5e-5)
        self.assertGreater(
            abs(mutant((50, 20, -20), (50, 80, 80)) - 39.01817399392473), 1e-4
        )

    def test_identical_lab_is_zero_including_signed_and_neutral(self):
        for lab in ((50, 30, -20), (0, 0, 0), (-50, -2, 4), (100, -0.0, -0.0)):
            with self.subTest(lab=lab):
                self.assertEqual(delta_e_2000(lab, lab), 0.0)

    def test_neutral_lightness_has_independent_unit_weighting(self):
        # Mean L*=50 sets SL=1; chroma and hue terms vanish.
        self.assertEqual(delta_e_2000((40, 0, 0), (60, 0, 0)), 20.0)
        self.assertEqual(delta_e_2000((0, 0, 0), (100, 0, 0)), 100.0)

    def test_exact_180_degree_seam_uses_arithmetic_mean(self):
        # a*=0 leaves G irrelevant: h1=90, h2=270, mean=180, ΔH'=20.
        t = (1 - 0.17 * math.cos(math.radians(150)) + 0.24
             + 0.32 * math.cos(math.radians(546))
             - 0.20 * math.cos(math.radians(657)))
        expected = 20 / (1 + 0.15 * t)
        self.assertAlmostEqual(delta_e_2000((50, 0, 10), (50, 0, -10)), expected, delta=1e-12)
        self.assertAlmostEqual(delta_e_2000((50, 0, -10), (50, 0, 10)), expected, delta=1e-12)

    def test_large_finite_chroma_does_not_overflow_seventh_power(self):
        self.assertTrue(math.isfinite(delta_e_2000((50, 1e50, -1e50), (50, 2e50, 1e50))))

    def test_tiny_nonzero_chroma_retains_hue_difference(self):
        # a*=0 fixes C'=C and the modified hues at 90/270. SH rounds to 1.
        self.assertAlmostEqual(
            delta_e_2000((50, 0, 1e-200), (50, 0, -1e-200)) / 2e-200,
            1.0, delta=2e-15,
        )

    def test_invalid_inputs_have_typed_errors(self):
        invalid = (None, "123", (1, 2), (1, 2, 3, 4), (True, 0, 0),
                   (1j, 0, 0), (math.inf, 0, 0), (math.nan, 0, 0), (10**400, 0, 0))
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ColorDomainError):
                    delta_e_2000(values, (50, 0, 0))
                with self.assertRaises(ColorDomainError):
                    delta_e_2000((50, 0, 0), values)

    def test_nonrepresentable_required_arithmetic_has_typed_errors(self):
        maximum = sys.float_info.max
        for a, b in (((maximum, 0, 0), (-maximum, 0, 0)),
                     ((50, maximum, maximum), (50, 0, 0)),
                     ((50, 0, maximum), (50, 0, -maximum))):
            with self.subTest(a=a, b=b), self.assertRaises(ColorDomainError):
                delta_e_2000(a, b)


class UCS1976Tests(unittest.TestCase):
    def test_analytical_values_and_retained_y(self):
        self.assertEqual(xyz_to_uvy((2, 3, 5)), (8 / 62, 27 / 62, 3.0))
        self.assertEqual(xyz_to_uvy((0, 1, 0)), (0.0, 0.6, 1.0))
        self.assertEqual(xyz_to_uvy((-1, 2, 3)), (-4 / 38, 18 / 38, 2.0))

    def test_scale_invariance_including_signed_scale(self):
        for factor in (0.01, 1.0, 100.0, -2.0):
            with self.subTest(factor=factor):
                result = xyz_to_uvy(tuple(value * factor for value in (2, 3, 5)))
                self.assertAlmostEqual(result[0], 8 / 62, delta=2e-16)
                self.assertAlmostEqual(result[1], 27 / 62, delta=2e-16)
                self.assertEqual(result[2], 3 * factor)

    def test_zero_denominator_including_black_and_signed_cancellation(self):
        for values in ((0, 0, 0), (3, 0, -1), (-15, 1, 0), (1, 1, -16 / 3)):
            with self.subTest(values=values), self.assertRaisesRegex(ColorDomainError, "is zero"):
                xyz_to_uvy(values)

    def test_nonzero_small_denominator_has_no_cutoff(self):
        for value in (1e-300, math.ulp(0.0)):
            with self.subTest(value=value):
                self.assertEqual(xyz_to_uvy((value, 0, 0)), (4.0, 0.0, 0.0))

    def test_invalid_and_nonrepresentable_inputs(self):
        values = (None, "123", (1, 2), (1, 2, 3, 4), (True, 0, 0), (1j, 0, 0),
                  (math.inf, 0, 0), (math.nan, 0, 0), (10**400, 0, 0),
                  (sys.float_info.max, 0, 0), (0, sys.float_info.max, 0),
                  (1, 1, -math.nextafter(16 / 3, 0)))
        for xyz in values:
            with self.subTest(xyz=xyz):
                # The final near-cancellation case remains finite and valid;
                # a small nonzero denominator is not an error condition.
                if xyz == values[-1]:
                    self.assertTrue(all(math.isfinite(v) for v in xyz_to_uvy(xyz)))
                else:
                    with self.assertRaises(ColorDomainError):
                        xyz_to_uvy(xyz)


class SpectralComparisonTests(unittest.TestCase):
    def spectrum(self, values, *, wavelengths=None, quantity="reflectance", unit="%"):
        grid = wavelengths or tuple(400.0 + 5 * index for index in range(len(values)))
        return Spectrum(grid, tuple(values), quantity, unit)

    def test_signed_analytical_residual_and_rms(self):
        a, b = self.spectrum((1, 2)), self.spectrum((-2, 6))
        result = compare_spectra(a, b)
        self.assertEqual(result.residual.values, (-3.0, 4.0))
        self.assertEqual(result.residual.wavelengths_nm, a.wavelengths_nm)
        self.assertEqual((result.residual.source_quantity, result.residual.unit),
                         ("reflectance", "%"))
        self.assertAlmostEqual(result.rms, math.sqrt(12.5), delta=1e-15)
        self.assertEqual(a.values, (1, 2))
        self.assertEqual(b.values, (-2, 6))
        self.assertIsNot(result.residual, a)
        with self.assertRaises(FrozenInstanceError):
            result.rms = 0.0

    def test_extracted_residual_keeps_semantic_provenance(self):
        a, b = self.spectrum((1, 2)), self.spectrum((-2, 6))
        residual = compare_spectra(a, b).residual
        self.assertIsInstance(residual, SpectralResidual)
        self.assertNotIsInstance(residual, Spectrum)
        self.assertEqual(residual.algorithm, "spectral-residual-b-minus-a")
        self.assertEqual(residual.algorithm_version, "1")
        self.assertEqual(residual.source_quantity, "reflectance")
        projection = asdict(residual)
        self.assertNotIn("quantity", projection)
        self.assertEqual(projection["source_quantity"], "reflectance")
        with self.assertRaises(FrozenInstanceError):
            residual.source_quantity = "radiance"
        with self.assertRaises(ColorDomainError):
            compare_spectra(residual, a)

    def test_swapped_sign_and_rms_invariance(self):
        a, b = self.spectrum((-1, 2, 10)), self.spectrum((3, 1, 11))
        forward, reverse = compare_spectra(a, b), compare_spectra(b, a)
        self.assertEqual(reverse.residual.values, tuple(-v for v in forward.residual.values))
        self.assertEqual(reverse.rms, forward.rms)

    def test_integer_residual_is_subtracted_before_float_conversion(self):
        a, b = self.spectrum((2**53 + 1,)), self.spectrum((2**53,))
        result = compare_spectra(a, b)
        self.assertEqual(result.residual.values, (-1.0,))
        self.assertEqual(result.rms, 1.0)
        self.assertEqual(compare_spectra(b, a).residual.values, (1.0,))
        self.assertEqual(a.values, (2**53 + 1,))
        self.assertEqual(b.values, (2**53,))

    def test_zero_residual_and_single_point(self):
        a = self.spectrum((-1, 0, 120))
        result = compare_spectra(a, a)
        self.assertEqual(result.residual.values, (0.0, 0.0, 0.0))
        self.assertEqual(result.rms, 0.0)
        self.assertEqual(compare_spectra(self.spectrum((2,)), self.spectrum((-1,))).rms, 3.0)

    def test_irregular_identical_grid_uses_equal_sample_weights(self):
        grid = (400, 401, 730)
        result = compare_spectra(self.spectrum((0, 0, 0), wavelengths=grid),
                                 self.spectrum((0, 0, 3), wavelengths=grid))
        self.assertAlmostEqual(result.rms, math.sqrt(3), delta=5e-16)

    def test_incompatible_or_missing_inputs_are_refused(self):
        base = self.spectrum((1, 2))
        others = (None, (1, 2), self.spectrum((1, 2), wavelengths=(400, 405.0000001)),
                  self.spectrum((1,)), self.spectrum((1, 2), quantity="radiance"),
                  self.spectrum((1, 2), unit="1"))
        for other in others:
            with self.subTest(other=other):
                with self.assertRaises(ColorDomainError):
                    compare_spectra(base, other)
                with self.assertRaises(ColorDomainError):
                    compare_spectra(other, base)

    def test_records_enforce_nonempty_quantity_unit_and_grid(self):
        for args in (((), (), "reflectance", "%"),
                     ((400,), (1,), "", "%"), ((400,), (1,), "reflectance", ""),
                     ((400, 400), (1, 2), "reflectance", "%")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                Spectrum(*args)

    def test_rms_handles_large_and_tiny_residuals_before_squaring(self):
        for value in (1e308, 1e-200, math.ulp(0.0)):
            with self.subTest(value=value):
                result = compare_spectra(self.spectrum((0,) * 4), self.spectrum((value,) * 4))
                self.assertEqual(result.rms, value)
        result = compare_spectra(self.spectrum((0, 0)), self.spectrum((3e200, 4e200)))
        self.assertAlmostEqual(result.rms / 1e200, math.sqrt(12.5), delta=1e-15)

    def test_nonrepresentable_residual_is_refused(self):
        for value in (1e308, int(1e308)):
            with self.subTest(value=value), self.assertRaises(ColorDomainError):
                compare_spectra(self.spectrum((-value,)), self.spectrum((value,)))


if __name__ == "__main__":
    unittest.main()
