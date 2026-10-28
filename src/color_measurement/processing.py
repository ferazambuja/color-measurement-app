"""Pure observation enrichment and comparison; native data stays separate."""

import math
from dataclasses import dataclass, replace

from .illumination import IlluminantWhite
from .models import XYZ, ComputedValue, ComputedValues, Observation, Spectrum, Triple
from .science import (
    ColorDomainError,
    StandardReferenceWhite,
    delta_e_2000,
    xyz_to_lab,
    xyz_to_uvy,
    xyz_to_xyy,
)


@dataclass(frozen=True)
class SpectralResidual:
    """Derived exact-grid B-A samples, never a native Spectrum.

    ``source_quantity`` and ``unit`` describe the compared inputs. Algorithm
    identity remains attached even when this object is separated from the
    comparison. Retain source observations for their acquisition context.
    """

    wavelengths_nm: tuple[float, ...]
    values: tuple[float, ...]
    source_quantity: str
    unit: str
    algorithm: str = "spectral-residual-b-minus-a"
    algorithm_version: str = "1"


@dataclass(frozen=True)
class SpectrumComparison:
    """Derived B-A samples and equal-weight RMS, both in the input spectrum unit.

    ``residual`` is a SpectralResidual, not a new native observation. Retain
    the source records separately for measurement and calibration context.
    """

    residual: SpectralResidual
    rms: float


def compare_spectra(a: Spectrum, b: Spectrum) -> SpectrumComparison:
    """Compare two spectra on an exact common nonempty grid, as B minus A.

    Residual r_i = B_i - A_i; RMS = sqrt(sum(r_i**2)/N). This is the
    equal-sample root-mean-square definition (NIST, Dataplot RMS command,
    https://www.itl.nist.gov/div898/software/dataplot/refman2/auxillar/rms.htm).
    Residual and RMS have the source unit; wavelengths remain in nm. An
    irregular shared grid is valid, but RMS is not a wavelength integral.

    Example: values A=(1, 2), B=(-2, 6) give residual=(-3, 4), RMS=sqrt(12.5).
    Spectrum records must have exactly equal wavelengths, quantity and unit.
    ColorDomainError refuses missing/non-Spectrum/incompatible inputs or a
    residual not representable as finite floats. No resampling, normalization,
    unit conversion or clipping occurs; native inputs are unchanged. A caller
    must establish higher-level measurement-context eligibility. Integer pairs
    are subtracted before float conversion; float/mixed pairs use binary64
    subtraction. Conversion/division can round and tiny results can underflow;
    software agreement does not establish physical validity or uncertainty.
    """
    if not isinstance(a, Spectrum) or not isinstance(b, Spectrum):
        raise ColorDomainError("Spectral comparison requires two Spectrum records")
    if a.wavelengths_nm != b.wavelengths_nm:
        raise ColorDomainError("Spectral comparison requires exactly equal wavelength grids")
    if a.quantity != b.quantity or a.unit != b.unit:
        raise ColorDomainError("Spectral comparison requires equal quantities and units")
    try:
        residual_values = tuple(float(bi - ai) for ai, bi in zip(a.values, b.values))
    except OverflowError as error:
        raise ColorDomainError("Spectral residual is not finite at float precision") from error
    if not all(math.isfinite(value) for value in residual_values):
        raise ColorDomainError("Spectral residual is not finite at float precision")
    largest = max(abs(value) for value in residual_values)
    # Scaling before squares protects both large and subnormal residuals.
    rms = (
        largest * math.sqrt(math.fsum((value / largest) ** 2 for value in residual_values)
                            / len(residual_values))
        if largest else 0.0
    )
    return SpectrumComparison(
        SpectralResidual(a.wavelengths_nm, residual_values, a.quantity, a.unit), rms
    )


@dataclass(frozen=True)
class XYZComparison:
    """Derived native-Y change and CIE 1976 coordinate distance, with separate absence.

    These values are never native observations. Each unavailable metric has a
    reason instead of a fabricated zero. Source records retain units, scale,
    observer, illuminant and acquisition context.
    """

    y_percent: float | None
    uv_distance: float | None
    y_reason: str | None
    uv_reason: str | None
    y_unit: str = "%"
    uv_unit: str = "1"
    algorithm: str = "xyz-native-reference-comparison"
    algorithm_version: str = "1"


