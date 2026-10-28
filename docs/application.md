# Headless descriptor-authorized batches

`color_measurement.application.BatchApplication` owns a complete headless
free-sample batch over an explicitly supplied, already-bound `InstrumentFrontend`
or `DirectSerialFrontend`.
The caller selects one advertised, available mode and supplies its current
options. Ordinary Measure requires its single-spot offer; descriptor-authorized
non-spot operations use the same durable owner. Demo is an explicit frontend
choice. Physical, simulated and replay provenance comes from the frozen frontend
descriptor and remains unchanged in retained records. No real-equipment testing
has been performed; software checks establish no physical measurement validity.

The [offline desktop host](desktop.md) uses this same owner for its operator route.
Frontend selection and native initialization belong to the existing
[generic instrument owner](generic-frontend.md#selection-and-acquisition) or
the [direct serial owner](direct-serial.md#explicit-connection-and-native-acquisition).
Constructing a batch does not enumerate, initialize or acquire from a device.

The application owns command admission, frozen setup and sample drafts,
instructions, retained outcomes, annotations, saving, recovery and lifecycle.
The selected [instrument frontend](generic-frontend.md) or
[direct serial frontend](direct-serial.md#shared-frontend-and-chart-facts) owns request
validation, acquisition, calibration and typed records. A renderer consumes
snapshots and sends commands; draining and terminal saving continue when that
renderer stops polling or binds again.

`BatchApplication(folder, *, frontend, mode, options, expected_descriptor=None, name="Untitled batch", notes="")`
requires all three acquisition arguments. It validates mode and options through
`frontend.prepare()` before creating the output directory or acquiring its batch
lock. A mode without spot uses its advertised zero-reading `mode_snapshot` for
this nondispatched configuration validation. This validation creates no acquisition
worker and performs no device I/O.

An operator frontend passes its cached, shown descriptor as `expected_descriptor`.
The owner compares its JSON content with the current frontend descriptor before
preparation, directory creation or lock acquisition. A changed descriptor is
refused so the operator can select the current facts. Object member order is
ignored; array order and field values remain significant. The existing preparation
validator also refuses a capability change between that comparison and request
validation. A direct headless caller with no shown selection may omit this argument
to bind the current frontend facts.

The current strict manifest schema is **5**: it retains the selected `mode` and nullable
`chart` alongside the frozen, content-authenticated descriptor. The descriptor supplies the exact
device identity, provenance and capability identity. Current draft options and
each operation's frozen options stay at their existing owners. Unsupported earlier
schemas are refused; no development-interface shim or migration is provided.

Measure admits ordinary spot. Explicit Calibrate is admitted only when the
selected mode's descriptor offers it. `run_workflow` admits the advertised strip,
X-Y, stored-chart, configuration, refresh-frequency and display-timing routes
through the same preparation, intent, dispatch, draining and publication owner.
Its patch count, identities and layout use the descriptor validator shared with
frontend preparation and saved-intent review. No device or native mask is selected
by an application-specific model branch.

Native XYZ and spectrum remain independently optional in color observations.
Scalar and timing records retain their typed values, stimulus and separate clocks;
they never acquire invented XYZ or spectra. This owner retains their canonical
operations. The Session/renderer consumer projects those record kinds through
their declared fields; ordinary color calculations do not accept them. This
source connection does not establish current packaged/native execution, physical
display timing or equipment qualification.

## Run the complete route

This standard-library example creates a new batch, names a sample, answers the
actual Demo prompts, reviews the reading and finishes. Choose a **new** directory
under an existing parent. The code intentionally answers every actual simulated
instruction; an operator UI must present the exact pending instruction instead.

```python
import json
import time
from pathlib import Path
from uuid import uuid4

from color_measurement.application import BatchApplication, review_saved
from color_measurement.demo import demo_frontends

frontend = next(front for front in demo_frontends()
                if json.loads(front.describe_json())["device_id"] == "demo:i1pro3")
batch = BatchApplication(Path("demo-batch"), frontend=frontend,
                         mode="emissive_spot", options={})
state = batch.bind()

def send(action, payload=None, *, identity=None):
    state = batch.snapshot()
    return batch.command(str(uuid4()) if identity is None else identity,
                         state["generation"], action, payload or {},
                         batch_id=state["batch_id"])

draft = state["draft"]
assert send("edit_draft", {"draft_id": draft["id"], "version": draft["version"],
                         "name": "White sample", "number_samples": False,
                         "notes": "Synthetic demonstration\nNo instrument used",
                         "options": {}})["admission"] == "admitted"
while batch.snapshot()["phase"] == "saving":
    time.sleep(0.01)
state = batch.snapshot()
draft = state["draft"]
result = send("measure", {"context_id": state["context_id"], "draft_id": draft["id"],
                          "draft_version": draft["version"]})
assert result["admission"] == "admitted"
while True:
    state = batch.snapshot()
    if state["instructions"]:
        instruction = state["instructions"]
        print(instruction["kind"], instruction["message"])
        assert send("respond", {"operation_id": instruction["operation_id"],
                                "request_id": instruction["request_id"],
                                "response": "accept"})["admission"] == "admitted"
    if state["phase"] == "idle":
        break
    time.sleep(0.01)
assert state["saved_count"] == 1, state["save_reason"]
details = batch.operation_details(state["operations"][0]["command_id"])
print(details["result"]["patches"][0]["observation"])
finish_id = str(uuid4())
assert send("finish", identity=finish_id)["admission"] == "admitted"
while batch.command_outcome(finish_id)["state"] == "pending":
    time.sleep(0.01)
finished = batch.command_outcome(finish_id)
assert finished["state"] == "completed", finished["reason"]
print(review_saved(state["folder"])["sample_count"])
assert send("close", {"discard_token": None})["state"] == "completed"
```

Fresh Measure reports `calibration_setup` then `sample_position`. Fresh explicit
Calibrate reports `calibration_setup` and retains a zero-reading terminal result.
After retained calibration, Measure may need no prompt. The frontend reports
those facts; the application never invents a new calibration requirement.
Cancel during Starting targets the already-held prepared operation. A cancellation
before dispatch resolves that handle without worker or device I/O.
Cancellation requested, terminal status, `software_stopped` and
`instrument_state` remain distinct.

## Commands and snapshots

`bind()` increments renderer generation and always returns the full snapshot.
`command(command_id, generation, action, payload, *, batch_id)` requires canonical
UUID command and batch identities. Every command carries its batch scope;
acquisition additionally carries context identity and expected draft version.
Inputs are captured as JSON values before waiting on the owner mutex. Sample and
annotation labels are single-line text without Unicode control or format characters or
line/paragraph separators, limited to 1,024 characters. Notes preserve multiline
text up to 65,536 characters; the serialized command is limited to 131,072 bytes.
JSON nonfinite numbers are rejected.

A command returns `admission`, `state`, `reason` and its semantic `content_sha256`.
Admission is `admitted`, `refused` or `no_change`; an admitted operation separately progresses
through `starting`, `active`, and `completed` or `failed`. A lost return can be
reconciled using `command_outcome(command_id)`. Repeating a
reserved command ID with identical content returns that outcome and never
repeats its effect. Different content under that ID is refused. An unadmitted
old-generation call is refused after rebind; already-admitted work continues.
The ledger belongs to this owner lifetime and is not replayed after process loss.

| Action | Exact payload fields | Effect |
| --- | --- | --- |
| `edit_draft` | `draft_id`, `version`, `name`, `notes`, `options`, `number_samples` | Resolve the visible label at this owner, validate through the existing frontend, retain the current draft, then save. A failed save retains the edit. |
| `configure_native_diagnostics` | `debug_level`, `limit_bytes` | Set separate native stderr provenance controls while idle. Level 0 disables capture; positive levels require the bound descriptor's authenticated native offer. This does not change optical options or chart context. |
| `measure`, `calibrate` | `context_id`, `draft_id`, `draft_version` | Prepare one operation, retain its normalized request/identity, reserve durable intent, then dispatch it once. |
| `run_workflow` | `context_id`, `draft_id`, `draft_version`, `workflow`, `patch_ids`, `layout` | Freeze a descriptor-authorized non-spot/configuration/scalar/timing request using the current validated options and batch mode. The existing durable intent and operation lifetime apply. Use Measure/Calibrate for their ordinary actions. |
| `presented` | `operation_id`, `request_id`, `stamp` | Submit the exact typed draw-completion stamp for a pending controlled presentation. This is separate from an operator response; wrong or expired identities do not consume it. |
| `repeat_sample` | `context_id`, `draft_id`, `draft_version`, `operation_id`, `observation_id` | Authenticate the source reading pair, repeat its frozen acquired label/options/sample identity with blank notes, and leave the prepared next target unchanged. |
| `set_comparison_context` | `version`, `reference`, `white` | Validate retained reference/white identities and declared assumptions through the existing presentation owner, then publish the versioned choice through the metadata writer. |
| `cancel` | `command_id` | Request cancellation of that current operation, including Starting. |
| `respond` | `operation_id`, `request_id`, `response` | Consume the exact current frontend instruction; stale or unsupported replies are refused. |
| `select` | `operation_id`, `observation_id` | Select an existing reading without acquisition. |
| `edit_annotation_draft` | `operation_id`, `observation_id`, `version`, `draft_version`, `name`, `notes` | Retain versioned editing text in Python, across renderer binds. |
| `annotate` | `operation_id`, `observation_id`, `version`, `draft_version` | Apply that exact Python draft, then publish the annotation asynchronously. |
| `discard_annotation_draft` | `operation_id`, `observation_id`, `draft_version` | Discard only that current editing version. |
| `retry_save`, `export_ti3`, `retry_companions` | None | Complete missing canonical publication, retry retained companion bytes, then refresh the accepted source without acquisition. Finished retries reacquire the batch lock and authenticate inputs. |
| `another_folder` | `folder` | Reserve a new directory, or retry a failed directory reserved by this owner; publish authenticated retained copies before releasing old ownership. |
| `finish` | None | Save a finished manifest when idle and canonical work is resolved, then release the writer lock. |
| `close` | `discard_token` | Close only after software stop; unresolved saving/drafts require the current `discard_summary()` token. |
| `confirm_process_exit` | `discard_token` | Acknowledge exact discard contents when this owner's publication destination is retired and software stop is confirmed or this owner is explicitly abandoned; perform no I/O or lock release. |
| `abandon` | `discard_token`, `reason` | After the worker is terminal, record explicit abandonment and cleanup facts, then retire this owner. |

`new_batch(folder, *, frontend, mode, options, expected_descriptor=None, name="Untitled batch", notes="")`
returns a distinct new owner only after durable Finish with
no active or unsaved work. Commands scoped to the previous batch are refused by
the new owner, even if its renderer generation has the same numeric value.
It forwards the shown descriptor expectation to construction; refusal leaves the
previous owner unchanged. Setup, annotation and operation guards live here rather
than in UI controls.

Snapshots include batch/context/generation/revision, descriptor, selected mode and current draft,
annotation summaries and saved flags, Python annotation draft summaries/status, selected
identity, pending instructions, the latest 32 source/application events, compact
operation summaries, counts, destination and optional export state. They exclude
full immutable observations/results and the lifetime command ledger. Per-reading
applied metadata and editing drafts contain `notes_preview` (the first 256 Unicode
characters, preserving leading newlines) and `notes_length` (full Unicode character
count), plus exact names, identities, versions and state. They omit full `notes`.
The one current sample draft remains complete: it is always displayed and bounded
at 65,536 note characters. Per-reading previews keep retained note text from
multiplying snapshot payload size as a batch grows.
`annotation_details(operation_id, observation_id)` returns a copied `applied`
overlay, with saved state, and `draft` metadata or null. Both contain exact full
notes, identities and versions; the draft also includes editing/publication state
and reason. The query reads memory without acquisition, publication or disk I/O.
After rebind an editor locates the exact identity/version/state in the snapshot
and fetches this detail; it never reconstructs editing text from a preview.
Caller mutation of returned details cannot change the owner.
`operation_details(command_id)` returns the retained operation or raw recovery
evidence. `reading_details(operation_id, observation_id)` returns one unchanged
observation. `command_outcome(command_id)` reconciles exactly one mutation.
Selection is an ephemeral presentation command and does not grow that ledger.
Mutation outcomes retain a canonical-content SHA-256 for duplicate detection,
rather than every historical copy of long editing text; exact current text and
frozen acquisition requests stay at their responsible owners.

Python owns per-reading annotation drafts, keyed by both identities. Editing
checks the applied annotation version and draft version. Bind preserves the text,
versions and state. States distinguish `editing`, `clean`, `pending`, `saving`,
`saved`, `unsaved` and `refused`; reasons and the separate command outcome remain
available. Apply reserves admission first and returns `pending`. Its worker
publishes the manifest outside the owner mutex; snapshots can show `saving`.
A retained result takes the same route: the drainer stages the operation and
recovery files and the manifest they imply under the mutex, publishes them
without it, then records the receipts, so snapshots and read-only queries answer
during the file and directory syncs while mutating commands are refused as
pending until the acceptance.
Application worker startup retains one callback owner and its thread identity.
An interruption during construction or before the callback claims ownership
revokes that callback, including a late bootstrap after a possibly launched
thread. Metadata remains retained and unsaved, with the guard released for Retry.
If the callback already claimed ownership, its writer alone resolves publication
and the guard; startup failure never retires it or starts a replacement. A
completed callback remains claimed even after its thread stops, so a failure
raised after completion cannot downgrade a successful publication. Command
outcomes preserve the startup exception type/message separately from their
actual eventual state. `KeyboardInterrupt` and `SystemExit` propagate after
ownership is made coherent.
CPython delivers operating-system signal handlers on the main interpreter
thread ([signals and threads](https://docs.python.org/3/library/signal.html#signals-and-threads)).
Worker failure branches also handle control exceptions explicitly raised by a
callback, dependency or injected test. Those checks establish control-exception
bookkeeping, not delivery of an operating-system signal to a worker thread.
Failed publication preserves the applied in-memory value and original draft with
`unsaved`, while new acquisition and Finish remain blocked. Retry completes the
manifest without acquiring a reading. A stale Apply is refused, with its reason.
Typing back to applied values clears the dirty guard, and Apply with no change
returns `no_change` without a write. Applied names and explicit sample drafts
must contain non-whitespace text; valid whitespace is preserved. New sample
identities get a nonblank generated default name.

Canonical saving checks newly retained/unaccepted records before accepting the
manifest. Each terminal, applied metadata or chart save then starts one owned
companion worker. Finish, Retry files and Another folder use the same route.
The owner freezes its already authenticated canonical native bytes, including
recovery, diagnostics and inherited chart originals. The worker reuses saved
review validation on that immutable capture, prepares `measurements.mat`,
`measurements.csv` and, for a chart, `chart-selected.csv`, and refreshes grouped
TI3/TI2 from the same frozen chart selections and annotations.
Serialization and companion publication run outside the application mutex.
Acquisition and applied metadata remain available during optional work;
`companions.saving` is separate from canonical publication and software-stop
state. One worker replaces queued, unprepared captures with the latest accepted
source. An output from an older capture remains explicitly stale until that
refresh completes. Prepared failed bytes are never replaced by coalescing.
Destination changes, Close and abandonment wait for active companion ownership;
finished-owner retry/export also waits before reacquiring the batch lock;
Finish saves canonical status and completes after the final refresh releases its
lock. Capture shares immutable original byte values and whole-batch serialization
cost grows with the saved source. Automatic preparation uses the live owner's
accepted source, rather than rereading every immutable operation from disk.
Publication refuses external manifest replacement and authenticates output
ownership; saved review and explicit finished-source retry authenticate current
disk originals independently. A live accepted capture is not a fresh disk review.
An owned atomic manifest replacement can cross a companion reader's safe open.
The reader reopens that classified ordinary-file inode race only when accepted
owner bytes have advanced or an expected owner publication is in flight. It then
rechecks ordinary-file/link safety, directory identity and exact accepted or
in-flight bytes. Symlinks, nonregular files, hardlinks, permission failures and
missing files retain their separate refusals.

`companions` in a live snapshot contains `source_manifest_sha256`, `saving` and
`formats` keyed by `mat`, `csv` and `chart_selected_csv`. Each format reports its
path, state, source digest, `payload_sha256`, `current`, `refresh_pending`, reason,
retryability and observed publication synchronization facts. States distinguish `absent`,
`preparing`, `publishing`, `current`, `stale`, `failed` and `unavailable`.
Under client contract 44, `refresh_pending` is an authoritative boolean for a
previously current output now stale while the acquisition/terminal drainer or
canonical writer owns its deferred refresh, or a queued capture owns the current accepted
manifest. The file stays stale, with its original source digest and retryability;
this witness does not claim a worker is running or publication has succeeded.
It is false for failed or unavailable formats, retained failed prepared payloads,
abandoned/closed owners and unconfirmed cleanup. Presentation can describe those
specific stale outputs as awaiting automatic refresh without hiding independent
failures. Close/discard continues to disclose the unresolved files and validate
the current token.
No-chart selected CSV is unavailable with an explicit applicability reason.
Missing SciPy prevents MAT while CSV and canonical records remain independently
available. Installed application packaging must include its MAT dependencies.

A failed prepared output remains in Python with its exact bytes and source
identity. A later source revision does not replace that unresolved payload.
`retry_companions` publishes the retained bytes first, including MAT's original
dated header, then separately refreshes a newer accepted source. Another folder
can publish those same retained bytes in the new owned destination. Current
successful files with the same authenticated source and payload digest are
reused. Companion failures do not invalidate saved canonical readings or
suppress other formats. Ordinary serialization failures remain retryable even
when no output bytes were prepared; missing dependencies remain unavailable.
Finish and explicit retry report failure if required automatic outputs remain
unresolved. Close requires the current discard token for unresolved outputs;
its summary names their format, path, source, reason and state. Prepared outputs
also name their payload hash and byte count; unprepared outputs have no invented
payload identity. Failed TI3 attempts remain separately disclosed; a first refresh
still in progress is labelled pending, while previous failure reasons remain
failures. These states
belong to the live owner lifetime;
saved review does not infer writer ownership from a companion's existence.

MAT captures only declared canonical operations, recovery originals, native
diagnostics and authenticated inherited chart closure. Generated companion
files and export receipts are excluded, preventing recursive MAT inclusion.
CSV preserves native types, quantities and full annotations; chart-selected CSV
includes selected original reading references for every pass, in pass and
canonical cell order, with an explicit `pass_id` column. TI3 quantity
eligibility and target-channel limitations remain at the existing export owner.
Canonical records remain authoritative. `can_measure`, `can_calibrate`,
`can_finish`, `can_edit_annotations`, `can_apply_annotations` and
`can_start_new_batch` expose current owner eligibility; controls need not
duplicate these guards. `can_start_new_batch` is the same rule `new_batch` enforces:
a durable Finish with no active, unsaved or unresolved companion work.

`cleanup_uncertain` is derived from unconfirmed software stop. Invalid terminal
data has its own retained recovery reason; invalid data alone does not imply that
a worker is active. A terminal event confirming software stop releases the
operation ownership even if its result is missing or invalid, preserving prior
good readings and allowing saving and Finish. Without that confirmation,
acquisition stays blocked. Invalid raw terminal JSON is retained as application recovery
evidence at `recovery-<command UUID>.json`, with exact source text, frozen intent,
validation reason and available terminal cleanup facts. It is never admitted as
a measurement. Retry or Another folder can preserve this evidence after the
worker is terminal. Ordinary Close still requires confirmed software stop.

An explicit `abandon` requires the current contents token and reason, and refuses
an active/nonterminal worker. It records `abandoned` and a shutdown outcome,
including cleanup confirmation separately from reported instrument state.
Publication failure is reported as `shutdown_saved: false`, so a volatile exit
is never described as durably recorded. It releases only the batch publication
lock. It retains the operation/frontend and their acquisition ownership; it does
not claim a device stop, release an instrument lease, kill a worker or send a
hardware command. Unconfirmed cleanup immediately blocks both new owners and already-created
owners from starting acquisition in that process, including before abandonment.
Explicit unconfirmed abandonment requires process shutdown. A retired batch cannot create New batch. Confirmed invalid-result
abandonment does not create that global uncertainty gate.
If a control interruption occurs before the abandoned manifest is authenticated
on disk, both prior status and prior shutdown data are restored. The owner keeps
its batch lock and saving reason; a new explicit confirmation is required for
discarding unresolved state. If abandoned bytes were authenticated before the
interruption (including a matching real publication receipt followed by a failed
best-effort readback), abandonment and their synchronization receipt remain, and the
existing owner-retirement bookkeeping completes before the same control propagates.

## Sample labels, automatic next and Repeat

The default target is `Sample #01`. A successful retained normal spot prepares a
fresh draft UUID/version 0 and `Sample #02`, clearing notes and preserving
options. Cancelled, failed and calibration outcomes preserve the exact prepared
target, including failures with valid retained partial readings. A retained
successful spot consumes its target even if saving fails; acquisition remains
blocked until recovery, and Retry never advances it again. Exact-name mode
preserves the label while still creating a fresh next sample UUID and clearing
notes. Labels are editable descriptions, not observation identity or evidence
of physical placement.

With numbering enabled, a bare stem such as `White` resolves to `White 01`
before acquisition. `White 03` selects that ordinal and padding, followed by
`White 04`. `White 003` retains three digits; `White #09` retains the separated
hash marker. `Yellow` starts its own series, and returning to `White` resumes
above the highest successful normal numbered White intent in this batch. An
explicit unused ordinal is honored even below that high-water mark. An explicit
already-consumed ordinal advances to the next unused ordinal at or above that
number. For example, after acquiring only `White 03`, `White 01` remains available,
while bare `White` resumes at `White 04`.
Case and hash-marker spelling distinguish series. Empty numbered input selects
the default Sample # series. Embedded digits are not interpreted as ordinals:
`D65` becomes `D65 01` in numbered mode, while exact-name mode preserves `D65`.
Exact numbered-looking labels, Repeat and later annotation edits do not consume
or rewind numbered series. New batch resets them. The owner derives these facts
from frozen successful intents; there is no separate counter registry.

Deleting the digits from a spaced hash series preserves that series: `Sample #`
resumes `Sample #02` after a successful `Sample #01`; `White #` also preserves its
acquired padding. Only a whitespace-separated decimal suffix is recognized.
`White-03`, `White_03` and `White#03` are bare stems in numbered mode; exact-name
mode preserves each label without adding or interpreting a suffix.

`preview_draft` is a read-only application/desktop query with `batch_id`,
`generation`, `draft_id`, `version`, input `name` and `number_samples`. It echoes
all six fields and adds `resolved_name`, using the same resolver as `edit_draft`.
Consumers discard stale bindings, draft identities/versions and local inputs,
and display the complete resolved target before Measure. Version alone cannot
fence a fresh draft whose version is again zero. The replaced `new_draft` command
is absent; the automatic next target remains editable.
The shared preview/edit resolver validates both the resolved target and its
immediate following label. If ordinal growth would exceed the label limit, it
refuses before frontend preparation or draft mutation and asks the operator to
shorten the stem or disable numbering. Measure retains the same guard before
dispatch.
After a valid reading reaches the boundary where its fresh draft would have an
unrepresentable successor, the owner saves the reading and keeps the last valid
series marker in a fresh draft with blank notes. The acquired ordinal stays
consumed. Resolving the next target skips that ordinal and refuses its impossible
successor before preparation; the operator must shorten the stem or disable
numbering. The owner does not truncate labels or change the numbering mode.

Snapshots contain nullable `repeat_source`, the latest completed retained spot's
`operation_id`, `observation_id` and frozen acquired `name`. Repeat also fences the
current prepared draft/context, authenticates both source identities, uses fresh
operation/observation IDs and records `repeat_of` provenance. It clears new notes
and preserves the next draft exactly. Applied reading annotations never replace
the source's acquired label/options.
The optional TI3 projection adds the original operation/observation pair and the
authenticated manifest operation's exact `repeat_of` value to each row's existing
`context.provenance`. These annotations do not change native observations,
material context or optical grouping.

An explicit `retry_save` or `another_folder` retries failed native diagnostic
retention using the same held terminal operation and its original collector.
The application retains failed diagnostic handles even after confirmed-stop
recovery clears the active handle. It validates the original operation identity,
metadata and bytes before clearing that command's retention failure. No new
worker, acquisition or collector is created. A permanent auxiliary failure remains
visible while canonical reading saving and rescue continue. Snapshot
`native_diagnostic_failures` separates `retention` and `publication` maps, keyed
by original command UUID with failure reasons. These warnings do not become
canonical save errors or block further measurements. Publication and destination
rollback continue to use the existing authenticated save owner.

## Persisted quantitative comparison context

Manifest schema 5 stores `comparison`: `version`, nullable `reference`, nullable
`white` and its validated `white_descriptor`. Reference uses the exact operation
and observation pair. A retained white uses `kind: retained-reading` and that
pair. A theoretical Lab reference retains the existing explicit declaration's
name, observer, reference Y, unit and scale. The owner reuses `resolve_white` and
stores its full resolved assumptions; the [scientific methods](science.md)
describe the existing Lab and white-reference owners. It introduces no new transform
and never selects a first-reading reference or infers white from calibration.

Snapshots add `comparison.saved`, based on the exact comparison object and version
in observed manifest acceptance. A failed
metadata publication keeps the chosen context and version in memory with
`saved: false`; ordinary Retry or Another folder preserves that choice without
acquisition. Discard confirmation includes its exact unresolved contents.
Quantitative queries refuse unsaved context and stale `comparison_version`.
They authenticate the current manifest and chosen source files against the
owner's exact accepted bytes, then verify the descriptor against the existing
resolver. Historical snapshot saved flags do not certify external edits.
An unrelated metadata save preserves available comparison context before and
after its atomic replacement. The reader accepts the exact current writer's
bytes only when their comparison, batch identity, acquisition context, descriptor, mode
and operation references match the saved context. Native source bytes are still
authenticated. Rescue rollback restores the saved comparison object with its
version before releasing the attempted destination's lock.
Read-only saved review reports the stored context plus `saved`, `available` and
`reason`; missing/changed canonical sources or mismatched assumptions make the
comparison unavailable. Annotation edits preserve the chosen identities and
assumptions. New batch starts with no reference or white declaration.

Desktop `projection` takes `scale`, `dataset_revision` and `comparison_version`;
`analysis` adds `selected`. They read the saved owner context and echo its version,
exact `white`, owner-produced `white_descriptor`, resolved dataset reference
identity or null, and data revision. There are no client white/reference
overrides. Without a saved reference, comparison values remain unavailable.
Camera/layout membership and scale are separate presentation choices; `set_view`
does not retain quantitative reference/white duplicates.

Contract 38 retains `analysis.xyz_comparison_by_source`, keyed by each selected
opaque operation/observation pair identity. Each derived result contains nullable `y_percent` and
`uv_distance`, independent nullable `y_reason` and `uv_reason`, `y_unit: "%"`,
`uv_unit: "1"`, `algorithm: "xyz-native-reference-comparison"` and
`algorithm_version: "1"`. The existing `comparison_by_source` remains spectral;
`delta_e_by_source` remains the explicit-white Lab comparison. A saved reference
need not be among the selected/plotted readings.

The shared [native XYZ comparison](science.md#native-y-change-and-cie-1976-coordinate-distance)
requires matching native unit, scale, observer and illuminant. It computes signed
sample-minus-reference Y change in percent and CIE 1976 u′v′ coordinate distance.
These metrics do not require a Lab white and do not use the RGB preview scale.
Y denotes luminance only for the declared absolute cd/m² context. No geometry,
calibration equivalence, physical uncertainty or pass/fail threshold is inferred.
A black sample can have `y_percent: -100` while its coordinate distance is
unavailable. Missing/domain/overflow results retain reasons instead of zeros.

Summary adds `y_percent_range` and `uv_distance_range`, each null or the existing
`min`, `max`, `count`, `unit` shape, plus `y_unavailable` and `uv_unavailable`
counts. Summaries exclude the reference reading and preserve each metric's
availability separately. Request fields and saved-context authentication remain
the same; the response is consumed only by the current application contract.

## Canonical publication and recovery

The application exclusively creates the directory and holds a persistent
`.batch.lock` kernel lock for its mutable lifetime. It never unlinks the lock
inode. The TI3 writer uses the separate `.ti3-export.lock`, so it can run while
batch ownership is held. These locks exclude cooperating writers. They do not
exclude unrelated editors or establish filesystem compare-and-swap guarantees.

The shared publication owner closes each authenticated-read, directory-sync and
lock descriptor once. If a first failure and descriptor close both fail, the
first control interruption and any real publication receipt survive; later
failures remain diagnostics. An unconfirmed exclusive publication-lock close
retains that lock object for the rest of the process and never retries the
ambiguous descriptor, because the OS may have closed and reused its number. The
ambiguity belongs to that one kernel lock, so only its destination is retired:
that folder accepts no further publication or lock from this process, and the
owner holding it refuses Measure, Finish, Retry and Close. Its retained work is
not lost: Another folder rescues it to a fresh destination with a fresh lock,
after which the owner continues there. Other owners and other folders are
unaffected. Snapshots expose the retirement reason and disable the corresponding
eligibility flags. A retired owner can still read retained data and select a
reading; it never holds an active operation or a pending instruction, because
every site that releases its own lock requires an idle or terminal owner. These
publication facts do not change software-stop or instrument-state observations.
Ordinary read/sync descriptor failures remain diagnostics and do not retire a
destination, and a destination that cannot be identified (its path is not a
directory, or cannot be read) is not reported as retired: the publication into
it reports its own failure.

When the operator explicitly chooses to discard volatile work and exit this
process, `confirm_process_exit` accepts the current `discard_summary()` token
without writing, authenticating a folder or releasing a descriptor. It requires
this owner's cached directory identity to match a retained retired batch or TI3
lock object whose close failed. Retirements in other folders neither authorize
the acknowledgment nor invalidate its token. Starting, active or nonterminal
operations and pending metadata or companion publication refuse this acknowledgment.
Software stop must be confirmed with no uncertain acquisition owner, or this
owner must already be explicitly abandoned with no other uncertain owner. The
second case preserves unconfirmed stop and instrument-state warnings; it does
not confirm acquisition cleanup. Changes to the exact
discard contents, stop facts or retired objects invalidate the token. Snapshots
expose `process_exit_ready` and `process_exit_acknowledged`. The command reports
the distinct `process_exit_acknowledged` effect; it does not mark the owner closed,
clear retirement or claim cleanup. The native host owns actual process exit.

If rescue publishes successfully but releasing the earlier destination lock
fails, the new canonical destination remains accepted. The command reports an
admitted failure and retains both the new lock and the uncertain earlier lock;
it does not roll back the successful publication or start another acquisition.
The graphical host terminates only after its owner-close command reports both
admission and completion. Failed or pending admission alone cannot confirm exit.

The current [`batch.json` manifest](data-format.md)
contains identity/context, frozen batch name and notes, exact descriptor,
draft/version, annotations and reserved operation references. Each acquisition
command calls `frontend.prepare()` once, retains that handle and its normalized
`request_json` plus operation UUID, and reserves `operation-<command UUID>.json`
**before dispatch**. Preparation has no worker or device I/O. Failed intent
publication refuses acquisition with a destination reason, cancels and drains
the held prepared operation, and retains its zero-reading outcome for save
recovery. Failure or interruption during intent publication, including after atomic
replacement, authenticates any published bytes and cancels/drains the same
never-dispatched prepared handle before a control signal propagates. The same
recovery span includes the admission transition after durable intent and the
drainer startup handoff, so a signal between those steps cannot strand Starting.
Same-folder
Retry still uses the known bytes. Drainer startup retains a callback claim guard
and thread identity. A failed startup revokes only an unclaimed callback, so a
late bootstrap cannot dispatch or become a second drainer. That revocation
allows synchronous retention of the prepared cancellation. If the callback
already claimed ownership, it stays the sole drainer and resolves its actual
terminal facts, including when it finishes before the startup call raises.
Thread termination alone is not used to infer that it never launched. Control
signals propagate after cancellation/ownership bookkeeping; a claimed worker's
software stop remains unconfirmed until its own terminal facts arrive. The first
control interruption survives later cancellation, event-draining and publication
failures. Later failures remain diagnostics rather than replacing that signal.
A dispatch exception retains the handle, requests cancellation and drains its
actual terminal facts; an exception does not prove software stop. If cancellation
itself fails before completing an unclaimed handle, the application retains that
same handle with unknown cleanup instead of waiting indefinitely or launching a
second drainer. If cancellation completes before raising, the sole drainer still
retains the actual completed outcome.
Best-effort manifest authentication also preserves the first control and the
real publication receipt. Its own failure remains a diagnostic; it cannot skip
metadata failure bookkeeping or rescue-folder owner rollback. Unauthenticated
bytes are never adopted as accepted manifest bytes.
An already-terminal prepared handle skips dispatch. Its intent remains
`reserved` until terminal retention classifies it; the application does not
publish `started` or an active command for that skipped attempt. `started`
records a frontend dispatch attempt, not physical acquisition or confirmed stop.
Publication order is:

1. Publish reserved intent containing the prepared operation UUID and request.
2. Dispatch that held operation once.
3. Publish exact immutable operation JSON using
   [`operation_exports.publish`](data-format.md#operation-files-json-and-mat).
4. Accept its identity, context and exact-byte SHA-256 in the manifest.
5. Attempt grouped TI3 companions and save their complete separate receipt.

Save retries authenticate existing canonical and recovery bytes before using
remembered publication facts. Missing recovery files are published from retained
original bytes; changed files are refused without overwrite. A normally completed
publication attempt, including an explicitly unsupported directory-sync result,
does not repeat full file synchronization for unchanged canonical or recovery
files. A failed publication receipt remains eligible for retry. Visible durability
facts retain their actual limitations; this bookkeeping makes no stronger
storage or power-loss claim.

If a canonical file was published but manifest acceptance failed, the result
stays accepted in memory and unsaved. Both Measure and Calibrate are blocked.
Retry compares the existing reserved file with the exact retained bytes and
completes only the missing publication. A different file is a conflict and is
never overwritten. If atomic manifest publication succeeded before directory
synchronization failed, the owner authenticates and remembers those exact
published bytes. It preserves the synchronization uncertainty and unsaved flags,
so a subsequent edit and Retry can use the same folder without a false ownership
conflict. No retry or destination change acquires another reading.
Another folder preserves prior files/manifests and destination history; failure
keeps the earlier ownership until a new canonical publication succeeds. If an
earlier destination is unreadable or was removed, rescue uses the exact retained
bytes authenticated against remembered accepted digests/context. The new manifest
records that earlier destination's uncertainty in `destination_issues`. A readable
changed source is recorded as a destination conflict, and its authenticated
original in-memory copy is rescued without altering the changed old file. A
failed rescue reserves the directory inode and known publication bytes, allowing
the same owner to retry that exact folder without unlinking its persistent lock
or overwriting unrelated files. Finished export retry still requires current
disk authentication and refuses changed inputs.

Names and multiline notes are versioned overlays keyed by **operation and
observation identity**. They never modify canonical native observations.
`discard_summary()` names unsaved operation IDs, reading counts, draft values and
annotation versions. It returns independent copies containing full exact metadata;
its token changes with those contents. An abandonment snapshot previews any
per-reading notes inside its shutdown summary, while the canonical shutdown and
`discard_summary()` retain full evidence. Close refuses a
stale token, preserves published files and never marks unaccepted work accepted
or unfinished work finished. Active work must first be cancelled and drained.

## Optional batch TI3 exports

The existing `retained_snapshot` and `update_json` owners project only
manifest-accepted observations. The batch UUID is the writer owner; the actual
manifest path is the source identity; the SHA-256 of its exact saved bytes binds
the revision and referenced record digests. Saved names/notes are applied to
projected rows by both identities. The native observations stay unchanged.

Compatible spots aggregate into one writer group. Scientifically different
reported contexts can require several TI3 files. Current Demo
spots with the same material context aggregate, including fresh and retained
calibration explanations; descriptive explanation/performed flags alone are not
scientific grouping boundaries. The application
implements no second grouping rule. Empty or calibration-only batches invent
no measurement rows. Duplicate observation IDs across operations cause an
explicit optional-export conflict, without relabeling or dropping canonical work.

The complete writer result is stored in `ti3-result.json`, outside the hashed
source manifest. Retired/current/stale files and unavailable components remain in
that receipt; retries use the complete prior result. Receipt-save failure keeps
the full result in memory and reports export status `unverified`, while canonical
work stays saved. Optional export failure does not block Finish or New batch.
Annotation/manifest revisions require a refreshed export. Finished retries lock
and authenticate the immutable manifest/records before asking the same writer
to authenticate and update its files. Changed canonical inputs are refused.

## Read-only review and limits

`review_saved(folder)` reads the manifest and only its reserved references,
without scanning unrelated files, writing, locking or starting an operation.
It rejects path traversal, linked record files, a linked batch directory,
mismatched record bytes/UUID/device identity/frozen request, and annotation
identity inconsistencies. Paths use their resolved canonical spelling; writable
publication also checks the owned directory inode before writing. An accepted reference with missing or malformed data
is `unknown`. A valid file at an unaccepted reserved reference is
`found_unaccepted`, and is never admitted automatically. The original terminal
status/calibration and non-sample outcomes remain available for inspection.

Completed and unfinished batches can be reviewed. Review does not resume
acquisition or replay commands. A mutable batch can change during unlocked
review; uncertainty stays explicit. Receipt schema/source ownership is validated,
but optional file state is `unverified` until an explicit writer retry authenticates
it. Historical `saved` state in a live owner's snapshot records publication; it
does not certify files against later unrelated external edits.

At most one operation result can be awaiting canonical saving in this route,
plus unsaved annotations/draft edits. Python process loss can lose those volatile
contents. Crash readback distinguishes reserved/started unknown work from a
found/unaccepted record; it does not infer a reading or insert zeros. Forced
termination can bypass Close. The shared publisher synchronizes file contents,
attempts macOS `F_FULLFSYNC`, and synchronizes POSIX directory entries after
publication and temporary cleanup. If full synchronization is unavailable or
returns an unsupported-operation error (`EINVAL`, `ENOTSUP`, `ENOSYS` or `ENOTTY`),
ordinary `fsync` remains in effect and a warning reports that device-cache
synchronization is unconfirmed. Other errors, including `EIO`, remain publication
failures.
A cleanup failure after publication remains a warning; it cannot turn existing
canonical bytes into an absent save. A directory-sync failure explicitly reports
that bytes were published while synchronization remains unconfirmed. Windows
has no standard-library directory-sync equivalent in this implementation. These
operations improve publication persistence but do not prove OS/power-loss
durability, and no such durability or physical equipment qualification is claimed.
There is no graphical host or accessibility qualification in this headless API.

The focused tests in `tests/test_application.py` exercise the real public Demo
frontend under a native-transport guard, command concurrency/rebind/Starting
cancellation, exact instructions, canonical/manifest faults, save/folder recovery,
crash classification, annotation preservation, locking, optional receipt failure,
full retired receipt reuse, invalid/missing terminal recovery with separate
confirmed-stop and uncertain-stop cases, same-folder post-publication sync
recovery, prepare/dispatch/drainer faults, explicit abandonment,
annotation draft/rebind/status, exact read-only metadata queries and bounded note
previews, and lifecycle. A 200-reading real Demo case checks
canonical new-record publication, authenticated companion refresh, and records bounded
local timings and compact snapshot size. It supplies software evidence, not host
responsiveness or physical validation.

The shared publication helpers in `exports` are deliberate package-internal
contracts used by application and TI3 owners, with common fault/locking tests.
They are not host callbacks or a second persistence implementation. Complete
TI3 receipt readback uses the public `validate_result_json` owner seam. Request
preparation uses the public `InstrumentFrontend.prepare` seam and the held
operation's read-only `request_json`. Dispatch checks current capability identity
and takes acquisition ownership at the existing frontend. The application never
reproduces device policy, request normalization or the dispatch state machine.

## Publication and callback failure boundaries

Every retained intent has the prepared operation UUID, including reserved and
unknown outcomes. A nullable identity belongs only to non-acquisition or refused
command outcomes; it is never an accepted intent format.

Unexpected callback exceptions are retained with their type and message.
The callback guard only claims or revokes launch ownership. The drainer and
metadata callbacks classify their own failures; the guard does not run a second
failure handler. Control interruptions propagate after ownership and available
recovery facts are recorded. Terminal
retention uses actual drained stop facts; an early callback failure retains an
explicit unknown outcome and operation ownership, requests cancellation of the
same handle, and keeps cleanup unconfirmed. It never infers stop from thread
termination or creates a replacement drainer. Prior valid readings survive.
An applied annotation whose publisher cannot start remains unsaved, with failed
admitted publication rather than a refused edit.

Finish follows authenticated publication. If finished bytes reached disk before
a sync exception or control signal, status remains finished and the command
retains its failed admitted publication and synchronization reason. New batch
stays blocked while that reason is unresolved. Retry authenticates and
synchronizes the same bytes, then refreshes optional exports without reopening
the batch, rewriting finished metadata or repeating acquisition. Before any
publication, a failed Finish can remain open.

Snapshots expose `durability.status`, `sha256`, `file_sync`,
`directory_synced` and human-readable limitations for canonical publications
observed by this live owner: `batch.json`, `operation-<command_id>.json` and
`recovery-<command_id>.json`, including known publications at earlier or attempted
destinations. This list excludes generated MAT/CSV, grouped TI3 companions and export receipts.
`durability.status` summarizes only receipts in the current `folder`, using
their immediate parent directory. A failed rescue keeps its actual attempted
publication receipts in `durability.files`; after rollback those receipts cannot
mark the restored current destination limited. Successful rescue switches the
status scope to the new destination while retaining earlier limitations as history.
Their separate export result reports projection and publication state, not the
filesystem synchronization observations in `durability.files`.
The common publication owner
reports actual calls and narrows only unsupported full-file/directory sync; I/O
and permission failures remain failures. `EOPNOTSUPP` and `ENOTSUP` are both
recognized. Reports never carry stronger synchronization across changed bytes.
An identical-file retry synchronizes the authenticated file and directory
without rewriting it. Windows directory synchronization remains unqualified.
The headless module supplies the durable owner consumed by the
[offline native host](desktop.md). Its passive browser fixture preview performs
no native saving and makes no claim to show actual saving durability.

Frozen batch name and notes are validated before directory creation and persisted
in manifest schema 5. Constructor and `new_batch` require explicit `frontend`,
`mode` and `options`, and accept `expected_descriptor`, `name` and `notes`. Default
headless values are Untitled batch and an empty note. New batch can select another
frontend or mode only after the old owner's durable Finish checks pass. It creates a new batch and
context identity rather than changing the old batch's frozen binding. Snapshots
preserve both fields. Unsupported schemas are refused; no earlier manifest
schema is migrated or resumed.


## Generic operation and controlled presentation snapshots

`workflows` contains the current frozen mode's descriptor offers and bounds.
`can_run_workflow` reports common idle/publication/ownership admission state;
the chosen workflow still undergoes exact descriptor and request validation.
`can_measure` additionally requires the spot offer. Non-spot results name rows
with their actual requested/returned patch IDs; canonical records retain the
original IDs and quantities. Ordinary sample numbering and Repeat stay separate.

`instructions` retains the current operator step. `presentation` independently
retains the controlled drawing request, declared black/white RGB, stimulus/display
identities, delay and deadline plus original native protocol facts. The host must
perform the actual rendering before submitting a `PresentationStamp` with
`source=client_draw_completed`, a named client clock and its draw-completion time.
An operator Continue cannot supply this evidence. A software test stamp is not
physical drawing, latency or optical qualification.

Successful completion and both unknown-terminal/drainer-failure classifiers clear
the expired presentation request. Uncertainty and raw failure/recovery facts stay
at their existing owners; clearing a drawing prompt does not manufacture a
successful measurement or stronger cleanup state. Saved review authenticates the
same descriptor, request, operation and typed result without contacting a device.


An advanced mode/configuration query can change the frontend's capability identity.
`context_current` and `context_reason` compare its cached descriptor with the
owner's immutable input descriptor, without native enumeration or initialization.
A changed/unavailable context disables new operations and refuses admission before
preparation. Finish, inspection, saving and recovery remain available. Current
sample text can still be saved with exactly unchanged acquisition options; new
options require a new context. JSON-semantic comparison distinguishes a boolean
from a numeric option. Create a new batch from the newly shown descriptor; retained
operations are never rewritten to belong to the new capability identity.

## Guided charts through the shared batch owner

Schema 5 adds nullable `chart`. An ordinary batch keeps `chart: null`.
Native importers and headless callers first call
`stage_chart_definition(definition, *, batch_id, generation, context_id)` in a
new empty batch. Definitions come from the [chart state owner](chart-state.md).
The owner validates the complete definition outside its mutex and checks the
exact batch, integer generation and current context both before and after
validation. It retains an independent serialized definition and one token.
Staging another definition replaces that token; `bind()` revokes it. Staging
neither prepares an instrument operation nor publishes a chart.

The returned packet contains those three binding fields, `definition_token`,
`definition_id`, `mapping_confirmation`, `source` and `preview`. The bounded
preview contains only `kind`, `name`, `rows`, `columns`, `traversal`, `strips_as`
and structural `cells`. TI2 source identity contains its filename, SHA-256 and
original byte count; preset and custom definitions return `source: null`.
The packet excludes target JSON and original bytes. TI2 retains its existing
1 MiB source and 140-cell limits at the target owner; staging does not enlarge
the 131,072-byte command envelope.

`configure_chart` accepts exactly `{definition_token, mapping_confirmation}`.
For TI2, confirmation must exactly match the staged `{definition_id, strips_as,
rows, columns}` packet, including the chosen row/column orientation and JSON
value types. Preset and custom definitions require `null`. Configuration
rechecks the staging binding, consumes the token and installs the retained
definition through the existing chart owner. The host imports complete TI2
definitions locally through staging and sends only the token and explicit
orientation acknowledgement through the command bridge. Replaying the same
command UUID still returns its recorded outcome after token consumption or a
new binding.

`measure_chart` accepts the
ordinary `context_id`, `draft_id` and `draft_version`, plus `chart_revision`.
It freezes the actual prepared request, operation UUID and intended canonical
cell before writing the durable intent. It uses the existing frontend dispatch,
terminal draining, native records, annotations and canonical save. Ordinary
Measure, Repeat and generic reads are refused while a batch owns a chart.
Calibration remains the existing descriptor-authorized action.

`chart_edit` accepts exactly `{chart_revision, action, operands}`. Actions are
`select_cell`/`inspect_cell` with `{cell}`, `rotate` with `{degrees}`,
`set_paused` with `{paused}`, `select_attempt` with `{pass_id, cell, attempt_id}`,
`resolve_position` with `{token, action}`, and `accept_context`/`start_pass` with
`{}`. The owner creates fresh pass UUIDs. Acquisition and inspection cells remain
separate; rotation during acquisition cannot change its frozen target. Failed or
cancelled retries preserve the earlier selected success and every native outcome.
Options changing material context require the helper's explicit pass decision.
`start_pass` starts an empty comparison pass while preserving previous history.

`chart_reference` accepts `{chart_revision, source_pass_id, qualification,
criteria}`. It uses selected original readings from the named source pass and
[shared chart science](chart-science.md) to freeze a reference in the current active
pass before any attempts.
The pass must have no frozen reference or pending material-context decision; use
`chart_edit` with `start_pass` explicitly when another comparison pass is needed.
It never accepts editable XYZ as a measured reference. Reference and
position packets retain declared limits and source identities; they do not prove
physical patch placement. Saved review reconstructs native context and scientific
checks from exact original records. Rehashing a forged chart packet cannot make
it agree with those originals.

`finish_chart` accepts `{chart_revision, allow_incomplete}`. Incomplete coverage
requires explicit `true`; pending placement and a live attempt still refuse.
Ordinary Finish refuses chart batches. Polled `snapshot().chart` is a compact
geometry/coverage/selection/decision summary. `chart_history(pass_id, cell, *,
offset=0, limit=50)` pages identities for a canonical cell numbered 1 through N;
the limit is at most 50. `chart_details(pass_id, attempt_id)` returns
one exact attempt. `chart_decision_details()` returns contexts and reference
packets explicitly. Raw requests, target bytes and prior manifests do not appear
in every snapshot, including abandonment summaries.
`chart_readings(pass_id=None)` explicitly returns original selected-reading
wrappers for the requested pass, or the active pass when omitted. Each wrapper
retains canonical cell, native observation, exact reading/source identity and
accepted context for scientific consumers. Saved review also checks every
selected original against the pass's material facts; newer known facts cannot
retroactively certify an earlier unknown measurement.

`continue_chart` accepts `{folder, source_sha256, action, source_pass_id,
acknowledged_material}` in a new empty owner. `action` is `continue` or
`new-pass`; a new pass accepts an empty acknowledgement list.
`folder` names the original saved-source directory. The fresh owner's constructor
has already selected its separate new destination. `source_sha256` fences the
exact original `folder/batch.json` bytes; it is not a destination hash. Source
review and the [portable source graph](chart-sources.md) authenticate exact
manifest, native operation and raw recovery bytes before admission. The current
owner retains each original once under its SHA-256 and publishes originals before
the new manifest. Future review and Another folder rescue use destination-owned
copies; the original directory need not remain available. Inherited reading,
operation, segment, failed/stopped and selected-attempt identities are preserved.

Continue uses the current bound descriptor's observed identity, not identity
copied from the source pass. Current description/binding metadata and the original
selected readings must agree. Unknown required calibration or other scientific
facts remain a refusal; an operator declaration cannot certify an earlier unknown
reading. The application keeps that reason and offers **New pass** for independent
measurements. New pass retains the original source readings separately without
claiming continuity or rewriting their calibration facts.

Compatible continuation requires established original and current facts.
[Context declarations](chart-context.md) are explicit operator statements, not
native readback; unknown identity or earlier unknown selected-reading material
cannot be certified retroactively. Missing required current identity requires a
new empty pass. Continuation never reopens the earlier writable owner.

Explicit TI3 refresh projects current acquisitions and selected readings for each
chart pass through the existing TI3 owner. It adds authenticated inherited records
outside `ordinary_record_ids`, preserving their original source identities.
Imported targets retain their source sample/location pairs and exact TI2 packet;
optional export failure leaves all canonical records intact. This application API
and its Demo proofs do not establish connected graphical controls, physical
instrument operation or optical accuracy.

## Native diagnostic originals

`snapshot().native_diagnostics` reports the current separate `debug_level` and
`limit_bytes`. Defaults are level 0 and 65,536 bytes; levels are integers 0..8
and caps are integers 1..1,048,576. Booleans are not integers in this contract.
Positive levels require the descriptor's `limits.native_diagnostics.available`.
Admission passes only the destination-owned `diagnostic-<command UUID>.log`
target and these controls to the shared frontend. Frozen acquisition requests,
draft options, capability identity and chart material context do not gain a
selected logging level.

Each operation intent retains nullable `native_diagnostics` capture metadata.
The held operation supplies its closed bounded original segments separately from
result parsing, including a cancelled or invalid terminal result. The shared
collector retains the stream within its cap, or the first floor(cap/2) and last
ceil(cap/2) bytes when truncated. Schema-2 metadata declares each exact stream
offset and length. The artifact concatenates those segments without inserting
gap text; even a one-byte cap retains the final byte. The collector keeps that
artifact in memory even if its initial file write, synchronization or
close fails. The application publishes the original through its existing save
and Another folder owner. An initial partial file is replaced only against its
reported exact artifact-prefix digest. Capture status, source ranges, initial file facts, original error,
truncation and `worker_started` remain unchanged by later canonical publication.
An empty transcript does not establish that a worker or instrument ran.
`sink_stopped` separately reports optional file-writer closure. Native process
stop does not imply that this auxiliary writer ended. An unconfirmed sink cannot
be overwritten at its original owned target; Another folder may retain the
closed memory original at a different destination. Capture uncertainty stays
visible after that rescue, and the native owner retains the original writer.

`operation_details()` and saved review expose `native_diagnostic_review`
separately from the optical operation's acceptance. A missing or changed log
does not turn valid observations into missing or zero readings. A publication
failure retains the original bytes for Retry/Another folder while canonical
saving continues. Current discard confirmation is required before Close when
diagnostic settings, bytes or failure reasons remain unresolved. The summary names unresolved
captures by command UUID, byte count and digest, without sending raw binary text
in polling replies. Chart continuation retains authenticated diagnostic originals
in its declared source closure, including truthful incomplete and empty captures.
