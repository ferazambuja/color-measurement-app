"""Original scalar color calculations using explicit caller-supplied conditions.

These functions operate on Python binary64 floats. They do not infer a white,
modify supplied observations, or establish physical validity. Explicit Bradford
adaptation predicts corresponding colors between supplied whites. Display
encoding retains signed linear results separately from its clipped output.
"""

from __future__ import annotations

import csv
import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from numbers import Real
from typing import Literal

from .models import Spectrum

Triple = tuple[float, float, float]
Pair = tuple[float, float]

_DELTA = 6.0 / 29.0
_EPSILON = _DELTA**3
_LINEAR_SLOPE = 841.0 / 108.0
_OFFSET = 4.0 / 29.0
# Single matrix authority: exact RGB-to-XYZ ratios, CSS Color 4 section 19.
_RGB_TO_XYZ_EXACT = {
    "srgb": (
        (Fraction(506752, 1228815), Fraction(87881, 245763), Fraction(12673, 70218)),
        (Fraction(87098, 409605), Fraction(175762, 245763), Fraction(12673, 175545)),
        (Fraction(7918, 409605), Fraction(87881, 737289), Fraction(1001167, 1053270)),
    ),
    "display-p3": (
        (Fraction(608311, 1250200), Fraction(189793, 714400), Fraction(198249, 1000160)),
        (Fraction(35783, 156275), Fraction(247089, 357200), Fraction(198249, 2500400)),
        (Fraction(0), Fraction(32229, 714400), Fraction(5220557, 5000800)),
    ),
}


class ColorDomainError(ValueError):
    """A calculation has invalid inputs, an undefined value, or numeric overflow."""


def _inverse_matrix(matrix: tuple[tuple[Fraction, ...], ...]) -> tuple[tuple[Fraction, ...], ...]:
    """Invert a fixed nonsingular 3x3 reference matrix by exact row reduction.

    [M | I] becomes [I | M^-1]. Only source-defined reference matrices
    call this helper; no measured values or floating tolerances participate.
    """
    rows = [list(row) + [Fraction(i == j) for j in range(3)]
            for i, row in enumerate(matrix)]
    for column in range(3):
        pivot = next(i for i in range(column, 3) if rows[i][column])
        rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        rows[column] = [value / divisor for value in rows[column]]
        for i in range(3):
            if i != column:
                factor = rows[i][column]
                rows[i] = [a - factor * b for a, b in zip(rows[i], rows[column])]
    return tuple(tuple(row[3:]) for row in rows)


_RGB_TO_XYZ = {name: tuple(tuple(float(c) for c in row) for row in matrix)
               for name, matrix in _RGB_TO_XYZ_EXACT.items()}
_XYZ_TO_LINEAR_RGB = {
    name: tuple(tuple(float(c) for c in row) for row in _inverse_matrix(matrix))
    for name, matrix in _RGB_TO_XYZ_EXACT.items()
}

# ICC.1:2004-10 Annex E.3, equation E.1. Invert these published decimal
# coefficients exactly, rather than using a separately rounded inverse table.
_BRADFORD_EXACT = tuple(
    tuple(Fraction(value) for value in row)
    for row in (("0.8951", "0.2664", "-0.1614"),
                ("-0.7502", "1.7135", "0.0367"),
                ("0.0389", "-0.0685", "1.0296"))
)
_BRADFORD = tuple(tuple(float(value) for value in row) for row in _BRADFORD_EXACT)
_BRADFORD_INVERSE = tuple(
    tuple(float(value) for value in row) for row in _inverse_matrix(_BRADFORD_EXACT)
)

_CIE1931_SHA256 = "fa663e3535a7e0763a745993a1f0a192eb0275ac46ad2d1befd7626841e713c1"
_CIE1931_SOURCE = "https://doi.org/10.25039/CIE.DS.xvudnb9b"
# Rounded standard-air maximum luminous efficacy, BIPM/CIE G1 (2019),
# equation (4); K_cd=683 lm/W applies at 540 THz, not at the V(lambda) peak.
_PHOTOPIC_EFFICACY = 683.002


def _finite(value: float, name: str) -> float:
    if not math.isfinite(value):
        raise ColorDomainError(f"{name} is not finite at float precision")
    return value


def _triple(values: Sequence[Real], name: str) -> Triple:
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes, bytearray))
        or len(values) != 3
    ):
        raise ColorDomainError(f"{name} must be a sequence of exactly three real numbers")
    converted = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ColorDomainError(f"{name} must contain real numbers, excluding bool")
        try:
            number = float(value)
        except (OverflowError, ValueError) as error:
            raise ColorDomainError(f"{name} cannot be represented as finite floats") from error
        converted.append(_finite(number, name))
    return converted[0], converted[1], converted[2]


def _white(reference_white: Sequence[Real]) -> Triple:
    white = _triple(reference_white, "reference_white")
    if any(component <= 0.0 for component in white):
        raise ColorDomainError("reference_white components must be strictly positive")
    return white


def _sum(values: tuple[float, ...], name: str) -> float:
    try:
        result = math.fsum(values)
    except OverflowError as error:
        raise ColorDomainError(f"{name} overflows float precision") from error
    return _finite(result, name)


def _result(values: Triple) -> Triple:
    for value in values:
        _finite(value, "conversion result")
    return values


def adapt_xyz(
    xyz: Sequence[Real], source_white: Sequence[Real], destination_white: Sequence[Real]
) -> Triple:
    """Return full linear Bradford corresponding XYZ between explicit whites.

    With B the published Bradford matrix, normalize each white to Y=1 and
    calculate B^-1 diag((B*destination)/(B*source)) B*XYZ. Only the whites
    are normalized: signed sample XYZ retains its input scale. A source white
    at Y=203 maps to the destination chromaticity at Y=203, irrespective of
    the destination white's original Y. Use that scale for subsequent Lab.

    Source: ICC.1:2004-10 Annex E.3, equations E.1-E.4 (full adaptation,
    omitted blue-channel nonlinearity): https://www.color.org/ICC1V42.pdf#page=102
    This implementation uses the published B coefficients and their exact
    rational inverse rounded to binary64; it does not translate another library.

    Example: adapt_xyz((20, 30, 40), (1, 1, 1), (2, 2, 2)) returns
    (20.0, 30.0, 40.0), since these whites have identical chromaticity.

    Whites must have finite positive XYZ and normalized Bradford responses.
    Caller must establish a common observer and compatible XYZ conventions.
    ColorDomainError refuses invalid inputs or nonfinite arithmetic. No
    clipping, partial adaptation, appearance prediction, spectral relighting,
    viewing-condition inference or change to native records takes place.
    """
    sample = _triple(xyz, "xyz")
    normalized = []
    for value in (source_white, destination_white):
        white = _white(value)
        white = _result(tuple(component / white[1] for component in white))
        if any(component <= 0.0 for component in white):
            raise ColorDomainError("white normalization must retain positive components")
        normalized.append(white)
    source, destination = normalized
    source_cones = _matrix_product(_BRADFORD, source)
    destination_cones = _matrix_product(_BRADFORD, destination)
    if any(component <= 0.0 for component in (*source_cones, *destination_cones)):
        raise ColorDomainError("whites must give positive Bradford cone responses")
    if source == destination:
        return sample
    ratios = _result(tuple(b / a for a, b in zip(source_cones, destination_cones)))
    cones = _matrix_product(_BRADFORD, sample)
    return _matrix_product(_BRADFORD_INVERSE, _result(tuple(
        ratio * cone for ratio, cone in zip(ratios, cones)
    )))


