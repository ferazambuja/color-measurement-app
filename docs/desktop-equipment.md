# Desktop equipment setup API

`DesktopSession` exposes deliberate equipment setup through the existing
`InstrumentBuild`. Report authentication, linked catalog, OS enumeration,
initialization policy, identification and frontend binding remain at that owner.
Setup does not replace the current batch or its retained data, drafts, source
binding or cleanup ownership. Ordinary acquisition still uses the selected
registered frontend through the application owner.

The [desktop connection guide](desktop.md#connect-an-instrument) describes the
operator flow. A standalone app ships authenticated instrument software and starts
with no measurement source selected. The host verifies its portable report against
the digest in `Info.plist` and the retained native notices against that report,
then calls the existing `select_build_report` method, which checks both workers.
That configures software without starting a worker, enumerating connections or
initializing an instrument. **Connect instrument**, deliberate listing and
selected-device initialization remain separate operator actions. Demo also
requires an explicit choice.

For ordinary CR-100, CR-250, PR-655 and i1Pro 3 acquisition, choose the
authenticated Argyll route. Python direct CR remains an explicit diagnostic
fallback after recording a failure from the equivalent Argyll operation. It
keeps its own backend identity and result. The application does not retry through
another backend automatically; diagnostic fallback must respect firmware,
optics, units and uncertain-stop refusals.
See [CR route guidance](direct-serial.md#choosing-a-cr-acquisition-route).
CS-2000/CS-2000A continue to use their direct route.

## Native direct serial setup

The same setup owner registers direct CR/CS frontends and retains their
persistent host port. `equipment_setup` adds an inert `direct_serial` catalogue
with contract `direct_serial_setup_v1`. Its five model rows expose string
`model_enum`, display label, backend, assessed initialization effects and refusal,
`opening_requires`, reported-serial assertion policy, timeout defaults/floors
and explicit unverified physical validation. They are direct protocol policies,
not Argyll enum numbers. The response-dependent CS floor is described in the
[direct instrument API](direct-serial.md); CR has no established worst-case
exposure floor.

PR-655 and PR-670 use the shared `InstrumentBuild` selection, initialization and
`InstrumentFrontend` acquisition path, including its native settings adapter.
Choose them through the authenticated Argyll build report and deliberate device
listing. Their initialization effects, native identity and capture qualification
remain with that owner; see [native PR controls](argyll-native-controls.md#photo-research).

The native host calls these methods after an explicit port choice and
acknowledgement:

```text
connect_direct_serial(port, *, command_id, setup_generation, model, expected_serial,
                      accept_disclosed_initialization, response_timeout, measurement_timeout,
                      executor=None)
reopen_direct_serial(port, *, command_id, setup_generation, binding_id, binding_generation,
                     accept_disclosed_initialization, response_timeout, measurement_timeout,
                     executor=None)
```

The renderer cannot call these methods or supply a port path. Native chooser
cancellation makes no connection call and preserves existing sources. Tests may
inject a transport and clock, which declares simulated provenance. Successful
connection identifies the chosen model, checks the serial assertion and reads
current settings before ordinary frontend registration. Its retained command
result provides selection/binding IDs, descriptor and separate native receipt
with identity exchanges and settings originals. Every direct model must report a
nonempty serial (CR `RC ID`, CS seven-digit `IDDR` serial) before registration,
so the reported model and serial supply stable source identity
(`identity_basis` `reported-model-and-serial`); duplicate active paths or reported
units refuse. Reopen asserts the binding's retained model and serial. A binding
that failed before identification without a serial assertion retains none and
cannot be reopened.
The operation does not acquire a sample, apply the normal recipe or calibrate.

## Pending setup and cancellation

Admission and final registration share the session mutex; Argyll metadata I/O,
direct serial initialization and settings readback run outside it. With the host's
existing worker executor, Argyll listing, connection and reconnection return
`state="pending"` promptly, as direct setup does. Without an executor, an Argyll
call waits for its terminal outcome. One admitted setup effect excludes another
setup, source change or acquisition until its runner ends. Exact UUID replay
returns its current ledger entry without another submission. Final registration rechecks
cancellation before installing the source; direct registration also rechecks the
setup and binding generations. Failed submission
revokes any queued callback before it can acquire an instrument.

`equipment_setup.active_setup` is null or
`{command_id, action, backend, stage, cancel_requested, cancellation_mode}`, where
`action` is the admitted request (`refresh_devices`, `connect_device`,
`reopen_device`, `connect_direct_serial` or `reopen_direct_serial`). Snapshot,
settings and path-free `equipment_outcome` remain available during actual I/O.
Renderer `cancel_setup` accepts exactly `command_id`, records cancellation for
that exact effect, and returns its current ledger outcome. Once source registration
has committed, or a listing has published its candidates, cancellation is refused
as too late without setting `cancel_requested`; the active lease still remains
owned until the runner ends.

| `backend` | `cancellation_mode` | Cancellation behavior |
| --- | --- | --- |
| `argyll` | `after_initialization` | Cancellation accepted before registration waits for the existing metadata helper to finish or reach its response timeout and shutdown, then prevents adding the source |
| `direct_serial` | `cooperative` | Signals the original driver-owned cancellation Event across identification and initial settings readback; cleanup and host release remain separate |

Argyll cancellation does not forcibly abort initialization. Its metadata response
wait defaults to 30 seconds, independently of the binding/read allowance; this is
not a strict 30-second bound on the whole setup or cancellation, which also waits
for the observed helper shutdown. A dispatched attempt whose cancellation was
accepted before registration ends with `state="failed"`, `cancel_requested=true`
and an explicit unconfirmed instrument-state reason. An actual native failure
remains the primary failure.
Validated original metadata `native_events`, including any reported cleanup or
software-stop facts, are retained. They do not establish physical abort, idle or
restoration. Cancellation confirmed before a queued worker starts can instead
report that no instrument was opened.

For direct serial setup, ordinary cleanup detaches the initialization Event,
retains startup originals and CS recovery debt, and reports host release
separately. Acquisition retains its own existing cancellation event.

Path-free `equipment_outcome` accepts exactly `command_id` and returns the retained
outcome for direct or Argyll setup. Delayed replies reconcile through that method;
the host does not reopen a chooser or initialize again. Unknown IDs and changed
content under an existing command ID refuse. Failure receipts retain completed
native originals, partial settings and cleanup facts.

## Direct connection cleanup and recovery

Existing `close_device` validates that the source is idle before closing a direct
host port. Only confirmed host release permits selection retirement. Failed close
retains a `cleanup_required` binding, its owned port and selection, and blocks
another initialization on that path. An explicitly new close command can retry
host release; replaying the failed UUID performs no retry. A failed candidate
whose host close remains unconfirmed also appears as cleanup-required, even if
frontend registration never succeeded. The reply never claims physical abort or
readiness.

The setup lifetime retains one shared CS backlight-restoration ledger. It passes
that same ledger to each direct instrument, including after confirmed selection
retirement and deliberate reopen. The driver keys recovery by verified model and
reported seven-digit serial; another unit cannot consume that debt. Failed startup
or restoration preserves it. Only confirmed restoration or genuine manual-on
readback clears the relevant entry. This session-owned state does not survive
application restart and does not establish physical device stop.

Direct reopen requires a previously closed binding. Its retained model and serial
supply the assertions; the renderer cannot replace them. A new native-selected
port may be used. Failed reopen preserves the old closed source unless its new
candidate needs retained host cleanup. Successful reopen keeps reported source
identity and increments binding generation. Registration rollback preserves
other direct instruments' actual host objects; it cannot undo native effects.
An active acquisition retains its shared I/O lease and blocks setup through the
application's existing stop guard. After its worker has stopped, an unconfirmed
host close retains the quarantined instrument/port/path and a cleanup-required
binding. Public close can retry release after Finish; a failed original record
remains unchanged. An interrupted channel construction retains the acquired
transport through that same cleanup owner even before registration succeeds.

## Select a build report

The native host calls `select_build_report(path, *, expected_setup_generation)`
with its verified packaged report, or with a file-dialog result in a wheel or
checkout launch. The manual chooser is under **Instrument software details**;
standalone apps use their shipped software instead. The renderer cannot supply
this path. The method
authenticates both workers through `InstrumentBuild.from_report`, reads only
the cached catalog and verifies that the report bytes did not change during
selection. It starts no worker and performs no enumeration or initialization.
A failed selection leaves the previous setup intact. Successful selection
increments the setup generation and retires previous candidate tokens.

For software rehearsal, the same native method accepts `simulated=True`, an
explicit `source_root` checkout, and a description `scenario` such as
`"unknown_serial"`. `InstrumentBuild.from_report` uses the same keywords. This
selects the original sibling `description-stub-worker` and
`generic-session-stub-worker` through `description_workflow.executable_sha256`
and `generic_session.executable_sha256`. It never substitutes bytes into the
production worker fields. Explicit `simulated_session="chart"` instead selects
the sibling `generic-chart-stub-worker`, authenticated against the original
`generic_session.chart_executable_sha256`. The default is `"generic"`; this
selection requires `simulated=True` and is not available to renderer requests.
The context names the selected session worker. This chooses an existing
substituted test actor, not a different device acquisition implementation.
The report's `build_system` must match the current host. All dedicated worker
names have the `.exe` suffix on Windows; no alternate basename is searched.
Both original sections must pass; the pinned archive,
version, executable digests, complete current native inputs, build transforms,
capability vocabulary, and selected contribution inputs are checked by
`native_receipt.authenticate_stub`, also used by the software gate. Verification
starts no process. The original `report.json` and broad driver catalog are
required. A packaged application without the native source checkout cannot
authenticate this rehearsal path.

The report snapshot includes `context` with worker kind and provenance. A
simulated context also declares the source checkout, description fixture and
limitations. The acquisition substitute parses selected paths but does not
prove OS-path attachment. Description and acquisition substitutes have
independent serial fixtures: an available mismatched serial remains a refusal.
The `unknown_serial` description fixture permits only session-local identity;
acquisition retains its own returned serial. These facts do not establish
physical initialization or measurement. Trusted local receipts detect drift;
they do not authenticate an arbitrary report author or make arbitrary bytes safe.

Renderer `equipment_setup` takes an empty object and returns an independent
snapshot: `generation`, `listing_generation`, `state`, `reason`, authenticated
report path/SHA-256, linked catalog and candidates. The report digest identifies
the bytes selected, rather than claiming that the path can never change.
The native host adds `instrument_software` with `packaged`, `ready` and `error`.
This describes software availability, not instrument attachment or successful
initialization. A missing or changed packaged kit leaves an explicit connection
error; it does not switch to Demo or another build report.
The cached catalog contains driver assessment and disclosure facts. No model
name establishes attachment, mode acceptance or physical validation.
Each catalog row also retains `model_enum`, canonical `display_label`,
`binding_policy` and `opening_requires` from `InstrumentBuild.model_policy`.
Binding views preserve those same facts for the actual initialized native type,
with its `initialization_assessed` and `initialization_refusal`. Reopening controls
use the retained enum and policy directly; they do not infer a native type or
opening effects by matching the reported model against a catalog source label.

## Direct serial connection

The setup snapshot also includes `direct_serial.models` from
`direct_serial_catalog()`. It declares the five supported CR/CS identities,
initialization effects and timeout policy without enumerating or opening ports.
The native host's explicit port chooser retains a selected path under an opaque
token scoped to `setup_generation`. Renderer `connect_direct_serial` and
`reopen_direct_serial` carry that token; raw port paths are refused. A new native
choice retires the previous token. A submitted token belongs to one exact command
and intent; another command must choose its port again. Exact command replay
continues through the shared ledger without repeating initialization. A delayed
initialization reply is queried through `equipment_outcome` with its original
command UUID, without selecting a port or initializing again.

Direct bindings expose `connection_available` and `connection_reason` from the
frontend's inert descriptor. A registered binding is not usable when its channel
is closed or quarantined. Reopen requires a confirmed closed binding, its exact
generation, retained model/serial and a new explicit native port choice.
`close_direct_sources()` is native-only and reuses the setup owner's retained
close ledger; application shutdown calls it after admitted batch close. Failed
cleanup remains owned and requires an explicit decision. The direct producer's
protocol, settings, quantity and cleanup limits are documented in
[direct serial instruments](direct-serial.md).

## Native correction material

`settings(selection_id)` also returns `settings_generation`. Registration,
retirement, replacement and changed cached descriptors advance that generation.
Native choosers can call:

```text
stage_correction(path, *, selection_id, mode, capability_id, settings_generation, options)
stage_current_correction(path, *, batch_id, generation, context_id, draft_id, version, options)
```

These methods are not renderer requests. They read an ordinary selected file,
capture exact UTF-8 bytes, reuse the existing CCMX/CCSS material parser and complete
frontend validator, and return `correction: {token}` plus separate `material`
metadata: kind, source name, source-byte SHA-256 and parsed-material SHA-256.
The selected mode must advertise that correction kind in
`correction.by_mode[mode].kinds`, which follows its exact configured capability
snapshot. The top-level kind list and `accepts_correction` describe availability
across offered modes. Installed display
corrections, ambient restrictions and driver matrix rules retain their existing
validation. Selection neither changes native calibration nor operates equipment.
The correction must fit the existing frontend request bound.

New batch passes that token as `options.correction` to `create`. Current Apply
uses the existing `edit_draft` command and its exact draft version. First use
reauthenticates the selected file bytes and token's selected source, mode,
descriptor and settings generation, or current batch/context/draft version.
The ordinary frozen request retains captured text with source path, byte digest
and material digest. The Configuration owner validates those provenance hashes;
it does not reread the documentary path instead of using captured material.
Current renderer draft options expose a stable opaque token, with documentary
metadata in packet `correction_material`. Raw paths and correction text remain
refused through renderer requests.

Explicit `correction: null` clears the selection and reuses the driver's default
factory/display-type calibration, where that mode permits it. A mode that
requires an explicit matrix still refuses an invalid clear. Native settings are
applied through the existing acquisition owner. Accepted command UUID replay
uses the existing batch registry and does not reread or reapply the correction;
changed command content remains a refusal. A changed source file requires new
native selection before another command uses it.

## Explicit listing and selected initialization

`refresh_devices` requires `command_id` and `setup_generation`. It enumerates
exactly once through `InstrumentBuild.attached`. Each listing increments
`listing_generation` and retires old tokens even if enumeration fails. Every
returned candidate has an opaque `candidate_id`, exact path/type/transport,
initialization assessment and disclosure, `identified`, `attachable`,
`opening_requires` and `consumed`. Unidentified, unassessed and prohibited
candidates remain visible. OS access during enumeration is platform dependent;
it is separate from instrument initialization.

`connect_device` requires exactly:

```text
command_id, setup_generation, listing_generation, candidate_id,
expect, expected_serial, allow_serial_identification,
accept_disclosed_initialization, accept_unassessed_initialization, timeout
```

The selected candidate must belong to the current listing. The three
acknowledgements must be booleans. `timeout` is finite seconds in `(0, 600]`
for the acquisition binding, above every published mode/output read allowance floor;
description keeps the existing metadata client's timeout. The facade delegates
type, serial, identification and disclosed/unassessed initialization policy
to `InstrumentBuild.describe`. An acknowledgement does not permit prohibited
firmware initialization. Typed serial selection and generic serial identification
remain different policies from the existing linked catalog.

Arguments-only selection, serial-policy and acknowledgement validation reuses
the description owner's validator before the candidate is consumed. A form
mistake leaves the same candidate available for an explicit corrected command.
Direct binding and setup both call `InstrumentBuild.validate_binding_timeout`,
which enforces the highest published mode/output allowance before binding.
The candidate is consumed before an attempted description. A later attempt
requires an explicit new listing and selected candidate. The facade calls
`bind` on the returned description, then `register_frontend`; the result provides
the normal source `selection_id` and full authoritative descriptor for New batch.
A path already registered in this session is refused before another description.
This does not acquire or calibrate a sample. The registered identity derives from
the reported model and serial, retaining path/index/listing facts separately.
Without a reported serial, identity is explicitly session-only; it does not
establish continuity after replug. Duplicate reported model/serial bindings are
refused. The catalog and resulting descriptor expose `binding_policy` with all
word-specific floors, the highest floor, and the application's declared default
timeout/margin. An omitted direct `InstrumentBuild.bind` timeout follows that
policy. The margin is an allowance policy, not proof that a read will complete.

## Close and deliberately reopen a binding

Successful connection also returns `binding_id` and `binding_generation`.
`equipment_setup.bindings` retains active and closed binding summaries with
generation, reported model/serial, device identity and the separate selected
path/index. These identifiers belong to logical session selections. Acquisition
workers are owned by individual operations, rather than remaining open here.

Path-free `close_device` requires exactly `command_id`, `setup_generation`,
`binding_id` and `binding_generation`. Finish an open batch using this source
before retiring it. Close removes its selection tokens and retires candidate
tokens. The reply explicitly reports software selection retirement,
`instrument_state="unknown"` and no persistent native worker cleanup. Retained
batch data and its original descriptor remain unchanged.

After explicit Refresh, `reopen_device` requires exactly those four identifiers
plus `listing_generation`, `candidate_id`, `allow_serial_identification`,
`accept_disclosed_initialization`, `accept_unassessed_initialization` and `timeout`.
The original reported model and serial supply the assertions; the renderer
cannot replace them. A missing original serial refuses continuity and requires
a new source selection. Initialization acknowledgements and driver policy remain
the same as ordinary connect. A changed port/index may be deliberately selected;
it does not establish identity without the reported model/serial checks.

Reopen supports a retired binding or replacement of an active binding after its
batch is finished. The previous source stays registered until description,
binding and replacement registration succeed. Failure retains that selection,
descriptor, binding generation and data. Each attempted initialization consumes
its candidate; lost replies reconcile under the original command ID, without
automatic retries or reinitialization. A successful replacement keeps device
identity, increments binding generation and gives a new selection token. Stale
listing/binding generations refuse before another initialization. No path loss
automatically creates a new source or rewrites an earlier batch context.
Registration and retirement update the selection and binding registries within
one bounded software transaction. A callback failure or control interruption
after it mutates selections rolls both registries back together. An attempted
native description remains consumed; rollback does not claim that initialization
or instrument effects were undone. The original control object still propagates,
and its retained command outcome prevents another initialization on reconciliation.

Refresh/connect replies contain `command_id`, semantic `content_sha256`,
`setup_generation`, terminal `state`, `reason` and `result`. Repeating the same
command ID and content returns its retained outcome without repeating native
work. Changed content under that ID refuses. Failed native work remains failed;
the facade does not retry it automatically. A control interruption propagates
the original signal after recording its stage and explicit unconfirmed native
cleanup/instrument-state reason. Repeating that command returns the interrupted
outcome without reinitialization; it does not turn the interruption into a driver
failure or confirmed cleanup. Setup changes are refused while live
acquisition, Demo preload or direct initialization is active. The native host runs
blocking effects outside its UI event loop and displays the exact initialization decision.

Tests authenticate inert report/worker bytes and use recording description
clients through real `InstrumentBuild` policy. They forbid description-worker
execution and native acquisition. This establishes software composition only;
it does not qualify physical equipment or claim an observed native GUI route.
