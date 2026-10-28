# Generic instrument frontend

## Opt-in native diagnostic provenance

`InstrumentFrontend.prepare` accepts native-only keyword arguments
`native_diagnostic_target`, `native_debug_level` and
`native_diagnostic_limit_bytes`. Level is an integer in `[0, 8]`, with zero
disabled; the byte cap is an integer in `[1, 1048576]`, defaulting to 65536.
A positive level requires a native owner's destination and an authenticated
acquisition worker. Preparation opens no file or process. Descriptor
`limits.native_diagnostics` declares availability and limits, separately from
measurement options. The selected logging level does not change the request,
mode, capabilities or scientific settings.

The shared supervisor exclusively creates the destination. Its stderr reader
retains a bounded original in memory and always continues draining. A stream
within the cap is retained whole. A longer stream retains its first
`floor(cap / 2)` bytes and last `ceil(cap / 2)` bytes, so setup context and late
failures survive. A one-byte cap retains the final byte. One owned optional
writer waits for the closed selection, then writes its exact concatenated bytes
without holding the collector lock during filesystem I/O. The shared session's `--native-debug-level` sets
Argyll's logger before selected attach/initialization; ordinary selection,
serial, initialization and transport guards still apply. Production workers
remain subject to separate hardware authorization. Logging is disabled by default.

The held operation's `native_diagnostics()` returns auxiliary metadata even
when there is no valid terminal result. It reports prepared/complete/incomplete
or not-started state, basename, level/cap, received/retained/written byte counts,
truncation, original SHA-256, initial file SHA-256, observed stderr drain,
file completion, whether a worker started, and an independent error.

If worker creation fails after reserving an empty file, successful file closure
reports `not_started` with the original startup reason, zero counts, null digests
and no drain/file-completion claim. The empty reserved file may remain. Failed or
unconfirmed auxiliary cleanup retains `incomplete` facts and the original control;
it cannot become `not_started` by relabeling.

Diagnostic metadata uses `schema_version=2`, `retention="head_tail"`, and `segments` containing
at most two original-stream `{offset, length}` ranges. Empty streams have no
ranges; uncapped streams have one contiguous range. The artifact contains those
ranges in order, with no inserted gap marker. Its SHA-256 identifies those exact
bytes; the initial file digest identifies the written prefix of that artifact. Before
publication, `sink_stopped` separately states whether the auxiliary writer ended.
An unfinished sink has `file_complete=false` and a null initial-file digest;
the original memory bytes remain available without waiting on disk. Its owner
must preserve that writer and target and must not replace its active path.
The writer has a 0.25-second cleanup wait; this does not qualify disk completion.
Acquisition software stop is separate from auxiliary sink ownership. Before
closure the digests are null. `native_diagnostic_bytes()` is native-owner-only
and returns the exact closed bounded original, including after a write,
sync or close failure. Those bytes never become numeric observations or a
renderer reply. A caller can preserve them through its existing save/recovery
owner without claiming that the original file publication succeeded.

Filesystem or drain failures leave explicit incomplete facts. A cap cut is
separate from failure: a successfully drained, synchronized head/tail artifact can
be complete with `truncated=true`. Cleanup preserves the first control object.
Neither a transcript nor an observed software stop establishes instrument
readiness, measurement accuracy or physical-device validation.
The ordinary error preview retains the last 4,096 stderr bytes so a crash tail
survives verbose logging. This preview is separate from the optional captured
original and does not change its content or digest.
An stderr reader failure remains in the session's `stderr_error` even without
optional capture. Error previews retain the original tail and explicitly label
the incomplete drain. This auxiliary failure does not reinterpret stdout events.

One polled frontend over linked Argyll instruments, including i1Pro: request,
poll, respond, cancel and the common `OperationResult`. A linked driver is not
proof that every workflow is implemented or physically validated.

## Why it can be generic

Argyll exposes a common `inst *` vtable. The frontend derives candidate modes
and controls from three capability words; native `check_mode` and `set_mode`
confirm each request. Source-backed driver facts cover semantics the words do
not express. The native `ambient_ccmx` fact, for example, controls whether a
matrix correction may apply to ambient readings. Clients do not maintain their
own instrument-identity table. Argyll applications themselves have bounded
device exceptions; zero device names is not an architectural requirement.

| word | becomes |
|---|---|
| `mode_capabilities` (`inst_mode`) | candidate modes and mode flags |
| `trigger_capabilities` (`inst2_capability`) | facilities and options |
| `filter_capabilities` (`inst3_capability`) | averaging and reflective conditions |

### It is advisory until the instrument confirms

`inst.h:305-306` says the capability macro is not definitive — `check_mode()` is.
Every route the descriptor offers is one the instrument *advertises*; the ladder
still confirms the requested configuration at bind time and refuses what the
driver rejects. Published fixtures retain native refusals as outcomes.

Each mode has `baseline_native_mode`, with scope
`initialized_before_mode_configuration`, `default` and `xyz_only`. Each non-null
selection contains its exact `mode_word`, `status`, `native_code` and `reason`.
`default` means the current descriptor's unmodified output selection, with a
spectrum when advertised; it does not select a preferred geometry. `xyz_only` is
null when there is no spectrum to suppress. The raw `baseline_mode_checks` fact
is retained at descriptor level.

| status | reason | meaning |
| --- | --- | --- |
| `confirmed` | null | That exact word returned native code zero at initialization |
| `rejected` | `native_mode_rejected` | Native code belongs to `inst_unsupported` |
| `unknown` | `check_mode_unavailable` | The native method was absent; its code is null |
| `unknown` | `native_check_inconclusive` | Another nonzero code; raw value is retained |
| `unknown` | `baseline_not_observed` | A directly constructed frontend was given masks without checks |
| `unknown` | `mode_not_checked` | This exact word was absent from the initial candidate set |

The native description helper collects these checks in its initialized lifetime;
the acquisition helper collects the same facts before selecting a mode and
carries them on `connected`. Later capabilities may differ from that initial
mask, so a newly advertised candidate remains unknown. A caller supplying only
raw masks to `InstrumentFrontend` supplies no observation, not an inferred pass.
Acquisition's existing retained-calibration request precedes these checks;
the snapshot does not describe calibration state or promise that no earlier
initialization or calibration-policy option ran.

Confirmation covers the exact baseline word only. It does not certify high
resolution, filters, display type, correction, calibration or present readiness.
Spectral acceptance does not imply XYZ-only acceptance, and telephoto acceptance
does not confirm ordinary spot geometry. There is no automatic cross-geometry
fallback. Baseline status does not change `available`, rank modes, or gate a
future request: native acquisition checks remain authoritative. Adding or changing
these facts changes the descriptor's `capability_id`.

## Selection and acquisition

```
enumerate  ->  name       ->  describe        ->  offer            ->  drive
candidates     build          capabilities()      InstrumentFrontend  request/poll
(OS paths)     catalog        (three words)       descriptor
```

`color_measurement.discovery.InstrumentBuild` is all five in one object.

```python
from color_measurement.discovery import InstrumentBuild

build = InstrumentBuild.from_report("…/report.json")   # both workers, one build
catalog_json = build.catalog_json()                   # Cached build facts; no enumeration.
for unit in build.attached():                          # Explicit platform enumeration.
    print(unit.index, unit.name, unit.transports, unit.attachable)

chosen = ...                                           # the operator picks a row
frontend = build.bind(build.describe(chosen))          # opens exactly that one
```

`unit.transports` labels the native serial, USB, HID and Bluetooth port bits
and the fast serial attribute from `icoms.h:88-100`. A fast serial USB path can
carry both serial and USB bits. `unit.transport` retains the complete original
mask; these labels describe connection metadata, not tested device operation.

`from_report()` with no argument reads the path from **`CM_ARGYLL_BUILD_REPORT`**.
There is no search and no built-in location: a report is produced by a run the
operator chose to make, lives wherever they put it, and authenticates the workers
beside it. Guessing at one would be the same mistake as searching for a helper.

Per-type facts are public on `InstrumentBuild`, read from the same catalog
and opening nothing: `typed_serial_selection(expect)` says whether a serial type
is selected without identification (so `allow_serial_identification` does not
apply), and `read_allowance_floor(expect)` gives the cheapest driver's floor
below. `binding_policy(expect)` publishes all word-specific floors and their
maximum. A client reads the selection policy before describing and the binding
policy before choosing an allowance; none of these calls opens an instrument.

The catalog's `read_allowance_floor_s` is the smallest allowance the
driver accepts for one read, in any mode it offers: for the CR-250 174 s and for
the CR-100 165 s -- the 120 s budget floor (`measurement_budget_floor_s`, read
from the composed header) plus the capture reads after M that the cheapest mode
cannot skip. The allowance is this application's, not Argyll's: the build composes
it onto the driver (`tools/native_argyll/cr250_host.py`), and `cr250_host_test.c`
holds its count of reads after M to the reads the driver actually makes. The
build asks that composed `cr250_required_floor_s` for every mode word the worker
can bind and publishes the least, rather than repeating its formula. The catalog
also carries the floor of every exact mode word. `bind()` and the equipment setup
facade share `validate_binding_timeout`: an explicit allowance must exceed the
maximum of the type floor and every published word floor, because mode/output
selection can follow binding. An omitted binding allowance uses the declared
application default and margin from `binding_policy`; exceeding that floor is
not a completion guarantee. Separately,
`prepare()` holds a read to the floor of the word it binds -- 182 s for a spectral
CR-250 spot read, 177 s for the same read with `xyz_only` -- which the descriptor
publishes per mode as `limits.read_allowance_floor_seconds_by_mode`. At or below
it the driver is certain to refuse, so no worker is started.
Exceeding it is necessary, **not sufficient**, and passing `bind()` does not mean
a read will measure. A spectral read, an instrument that has the optional
settings the cheapest path lacks, the driver's preflight reads and writes, any
wait for an operator trigger, and a longer exposure ceiling all consume or raise
what one read needs. A timeout just above the floor can still be
refused at run time -- always before M, recording `required_s` and
`host_allowance_s` -- which is the correct outcome, not a fault.

### It takes a build report, not a helper path

Two reasons, and both are load-bearing.

A bare path authenticates nothing — a matching basename is not a worker. The
report names each executable's SHA-256, and `from_report` checks both from one
validated report buffer. The published report digest identifies those same
bytes, even if the pathname changes afterwards. Direct construction requires an
explicit session-worker digest; a report-bound client also requires that report's
sibling worker and recorded digest. Acquisition rechecks the digest before every
process launch. Offline descriptors and the demo replay do not launch a process.