@dataclass(frozen=True)
class SpectralXYZ:
    """A separate derived result, with original spectra and numerical context.

    ``values`` are never inserted into native ``ReportedValues``. For absolute
    inputs Y has the declared photometric unit; reflectance uses a grid-specific
    perfect-diffuser Y=100 and supplies its matching ``reference_white``.
    Retain the owning observation for geometry, calibration and provenance.
    """

    values: Triple
    spectrum: Spectrum
    illuminant: Spectrum | None
    illuminant_id: str | None
    unit: str
    scale: str
    reference_white: Triple | None
    wavelength_step_nm: float
    limited_range: bool
    photometric_factor: float | None
    observer: str = field(default="CIE1931_2", init=False)
    observer_source: str = field(default=_CIE1931_SOURCE, init=False)
    observer_sha256: str = field(default=_CIE1931_SHA256, init=False)
    wavelength_convention: str = field(default="standard-air-nm", init=False)
    cmf_interpolation: str = field(default="linear; no extrapolation", init=False)
    integration: str = field(default="uniform inclusive sum; full endpoint weights", init=False)
    algorithm: str = field(default="cie1931-spectral-xyz", init=False)
    algorithm_version: str = field(default="1", init=False)


def _spectral_grid(spectrum: Spectrum) -> tuple[tuple[float, ...], float]:
    if not isinstance(spectrum, Spectrum):
        raise ColorDomainError("spectral integration requires a Spectrum record")
    wavelengths = tuple(float(value) for value in spectrum.wavelengths_nm)
    if len(wavelengths) < 2:
        raise ColorDomainError("spectral integration requires at least two wavelengths")
    if wavelengths[0] < 360.0 or wavelengths[-1] > 830.0:
        raise ColorDomainError("spectral integration must stay within 360-830 nm")
    step = (wavelengths[-1] - wavelengths[0]) / (len(wavelengths) - 1)
    if step <= 0.0:
        raise ColorDomainError("spectral integration requires a positive wavelength step")
    # Test the complete grid against its endpoint-defined uniform progression.
    # Allow binary64 roundoff, capped so nearly coincident irregular bins refuse.
    tolerance = min(4.0 * math.ulp(wavelengths[-1]), step * 1e-8)
    if any(abs(value - (wavelengths[0] + index * step)) > tolerance
           for index, value in enumerate(wavelengths)):
        raise ColorDomainError("spectral integration requires a uniform wavelength grid")
    return wavelengths, step


def _cie1931_matching(csv_bytes: bytes, wavelengths: tuple[float, ...]) -> tuple[Triple, ...]:
    if not isinstance(csv_bytes, bytes) or hashlib.sha256(csv_bytes).hexdigest() != _CIE1931_SHA256:
        raise ColorDomainError("observer CSV must be the authenticated original CIE 1931 table")
    rows = tuple(tuple(float(value) for value in row)
                 for row in csv.reader(csv_bytes.decode("ascii").splitlines()))
    matching = []
    for wavelength in wavelengths:
        low = math.floor(wavelength)
        fraction = wavelength - low
        a = rows[low - 360][1:]
        if fraction == 0.0:
            matching.append(a)
        else:
            b = rows[low - 359][1:]
            matching.append(tuple(x + fraction * (y - x) for x, y in zip(a, b)))
    return tuple(matching)


def spectral_xyz(
    spectrum: Spectrum, *, observer: str, observer_csv: bytes,
    wavelength_convention: str, illuminant: Spectrum | None = None,
    illuminant_id: str | None = None,
) -> SpectralXYZ:
    """Integrate a declared spectrum with authenticated CIE 1931 2-degree CMFs.

    For radiance/irradiance, XYZ = 683.002*sum(P_i*CMF_i*delta_lambda),
    with P in W per nm. Y is cd/m^2 or lux. The rounded 683.002 lm/W is the
    standard-air maximum luminous efficacy in BIPM/CIE G1 (2019), equations
    (4), (14) and section 9: https://www.bipm.org/documents/20126/52718503/G1-2019.pdf
    For reflectance, XYZ = 100*sum(R_i*S_i*CMF_i)/sum(S_i*ybar_i),
    R a fraction, with the same formula at R=1 providing the diffuser white.
    Source: Li (2008), CIE TC1-71 Perspective, p.22, equations (1)-(3):
    https://library.imaging.org/admin/apis/public/api/ist/website/downloadArticle/cic/16/1/art00005

    Supply the original CIE CSV bytes (SHA256 checked), observer CIE1931_2,
    and wavelength_convention standard-air-nm. CMFs interpolate linearly on
    the supplied uniform 360-830 nm grid; both endpoints receive full weight.
    Native spectra are neither resampled nor overwritten. A shorter range is
    explicitly marked; absent wavelengths are not invented or extrapolated.

    Supported quantities are spectral_radiance in W/(m^2 sr nm) or
    mW/(m^2 sr nm), spectral_irradiance in W/(m^2 nm) or mW/(m^2 nm), and
    reflectance in 1 or %. Reflectance requires a same-grid, nonnegative,
    unit-one relative_spectral_power illuminant and nonempty illuminant_id.
    Signed samples and factors above one remain unclipped. Illuminant identity
    is caller-declared, not inferred or authenticated by its name.

    Example: a 50% constant reflectance under an explicit positive SPD has
    Y=50 and XYZ equal to half this grid's returned perfect-diffuser white.
    Pair that white with derived XYZ for Lab rather than a nominal white.

    ColorDomainError refuses unsupported/missing context, unusable grids or
    nonfinite arithmetic. Caller establishes actual units, wavelength medium,
    observer, illumination/geometry and nonfluorescent reflectance applicability.
    No bandpass correction, fluorescence model, physical validation, uncertainty
    bound, native XYZ replacement or automatic acquisition enrichment occurs.
    Binary64 rounding and underflow limit very small calculated components.
    """
    if observer != "CIE1931_2" or wavelength_convention != "standard-air-nm":
        raise ColorDomainError("require CIE1931_2 observer and standard-air-nm wavelengths")
    wavelengths, step = _spectral_grid(spectrum)
    matching = _cie1931_matching(observer_csv, wavelengths)
    limited_range = wavelengths[0] > 360.0 or wavelengths[-1] < 830.0
    quantities = {
        ("spectral_radiance", "W/(m^2 sr nm)"): (1.0, "cd/m^2", "absolute; Y is luminance"),
        ("spectral_radiance", "mW/(m^2 sr nm)"): (0.001, "cd/m^2", "absolute; Y is luminance"),
        ("spectral_irradiance", "W/(m^2 nm)"): (1.0, "lux", "absolute; Y is illuminance"),
        ("spectral_irradiance", "mW/(m^2 nm)"): (0.001, "lux", "absolute; Y is illuminance"),
    }
    quantity = spectrum.quantity, spectrum.unit
    if quantity in quantities:
        if illuminant is not None or illuminant_id is not None:
            raise ColorDomainError("absolute spectral integration does not accept an illuminant")
        multiplier, unit, scale = quantities[quantity]
        # Apply the small unit factor before the photometric factor and sums.
        # Every term and compensated sum must remain finite; no clamp occurs.
        factors = tuple(_finite(value * multiplier * _PHOTOPIC_EFFICACY * step,
                                "spectral photometric term") for value in spectrum.values)
        values = tuple(_sum(tuple(_finite(factor * cmf[channel], "spectral XYZ term")
                                  for factor, cmf in zip(factors, matching)), "spectral XYZ sum")
                       for channel in range(3))
        white = None
        efficacy = _PHOTOPIC_EFFICACY
    elif quantity in (("reflectance", "1"), ("reflectance", "%")):
        if (not isinstance(illuminant, Spectrum)
                or illuminant.quantity != "relative_spectral_power" or illuminant.unit != "1"
                or illuminant.wavelengths_nm != spectrum.wavelengths_nm
                or not isinstance(illuminant_id, str) or not illuminant_id.strip()):
            raise ColorDomainError(
                "reflectance requires an identified unit-one SPD on the exact grid"
            )
        peak = max(illuminant.values)
        if peak <= 0.0 or any(value < 0.0 for value in illuminant.values):
            raise ColorDomainError("illuminant SPD must be nonnegative with a positive sample")
        weighted = tuple(tuple(component * (power / peak) for component in cmf)
                         for power, cmf in zip(illuminant.values, matching))
        denominator = _sum(tuple(cmf[1] for cmf in weighted), "illuminant photopic weight")
        if denominator <= 0.0:
            raise ColorDomainError("illuminant has no positive photopic weight on the grid")
        weights = tuple(tuple(_finite(100.0 * (component / denominator), "reflectance weight")
                              for component in cmf) for cmf in weighted)
        factors = tuple(value / 100.0 if spectrum.unit == "%" else float(value)
                        for value in spectrum.values)
        values = tuple(_sum(tuple(_finite(factor * weight[channel], "reflectance XYZ term")
                                  for factor, weight in zip(factors, weights)),
                            "reflectance XYZ sum")
                       for channel in range(3))
        white = tuple(_sum(tuple(weight[channel] for weight in weights), "diffuser white")
                      for channel in range(3))
        unit, scale, efficacy = "1", "relative; perfect-diffuser Y=100", None
    else:
        raise ColorDomainError("unsupported spectral quantity or unit")
    return SpectralXYZ(
        values, spectrum, illuminant, illuminant_id, unit, scale, white,
        step, limited_range, efficacy
    )


