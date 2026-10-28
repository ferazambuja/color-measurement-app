"""Sourced nominal whites and explicit theoretical Lab references.

Oracles use exact rational xy values from pinned CSS Color 4 section 19 and
independent elementary XYZ/Lab equations. No spectra, color library or physical
measurement provide expected values. Tolerances concern binary64 arithmetic.
"""

import math
import sys
import unittest
from dataclasses import FrozenInstanceError, asdict, replace
from fractions import Fraction

from color_measurement.models import XYZ, Observation, ReportedValues
from color_measurement.science import (
    ColorDomainError,
    StandardReferenceDefinition,
    StandardReferenceWhite,
    rgb_gamut_status,
    rgb_reference_geometry,
    standard_reference_catalog,
    standard_reference_white,
    xyz_to_lab,
    xyz_to_rgb,
    xyz_to_xyy,
)

NOMINAL_XY = {
    "D50": (Fraction(3457, 10000), Fraction(3585, 10000)),
    "D65": (Fraction(3127, 10000), Fraction(3290, 10000)),
}
DECLARATIONS = {"observer": "CIE1931_2", "unit": "1", "scale": "relative"}


class StandardReferenceWhiteTests(unittest.TestCase):
    def test_catalog_is_immutable_sourced_nominal_data(self):
        catalogue = standard_reference_catalog()
        self.assertIsInstance(catalogue, tuple)
        self.assertEqual(tuple(entry.name for entry in catalogue), ("D50", "D65"))
        for definition in catalogue:
            with self.subTest(name=definition.name):
                self.assertIsInstance(definition, StandardReferenceDefinition)
                self.assertEqual(definition.xy, tuple(map(float, NOMINAL_XY[definition.name])))
                self.assertEqual(definition.observer, "CIE1931_2")
                self.assertEqual(definition.convention, "css-color-4-2026-10-09-nominal-xy")
                self.assertEqual(definition.nominal_precision, "four decimal places in x and y")
                self.assertEqual(
                    definition.source,
                    "https://www.w3.org/TR/2026/CRD-css-color-4-20261009/#color-conversion-code",
                )
                self.assertTrue(definition.limitations)
                self.assertIsInstance(definition.limitations, tuple)
                self.assertFalse(hasattr(definition, "reference_y"))
                with self.assertRaises(FrozenInstanceError):
                    definition.xy = (0.3, 0.3)

    def test_scaled_xyz_matches_independent_rational_equations(self):
        for name, (x, y) in NOMINAL_XY.items():
            for reference_y in (1, 100, 120, Fraction(7, 10), 1e-300):
                with self.subTest(name=name, reference_y=reference_y):
                    white = standard_reference_white(name, reference_y=reference_y, **DECLARATIONS)
                    rational_y = (reference_y if isinstance(reference_y, Fraction)
                                  else Fraction(str(reference_y)))
                    expected = (x * rational_y / y, rational_y, (1 - x - y) * rational_y / y)
                    self.assertEqual(white.reference_y, float(reference_y))
                    self.assertEqual(white.xyz[1], float(reference_y))
                    for actual, oracle in zip(white.xyz, expected):
                        self.assertTrue(math.isclose(actual, float(oracle),
                                                     rel_tol=2e-15, abs_tol=0))

    def test_reference_kind_is_separate_from_native_xyz_and_observations(self):
        white = standard_reference_white("D65", reference_y=100, **DECLARATIONS)
        self.assertIsInstance(white, StandardReferenceWhite)
        self.assertNotIsInstance(white, XYZ)
        self.assertNotIsInstance(white, Observation)
        self.assertEqual(white.kind, "theoretical-lab-reference")
        with self.assertRaises(FrozenInstanceError):
            white.reference_y = 120
        with self.assertRaises(FrozenInstanceError):
            white.kind = "physical"
        with self.assertRaisesRegex(ValueError, "Native XYZ"):
            ReportedValues(xyz=white)
        with self.assertRaises(TypeError):
            StandardReferenceWhite(**asdict(white))  # kind cannot be supplied or overridden

    def test_direct_construction_and_replacement_retain_valid_nominal_values(self):
        for name in NOMINAL_XY:
            for reference_y in (1, 100, 1e-300, math.ulp(0.0)):
                with self.subTest(name=name, reference_y=reference_y):
                    white = standard_reference_white(name, reference_y=reference_y, **DECLARATIONS)
                    fields = asdict(white)
                    fields.pop("kind")
                    self.assertEqual(StandardReferenceWhite(**fields), white)
                    self.assertEqual(replace(white), white)
                    declared = replace(white, unit="custom unit", scale="explicit user scale")
                    self.assertEqual(declared.xyz, white.xyz)
                    self.assertEqual(declared.reference_y, white.reference_y)
                    self.assertEqual((declared.unit, declared.scale),
                                     ("custom unit", "explicit user scale"))

    def test_direct_construction_and_replacement_refuse_context_and_coordinate_tampering(self):
        white = standard_reference_white("D65", reference_y=100, **DECLARATIONS)
        fields = asdict(white)
        fields.pop("kind")
        changes = (
            {"name": "D50"}, {"name": "d65"}, {"observer": "CIE1964_10"},
            {"reference_y": -100}, {"reference_y": math.nan}, {"reference_y": True},
            {"reference_y": 120}, {"unit": " "}, {"scale": None},
            {"xy": list(white.xy)}, {"xy": (0.3,)}, {"xy": (math.nan, 0.3290)},
            {"xy": (True, 0.3290)}, {"xy": (0.3127, 0.33)},
            {"xyz": list(white.xyz)}, {"xyz": (95.0, 100.0)},
            {"xyz": (math.nan, 100.0, white.xyz[2])},
            {"xyz": (True, 100.0, white.xyz[2])},
            {"xyz": (-white.xyz[0], 100.0, white.xyz[2])},
            {"xyz": (white.xyz[0], 120.0, white.xyz[2])},
            {"xyz": (95.0, 100.0, white.xyz[2])},
            {"source": "https://example.com/another-source"},
            {"convention": "another-convention"}, {"nominal_precision": "exact"},
            {"limitations": list(white.limitations)}, {"limitations": ()},
        )
        for change in changes:
            with self.subTest(change=change):
                with self.assertRaises(ColorDomainError):
                    StandardReferenceWhite(**{**fields, **change})
                with self.assertRaises(ColorDomainError):
                    replace(white, **change)

    def test_units_and_scale_are_explicit_retained_declarations(self):
        white = standard_reference_white("D65", observer="CIE1931_2", reference_y=120,
                                         unit="cd/m^2", scale="absolute")
        self.assertEqual((white.unit, white.scale, white.reference_y), ("cd/m^2", "absolute", 120))
        relative = standard_reference_white("D65", observer="CIE1931_2", reference_y=120,
                                            unit="1", scale="relative-user-scale")
        self.assertEqual(relative.xyz, white.xyz)
        self.assertNotEqual((relative.unit, relative.scale), (white.unit, white.scale))

    def test_all_context_arguments_are_required(self):
        arguments = {"name": "D65", "reference_y": 100, **DECLARATIONS}
        for key in arguments:
            with self.subTest(missing=key), self.assertRaises(TypeError):
                standard_reference_white(**{k: value for k, value in arguments.items() if k != key})

    def test_unsupported_names_have_no_alias_or_fallback(self):
        for name in ("d65", "D 65", " D65", "D65 ", "D50/2", "A", "E", "D60",
                     "", None, True, 65, ["D65"], {"name": "D65"}):
            with self.subTest(name=name), self.assertRaisesRegex(ColorDomainError, "name"):
                standard_reference_white(name, reference_y=100, **DECLARATIONS)

    def test_unsupported_observers_are_refused(self):
        for observer in ("CIE1964_10", "1931", "CIE1931_2 ", "", None, True, 2, ["CIE1931_2"]):
            with self.subTest(observer=observer), self.assertRaisesRegex(ColorDomainError,
                                                                         "observer"):
                standard_reference_white("D65", observer=observer, reference_y=100,
                                         unit="1", scale="relative")

    def test_invalid_reference_y_is_refused(self):
        for reference_y in (0, -0.0, -1, True, False, "100", None, [], 1 + 0j,
                            math.nan, math.inf, -math.inf, 10**1000):
            with self.subTest(reference_y=reference_y), self.assertRaisesRegex(ColorDomainError,
                                                                               "reference_y"):
                standard_reference_white("D65", reference_y=reference_y, **DECLARATIONS)

    def test_invalid_unit_and_scale_are_refused(self):
        for key in ("unit", "scale"):
            for invalid in ("", "  ", None, True, 1, [], {}):
                arguments = {**DECLARATIONS, key: invalid}
                with self.subTest(key=key, invalid=invalid), self.assertRaisesRegex(
                    ColorDomainError, "unit and scale"
                ):
                    standard_reference_white("D50", reference_y=100, **arguments)

    def test_existing_float_domain_limits_are_retained(self):
        for name in NOMINAL_XY:
            with self.subTest(name=name):
                white = standard_reference_white(name, reference_y=math.ulp(0.0), **DECLARATIONS)
                self.assertEqual(white.reference_y, math.ulp(0.0))
                self.assertTrue(all(component > 0 for component in white.xyz))
                with self.assertRaisesRegex(ColorDomainError, "Y/y"):
                    standard_reference_white(name, reference_y=sys.float_info.max, **DECLARATIONS)

    def test_metadata_agrees_with_selected_definition(self):
        for definition in standard_reference_catalog():
            white = standard_reference_white(definition.name, reference_y=100, **DECLARATIONS)
            for attribute in ("name", "observer", "xy", "source", "convention",
                              "nominal_precision", "limitations"):
                with self.subTest(name=definition.name, attribute=attribute):
                    self.assertEqual(getattr(white, attribute), getattr(definition, attribute))

    def test_lab_composition_uses_explicit_theoretical_white(self):
        # Independent perfect-cube ratios: roots .6, .5, .4 yield Lab (42, 50, 20).
        for name in NOMINAL_XY:
            for reference_y in (1, 100, 120):
                with self.subTest(name=name, reference_y=reference_y):
                    white = standard_reference_white(name, reference_y=reference_y, **DECLARATIONS)
                    xyz = tuple(component * ratio for component, ratio in
                                zip(white.xyz, (27 / 125, 1 / 8, 8 / 125)))
                    self.assertEqual(xyz_to_lab(white.xyz, white.xyz), (100.0, 0.0, 0.0))
                    for actual, expected in zip(xyz_to_lab(xyz, white.xyz), (42, 50, 20)):
                        self.assertAlmostEqual(actual, expected, delta=2e-12)
                    xy = xyz_to_xyy(white.xyz)
                    for actual, expected in zip(xy[:2], NOMINAL_XY[name]):
                        self.assertAlmostEqual(actual, float(expected), delta=2e-16)

    def test_selecting_lab_white_preserves_native_xyz_and_explicit_rgb_scale(self):
        native = XYZ(values=(-5.0, 60.0, 180.0), unit="cd/m^2", scale="absolute",
                     observer="CIE1931_2", illuminant="not_applicable")
        original = asdict(native)
        for space in ("srgb", "display-p3"):
            baseline = xyz_to_rgb(native.values, color_space=space, reference_luminance=120)
            gamut = rgb_gamut_status(native.values, color_space=space, reference_luminance=120)
            self.assertTrue(any(channel < 0 for channel in baseline.linear_rgb))
            lab_results = []
            for name in NOMINAL_XY:
                white = standard_reference_white(name, observer=native.observer, reference_y=100,
                                                 unit=native.unit, scale=native.scale)
                lab_results.append(xyz_to_lab(native.values, white.xyz))
                self.assertEqual(asdict(native), original)
                self.assertEqual(
                    xyz_to_rgb(native.values, color_space=space, reference_luminance=120), baseline
                )
                self.assertEqual(
                    rgb_gamut_status(native.values, color_space=space, reference_luminance=120),
                    gamut,
                )
            self.assertNotEqual(lab_results[0], lab_results[1])

    def test_reference_geometry_preserves_xyz_when_lab_white_changes(self):
        for space in ("srgb", "display-p3"):
            geometries = []
            for name in NOMINAL_XY:
                white = standard_reference_white(name, reference_y=100, **DECLARATIONS)
                geometries.append(rgb_reference_geometry(
                    color_space=space, reference_luminance=120,
                    reference_white=white.xyz, samples_per_edge=2,
                ))
            first, second = geometries
            self.assertEqual(first.reference_luminance, 120)
            self.assertEqual(second.reference_luminance, 120)
            self.assertEqual(tuple(point.xyz for point in first.surface),
                             tuple(point.xyz for point in second.surface))
            self.assertNotEqual(tuple(point.lab for point in first.surface),
                                tuple(point.lab for point in second.surface))


