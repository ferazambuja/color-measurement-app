# Synthetic equipment demos

`color_measurement.demo` supplies hardware-free equipment choices to the generic
frontend. It works from the installed package and does not enumerate instruments,
launch a native worker or import the test suite. Every descriptor and retained
reading is simulated, with backend `argyll-demo` and a `DEMO-` serial label.
That explicit backend identifies palette-generated observations, even when its
model label names a CR or PR instrument. These observations require simulated
provenance and cannot carry native CR or PR capture facts. Actual native
bindings retain their separate identity and same-capture publication checks.

```python
import json
from color_measurement.demo import demo_catalog_json, demo_frontends

catalog = json.loads(demo_catalog_json())
frontends = demo_frontends()
front = next(f for f in frontends
             if json.loads(f.describe_json())["device_id"] == "demo:i1pro3")
descriptor = json.loads(front.describe_json())
```

The catalog is a JSON list of `persona`, `device_id`, `model`, `brand`,
`display_model`, `label`, `synthetic`, `live` and `preview` fields. `brand` and
`display_model` provide picker grouping and model search from the canonical
Argyll name, including its historical branding. [ColorMunki Smile](https://www.xrite.com/categories/calibration-profiling/colormunki-smile)
groups under X-Rite with display model `ColorMunki Smile`; its canonical name
omits the manufacturer. HCFR groups under HCFR with display model
`Colorimeter`; its canonical `model` remains `Colorimtre HCFR`. Historical
GretagMacbeth instruments use picker brand `X-Rite (GretagMacbeth)` so both
manufacturer names are searchable. [X-Rite's company history](https://www.xrite.com/page/our-brands)
documents the 2006 merger; the canonical model retains its historical branding.
Cube uses `Palette (SwatchMate)` for the same reason: [Palette's 2015 company
announcement](https://s3-eu-west-1.amazonaws.com/asset.palette.com/docs/Cube_Creators_Unveil_New_Identity_and_Photoshop_Integration.pdf)
identifies the former SwatchMate name. Select by the stable `device_id`.
These presentation fields do not change frontend descriptors,
capability IDs, native binding names or retained device identities.
`is_demo_frontend(frontend)` identifies the actual packaged Demo frontend and
its Demo I/O implementation. A simulated provenance label on another binding
does not satisfy this check. Native desktop bulk loading uses it before
automatically acknowledging Demo prompts through the ordinary batch owner.

Factory objects are `InstrumentFrontend` subclasses:
use Python `isinstance` when checking an object received by another language.
The regular [generic frontend request/event/result contract](generic-frontend.md)
applies, including capability IDs, calibration-only operations, operator answers,
cancellation, retained outcomes and JSON/MAT export.

| Persona | Preview equipment |
|---|---|
| `i1pro3`, `i1pro2`, `i1pro`, `i1monitor` | X-Rite i1 Pro 3, i1 Pro 2, i1 Pro (rev A-D) and i1 Monitor |
| `colormunki` | X-Rite ColorMunki spectrophotometer |
| `i1_display_3`, `i1_display_2`, `i1_display_1` | X-Rite i1 DisplayPro / ColorMunki Display; GretagMacbeth i1 Display 2 and 1 |
| `colormunki_smile` | ColorMunki Smile |
| `huey` | GretagMacbeth Huey |
| `dtp20` | X-Rite DTP20, including synthetic stored-chart reading |
| `dtp22` | X-Rite DTP22, calibrated by clicking onto its plaque |
| `dtp41` | X-Rite DTP41 |
| `dtp51` | X-Rite DTP51, including a simulated device-button trigger |
| `dtp92`, `dtp94` | X-Rite DTP92 and DTP94 colorimeters |
| `spectroscan`, `spectroscant` | GretagMacbeth SpectroScan and SpectroScanT, including X-Y placement/navigation |
| `spectrolino` | GretagMacbeth Spectrolino |
| `spyder3`, `spyder4`, `spyder5` | Datacolor Spyder3, Spyder4 and Spyder5 |
| `spyderx`, `spyderx2`, `spyder2024` | Datacolor SpyderX, SpyderX2 and Spyder 2024 |
| `colorhug`, `colorhug2` | Hughski ColorHug and ColorHug2 |
| `hcfr` | HCFR colorimeter |
| `klein_k10` | Klein K-10, emissive; its ambient mode needs a correction matrix, which the demo does not model |
| `specbos_1201`, `specbos`, `specbos_2501`, `spectraval` | JETI specbos 1201, 1211, 2501 and spectraval |
| `ex1` | Image Engineering EX1 |
| `smcube` | SwatchMate Cube |
| `cr250` | Colorimetry Research CR-250 over the contributed driver: emissive spot and telescopic, with its spectrum or, with `xyz_only`, XYZ alone |
| `cr100` | Colorimetry Research CR-100 over the same driver: an XYZ colorimeter, emissive spot and telescopic |
| `cr300` | Colorimetry Research CR-300 over the shared CR driver: emissive spot and telescopic, with its captured native grid |
| `pr655`, `pr670` | Photo Research PR-655 and PR-670 over one shared PR driver: emissive spot/telescopic luminance and radiance, or ambient illuminance and irradiance; 101 and 201 bands respectively from 380 to 780 nm, or XYZ alone with `xyz_only`. All Demo readings are simulated. See the [PR quantity and unit limits](argyll-native-controls.md#photo-research). |

These personas retain different capability words, spectral availability, modes
and calibration examples from the existing substituted native captures. They are
not labels over one instrument: those outside the hand-written stand-ins are rows
of `tools/native_argyll/driver_personas.c`, each transcribed from its own driver. An advertised upstream mode without a
completed synthetic capture is explicitly unavailable in this preview. The
current descriptor, not this table, gives the exact available mode/workflow set.

Packaged capture facts are cached in the process after loading. Descriptor and
snapshot polling reuse those static facts rather than rereading the full capture
file. Frontend calibration and sample counters remain isolated; mutable
connection, setup, calibration and patch data are copied before an operation
changes them. The cache does not store acquired results or physical-device state.

The descriptor also retains the captures' `baseline_mode_checks` and per-mode
`baseline_native_mode` answers. These come from the substituted driver's initial
exact-word checks, not from inferring acceptance because a synthetic reading
succeeded. They remain explicitly simulated, including a `confirmed` status;
they do not establish physical support. The generator refuses conflicting
descriptor and captured baseline facts. Changing demo options does not recheck a
physical instrument or certify those settings.
Every constructible Argyll type has a persona except the Spyder1 and Spyder2,
whose initialization downloads firmware (`spyd2.c:2998-3002`); discovery lists
them as never openable rather than leaving them out.

`descriptor.preview` adds `persona`, `label`, `modeled`, `unmodeled`, `data` and
`example_layouts`. Show its data limitation alongside the equipment picker.
Free samples from every colour-reading persona cycle through the same twelve illustrative shapes
used by the MATLAB serial demo. The sequence advances across operations: twelve
separate spot readings exercise the same set as a twelve-patch chart. Calibration,
descriptor queries and cancellation before a reading do not advance it. A fresh
equipment preview starts the sequence again; restoring its existing window keeps
its position. Samples follow a repeatable sequence rather than random ordering.

Each exposure adds a repeatable random brightness gain between 0.995 and 1.005
(up to ±0.5%). The same gain multiplies XYZ and every spectral band, preserving
their agreement and chromaticity for that shape. Repeating a shape on the next
cycle gives slightly different brightness. This is illustrative demo jitter,
not an instrument noise distribution, repeatability specification or simulation
of wavelength-dependent noise. It does not affect physical readings, calibration,
refresh-frequency or timing results, or numerical setting effects.

The packaged palette preserves each persona's captured wavelength grid. Its base
reflective and transmissive curves are 5 to 95 % factors before the small gain;
emissive curves describe emitted light. XYZ-only personas receive the palette's
XYZ without a spectrum. Argyll's existing colour converter calculates base XYZ
offline, under D50 for relative curves and without an illuminant for emission;
no native worker runs during a demo. These are synthetic examples, not measured
sources, references or instrument models. The raw simulated event retains the
profile name, gain and integer random seed as `demo_sample`.

A guided spot preview supplies explicit per-reading synthetic context in the
existing request options:

```json
{"demo_chart_sample": {"cell_index": 1, "attempt": 2}}
```

`cell_index` is the caller's frozen, row-major cell number, an integer from 1 to
140. `attempt` is an integer from 1 to `2^53 - 1`, supplied by the caller, not a
chart counter maintained by Python. The bound keeps the context exactly
representable in the MATLAB client and bounds the serialized random seed.
Booleans, fractional values, missing or extra fields are
refused. This context is available only for a one-reading spot request; null or
an absent option keeps the ordinary free-sample sequence. It is Demo-only and
never reaches native configuration or worker flags. Clients supply it as
dispatch context, not an editable instrument setting.

The cell selects palette index `(cell_index - 1) % 12`; the random seed is
`140 * (attempt - 1) + cell_index`. Reading the same cell out of order preserves
its base shape. A new attempt selects a separately seeded bounded uniform gain;
the same cell/attempt and preview mode/grid reproduce the same synthetic values. Guided reads do not
advance the free-sample sequence. Ordinary patch IDs remain opaque retention
labels, including repeated IDs or renamed readings; they do not select colours.
These twelve repeated illustrative shapes are not Classic/SG reference values
or simulated measurements of a particular physical chart.

The canonical request and `requested_options_json` retain `demo_chart_sample`.
The raw simulated result retains `{profile, gain, seed, chart_sample}`, where
`chart_sample` contains the accepted `cell_index` and `attempt`. Ordinary free
results omit `chart_sample`. Result and observation provenance remain
`simulated`, with the same gain applied to XYZ and every spectral band.

`tools/generate_demo_palette.py --argyll-source <Argyll_V3.5.0>` generates
`src/color_measurement/data/demo_palette.json` with the existing shape table and
colour calculator from `tools/native_argyll/chart_stub.c`. It compiles only that
calculator and the pinned upstream colour-conversion dependencies on macOS or
Linux, never a driver, session or transport. `--check` compares regenerated bytes.
The installed Python package needs neither the compiler nor upstream source.
Example layouts are suggestions: sample IDs, strip counts and chart/sheet
geometry remain editable within the normal workflow bounds. Stored charts are
synthesized for the requested geometry; this does not simulate physical memory
contents, chart recognition, motors or failed scans.

The normal parser, calibration callbacks, sighted-navigation computation, strip
direction handling, process-independent cancellation and result retention run
unchanged. Only session I/O is substituted in memory. A reading replays what its
persona's driver asks for at startup, recorded from a fresh start with nothing
restored: the i1 Pro 2, i1 Pro 3 and ColorMunki calibrate before their first
reading on the white tile or with the dial
at its calibration position, and a non-adaptive emissive spot or tele reading
then asks for a full-white display patch to set its integration time; the
SpectroScan calibrates itself on its own tile;
the i1 Display's captured non-refresh default (`i1d3.c:4030-4039`) has
no refresh-rate calibration to do (`:3346-3355`). On a real driver, selecting
a refresh display can require an 80 % patch (`:3383-3418`); this preview does
not select a different calibration capture when `display_type` or refresh
options change. The same limitation applies to its i1 Display 2, Spyder3/4/5
and JETI examples. The i1 Pro and i1
Monitor calibrate at every start like the i1 Pro 2, with a dark of their own for
emissive routes because they have no wavelength LED; the Spectrolino on its white
tile, with a transmission white after it; the DTP22 by being clicked onto its
plaque; the SMCube on its white; the SpyderX and EX1 take a dark at every start;
a JETI transmission reading asks for its white; the DTP20, DTP41, DTP51, DTP92,
DTP94, Huey, ColorHug, HCFR, Klein, SpyderX2, Spyder 2024 and CR-250/CR-100
measure straight away. A JETI's 401-band reading is too large for an inline
patch event, so its captures are notified by reference (`retained_patch_v1`).
Calibration on its own
still shows a recorded example scenario. Once a mode has been calibrated -- by a
reading or on its own -- a later reading in that mode replays the same route
recorded from a start whose driver kept that calibration
(`<persona>/calibration_retained.json`). The i1 Pro family, ColorMunki, SMCube,
EX1 and SpyderX then measure without calibrating, except that a non-adaptive
emissive reading still asks for its display white and a transmission reading for
its white. The Spectrolino, SpectroScans, DTP22 and JETIs, whose drivers are not
asked to keep one, calibrate as before. A kept calibration lasts until the demo
ends: the drivers' own time and temperature limits are not modelled, and each
equipment preview keeps its own. The demo's own option
`demo_sensor_misplaced` replays a run recorded with the sensor, dial, diffuser or
adapter in the wrong place, so the `sensor_position` prompt can be seen without
hardware. It is offered, in `applies_to`, only on the spot modes a run was recorded
for: the ColorMunki's projector reading and transmission calibration, the i1
Display's ambient reading, and the i1 Pro 3's reflective calibration and ambient
reading, which asks about the tile and then the ambient head. It holds for each
of those modes' workflows: calibration on its own replays the run's recorded
calibration, with its wrong position where the run met one while calibrating, and
calibrates as usual otherwise. It is off by default, and every other mode refuses
it. None of this predicts the calibration a
physical unit currently needs. Synthetic readings are supplied
without an artificial delay per reading. A demo-specific `device_trigger`
instruction means “simulate pressing the instrument button”; it accepts `accept`
or `cancel`. A calibration notification is still not answered as a calibration
prompt: a separate simulated-button instruction supplies that action.
That button's pending payload keeps the calibration/reference block as context,
including its original `acknowledged: false`; the response answers the simulated
button action only. X-Y prompts retain zero-based `location_index` and
`location_count` so clients can identify each fiducial before answering.

Source-advertised setting forms retain normal type/range validation. Resolution,
XRGA, display-type and applicable averaging selections are retained as simulated
configuration; they do not transform the template's numerical values or grid,
or select a different calibration sequence for display-type or refresh settings.
Reflective filter selection changes the simulated condition metadata, not an
optical model. Every option has `preview_effect`, and the synthetic connected
event retains `demo_configuration` and `demo_numerical_effects_modeled: false`.
Hardware-unavailable controls retain their own reasons. CCMX/CCSS is the explicit
demo exception: correction controls are unavailable because no transform is
performed and no `correction_applied` success may be invented.

Advertised fresh-frequency workflows also run through the common session parser
and polled operation. They return an explicitly simulated 59.94 Hz scalar with
the caller's external-stimulus declaration and no colour quantities.
That frequency is a synthetic constant, not a display-rate prediction or the
effect of a selected calibration. Run
`uv run --locked python examples/refresh_frequency_demo.py`; add
`--output outcome.json` to save to a new path, or install the `mat` extra and
use `--format mat --output outcome.mat`. Saving never measures again.

Synthetic values are illustrative patterns, repeated for longer batches.
Their spectra and XYZ are not a metrologically consistent optical simulation.
Calibration validity, exposure duration, noise, settings' numerical effects,
physical buttons/accessories and transport errors are not established by this
demo. Never use these values for scientific conclusions or device validation.

## Updating and checking

The installed `data/demo.json` contains reduced synthetic I/O templates and hashes
of their source captures. It contains no physical measurements. Regenerate from
the existing generic frontend captures, without running a worker:

```sh
uv run --locked python tools/generate_demo_data.py
uv run --locked python tools/generate_demo_data.py --check
uv run --locked pytest -q tests/test_demo.py
```

The focused checks exercise offered workflows, variable counts/IDs, distinct
calibration/trigger instructions, changed settings, cancellation, repeated
measurement, reverse ordering and ordinary record persistence. MATLAB exercises
its own public picker and consumer against this same factory. Python checks do
not establish MATLAB UI or physical acceptance.
