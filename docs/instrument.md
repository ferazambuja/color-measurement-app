# Generic instrument API

[`color_measurement.instrument`](../src/color_measurement/instrument.py) drives
linked ArgyllCMS instruments through one native helper, including i1Pro.
The initialized driver confirms each mode through `check_mode` and `set_mode`.
Shared workflows use the common vtable; bounded driver-specific policies and
patches preserve differences the capability words do not express. Argyll's own
applications have exceptions, for example DTP51 alignment in `chartread.c:1756-1759`.

| | |
|---|---|
| catalog | 38 stock names; 43 with the CR/PR contribution (CR-100/250/300, PR-655/670); native construction remains a separate build fact |
| workflows | spot, strip, X-Y, stored chart, calibration |
| acquisition under test | substituted native worker; no physical validation |

> **Nothing on this page has been validated against a physical instrument.**
> Every check runs against substituted instruments that touch no hardware. The
> routes are built from ArgyllCMS's own capability words and driver behaviour,
> and that is the whole basis for expecting them to work.

## Selecting a device

```python
from color_measurement.discovery import InstrumentBuild

build = InstrumentBuild.from_report("/path/to/report.json")
chosen = ...  # A row from this build's explicit attached() enumeration.
description = build.describe(
    chosen, expect="GretagMacbeth SpectroScan",
    allow_serial_identification=True,
    accept_disclosed_initialization=True,
)
frontend = build.bind(description, allow_serial_identification=True)
```

`InstrumentBuild.bind` exposes the workflows below through `InstrumentFrontend`.
The low-level `Instrument` requires an explicit `expected_helper_sha256` before
launching a process. Production callers use the report-bound route; substituted
worker tests supply a digest after authenticating the test worker.

Setting `CM_ARGYLL_BUILD_REPORT` authorizes selecting that report, just as
`--build-report` does; validating the report authenticates the build, but does
not authorize hardware operation.

`instrument` accepts either an `instType` symbol (`"instSpectroScan"`) or any of
the 56 spellings Argyll itself matches, held in
`argyll_capability.INSTRUMENTS` and re-derived from `inst_enum`'s own body by a
provenance test. A name Argyll would answer `instUnknown` for raises `KeyError`
immediately, rather than failing later as an attach in front of an operator
holding the instrument.

Three constraints a client must respect:

- **`path_index` is a position, never a search.** Take it from a fresh
  enumeration in the same process; never store one and reuse it. Enabling HID
  paths prepends them and pushes every USB index down, so an index that named an
  i1Pro3 in one build may name an i1Display in another. A shifted index that
  lands on a different model is refused (`different_instrument_at_index`), not
  measured. `InstrumentBuild.bind` also pins `expected_path`; if enumeration
  changes the index-to-path association, acquisition refuses before construction
  with `selected_path_changed`. Direct API callers should supply that path too.
- **`expected_serial` is optional and must stay so.** Nine drivers inherit
  `inst.c:99-101`'s default `get_serial_no`, which returns `""`. Seven reachable
  types cannot be pinned by serial at all: DTP20, DTP92, DTP94, HCFR, SpyderX,
  SpyderX2, Spyder2024. Pin the selected OS path for these; a path is not a
  guarantee against physically replacing a unit at that same path.
- **`instSpectrocam` is named but not constructible.** Upstream comments its
  constructor out (`/* NYI ... new_spc ... */`). The table transcribes
  `inst_enum` and nothing else, so it is listed; attaching it fails cleanly.

## Acquisition workflows

| method | workflow | what it is |
|---|---|---|
| `measure(mode)` | spot | one reading; `positioned=True` drives a motorised table to a sighted point |
| `measure_refresh(mode)` | refresh frequency | one fresh native frequency in Hz, without XYZ or spectrum |
| `measure_display_timing(mode, presenter=...)` | display timing | native marker-relative delay and signed reaction in ms, with controlled presentation context |
| `measure_strip(mode, patch_count=…)` | strip | one strip pass, read in one motion |
| `measure_sheet(mode, …)` | X-Y | a sheet on a motorised table (SpectroScan); the operator sights fiducials and the table navigates |
| `measure_chart(mode, …)` | chart | a chart the instrument already stored (DTP20); no placement step and no trigger |
| `stored_readings(mode)` | — | what the instrument is holding now, as `inst_stat_savdrd` bits |
| `native_controls(mode, changes=None)` | settings | read current native settings, or explicitly apply a typed changes object; no measurement patches |

Mode names are composed in Python from Argyll's illumination × action table
(`argyll_capability.mode_word`); the helper never sees a route name. Which
facilities each workflow genuinely requires is declared once:

```python
WORKFLOW_FACILITIES = {
    "refresh_frequency": ("emis_refr_meas",),
    "display_timing": ("meas_disp_update",),
    "spot": (),
    "strip": (),
    "xy": ("xy_locate", "xy_position"),
    "chart": (),
}
```

Among colour workflows, only X-Y demands facilities, and only the two bits a sheet cannot be located
without. `xy_holdrel` is deliberately absent: upstream guards every use of it
(`chartread.c:1071`, `:1142`, `:1344`) rather than requiring it. No workflow
demands a trigger — the ladder negotiates one from the capability word, which is
why the DTP51, the one acquisition driver with only `inst2_switch_trig`
(`dtp51.c:809`), can be driven at all.

### Typed native settings

