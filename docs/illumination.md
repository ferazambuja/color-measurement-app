# Explicit illumination and signed color comparisons

These methods derive results from retained inputs without acquisition, native
XYZ replacement or modification of measured spectra. Results retain method,
quantity, scale, source and model declarations separately. Software equations
and numerical tests do not establish physical calibration, material applicability,
matched optical geometry, fluorescence absence or displayed-color accuracy.

## Signed comparison under one explicit white

`processing.compare_color(reference, sample, reference_white=...)` accepts
native typed `XYZ` records or null. A pair must have equal unit, scale, observer
and illuminant. The caller additionally establishes acquired material/geometry
conditions and provenance. This equality checks declared context; it cannot
prove the truth of physical measurement claims.

The result keeps independent metrics and reasons:

| Result | Equation and unit |
| --- | --- |
| `delta_xyz` | B−A, in native XYZ units |
| `delta_xy` | (xB−xA, yB−yA), unit one |
| `delta_uv` | (uB′−uA′, vB′−vA′), unit one |
| `lab_reference`, `lab_sample` | Existing CIELAB under the same white, unit one |
| `delta_lab` | (LB*−LA*, aB*−aA*, bB*−bA*), unit one |
| `delta_chroma` | CB*−CA*, where C*=hypot(a*,b*), unit one |
| `delta_hue` | Signed CIELAB hue chord below, unit one |
| `delta_e2000` | Existing CIEDE2000 with unit weights |