def _lab_f(ratio: float) -> float:
    value = math.cbrt(ratio) if ratio > _EPSILON else _LINEAR_SLOPE * ratio + _OFFSET
    return _finite(value, "CIELAB intermediate")


def _lab_f_inverse(value: float) -> float:
    try:
        ratio = value**3 if value > _DELTA else (value - _OFFSET) / _LINEAR_SLOPE
    except OverflowError as error:
        raise ColorDomainError("inverse CIELAB intermediate overflows float precision") from error
    return _finite(ratio, "inverse CIELAB intermediate")


def xyz_to_xyy(xyz: Sequence[Real]) -> Triple:
    """Return (x, y, Y) from scalar (X, Y, Z), retaining the input Y scale.

    With S = X + Y + Z, x = X/S and y = Y/S. Chromaticities have unit one;
    Y retains the caller's relative or absolute tristimulus units. All three
    input components must share that scale. Signed components are preserved.

    Source: CIE S 017:2020, e-ILV 17-23-053, definition and notes 1 and 3:
    https://cie.co.at/eilvterm/17-23-053

    Example: xyz_to_xyy((2, 3, 5)) == (0.2, 0.3, 3.0).
    ColorDomainError rejects invalid inputs, a zero tristimulus sum (including
    black), or nonfinite arithmetic. There is no fallback chromaticity. Signed
    values are algebraically accepted, without asserting a physical stimulus.
    Finite precision, including rounding and underflow, limits the result.
    """
    x_value, y_value, z_value = _triple(xyz, "xyz")
    total = _sum((x_value, y_value, z_value), "XYZ sum")
    if total == 0.0:
        raise ColorDomainError("chromaticity is undefined when the XYZ sum is zero")
    return _result((x_value / total, y_value / total, y_value))


def xyy_to_xyz(xyy: Sequence[Real]) -> Triple:
    """Return scalar (X, Y, Z) from (x, y, Y), retaining the input Y scale.

    Algebraically inverting x = X/S, y = Y/S, and z = 1 - x - y gives
    X = x*Y/y and Z = (1-x-y)*Y/y. The chromaticities x and y have unit one;
    all returned tristimulus values share the supplied Y units and scale.

    Source: algebraic inverse of CIE S 017:2020, e-ILV 17-23-053:
    https://cie.co.at/eilvterm/17-23-053

    Example: xyy_to_xyz((0.2, 0.3, 3)) == (2.0, 3.0, 5.0).
    ColorDomainError rejects invalid inputs, y == 0 (even when Y == 0), or
    nonfinite intermediate/results. Signed components are not clamped; their
    physical validity is not established. A zero Y with nonzero y returns zero
    XYZ, so the supplied chromaticity cannot be recovered from that black.
    Finite precision, including rounding and underflow, limits the result.
    """
    x, y, luminance = _triple(xyy, "xyy")
    if y == 0.0:
        raise ColorDomainError("XYZ is undefined from xyY when y is zero")
    total = _finite(luminance / y, "Y/y")
    z = _sum((1.0, -x, -y), "1-x-y")
    return _result((x * total, luminance, z * total))


@dataclass(frozen=True)
class StandardReferenceDefinition:
    """A sourced nominal white chromaticity, independent of measured data.

    ``xy`` has unit one under the declared observer. Precision describes the
    source's nominal coordinates, not measurement uncertainty. No luminance,
    observation, spectral samples or adaptation is supplied by this record.
    """

    name: str
    observer: str
    xy: Pair
    source: str
    convention: str
    nominal_precision: str
    limitations: tuple[str, ...]


_STANDARD_REFERENCE_DEFINITIONS = tuple(
    StandardReferenceDefinition(
        name=name,
        observer="CIE1931_2",
        xy=xy,
        source="https://www.w3.org/TR/2026/CRD-css-color-4-20261009/#color-conversion-code",
        convention="css-color-4-2026-10-09-nominal-xy",
        nominal_precision="four decimal places in x and y",
        limitations=(
            "Nominal chromaticity; not measured white or SPD-integrated XYZ.",
            "Caller must supply compatible XYZ units, scale and observer.",
            "No chromatic adaptation, illuminant inference or display-profile correction.",
        ),
    )
    for name, xy in (("D50", (0.3457, 0.3585)), ("D65", (0.3127, 0.3290)))
)


@dataclass(frozen=True)
class StandardReferenceWhite:
    """An explicit theoretical Lab reference, separate from native XYZ.

    ``xyz`` has the caller's ``unit`` and ``scale``; its Y is ``reference_y``.
    Metadata identifies the nominal xy convention. This record cannot be
    inserted as native XYZ into ``ReportedValues`` and is not an observation.
    Construction validates immutable coordinates, explicit context and nominal
    metadata. ``standard_reference_white`` supplies those nominal fields.
    """

    name: str
    observer: str
    xy: Pair
    xyz: Triple
    reference_y: float
    unit: str
    scale: str
    source: str
    convention: str
    nominal_precision: str
    limitations: tuple[str, ...]
    kind: Literal["theoretical-lab-reference"] = field(
        default="theoretical-lab-reference", init=False
    )

    def __post_init__(self) -> None:
        definition, luminance, xyz = _standard_reference_parameters(
            self.name, self.observer, self.reference_y, self.unit, self.scale
        )
        if not isinstance(self.xy, tuple) or len(self.xy) != 2:
            raise ColorDomainError("xy must be an immutable two-component tuple")
        xy = _triple((*self.xy, luminance), "xyY")[:2]
        if xy != definition.xy:
            raise ColorDomainError("xy must match the selected nominal reference")
        if not isinstance(self.xyz, tuple):
            raise ColorDomainError("xyz must be an immutable three-component tuple")
        if _white(self.xyz) != xyz:
            raise ColorDomainError("xyz must match nominal xy and reference_y")
        for attribute in ("source", "convention", "nominal_precision", "limitations"):
            value, expected = getattr(self, attribute), getattr(definition, attribute)
            if not isinstance(value, type(expected)) or value != expected:
                raise ColorDomainError(f"{attribute} must match the selected nominal reference")
        object.__setattr__(self, "xy", definition.xy)
        object.__setattr__(self, "xyz", xyz)
        object.__setattr__(self, "reference_y", luminance)


