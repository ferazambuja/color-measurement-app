"""Explicit illuminants and reference-based object-color calculations; no acquisition."""

import bisect
import csv
import hashlib
import math
from dataclasses import dataclass, field
from importlib import resources
from numbers import Real

from .models import Spectrum
from .science import ColorDomainError, Triple, spectral_xyz

_ILLUMINANTS = {
    "D50": (
        "CIE_std_illum_D50",
        "10.25039/CIE.DS.etgmuqt5",
        "b23049c6f7b266c1c1fbe147aa271e8930ca02d6e569c5ae1804c036faea4193",
    ),
    "D65": (
        "CIE_std_illum_D65",
        "10.25039/CIE.DS.hjfjmt59",
        "e76f210bffff3d552ef7113025da5f325d5dfec200dd4b878b1a2f3a507032cb",
    ),
    "A": (
        "CIE_std_illum_A_1nm",
        "10.25039/CIE.DS.8jsxjrsn",
        "61ef23fe146b8b665c74706717ab28cec7db6c9022993490bdc71991f43cb59b",
    ),
}


@dataclass(frozen=True)
class IlluminantSpectrum:
    name: str
    spectrum: Spectrum
    source: str
    source_sha256: str
    interpolation: str
    limitation: str
    native_range_nm: tuple[int, int] = field(default=(300, 830), init=False)
    license: str = field(default="CC-BY-SA-4.0", init=False)


@dataclass(frozen=True)
class IlluminantWhite:
    name: str
    xyz: Triple
    observer: str
    reference_y: float
    unit: str
    scale: str
    source: str
    illuminant_sha256: str
    observer_sha256: str
    kind: str = field(default="cie-illuminant-reference", init=False)
    convention: str = field(
        default="CIE 1 nm inclusive sum, 360-830 nm; explicit white Y", init=False
    )


def _sample_table(grid, table):
    if (
        not isinstance(grid, (tuple, list))
        or not grid
        or any(type(value) not in (int, float) for value in grid)
    ):
        raise ColorDomainError("supply finite real wavelength samples")
    try:
        grid = tuple(float(value) for value in grid)
    except (ValueError, OverflowError) as error:
        raise ColorDomainError("wavelength samples exceed the finite calculation range") from error
    if any(not math.isfinite(value) for value in grid):
        raise ColorDomainError("supply finite real wavelength samples")
    if (
        grid[0] < table[0][0]
        or grid[-1] > table[-1][0]
        or any(b <= a for a, b in zip(grid, grid[1:]))
    ):
        raise ColorDomainError("wavelengths must increase within the supplied table range")
    native = tuple(row[0] for row in table)
    result = []
    for wavelength in grid:
        index = bisect.bisect_left(native, wavelength)
        if native[index] == wavelength:
            result.append(table[index][1])
        else:
            x0, y0 = table[index - 1]
            x1, y1 = table[index]
            result.append(y0 + (y1 - y0) * ((wavelength - x0) / (x1 - x0)))
    return grid, tuple(result)


def illuminant_spectrum(name: str, wavelengths_nm) -> IlluminantSpectrum:
    """Sample an authenticated original CIE D50, D65 or A relative SPD.

    Integer rows are exact; intermediate S(lambda) uses linear interpolation
    S0+(S1-S0)*(lambda-lambda0)/(lambda1-lambda0). No extrapolation or native
    measurement resampling occurs. Explicit positive ascending nm wavelengths
    must lie within 300-830. Output quantity is relative_spectral_power, unit 1.

    Sources: CIE datasets etgmuqt5 (D50), hjfjmt59 (D65), 8jsxjrsn (A):
    https://doi.org/10.25039/CIE.DS.etgmuqt5
    https://doi.org/10.25039/CIE.DS.hjfjmt59
    https://doi.org/10.25039/CIE.DS.8jsxjrsn
    Official metadata specifies linear interpolation for D50/D65. For A this
    table interpolation is an explicit approximation to its recommended formula.
    Unmodified/derived data retain CIE CC BY-SA 4.0 terms and digest identity.

    Example: illuminant_spectrum("D65", (560,)).spectrum.values == (100.0,).
    No absolute units, measured illumination or specimen applicability is implied.
    Unknown names, changed data and unusable grids raise ColorDomainError.
    """
    if not isinstance(name, str) or name not in _ILLUMINANTS:
        raise ColorDomainError("choose exactly D50, D65 or A")
    stem, doi, expected = _ILLUMINANTS[name]
    try:
        raw = (
            resources.files("color_measurement")
            .joinpath("data", "illuminants", stem + ".csv")
            .read_bytes()
        )
    except OSError as error:
        raise ColorDomainError("the required CIE illuminant resource is unavailable") from error
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ColorDomainError("the original CIE illuminant bytes changed")
    table = tuple(
        tuple(float(value) for value in row) for row in csv.reader(raw.decode("ascii").splitlines())
    )
    grid, values = _sample_table(wavelengths_nm, table)
    return IlluminantSpectrum(
        name,
        Spectrum(grid, values, "relative_spectral_power", "1"),
        "https://doi.org/" + doi,
        expected,
        "exact native samples; no extrapolation" if all(value.is_integer() for value in grid)
        else "linear table interpolation; no extrapolation",
        "Nominal relative SPD; A table interpolation approximates its recommended formula",
    )