def compare_xyz(reference: XYZ | None, sample: XYZ | None) -> XYZComparison:
    """Compare native XYZ without a Lab white, adaptation or display scaling.

    Define signed Y change as 100*(Ysample-Yreference)/Yreference; percent
    means parts per hundred (BIPM SI Brochure, 9th ed., section 5.4.7,
    https://www.bipm.org/en/publications/si-brochure). Require positive reference
    Y. Y is luminance only when its declared unit/scale says so.
    Distance = hypot(usample'-ureference', vsample'-vreference'), unit one,
    from xyz_to_uvy's CIE S 017:2020, e-ILV 17-23-073 equations:
    https://cie.co.at/eilvterm/17-23-073

    Example: reference=(1,1,1), sample=(2,2,2) in the same declared context
    gives +100 percent and zero coordinate distance. Both inputs must be XYZ
    records or None; other types raise ColorDomainError. Missing XYZ or unequal
    unit/scale/observer/illuminant makes both metrics unavailable. Arithmetic
    refusal is independent: a black sample gives -100 percent but no distance.
    Signed native values remain unchanged; signed coordinate results are only
    algebraic. No illuminant, physical quantity, geometry or calibration
    equivalence is inferred. This is not CIELUV Delta E, signed Planckian Duv,
    an uncertainty estimate or a perceptual/acceptance threshold.

    Integer Y pairs are subtracted before division. Float/mixed arithmetic,
    division, multiplication and coordinate transforms use binary64; rounding
    and underflow remain. Nonfinite required intermediates/results become
    unavailable even when a different rearrangement could remain finite.
    There is no near-zero reference cutoff or fallback chromaticity.
    """
    if any(value is not None and not isinstance(value, XYZ) for value in (reference, sample)):
        raise ColorDomainError("XYZ comparison requires XYZ records or None")
    if reference is None or sample is None:
        reason = (
            "Reference XYZ is unavailable" if reference is None else "Sample XYZ is unavailable"
        )
        return XYZComparison(None, None, reason, reason)
    context = ("unit", "scale", "observer", "illuminant")
    if any(getattr(reference, key) != getattr(sample, key) for key in context):
        reason = "XYZ comparison requires equal unit, scale, observer and illuminant"
        return XYZComparison(None, None, reason, reason)

    y_percent, y_reason = None, None
    if reference.values[1] <= 0:
        y_reason = "Y percentage requires strictly positive reference Y"
    else:
        try:
            candidate = 100.0 * ((sample.values[1] - reference.values[1]) / reference.values[1])
            if not math.isfinite(candidate):
                raise ColorDomainError("Y percentage is not finite at float precision")
            y_percent = candidate
        except (ColorDomainError, OverflowError) as error:
            y_reason = str(error)

    uv_distance, uv_reason = None, None
    try:
        a, b = xyz_to_uvy(reference.values), xyz_to_uvy(sample.values)
        candidate = math.hypot(b[0] - a[0], b[1] - a[1])
        if not math.isfinite(candidate):
            raise ColorDomainError("CIE 1976 coordinate distance is not finite at float precision")
        uv_distance = candidate
    except (ColorDomainError, OverflowError) as error:
        uv_reason = str(error)
    return XYZComparison(y_percent, uv_distance, y_reason, uv_reason)


def reflectance_fraction(observation: Observation) -> Spectrum:
    """Return a new unit-one spectrum from the currently supported M1 contract.

    Percent means parts per hundred: R = R_percent / 100 (BIPM SI Brochure,
    section 5.4.7). Validate quantity, units and native context before using
    the recorded normalization. Norm alone never identifies reflectance.
    Preserve wavelengths and signed/above-one values; do not alter the native
    observation, integrate XYZ, or generalize this contract to other modes.
    """
    if not isinstance(observation, Observation):
        raise ValueError("Expected an Observation with the supported M1 reflectance contract")
    spectrum = observation.backend_reported.spectrum
    config = dict(observation.configuration)
    try:
        norm = float(config.get("spectrum_norm", ""))
    except ValueError:
        norm = None
    if (
        observation.requested_mode != "reflective_spot_m1"
        or observation.actual_mode != "reflective_spot_m1"
        or spectrum is None
        or spectrum.quantity != "reflectance"
        or spectrum.unit != "%"
        or config.get("native_mtype") != "5"
        or config.get("native_mcond") != "1"
        or norm != 100.0
    ):
        raise ValueError(
            "Expected M1 percent reflectance with native type 5, condition 1, norm 100"
        )
    return replace(spectrum, values=tuple(value / norm for value in spectrum.values), unit="1")


