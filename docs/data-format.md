# Observations and file formats

The [record model](../src/color_measurement/models.py) separates source readings
from calculations. Acquisition uses either the synthetic source or the shared
Argyll API. Substituted workers produce simulated records. Physical or replay
provenance in a record cannot establish that claim's truth or hardware accuracy.

## Observation schema 1

An `Observation` contains:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1`; other versions are refused |
| `observation_id`, `operation_id`, `sample_id` | Reading, acquisition operation and caller's sample identifiers |
| `acquired_at` | ISO timestamp with a UTC offset; simulator timestamps use UTC |
| `provenance` | `simulated`, `replay` or `physical`; simulator emits `simulated` |
| `device` | Device ID/model and backend/version; [serial text and encoding](#device-serials) |
| `requested_mode`, `actual_mode` | Must agree; implicit mode fallback is rejected |
| `configuration`, `calibration` | Retained settings and calibration status/detail |
| `backend_reported` | Native XYZ and/or spectrum; at least one must be present |
| `project_computed` | Separate xyY and Lab calculation records |
| `raw_backend_output`, `warnings` | Original source text and interpretation warnings |

The simulator's original source text is synthetic JSON. It is not an instrument
transcript or raw detector data. Calibration is `not_applicable`.
The generic API retains native result events and their session context,
including calibration, type, condition, normalization and duration. Each batch
row includes its own native patch frame, not the other rows. This is driver
output, not raw detector samples. Native XYZ/spectral quantities are labelled
from `inst_meas_type`, and mismatches with the requested mode/condition are
refused. Signed spot XYZ is preserved using `instNoClamp`; strip/chart clamping
is driver-defined. No missing value becomes a zero measurement.

`configuration` retains `native_mtype`, `native_mcond`, `spectrum_norm` when
present, `native_duration_seconds`, `correction_applied`, `confirmed_routes`,
and `native_clamping`. A reading whose driver left its type unset keeps
`native_mtype` `"0"` and adds `units_source`: its units are its requested
mode's, as upstream labels it. Strip records also retain `strip_direction`,
`strip_direction_source` and `native_patch_index`; IDs follow the declared
order, not automatic target recognition. Flash quantities are already
integrated exposures; native duration is not a second integration factor.

A driver with a support adapter also leaves `native_facts_json`: what the driver
itself retained about the attempt that produced the reading, as it sent it. Today
the CR family (CR-100/250/300) and PR-655/670 have adapters in a build that
composed the CR/PR contribution. The CR packet described here
carries the verified identity (model, serial, exact firmware text and parsed
components, instrument type), retained single-line replies in order (`status`,
`code`, `context`, `value`), the native XYZ (`xyz`: RM XYZ whenever the read parsed it,
kept on a refused read; `valid` records capture validity, while spectral
publication also requires matching actual output), the
matched inventory records, the measurement budget labelled as host policy,
`required_s` (that budget plus the driver's worst case for the capture reads after
M) beside the host's allowance for the read (`host_allowance_s`) and what of it
remained just before M (`remaining_s`, null when no allowance was set or the
comparison was not reached), `factory_calibration: "unknown"` and the XYZ origin.
The retained replies, the allowance fields and which native values were parsed
come from this application's transform of the driver
(`tools/native_argyll/cr250_host.py`), not from the Argyll contribution. It also carries:

- `problem`: the check that refused the read, with both values, or null;
- `settings`: every setting the model row checks -- `key`, the `value` accepted
  from RS (null when the model lacks it on this connection), `presence` and
  whether the driver sets it (`driver_set`);
- `writes`: every setting the driver wrote for this read -- `key`, `before`,
  `target`, `after` (null when not reached) and `confirmed`. The instrument keeps
  a written value until it is powered down; the driver does not restore it;
- the exposure the capture ran with (`exposure_ms`, `fixed_exposure_ms`,
  `max_auto_exposure_ms`, `exposure_x`), `sync_hz`, and `radiometric` (RM
  Radiometric radiance in W/(sr.m^2) and photon radiance), each null when not
  read; `radiometric` is also null when its triplet did not parse or is not of
  the model's type, and a zero or signed dark reading is kept as the value it
  is. A synchronized capture whose RM SyncFreq period is longer than the M
  budget is refused: the capture that finished within the budget cannot have
  integrated such a period;
- `spectrum`: the native RM Spectrum grid. Its `unit` is null because the remote
  manual does not establish the density denominator. A `consistent` check supports
  `inferred_unit="W/(sr.m^2.nm)"` with
  `unit_basis="same_capture_consistency_under_driver_model"`; otherwise
  `inferred_unit` is null and `unit_basis="unverified"`. This inference remains
  available if a later publication step fails. `published_unit="mW/(m^2.sr.nm)"`
  and `published_scale=1000` appear only when the final Argyll output contains
  that capture's nonzero spectral count (`xspect.h:40-42`); both are null otherwise.
  Completely parsed native values are retained without scaling, on success and
  refusal, in the joined `values` list. This basis must survive persistence,
  exports and derived provenance in `units_source`, warnings and
  `input_quantity_basis`; it does not establish manufacturer-confirmed physical
  spectral units. Published fields additionally require a consistent, valid
  capture and an actual output count exactly matching the native grid;
- `unit_check`: the spectrum against the instrument's own totals for the same
  capture -- a `verdict` (`not_checked`, `consistent`, `inconclusive` or
  `mismatch`, which refuses the read), which comparisons were made
  (`discriminating`: `power`, `axis`, `luminance`, `chromaticity`), and their
  numbers. Power and luminance are compared when either the instrument's total or
  the spectrum's own reaches its threshold, so a dim native reading does not
  excuse a spectrum that claims far more light; the wavelength-axis and
  chromaticity comparisons also need the native side above its threshold.
  `inconclusive` means neither the instrument's totals nor the spectrum's own
  reach a comparison threshold, so no comparison could discriminate. It refuses
  requested spectral publication with explicit XYZ-only guidance, as does
  `not_checked` or `mismatch`; the driver does not silently downgrade the read. Explicit
  XYZ-only reads, including zero readings and CR-100, remain available. A number is null,
  never 0, when the driver did not form it: its comparison was not made, or it
  has no finite value -- a ratio to a total that is not positive or that
  overflows, a native X+Y+Z that is not positive, the mean wavelength of a
  spectrum summing to zero. A comparison made without a value refuses the read,
  and the refusal text says "no value". `mismatch` is also the verdict for a
  spectrum that is not finite once scaled to mW, summed or integrated: nothing
  derived from it is compared, every number is null, and only the values as
  sent are kept. Spectral publication additionally requires 4–601 samples within
  200–1,100 nm with a step of at least 0.1 nm. These are driver policy limits,
  not the limits for retaining a raw declaration. A synthetic capture agreeing
  with itself shows consistency under the declared model, without proving
  the physical unit or manufacturer spectral-unit contract;
- `requested`: the mode word, averaging and refresh rate the host asked for.

The current CR raw declaration can retain a complete finite header and
1–10,000 finite signed values, including a header that cannot form a usable
wavelength axis. A usable CR grid needs at least two values, positive start and
step, a reported end greater than the start, strictly increasing calculated
wavelengths and an endpoint discrepancy no greater
than `1e-8 * max(1, end_nm)` nm. The calculated wavelengths use `start_nm + i *
step_nm`; the original reported end remains in native facts. Accepted Argyll
publication also retains its actual output start/end/count grid. No grid is
resampled to make it fit.

On a refused reading, a usable native grid can form a saved `Spectrum` with
unverified units. If the raw declaration cannot form that strict axis, its header
and vector remain in `native_facts_json`; independently parsed XYZ can still
form the failed observation. `native_spectrum_mapping_refusal` records that
additional refusal in the observation configuration and warnings, without
replacing the primary acquisition failure. Missing or incomplete numeric bodies
cannot claim a parsed vector and remain raw bytes. Neither form becomes a
successful measurement.

The adapter does not re-query the instrument or recalculate XYZ or spectral bins.
The top-level `identity` describes current verification. `capture.identity` is
the separately verified capture-start snapshot. After quarantine invalidates the
current identity, only failed-original attribution can use that snapshot, still
checking the selected model and reported serial. Successful publication requires
current verified identity. Unverified identities retain null native fields.
Replies keep their order and the
instrument's own `context`, whose command prefix is the grouping: `RS <setting>`
is the current setting checked before M, `RM <setting>` the same setting as the
last capture recorded it, `RC` a configuration or inventory read and `M` the
measurement. They are not duplicated into separate groups; a client groups by
that prefix, and never needs to parse a reply's prose. Parsed reply lists retain
matched inventory records rather than every reply line. Counted `native_record`
events reconstruct `matched_records` and `range_record`; missing, duplicate or
reordered records refuse the packet. Separate `raw_replies` hold bounded original
received bytes, including malformed input and partial table tails, with the
requested command, framing completion and error. They are not a transcript of the
entire instrument lifetime. Each control or capture trace admits 256 exchanges,
1 MiB per exchange and 4 MiB in total; `trace_overflow` records retention failure.
The spectrum header and parsed values remain separate from those bytes.

Calibration readiness comes from the native negotiation. The default upstream
status method leaves readiness unknown. Completed negotiation with no remaining
nondeferrable work is driver-reported valid, including permitted optional skips.
Remaining work is the driver's assessment after the negotiation completed: a
nonzero one is `required`, with a detail naming it as that later assessment,
never as the negotiation's.
This is distinct from work performed, reference identity, physical validation or
certification. Operation configuration retains `calibration_outcome_json` and
`calibration_events_json`, including calibration-only operations.

Direct Python records require the declared nested record types. Configuration
is an immutable tuple of text key/value pairs with unique keys; warnings are an
immutable tuple of text. Calibration status must be one of `not_applicable`,
`unknown`, `valid`, `required` or `failed`, with a text description. A frozen
record does not accept mutable lists in those metadata fields.

XYZ retains a three-component tuple, unit, scale, observer and illuminant text.
These fields describe caller-supplied context; a tuple alone cannot establish
units or a reference condition. The simulator declares relative XYZ with
reference-white Y=100, unit `1`, and unspecified observer/illuminant.

A spectrum, when supplied through the record API, retains wavelength samples
in nm, corresponding values, quantity and unit. Wavelengths must be positive,
finite and strictly increasing; signed values and irregular grids are accepted.
The CLI simulator and XYZ-only colorimeters emit no spectrum. Substituted
spectrometers supply explicitly simulated spectra with the route's quantity.
The Python record API does not integrate these spectra into project-computed XYZ.
The CR-250 backend separately uses Argyll's integrator for its
[spectral consistency check](science.md#cr-250-spectral-consistency-check);
published XYZ still comes from the instrument. Grid validation does not establish
optical bandwidth or instrument accuracy.

For the declared M1 percent-reflectance contract,
[`processing.reflectance_fraction`](science.md#reflectance-percent-to-fraction)
returns a separate unit-one `Spectrum`. It preserves the wavelength grid and
does not change the observation, native spectrum or `project_computed` fields.
Keep the original observation with this derived spectrum to retain its context.

## Capture metadata

Native facts are retained in observation `configuration`, raw event text and
warnings. Operation configuration also keeps requested options/layout and the
calibration negotiation. Absent metadata is not proof of a physical condition.
The generic frontend documents [what a reading records](generic-frontend.md#what-a-reading-records).

## Missing and computed values

An optional field set to `None` becomes JSON `null`. A missing spectrum is not
an array of zeros. Within `project_computed`, a missing calculation record is
different from an attempted calculation that was unavailable:

- `status="ok"`: three finite values and `reason=None`.
- `status="unavailable"`: `values=None` and a nonempty explanation.

Each computation records its algorithm/version and reference context. Every Lab
record requires an explicit white with finite positive components. Native XYZ
remains intact if its chromaticity is undefined. A genuine zero measurement or
zero Lab component remains numeric zero.

## Device serials

[`DeviceIdentity`](../src/color_measurement/models.py) keeps `serial` and
`serial_encoding` together. This identity appears in operation envelopes and
colour, scalar and timing observations. Both fields are required in JSON:

| `serial` | `serial_encoding` | Meaning |
| --- | --- | --- |
| `null` | `null` | No serial was reported or pinned |
| Nonempty Unicode text | `"utf8"` | Decode the native serial as UTF-8 |
| Nonempty text whose code points are in 0–255 | `"bytes"` | Each code point preserves one native byte; no character encoding is inferred |

Neither text form permits NUL. Empty strings, an absent marker beside a serial,
and a marker beside a null serial are refused. The `bytes` representation is
byte preservation, not a claim that the instrument uses Latin-1 for text.
For example, the same displayed character represents different native bytes:

| Serial text | Marker | Native bytes, hexadecimal |
| --- | --- | --- |
| `é` (U+00E9) | `utf8` | `C3 A9` |
| `é` (U+00E9) | `bytes` | `E9` |

Keep the marker when copying, comparing or exporting a serial. Description and
`expected_serial` pins use UTF-8; an unpinned acquisition can retain opaque
native bytes. A previously unpinned identity may learn the pair from a reading.
After that, both the text and marker must agree across the operation's readings.
[`identity_agrees`](../src/color_measurement/models.py) and the
[operation validator](../src/color_measurement/operation_records.py) enforce
that rule; matching text alone does not identify matching serial data.

Canonical JSON and the JSON retained inside operation MAT preserve both fields.
The observation CSV also keeps them in `record_json`; it has no separate serial
columns. A serial and its encoding do not establish physical provenance or
measurement accuracy.

## JSON

[`to_json` and `from_json`](../src/color_measurement/exports.py) serialize and
reconstruct the complete schema. JSON arrays reconstruct immutable tuples.
The decoder rejects duplicate/unknown/missing fields, invalid types, unsupported
schema versions and nonfinite scientific numbers. The serializer validates
directly constructed records too. Unicode text is preserved.

This round-trip contract applies to the Python record APIs and canonical JSON
passed through a host unchanged. A host-decoded structure is an inspection view,
not automatically a lossless replacement. MATLAB's `jsondecode`, for
one, turns optional JSON `null` values into `[]`; re-encoding that view changes
the schema and `from_json` rejects it. Retain the original JSON text or file for
saving and reload it through Python.

Do not treat JSON containing plausible field names as a validated reading:
load it through `from_json`. Structural validation cannot verify that claimed
physical provenance, units or calibration describe a real acquisition.

## Scalar observations

`ScalarObservation` is the non-photometric branch of a retained reading.
`record_kind` is `"scalar"`; identity, acquisition timestamp, provenance,
requested/actual sensor mode, configuration, calibration and raw native events
use the same context invariants as colour observations. Its native quantity is:

```json
{"quantity": "refresh_frequency", "unit": "Hz",
 "native_method": "read_refrate", "value": 59.94}