def illuminant_white(
    name: str, *, observer: str, reference_y: Real, unit: str, scale: str, observer_csv: bytes
) -> IlluminantWhite:
    """Return a CIE SPD-integrated nominal white at an explicitly declared Y.

    W=Yref*sum(S_i*CMF_i)/sum(S_i*ybar_i), evaluated at 360:1:830 nm
    using the accepted authenticated spectral_xyz owner. Sources are the CIE
    dataset named by illuminant_spectrum and CIE1931 CMFs DOI xvudnb9b.
    Relative object normalization: Li (2008), p.22 equations (1)-(3), as cited
    by spectral_xyz. No photometric efficacy multiplier is used.

    Example: D65 at reference_y=100 gives approximately (95.0470558654,
    100, 108.882873640), with CIE1931_2 and explicit caller unit/scale.
    This is a theoretical reference, never native XYZ or a measured white.
    Observer must be CIE1931_2, Y positive/finite, unit/scale nonempty. No
    adaptation, sample normalization, calibration or physical validation occurs.
    These full-range 1 nm whites differ from truncated-grid integration whites
    and rounded CSS nominal xy references; retain their separate conventions.
    """
    try:
        y = float(reference_y)
    except (TypeError, ValueError, OverflowError) as error:
        raise ColorDomainError("reference_y must be a finite positive real number") from error
    if (
        isinstance(reference_y, bool)
        or not isinstance(reference_y, Real)
        or not math.isfinite(y)
        or y <= 0
        or any(not isinstance(value, str) or not value.strip() for value in (unit, scale))
    ):
        raise ColorDomainError("white requires positive finite reference_y and explicit unit/scale")
    grid = tuple(range(360, 831))
    spd = illuminant_spectrum(name, grid)
    integrated = spectral_xyz(
        Spectrum(grid, (1,) * len(grid), "reflectance", "1"),
        observer=observer,
        observer_csv=observer_csv,
        wavelength_convention="standard-air-nm",
        illuminant=spd.spectrum,
        illuminant_id=spd.source + "; " + spd.source_sha256,
    )
    xyz = tuple((component / integrated.values[1]) * y for component in integrated.values)
    if any(not math.isfinite(value) or value <= 0 for value in xyz):
        raise ColorDomainError("integrated white exceeds the positive finite calculation range")
    return IlluminantWhite(
        name,
        xyz,
        observer,
        y,
        unit,
        scale,
        spd.source,
        spd.source_sha256,
        integrated.observer_sha256,
    )


@dataclass(frozen=True)
class ReferenceReflectance:
    spectrum: Spectrum
    sample: Spectrum
    reference: Spectrum
    characterization: Spectrum | None
    characterized_values: tuple[float, ...]
    assume_ideal_white: bool
    setup_declaration: str
    matched_setup: bool
    nonfluorescent: bool
    relative_reference_floor: float
    algorithm: str = field(default="measured-reference-spectral-factor", init=False)
    algorithm_version: str = field(default="1", init=False)