def standard_reference_catalog() -> tuple[StandardReferenceDefinition, ...]:
    """Return frozen nominal D50/D65 definitions for CIE 1931 2 degrees.

    Source: W3C CSS Color 4, 9 October 2026 Candidate Recommendation Draft,
    section 19 (informative), nominal four-figure CIE x,y chromaticities:
    https://www.w3.org/TR/2026/CRD-css-color-4-20261009/#color-conversion-code

    D50 has xy=(0.3457, 0.3585); D65 has xy=(0.3127, 0.3290), with unit one.
    Example: standard_reference_catalog()[0].name == "D50".
    CIE1931_2 is this catalogue's restricted API context; the nominal source
    excerpt does not provide observer-dependent spectral definitions.
    The catalogue contains no Y, measured white or SPD-integrated result.
    These rounded conventions are not a full CIE illuminant implementation,
    a spectral definition, physical validation or uncertainty estimate.
    """
    return _STANDARD_REFERENCE_DEFINITIONS


def _standard_reference_parameters(
    name: str, observer: str, reference_y: Real, unit: str, scale: str
) -> tuple[StandardReferenceDefinition, float, Triple]:
    """Validate nominal context and compose the existing xyY-to-XYZ owner."""
    definition = next(
        (entry for entry in _STANDARD_REFERENCE_DEFINITIONS
         if isinstance(name, str) and name == entry.name), None
    )
    if definition is None:
        raise ColorDomainError('name must be exactly "D50" or "D65"')
    if not isinstance(observer, str) or observer != definition.observer:
        raise ColorDomainError('observer must be exactly "CIE1931_2"')
    if isinstance(reference_y, bool) or not isinstance(reference_y, Real):
        raise ColorDomainError("reference_y must be a real number, excluding bool")
    try:
        luminance = float(reference_y)
    except (OverflowError, ValueError) as error:
        raise ColorDomainError("reference_y cannot be represented as a finite float") from error
    _finite(luminance, "reference_y")
    if luminance <= 0.0:
        raise ColorDomainError("reference_y must be strictly positive")
    if not all(isinstance(text, str) and text.strip() for text in (unit, scale)):
        raise ColorDomainError("unit and scale must be nonempty strings")
    x, y = definition.xy
    xyz = _white(xyy_to_xyz((x, y, luminance)))
    return definition, luminance, xyz


def standard_reference_white(
    name: str, *, observer: str, reference_y: Real, unit: str, scale: str
) -> StandardReferenceWhite:
    """Return an explicitly scaled theoretical D50 or D65 Lab reference.

    Compose xyy_to_xyz: Xn=x*Yn/y, Yn=reference_y, Zn=(1-x-y)*Yn/y.
    The source and nominal xy convention are the same as the catalogue:
    https://www.w3.org/TR/2026/CRD-css-color-4-20261009/#color-conversion-code
    Only exact names D50/D65 and observer CIE1931_2 are supported. Yn must be
    finite, strictly positive and real, excluding bool. Nonempty unit/scale
    declarations are required and retained verbatim, without physical inference.

    Example: standard_reference_white("D65", observer="CIE1931_2",
    reference_y=100, unit="1", scale="relative-white-Y=100").xyz
    is approximately (95.0456, 100, 108.9058).
    No observation, illuminant, native XYZ or display scale is changed; no
    chromatic adaptation occurs. Callers must establish matching input context
    before using xyz_to_lab(xyz, result.xyz). Nominal coordinate rounding,
    binary64 underflow and the existing xyY intermediate overflow limits apply.
    ColorDomainError refuses unsupported/invalid declarations and arithmetic.
    The result is theoretical, not measured or an SPD integration.
    """
    definition, luminance, xyz = _standard_reference_parameters(
        name, observer, reference_y, unit, scale
    )
    return StandardReferenceWhite(
        name=definition.name, observer=definition.observer, xy=definition.xy,
        xyz=xyz, reference_y=luminance, unit=unit, scale=scale,
        source=definition.source, convention=definition.convention,
        nominal_precision=definition.nominal_precision, limitations=definition.limitations,
    )


def xyz_to_lab(xyz: Sequence[Real], reference_white: Sequence[Real]) -> Triple:
    """Return scalar (L*, a*, b*) relative to an explicit (Xn, Yn, Zn).

    XYZ and reference_white must share units and scale; every white component
    must be positive. Let delta = 6/29 and f(t) = cbrt(t) for t > delta**3,
    otherwise f(t) = (841/108)*t + 4/29. Then L* = 116*f(Y/Yn)-16,
    a* = 500*(f(X/Xn)-f(Y/Yn)), b* = 200*(f(Y/Yn)-f(Z/Zn)).
    Lab coordinates have no physical unit.

    Source: CIE S 017:2020, e-ILV 17-23-076, defining equations:
    https://cie.co.at/eilvterm/17-23-076

    Example: xyz_to_lab((1, 1, 1), (1, 1, 1)) == (100.0, 0.0, 0.0).
    Negative XYZ uses an algebraic extension of the linear branch; this is not
    a claim of physically meaningful CIELAB for those observations. No default
    white, normalization, chromatic adaptation, or clipping is performed.
    ColorDomainError rejects invalid inputs/white and nonfinite arithmetic.
    Finite precision, including rounding and underflow, limits the result;
    CIELAB is not a complete appearance model or an uncertainty estimate.
    """
    tristimulus = _triple(xyz, "xyz")
    white = _white(reference_white)
    fx, fy, fz = (
        _lab_f(_finite(value / component, "XYZ/reference_white"))
        for value, component in zip(tristimulus, white)
    )
    return _result((116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)))


def lab_to_xyz(lab: Sequence[Real], reference_white: Sequence[Real]) -> Triple:
    """Return scalar XYZ in the units and scale of an explicit positive white.

    Set fy = (L*+16)/116, fx = fy+a*/500, fz = fy-b*/200. The inverse branch
    g(u) = u**3 for u > 6/29, otherwise g(u) = (u-4/29)/(841/108), gives
    (X, Y, Z) = (Xn*g(fx), Yn*g(fy), Zn*g(fz)). Lab has no physical unit;
    reference_white is (Xn, Yn, Zn), each finite and strictly positive.

    Source: algebraic inverse of CIE S 017:2020, e-ILV 17-23-076 equations:
    https://cie.co.at/eilvterm/17-23-076

    Example: lab_to_xyz((100, 0, 0), (1, 1, 1)) == (1.0, 1.0, 1.0).
    Signed values use the same algebraic extension as xyz_to_lab, without
    claiming physical validity. No default white, adaptation, or clipping is
    performed. ColorDomainError rejects invalid inputs/white and any nonfinite
    intermediate/results, including cubic overflow before white scaling.
    Finite precision, including rounding and underflow, limits the result.
    """
    lightness, a_star, b_star = _triple(lab, "lab")
    white = _white(reference_white)
    fy = _finite((lightness + 16.0) / 116.0, "fy")
    fx = _finite(fy + a_star / 500.0, "fx")
    fz = _finite(fy - b_star / 200.0, "fz")
    x, y, z = (component * _lab_f_inverse(value) for component, value in zip(white, (fx, fy, fz)))
    return _result((x, y, z))


