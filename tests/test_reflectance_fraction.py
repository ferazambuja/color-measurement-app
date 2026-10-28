"""Unit-conversion contracts use synthetic observations, never instrument IO."""

from dataclasses import replace

import pytest

from color_measurement.exports import to_json
from color_measurement.models import Spectrum
from color_measurement.operations import SimulatedDevice
from color_measurement.processing import reflectance_fraction


@pytest.fixture
def reading():
    base = list(SimulatedDevice().measure())[-1].observation
    return replace(
        base,
        requested_mode="reflective_spot_m1",
        actual_mode="reflective_spot_m1",
        configuration=(("native_mtype", "5"), ("native_mcond", "1"), ("spectrum_norm", "100.0")),
        backend_reported=replace(
            base.backend_reported,
            spectrum=Spectrum(
                (402.5, 407.5, 412.5, 417.5), (-0.125, 1.25, 120.25, 0), "reflectance", "%"
            ),
        ),
    )


def test_signed_above_one_and_zero_preserved_without_changing_native_record(reading):
    before = to_json(reading)
    converted = reflectance_fraction(reading)
    assert converted.values == (-0.00125, 0.0125, 1.2025, 0)
    assert converted.wavelengths_nm == reading.backend_reported.spectrum.wavelengths_nm
    assert converted.unit == "1" and converted.quantity == "reflectance"
    assert converted is not reading.backend_reported.spectrum
    assert to_json(reading) == before


@pytest.mark.parametrize("percent, fraction", [(100, 1), (50, 0.5), (0, 0)])
def test_flat_spectra_on_36_band_grid_check_scale_only(reading, percent, fraction):
    spectrum = Spectrum(tuple(range(380, 731, 10)), (percent,) * 36, "reflectance", "%")
    reading = replace(
        reading, backend_reported=replace(reading.backend_reported, spectrum=spectrum)
    )
    converted = reflectance_fraction(reading)
    assert converted.wavelengths_nm == tuple(range(380, 731, 10))
    assert converted.values == (fraction,) * 36


@pytest.mark.parametrize(
    "field,value",
    [
        ("native_mtype", "1"),
        ("native_mtype", None),
        ("native_mcond", "0"),
        ("native_mcond", None),
        ("spectrum_norm", "1"),
        ("spectrum_norm", "0"),
        ("spectrum_norm", "nan"),
        ("spectrum_norm", "inf"),
        ("spectrum_norm", "nonsense"),
        ("spectrum_norm", None),
    ],
)
def test_inconsistent_or_missing_native_context_refused(reading, field, value):
    config = dict(reading.configuration)
    if value is None:
        del config[field]
    else:
        config[field] = value
    with pytest.raises(ValueError, match="Expected M1 percent reflectance"):
        reflectance_fraction(replace(reading, configuration=tuple(config.items())))


@pytest.mark.parametrize(
    "quantity,unit", [("spectral_radiance", "%"), ("reflectance", "1")]
)
def test_norm_100_does_not_identify_quantity_or_unit(reading, quantity, unit):
    spectrum = replace(reading.backend_reported.spectrum, quantity=quantity, unit=unit)
    changed = replace(
        reading, backend_reported=replace(reading.backend_reported, spectrum=spectrum)
    )
    with pytest.raises(ValueError, match="Expected M1 percent reflectance"):
        reflectance_fraction(changed)


@pytest.mark.parametrize("mode", ["emissive_spot_adaptive", "reflective_spot_m0"])
def test_other_mode_refused_even_with_reflectance_metadata(reading, mode):
    with pytest.raises(ValueError, match="Expected M1 percent reflectance"):
        reflectance_fraction(replace(reading, requested_mode=mode, actual_mode=mode))


def test_missing_spectrum_and_wrong_input_refused(reading):
    with pytest.raises(ValueError):
        reflectance_fraction(
            replace(reading, backend_reported=replace(reading.backend_reported, spectrum=None))
        )
    with pytest.raises(ValueError):
        reflectance_fraction(None)


