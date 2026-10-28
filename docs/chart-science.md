# Chart colors and declared placement advisories

`chart_science` consumes original immutable observations selected by the application.
It performs no acquisition, native-worker operation, saved-source access or hardware calls.
The application authenticates the exact source bytes, request, terminal operation,
selected attempt and source owner before providing a scientific source. Native
sample identity and canonical cell binding remain separate. See the
[chart state](chart-state.md), [TI2 target](data-format.md#import-a-ti2-target),
[illumination and comparison](illumination.md) and [scientific methods](science.md).

The [source-bound context adapter](chart-context.md) keeps requested, acknowledged
and readback facts separate before these scientific checks.

## Original sources and native quantities

A selected source is exactly `{cell_index, observation, reading_ref,
accepted_context}`. `observation` is the original typed `Observation`.
`reading_ref` retains `segment_id`, `operation_id`, `observation_id` and
`sample_id`; the last three must equal the original observation. `cell_index`
is the one-based canonical grid index, not the sample ID. For example, a native
sample named `native-002` can bind canonical cell B1. A TI2 source can call that
cell `007` at `01A`; neither spelling replaces the native identity.

`qualify_chart_reading` delegates native qualification to the shared
`presentation.qualify_reflective_xyz` used by ordinary dataset previews. It
admits native finite nonnegative XYZ with unit `1`,
scale `relative; perfect-diffuser Y=100`, observer `CIE1931_2` and illuminant
D50, D65 or A. It uses the shared Argyll mode parser for live and saved reflection,
including spot, strip, X-Y and chart routes, with the native or explicitly
simulated Demo backend. It requires accepted established facts `xyz_unit`,
`xyz_scale`, `observer`, `illuminant` and `actual_mode` to equal the original
observation, and `provenance.observation_provenance` to match. Unknown or requested
native facts cannot qualify. Native measurement condition, when supplied among
material facts, remains distinct from the XYZ reference illuminant: an M0/M1/M2/M3
or D65 acquisition condition does not change an original D50 XYZ declaration.

Missing XYZ stays unavailable even when a spectrum or project-computed Lab exists.
Black is valid native XYZ and has Lab=(0,0,0), without inventing chromaticity.
Signed original XYZ is retained in the source but unavailable for this chart
illustration. Absolute radiance/luminance, transmission and other observers
are never relabelled as reflective relative XYZ. Accepted context and complete
original observation survive separately from each derived result.

## One common-white display model

`chart_reading_colors` uses the shared qualifier's authenticated original CIE
D50/D65/A SPD and CIE 1931 two-degree observer through
`illumination.illuminant_white`. This gives
one full-range 360–830 nm inclusive 1 nm theoretical white at Y=100. It computes
Lab with the established `science.xyz_to_lab` and then calls
`presentation.corresponding_color` for full Bradford adaptation to the RGB
owner's nominal D65 and common-scale sRGB or Display P3 encoding. Every cell
uses the same source-white Y; brightness is never normalized per reading.
Signed linear RGB, bounded display RGB, clipping flags, source/destination
whites and original native XYZ remain distinct. White illustrates approximately
RGB=(1,1,1), black illustrates (0,0,0); a darker neutral stays darker.
The corresponding-color packet uses algorithm version 2. Only when the validated
sample XYZ equals its declared source white exactly, its clipping advisory
suppresses positive upper excursions of at most 16 binary64 ulps at 1, because
the mathematical white endpoint is (1,1,1). Adapted XYZ, signed linear channels
and bounded display RGB remain exactly as computed. Larger upper excursions,
negative channels and every unequal sample retain strict clipping flags.
The shared direct `science.xyz_to_rgb` encoder and gamut status remain strict,
without an epsilon. This narrow numerical bound supplies no physical gamut or
measurement-uncertainty tolerance.

The equations, primary sources, sampled-white conventions and actual kernel
excerpts are in [illumination](illumination.md) and [science](science.md).
These swatches are conditional ideal-display illustrations. Unknown specimen
geometry does not invalidate the native XYZ model, but cannot establish a
measured reference or placement advisory. No display calibration, profile,
physical appearance, fluorescence, observer replacement or bandpass correction
is inferred.

`chart_expected_colors` reuses `chart_targets.expected_reference_json` for
original authenticated TI2 XYZ or explicit-white Lab interpretation. It maps
each canonical cell to the exact source row, ID and location. Both confirmed
TI2 axis choices are supported. The producer spelling `CIE1931_2deg` denotes
exactly the internal `CIE1931_2` fixed two-degree definition. No other observer
is translated. Explicit E/custom expected whites remain their own declared
models; measured chart qualification still requires D50/D65/A. Negative expected
XYZ remains original and unavailable for chart display. Expected target values
and printer device channels never become measurements or position references.

## Theoretical chart white and native driver normalization

The declared chart Lab white is a theoretical perfect diffuser integrated from
the authenticated CIE illuminant and observer tables over 360–830 nm at 1 nm,
normalized to Y=100. It is not a retained measurement of the instrument's white
tile, an ICC media white, or a replacement for native XYZ.

The pinned [ArgyllCMS 3.5.0 source](https://www.argyllcms.com/Argyll_V3.5.0_src.zip)
explains the related driver convention. In `xicc/xspect.c`, D50 has 107 samples
from 300–830 nm at 5 nm (lines 244–246), while the CIE 1931 two-degree observer
has 471 samples from 360–830 nm at 1 nm (lines 832–835). `getval_xspec` divides
interpolated values by the spectrum's declared norm (lines 4380–4383).
For nonemissive conversion, `xsp2cie` accumulates illuminant × observer × sample
and normalizes by the illuminant × Y-observer sum (lines 5983–5991 and 6000–6004).
Its default integration uses 1 nm steps over the observer's bounds
(lines 6116–6118). Thus an ideal unit reflector has Y=1 before driver scaling.

The i1Pro 3 nonemissive path in `spectro/i1pro3_imp.c` selects D50 and the
CIE 1931 two-degree observer (lines 4790–4793), retains percentage spectra with
norm=100, converts through that owner and multiplies XYZ by 100
(lines 4855–4865). This establishes that path's perfect-diffuser Y=100 convention.
The inspected source archive SHA-256 is
`f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f`;
`xicc/xspect.c` is `ec494fe4a87f3498c95692b2339824d952beebe2fbf67f2a8c7d92051dbab0c5`
and `spectro/i1pro3_imp.c` is
`42c684afa3bbb15dbbb880820c55a17f32d79ceaa7f8cbcf07cddaf3f2073e32`.

Shared perfect-diffuser Y=100 does not establish identical X/Z whites, sampled
illuminants, interpolation, spectral integration or calibration across drivers.
The Python chart model declares its own full-range theoretical white and keeps
every original native XYZ unchanged. It does not substitute an ICC white,
renormalize a driver's XYZ, infer matching measurement conditions, or claim
instrument accuracy from this source inspection.

## Frozen measured-reference policy

`prepare_position_reference` requires an explicit qualification object with
`specimen_id`, `edition`, `geometry`, `backing`, `lighting`, `declarer` and
`matched_conditions_confirmed=true`. It also requires criteria
`maximum_delta_e`, `reference_bound`, `measurement_bound`, `basis` and `coverage`.
Each numeric criterion is a finite nonnegative scalar or one value per canonical
cell. Scalars expand to N entries; a nonfinite combined budget is refused.
There are no default uncertainty limits or specimen assumptions.

Each source remains paired with its canonical mapping, original observation,
reading identities and accepted context. Missing cells remain holes. Unavailable
sources remain retained; any eligible measured coverage qualifies only those
cells. Required scientific material facts must be established. Established
specimen/edition/geometry/backing/lighting facts must match the explicit reference
qualification. Device route/model/serial/backend/origin, workflow ownership and
unused spectral grid/units are retained provenance, without becoming native-XYZ
colorimetric equality requirements. Binding device_id, driver and native_identity
facts are likewise excluded only when their values match the original observation
or its retained native-facts identity/driver fields. Different binding/driver/firmware
identities and unreported driver identity do not veto otherwise qualified Lab
comparison. Identity-shaped labels without that original source attribution remain
required material conditions; optical/filter/effective-correction/calibration/geometry
facts retain their existing comparison fences. Unknown serial alone does not invalidate a
chart color or reference. Other required acquisition facts, such as optical mode,
native measurement condition, filters or geometry, remain comparison conditions.

The packet's SHA256 binds exact mapping, sources, criteria, qualification and
computed projection. `validate_position_reference` reconstructs it from original
observations and current owners; editing and rehashing convenience XYZ/Lab does
not authenticate those projections. The caller separately retains its exact
source-byte, run, pass and segment ownership proofs. The packet neither replaces
those proofs nor makes a mutable old source writable.

## CIE76 and explicit limits

[CIE S 017:2020 17-23-076](https://cie.co.at/eilvterm/17-23-076) defines CIELAB;
[17-23-077](https://cie.co.at/eilvterm/17-23-077) defines CIE 1976 color difference.
For original sample Lab and its frozen measured reference Lab under the same
white and quantity context, the implementation uses

\[
d=\sqrt{(L_s^*-L_r^*)^2+(a_s^*-a_r^*)^2+(b_s^*-b_r^*)^2},\qquad
T_i=A_i+R_i+M_i,\qquad \mathrm{warn}\iff d>T_i.
\]

Distances and limits are dimensionless CIELAB vector distances. `A` is the
application allowance, `R` and `M` are declared reference and measurement
same-space vector envelopes. The Euclidean triangle inequality motivates their
sum; the code does not estimate instrument uncertainty, establish the stated
coverage, or infer a universal perceptual tolerance. Equality does not warn.
For Lab reference=(50,3,4), sample=(50,6,8), the independent Euclidean distance
is sqrt(3²+4²)=5. Allowance2 plus reference bound1 and measurement bound1 gives
T=4, so the check warns. Black versus its theoretical white has exact computed
d=100; an explicit total100 does not warn.

`chart_position_check` authenticates the frozen science packet and requires
compatible established material facts, provenance, white, illuminant, observer,
units and relative scale. It computes CIE76 before RGB or clipping. Missing
reference cells, missing native XYZ and incompatible or uncertain conditions
return explicit unavailable results while the caller retains the observation.
The warning packet includes its original reading identities, canonical cell
and exact frozen reference digest for the state owner's decision token.

For compatible reference cells i and j, their declared regions overlap when
reference separation <= Ti+Tj. The check retains nearest compatible separation
and nearest overlapping cell. If a sum overflows, any finite separation is still
inside that sum; it cannot be claimed separated. A nonfinite separation remains
unestablished. Overlap makes alternative positions ambiguous. Separated regions
still provide no independent physical-location evidence: a within-limit result
never verifies placement, and an alternative reference never identifies a cell.

## Numerical proof and limits

The registered target tests construct original synthetic observations and
independent cube-branch Lab vectors, check both TI2 orientations and original
source IDs/locations, missing and signed quantities, accepted-fact refusals,
unknown identity versus unknown material, projection/digest tampering, explicit
limit refusals, holes, duplicate native selections, nearest overlap and equality.
Binary64 tolerances bound arithmetic differences only. This software proof
establishes no physical measurement, instrument calibration, actual UI integration,
native worker or displayed-color accuracy.

## Checked implementation excerpts

<!-- source: src/color_measurement/chart_science.py:qualify_chart_reading -->
```python
def qualify_chart_reading(observation, *, reading_ref, accepted_context, observer_csv):
    """Retain native XYZ only when original identity and reflective facts agree.

    Units must be one, scale perfect-diffuser Y=100, observer CIE1931_2 and
    illuminant D50/D65/A. Require finite nonnegative XYZ from an Argyll native
    or explicitly simulated Demo reflective mode. No radiance relabelling,
    zero replacement, computed-XYZ substitution or sample normalization occurs.
    The theoretical common white reuses illumination.illuminant_white: CIE
    original 1 nm SPD/CMF sum on 360..830 nm, at Y=100. See its primary sources.

    Example: native XYZ=(0,0,0) is available with Lab=(0,0,0); missing native
    XYZ is unavailable even if project-computed Lab exists. Accepted facts
    must match the actual observation; requested/unknown facts cannot qualify.
    Application caller authenticates bytes, source ownership and selected
    attempt beforehand. Neither those checks nor this function prove physical
    specimen, illumination, calibration, geometry or placement.
    """
    if not isinstance(observation, Observation):
        raise ValueError("Chart source requires an original Observation")
    context = validate_context(accepted_context)
    if (
        not isinstance(reading_ref, dict)
        or set(reading_ref) != {"segment_id", "operation_id", "observation_id", "sample_id"}
        or any(
            not isinstance(value, str) or not value or "\0" in value
            for value in reading_ref.values()
        )
    ):
        raise ValueError("Chart source requires the exact four reading identities")
    result = dict(available=False, reason="", xyz=None, context=None, lab=None)
    reason = None
    if any(
        reading_ref[key] != getattr(observation, key)
        for key in ("operation_id", "observation_id", "sample_id")
    ):
        reason = "Original observation identity differs from the selected reading"
    elif context["phase"] != "accepted":
        reason = "Chart colors require accepted acquisition facts"
    native = observation.backend_reported.xyz
    if not reason and native is None:
        reason = "Native XYZ is unavailable; no replacement is inferred"
    if not reason:
        facts = dict(
            xyz_unit=native.unit,
            xyz_scale=native.scale,
            observer=native.observer,
            illuminant=native.illuminant,
            actual_mode=observation.actual_mode,
        )
        for key, expected in facts.items():
            fact = context["material"].get(key)
            if not _known(fact) or _json(fact["value"]) != _json(expected):
                reason = f"Accepted {key} is missing, unestablished or differs from native data"
                break
        if context["provenance"].get("observation_provenance") != observation.provenance:
            reason = "Accepted provenance differs from the original observation"
    if not reason:
        qualified = qualify_reflective_xyz(observation, observer_csv=observer_csv)
        reason = qualified["reason"] or None
    if not reason:
        try:
            lab = xyz_to_lab(native.values, qualified["context"]["white"]["xyz"])
            result.update(
                available=True,
                xyz=list(native.values),
                lab=list(lab),
                context=qualified["context"],
            )
        except (ColorDomainError, ValueError, OverflowError, TypeError) as error:
            reason = f"Relative chart calculation unavailable: {error}"
    result["reason"] = reason or ""
    return _plain(result)
```

<!-- source: src/color_measurement/chart_science.py:chart_reading_colors -->
```python
def chart_reading_colors(wrapper, *, observer_csv, color_space="srgb"):
    """Return a qualified native chart swatch under the shared corresponding model.

    Calls qualify_chart_reading then presentation.corresponding_color, retaining
    original native XYZ/Lab/white, signed linear RGB and display clipping separately.
    All cells use common white Y=100, never each sample's own luminance.
    Example: black produces RGB=(0,0,0), white approximately (1,1,1).
    Output is an ideal-display illustration, not a measured display appearance.
    Existing Bradford and RGB scientific owners retain equations and citations.
    """
    source = _source(wrapper, MAX_PATCHES)
    values = qualify_chart_reading(
        wrapper["observation"],
        reading_ref=source["reading_ref"],
        accepted_context=source["accepted_context"],
        observer_csv=observer_csv,
    )
    return dict(
        values, cell_index=source["cell_index"], color=_color(values, color_space), source=source
    )
```

<!-- source: src/color_measurement/chart_science.py:chart_expected_colors -->
```python
def chart_expected_colors(definition, context_json, *, color_space="srgb"):
    """Compose authenticated TI2 expected values with exact canonical mapping.

    Reuses chart_targets.expected_reference_json, its strict retained-source
    validation and established Lab inverse. Source row/ID/location must match
    the canonical cell, independently of a native reading's sample ID.
    CIE1931_2deg producer spelling denotes exactly Python's CIE1931_2, the same
    fixed two-degree definition; no other observer is mapped. An explicit common
    white is retained, including declared E/custom expected contexts. Negative
    expected XYZ stays original but is unavailable for nonnegative chart display.
    Example: expected Lab=(100,0,0) illustrates the declared white as white.
    No expected row becomes a measurement or qualifies a position warning.
    The existing inverse/Bradford/RGB owners retain equations and primary sources.
    """
    definition = validate_definition(definition)
    if definition["kind"] != "ti2":
        return dict(
            available=False,
            reason="No authenticated TI2 expected values",
            definition_id=definition["id"],
            context=None,
            cells=[],
        )
    expected = json.loads(expected_reference_json(definition["target_json"], context_json))
    rows = {row["source_row"]: row for row in expected["cells"]}
    cells = []
    for cell in definition["cells"]:
        row = rows[cell["source_row"]]
        if (row["sample_id"], row["sample_loc"]) != (cell["source_id"], cell["source_location"]):
            raise ValueError("Expected row identity differs from canonical source mapping")
        context = expected["context"]
        reason = row["reason"]
        if row["available"] and any(value < 0 for value in row["XYZ"]):
            reason = "Expected XYZ is retained but negative chart display is unavailable"
        values = dict(
            available=not reason,
            reason=reason,
            xyz=row["XYZ"],
            context=(
                dict(observer="CIE1931_2", white=dict(xyz=context["whiteXYZ"]))
                if row["available"]
                else None
            ),
        )
        cells.append(dict(cell, expected=row, color=_color(values, color_space)))
    return dict(
        available=any(cell["color"]["available"] for cell in cells),
        reason="",
        definition_id=definition["id"],
        source_sha256=expected["source_sha256"],
        context=expected["context"],
        cells=cells,
    )
```

<!-- source: src/color_measurement/chart_science.py:prepare_position_reference -->
```python
def prepare_position_reference(definition, selected, *, qualification, criteria, observer_csv):
    """Freeze explicit policy and selected original measured sources, one per cell.

    CIE S 017:2020 17-23-076/077 define Lab and Euclidean DeltaE*ab:
    https://cie.co.at/eilvterm/17-23-076 and https://cie.co.at/eilvterm/17-23-077.
    Criteria are nonnegative scalar or N-cell Lab-distance declarations, not
    sensor uncertainty estimates. This factory preserves holes and unavailable
    originals; any eligible coverage qualifies only those cells. The SHA256
    digest binds exact canonical mapping, sources, computed values and policy.
    Caller retains separately authenticated source-byte/run/pass ownership.

    Example: allowance=2, reference_bound=1, measurement_bound=1 yields budget4.
    Qualification explicitly names specimen, edition, geometry, backing, lighting,
    declarer and matched conditions. Those declarations are not optical proof.
    No illustration-only target, editable XYZ or computed Lab replaces native data.
    No acquisition, user-source file access, inferred position or default limit occurs.
    """
    definition = validate_definition(definition)
    count = len(definition["cells"])
    qualification, criteria = _policy(qualification, criteria, count)
    if not isinstance(selected, (list, tuple)) or len(selected) > count:
        raise ValueError("Select at most one original measured reading per canonical cell")
    cells = [
        dict(
            mapping=cell,
            source=None,
            values=dict(
                available=False,
                reason="No selected measured reference",
                xyz=None,
                context=None,
                lab=None,
            ),
        )
        for cell in definition["cells"]
    ]
    seen, readings = set(), set()
    for wrapper in selected:
        if wrapper is None:
            continue
        source = _source(wrapper, count)
        index = source["cell_index"]
        if index in seen:
            raise ValueError("Duplicate selected reference cell")
        reading_identity = tuple(
            source["reading_ref"][key] for key in ("operation_id", "observation_id")
        )
        if reading_identity in readings:
            raise ValueError("One original reading cannot be selected for multiple reference cells")
        seen.add(index)
        readings.add(reading_identity)
        values = qualify_chart_reading(
            wrapper["observation"],
            reading_ref=source["reading_ref"],
            accepted_context=source["accepted_context"],
            observer_csv=observer_csv,
        )
        if values["available"]:
            context = source["accepted_context"]
            reason = None
            if not _material_compatible(source, source):
                reason = "Required measured-reference material conditions are unestablished"
            for key in ("specimen_id", "edition", "geometry", "backing", "lighting"):
                fact = context["material"].get(key)
                if _known(fact) and _json(fact["value"]) != _json(qualification[key]):
                    reason = f"Measured reference {key} differs from the matched qualification"
                    break
            if reason:
                values = dict(available=False, reason=reason, xyz=None, context=None, lab=None)
        cells[index - 1].update(source=source, values=values)
    coverage = sum(cell["values"]["available"] for cell in cells)
    packet = dict(
        format="chart-position-reference",
        version=1,
        definition_id=definition["id"],
        qualified=coverage > 0,
        coverage=coverage,
        total=count,
        cells=cells,
        qualification=qualification,
        criteria=criteria,
        algorithm="CIE76-declared-Lab-envelope",
        algorithm_version="1",
    )
    return dict(packet, digest=_digest(packet))
```

<!-- source: src/color_measurement/chart_science.py:validate_position_reference -->
```python
def validate_position_reference(reference, definition, *, observer_csv):
    """Rebuild a frozen scientific reference from its retained originals and policy."""
    reference = _plain(reference)
    if not isinstance(reference, dict) or not isinstance(reference.get("cells"), list):
        raise ValueError("Invalid measured chart reference")
    sources = []
    try:
        for cell in reference["cells"]:
            if cell["source"] is not None:
                source = cell["source"]
                sources.append(
                    dict(source, observation=observation_from_dict(source["observation"]))
                )
        rebuilt = prepare_position_reference(
            definition,
            sources,
            qualification=reference["qualification"],
            criteria=reference["criteria"],
            observer_csv=observer_csv,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Measured chart reference cannot be reconstructed: {error}") from error
    if _json(reference) != _json(rebuilt):
        raise ValueError("Measured chart reference source, mapping, policy or projection changed")
    return rebuilt
```

<!-- source: src/color_measurement/chart_science.py:chart_position_check -->
```python
def chart_position_check(reference, definition, wrapper, *, observer_csv):
    """Compare original Lab to a frozen measured cell using declared CIE76 limits.

    d=hypot(deltaL*,deltaa*,deltab*), T=allowance+reference_bound+measurement_bound.
    Warn only if d>T; equality does not warn. The triangle inequality supports
    addition of declared same-space Lab-vector envelopes, without establishing
    their coverage or measurement uncertainty. CIE S 017:2020 17-23-077:
    https://cie.co.at/eilvterm/17-23-077. Compute before RGB or clipping.

    Example: Lab50 to Lab54 gives d4; a declared total4 does not warn, total3 does.
    Require established matching material and native white/quantity context.
    Device model/serial/backend identity is retained, not a colorimetric equality
    law. Compatible reference regions overlap when separation<=Ti+Tj; preserve
    nearest overlapping peer and numerical refusal. Even separated regions do
    not identify an alternative cell or verify physical placement. Missing,
    unqualified or mismatched sources remain unavailable with original data intact.
    """
    definition = validate_definition(definition)
    source = _source(wrapper, len(definition["cells"]))
    result = dict(
        available=False,
        warning=False,
        reference_digest=None,
        cell_index=source["cell_index"],
        reading_ref=source["reading_ref"],
        reason="No qualified measured reference",
        distance=None,
        limit=None,
        nearest_reference_separation=None,
        overlapping_cell=None,
        alternative_reason="Physical placement is not verified",
    )
    if reference is None:
        return result
    reference = validate_position_reference(reference, definition, observer_csv=observer_csv)
    result["reference_digest"] = reference["digest"]
    index = source["cell_index"] - 1
    cell = reference["cells"][index]
    values = qualify_chart_reading(
        wrapper["observation"],
        reading_ref=source["reading_ref"],
        accepted_context=source["accepted_context"],
        observer_csv=observer_csv,
    )
    if not cell["values"]["available"] or not values["available"]:
        result["reason"] = (
            cell["values"]["reason"] if not cell["values"]["available"] else values["reason"]
        )
        return result
    if cell["values"]["context"] != values["context"] or not _material_compatible(
        cell["source"], source
    ):
        result["reason"] = "Reference and reading conditions are mismatched or unestablished"
        return result
    budgets = [
        sum(
            reference["criteria"][key][i]
            for key in ("maximum_delta_e", "reference_bound", "measurement_bound")
        )
        for i in range(reference["total"])
    ]
    try:
        distance = _distance(values["lab"], cell["values"]["lab"])
        nearest, overlap, overlap_distance, unavailable, compatible = None, None, None, False, 0
        for i, peer in enumerate(reference["cells"]):
            if (
                i == index
                or not peer["values"]["available"]
                or peer["values"]["context"] != cell["values"]["context"]
                or not _material_compatible(peer["source"], cell["source"])
            ):
                continue
            compatible += 1
            try:
                separation = _distance(peer["values"]["lab"], cell["values"]["lab"])
            except ColorDomainError:
                unavailable = True
                continue
            nearest = separation if nearest is None else min(nearest, separation)
            if separation <= budgets[index] + budgets[i]:
                if overlap_distance is None or separation < overlap_distance:
                    overlap, overlap_distance = i + 1, separation
        if overlap is not None:
            alternative = "Declared reference regions overlap; alternative positions are ambiguous"
        elif unavailable:
            alternative = "Reference separation exceeds finite range; separation is unestablished"
        elif compatible == 0:
            alternative = "No other reference has compatible established conditions"
        else:
            alternative = (
                "Declared reference regions are separated; physical position is unverified"
            )
        warning = distance > budgets[index]
        result.update(
            available=True,
            warning=warning,
            distance=distance,
            limit=budgets[index],
            nearest_reference_separation=nearest,
            overlapping_cell=overlap,
            alternative_reason=alternative,
            reason=(
                "Reading exceeds the declared total; check placement"
                if warning
                else "Within the declared total; physical position is not verified"
            ),
        )
    except ColorDomainError as error:
        result["reason"] = f"Position check unavailable: {error}"
    return result
```