```

The value must be a finite positive number. It is measured by the driver, not
retrieved from a stored rate or copied from a configuration override.
`stimulus` holds `stimulus_id`, `description` and `source: "external"`.
Identity and description are bounded to 128 and 2048 UTF-8 bytes respectively.
They must agree with the retained request. They describe what the caller
declared, not independently verified display output.

XYZ, spectrum, observer, illuminant, M-condition and `project_computed` are
absent. Sensor mode and calibration remain operational context, not
photometric labels attached to frequency. No missing or failed measurement
creates a scalar. A failed or cancelled operation can retain a complete scalar
delivered before software stopped, alongside its actual terminal failure.

## Timing observations

`TimingObservation`, `record_kind: "timing"`, retains the common identity,
mode, calibration, provenance and raw-event context without photometric fields.
Its `backend_reported` is a native result, for example:

```json
{"display_update_delay_ms": 37, "instrument_reaction_ms": -230,
 "unit": "ms", "native_method": "meas_delay", "time_reference": "white_change_call"}
```

Display delay is a nonnegative signed-32-bit integer. Reaction is signed;
`INT_MIN` is reserved for an unwritten native value. Neither error defaults nor
missing values become measurements. The native integer delay is preserved,
including zero. All five implementing drivers clamp negative integer delays
to zero; millisecond rounding can also produce zero from a positive delay.
A recorded zero therefore does not prove exact zero or a nonpositive underlying
delay. Klein K10 advances the clamped value to the next sample-spacing multiple.
Only the spectrophotometer drivers measure reaction; the i1Display Pro family
always reports 0 and Klein K10 a value derived from its sample spacing.
See the [driver interpretation](instrument.md#controlled-display-timing).

`stimulus` contains `stimulus_id`, `description`, `display_id`,
`source: "client_controlled"` and `transition: "black_to_white"`. Identifiers are
bounded to 128 UTF-8 bytes and the description to 2048. These declarations must
match the retained request. `timing_context.presentations` contains exactly
black then white. Each stamp holds phase, stimulus/display IDs, client `clock_id`,
finite nonnegative `presented_at_seconds`, and `source: "client_draw_completed"`.
The two stamps use the same identities and clock, with increasing times.

The remaining context fields are `native_clock_id: "worker_monotonic"`,
`method_window_started_seconds`, `marker_call_started_seconds`,
`marker_call_finished_seconds`, and `method_window_finished_seconds`. They are
finite, nonnegative and ordered in this worker clock. The marker begins at
least 200 ms after the window start. This wrapper window includes notification
and thread setup; it does not claim first sampling time. The client clock and
worker clock have unrelated epochs. They are retained separately, not combined
into a supposed presentation or optical latency.

Use operation JSON/MAT persistence. The MAT inspection row has native integer
timings, stimulus, and a context struct with four double timestamps and a 1-by-2
cell array of presentation structs. It contains no empty colour quantities.
The canonical JSON remains authoritative; mismatched inspection values fail
import. The record establishes structural and software exchange validity,
not physical display response or independently verified optical onset.

## Operation results

[`OperationResult`](../src/color_measurement/operation_records.py) is a separate
schema-1 operation envelope containing ordered `PatchResult` entries, each with
either a colour, scalar or timing observation schema-1 record.
Use `result_to_json` and
`result_from_json` from that module; the observation JSON/CSV APIs still accept
individual observations. An envelope retains the original request JSON,
configuration, calibration context, device identity, provenance, expected patch
IDs, terminal status and separate software/instrument state. Observation warnings
remain attached to each original reading. See the
[frontend guide](frontend.md#operation-results) for the complete contract and
the frontend's request normalization.

The `refresh_frequency` workflow declares exactly one sample in `patch_ids`.
This existing container indexes the reading; the display stimulus has its own
identity. Its entries must be scalar observations. A scalar cannot enter a
colour workflow. Use operation JSON/MAT APIs for scalar persistence; individual
observation CSV and SPECT export remain colour formats.

`display_timing` also declares exactly one sample, with only timing observations.
Its layout carries the stimulus identifier/description and display identifier.
Timing observations cannot enter scalar or colour workflows.

The scalar MAT inspection row contains `record_kind`, its index/identifiers,
timestamp, configuration, calibration, native scalar, stimulus and warnings.
It contains no empty colour fields. The canonical `record_json` stays
authoritative, and a contradictory MAT inspection view is rejected.
Saving and explicit save retry use the same exclusive publication mechanism
as colour operations and never repeat acquisition.

Completed measurement operations require every expected patch once, in order.
Failed or cancelled operations can retain a validated subset, including gaps or
no readings; absent patches are not zero measurements. Calibration operations
contain no measurement patches. A complete patch set can still have a failed
operation outcome, for example after cleanup failure. Validation checks identity
and completeness, not physical provenance or instrument readiness. Request option
and capability validation belongs to the request producer; envelope validation
does not establish that arbitrary retained options were supported.

The generic native client validates an entire returned batch and terminal event
before exposing a `Measurement`. An incomplete or invalid scan produces no
observations; raw failure events remain available on the exception. A client
cancellation after native completion retains the completed readings with a
cancelled operation status. A later publication failure can retain a subset in
the common operation envelope. Optional device identity may be refined once;
a pinned identity or a previously observed serial/encoding pair may not change
across rows.

## Operation files: JSON and MAT

Native settings operations use the existing schema-1 operation envelope with
`workflow="native_controls"`, no expected patch IDs and no patches. Their
configuration retains `native_control_json`, containing validated original
before/current/requested settings and independent write/readback masks.
CR and PR raw replies retain contiguous counted hex bytes, completion and error,
including malformed, non-ASCII and NUL bytes. Parsed CR replies retain
status/code/context/value only when complete. Unknown current values remain
null. Settings and subsequent capture raw traces are joined within separate
counted envelopes even when exchange indices restart in one worker lifetime.

PR `native_facts_json` retains independently parsed M5 spectra and D2 XYZ
originals after publication refusal. A complete M5 survives a later failed D2 or
restoration; absent quantities stay absent. Native spectral density units remain
unknown. The same-capture driver's verdict, inferred unit, manual-model basis and
actual published unit/scale remain separate. PR's manual M5 integral and D116
model can qualify dim publication under an inconclusive consistency verdict;
that verdict does not establish inferred native units. CR keeps its distinct
consistency-only spectral publication rule.

PR `capture.xyz_publication` retains the actual final C-owned XYZ, `unit="cd/m2"`
for emission or `"lux"` for ambient, observer 2 and `native_to_published_scale`.
Python checks that the actual output
matches both this declaration and the retained native source under its declared
factor; it neither repeats the driver's unit conversion nor infers missing XYZ.
The actual native measurement type must be emission (1) or ambient (2), with
matching XYZ and spectral publication units; the requested mode cannot supply
a missing type. Ambient spectra publish `mW/(m^2.nm)` at scale 1000, with
`inferred_unit="W/(m^2.nm)"` only for a consistent same-capture verdict. Their
native `unit` remains null. Emission keeps its separate solid-angle denominator.
The C driver owns the selected XYZ factor: Metric is one; English uses the
footcandle factor for ambient and foot-lambert factor for emission. Python
matches output to that declared factor, without independently deriving it from
English/Metric setup. Raw failed originals do not acquire published units.
Publication and raw-original admission both require the bound model/serial.
The complete raw events remain available after mapping or cleanup failure.

The generic instrument frontend defaults to the reference-event profile; it
can also be requested explicitly by adding
`event_contract: "retained_patch_v1"` to a [generic frontend
request](generic-frontend.md#requests). **These native frontend envelopes stay
schema 1**: descriptor, request, events, `OperationResult` and `Observation`.
The [batch manifest](#batch-manifest-schema-5) is a separate schema-5 contract. The canonical
`request_json` retains the contract that was selected, and the `started` event
echoes it. A `patch_reference` contains no observation and is not a persistable
substitute for the terminal record. Full values, raw history, calibration and warnings remain unchanged;
JSON/MAT persistence below uses that complete record. Notification or reference
verification failure does not authorize dropping valid canonical data.

[`operation_exports`](../src/color_measurement/operation_exports.py) persists
terminal operations without a frontend or device handle, including failed,
cancelled, partial and zero-reading calibration outcomes. Observation
[`exports.save`](../src/color_measurement/exports.py) remains a separate API.

| API | Behavior |
| --- | --- |
| `save(result, path, format="json", previous_ti3=None)` | Publish a new JSON or MAT file without overwrite, then attempt TI3 companions; return `SaveResult(path, ti3)` |
| `load(path, format="json")` | Validate and reconstruct an operation; format is explicit, not inferred from the extension |
| `to_mat(result)` / `from_mat(payload)` | Encode/decode MAT bytes using the optional `mat` extra |

```python
from color_measurement.operation_exports import OperationSaveError, load, save

