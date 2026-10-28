# Frontend operations and retained results

Use `InstrumentFrontend` to drive an instrument from a Python client or a host
such as MATLAB. The [generic frontend guide](generic-frontend.md) defines the
current descriptor, request fields, workflow layouts and instrument instructions.
This page explains how to poll an operation, retain its outcome and save it.

For client development without hardware, use the
[equipment Demo API](equipment-demo.md). Its `InstrumentFrontend` subclasses
use the same request, polling and result APIs with simulated session I/O.
They do not enumerate instruments or launch a native worker. Demo values and
calibration examples do not establish physical support or numerical accuracy.

## Catalog and capability snapshots

`color_measurement.demo.demo_catalog_json()` returns the synthetic equipment
picker as a JSON list. `demo_frontends()` returns reusable frontend objects.
Select by `device_id` and obtain the selected object's descriptor with
`describe_json()`. For a physical instrument, follow the
[selection and binding workflow](generic-frontend.md#selection-and-acquisition).

The descriptor supplies modes, workflow availability, patch bounds, layout fields,
options and instruction metadata. Use those fields to build the client controls;
show unavailable reasons and refuse a workflow whose required fields the client
cannot represent. Hardware confirmation can still refuse an advertised route.

Copy the current `device_id` and `capability_id` into each request. The capability
ID detects changed capabilities or binding identity. Native settings reports and
pending recovery do not change it; equal IDs do not establish equal settings,
worker receipts or instrument readiness. See the [descriptor's identity
rules](generic-frontend.md#descriptor). Keep requested option values separate
from reported configuration and readback facts.

## Requests and asynchronous operations

Call `frontend.prepare(request_json)` with the
[generic request](generic-frontend.md#requests). It validates the descriptor
identity, selected workflow, patch IDs, options and layout without a thread,
ownership acquisition or device I/O. Retain the returned operation and its ID
before persisting dispatch intent, then call `operation.dispatch()` once.
Workflow limits and target geometry come from the selected
descriptor. A spot reading uses one patch ID and an empty layout; calibration
uses no patch IDs. Other workflows have their own declared bounds and fields.

| API | Behavior |
| --- | --- |
| `operation_id` | Python-assigned identity already available before dispatch |
| `dispatch()` | Starts this held operation once; startup failures remain drainable on it |
| `poll_event_json()` | Returns one queued JSON event or `""` without advancing acquisition |
| `respond(operation_id, request_id, response)` | Answers the exact pending operator instruction with an allowed response |
| `presented(operation_id, request_id, stamp_json)` | Supplies the typed draw-completion response for [display timing](generic-frontend.md#display-timing-request) |
| `cancel(operation_id)` | Requests cancellation; a completed result remains unchanged |
| `close()` | Requests cancellation; a prepared operation resolves without device I/O |
| `done` | Reports that finalization has finished, not that the event queue has been drained |
| `result_json()` | Returns the retained outcome after completion; raises `RuntimeError` before completion or if no result was published |

The event loop must keep the operation object alive through cancellation and
finalization. An empty poll means no event is available now, not that acquisition
has ended. Drain the queue, check `done`, and drain once more before reading the
result: completion can occur between the first drain and the check. In a GUI,
schedule subsequent polls instead of sleeping in a UI callback.

Ordinary failures before the worker's dispatch gate opens produce a failed
terminal result with confirmed no-dispatch cleanup. Interruptions can be
re-raised, but the already-held handle remains accessible. The result's
`configuration` retains `dispatch_failure_stage` for a failed no-dispatch
outcome and `stop_requested_by` for the actual host or client stop request.
These are host facts; `error_stage` and `error_native_code` remain null because
no driver reported a failure. Clients can distinguish a stale snapshot, a busy
instance and worker startup failure without parsing the diagnostic sentence.
See the [stage values](generic-frontend.md#results).
The recovery template preserves the already validated notification contract.
Ordinary finalization failure cannot erase that declaration or the known
no-dispatch stop facts; mutable live configuration is not promoted into recovery.
Once dispatch may have occurred, a startup exception is re-raised. A cause
recorded before terminal publication is retained in the pending failed outcome;
a published result stays
immutable, so dispatch can raise while the retained result is already completed.
Inspect that result and its stop facts even after an exception. Cancellation
retains ownership until the worker's normal cleanup
confirms termination. A lost dispatch response or exception is never permission
to repeat acquisition. Poll/drain the held operation and inspect its result's
`software_stopped`; an unconfirmed stop keeps Start blocked. Preparation failures
occur before dispatch and create no device I/O. There is no combined
`frontend.start` interface or legacy alias.

For `instruction_required`, show the instruction and use its `request_id`, the
operation ID and one of its `allowed_responses`. Wrong, stale, repeated or
disallowed responses are refused. `response_timeout_seconds` is the actual
operation-owned response budget, normally 120 seconds and configurable through
the generic frontend's current preparation API. The monotonic timeout starts
when the backend requests the instruction; this field is not a countdown or a
wall-clock deadline. A late response remains refused even before the waiter
processes its timeout. Display `instruction_notified` without replying
to it. Display-timing requests use `presented`, not an operator acknowledgment.
The [instruction reference](generic-frontend.md#operator-instructions) explains
calibration, positioning and device-trigger context.

A frontend permits one dispatched operation at a time. Preparation raises
`OperationBusyError` while the instance is owned, but does not reserve it:
multiple inert handles can be prepared while it is free. If another operation
takes ownership before a held handle is dispatched, that handle resolves with a
failed no-dispatch terminal. Repeating dispatch on the same started or resolved
handle raises `OperationBusyError`. This process-local ownership does not lock an
instrument against another application. Response waits and operation timeouts
are separate; see the generic guide for cancellation and retained worker-state details.

### Minimal synthetic reading

This example selects a simulated i1Pro3 spot workflow. It accepts its synthetic
instructions automatically; a physical client must obtain the operator's actual
decision. Run it in the [checkout environment](usage.md#install-from-the-checkout).

```python
import json
import time

from color_measurement.demo import demo_frontends
from color_measurement.operation_records import result_from_json

frontend = next(front for front in demo_frontends()
                if json.loads(front.describe_json())["device_id"] == "demo:i1pro3")
snapshot = json.loads(frontend.describe_json())
request = {
    "schema_version": 1,
    "device_id": snapshot["device_id"],
    "capability_id": snapshot["capability_id"],
    "mode": "reflective_spot_m1",
    "workflow": "spot",
    "patch_ids": ["sample-1"],
    "options": {},
    "layout": {},
    "event_contract": "retained_patch_v1",
}
operation = frontend.prepare(json.dumps(request))
operation_id = operation.operation_id

def drain():
    while text := operation.poll_event_json():
        event = json.loads(text)
        if event["kind"] == "instruction_required":
            prompt = event["payload"]
            operation.respond(event["operation_id"], prompt["request_id"], "accept")

try:
    operation.dispatch()
    while True:
        drain()
        if operation.done:
            drain()
            break
        time.sleep(0.005)
    result = result_from_json(operation.result_json())
finally:
    operation.close()
print(result.status, len(result.patches), result.provenance)
```

If dispatch or event handling raises, `finally` requests cancellation. Keep the
held operation and identity accessible, continue polling and draining it, and
inspect the retained stop facts before allowing another acquisition. `close()`
alone does not confirm termination; `software_stopped=false` keeps Start blocked.

The tracked [generic frontend example](../examples/generic_frontend.py) uses
the simulated i1Pro3 strip workflow and checks three completed patches:

```sh
uv run --locked python examples/generic_frontend.py
```

## Events and retained outcomes

Each event contains `schema_version`, `operation_id`, a zero-based `sequence`,
`kind` and `payload`. Track identity and sequence to detect mismatched or missing
notifications. Events are limited to 16,384 UTF-8 bytes; requests have the same
byte limit. Decode JSON before using its values. Unicode escapes and literal
Unicode are both valid, so character count is not a byte count.

The default `retained_patch_v1` profile emits `patch_reference` after retaining a
reading. Its `index`, `patch_id` and `observation_id` identify the full observation
in the terminal result. The alternative `patch_v1` embeds the observation in a
`patch` event where the descriptor permits it. Reference events allow full spectra
and raw context to exceed the per-event limit. They are notifications, not saved
measurement records. Reconcile their identities against the retained patches.

An operation starts with `started` and normally ends with one `terminal` event.
Intermediate instructions and reading events depend on the selected workflow.
There is no `measuring` event. The terminal payload reports status, counts,
diagnostics and separate software/instrument state; it does not repeat all
readings. Retrieve `result_json()` even after cancellation or a failed operation.
Reading or saving a retained result never starts another acquisition.

The shared finalizer retains validated readings before attempting their
notifications. Ordinary result-construction, serialization or terminal-notification
failures produce a failed envelope and terminal event, preserving readings and
releasing ownership only when the helper's software stop is confirmed. A missing patch
notification therefore does not imply a
missing retained reading. Terminal publication and result availability are
synchronized with completion and the conditional ownership release.

Before completion, polling takes the operation condition without waiting. This
keeps staged or early terminal events hidden until the retained result is ready.
After `done`, polling reads the restored thread-safe queue directly: a finalizer
still holding the condition cannot hide an already committed event. Empty text
then means the queue is empty. After observing `done`, drain it once more.

If the primitive recovery path itself fails, `done` is still set, but neither a
result nor a terminal event is guaranteed. Ownership is released only if the
software stop is confirmed; a terminal state alone does not release it.
`result_json()` raises `RuntimeError("Terminal result was not published")` when
there is no retained outcome. Handle that error instead of polling forever or
reacquiring automatically. Memory exhaustion and process loss are outside the
ordinary recovery guarantee.

If a result reports `software_stopped=false`, save its retained readings before
retiring the backend. Calling `close()`, retrieving the result or saving it does
not release the device instance or permit another Start in that backend. Restart
the backend through the client's guarded recovery path. Before acquiring again,
confirm that any surviving helper has stopped through the authorized process
owner. Backend restart alone does not establish that fact or instrument readiness.
Do not automatically retry the interrupted acquisition or terminate unrelated
processes. The original result remains an unconfirmed stop after recovery.

## Operation results

`OperationResult` and `PatchResult` are frozen dataclasses in
[`operation_records`](../src/color_measurement/operation_records.py). Direct
sequence fields use tuples; the strict JSON decoder reconstructs those tuples.

| Field | Meaning |
| --- | --- |
| `schema_version` | Operation-envelope schema `1`, separate from the nested observation schema |
| `operation_id`, `device_id`, `mode`, `workflow` | Operation identity and requested workflow |
| `device`, `provenance` | Device identity and simulated/replay/physical declaration, retained even without readings |
| `expected_patch_ids` | Unique patch IDs in requested order |
| `patches` | Increasing zero-based indices, matching patch IDs and original observations |
| `request_json` | Producer-retained request, including options, layout and event contract |
| `configuration`, `calibration` | Reported configuration facts and calibration context, including zero-reading outcomes |
| `status`, `error` | `completed` with null error, or `failed`/`cancelled` with an explanation |
| `error_stage`, `error_native_code` | Structured native failure details when available |
| `software_stopped`, `instrument_state` | Separate software outcome and reported instrument state |

The frontend normalizes the validated request to compact JSON before retaining
it; values are preserved, not its original spacing. Record validation checks
request identities and structure. The producer separately validates capability,
option and layout semantics. Configuration facts describe what was reported;
an empty set does not establish default hardware settings.

A completed measurement requires every expected patch exactly once. Failure or
cancellation may retain an ordered subset, including gaps or no patches. Missing
readings stay missing. All readings can also be present in a failed result if a
later stage failed. Assess both status and retained readings; their count alone
does not establish success or physical-device readiness.

Patches contain the workflow's typed record: colour `Observation`,
`ScalarObservation` for fresh refresh frequency, or `TimingObservation` for
display timing. Scalar and timing records do not invent XYZ or spectra. The
schema rejects a record kind in the wrong workflow and arbitrary unsupported
quantities. Calibration and configuration queries can complete without
measurement patches. See the [record definitions](data-format.md#operation-results).

Nested readings retain identifiers, native values, units, source text,
configuration and calibration. Observation IDs must be unique and identities
and provenance must agree across patches. This layer performs no spectral
integration, interpolation, clipping or scientific unit conversion.

## JSON and MAT persistence

Use `result_to_json` and `result_from_json` for operation text. Use
[`operation_exports.save` and `load`](data-format.md#operation-files-json-and-mat)
for complete JSON/MAT files, including partial, failed and cancelled operations.
Saving requires a new path in an existing directory; it does not overwrite a file
or acquire another reading. MAT support requires the optional `mat` extra.

```python
from color_measurement.operation_exports import load, save

# result is the already retained OperationResult from the example above.
saved = save(result, "operation.json")
restored = load(saved.path)
print(saved.ti3["status"])
```

An I/O failure raises `OperationSaveError` with the result, complete payload,
path and format for an explicit retry at a new destination. Validation and
missing-dependency failures occur before publication. See
[save and recovery semantics](data-format.md#saving-and-retrying).
There is no general operation-envelope CSV format. The individual-observation
JSON/CSV APIs and [bounded reflective SPECT export](data-format.md#operation-files-json-and-mat)
have different scopes; they do not replace the complete operation record.

The save returns `SaveResult(path, ti3)`. After publishing the complete operation,
it attempts [TI3 measurement companions](data-format.md#automatic-ti3-measurement-companions).
Inspect their file states and unavailable-component reasons separately from
canonical save success. No color rows are fabricated for scalar, timing or
zero-reading outcomes, and a companion failure never requests another reading.

### Actual serializer

<!-- source: src/color_measurement/operation_records.py:result_to_json -->
```python
def result_to_json(result: OperationResult) -> str:
    """Validate and serialize an envelope with explicit nulls and native text.

    The canonical format matches the existing observation JSON conventions.
    Nested observations preserve their values and source text; parsing JSON does
    not retain the incidental whitespace or escape spelling of its input file.
    """
    if not isinstance(result, OperationResult):
        raise ValueError("Expected an OperationResult record")
    data = asdict(result)
    _decode(OperationResult, data)
    return json.dumps(data, ensure_ascii=True, allow_nan=False, indent=2) + "\n"
```

Serialization revalidates nested records and preserves their values and source
text. Keep canonical JSON unchanged when presenting inspection views, including
the [MATLAB null/empty-array distinction](data-format.md#json). These structural
checks do not establish physical provenance, calibration validity or accuracy.