if __name__ == "__main__":
    unittest.main()


class CIEIlluminantTests(unittest.TestCase):
    def test_cie_illuminant_unrepresentable_integer_wavelength_refuses_as_domain_error(self):
        from color_measurement.illumination import illuminant_spectrum

        for grid in ((10**1000,), (-(10**1000),)):
            with self.subTest(grid=grid), self.assertRaisesRegex(
                ColorDomainError, "finite calculation range"
            ):
                illuminant_spectrum("D65", grid)

    def test_cie_illuminant_authentic_rows_fractional_sampling_and_refusal(self):
        from color_measurement.illumination import illuminant_spectrum

        for name in ("D50", "D65", "A"):
            result = illuminant_spectrum(name, (560, 561))
            self.assertEqual(result.spectrum.values[0], 100)
            midpoint = illuminant_spectrum(name, (560.5,)).spectrum.values[0]
            self.assertEqual(midpoint, sum(result.spectrum.values) / 2)
            self.assertEqual(result.spectrum.quantity, "relative_spectral_power")
            self.assertTrue(result.source_sha256 and result.source and result.license)
        for name, grid in (
            ("invalid", (560,)),
            ([], (560,)),
            ("A", (299, 300)),
            ("D65", (830, 831)),
            ("D50", (560, 560)),
            ("D50", (True,)),
            ("D50", (math.nan,)),
        ):
            with self.subTest(name=name, grid=grid), self.assertRaises(ColorDomainError):
                illuminant_spectrum(name, grid)

    def test_cie_illuminant_integrated_primary_whites_and_explicit_scale(self):
        from importlib import resources

        from color_measurement.illumination import illuminant_white

        csv = (
            resources.files("color_measurement")
            .joinpath("desktop_assets", "resources", "CIE_xyz_1931_2deg.csv")
            .read_bytes()
        )
        # Approximate regression targets, cross-checked by separate high-precision
        # Decimal arithmetic over the authenticated full-range official CSVs.
        golden = {
            "D50": (0.964240837883784, 1, 0.825128116837737),
            "D65": (0.950470558654283, 1, 1.08882873639588),
            "A": (1.09850315268456, 1, 0.355849301338885),
        }
        for name, target in golden.items():
            white = illuminant_white(
                name,
                observer="CIE1931_2",
                reference_y=203,
                unit="1",
                scale="explicit relative scale",
                observer_csv=csv,
            )
            for value, expected in zip(white.xyz, target):
                self.assertAlmostEqual(value, expected * 203, delta=2e-12)
            self.assertEqual(white.xyz[1], 203)
            self.assertEqual(white.kind, "cie-illuminant-reference")
        for y in (0, -1, True, math.inf, 10**1000):
            with self.subTest(y=y), self.assertRaises(ColorDomainError):
                illuminant_white(
                    "D65",
                    observer="CIE1931_2",
                    reference_y=y,
                    unit="1",
                    scale="relative",
                    observer_csv=csv,
                )


def test_cie_illuminant_changed_or_missing_resource_refuses(monkeypatch):
    from color_measurement import illumination

    class Source:
        def joinpath(self, *parts):
            return self

        def read_bytes(self):
            return b"560,100\n"

    monkeypatch.setattr(illumination.resources, "files", lambda package: Source())
    with __import__("pytest").raises(ColorDomainError, match="bytes changed"):
        illumination.illuminant_spectrum("D65", (560,))

    def missing(package):
        raise FileNotFoundError("test missing resource")

    monkeypatch.setattr(illumination.resources, "files", missing)
    with __import__("pytest").raises(ColorDomainError, match="unavailable"):
        illumination.illuminant_spectrum("D65", (560,))
