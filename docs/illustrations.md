# Scientific illustration methods

This document describes the compositions in
[`presentation.py`](../src/color_measurement/presentation.py). They reuse the
[shared scientific definitions](science.md) for matrices, xy/u′v′, Lab, raw RGB
and numerical boundary diagnostics. Demo outcomes are simulations, not physical
measurements. Native quantities and their metadata remain separate from each
illustrative result.

## Sources, attribution and scope

The RGB owner uses [CSS Color 4, 9 October 2026, §§10.2,10.4,19](https://www.w3.org/TR/2026/CRD-css-color-4-20261009/#color-conversion-code)
for primary/D65 matrices and the shared transfer, also checked against
[ICC sRGB interpretation A.8](https://registry.color.org/rgb-registry/files/sRGB.pdf).
Display P3 is distinct from DCI-P3. CIE
[17-23-053](https://cie.co.at/eilvterm/17-23-053),
[17-23-073](https://cie.co.at/eilvterm/17-23-073) and
[17-23-076](https://cie.co.at/eilvterm/17-23-076) define the existing xy/u′v′/Lab
calculations. The numerical band uses Higham (2002), *Accuracy and Stability of Numerical
Algorithms*, second edition, [Lemma 3.1](https://doi.org/10.1137/1.9780898718027)
for γₙ. Goldberg (1991) supplies general rounding/cancellation discussion,
[DOI 10.1145/103162.103163](https://doi.org/10.1145/103162.103163), with a declared
conservative binary64 budget; it is not measurement uncertainty.

The CMF resource is CIE's [Colour-matching functions of CIE 1931 standard
colorimetric observer](https://doi.org/10.25039/CIE.DS.xvudnb9b), 2019.
Its inspected digital metadata names CIE 018:2019 Table 6 as the original source;
this is not a claim to have inspected the full standards. The immutable CSV
contains 471 original 1 nm rows, 360–830 nm. `cie_cmf_rows` authenticates SHA-256
`fa663e3535a7e0763a745993a1f0a192eb0275ac46ad2d1befd7626841e713c1`
and its exact wavelength sequence before reuse.

The original data and **derived** locus, field and wavelength-guide outputs
retain [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) with CIE
attribution, DOI, observer and resource digest. These terms remain attached
when derived data/illustrations are distributed. They do not relabel the
application source code, native observations or imply CIE endorsement.
See [the resource and derived-data notice](../src/color_measurement/desktop_assets/resources/NOTICE.md).

## Measurement previews and boundary diagnostics

`encoded_targets` composes the established encoder for both targets. With
explicit common scale S, c=M⁻¹XYZ/S. It retains signed c, clamps only a separate
display result to [0,1], then applies E(d)=12.92d for d≤0.0031308 or
E(d)=1.055d^(1/2.4)−0.055 otherwise. It neither selects a white nor normalizes
individual readings.

`display_projections` accepts absolute emissive preview context when
observer=CIE1931_2, unit=cd/m², scale=absolute luminance and illuminant=none
(emissive). Qualified relative reflective context uses the common-white model
below. Incompatible or nonrepresentable previews stay absent with a
reason; absence is not zero or black. Native XYZ/calibration metadata is
unchanged. Lab-white declaration is separate from common display scale.

The absolute emissive DTO preserves signed `linear_rgb`, raw
clipping/lower/upper/outside-volume facts and raw `outside_triangle`.
It also supplies owner-generated
`numerical_state`, `numerical_error_bounds`, `max_lower_excess`,
`max_upper_excess`, raw `required_positive_rgb_scale`, conservative
`fit_positive_rgb_scale`, `fit_scale_reason`, and positive-stimulus eligibility.
Both chromaticity views preserve their own availability/reason/numerical state.

The arithmetic band is τᵢ=γ₈Σ|M⁻¹ᵢⱼvⱼ|+8ulp(0), with u=2⁻⁵³ and
γ₈=8u/(1−8u). It concerns converted floats, not instrument uncertainty,
perceptual thresholds or physical-realizability proof. Raw masks remain exact.
An arithmetic-sized excursion is numerically on-boundary; substantive
exclusion is outside. A genuine near-boundary exclusion can also fall in the
band, so the DTO retains the signed value rather than diagnosing its cause.

Excess magnitudes are max(0,−min c) and max(0,max c−1), with unit one. Raw required
positive scale is S·max(0,c); the conservative fit adds the arithmetic margin
and rounds upward. Both have the declared XYZ scale/unit. An explicit host
action may take the maximum supplied fit over compatible checked readings;
no function applies it automatically. Max Y alone is insufficient for a
saturated channel. Negative channels remain negative after such a fit.

Example: XYZ=(20,30,40), S=100 cd/m² gives sRGB red≈−0.01246527 and an outside
state, while the same P3 projection is inside. A synthetic blue channel 1.6
at S=100 requires roughly 160 even when its native Y is below 100.

## Relative reflective measurement previews

`qualify_reflective_xyz` is the common native-context owner for ordinary and
chart previews. It requires an original typed observation from `argyll-native`
or an explicitly simulated `argyll-demo`, with a live or saved reflective mode
recognized by the shared Argyll mode parser. Native XYZ must be finite and
nonnegative, with exact unit `1`, scale `relative; perfect-diffuser Y=100`,
observer `CIE1931_2` and declared XYZ illuminant D50, D65 or A. Missing XYZ,
signed XYZ and incompatible or unknown context remain retained but unavailable
for this illustration. Spectra and computed values cannot replace native XYZ.
Chart admission additionally authenticates reading identity and accepted
acquisition facts through [the chart owner](chart-science.md).

The declared illuminant selects `illumination.illuminant_white`, which integrates
the authenticated original CIE illuminant and observer over 360–830 nm at 1 nm
with common Y=100. Its source data, primary citations, equations and limitations
are in [illumination](illumination.md). This theoretical perfect-diffuser white
is neither a measured calibration tile nor a measurement of the illumination.
It does not change the instrument's XYZ or native chromaticity.

`display_projections` composes the same `corresponding_color` as chart swatches.
Writing W for that theoretical white, D for the RGB owner's nominal D65 white
at Y=1, B for the Bradford matrix and M for the target RGB-to-XYZ matrix:

\[
\mathbf{XYZ}'=B^{-1}\operatorname{diag}\!\left(
\frac{BD}{BW/100}\right)B\mathbf{XYZ},\qquad
\mathbf c=M^{-1}\frac{\mathbf{XYZ}'}{100}.
\]

Whites use the [full Bradford owner](science.md#full-linear-bradford-adaptation)
and its [ICC.1:2004-10 Annex E.3 source](https://www.color.org/ICC1V42.pdf).
The existing RGB encoder supplies signed linear channels, bounded display
channels and clipping flags for sRGB or Display P3. Every sample shares the
same white scale. Native XYZ equal to W illustrates approximately RGB=(1,1,1);
W/2 has linear RGB approximately (0.5,0.5,0.5); native black illustrates
(0,0,0), while its undefined chromaticity stays unavailable.

The relative DTO carries `common_reference_y=100`, `reference_unit="1"`,
`reference_scale`, full theoretical `source_white` metadata and source hashes,
original `native_context`, `destination_white`, separate `adapted_xyz`, signed
`linear_rgb`, bounded `display_rgb` and `clipped_channels`. The algorithm is
`bradford-corresponding-color-display`, version `2`, with the narrow exact-white
rounding rule described in [chart science](chart-science.md#one-common-white-display-model).
It has no `reference_luminance`; `gamut_status` is null because an adapted
illustration does not establish native absolute D65 RGB membership. The absolute
cd/m² preview slider does not alter these relative packets.

Ordinary, saved and older chart-attempt datasets reuse this composition.
The source observations, Y values, coordinates and saved bytes remain intact.
These are conditional ideal-display colors, without spectral relighting,
per-sample brightness normalization, measured-white inference, display-profile
correction, physical appearance prediction or hardware validation.

## Subdued chromaticity illustration recipe

`illustrative_targets` is used **only for illustrations**. For a relative,
dimensionless illustrative XYZ, first compute c=M⁻¹XYZ at relative scale 1.
Let pᵢ=max(cᵢ,0). `_illustration_y_weights` obtains the target's positive primary
Y coefficients wᵢ from the shared RGB geometry at reference scale 1. Their
weighted sum Y₊=Σᵢwᵢpᵢ includes the brightness introduced when negative channels
are clamped. Choose S=max(maxᵢpᵢ,Y₊/0.25), then call the existing encoder at S:

\[
\mathbf c_{\mathrm{illustration}}=M^{-1}\frac{\mathbf{XYZ}}{S},
\qquad S=\max\!\left(\max_i p_i,\frac{\sum_iw_ip_i}{0.25}\right)>0.
\]

In exact arithmetic, S≥maxᵢpᵢ bounds every positive channel by 1, and
S≥Y₊/0.25 bounds the output's ideal relative Y by 0.25. A neutral D65 cue has
approximately linear RGB (0.25,0.25,0.25). Uniform scaling preserves in-gamut
chromaticity before quantization. Outside the target, the negative-channel clamp
remains the same chromaticity approximation at a lower brightness. Using the
original XYZ Y instead of Y₊ would overlook that clamp's brightness contribution.
The cap does not change native observations, acquired amplitude or common
measurement-preview scale. Binary64 arithmetic can cause tiny excursions; the DTO carries
`normalization_scale`, `negative_channels`, `upper_channels` and bounded display
RGB for each target. The cap applies before RGBA8 rounding, browser scaling and
alpha compositing. It is dimensionless ideal relative Y, not physical luminance
or a spectral-appearance model. Fixed raster inputs are bounded; arbitrary
overflowing or underflowing inputs retain the scientific owner's domain limits.

At 550 nm the original CMF XYZ=(0.4334499,0.9949501,0.008749999) produces
encoded sRGB approximately (0,0.6258591225,0), with scale 4.1386499704 and
lower red/blue clamps. Display P3 is approximately (0.2141492884,0.6253442114,0),
with scale 3.9962532644 and a lower blue clamp. These are illustrative target
cues, not physical spectral appearance.

## Wavelength guide

`wavelength_guide` selects 95 original rows every 5 nm, preserving each original
CMF triple and wavelength. It uses the original CMF XYZ at one common relative scale 1 through the shared
RGB encoder. There is no per-wavelength normalization; dim CMF tails remain dim.
The dark-only interface renders this guide at opacity 0.38, restoring its earlier
subdued look. This brightness recipe is distinct from the capped chromaticity
field. The original CMF ȳ and measured spectrum/residual y-axis are unchanged.

The guide encodes wavelength only, never acquired intensity or residual sign
or magnitude. Browser stop interpolation is presentation styling, not new
scientific samples or physical spectral-color reproduction. The guide's
observer, DOI, resource hash and ShareAlike terms remain in the returned DTO.
Native measurement previews continue to use one explicit common display scale.

## Full locus, line of purples and shared color field

`spectral_locus` retains all 471 original monochromatic triples through the
existing xy/u′v′ owners. Coordinates have unit one; wavelength has unit nm. The
last-to-first closing segment is the line of purples, whose interior is not
monochromatic light. This is CIE 1931 2°, not an observer-independent locus,
a measured display gamut or a physical-eligibility validator.

`chromaticity_field()` supplies a cached DTO with separate 384×384 regular
rasters for xy and u′v′. Each extent encloses the original locus with a 0.002
coordinate margin. Row zero is the top: a pixel center at column c, row r is

\[
p_x=x_0+\frac{c+1/2}{384}(x_1-x_0),\qquad
p_y=y_1-\frac{r+1/2}{384}(y_1-y_0).
\]

The full 471-point locus, closed by the line of purples, supplies even-odd
scanline intervals. Outside pixels have transparent RGBA (0,0,0,0). Inside
pixel centers use the existing inverse at illustrative Y=1: xy composes
`xyy_to_xyz`; u′v′ composes the shared `uvy_to_xyz`, whose inverse equations are
X=9u′Y/(4v′), Z=(12−3u′−20v′)Y/(4v′), from CIE's defining equations cited above.
Both views then call `illustrative_targets`. They evaluate the same scientific
recipe at their own coordinates; they do not interpolate colors from an xy
triangle after the projective change of coordinates.

Each encoded channel rounds to the nearest integer in [0,255], with alpha 255
for admitted centers. Quantization error is at most 0.5/255 per encoded channel.
The browser creates `ImageData` and Canvas2D in the explicitly selected encoded
color space, verifies context attributes, and puts those Python bytes unchanged.
Unavailable P3 canvas output uses the supplied sRGB raster with a visible
fallback label. CSS P3 syntax support is checked separately. The Python locus
mask keeps outside pixels transparent, and the browser clips the canvas to the
plot frame. RGB comparison triangles stay independent.

Browser scaling interpolates the finite pixels for smooth viewing. Pixel-center
sampling, finite resolution, boundary antialiasing, alpha compositing and RGBA8
quantization are illustration limits. Expanded views do not add scientific
samples. No instrument spectrum is resampled, and no measured brightness,
coverage or physical monochromatic appearance is inferred. The spectrum and
residual wavelength guide remains common-scale CMF colors at opacity 0.38;
this field renders its separately capped illustration colors without additional opacity.

## Reference-white declaration

`resolve_white` admits a current structured declaration or explicit null. A
retained-reading reference preserves source identity and native XYZ plus unit,
scale, observer and illuminant. A theoretical D50/D65 reference uses the shared
frozen catalogue and requires explicit reference Y, units, scale and observer.
It creates no native observation or spectral samples. Nominal xy comes from
pinned CSS Color 4; this four-decimal convention differs from ICC PCS D50.
The core scientific documentation describes the sourced definitions, equations
and limitations. The chooser labels standards as assumptions and displays the
reading context before declaration; no standard is chosen automatically.

`lab_projections` composes the existing `xyz_to_lab`, with matching unit/scale/
observer and, for retained references, matching native illuminant. An assumed
standard has no inferred native illuminant. This computes direct Lab under a
declared assumption; it does not adapt XYZ or relight spectra. Native data and
preview scale remain unchanged. White kind/identity/Y/context are included in
requests, caches and reply identity. For example, assumed D65 at Y=100 cd/m²
has XYZ≈(95.0455927,100,108.9057751); changing reference Y changes Lab, while the
120 cd/m² measurement preview stays unchanged. Missing context or unsupported
observer is refused with a reason rather than a fallback white.

## 3D references and independent optional components

`reference_projections` requests the existing ideal XYZ cube shell with nine
samples per edge: 386 points and 768 triangles. It retains 12 sampled cube-edge
curves (96 segments) instead of a dense face lattice. Its per-vertex colors come
from `encoded_targets` at the explicit common model scale. These are ideal
additive D65/zero-black references, not measured device coverage or ICC volumes.
Lab composes the existing owner under the complete declared white. The model
requires CIE1931_2, absolute cd/m²; an incompatible white leaves model Lab absent
with a reason while preserving healthy XYZ and preview components. The optional
Lab surface is direct Lab under that white, without chromatic adaptation.

When the session supplies a native dataset, model XYZ also requires matching
absolute emissive cd/m², scale, CIE1931_2 observer and illumination. Incompatible
data leave model XYZ/Lab components unavailable with explicit reasons; valid
native Lab and spectra are separate. Dimensionless xy/u′v′ geometry retains its
CIE1931_2/D65 model assumptions and is unavailable for a different or missing
native observer. Standalone illustrations explicitly omit the dataset argument.

Offline Three.js 0.186.1 supplies orthographic projection, orbiting, equal world
unit scales, neutral axes/ticks and nearest visible marker picking. XYZ Y and
Lab L* are vertical. Default references use sparse neutral wireframes; an
optional translucent, see-through surface uses Python vertex previews. Its
interpolated colors are approximate illustrations. Markers decode their encoded
Python preview once with an explicit source space into the renderer's matching
linear working space. Materials are unlit and use NoToneMapping. Actual WebGL
output color-space readback distinguishes P3 from an explicit sRGB fallback.
Neutral circles identify inspection; diamonds identify numerical reference
volume exclusion. Hover gives retained numerical coordinates. The readings
table remains the keyboard and hidden/coincident-point selection route.

The dependency's offline [MIT notice](../src/color_measurement/desktop_assets/vendor/LICENSE)
is included in the package. The two addon imports are changed only from bare `three` to
`./three.module.js`. No CDN or runtime network dependency is introduced. Three's
float32 geometry/color attributes, approximate shader coefficients, finite shell
sampling and output compositing do not qualify physical display accuracy or
scientific figure export. Full dataset identity and coordinates remain Python
values; plot distance is not ΔE00. No CMF failure can suppress independent model
XYZ/Lab components. Renderer availability does not qualify a native host.

## Checked code and numerical tests

The following excerpts are extracted byte-for-byte from
[`presentation.py`](../src/color_measurement/presentation.py).
[RGB tests](../tests/test_science_rgb.py) check the shared conversion,
boundary-band and fit definitions;
[nominal-white tests](../tests/test_science_standard_white.py) check the explicit
reference context and constructor invariants. These are software checks without
a display or instrument.
[Presentation tests](../tests/test_presentation.py) check D50/D65/A white,
half-white and black controls, context refusals, chart/common equivalence and
unchanged absolute scale behavior. [Dataset tests](../tests/test_reading_dataset.py)
exercise actual Demo ordinary and chart operations, saved reopening and retained
older attempts, preserving source bytes and native Y.

### encoded_targets

<!-- source: src/color_measurement/presentation.py:encoded_targets -->
```python
def encoded_targets(xyz, scale):
    """Use the established encoder; rendering colors do not become observations."""
    return {
        target: xyz_to_rgb(xyz, color_space=target, reference_luminance=scale).display_rgb
        for target in TARGETS
    }
```

### qualify_reflective_xyz

<!-- source: src/color_measurement/presentation.py:qualify_reflective_xyz -->
```python
def qualify_reflective_xyz(observation, *, observer_csv=None):
    """Qualify original relative reflective XYZ and its declared theoretical white.

    Reuses the Argyll mode grammar and illumination.illuminant_white (CIE 1 nm
    SPD/CMF sum, 360..830 nm, common Y=100). No sample normalization or source
    white guessing occurs. Native units/scale/observer/illuminant must declare
    unit 1, perfect-diffuser Y=100, CIE1931_2 and D50/D65/A respectively.
    Chart callers separately authenticate accepted context and reading identity;
    ordinary dataset callers supply their authenticated original observations.

    Example: a retained black (0,0,0) is available without invented chromaticity;
    a missing XYZ is unavailable even if computed values or spectra exist.
    This validates an illustrative model's input, never calibration, physical
    appearance, placement or native D65 measurements. Scientific equations and
    source attribution remain with the illumination and corresponding-color owners.
    """
    result = dict(available=False, reason="", xyz=None, context=None)
    if not isinstance(observation, Observation):
        result["reason"] = "Relative preview requires an original color observation"
        return result
    native = observation.backend_reported.xyz
    if native is None:
        result["reason"] = "Native XYZ is unavailable; no replacement is inferred"
        return result
    try:
        reflective = route_illumination(parse_mode_name(observation.actual_mode)[0]) in {
            "reflection", "s_reflection",
        }
    except ValueError:
        reflective = False
    if (observation.device.backend not in {"argyll-native", "argyll-demo"} or not reflective
            or observation.device.backend == "argyll-demo"
            and observation.provenance != "simulated"):
        result["reason"] = "Relative previews require retained Argyll reflective acquisition"
    elif (native.unit != "1" or native.scale != RELATIVE_SCALE
          or native.observer != "CIE1931_2" or native.illuminant not in {"D50", "D65", "A"}):
        result["reason"] = "Relative preview needs CIE1931_2 unit-one Y=100 XYZ under D50, D65 or A"
    elif any(value < 0 for value in native.values):
        result["reason"] = "Relative reflective XYZ must be finite and nonnegative"
    else:
        try:
            white = _reflective_white(
                native.illuminant, RESOURCE.read_bytes() if observer_csv is None else observer_csv,
            )
            result.update(available=True, xyz=list(native.values), context=dict(
                unit=native.unit, scale=native.scale, observer=native.observer,
                illuminant=native.illuminant, white=asdict(white),
            ))
        except (OSError, ValueError, OverflowError, TypeError) as error:
            result["reason"] = f"Relative reflective calculation unavailable: {error}"
    return result
```

### display_projections

<!-- source: src/color_measurement/presentation.py:display_projections -->
```python
def display_projections(readings, scale):
    previews = {}
    for key, row in readings.items():
        native = row["observation"]["backend_reported"]["xyz"]
        eligible = (
            native is not None
            and native["observer"] == "CIE1931_2"
            and native["unit"] == "cd/m^2"
            and native["scale"] == "absolute; Y is luminance"
            and native["illuminant"] == "none (emissive)"
        )
        relative = None
        if not eligible and native is not None:
            try:
                relative = qualify_reflective_xyz(observation_from_dict(row["observation"]))
            except (TypeError, ValueError, OverflowError) as error:
                relative = dict(available=False, reason=str(error))
        previews[key] = {}
        for target in TARGETS:
            if relative is not None and relative["available"]:
                context = relative["context"]
                try:
                    encoding = corresponding_color(
                        relative["xyz"], reference_white=context["white"]["xyz"],
                        observer=context["observer"], color_space=target,
                    )
                except (TypeError, ValueError, OverflowError) as error:
                    previews[key][target] = dict(display_rgb=None, gamut_status=None,
                                                 reason=str(error))
                    continue
                previews[key][target] = dict(
                    linear_rgb=encoding.linear_rgb, display_rgb=encoding.display_rgb,
                    clipped_channels=encoding.clipped_channels,
                    common_reference_y=encoding.common_reference_y, reference_unit=context["unit"],
                    reference_scale=context["scale"], algorithm=encoding.algorithm,
                    algorithm_version=encoding.algorithm_version,
                    mapping="full Bradford to nominal D65; common relative white Y=100; "
                            "display clamp",
                    source_white=context["white"],
                    native_context={name: value for name, value in context.items()
                                    if name != "white"},
                    destination_white=encoding.destination_white, adapted_xyz=encoding.adapted_xyz,
                    gamut_status=None,
                )
                continue
            if not eligible:
                previews[key][target] = {
                    "display_rgb": None,
                    "gamut_status": None,
                    "reason": "Native XYZ is unavailable." if native is None else
                    relative["reason"] if relative is not None else
                    "Preview requires qualified native XYZ context.",
                }
                continue
            try:
                status = rgb_gamut_status(
                    native["values"], color_space=target, reference_luminance=scale
                )
            except (TypeError, ValueError, OverflowError) as error:
                previews[key][target] = {
                    "display_rgb": None,
                    "gamut_status": None,
                    "reason": str(error),
                }
                continue
            encoding = status.encoding
            coordinates = {"xyy": status.xy, "uvy": status.uv}
            previews[key][target] = {
                "linear_rgb": encoding.linear_rgb,
                "display_rgb": encoding.display_rgb,
                "clipped_channels": encoding.clipped_channels,
                "reference_luminance": scale,
                "reference_unit": "cd/m^2",
                "algorithm": status.algorithm,
                "algorithm_version": status.algorithm_version,
                "mapping": "per-channel clamp; no chromatic adaptation or tone mapping",
                "gamut_status": {
                    "below_zero": [i for i, flag in enumerate(status.below_zero_channels) if flag],
                    "above_one": [i for i, flag in enumerate(status.above_one_channels) if flag],
                    "outside_volume": not status.within_rgb_cube,
                    "numerical_state": status.numerical_state,
                    "numerical_error_bounds": status.numerical_error_bounds,
                    "max_lower_excess": status.max_lower_excess,
                    "max_upper_excess": status.max_upper_excess,
                    "required_positive_rgb_scale": status.required_positive_rgb_scale,
                    "fit_positive_rgb_scale": status.fit_positive_rgb_scale,
                    "fit_scale_reason": status.fit_scale_reason,
                    "positive_stimulus_eligible": status.positive_stimulus_eligible,
                    "chromaticity": {
                        name: {
                            "coordinates": value.coordinates,
                            "outside_triangle": None
                            if value.in_triangle is None
                            else not value.in_triangle,
                            "reason": value.unavailable_reason,
                            "numerical_state": value.numerical_state,
                            "numerical_error_bounds": value.numerical_error_bounds,
                        }
                        for name, value in coordinates.items()
                    },
                }
                if eligible
                else None,
            }
    return previews
```

### cie_cmf_rows

<!-- source: src/color_measurement/presentation.py:cie_cmf_rows -->
```python
@lru_cache(maxsize=1)
def cie_cmf_rows():
    """Authenticate the immutable published CIE 1931 2° / 1 nm resource once."""
    raw = RESOURCE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != CMF_SHA256:
        raise ValueError("The official CIE CMF resource bytes changed.")
    rows = [tuple(float(value) for value in row) for row in csv.reader(raw.decode().splitlines())]
    if len(rows) != 471 or any(len(row) != 4 or row[0] != 360 + i for i, row in enumerate(rows)):
        raise ValueError("Require the published 360–830 nm / 1 nm table.")
    return tuple(rows)
```

### illustrative_targets

<!-- source: src/color_measurement/presentation.py:illustrative_targets -->
```python
def illustrative_targets(xyz):
    """Dim only the chromaticity illustration to relative output Y <= 0.25.

    c=M^-1*XYZ, p=max(c,0), Y+=sum(primary_Y*p), S=max(max(p),Y+/0.25).
    Reuse the shared XYZ encoder at S. Positive-channel Y includes the effect
    of negative-channel clamping; using original XYZ Y would miss that effect.
    Signed channels remain signed until the display output clamps them. This
    changes illustrative brightness, never a native reading or physical spectral
    appearance. The cap is ideal relative Y before RGBA8 quantization and
    browser scaling, with binary64 roundoff; it is not a physical luminance.
    Fixed raster inputs remain finite; existing numerical domain limits apply.
    CSS Color 4 defines the matrices/transfer; CIE's CMFs define
    the wavelength/chromaticity inputs. No private color kernel is introduced.
    """
    result = {}
    for target in TARGETS:
        raw = xyz_to_rgb(xyz, color_space=target, reference_luminance=1.0)
        positive = tuple(max(0.0, channel) for channel in raw.linear_rgb)
        luminance = math.fsum(
            weight * channel for weight, channel in zip(_illustration_y_weights(target), positive)
        )
        scale = max(*positive, luminance / 0.25)
        if scale == 0.0:
            raise ValueError("An illustration requires a positive RGB channel.")
        encoded = xyz_to_rgb(xyz, color_space=target, reference_luminance=scale)
        result[target] = {
            "display_rgb": encoded.display_rgb,
            "normalization_scale": scale,
            "negative_channels": tuple(
                i for i, value in enumerate(encoded.linear_rgb) if value < 0
            ),
            "upper_channels": tuple(i for i, value in enumerate(encoded.linear_rgb) if value > 1),
        }
    return result
```

### wavelength_guide

<!-- source: src/color_measurement/presentation.py:wavelength_guide -->
```python
@lru_cache(maxsize=1)
def wavelength_guide():
    """Label wavelengths with common-scale CMF colors, not measured intensity."""
    samples = []
    for row in cie_cmf_rows()[::5]:
        targets = {
            target: xyz_to_rgb(row[1:], color_space=target, reference_luminance=1.0)
            for target in TARGETS
        }
        samples.append(
            {
                "wavelength_nm": row[0],
                "xyz": row[1:],
                "colors": {target: value.display_rgb for target, value in targets.items()},
                "negative_channels": {
                    target: tuple(i for i, channel in enumerate(value.linear_rgb) if channel < 0)
                    for target, value in targets.items()
                },
                "upper_channels": {
                    target: tuple(i for i, channel in enumerate(value.linear_rgb) if channel > 1)
                    for target, value in targets.items()
                },
            }
        )
    return {
        "observer": "CIE1931_2",
        "source_doi": "10.25039/CIE.DS.xvudnb9b",
        "resource_sha256": CMF_SHA256,
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "unit": "relative XYZ / dimensionless CMF",
        "reference_scale": 1.0,
        "recipe": (
            "XYZ=(x_bar,y_bar,z_bar); one common relative scale 1; "
            "signed channel clamp; no per-band normalization"
        ),
        "samples": samples,
        "scope": (
            "Wavelength identification only; not measured amplitude, "
            "physical spectral appearance or a per-reading scale"
        ),
    }
```

### spectral_locus

<!-- source: src/color_measurement/presentation.py:spectral_locus -->
```python
@lru_cache(maxsize=1)
def spectral_locus():
    """Project original 1 nm CMF triples with the existing coordinate owners."""
    samples = [
        {"wavelength_nm": row[0], "xyy": xyz_to_xyy(row[1:])[:2], "uvy": xyz_to_uvy(row[1:])[:2]}
        for row in cie_cmf_rows()
    ]
    return {
        "observer": "CIE1931_2",
        "source_doi": "10.25039/CIE.DS.xvudnb9b",
        "resource_sha256": CMF_SHA256,
        "license": "CC BY-SA 4.0",
        "samples": samples,
        "wavelength_range_nm": (360, 830),
        "step_nm": 1,
        "purple_boundary_indices": (0, len(samples) - 1),
        "coordinate_unit": "1",
        "scope": (
            "Published monochromatic reference chromaticities; "
            "not a display gamut or measured coverage"
        ),
    }
```

### chromaticity_field

<!-- source: src/color_measurement/presentation.py:chromaticity_field -->
```python
@lru_cache(maxsize=1)
def chromaticity_field():
    """Evaluate a regular pixel-center field with the existing Python owners.

    Each view inverts its own dimensionless coordinates at illustrative Y=1.
    The complete original locus, closed with the purple line, supplies even-odd
    scanline intervals. Outside pixels are transparent. Each admitted point
    uses illustrative_targets; encoded channels round to RGBA8. Canvas scaling
    and quantization remain presentation approximations, not native data,
    physical spectral appearance, luminance or measured coverage.
    """
    size = 384
    locus = spectral_locus()["samples"]
    views = {}
    for view, inverse in (("xyy", xyy_to_xyz), ("uvy", uvy_to_xyz)):
        boundary = tuple(sample[view] for sample in locus)
        # A small margin keeps the authenticated edge within the raster.
        x0 = min(p[0] for p in boundary) - 0.002
        x1 = max(p[0] for p in boundary) + 0.002
        y0 = min(p[1] for p in boundary) - 0.002
        y1 = max(p[1] for p in boundary) + 0.002
        buffers = {target: bytearray(size * size * 4) for target in TARGETS}
        valid_pixels = 0
        for row in range(size):
            y = y1 - (row + 0.5) / size * (y1 - y0)
            intersections = []
            for a, b in zip(boundary, (*boundary[1:], boundary[0])):
                if (a[1] > y) != (b[1] > y):
                    intersections.append(a[0] + (y - a[1]) / (b[1] - a[1]) * (b[0] - a[0]))
            intersections.sort()
            if len(intersections) % 2:
                raise ValueError("Locus scanline intersections must form closed intervals.")
            for col in range(size):
                x = x0 + (col + 0.5) / size * (x1 - x0)
                if not any(a <= x < b for a, b in zip(intersections[::2], intersections[1::2])):
                    continue
                xyz = inverse((x, y, 1.0))
                targets = illustrative_targets(xyz)
                offset = (row * size + col) * 4
                for target, value in targets.items():
                    channels = bytes(round(c * 255) for c in value["display_rgb"])
                    buffers[target][offset : offset + 4] = channels + b"\xff"
                valid_pixels += 1
        views[view] = {
            "width": size,
            "height": size,
            "extent": (x0, x1, y0, y1),
            "row_order": "top-to-bottom; y decreases",
            "valid_pixels": valid_pixels,
            "rgba8": {
                target: base64.b64encode(raw).decode("ascii") for target, raw in buffers.items()
            },
        }
    return {
        "views": views,
        "observer": "CIE1931_2",
        "source_doi": "10.25039/CIE.DS.xvudnb9b",
        "resource_sha256": CMF_SHA256,
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "coordinate_unit": "1",
        "authoritative_boundary_step_nm": 1,
        "relative_y_cap": 0.25,
        "recipe": ("per-pixel Python coordinate inverse; relative output Y cap 0.25 "
                   "after negative-channel clamp; encoded RGBA8; full locus clip"),
        "scope": ("Approximate subdued chromaticity illustration, not physical spectral "
                  "appearance, measured coverage or per-reading normalization"),
    }
```

### resolve_white

<!-- source: src/color_measurement/presentation.py:resolve_white -->
```python
def resolve_white(readings, request):
    """Resolve a current declaration without manufacturing native observations.

    Retained references preserve the native four-field context. Theoretical
    references use the shared nominal catalogue with explicit Y/unit/scale/
    observer. Their direct Lab assumption does not infer an illuminant, adapt
    native XYZ or integrate an SPD. No declaration is selected by default.
    """
    if request is None:
        return None
    if not isinstance(request, dict):
        raise ValueError("White declaration must be an object or null.")
    if request.get("kind") == "retained-reading":
        if set(request) != {"kind", "source_id"} or request["source_id"] not in readings:
            raise ValueError("Choose one retained white identity.")
        source_id = request["source_id"]
        native = readings[source_id]["observation"]["backend_reported"]["xyz"]
        if native is None:
            raise ValueError("Declared white source has no native XYZ.")
        xyz = tuple(native["values"])
        if any(v <= 0 for v in xyz):
            raise ValueError("Declared white must have positive XYZ.")
        return {
            "kind": "retained-reading",
            "source_id": source_id,
            "xyz": xyz,
            "reference_y": xyz[1],
            **{k: native[k] for k in ("unit", "scale", "observer", "illuminant")},
        }
    if request.get("kind") == "theoretical-lab-reference":
        fields = {"kind", "name", "observer", "reference_y", "unit", "scale"}
        if set(request) != fields:
            raise ValueError("Standard white needs explicit name, observer, Y, unit and scale.")
        return asdict(standard_reference_white(**{k: request[k] for k in fields - {"kind"}}))
    raise ValueError("Unsupported white kind.")
```

### lab_projections

<!-- source: src/color_measurement/presentation.py:lab_projections -->
```python
def lab_projections(readings, white):
    """Compose Lab only for matching declaration context; keep absence explicit."""
    values, reasons = {}, {}
    if white is None:
        return values, reasons
    keys = ("unit", "scale", "observer")
    if white["kind"] == "retained-reading":
        keys += ("illuminant",)
    for key, row in readings.items():
        native = row["observation"]["backend_reported"]["xyz"]
        if native is None:
            reasons[key] = "Native XYZ is unavailable."
            continue
        if any(native[k] != white[k] for k in keys):
            reasons[key] = "Reading and declared-white units/scale/observer/conditions differ."
            continue
        try:
            values[key] = xyz_to_lab(native["values"], white["xyz"])
        except (TypeError, ValueError, OverflowError) as error:
            reasons[key] = str(error)
    return values, reasons
```

### reference_projections

<!-- source: src/color_measurement/presentation.py:reference_projections -->
```python
def reference_projections(scale, white=None, *, readings=None):
    """Keep ideal components and their native-context eligibility independent.

    With no readings argument this is a standalone reference illustration.
    A bound dataset never borrows the model's absolute cd/m² scale or observer.
    """
    contexts = []
    if readings is not None:
        for row in readings.values():
            native = row["observation"]["backend_reported"]["xyz"]
            if native is not None:
                context = {key: native[key] for key in ("unit", "scale", "observer", "illuminant")}
                if context not in contexts:
                    contexts.append(context)
    xyz_reason, chromaticity_reason = None, None
    if readings:
        if not contexts:
            xyz_reason = "Native XYZ is unavailable for comparison with the reference model."
            chromaticity_reason = "No native XYZ observer is available for reference comparison."
        else:
            if any(context != dict(
                unit="cd/m^2", scale="absolute; Y is luminance",
                observer="CIE1931_2", illuminant="none (emissive)",
            ) for context in contexts):
                xyz_reason = (
                    "Native XYZ context does not match the absolute emissive CIE1931_2 RGB model."
                )
            if any(context["observer"] != "CIE1931_2" for context in contexts):
                chromaticity_reason = (
                    "Native observer does not match the CIE1931_2 reference model."
                )
    result = {}
    for target in TARGETS:
        reasons = {}
        if xyz_reason is not None:
            reasons["xyz"] = xyz_reason
        if chromaticity_reason is not None:
            reasons["chromaticity"] = chromaticity_reason
        try:
            geometry = rgb_reference_geometry(
                color_space=target,
                reference_luminance=scale,
                reference_white=None,
                samples_per_edge=9,
            )
        except (TypeError, ValueError, OverflowError) as error:
            result[target] = {
                "name": TARGET_NAMES[target],
                "primaries": None,
                "xyz_points": None,
                "lab_points": None,
                "vertex_colors": {},
                "lines": [],
                "triangles": [],
                "component_reasons": {"geometry": str(error)},
            }
            continue
        xyz = [point.xyz for point in geometry.surface]
        lab = None
        if white is not None and xyz_reason is not None:
            reasons["lab"] = xyz_reason
        elif white is not None:
            compatible = (
                white["observer"] == "CIE1931_2"
                and white["unit"] == "cd/m^2"
                and white["scale"] == "absolute; Y is luminance"
            )
            if not compatible:
                reasons["lab"] = "Declared white and RGB model units/scale/observer differ."
            else:
                try:
                    lab = [xyz_to_lab(point, white["xyz"]) for point in xyz]
                except (TypeError, ValueError, OverflowError) as error:
                    reasons["lab"] = str(error)
        vertex_colors = {}
        try:
            encoded = [encoded_targets(point, scale) for point in xyz]
            vertex_colors = {
                encoding: [value[encoding] for value in encoded] for encoding in TARGETS
            }
        except (TypeError, ValueError, OverflowError) as error:
            reasons["surface_colors"] = str(error)
        result[target] = {
            "name": TARGET_NAMES[target],
            "observer": "CIE1931_2",
            "white": "D65 target",
            "reference_luminance": scale,
            "reference_unit": "cd/m^2",
            "reference_scale": "absolute; Y is luminance",
            "reference_illuminant": "none (emissive)",
            "native_contexts": contexts,
            "black": "zero",
            "primaries": {"xyy": geometry.primaries_xy, "uvy": geometry.primaries_uv}
            if chromaticity_reason is None else None,
            "white_point": {"xyy": geometry.white_xy, "uvy": geometry.white_uv}
            if chromaticity_reason is None else None,
            "xyz_points": xyz if xyz_reason is None else None,
            "lab_points": lab,
            "lines": [
                edge
                for edge in geometry.lines
                if sum(
                    geometry.surface[edge[0]].linear_rgb[axis]
                    == geometry.surface[edge[1]].linear_rgb[axis]
                    and geometry.surface[edge[0]].linear_rgb[axis] in (0.0, 1.0)
                    for axis in range(3)
                )
                == 2
            ],
            "triangles": geometry.triangles,
            "vertex_colors": vertex_colors,
            "component_reasons": reasons,
            "algorithm": geometry.algorithm,
            "algorithm_version": geometry.algorithm_version,
            "samples_per_edge": geometry.samples_per_edge,
            "convention": "Ideal additive RGB reference; not measured device coverage, ICC or HDR",
        }
    return result
```


### _illustration_y_weights

<!-- source: src/color_measurement/presentation.py:_illustration_y_weights -->
```python
@lru_cache(maxsize=2)
def _illustration_y_weights(target):
    """Reuse the shared CSS target's primary Y coefficients at unit scale."""
    geometry = rgb_reference_geometry(
        color_space=target, reference_luminance=1.0, reference_white=None, samples_per_edge=2
    )
    return tuple(primary[1] for primary in geometry.primaries_xyz)
```