def test_reference_factor_characterization_interpolation_and_native_preservation():
    from dataclasses import asdict

    from color_measurement.illumination import characterized_reflectance

    grid = (450, 500, 550)
    a = Spectrum(grid, (-1, 2, 6), "spectral_radiance", "W/(m^2 sr nm)")
    b = Spectrum(grid, (2, 4, 4), "spectral_radiance", "W/(m^2 sr nm)")
    c = Spectrum((450, 550), (0.8, 0.6), "reflectance", "1")
    before = asdict(a), asdict(b), asdict(c)
    result = characterized_reflectance(
        a,
        b,
        characterization=c,
        assume_ideal_white=False,
        setup_declaration="matched synthetic geometry/illumination",
        matched_setup=True,
        nonfluorescent=True,
    )
    assert result.spectrum.values == pytest.approx((-0.4, 0.35, 0.9))
    assert result.characterized_values == pytest.approx((0.8, 0.7, 0.6))
    assert result.spectrum.wavelengths_nm == grid and result.spectrum.unit == "1"
    assert (asdict(a), asdict(b), asdict(c)) == before


def test_reference_factor_ideal_white_and_model_grid_unit_weak_bin_refusals():
    from color_measurement.illumination import characterized_reflectance
    from color_measurement.science import ColorDomainError

    a = Spectrum((450, 550), (1, 6), "spectral_radiance", "mW/(m^2 sr nm)")
    b = replace(a, values=(2, 4))
    params = dict(
        characterization=None,
        assume_ideal_white=True,
        setup_declaration="synthetic matched setup",
        matched_setup=True,
        nonfluorescent=True,
    )
    result = characterized_reflectance(a, b, **params)
    assert result.spectrum.values == (0.5, 1.5) and result.assume_ideal_white
    for params2 in (
        {"assume_ideal_white": False},
        {"nonfluorescent": False},
        {"matched_setup": False},
        {"setup_declaration": ""},
        {"characterization": Spectrum((450, 550), (1, 1), "reflectance", "1")},
    ):
        with pytest.raises(ColorDomainError):
            characterized_reflectance(a, b, **(params | params2))
    for bad in (
        replace(b, unit="W/(m^2 sr nm)"),
        replace(b, quantity="reflectance"),
        replace(b, wavelengths_nm=(451, 551)),
        replace(b, values=(0, 4)),
        replace(b, values=(1e-10, 4)),
    ):
        with pytest.raises(ColorDomainError):
            characterized_reflectance(a, bad, **params)


def test_reference_factor_named_relighting_uses_grid_specific_white_and_explicit_model():
    from importlib import resources

    from color_measurement.illumination import relight_reflectance
    from color_measurement.science import ColorDomainError

    csv = (
        resources.files("color_measurement")
        .joinpath("desktop_assets", "resources", "CIE_xyz_1931_2deg.csv")
        .read_bytes()
    )
    spectrum = Spectrum(tuple(range(380, 781, 10)), (0.5,) * 41, "reflectance", "1")
    params = dict(
        observer="CIE1931_2",
        observer_csv=csv,
        wavelength_convention="standard-air-nm",
        illuminant_id=None,
        nonfluorescent=True,
    )
    result = relight_reflectance(spectrum, illuminant="D65", **params)
    assert result.values[1] == pytest.approx(50, abs=2e-14)
    assert result.values == pytest.approx(tuple(value / 2 for value in result.reference_white))
    assert result.limited_range and "SHA256=" in result.illuminant_id
    with pytest.raises(ColorDomainError):
        relight_reflectance(spectrum, illuminant="D65", **(params | {"nonfluorescent": False}))
    with pytest.raises(ColorDomainError):
        relight_reflectance(spectrum, illuminant="D65", **(params | {"illuminant_id": "fake"}))