def characterized_reflectance(
    sample: Spectrum,
    reference: Spectrum,
    *,
    characterization: Spectrum | None,
    assume_ideal_white: bool,
    setup_declaration: str,
    matched_setup: bool,
    nonfluorescent: bool,
) -> ReferenceReflectance:
    """Estimate R=sample/reference*characterized_reference on the actual native grid.

    Require declared spectral_radiance in equal W/(m^2 sr nm) or mW/(m^2 sr nm)
    and exactly equal sample/reference wavelengths. An explicit positive unit-one
    reflectance characterization may interpolate linearly to that grid, but must
    cover it fully. Measured spectra are not interpolated. An explicitly chosen
    ideal-white assumption supplies factor 1 and is retained distinctly.

    Sources: CIE S 017:2020 radiance/reflectance-factor definitions,
    https://cie.co.at/eilvterm/17-24-070 and 17-24-075; measured characterized
    reference workflow, Konica Minolta CS-S10w Professional v1.6, section 2.10.15.
    Caller explicitly confirms matched illumination/geometry/settings/scale and a
    nonfluorescent reflective object; the function does not verify that claim.

    Example: sample=(1,2), reference=(2,4), characterized factor=(0.8,0.6)
    gives derived fractions (0.4,0.3). Signed estimates and values above 1 remain.
    Reference bins must exceed sqrt(binary64 epsilon)*reference peak and be
    positive. This numerical floor is engineering policy, not measured SNR.
    No dark subtraction, smoothing, clipping, acquisition or native replacement
    occurs. Nonfinite required ratios/products raise ColorDomainError.
    """
    if (
        not isinstance(sample, Spectrum)
        or not isinstance(reference, Spectrum)
        or sample.quantity != "spectral_radiance"
        or reference.quantity != sample.quantity
        or sample.unit not in ("W/(m^2 sr nm)", "mW/(m^2 sr nm)")
        or sample.unit != reference.unit
        or sample.wavelengths_nm != reference.wavelengths_nm
        or len(sample.values) < 2
    ):
        raise ColorDomainError("reference ratio requires compatible same-grid declared radiance")
    if (
        type(assume_ideal_white) is not bool
        or nonfluorescent is not True
        or matched_setup is not True
        or not isinstance(setup_declaration, str)
        or not setup_declaration.strip()
    ):
        raise ColorDomainError(
            "declare matched nonfluorescent reference setup and ideal-white choice"
        )
    if assume_ideal_white:
        if characterization is not None:
            raise ColorDomainError("choose characterization or ideal-white assumption, not both")
        factors = (1.0,) * len(sample.values)
    else:
        if (
            not isinstance(characterization, Spectrum)
            or characterization.quantity != "reflectance"
            or characterization.unit != "1"
            or any(value <= 0 for value in characterization.values)
        ):
            raise ColorDomainError("supply positive unit-one characterized reference reflectance")
        _, factors = _sample_table(
            sample.wavelengths_nm,
            tuple(zip(characterization.wavelengths_nm, characterization.values)),
        )
    peak = max(reference.values)
    floor = math.sqrt(2.0**-52)
    if peak <= 0 or any(value <= 0 or value / peak <= floor for value in reference.values):
        raise ColorDomainError("reference contains nonpositive or numerically weak bins")
    values = tuple(
        (a / b) * factor for a, b, factor in zip(sample.values, reference.values, factors)
    )
    if any(not math.isfinite(value) for value in values):
        raise ColorDomainError(
            "reference-normalized reflectance exceeds the finite calculation range"
        )
    return ReferenceReflectance(
        Spectrum(sample.wavelengths_nm, values, "reflectance", "1"),
        sample,
        reference,
        characterization,
        factors,
        assume_ideal_white,
        setup_declaration,
        matched_setup,
        nonfluorescent,
        floor,
    )


def relight_reflectance(
    reflectance: ReferenceReflectance | Spectrum,
    *,
    illuminant: str | Spectrum,
    illuminant_id: str | None,
    observer: str,
    observer_csv: bytes,
    wavelength_convention: str,
    nonfluorescent: bool,
):
    """Compose explicit-factor relighting with the accepted spectral XYZ owner.

    XYZ=100*sum(R*S*CMF)/sum(S*ybar), with a grid-specific diffuser white.
    Named CIE D50/D65/A SPD uses authenticated original tables sampled onto
    the exact input grid. A supplied SPD must already be declared relative
    spectral power, unit 1, on that grid with a nonempty caller identity.
    Source: Li (2008), equations (1)-(3), cited by spectral_xyz; nominal
    illuminant dataset identity and interpolation are at illuminant_spectrum.

    Example: constant fractional R=0.5 under D65 returns Y=50 and half its
    grid-specific white. A measured-reference factor retains its original
    reference/model separately. No illumination is inferred from a white name
    attached to native XYZ. Signed factors remain; native spectra are unchanged.

    Require an explicit nonfluorescent model, CIE1931_2 and standard-air-nm.
    Existing spectral_xyz owns quantity/unit/grid/range refusals and metadata.
    This is not fluorescence compensation, bandpass correction, geometry
    correction, physical validation or automatic native XYZ enrichment.
    """
    if nonfluorescent is not True:
        raise ColorDomainError("declare the nonfluorescent reflectance model")
    spectrum = (
        reflectance.spectrum if isinstance(reflectance, ReferenceReflectance) else reflectance
    )
    if not isinstance(spectrum, Spectrum) or spectrum.quantity != "reflectance":
        raise ColorDomainError("relighting requires an explicit reflectance spectrum")
    if isinstance(illuminant, str):
        if illuminant_id is not None:
            raise ColorDomainError("named CIE illuminant supplies its own authenticated identity")
        selected = illuminant_spectrum(illuminant, spectrum.wavelengths_nm)
        spd = selected.spectrum
        identity = selected.source + "; SHA256=" + selected.source_sha256
    else:
        spd, identity = illuminant, illuminant_id
    return spectral_xyz(
        spectrum,
        observer=observer,
        observer_csv=observer_csv,
        wavelength_convention=wavelength_convention,
        illuminant=spd,
        illuminant_id=identity,
    )
