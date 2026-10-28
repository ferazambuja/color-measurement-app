# Python session selection and quantity bridge

`DesktopSession` composes the existing batch and scientific owners. It receives
an explicit list or tuple of already-bound `InstrumentFrontend` objects. Demo
frontends are explicit simulated choices in that collection. Construction and
ordinary settings queries do not enumerate or initialize instruments. The
separate [equipment setup API](desktop-equipment.md) accepts a native report
choice and delegates deliberate listing and selected initialization/binding to
`InstrumentBuild`. It starts no HTTP service and infers no attachment from a
model name.

The portable host uses its existing `desktop-demo-load` executor for Argyll setup,
direct serial setup and Demo loading. It passes that executor as
`DesktopSession(frontends, setup_executor=demo_executor)` so Argyll listing,
connection and reconnection can return pending outcomes. Ordinary requests,
snapshots and status polling use the separate `desktop-owner` executor.
Omitting `setup_executor` retains synchronous Argyll calls.

A native caller can later pass an already-bound frontend to
`register_frontend(frontend)`. It returns a new selection token and reads only
the frontend's cached descriptor. It does not change the current batch, retained
data or scientific identity. The same registration owner handles constructor
choices and later choices; duplicate device identities are refused.
The existing `InstrumentBuild` owns authenticating a deliberately selected report,
explicit enumeration, selected initialization acknowledgements and binding.
Registration exposes that binding to `settings` and the existing guarded New
batch route. Renderer requests cannot register objects or select report paths. Chart
continuation also requires the native source-selection boundary; the raw
`continue_chart` action with a folder is refused at the renderer dispatcher.

## Current contract 48