@dataclass(frozen=True)
class ColorComparison:
    delta_xyz: Triple | None = None
    delta_xy: tuple[float, float] | None = None
    delta_uv: tuple[float, float] | None = None
    lab_reference: Triple | None = None
    lab_sample: Triple | None = None
    delta_lab: Triple | None = None
    delta_chroma: float | None = None
    delta_hue: float | None = None
    delta_e2000: float | None = None
    xyz_reason: str | None = None
    xy_reason: str | None = None
    uv_reason: str | None = None
    lab_reason: str | None = None
    lab_reference_reason: str | None = None
    lab_sample_reason: str | None = None
    delta_lab_reason: str | None = None
    chroma_reason: str | None = None
    hue_reason: str | None = None
    delta_e2000_reason: str | None = None
    xyz_unit: str | None = None
    xyz_scale: str | None = None
    observer: str | None = None
    illuminant: str | None = None
    algorithm: str = "signed-color-pair-comparison"
    algorithm_version: str = "1"


def compare_color(
    reference: XYZ | None, sample: XYZ | None, *,
    reference_white: XYZ | StandardReferenceWhite | IlluminantWhite | None,
) -> ColorComparison:
    """Return independent signed B-A color differences under explicit context.

    DeltaXYZ=B-A; deltaxy=(xB-xA,yB-yA); deltaUV=(uB'-uA',vB'-vA').
    XYZ differences retain input units; coordinates have unit one. With one
    common white, DeltaLab=LabB-LabA and DeltaC=hypot(aB,bB)-hypot(aA,bA).
    DeltaH=2*sqrt(CA)*sqrt(CB)*sin((hB-hA)/2), with signed shortest hue turn
    in [-pi,pi]. Neutral hue is undefined; no zero hue is invented. DeltaH
    is the CIELAB hue chord, not CIEDE2000's primed hue term.

    Sources: CIE S 017:2020 terms 17-23-053, 17-23-073 and 17-23-076,
    https://cie.co.at/eilvterm/17-23-076 ; the CIE 1976 hue-chord
    identity follows the Euclidean a*b* plane. Existing xyz_to_lab/xyy/uvy
    and delta_e_2000 owners retain their equations and primary citations.

    Example: common-white Lab A=(50,3,4), B=(60,-4,3) gives DeltaLab
    (10,-7,-1), DeltaC=0 and DeltaH=sqrt(50), with positive quarter-turn.
    Pair unit/scale/observer/illuminant must match. Retained white XYZ must
    match all four; theoretical whites match unit/scale/observer and remain
    explicit assumptions. Missing white withholds only Lab-derived metrics.

    Invalid argument types raise ColorDomainError. Other unavailable values
    retain null and independent reasons; finite signed inputs remain algebraic
    data, not proof of physical stimuli. No normalization, adaptation, clipping,
    native modification, acquisition-condition inference or uncertainty occurs.
    Caller separately establishes geometry/material conditions and provenance.
    Binary64 finite-intermediate failures do not suppress independent metrics.
    """
    if any(value is not None and not isinstance(value, XYZ) for value in (reference, sample)):
        raise ColorDomainError("color comparison requires two XYZ records or None")
    white_types = XYZ, StandardReferenceWhite, IlluminantWhite
    if reference_white is not None and not isinstance(reference_white, white_types):
        raise ColorDomainError("color comparison requires a typed explicit white or None")
    output = {}
    reason_fields = ("xyz_reason", "xy_reason", "uv_reason", "lab_reason", "delta_lab_reason",
                     "chroma_reason", "hue_reason", "delta_e2000_reason",
                     "lab_reference_reason", "lab_sample_reason")
    reason = None
    if reference is None or sample is None:
        reason = "Reference or sample native XYZ is unavailable"
    elif any(getattr(reference, key) != getattr(sample, key)
             for key in ("unit", "scale", "observer", "illuminant")):
        reason = "Pair unit, scale, observer or illuminant differs"
    if reason:
        return ColorComparison(**dict.fromkeys(reason_fields, reason))
    output.update(xyz_unit=reference.unit, xyz_scale=reference.scale,
                  observer=reference.observer, illuminant=reference.illuminant)

    def calculate(field, reason_field, function):
        try:
            result = function()
            values = result if isinstance(result, tuple) else (result,)
            if not all(math.isfinite(value) for value in values):
                raise ColorDomainError(f"{field} exceeds the finite calculation range")
            output[field] = result
        except (ColorDomainError, OverflowError, ValueError) as error:
            output[reason_field] = str(error)

    calculate("delta_xyz", "xyz_reason",
              lambda: tuple(float(b - a) for a, b in zip(reference.values, sample.values)))
    for field, reason_field, transform in (("delta_xy", "xy_reason", xyz_to_xyy),
                                           ("delta_uv", "uv_reason", xyz_to_uvy)):
        calculate(field, reason_field, lambda f=transform: tuple(
            b - a for a, b in zip(f(reference.values)[:2], f(sample.values)[:2])
        ))
    white_reason = None
    if reference_white is None:
        white_reason = "Choose an explicit Lab reference white"
    elif any(getattr(reference_white, key) != getattr(reference, key)
             for key in ("unit", "scale", "observer")):
        white_reason = "Lab white unit, scale or observer differs from the pair"
    elif isinstance(reference_white, XYZ) and reference_white.illuminant != reference.illuminant:
        white_reason = "Retained Lab white illuminant differs from the pair"
    if white_reason:
        for field in reason_fields[3:]:
            output[field] = white_reason
        return ColorComparison(**output)
    white = reference_white.values if isinstance(reference_white, XYZ) else reference_white.xyz
    calculate("lab_reference", "lab_reference_reason", lambda: xyz_to_lab(reference.values, white))
    calculate("lab_sample", "lab_sample_reason", lambda: xyz_to_lab(sample.values, white))
    if "lab_reference" not in output or "lab_sample" not in output:
        reason = output.get("lab_reference_reason") or output["lab_sample_reason"]
        for field in reason_fields[3:8]:
            output[field] = reason
        return ColorComparison(**output)
    a, b = output["lab_reference"], output["lab_sample"]
    output.update(lab_reference=a, lab_sample=b)
    calculate("delta_lab", "delta_lab_reason", lambda: tuple(y - x for x, y in zip(a, b)))
    ca, cb = math.hypot(a[1], a[2]), math.hypot(b[1], b[2])
    calculate("delta_chroma", "chroma_reason", lambda: cb - ca)
    if ca == 0.0 or cb == 0.0:
        output["hue_reason"] = "CIELAB hue chord requires two nonneutral hue angles"
    else:
        turn = math.atan2(b[2], b[1]) - math.atan2(a[2], a[1])
        if turn > math.pi:
            turn -= 2.0 * math.pi
        elif turn < -math.pi:
            turn += 2.0 * math.pi
        calculate("delta_hue", "hue_reason",
                  lambda: 2.0 * math.sqrt(ca) * math.sqrt(cb) * math.sin(turn / 2.0))
    calculate("delta_e2000", "delta_e2000_reason", lambda: delta_e_2000(a, b))
    return ColorComparison(**output)