# result is an already terminal OperationResult, including a failed/partial one.
try:
    saved = save(result, "operation.json")
except OperationSaveError as error:
    saved = save(error.result, "operation-retry.json", format=error.format)
print(saved.path)           # The complete operation was saved here.
print(saved.ti3["status"])  # Independent optional output: inspect files/reasons below.
```

I/O failures retain `result`, complete binary `payload`, `path` and `format`.
Validation and missing-dependency errors occur before publication. The
[exclusive temporary-sibling contract](#saving-and-retrying) also applies here;
there is no automatic save retry or acquisition. A TI3 companion failure does
not undo a successful canonical save. `SaveResult` is not a `Path`; use its
`.path` member when passing the saved filename to another API.

Install MAT support from the checkout with `uv sync --locked --extra mat`, then:

```python
saved = save(result, "operation.mat", format="mat")
restored = load(saved.path, format="mat")
assert restored == result
```

The level-5 MAT payload contains exactly `export_schema=1`, `record_json`, and
`operation`. Canonical JSON retains the full record, including nulls, raw source
text, original request and computed values. `operation` is an inspection view:
`patches` is a 1-by-N cell array of structs; XYZ, wavelengths and spectral values
are row vectors. Each patch keeps its grid, units and zero-based
`index_zero_based`; configuration uses N-by-2 key/value cells. Presence flags
distinguish missing quantities, serials and errors from zero or empty text.
No clipping, resampling or fabricated missing-patch rows occurs.

The inspection view's `operation.device` contains `serial`, `serial_encoding`
and `serial_present`. When absent, `serial_present` is false and both text
fields are empty; the canonical JSON keeps the original null/null pair. Read
each observation's retained identity from `record_json` when a reading has
learned a serial that was absent from the operation's initial identity.

ASCII/BMP text is a character row. Non-BMP inspection text uses a plain struct
with `encoding='UTF-16'` and a uint16 `code_units` row. Decode either in MATLAB:

```matlab
data = load('operation.mat');
canonical = data.record_json;  % Keep unchanged for lossless reconstruction.
value = data.operation.device.model;
if isstruct(value) && strcmp(value.encoding, 'UTF-16')
    value = char(value.code_units);
end
```

Python reload checks field names, types/shapes and agreement of the entire view
with canonical JSON; editing only MATLAB numerical fields is rejected. This is
not arbitrary-MAT or v7.3/HDF5 import. NUL/invalid Unicode in inspection text,
numbers not exactly representable as doubles, and writer warnings cause refusal;
JSON remains available for records outside the projection. The writer uses
ordinary structs/cells and explicit row orientation, never Python objects.
[SciPy `savemat`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.io.savemat.html),
[SciPy `loadmat`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.io.loadmat.html).


The implemented file writer validates/serializes before exclusive publication:

`operation_exports.publish(result, path, *, format="json")` publishes only the
canonical JSON or MAT operation and returns `PublicationResult`: `path`,
exact `payload` bytes, observed `file_sync` (`fsync` or `fullsync`) and
`directory_synced`. Its `limitations` report narrower synchronization. These
are filesystem-call observations, not power-loss or physical storage proof. It retains the same
validation, no-overwrite publication and `OperationSaveError` recovery as `save`.
The headless batch owner uses this seam to accept canonical records before
attempting grouped batch TI3 companions. Standalone `save` composes `publish`
with operation-level companions for its current callers.

<!-- source: src/color_measurement/operation_exports.py:publish -->
```python
def publish(
    result: OperationResult, path: str | Path, *, format: str = "json"
) -> PublicationResult:
    """Publish canonical bytes and return observed synchronization separately.

    The caller owns acceptance and optional export ordering. Publication never
    overwrites an existing path; I/O failures retain the result and payload.
    """
    if format not in ("json", "mat"):
        raise ValueError("format must be json or mat")
    payload = result_to_json(result).encode("utf-8") if format == "json" else to_mat(result)
    target = Path(path)
    return _publish_new_file(
        target, payload, lambda cause: OperationSaveError(target, result, payload, format, cause)
    )
```

<!-- source: src/color_measurement/operation_exports.py:save -->
```python
def save(result: OperationResult, path: str | Path, *, format: str = "json",
         previous_ti3: dict | None = None) -> SaveResult:
    """Publish one complete new JSON/MAT file, without overwrite or acquisition.

    I/O failures retain the result and payload in OperationSaveError. Parent
    directories must already exist. Publication uses the same exclusive hard
    link and temporary-sibling contract as observation exports. Call again with
    the retained result and a new path to retry; no automatic retry occurs.
    SaveResult.path identifies the durable source. SaveResult.ti3 reports the
    optional owned companions independently, including explicit non-color and
    zero-reading dispositions. previous_ti3 is the complete last writer result.
    """
    publication = publish(result, path, format=format)
    payload = publication.payload
    target = Path(path)
    from .ti3_exports import save_companions
    return SaveResult(target, save_companions(result, target, payload, previous_ti3))