Contract 48 adds two labels to dataset reading rows, `repeat_of` and
`chart_cell`; see [scientific identity and metadata](#scientific-identity-and-metadata).
Neither enters `dataset_revision`. Contract 48 also restores PR `ambient_spot`,
described below; the rest of this section's CR and PR behaviour arrived with
contract 47 and remains current.

PR-655 and PR-670 acquisition and settings use the shared Argyll binding. Direct
serial selection now lists the three CR and two CS models. PR's replaced direct
transactions, settings plans and parsers are removed; retained observations keep
their original attribution and quantities.

PR `ambient_spot` uses that same binding for illuminance XYZ in lux and spectral
irradiance, alongside emission's luminance and radiance. Publication requires
reported 2° observer and matching configured optics. Ambient honors the
reported Fixed or Adaptive exposure; it does not silently change that choice.
See the [PR setup and unit limits](argyll-native-controls.md#photo-research),
including ambient's unavailable TI3 projection.

Native PR reports distinguish unavailable inventories from known empty lists,
and retain D112's reported counts independently of parsed D116/D117 rows. The
current report includes inventory availability and original native errors.
Scalar settings can be checked when optical inventories are unavailable;
inventory-dependent writes require a known reported ID. Original captures
remain available when geometry refuses Argyll publication. Serial identity and
per-unit settings recovery remain exact and nonempty.

CR native reports retain 256-row inventories, exact IDs and original received
bytes, including malformed replies and partial tables. Counted records and raw
chunks keep these facts within the shared helper envelope. Complete finite raw
vectors can remain in native facts when their header cannot form a usable
`Spectrum`; independently parsed XYZ and a mapping-refusal reason can survive a
failed reading. A verified capture-start identity can attribute those originals
after quarantine, without restoring current identity or authorizing publication.
See the [record format](data-format.md) for the distinct raw, mapping and
publication limits. Direct CR remains available for its different firmware,
manual-recipe and radiometric domains; see the [route guide](direct-serial.md#choosing-a-cr-acquisition-route).

The shared native metadata envelope admits 4,096-byte identity text and 65,536-byte
helper frames. CR/PR inventory, text and raw-response limits are documented in
[native controls](argyll-native-controls.md). Public frontend request/event caps
remain 16,384 bytes. Clients must use contract 48 and the current generated
descriptors; no earlier development contract is supported.

Authenticated CR/PR bindings can expose typed native settings through the
[shared frontend](generic-frontend.md#native-settings-requests-and-retained-recovery).
Desktop setup owns one locked native control recovery state across reconnects.
Pending fields remain tied to canonical model and reported serial; reading
settings cannot clear an earlier failed application's uncertainty. Native
settings records contain no invented measurement. The renderer remains a
consumer of these shared descriptors and requests.

Live batch `companions.formats` entries expose `refresh_pending`, an authoritative
boolean distinguishing a healthy stale output awaiting its owned deferred/queued
refresh from an actual file failure. Its state, source digest and retryability
remain unchanged, including in exact Close/discard disclosure. See
[automatic companion snapshots](application.md#commands-and-snapshots) for the
owner conditions and failure boundaries.

Qualified relative reflective dataset previews expose `common_reference_y`,
unit-one scale, authenticated theoretical `source_white` and original
`native_context`, with separate adapted XYZ/display encoding and null native
gamut status. Absolute emissive previews retain their cd/m² scale.
The shared imported qualifier is `presentation.qualify_reflective_xyz`.

Argyll and direct setup can return pending outcomes. Snapshots expose
`equipment_setup.active_setup` with `command_id`, `action`, `backend`, `stage`,
`cancel_requested` and `cancellation_mode`; `cancel_setup` requests cancellation
of that exact command. Argyll uses `after_initialization`, which waits for the
native metadata helper to end and prevents registration when cancellation was
accepted before its commit; direct serial uses `cooperative`. Cancellation after
registration, or after a listing has published its candidates, is refused as too
late without setting `cancel_requested`.
Neither mode confirms physical abort or idle. `equipment_outcome`
reconciles the original UUID without repeating initialization. See
[equipment setup](desktop-equipment.md#pending-setup-and-cancellation) for terminal
outcomes, timing and cleanup scope.

Argyll bindings can carry validated `current_binding_identity` and receipt
provenance (see [instrument descriptions](argyll-description.md)). The
[record format](data-format.md) keeps unknown native CR spectral units separate
from the driver's explicitly inferred density and its publication basis.

Settings reads refresh the displayed native report and recovery mask without
replacing the frontend selection token. Selection, batch creation and correction
checks ignore only `native_controls.current` and `native_controls.pending` when
comparing descriptors. Changed build receipts, initialized identity or capability
content still invalidate a stale selection. Pending recovery remains enforced by
the frontend at preparation and dispatch.

Each chart `view` cell exposes nullable `patch_name`: descriptive Classic names,
`null` for SG, custom and TI2 (see [chart state](chart-state.md)). Names change no
request, stored definition or digest.

The [analysis workspace guide](analysis-workspace.md) documents live/saved dataset
binding, JSON/CSV/MAT export and qualified derived-science requests. Snapshot packets
retain the actual live `batch` owner and separately expose `dataset_binding`.
The selected binding has an opaque `id`, integer `generation`, `kind`,
`read_only`, `available`, `reason`, `roster_revision`, frozen `source`,
`comparison` and optional saved-file `review`. It never fabricates a writable
owner for saved files. Native cleanup, software-stop and close still belong to
the actual live batch.

Renderer `dataset`, `projection`, `analysis`, `annotation`, `set_view`,
`set_analysis_context` and `derive` requests require the selected `binding_id`
and `generation`. Data/projection/analysis/derive replies carry that fence.
The native `choose_science_material` dialog accepts that fence, `dataset_revision`
and role `illuminant` or `characterization`. It selects one bounded (131,072-byte)
UTF-8 JSON document using the existing Spectrum fields `wavelengths_nm`, `values`,
`quantity` and `unit`; no CSV or SPECT input grammar is inferred. Illuminants
declare `relative_spectral_power` and unit `1`; characterizations declare
`reflectance` and unit `1`. Cancellation returns null. A successful capture returns
only `token`, `source_name`, raw-file `sha256`, capture `identity` and `role`.
Use `{token}` in a derivation's illuminant or characterization field and null for
its associated identity field. The session authenticates the captured file bytes
and inode and resolves both derivation and analysis export through the same
existing scientific owner, supplying that capture identity. Tokens remain bound
to their dataset identity and scientific revision; changed source files, roles,
datasets or fabricated tokens are refused. A renderer rebind of the same data
retains the token, while each call still requires its current generation.
Ordinary typed-spectrum owner requests remain supported. Grid, power, reference
coverage and model-assumption eligibility stay with the numerical owners.

Each binding's `set_view` requires `plotSlots`: two distinct values chosen from
`spectrum`, `chromaticity` and `space`. Views retain their plotted membership,
scale and control choices separately; the renderer fences caches and late replies
by binding identity and generation. Saved comparison context is ephemeral and
can be available without a live manifest save.

Projection/analysis additionally retain their existing scientific revision,
comparison version and explicit scale; analysis carries selected paired keys.
Annotation carries `reading_ref`: a local operation/observation pair or the exact
source-qualified inherited reference returned by the dataset. Inherited metadata
is queried through the existing read-only owner and cannot be edited. View membership and
ephemeral context refer to this selected dataset, including when no live batch
exists. Old unfenced data envelopes are refused rather than aliased.

`settings` accepts exactly `{"selection_id": null}` or a retained opaque selection
ID. It returns a compact `selections` catalogue of selection ID, device ID,
device identity, provenance and synthetic flag. With an ID, it also returns the
selected full authoritative descriptor and current selection ID. Without one,
`descriptor` and `selection_id` are null. All returned data are independent
copies; mutating a reply cannot modify the retained descriptor.

The public frontend's cached `describe_json()` supplies capability, mode,
option, calibration and quantity facts. It performs no discovery here. A changed
descriptor invalidates the old selection token. An explicit `settings` refresh
returns a replacement token with the changed descriptor. Creation refuses an
unknown token or a descriptor that changed after the operator was shown it,
before directory creation or batch mutation.

The native host calls
`create(folder, name, notes, *, selection_id, mode, options)`. The folder comes
from its native dialog. The renderer supplies bounded selection, mode and option
values, never an instrument object or device path. The selected frontend and
explicit advertised mode go to the existing `BatchApplication` constructor
or its guarded New batch transition. That owner validates through public held
`prepare()` before directory creation. The bridge passes the shown descriptor as
`expected_descriptor`; the owner compares it with its current descriptor before
that single preparation. A change after the bridge's preliminary check therefore
requires selecting the source again, without creating a folder or replacing the
current batch. The owner then freezes the actual descriptor, mode,
provenance and options in strict schema 5. Available descriptor workflows use
the same durable owner. Changing source or mode requires an
explicit new context/New batch. There is no implicit Demo constructor or older
create-envelope alias.

Display-calibration selections use the frontend's observed catalogue identities;
arbitrary paths are not choice IDs. The native host stages correction files through
the [equipment setup boundary](desktop-equipment.md#native-correction-material).
Renderer-created and edited options carry only the returned opaque token. Raw
paths and material text are refused. Selection captures and validates bytes without
operating equipment; Create or Apply reauthenticates the exact source and settings
scope before freezing the request. The driver's declared factory/display-type
calibration remains distinct, and explicit `correction: null` clears the selection
where that mode permits it.

## Selected chart queries

`chart_view` and `chart_readings` require an explicit nullable `pass_id`.
`chart_history` requires `pass_id`, `cell`, `offset` and `limit`; `chart_details`
requires `pass_id` and `attempt_id`. `chart_decision_details` has no operands,
and `operation_details` requires `command_id`. Selected-source `activity` has
no operands; `activity_details` requires the opaque `key` and exact
`activity_revision` from that roster. Both use the same read-only binding owner
for live, saved, standalone and qualified inherited sources. Each also requires the selected
`binding_id` and `generation`, and returns `{binding_id, generation, value}`.
They delegate to the selected live owner or authenticated saved binding. They
never retarget acquisition or mutate saved files.
Activity summaries use `reading_count: null` when the operation result envelope
is unavailable. Parsed results report the exact integer patch count even when
source review excludes them; a parsed zero-reading result reports `0`.
Observation-only imports report `1` without inventing command or terminal facts.

## Native dense Demo data

`preload_demo(*, batch_id, generation, count, stop_event=None)` appends 40 or 160
readings to the currently bound packaged Demo source. It uses ordinary batch
Measure, instruction response and canonical save commands. It retains native
pairs, quantities, unavailable values, annotations and existing readings through
the same owners. A simulated provenance label on a native worker does not admit
this method: `demo.is_demo_frontend(frontend)` checks the packaged Demo frontend
and I/O implementation.

The native caller runs loading off the UI thread. It may retain a
`threading.Event` and set it directly from the UI thread to stop loading; this
does not require another acquisition worker. The loader cancels its own admitted
Demo operation, observes the owner's terminal/stop/save facts, and stops on a
failed or refused operation. Binding changes stop further loading. It never
retries an unsaved result or substitutes missing quantities.

The returned object contains `batch_id`, `requested`, `completed`, `retained`,
`state` and `reason`. `completed` counts successful saved operations in this load;
`retained` counts newly retained readings, including any retained failure data.
Existing data remain unchanged. Renderer requests cannot invoke the native
method. The host refreshes authoritative snapshot/dataset state after return.
These generated data exercise tables and plots without establishing physical
measurement, calibration, display accuracy or instrument repeatability.

## Native process-exit confirmation

The native host calls `confirm_process_exit(*, batch_id, generation, discard_token)`
with the binding and contents token captured for its confirmation dialog. The
bridge forwards those exact values to the batch owner's `confirm_process_exit`
action once, without refreshing a stale confirmation. Only the owner's exact
`admission="admitted"`, `state="completed"`,
`effect="process_exit_acknowledged"` result can authorize the host's process exit.
The action acknowledges eligible retired publication locks and confirmed software
stop in memory; it does not publish files or release uncertain locks. Renderer
requests cannot invoke this native method or send the raw action.

## Native quantities and unavailable values

Colour rows preserve each original `backend_reported.xyz` and `spectrum`,
including null. Missing XYZ produces null xyY/u′v′ coordinates and explicit
reasons, unavailable RGB previews and Lab, and no retained-white eligibility.
It never produces zero XYZ/Y or suppresses a retained spectrum row. A valid
XYZ-only reading remains eligible for colour analysis; absent spectrum produces
an unavailable spectral comparison without hiding valid Lab or native-Y metrics.

Reference/white selection and numeric analysis retain the batch owner's saved
version and exact canonical-byte authentication. Native Y percentage and u′v′
distance retain their [independent scientific domains](science.md#native-y-change-and-cie-1976-coordinate-distance).
Scalar refresh-frequency and display-timing outcomes are separate from these
colour rows; this dataset bridge does not reinterpret them as XYZ.

Reference model DTOs separately declare absolute emissive cd/m², CIE1931_2 and
their ideal D65 target. A bound dataset with incompatible unit, scale, observer
or illumination has no model `xyz_points` or model Lab overlay, with explicit
component reasons. Native Lab results remain independently available under a
matching declared white. Dimensionless xy/u′v′ primaries and model white remain
available only for a matching observer; their source/model assumptions are
retained. No relative Y or illuminance is silently normalized into display XYZ.

## Scientific identity and metadata

Current standalone import and native component export APIs are described in the
[standalone record guide](standalone-records.md). They reuse this same binding and
preserve any actual live owner. Standalone file identity is explicit; it is not
represented as a batch/context UUID.

Dataset rows, dirty annotation drafts, plotted/selected membership and analysis
reference keys use one opaque identity for the native `(operation_id,
observation_id)` pair. The Python bridge generates that identity; consumers use
it unchanged. Equal observation ID text in different operations remains distinct.
Each original ID stays inside the observation, and owner commands, annotation
queries and reference/white choices carry both original native IDs. No native ID
is rewritten, and a dataset key is not an observation ID.

The dataset's metadata cache follows its source revision so applied names, notes
and draft state can refresh. Its explicit `roster_revision` is the current
`batch_id/revision` cache key for a batch, or its standalone source-identity
digest/revision. Consumers compare that
roster stamp with `snapshot.dataset_binding`, together with binding identity and
generation, while refreshing rows; the scientific hash is not a roster stamp.
Both revisions are `"empty"` when no dataset source exists. The reply
also carries `preview_scale`, the common luminance in cd/m² for absolute
emissive `display_previews` (the presentation owner's default, 100); the renderer
takes its initial absolute preview scale from it rather than restating the
number, and `projection` encodes any other absolute scale on request. Qualified
relative reflective previews independently carry `common_reference_y=100`,
unit `1` and their declared perfect-diffuser scale. Changing `preview_scale`
does not alter those relative packets. See the shared
[measurement preview models](illustrations.md#relative-reflective-measurement-previews).
Rows expose the bounded text as `notes_preview` and
its full character count as `notes_length`. `repeat_of` is the reading reference a
Repeat measured again, or null. `chart_cell` is the guided-chart patch
`coordinate` and Classic `patch_name` (null otherwise) of the attempt that
produced the reading, or null outside a chart. Both come from the bound source's
own operations and chart, so saved review keeps the labels its batch recorded,
and neither enters `dataset_revision`. Exact applied/draft notes remain at
the read-only `annotation` query; a preview is never an editing value. There is
no older `notes` row alias. Its `dataset_revision` instead hashes explicit source
identity plus ordered immutable native-observation identities and
content digests. Each observation digest is calculated once when the immutable
record enters this session's cache. Draft text, annotation names/notes and view
changes do not change that scientific identity. A newly retained observation or
new context does. Comparison version, exact reference/white, selected identities
and common preview scale remain separately fenced request/reply inputs.

The bridge refuses unauthenticated comparison choices. Live comparison context
retains the batch owner's saved-byte authentication; a saved dataset may keep
explicit ephemeral context without rewriting its source. A stable scientific
key does not authorize reuse across changed inputs or erase saving status.
Disk review remains read-only; this API does not reopen a saved batch as a
writable acquisition session.

## Software checks and limits

`tests/test_desktop_session.py` covers explicit source creation, another source
on New batch, stale descriptor/unknown-token/bad-mode/option refusal before folder
mutation, a valid descriptor change between preliminary and final admission,
independent copies, optional native quantities and stable scientific identity.
Two actual retained Demo operations with equal observation ID text authenticate
independently and retain separate dataset keys, selections, comparison/white
sources and annotation drafts. Raw correction and display-type path checks
observe the existing correction reader and verify refusal without draft or
manifest changes. Source substitutions used for spectrum-only cases are identified in
the tests. `tests/test_presentation.py` checks missing-XYZ preview/Lab/white guards
without changing valid source values. The actual renderer and native host are
separate consumers with their own verification. Bound frontend support does not
establish physical device discovery, initialization, calibration or accuracy.


## Native chart input staging

`stage_chart` accepts exactly `batch_id`, `generation`, `context_id` and a small
`recipe`. Classic/SG recipes contain only `kind`; custom recipes add `name`,
`rows` and `columns`. Complete definitions go directly to the shared batch owner's
`stage_chart_definition` method. `stage_chart_target(path, ..., strips_as)` is native
only: it imports the selected TI2 locally and returns the same bounded token/preview.
`configure_chart` carries only that token and exact nullable mapping confirmation.
Paths, target JSON and original source bytes are never accepted in renderer recipes.

`stage_chart_continuation(folder, ...)` is native only. It returns a session-held
source token, pass coverage and explicit declarable material facts. The renderer's
`continue_chart` method requires the exact batch/generation/context, source token,
caller-owned `command_id`, `action`, `source_pass_id` and `acknowledged_material`.
The bridge constructs the existing owner command using the held folder/hash. Replaying
identical command content retains its UUID and original effect; raw source paths and
source substitutions are refused.

`chart_colors` requires the selected dataset fence plus explicit nullable `pass_id`,
`color_space` and typed nullable `expected_context`. Its reply uses the standard
`{binding_id, generation, value}` envelope. The dataset owner verifies canonical-source
and chart revisions independently of optional-progress/roster stamps. HTML preserves
that scientific source identity and fences late replies by binding, request, pass,
definition and chart revision; it performs no scientific conversion.