For PR-655/PR-670, the same `measure` API supports `ambient_spot` for illuminance
XYZ and spectral irradiance. Emission spot/telephoto retains luminance and
radiance. The [PR guide](argyll-native-controls.md#photo-research) explains the
required reported optics and 2° observer, exposure intent and unit limitations.

`Instrument.native_controls("emissive_spot")` reads supported current settings
through the initialized CR/PR control adapter. Supplying a changes dictionary
performs an explicit application, including when the dictionary is empty.
`None` means read; `{}` means apply with no requested fields. A settings operation
does not calibrate or acquire a color, spectrum or frequency. Initialization and
setters remain active device operations requiring the ordinary authorized session.

```python
from color_measurement.instrument import Configuration
from color_measurement.native_controls import NativeControls

# meter is an already selected, authenticated Instrument in an authorized session.
settings = meter.native_controls("emissive_spot")
requested = NativeControls.from_changes("pr655", {"cycles": 3, "speedModeCode": 0})
configuration = Configuration(native_controls=requested)
# Ordinary spot readiness, controls and measurement share one native lifetime.
reading = meter.measure("emissive_spot", calibrate=True, configuration=configuration)
```

`NativeControls` freezes supplied fields in the native header's order. Explicit
zero remains present. It rejects unknown names, booleans, nonfinite numbers and
integers outside their exact representation. PR synchronization frequency is a
whole number of Hz from 20 to 400, the manual's only SK form; its readback stays
decimal Hertz. The initialized adapter owns model support, fresh inventories, semantic
bounds and validation of the complete target before its first setter. No arbitrary
device commands are accepted. CR Matrix/Match/user-calibration requests cannot
also select generic display calibration or a correction.

PR `instrumentObserver` can request 2° or 10° for instrument setup. Current
measurement publication requires 2°: a 10° setup remains in the original
receipt and refuses publication without an automatic reset.

The actual request serializer in `native_controls.py` contains:

```python
    def flags(self):
        return dict(controls_action="apply", controls_fields=self.argument())
```

Only those typed flags enter the shared worker. The native headers
`cr250_control.h` and `pr655_control.h` define field order and bit presence;
`native_controls.h` maps each field to the initialized adapter. The manufacturer
references and native constraints are documented with those
[adapter definitions](argyll-native-controls.md).

Both settings and ordinary spot application use the shared worker, cancellation,
allowance handshake and terminal validation. The premeasurement allowance is
refreshed after controls. Non-acquiring settings requests do not pay a color
measurement's timeout floor. A native failure before the settings transaction
keeps its original stage/code and absence of a report.

`Measurement.native_controls` and `InstrumentError.native_controls` retain the
validated native receipt: before/current/requested presence, planned/attempted/
acknowledged/confirmed masks, recovery block, inventories and original replies.
The report must match the dispatched request. Confirmed observable settings must
equal the requested values. A failed report must block every attempted field.
PR SG/Shutter and CR MaxAutoExposure keep ACK separate from unavailable
current/confirmed values. PR setup readbacks, including a capture's
`setup_before`/`setup_after`, are admitted only within the native parser's own
D601/D602 bounds. See the
[native adapter definitions](argyll-native-controls.md).

The frontend's session-owned `NativeControlState` retains uncertainty by canonical
model and reported serial across worker and frontend replacement. Read-only
refresh, an empty apply and partial recovery cannot clear pending fields. A later
successful explicit apply must cover all pending fields and confirm them, using
fresh ACK only for documented settings without a readback. Replacements share a
lock and per-unit operation ownership. An unrelated serial retains independent
state. The bound worker must be pinned to that serial, so a worker that reaches
another unit is refused before its report can change this state. A bound
operation can fail without a native ending that carries its validated report:
Python refuses a malformed or contradictory report or a completion without its
reading, or cleanup fails. Every dispatched field then stays pending unless a
completed reading is retained with that report. A report already matched to the
dispatched request is still merged and retained as `native_control_json`; the
complete events stay in `native_control_exchange_json`, and a native ending keeps
its stage and code.
This is software recovery tracking, not proof of device rollback or
physical readiness. Direct routes remain available until equivalent connected
software evidence is accepted; no physical validation is claimed.

### Measured refresh frequency

`Instrument.measure_refresh(mode="emissive_spot", calibrate=True,
acknowledge=..., configuration=...)` calls the shared
`read_refrate(double *)` method once. The sensor mode must be emissive spot or
telephoto and pass `check_mode`. The configured instrument must advertise `inst2_emis_refr_meas`; this is
checked after mode selection and rechecked after calibration. i1Pro and
ColorMunki advertise this facility only while an emissive mode is current,
so absence in an initial reflective state does not refuse a direct request.
The refresh method does not configure a colour-reading trigger.

The primary contract is ArgyllCMS 3.5.0, `spectro/inst.h:980–989`. These
comments were checked against the digest-verified archive:

```c
/* Measure the emissive refresh rate in Hz. */
/* (Available if cap2 & inst2_emis_refr_meas) */
```

This is a native measurement, not a frequency calculated by the application.
Acceptance requires native `inst_ok`, finite `f`, and `f > 0`, with `f` in Hz
(cycles per second). Zero, negative, NaN, infinite and unwritten results fail.
An invalid value returned with `inst_ok` retains that original status code and
an `invalid_refresh_frequency` diagnostic; it does not become zero Hz.

`inst2_get_refresh_rate` and `inst2_set_refresh_rate` independently advertise
stored-rate retrieval and configuration. Neither admits this measurement.
The pinned upstream census advertises fresh measurement in DTP92, i1Display,
i1Display3, i1Pro, i1Pro3, Klein K10, ColorMunki, Specbos and Spyder2 drivers.
The i1Display3 driver excludes the ColorMunki Display variant from this facility
(`i1d3.c:3922–3926`). This is source support, not physical validation, and initialization policy can
still refuse a particular instrument. The frontend offers refresh only when the
provided capability snapshot advertises it. A snapshot from reflective mode can
therefore omit it even though a direct emissive `measure_refresh` is supported;
a caller needs a capability snapshot from that configured mode. No instrument
identity table fills missing capability bits.

Calibration uses the common needed-driven ladder in the same native lifetime.
Its display-patch instructions and subsequent stimulus must be presented
externally. Only named `display_type` selection applies here; resolution,
colour output, averaging, conversion standards, filters and corrections are
refused rather than recorded as if applied to Hz. No second acquisition call
or read-triggered calibration retry occurs automatically. Driver algorithms and
calibration may themselves take multiple samples. Any driver error,
including `inst_needs_cal`, remains a failed attempt. Start a new operation
explicitly after resolving it.

The polled frontend requires an external stimulus identifier and description,
asks for confirmation before the read and retains them with the scalar.
They are caller declarations, not proof that a display presented them.
The refresh workflow does not control a display; controlled transitions use the
separate display-timing workflow below.
See the [scalar record](data-format.md#scalar-observations) and
[frontend request](generic-frontend.md#refresh-frequency-request).

Run the software-only example without a native build or device:

```sh
uv run --locked python examples/refresh_frequency_demo.py
```

The example produces simulated 59.94 Hz. It exercises the shared operation and
persistence contract; it does not establish physical frequency accuracy.

### Configured mode and stored refresh state

`Instrument.mode_snapshot(mode, configuration=...)` uses the common Argyll
worker to initialize, confirm and set the requested mode, apply acquisition's
options, and return final capability words, display catalogue/readback and
stored refresh state. It does not calibrate, configure a reading trigger or
acquire a colour, frequency or delay. Initialization and setters are active
instrument operations. DTP92's getter also queries device memory.

`Instrument.stored_refresh_rate(mode, calibrate=False, configuration=...)`
queries `get_refr_rate`, gated independently by `inst2_get_refresh_rate`. Its
`StoredRefreshRate` retains `native_method`, `native_code`, `status`, `unit` and
nullable `value`. Native success requires finite positive Hz. `unsupported`,
`needs_calibration` and `undetermined` retain native status with null values.
`not_advertised` has no native code: the configured word lacks the getter, so
nothing was called. A completion after `invalid` or `failed` is refused.
Success with zero, an unwritten value, NaN or infinity fails as
`invalid_stored_refresh_rate`; other failures use `get_refresh_rate`. This is
stored driver state, which may echo an input. No measurement patch is created.

The source contract is pinned ArgyllCMS 3.5.0 `spectro/inst.h:1076–1095`.
These excerpts were checked against the digest-verified archive:

```c
/* Set refresh rate to 0.0 to mark it as invalid */
/* Rates outside the range 5.0 to 150.0 Hz will return an error */
```

`Configuration(refresh_rate=...)` is null (no override), 0 (invalidate stored
rate), or finite Hz in [5, 150]. The independent `inst2_set_refresh_rate` bit
must be present after mode configuration. The worker applies the override after
display selection and corrections, before calibration, matching
`dispsup.c:2407–2418`. Changing i1Display3's effective refresh mode clears its
rate; ordering prevents that from clearing a new override. Zero does not
universally disable synchronization or demand calibration. For i1Display3,
zero returns `inst_misread` and leaves its refresh-calibration request cleared
(`i1d3.c:3657–3664,3680–3710,3352`). Pristine Spyder 2 restores its 50 Hz
default without clearing validity (`spyd2.c:3352–3357`); the workers compose
the missing reset, as [Calibration state and failure cleanup](argyll-native-feasibility.md#calibration-state-and-failure-cleanup)
records. Fresh frequency and display timing operations refuse this option.

Every operation starts a new helper. Host-held rates belong to that operation;
device-backed getters can instead retrieve instrument memory. Calibration is
optional and explicit on a stored query. Unavailable states never trigger an
automatic calibration. Runtime capability words cover model and subtype
exclusions without a per-family support table.

```python
# With an already authorized instrument, inspect without acquiring a reading:
state = meter.mode_snapshot("emissive_spot", configuration=Configuration(refresh_rate=60))
assert not state.has_readings
assert state.stored_refresh.native_method == "get_refr_rate"
# A returned 60 Hz can be the override, not a measured frequency.
```

### Controlled display timing

`Instrument.measure_display_timing(mode="emissive_spot", presenter=...,
calibrate=True, acknowledge=..., configuration=...)` calls the shared
`meas_delay(int *, int *)` method once, on the instrument-owning thread. The
configured mode must be emissive spot or telephoto, pass `check_mode`, and
advertise `inst2_meas_disp_update` after mode selection and calibration. Only
named display calibration selection applies. Colour settings and read triggers
do not apply. Source advertisement exists in i1d3, i1Pro, i1Pro3, ColorMunki and
Klein K10; that is not physical model validation. As with refresh, a reflective
capability snapshot can omit a facility available after selecting emission.

The presenter receives black and white `presentation_required` messages. It
must control the declared display surface and return a `PresentationStamp`
after each draw completes. Both stamps identify the stimulus, display and one
client clock, with finite increasing timestamps. Each phase has a 2 s deadline.
The worker waits 600 ms after accepting black, resets `white_change(1)`, and
starts acquisition. The backend waits at least 200 ms after receiving the native white request
before invoking the presenter; the reader thread calls `white_change(0)` after accepting its stamp. This follows
the actual black-to-white caller in Argyll 3.5.0 `spectro/dispsup.c:414-440,497-545`,
including its thread ownership. Nearby upstream comments describe the opposite
transition; the executed caller and drivers use black to white.

The native contract at `spectro/inst.h:1061-1065` states:

```c
/* This value may be negative if there is a filter delay. */
/* Note that a default instmsec will be returned even on error. */
```

Successful `inst_ok` must write both integer millisecond values. Zero display
delay and signed reaction are valid; `INT_MIN` is reserved as the unwritten
sentinel. Any native error, failed presentation, missing/early/duplicate marker
or invalid context yields no timing reading. Cancellation uses the existing
operation supervisor and cooperative callback. A valid reading delivered before
a late stop can be retained with the terminal stop facts.

The delay is relative to Argyll's **software white marker**. It does not measure
independent optical onset, presentation latency or IPC latency. All five
implementing drivers clamp negative integer delays to zero after converting
to milliseconds. A positive delay below half a millisecond can also round to
zero, so zero does not establish an exact zero or a nonpositive underlying
delay. Klein K10 additionally advances the clamped delay to the next multiple
of its sample spacing, including when the delay was already a multiple.
These operations are in Argyll 3.5.0 `spectro/i1d3.c:3604-3615`,
`spectro/kleink10.c:2106-2127`, `spectro/i1pro_imp.c:2867-2878`,
`spectro/i1pro3_imp.c:1728-1739` and `spectro/munki_imp.c:1941-1952`.
Only the i1Pro, i1Pro 3 and ColorMunki methods measure instrument reaction:
it is the time from the method's start to its measurement-trigger command
(`spectro/i1pro_imp.c:2869`, `spectro/i1pro3_imp.c:1730`,
`spectro/munki_imp.c:1943`). The i1Display Pro family method always reports 0
(`spectro/i1d3.c:3606`). Klein K10 reports minus two of that call's average
sample spacings (`spectro/kleink10.c:2027,2115`), a lookahead it also adds to
the delay rather than a measured reaction.
The application preserves the native result and does not apply `dispsup`'s
additional safety margin. Native marker call bounds
and the wrapper acquisition window use `worker_monotonic`, separate from client
draw-completion timestamps. The window includes notification and thread setup;
it is not the first optical sample. No clock correlation is performed, so the
two clock epochs must never be subtracted.

See the [timing record](data-format.md#timing-observations) and
[polled presentation contract](generic-frontend.md#display-timing-request).
Stored refresh retrieval/settings remain separate facilities.

### Strip order

**Strip support: in development.** The manual workflow is available, with
software checks over substituted instruments. Target-aware chart workflows and
physical-device validation are incomplete.

`measure_strip(..., direction="forward")` keeps the driver's returned order.
`direction="reverse"` reverses the association to caller patch IDs and requires
`inst2_bidi_scan`. Each patch retains its original `native_patch_index`; records
state the operator-declared direction. The frontend's `layout.direction` uses
the same rule and displays it before the scan. Direction defaults to forward.

Complete validated passes retain that association and direction when a native,
timeout or cleanup failure follows the read. Raw event order and native patch
indices stay unchanged. Retaining the complete measurement preserves the failed
operation status; an incomplete pass does not establish recoverable scan coverage.

This is explicit ordering, not automatic target/row recognition. Upstream
`chartread.c:1736-1805` additionally correlates expected chart values and DTP51
padding offsets; that application-level matching is not implemented. For such
scans, IDs identify declared returned positions, not independently recognized
printed targets. There are at most 4096 patches per request; individual drivers
can impose tighter limits.

### Strip operands

The frontend's per-mode strip workflow publishes the consumed `layout_fields`,
required keys, defaults and bounds from `tools/native_argyll/strip_policy.h`.
The shared native session validates consumed fields before calibration or reading.
These bounds prevent malformed driver commands and known reply-buffer overflow;
they do not establish the
maximum physically readable strip on any instrument.

| Driver | Count limit | Required target operands | Optional consumed operands |
| --- | ---: | --- | --- |
| DTP20 | 99 | Positive patch length, nonnegative gap length, in mm | None; the strip must carry a compatible SID field |
| DTP41 | 403 | Patch length 0.01–99.99 mm, gap 0–99.99 mm | None |
| DTP41 transmission | 403 | Those lengths plus trailer length 0–9999 mm | None |
| DTP51 | 99 | Guide number 0–99, sent unchanged | Printable ASCII strip name (1–7 bytes, default `STRIP`) and pass name (1–3 bytes, default `1`) |
| i1Pro generations, ColorMunki spectrometer | 4096 application limit | Patch count | Driver-supported tolerance and declared direction |

DTP41's three-digit count command permits 999, but its 100,000-byte spectral
reply buffer cannot hold 404 patches of at least 248 bytes each
(`dtp41.c:66,497,629,642,659`). The application therefore caps every DTP41 strip
at 403, including XYZ-only reads because conversion may request spectra. This
ceiling does not guarantee that every smaller physical strip is readable.
DTP41 rounds patch/gap lengths to hundredths of a millimetre and the trailer
to whole millimetres (`dtp41.c:265–313`). Outgoing zero geometry is made positive
zero so the driver's digit-only command fields never contain a minus sign;
the supplied request and measured quantities are preserved.
DTP20 selects discrete width bins and
enables but does not report the SID (`dtp20.c:282–339`). DTP51 formats count and
guide as two digits (`dtp51.c:312–350`); the driver does not subtract five from
the supplied guide. Required lengths or guide have no guessed zero/default.
The direct API uses `None` for an absent operand, which the native session
refuses if that driver consumes it. Unused operands are not offered by the
frontend. Direction and tolerance are shared controls gated by capability.

A newly added strip driver without an assessed policy is unavailable. DTP20's
saved-strip capability does not make its live `read_strip` method a retrieval
method: it explicitly rejects saved-data mode (`dtp20.c:636–638`). Saved charts
use the separate chart workflow. Neither DTP51 padding/alignment nor target
recognition is implemented by supplying these operands.

### Calibration lifetime

Low-level `measure`/`measure_strip`/`measure_sheet` require `calibrate=True` to
negotiate calibration before reading. The common frontend enables this for
live reads in the same native process. What carries from one operation to the
next is only what a driver keeps in its own calibration file; a reading asks
the drivers that keep one to use it (see
[initial-calibration suppression](#upstream-initial-calibration-suppression)).
Transmission reads request a fresh available white reference. A placed spot
sample is read without another confirmation when calibration needs no operator
setup, including calibration performed internally. After a physical or display
calibration setup, `sample_position` asks the operator to return to the sample;
an optional setup that was skipped can still leave its position uncertain. A
driver message alone does not require this return. Strip acquisition keeps its
`strip_position` readiness instruction. Read-discovered calibration keeps its
separate retry placement instruction.

`calibration_ready` retains needed, available and remaining masks, performed and
skipped work, source and reference information, and
`initial_calibration_suppressed`: whether the driver was asked, and agreed, to
keep a calibration it retained rather than ask for its at-open one. `calibrate()` returning `inst_ok`
ends the negotiation, as it does upstream (`inst.h:1032-1033`,
`instappsup.c:189-199`). Two driver records follow it and go stale differently:
- `*calt`, the negotiation's own bookkeeping, is not read. `spydX.c:990-1004` and
  `spydX2.c` never clear the black calibration they performed, and
  `kleink10.c:2346` clears a condition bit instead of a calibration type. Reading them
  failed every SpyderX start.
- The remaining mask is the driver's fresh `get_n_a_cals()` assessment, kept as
  the driver gave it. If it still reports required work, the record stays
  conservatively `required`, and
  its detail says the negotiation completed and only the driver's later
  assessment reports the need; both results are retained. A read that reports
  the need again after that is refused within the single retry, with no
  measurement.

The broad build carries [source-bound calibration repairs](argyll-native-feasibility.md#shared-driver-calibration-repairs).
DTP41 transmission accepts acknowledgement of its user-operated white
calibration. DTP92/DTP94 clear a completed calibration's need flag and pending bit
only after the instrument command succeeds. Specbos releases its read lock on
averaging errors and restores temporary host calibration state before returning
averaging failures. These are driver repairs; the shared workflow still retains
each driver's actual assessment and refuses failed calibration.

The SpyderX2/Spyder2024 low-level reading path restores all six black-offset
channels from its calibration cache. A legacy cache from a pre-fix repair build
with the same object layout but containing only three offsets is refused before cached black offsets, validity or date are applied;
the driver's unset black-calibration state remains. The application does not request the optional
black calibration. Only a cache with the driver's compatible version and object
layout can be restored. A stock Argyll 3.5.0 cache fails the object-size check:
the application's calibration-slot repair grows the driver's layout from five
slots to seven. The application does not create a three-offset legacy cache in
its normal calibration workflow.
This repair preserves the driver's calibration policy and has only been tested
with an in-memory cache and simulated replies.

A call that returns `inst_ok` under a
condition the operator established counts as performed work. A default upstream status method
means unknown readiness, not proof of calibration. Optional deferrable work may
remain without invalidating driver-reported readiness. Neither state proves
physical accuracy or certification.

Live, unpositioned spot and strip reads with `calibrate=True` also handle an
`inst_needs_cal` discovered by the read itself. The helper retains the same
driver, reports `calibration_retry`, reassesses/calibrates, asks for sample or
strip placement again, and retries once. This follows `spotread.c:2313–2323`
and `chartread.c:1625–1645`; DTP20/41/51 can learn the need only from a read
response. Other failures, stored readings, positioned spots and X-Y sheets are
not replayed automatically. Cancellation and the existing operation timeout
still apply. Failed attempts publish no patches.

A driver's `inst_wrong_config` from an unpositioned spot, a strip or a
calibration step — a sensor, dial, diffuser or adapter where that step cannot
work — gets a `sensor_position` instruction naming the position the driver's
`config_enum` gives, and the step is tried again: once while calibrating and once
while reading. It needs no `calibrate=True`,
and a second wrong position fails the step with the driver's text. See
[the frontend's prompt](generic-frontend.md#when-the-sensor-is-in-the-wrong-position).

The native history preserves both assessments. `Measurement.calibration` and
the frontend's `calibration_outcome_json` describe the final applicable outcome.
An unsuccessful recovery or another `inst_needs_cal` invalidates the preceding
ready assessment: the frontend retains `calibration.status="required"` and a
null outcome, with the sequence in `calibration_events_json`.

#### Calibration across the public driver set

The pinned catalog's 37 constructible model types use 21 constructors. Eighteen
constructors assign calibration-assessment and calibration methods. Huey, HCFR
and ColorHug/ColorHug2 inherit upstream's default assessment instead. That
default does not establish that a physical device is calibrated. Spectrocam is
the remaining catalog name, without a public constructor.

The table describes the driver's assessment responsibilities, not physically
verified support or a second application policy table. Source references name
`spectro/` files in the pinned archive; rows follow each constructor's actual
`get_n_a_cals` method, including delegated implementation methods.

| Model types / source | Driver assessment |
| --- | --- |
| DTP20 / `dtp20.c` | Reflective white; a read can set the needed flag. Stored-data retrieval is a separate workflow. |
| DTP22 / `dtp22.c` | Reflective white with its initial-needed flag and auto-calibration policy; calibration uses the physical read switch. |
| DTP41 / `dtp41.c` | Reflective or transmission white, selected by illumination; a read can set the needed flag. |
| DTP51 / `dtp51.c` | Reflective white; a strip response can set the needed flag. |
| DTP92, DTP94 / `dtp92.c` | Black offset; DTP92 additionally offers cell-ratio calibration. Needed flags and model determine the request. |
| Spectrolino, SpectroScan, SpectroScanT / `ss.c` | Reflective white and, in transmission, model-dependent white or variable-white reference. Count/mode changes can require calibration. |
| Specbos1201, Specbos2501, Specbos, Spectraval / `specbos.c` | Refresh calibration in refresh mode; transmission needs a current in-memory variable-white reference. |
| Klein K10 / `kleink10.c` | Black offset is available but never marked needed by this assessment. |
| i1 Display1, i1 Display2, ColorMunki Smile / `i1disp.c` | Black offset or refresh calibration depends on the driver subtype and refresh mode. |
| i1 Display3 family / `i1d3.c` | Refresh calibration depends on subtype, refresh mode and whether a rate is set. |
| i1Monitor, i1Pro, i1Pro2 / `i1pro.c`, `i1pro_imp.c` | Mode/hardware-dependent wavelength, dark and white state, age checks, nonadaptive integration setup and high-resolution dependencies. |
| i1Pro3 / `i1pro3.c`, `i1pro3_imp.c` | Mode/hardware-dependent wavelength, dark and white state, validity checks and nonadaptive integration setup. |
| ColorMunki spectrometer / `munki.c`, `munki_imp.c` | Mode-dependent dark/white validity and age, plus nonadaptive integration setup. |
| Spyder1–5 / `spyd2.c` | Refresh calibration when refresh mode has no established rate. |
| SpyderX / `spydX.c` | Non-ambient black offset depends on valid black state, age and initial-calibration policy. |
| SpyderX2, Spyder2024 / `spydX2.c` | Black offset availability depends on ambient/high-luminance selection; the automatic needed predicate is disabled in this source. |
| EX1 / `ex1.c` | Emission dark depends on interpolation-dark validity, age and initial-calibration policy. |
| SMCube / `smcube.c` | Reflective white depends on validity, age and initial policy; dark and subtype-dependent gloss references are available separately. |
| Huey / `huey.c` | Upstream default assessment; no driver calibration method. |
| HCFR / `hcfr.c` | Upstream default assessment; no driver calibration method. |
| ColorHug, ColorHug2 / `colorhug.c` | Upstream default assessment; no driver calibration method. |

Shared application code configures a mode, asks the driver what is needed,
passes calibration conditions and operator responses through the common method,
and interprets the read status. It does not reproduce these per-driver validity
algorithms. Upstream's `inst_handle_calibrate` handles the interaction loop;
`inst_calt_needed`, `inst_calt_available` and `inst_calt_all` have different
meanings. Our standalone action currently requests outstanding work, so it does
not force available-only work such as Klein black calibration. Neither a
no-work outcome nor cache support proves that a fresh calibration was performed.

#### Upstream initial-calibration suppression

One helper runs per operation, so every reading opens the instrument afresh.
Several drivers ask for their whole calibration at every open, even when the
one they restored from their own file is still valid. The i1Pro 3 sets its wants
at init (`i1pro3_imp.c:666-668`), restores its file (`:911`) and then reports
wavelength and white as needed unless `inst_opt_noinitcalib` is set
(`:1083-1093`). Without the option every i1Pro 3 reading asked for the tile.

A **reading** therefore requests `inst_opt_noinitcalib` with `losecs` 0 --
`spotread -N`'s value (`spotread.c:1538-1540`) -- after init and before the mode,
from the drivers `driver_policy.h` names (`cm_waives_initial_calibration`). What
the driver still reports needed after that is its own validity judgement, and is
negotiated like any other need. A **standalone calibration** does not request it:
it negotiates the work reported needed, plus the requested transmission white.
It can complete with `performed: false` when no work is needed; persistence of
any performed calibration remains driver- and mode-dependent. A refused option leaves the driver's
default, which is more calibration, never less. The outcome's
`initial_calibration_suppressed` says whether the request applied. When it did
and nothing was performed, the record's calibration detail says the reading
relied on the driver's own checks of what it retained.

In pinned Argyll 3.5.0, `inst_opt_noinitcalib(losecs)` changes the driver's
initial-calibration policy. A positive `losecs` is a limit on time since last
open, **not** an expiry time for the calibration. Zero removes that recency
limit. The behavior varies:

| Upstream implementation | Positive `losecs` request |
| --- | --- |
| `i1pro`, `i1pro3`, `munki`, `ex1`, `smcube`, `spydX`, `spydX2` | Set the suppression flag only when last-open age is less than the limit; otherwise leave it unchanged. Return success in either case. |
| `ss` (Spectrolino/SpectroScan/SpectroScanT) | Leave the flag unchanged and return success; the positive bound is not implemented. Zero sets the flag. |
| Remaining public constructors | No option handler; the initialized option path falls through to `inst_unsupported`. |

These are **eight handler implementations covering 13 model types**, not eight
of 38 models. Of the 38 catalog entries, 24 other types have public constructors
without this handler; Spectrocam has no public constructor.

The helper asks eight of the first row's ten types. Each of those ORs the waived
want with a validity test the flag does not touch (`i1pro_imp.c:1596-1630`,
`i1pro3_imp.c:1083-1125` after `i1pro3_check_calib`'s time and temperature limits
at `:1023-1057`, `munki_imp.c:957-985`, `ex1.c:695-702`, `smcube.c:580-587`,
`spydX.c:917-925`). With `losecs` 0 each setter sets the flag unconditionally, so
success does mean it applied. The SpyderX2 and Spyder 2024 are not asked: `spydX2.c`
reads the flag only under `ENABLE_BLACK_CAL`, which it leaves undefined
(`spydX2.c:68,1001–1004`), so asking would waive nothing while the record claimed a
waiver. It is **not** asked of `ss` either. There the flag masks the
need itself, with no validity term (`ss.c:1333-1349`), and `ss.c` keeps no
calibration file, so a waived white would never be measured. Nor does a waiver
cover everything: a non-adaptive emissive reading still chooses its integration
time, which no file holds (`i1pro3_imp.c:1108,1478`), and a transmission reading
still takes its white.

No client-side validity countdown follows from this interface. Expiry stays the
driver's: the API exposes no native calibration dates, board temperature or
expiry timestamp. Unsupported suppression is separate from a failed calibration
assessment: ordinary calibration remains necessary.

Sources in the pinned archive: `inst.h:493,591–597`; `spotread.c:1539,2507`;
`i1pro_imp.c:1554–1656,11114–11122`; `i1pro3_imp.c:1023–1125,4916–4924`;
`ss.c:119–147,803,1326–1358,1854–1902`; `inst.c:208–228,696–728`.
The source regression in `tests/test_argyll_description.py` derives the handler
and model census, executes the extracted option arms and setters with inert
state (including age boundaries and pre-existing flags), and requires the
helper's list to be exactly the first row's types whose driver reads the flag in
code its own `#undef`s leave compiled. It never opens an instrument
or a calibration cache, and it does not establish physical cache reuse. The
substituted instruments model a restored calibration (`retained_calibration`)
and the SpectroScan's masking, so the connected tests fail if a reading stops
asking, if a standalone calibration starts asking, or if `ss` is asked.

### Sheet geometry is the client's, not the instrument's

An X-Y sheet asks the operator to sight fiducials and then navigates by vectors
the **caller** computes. That is deliberate: `chartread.c:1254-1300` turns
located points into vectors with chart algebra that differs for hexagonal and
rectangular layouts — a property of the chart, not of the instrument — so the
helper publishes the points it measured and takes the vectors back. Supporting a
further layout is a Python change and no C at all.

How many points to sight, and how long the first pass is, follow upstream
exactly:

<!-- source: src/color_measurement/instrument.py:sheet_layout -->
```python
def sheet_layout(passes: int, steps: int, patch_count: int) -> tuple[int, int]:
    """``(fiducials, steps_in_first_pass)`` for one sheet.

    ``chartread.c:1090-1107``. A full sheet holds ``passes * steps`` patches. When
    the chart ends mid-sheet the shortfall comes off the first pass ONLY on a
    single-pass sheet (``:1093-1100``: upstream guards that reduction with
    ``paist == 1``); a multi-pass sheet keeps its full first pass and simply reads
    fewer patches. A sheet of one pass also needs fewer sighted points, because the
    pass direction is then the only direction there is:

    * three points -- origin, end of the first pass, start of the last pass
    * two, when there is one pass (no second direction to measure)
    * one, when that single pass holds a single patch (nothing to measure at all)
    """
    if (any(type(value) is not int or not 1 <= value <= MAX_PATCHES
            for value in (passes, steps, patch_count)) or patch_count > passes * steps):
        raise ValueError("A sheet needs at least one patch and no more than passes x steps")
    first_pass_steps = steps
    if passes == 1:
        first_pass_steps = patch_count
    fiducials = 3
    if passes == 1:
        fiducials = 2 if first_pass_steps > 1 else 1
    return fiducials, first_pass_steps
```

`navigation_vectors` then turns the sighted points into the eight doubles the
helper wants, in `chartread.c`'s own order (`ox oy ax ay aax aay px py`). A
client that reimplements this must reproduce both branches; deriving the branch
from however many points happened to arrive would navigate a real chart by a
geometry nobody established.

## Configuration

```python
from color_measurement.instrument import Configuration

meter.measure("emissive_spot", configuration=Configuration(
    highres=True,          # high-resolution spectral mode
    averages=20,           # readings averaged into one
    correction=None,       # see below
))
```

The helper checks capability bits where available and the driver's return status.
Unsupported requests and contradictory readbacks are refused. Interactive
upstream tools sometimes warn and carry on; this API returns a named failure.

A successful filter setter can record a pending physical change. SpectroScan's
setter records the desired filter (`ss.c:1908–1938`); its calibration method
requests the physical change (`ss.c:1479–1489`). The shared worker therefore
verifies filter readback **after calibration, before acquisition**, including
after a read-discovered calibration retry. Without calibration, a mismatch is
refused before reading. For a fixed filter with no setter, matching readback is
required to accept the request. An unsupported getter is distinct from a failed
getter: communication errors retain their native code at `filter_readback`.
Each returned patch is still checked against the requested measurement condition.

Leave `display_type=None` to keep the driver's default selection. Named choices
come from `description.display_types` or a retained native binding, through
Argyll's shared `get_disptypesel(allconfig=1, recreate=1)` interface. Resolve a
choice with `DisplayTypeSelection.from_catalogue(catalogue, choice_id)` and pass
it as `Configuration(display_type=selection)`. A bare index is not accepted.
The generic frontend accepts the choice ID directly in `options.display_type`.
Every catalogue row publishes `native_default`, a boolean copied from the
driver's `inst_dtflags_default` flag (`inst.h:426`). It declares a default
calibration, not the currently selected index. Zero, multiple or unavailable
marked rows are retained as returned; no first-row or unique-default guess is
made. Omitting the option still makes no `set_disptype` call.

Each catalogue carries a SHA-256 identity over the instrument type and ordered
entries. Each choice identity covers its index and native metadata. Installed
CCMX/CCSS identities also cover the actual loaded matrix or spectral samples,
including their grids and normalization, rather than a later reread of the file.
`source_path` records the driver's source path; these are loaded-data identities,
not checksums of entire original files. Builtin and instrument-bank entries have
`identity_scope: "catalogue_metadata"`; they do not fingerprint calibration data
held privately by the instrument. Installed entries have
`identity_scope: "loaded_correction_data"`.

Every operation rebuilds the catalogue and verifies both identities before
calling `set_disptype`. Changes to order, metadata or loaded correction data
refuse at `display_type_stale`, before selection or measurement. Refresh the
catalogue and choose again; the backend never substitutes the first entry or
retries acquisition. Up to 1,000 entries are supported, with one bounded native
frame per entry. Duplicate native labels retain distinct IDs. Selector letters
are neither published nor used.

Invalid UTF-8 disables only the affected entry. Every row retains its original
index and identity, with `available` and `unavailable_reason`. An undecodable
`label` or `source_path` is null; `native_label_bytes` or
`native_source_path_bytes` retains the exact original bytes as lowercase hex.
Valid text stays unchanged and its corresponding byte field is null. No code
page is guessed and no row is dropped or renumbered. Valid builtin and installed
choices remain selectable; an unavailable choice is refused before selection.

The catalogue identity includes all rows, including their undecodable native
bytes. Changing an unavailable entry therefore invalidates an older selection
of a valid entry too. If every row is unavailable, the native catalogue still
reports its successful retrieval and complete contents, while the frontend
offers no explicit choice. Omitting `display_type` keeps the driver's default.
Malformed counts, unbounded strings or invalid calibration data still fail the
complete catalogue; this isolation applies only to text encoding.

`get_disptechi` reports native technology, refresh mode and calibration base.
It does not report a selected index. `applied_choice_id` means the explicit
selection call succeeded; it is separate from this readback. Unwritten fields
and failed/unsupported queries remain null with their native status. A native
zero is preserved. These queries can return driver state rather than a new
physical read. Inherited default getters report unknown technology and default
flags; they do not independently verify a hardware setting.
Selecting an installed CCMX/CCSS and supplying a separate
`correction` is refused, because they are alternative corrections. A builtin
base selection followed by an explicit correction remains supported.

The native fingerprints reuse the BSD-licensed axTLS SHA-256 implementation
from the digest-verified Argyll archive (`ccast/axTLS/sha256.c`); the retained
build report records its source, generated header and notice.

A client sending JSON builds the same object with `Configuration.from_options`,
which is the one validator acquisition itself uses. Every field is a JSON scalar
except `correction`, which is `{"path": …}` or `{"text": …}`; see
[the generic frontend](generic-frontend.md#passing-a-colorimeter-correction).

### Choosing an X-Rite calibration standard

Use `xcalstd` when comparing **reflective measurements**, such as printed
patches, with reference data made under a particular X-Rite or GretagMacbeth
calibration standard. Conversion can reduce differences caused by the standards;
it cannot make different instruments agree exactly. Leave the default `"none"`
unless your comparison or reference data specifies a standard.

| Choice | When to use it |
| --- | --- |
| `"none"` (default) | Leave the driver's default standard unchanged; do not request an override. |
| `"native"` | Explicitly request the instrument model's own native standard. |
| `"xrga"` | Match data in the shared standard used by newer X-Rite instruments. |
| `"xrdi"` | Match data in the older X-Rite standard. |
| `"gmdi"` | Match data in the older GretagMacbeth standard. |

The native option handlers accept only native, XRGA, XRDI and GMDI. Invalid
targets return `inst_bad_parameter` without changing the previous standard.
The application's `"none"` remains an omitted override, not a target sent to
the driver. This guard does not change the conversion equations.

For example, an original i1Pro uses GMDI natively, while an i1Pro 3 uses XRGA.
To compare their measurements of the same printed patch on an XRGA basis,
request `"xrga"` on both, leave **XYZ only off**, and use the same measurement
condition. With `meter` bound to each instrument in turn and both units advertising
an unfiltered reflective condition, a reflective M0 request is:

```python
result = meter.measure(
    "reflective_spot_m0",
    configuration=Configuration(xcalstd="xrga"),
)
```

This changes the standard used for returned reflective values. It does not
replace white-tile calibration, select M0/M1/M2/M3, or correct display
measurements. Follow the instrument's calibration prompts as usual. If the
instrument does not support the requested standard, the operation stops;
choose a supported standard or return to `"none"` and reconsider the comparison.

Keep spectral output enabled when selecting `"xrga"`, `"xrdi"` or `"gmdi"`.
Combining one of these with **XYZ only** is rejected before the instrument is
opened; turn **XYZ only off** to continue. `"none"` and `"native"` do not request
a conversion and can be used with XYZ-only reflective readings, subject to
driver support.

Explicit standard choices apply to live and stored reflective measurements.
For emission, ambient or transmission measurements, leave `"none"` selected;
other choices are rejected before the instrument is opened.

See [Argyll's calibration-standard guide](https://www.argyllcms.com/doc/XRGA.html)
for instrument mappings and conversion limitations.

<details>
<summary>Implementation reference</summary>

[`Configuration`](../src/color_measurement/instrument.py) validates the five
names in [`XCALIBRATION_STANDARDS`](../src/color_measurement/argyll_capability.py).
The [native configuration step](../tools/native_argyll/session.c) skips
`inst_opt_set_xcalstd` for `"none"`; supported request combinations call the
setter for other choices and stop at `set_xcalstd` if it fails.
`Configuration` rejects conversion targets combined with `xyz_only=True`;
its `flags()` method restricts explicit standards to reflective modes. The
native binding repeats these guards before attaching, with stages
`xcalstd_requires_spectrum` and `xcalstd_requires_reflection`.
Python does not implement this numerical conversion.
In the pinned ArgyllCMS 3.5.0 source, `spectro/xrga.c:ipatch_convert_xrga`
converts reflective spectra and recomputes valid XYZ; it skips non-reflective
readings and readings without spectra. Although upstream drivers can accept
environment overrides, `Instrument._open_session` strips all `ARGYLL_*`
environment variables before launching this application's worker. Those
overrides therefore do not apply through this API. The requested option alone
is not a readback of the effective standard. For readers of upstream's own
documentation: `doc/XRGA.html:125-128` and `doc/Environment.html:394-395` name
the override `ARGYLL_XRGA`, but
the seven drivers that read one read `ARGYLL_XCALSTD` (`i1pro_imp.c:512`,
`i1pro3_imp.c:562`, `munki_imp.c:517`, `dtp20.c:362`, `dtp22.c:304`,
`dtp41.c:335`, `ss.c:451`); nothing in the pinned source reads the documented
name. Neither name reaches this application's worker.

The spectrum requirement avoids inconsistent upstream behavior: pristine
i1Pro, i1Pro 3 and ColorMunki discard spectral output before conversion in
XYZ-only mode, so a requested standard is silently not applied; this
application's composed drivers [convert before dropping the
spectrum](../tools/native_argyll/xyz_only_conversion.py). DTP20, DTP22, DTP41
and Spectrolino spot paths instead retain the spectra needed for conversion and
return them despite XYZ-only, so the result shape still differs across drivers
and the refusal stays. DTP20 saved reflective charts and spots also perform
conversion.

For SpectroScan X-Y sheets, the application build applies a
[metadata and retry corrections](../tools/native_argyll/ss_xy_metadata.py) to `ss_read_xy`.
After successful spectrum and XYZ retrieval, it marks the patch as reflective
and records the filter condition using `ss_read_sample`'s policy. This lets
Argyll's existing converter process the sheet results. Exhausted retries now
return the original failure before another patch can overwrite it with success;
invalid XYZ is an error even if its query returned native code zero. The
[focused proof](../tests/test_ss_xy_metadata.py) executes the actual X-Y function
and XRGA spectral conversion with simulated commands. XYZ recomputation uses a
call spy, so this verifies conversion routing and retained metadata, not XYZ
numerical accuracy or physical table operation.

</details>

## Colorimeter corrections

Without a supplied correction, the driver's factory/display-type calibration
remains in use. A display-specific correction can reduce spectral mismatch;
absence of a file alone does not establish invalid XYZ. A CCMX is a matrix;
CCSS contains reference spectra used by a supporting driver. Correction values
must be finite and dimensions must fit the native API.

CCSS calibration requires 2 to 601 bands on a finite, positive, increasing
wavelength grid and a positive finite normalization. Two- and three-band sets
are accepted at any spacing: the application's
[imported native repair](argyll-native-feasibility.md#source-ownership-and-composition)
gives Argyll's four-point interpolator a linear fallback below four samples,
so the worker interpolates them linearly instead of reading unused sample
slots. Whether so few bands describe a display is not judged here.
Single-band calibration is refused rather than treating
a parseable file as an interpolable spectrum. Finite negative sample values
remain accepted, preserving noise around black. Each sample divided by the
normalization must also remain finite; a tiny positive normalization is valid
when its samples scale with it. Installed files receive the same grid/value
safety checks before entering a display-calibration list.

In saved readings, `correction_applied` describes only an explicit CCMX or
CCSS submitted through `Configuration.correction`: `"matrix"`, `"spectral"`,
or `"none"`. `"none"` means no submitted correction was applied. It does not
mean the reading bypassed factory or display-type calibration, and this field
does not identify which factory calibration or display type was used.

```python
from color_measurement.colorimeter_correction import read_correction

correction = read_correction("/path/to/DTP-94_Dell_2408W.ccmx")
meter.measure("emissive_spot", configuration=Configuration(correction=correction))
```

`read_correction` dispatches on the file's CGATS identifier and returns a
`CorrectionMatrix` or a `CalibrationSpectralSet`. Both raise `CorrectionError`
for anything unusable, including a malformed CGATS file — one error channel, as
upstream has. Every correction ArgyllCMS ships parses, and a test asserts that
against the digest-verified archive.

`correction_material_json(text)` uses that same parser to return inert JSON of
the native payload, without reading a file or opening an instrument. Matrix
payloads contain `kind`, all nine `matrix` values, `display_technology` and the
resolved `calibration_base_id`. Spectral payloads contain `kind`, `bands`,
`start_nm`, `end_nm`, `norm`, every row in `samples` and `display_technology`.
These are also the fields used to serialize native correction arguments.
The native desktop chooser can retain `{"text": ..., "source": ...}` in a
frozen option. `source` contains its documentary `path`, exact UTF-8 byte
`sha256` and parsed `material_sha256`. Configuration validates both digests
through the existing correction parser/material projection, without rereading
the documentary path. Desktop renderer requests use an opaque native-held token.
Whitespace, comments, descriptions and documentary metadata do not change this
payload. Clients keep the exact requested text and source digest separately;
payload equality does not establish physical calibration equivalence or that
the correction suits a meter/display.

Four rules a client must carry:

1. **Ambient applicability is driver-dependent.** A native `ambient_ccmx`
   fact permits a CCMX for Klein K10 (`kleink10.c:1410-1427`, `:2495-2510`).
   Other ambient corrections are refused because the corresponding driver
   paths ignore them. K10's matrix determines ambient versus emission scaling;
   a result with the wrong native quantity is refused, never relabelled. The
   descriptor states the matrix each mode needs (`correction_required`,
   `correction_matrix_diagonal_mean`), and `prepare()` refuses a mismatch before
   the instrument is opened.
2. **One correction per session.** A `.ccmx` and a `.ccss` are alternative
   answers to the same question. Do not present them as stacking accuracy
   upgrades; see [i1Display family](i1display-family.md).
3. **The file's `instrument` field is not checked against the device.** It is
   free text that may carry an observer prefix — the shipped
   `DTP-94_1964_10_Dell_2408W.ccmx` says `"1964_10 Xrite DTP94"` — and is not a
   spelling `inst_enum()` matches, so an automatic check would refuse valid
   files. It is preserved verbatim; compare it yourself if it matters.
4. **Choose a correction appropriate to the meter and display.** A CCMX
   characterizes a particular colorimeter/display combination; its accuracy
   is not guaranteed for another instrument. A CCSS contains display spectra
   that a suitable colorimeter combines with its own sensor characterization.
   It can serve another compatible colorimeter for that display. See
   [Argyll's correction-file guidance](https://www.argyllcms.com/doc/ccxxmake.html)
   and the [i1Display family reference](i1display-family.md).

The correction is applied **after** the display type, and that order is
load-bearing rather than tidy: `set_disptype` "clears `col_cor_mat()` and
`col_cal_spec_set()`" (`inst.h:843-845`). Applying it earlier is completely
silent — every call still returns `inst_ok`.

A `.ccss` reaches the helper as a file rather than an argument, because one may
hold 601 bands (`XSPECT_MAX_BANDS`) × N samples, past the 128 KiB Linux allows
in a single `argv` element. The file is written and removed around the session;
CGATS parsing stays in Python.

## Serial identification

A serial path carries **no identity**. `icompaths_add_serial` never writes
`dtype` (`icoms.c:269-299`), an `icompath` is `calloc`ed, and this build
deliberately suppresses the automatic probe. The generic-identification
allowlist in `cm_serial_driver_type`
([`serial_policy.h`](../tools/native_argyll/serial_policy.h)) contains fourteen
native instrument types: DTP22, DTP41, DTP51, DTP92, DTP94, Spectrolino,
SpectroScan, SpectroScanT, Specbos1201, Specbos2501, Specbos, Spectraval,
KleinK10 and SMCube. Their unidentified serial paths cannot match a requested
type until identification is explicitly permitted. Without that permission,
attach fails with `serial_identification_required`. The separately selected
CR types use the [typed serial route](#typed-serial-selection) below.

Learning what is on a serial port means opening it and asking. After the
[authorized description](#selecting-a-device), permit identification for the
acquisition binding explicitly:

```python
frontend = build.bind(description, allow_serial_identification=True)
```

This uses the selected device path and authenticated worker from the build
report. Permission for the description does not automatically grant permission
for the acquisition helper to identify the port again.

What it does **not** do is widen enumeration. Ports are still listed passively,
and only the one path at `path_index` is opened. `new_inst` does the identifying
(`inst.c:636-650`), and a device that turns out to be another family is refused
before its constructor runs — so a wrong guess costs an open and a query, never a
session against the wrong instrument. Failure is
`serial_identification_failed`.

### Typed serial selection

A driver in `serial_policy.h`'s `cm_typed_serial()` is the exception, and needs
no generic serial-identification permission. CR-100, CR-250, CR-300, PR-655 and
PR-670 use this route in a build that composed their contribution series.
`argyll_native_check.py` defines each `CM_CONTRIBUTED_<instType>` from the series
manifest; a stock build compiles none of these types.

For such a type the requested type goes on a **copy** of the one selected path,
and the driver is constructed from it with no generic identification, so none of
Argyll's serial probes run. Its `init_coms` then opens only that port and itself
refuses any other instrument there: the shared CR driver reads `RC Model`, `RC ID`,
`RC Firmware` and `RC InstrumentType` and requires the exact model and type.
The PR driver enters remote mode through PHOTO and verifies its exact model,
serial, firmware and model-specific wavelength grid. Selected PR initialization
therefore requires acceptance of its disclosed remote-mode effect. The
catalog publishes the fact per row as `typed_serial_selection`, and
`InstrumentBuild.describe` takes the route from it.

Because an index alone can move between enumerations, and this route opens the
port without identifying it first, the session route requires the pinned OS path
(`--expected-path`). `bind()` always supplies it. Asking for
`--allow-serial-identification` on this route is refused as not applicable.

## Results

A `Measurement` carries `patches`, `locations`, `calibration`, `stored`,
`confirmed_routes`, `provenance` and the raw `events`. Two properties matter for
clients:

- **A colorimeter's spectrum is `None`, not zeros.** An absent spectrum stays
  absent. An instrument advertising no spectral bit that returns bands anyway
  fails the session (`unexpected_spectrum`) rather than publishing them. A
  spectrometer read with `xyz_only` may still hand the worker a spectrum (the
  SpectroScan table read always does); it is left out, and the reading's
  spectrum is `None`.
- **A reading's units come from its own `inst_meas_type`,** not from the mode
  that was requested. `observation_from_patch` records it as `native_mtype`,
  alongside `native_mcond`, `spectrum_norm` and `correction_applied` — the
  vocabulary `processing.reflectance_fraction` and `spectral_exports` read. A
  driver that leaves the type unset (`inst_mrt_none`, 0) states nothing:
  upstream treats it as compatible with every type (`spotread.c:1923-1935`) and
  labels the reading by its own mode, and so does this, keeping `native_mtype`
  `"0"` and adding `units_source`. `huey.c:1246` does this for every emissive
  reading and `ss.c:829` for every X-Y one. Those two export paths still require
  a stated reflective type.
- **`spectral=True` is the caller's claim, not the instrument's.** A missing
  spectral bit is a fault for a spectrophotometer and normal for a colorimeter,
  and no capability word distinguishes those, so the caller states which it
  expects. A spectral-required caller is refused on a colorimeter
  (`spectral_unavailable`).

`InstrumentError.measurement` retains a complete, validated read when subsequent
cleanup fails, for example `xy_restore` or `xy_release`, or when it completes
during the cancellation grace period after the host deadline. The operation still
failed; instrument readiness is unknown. The retained measurement includes the
native failure events. Failed reads, incomplete batches and inconsistent wire
exchanges do not produce a measurement from partially filled driver buffers.

Saved acquisitions check the matching stored-reading status before reading:
stored chart availability alone cannot authorize retrieval of a stored spot.
Chart/sheet dimensions and patch counts are integers bounded by 4096. Stored
charts also require passes × steps to fit within 4096 slots, including unused
slots in a partial final row. They allow at most 64 sheets whose pass counts
sum to the chart's pass count.

## Wire contract, for a client that is not Python

The helper speaks one JSON object per line on stdout and takes
`v1 <operation-id> <action> [argument]` on stdin. The event names are:

`connected`, `instruction_required`, `calibration_condition`, `xy_location`,
`calibration_ready`, `calibration_retry`,
`result`, `strip_patch`, `xy_patch`, `chart_patch`, `saved_readings`,
`instrument_event`, `native_facts`, `native_reply`, `native_setting`, `native_write`,
`native_spectrum`, `read_allowance_required`, `error`, `terminal`.

The shared decoder preserves signed zero, including the native double token
`-0`, and representable binary64 subnormals. A nonzero real token that underflows
to zero or overflows is refused before event admission. Ordinary integer tokens
retain their integer type for version, sequence, count and index checks; `-0`
retains its real-number sign and cannot stand in for an integer header.

Operator replies are `acknowledge`, `skip`, `cancel` and `navigate`. `skip` is valid only
when the prompt says `skippable`; the driver decides that, not the client.

The `connected` event confirms what the binding actually established: the
driver-reported `serial` and `serial_encoding` pair, the `mode` `check_mode`
accepted, the three capability words, the negotiated `trigger`, whether the
instrument is `spectral`, and
`correction` — `none`, `matrix` or `spectral`, **as applied rather than as
requested**. `set_disptype` "clears `col_cor_mat()` and `col_cal_spec_set()`"
(`inst.h:843-845`) without saying so, so a caller that passed a `.ccmx` cannot
otherwise know whether the reading was corrected. A requested correction the
driver did not apply fails the session.

The serial marker is `utf8` for valid UTF-8 text or `bytes` when each JSON text
code point preserves one native byte. An absent serial has null for both fields.
The Python client validates the pair before retaining it in measurements and
observations; an explicit serial pin must match the text and the `utf8` marker.
See [device serials](data-format.md#device-serials) for the saved-record form.

Drivers with a support adapter (`native_facts.h`) emit `native_facts`,
`native_reply`, `native_setting`, `native_write`, `native_spectrum` and
`native_raw` as applicable. The composed CR/PR contribution covers CR-100,
CR-250, CR-300, PR-655 and PR-670. After each attempt, including refusal, the
adapter emits the driver's retained state while the instrument is alive.
CR matched and range records use `native_record`; PR settings reports use
counted accessory/aperture events. Raw bytes are separate from parsed
values and publication.

The head counts each list. Spectral events carry at most 100 values, raw events
carry at most 1,024 received bytes as hex, and individual records have separate
events, keeping lines within the protocol's 64 KiB native helper cap. The spectral
chunk-count ceiling is derived from the driver's largest retained vector: 100
chunks for the CR family (10,000 values), and three for the PR family (201 values).
The PR family ceiling covers both models; their separate grid contracts remain
101 samples for PR-655 and 201 for PR-670. These are software protocol bounds;
the [shared constants](../src/color_measurement/native_protocol.py) and
[PR grid contracts](../src/color_measurement/argyll_description.py) supply them
to `instrument.native_facts`. The client refuses oversized chunks, gaps,
reordering and count mismatches. A
complete raw vector can survive publication refusal without claiming a usable
`Spectrum` or verified units. See the [retained record format](data-format.md)
for raw bounds, mapping refusals and capture-start identity after quarantine.

`Configuration(xyz_only=True)` -- the frontend option `xyz_only` -- binds an
instrument that could return a spectrum without `inst_mode_spectral`, so a driver
whose spectral read costs time and checks of its own (the CR-250's RM Spectrum and
unit check) skips them. It is this host's request, not upstream practice:
`spotread` adds the bit whenever it is advertised (`spotread.c:1586-1588`). On an
instrument with no spectrum it is refused as `xyz_only_unavailable`, and it cannot
be combined with `spectral=True`.

The client passes its session timeout as the upper bound `--read-allowance-s`.
Immediately before a spot read by a driver that takes an allowance (one with a
support adapter; `native_facts.h`), the worker emits `read_allowance_required`;
other drivers read without the exchange.
The client answers `read_allowance <seconds>` using its actual remaining deadline,
including the existing pause for operator instructions. This exchange is internal;
it creates no operator prompt. The worker conservatively deducts the whole
exchange duration before handing the driver a positive allowance. Neither side
assumes the two processes started at the same instant or share a clock epoch.
Cancellation or an expired client deadline answers `cancel`, before the read.

Only time spent answering an operator instruction pauses the acquisition
deadline; writing its reply does not. If a reply meets a closed pipe, the client
drains the worker's queued ending and retains its stage, native code and text,
with the failed write as the exception cause. After a valid completed ending
without a native failure, the write remains the error. An invalid or incomplete
ending retains its protocol, deadline or cleanup failure and the failed write.
A failed operator or presentation callback retains its original exception
object, including through EOF or grace expiry. A subsequent failed cancel write
is attached as its cause; multiple ending or cleanup diagnostics are retained
in an exception group. Unrelated I/O errors do not count as evidence that the
worker exited.

The CR driver, with this application's transform composed onto it
(`tools/native_argyll/cr250_host.py`; the Argyll contribution carries none of
this), counts the granted allowance from `read_sample` and, just before M,
compares what remains with `capture.required_s`: its measurement budget plus
bounded capture reads after M. It retains `required_s` and the granted
`host_allowance_s`, including on refusal. This reserves command time;
it does not guarantee completion despite transport failure, process scheduling,
cancellation or forced termination. Closing the helper is not a device abort.

A failure the **driver** returned -- `init_coms`, `init_inst` and the four reads --
also carries `native_text`, the driver's own `interp_error` for the whole
`inst_code` as upstream prints it (`spotread.c:397`), cut at 512 bytes with
`native_text_truncated` saying whether it was. It is never parsed for facts.

An `error` event carries a `stage` and a `native_code`. During normal worker
cleanup, the helper closes its instrument and emits a `terminal` event. Keep
reading within the client's deadline so cleanup and the worker's exit status
can be retained. Forced termination or a broken connection can prevent delivery
of the terminal event. The worker emits
stages through `fail` and `native_fail` in
[`session.c`](../tools/native_argyll/session.c); attachment reasons come from
`cm_attach_stage` in [`session_attach.c`](../tools/native_argyll/session_attach.c).
The table lists stages that need a specific client response. `attach` is the
fallback when attachment fails without a specific reason, including failure
to enumerate paths.

| stage | meaning |
|---|---|
| `no_path_at_index` | nothing enumerated at that index — re-enumerate |
| `selected_path_changed` | the index no longer names the pinned OS path; re-enumerate and select the device again |
| `expected_type_has_no_serial_driver` | the requested type cannot use generic serial identification; refuse before opening or probing the port, even when identification was permitted |
| `attach` | attachment failed without a more specific reason; retain the error and do not infer a connected device |
| `different_instrument_at_index` | the index moved; re-enumerate, do not retry |
| `serial_identification_required` | a serial device needs `--allow-serial-identification` |
| `serial_identification_failed` | opened and asked; it is not the requested family |
| `serial_identification_not_applicable` | identification was requested for a typed serial type, which is never identified |
| `typed_serial_requires_path` | a typed serial selection without the pinned OS path |
| `typed_serial_construction_failed` | the driver for the requested type could not be constructed on that path |
| `serial_mismatch` | the serial pin did not match |
| `spectral_unavailable` | the caller required a spectrum this instrument has none of |
| `facilities_unavailable` | the workflow needs a capability this instrument lacks |
| `no_trigger_available` | no trigger this ladder can raise |
| `correction_matrix_unavailable` / `correction_spectral_unavailable` | correction asked of an instrument that advertises neither |
| `correction_not_applied_to_ambient` | a correction the ambient driver path cannot apply |
| `ambient_matrix_required` / `ambient_matrix_on_emissive_route` | the Klein K-10 types every reading by the matrix it applies (`kleink10.c:1422-1425`): an ambient route needs an illuminance-scaled matrix, and an emissive one must not have one |
| `calibration_not_skippable` | the driver did not offer to skip that condition |

The helper's own command line begins with four fixed pairs, which every attach
unit takes — a substituted instrument takes exactly the command line a real one
does:

```
--usb-index <n> --expected-serial <s|""> --instrument <name>
--allow-serial-identification <0|1> [--expected-path <selected OS path>] [ladder flags…]
```

## What the helper must be built from

The generic worker only constructs instruments its library links. The current
broad recipe links **37 of 38** stock types, or **42 of 43** with the combined
CR/PR contribution. Spectrocam remains unlinked; a catalog name alone is not
construction support. Check `report.json`'s `generic_worker_build_link.linked_instrument_types` before
trusting a worker, and see [native build and software checks](argyll-native-feasibility.md)
for the selected contribution and platform boundaries.

## Status

The stock broad selection includes 37 constructible instrument types; the
combined CR/PR contribution adds five. Native build recipes cover macOS, Linux
and Windows x64 MinGW-w64; each platform needs its own executed software checks.
See the [transport and access boundaries](argyll-native-feasibility.md#platform-and-device-access).
Firmware-loading Spyder1/2 initialization remains refused. Substituted personas exercise all four
read verbs and calibration, including i1Pro2 and i1Pro3. **No physical route is
validated.** See the build report for its actual linked set and exercised cases.