```


The shared publisher synchronizes the temporary file before publication, uses
macOS `F_FULLFSYNC`, and synchronizes POSIX directory entries after publication
and temporary cleanup. Windows has no standard-library directory-sync step.
On Windows, strict owned-file reads use a binary handle with read/write/delete
sharing. Owned replacement uses `SetFileInformationByHandle(FileRenameInfoEx)`
with `REPLACE_IF_EXISTS | POSIX_SEMANTICS`, so existing destination readers keep
their old file while the name atomically refers to the newly synchronized file.
The source is opened with additional `DELETE` access and reauthenticated as the
single ordinary inode that was synchronized. Unsupported API/filesystem and
readonly/access failures remain failures; there is no copy/unlink or legacy
rename fallback and no flag to ignore readonly protection. The
handle opens the final path component's reparse point itself. File type,
single-link and descriptor/path identity checks refuse unsafe or replaced
entries at opening; retained-source validation handles a later authorized
replacement while the old descriptor is read. Re-synchronizing
identical bytes requests write access because Windows CRT `_commit` (used by
`os.fsync`) requires it; ordinary authentication remains read-only. See Microsoft's
[CreateFileW sharing and reparse-point semantics](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew),
[`_commit` access requirements](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/commit),
[`FILE_RENAME_INFO`](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_rename_info)
and the [POSIX rename flag semantics](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information).
After linking or renaming, the publisher authenticates the actual destination's
original temporary-file identity and exact expected bytes before recording
success. After a failure, another inode at the name, such as the existing file a
refused save leaves in place, is reported as no publication rather than a failed
authentication. This also checks effects of name-based linking and POSIX replacement
if the staging name changes after its source descriptor was authenticated.
New-file authentication permits two links only while the known temporary name
is an ordinary alias of that same inode; ordinary owned-file readers still
require a single link. Temporary cleanup checks the inode captured at creation
and leaves an observed replacement entry untouched. Confirmed publication is
recorded before either authentication descriptor closes, so a later close
failure retains its publication receipt.
An ordinary cleanup I/O failure after publication is reported as a warning; existing
canonical bytes do not become an absent save. Directory-sync failure retains the
exact payload and explicitly notes that publication occurred while synchronization
is unconfirmed. This is software publication evidence, not power-loss testing.

Batch application saving uses canonical-only `publish`, accepts the current
manifest, and leaves optional batch TI3 refresh pending until explicit
`export_ti3`, Retry save or Finish. Metadata edits publish the manifest without
rewriting/rereading every operation or invoking the companion writer. The
[application owner](application.md) preserves native bytes, versioned annotation
drafts, saving failures, raw terminal recovery and explicit abandonment status.

## Batch manifest schema 5

`batch.json` is the saved manifest of the [shared descriptor-authorized batch
owner](application.md), with an explicitly selected frontend, advertised mode
and validated options. The frozen descriptor retains the actual device,
capability identity and physical, simulated or replay provenance. Demo is an
explicit simulated choice; constructing or saving a batch establishes no
physical-device validation.
Its schema is separate from the schema-1 operation and observation records it
references. The manifest contains exactly these required top-level fields:

| Field | Implemented meaning |
| --- | --- |
| `schema_version` | Integer `5`; unsupported values and non-integer types are refused. |
| `batch_id`, `context_id` | Canonical UUID strings identifying this batch and its frozen acquisition context. |
| `batch_name`, `batch_notes` | Frozen setup text. Name requires non-whitespace text and at most 1,024 characters; notes allow at most 65,536 characters. NUL is refused; valid Unicode and whitespace are preserved. |
| `revision` | Nonnegative integer manifest revision. Renderer generation and the command ledger remain in the live owner, outside this file. |
| `status` | `open`, `finished` or `abandoned`; a closed live owner does not rewrite these saved statuses. |
| `descriptor` | Frozen generic instrument descriptor with validated device identity, provenance and a capability ID authenticated against its JSON content. Each reserved request must match its device and capability identity. |
| `mode` | Explicitly selected, advertised, available mode offering a supported workflow. Each reserved request must match it and its descriptor-authorized workflow, patch identities and layout. Ordinary Measure requires exactly one spot reading; generic workflows use the same owner. |
| `draft` | Current sample `id`, `version`, resolved `name`, `notes`, `options` and boolean `number_samples`. The ID is a canonical UUID and version is a nonnegative integer. Each acquisition intent freezes its own copy. Sample labels allow at most 1,024 characters and refuse Unicode controls and line/paragraph separators. Numbered labels require a positive resolved ordinal whose immediate successor is representable within 1,024 characters (the validator checks it; the live owner additionally skips ordinals its retained readings consumed); exact mode preserves meaningful digits. |
| `operations` | Ordered acquisition intents with reserved paths, prepared operation UUIDs, frozen requests/drafts, state and exact-byte references, described below. |
| `annotations` | Applied per-reading `operation_id`, `observation_id`, `name`, `notes` and `version`. Identities are unique as a pair and belong to an accepted operation. Names use the same single-line sample-label rules; notes remain separately bounded multiline text. |
| `destinations`, `destination_issues` | Nonempty history of absolute POSIX, Windows drive or UNC destination strings, with exact original spelling and issues keyed only by those strings. These are provenance; current writable folders are selected and authenticated separately. Rescue does not grant overwrite authority over unrelated files. |
| `shutdown` | `null`, or the abandonment record with `reason`, `cleanup_confirmed`, `instrument_state` and confirmed `discarded` contents, excluding the live confirmation token. Abandoned status requires this record. |
| `chart` | Null for ordinary batches; otherwise the validated guided-chart state whose segment UUID equals the batch UUID. Portable parent manifests and their native operation/recovery bytes are stored once in destination-owned `source-<sha256>.json` files. Read-only review authenticates the graph and reconstructs context from original native records. |
| `comparison` | Nonnegative `version`, nullable exact operation/observation `reference` pair, nullable explicit `white` declaration and `white_descriptor`. Retained white declarations use the exact pair; theoretical declarations preserve name, observer, reference Y, unit and scale. Resolved assumptions are validated by the existing white owner and against authenticated native source on review. |
| `native_diagnostics` | Separate provenance settings `debug_level` (integer 0..8, default 0) and `limit_bytes` (integer 1..1,048,576, default 65,536). They are not acquisition options, material conditions or proof of worker execution. |

Every operation entry contains exactly `command_id`, `path`, `context_id`,
`request`, `draft`, `operation_id`, `state`, `sha256`, `reason`, `recovery`,
`repeat_of` and `native_diagnostics`. `repeat_of` is null for normal acquisition, or the source operation
and observation pair for Repeat. A Repeat preserves the earlier accepted spot's
frozen target/options with blank notes. Read-only review additionally requires
its canonical successful source reading to authenticate.
Command and operation UUIDs are unique within the manifest. The prepared
`operation_id` is required in every state, including `reserved` and
`terminal_unknown`; it is never inferred from a later result. Nullable operation
identities in non-acquisition or refused command outcomes are a separate live
command contract.

The record path is exactly `operation-<command_id>.json`. The frozen request
uses schema 1 and `retained_patch_v1`, the manifest's device/mode/capability,
the intent's sample UUID for a spot read, an empty patch list for calibration,
or exact descriptor-validated generic workflow patch identities/layout, and the
intent's frozen options. Its context UUID must equal the batch context.

An intent's `native_diagnostics` is null when off. Otherwise it contains exactly
`schema_version` (2), `kind` (`native_stderr`), `state`, `path`, `debug_level`,
`limit_bytes`, `received_bytes`, `retained_bytes`, `written_bytes`, `truncated`,
`sha256`, `file_sha256`, `drain_complete`, `file_complete`, `worker_started`, `sink_stopped` and
`error`, `retention` (`head_tail`) and `segments`. The path is `diagnostic-<command_id>.log`. Prepared/capturing capture
is unfinished; `not_started` preserves zero counts, null digests and no worker
execution. Closed `complete` or `incomplete` captures retain a SHA-256 of their
exact bounded retained artifact, including the SHA-256 of an empty artifact.
Within the cap, `segments` is one `{offset: 0, length: received_bytes}` range,
or empty for no bytes. Above the cap it retains the first floor(cap/2) bytes
and last ceil(cap/2) bytes in two ordered ranges, omitting a zero-length head.
A one-byte cap retains the final byte. Each offset/length is an integer;
segments must match this fixed policy and sum to `retained_bytes`.
An unclosed capturing state exposes counts but no frozen ranges or byte digest.
The saved artifact concatenates the exact declared segments without inserted
gap markers. Offsets disclose omitted bytes; the artifact is not the full stream.
The initial file digest authenticates precisely the first `written_bytes` of
that retained artifact, rather than a prefix of the complete stream.
A recovered canonical file contains the full retained artifact,
while initial write/error/completion facts remain historical capture observations.
Truncation means received bytes exceed retained bytes. File/drain completion and
worker execution are separate boolean facts. Incomplete capture is not a full
exchange, and an empty file is not proof of worker or hardware operation.
`sink_stopped` is an independent auxiliary-writer fact. When false, the initial
file digest is null and file completion is false; the original target cannot be
replaced while that writer may still operate. A rescued file may authenticate
the closed retained memory artifact without rewriting those original sink facts.

Saved diagnostic authentication is separate from operation acceptance. Missing
or tampered diagnostic bytes produce an explicit diagnostic-review reason and
never fabricate an optical result. Portable chart source closure authenticates
the retained original and its initial written prefix. Empty blobs are allowed
only for diagnostic originals; manifests, operation and recovery JSON remain
nonempty. Pending/unsaved captures cannot qualify chart continuation.

| Intent state | Meaning and required byte reference |
| --- | --- |
| `reserved` | Prepared UUID, request and draft retained before dispatch. `sha256` and `reason` are `null`. A prepared handle already terminal before the attempt skips `started`. |
| `started` | The application attempted frontend dispatch. This does not establish physical acquisition or software stop. `sha256` and `reason` are `null`. |
| `accepted` | Exact canonical operation bytes were published and accepted by the manifest. `sha256` is their 64-character lowercase hexadecimal SHA-256; `reason` is `null`. The operation itself may be completed, failed or cancelled, including a zero-reading calibration outcome. |
| `terminal_unknown` | No accepted terminal record is available. A nonempty reason retains the failure; `sha256` is `null`. This state does not establish software stop. |

`recovery` is `null` or, only for `terminal_unknown`, an object containing
`path: "recovery-<command_id>.json"` and the exact recovery file's lowercase
64-character SHA-256. Available raw result text and drained stop facts remain
in that separate schema-1 recovery record. Recovery evidence never becomes an
accepted observation automatically.

Digests bind file bytes, not reserialized JSON values. `review_saved()` reports
the manifest's exact-byte digest as `manifest_sha256`, then authenticates accepted
operation references and their frozen context. It checks that each accepted
operation's reading identities match its annotation pairs. A valid reserved
record not accepted in the manifest is `found_unaccepted`; missing, changed or
invalid accepted bytes are `unknown`. Review remains read only and never resumes
acquisition or replays the live command ledger.

Only the current field set and schema are supported. A present unsupported or
noninteger `schema_version` is refused before validating the current field set.
The version diagnostic names both values, for example:
`Unsupported batch manifest schema 1; expected 5`. Missing schema headers and
invalid current-schema field sets are refused by exact field validation. No earlier development schema is migrated, translated or
resumed.

The validator uses these actual source helpers for canonical UUIDs and exact
field sets:

<!-- source: src/color_measurement/application.py:_uuid -->
```python
def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Identity must be a canonical UUID")
    return value
```

<!-- source: src/color_measurement/application.py:_fields -->
```python
def _fields(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"Expected fields: {sorted(fields)}")
```

The live owner's `durability.files` records observed synchronization for the
manifest, canonical operation files and recovery files, including known
destination history. It excludes grouped TI3 companions and their separate
`ti3-result.json` receipt. Those optional publications have their own export
result and do not inherit a synchronization claim from the canonical files.
Filesystem synchronization observations do not qualify power-loss survival,
physical measurement or an installed graphical host.

## Import a TI2 target

[`color_measurement.chart_targets`](../src/color_measurement/chart_targets.py)
reads a bounded printed-target description without acquiring or selecting an
instrument. Argyll's [file-format reference](https://www.argyllcms.com/doc/File_Formats.html#ti2)
describes TI2 as device values, patch locations and estimated CIE values.
Those estimates are not measurements of the physical sheet. The relationship
between a printable target and its `.ti2` is described in
[`printtarg`](https://www.argyllcms.com/doc/printtarg.html).

```python
import json
from color_measurement.chart_targets import import_ti2_json, validate_target_json