def _chroma_seventh_ratio(chroma: float) -> float:
    """Evaluate C**7 / (C**7 + 25**7) without raising a large C to a power."""
    if chroma >= 25.0:
        seventh = (25.0 / chroma) ** 7
        return 1.0 / (1.0 + seventh)
    seventh = (chroma / 25.0) ** 7
    return seventh / (1.0 + seventh)


def _lab_hue(a_prime: float, b_star: float) -> float:
    """Return the modified hue in degrees, with zero for an achromatic pair."""
    if a_prime == 0.0 and b_star == 0.0:
        return 0.0
    return math.degrees(math.atan2(b_star, a_prime)) % 360.0


def delta_e_2000(lab_a: Sequence[Real], lab_b: Sequence[Real]) -> float:
    """Return CIEDE2000 for two scalar Lab triples with kL = kC = kH = 1.

    Source: Sharma, Wu and Dalal (2005), equations 2-22,
    https://doi.org/10.1002/col.20070. Chroma and hue are modified by G;
    signed lightness/chroma/hue differences are weighted by SL, SC and SH.
    The squared difference includes RT * (delta_C/SC) * (delta_H/SH).
    Hue uses degrees and the paper's exact 180-degree and zero-chroma branches.

    Example: delta_e_2000((50, 2.6772, -79.7751), (50, 0, -82.7485))
    is approximately 2.0425. Lab and the returned difference have unit one.
    The caller must establish compatible Lab white/observer/conventions;
    triples cannot do that. No adaptation, clipping or acquisition occurs.
    Unit parametric factors are fixed; other application-specific weightings
    are outside this API. Finite signed Lab is algebraically accepted, without
    establishing physical/perceptual validity. ColorDomainError rejects invalid
    inputs or nonfinite required arithmetic. Binary64 rounding/underflow and
    the formula's hue-seam discontinuities remain; this is not uncertainty.
    """
    l_a, a_a, b_a = _triple(lab_a, "lab_a")
    l_b, a_b, b_b = _triple(lab_b, "lab_b")
    c_a = _finite(math.hypot(a_a, b_a), "Lab chroma")
    c_b = _finite(math.hypot(a_b, b_b), "Lab chroma")
    c_mean = c_a / 2.0 + c_b / 2.0
    g = 0.5 * (1.0 - math.sqrt(_chroma_seventh_ratio(c_mean)))
    a_prime_a = _finite((1.0 + g) * a_a, "modified a*")
    a_prime_b = _finite((1.0 + g) * a_b, "modified a*")
    c_prime_a = _finite(math.hypot(a_prime_a, b_a), "modified chroma")
    c_prime_b = _finite(math.hypot(a_prime_b, b_b), "modified chroma")
    h_a, h_b = _lab_hue(a_prime_a, b_a), _lab_hue(a_prime_b, b_b)

    delta_l = _finite(l_b - l_a, "lightness difference")
    delta_c = c_prime_b - c_prime_a
    if c_prime_a == 0.0 or c_prime_b == 0.0:
        delta_h = 0.0
        h_mean = h_a + h_b
    else:
        hue_difference = h_b - h_a
        if hue_difference > 180.0:
            hue_difference -= 360.0
        elif hue_difference < -180.0:
            hue_difference += 360.0
        geometric_chroma = _finite(
            math.sqrt(c_prime_a) * math.sqrt(c_prime_b), "geometric chroma"
        )
        delta_h = _finite(
            geometric_chroma * (2.0 * math.sin(math.radians(hue_difference / 2.0))),
            "hue difference",
        )
        hue_sum = h_a + h_b
        if abs(h_b - h_a) <= 180.0:
            h_mean = hue_sum / 2.0
        elif hue_sum < 360.0:
            h_mean = (hue_sum + 360.0) / 2.0
        else:
            h_mean = (hue_sum - 360.0) / 2.0

    l_mean = l_a / 2.0 + l_b / 2.0
    c_prime_mean = c_prime_a / 2.0 + c_prime_b / 2.0
    t = (
        1.0 - 0.17 * math.cos(math.radians(h_mean - 30.0))
        + 0.24 * math.cos(math.radians(2.0 * h_mean))
        + 0.32 * math.cos(math.radians(3.0 * h_mean + 6.0))
        - 0.20 * math.cos(math.radians(4.0 * h_mean - 63.0))
    )
    theta = 30.0 * math.exp(-((h_mean - 275.0) / 25.0) ** 2)
    r_c = 2.0 * math.sqrt(_chroma_seventh_ratio(c_prime_mean))
    l_deviation = _finite(abs(l_mean - 50.0), "mean lightness deviation")
    s_l = 1.0 + 0.015 * l_deviation * (l_deviation / math.hypot(math.sqrt(20), l_deviation))
    s_c = 1.0 + 0.045 * c_prime_mean
    s_h = 1.0 + 0.015 * c_prime_mean * t
    r_t = -math.sin(math.radians(2.0 * theta)) * r_c
    weighted_l, weighted_c, weighted_h = delta_l / s_l, delta_c / s_c, delta_h / s_h
    # Complete the square in equation 22, then use hypot instead of squaring
    # large weighted differences. The cross-term retains both signed differences.
    combined_c = _finite(weighted_c + r_t * weighted_h / 2.0, "weighted chroma/hue")
    remaining_h = weighted_h * math.sqrt(1.0 - (r_t / 2.0) ** 2)
    return _finite(math.hypot(weighted_l, combined_c, remaining_h), "CIEDE2000 result")


def xyz_to_uvy(xyz: Sequence[Real]) -> Triple:
    """Return CIE 1976 (u', v', Y) from scalar XYZ, retaining supplied Y.

    D = X + 15Y + 3Z, u' = 4X/D, v' = 9Y/D. Coordinates have unit one;
    Y retains its input units/scale, which all XYZ components must share.
    Source: CIE S 017:2020, e-ILV 17-23-073 defining equations:
    https://cie.co.at/eilvterm/17-23-073

    Example: xyz_to_uvy((2, 3, 5)) == (8/62, 27/62, 3.0).
    ColorDomainError rejects invalid inputs, D == 0 (including black and
    signed cancellation), or nonfinite required arithmetic. There is no
    near-zero cutoff or fallback chromaticity. Signed values are algebraically
    accepted, without claiming a physical stimulus. No white, observer,
    adaptation or clipping is inferred. Binary64 rounding/underflow applies.
    These are 1976 u'v' coordinates, not 1960 uv or a colour-difference metric.
    """
    x, y, z = _triple(xyz, "xyz")
    denominator = _sum(
        (x, _finite(15.0 * y, "15Y"), _finite(3.0 * z, "3Z")), "u'v' denominator"
    )
    if denominator == 0.0:
        raise ColorDomainError("u'v' chromaticity is undefined when X + 15Y + 3Z is zero")
    return _result((4.0 * x / denominator, 9.0 * y / denominator, y))


