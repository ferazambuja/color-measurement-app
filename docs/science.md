# Scientific methods

The scalar functions below change tristimulus representations, calculate
CIEDE2000 differences, and encode an explicitly scaled XYZ for sRGB or Display P3.
The spectral comparison
calculates a signed residual and sample-wise RMS on an exact common grid.
The typed XYZ comparison reports signed native-Y change and CIE 1976 coordinate
distance with independent unavailable states.
Explicit Bradford adaptation and authenticated-observer spectral integration
return separate derived values. These functions do not acquire or calibrate a
measurement. CIELAB
compares values with an explicit reference white; it is not a complete prediction
of appearance or an instrument uncertainty estimate.

The [CR-250 consistency check](#cr-250-spectral-consistency-check) below is a
separate native-driver calculation using Argyll's spectral integrator. It does
not replace instrument XYZ. The explicit Python integration below has its
own declared sampling and photometric conventions.

The [chart scientific consumers](chart-science.md) bind original reflective XYZ,
common-white chart illustrations and explicitly qualified CIE76 placement advisories.

## API and conventions

The scalar and reference functions live in [`color_measurement.science`](../src/color_measurement/science.py).
Tristimulus/Lab inputs are sequences of exactly three finite real numbers,
excluding booleans. Outputs are three Python floats, a single float for
CIEDE2000, or frozen nominal-white, RGB encoding, gamut-status and
reference-geometry and separate spectral XYZ results as described below. Strings,
missing components, complex numbers,
NaN and infinities are refused with `ColorDomainError`, a `ValueError` subclass.
CIELAB functions require a separate three-component `reference_white` argument.

| Function | Input | Output and scale |
| --- | --- | --- |
| `xyz_to_xyy(xyz)` | `(X,Y,Z)` on one common scale | `(x,y,Y)`; x,y dimensionless, Y unchanged |
| `xyy_to_xyz(xyy)` | `(x,y,Y)` | `(X,Y,Z)` in the supplied Y scale |
| `standard_reference_catalog()` | No input | Frozen sourced nominal D50/D65 xy definitions, without luminance |
| `standard_reference_white(name, *, observer, reference_y, unit, scale)` | Exact D50/D65 name, CIE1931_2 observer and explicit positive Y/unit/scale | Frozen theoretical Lab reference with nominal xy, scaled XYZ and source/convention/limitations |
| `xyz_to_lab(xyz, reference_white)` | XYZ and `(Xn,Yn,Zn)` in compatible units/scale | Dimensionless `(L*,a*,b*)` |
| `adapt_xyz(xyz, source_white, destination_white)` | Explicit sample/source/destination XYZ, compatible observer and conventions | Signed Bradford corresponding XYZ, sample scale retained |
| `spectral_xyz(spectrum, *, observer, observer_csv, wavelength_convention, illuminant=None, illuminant_id=None)` | Qualified spectrum, authenticated original CIE CSV and explicit grid context; reflectance also requires an identified same-grid SPD | Frozen separate `SpectralXYZ` with unit/scale, original spectra, sampling facts and diffuser white when applicable |
| `lab_to_xyz(lab, reference_white)` | Lab and explicit white | XYZ in the white's units/scale |
| `delta_e_2000(lab_a, lab_b)` | Two compatible Lab triples | ΔE00 with unit one; kL=kC=kH=1 |
| `xyz_to_uvy(xyz)` | `(X,Y,Z)` on one common scale | CIE 1976 `(u′,v′,Y)`; u′,v′ dimensionless, Y unchanged |
| `uvy_to_xyz(uvy)` | CIE 1976 `(u′,v′,Y)` with nonzero v′ | `(X,Y,Z)` in supplied Y units/scale, preserving signed components |
| `processing.compare_xyz(reference, sample)` | Two typed `XYZ` records or null with matching unit/scale/observer/illuminant | Frozen independent signed Y change (%) and CIE 1976 coordinate distance (unit one), or explicit reasons |
| `xyz_to_rgb(xyz, *, color_space, reference_luminance)` | XYZ, exact `srgb` or `display-p3`, and explicit display-white Y | Space, signed linear RGB, bounded display RGB and exact clipping flags |
| `rgb_gamut_status(xyz, *, color_space, reference_luminance)` | Same encoder inputs | Raw channel/triangle/cube facts, separate numerical boundary state, excess magnitudes and explicit positive-channel fit scales |
| `rgb_reference_geometry(*, color_space, reference_luminance, reference_white, samples_per_edge)` | Explicit target scale, optional explicit Lab white (`None` for XYZ-only), grid resolution | Ideal primaries/white, unique cube-surface points, XYZ/Lab projections and mesh/grid indices |

The caller supplies compatible observer/reference conditions. Tuples cannot
prove those conditions or distinguish a relative Y=1 scale from Y=100 or an
absolute photometric scale. Every white component must be finite and positive.
The code supplies no default white or automatic adaptation. Explicit Bradford
adaptation normalizes only its supplied whites; reflectance integration uses
a declared grid-specific perfect-diffuser normalization.

Finite signed values are algebraically accepted; native inputs are never changed.
The RGB function retains its signed linear calculation and clips only its
separate display output, with explicit flags. This preserves
the distinction between a supplied number and its interpretation; it does not
establish physical realizability. Negative Lab ratios use the linear branch.
L* can exceed 100 when the supplied Y exceeds the reference white's Y.

## XYZ and xyY

CIE S 017:2020, *International Lighting Vocabulary*, 2nd edition,
[e-ILV 17-23-053](https://cie.co.at/eilvterm/17-23-053), definition and notes 1–3,
defines normalized tristimulus coordinates. Writing S=X+Y+Z gives

\[
x=\frac{X}{S},\qquad y=\frac{Y}{S},\qquad z=1-x-y.
\]

xyY stores x and y together with the original Y. Algebraic inversion gives

\[
X=\frac{xY}{y},\qquad Z=\frac{(1-x-y)Y}{y}.
\]

At S=0, including black or signed cancellation, `xyz_to_xyy` raises
`ColorDomainError`. The inverse rejects y=0, even when Y=0. There is no fallback
white chromaticity. With nonzero y and Y=0, the inverse produces zero XYZ and
loses the supplied chromaticity. Conversely, a nonzero XYZ with Y=0 can have
defined forward chromaticity but no unique inverse. Round trips apply only
where both directions retain information and their arithmetic is representable.

### Worked example

For synthetic XYZ=(20,30,50), S=100, so xyY=(0.2,0.3,30).
The inverse recovers X=0.2×30/0.3=20 and Z=0.5×30/0.3=50.
Y remains 30 in the original scale.

```python
from color_measurement.science import xyz_to_xyy, xyy_to_xyz

print(xyz_to_xyy((20, 30, 50)))      # (0.2, 0.3, 30.0)
print(xyy_to_xyz((0.2, 0.3, 30)))   # (20.0, 30.0, 50.0)
```

### Actual implementation: `xyz_to_xyy`

<!-- source: src/color_measurement/science.py:xyz_to_xyy -->
```python
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
```

### Actual implementation: `xyy_to_xyz`

<!-- source: src/color_measurement/science.py:xyy_to_xyz -->
```python
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
```

## Explicit nominal D50/D65 Lab references

Users without a measured white can explicitly choose a **theoretical Lab
reference** with `standard_reference_white`. Its `kind` is
`"theoretical-lab-reference"`. This frozen scientific result is a separate type
from native [`XYZ` and `Observation`](../src/color_measurement/models.py).
It does not supply an instrument observation or replace a measured white.
`ReportedValues` refuses this type as native XYZ. The result's `xyz` tuple is
an explicit reference argument for `xyz_to_lab`; the scalar Lab API itself
cannot verify that a native sample has matching context.

`standard_reference_catalog()` returns a tuple of frozen
`StandardReferenceDefinition` records, with `name`, `observer`, `xy`, `source`,
`convention`, `nominal_precision` and `limitations`. The scaled
`StandardReferenceWhite` result retains those fields and adds `xyz`,
`reference_y`, `unit`, `scale` and its fixed theoretical `kind`.
Use the factory to compose a nominal reference. Direct construction and
`dataclasses.replace` also validate immutable coordinates, positive finite Y,
the supported observer and exact nominal metadata/XYZ consistency. These
records are not a general user-defined illuminant API.

### Source and coordinate convention

The nominal coordinate authority is the pinned W3C *CSS Color Module Level 4*,
[9 October 2026 Candidate Recommendation Draft, section 19](https://www.w3.org/TR/2026/CRD-css-color-4-20261009/#color-conversion-code).
That section is informative. Its source text declares:

```javascript
// standard white points, defined by 4-figure CIE x,y chromaticities
const D50 = [0.3457 / 0.3585, 1.00000, (1.0 - 0.3457 - 0.3585) / 0.3585];
const D65 = [0.3127 / 0.3290, 1.00000, (1.0 - 0.3127 - 0.3290) / 0.3290];
```

The W3C excerpt itself does not name an observer or provide an
observer-specific illuminant table. `CIE1931_2` is this catalogue's explicitly
restricted API context, rather than an inferred or selectable spectral observer.
The public primary [Adobe RGB (1998) Color Image Encoding, May 2005](https://www.adobe.com/digitalimag/pdfs/AdobeRGB1998.pdf),
sections 4.2.1 and 4.2.9, also associates `(0.3127, 0.3290)` with D65 and the
CIE 1931 two-degree standard observer. The implementation supports only
`CIE1931_2`; it refuses other observer declarations rather than treating these
coordinates as observer independent. No Adobe RGB transform, luminance
convention or viewing-environment assumption is implemented here.

| Exact name | Supported observer | Nominal x | Nominal y | XYZ at Y=100, approximately |
| --- | --- | --- | --- | --- |
| `D50` | `CIE1931_2` | 0.3457 | 0.3585 | (96.4296, 100, 82.5105) |
| `D65` | `CIE1931_2` | 0.3127 | 0.3290 | (95.0456, 100, 108.9058) |

The declared convention is `css-color-4-2026-10-09-nominal-xy`.
`nominal_precision` identifies four decimal places in x and y; those are fixed
nominal coordinates, not measurement precision or an uncertainty interval.
All XYZ values are derived from that same xy definition. They are not copied
from a differently rounded table. In particular, the ICC PCS convention
`(0.9642, 1, 0.8249)`, described in [ICC's sRGB interpretation, section B.2](https://registry.color.org/rgb-registry/files/sRGB.pdf),
is separately rounded and is not substituted for this nominal D50.

The primary CIE [D50 dataset](https://cie.co.at/datatable/cie-standard-illuminant-d50)
(2022, DOI [10.25039/CIE.DS.etgmuqt5](https://doi.org/10.25039/CIE.DS.etgmuqt5))
and [D65 dataset](https://www.cie.co.at/datatable/cie-standard-illuminant-d65)
(2019, DOI [10.25039/CIE.DS.hjfjmt59](https://doi.org/10.25039/CIE.DS.hjfjmt59))
define spectral distributions at 1 nm steps. This catalogue does not reproduce
or integrate those datasets. Its source/convention metadata identifies a
nominal chromaticity reference, not an SPD-derived result or complete
ISO/CIE standard-illuminant implementation.

### Equation, scale and required declarations

For the selected nominal `(x_n,y_n)` and caller-supplied positive `Y_n`, the
factory composes the existing `xyy_to_xyz` owner:

\[
X_n=\frac{x_nY_n}{y_n},\qquad
Y_n=\texttt{reference\_y},\qquad
Z_n=\frac{(1-x_n-y_n)Y_n}{y_n}.
\]

x and y have unit one. XYZ retains the declared Y unit and scale. Required
`unit` and `scale` arguments are nonempty strings retained verbatim, following
the native XYZ record's declaration convention. They are labels supplied by
the caller; the factory does not certify dimensional compatibility. Relative
examples can declare `unit="1"`, `scale="relative-white-Y=100"` and Y=100.
An absolute emissive reference can instead declare `unit="cd/m^2"`,
`scale="absolute"` and the chosen Y in cd/m². That is a hypothetical luminance
chosen by the caller, not evidence that D65 or the sample was measured at it.

`name`, `observer`, `reference_y`, `unit` and `scale` have no defaults.
Names must be exactly `D50` or `D65`; aliases and other names are refused.
`reference_y` must be a real, finite, strictly positive value, excluding bool.
Invalid values raise `ColorDomainError`; omitting a required argument raises
Python's `TypeError`. Nonfinite xyY intermediate/results also raise
`ColorDomainError`. Subnormal values can lose nominal-coordinate accuracy
through binary64 underflow. Extremely large finite Y can overflow the reused
`Y/y` intermediate even when another rearrangement might avoid that overflow.

### Worked example and isolation from display encoding

```python
from color_measurement.science import standard_reference_white, xyz_to_lab

white = standard_reference_white(
    "D65", observer="CIE1931_2", reference_y=100,
    unit="1", scale="relative-white-Y=100",
)
print(white.kind)  # theoretical-lab-reference
print(white.xyz)   # approximately (95.0456, 100, 108.9058)

# Synthetic same-context XYZ, with independent perfect-cube ratios.
xyz = tuple(component * ratio for component, ratio in
            zip(white.xyz, (0.216, 0.125, 0.064)))
print(xyz_to_lab(xyz, white.xyz))  # approximately (42, 50, 20)
```

The caller must establish the same observer, units and scale for input XYZ and
reference XYZ. A native sample's measurement illuminant remains its original
value; choosing D50 or D65 does not identify or change it. Selecting a different
reference changes Lab coordinates, without chromatic adaptation of the native
sample. The factory does not change `xyz_to_rgb`'s D65 target, its explicit
`reference_luminance`, signed channels or clipping facts, and does not perform
display profile correction. Equal common scaling of sample and reference
preserves Lab; changing only the reference Y changes the calculation.

[`tests/test_science_standard_white.py`](../tests/test_science_standard_white.py)
checks independent exact-rational nominal-xy equations at Y=1, 100, 120 and
other representable scales, explicit Y retention, positive subnormal behavior,
overflow, context refusal, immutable metadata and separation from native
records. Perfect-cube Lab examples check composition. The tests also check
that reference selection preserves native XYZ, signed RGB, clipping/gamut
diagnostics and the independently chosen display scale. Reference geometry
keeps its XYZ while explicit Lab whites change. Relative numerical tolerance
2×10⁻¹⁵ in the rational XYZ comparison and absolute 2×10⁻¹² in the Lab example
are binary64 test tolerances, not physical accuracy claims.

### Actual implementation: `standard_reference_catalog`

<!-- source: src/color_measurement/science.py:standard_reference_catalog -->
```python
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
```

### Shared validation and nominal composition

The constructor and factory reuse one validation/composition owner. The
constructor additionally checks its supplied immutable tuples and catalogue
metadata against this exact result.

<!-- source: src/color_measurement/science.py:_standard_reference_parameters -->
```python
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
```

### Actual implementation: `standard_reference_white`

<!-- source: src/color_measurement/science.py:standard_reference_white -->
```python
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
```

## Full linear Bradford adaptation

`adapt_xyz` predicts corresponding colors between two explicit adopted-white
chromaticities. Changing the reference passed to `xyz_to_lab` alone does not
perform this operation. The model assumes full adaptation and omits the
blue-channel nonlinearity, following [ICC.1:2004-10 Annex E.3, equations
E.1–E.4](https://www.color.org/ICC1V42.pdf#page=102). This is an independent
implementation of the published equations, not a translation of MATLAB or an
external library.

Normalize the source and destination whites to Y=1, denoting them by
\(w_s\) and \(w_d\). The published matrix is

\[
B=\begin{bmatrix}
0.8951&0.2664&-0.1614\\
-0.7502&1.7135&0.0367\\
0.0389&-0.0685&1.0296
\end{bmatrix},\qquad
XYZ_d=B^{-1}\operatorname{diag}\left(\frac{Bw_d}{Bw_s}\right)BXYZ_s.
\]

The ratio is component-wise. The inverse is derived from the exact published
decimal coefficients using the shared rational matrix owner, then rounded to
binary64. Both whites must have positive XYZ and positive finite Bradford cone
responses. This explicitly refuses positive XYZ whites outside the model's
usable cone-response domain. Identical normalized whites return the original
triple exactly after validation.

Only the whites are normalized. Sample XYZ keeps its numerical scale and
signed values, with no clipping or brightness normalization. A source white
at Y=203 maps to the destination chromaticity at Y=203 even if the supplied
destination was expressed at Y=100. Individual sample Y can change under the
transform. Subsequent Lab must use the destination white at the intended sample
scale. Caller establishes the common observer and compatible XYZ conventions;
a tuple cannot authenticate these conditions.

```python
from color_measurement.science import adapt_xyz, xyz_to_lab

source = (0.9505 * 203, 203, 1.0890 * 203)
destination_y100 = (96.42, 100, 82.51)
adapted = adapt_xyz(source, source, destination_y100)
# Approximately (195.7326, 203.0, 167.4953), the destination white at Y=203.
destination_y203 = (0.9642 * 203, 203, 0.8251 * 203)
lab = xyz_to_lab(adapted, destination_y203)  # Approximately (100, 0, 0).
```

This predicts corresponding XYZ under the model, not measured observer
adaptation, perceptual appearance under unspecified viewing conditions, or
spectral relighting. It infers no illuminant SPD, display profile, reference
measurement or calibration. Retain source XYZ, both supplied whites, units,
observer and scale alongside derived XYZ. Native observations remain unchanged.
Finite required products, ratios and compensated sums can refuse extreme inputs;
binary64 rounding and underflow still limit representable results.

### Actual implementation and proof

<!-- source: src/color_measurement/science.py:adapt_xyz -->
```python
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
```

The existing [numerical module](../tests/test_science.py) evaluates 36
sample/source/destination combinations independently at 60-digit Decimal
precision using Cramer's rule. The oracle uses the ICC matrix literals rather
than production constants or another color library. It covers unequal channels,
signed XYZ, black, identical whites, brightness scaling, inverse adaptation,
source-white mapping and invalid/cone/nonfinite domains. Selected moderate
vectors use the existing 2e-12 absolute and 2e-14 relative tolerance; these
are software arithmetic tolerances, not perceptual or instrument limits.

## CIELAB

The defining equations and piecewise branches are available directly in
[CIE e-ILV 17-23-076](https://cie.co.at/eilvterm/17-23-076), CIE S 017:2020.
The equations on that page are unnumbered. For d=6/29, define

\[
f(t)=\begin{cases} \sqrt[3]{t},&t>d^3,\\
\frac{841}{108}t+\frac4{29},&t\le d^3.\end{cases}
\]

Then

\[
L^*=116f(Y/Y_n)-16,\quad
a^*=500[f(X/X_n)-f(Y/Y_n)],\quad
b^*=200[f(Y/Y_n)-f(Z/Z_n)].
\]

The inverse here is derived algebraically: set
fy=(L*+16)/116, fx=fy+a*/500, fz=fy−b*/200, and

\[
g(u)=\begin{cases}u^3,&u>d,\\
(u-4/29)/(841/108),&u\le d.\end{cases}
\]

XYZ=(Xn·g(fx), Yn·g(fy), Zn·g(fz)). Both branches meet continuously with matching
first derivatives. At a neutral ratio d³, L*=8.

The [public scope of ISO/CIE 11664-4:2019](https://www.cie.co.at/publications/colorimetry-part-4-cie-1976-lab-colour-space-1)
addresses reflecting/transmitting object stimuli and suitably normalized displays
simulating them. It excludes a general primary-light-source appearance claim.
The full standard and its Annex A were not inspected for this implementation;
agreement with these accessible equations is not a claim of complete normative
conformance.

### Worked example in both directions

Use a **synthetic** white W=(80,100,120), not a named illuminant or measured white.
XYZ=(17.28,12.5,7.68) gives ratios (0.216,0.125,0.064), the cubes of
(0.6,0.5,0.4). All three use the cube-root branch, giving Lab=(42,50,20).
The inverse recovers those roots, cubes them and multiplies by W.

```python
from color_measurement.science import xyz_to_lab, lab_to_xyz

white = (80, 100, 120)
print(xyz_to_lab((17.28, 12.5, 7.68), white))  # approximately (42, 50, 20)
print(lab_to_xyz((42, 50, 20), white))        # approximately (17.28, 12.5, 7.68)
```

Multiply both XYZ and W by the same positive factor and Lab is unchanged.
Scaling only one changes the question and the result. There is no implicit
illuminant identification or adaptation in this example.

### Actual implementation: `xyz_to_lab`

<!-- source: src/color_measurement/science.py:xyz_to_lab -->
```python
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
```

### Actual implementation: `lab_to_xyz`

<!-- source: src/color_measurement/science.py:lab_to_xyz -->
```python
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
```

### Actual piecewise helpers

<!-- source: src/color_measurement/science.py:_lab_f -->
```python
def _lab_f(ratio: float) -> float:
    value = math.cbrt(ratio) if ratio > _EPSILON else _LINEAR_SLOPE * ratio + _OFFSET
    return _finite(value, "CIELAB intermediate")
```

<!-- source: src/color_measurement/science.py:_lab_f_inverse -->
```python
def _lab_f_inverse(value: float) -> float:
    try:
        ratio = value**3 if value > _DELTA else (value - _OFFSET) / _LINEAR_SLOPE
    except OverflowError as error:
        raise ColorDomainError("inverse CIELAB intermediate overflows float precision") from error
    return _finite(ratio, "inverse CIELAB intermediate")
```

## Numerical choices and checks

The implementation uses Python binary64 floats, `math.fsum` for chromaticity
sums, and `math.cbrt` for the upper Lab branch. Constants are formed from 6/29,
841/108 and 4/29, rather than rounded decimal threshold/slope values.
Branch equality uses the linear expression. Nonfinite inputs, required
intermediates and results are refused. There is no near-zero denominator cutoff.
Large inputs can be refused even if a differently rearranged expression would
avoid an intermediate overflow. Ordinary rounding and underflow still apply;
arbitrary magnitudes are not covered by a uniform accuracy guarantee.

[`tests/test_science.py`](../tests/test_science.py) uses original analytical
values, rather than another color implementation or captured instrument data:

| Synthetic XYZ and white | Expected Lab |
| --- | --- |
| Zero, W=(80,100,120) | (0,0,0) |
| W, same W | (100,0,0) |
| W/8, same W | (42,0,0) |
| (17.28,12.5,7.68), W | (42,50,20) |
| (0,e,1), white=(1,1,1), e=216/24389 | (8,−1000/29,−4600/29) |
| (−e,0,e), white=(1,1,1) | (0,−1000/29,−400/29) |

These exercise unequal channels and different branches within one conversion.
Neutral ratios 0.999e and 1.001e give L*=7.992 and approximately
8.007997334813827884. They distinguish branches more effectively than testing
only adjacent floats at the junction, where both branches agree to rounding.
Other tests cover signed values, common-scale invariance, inverse conversions,
zero denominators, invalid whites, nonfinite values and overflow.

The default test comparison uses absolute tolerance 2e-12 and relative tolerance
2e-14; selected independently specified moderate Lab/XYZ vectors use absolute
1e-10 and zero relative tolerance. These permit accumulated binary64 arithmetic
error while detecting scale, channel and branch mistakes. Tighter junction checks
test local continuity; they are not a precision claim over all possible inputs.
None of these tolerances represents measurement uncertainty or a perceptual
acceptance threshold. These four conversion tests require no external
colorimetric dataset. CIEDE2000 separately uses the attributed test data below.

Run the numerical and excerpt checks from the repository root:

```sh
uv run pytest tests/test_science.py
uv run python tools/check_doc_snippets.py --check docs --require xyz_to_xyy --require xyy_to_xyz --require xyz_to_lab --require lab_to_xyz
```

Each displayed implementation block is extracted by the adjacent source marker;
the checker parses the real symbol and compares its source bytes without importing
it. To retrieve one block for a documentation update:

```sh
uv run python tools/check_doc_snippets.py --extract src/color_measurement/science.py:xyz_to_lab
```

## CIEDE2000

[`delta_e_2000(lab_a, lab_b)`](../src/color_measurement/science.py) follows
Sharma, Wu and Dalal (2005), *The CIEDE2000 Color-Difference Formula:
Implementation Notes, Supplementary Test Data, and Mathematical Observations*,
Color Research & Application 30(1), 21–30,
[doi:10.1002/col.20070](https://doi.org/10.1002/col.20070), equations 2–22 on
p. 22 and implementation notes on p. 23. The
[authors' accessible paper](https://hajim.rochester.edu/ece/sites/gsharma/ciede2000/ciede2000noteCRNA.pdf)
is the formula source; their restricted MATLAB/Excel code is not copied or
translated. This Python implementation fixes the parametric factors
kL=kC=kH=1. Other application-specific weighting choices are outside this API.

For two CIELAB triples, define, for i=1,2,

\[
C_i=\sqrt{a_i^{*2}+b_i^{*2}},\quad \bar C=(C_1+C_2)/2,\quad
G=\tfrac12\left(1-\sqrt{\frac{\bar C^7}{\bar C^7+25^7}}\right),
\]

\[
a'_i=(1+G)a_i^*,\quad C'_i=\sqrt{a_i'^2+b_i^{*2}},\quad
h'_i=\operatorname{atan2}(b_i^*,a'_i)\text{ in }[0,360)\text{ degrees}.
\]

When both modified coordinates are zero, set h′=0. Set ΔL′=L₂*−L₁* and
ΔC′=C₂′−C₁′. Δh′ is zero if either C′ is zero. Otherwise subtract the two
hues and wrap differences above 180° by subtracting 360°, and differences
below −180° by adding 360°. Exact ±180° keeps its sign. Then

\[
\Delta H'=2\sqrt{C'_1 C'_2}\sin(\Delta h'/2),\quad
\bar L'=(L_1^*+L_2^*)/2,\quad \bar C'=(C'_1+C'_2)/2.
\]

The mean hue is h₁′+h₂′ if either chroma is zero. For nonzero chromas it is
(h₁′+h₂′)/2 when their absolute difference is at most 180°. Otherwise it is
(h₁′+h₂′+360)/2 if their sum is below 360°, and
(h₁′+h₂′−360)/2 if their sum is at least 360°. This last branch matters because
the Gaussian below depends on the represented angle, not only its periodic
trigonometric value.

With angles in degrees, the weighting and rotation terms are

\[
T=1-0.17\cos(\bar h'-30)+0.24\cos(2\bar h')
 +0.32\cos(3\bar h'+6)-0.20\cos(4\bar h'-63),
\]

\[
\Delta\theta=30\exp[-((\bar h'-275)/25)^2],\quad
R_C=2\sqrt{\frac{\bar C'^7}{\bar C'^7+25^7}},\quad
R_T=-\sin(2\Delta\theta)R_C,
\]

\[
S_L=1+\frac{0.015(\bar L'-50)^2}{\sqrt{20+(\bar L'-50)^2}},\quad
S_C=1+0.045\bar C',\quad S_H=1+0.015\bar C'T,
\]

\[
\Delta E_{00}=\sqrt{(\Delta L'/S_L)^2+(\Delta C'/S_C)^2
 +(\Delta H'/S_H)^2+R_T(\Delta C'/S_C)(\Delta H'/S_H)}.
\]

The signed differences remain signed in the cross-term. The result and Lab
coordinates have unit one. Callers must establish that the two Lab values use
compatible reference-white/observer/calculation conventions. The function does
not adapt mismatched whites, choose an acceptance threshold, attach an uncertainty,
or establish perceptual validity for arbitrary signed/out-of-range triples.

### Worked examples and numerical proof

For neutral Lab=(40,0,0) and (60,0,0), mean L*=50 makes SL=1, and the chroma/hue
terms vanish: ΔE00=20. The first published test pair gives approximately 2.0425.

```python
from color_measurement.science import delta_e_2000

print(delta_e_2000((40, 0, 0), (60, 0, 0)))  # 20.0
print(delta_e_2000((50, 2.6772, -79.7751), (50, 0, -82.7485)))  # 2.0424596801565764
```

[`tests/test_science_s1.py`](../tests/test_science_s1.py) checks all 34
[published pairs](https://hajim.rochester.edu/ece/sites/gsharma/ciede2000/dataNprograms/ciede2000testdata.txt)
in both orders with absolute tolerance 5×10⁻⁵ for four-decimal rounding.
The retained fixture and its separate published-byte/fixture hashes are
documented in [`tests/data/README.md`](../tests/data/README.md); matching numeric
values does not imply identical source formatting.

The extra pair (50,20,−20)/(50,80,80) expects `39.01817399392473` with absolute
tolerance 10⁻¹⁰. Its provenance is an earlier independent 85-digit evaluation
of the paper equations; that evaluation was not rerun for this implementation.
A bounded mutation of the above-turn mean-hue branch passes the 34 rounded
reference pairs but fails this separate oracle. Tests also cover exact 180°
branches, zero/neutral values, symmetry, tiny nonzero chromas, large finite
chromas, invalid triples and nonrepresentable required arithmetic. Tolerances
cover software arithmetic, not an optical specification.

### Actual implementation: `delta_e_2000`

<!-- source: src/color_measurement/science.py:delta_e_2000 -->
```python
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
```

### Actual hue and chroma helpers

The chroma helper evaluates C⁷/(C⁷+25⁷) through a ratio bounded by one before
raising it to the seventh power. This avoids power overflow for large finite
chromas. The hue helper implements the zero-chroma convention and degree range.

<!-- source: src/color_measurement/science.py:_chroma_seventh_ratio -->
```python
def _chroma_seventh_ratio(chroma: float) -> float:
    """Evaluate C**7 / (C**7 + 25**7) without raising a large C to a power."""
    if chroma >= 25.0:
        seventh = (25.0 / chroma) ** 7
        return 1.0 / (1.0 + seventh)
    seventh = (chroma / 25.0) ** 7
    return seventh / (1.0 + seventh)
```

<!-- source: src/color_measurement/science.py:_lab_hue -->
```python
def _lab_hue(a_prime: float, b_star: float) -> float:
    """Return the modified hue in degrees, with zero for an achromatic pair."""
    if a_prime == 0.0 and b_star == 0.0:
        return 0.0
    return math.degrees(math.atan2(b_star, a_prime)) % 360.0
```

`math.hypot` evaluates chromas without directly squaring large components.
The geometric chroma uses sqrt(C₁′)sqrt(C₂′), avoiding the direct chroma product.
The final expression completes the square: for weighted L,C,H and r=RT,
ΔE00=hypot(L, C+rH/2, H·sqrt(1−(r/2)²)). This retains the rotation cross-term
while avoiding squares of large weighted values. The lightness weight similarly
uses `hypot(sqrt(20), deviation)` instead of squaring a large deviation.
Required nonfinite intermediate/results raise `ColorDomainError`; the API may
refuse extreme values even when a different rearrangement could avoid an
intermediate overflow. Binary64 rounding and underflow remain. The formula
itself has hue-seam discontinuities discussed in the paper; this implementation
does not smooth them or claim a uniform accuracy bound over arbitrary magnitudes.

## CIE 1976 u′v′ with retained Y

[`xyz_to_uvy(xyz)`](../src/color_measurement/science.py) implements CIE S
017:2020, *International Lighting Vocabulary*,
[e-ILV 17-23-073](https://cie.co.at/eilvterm/17-23-073), defining equations:

\[
D=X+15Y+3Z,\qquad u'=\frac{4X}{D},\qquad v'=\frac{9Y}{D}.
\]

The result `(u′,v′,Y)` retains supplied Y. X,Y,Z must share a unit/scale;
chromaticity has unit one and Y retains its original relative/absolute units.
This is CIE 1976 UCS: v′=1.5v of CIE 1960 uv. It is not a colour-difference
metric or a CIELUV conversion, and no reference white is inferred.

D=0, including black or signed cancellation, raises `ColorDomainError`.
There is no fabricated white chromaticity or near-zero denominator cutoff.
An analysis owner must represent that error as an explicit unavailable result
when attaching it to a measurement. The existing observation enrichment still
stores only xyY and optional Lab; adding this pure function does not silently
change observation fields or serialization.

### Worked example and checks

For synthetic XYZ=(2,3,5), D=2+45+15=62, so u′=8/62 and v′=27/62; Y remains 3.
Multiplying all three inputs by a common nonzero factor retains chromaticities
and scales only Y. Signed inputs remain an algebraic calculation, without a
physical-stimulus claim.

```python
from color_measurement.science import xyz_to_uvy

print(xyz_to_uvy((2, 3, 5)))  # (0.12903225806451613, 0.43548387096774194, 3.0)
```

Tests check the analytic vector, signed scaling, retained Y, black/cancellation,
small nonzero denominators, malformed/nonfinite inputs and arithmetic overflow.
Required intermediate/results must be finite; weighting a very large input
can overflow even if an alternative expression would yield finite coordinates.
`math.fsum` combines the three weighted terms, but it cannot remove binary64
rounding of each multiplication or underflow. No absolute accuracy guarantee
is asserted near cancellation.

### Actual implementation: `xyz_to_uvy`

<!-- source: src/color_measurement/science.py:xyz_to_uvy -->
```python
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
```

### Inverse u′v′Y and the information-preserving domain

`uvy_to_xyz(uvy)` algebraically inverts the same primary defining equations.
From D=9Y/v′ and X=u′D/4, solve 3Z=D−X−15Y to obtain

\[
X=\frac{9u'Y}{4v'},\qquad
Y=Y,\qquad
Z=\frac{(12-3u'-20v')Y}{4v'}.
\]

The code evaluates q=Y/v′, X=(9/4)u′q and
Z=3q−(3/4)u′q−5Y. `math.fsum` combines the three Z terms. Every required term
and result must remain finite. This factorization avoids an unnecessary 20v′
intermediate and retains the same algebraic definition. Binary64 rounding,
underflow, cancellation and finite intermediate limits still apply; a
differently arranged expression may avoid an overflow this API refuses.

The output preserves the supplied Y exactly, with all XYZ components in its
units and scale. u′ and v′ have unit one. Signed XYZ is retained without
clamping or a physical-stimulus claim. The caller must supply CIE 1976 u′v′,
rather than CIE 1960 uv; the inverse does not infer a white, observer, adaptation,
or a physically valid stimulus.

Zero v′ is refused with `ColorDomainError`, including when Y=0. There is no
near-zero cutoff. Nonzero v′ and Y=0 produce black, which loses the supplied
chromaticity and cannot be sent back through the forward map. Conversely,
nonzero XYZ with Y=0 can have a valid forward coordinate with v′=0, but that
u′v′Y representation has lost the scale needed for a unique inverse. Thus
round-trip assertions apply only where Y≠0, D≠0, v′≠0, and both computations
retain information and finite arithmetic. Rounding/underflow can narrow that
domain further.

For the synthetic example above, u′=8/62, v′=27/62 and Y=3. Inversion gives
X=9(8/62)3/[4(27/62)]=2 and Z=5, retaining Y=3:

```python
from color_measurement.science import uvy_to_xyz

print(uvy_to_xyz((8 / 62, 27 / 62, 3)))  # approximately (2, 3, 5)
```

[`tests/test_science_uvy_inverse.py`](../tests/test_science_uvy_inverse.py)
uses exact-rational primary forward ratios and independently formed inverse
equations, including signed and positive vectors. It checks both round-trip
directions on the retained-information domain, shared Y scaling, zero-v′
refusal, black/zero-Y losses, tiny nonzero v′ without a cutoff, malformed and
nonfinite inputs, and intermediate/result overflow. Moderate-vector tolerances
are absolute 2×10⁻¹⁴ and relative 2×10⁻¹⁴, with absolute 2×10⁻¹³ for the larger
inverse vectors. Tiny-scale round trips use zero absolute tolerance. These
are software numerical tolerances, not measurement error or uncertainty bounds.

### Actual implementation: `uvy_to_xyz`

<!-- source: src/color_measurement/science.py:uvy_to_xyz -->
```python
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
```

## Native Y change and CIE 1976 coordinate distance

[`processing.compare_xyz(reference, sample)`](../src/color_measurement/processing.py)
composes two derived metrics from typed native
[`XYZ` records](../src/color_measurement/models.py). It reuses
[`xyz_to_uvy`](#cie-1976-uv-with-retained-y) for coordinates. Both inputs retain
values, unit, scale, observer and illuminant; none is rewritten or normalized.
The function requires exact agreement of all four declared context fields.
Matching labels do not establish matching acquisition geometry, calibration,
measurement conditions or physical accuracy. Those remain source-record and
caller concerns.

This API defines signed sample-minus-reference native-Y change as

\[
\Delta Y_{\%}=100\frac{Y_{\mathrm{sample}}-Y_{\mathrm{reference}}}
                         {Y_{\mathrm{reference}}},\qquad
Y_{\mathrm{reference}}>0.
\]

Percent is parts per hundred, as specified by the
[BIPM SI Brochure, ninth edition, section 5.4.7](https://www.bipm.org/en/publications/si-brochure).
The common source unit cancels; the result is a dimensionless value expressed in
percent. It is not a percentage-point subtraction, unsigned error or symmetric
metric. Y is luminance only when the record declares absolute cd/m² and
`scale="absolute; Y is luminance"`. The authoritative
[quantity mapping](../src/color_measurement/argyll_capability.py) also retains
illuminance, time-integrated quantities and distinct relative reflection and
transmission scales. They must not be relabeled or mixed merely because a
percentage has no source unit.

The second result is ordinary Euclidean separation in the CIE 1976 diagram:

\[
\Delta u'v'=\sqrt{(u'_{\mathrm{sample}}-u'_{\mathrm{reference}})^2+
                        (v'_{\mathrm{sample}}-v'_{\mathrm{reference}})^2}.
\]

Coordinates use the defining equations from
[CIE S 017:2020, e-ILV 17-23-073](https://cie.co.at/eilvterm/17-23-073),
\(u'=4X/(X+15Y+3Z)\) and \(v'=9Y/(X+15Y+3Z)\). The distance has unit one
and is symmetric. It is a planar coordinate separation, not CIELUV ΔE, signed
CIE 1960 Planckian-locus Duv, an uncertainty estimate, a just-noticeable difference
or a pass/fail threshold.

### Result and independent availability

The frozen `XYZComparison` contains `y_percent`, `uv_distance`, independent
`y_reason` and `uv_reason`, fixed `y_unit="%"` / `uv_unit="1"`, and algorithm
identity `xyz-native-reference-comparison`, version `1`. A value is null exactly
when that metric is unavailable; its reason is then present. A valid zero stays
zero. Missing XYZ and unequal context make both metrics unavailable. Non-XYZ,
non-null inputs raise `ColorDomainError` rather than being guessed from tuples.

A black sample against a positive-Y reference has ΔY%=−100 but undefined u′v′.
A reference with Y=0 can have algebraic chromaticity, allowing a coordinate
distance while its percentage is unavailable. Signed samples are retained,
including percentage values below −100; signed chromaticities are algebraic and
carry no physical-stimulus claim. No positive-Y cutoff beyond zero, fabricated
white chromaticity, normalization, clipping, adaptation or spectral integration
is applied. Neither metric uses a Lab white or RGB preview scale. The desktop
owner still requires its [saved authenticated reference](application.md#persisted-quantitative-comparison-context).

### Worked examples and numerical checks

For reference XYZ=(1,1,1) and sample XYZ=(2,2,2) on one compatible scale,
ΔY%=100 and coordinate distance is zero. Swapping the pair gives −50 percent,
while its coordinate distance remains zero.

A separate coordinate example uses reference=(0,1,0) and sample=(1,1,0).
Their coordinates are (0,3/5) and (1/4,9/16). The difference is (20,−3)/80,
so the distance is \(\sqrt{409}/80\); their unchanged Y gives ΔY%=0.

```python
from color_measurement.models import XYZ
from color_measurement.processing import compare_xyz

context = dict(unit="cd/m^2", scale="absolute; Y is luminance",
               observer="CIE1931_2", illuminant="none (emissive)")
reference = XYZ((1, 1, 1), **context)
sample = XYZ((2, 2, 2), **context)
result = compare_xyz(reference, sample)
print(result.y_percent, result.uv_distance)  # 100.0 0.0
black = compare_xyz(reference, XYZ((0, 0, 0), **context))
print(black.y_percent, black.uv_distance)    # -100.0 None
```

[`tests/test_xyz_comparison.py`](../tests/test_xyz_comparison.py) checks these
analytic vectors, symmetry versus percentage asymmetry, common and signed
scaling, exact integer residuals before division, immutable inputs and results,
missing records, all four metadata mismatches, incompatible native quantities,
nonpositive/tiny reference Y, independently unavailable metrics and overflow.
[`tests/test_desktop_session.py`](../tests/test_desktop_session.py) checks the
saved unplotted reference, independent reasons/summaries, Lab-white and preview-
scale independence, unchanged observations and refusal of unsaved or externally
changed comparison context. These are software and substituted-DTO tests.

Binary64 division, multiplication, subtraction and coordinate transforms can
round or underflow. Integer Y pairs subtract before division to preserve an
integer residual. Float/mixed subtraction remains floating point. The existing
coordinate transform can reject a nonfinite required intermediate even if
rearranging its formula could avoid that intermediate. Nonfinite percentage or
distance arithmetic remains unavailable rather than overflowing JSON or
fabricating zero. There is no accuracy guarantee near denominator cancellation
or tiny positive reference Y. No instrument, optical or perceptual accuracy is
established by these calculations.

### Actual implementation: `compare_xyz`

<!-- source: src/color_measurement/processing.py:compare_xyz -->
```python
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
```

## Explicit-scale XYZ to sRGB or Display P3

[`xyz_to_rgb(xyz, *, color_space, reference_luminance)`](../src/color_measurement/science.py)
requires the exact space name `"srgb"` or `"display-p3"`. Both matrices follow
[CSS Color Module Level 4, §19](https://www.w3.org/TR/2026/CRD-css-color-4-20261009/#color-conversion-code),
pinned to the 9 October 2026 Candidate Recommendation Draft. Its conversion
code is informative. The implementation uses its numerical definitions and
original Python arithmetic, rather than translating the JavaScript library.
[§10.2](https://www.w3.org/TR/2026/CRD-css-color-4-20261009/#predefined-sRGB)
and [§10.4](https://www.w3.org/TR/2026/CRD-css-color-4-20261009/#predefined-display-p3)
define the primary chromaticities and D65 white. Display P3 is not DCI-P3.

The shared bounded transfer also follows A.8 of the ICC registry's
[*How to interpret the sRGB color space (specified in IEC 61966-2-1) for ICC profiles*](https://registry.color.org/rgb-registry/files/sRGB.pdf),
pp. 1–2. The ICC document's rounded A.7 matrix is **not** used: its coefficient
rounding shifts nominal D65 by roughly 10⁻⁴ in linear channels, separately from
binary64 arithmetic. Both spaces now use consistent exact-derived reference
matrices. The full IEC standard was not inspected; this API makes no blanket
IEC or CSS conformance claim.

### One matrix owner, scale and equations

`_RGB_TO_XYZ_EXACT` is the authoritative pair of source-defined, exact-rational
forward matrices. The source coefficients encode their primaries and D65
white at Y=1. The inverse is derived once by exact `Fraction` row reduction,
then both matrices are rounded to binary64 for scalar calculations. Geometry,
encoding and status all use those matrices; there is no second inversion in
the presentation layer.

For an explicit strictly positive finite display-white Yref on the same scale
and units as XYZ,

\[
(X_r,Y_r,Z_r)=\frac{(X,Y,Z)}{Y_{\mathrm{ref}}},\qquad
\begin{bmatrix}R_L\\G_L\\B_L\end{bmatrix}=M^{-1}
\begin{bmatrix}X_r\\Y_r\\Z_r\end{bmatrix}.
\]

The resulting inverse matrices equal CSS §19's rational definitions:

\[
M^{-1}_{\mathrm{srgb}}=
\begin{bmatrix}
\frac{12831}{3959}&-\frac{329}{214}&-\frac{1974}{3959}\\
-\frac{851781}{878810}&\frac{1648619}{878810}&\frac{36519}{878810}\\
\frac{705}{12673}&-\frac{2585}{12673}&\frac{705}{667}
\end{bmatrix},
\]

\[
M^{-1}_{\mathrm{display-p3}}=
\begin{bmatrix}
\frac{446124}{178915}&-\frac{333277}{357830}&-\frac{72051}{178915}\\
-\frac{14852}{17905}&\frac{63121}{35810}&\frac{423}{17905}\\
\frac{11844}{330415}&-\frac{50337}{660830}&\frac{316169}{330415}
\end{bmatrix}.
\]

For absolute emissive XYZ, the caller may declare Yref=100 cd/m² and supply
XYZ in cd/m². Relative scales are also algebraically supported. Zero-black
XYZ/Yref scaling is an explicit project choice, distinct from ICC A.6's
glare/black normalization or CSS reference viewing conditions. No default,
reading-specific white, black subtraction, flare compensation or tone mapping
is inferred. CIE 1931 2° / D65-target conditions must be established by the
caller; source white/observer cannot be identified from three numbers.
No chromatic adaptation neutralizes a non-D65 stimulus.

For each calculated channel c, retain c and separately form
cᵈ=min(1,max(0,c)). The clipping flag is exactly c<0 or c>1. Encode cᵈ with

\[
E(c^d)=\begin{cases}
12.92c^d,&c^d\le0.0031308,\\
1.055(c^d)^{1/2.4}-0.055,&c^d>0.0031308.
\end{cases}
\]

The frozen `RGBEncoding` holds `color_space`, signed/unbounded `linear_rgb`,
bounded `display_rgb` and `clipped_channels`, in R/G/B order. RGB has unit one.
Boundary comparisons operate on the calculated floats **without an epsilon**.
Nominal D65 and primary tests allow binary64 numerical error, but the returned
channels are not snapped to symbolic 0 or 1. A nominal white can therefore
retain an excursion of a few 10⁻¹⁶ and its arithmetic flag. Show the signed
value and scale when interpreting that flag; it is not proof of physical
clipping, device gamut, optical accuracy or measurement uncertainty.

### Worked example

Synthetic XYZ=(20,30,40), with explicit Yref=100, gives relative XYZ=(0.2,0.3,0.4).
Both conversions preserve native XYZ and its scale:

| Requested space | Signed linear RGB, approximately | Encoded display RGB, approximately | Clipped R/G/B |
| --- | --- | --- | --- |
| `srgb` | (−0.01246527, 0.38556355, 0.37272153) | (0, 0.65423873, 0.64429860) | (True, False, False) |
| `display-p3` | (0.05819998, 0.37235130, 0.36707126) | (0.26757966, 0.64400909, 0.63986182) | (False, False, False) |

```python
from color_measurement.science import xyz_to_rgb

rgb = xyz_to_rgb((20, 30, 40), color_space="display-p3", reference_luminance=100)
print(rgb.color_space)       # display-p3
print(rgb.linear_rgb)        # approximately (0.05819998, 0.37235130, 0.36707126)
print(rgb.display_rgb)       # approximately (0.26757966, 0.64400909, 0.63986182)
print(rgb.clipped_channels)  # (False, False, False)
```

Scaling XYZ and Yref by the same positive factor preserves the calculation
within binary64 rounding. Changing only Yref changes the display representation.
A `color(display-p3 R G B)` CSS value can carry the bounded encoding. Browser
parsing/color management and a connected monitor's physical gamut need separate
qualification. This API performs channel clipping, not CSS gamut mapping or
an appearance-matching transform. Clipping can remove detail and change hue.

### Actual implementation and shared numerical helpers

The inverse helper computes [M|I] → [I|M⁻¹] by exact row operations on the fixed
source matrices. The product helper computes each row sum with `math.fsum`,
rejecting nonfinite products before summation. Required arithmetic can fail
even if a different rearrangement would avoid an intermediate overflow.

<!-- source: src/color_measurement/science.py:_inverse_matrix -->
```python
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
```

<!-- source: src/color_measurement/science.py:_matrix_product -->
```python
def _matrix_product(matrix: tuple[Triple, ...], values: Triple) -> Triple:
    """Return M*v with finite products and finite, compensated channel sums."""
    channels = tuple(
        _sum(tuple(_finite(coefficient * component, "matrix term")
                   for coefficient, component in zip(row, values)), "matrix channel")
        for row in matrix
    )
    return channels[0], channels[1], channels[2]
```

<!-- source: src/color_measurement/science.py:xyz_to_rgb -->
```python
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
```

The transfer helper receives only a channel already bounded to [0,1]. For
example, E(0.5)≈0.735356983052449491. It does not expose a public signed transfer
or quantization API. The rounded breakpoint has a discontinuity of roughly
2.85×10⁻⁸; the implementation preserves it rather than smoothing it.

<!-- source: src/color_measurement/science.py:_display_encode -->
```python
def _display_encode(linear: float) -> float:
    """Encode a bounded sRGB or Display P3 channel by the shared sRGB transfer."""
    return 12.92 * linear if linear <= 0.0031308 else 1.055 * linear ** (1.0 / 2.4) - 0.055
```

## Numerical gamut status, separately from display encoding

[`rgb_gamut_status`](../src/color_measurement/science.py) returns a frozen
`RGBGamutStatus` with its `RGBEncoding`, explicit `reference_luminance`,
`below_zero_channels`, `above_one_channels`, `within_rgb_cube`,
`positive_stimulus_eligible`, separate `xy`/`uv` results, algorithm/version,
`numerical_state`, `numerical_error_bounds`, `max_lower_excess`,
`max_upper_excess`, `required_positive_rgb_scale`, `fit_positive_rgb_scale` and
`fit_scale_reason`. This contract uses algorithm version `"2"`.
The two channel masks are independent. The closed scaled cube requires
0≤Rᴸ,Gᴸ,Bᴸ≤1; it is not a chromaticity triangle or a measured device volume.

Each `ChromaticityGamutStatus` carries `coordinates`, raw `in_triangle`,
`unavailable_reason`, `numerical_state` and `numerical_error_bounds`. The existing CIE xy/u′v′ calculations supply coordinates.
Closed-triangle membership follows the same primary matrix. For projection
denominator D and reference-primary denominator Dᵢ, barycentric weights are

\[
\lambda_i=\frac{c_iD_i}{D},\qquad\sum_i\lambda_i=1.
\]

Here D=X+Y+Z for xy or X+15Y+3Z for u′v′. Reference-primary Dᵢ are positive.
For **positive-stimulus eligible** XYZ, D>0, so all cᵢ≥0 is the algebraic
closed-triangle test. No upper channel bound appears in that test. A common
max-absolute scaling avoids chromaticity overflow without affecting the
separate display scale. This scaling is only a numerically bounded
chromaticity calculation, not a brightness normalization of the observation.

`positive_stimulus_eligible` requires native X,Y,Z≥0 and native Y>0. These are
necessary domain conditions, not proof of a realizable spectrum or an accurate
instrument reading. Signed/nonpositive-luminance inputs may still return
algebraic coordinates, but `in_triangle=None` with a reason. Neither an
all-negative XYZ normalization nor a black should be labelled inside a
positive display triangle. Black has zero RGB and is inside the cube; its
chromaticities are undefined. Zero-denominator/nonfinite projections also
return `None` with a reason rather than invented coordinates.

For (20,30,40) at Yref=100, sRGB reports a negative red channel and outside
both reference triangles; P3 reports inside both triangles and the cube.
Doubling a nominal D65 white beyond Yref leaves its chromaticity inside both
triangles but raises the upper-bound channel flags. Those are separate
arithmetic facts; no returned suggestion changes the supplied scale or performs gamut mapping.

```python
from color_measurement.science import rgb_gamut_status

status = rgb_gamut_status((20, 30, 40), color_space="srgb", reference_luminance=100)
print(status.below_zero_channels)  # (True, False, False)
print(status.above_one_channels)   # (False, False, False)
print(status.xy.in_triangle)       # False
print(status.within_rgb_cube)      # False
```

<!-- source: src/color_measurement/science.py:_chromaticity_gamut_status -->
```python
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
```

<!-- source: src/color_measurement/science.py:rgb_gamut_status -->
```python
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
```

### Declared numerical boundary state

Raw channels, clipping masks and raw triangle/cube comparisons are preserved
without epsilon or snapping. The separate `numerical_state` is `"inside"`,
`"on-boundary"` or `"outside"`. It uses an explicitly declared binary64
arithmetic band, not an instrument uncertainty, optical specification, input
quantization allowance or perceptual threshold. A genuinely tiny exclusion
can lie in that band too; on-boundary is a numerical policy, not a diagnosis
that rounding caused the exclusion.

For unit roundoff u=2⁻⁵³ and the computed scaled inputs v, each row receives

\[
\gamma_8=\frac{8u}{1-8u},\qquad
\tau_i=\gamma_8\sum_j|M^{-1}_{ij}v_j|+8\operatorname{ulp}(0).
\]

This is a conservative engineering budget for coefficient rounding, scale
division, products and compensated summation, including possible double
rounding and a gradual-underflow floor. The standard γₙ model is Higham,
*Accuracy and Stability of Numerical Algorithms*, second edition (2002),
[Lemma 3.1](https://doi.org/10.1137/1.9780898718027). The choice n = 8 is this
implementation’s conservative engineering budget. General rounding and
cancellation discussion follows Goldberg,
[*What Every Computer Scientist Should Know About Floating-Point Arithmetic*](https://doi.org/10.1145/103162.103163),
1991, *Relative Error and Ulps*, *Cancellation* and *Rounding Error*; the
[authorized reprint](https://docs.oracle.com/cd/E19957-01/806-3568/ncg_goldberg.html)
was inspected. Python's [`math.fsum`](https://docs.python.org/3/library/math.html#math.fsum)
uses compensated summation and documents possible double rounding on some
builds. The inputs to this arithmetic policy are the already converted floats;
it does not bound the difference between a recorded float and a physical
quantity, or certify arbitrary rounding environments.

The implementation scales the sum of absolute terms before applying γ₈,
avoiding overflow of an otherwise diagnostic sum. `numerical_error_bounds`
have unit one in the corresponding linear-channel coefficient coordinates.
Triangle bounds use max-absolute-scaled XYZ coefficients, not distances in
xy/u′v′ and not the display's upper channel bounds.

For the cube, `"outside"` requires cᵢ<−τᵢ or cᵢ−1>τᵢ. Otherwise any
|cᵢ|≤τᵢ or |cᵢ−1|≤τᵢ gives `"on-boundary"`; the remaining case is
`"inside"`. Triangle numerical status uses the lower boundary only. Its
positive-stimulus domain/availability rules still apply; signed or black
chromaticities do not acquire a triangle status through this band.

A nominal D65 white at Yref=1,100 or120 is numerically on the cube boundary;
primaries are on the cube and triangle boundaries. A retained raw channel may
still be 1+2×10⁻¹⁶ with its upper flag true. The numerical state distinguishes
that arithmetic-sized excursion from substantive exclusion without hiding the
signed value or exact flag.

<!-- source: src/color_measurement/science.py:_matrix_error_bounds -->
```python
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
```

<!-- source: src/color_measurement/science.py:_numerical_rgb_state -->
```python
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
```

### Magnitudes and explicit positive-channel scale fitting

For the returned signed c, raw excesses are

\[
e_-=\max(0,\max_i(-c_i)),\qquad
e_+=\max(0,\max_i(c_i-1)).
\]

They have unit one, no threshold, and do not express a perceptual color
error. A useful common-scale suggestion must consider the largest positive
RGB channel, rather than only native Y. The raw required and conservative
fit values are

\[
S_{\rm required}=S\max(0,c_R,c_G,c_B),\qquad
S_{\rm fit}=\operatorname{nextafter}\!\left(S\max_i(c_i+\tau_i),+\infty\right).
\]

A positive fit is offered only when a positive channel exists and the result
is representable. Otherwise it is `None` with `fit_scale_reason`; the other
status/encoding results remain available. Both scales have the same units
and reference convention as XYZ/Yref. A host can take the maximum provided
fit value over explicitly checked compatible readings and apply it as one
common display scale after the operator's action. This function itself does
not alter a scale, white, native XYZ or any reading.

Example: a synthetic (0.1,0.1,1.6) linear sRGB color at S=100 has native Y
below100 but requires a positive-channel scale of about160. Fitting to maximum
Y would miss that upper blue channel. The conservative suggested scale carries
an arithmetic margin; it is not a calibrated display white or measured peak.
Finite precision can still produce raw near-boundary flags after recomputation;
interpret them with the declared numerical state rather than claiming exact
no-clipping behavior.

A larger scale does not remove negative channels or move chromaticity inside
a triangle. It performs neither adaptation, gamut mapping, a Lab-white change
nor per-reading brightness normalization. Spectrally/physically invalid inputs
remain invalid for positive-stimulus triangle classification. No returned
quantity certifies physical gamut, coverage or measurement uncertainty.

<!-- source: src/color_measurement/science.py:_positive_rgb_fit -->
```python
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
```

Numerical tests independently check nominal D65 and primaries at scales1/100/120,
near/substantively outside and signed inputs, exact raw masks/excesses, upper
scale fitting independent of Y, nonrepresentable suggestions and 246 vectors
against exact rational primary-source matrix evaluation. These are software
arithmetic checks, not a measurement or optical calibration.

## Ideal reference geometry and explicit-white Lab projection

[`rgb_reference_geometry`](../src/color_measurement/science.py) provides ideal
sRGB/P3 primary and D65-white XYZ, xy and u′v′, plus a sampled linear-RGB cube
surface with supplied triangle/grid-line indices. It reuses the forward
matrix owned by the encoder, with

\[
\mathbf{XYZ}=Y_{\mathrm{ref}}M\mathbf c,\qquad
c_i=\frac{k_i}{n-1},\quad k_i\in\{0,\ldots,n-1\}.
\]

Only lattice points with at least one channel 0 or 1 are retained. They form
a unique shell of n³−(n−2)³ points in lexicographic R/G/B order. `triangles`
connect two triangles per cell of all six cube faces with outward orientation
in linear RGB; `lines` connect nearest shell neighbors. Both use indices into
`surface`. A host can project these supplied points and indices without
reconstructing a color model or inventing connectivity. The finite triangular
mesh approximates the transformed nonlinear Lab surface; it is not a
continuous perceptual-volume measurement.

`RGBReferenceGeometry` records the target space, Yref, supplied Lab white,
sampling resolution, primary/white coordinates, surface/topology and method
version. Each `RGBReferencePoint` holds `linear_rgb`, `xyz`, `lab`, `xy` and
`uv`. XYZ has the caller's Yref units/scale; the other coordinates have unit
one. The synthetic zero vertex has no xy/u′v′. Chromaticities are calculated
from relative model XYZ, avoiding absolute-scale overflow; extremely small
absolute scaling can still underflow sample XYZ components.

The required `reference_white` argument accepts **explicit `None`** for
XYZ-only geometry, leaving every point's `lab=None`. With a positive white,
Lab is calculated solely by the existing `xyz_to_lab` equations with that white
on the same scale and units as sample XYZ. The target D65 white is not silently
selected as the Lab reference or used for chromatic adaptation.

```python
from color_measurement.science import rgb_reference_geometry

reference = rgb_reference_geometry(
    color_space="display-p3", reference_luminance=100,
    reference_white=None, samples_per_edge=3,
)
print(len(reference.surface))    # 26
print(len(reference.triangles))  # 48
print(len(reference.lines))      # 48
print(reference.surface[0].xyz)  # (0.0, 0.0, 0.0)
print(reference.surface[0].lab)  # None: no Lab white selected
```

<!-- source: src/color_measurement/science.py:rgb_reference_geometry -->
```python
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
```

Sampling accepts integer n in [2,65], excluding bool. This bound limits the
returned geometry size; it does not set a scientific accuracy threshold.
Unsupported targets, invalid scale/white and nonfinite required arithmetic
raise `ColorDomainError`. The output is a **synthetic ideal reference**, not a
measured device gamut, coverage percentage, profile, appearance model or
uncertainty estimate. No adaptation, interpolation of measurements, CSS gamut
mapping or wider encoding is performed.

### Numerical proof and limits

[`tests/test_science_rgb.py`](../tests/test_science_rgb.py) compares nominal
D65 independently in both spaces, verifies source-defined primary
chromaticities, and tests the production inverse against independent published
forward vectors and inverse columns. Transfer references use 70-digit Decimal
arithmetic. Tests check a visible P3-only color, exact raw flags, signed/black
triangle availability, upper scale overflow separately from triangle membership,
common scaling, source preservation, invalid domains and binary64 extremes.
Geometry tests check unique shell counts, outward closed mesh connectivity,
grid lines, explicit-white Lab composition, XYZ-only mode and source-defined
xy/u′v′ geometry. Stated absolute tolerances account for binary64 rounding,
not optical or instrument specifications. Physical displays, CSS hosts and
hardware are outside this proof.

```sh
uv run pytest tests/test_science.py tests/test_science_s1.py tests/test_science_rgb.py
uv run python tools/check_doc_snippets.py --check docs --require xyz_to_rgb --require rgb_gamut_status --require rgb_reference_geometry
```

## Signed spectral comparison and sample-wise RMS

[`processing.compare_spectra(a, b)`](../src/color_measurement/processing.py)
takes two existing immutable `Spectrum` records. Their nonempty wavelength
tuples must match exactly, and quantity and unit must be identical. The
`Spectrum` constructor already requires finite samples and positive ascending
wavelengths. Missing records, a different grid, quantity or unit raise
`ColorDomainError` at comparison; constructing malformed records raises
`ValueError` at the record owner. No interpolation, normalization or unit
conversion is implicit.

For N corresponding samples, the comparison returns a frozen
`SpectrumComparison` containing a **derived** `SpectralResidual` and scalar RMS.
The residual is a separate frozen type, not a native `Spectrum`. Its extracted
object retains `wavelengths_nm`, signed `values`, `source_quantity`, `unit`,
`algorithm="spectral-residual-b-minus-a"` and `algorithm_version="1"`. It has
no native `quantity` field or compatibility alias. Retain source observations
for their acquisition/calibration context; this method identity does not
replace those records. Passing a residual back as a native comparison input
is refused. The arithmetic is

\[
r_i=B_i-A_i,\qquad \operatorname{RMS}(r)=\sqrt{\frac1N\sum_{i=1}^{N}r_i^2}.
\]

This uses the statistical root-mean-square definition documented by
[NIST Dataplot, ROOT MEAN SQUARE ERROR](https://www.itl.nist.gov/div898/software/dataplot/refman2/auxillar/rms.htm),
applied to the signed residual, rather than the difference of two individual
RMS values. The B−A direction is the application's explicit comparison
convention. Residual and RMS have the source unit; residual wavelengths remain
in nm. Each sample has equal weight, including an irregular common grid.
This is not an integral over wavelength or a grid-independent error measure.
Do not compare RMS across different grids without another defined method.

Source records are unchanged. The returned residual does not become a native
measurement and cannot establish calibration/context compatibility. Retain the
two original observations separately; eligibility beyond exact grid, quantity
and unit belongs to the analysis owner. A nonrepresentable residual raises
`ColorDomainError`. Signed or above-range values are preserved without a claim
of physically meaningful radiance or reflectance.

### Worked example and checks

A=(1,2), B=(−2,6) gives residual=(−3,4), RMS=sqrt((9+16)/2)=sqrt(12.5).

```python
from color_measurement.models import Spectrum
from color_measurement.processing import compare_spectra

a = Spectrum((400, 500), (1, 2), "reflectance", "%")
b = Spectrum((400, 500), (-2, 6), "reflectance", "%")
comparison = compare_spectra(a, b)
print(comparison.residual.values)  # (-3.0, 4.0)
print(comparison.rms)              # 3.5355339059327378, in percent
print(a.values)                    # (1, 2), unchanged
```

Tests cover the analytical signed case, zero/single-sample residuals, swapped
sign with invariant RMS, an irregular shared grid, exact-grid/quantity/unit
refusal, immutability, huge/tiny finite residuals and subtraction overflow.
Scaling by the maximum absolute residual before squaring prevents overflow of
otherwise representable RMS and loss of all tiny squares to underflow. The
Integer pairs are subtracted before conversion to binary64, preserving a small
integer residual even when the inputs individually exceed exact binary64 integer
precision. Float or mixed integer/float pairs use Python binary64 subtraction;
rounding during that subtraction, float conversion, division and the final
multiplication remains. Subnormal underflow can still occur. This statistic is not a perceptual
difference, measurement uncertainty, spectral integration or physical validation.

### Actual implementation: `compare_spectra`

<!-- source: src/color_measurement/processing.py:compare_spectra -->
```python
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
```

Run the S1 checks without an instrument:

```sh
uv run pytest tests/test_science_s1.py tests/test_science.py tests/test_reflectance_fraction.py tests/test_workflow.py
uv run python tools/check_doc_snippets.py --check docs --require delta_e_2000 --require xyz_to_uvy --require compare_spectra
```

## Qualified spectral XYZ integration

`spectral_xyz` requires the exact original CIE 1931 2° observer CSV bytes,
verified against the publisher's SHA-256
`fa663e3535a7e0763a745993a1f0a192eb0275ac46ad2d1befd7626841e713c1`.
A declared hash alone does not authenticate supplied rows. The caller provides
these bytes through its existing resource owner; the function performs no
filesystem, network or acquisition operation. The retained observer is
[CIE 2019, DOI 10.25039/CIE.DS.xvudnb9b](https://doi.org/10.25039/CIE.DS.xvudnb9b),
from CIE 018:2019 Table 6. The [original CSV](https://files.cie.co.at/Publications-datasets/CIE_xyz_1931_2deg.csv)
and [official metadata](https://files.cie.co.at/Publications-datasets/CIE_xyz_1931_2deg.csv_metadata.json)
are attributed to CIE under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
Those terms apply to original/derived table data separately from application
code. The shared resource retains its data and illustration notices; no new
observer dataset is manufactured or duplicated by this function.

The explicit observer is `CIE1931_2`; the wavelength convention is
`standard-air-nm`. The supplied grid must contain at least two increasing,
uniform samples, wholly within 360–830 nm. The complete grid is checked against
its endpoint-defined spacing with a bounded binary64 roundoff allowance.
CMFs use exact integer rows or linear interpolation, consistent with CIE's
metadata and [BIPM/CIE *Principles Governing Photometry*, G1 (2019), §9](https://www.bipm.org/documents/20126/52718503/G1-2019.pdf).
There is no CMF extrapolation, input-spectrum interpolation, missing-range energy
estimate or automatic cropping. Out-of-range spectra and irregular grids are
refused. A shorter in-range grid sets `limited_range=True` and retains its
original wavelengths. Its result describes only that stated range.

### Absolute radiance and irradiance

For the supplied sampled spectral quantity \(P_i\), converted from mW to W
when explicitly declared,

\[
(X,Y,Z)=K_m\sum_i P_i(\bar{x}_i,\bar{y}_i,\bar{z}_i)\Delta\lambda,
\qquad K_m=683.002\ {\rm lm/W}.
\]

This is the rounded standard-air maximum luminous efficacy in BIPM/CIE G1
(2019), §6.2 equation (4). The exact SI value 683 lm/W applies at 540 THz;
it is not an exact coefficient at the peak of the tabulated V(λ). G1 §8.2
equation (14) gives the tristimulus integrals and §9 describes their discrete
sums and linear interpolation. Here both endpoints receive their full step
weight, an inclusive sum rather than trapezoidal integration. The result
retains this convention and its coefficient explicitly.

| Input quantity | Supported units | Derived output |
| --- | --- | --- |
| `spectral_radiance` | `W/(m^2 sr nm)`, `mW/(m^2 sr nm)` | Unit `cd/m^2`, scale `absolute; Y is luminance` |
| `spectral_irradiance` | `W/(m^2 nm)`, `mW/(m^2 nm)` | Unit `lux`, scale `absolute; Y is illuminance` |

Matching a label is not proof of actual physical units, calibration, optical
bandpass or wavelength-medium agreement. Caller establishes these facts from
retained measurement context. An absolute input refuses an extra illuminant;
its measured spectrum already contains the stimulus. Time-integrated exposures,
transmittance, counts and ambiguous unit spellings are outside this API.

The retained MATLAB emission integrator uses a separately documented inherited
convention: it evaluates air-tabulated y-bar at the vacuum wavelength
c/(540 THz), about 555.1712185 nm, yielding approximately 683.016758 before
multiplication by the step. Python uses the explicitly rounded standard-air
coefficient above and linear CMF interpolation. These are intentional different
numerical conventions; equal source spectra do not imply bitwise or metrological
equivalence between those implementations. This does not change MATLAB's owner,
reference evidence or native instrument XYZ.

### Reflectance under an explicit illuminant

Reflectance accepts only quantity `reflectance` and unit `%` or `1`, with
percent divided by 100. Supply an illuminant `Spectrum` on the **exact same
original grid**, with quantity `relative_spectral_power`, unit `1`, nonnegative
values and a positive sample. Supply a nonempty `illuminant_id`; it is a retained
caller identity, not verification that an arbitrary SPD is D50 or D65. No SPD
is inferred from a nominal white chromaticity or an illuminant name.

For factors \(R_i\) and relative illuminant samples \(S_i\),

\[
XYZ=100\frac{\sum_i R_iS_i(\bar{x}_i,\bar{y}_i,\bar{z}_i)}{\sum_i S_i\bar{y}_i},
\qquad
XYZ_{white}=100\frac{\sum_i S_i(\bar{x}_i,\bar{y}_i,\bar{z}_i)}{\sum_i S_i\bar{y}_i}.
\]

These follow Changjun Li, [*CIE TC1-71 Perspective: An Overview on Accurately
Computing Tristimulus Values*](https://library.imaging.org/admin/apis/public/api/ist/website/downloadArticle/cic/16/1/art00005)
(2008), p.22 equations (1)–(3), evaluated on the declared uniform grid.
The common step cancels. Scaling the SPD by its maximum before summation also
cancels and avoids needless overflow; sample factors remain signed and unclipped.
The denominator must have positive photopic weight. No 683 multiplier is used.
Output has unit `1`, scale `relative; perfect-diffuser Y=100`, and the returned
grid-specific diffuser `reference_white`. Use this white for Lab derived from
this XYZ. A full-range nominal white can differ from a truncated-grid white.

Caller establishes that factors describe the intended illumination/collection
geometry and remain applicable under the target SPD. The formula supplies no
fluorescence, gloss/angle model, bandpass correction or uncertainty. A measured
radiance spectrum cannot be relabelled reflectance to remove its original
illumination. Signed factors or factors above one remain numerical data;
acceptance does not assert their physical interpretation.

### Worked example, separate result and proof

```python
from importlib import resources
from color_measurement.models import Spectrum
from color_measurement.science import spectral_xyz, xyz_to_lab

observer_csv = resources.files("color_measurement").joinpath(
    "desktop_assets", "resources", "CIE_xyz_1931_2deg.csv"
).read_bytes()
reflection = Spectrum((555, 556), (50, 50), "reflectance", "%")
illumination = Spectrum((555, 556), (1, 1), "relative_spectral_power", "1")
derived = spectral_xyz(
    reflection, observer="CIE1931_2", observer_csv=observer_csv,
    wavelength_convention="standard-air-nm", illuminant=illumination,
    illuminant_id="explicit two-band synthetic SPD",
)
# Y is 50; XYZ is half this grid's perfect-diffuser white.
lab = xyz_to_lab(derived.values, derived.reference_white)
# Approximately (76.0692610142, 0, 0), relative to the same grid's white.
```

`SpectralXYZ` is frozen and distinct from native `XYZ`/`ReportedValues`. It
retains original spectrum/SPD objects, caller illuminant identity, observer
source/digest, output unit/scale, grid step/range qualification, interpolation,
endpoint convention and coefficient or diffuser white. Retain owning observations
separately for acquisition/calibration/geometry and physical/simulated provenance.
No missing native XYZ is filled and no `project_computed` or saved record is
mutated by this API. A consumer handles `ColorDomainError` as an explicit
unavailable derived result; a legitimate signed or zero calculation remains
numeric. Binary64 rounding, underflow and finite-intermediate refusals limit
extreme calculations, without a universal accuracy guarantee.

The [existing numerical module](../tests/test_science.py) checks independent
555/556 nm rows, fractional linear interpolation, full-table sums supplied by
the official metadata, W/mW scaling, radiance/irradiance distinctions, constant
and mixed signed reflectance, SPD-scale invariance, grid-specific diffuser
white, unchanged input objects and observer/domain/context/overflow refusals.
The full-table sum check uses 1e-8 absolute tolerance; selected moderate scalar
checks use 1e-12 or 2e-14 as stated in the tests. These are binary64 comparison
limits, not optical tolerances. No physical source, native worker, MATLAB runtime
or additional color library is involved.

### Actual implementation and grid/data owners

<!-- source: src/color_measurement/science.py:spectral_xyz -->
```python
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
```

<!-- source: src/color_measurement/science.py:_spectral_grid -->
```python
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
```

<!-- source: src/color_measurement/science.py:_cie1931_matching -->
```python
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
```

Run the numerical and source-excerpt checks from this checkout:

```sh
uv run pytest tests/test_science.py
uv run python tools/check_doc_snippets.py --check docs --require adapt_xyz --require spectral_xyz
```

## Applying calculations to observations

[`processing.compute`](../src/color_measurement/processing.py) computes xyY and,
only when a white is supplied, Lab. It returns a new observation and keeps native
XYZ and original source output separate from derived values. Undefined
chromaticity becomes an unavailable value with a reason, while a valid black Lab
remains numeric `(0,0,0)`. Record serialization preserves those distinctions;
see [data formats](data-format.md).


## Reflectance percent to fraction

[`processing.reflectance_fraction(observation)`](../src/color_measurement/processing.py)
returns a new `Spectrum` with quantity `reflectance` and unit `1` from the
currently supported M1 percent-reflectance record. Both mode fields must be
`reflective_spot_m1`, the spectrum must declare `reflectance` / `%`, and native
configuration must contain type `5`, condition `1` and normalization `100`.
Missing or inconsistent context raises `ValueError`. Normalization alone cannot
identify reflectance; spectral radiance and an already unit-one spectrum are
refused. These checks validate the declared contract, not the authenticity of a
physical acquisition.

The BIPM *SI Brochure*, 9th edition, version 4.01 (June 2026),
[§5.4.7, p. 146](https://www.bipm.org/documents/20126/41483022/SI-Brochure-9.pdf/fcf090b2-04e6-88cc-1149-c3e029ad8232?download=true&t=1780473476314&version=6.1),
defines percent as parts per hundred. For a recorded percent sample \(p_i\)
and validated normalization \(n=100\), the unit-one value is

\[
R_i = \frac{p_i}{n} = \frac{p_i}{100}.
\]

The [native M1 contract](instrument.md) establishes the input
quantity and scale. This unit conversion preserves the supplied wavelengths;
it uses no illuminant, observer, reference white, interpolation or integration.

### Actual implementation: `reflectance_fraction`

<!-- source: src/color_measurement/processing.py:reflectance_fraction -->
```python
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
```

### Worked example and checks

This example constructs the same four-point reflective sentinel as
[`tests/test_reflectance_fraction.py`](../tests/test_reflectance_fraction.py):
402.5–417.5 nm with −0.125, 1.25, 120.25 and 0 percent reflectance. It creates
synthetic M1 metadata and never connects to an instrument.

```python
from dataclasses import replace

from color_measurement.models import Spectrum
from color_measurement.operations import SimulatedDevice
from color_measurement.processing import reflectance_fraction

base = list(SimulatedDevice().measure())[-1].observation
reading = replace(
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
fraction = reflectance_fraction(reading)
print(fraction.unit)        # 1
print(fraction.values[0])   # -0.00125, from -0.125 percent
print(fraction.values[2])   # 1.2025, from 120.25 percent
print(reading.backend_reported.spectrum.unit)  # %, unchanged
```

Analytically, 100%, 50% and 0% become 1, 0.5 and 0.
[`tests/test_reflectance_fraction.py`](../tests/test_reflectance_fraction.py)
checks those exactly representable results on a synthetic 36-band, 380–730 nm
grid, plus signed and above-one samples, unchanged native JSON, and refusal of
missing/inconsistent metadata, quantities, units and modes. Equality for these
selected fixtures checks scale and preservation; it is not a tolerance or
accuracy guarantee for arbitrary spectra. Python binary64 division can round,
and sufficiently small subnormal values can underflow to zero.

The function does not clip negative or above-one values, alter the observation,
or populate `project_computed`. Retain the original observation alongside the
returned spectrum for provenance and calibration context. This narrow API does
not infer units for other modes or integrate XYZ. Flat-spectrum scaling checks
do not establish D50/CIE integration, agreement with Argyll, complete spectral
coverage, optical accuracy or the physical meaning of a signed sample. The
four-point synthetic spectrum and XYZ remain deliberately inconsistent sentinels.


## CR-250 spectral consistency check

The contributed CR-250 driver compares a spectrum with the instrument's XYZ,
radiance and photon radiance from the same capture. This catches gross scale or
wavelength mistakes. A consistent result supports an explicitly inferred density
model for that capture; it does not confirm the manufacturer's spectral unit,
independent calibration or physical accuracy. An inconclusive result refuses the
requested spectral reading and asks the caller to choose XYZ-only explicitly.
The CR-100 is a colorimeter and does not run this spectral check.

### Inputs, quantities and attribution

[`unit_check` in `cr250.c`](../tools/native_argyll/contributions/cr_pr/files/spectro/cr250.c)
implements the comparison policy; ArgyllCMS 3.5.0's `xicc/xspect.c`,
`new_xsp2cie` / `xsp2cie_sconvert`, performs the spectral-to-XYZ calculation.
This is attributed backend integration, not one of the original Python
conversions above. The contribution's [driver reference](../tools/native_argyll/contributions/cr_pr/files/spectro/cr250.txt)
names the manufacturer protocol sections and distinguishes documented units
from the spectrum's assumed interpretation. The primary references are Colorimetry
Research's *Remote Communication* 1.36, §§4.3.1.36–4.3.1.37, and *CRI App Manual*
1.5.10, measurement-data definitions, available from the
[manufacturer](https://www.colorimetryresearch.com/downloads). The remote protocol
defines the spectrum's grid and the radiometric total, but does not specify the
spectrum's density denominator.

Let samples be \(s_i\) at \(\lambda_i=\lambda_0+i\Delta\lambda\) in nm.
The check evaluates the hypothesis that \(s_i\) is W/(m² sr nm), using a factor
of 1000 for Argyll's mW/(m² sr nm) representation with `norm=1`. Only a consistent
verdict may publish that inferred conversion; original native bins remain separate
with an unknown unit. The check compares against
native radiance \(P_n\) in W/(m² sr), photon radiance \(N_n\) in
s⁻¹ m⁻² sr⁻¹, and unclamped \((X_n,Y_n,Z_n)\), with \(Y_n\) in cd/m².
The source's `power`/`spectrum_w` names denote radiance here, not radiant flux
in watts. The device is configured to CMF 0, CIE 1931 2°.

### Actual calculations

The native-grid sum and radiance-weighted mean are

\[
P_s=\Delta\lambda\sum_i s_i,\qquad
\bar\lambda_s=\frac{\sum_i\lambda_i s_i}{\sum_i s_i}.
\]

This is a full-weight rectangular sum, including both endpoint samples, not a
trapezoidal rule. From photon energy \(E=hc/\lambda\), the independently reported
totals imply

\[
\bar\lambda_n=10^9 hc\frac{N_n}{P_n}\quad\text{nm}.
\]

That is the **radiance-weighted** mean wavelength: multiplying photon count by
\(hc\) forms the first moment of radiance. It is not a photon-weighted mean.
The driver uses \(h=6.62607015\times10^{-34}\) J s and
\(c=299792458\) m/s. See CIE S 017:2020,
[17-21-107, photon](https://cie.co.at/eilvterm/17-21-107), and the
[BIPM defining constants](https://www.bipm.org/en/measurement-units/si-defining-constants).
No air/vacuum conversion is performed by this comparison; it is a coarse
wavelength check, not wavelength calibration.

The XYZ comparison uses the usual emissive form
\((X_s,Y_s,Z_s)=K_m\int L_\lambda(\bar x,\bar y,\bar z)\,d\lambda\).
[BIPM/CIE, *Principles Governing Photometry* (2019)](https://www.bipm.org/documents/20126/52718503/G1-2019.pdf/692275b2-049d-5222-35de-c975dd1850fc),
§6.2 equations (3)–(4), §8.2 equation (14), and §9 explain the
photometric scale, tristimulus integrals and numerical summation. The pinned
Argyll implementation uses `0.683002` for mW, corresponding to
\(K_m=683.002\) lm/W, distinct from the exact SI defining \(K_{cd}=683\)
lm/W at 540 THz.

Argyll evaluates at 1 nm steps starting at the spectrum's supplied lower
endpoint while the wavelength is no greater than its upper endpoint. It uses
its bundled CIE 1931 2° tables, without an illuminant multiplier or XYZ
normalization to Y=100. `getval_raw_xspec` uses linear interpolation when the
sample spacing inferred from the two endpoints and count is below 5.01 nm,
and third-order interpolation otherwise. In the application's composed native
source, the third-order helper falls back to linear interpolation below four
samples ([data format](data-format.md)); stock Argyll 3.5.0 has no such
fallback. Thus the power sum uses the header's
step, while XYZ uses the endpoint/count grid; the parser admits an endpoint
residual of at most `1e-8 * max(1, end_nm)` nm ([data format](data-format.md)).
These are the implemented numerical choices,
not exact continuous integrals. No extrapolated missing spectral tails or
optical bandpass correction are added by the driver. Argyll's observer lookup
holds its endpoint values outside the observer table; an accepted wide grid
is not a claim to colorimetric information across that whole range.

This contiguous excerpt is from `unit_check` (including its original comments):

```c
	for (i = 0; i < s->count; i++) {
		sp.spec[i] = 1000.0 * s->value[i];	/* W to mW */
		sum += s->value[i];
		moment += s->value[i] * (s->start_nm + i * s->step_nm);
	}
	spectrum_w = sum * s->step_nm;
	if ((conv = new_xsp2cie(icxIT_none, 0.0, NULL, icxOT_CIE_1931_2, NULL,
	                        icSigXYZData, icxNoClamp)) == NULL)
		return CR250_INTERNAL_ERROR;
	/* Integrate over the band sent, not the observer's wider range. The
	 * driver's publication grid bounds keep this to at most 900 one-nm steps. */
	conv->set_int_steps(conv, 1.0, s->start_nm, s->end_nm);
	conv->convert(conv, xyz, &sp);
	conv->del(conv);
```

### Comparisons and unavailable values

| Comparison | Calculated value | When compared | Accepted interval |
| --- | --- | --- | --- |
| Radiance | \(P_s/P_n\) | Either radiance ≥ 3×10⁻⁴ W/(m² sr) | 0.9 to 1.1, inclusive |
| Wavelength | \(\lvert\bar\lambda_s-\bar\lambda_n\rvert\) | Native radiance ≥ its floor and native photon radiance > 0 | ≤ 5 nm |
| Luminance | \(Y_s/Y_n\) | Either Y ≥ 0.2 cd/m² | 0.9 to 1.1, inclusive |
| Native-normalized X/Z error | \(\max(\lvert X_s-X_n\rvert,\lvert Z_s-Z_n\rvert)/(X_n+Y_n+Z_n)\) | Native Y ≥ 0.2 cd/m² | ≤ 0.05 |

The serialized field `chroma_error` is the last row's diagnostic. It is **not**
a CIE xy/uv distance or a perceptual colour difference. Its implementation
quarters each term before subtraction/summation to avoid overflowing the
normalization on finite large XYZ. These bounds are driver policy for gross
inconsistency; they are not accuracy specifications or uncertainty intervals.

A ratio needs a positive native denominator and a finite quotient. A mean
needs a nonzero spectral sum; the X/Z diagnostic needs a positive native XYZ
sum. Required comparisons without a finite value refuse the reading. Nonfinite
scaled samples, radiance totals or integrated XYZ also refuse it. If no comparison
can discriminate above its floor, the verdict is `inconclusive`. It provides no
positive unit evidence, including for exact zero: multiplying zero by any scale
still gives zero. A previous bright reading cannot qualify a later dim capture.
`computed` and `discriminating` remain separate native masks: in
[retained JSON](data-format.md), an unavailable numeric is `null`, while a
calculated zero remains zero.

### Publication and retained originals

Only `consistent` permits the requested spectral publication. `inconclusive`,
`not_checked` and `mismatch` publish no accepted Argyll patch; the driver does not
silently turn that request into an XYZ-only success. Explicit XYZ-only requests
remain available for valid dark and zero readings, including CR-100. Published
XYZ always remains native `RM XYZ`, not \((X_s,Y_s,Z_s)\).

The admission guard in
[`capture_reading`](../tools/native_argyll/contributions/cr_pr/files/spectro/cr250.c)
follows the same-capture calculation:

```c
		if ((ec = unit_check(p)) != CR250_OK) return map(ec);
		/* The remote manual does not establish the density unit. Only this
		 * capture's discriminating agreement can support our inferred model;
		 * a dark or unchecked spectrum cannot establish its scaling. */
		if (c->unit.verdict != CR250_UNIT_CONSISTENT)
			return map(refuse(p, CR250_SPECTRUM_UNVERIFIED,
			                  "spectral density unit is unverified for this capture; request XYZ-only"));
```

The [support adapter](../tools/native_argyll/cr250_support.c) retains a completely
parsed native header and unscaled bins on success and refusal, with native
`unit=null`. Raw declarations can contain 1–10,000 finite samples; a usable CR
grid needs at least two, while publication remains limited to 4–601 and the
driver's wavelength, step and same-capture consistency requirements. Incomplete
or unparseable bodies remain original bytes rather than fabricated samples.
`inferred_unit` and `unit_basis` record the consistent model even if a later
publication fails. `published_unit` and `published_scale=1000` require a valid
publication whose actual nonzero spectral output count equals that capture's raw
count. `capture.valid` alone is insufficient. Refused native XYZ and usable
spectra can be saved as an ordinary retained observation with unverified units
and a failed terminal result. If finite header values cannot form a valid
wavelength axis, their declaration remains in native facts with an explicit
mapping refusal; independently parsed XYZ can still be saved. No replacement
axis, acquisition retry or successful reading is implied. The
[record format](data-format.md) describes these separate retained forms.

Accepted converted spectra retain the inferred basis in `units_source`, warnings
and derived `input_quantity_basis`; [persistence and exports](data-format.md)
retain the source attribution. Agreement remains internal consistency, not
manufacturer confirmation or independent physical qualification.

### Worked example and verification limits

For four synthetic samples at 550, 551, 552 and 553 nm under the declared
density hypothesis, each
\(s_i=10^{-4}\) W/(m² sr nm), the implemented sum gives
\(P_s=4\times10^{-4}\) W/(m² sr) and \(\bar\lambda_s=551.5\) nm.
Each published sample is 0.1 mW/(m² sr nm). If native radiance equals that
sum and \(N_n=P_s(551.5\times10^{-9})/(hc)\), the radiance ratio is 1 and
wavelength discrepancy is zero, both within their bounds. This checks those
two calculations only; native XYZ must still satisfy the other comparisons.

The real driver [synthetic-transport tests](../tools/native_argyll/contributions/cr_pr/files/spectro/cr250_driver_test.c)
exercise each comparison separately, factors 1000/0.001/2/0.5, a shifted axis,
dim versus bright disagreement, zero/negative denominators, overflow and
retained native facts. Publication controls cover bright-to-dark freshness,
inconclusive nonzero and exact-zero spectra, refused density with retained
originals, and explicit XYZ-only/CR-100 success. Host controls distinguish actual
spectral output from capture validity. An independent approximate physics check
uses a narrow
555 nm line and requires its integrated Y to lie between 98 % and 100 % of
`683.002 * spectrum_w`; that wide bound covers the finite line width, not
instrument accuracy. Other agreement fixtures share the Argyll converter, so
those tests validate wiring and refusal behavior, not an independent XYZ
algorithm. A 1 nm integration grid does not establish 1 nm optical resolution;
truncated spectra, signed noise, spectral units and real-device transport remain
physical-validation limits.

## PR-655/PR-670 native quantities and publication model

The PR native driver publishes Argyll XYZ and spectra only after checking the
reported model, native grid, setup and optical declarations. Its original replies
remain separate from the published values. This is backend unit interpretation,
not a replacement for native XYZ or an application color-calculation kernel.
See the [instrument API](instrument.md#typed-native-settings) and
[retained native records](data-format.md#operation-files-json-and-mat).

### Declared quantities and reference conditions

The primary source is Photo Research's *PR-655/PR-670 User Manual*,
9903-0006-65 Rev. B, 29 July 2009: printed p. 23 gives the XYZ relationship and
4 nm/2 nm increments; p. 113, Table 9, gives quantity/unit codes; p. 116 defines
the M5 radiometric integral as the sum of spectral data times wavelength increment.
Printed pp. 47–49 describe illuminance/irradiance measurements, and p. 119
defines the D116 accessory photometry/radiometry declarations.
The [driver source note](../tools/native_argyll/contributions/cr_pr/files/spectro/pr655.txt)
records the protocol assumptions and remaining limits.

PR-655 retains 101 samples from 380 to 780 nm at 4 nm; PR-670 retains 201 at 2 nm.
D120 must match the model. Sampling increment, detector pixel count and optical
bandwidth are separate facts. No spectrum is resampled. M5 obtains the spectrum;
D2 obtains that exposure's native XYZ. XYZ-only acquisition uses M2.

Publication requires the reported two-degree observer and stable setup across
the capture. The primary and every active add-on must declare the requested
geometry in D116:

| Acquisition | Required photometry/radiometry | Published XYZ unit | Spectral density model |
| --- | --- | --- | --- |
| Emission spot/telephoto | Luminance/Radiance | cd/m² | W/(sr m² nm) |
| `ambient_spot` | Illuminance/Irradiance | lux | W/(m² nm) |

Those configured declarations do not establish which physical optics are fitted,
their placement or calibration. A 10° setup can be retained or explicitly
requested, but cannot supply this driver's 2° publication. It is not silently
reset. Ambient uses the reported Fixed or Adaptive exposure; it does not request
an adaptive/nonadaptive modifier. Emission retains its explicit exposure-mode
requirements described in [native controls](argyll-native-controls.md#photo-research).

For emission, M2/D2 code 0 or luminance code 111 is interpreted with the reported
English/Metric setup; M5 admits codes 0, 11 or 111 under the declared radiance
geometry. Ambient requires explicit M5 irradiance code 12 and M2/D2 illuminance
code 112 or the retained undefined code 0 under its verified geometry and unit
system. Code 0 alone establishes no unit. Opposite quantity codes or incompatible
geometry refuse publication while retaining parsed originals.

For M5, combining the manual's integral definition with the declared geometry
supplies the corresponding **working density model** in the table. The native unit
still remains unknown in the retained facts. A consistent same-capture comparison
can support `inferred_unit`; an inconclusive dim comparison cannot. A dim PR
spectrum can nevertheless be published under the declared manual model, with
that inconclusive verdict retained. The
[CR publication rule](#cr-250-spectral-consistency-check) separately requires a
consistent verdict before spectral publication.

### Actual scale conversion and worked examples

Let \(F\) convert native XYZ to the requested quantity's Argyll scale:

\[
F=\begin{cases}
1 & \text{reported Metric setup},\\
1/(\pi\,0.09290304) & \text{English emission, fL to cd/m²},\\
1/0.09290304 & \text{English ambient, fc to lux}.
\end{cases}
\qquad \mathbf{XYZ}_{\mathrm{published}}=F\,\mathbf{XYZ}_{\mathrm{native}}.
\]

One international foot is exactly 0.3048 m; one foot-lambert corresponds to
\(1/\pi\) cd/ft², and one footcandle is one lumen/ft². The English factors are
approximately 3.426259 cd/m² per fL and 10.763910 lux per fc.
See [NIST SP 811 (2008), §§B.6 and B.8](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication811e2008.pdf).
The implemented binary64 factors are rounded, rather than exact finite decimals.

For a constructed, otherwise admitted emission XYZ-only reply `(0, 1, 0)`, Metric setup
publishes `(0, 1, 0)` cd/m². English setup publishes `(0, F, 0)` cd/m², with
Y approximately 3.426259 cd/m². For the same constructed triple under admitted
ambient geometry, Metric publishes Y=1 lux and English publishes approximately
10.763910 lux. The original triple and unit/setup declarations remain retained.
These are conversion examples, not physical measurements.

For an admitted M5 bin \(s_i\), the published density and radiometric diagnostic are

\[
s_{i,\mathrm{published}}=1000s_i,\qquad
P_{\mathrm{check}}=\Delta\lambda\sum_i s_i.
\]

Under the declared model, a bin value `1e-4` becomes `0.1` mW/(sr m² nm).
Its original value stays `1e-4`, with native unit unknown. A 4 nm increment gives
that bin a `4e-4` W/(sr m²) contribution to the rectangular integral; 2 nm gives
`2e-4`. The increment weights the integral, not the published density samples.
For ambient, the same numeric bin becomes `0.1` mW/(m² nm), contributing `4e-4`
W/m² at a 4 nm increment or `2e-4` at 2 nm. No solid-angle, exposure-time or
cycle-count multiplier is added. Ambient is a separately declared geometry;
these equations do not convert a measured radiance into irradiance.

The following are separate exact extracts from
[`pr655.c:unit_check`](../tools/native_argyll/contributions/cr_pr/files/spectro/pr655.c),
including its factor definitions. They are also present in the composed native
source used by the software checks.

```c
#define FOOT_LAMBERT_TO_CD_M2 (1.0 / (3.14159265358979323846 * 0.09290304))
#define FOOT_CANDLE_TO_LUX (1.0 / 0.09290304)
```

```c
	double scale = p->capture.before.units == 0
	             ? ambient(p) ? FOOT_CANDLE_TO_LUX : FOOT_LAMBERT_TO_CD_M2 : 1.0;
```

```c
	for (i = 0; i < 3; i++) {
		xyz[i] = native->xyz[i] * scale;
		if (!finite_number(xyz[i])) return PR655_UNIT_MISMATCH;
	}
```

```c
	for (i = 0; i < native->count; i++) {
		spectrum->spec[i] = 1000.0 * native->spectrum[i];
		power += step * native->spectrum[i];
		if (!finite_number(spectrum->spec[i]) || !finite_number(power)) return PR655_UNIT_MISMATCH;
	}
```

The photometric diagnostic reuses Argyll's existing CIE 1931 converter on the
native grid, with inclusive rectangular weights and its approximately
683.002 lm/W normalizer. This is consistent with the photopic normalization
explained in [BIPM/CIE, *Principles Governing Photometry*, G1-2019, §6.2,
Eqs. (3)–(4)](https://www.bipm.org/documents/20126/52718503/G1-2019.pdf).
For ambient, the corresponding illuminance integral is
\(E_v=K_m\int E_{e,\lambda}(\lambda)V(\lambda)\,d\lambda\), defined in the same
guide's §2.4. The driver applies the same photopic weighting to the separately
declared irradiance spectrum and compares it with same-exposure native illuminance.
It does not replace D2 XYZ with integrated XYZ. Python checks the C-owned
publication against the actual output and original values, retaining the declared
conversion factor; the driver remains the conversion owner.

### Numerical checks and limits

The [driver cases](../tools/native_argyll/contributions/cr_pr/files/spectro/pr655_driver_test.c)
exercise both models, Metric/English setup, XYZ-only/spectral output, wrong-scale
refusal and dim inconclusive publication for emission and ambient. Ambient cases
also cover Fixed exposure, the unsupported nonadaptive modifier, opposite unit codes
and incompatible primary/add-on geometry or observer. Selected XYZ assertions use an absolute
`1e-10` bound; the selected `1e-4` to `0.1` spectral conversion uses `1e-12`.
These are floating-point software checks. Some expected native Y values use the
same Argyll converter and establish wiring/refusal behavior, not an independent
spectral algorithm or instrument accuracy. The host diagnostic's 0.9–1.1 ratio
range and discrimination floors are separate policy bounds, described in the
[driver reference](../tools/native_argyll/contributions/cr_pr/files/spectro/pr655.txt).

Missing XYZ or spectrum stays missing. A complete M5 spectrum survives a later
D2 or restoration failure; a malformed partial reply stays original bytes,
without an invented complete spectrum. Published values, unknown native units,
model basis, consistency verdict and original signed samples remain distinct.
Overflow refuses publication. Neither this model nor agreement with the same
capture qualifies physical units, calibrated optics, optical bandwidth, stray
light, uncertainty or unmeasured wavelengths. Physical-device behavior is untested.

## Explicit illumination and signed comparison composition

The [illumination reference](illumination.md) documents the implemented full signed
comparison, authenticated CIE D50/D65/A spectra and integrated whites, characterized
measured-reference factor, explicit target-SPD relighting and corresponding-color
swatches. These methods reuse the kernels above and preserve native quantities.