target_json = import_ti2_json("tests/fixtures/targets/asymmetric-six-patch.ti2")
target = json.loads(target_json)
print(target["strips"], target["steps"])  # 2 3
first = target["rows"][0]
print(first["sample_id"], first["sample_loc"], first["strip"], first["step"])
# blk-41 02B 2 2: source table order is deliberately different from grid order.
retained_json = validate_target_json(target_json)  # No access to the original path.
```

The [six-patch fixture](../tests/fixtures/targets/asymmetric-six-patch.ti2) is
original synthetic input under this repository's MIT license. It includes a
leading-zero ID, a formula-like text ID, repeated device triplets and shuffled
source rows. Repeated colors remain distinct patches. A frontend must ask
whether strips form rows or columns and confirm the displayed mapping against
the operator's reflective sheet; parsing does not establish physical placement.

| API | Result |
| --- | --- |
| `import_ti2_json(path)` | Read one regular file, bounded to 1 MiB; return validated retained-target JSON text |
| `validate_target_json(text)` | Reparse the retained bytes, verify their digest and every semantic projection, and return canonical target JSON; no original-path access |

The retained target has `schema_version=1` and `importer_revision=1`:

| Fields | Meaning |
| --- | --- |
| `filename`, `source_base64`, `source_sha256` | Descriptive basename, exact source bytes and their SHA-256; filename is not an acquisition identity |
| `keywords`, `fields`, `field_types` | Parsed source metadata and declared columns/types |
| `strips`, `steps`, `color_rep`, `channel_fields` | Validated rectangular dimensions and explicit device-channel convention |
| `rows` | Original table order; each row keeps one-based `source_row`, text `sample_id`/`sample_loc`, one-based `strip`/`step`, `device_values`, typed `values` and original `raw_values` tokens |
| `reference` | `available=false` and a reason at this structural importer; expected fields are retained without inferring observer, white or scale. Explicit interpretation is separate below. |

Keep the returned JSON text intact across frontend boundaries. Revalidation
rejects edited digests, projections, field types, duplicate JSON keys and
nonfinite constants. A decoded preview is not a replacement serialization.
Malformed retained JSON raises `ValueError`. Decoder recursion failures are
also converted to `ValueError`.

### Explicit expected-reference illustrations

`chart_targets.expected_reference_json(target_json, context_json="null")`
interprets expected values once during setup. It authenticates the entire
current retained TI2 packet before interpreting it; the raw packet, source
bytes, channels and mapping remain unchanged. This does not acquire a reading,
access the original path, qualify a specimen or enable position checking.
MATLAB owns subsequent rendering and quantitative-reference qualification.

The context is explicit JSON with exactly these fields:

| Field | Required interpretation |
| --- | --- |
| `sourceFields` | Exactly `["XYZ_X","XYZ_Y","XYZ_Z"]` or `["LAB_L","LAB_A","LAB_B"]`, in that order |
| `observerId` | `"CIE1931_2deg"` for the current illustration projection |
| `xyzUnits`, `xyzScale` | `"1"` and `"relative; perfect-diffuser Y=100"` |
| `whiteXYZ` | Three finite positive numbers, in the expected XYZ scale, with Y equal to 100 |
| `illuminant` | Nonblank declaration of the source interpretation; never defaulted to D50 |
| `interpretation`, `meaning` | Nonblank descriptions of the supplied interpretation and what the expected values represent; neither verifies physical conditions |

Each of `observerId`, `xyzUnits`, `xyzScale`, `illuminant`, `interpretation`
and `meaning` must contain nonblank text of at most **2,048 UTF-16 code units**,
matching MATLAB's `numel(char(field))` bound. A Basic Multilingual Plane
character occupies one unit; a supplementary character occupies two.
Thus 2,048 ASCII characters or 1,024 supplementary characters fit, while
adding one ASCII character exceeds the limit. Counting happens after JSON
decoding; escaped and literal spellings have the same decoded field limit.
This bound validates metadata only and does not qualify reference conditions.

The explicit field choice handles a file containing both XYZ and Lab without
a hidden preference. Incomplete selected columns do not fall back to another
triplet or device RGB. `null` context leaves every cell unavailable. A complete
but unsupported observer/unit/scale/white-Y declaration also returns unavailable
cells with its context and reason retained. Malformed JSON, duplicate keys,
nonfinite constants, boolean white components and missing/extra context fields
raise `ValueError`. Context text is bounded to 16,384 characters.

The schema-1 result contains `source_sha256`, `context` and `cells` in original
source order. Every cell retains `source_row`, `sample_id`, `sample_loc`,
`originalFields`, `originalValues`, `available`, `XYZ` and `reason`. Original
lexical tokens remain in the authenticated raw packet. XYZ values stay exact,
including signed and above-100 values; there is no clipping or per-cell
brightness normalization. Lab uses the existing
[`science.lab_to_xyz` inverse](science.md#actual-implementation-lab_to_xyz),
with the declared common white. Its equations, numerical tests, units and
limitations follow the [CIE CIELAB definition](https://cie.co.at/eilvterm/17-23-076).
Conversion overflow withholds only the affected cell, preserving its original
values and reason. The shared strict CGATS reader already refuses nonnumerical
standard XYZ/Lab columns before this interpretation.

For example, the original synthetic
[Lab fixture](../tests/fixtures/targets/explicit-lab-six-patch.ti2) and
[explicit context](../tests/fixtures/targets/explicit-lab-six-patch.context.json)
use an equal-component white `[100,100,100]`. Lab `[100,0,0]` produces that white;
`[42,0,0]` has inverse-branch value `0.5³` and produces `[12.5,12.5,12.5]`.
Those are analytical illustration inputs, not specimen measurements. The
[generated interpretation](../tests/fixtures/targets/explicit-lab-six-patch.expected.json)
also covers black, the branch boundary, signed values and a chromatic cell.
The [null-context response](../tests/fixtures/targets/explicit-lab-six-patch.unavailable.json)
keeps all cells unavailable without guessing conditions.
Regenerate/check both raw and interpreted fixtures with
`python tools/generate_target_fixtures.py [--check]`.

`ACCURATE_EXPECTED_VALUES` does not supply a white, observer or physical
qualification. Printer RGB/iRGB/CMYK channels never become sRGB. The caller
must reconcile its declared interpretation with the source's stated conditions;
this API does not infer missing conditions or certify those declarations.
Actual implementation:

<!-- source: src/color_measurement/chart_targets.py:expected_reference_json -->
```python
def expected_reference_json(target_json: str, context_json: str = "null") -> str:
    """Project frozen expected XYZ/Lab once for reference illustrations.

    The current TI2 packet is revalidated from its retained bytes. The caller
    explicitly selects XYZ_X/Y/Z or LAB_L/A/B and declares a common positive
    white XYZ, CIE1931_2deg observer, dimensionless relative Y=100 scale,
    illuminant, interpretation and meaning. These declarations are retained;
    they do not prove specimen conditions or qualify a position warning.
    Each of the six context text fields is limited to 2048 UTF-16 code units,
    matching the MATLAB consumer; a supplementary character occupies two.

    XYZ is retained exactly. Lab uses the established science.lab_to_xyz
    inverse of CIE S 017:2020 e-ILV 17-23-076, in the declared white's scale:
    https://cie.co.at/eilvterm/17-23-076
    For example, (L*, a*, b*)=(100, 0, 0) returns the supplied white XYZ.
    Neither printer channels, ACCURATE_EXPECTED_VALUES nor numerical ranges
    supply missing context. No default white, clipping, adaptation, rendering
    or acquisition occurs. Missing selected columns or conversion overflow
    leave that source row explicitly unavailable. The shared strict CGATS
    reader refuses nonnumerical standard XYZ/Lab columns before interpretation.

    The result keeps every original source-row identity/location in source
    order, selected original fields/values, context and source digest. The raw
    packet is unchanged. Subsequent rendering uses this retained projection
    without another Python call or access to the original TI2 file.
    """
    target = json.loads(validate_target_json(target_json))
    context, context_reason = _expected_context(context_json)
    selected = context.get("sourceFields", [])
    positions = [target["fields"].index(field) for field in selected if field in target["fields"]]
    complete = len(positions) == 3
    cells = []
    for row in target["rows"]:
        originals = [row["values"][position] for position in positions]
        reason = context_reason
        xyz = []
        if not reason and not complete:
            reason = "The selected expected XYZ/Lab columns are missing or incomplete"
        if not reason:
            try:
                xyz = (
                    list(lab_to_xyz(originals, context["whiteXYZ"]))
                    if selected[0] == "LAB_L"
                    else list(originals)
                )
            except ColorDomainError as error:
                reason = f"Expected Lab conversion unavailable: {error}"
        cells.append(
            dict(
                source_row=row["source_row"],
                sample_id=row["sample_id"],
                sample_loc=row["sample_loc"],
                available=not reason,
                XYZ=xyz,
                originalFields=[target["fields"][position] for position in positions],
                originalValues=originals,
                reason=reason,
            )
        )
    return _json(
        dict(schema_version=1, source_sha256=target["source_sha256"], context=context, cells=cells)
    )