A bare path also cannot NAME anything. Candidates carry `dtype`, an `instType`
**ordinal**, and `insttypes.h:29-69` assigns the enum no explicit values, so an
ordinal is a position upstream may reorder. The naming comes from the build's own
catalog in that same report. Without it you have integers.

There is a third reason the pairing matters. Enumeration and acquisition are
different processes, and an index only carries between them if both enumerate the
same set — which they do not automatically. `usb_get_paths` skips any device
`inst_usb_match` answers `instUnknown` for (`usbio_ox.c:99`), and that filter is
compiled from the linked driver set. The current broad build uses the same
selected catalog for both processes. One report authenticates both workers;
workers from different builds cannot safely share numeric indices.
Acquisition also verifies the selected
OS path before construction; a stale numeric index cannot silently choose a
different path. A serial learned later may refine an unpinned identity, but may
not contradict a pinned serial or switch units between retained patches.

### Enumerate and describe are separate, and a client must keep them separate

On macOS, `attached()` reads transport metadata from the
IORegistry — USB at `usbio_ox.c:34-98`, HID at `hidio.c:288-297` (upstream's own
comment: *"Enumerate the devices (doesn't seem to have to open)"*), serial through
`kIOSerialBSDServiceValue`. This is not a portable promise of unopened devices:
Linux's `usbio_lx.c:65-77` opens device nodes and reads descriptors; Windows'
`usbio_w0.c:99` takes query handles. Enumeration is an explicit hardware action,
separate from instrument initialization, and is never run by software checks.
The broad build removes Linux serial candidate opens and automatic serial
identification on each platform, and suppresses Windows USB enumeration's
kernel-debug-level write. Selected-port identification remains explicit.
See the [platform boundaries](argyll-native-feasibility.md#platform-and-device-access)
for the pinned Linux HID limitation and host dependencies.

`describe()` **initialises the instrument**, and that is not free. Five of the
thirty-eight change their visible state doing it — Huey, ColorHug, ColorHug2,
i1Disp3 and Smile all flash their indicator LEDs (`huey.c:1130` plus the
eight-step sequence at `:1145-1168`; `colorhug.c:653-666`; `i1d3.c:3079`). Those
carry `initialization_changes_indicator_leds`, and `describe` refuses them until
the caller passes `accept_disclosed_initialization=True`.

**So a device list is built from `attached()` alone.** Opening every unit to find
out what it is would flash five models' lights every time someone pressed
Refresh.

An unassessed initialisation — a driver this build has not read — takes a
*different* acknowledgement, `accept_unassessed_initialization=True`. The two are
deliberately not one flag: agreeing that a known LED may flash is not agreeing to
open a driver nobody has read, and one flag covering both would be the same
fail-open that `attachable` used to have.

A **serial** unit is judged by what it will turn out to be, not by the bare port.
An unidentified path's own facts describe `instUnknown`; the instrument that gets
initialised is the one named in `expect=`, so that type's disclosure is applied
before anything is opened, and the identified record carries it afterwards. Both
matter, because `bind` initialises that model again on the session worker. The
listing reports the port itself `initialization_assessed: false`, since nobody
assesses `instUnknown`. That is not a gate, so its `opening_requires` is `expect`,
and a type this build has read, such as the CR-250, opens with `expect=` alone.

### Refusals

| refusal | when |
|---|---|
| `NativeRefusal` | the helper answered and the answer was a refusal; carries its own `stage`, `native_code` and `refusal_reason` |
| `ValueError` naming the flag | a disclosed or unassessed initialisation, not yet accepted |
| `ValueError` naming `expect=` | an unidentified path with nothing said about what it should be |
| `ValueError` naming `allow_serial_identification` | identifying a serial port means opening it |
| `ValueError` from the report | either worker missing, executed, or not matching its digest |

Nothing attached is an empty tuple, **not** a refusal. A failed enumeration is a
`NativeRefusal` and never an empty list — a picker that shows "no devices" for a
helper that failed to enumerate tells an operator holding an instrument that it
is not there.

### Refreshing

A second enumeration constructs anew; frontends are not reused. `index` is a
position in **one** enumeration, HID paths are added before USB ones
(`icoms.c:489-497`), and a serial only exists for a unit already opened — so no
key survives a refresh reliably. A unit that vanishes under a running operation
surfaces as a failed `OperationResult` carrying the stage it failed at; no
reading is fabricated and no partial batch is published.

`device_id` defaults to the enumerated **path**, because two units of one model
share a model name and would otherwise write records nobody can tell apart. A
path is unique within its enumeration and is not stable across a replug, so a
client keeping records across sessions should pass its own label.

## Client contract level

`color_measurement.CLIENT_CONTRACT` is an integer a client compares for
**equality** before using the backend. It is incremented in the same commit as
any client-visible change to descriptors, requests, events, records or the
imported API, and this section names the new level and what it adds. Importing the package loads no submodule and touches no device, so a client
can read the level first. Descriptor, request, event, observation and operation
schema numbers remain 1; the TI3 projection schema is 3 and its result schema is 2.
Neither those numbers nor per-run fixture identifiers replace this shared
client-level equality check.

The current level is 49. It adds `BatchApplication.snapshot().can_start_new_batch`,
the owner eligibility rule shared with `new_batch`: a durable Finish with no
active, unsaved or unresolved companion or native diagnostic work. Clients must
use the current contract level before consuming the snapshot.
It retains the capability-gated native settings workflow,
typed changes and validated retained settings receipts described below. Live
application companion formats retain the authoritative
`refresh_pending` witness for owned deferred/queued refresh, preserving existing
stale/failure/retry and exact discard facts. It includes explicit bound-frontend/mode/options batch
creation, strict batch schema 5 and the current
[session selection/quantity bridge](desktop-session.md), including qualified
relative reflective previews through the shared
`presentation.qualify_reflective_xyz` owner. Their common relative Y=100,
original native context, theoretical source white and corresponding display
encoding remain separate from absolute emissive previews and native gamut facts. It retains the imported `processing.compare_xyz` API and
independent native-Y percentage / CIE 1976 coordinate-distance results in desktop
analysis, with exact XYZ context guards and explicit unavailability. See the
[native XYZ comparison](science.md#native-y-change-and-cie-1976-coordinate-distance)
and [current desktop response](application.md#persisted-quantitative-comparison-context).
It retains application-owned numbered
sample preview and automatic next/Repeat, mandatory draft identity/version,
persisted quantitative comparison reference/white assumptions and version-fenced
desktop analysis. Instruction-required events expose their actual
`response_timeout_seconds` budget, without a countdown or wall-clock deadline.
Earlier batch schemas and request envelopes are refused.
The current level also identifies structured no-dispatch results through
`dispatch_failure_stage`, `stop_requested_by` and `software_stopped=true`, as
specified in [results](#results).
It requires staged `frontend.prepare()` and
`operation.dispatch()`: callers retain the validated operation and identity
before any device I/O can start. The replaced combined frontend start is absent.
It includes setup-only `chart_targets.expected_reference_json`
for explicitly declared expected XYZ/Lab illustrations from a retained TI2.
The raw target packet and acquisition schemas are unchanged. Its TI3 projection
requires `ordinary_record_ids`,
an ordered subset distinguishing new acquisitions from retained chart history.
It includes source-backed per-mode `physical_chart`
assessments, the Demo-only `demo_chart_sample` request
context and raw synthetic sample context documented in
[equipment previews](equipment-demo.md), plus
`colorimeter_correction.correction_material_json(text)` for comparing parsed
native correction payloads without opening an instrument, plus bounded
[TI2 import and source-aware TI3 output](data-format.md#import-a-ti2-target).
Clients adopt
the current level together; earlier development interfaces are not retained.

## Descriptor

### Native settings requests and retained recovery

A bound worker advertises `native_controls` only when compiled metadata and its
verified current native identity both establish support. A model label alone
cannot enable it. Its option has `type="object"`, `default=null` and ordered
`members` in the existing typed layout-field shape (`key`, `label`, `help`,
`type`, `required`, `default`, numeric limits and unit). Blank members are omitted;
zero remains explicit. These limits describe representation. Current model
availability, numeric limits and inventories remain with the native adapter.

The zero-patch `native_controls` workflow requires `retained_patch_v1` and
`layout={"action":"read"}` or `{"action":"apply"}`. Read has no changes;
apply requires `options={"native_controls":{...}}`, including an explicit empty
object. Ordinary `spot` accepts the same changes object and applies it before
measurement in the same native lifetime. Other workflows refuse settings changes
before dispatch. The descriptor's `native_controls` packet retains adapter name,
ordered field names, pending recovery mask and nullable cached current report.
Original raw/parsed replies stay in the complete terminal receipt rather than
the descriptor. Reads cannot clear recovery debt, and dispatch rechecks current
debt and descriptor identity. A failed operation whose worker bound keeps every
dispatched field pending unless its native ending, or a retained completed
reading, carries the validated report. See the [shared API](instrument.md#typed-native-settings)
for recovery and ACK/readback distinctions.

`InstrumentFrontend.describe_json()` returns one object whose `capability_id` is a
SHA-256 over its own content, excluding the identifier itself and the build-receipt
`provenance` inside `current_binding_identity`. The runtime
`native_controls.current` report and `native_controls.pending` recovery mask are
also excluded. A settings read, reported recovery debt or receipt-only rebuild
therefore keeps the capability ID. The complete descriptor still exposes these
observations. Adapter availability, driver, ordered fields, current native
identity and all other capability content remain part of the digest.

Object member names are sorted recursively for hashing; changing member order
does not change identity. Array order and included field values remain
significant. Recomputing the ID checks this logical content, not the excluded
observations, and is not a signature. **A request carries that ID and is refused
if it does not match current capabilities.** Equal capability IDs do not prove
equal instrument settings or worker receipts. The desktop's selected-source
comparison retains build provenance while ignoring only the two runtime fields;
prepare and dispatch independently enforce pending recovery. Acquired chart
settings come from [original reading context](chart-context.md).

```jsonc
{
  "schema_version": 1,
  "contract": "argyll_generic_instrument_v1",   // descriptor contract for instrument and Demo clients
  "capability_id": "b28ef47d…",
  "device_id": "X-Rite i1 DisplayPro, ColorMunki Display",
  "device": { "device_id": …, "model": …, "backend": "argyll-native",
              "backend_version": "3.5.0", "serial": "CM-COLORIMETER-0001",
              "serial_encoding": "utf8" },
  "calibration_setup": [],     // the manuals' calibration setups, each with its condition
  "provenance": "physical" | "simulated" | "replay",
  "synthetic": false,      // simulated values are INVENTED; replay values were measured
  "live": true,            // only `physical` is live
  "mode_capabilities": 93464, "trigger_capabilities": 234881792, "filter_capabilities": 0,
  "facilities": ["ccmx", "ccss", "disptype", "prog_trig", "user_trig"],
  "mode_flags": ["colorimeter", "emis_nonadaptive", "emis_norefresh_ovd"],
  "spectral": false,
  "accepts_correction": true,
  "filters": [],
  "modes": [ … ],
  "options": [ … ],
  "instructions": [ … ],
  "limits": { "max_request_bytes": 16384, "max_event_bytes": 16384,
              "max_patches": 4096, "max_patch_id_bytes": 128,
              "max_response_timeout_seconds": 600 }
}
```

The three raw words are published so a client can decode a capability this
project has not named yet, rather than waiting for a release.

### `device_id` identifies an INSTANCE

It defaults to the native binding name, which is unambiguous for one attached instrument
and **ambiguous for two of the same kind**. A caller holding one
`discovery.DiscoveredInstrument` per unit passes its own label:

```python
InstrumentFrontend(meter, device_id="bench-left", …)
```

Saved records then tell two identical instruments apart. `model` uses the
canonical Argyll `inst_name()` label; the spelling used to bind a native worker
remains separate. For example, Spyder 2024 is displayed as "Datacolor Spyder
2024", while pinned Argyll's `inst_enum()` requires "GretagMacbeth Spyder 2024".
Both names resolve through the backend's single identity table. Paths, native
type symbols, serial pins and supplied device IDs are preserved.

### `synthetic` and `live` — what `replay` means

Three provenances, two independent facts:

| provenance | `synthetic` | `live` | meaning |
|---|---|---|---|
| `physical` | false | true | measured now, by an instrument |
| `replay` | false | false | measured earlier; real numbers, not a live reading |
| `simulated` | true | false | **invented**; never to be reported as a measurement |

A client showing a "live" indicator wants `live`. One labelling data
trustworthiness wants `synthetic`.

### Modes

Reflective condition names require the matching filter capability, including M0's
`filter_none` and the manufacturer's `filter_Custom`. A fixed UV-cut instrument
offers M2 and its unqualified reflective routes, without offering M0. A switchable
instrument offers each condition its driver advertises. An unqualified route or
`filter: "unknown"` leaves the existing filter unchanged. Explicit unavailable
filter options are refused before a worker starts; native configuration and actual
filter readback remain authoritative when an operation runs.

The shared mode labels explain the ISO 13655 condition:

| Condition | Meaning |
| --- | --- |
| M0 | Legacy illumination; UV content is unspecified. |
| M1 | D50 illumination with controlled UV. |
| M2 | UV excluded. |
| M3 | Polarized measurement with UV excluded. |

These descriptions follow [Konica Minolta's explanation of ISO 13655](https://sensing.konicaminolta.us/us/learning-center/color-measurement/iso-demystified/)
and the [X-Rite eXact manual, measurement conditions](https://www.xrite.com/-/media/xrite/files/manuals_and_userguides/e/exact_user_guide/exact_user_guide_en.pdf).
A driver capability names a requested condition; it does not establish physical
ISO conformity. The native filter setting is separate from Argyll `spotread -I`
fluorescent-whitener compensation and simulated illumination.

```jsonc
{
  "mode": "emissive_spot",
  "label": "Emissive spot",
  "axes": { "illumination": "emissive", "action": "spot", "modifiers": [],
            "condition": null, "route": "emis_spot", "mode_flags": [] },
  "canonical": true,
  "equivalent_to": "emissive_spot",
  "expected_measurement_type": 1,
  "expected_measurement_type_name": "emission",
  "expected_quantities": { "xyz_unit": "cd/m^2", "illuminant": "none (emissive)",
                           "scale": "absolute; Y is luminance",
                           "quantity": "spectral_radiance",
                           "spectrum_unit": "mW/(m^2 sr nm)" },
  "operator_setup": { "text": "Swing the diffuser arm to the rear, then hang the unit on …",
                      "document": "X-Rite i1Display Pro Quick Start Guide, EODIS3-550 (12/10)",
                      "location": "PDF p. 21", "quote": "To mount the device on your display, …",
                      "official": true },
  "physical_chart": { "kind": "display_only", "assessment": {
                      "text": "The documented setup measures a display or projector screen. …",
                      "document": "X-Rite i1Display Pro Quick Start Guide, EODIS3-550 (12/10)",
                      "location": "PDF p. 21", "quote": "To mount the device on your display, …",
                      "official": true } },
  "calibration_setup": [],          // this mode's possible, sourced calibration steps
  "workflow": "spot",
  "workflows": [ { "workflow": "spot", "available": true, "unavailable_reason": null,
                   "min_patches": 1, "max_patches": 1,
                   "layout_keys": [], "required_layout_keys": [] } ],
  "requires_positioning": false,
  "correction_required": false,
  "correction_matrix_diagonal_mean": null,
  "stored": false,
  "available": true,
  "unavailable_reason": null
}
```

**`correction_required` / `correction_matrix_diagonal_mean` — a reading typed by
its matrix.** The Klein K-10 labels every reading by the correction matrix it
applied, not by the mode asked for: ambient when the matrix diagonal's mean is
above 5.0, emission otherwise (`kleink10.c:1422-1425`). For such a driver each
mode states the mean its matrix needs, `{"above": 5.0}` for an ambient mode and
`{"at_most": 5.0}` for the others, and an ambient mode has `correction_required:
true`: it cannot be read without an illuminance-scaled `.ccmx`. `prepare()` refuses a
request that breaks either rule before an instrument is opened, in words a client
can show. The threshold comes from the native policy that also gates the binding
(`driver_policy.h`), carried by the description as `ambient_matrix_threshold`. For
every other instrument both fields are `false` and `null`.

**`operator_setup` — the accessory, from the manual.**
This is one short instruction for the operator's prompt card: the holder,
diffuser, ruler, dial position or calibration plate the manufacturer's manual
names for this instrument and route, or for one calibration condition. It carries
its `document`, `location` (a PDF page, or the URL of a support page without
pages), a verbatim `quote` of 25 words or fewer that backs it, and `official`,
false for an archived manufacturer copy. It is null wherever the manual states
nothing: nothing is carried over from another model, mode or condition. The table
is `color_measurement/operator_setup.py`; the quoted source and location identify the supporting manufacturer material.
The instruction `detail` names no accessory.

**`physical_chart` — documented target scope, independent of capability bits.**
Every mode carries `kind` and `assessment`. `assessment` has the same source-row
fields as `operator_setup`, or is null for `unknown`. Clients use the enum, never
parse its prose or infer target optics from `tele`, `spectral`, or an expected
quantity. The policy has one owner, `color_measurement/operator_setup.py`.

- `reflective`: the advertised native route measures reflection. Its source is
  the pinned `inst.h` mode definition. Operator setup and patch isolation still apply.
- `naming_only`: documented target-selective optics permit a conditional patch
  naming workflow using radiance or luminance. The operator must confirm that the
  illuminated patch fills the actual measurement field at the chosen distance.
  This does not convert the result to reflectance or establish comparison with a
  reference. CR-100, CR-250 and Klein K-10 emissive spot/tele routes have this
  assessment; they retain their distinct manufacturer sources.
- `display_only`: the assessed documentation describes a display or projector
  setup, and physical-chart optics remain unassessed. This is an application
  admission limit, not a claim that another surface is physically unmeasurable.
  It applies to i1DisplayPro/ColorMunki Display and SpyderX, X2 and 2024 emissive
  routes; the i1Display tele row cites its projector setup rather than contact setup.
- `unknown`: no physical-chart optics assessment exists for this instrument and
  route. Other models, ambient, transmission and saved-data routes do not inherit
  an assessment. A client cannot treat this state as permission to measure a chart.

These are source assessments, not physical acceptance tests. The descriptor does
not report the instrument's fitted aperture, a verified patch footprint, lighting,
or placement. Actual records retain native quantities and calibration context.

The instrument-level `calibration_setup` is a list, one entry per condition a manual describes, each
with its `condition` (a name from the calibration vocabulary, or null for a
calibration the instrument does with no operator condition, like the
SpectroScan's). **Show a calibration prompt's own `calibration.setup`, never an
instrument-wide note**: an instrument asks for more than one condition, and a note
written for one is wrong on another. The ColorMunki's dial goes to its
calibration position for `man_cal_smode` and must be back at surface for the
transmission white that follows (`munki_imp.c:1071-1073`), so that prompt's
`setup` is null.

Each mode also carries its own `calibration_setup` list in the same sourced row
format, required by client contract 28. This previews only references relevant to that mode, from its manual or
the pinned ArgyllCMS 3.5.0 driver's `get_n_a_cals` and `calibrate` functions.
Source notes identify the C file and quote its actual condition selection;
they are not manufacturer instructions. The CR-100/CR-250/CR-300 and PR-655/PR-670
notes identify their bundled contribution source (`cr250.c` or `pr655.c`), which
is not in the stock Argyll archive. A `location`
can therefore also be a labelled bundled source path, shown as provenance text.
These are possible steps, not a report
of what is currently needed or valid. The native prompt still supplies the actual
condition, expected reference identifier, warnings and acknowledgement rule.

For example, DTP41 transmission previews a transmission white, not its reflective
reference; ColorMunki transmission distinguishes the calibration dial position
from the later surface position. i1Pro 3 can need its white plate for wavelength
calibration even in emissive or ambient modes. i1Pro 2 ambient wavelength calibration
instead uses its capped ambient adapter; a separate high-resolution fine calibration
can still need a white reference. Nonadaptive exposure selection and refresh
calibration have their own conditional display-patch notes. Refresh previews use
the observed variant's native facility rather than guessing from a shared model name.

Optional-only black, light-trap or gloss calibrations are not prescribed by these
notes. Ordinary Measure and Calibrate request needed calibrations. The Klein's
optional black calibration writes instrument memory and is not requested here.
Stored reads have an empty mode list. A driver with no host calibration says so
without certifying its factory calibration. An unassessed driver has no preview;
absence of a note is not evidence that calibration is unnecessary. Clients suppress
acquisition preparation for refused and configuration-only workflows, and use the
mode list rather than the instrument-level manual catalogue for mode help.

**`canonical` / `equivalent_to` — do not offer one measurement twice.**
`emissive_spot` and `emissive_spot_adaptive` compose the *same* instrument word:
adaptive acquisition is the default and the suffix only says so out loud. The
first name for each composed effect is `canonical: true`; the others carry
`canonical: false` and point at it. `emissive_spot_nonadaptive` sets
`inst_mode_emis_nonadaptive` and is therefore its own measurement, not an alias.

**`expected_measurement_type` identifies the quantity expected for the mode.**
A client can use it to label an axis before reading. A stated native
`inst_meas_type` must agree with the requested mode; a mismatch is refused by
`_validate_reading` in [`instrument.py`](../src/color_measurement/instrument.py).
When the driver leaves the type unset (`inst_mrt_none`), `_labelled_type` uses
the requested mode to assign quantity labels. The raw native type is retained;
a conflicting stated type is never relabelled to make it fit.

**`available: false` includes a reason to show the user.** X-Y acquisition is
implemented. A reported X-Y mode is unavailable when the instrument lacks the
required chart-positioning and location capabilities. When available, the client
supplies sheet geometry and answers `xy_sheet` and `xy_locate` instructions; Python
calculates navigation from the instrument's reported fiducial positions. The
client does not send coordinates. A strip mode can separately be unavailable
when no assessed driver operand contract exists. Use the descriptor's reason
for the selected mode rather than treating either workflow as unimplemented.

`stored` marks a `saved_*` route, which reads what the instrument already holds.
Whether anything *is* held is a runtime status, not a capability
(`inst.h:467-468`), so no descriptor field can carry it. Live and stored routes
are ranked **separately**, because upstream picks that branch from
`inst_stat_saved_readings` rather than from a capability (`chartread.c:900-931`).

A client reads the status from the **result** of a stored operation, as
`configuration["stored_readings"]` — the raw `inst_stat_savdrd` bits. The key is
absent when the instrument was never asked, which is not the same as zero. There
is no way to ask without opening the instrument, and the operation that reads a
stored chart asks on the way past, so nothing extra is opened to learn it.

**Only the DTP20 answers it.** `dtp20.c` is the one driver in the pinned tree
that assigns `inst_stat_saved_readings`, so `saved_*` routes exist for it alone;
that is upstream's shape, not a limit of this path.

A stored route that starts with **nothing stored is refused** before the read,
with stage `no_saved_readings` on the result — the status exists to be asked
first, so spending an operator's wait to reach a driver error instead would be
the wrong answer twice.

### Options

Every option names the capability that gates it and the refusal that gate would
produce, so a client can grey one out **and say why**:

| id | type | gate | refusal stage |
|---|---|---|---|
| `highres` | boolean | `inst_mode_highres` in the primary word | `highres_unavailable` |
| `xyz_only` | boolean | `inst_mode_spectral` in the primary word | `xyz_only_unavailable` |
| `averages` | integer 0–1000 | `inst3_average` | `averaging_unavailable` |
| `xcalstd` | choice | live/saved reflection; conversion targets require spectral output; driver support checked at set time | `xcalstd_requires_reflection`, `xcalstd_requires_spectrum`, `set_xcalstd` |
| `display_type` | named choice ID | `inst2_disptype` and an observed catalogue | `display_type_unavailable`, `display_type_stale` |
| `filter` | choice | the tertiary word's filter bits | `set_filter` |
| `correction` | object | `inst2_ccmx` or `inst2_ccss` | `correction_matrix_unavailable` |
| `refresh_rate` | number: null, 0 or [5, 150] Hz | `inst2_set_refresh_rate` in the configured word | `refresh_rate_unavailable`, `set_refresh_rate` |

Display-type choices use exact string IDs in `choices`, containing only
available entries in native order. `choice_details` retains every original row
including unavailable entries; its length can exceed `choices`. It preserves
native names, index, technology, refresh flag, calibration base, kind, custom flag,
`native_default` marker, source path, identity scope, availability and any
lossless invalid native text
bytes. `presentation_label` adds kind and original list position when needed to
distinguish names or label an unavailable entry. An undecodable name uses the
presentation text "Unavailable display calibration"; this is not a guessed
native label. Send an available ID, never its label or list position.
The default is null: omitting the option does not select the first choice.
`native_default` is the driver's `inst_dtflags_default` declaration for that row.
Zero, one or multiple markers are preserved, including on unavailable entries.
It is neither active-selection readback nor an instruction to select a choice.
The client keeps its "Not set" option and can label declared defaults separately.
`display_types` also publishes catalogue status and separate native readback.
The retained operation configuration carries `display_types_json`, including the
accepted explicit choice and native readback. See the
[identity and revalidation rules](instrument.md).

`applies_to` is `null` for an option that applies to every mode, and a non-empty
list of mode names otherwise; it is never an empty list, which a JSON reader can
decode exactly as it decodes `null`. An option no mode of the instrument can take —
`filter` on an instrument with no reflective mode — is published unavailable,
with its reason and `applies_to: null`. `filter` applies only to reflective modes — a measurement
condition is a property of a reflective measurement. `correction` applies to
emissive modes and ambient only when the native `ambient_ccmx` policy permits
it. Klein K10 applies its matrix on the ambient path; other ambient correction
requests are refused when the driver ignores them. Clients consume `applies_to`
and do not reproduce this device policy.

#### Passing a colorimeter correction

Every option is a JSON scalar except `correction`, which is an object:

```jsonc
"options": { "correction": { "path": "/…/DTP94_Dell_2408W.ccmx" } }
"options": { "correction": { "text": "CCMX\n…" } }
```

**The kind is not stated by the caller.** Dispatch is on the file's own CGATS
identifier, exactly as `spotread -X file` does it, so a client that picked a file
does not need to know whether it holds a matrix or a spectral set. `text` exists
for a client that has bytes rather than a path this process can open.

Without a correction, the driver's factory/display-type calibration remains in
use; this alone does not establish invalid XYZ. See
[colorimeter corrections](instrument.md#colorimeter-corrections).

### Operator instructions

The helper's own instruction words, each with a **stable kind** this contract
owns, so a client picks a screen without matching a spelling upstream may change:

| instruction | kind | answered |
|---|---|---|
| `strip_position` | `present_strip` | yes |
| `sample_position` | `present_sample` | yes |
| `calibration_setup` | `present_calibration_reference` | yes |
| `calibration_condition` | `notify_calibration_condition` | **no** |
| `xy_sheet` | `place_sheet` | yes |
| `xy_locate` | `sight_fiducial` | yes |
| `sensor_position` | `reposition_sensor` | yes |

`xy_navigate` never reaches a client: the ladder answers it itself from the
sighted fiducials.

`calibration_condition` takes **no answer**. It is a condition the instrument
itself triggers, and the ladder calls `calibrate()` straight back rather than
waiting — so it arrives as `instruction_notified`, not as a question. Offering a
button there would wait on a press that means nothing.

### When the sensor is in the wrong position

Some drivers check where their sensor, dial, diffuser or adapter is before they
read or calibrate, and refuse with `inst_wrong_config` when it is not where the
step needs it: the ColorMunki's dial (`munki_imp.c:2191-2206`, and for a
transmission white `:1071-1073`), the i1Display's diffuser arm on every reading
(`i1d3.c:2318-2346`), the i1Pro 3's adapter or calibration tile
(`i1pro3_imp.c:7561-7564`, `:9589-9600`). As `spotread.c:2323-2326` and
`instappsup.c:210-233` do, the backend asks the operator to fix it and tries the
step again: once while calibrating and once while reading, for an unpositioned
spot, a strip or a
calibration step. The prompt is `sensor_position` (`reposition_sensor`), answered
`accept` or `cancel`, and names the position:

```jsonc
"position": {
  "needed": "projector",    // inst_config, inst.h:717-726: projector, surface,
                            // surface_polarized, emission, calibration,
                            // polarized_calibration, ambient, or unknown
  "stage": "read_sample",   // read_sample, read_strip or calibrate
  "driver_text": "Sensor should be in projector position"   // or null
}
```

`needed` is the driver's own `config_enum` answer and `driver_text` its own words
for the code. `unknown` means the driver named no position: `specbos.c:1114-1119`
raises the code but inherits upstream's default, which names none. Every other
prompt carries `"position": null`. A position still wrong on the retry fails the
operation at that stage with the driver's text. A prompt raised while calibrating
is kept in `calibration_events_json`. The backend's own refusals that reuse the
code — a serial that is not the selected unit's, a sheet that cannot hold its
patches — stay refusals and never prompt.

### The `calibrate` workflow

Every **spot** mode offers it, on every instrument. It runs the instrument's
outstanding calibrations and **reads nothing** — `patch_ids` must be empty, and
the result carries no patches.

It is not gated on a capability word, and specifically not on
`inst_mode_calibration`. Only `munki.c` sets that bit in the whole pinned tree,
and `inst.h:251-252` defines it as *"Configured for calibration"* — a **sensor
position**, reported through `meas_config()` because the ColorMunki's dial has a
calibration detent. Nothing else has one. The authority is `get_n_a_cals()`,
which the ladder calls first. Some drivers inherit an upstream default that
reports no calibration work; those outcomes retain `status_source="upstream_default"`
and calibration status `unknown`.

So **whether anything needs doing is a runtime answer, not a capability**. An
instrument needing nothing completes immediately with `performed: false` in
the JSON text configuration key `calibration_outcome_json`. The additional
`calibration_events_json` retains the negotiation, reference and warning events.
Driver-reported readiness is `valid` only when nondeferrable work is clear;
optional skips and work performed remain separate retained facts. The outcome
arrives once the negotiation has completed, so `required` there means the
driver's assessment AFTER it still names work, and its `detail` says so:
*"Calibration negotiation completed; the driver's subsequent assessment still
reports required calibration. Both native results are retained."* The broad
build's DTP92 repair clears successfully completed needs in the driver; the
frontend does not override the native assessment.

A reading asks the drivers that keep a calibration file to keep what they
restored instead of asking for their at-open calibration, and the outcome's
`initial_calibration_suppressed` says whether that applied (see
[initial-calibration suppression](instrument.md#upstream-initial-calibration-suppression)).
An i1Pro 3 can use a valid restored calibration even on the first reading of a
new application session. Missing, invalid or expired state still requires work;
mode-specific integration setup and transmission white remain separate. The `calibrate`
workflow does not request that waiver; it negotiates needed work and can finish
with `performed: false` when the driver reports none. When a waived reading performs nothing, the `valid`
detail says so: *"No calibration was performed in this operation. The driver was
asked to keep the calibration it retained instead of calibrating at open, and
reports none outstanding: any calibration the reading uses is one the driver kept
and judges valid. This is not physical validation."* It claims no more than
that, because a mode may need no calibration at all: a SpyderX in ambient
(`spydX.c:922`).

Live acquisition performs this negotiation in the same native lifetime as its
read, so volatile references survive. Ordinary placed spot samples need no
additional confirmation when calibration asks for no operator setup or only
issues a message. After physical or display calibration setup, `sample_position`
(`present_sample`) asks the operator to return to the sample. This includes
optional setup later skipped, since placement may already have changed.
`strip_position` (`present_strip`) keeps its strip readiness step. These
instructions have no calibration detail; they
accept `accept` or `cancel`. A standalone calibration cannot transfer volatile
state into a later process. Some drivers restore cached state, but that does not
guarantee readiness or remove initial prompts; see the
[all-driver calibration behavior](instrument.md#calibration-across-the-public-driver-set).

A live unpositioned spot or strip read may discover a new calibration need.
With calibration enabled, the backend keeps that driver instance, negotiates
calibration and placement again, and retries once. Clients handle the repeated
instructions through the same operation. Native `calibration_retry` marks the
invalidated assessment in `calibration_events_json`; both assessments remain in
history and `calibration_outcome_json` selects the latest applicable one. If
recovery fails, the outcome is null and calibration is `required`, rather than
retaining the earlier ready claim. No model-specific MATLAB rule is needed.


### What a calibration prompt says

Do not key a calibration screen on the instruction word. Both calibration
instructions carry a `calibration` block that names what is actually being asked:

```jsonc
{
  "condition": "man_ref_whitek",     // one of 19, inst.h:636-676
  "action": "click_reference",       // one of 7 — the screen to show
  "instruction": "Click the instrument down on its reflective white reference",
  "reference_id": "TILE-0022",
  "reference_id_holds": "The expected white reference identifier, held by the driver, not read from the reference placed",
  "reference": "ref_sn",             // inst_calc_id_type, decoded
  "warnings": [],                    // e.g. transmission reference light too low
  "display_level": null,             // fraction of full white, for display_patch
  "skippable": false,
  "acknowledged": false,             // false ⇒ nothing is waiting for you
  "needed": ["ref_white"],           // inst_cal_type, decoded — a bitfield
  "remaining": ["ref_white"],
  "setup": null                      // the manual's note for THIS condition, or null
}
```

**The 7 actions are the screens you build**, for all 19 conditions:

| action | what the operator does |
|---|---|
| `present_reference` | puts the instrument on a reference — white, light trap, gloss black, ambient cap, transmissive source or block |
| `click_reference` | clicks the instrument down on its tile; **the instrument triggers, you do not** |
| `operate_instrument` | works the instrument's own panel — the DTP41/DTP51 `uop_*` group |
| `set_sensor_position` | moves the sensor to its calibration position — the ColorMunki dial |
| `display_patch` | shows a patch at `display_level` |
| `change_filter` | fits the filter named in `reference_id` |
| `message` | shows the driver's own text, carried in `reference_id` |

The descriptor publishes this whole vocabulary under `calibration`, so a client
can build all seven ahead of ever meeting an instrument that raises them.

Manufacturer preparation notes supplement these shared actions. The DTP22's
white-reference note names its calibration holder; the physical read switch
triggers that reading without a software confirmation. For Klein ambient
readings, the mode note asks for the diffuser and the instrument's named Lux
calibration. Selecting that calibration is configuration, not a black-calibration
request. The application does not choose a fixed calibration slot. See
[X-Rite's DTP22 procedure](https://www.xrite.com/service-support/using_hyperterminal_to_calibrate_a_dtp22)
and [Klein's diffuser instructions](https://www.kleininstruments.com/faq).

#### `display_level` is not optional and must not be invented

For a `display_patch` condition, `display_level` is the fraction of full white to
display. It is the **application's** to compute, not the driver's: no driver reads
it back, and upstream computes it in `dispsup.c:186-200` — 100% for
`emis_white`, 80% for `emis_80pc`, and for the DTP92 cell-ratio ladder a feedback
loop that starts at 0.6, multiplies by 0.7 when the driver asks for darker and by
1.4 (clamped to 1.0) when it asks for lighter, failing after four adjustments.
The ladder runs that loop and publishes the result. **"A grey patch" is not a
colour**; a client that guesses one calibrates against a level the driver did not
ask for.

#### Why this is named at all

The i1Pro family raises 7 of the 19 conditions. The other 12 belong to
instruments this project had never driven — and a client that learned the
vocabulary from an i1Pro would meet an integer it could not render the first time
it drove a DTP41, a DTP22, a ColorMunki, an smcube, a DTP92 or a SpectroScan.
Publishing the number alone did not make the contract device-independent; it
moved the device knowledge into every client.

## Requests

Exactly these eight fields, plus one optional ninth — no more and no fewer:

```jsonc
{
  "schema_version": 1,
  "device_id": "…",            // must match the descriptor
  "capability_id": "…",        // must match, or the request is refused
  "mode": "reflective_strip_m1",
  "workflow": "strip",         // one of the mode's own `workflows`
  "patch_ids": ["P1", "P2"],   // unique, non-empty, within the workflow's bounds
  "options": { … },
  "layout": { … },
  "event_contract": "patch_v1" // OPTIONAL; one of the descriptor's `event_contracts`
}
```

**Option values are type-checked against the descriptor before anything starts.**
An option typed `boolean` takes `true`/`false` and not `1`; one typed `integer`
takes an integer and not `"5"` — and not `true` either, which in JSON-to-Python
terms is an integer and would otherwise mean "average one reading". A `choice`
value outside the published `choices` is refused by name. All of these are
refused by `prepare`, in the validator's own words, before any event is emitted.

The check lives on `Configuration`, which is the same validator acquisition
itself uses, so a caller driving `Instrument` directly gets the identical
refusal. A second copy here could drift from it.

`event_contract` is optional because a client that has never heard of it must
choose either profile; omitting it selects `retained_patch_v1`, also stated by
`default_event_contract` in the descriptor. It replaces each `patch` event's whole observation with a
`patch_reference` carrying only `index`, `patch_id` and `observation_id` after
canonical retention — which is what keeps a 601-band reading inside the frame
cap. The descriptor publishes the accepted values as `event_contracts`, and the
one in force is echoed on `started` and in the result's `configuration`. An
instrument whose driver publishes native facts (the CR family) offers
`retained_patch_v1` only: every reading carries those facts, which no inline event
can hold, and `patch_v1` is refused before any worker runs rather than failing
after the reading is retained. Any other reading too large for one event -- a
JETI's 401 bands, twice over with its native frame -- fails a `patch_v1`
operation with a message naming `retained_patch_v1`; no driver publishes its band
count before it reads, so no descriptor can refuse that in advance.

### `layout` is the CHART's geometry, not the instrument's

The caller supplies the target's geometry. The native driver's strip policy
states which operands it consumes; the frontend publishes that policy without
maintaining a second model table. DTP51 consumes names and a guide number and
ignores widths. DTP20 and DTP41 consume widths and ignore names and guide.

| workflow | layout keys | required |
|---|---|---|
| `spot`, `calibrate` | — | — |
| `strip` | only keys in that mode's `layout_fields` | that mode's `required_layout_keys` |
| `chart` | `passes`, `steps`, `sheets`, `chart_id` | `passes`, `steps`, `sheets` |
| `xy` | `passes`, `steps`, `pass_name`, `step_name`, `hexagonal` | `passes`, `steps` |

`direction` is `forward` (default) or `reverse`; reverse requires bidirectional
scan capability and reverses patch association while retaining native indices.
It is operator-declared, not automatic target recognition or DTP51 alignment.

Read them from the descriptor's `layout_keys` / `required_layout_keys` rather than
from this table; the descriptor is generated from the same source this check uses.

Every workflow declares `layout_fields` with one current shape. Strip also adds
`strip_requirement` and `patch_association="manual_returned_positions"` from its
native policy. Field records contain `key`,
`label`, `type`, `required`, `default` and `help`. Numeric fields (`integer` or
`number`) add `minimum`, `exclusive_minimum`, `maximum` (null means no declared
upper bound) and `unit`. Text fields add `max_bytes` and `encoding`:
`ascii_printable` for native target names, `utf8` for nonblank display-stimulus
context. Lengths are bytes; printable ASCII excludes control characters.
Choice fields add `choices`; boolean fields require actual booleans.
`integer_array` fields use the numeric item bounds plus `min_items` and
`max_items`. Unknown keys or types are not silently
discarded. A consumer unable to render a required field must refuse that workflow.

X-Y and chart workflows publish their grid operands through these same fields.
Passes and steps use the shared 4096-patch bounds. Chart `sheets` is an ordered
integer array with at most 64 entries; its sum must equal the pass count, and the
chart grid must contain at most 4096 slots. The backend's ordinary geometry
validators retain these coupled checks. Optional native chart IDs use nonnegative
signed 32-bit integers. X-Y starting names retain Argyll's two-character
`inst.h:942-943` interface and omission keeps the native starting names. These are
target operands, separate from native observation IDs.

Fresh frequency and controlled timing declare required stimulus context with
the same field shape and model-owned UTF-8 limits. Timing additionally publishes
`presentation_reply_fields` and `presentation_constraints`. These describe the
host's draw-completion receipts, separate from editable layout. The ordinary
`PresentationStamp` owner still checks phase/identities, deadlines, matching clock
and source, and increasing black/white timestamps. No timestamp is interpreted
as measured optical onset.

Omission and an explicit value remain different. Required target fields have
no guessed default. An omitted optional name sends the documented native name;
omitted direction keeps driver order; omitted tolerance leaves the driver's
default alone. A null numeric value is not an omission. Render field labels,
help and units from this descriptor, including patch **length along the scan**.
Workflow patch bounds include the native wire-format limits described in the
[instrument guide](instrument.md#strip-operands).

The `present_strip` instruction's `message` includes the actual selected trigger
and declared patch order. Display this operation-specific message: a programmatic
trigger starts reading after the user continues. Its guidance distinguishes
manual movement across the selected row from DTP41's motor feed; DTP51 must
instead be fed into its microswitch. The native event retains `effective_trigger` and
`trigger_guidance`. A descriptor capability bit alone does not identify the
trigger actually selected for an operation. These are software/source facts;
physical operation remains unvalidated.

`sheets` must be a non-empty list of positive pass counts summing to exactly
`passes` — the driver walks that list, and a short one walks off the end of the
array the helper allocated.

### Refresh-frequency request

An emissive spot or telephoto mode can offer `refresh_frequency` alongside
its colour workflow when the native measurement capability is advertised. Availability reflects the provided capability snapshot.
Drivers that advertise the facility only in emissive mode need a snapshot from
that configured mode; an initialized reflective snapshot does not offer it.
The workflow publishes `result_kind: "scalar"` and its own
`expected_quantities` (`refresh_frequency`, Hz, `read_refrate`).
Mode-level colour forecasts describe colour acquisition, not this scalar result.

Use one sample ID, `retained_patch_v1`, and both required layout fields:

```python
request.update(
    workflow="refresh_frequency",
    patch_ids=["RATE-1"],
    event_contract="retained_patch_v1",
    layout={"stimulus_id": "WHITE-60",
            "stimulus_description": "Externally presented steady white"},
    options={},
)
```

Only a named `display_type` choice may be nondefault. The descriptor supplies
the stimulus text fields and their UTF-8 byte bounds. After any required
calibration, `refresh_stimulus` asks the client to present its declared stimulus;
its descriptor kind is `present_refresh_stimulus`. Responding `accept` authorizes
one native frequency read. There is no stored-rate getter, rate override or
automatic repeated acquisition call in this workflow.

The normal `patch_reference` identifies the retained scalar. Retrieve it through
`result_json()`, discriminate `observation.record_kind == "scalar"`, and read
`backend_reported.value` with its Hz unit. Failed attempts retain native stage,
code and diagnostic, with no manufactured measurement. Poll/respond/cancel,
ownership, stop facts and JSON/MAT persistence use the same operation as colour
acquisition.

### Display-timing request

An emissive spot or telephoto mode offers `display_timing` when its provided
capability snapshot advertises `meas_disp_update`. Its result kind is `timing`,
with native marker-relative display delay and signed instrument reaction in ms.
Colour forecasts and stored refresh capabilities do not describe this result.
Use exactly one sample ID, `retained_patch_v1`, and this required layout:

```python
request.update(
    workflow="display_timing",
    patch_ids=["TIMING-1"],
    event_contract="retained_patch_v1",
    layout={"stimulus_id": "BLACK-WHITE-1",
            "stimulus_description": "Controlled display transition",
            "display_id": "DISPLAY-1"},
    options={},
)
```

The client must control a presentation surface on that display. After common
calibration, `presentation_required` emits black, then white, with exact
`request_id`, `phase`, `stimulus_id`, `display_id`, `stimulus_description`,
`rgb` (three channels, each 0.0 or 1.0), `timestamp_source`, `minimum_delay_ms`,
`native_minimum_delay_ms`, `deadline_ms`, and native method-window context. The backend owns the minimum wait
(`presentation_wait_owner: "backend"`); its presenter event carries remaining
wait 0 and the original native minimum separately. This is a
separate exchange from operator instructions. `respond(..., "accept")` cannot
satisfy it. After drawing and completing presentation, call:

```python
operation.presented(operation.operation_id, payload["request_id"], json.dumps({
    "phase": payload["phase"], "stimulus_id": payload["stimulus_id"],
    "display_id": payload["display_id"], "clock_id": "CLIENT-MONOTONIC",
    "presented_at_seconds": client_monotonic_seconds,
    "source": "client_draw_completed",
}))
```

The backend waits a conservative 200 ms after receipt of the native white
request before asking the client to draw. The client draws immediately; it
must not wait another 200 ms. The published `deadline_ms` is the remaining
phase budget after that wait, bounded by the original 2 s response deadline. A matching ID, identity, phase, clock and ordered stamp are
required. Wrong, duplicate or stale responses fail. An otherwise matching
presentation reply or operator response that arrives after its request's deadline
is refused with `arrived after its deadline`. An already published terminal
timeout remains unchanged; cancellation that wins the operation lock before
the waiter handles the expired deadline retains its cancellation outcome.
Deadline checks and reply
acceptance share the same operation lock, so
a reply cannot become valid while the timeout waiter is waiting to resume.
Cancel on surface loss or
closure, stop scheduled presentation work, and use the existing operation
cleanup; never draw queued white after cancellation or completion. The native
worker settles black for 600 ms and marks white during acquisition on a reader
thread, while the owning thread runs `meas_delay`.

`patch_reference` retrieves a retained `TimingObservation`, separate from colour
and refresh observations. Preserve both clock domains and use the native
`white_change_call` reference; no IPC or optical-onset inference is supported.
JSON/MAT persistence, failures and late-result retention use the existing
operation envelope.

## Driving

```python
operation = frontend.prepare(json.dumps(request))
operation_id = operation.operation_id  # Retain before durable intent and dispatch.

def drain():
    while True:
        raw = operation.poll_event_json()     # one bounded JSON event, or empty
        if not raw:
            return
        event = json.loads(raw)
        handle(event)
        if event["kind"] == "instruction_required":
            operation.respond(operation.operation_id,
                              event["payload"]["request_id"], "accept")  # or "skip"/"cancel"

try:
    operation.dispatch()
    while True:
        drain()
        if operation.done:
            drain()          # SEE BELOW -- completion can race with the first drain
            break
        time.sleep(0.01)
    result = json.loads(operation.result_json())
finally:
    operation.close()
```

If dispatch or a client callback raises, keep the already-held operation and
identity through cancellation and drain its remaining events. The `finally`
block requests cancellation; it is not stop proof. Inspect the retained
`software_stopped` fact before enabling another acquisition. A terminal with an
unknown software stop keeps the instance owned, rather than permitting a retry.

**Drain once more after observing `done`.** Final events can arrive between
the previous drain and the completion check. Ordinary finalization publishes the
terminal event before setting `done`; completion does not mean the client has
consumed the queue. If primitive recovery itself fails, `done` can be true without
a terminal event or retained result. Handle the
[finalization limit](frontend.md#events-and-retained-outcomes) instead of waiting
forever or restarting acquisition.

`instruction_notified` is **not** answered — see
[operator instructions](#operator-instructions).

### Events

Every event is `{schema_version, operation_id, sequence, kind, payload}`, bounded
by `max_event_bytes`. `sequence` is monotonic per operation, so a client can
detect a gap rather than assume it read everything.

Preparation performs no acquisition and starts no worker. Dispatch is one-shot;
closing a prepared operation resolves it without I/O. A `started` event denotes
entry into the operation lifecycle, including a failed/cancelled no-dispatch
outcome; it does not prove that a native helper launched. Keep the operation
handle through interruption or a lost dispatch response, cancel and drain it,
and use terminal stop facts to decide whether another operation is admissible.

| kind | when | answered |
|---|---|---|
| `started` | once, first | — |
| `instruction_required` | an operator condition | **yes** — `respond(...)` |
| `instruction_notified` | a condition the instrument triggers | no |
| `presentation_required` | a display phase the client must present | **yes** — `presented(...)`, never `respond` |
| `patch` | one reading, with its whole observation | — |
| `patch_reference` | one reading, identifier only — `retained_patch_v1` only | — |
| `terminal` | once, last | — |

**Only `started`, `patch`/`patch_reference` and `terminal` are guaranteed**, and
`patch` only when the operation reads. A `calibrate` operation publishes no patch
at all. Everything else depends on what the instrument asks for, so a client must
tolerate an operation that emits none of the instruction kinds and one that emits
several.

There is no `measuring` event. Reading notifications are emitted after the
returned observations have been validated and retained.

**Answer the current instruction using its offered responses.** The frontend
maps `accept` to the native `acknowledge` command; `skip` is valid only when the
prompt is `skippable`. `cancel` is also a native command. Python handles the
separate `navigate` exchange from sighted fiducials; it is not an operator response
to offer in the UI. These mappings are implemented by `_acknowledge` in
[`instrument_frontend.py`](../src/color_measurement/instrument_frontend.py) and
`_reply` in [`instrument.py`](../src/color_measurement/instrument.py).

**Cancellation requests a stop; it does not guarantee an immediate device abort.**
The supervisor sends `cancel` to the helper, whose command reader and driver
callback can observe it during an instruction or acquisition. If the helper does
not finish within the bounded grace period, supervision ends the process.
A complete validated read received before that cutoff is retained, including
when it finishes after the stop request. A result can therefore be `cancelled`
and still contain readings. Inspect the stop facts below to distinguish a
helper acknowledgment from forced termination; use each record's provenance to
distinguish physical, simulated and replayed values.

So `status` answers *"did this operation run to completion?"*, and `patches`
answers *"what was measured?"*. They are separate questions. A cancelled result
with patches retains validated readings available before the stop completed.
With no patches, no validated readings were retained; that does not prove the
instrument never started or finished a measurement. A client showing
readings should show these, labelled as a stopped operation, rather than treating
`cancelled` as an empty outcome.

### Results

Save the retained terminal result with
[`operation_exports.save`](data-format.md#operation-files-json-and-mat), then
inspect both `saved.path` and `saved.ti3`. The latter reports optional TI3 files
and unavailable quantities separately from the complete JSON/MAT save. A failed
TI3 update calls for saved-data recovery, never another measurement. Scalar,
timing and zero-reading outcomes remain valid canonical records even though
they cannot supply TI3 color rows.

`OperationResult` as [documented in the shared operation guide](frontend.md#operation-results),
plus a **structured native failure**:

```jsonc
{ "status": "failed",
  "error": "check_mode (native code 327680)",   // the sentence, for an operator
  "error_stage": "check_mode",                  // what a client BRANCHES on
  "error_native_code": 327680 }
```

Both appear on the `terminal` event too, so a client watching events and never
fetching the result is not left with only prose. They are absent together for
anything that did not fail inside the instrument.

If a native ending and cleanup both fail, the operation remains `failed` and
keeps that ending's stage, code and validated native facts. Stop facts remain
separate when a stop was requested. A complete reading survives failed cleanup
only after the terminal exchange and normal result validation succeed; an
invalid drain cannot publish it. Failed cleanup also keeps an otherwise successful
validated reading, with `failed` status and no fabricated native error code.
The error text includes bounded summaries of the ending, cleanup failures and
explicit causes. Within the traversal budget, per-member text bounds keep a
long message from crowding out a short secondary diagnostic; deeper or wider
summaries carry `[diagnostic limit]`.
A callback remains the principal failure even if its group or cause contains
an instrument error. A native cancellation followed by failed cleanup remains
`failed`, with the cancellation and acknowledgment facts recorded separately.

When the **driver** returned the failure, the result's `configuration` also keeps
the driver's own sentence, as upstream prints it (`interp_error` with the whole
`inst_code`): `error_native_text`, cut at 512 bytes by the helper, with
`error_native_text_truncated` saying whether it was. It is for an operator; a
client branches on `error_stage` and `error_native_code`, never on this text. A
driver with a support adapter also leaves `native_facts_json` there for a failed
attempt. If a refused CR publication already parsed native XYZ or complete
spectral bins, the ordinary retained observation keeps those originals with
unverified native units and scale. It emits `patch_reference` and saves through
the existing record owners; the terminal remains failed. Configuration records
`native_observation_retained=true` and `argyll_publication_status=refused`.
No acquisition retry, invented ipatch or successful publication is implied.
Other warning-bearing or refused reads retain replies as evidence
([records](data-format.md)). These live in
`configuration` beside `calibration_events_json`.

Before the dispatch gate opens, a failure instead retains the host-owned
`dispatch_failure_stage` in `configuration`. It classifies the boundary that
failed, not the exception's wording. The native `error_stage` and
`error_native_code` stay null. These results have no readings and confirm that
acquisition was not dispatched, even if a bootstrap thread was created.

| `dispatch_failure_stage` | Host boundary |
|---|---|
| `admission` | Taking the prepared handle into its single dispatch attempt |
| `snapshot_validation` | Obtaining and validating the current capability snapshot |
| `stale_snapshot` | The current snapshot differs from the one that validated the request |
| `instance_busy` | Another operation owns the instance |
| `worker_start` | Constructing or starting the bootstrap worker, including acquiring its lease |
| `commit` | Preparing to open the dispatch gate; it has not opened |
| `notification` | Publishing the no-dispatch startup notification without an earlier dispatch failure |

The first dispatch failure keeps its stage if a recovery notification also
fails. A client cancellation before dispatch records `stop_requested_by=client`
without a failure stage; a subsequent notification failure adds its stage while
preserving that actual stop requester. Host dispatch failures record
`stop_requested_by=host_failure`. The facts survive ordinary result-finalization
failures. A cause after commitment continues to use the existing worker cleanup
and retained-result rules; it does not assert no dispatch occurred.

An operation that was **stopped** -- cancelled by the client, or ended by the
host's deadline -- records how, as four separate facts in `configuration` once
the worker enters the acquisition lifetime. No-dispatch outcomes retain only
their known host stop facts above, without inventing helper acknowledgment or
device-completion facts:

| key | values |
|---|---|
| `stop_requested_by` | `client`, `host_deadline`, or `host_failure` for a dispatch failure |
| `stop_acknowledged_by` | `helper` when the helper reported the stop itself; `none` when this client had to stop waiting |
| `worker_stop` | how the helper process was **observed** to end: `exited`, `terminated`, `killed`; `unknown` when a started helper's end was not confirmed; `not_started` when none existed; `simulated` for the in-memory demo |
| `device_completion` | `unknown` for an interrupted read; `completed` when a complete validated reading arrived despite the stop request |

They are kept apart because they are different claims. A helper killed
mid-exposure acknowledged nothing, and its instrument may or may not have finished
the measurement; nothing here reports a driver acknowledgment or a device abort
that did not happen, and no second measurement is ever sent to recover.
If a started helper's stop cannot be confirmed, `software_stopped` remains false
even though the operation has a terminal result. Clients must keep another
operation unavailable; the frontend keeps ownership and refuses a second Start.
The reported instrument state remains unknown.

To recover, first save any retained readings and finish the client's batch. Then
retire and restart the Python backend through the client's guarded recovery path.
The frontend has no force-release API: `close()` requests cancellation, and
neither closing nor saving a terminal result clears unconfirmed ownership.
Restarting the backend removes its in-memory ownership, but does not prove that
its helper ended. Confirm that any surviving helper has stopped through its
authorized process owner before another acquisition. A client can require an
explicit operator confirmation for reconnecting; that is an operator assertion,
not a newly observed native stop. Preserve the original `software_stopped=false`
and unknown instrument state, and never retry an uncertain exposure automatically.

When the host deadline expires, the operation records `timeout` even if the
helper then completes within the cancellation grace period. A complete validated
reading is still retained, with `device_completion=completed`. A native error
arriving in that period keeps its original text and code; only an explicit helper
stop counts as `stop_acknowledged_by=helper`, not an unrelated driver failure.

A client cancellation racing with an unrelated driver failure keeps that failure's
stage, code and text, alongside `stop_requested_by=client` and
`stop_acknowledged_by=none`. A stop request does not turn a driver error into an
acknowledgment. Once a stop has been sent, queued instructions are cancelled
without another operator question or an extension of the stop grace period. A
helper that took the first cancel as its answer and exited is drained, so the
repeated cancel meeting its closed pipe is not reported as a new failure.

A failed operation can contain valid patches: a complete spot or sheet may have
been read before restoring the table failed. Preserve and export those patches
with the failed status and native stage/code; never reacquire automatically.
The complete validated batch is retained before patch notifications, so a
notification failure also cannot discard the remaining rows.

### What a reading records

Each patch carries an `Observation` whose units come from the reading's own
`inst_meas_type` when stated. If the driver leaves it unset (`0`), the shared
backend labels units from the requested mode and retains that distinction in
`units_source`. Its `configuration` carries:

| key | meaning |
|---|---|
| `native_mtype` | the driver's `inst_meas_type` for this reading |
| `units_source` | present when the native type was unset and units follow the requested mode |
| `native_mcond` | the condition that WAS present (`inst_meas_cond`), as distinct from the D50 a reflective XYZ is *computed* under (`inst.h:92-94`) |
| `spectrum_norm` | the divisor the spectral values are expressed against (`xspect.c:4380-4382`); present only with a spectrum |
| `correction_applied` | `none`, `matrix` or `spectral` — the explicit correction option the driver accepted; not an inventory of factory or display-type calibration |
| `confirmed_routes` | the routes `check_mode` confirmed |
| `acquired_at_source` | client receipt; the ladder publishes no instrument clock |

This is the vocabulary `processing.reflectance_fraction` and `spectral_exports`
read. Their bounded reflective routes require a stated reflective native type;
a requested-mode label with `native_mtype="0"` does not satisfy that requirement.
Canonical JSON/MAT retains the original result even when a specialized
conversion or spectral export is unavailable.

A flash reading additionally carries `warnings` saying what its duration means:
it is the instrument's *apparent* total duration, limited by its sampling rate
(`inst.h:126-127`), and the values beside it are already integrated over time,
so multiplying by it integrates twice. Ambient flash reports luminous exposure
(`lux s`) and spectral radiant exposure (`mW s/(m^2 nm)`). The native
emission-flash vocabulary reports time-integrated luminance (`cd s/m^2`) and
time-integrated spectral radiance (`mW s/(m^2 sr nm)`); no current named mode
routes to that type. These quantities retain their different angular bases:
[CIE luminance](https://cie.co.at/eilvterm/17-21-050) has a directional basis,
whereas [luminous exposure](https://cie.co.at/eilvterm/17-21-072) concerns
incident luminous energy per area. A zero
duration means unreported, not a zero-length exposure — zero is the field's
initialised state (`i1pro3_imp.c:1787`, `:4825`).

**`correction_applied` is reported by the helper, not inferred.** `set_disptype`
"clears `col_cor_mat()` and `col_cal_spec_set()`" (`inst.h:843-845`) *without
saying so*, so passing a `.ccmx` is not evidence the reading was corrected. If a
correction was requested and the driver applied none, the session fails rather
than publishing numbers that look corrected.
`none` means no explicit correction was applied through this option. It does
not mean the instrument lacks factory calibration or that its selected display
type applies no calibration or correction of its own.

### Serial

A reading can add the serial an instrument reports when the binding initially
had none. Retain `serial` and `serial_encoding` as one identity pair. Described
serials and explicit pins use `utf8`; an unpinned native reading may instead
carry `bytes`. Missing identity is the null/null pair. The
[record guide](data-format.md#device-serials) explains these representations.

Once a serial is pinned or learned, changing either its text or encoding is an
identity mismatch. The native helper verifies an explicit UTF-8 pin; the
operation validator also prevents a later patch from changing the learned
pair. Matching displayed text does not permit an encoding change.

## Published fixtures

`tests/fixtures/generic_frontend/` holds one persona per constructible Argyll
type, driven end to end, except the Spyder1 and Spyder2, whose initialization
downloads firmware: `i1_display_3`, `dtp41`, `spectroscan`, `spectroscant`,
`dtp20`, `dtp51`, `colormunki`, `i1pro2` and `i1pro3` over the hand-written
substituted instruments, `cr250` and `cr100` over the real CR driver, and the 26
others over the rows of `tools/native_argyll/driver_personas.c`, each transcribed
from its own driver. Each has `describe.json`, a request/result/events capture
per workflow, and `refusals.json` with one example per refusal class. Captures
are inline (`patch_v1`) where the reading fits an event; a JETI's 401 bands do
not, so its are recorded by reference (`retained_patch_v1`), which is how a
client must take them. `test_every_constructible_instrument_type_is_published`
derives the set from the build's own catalog.

A client pins against these rather than hand-written JSON, so a contract change
shows up as a fixture diff. `index.json` records each capture's own outcome
(`status`, patch count, `error_stage`, `error_native_code`): **a `failed` capture
is a recorded refusal by that substituted instrument, not a gap.**

The generator waits for both completion and the terminal event before retaining
a capture. Before completion, an empty nonblocking poll can mean the staging
condition is busy. After completion, polling reads the restored queue without
that condition; completion still does not prove the reader consumed its terminal
event. Missing terminal delivery at the bounded capture deadline
fails generation instead of writing an incomplete event history.

Each persona that reads also has one `<mode>.retained.json` — the same spot read
under `event_contract: "retained_patch_v1"` — so the `started` echo and the
`patch_reference` shape are pinned from a recording rather than from a client's
own double. `index.json` lists them under each persona's `retained_patch_v1`.

`<persona>/scenario.<name>.json` records an outcome a client must handle, with the
persona's `describe.json` as its descriptor, and `index.json` lists each under
`scenarios` with the status, error stage and retained patch count it must keep:
a DTP41 read that discovers a calibration need and recovers (`late_calibration`),
fails to calibrate (`late_calibration_failure`) or exhausts its single retry
(`repeated_calibration`); a DTP92 whose read-reported dark calibration completes
and whose repaired driver then reports no outstanding need (`late_calibration`); a DTP20
with nothing stored (`nothing_stored`); and a
SpectroScan whose table release fails after six patches (`xy_release_failure`) or
whose restore fails after one (`xy_restore_failure`); a ColorMunki whose dial is
wrong for a projector reading and is moved (`wrong_dial`), is wrong for a
transmission white while calibrating (`wrong_dial_calibration`) or is left wrong
(`dial_left_wrong`); an i1Display with its diffuser arm off the optics for an
ambient reading (`diffuser_arm`); and an i1Pro 3 without its ambient adapter
(`ambient_head`), off its tile while calibrating (`off_tile_calibration`), both
in one operation, each asked about once (`tile_then_ambient_head`), refusing a
calibration position so the operation fails at `calibrate` with no patch
(`calibration_position_refused`), or reusing a retained calibration
(`retained_calibration`). The
generator refuses a run whose outcome differs, and the CR failures are
`cr250/failure.<name>.json`.

`tests/fixtures/discovery/` holds the same thing for detection: one capture per
enumeration scenario, recorded from the substituted description worker. It covers
two units of one model at different paths, two candidates at one path, nothing
attached, a failed enumeration, the three LED-disclosing personas listed but
not attachable, and a Spyder1 that no acknowledgement opens because its
initialization downloads firmware. Each unit records every value
`DiscoveredInstrument` gives a picker, derived ones included. Its
`opening_requires` is the argument `describe` needs first: `null` when it needs
none, `expect` for an unidentified path, `accept_disclosed_initialization` or
`accept_unassessed_initialization` when a disclosed or an unassessed
initialization must be accepted before the unit opens, or `not_permitted` when
nothing opens it; `opening` repeats that per index. `describe` refuses a `not_permitted` unit
whatever is passed, and says so. `catalog.json` is
the build's own `native_type` -> symbol table, which is a build artefact and
never a table in client code.

The same index's `native_messages` maps scenario names to complete JSON responses
under `discovery/native/`: `listing` is the exact `list_candidates_json()` return;
each `descriptions` entry names a selected `path` and its `response` file. A
disclosed initialization also has a `without_acknowledgement` refusal file.
These responses include backend identity, raw events, baseline checks and JSON
nulls/arrays, as produced by `NativeDescriptionClient` over the authenticated
substituted worker. A consumer forwards their text unchanged; it must not rebuild
the native envelope from the normalized picker rows or round-trip it through a
frontend's struct representation.

Reordered paths, shared serials and missing serials are explicit native stub
variants for the openable two-unit scenarios. `typed_cr250` records a single
unidentified port and its typed description; its `expected_initialized_dtype`
comes from this build's catalog. These are synthetic routing scenarios, not
physical model capabilities. Duplicate paths retain the actual `ambiguous_path`
refusal. `catalog_without_contribution` points to the same catalog with the
declared contribution rows removed, for a missing-type refusal check; it is a
projection, not evidence of a separate stock build. Normalized enumeration
captures remain available as independent expected picker results.

Regenerate with:

```bash
uv run python tools/generate_frontend_fixtures.py --build <native-check-output>
```

`--check` regenerates into a temporary directory instead and fails if any
committed fixture differs beyond per-run fields or is missing or extra. The
failure names each such file and, for a differing one, the lines that differ,
so a one-off difference can be diagnosed after the temporary directory is gone.
The full-form gate runs it whenever both substituted workers come from one
build composed with the CR series.

## Status

The shared acquisition workflows — spot, strip, chart, X-Y and calibrate — have
software gate coverage over instrument personas. The persona catalog covers
every constructible Argyll type except the firmware-downloading Spyder1 and
Spyder2, with recorded captures for calibration operator actions.

**Strip support is in development.** Manual strip reads are available with
operator-declared ordering and returned-position sample association. Target
recognition, chart resumption and physical-device validation are incomplete.
Software gate coverage does not establish completed strip support.

Detection is implemented: `InstrumentBuild` enumerates, describes one chosen unit
and binds a frontend to it, with published captures for the enumeration cases a
device picker has to render. The last step of the chain a client could not take
for itself — "what is plugged in?" — is no longer the caller's problem.

X-Y is driven through this frontend. Its "operator-sighted fiducial exchange"
turned out to be the ordinary `xy_sheet` / `xy_locate` instruction pair: the
client sights and accepts, the **instrument** reports where its own sight ended
up, and the navigation vectors are derived from those points. The client never
sends a coordinate. A sheet is refused only where the instrument advertises
neither `inst2_xy_locate` nor `inst2_xy_position`, and the descriptor says so.

No hardware has been exercised. Physical device behaviour remains untested.

## Configured-mode queries and stored refresh

The descriptor offers `mode_snapshot` on every advertised mode. The request has
zero patch IDs, empty layout, `retained_patch_v1`, and common configuration
options. This explicit operation opens and configures the shared worker, without
calibration, trigger setup or acquisition. `describe_json` never opens a worker.
A successful query retains `configuration["mode_snapshot_json"]`: confirmed mode,
final capability words, display catalogue/readback, correction, exact
`requested_options`, typed `stored_refresh` and derived workflows. Its `source`
is `"mode_snapshot"`.

The frontend retains these facts under that mode's `capability_snapshot`, and
under every equivalent name that composes the same word, and recomputes its
descriptor. A display type the query applied stays in those facts. The
instrument-wide catalogue may take a changed catalogue, but never that choice or
its readback, because every operation starts at the driver default. Fetch `describe_json` after completion and use the
new `capability_id`. Earlier IDs are stale. This lets i1Pro, i1Pro3 and ColorMunki
offer refresh/timing after an emissive query even if their initial reflective
snapshot withholds those bits. Query-applied corrections also stay in the query
facts. Without a previously observed default readback, a corrected query does
not establish one for the descriptor. Facts describe the exact options and completed
helper lifetime, not a reservation or guarantee for changed settings. Later
operations independently configure and check the driver. Failed or cancelled
queries leave retained facts unchanged.

Each option has `by_mode` entries with availability, unavailable reason,
capability source and, for choices, that mode's values. Configured primary,
secondary and tertiary words feed the existing option owner independently for
each canonical mode; equivalent names share the same observed word. The top-level
option list is a catalog summary, not permission to use every feature in every
mode. Fetch the selected mode's entry. High resolution, averaging and filters
cannot leak from an emissive query into an unqualified reflective mode. A query
can withdraw an initially advertised modifier as well as expose one. Preparation
checks nondefault requests and filter choices against the selected mode; native
configuration remains authoritative. Neutral defaults request no unavailable
feature. Initialized capability candidates remain labelled separately from
configured observations and do not establish physical workflow support.

`stored_refresh` is a separate zero-patch configuration workflow, offered by the
selected mode's observed getter capability. Optional boolean `layout.calibrate`
defaults to false. True explicitly calibrates before the query in the same helper;
clients render its normal instructions. These queries require neither strip/X-Y
geometry nor a colour-reading allowance. Both complete with zero patches and
emit neither `patch` nor `patch_reference` reading events.

The `refresh_rate` option requires the independent setter capability: null means
no override; 0 requests the driver's reset behavior; nonzero values must be in
[5, 150] Hz. Spyder resets to 50 Hz without changing validity. The option is
applied after display/corrections and before calibration or reading, so a reset
does not establish a calibrated stored rate. It cannot accompany
`refresh_frequency` or `display_timing`. Getter support never implies setter or
fresh measurement support.

`configuration["stored_refresh_json"]` contains `native_method: "get_refr_rate"`,
`native_code`, `status`, `unit: "Hz"` and nullable `value`. `available` requires
native success with finite positive Hz. `unsupported`, `needs_calibration` and
`undetermined` have null values. `not_advertised` has a null `native_code`
because the configured word lacks `inst2_get_refresh_rate` and nothing was
called. A `mode_snapshot` always asks, so a query on an instrument without a
getter records that state. Invalid successes and other native failures fail the
operation; a failed operation keeps a stored state only after it validates, and
never a reported value beside a non-success code. The accepted override is
retained separately as `refresh_rate_override_hz`, only when it is a numeric
value equal to the request. Boolean values are not numeric overrides. These facts are
not freshly measured frequency records. Every worker is fresh; only device-backed
getters or explicit work within that operation provide state. The in-process demo
models overrides and unavailable states, without inventing a calibrated rate
from replayed calibration.

## Frozen prepared request

`InstrumentFrontend.prepare(request_json)` validates and normalizes one request
at the existing request owner and returns a held operation with no worker or
device I/O. Its read-only `request_json` exposes that same frozen normalized text,
including the default event contract. Reading or decoding the text does not
alter the request; mutating the caller's source values or a decoded copy does
not affect it. No second validation/normalization API exists.

The accessor's implemented source in `InstrumentOperation` is:

```python
    @property
    def request_json(self) -> str:
        """Return this prepared operation's frozen, normalized request text."""
        return self._initial_result["request_json"]
```

The headless application retains this handle, operation UUID and request before
publishing durable intent, then calls `operation.dispatch()` on that held handle.
Dispatch rechecks current capability identity and owns worker admission. Prepared
handles take no acquisition lease; cancelling one before dispatch produces a
terminal zero-reading result, with confirmed software stop. A failed intent
write can therefore retain and drain that cancellation without acquisition.
Ordinary preparation refusal also performs no dispatch and cannot establish
cleanup uncertainty. A dispatch exception requires draining the retained handle
rather than guessing whether its worker started.

`tests/test_instrument_frontend.py` checks one validation, no worker/session,
read-only access and isolation from source/copy mutation; the batch tests check
identity/request retention before intent and cancellation on failed reservation.