def uvy_to_xyz(uvy: Sequence[Real]) -> Triple:
    """Return scalar XYZ from CIE 1976 (u', v', Y), retaining supplied Y.

    Algebraically invert u'=4X/D, v'=9Y/D, D=X+15Y+3Z:
    X=9u'Y/(4v'), Z=(12-3u'-20v')Y/(4v'). Coordinates have unit one;
    returned XYZ shares the supplied Y units/scale. The implementation uses
    q=Y/v', X=(9/4)u'q and Z=3q-(3/4)u'q-5Y with compensated summation.
    Source: inverse of CIE S 017:2020, e-ILV 17-23-073 equations:
    https://cie.co.at/eilvterm/17-23-073

    Example: uvy_to_xyz((8/62, 27/62, 3)) is approximately (2, 3, 5).
    ColorDomainError rejects invalid inputs, v'==0 (even when Y==0), or
    nonfinite required arithmetic. There is no near-zero cutoff or fallback.
    Signed components are preserved without claiming a physical stimulus;
    no white, adaptation, observer or clipping is inferred. With nonzero v'
    and Y==0, output is black, which loses supplied chromaticity. Nonzero XYZ
    with Y==0 has v'==0 and no inverse from u'v'Y. Binary64 rounding/underflow,
    cancellation and intermediate overflow limit the result. Round trips apply
    only where both representations retain information and remain finite.
    """
    u_prime, v_prime, luminance = _triple(uvy, "uvy")
    if v_prime == 0.0:
        raise ColorDomainError("XYZ is undefined from u'v'Y when v' is zero")
    quotient = _finite(luminance / v_prime, "Y/v'")
    x = _finite(2.25 * _finite(u_prime * quotient, "u'Y/v'"), "inverse u'v' X")
    z = _sum(
        (_finite(3.0 * quotient, "3Y/v'"),
         _finite(-0.75 * u_prime * quotient, "-3u'Y/(4v')"),
         _finite(-5.0 * luminance, "-5Y")), "inverse u'v' Z"
    )
    return _result((x, luminance, z))


@dataclass(frozen=True)
class RGBEncoding:
    """Declared RGB space, signed linear result, bounded encoding and clip flags.

    All coordinates have unit one and R/G/B order. ``linear_rgb`` retains the
    unclipped calculation. ``display_rgb`` encodes channels clamped to [0, 1].
    ``clipped_channels`` marks exactly which linear channels were outside that
    interval; it is an arithmetic fact, not a physical gamut/accuracy verdict.
    """

    color_space: str
    linear_rgb: Triple
    display_rgb: Triple
    clipped_channels: tuple[bool, bool, bool]


def _display_encode(linear: float) -> float:
    """Encode a bounded sRGB or Display P3 channel by the shared sRGB transfer."""
    return 12.92 * linear if linear <= 0.0031308 else 1.055 * linear ** (1.0 / 2.4) - 0.055


def _rgb_space(color_space: str) -> str:
    if not isinstance(color_space, str) or color_space not in _RGB_TO_XYZ:
        raise ColorDomainError('color_space must be exactly "srgb" or "display-p3"')
    return color_space


def _positive_scale(reference_luminance: Real) -> float:
    if isinstance(reference_luminance, bool) or not isinstance(reference_luminance, Real):
        raise ColorDomainError("reference_luminance must be a real number, excluding bool")
    try:
        reference = float(reference_luminance)
    except (OverflowError, ValueError) as error:
        raise ColorDomainError(
            "reference_luminance cannot be represented as a finite float"
        ) from error
    _finite(reference, "reference_luminance")
    if reference <= 0.0:
        raise ColorDomainError("reference_luminance must be strictly positive")
    return reference


def _matrix_product(matrix: tuple[Triple, ...], values: Triple) -> Triple:
    """Return M*v with finite products and finite, compensated channel sums."""
    channels = tuple(
        _sum(tuple(_finite(coefficient * component, "matrix term")
                   for coefficient, component in zip(row, values)), "matrix channel")
        for row in matrix
    )
    return channels[0], channels[1], channels[2]


def _matrix_error_bounds(matrix: tuple[Triple, ...], values: Triple) -> Triple:
    """Return a conservative binary64 arithmetic boundary band per channel.

    u=2^-53, gamma8=8u/(1-8u); tau_i=gamma8*sum_j(abs(M_ij*v_j))
    +8*ulp(0). The budget covers coefficient rounding, a prior scale division,
    products and summation (including double rounding), with an underflow floor.
    It concerns converted float inputs, not measurement/input-representation
    uncertainty. The gamma_n model is Higham (2002), Accuracy and Stability of
    Numerical Algorithms, second edition, Lemma 3.1:
    https://doi.org/10.1137/1.9780898718027
    Eight operations is this implementation's engineering budget, not a
    universal perceptual or instrument bound. For roundoff/cancellation:
    Goldberg (1991), sections
    Relative Error and Ulps, Cancellation, and Rounding Error:
    https://doi.org/10.1145/103162.103163

    Example: a nominal D65 unit-white channel can differ from 1 by ~1e-15
    while remaining on-boundary by this declared arithmetic policy. Magnitude
    scaling avoids overflow of the absolute term sum. This is a conservative
    diagnostic band, not proof that every nearby exclusion is caused by rounding.
    Native values and exact raw channel flags remain unchanged.
    """
    u = 2.0**-53
    gamma = 8.0 * u / (1.0 - 8.0 * u)
    bounds = []
    for row in matrix:
        terms = tuple(abs(_finite(a * b, "RGB error-bound term")) for a, b in zip(row, values))
        largest = max(terms)
        total = (gamma * largest * math.fsum(value / largest for value in terms)
                 if largest else 0.0)
        bounds.append(_finite(total + 8.0 * math.ulp(0.0), "RGB arithmetic boundary band"))
    return bounds[0], bounds[1], bounds[2]


def _numerical_rgb_state(values: Triple, bounds: Triple, *, upper: bool) -> str:
    """Classify the closed positive cone or unit cube with a declared band.

    Outside means c_i<-tau_i or (for the cube) c_i>1+tau_i. On-boundary means
    no substantive exclusion and at least one |c_i|<=tau_i or |c_i-1|<=tau_i.
    Inside means neither. The raw comparisons are retained separately.
    """
    if any(value < -bound or (upper and value - 1.0 > bound)
           for value, bound in zip(values, bounds)):
        return "outside"
    if any(abs(value) <= bound or (upper and abs(value - 1.0) <= bound)
           for value, bound in zip(values, bounds)):
        return "on-boundary"
    return "inside"


def _positive_rgb_fit(
    values: Triple, bounds: Triple, reference: float
) -> tuple[float | None, float | None, str | None]:
    """Return raw required and conservative fit scales in the supplied XYZ unit.

    Required=S*max(0,c_i). Fit=nextafter(S*max(c_i+tau_i),+inf) when positive.
    This explicit common-scale action bounds positive channels, not negative
    channels, adaptation, Lab white, measurement uncertainty or physical gamut.
    Black/all-nonpositive channels have required=0 and no positive fit to infer.
    Nonrepresentable scales stay unavailable while other diagnostics survive.
    """
    largest = max(0.0, *values)
    if largest == 0.0:
        return 0.0, None, "no positive RGB channel needs fitting"
    required = reference * largest
    if not math.isfinite(required):
        return None, None, "required positive RGB scale is not representable as a finite float"
    fit = reference * max(value + bound for value, bound in zip(values, bounds))
    fit = math.nextafter(fit, math.inf)
    if not math.isfinite(fit):
        return required, None, "conservative positive RGB fit scale is not a finite float"
    return required, fit, None