Coordinates and Lab follow [CIE S 017:2020 17-23-053](https://cie.co.at/eilvterm/17-23-053),
[17-23-073](https://cie.co.at/eilvterm/17-23-073) and
[17-23-076](https://cie.co.at/eilvterm/17-23-076); their existing primary-backed
[implementations](science.md) remain the mathematical owners. No alternate Lab,
coordinate or CIEDE2000 implementation is introduced.

Writing h=atan2(b*,a*), choose the signed shortest B−A turn θ in [−π,π].
The a*b* plane geometry gives

\[
\Delta H^*=2\sqrt{C_A^*}\sqrt{C_B^*}\sin(\theta/2),\qquad
(\Delta a^*)^2+(\Delta b^*)^2=(\Delta C^*)^2+(\Delta H^*)^2.
\]

This is the signed CIELAB chord, not CIEDE2000's primed hue term. If either
chroma is exactly zero its hue is undefined, so `delta_hue` is null with a
reason. No neutral hue or perceptual cutoff is invented. Exactly antipodal
angles retain the sign of the atan2-derived ±π turn.

Retained white `XYZ` must match all four pair context fields and have positive
components. Explicit nominal `StandardReferenceWhite` or CIE `IlluminantWhite`
references match unit/scale/observer; choosing these theoretical whites is an
operator assumption, not a change to the native illuminant. Missing white leaves
XYZ and coordinate differences available while withholding Lab-derived metrics.
Black can have valid Lab while coordinates are undefined. A Lab overflow on one
reading preserves the other reading's valid Lab; dependent pair metrics retain
reasons. Every numerical field has an independent nullable state, and the result
retains native unit/scale/observer/illuminant. Signed native values remain signed.

For Lab A=(50,3,4) and B=(60,−4,3), ΔLab=(10,−7,−1), CA*=CB*=5,
ΔC*=0 and ΔH*=2×5×sin(π/4)=√50. Swapping A/B negates signed differences
and preserves ΔE00. Tests construct XYZ independently from the elementary
cube-branch Lab inverse, exercise that example and independent rational
coordinate differences, neutral/black behavior, hue wrapping, context fences,
unchanged inputs and one-sided Lab arithmetic failure. Finite-intermediate
refusals and binary64 rounding still apply; these values are not uncertainty
or physical/perceptual acceptance limits.

<!-- source: src/color_measurement/processing.py:compare_color -->
```python
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
```

## Authenticated CIE D50, D65 and A spectra

`illumination.illuminant_spectrum(name, wavelengths_nm)` loads the unchanged
original CIE relative SPD and validates its published SHA-256 before sampling.
The [bundled notice](../src/color_measurement/data/illuminants/NOTICE.md) retains
CSV/metadata identity, primary sources and CC BY-SA 4.0 terms.

| Name | Primary dataset | Original definition |
| --- | --- | --- |
| D50 | [CIE 2022, etgmuqt5](https://doi.org/10.25039/CIE.DS.etgmuqt5) | ISO/CIE 11664-2:2022 Table B.1 |
| D65 | [CIE 2019, hjfjmt59](https://doi.org/10.25039/CIE.DS.hjfjmt59) | ISO/CIE 11664-2:2022 Table B.1 in retained revision-2 metadata |
| A | [CIE 2018, 8jsxjrsn](https://doi.org/10.25039/CIE.DS.8jsxjrsn) | Equation 1/Table A.1 in retained revision-2 metadata |

Tables cover 300–830 nm at 1 nm; value at 560 nm is 100. They are nominal
relative SPDs, not absolute illumination or measurements. Native integer rows
are exact. Fractional wavelengths interpolate linearly:

\[
S(\lambda)=S_0+(S_1-S_0)\frac{\lambda-\lambda_0}{\lambda_1-\lambda_0}.
\]

Every requested wavelength must lie inside the table; no extrapolation occurs.
D50/D65 metadata specifies linear interpolation. A metadata recommends its
related formula; linear interpolation of A's table is an explicit approximation.
Only the theoretical table is sampled. The native measured grid is not changed.
The frozen result retains the source DOI, digest, relative `Spectrum`, native
range, interpolation and limitation. Values and derived/interpolated data keep
CIE CC BY-SA 4.0 attribution separately from application code.

```python
from color_measurement.illumination import illuminant_spectrum

source = illuminant_spectrum("D65", (560, 560.5, 561))
# First value is exactly 100; midpoint is the mean of original 560/561 nm rows.
```

<!-- source: src/color_measurement/illumination.py:illuminant_spectrum -->
```python
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
```

## CIE-derived white at an explicit Y

`illuminant_white` composes the authenticated SPD and accepted original CIE1931
observer over 360:1:830 nm, then scales the white to the explicitly supplied Y:

\[
W=Y_{ref}\frac{\sum_i S_i(\bar x_i,\bar y_i,\bar z_i)}{\sum_i S_i\bar y_i}.
\]

The [existing spectral owner](science.md#qualified-spectral-xyz-integration)
implements the shared normalized sum. Source: Changjun Li (2008),
[*CIE TC1-71 Perspective*](https://library.imaging.org/admin/apis/public/api/ist/website/downloadArticle/cic/16/1/art00005),
p.22 equations (1)–(3). The common interval cancels and no photometric efficacy
coefficient is applied. Caller supplies authenticated observer bytes,
`observer="CIE1931_2"`, positive finite Y and explicit unit/scale.

| White | XYZ at Y=1, inclusive 1 nm full-range convention |
| --- | --- |
| D50 | (0.964240837883784, 1, 0.825128116837737) |
| D65 | (0.950470558654283, 1, 1.08882873639588) |
| A | (1.09850315268456, 1, 0.355849301338885) |

These are derived theoretical data, not native or measured XYZ. Their nominal
source accuracy and sampling convention remain attached. They differ from
truncated-grid diffuser whites and rounded CSS nominal xy references. CSS's
nominal D65 remains the ideal RGB matrix convention; no silent replacement or
renaming makes it an SPD-integrated CIE white.

```python
from importlib import resources
from color_measurement.illumination import illuminant_white

cmfs = resources.files("color_measurement").joinpath(
    "desktop_assets", "resources", "CIE_xyz_1931_2deg.csv"
).read_bytes()
white = illuminant_white("D65", observer="CIE1931_2", reference_y=203,
    unit="cd/m^2", scale="absolute; Y is luminance", observer_csv=cmfs)
# Approximately (192.9455234068, 203.0, 221.0322334884).
```

This explicit reference does not adapt a sample, infer a lamp, qualify calibration
or prove matched units. Numerical tests use approximate regression targets
cross-checked by separate high-precision Decimal arithmetic over the authenticated
official SPD and observer CSVs. The listed XYZ triples are derived values under
the stated sampling and normalization conventions; the CIE datasets provide
the original spectral samples.
Tests also cover positive Y scaling, exact/native and fractional sampling,
invalid names/grids/context and immutable source identity. Moderate white checks
use 2e-12 absolute tolerance at Y=203; this is numerical tolerance, not source-data
uncertainty, calibration or optical accuracy.

<!-- source: src/color_measurement/illumination.py:illuminant_white -->
```python
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
```

## Characterized measured-reference spectral factor

`characterized_reflectance` calculates

\[
R_i=\frac{P_{sample,i}}{P_{reference,i}}R_{reference,i}.
\]

Sample and reference must be declared spectral radiance in equal supported
W/(m² sr nm) or mW/(m² sr nm) units on exactly the same original wavelength grid.
The caller explicitly confirms matched illumination, geometry, settings and
scale using `matched_setup=True`, preserves `setup_declaration`, and declares
`nonfluorescent=True`. These remain operator/model declarations, not verified
physical facts. Backend consumers must bind the exact source identities and
actual retained quantity/context before presenting these choices.

A characterized reference is an explicit `reflectance`/`1` spectrum with positive
factors. It must cover every native wavelength. Its known characterization curve
can be linearly interpolated to the measured grid; measured sample/reference
spectra are never interpolated, dark-corrected, smoothed or clipped. Alternatively,
explicit `assume_ideal_white=True` with no characterization supplies factor 1.
The returned result keeps that assumption distinct from characterized data.
Neither a missing file nor an uncharacterized white tile silently becomes ideal.

This directional radiance ratio estimates a spectral factor for the retained
geometry, not automatically total hemispherical reflectance. Primary definitions:
[CIE S 017:2020 17-24-070](https://cie.co.at/eilvterm/17-24-070) and
[17-24-075](https://cie.co.at/eilvterm/17-24-075). The characterized-reference
workflow is described in [Konica Minolta CS-S10w Professional v1.6 §2.10.15,
printed p.143](https://www.konicaminolta.com/instruments/download/instruction_manual/software/pdf/cs-s10w_16x_pro_instruction_eng.pdf#page=145).
No instrument calibration commands or software qualification against CS-S10w
are implied.

Reference bins must be positive and strictly greater than
√ε×reference-peak, ε=2⁻⁵². This numerical weak-denominator floor is an explicit
engineering policy, not a measured signal-to-noise requirement. Passing it does
not prove useful measurement quality. Signed sample estimates and factors above
one remain visible. Results retain both original spectra, original characterization,
characterized factors sampled onto the grid, and model/setup declarations.
Nonfinite ratios/products refuse rather than discard wavelengths or fabricate zeros.

For sample (1,2), reference (2,4) and characterized fractions (0.8,0.6),
the result is (0.4,0.3). Tests cover that ratio, signed/above-one values,
characterization interpolation, exact grids/units, distinct ideal-white choice,
weak/nonpositive reference refusal and unchanged originals. No physics model
for fluorescence, changed lighting-dependent materials, geometry or bandpass
is implemented.

<!-- source: src/color_measurement/illumination.py:characterized_reflectance -->
```python
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
```

## Relighting with one explicit target SPD

`relight_reflectance` delegates the normalized object-color equation to accepted
`spectral_xyz`. Named D50/D65/A selects authenticated theoretical SPD samples on
the original factor grid. Its DOI/digest supplies the target identity. An explicit
relative-power SPD must instead carry a caller identity and exact matching grid.
Observer, wavelength medium and nonfluorescent applicability remain explicit.
No native spectrum is reinterpreted as reflectance or silently resampled.

A constant fractional factor 0.5 gives Y=50 and half the returned grid-specific
diffuser white, under any valid target SPD. Use that returned white for Lab under
this target and grid. The source reference model remains separately retained;
relighting is not Bradford adaptation. Coarse/truncated sampling, bandpass,
fluorescence and uncertain material applicability still limit the result.

<!-- source: src/color_measurement/illumination.py:relight_reflectance -->
```python
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
```

## Separate corresponding-color display projection

`presentation.corresponding_color` uses the accepted full Bradford owner from
an explicit source white to the RGB owner's nominal D65. The accepted RGB
encoder then divides by the common source-white Y. All samples share this scale:

\[
RGB_{linear}=M_{XYZ\to RGB}\frac{B^{-1}\operatorname{diag}
 ((BW_{D65})/(BW_{source}/Y_{source}))BXYZ}{Y_{source}}.
\]

Here the destination D65 has Y=1. Matrix/adaptation definitions and transfer
functions remain at the existing [science owners](science.md), following
[ICC.1:2004-10 Annex E.3](https://www.color.org/ICC1V42.pdf) and
[CSS Color 4 §19](https://www.w3.org/TR/2026/CRD-css-color-4-20261009/#color-conversion-code).
No per-reading brightness normalization or second display matrix is introduced.
Caller establishes compatible XYZ/white units and CIE1931_2 observer, and selects
exact `srgb` or `display-p3`. The pure triples cannot prove those input facts.

White maps approximately to RGB=(1,1,1), half-white to linear RGB=(0.5,0.5,0.5),
and black to (0,0,0). Signed linear values remain separate from bounded encoded
RGB and per-channel clipping flags. The result retains adapted XYZ, both whites,
observer, target and common Y. It is an explicitly modelled derived swatch,
not replacement of native view, measured appearance, display-profile correction,
geometry validation or guaranteed screen color. Domain refusal stays explicit.
The corresponding-color packet uses algorithm version 2. For sample XYZ exactly
equal to its validated declared white, an upper excursion no larger than
16 binary64 ulps at 1 is suppressed only in the illustrative clipping advisory.
The exact-white invariant establishes the mathematical endpoint (1,1,1).
Adapted XYZ, signed linear channels and display RGB are unchanged; negative or
larger excursions and every unequal sample retain strict flags. The direct
`science.xyz_to_rgb` encoder and its gamut status remain strict. This numerical
bound supplies no measurement uncertainty or physical gamut tolerance.
Tests check the exact-white invariant in both color spaces, the 16/17-ulp bound,
near-but-unequal and above-white inputs, signed/clipped channels, invalid
observer/white/target and unchanged input scale.

<!-- source: src/color_measurement/presentation.py:corresponding_color -->
```python
def corresponding_color(xyz, *, reference_white, observer, color_space) -> CorrespondingColor:
    """Project explicit-white XYZ to an ideal D65 display at one common white Y.

    Adapt XYZ with full Bradford from the supplied white to the RGB matrix
    owner's nominal D65; encode adapted_xyz/white_Y using shared xyz_to_rgb.
    Only whites are normalized inside adaptation. All samples share the same
    white_Y, never their own brightness. Source: ICC.1:2004-10 Annex E.3 and
    CSS Color 4 section 19, https://www.color.org/ICC1V42.pdf and
    https://www.w3.org/TR/2026/CRD-css-color-4-20261009/#color-conversion-code

    Example: XYZ equal to the supplied white produces approximately RGB=(1,1,1);
    black produces (0,0,0). Input XYZ/white must have compatible units/scale and
    CIE1931_2 observer. The scalar triples cannot establish those facts: caller
    validates retained relative/derived context before invoking this method.

    Signed linear channels and individual clipping flags remain separate from
    the bounded illustrative display RGB. For a validated sample exactly equal
    to its declared white, the mathematical display endpoint is (1,1,1).
    Version 2 suppresses only positive upper rounding excursions of at most
    16 binary64 ulps at 1 in this endpoint's illustrative clipping flags.
    Adapted XYZ, signed channels and bounded display RGB remain unchanged;
    every other sample and the direct RGB owner keep their strict flags.
    This numerical-only bound is not a measurement uncertainty or gamut tolerance.
    This is a declared corresponding-color
    model, not native preview replacement, spectral relighting, profile correction,
    calibrated screen color, viewing-condition prediction or physical validation.
    No input record is modified. Domain/range refusal raises ColorDomainError.
    """
    if observer != "CIE1931_2":
        raise ColorDomainError("corresponding display color requires CIE1931_2")
    destination = standard_reference_white(
        "D65", observer=observer, reference_y=1, unit="1", scale="RGB reference Y=1"
    ).xyz
    # Shared adaptation owner validates positive finite white and sample floats.
    adapted = adapt_xyz(xyz, reference_white, destination)
    white = tuple(float(value) for value in reference_white)
    encoded = xyz_to_rgb(adapted, color_space=color_space, reference_luminance=white[1])
    clipped = encoded.clipped_channels
    if tuple(float(value) for value in xyz) == white:
        upper = 1.0 + 16 * math.ulp(1.0)
        clipped = tuple(flag and not 1.0 < value <= upper
                        for flag, value in zip(clipped, encoded.linear_rgb))
    return CorrespondingColor(
        adapted, encoded.linear_rgb, encoded.display_rgb, clipped,
        white, destination, observer, color_space, white[1],
    )
```

## Verification scope

Run the relevant new numerical cases from the registered existing test modules:

```sh
uv run pytest tests/test_xyz_comparison.py tests/test_science_standard_white.py \
  tests/test_reflectance_fraction.py tests/test_presentation.py \
  -k 'full_color or cie_illuminant or reference_factor or corresponding_color'
uv run python tools/check_doc_snippets.py --check docs
```

Original accepted Bradford/spectral, CIELAB, CIEDE2000, coordinate and RGB
numerical evidence remains at those owners; it need not be replayed to prove
every new composition. These scoped software checks do not establish a physical
device, native worker, GUI operation, MATLAB numerical equivalence or installed
package/UI adoption.


## Retained dataset composition

The implementation in `qualified_science.derive_readings` exposes these existing
owners through the same authenticated Dataset/Session query and optional analysis
export. `characterized_reflectance` binds one exact retained reference, an
explicit characterization identity/data or ideal-white choice, matched setup
text and the nonfluorescent declaration. `relight_reflectance` takes either
native reflectance or that characterized-reference context, with an identified
target SPD, explicit observer and wavelength convention, and optional display
encoding. See the complete [request and result contracts](analysis-workspace.md#characterized-reference-and-identified-target-derivations).

Derived factors retain the original sample/reference observation identities and
digests. Analysis exports preserve the authenticated reference, including one
outside the selected readings, in `role_inputs.reflectance_reference` with its
canonical operation JSON and operation digest. Characterization and declared-SPD
hashes bind their supplied data; named
CIE targets retain their authenticated original resource digests. Factor, XYZ,
Lab and corresponding-color stages preserve independent unavailable reasons.
A valid factor is retained when the later target computation refuses, and valid
XYZ is retained when a grid-specific white cannot support Lab or display
adaptation. Native quantities, measurements, calibration/context and source
records remain unchanged. Setup/material declarations and these software proofs
do not establish physical applicability or accuracy.