```

The actual bounded-file read is:

<!-- source: src/color_measurement/chart_targets.py:import_ti2_json -->
```python
def import_ti2_json(path: str) -> str:
    """Read at most 1 MiB plus a limit sentinel; return frozen plain target JSON."""
    source = Path(path)
    filename = _filename(source.name)
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("TI2 source must be an ordinary file before opening")
    descriptor = os.open(
        source, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        opened = os.fstat(descriptor)
        current = source.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ValueError("TI2 source changed identity while opening")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(MAX_SOURCE_BYTES + 1)
    finally:
        os.close(descriptor)
    return validate_target_json(_json(_parse(payload, filename)))
```

Supported input is one complete rectangular `CTI2` table with 1–140 patches.
`STEPS_IN_PASS` and `PASSES_IN_STRIPS2` must each be one positive integer whose
product equals the patch count. `COLOR_REP` must be RGB, iRGB or CMYK, with
finite 0–100 channel values. IDs and quoted locations must be unique printable
ASCII labels; IDs stay text, including leading zeros. Duplicate IDs are refused
separately from the unsupported padding ID `0`. Argyll 3.5.0's `printtarg`
assigns `"0"` to extra patches that complete a final printed row
(`target/printtarg.c:2364-2378`); `chartread` skips those patches when writing
measurement rows (`spectro/chartread.c:330-335`). Such a padded target is valid
for those Argyll tools, but outside this importer's complete rectangle of real
patches. The importer refuses it explicitly rather than treating padding as a
sample or silently dropping it. The strip pattern is `A-Z, A-Z`; step patterns are
`0-9,@-9,@-9;1-999` or `@-9,0-9;01-99`. `INDEX_ORDER` is
`STRIP_THEN_PATCH` or `PATCH_THEN_STRIP`. Defaults and index conventions follow
the pinned ArgyllCMS 3.5.0 sources: `spectro/chartread.c:2918–2995`,
`target/printtarg.c:3756–3788`, and `target/alphix.c:31–103,163–177`.
The archive digest is recorded by the [source pin](../src/color_measurement/argyll_source.py).

The importer refuses multiple tables/sheets, appended calibration tables,
legacy `PASSES_IN_STRIPS`, hexagonal layouts, missing cells, duplicate
normalized locations and unsupported indexing/channel conventions.
`TARGET_INSTRUMENT` is retained metadata, not a device-selection command.
Expected XYZ/Lab cannot replace measured values, and device RGB/CMYK is not
silently rendered as sRGB. Import does not qualify external profiling,
chartread resume, or a physical measurement workflow.

## Automatic TI3 measurement companions

After `exports.save` or `operation_exports.save` publishes the complete record,
the shared [TI3 writer](../src/color_measurement/ti3_exports.py) attempts a
measurement projection in the same directory. Keep the JSON/MAT record: TI3
does not contain the complete operation history, unsupported quantities or
every acquisition detail. Observation CSV retains its existing, narrower scope.

Use the returned `SaveResult` to find the canonical file and inspect optional
output. Do not treat the presence of an older `.ti3` file as a successful update:

```python
# saved is the SaveResult from one of the save examples above.
for file in saved.ti3["files"]:
    print(file["state"], file["path"], file["row_count"], file["reason"])
for missing in saved.ti3["unavailable"]:
    print(missing["record_id"], missing["component"], missing["reason"])
```

| TI3 `status` | Meaning and action |
| --- | --- |
| `current` | At least one file represents the saved snapshot, and none is stale. Inspect `unavailable` for omitted records or components. |
| `unavailable` | No current file could be produced for these quantities or this projection. Keep the canonical record and inspect the reason; retrying cannot supply a missing quantity or resolve unknown units. |
| `stale` | A required file update failed or could not be authenticated. Inspect `retryable`, `relocatable` and the reason before recovery. There may be no earlier usable file. |

Each file describes its `scope`, `group_id`, `provenance`, `path`, `row_ids`,
`row_count`, `state`, `reason`, `snapshot_id`, `payload_sha256`, `target_path`
and `target_sha256`. Only target-associated files have a non-null target path
and digest. Chart files
also identify their `chart_id` and `total_rows`; ordinary files leave those two
fields null. Count current rows only from files whose state is `current`.
`unavailable` identifies the original record and the omitted `record`, `xyz` or
`spectrum` component. Unsupported XYZ can coexist with an eligible spectrum,
and the reverse is also possible.

A file can also have `state="retired"`: its group is absent from the current
snapshot, or its chart selection is now empty. Authenticated last-good output
is retained for history, but its rows are excluded from current counts.
A never-published empty chart remains `unavailable` on repeated unchanged saves;
a receipt for a plan alone is not historical output. Retirement requires a
prior published payload identity or an already-retired historical row. History
must retain nonempty original row identities even when its file bytes are no
longer authenticated; a zero-row retired receipt is refused. The retired state
alone does not make the overall result stale or retryable, including
when normalization, canonical-save projection or folder locking fails. Retired
rows whose earlier bytes cannot be authenticated remain retired with null
payload and snapshot digests; their historical identities are still retained.
A result with
only retired files is `unavailable`, because it has no current color output.
Inactive historical plans cannot offer relocation either.
If that group later returns, supplying the complete previous result permits
safe reactivation after ownership and byte checks. File presence alone does
not make retired measurements current.

### Quantities and the intended reader

Ordinary and preset/custom-chart files are Argyll `CTI3` measurement/reference
tables, with `INPUT` for reflective data and `EMISINPUT` for emission. They
contain no invented RGB/CMYK device channels and no `COLOR_REP` pairing. They
are therefore not profile-ready device characterization files. Imported-target
selections instead use `OUTPUT` with retained device channels paired with
measured values, as described below. Argyll documents device-channel-free use for
[`txt2ti3`](https://www.argyllcms.com/doc/txt2ti3.html); its general
[TI3 format](https://www.argyllcms.com/doc/ti3_format.html) describes the paired
format and spectral field names.

Eligibility comes from explicit quantities and reference conditions, not an
instrument's name or the numerical range of its readings:

| Retained component | Required declaration | Written values |
| --- | --- | --- |
| Reflective XYZ | `unit="1"`, `scale="relative; perfect-diffuser Y=100"`, `observer="CIE1931_2"`, `illuminant="D50"` | Original XYZ, unchanged |
| Emissive XYZ | `unit="cd/m^2"`, `scale="absolute; Y is luminance"`, `observer="CIE1931_2"`, `illuminant="none (emissive)"` | Original absolute XYZ, unchanged |
| Reflectance spectrum | `quantity="reflectance"`, `unit="%"` | Original percentage samples |
| Spectral radiance | `quantity="spectral_radiance"` or `"radiance"`; an admitted W or mW density unit below | Density in `mW/(m^2 sr nm)` |

Radiance accepts `mW/(m^2 sr nm)` unchanged, or `W/(m^2 sr nm)` multiplied by
1000. The direct CS spelling `W/(sr m2 nm)` requires the additional retained
`context.material.spectralUnitsConfirmed=true`; a label alone is insufficient.
If configuration declares `spectrum_norm`, it must be 100 for reflectance or
1 for radiance. Unknown scales, other XYZ observers/illuminants, transmission,
irradiance, time-integrated flash quantities, scalar Hz, timing and configuration
outcomes are unavailable in this projection. They remain in the source record.
In particular, PR ambient XYZ in lux and its spectral irradiance do not produce
TI3 components. Save their canonical operation JSON/MAT or observation CSV instead.

The only physical-unit conversion is

\[
L_{\mathrm{mW}}(\lambda)=10^3 L_{\mathrm{W}}(\lambda).
\]

The area, solid angle and wavelength-density basis stay unchanged. For example,
`0.001 W/(m^2 sr nm)` becomes `1 mW/(m^2 sr nm)`; this does not set luminance
to 100 or change any retained XYZ. The factor follows the
[BIPM SI prefix definition](https://www.bipm.org/en/measurement-units/si-prefixes)
of milli. Argyll specifies mW density for emission in its
[TI3 quantity table](https://www.argyllcms.com/doc/ti3_format.html).

Spectra must have 2–601 bands on a uniform grid representable by the named
reader. The reconstruction is

\[
\lambda_i=\lambda_0+\frac{i}{N-1}(\lambda_{N-1}-\lambda_0),
\qquad i=0,\ldots,N-1.
\]

The writer accepts at most four floating-point units in the last place of
disagreement with that grid. It does not interpolate the values. Fractional
start/end wavelengths remain in the header; only `SPEC_` field labels use
`int(wavelength + 0.5)`. Colliding labels, values not exactly representable as
doubles, conversion overflow and nonzero values that underflow to zero are
refused. Finite signed and above-100 values are retained without clipping.

Two- and three-band spectra are eligible at narrow or coarse spacing when
they pass these format and quantity checks. The application's
[imported native repair](argyll-native-feasibility.md#source-ownership-and-composition)
repairs Argyll's common `getval_raw_xspec_poly3_ex` helper: with fewer than four
samples it calls the existing linear evaluator. Both automatic interpolation
and explicit cubic requests use that fallback. The Python writer still exports
the retained samples without interpolation or resampling.

An external, unmodified Argyll 3.5.0 `spec2cie` binary does not receive this
repair. Its coarse-grid path (spacing at least 5.01 nm) can access samples beyond
a two- or three-band spectrum. Valid exported TI3 bytes therefore do not by
themselves establish safe conversion with that external reader. The consumer
checks below use the application's repaired native source, not a stock binary.
Actual Python eligibility implementation:

<!-- source: src/color_measurement/ti3_exports.py:_spectrum_kind -->
```python
def _spectrum_kind(row):
    spectrum = row.spectrum
    if spectrum is None:
        return None, (), "No retained spectrum"
    if (spectrum.quantity, spectrum.unit) == ("reflectance", "%"):
        kind, multiplier = "reflective", 1.0
    elif spectrum.quantity in ("spectral_radiance", "radiance") and spectrum.unit in (
        "mW/(m^2 sr nm)",
        "W/(m^2 sr nm)",
        "W/(sr m2 nm)",
    ):
        kind = "emissive"
        multiplier = 1.0 if spectrum.unit.startswith("mW") else 1000.0
    else:
        return None, (), "Spectrum quantity/units are unresolved or have no qualified CTI3 dialect"
    if (
        spectrum.unit == "W/(sr m2 nm)"
        and row.source["context"]["material"].get("spectralUnitsConfirmed") is not True
    ):
        return None, (), "CS native spectral units have not been explicitly confirmed"
    configuration = row.source["context"]["material"].get("configuration", {})
    if isinstance(configuration, dict) and "spectrum_norm" in configuration:
        try:
            norm = float(configuration["spectrum_norm"])
        except (ValueError, TypeError, OverflowError):
            norm = None
        if norm != (100.0 if kind == "reflective" else 1.0):
            return None, (), "Retained spectral normalization differs from the qualified units"
    grid = spectrum.wavelengths_nm
    # spec2cie.c:674-703 reconstructs a uniform grid and rounds its FIELD NAMES,
    # not its retained start/end wavelengths. No implicit resampling occurs.
    if not 2 <= len(grid) <= 601:
        return None, (), "CTI3 spectral reader requires 2..601 bands"
    if any(float(value) != value for value in grid):
        return None, (), "Spectral grid is not representable by the named reader's doubles"
    expected = _grid(grid)
    if any(abs(a - b) > 4 * max(math.ulp(a), math.ulp(b)) for a, b in zip(grid, expected)):
        return None, (), "CTI3 cannot represent an irregular grid without resampling"
    if grid[-1] > 2**31 - 1 - 0.5:
        return None, (), "Spectral field wavelength exceeds the named reader's integer range"
    names = [int(w + 0.5) for w in expected]
    if len(set(names)) != len(names):
        return None, (), "Rounded spectral field names collide"
    converted = tuple(float(v) * multiplier for v in spectrum.values)
    if any(
        not math.isfinite(v) or (v == 0 and original != 0)
        for original, v in zip(spectrum.values, converted)
    ):
        return None, (), "Spectral unit conversion is not representable"
    if any(float(v) != v for v in spectrum.values):
        return None, (), "Spectral values are not representable by the named reader's doubles"
    return kind, converted, ""
```

The consumer boundary is ArgyllCMS 3.5.0, from its
[official source archive](https://www.argyllcms.com/Argyll_V3.5.0_src.zip):
`profile/txt2ti3.c:591–670` supplies the reference-only `INPUT` precedent;
`spectro/spec2cie.c:470–484,674–703,1051–1053` controls emissive dispatch,
spectral interpretation and XYZ output scaling; `xicc/xspect.c`
`getval_raw_xspec` and `xsp2cie_sconvert` supply interpolation and integration.
The application writer performs neither integration nor white adaptation.
The prose description of `NORMALIZED_TO_Y_100` addresses `DISPLAY` data; it
alone does not establish the `EMISINPUT` behavior. The
[consumer documentation](https://www.argyllcms.com/doc/spec2cie.html) explains
the reflective/emissive distinction.

The [inert reader tests](../tests/test_ti3_exports.py) use extracted, unmodified
dispatch/grid/scaling blocks with the application's repaired native spectral
support through a [small reader probe](../tools/native_argyll/ti3_reader_probe.c).
Flat 50% reflectance must produce Y=50 for narrow/coarse two- and three-band
grids, a four-band grid and the 601-band boundary. On the synthetic four-band
grid `[400.5, 410.5, 420.5, 430.5]` nm, flat `0.001 W/(m^2 sr nm)` must match
an independent sum of the reader's 1 nm observer table for a 1 mW density,
rather than Y=100. These comparisons use relative tolerance `2e-14` for
floating-point arithmetic.

The native fallback is also checked independently of TI3 formatting: values
`[20, 80]` at `[400, 410]` nm yield `[20, 35, 50, 65, 80]` at 2.5 nm steps
through the explicit cubic API, using its linear fallback. Changing unused
array values leaves the repaired result unchanged; removing the guard makes
the result depend on those unrelated values. The four- and 601-band controls
have identical reader output with and without the small-band guard.
These checks concern native interpolation, extrapolation and scaling on
synthetic inputs. They establish neither optical accuracy nor that a short
measured spectrum covers the visible range, and do not qualify every downstream
TI3 application or establish upstream adoption of the repair.

### Grouping, charts and saved-data retry

Ordinary files include every eligible current acquisition, including repeated
readings of one chart patch and readings taken outside a chart. The writer
separates provenance (`physical`, `simulated`, `replay`), material measurement
context, XYZ reference/derivation metadata, spectral quantities and grids.
Transport route and timestamps remain provenance, without forcing otherwise
compatible readings into separate files. Filenames begin with
`measurements-<provenance>-` and end with the group digest and `.ti3`.
For canonical observations, typed calibration readiness remains part of the
material context. The full calibration explanation is retained in row
provenance: a valid retained calibration and an initial valid assessment can
use different explanatory sentences without changing the measurement setup.
Batch companions also retain the original `operation_id`, `observation_id` and
manifest-authenticated `repeat_of` in each row's `CM_CONTEXT_JSON` provenance.
`repeat_of` is null for ordinary acquisition or the original operation/observation
pair for a deliberate repeat. Labels and later annotation edits do not define
that lineage. This provenance does not split otherwise compatible material
groups or change the retained native records.

Actual calibration standards, corrections and native settings remain material
facts; this distinction never confirms a physical calibration or waives an
unknown or required readiness state.

A caller may also supply chart selections. For preset/custom charts, separate
`chart-reference-<provenance>-<digest>.ti3` files contain only selected measured
rows, in supplied chart order, with immutable record IDs and `SAMPLE_LOC`.
Every selected row needs compatible reflective D50/2-degree XYZ; spectra are
included only when every selected row has the same eligible reflective grid.
Otherwise an eligible chart is XYZ-only, with the omitted spectra reported.
Unmeasured cells are omitted, never filled with zero; `total_rows` and
`CM_COVERAGE` distinguish partial and complete selections. Edited sample names
do not replace chart locations.

For an imported chart, `target` contains the intact JSON text returned by the
[TI2 importer](#import-a-ti2-target). This produces `scope="chart_target"`
instead of an additional reference file. Selected rows are matched by the
exact source ID/location pair and written in original TI2 row order. The
`OUTPUT` file preserves the original RGB/iRGB/CMYK device values, with measured
XYZ and eligible spectra from the selected readings. Source expected values
never become measured data. Missing cells remain omitted.

The pair is placed in `chart-target-<provenance>-<group>/`, with matching
`target-<provenance>.ti2` and `.ti3` basenames. The TI2 is an exact copy of the
retained source bytes; its path and digest are reported in `target_path` and
`target_sha256`. Keep both files together when moving them. Argyll's
[`chartread` interface](https://www.argyllcms.com/doc/chartread.html) uses a
shared input/output basename. Matching that convention does not establish
that an external resume or profiling workflow has been qualified.

For programmatic saved-data updates, `ti3_exports.update_json(snapshot_json,
folder, previous_json="null")` accepts the strict schema-3 retained snapshot
and returns a schema-2 JSON status object. Supply the entire previous result
on retry, not just `files`. The snapshot contains `owner_id`, `source_id`,
`source_sha256`, `records`, `ordinary_record_ids` and `charts`, besides
`schema_version`. `ordinary_record_ids` is a required array of unique record
IDs in acquisition order; every ID must resolve in `records`. It selects the
ordinary output independently of the order of the complete retained records.
All records are validated, including those excluded from ordinary output.
Chart selections may reference any retained record. This lets a new session
segment retain inherited chart readings under their original identities
without reporting them as new acquisitions. An empty ordinary selection emits
no new ordinary file; eligible chart selections still export. Unsupported
current acquisitions retain their explicit unavailable dispositions.
Every chart includes `target`: null for a preset/custom chart, or retained
target JSON **text** for an imported chart. `update_json` performs canonical
normalization itself, verifying native MATLAB rows' original operation/observation
identities, quantities and provenance against canonical operation JSON. The
projection keeps row-local provenance, original IDs and that JSON's SHA-256;
it does not repeat the whole operation in every TI3 row. Direct
rows must supply stable source IDs and explicit units; editable labels are not
IDs. The caller owns saving the authoritative bytes and binding their SHA-256
to `source_sha256` before invoking the file-only writer.

A linked segment supplies its new canonical source identity and starts with no
previous TI3 outcome from the source segment. The original MAT/JSON and its
companions stay owned by the original source; moving retained records never
authorizes replacing those files. Snapshot schema 2 is refused, rather than
silently treating inherited readings as current acquisitions. Result/status
schema 2 and native operation schema 1 remain separate contracts.

An invalidated display-timing presentation supplies one `kind="timing"` row
identified by the canonical operation ID, with null XYZ and spectrum. Its
context provenance carries `canonicalOperationJson`, `canonicalOperationId`,
`presentationInvalidated=true` and a nonblank `presentationInvalidationReason`.
Normalization verifies the timing workflow, identity and provenance, retains
the native JSON digest and invalidation reason, and accounts for the operation
as unavailable without exporting a measurement row. This also applies when the
invalidated operation retained no reading. Other eligible records still export;
the canonical native result remains unchanged.

`source_id` identifies the authoritative saved path, independently of batch or
operation `owner_id` and mutable file bytes. Canonical save APIs use the
resolved JSON/CSV/MAT path; MATLAB supplies its canonical MAT path. Companion
names bind owner, source and projection group, so saving one observation as
both CSV and JSON in the same directory gives separate companions. Updating
one source's contents keeps its companion names stable for the same group.

The exchange file's `CM_SOURCE_ID` is the lowercase SHA-256 of the exact
`source_id` UTF-8 bytes, not the path itself. It is deterministic ASCII and
participates in last-good file authentication. The private snapshot and result
retain the exact local path for ownership checks. A different source header
does not grant permission to replace a file, even if a supplied receipt matches
that file's current byte digest.

### TI3 metadata and privacy

The writer emits the following custom metadata. JSON escaping preserves text
and makes the JSON representation ASCII; it does not remove private information.

| Keywords | Contents |
| --- | --- |
| `CM_EXPORT_KIND`, `CM_PROVENANCE` | Projection scope and physical, simulated or replay provenance. |
| `CM_OWNER_ID`, `CM_GROUP_ID` | Caller-owned record/operation identity and projection group digest. |
| `CM_SOURCE_ID`, `CM_SOURCE_SHA256`, `CM_SNAPSHOT_ID` | Opaque saved-path identity, saved-byte digest and normalized-projection digest. |
| `CM_MATERIAL_JSON` | Device, mode, configuration, typed calibration readiness and available native identity/settings/request context used for grouping. |
| `CM_XYZ_JSON`, `CM_SPECTRUM_JSON` | Quantity, units and reference/derivation metadata; spectral values and wavelengths are columns and grid keywords. |
| `CM_SPECTRAL_UNIT` | Published spectral unit, when spectra are present. |
| `CM_TARGET_SHA256` | Retained TI2 byte digest for an imported target pair. |
| `CM_TOTAL_CELLS`, `CM_COVERAGE` | Chart size and partial/complete selected coverage. |

The custom row columns are `CM_RECORD_ID`, `CM_NAME_JSON`, `CM_NOTES_JSON`,
`CM_PROVENANCE` and `CM_CONTEXT_JSON`. The last retains the row's material and
provenance context: device identity, acquisition timestamp, configuration and
available native facts. Native MATLAB rows retain their canonical operation and
observation IDs and the operation JSON digest, without duplicating its entire
JSON into the row.

These files are not anonymized. Owner/record IDs, edited names and notes,
device names and serial numbers, correction names or paths, port/path
configuration and other native facts may reveal private information. The
opaque source header removes the automatic disclosure of the canonical
save path through that header only. An imported pair also includes the exact
original TI2 bytes and selected target keywords. Inspect both files before
sharing; changing ownership metadata or payload bytes invalidates the private
receipt used for safe replacement.

### Projection failure and publication

`source_sha256` authenticates the saved file bytes. `snapshot_id` instead
authenticates the canonical normalized projection JSON; it is null when that
projection could not be constructed. A stale last-good file can retain its
own earlier snapshot digest. The result's `retryable` boolean distinguishes
publication failures that can be retried from unsupported or malformed
retained facts (including JSON nesting too deep to decode or encode) and
changed/foreign-file refusals that cannot be repaired by
repeating the same export. The
canonical source remains intact in either case.

The current schema-2 result requires a separate `relocatable` boolean, including
in every previous-result receipt. It is true when an active publication or
destination fails and choosing a new owned folder can resolve the failure.
For example, an ownership refusal has `retryable=false` and `relocatable=true`:
retrying the same folder cannot authorize overwriting a foreign file. A write or
lock failure can set both flags. Projection validation, unsupported quantities
and inactive historical plans set neither. A new destination starts with a null
previous receipt; it retains the same authoritative source and values and does
not acquire another reading. These two flags come from the publisher; clients
must not infer relocation from a stale status or error-message text. Older
development result shapes without the required field are refused.

After validation and planning, the writer creates the exact companion folder
if needed; its parent must already exist. An existing non-directory is refused.
This does not change the canonical save APIs' requirement for an existing
parent directory.

Each companion is published or replaced atomically after ownership and content
digest checks. Identical already-published bytes can be reused after a lost
response. Changed or foreign files are refused, and verified last-good output
is retained as stale when an update fails. Updates in one folder use a
nonblocking kernel lock; contention reports failure instead of waiting.
An ownership refusal is nonretryable. Use a new destination, or restore the
authentic previous result for that same source when it was lost; a result from
another source or destination does not grant permission to replace existing
bytes. Do not delete or overwrite a foreign file to force publication.

Files in the old cleartext `CM_SOURCE_ID` format are also refused nonretryably.
Even untouched, originally owned bytes and their authentic previous result do
not authorize replacing that old header with the current opaque identity.
Restoring the same result cannot repair this transition: export the retained
data to a new destination. The refusal names the old format and leaves those
earlier files intact; there is no alias or automatic reuse of the old format.

The group of files is not one atomic transaction, and the mechanism does not
provide a compare-and-swap guarantee against unrelated editors. A retry reads
retained data; it never reconnects to an instrument or acquires again.

## Bounded Argyll SPECT export

[`spectral_exports.save_sp`](../src/color_measurement/spectral_exports.py)
exports native reflective percent spectra for the `reflective_spot_m1`,
`reflective_spot_m2` and `reflective_spot_m3` modes with mandatory canonical
JSON. It implements the ArgyllCMS 3.5.0 `SPECT` dialect, not general CGATS import
or a `.ti3` target file; no RGB/CMYK stimulus channels are invented. Its named
consumer is `read_nxspect` in `xicc/xspect.c` from the
[official archive](https://www.argyllcms.com/Argyll_V3.5.0_src.zip).
See also [Argyll's format index](https://www.argyllcms.com/doc/File_Formats.html).

```python
from color_measurement.spectral_exports import save_sp

# result contains compatible retained M1, M2 or M3 percent spectra.
saved = save_sp(result, "spectra.sp", canonical_path="spectra.json")
print(saved.path)           # The SPECT file.
print(saved.ti3["status"])  # Independent TI3 companions of spectra.json.
```

`save_sp` returns the shared `SaveResult`. It reports TI3 outcomes whether the
canonical JSON was newly saved or identical existing bytes were reused.
Pass the complete previous `saved.ti3` as `previous_ti3` when updating those
companions; a repeated SPECT export still requires a new `.sp` filename.
Optional TI3 failure leaves successful JSON/SPECT files intact; that failure
is visible in the returned TI3 status.

Every patch requires reflective type `5`, matching native condition, norm `100`
and unit `%`. All patches share a uniform grid of 2–601 integer-nm wavelengths
within the reader's signed-integer range. Values must be exactly representable
as doubles. Signed and above-100 samples remain unchanged. Missing, fractional,
irregular or mixed grids, emission, steady ambient, flash, transmission and zero-reading outcomes
are refused without
resampling. The fractional-nm native stub spectrum in the API examples is thus
ineligible for SPECT export.
The native strip mode names are not admitted by this exporter; retain those
operations as JSON or MAT. High-resolution i1Pro3 nonreflective records are also
ineligible: their modes are refused before grid eligibility is checked. Their
10/3-nm grid also contains fractional wavelengths, which the format cannot
represent under the current integer-nanometre contract. JSON/MAT preserves all
106 values, the returned grid,
XYZ, resolution selection and native history without resampling or rounding.
Original i1Pro/i1Pro2 adaptive/nonadaptive emissive records are likewise outside the reflective
SPECT mode gate, even though their 36-point grid uses integer nanometres. Preserve
those records, including their raw serial/reference strings and initialized
numeric serial, in canonical JSON/MAT.

| Mode | Explicit `MEAS_CONDITIONS` | Native condition |
| --- | --- | --- |
| M1 | `D50` | `1` |
| M2 | `UVCUT` | `3` |
| M3 | `POLARIZED` | `4` |

M0 is refused: the named reader has no explicit recognized M0 tag, and an absent
condition leaves its incoming state unchanged. The writer uses recognized reader
tags directly, avoiding the upstream writer's `switch (mt)` condition-selector
defect (`xicc/xspect.c:3695–3715`). Parsing does not establish physical conformance.

Destinations need distinct names in the same existing directory. The JSON
basename starts with an ASCII letter/digit and contains only ASCII letters,
digits, dots, underscores or hyphens. Identical existing canonical bytes may be
reused; conflicting JSON and any existing `.sp` are refused. Each file is
published exclusively, but the pair is not atomic: an `.sp` failure may leave
complete JSON for explicit retry. `OperationSaveError` retains the result/payload.

Rows follow the retained canonical `patches` array, including failed/cancelled
partial outcomes; missing expected patches are omitted. IDs, status, XYZ,
calibration, raw text and omitted metadata remain in JSON. Custom `CM_*` keywords
name/hash that companion; Argyll ignores them. A consumer interpreting these
fields must verify the companion SHA-256 itself. For multiple rows, call
`read_nxspect` with capacity at least `NUMBER_OF_SETS` and verify its returned
count. `read_xspect` reads only the first row; success is not complete import.

## CSV

CSV contains a header and exactly one observation row. Columns are
`schema_version`, `observation_id`, `provenance`, `sample_id`, `xyz_X`, `xyz_Y`,
`xyz_Z`, `xyz_unit`, `xyz_scale`, and `record_json`.

The XYZ columns are native-value conveniences. `record_json` contains the
complete JSON record, including reference white, calculation availability,
warnings, spectrum/grid if present and original text. Preserve that column for
a complete round trip. `from_csv` checks that convenience cells agree with it;
editing only a spreadsheet's XYZ cell is rejected rather than silently changing
the original reading.

Text convenience cells that begin with formula-sensitive characters are prefixed
with an apostrophe; signed numerical samples remain numbers. Exact original text
is retained in `record_json`. This CSV is a one-record interchange format, not
a multi-observation table importer. Spreadsheet applications can modify text or
truncate large cells; keep the original JSON as the preferred archival file.

## Saving and retrying

[`save`](../src/color_measurement/exports.py) writes a temporary sibling,
flushes its contents, then creates the destination exclusively with a hard link.
It refuses an existing destination and does not create parent directories.
The filesystem must support hard links; unsupported operations fail explicitly.
This prevents a partially written destination during normal saving, but is not
a guarantee of persistence through every filesystem or power failure.

An I/O `SaveError` carries `observation` and `serialized` so the caller can save
the same reading elsewhere. Saving never invokes acquisition. Format/schema
errors are validation errors, not successful saves or reasons to remeasure.
An ordinary temporary-file cleanup I/O failure is attached to the original save exception,
or logged as a warning if the destination was saved successfully. It does not
discard the recovered record; a warning identifies the leftover temporary path.
Control signals propagate. A later authentication or cleanup failure cannot
replace an earlier control signal or erase its real publication receipt. Failed
authentication does not create a receipt for bytes it could not verify.

```python
from color_measurement.exports import SaveError, save

# observation is an already completed Observation.
try:
    saved = save(observation, "reading.json")
except SaveError as error:
    saved = save(error.observation, "reading-retry.json")
print(saved.path)
print(saved.ti3["status"])
```

Both paths must be new filenames in existing directories. The caller chooses
whether and where to retry; there is no automatic acquisition or save retry.
Both JSON and CSV observation saves return `SaveResult(path, ti3)` and attempt
[TI3 companions](#automatic-ti3-measurement-companions) after publication.
Color observations also support `exports.to_mat`, `exports.from_mat` and
`exports.save(..., format="mat")`. This current level-5 document declares
`document_kind="observation"`, schema 1, complete canonical JSON and its SHA-256.
The shared document reader checks the type/digest before the authoritative
Observation validator. It does not invent an enclosing terminal operation.
See [standalone records](standalone-records.md) for explicit import formats,
original-byte authentication and native component exports.

`to_json`, `to_csv` and observation/operation `to_mat` only serialize: they create no files
and perform no companion update.

## Configuration queries

`mode_snapshot` and `stored_refresh` results can complete with zero patches.
They retain context in the existing configuration key/value collection and
round-trip through operation JSON and MAT. They create no colour, scalar or timing
observations, with no illuminant, observer or measurement-condition defaults.

`mode_snapshot_json` retains configured mode, capability words, named display
catalogue/readback, accepted correction, requested options, source and derived
workflows. `stored_refresh_json` retains getter status, code, nullable Hz value
and method; the code is null for `not_advertised`, when no getter was called. `refresh_rate_override_hz` is input configuration, including explicit
zero reset request. An available getter value can be that input echoed by the
driver. Unavailable state is null, never zero Hz. These facts are scoped to their
operation and must not become freshly measured refresh-frequency observations.