def xyz_to_rgb(
    xyz: Sequence[Real], *, color_space: str, reference_luminance: Real
) -> RGBEncoding:
    """Encode scalar XYZ directly for explicit-scale sRGB or Display P3 display.

    Set relative XYZ = XYZ / reference_luminance, then multiply by the matrix
    for the exact color_space name "srgb" or "display-p3". Keep signed linear
    RGB. Clamp each channel to [0, 1], then use their shared transfer: 12.92*c
    for c <= 0.0031308, otherwise 1.055*c**(1/2.4)-0.055.
    Source of shared transfer: ICC sRGB interpretation A.8, pp. 1-2;
    https://registry.color.org/rgb-registry/files/sRGB.pdf
    Both matrices: W3C CSS Color 4, 9 October 2026, sections 10.2, 10.4 and 19:
    https://www.w3.org/TR/2026/CRD-css-color-4-20261009/#predefined-display-p3
    https://www.w3.org/TR/2026/CRD-css-color-4-20261009/#color-conversion-code

    reference_luminance is the declared target display-white Y, strictly
    positive, finite and in the same units/scale as all XYZ components. The
    caller establishes CIE 1931 2-degree/D65-target conventions; no source
    white, observer or chromatic adaptation can be inferred from a triple.
    For an absolute emissive preview, XYZ and reference_luminance can be in
    cd/m^2. An explicit relative scale is also algebraically supported.

    Example: xyz_to_rgb((0, 0, 0), color_space="display-p3",
    reference_luminance=100) returns zero linear/display RGB and no clipping.
    Zero-black XYZ/Y normalization differs from ICC A.6's veiling-glare model
    and CSS's reference viewing conditions. This is not full IEC/CSS conformance,
    an ICC profile, adaptation, gamut mapping, tone mapping, quantization, or a
    prediction of displayed/measured appearance. Display P3 is not DCI-P3.
    ColorDomainError rejects invalid/nonfinite input, scale or required arithmetic.
    Exact rational reference matrices are inverted before conversion to binary64.
    Binary64 rounding/underflow can produce tiny boundary excursions; comparisons
    use the returned values exactly, without epsilon or snapped nominal white.
    Native XYZ is unchanged; only the separate display output is clipped.
    """
    color_space = _rgb_space(color_space)
    tristimulus = _triple(xyz, "xyz")
    reference = _positive_scale(reference_luminance)
    relative = tuple(_finite(value / reference, "XYZ/reference_luminance")
                     for value in tristimulus)
    linear_rgb = _matrix_product(_XYZ_TO_LINEAR_RGB[color_space], relative)
    clipped_channels = tuple(value < 0.0 or value > 1.0 for value in linear_rgb)
    display_rgb = tuple(_display_encode(min(1.0, max(0.0, value))) for value in linear_rgb)
    return RGBEncoding(color_space, linear_rgb, display_rgb, clipped_channels)


@dataclass(frozen=True)
class ChromaticityGamutStatus:
    """Algebraic coordinates and closed ideal-triangle membership, or a reason."""

    coordinates: Pair | None
    in_triangle: bool | None
    unavailable_reason: str | None
    numerical_state: str | None
    numerical_error_bounds: Triple | None


@dataclass(frozen=True)
class RGBGamutStatus:
    """Separate ideal-triangle and explicit-scale RGB-cube arithmetic facts.

    Raw channel comparisons have no epsilon; a separate declared arithmetic
    band provides numerical boundary status. Neither establishes physically
    achievable color, instrument validity, device coverage or appearance.
    """

    encoding: RGBEncoding
    reference_luminance: float
    below_zero_channels: tuple[bool, bool, bool]
    above_one_channels: tuple[bool, bool, bool]
    within_rgb_cube: bool
    positive_stimulus_eligible: bool
    xy: ChromaticityGamutStatus
    uv: ChromaticityGamutStatus
    numerical_state: str
    numerical_error_bounds: Triple
    max_lower_excess: float
    max_upper_excess: float
    required_positive_rgb_scale: float | None
    fit_positive_rgb_scale: float | None
    fit_scale_reason: str | None
    algorithm: str = "ideal-rgb-gamut-status"
    algorithm_version: str = "2"


def _chromaticity_gamut_status(
    xyz: Triple, linear_rgb: Triple, bounds: Triple, *, uv: bool, stimulus_eligible: bool
) -> ChromaticityGamutStatus:
    """Project XYZ and test triangle barycentric signs without an epsilon.

    For denominator D (sum XYZ for xy, X+15Y+3Z for u'v'), barycentric weights
    are c_i*D_i/D. Each reference-primary D_i is positive, so only c_i/D signs
    determine closed-triangle membership. This is algebra, not physical gamut.
    """
    denominator = _sum((xyz[0], 15.0 * xyz[1], 3.0 * xyz[2]) if uv else xyz,
                       "chromaticity denominator")
    try:
        projected = xyz_to_uvy(xyz) if uv else xyz_to_xyy(xyz)
    except ColorDomainError as error:
        return ChromaticityGamutStatus(None, None, str(error), None, None)
    if not stimulus_eligible:
        return ChromaticityGamutStatus(
            projected[:2], None, "triangle comparison requires nonnegative XYZ and positive Y",
            None, None,
        )
    inside = all(value >= 0.0 for value in linear_rgb) if denominator > 0.0 else (
        all(value <= 0.0 for value in linear_rgb)
    )
    return ChromaticityGamutStatus(
        projected[:2], inside, None, _numerical_rgb_state(linear_rgb, bounds, upper=False), bounds
    )


def rgb_gamut_status(
    xyz: Sequence[Real], *, color_space: str, reference_luminance: Real
) -> RGBGamutStatus:
    """Report separate channel, ideal-chromaticity and scaled-cube statuses.

    c=M^-1*(XYZ/Yref). Report c<0 and c>1 independently; the closed cube needs
    all channels in [0,1]. Ideal xy and u'v' triangles use primary barycentric
    weights c_i*D_i/D, not upper channel bounds. Sources: CSS Color 4 sections
    10.2, 10.4 and 19; CIE e-ILV 17-23-053 and 17-23-073:
    https://www.w3.org/TR/2026/CRD-css-color-4-20261009/#color-conversion-code
    https://cie.co.at/eilvterm/17-23-053
    https://cie.co.at/eilvterm/17-23-073

    Example: black is inside the RGB cube; both chromaticities are unavailable.
    A bright neutral can be inside both triangles but above the RGB cube.
    Signed XYZ may have algebraic coordinates, but triangle comparison is
    unavailable unless native XYZ are nonnegative and native Y>0. This necessary
    positive-stimulus domain check is not proof of spectral/physical validity.
    No brightness remedy or gamut mapping is inferred.
    Coordinates have unit one; Yref shares XYZ units/scale, as in xyz_to_rgb.
    Common max-absolute scaling for chromaticity avoids overflow and has no
    effect on the display encoding or its explicitly supplied luminance scale.
    Undefined/nonfinite projection returns None with a reason. Invalid encoder
    inputs or required channel arithmetic raise ColorDomainError.
    Raw comparisons are exact on calculated floats, with no epsilon. Separate
    numerical_state uses the documented binary64 arithmetic band, not an
    instrument/perceptual tolerance. Raw excesses have unit one. Required and
    conservative fit scales share XYZ/Yref units; fitting positive channels does
    not repair negative channels. Nonrepresentable fit is None with a reason.
    Rounding and underflow apply. Native inputs and display encoding are unchanged.
    """
    tristimulus = _triple(xyz, "xyz")
    encoding = xyz_to_rgb(tristimulus, color_space=color_space,
                          reference_luminance=reference_luminance)
    below = tuple(value < 0.0 for value in encoding.linear_rgb)
    above = tuple(value > 1.0 for value in encoding.linear_rgb)
    reference = float(reference_luminance)
    relative = tuple(value / reference for value in tristimulus)
    matrix = _XYZ_TO_LINEAR_RGB[encoding.color_space]
    bounds = _matrix_error_bounds(matrix, relative)
    required, fit, fit_reason = _positive_rgb_fit(encoding.linear_rgb, bounds, reference)
    maximum = max(abs(value) for value in tristimulus)
    scaled = tuple(value / maximum for value in tristimulus) if maximum else tristimulus
    chroma_rgb = _matrix_product(matrix, scaled)
    chroma_bounds = _matrix_error_bounds(matrix, scaled)
    eligible = tristimulus[1] > 0.0 and all(value >= 0.0 for value in tristimulus)
    return RGBGamutStatus(
        encoding, reference, below, above,
        not any(below) and not any(above), eligible,
        _chromaticity_gamut_status(scaled, chroma_rgb, chroma_bounds,
                                  uv=False, stimulus_eligible=eligible),
        _chromaticity_gamut_status(scaled, chroma_rgb, chroma_bounds,
                                  uv=True, stimulus_eligible=eligible),
        _numerical_rgb_state(encoding.linear_rgb, bounds, upper=True), bounds,
        max(0.0, *(-value for value in encoding.linear_rgb)),
        max(0.0, *(value - 1.0 for value in encoding.linear_rgb)), required, fit, fit_reason,
    )


