# Pure guided-chart state

[`color_measurement.chart_state`](../src/color_measurement/chart_state.py) owns
canonical cells, pass membership, attempt history and selected reading references.
Its functions return independent JSON data. They perform no acquisition, file
access, calibration, worker management or publication. The application owns those
effects through its existing request, retained-record and publication owners.

Every attempt binds its intended canonical cell before acquisition. Display
rotation and inspection never change that association. Native `sample_id`,
operation and observation identities remain separate from coordinate labels.
Failed or cancelled retries retain their results and preserve the previous
successful selected attempt. Coverage counts selected cells in the active pass;
it does not combine cells from different material contexts.

## Structural definitions

`definition("classic")` creates a 4 × 6 grid; `definition("sg")` creates a
10 × 14 grid. `definition("custom", name=..., rows=..., columns=...)` validates
positive integer dimensions before allocation and permits at most 140 cells.
Definitions contain structural mappings, not measured colors or reference values.

Canonical cells are one-based and traverse rows first. Coordinates use column
letters followed by the row number. For example, canonical cell 2 in Classic is
`B1`, while its original source location is `A2` and source ID is 2. SG's same
canonical cell is `B1`, source location `B01`, source ID 11. Those distinct source
conventions are preserved rather than interpreted as physical placement.

The compact `view` adds nullable `patch_name` as descriptive presentation metadata.
Classic names follow X-Rite's
[CxF3 Schema overview](https://www.xrite.com/-/media/xrite/files/literature/misc/c/cxf3_schema_overview_en.pdf),
which lists the traditional descriptive names. Calibrite's [24-patch guide](https://calibrite.com/know-your-chart-a-color-perfectionists-guide-to-the-24-patch-colorchecker-classic/)
also lists the first three rows in that order and describes the six neutral patches.
Names are mapped by the validated Classic kind and canonical cell index, not by
dimensions or color similarity. SG, custom and TI2 cells have `patch_name: null`;
no unprovided name is inferred. A name remains separate from its coordinate,
original source location and native sample identity. It supplies no expected
color, specimen identification or measurement condition. Names are derived in
the view and do not change the stored definition, chart digest or original data.

For TI2, pass the retained packet from
[`chart_targets`](data-format.md#import-a-ti2-target):
`definition("ti2", target_json=packet, strips_as="rows" | "columns", mapping_confirmed=True)`.
The existing importer authenticates the exact source bytes and all projections.
The explicit axis choice maps source strip/step to application row/column;
rotation does not change it. Each cell retains the original text sample ID,
source location and source-table row. No original-path access occurs here.

## Worked software-only example

This example associates synthetic references with a two-cell grid. It is not a
measurement or a scientific qualification of these declared facts.

```python
from copy import deepcopy
from uuid import UUID
from color_measurement import chart_state

def identity(number):
    return str(UUID(int=number))

facts = {
    "phase": "intended",
    "material": {
        "observer": {"value": "synthetic observer", "certainty": "requested",
                     "source": "software-only example"},
        "options": {"value": {}, "certainty": "requested",
                    "source": "software-only example"},
    },
    "provenance": {"scope": "software-only"},
    "required_material": ["observer", "options"],
}
grid = chart_state.definition("custom", name="Example", rows=1, columns=2)
state = chart_state.new_state(grid, facts, run_id=identity(1),
                              segment_id=identity(2), pass_id=identity(3))
state = chart_state.begin_attempt(
    state, attempt_id=identity(4), command_id=identity(5), operation_id=identity(6),
    request={"workflow": "spot", "patch_ids": ["native-sample"], "options": {}},
    intended_context=facts,
)
accepted = deepcopy(facts)
accepted["phase"] = "accepted"
for fact in accepted["material"].values():
    fact["certainty"] = "readback"
state = chart_state.complete_attempt(
    state, identity(4), status="completed",
    reading_refs=[{"segment_id": identity(2), "operation_id": identity(6),
                   "observation_id": "synthetic-reading", "sample_id": "native-sample"}],
    accepted_context=accepted,
)
summary = chart_state.view(state)
assert summary["coverage"] == 1 and summary["current_cell"] == 2
assert summary["cells"][0]["coordinate"] == "A1"
details = chart_state.attempt_details(state, identity(4))
assert details["readings"][0]["sample_id"] == "native-sample"
history = chart_state.history(state, identity(3), 1)
assert history["total"] == 1 and history["attempts"][0]["id"] == identity(4)
assert chart_state.decision_details(state)["pending_context"] is None
```

## Context and decisions

A context retains `phase` (`intended` or `accepted`), `material`, `provenance`
and `required_material`. Each material fact contains `value`, `certainty` and
`source`. Supported certainty values are `unknown`, `requested`, `acknowledged`,
`derived`, `readback` and `operator-declared`. The application constructs these
facts from actual descriptor, request and retained-record evidence. The helper
does not infer units, observer, optical equivalence or missing measurements.

Material value changes, accepted loss of an established fact and newly requested
intended facts trigger a context decision. Accepted enrichment or a change of
certainty/source alone does not split a pass. Observer, unit, calibration and
option values therefore stay within their declared pass. `accept_context`
creates an empty pass explicitly; earlier passes remain inspectable. Resume
refuses an unresolved context. Returning to the previous intended setup can
clear an ordinary proposed change; a returned unresolved context still requires
an explicit decision.

Reference qualification and placement calculations belong to shared science.
`set_reference` retains its complete plain packet with `qualified` and `digest`.
A nonnull reference is frozen for that pass even before its first attempt. It
cannot be replaced or cleared; replaying the exact packet returns unchanged
state. Material changes after reference freezing require explicit pass acceptance
even when no readings have been attempted yet.

`start_pass(state, pass_id=..., context=...)` explicitly starts another empty pass,
including a pass under the same material context. It preserves prior attempts,
selected reading identities and references, resets the acquisition cell to one
and retains the current pause state. It refuses an active attempt, pending
placement or pending material-context decision. The new pass has no reference or
selected readings. An omitted context preserves the previous context; supplied
facts use the same context validator and no scientific defaults are invented.
This permits a measured run to become the application-authenticated source for a
frozen reference in a fresh comparison pass without changing the original run.
`complete_attempt` retains the complete position-check packet with `available`,
`warning`, `reason` and, for a warning, the exact `reference_digest`. A warning
requires the pass's qualified reference. This helper computes no color distance
and never identifies a different physical patch.

A pending position token binds the run, decision-owner segment, pass, attempt,
intended cell, original operation/reading identities and complete check. Keep
selects that retained attempt for its original cell and advances. Retry retains
the attempt and any earlier selected reading, then returns to the same cell.
Later returns unchanged state, matching closing the review dialog. It does not
pause, advance, select, clear the pending decision or add a decision receipt.
Pending placement blocks acquisition, target changes, pause/context acceptance
and ordinary finish. Rotation and read-only inspection remain available.
Finish also requires an explicit `allow_incomplete=True` for incomplete coverage
and refuses a still-active chart attempt.

## API and durable data

| Function | Purpose |
| --- | --- |
| `definition`, `validate_definition`, `coordinate` | Create and authenticate canonical structural mappings |
| `new_state`, `validate_state` | Create or validate strict version-1 plain chart state |
| `select_cell`, `inspect_cell`, `rotate`, `set_paused` | Keep acquisition target, inspection and orientation distinct |
| `validate_context`, `context_difference`, `propose_context`, `accept_context` | Share strict context validation, compare declared material facts and explicitly start a new pass |
| `start_pass` | Explicitly start empty coverage/reference under a supplied or preserved context |
| `begin_attempt`, `complete_attempt` | Freeze request/cell ownership, retain outcomes and update selection |
| `set_reference`, `resolve_position` | Store shared-science packets and resolve an exact retained placement token |
| `select_attempt`, `view`, `attempt_details` | Select an original active-pass attempt, inspect a bounded summary or retrieve one exact retained attempt |
| `history`, `decision_details` | Page attempt identities for one pass and cell or retrieve exact context/reference/placement packets |
| `finish` | Apply explicit incomplete-finish and pending-operation/decision guards |
| `continue_state` | Link accepted source bytes into a fresh segment without reopening an old writer |

State stores the run/segment identities, definition, orientation, current and
inspection cells, pause/finish facts, active pass, all passes, pending context,
source metadata, continuation relation and revision. Each pass stores its full
context, attempts, one nullable selected attempt ID per cell, frozen reference,
pending position and decision receipts. Each attempt stores command/operation
identities, intended cell, exact request and canonical request digest, intended
and nullable accepted contexts, original native `result_status`/`result_reason`,
chart-classified `status`/`reason`, exact reading references, position check and
immutable terminal-outcome digest. `terminal_sha256` hashes the canonical JSON
object containing `result_status`, `result_reason`, `status`, `reason`, `readings`,
`accepted_context` and `position_check` after classification. Canonical JSON uses
sorted keys, ASCII escaping, no nonfinite values and comma/colon separators.
Validation recomputes this digest; it is separate from operation-file SHA-256.
Replaying the original native outcome is idempotent even when the chart classified
it as unresolved. Changing a retained terminal outcome is refused.
`complete_attempt` normalizes supplied reading sequences to a plain list and
refuses nonlist values with `ValueError` before inspecting their length or
classifying the retained outcome. Invalid input leaves the caller's state unchanged.

`view(state, pass_id=...)` returns geometry without the imported target packet,
coverage, pass/source/attempt counts, selected IDs, per-cell latest status and
compact pending decisions. It returns no source bytes, full contexts, attempts,
pass history or scientific reference/check packets. Pending reason previews are
limited to 1,024 characters. Its shape is bounded by chart geometry, not history
length. `attempt_details(state, attempt_id, pass_id=...)` returns an independent
copy of one exact retained attempt plus its owning pass ID. The optional pass
guard rejects an attempt owned by another pass. Exact decision packets and history
navigation remain explicit queries rather than repeated snapshots.

`history(state, pass_id, cell, offset=0, limit=50)` returns `{total, offset,
attempts}` for one explicitly named pass and canonical cell. Attempts retain their
chronological insertion order and contain only `id`, `segment_id`, `operation_id`
and chart-classified `status`. The total counts all attempts for that cell in that
pass, including failed and cancelled retries; it is independent of the requested
page. Cells are one-based integers, offset is a nonnegative integer, and limit is
an integer from 1 through 50. Boolean values are refused. An offset beyond the
history returns an empty page with the original total, and an unknown or omitted
pass identity is refused rather than selecting the active pass.

`decision_details(state)` returns the complete `pending_context` plus an ordered
`passes` list containing each pass's `id`, `context`, `reference` and
`pending_position`. It does not include attempt history, operation files or source
bytes. These two read-only helpers validate state at their public boundary and
return independent data. Inspecting or editing the returned packets cannot alter
retained chart state, accept a decision or change selected coverage.

## Linked saved continuation

The application's read-only saved-source review must authenticate the accepted
manifest receipt and every referenced operation/result before calling
`continue_state`. The supplied expected digest comes from that accepted source,
not a digest calculated from untrusted replacement bytes. The helper verifies
the exact accepted manifest bytes against that digest and their embedded
`batch_id` and chart state. It retains only the source segment, descriptive source
ID and SHA-256 as `{source_id, segment_id, sha256}`. Admission accepts at most
32 MiB of exact manifest bytes and at most 64 inherited segments. Source segments
must be unique and precede the fresh segment; the continuation relation names
the immediate source. No ancestor manifest is embedded recursively.

The application publication owner must retain each original manifest and operation
file once in a portable SHA-addressed source store, authenticate the referenced
graph during review or rescue and include that store with the saved continuation.
This does not depend on the original source folder. `validate_state` verifies
metadata and reference invariants; it does not read or authenticate that external
store. Source-store publication and transport are application responsibilities,
not effects performed by this helper.

Continuation requires a fresh segment identity. Compatible continuation preserves
original operation/observation/sample/segment references, selected attempts and
coverage. Required material facts must be established in the source pass, every
selected reading's original accepted context and the target context. Later
enrichment of a pass cannot certify an earlier selected reading's unknown facts.
A changed or unresolved material context requires a new empty pass; old history
remains retained and never contributes to that pass's coverage. An inherited
reference stays frozen. A continued active placement decision gets a fresh owner
token while its original reading identities remain unchanged. Historical pending
decisions remain inspectable.

Source authentication is a caller responsibility as well as a byte-binding check.
A matching helper digest alone does not establish that an external source was
accepted, validate physical measurement conditions or authorize hardware access.
