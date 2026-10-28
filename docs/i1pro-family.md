# i1Pro family: modes and measurement workflows

Reference checked 2026-09-18 against X-Rite literature and ArgyllCMS 3.5.0 source.
This is a device/backend reference, not a declaration that this package supports
every listed operation. See the [current API boundary](#current-package-boundary).

Argyll support is checked against its per-device documentation first:
[original i1Pro][a-original], [i1Pro 2][a-two] and [i1Pro 3/Plus][a-three], then
the relevant [spotread][a-spotread]/[chartread][a-chartread] instructions and
versioned driver source. Manufacturer manuals establish physical capabilities
and setup, not which functions Argyll exposes. A source capability bit alone
does not override an upstream documented limitation.

## In plain language

The i1Pro generations share a basic sequence: identify the instrument, select a
supported measurement mode, satisfy its calibration instructions, position the
correct accessory and sample, then acquire. Their available conditions and
physical setup differ. Measuring reflected colour, a display, room illumination
and transmission does not produce the same quantity or use the same reference.
Manufacturer capability, Argyll implementation and wrapper support are three
separate facts. This reference adds no physical validation.

## Generation differences

M0/M1/M2/M3 below are **reflective measurement conditions**, not display types or
choices of illuminant for a later XYZ calculation. M1 controls the measurement
illumination toward D50; M2 excludes UV; M3 adds polarization to UV-excluded
measurement. X-Rite's OBC software feature is not another hardware mode.

| Instrument | Manufacturer workflow | Argyll 3.5.0 distinction |
| --- | --- | --- |
| Original Eye-One Pro / i1Pro | Separate unfiltered M0 and fixed UV-cut M2 versions; filters are not interchangeable. Spot and strip workflows. | The `i1pro` driver reports the actual fixed filter. Do not infer selectable M1/M3 or standards conformity from the family name alone. |
| i1Pro 2 / Rev E | M0/M1/M2. Strip M0 uses one pass; native M1/M2 use tungsten and UV-LED passes with the positioning ruler. This does not imply two manual triggers for every spot reading. | Upstream documents native UV measurement as unsupported. Its hidden `-YU` test path and `inst_mode_ref_uv` bit do **not** establish a supported native M1/M2 workflow. |
| i1Pro 3 | LED illumination; M0/M1/M2 from a single scan. Standard aperture specified as 4.5 mm. | Reflective spot/strip support M0, M1 and M2 through the `i1pro3` driver. |
| i1Pro 3 Plus | Larger 8 mm aperture; M0/M1/M2 and optional polarized M3. | Same driver family, but M3 requires the polarization capability, correct adapter and polarized calibration. Standard i1Pro 3 is not a Plus. |

Manufacturer sources: [original/2 comparison, L7-518][x-original],
[i1Pro 2 manual, pp. 16–17][x-two],
[2/3/Plus comparison, L7-713][x-compare] and
[i1Pro 3 specification, L7-718][x-three-spec]. Backend qualifications:
[Argyll instrument guide][a-instruments] and source locations below.

Basic, Photo and Publish names also describe software bundles. An i1Profiler
license or bundle name is not a complete hardware capability description.
OEM variants can omit capabilities: Argyll documents restricted i1Pro 2/EFI
variants with UV-cut-only operation and missing ambient/UV-LED features.
Inspect the actual identity and capabilities rather than copying retail defaults.
[X-Rite family overview][x-family]; [Argyll instrument guide][a-instruments].

Argyll's paper-reference FWA compensation is distinct from the i1Pro 3 driver's
condition processing. It estimates fluorescence effects using a paper-white
measurement; it must not be labelled native M1 acquisition. In the i1Pro 3 driver,
M0/M1/M2 spectra are computed from normal/UV submeasurements and stored calibration
factors, not three interchangeable physical filters.
[FWA documentation][a-fwa]; `i1pro3_comp_refl_value` below.

## Workflows and quantities

The following are upstream driver paths, subject to the actual unit's
capabilities and mode validation. They are not all exposed by this package.
The units describe Argyll's instrument result contract (`xicc/xspect.h`), not every
possible normalized CLI display/export.

| Workflow | Setup and calibration distinction | Result meaning |
| --- | --- | --- |
| Reflective spot / strip | Instrument illumination; matched white tile. Strip measurement additionally needs suitable patches, backing, scan geometry and start/end margins. Plus M3 has its own polarizer setup. | Relative spectral reflectance factor; Argyll spectra conventionally use percent. XYZ depends on the processing illuminant/observer. |
| Adaptive emissive spot | Display holder or appropriate viewing geometry; required dark and, where supported, wavelength calibration. The instrument selects integration time. | Spectral radiance, mW/(m² sr nm); Y is luminance, cd/m². |
| Nonadaptive emissive spot | Separate integration-time selection/calibration against an emissive white condition in addition to required dark/wavelength calibration. | Same radiance/luminance quantities; not a reflective white-tile measurement. |
| Emissive strip | Driver has a separate scan path; not merely repeated adaptive spot requests. | A sequence of radiance measurements. |
| Steady ambient | Supported unit plus its matched cosine diffuser/ambient head, positioned for the intended illumination geometry. | Spectral irradiance, mW/(m² nm); illuminance, lux. Not display luminance. |
| Ambient flash | Separate supported flash mode and ambient head; includes temporal integration. | Spectral radiant exposure, mW s/(m² nm); luminous exposure, lux s. Not steady lux. |
| Transmission spot / strip | Argyll's manually illuminated light-table path needs dark and unobstructed-source white references. The reflective tile alone cannot establish transmission. | Relative spectral transmittance, conventionally percent. |

`i1pro_convert_mode` and `i1pro3_convert_mode` implement these distinctions.
Ambient/flash are conditional, not guaranteed for every original or OEM unit.
The upstream “telephoto” mode maps to emissive spot; it does not prove that the
instrument has separate telephoto optics. X-Rite's projector-holder instructions
point toward the projected **screen**, a different setup from measuring room
illumination with a diffuser. [i1Pro 3 manual, pp. 24–31][x-three].

X-Rite advertises transmission hand scanning and i1iO automation for the Plus in
its family comparison. That manufacturer workflow is not the same as Argyll's
manual external-light-source transmission path for older instruments. Likewise,
an instrument's strip capability does not establish control of an i1iO robot.
[Family comparison][x-compare]; [Argyll chartread guide][a-chartread].

Argyll also documents display refresh-rate and update-delay measurements for
i1Pro 3, zebra-ruler-assisted scanning and repeated ruler passes for more samples
per patch. The drivers expose programmatic and operator/button triggers and
scan-tolerance controls; `chartread -T` changes patch-consistency tolerance.
These controls are separate from measurement quantity and reflective condition.
A timing result must not be presented as another spectrum. See
[the i1Pro 3 entry][a-three], [strip operation][a-instruments] and the capability
functions in the source table.

## Calibration and accessories

- Match the calibration plate's serial number to the instrument. For i1Pro 3,
  open the tile's protective slider fully and seat the meter firmly. A closed
  black cover is not the white calibration reference. Keep the tile clean and
  protect it when unused. [i1Pro 3 manual, pp. 14–15][x-three].
- The manufacturer ambient procedure calibrates on the base **before** fitting
  the matched head. The Plus manual explicitly excludes M3 for ambient work.
  [i1Pro 3 manual, pp. 26–27][x-three];
  [Plus manual, pp. 26–27][x-plus].
- Follow Argyll's returned calibration type and setup condition. A tile request
  can be for wavelength calibration during an emissive workflow, not a switch
  to reflectance. Dark, wavelength, reflective-white, transmission-white and
  integration-time calibrations are not interchangeable. The shared
  [calibration lifetime](instrument.md#calibration-lifetime) section explains
  when live reads negotiate calibration and what a driver can retain between
  operations; there is no per-family instruction set.
- Original/i1Pro 2 reflection exposes dark and white calibration; the i1Pro 3
  reflective path also performs dark correction within acquisition. Polarized
  reflection has a separate calibration path. Retained calibration validity
  depends on mode, timestamps, temperature and driver state. `-N` is not a
  blanket calibration bypass: invalid required calibration still needs action.
  The shared native worker starts fresh for every operation, and a reading
  selects that upstream option (`losecs` 0, as `-N`), so a calibration the
  driver restored and still judges valid serves without the tile. Standalone
  calibration does not request that waiver; it negotiates the driver's needed
  work, which depends on the selected mode. A substituted cache-valid test is not proof of
  that physical behavior.

The last two points are source-derived; see `*_imp_get_n_a_cals`,
`*_imp_calibrate` and `i1pro3_check_calib` below. This reference does not prescribe
one unconditional “calibrate every hour” rule for the entire family.
The current API does not expose native calibration dates, board temperature or
an expiry timestamp from which a client could derive such a timer.
See [initial-calibration suppression](instrument.md#upstream-initial-calibration-suppression)
for the distinction between a last-open age bound, an accepted option and actual
calibration readiness, including the different SpectroScan behavior.

## Identification, spectra and source discrepancies

USB identity alone does not uniquely resolve the retail generation/variant.
Argyll recognizes original/i1Pro 2 under `0971:2000`, initially distinguishing
Rev E by endpoint information and refining that through initialization.
i1Pro 3/Plus share `0765:6009`: the Plus designation comes from EEPROM device
type, while ambient/polarization capability bits are separate. Enumeration and
initialized identity are different stages; initialization can affect hardware
state. See [description API restrictions](argyll-description.md).

For standard i1Pro 3, X-Rite distinguishes 3.5 nm physical sampling, 10 nm optical
resolution and a 380–730 nm reporting grid at 10 nm intervals. These are not
synonyms. Argyll's optional finer-grid processing does not prove finer optical
resolution; the i1Pro 3 driver advertises high-resolution mode only outside
reflection. Request finer-grid processing with `highres=True` in the shared
[configuration](instrument.md#configuration). The upstream i1Pro 3 implementation
defines a 106-sample, 380–730 nm grid (`i1pro3_imp.c:178–180,6866–6869` in the
[pinned source][a-source]) for the seven nonreflective modes, as
[testing](testing.md) pins; there is no separate i1Pro 3 API, and standard
sampling remains the default. Preserve returned wavelengths, scaling and quantity rather than
assigning a grid solely from the model name. [L7-718 specification][x-three-spec].

There is an unresolved manufacturer-document discrepancy: the inspected i1Pro 3
manual copy, pp. 38–39, lists a **5 mm** aperture and **16 × 16 mm** ruler-scan
patches. L7-718 and L7-713 instead specify **4.5 mm** and **7 × 7 mm** with the
sensor ruler; L7-718 separately gives **10 × 7 mm** without it. The reference
uses the model-specific sheet's aperture, but these conflicting manual numbers
must not silently become acquisition limits. The reason for the discrepancy has
not been established. [Inspected manual copy][x-three-mirror];
[L7-718][x-three-spec]; [L7-713][x-compare].

Upstream documentation also needs version checks: the live `spotread` filter
synopsis omits `5`, whereas 3.5.0 accepts `-F5` for D50/M1. Its generic ambient
fallback prose conflicts with 3.5.0's unsupported-ambient refusal. Consult the
actual backend/version and returned measurement type, not a generic menu label.
The historical [i1Pro driver comparison][a-old-comparison] describes Argyll 1.2.0;
its numerical comparisons do not validate current i1Pro 2/3/Plus operation.
[Current spotread guide][a-spotread].

## Reproducible Argyll source locations

These locations refer to the unmodified [official 3.5.0 source archive][a-source],
SHA-256 `f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f`.
They describe source behavior, not an assertion that every installed executable
has that version or that every reachable path has been physically tested.

| Source location | Relevant behavior |
| --- | --- |
| `spectro/i1pro.c:130`, `:672`; `spectro/i1pro3.c:127`, `:632` | Capability masks, filter conditions, mode conversion and validation |
| `xicc/xspect.h:42`; `spectro/i1pro3_imp.c:4821` | Active measurement-type/unit definitions and actual result scaling, including ambient flash |
| `spectro/insttypes.c:479`; `spectro/i1pro_imp.c:522`; `spectro/i1pro3_imp.c:6847` | USB generation hints, firmware refinement and EEPROM variant identity |
| `spectro/i1pro_imp.c:1554`; `spectro/i1pro3_imp.c:1022`, `:1073`, `:1144` | Calibration validity, needed/available calibrations and calibration dispatcher |
| `spectro/i1pro3_imp.c:7533`, `:8689`, `:9581` | Wavelength calibration, polarized reflective calibration and adapter checks |
| `spectro/i1pro3_imp.c:10729`, `:10985` | `i1pro3_comp_refl_value`, normal/UV data and condition-specific spectra |
| `spectro/spotread.c:555`, `:1114`, `:1591`; `spectro/i1pro_imp.c:5479` | Experimental i1Pro 2 UV path, not supported native M1 |
| `spectro/spotread.c:945`, `:1427` | D50 filter parser and unsupported-ambient refusal |

## Current package boundary

Original i1Pro, i1Pro2 and i1Pro3 all use the [shared instrument API](instrument.md)
and [generic frontend](generic-frontend.md). There are no separate family
acquisition modules. The initialized upstream driver decides which routes and
options are available; `check_mode`, setters/getters and native result quantities
confirm each request. A fixed filter with no setter can satisfy a request only
when its getter reports the requested filter. An incompatible condition fails.

Live reads negotiate calibration in the same native lifetime. Transmission asks
for a fresh available white reference, and the operator must then present the
sample. Native reference/warning events are retained. This does not claim optical
reference quality, tile identity verification or private driver-field checks that
the shared API cannot expose.

Strip direction is operator-declared and associated with retained native indices.
Automatic chart identification and DTP51 alignment are separate from this contract.
Resolution is capability-gated, with no promise about optical bandwidth or
per-sample exposure metadata. The upstream source corrections remain attributed
and build-bound; they are not separate acquisition implementations.

**No family model/mode/platform/backend combination has physical validation.**
Substituted workers exercise the software contract. Historical narrow-build
platform checks do not establish current broad-driver platform support.

## Sources and access notes

Sources are manufacturer-authored material and upstream documentation/source.
Page numbers refer to **printed English pages**, not a PDF viewer's spread count.
Some X-Rite PDF downloads returned HTTP 403. The original/2 material was inspected
through indexed manufacturer text; the standard-3 manual and L7-713 comparison
were also inspected as rendered pages from manufacturer-document mirrors.
The Plus manual was inspected through a text mirror. No manual/PDF is distributed
with this repository. Retailer descriptions were not used to resolve mode support.

- X-Rite, *i1Pro and i1Pro 2 Comparison*, L7-518 (03/12): [official PDF][x-original].
- X-Rite, *i1Pro 2 User Manual*, especially pp. 16–17 and 40: [official PDF][x-two].
- X-Rite, *i1Pro 3 User Manual*, pp. 14–31 and 38–39: [official URL][x-three],
  [inspected mirror][x-three-mirror].
- X-Rite, *i1Pro 3 Plus User Manual*, especially pp. 14–17 and 26–27:
  [official URL][x-plus], [inspected mirror][x-plus-mirror].
- X-Rite, *i1Pro 3 Plus / i1Pro 3 / i1Pro 2 Comparison*, L7-713 (06/20):
  [official PDF][x-compare], [inspected mirror][x-compare-mirror].
- X-Rite, *i1Pro 3 Specification*, L7-718: [official PDF][x-three-spec].
- ArgyllCMS, *Operation of particular instruments*, *spotread*, *chartread*,
  *FWA compensation* and 3.5.0 source: linked at the relevant statements above.

[x-original]: https://www.xrite.com/-/media/xrite/files/literature/l7/l7-500_l7-599/l7-518_i1pro_compared_to_i1pro2/l7-518_i1pro-i1pro2_en.pdf
[x-two]: https://www.xrite.com/-/media/xrite/files/manuals_and_userguides/i/i1pro2_user_manual_all.pdf
[x-three]: https://www.xrite.com/-/media/xrite/files/manuals_and_userguides/i/1/i1pro-3/i1pro3_sa_user_manual.pdf
[x-three-mirror]: https://device.report/m/435857836ab68e0d9bac8c5779e51e578e0174e85549a9ca0029bec37cb094a4
[x-plus]: https://www.xrite.com/-/media/xrite/files/manuals_and_userguides/i/1/i1pro-3/i1pro3plus_la_manual_qsg.pdf
[x-plus-mirror]: https://www.manualslib.com/manual/1743442/X-Rite-Pantone-I1-Pro-3-Plus.html
[x-compare]: https://www.xrite.com/-/media/xrite/files/literature/l7/l7-700_l7-799/l7-713-i1pro3pro3pluspro2-comparison/l7-713_i1pro3plusi1pro3i1pro2-comparison_en.pdf
[x-compare-mirror]: https://repo.chromachecker.com/pdf/display/MOEK3q/EN/store_48
[x-three-spec]: https://www.xrite.com/-/media/xrite/files/literature/l7/l7-700_l7-799/l7-718-i1pro-3-spec-sheet/l7-718_i1pro3_spec-sheet_en.pdf
[x-family]: https://www.xrite.com/categories/calibration-profiling/i1-pro-3-family
[a-instruments]: https://www.argyllcms.com/doc/instruments.html
[a-original]: https://www.argyllcms.com/doc/instruments.html#i1p
[a-two]: https://www.argyllcms.com/doc/instruments.html#i1p2
[a-three]: https://www.argyllcms.com/doc/instruments.html#i1p3
[a-spotread]: https://www.argyllcms.com/doc/spotread.html
[a-fwa]: https://www.argyllcms.com/doc/FWA.html
[a-chartread]: https://www.argyllcms.com/doc/chartread.html
[a-old-comparison]: https://www.argyllcms.com/doc/i1proDriver.html
[a-source]: https://www.argyllcms.com/Argyll_V3.5.0_src.zip
