# i1Display family: Argyll support and measurement workflows

Checked 2026-09-18 against Argyll's per-device documentation, its official
3.5.0 source and X-Rite/Calibrite literature. The [upstream download page][a-source]
identifies 3.5.0 as the stable release at this check. An installed executable's
version/build still needs independent identification.

## In plain language

i1Display instruments are **colorimeters**, not i1Pro spectrophotometers. They
measure tristimulus values such as XYZ; they do not measure a sample spectrum.
Older Display 1/2 instruments and newer i1D3 instruments have different modes,
calibration and correction support. Even similar-looking i1D3 models can have
different timing capabilities. A common UI can share the acquisition lifecycle,
but cannot give every model the same controls.

Support here means what upstream documents or implements. It does not mean this
package already exposes those operations or that this review operated a device.
See the [current package boundary](#current-package-boundary).

## Names, drivers and support boundaries

Check the [legacy i1Display entry][a-legacy] and [i1D3 entry][a-i1d3] first, then
the command documentation and versioned driver. Manufacturer software feature
tables do not establish Argyll support.

| Names | Argyll mapping and distinction |
| --- | --- |
| Eye-One Display / i1Display 1; MonacoOPTIX and some Sequel rebadges | `instI1Disp1`, `i1disp.c`. Emissive spot with LCD/CRT factory calibration selections; those selections do not imply Display 2's refresh-measurement capability. |
| i1Display 2, LT, ColorMunki Create | `instI1Disp2`, `i1disp.c`. Display 2/LT/Create operate equivalently in Argyll despite manufacturer software-bundle differences. Ambient and refresh facilities differ from Display 1. |
| HP DreamColor/APS, CalMAN X2, LaCie Blue Eye legacy variants | Upstream reports Display-2-family operation; the DreamColor calibration is display-specific. Some of these brands also have newer devices, so the label alone is insufficient. |
| ColorMunki Smile | Listed by upstream but distinct from ColorMunki Display; dispatched to `i1disp.c`. See the pinned-source qualification below. |
| i1Display Pro, i1Display Pro Plus | `instI1Disp3`, `i1d3.c`, with internal subtype/revision identification. “i1 Display 3” is an Argyll family name, not an i1Pro 3 spectrometer. |
| ColorMunki Display, i1Display Studio; Calibrite ColorChecker Display/Pro/Plus | Explicitly included in Argyll's supported i1D3-family list. Timing follows the initialized subtype, not a generic family label. |
| Calibrite Display SL, Display Pro HL, Display Plus HL | Manufacturer models with separate specifications. Their individual retail-name mapping is not established by the upstream references cited here; retain that uncertainty rather than asserting either universal compatibility or incompatibility. A supported native identity is not invalid merely because its marketing name is newer. |

Do not confuse **i1Display Studio** with **i1Studio / ColorChecker Studio**, or
**ColorMunki Display** with **ColorMunki Photo/Design**. The latter pairs are
spectrophotometers on a different Argyll driver. [Argyll instrument list][a-all].

OEM support is not universal. Argyll names several working i1D3 OEM variants,
but its [2.3.0 release notes][a-changes] explicitly remove built-in C6 support.
The documented [OEM unlock extension][a-environment] is not evidence that every
branded device works automatically. Unknown-model/unlock failures must remain
failures, not trigger guessed commands or an automatic downgrade.

### Manufacturer limits are model-specific

X-Rite's i1Display Studio/Pro/Pro Plus comparison gives maximum luminance ratings
of 1,000/1,000/2,000 cd/m²; Calibrite's 2021 ColorChecker Display/Pro/Plus chart
gives the same respective ratings. Current SL/Pro HL/Plus HL ratings are
1,000/3,000/10,000 cd/m². Calibrite describes new HL sensors; these are not simply
new labels for the older optical specification. These are manufacturer specifications, not measurements
made here, guaranteed accuracy at every level, or Argyll compatibility evidence.
Do not assign a generic `i1d3` device a 10,000 cd/m² rating.
[X-Rite comparison][x-ranges]; [ColorChecker comparison][c-old-ranges];
[SL/HL introduction][c-intro], [SL][c-sl], [Pro HL][c-pro-hl], [Plus HL][c-plus-hl].

## Upstream modes and controls

The source's mode checks and result types matter as well as its capability bits.
Table entries below refer to initialized driver subtypes, not merely USB names.

| Operation | Display 1 | Display 2/LT/Create | i1D3 |
| --- | --- | --- | --- |
| Emissive spot | Yes | Yes | Yes |
| Projector / `emis_tele` route | Not advertised | Not advertised | Advertised; use correct screen-viewing geometry |
| Steady ambient | Not advertised | Advertised | Advertised; diffuser position checked |
| Refresh-rate measurement / synchronization | Not advertised | Advertised | Pro-class subtypes; excluded for `i1d3_munkdisp` |
| Adaptive versus nonadaptive option | Not advertised as a selectable mode | Not advertised as a selectable mode | Selectable; faster nonadaptive readings can sacrifice low-light performance |
| CCMX correction | Yes | Yes | Yes |
| CCSS and selectable observer | No CCSS route | No CCSS route | Supported |
| Measured spectrum, reflectance/transmittance, strip scan, ambient flash | Not advertised | Not advertised | Not advertised |

These last exclusions come from the explicit accepted mode sets and result
construction in the pinned drivers, not from the absence of a menu item. Smile's
intended subtype has emissive spot, CCFL/WLED selections and CCMX, without ambient
or refresh facilities; the source qualification below prevents treating that
intention as a verified runtime capability report.

For ColorMunki Display, upstream specifically documents a minimum measurement
time of about one second and no refresh-rate measurement/synchronization. Dark
samples can take longer. Do not promise Pro-class refresh behavior because a
generic i1D3 capability mask contains a refresh-override bit. [i1D3 guide][a-i1d3].

Key [spotread controls][a-spotread] and driver options:

- `-e`, `-p`, `-a`: emissive, tele/projector and ambient requests, where supported.
- `-y`: instrument-specific display selection. Legacy `l`/`c` combines factory
  calibration with LCD/CRT behavior; Smile uses CCFL/WLED choices. i1D3 `n`/`r`
  distinguishes non-refresh/refresh, with additional installed correction choices.
- `-X file.ccmx` or `-X file.ccss`: correction selection, not another physical mode.
- `-Yr`/`-Yn`, `-YR:rate`: supported refresh overrides, not a way to add a missing
  refresh sensor capability. Interactive `F` measures refresh; `f` reads the
  calibrated rate when supported.
- `-YA`: i1D3 nonadaptive measurement. The native driver also exposes minimum
  integration-time get/set; the requested minimum is not a guaranteed total
  operation duration.
- i1D3 exposes programmatic/user triggers, indicator LED control and, outside the
  slower Munki subtype, display-update-delay measurement. Timing results are not
  XYZ observations. These are backend facilities, not claims about this package's UI.

Revision B's AIO acquisition path is another driver implementation distinction;
it is not an HDR mode or a marketing-model guarantee. Its documented compatibility
override is described in [Argyll's environment reference][a-environment].

## Calibration and physical setup

“Calibrate” can mean several different things:

1. **Factory instrument characterization:** i1D3 reads stored sensor sensitivity
   and calibration data. Selecting display corrections builds a host-side
   conversion; it is not a measured spectrum or a white-tile operation.
2. **Refresh calibration:** eligible Display 2/i1D3 modes determine display timing.
   The i1D3 driver requests an emissive 80% patch condition when using its explicit
   calibration dispatcher, and can also attempt refresh calibration during a read.
3. **Original Display 1 optional black calibration:** the driver acquires covered-sensor
   offsets and **writes them to instrument EEPROM**. This is persistent state
   modification, not passive observation. A no-EEPROM investigation must not
   invoke it under a generic “calibrate all” action.
4. **Display calibration/profiling:** adjusting a monitor or loading display LUTs
   is a separate workflow from calibrating the measuring instrument.

The i1D3 needed/available-calibration implementation exposes refresh calibration,
not an i1Pro-style white-tile or user dark calibration. Do not transplant those
prompts across families. Follow the actual returned calibration/setup condition.
Source: `i1disp_do_black_cal`, `i1disp_get_n_a_cals`,
`i1d3_get_n_a_cals` and `i1d3_calibrate`.

The modern [Calibrite quick-start guide][c-qsg] distinguishes the setups:

- Display: move the diffuser away, position the optics at the displayed patch and
  support the device using the cable/counterweight.
- Ambient: snap the diffuser over the optics; its desktop example points upward.
  The result's Y is illuminance in lux, not display luminance in cd/m².
- Projector: use the arm as a stand or a tripod, keep optics uncovered and point
  toward the projected **screen**, not the projector lamp. This returns an
  emissive/luminance observation despite the screen physically reflecting light.

i1D3 checks diffuser position and has separate emissive/ambient calibration
matrices. A diffuser-position report is not permission to silently change the
requested quantity. Argyll also warns that the diffuser magnet can disturb CRTs;
keep its placement in the measurement setup. [i1D3 operation notes][a-i1d3].

## Corrections and result interpretation

**CCSS** contains reference display spectra from a spectrometer. The i1D3 combines
them with its own stored sensor sensitivities and selected observer to calculate
the conversion to XYZ. **CCMX** is a matrix correction for a particular meter and
display combination, over its recorded base calibration. It is not universally
transferable to another unit. Do not stack CCSS and CCMX as independent accuracy
upgrades. [ccxxmake][a-ccxxmake]; [CCMX qualifications][a-ccmx].

Correction application is mode-specific: both pinned drivers apply CCMX to
emission, not ambient. i1D3's CCSS samples determine its emission matrix; ambient
uses a separately computed matrix from stored ambient sensitivities and the
selected observer. A display correction selected in the UI therefore must not
be described as having corrected ambient XYZ.

Argyll's [oeminst][a-oeminst] can translate manufacturer EDR files into CCSS and
install correction files. Installed files alter available `-y` choices, so a
fixed list of display technologies is not exhaustive. Conversion capability does
not itself grant redistribution rights to the source files.

Preserve the chosen correction, its identity/digest, base display type, observer,
refresh policy, physical configuration and returned quantity with results.
In the pinned i1D3 `read_sample`, `XYZ_v=1` and `sp.spec_n=0`: **there is no measured
spectrum to export**. Calibration sensitivity curves available through `-YS`
describe the meter, not the light currently measured. Do not substitute a CCSS
reference spectrum for a missing observation spectrum.

The core i1D3 result path is `i1d3_take_emis_measurement` or
`i1d3_take_aio_measurement` → `i1d3_take_XYZ_measurement` →
`i1d3_read_sample`: sensor measurements, selected conversion/correction, then
typed XYZ. Emission Y is cd/m²; ambient Y is lux. Subsequent xyY/Lab calculations
must remain distinguishable from backend XYZ and identify their conventions.

## Discovery and platform boundaries

Argyll's USB matcher maps `0765:5020` to the i1D3 family, not a unique retail model.
Other routes include `0971:2003` for Display 2 and `0765:6003` for Smile.
Initialization reads product/revision/serial information and handles known OEM
variants. Discovery can therefore be automatic without making a family name an
exact model, measurement mode or selected-device identity.

i1D3 supports HID or direct USB transport, not serial/baud-rate framing. On
Windows, upstream documents the ordinary HID driver for i1Display Pro/ColorMunki
Display, so installing an Argyll USB driver is not normally necessary for those
devices. Legacy USB instruments need their own platform instructions.
[Windows][a-windows], [macOS][a-macos], [Linux][a-linux].

Opening/initializing is active access even without measurement: i1D3 can set an
LED and monitor diffuser position. Vendor applications can compete for access.
Do not apply old service-termination workarounds automatically; this package's
[native description boundary](argyll-description.md) documents its shared
no-service-termination patches and active-access requirements.

## Source pin and qualifications

All source locations below refer to [ArgyllCMS 3.5.0][a-archive], archive SHA-256
`f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f`.

| Source | What to inspect |
| --- | --- |
| `spectro/insttypes.c:423`; `spectro/inst.c:724` | USB family mapping and constructor dispatch |
| `spectro/i1disp.c:1286`, `:2282` | Display 1 persistent black calibration; subtype-specific capabilities/mode checks |
| `spectro/i1d3.h:84`; `spectro/i1d3.c:3894` | Internal subtype definitions and conditional capabilities |
| `spectro/i1disp.c:1233`, `:1997`; `spectro/i1d3.c:2306`, `:2743` | Emission/ambient correction paths and legacy calibration availability |
| `spectro/i1d3.c:3105`, `:3275`, `:3310` | Read result, CCMX and CCSS operations |
| `spectro/i1d3.c:3346`, `:3367` | Needed/available calibrations and refresh setup |
| `spectro/i1d3.c:3938`, `:4269`, `:4283` | Physical configuration and integration-time controls |
| `xicc/xspect.h:42` | Active measurement-type and unit definitions |

**Smile source qualification:** the 3.5.0 `new_i1disp` constructor tests
`p->btype == instSmile` where its surrounding dispatch uses instrument type.
That is inconsistent with the intended Smile subtype assignment. The later
model-refinement condition at `i1disp.c:1519` also uses assignment rather than
comparison and can select a different subtype. This review does not claim a physically
observed Smile failure or verify its actual reported capability mask. Consult
`spectro/i1disp.c:2686` and its initialization path before relying on the intended
Smile restrictions in the table.

**New retail names:** the cited positive upstream family list covers
i1Display Pro/Plus, ColorMunki Display/i1Display Studio and ColorChecker
Display/Pro/Plus. This research does not resolve a one-to-one SL/HL naming map or
claim that all generic OEM variants are supported. Compatible identification
through the existing driver can be valid; an unresolved label is not evidence
that a new driver is necessary.

## Current package boundary

The [broader native catalog](argyll-description.md) includes these upstream
drivers. The [shared instrument API](instrument.md) and
[generic frontend](generic-frontend.md) provide XYZ-only acquisition with no
fabricated spectrum, capability-based controls and supported correction handling.
The CLI's `--xyz-only` removes its spectrum requirement. Initialization effects
must be accepted explicitly through discovery. No physical model/mode/platform
combination is validated by the substituted software checks.

## Source notes

Manufacturer readings use their authored documents, not reseller specifications.
The Calibrite June 2023 quick-start PDF was downloaded outside the repository;
its ambient and projector pages were visually inspected. Page references are
one-based PDF pages: display 25–31, ambient 32–35, projector 36–39. Manufacturer
software requirements are not Argyll installation requirements. No source PDFs,
proprietary correction files or SDK binaries are distributed here.

[a-source]: https://www.argyllcms.com/downloadsrc.html
[a-archive]: https://www.argyllcms.com/Argyll_V3.5.0_src.zip
[a-all]: https://www.argyllcms.com/doc/instruments.html
[a-legacy]: https://www.argyllcms.com/doc/instruments.html#i1d
[a-i1d3]: https://www.argyllcms.com/doc/instruments.html#i1d3
[a-changes]: https://www.argyllcms.com/doc/ChangesSummary.html
[a-environment]: https://www.argyllcms.com/doc/Environment.html
[a-spotread]: https://www.argyllcms.com/doc/spotread.html
[a-ccxxmake]: https://www.argyllcms.com/doc/ccxxmake.html
[a-ccmx]: https://www.argyllcms.com/doc/ccmxs.html
[a-oeminst]: https://www.argyllcms.com/doc/oeminst.html
[a-windows]: https://www.argyllcms.com/doc/Installing_MSWindows.html
[a-macos]: https://www.argyllcms.com/doc/Installing_OSX.html
[a-linux]: https://www.argyllcms.com/doc/Installing_Linux.html
[x-ranges]: https://www.xritephoto.com/documents/literature/en/L11-293_i1_Solutions_en.pdf
[c-old-ranges]: https://calibrite.com/us/wp-content/uploads/sites/2/2021/11/Calibrite-Calibrator-comparison-chart-USA.pdf
[c-sl]: https://calibrite.com/us/product/display-sl/
[c-pro-hl]: https://calibrite.com/us/product/display-pro-hl/
[c-plus-hl]: https://calibrite.com/us/product/display-plus-hl/
[c-qsg]: https://calibrite.com/us/wp-content/uploads/sites/2/2023/06/Calibrite-Display-devices-QSG-0623.pdf
[c-intro]: https://calibrite.com/learning/calibrite-introduces-a-new-standard-in-calibration-devices-for-next-generation-displays/