def compute(observation: Observation, *, reference_white: Triple | None = None) -> Observation:
    """Return a new record with xyY, and Lab only with an explicit supplied white.

    White must share native XYZ units, scale, observer, and illuminant. The caller
    asserts this compatibility: a tuple cannot establish it. No adaptation or
    default illuminant is applied. Original values and raw output are retained.
    """
    xyz = observation.backend_reported.xyz
    if xyz is None:
        raise ValueError(
            "XYZ computation requires native XYZ; missing native XYZ is not implicitly inferred"
        )

    def calculate(algorithm: str, white: Triple | None) -> ComputedValue:
        try:
            values = (
                xyz_to_xyy(xyz.values)
                if algorithm == "xyz_to_xyy"
                else xyz_to_lab(xyz.values, white)
            )
        except ColorDomainError as error:
            status, values, reason = "unavailable", None, str(error)
        else:
            status, reason = "ok", None
        return ComputedValue(
            status=status,
            values=values,
            reason=reason,
            algorithm=algorithm,
            algorithm_version="1",
            observer=xyz.observer,
            illuminant=xyz.illuminant,
            scale=(
                f"x,y unit 1; Y unit {xyz.unit}; {xyz.scale}"
                if algorithm == "xyz_to_xyy"
                else "L*,a*,b* unit 1; sample and white share native XYZ scale"
            ),
            reference_white=white,
        )

    return replace(
        observation,
        project_computed=ComputedValues(
            xyy=calculate("xyz_to_xyy", None),
            lab=calculate("xyz_to_lab", reference_white) if reference_white is not None else None,
        ),
    )