@dataclass(frozen=True)
class RGBReferencePoint:
    """A synthetic ideal-cube sample, with explicit-scale XYZ and white-based Lab."""

    linear_rgb: Triple
    xyz: Triple
    lab: Triple | None
    xy: Pair | None
    uv: Pair | None


@dataclass(frozen=True)
class RGBReferenceGeometry:
    """Ideal reference geometry, never measured device coverage or volume."""

    color_space: str
    reference_luminance: float
    reference_white: Triple | None
    primaries_xyz: tuple[Triple, Triple, Triple]
    primaries_xy: tuple[Pair, Pair, Pair]
    primaries_uv: tuple[Pair, Pair, Pair]
    white_xyz: Triple
    white_xy: Pair
    white_uv: Pair
    samples_per_edge: int
    surface: tuple[RGBReferencePoint, ...]
    triangles: tuple[tuple[int, int, int], ...]
    lines: tuple[tuple[int, int], ...]
    algorithm: str = "ideal-rgb-cube-surface"
    algorithm_version: str = "1"


def rgb_reference_geometry(
    *, color_space: str, reference_luminance: Real,
    reference_white: Sequence[Real] | None, samples_per_edge: int
) -> RGBReferenceGeometry:
    """Sample an ideal sRGB/P3 linear cube, its primaries and its D65 white.

    XYZ=Yref*M*c with each channel c=i/(n-1), i=0..n-1; retain only lattice
    points with at least one channel 0 or 1. The unique shell has n^3-(n-2)^3
    points, in R/G/B lexicographic order. xy and u'v' use existing conversions;
    Lab uses xyz_to_lab with the explicit supplied positive reference_white;
    an explicit None requests XYZ-only geometry and yields point.lab=None.
    Triangle and grid-line index tuples supply cube-face topology to the host.
    Sources: CSS Color 4 sections 10.2, 10.4 and 19; CIE e-ILV 17-23-053,
    17-23-073 and 17-23-076:
    https://www.w3.org/TR/2026/CRD-css-color-4-20261009/#color-conversion-code
    https://cie.co.at/eilvterm/17-23-076

    Example: n=3 yields 26 samples, including black, primaries and white.
    XYZ, Yref and Lab reference_white share caller units/scale; RGB, xy, u'v'
    and Lab have unit one. ColorDomainError rejects invalid space/scale/white,
    bool/noninteger n, n outside [2,65], or nonfinite required arithmetic.
    Black has no xy/u'v'; those fields are None, not invented coordinates.
    This is a finite sampling of an ideal reference, not a measured display,
    coverage estimate, perceptual gamut metric or ICC transform. No adaptation,
    viewing conditions or gamut mapping is applied. Binary64 rounding and
    underflow apply; very small scales can lose nonzero XYZ sample components.
    """
    color_space = _rgb_space(color_space)
    reference = _positive_scale(reference_luminance)
    white = _white(reference_white) if reference_white is not None else None
    if (isinstance(samples_per_edge, bool) or not isinstance(samples_per_edge, int)
            or not 2 <= samples_per_edge <= 65):
        raise ColorDomainError("samples_per_edge must be an integer in [2,65], excluding bool")
    matrix = _RGB_TO_XYZ[color_space]
    relative_primaries = tuple(tuple(row[i] for row in matrix) for i in range(3))
    relative_white = _matrix_product(matrix, (1.0, 1.0, 1.0))
    primaries = tuple(_result(tuple(reference * value for value in primary))
                      for primary in relative_primaries)
    target_white = _result(tuple(reference * value for value in relative_white))
    primary_xy = tuple(xyz_to_xyy(primary)[:2] for primary in relative_primaries)
    primary_uv = tuple(xyz_to_uvy(primary)[:2] for primary in relative_primaries)
    target_xy = xyz_to_xyy(relative_white)[:2]
    target_uv = xyz_to_uvy(relative_white)[:2]
    surface = []
    indices = {}
    last = samples_per_edge - 1
    for r in range(samples_per_edge):
        for g in range(samples_per_edge):
            # Visit only shell points; avoid scanning the cubic interior.
            b_values = range(samples_per_edge) if r in (0, last) or g in (0, last) else (0, last)
            for b in b_values:
                channels = (r / last, g / last, b / last)
                relative = _matrix_product(matrix, channels)
                xyz = _result(tuple(value * reference for value in relative))
                if any(relative):
                    xy, uv = xyz_to_xyy(relative)[:2], xyz_to_uvy(relative)[:2]
                else:
                    xy, uv = None, None
                indices[(r, g, b)] = len(surface)
                lab = xyz_to_lab(xyz, white) if white is not None else None
                surface.append(RGBReferencePoint(channels, xyz, lab, xy, uv))
    lines = []
    for lattice, index in indices.items():
        for axis in range(3):
            neighbor = list(lattice)
            neighbor[axis] += 1
            if tuple(neighbor) in indices:
                lines.append((index, indices[tuple(neighbor)]))
    triangles = []
    for axis in range(3):
        u, v = [i for i in range(3) if i != axis]
        for side in (0, last):
            for i in range(last):
                for j in range(last):
                    corners = []
                    for du, dv in ((0, 0), (1, 0), (1, 1), (0, 1)):
                        lattice = [0, 0, 0]
                        lattice[axis], lattice[u], lattice[v] = side, i + du, j + dv
                        corners.append(indices[tuple(lattice)])
                    a, b, c, d = corners
                    # Choose outward orientation in linear RGB coordinates.
                    if (side == last) == (axis != 1):
                        triangles.extend(((a, b, c), (a, c, d)))
                    else:
                        triangles.extend(((a, c, b), (a, d, c)))
    return RGBReferenceGeometry(
        color_space, reference, white, primaries, primary_xy, primary_uv,
        target_white, target_xy, target_uv, samples_per_edge, tuple(surface),
        tuple(triangles), tuple(lines),
    )
