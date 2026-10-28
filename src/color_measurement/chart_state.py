"""Pure guided-chart state over the application's existing durable operations.

Canonical cells are independent of native sample IDs and display orientation.
The application authenticates requests/results and owns every acquisition,
publication and saved-source read. This module performs none of those effects.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from copy import deepcopy
from uuid import UUID

from .chart_targets import MAX_PATCHES, validate_target_json
from .json_values import unique_object

MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SOURCES = 64
# Traditional descriptive labels, not expected colors or measurements.
# X-Rite CxF3 Schema overview; source and canonical mapping in docs/chart-state.md.
_CLASSIC_PATCH_NAMES = (
    "Dark skin", "Light skin", "Blue sky", "Foliage", "Blue flower", "Bluish green",
    "Orange", "Purplish blue", "Moderate red", "Purple", "Yellow green", "Orange yellow",
    "Blue", "Green", "Red", "Yellow", "Magenta", "Cyan", "White", "Neutral 8",
    "Neutral 6.5", "Neutral 5", "Neutral 3.5", "Black",
)
_TERMINAL_FIELDS = (
    "result_status",
    "result_reason",
    "status",
    "reason",
    "readings",
    "accepted_context",
    "position_check",
)
_CERTAINTIES = {"unknown", "requested", "acknowledged", "derived", "readback", "operator-declared"}
_STATE_FIELDS = {
    "format",
    "version",
    "run_id",
    "segment_id",
    "definition",
    "orientation",
    "current_cell",
    "inspection_cell",
    "paused",
    "active_pass",
    "passes",
    "pending_context",
    "sources",
    "continuation",
    "revision",
    "finished",
}


def _json(value):
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _plain(value):
    try:
        return json.loads(_json(value), object_pairs_hook=unique_object)
    except (ValueError, TypeError, RecursionError) as error:
        raise ValueError("Chart state requires finite, bounded-depth JSON data") from error


def _digest(value):
    return hashlib.sha256(_json(value).encode("ascii")).hexdigest()


def _terminal_facts(attempt):
    return {key: attempt[key] for key in _TERMINAL_FIELDS}


def _fields(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"Chart requires fields {sorted(fields)}")


def _uuid(value):
    try:
        valid = isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Chart identities must be canonical UUIDs")
    return value


def _text(value, *, limit=1024, empty=False):
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (not empty and not value.strip())
        or any(unicodedata.category(char) in {"Cc", "Zl", "Zp"} for char in value)
    ):
        raise ValueError("Chart labels require bounded single-line text")
    return value


def _index(value, limit):
    if type(value) is not int or not 1 <= value <= limit:
        raise ValueError(f"Chart index must be an integer in 1..{limit}")
    return value


def _identity_text(value):
    if not isinstance(value, str) or not value or len(value) > 1024 or "\0" in value:
        raise ValueError("Chart reading identities require bounded nonempty text without NUL")
    return value


def coordinate(row, column):
    """Return column letters plus row number, without interpreting source labels."""
    _index(row, MAX_PATCHES)
    _index(column, MAX_PATCHES)
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row}"


def definition(
    kind,
    *,
    name=None,
    rows=None,
    columns=None,
    target_json=None,
    strips_as=None,
    mapping_confirmed=False,
):
    """Construct structural cells; TI2 mapping needs an explicit axis confirmation."""
    if not isinstance(kind, str):
        raise ValueError("Choose classic, sg, custom or ti2")
    if kind in {"classic", "sg"}:
        if any(value is not None for value in (name, rows, columns, target_json, strips_as)):
            raise ValueError("Preset chart dimensions and source mapping are fixed")
        rows, columns = (4, 6) if kind == "classic" else (10, 14)
        name = "ColorChecker Classic" if kind == "classic" else "ColorChecker Digital SG"
    elif kind == "custom":
        _text(name, limit=128)
        _index(rows, MAX_PATCHES)
        _index(columns, MAX_PATCHES)
        if rows * columns > MAX_PATCHES:
            raise ValueError("Chart dimensions exceed 140 cells")
        if target_json is not None or strips_as is not None:
            raise ValueError("A custom grid has no imported target mapping")
    elif kind == "ti2":
        if strips_as not in {"rows", "columns"} or mapping_confirmed is not True:
            raise ValueError("Confirm whether TI2 strips form rows or columns on the chart")
        if any(value is not None for value in (name, rows, columns)):
            raise ValueError("Imported dimensions come from authenticated TI2 bytes")
        target_json = validate_target_json(target_json)
        target = json.loads(target_json)
        rows, columns = target["strips"], target["steps"]
        if strips_as == "columns":
            rows, columns = columns, rows
        name = target["filename"][:128]
    else:
        raise ValueError("Choose classic, sg, custom or ti2")
    cells = []
    for index in range(1, rows * columns + 1):
        row, column = divmod(index - 1, columns)
        row, column = row + 1, column + 1
        source_id, source_location, source_row = None, coordinate(row, column), None
        if kind == "classic":
            source_id, source_row = index, index
            source_location = f"{chr(64 + row)}{column}"
        elif kind == "sg":
            source_id = (column - 1) * rows + row
            source_row, source_location = source_id, f"{chr(64 + column)}{row:02d}"
        cells.append(
            dict(
                index=index,
                row=row,
                column=column,
                coordinate=coordinate(row, column),
                source_id=source_id,
                source_location=source_location,
                source_row=source_row,
            )
        )
    if kind == "ti2":
        for item in target["rows"]:
            row, column = item["strip"], item["step"]
            if strips_as == "columns":
                row, column = column, row
            cells[(row - 1) * columns + column - 1].update(
                source_id=item["sample_id"],
                source_location=item["sample_loc"],
                source_row=item["source_row"],
            )
    value = dict(
        kind=kind,
        name=name,
        rows=rows,
        columns=columns,
        traversal="row-major-v1",
        cells=cells,
        target_json=target_json,
        strips_as=strips_as,
        mapping_confirmed=mapping_confirmed if kind == "ti2" else False,
    )
    return dict(value, id=_digest(value), revision=1)


def validate_definition(value):
    value = _plain(value)
    _fields(
        value,
        {
            "kind",
            "name",
            "rows",
            "columns",
            "traversal",
            "cells",
            "target_json",
            "strips_as",
            "mapping_confirmed",
            "id",
            "revision",
        },
    )
    kind = value["kind"]
    arguments = {}
    if kind == "custom":
        arguments = {key: value[key] for key in ("name", "rows", "columns")}
    elif kind == "ti2":
        arguments = {key: value[key] for key in ("target_json", "strips_as", "mapping_confirmed")}
    expected = definition(kind, **arguments)
    if _json(value) != _json(expected):
        raise ValueError("Chart definition or canonical source mapping changed")
    return value


def validate_context(value):
    value = _plain(value)
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("phase"), str)
        or value["phase"] not in {"intended", "accepted"}
        or not isinstance(value.get("material"), dict)
        or not isinstance(value.get("provenance"), dict)
        or not isinstance(value.get("required_material"), list)
    ):
        raise ValueError(
            "Chart context needs phase, material facts, provenance and required_material"
        )
    for key, fact in value["material"].items():
        _text(key, limit=128)
        _fields(fact, {"value", "certainty", "source"})
        if not isinstance(fact["certainty"], str) or fact["certainty"] not in _CERTAINTIES:
            raise ValueError("Unknown chart material-fact certainty")
        if (
            not isinstance(fact["source"], str)
            or not fact["source"].strip()
            or len(fact["source"]) > 2048
        ):
            raise ValueError("Chart material facts require a bounded source description")
    required = value["required_material"]
    if (
        any(not isinstance(key, str) for key in required)
        or len(set(required)) != len(required)
        or any(key not in value["material"] for key in required)
    ):
        raise ValueError("Required chart material facts must be present exactly once")
    return value


def context_difference(before, after):
    """Compare material values, retaining intent/readback certainty separately."""
    before, after = validate_context(before), validate_context(after)
    unknown = dict(value=None, certainty="unknown", source="not reported")
    differences = []
    for key in sorted(set(before["material"]) | set(after["material"])):
        old, new = before["material"].get(key, unknown), after["material"].get(key, unknown)
        old_known, new_known = old["certainty"] != "unknown", new["certainty"] != "unknown"
        kind = None
        if old_known and new_known and _json(old["value"]) != _json(new["value"]):
            kind = "changed"
        elif not old_known and new_known and after["phase"] == "intended":
            if new["certainty"] == "requested":
                kind = "specified"
        elif old_known and not new_known and after["phase"] == "accepted":
            kind = "lost"
        if kind:
            differences.append(dict(key=key, kind=kind, before=old, after=new))
    return differences


def _new_pass(pass_id, context, count):
    return dict(
        id=_uuid(pass_id),
        context=validate_context(context),
        attempts=[],
        selected_attempts=[None] * count,
        reference=None,
        pending_position=None,
        position_decisions=[],
    )


def new_state(chart_definition, context, *, run_id, segment_id, pass_id):
    """Start one empty chart; IDs and publication belong to the application."""
    chart_definition = validate_definition(chart_definition)
    return dict(
        format="guided-chart",
        version=1,
        run_id=_uuid(run_id),
        segment_id=_uuid(segment_id),
        definition=chart_definition,
        orientation=0,
        current_cell=1,
        inspection_cell=1,
        paused=False,
        active_pass=pass_id,
        passes=[_new_pass(pass_id, context, len(chart_definition["cells"]))],
        pending_context=None,
        sources=[],
        continuation=None,
        revision=1,
        finished=False,
    )


def _pass(state, pass_id=None):
    selected = state["active_pass"] if pass_id is None else pass_id
    matches = [item for item in state["passes"] if item["id"] == selected]
    if len(matches) != 1:
        raise ValueError("Unknown chart pass")
    return matches[0]


def _attempt(pass_state, attempt_id):
    matches = [item for item in pass_state["attempts"] if item["id"] == attempt_id]
    if len(matches) != 1:
        raise ValueError("Unknown chart attempt")
    return matches[0]


def _reading(value, operation_id):
    _fields(value, {"segment_id", "operation_id", "observation_id", "sample_id"})
    _uuid(value["segment_id"])
    _uuid(value["operation_id"])
    _identity_text(value["observation_id"])
    _identity_text(value["sample_id"])
    if value["operation_id"] != operation_id:
        raise ValueError("Chart reading belongs to another operation")


def validate_state(state):
    """Validate durable references/invariants; do not authenticate external files."""
    state = _plain(state)
    _fields(state, _STATE_FIELDS)
    if state["format"] != "guided-chart" or type(state["version"]) is not int:
        raise ValueError("Unsupported chart state format")
    if state["version"] != 1:
        raise ValueError("Unsupported chart state version")
    _uuid(state["run_id"])
    _uuid(state["segment_id"])
    validate_definition(state["definition"])
    count = len(state["definition"]["cells"])
    _index(state["current_cell"], count)
    _index(state["inspection_cell"], count)
    if type(state["orientation"]) is not int or state["orientation"] not in {0, 90, 180, 270}:
        raise ValueError("Use chart orientation 0, 90, 180 or 270")
    if any(type(state[key]) is not bool for key in ("paused", "finished")):
        raise ValueError("Chart pause/finish facts must be boolean")
    if type(state["revision"]) is not int or state["revision"] < 1:
        raise ValueError("Chart revision must be positive")
    if not isinstance(state["passes"], list) or not state["passes"]:
        raise ValueError("Chart needs at least one pass")
    pass_ids, attempt_ids, operation_ids, started = set(), set(), set(), []
    for pass_state in state["passes"]:
        _fields(
            pass_state,
            {
                "id",
                "context",
                "attempts",
                "selected_attempts",
                "reference",
                "pending_position",
                "position_decisions",
            },
        )
        _uuid(pass_state["id"])
        if pass_state["id"] in pass_ids:
            raise ValueError("Duplicate chart pass")
        pass_ids.add(pass_state["id"])
        validate_context(pass_state["context"])
        _reference(pass_state["reference"])
        if not isinstance(pass_state["position_decisions"], list):
            raise ValueError("Chart placement decisions must be retained as a list")
        for decision in pass_state["position_decisions"]:
            _fields(decision, {"token", "attempt_id", "segment_id", "action"})
        if not isinstance(pass_state["attempts"], list):
            raise ValueError("Chart attempts must be a list")
        for attempt in pass_state["attempts"]:
            _fields(
                attempt,
                {
                    "id",
                    "segment_id",
                    "cell",
                    "command_id",
                    "operation_id",
                    "request",
                    "request_sha256",
                    "intended_context",
                    "accepted_context",
                    "status",
                    "reason",
                    "result_status",
                    "result_reason",
                    "readings",
                    "position_check",
                    "terminal_sha256",
                },
            )
            for key in ("id", "segment_id", "command_id", "operation_id"):
                _uuid(attempt[key])
            if attempt["id"] in attempt_ids or attempt["operation_id"] in operation_ids:
                raise ValueError("Duplicate chart attempt or operation")
            attempt_ids.add(attempt["id"])
            operation_ids.add(attempt["operation_id"])
            _index(attempt["cell"], count)
            if not isinstance(attempt["request"], dict):
                raise ValueError("Chart attempt needs its exact request object")
            if attempt["request_sha256"] != _digest(attempt["request"]):
                raise ValueError("Frozen chart request changed")
            validate_context(attempt["intended_context"])
            if attempt["accepted_context"] is not None:
                validate_context(attempt["accepted_context"])
            if not isinstance(attempt["status"], str) or attempt["status"] not in {
                "started",
                "completed",
                "cancelled",
                "failed",
                "unresolved",
            }:
                raise ValueError("Unknown chart attempt state")
            if not isinstance(attempt["reason"], str) or len(attempt["reason"]) > 8192:
                raise ValueError("Chart attempt reason must be bounded text")
            if not isinstance(attempt["readings"], list):
                raise ValueError("Chart reading references must be a list")
            for reading in attempt["readings"]:
                _reading(reading, attempt["operation_id"])
                if reading["segment_id"] != attempt["segment_id"]:
                    raise ValueError("Reading source segment differs from its frozen attempt")
            terminal = attempt["terminal_sha256"]
            if attempt["status"] == "started":
                if (
                    terminal is not None
                    or attempt["readings"]
                    or attempt["accepted_context"] is not None
                    or attempt["result_status"] is not None
                    or attempt["result_reason"] is not None
                    or attempt["position_check"] is not None
                    or attempt["reason"]
                ):
                    raise ValueError("Started chart attempt cannot contain a terminal receipt")
                started.append((pass_state["id"], attempt["segment_id"]))
            else:
                if (
                    not isinstance(attempt["result_status"], str)
                    or attempt["result_status"] not in {
                        "completed", "cancelled", "failed", "unresolved"
                    }
                    or not isinstance(attempt["result_reason"], str)
                    or len(attempt["result_reason"]) > 8192
                ):
                    raise ValueError("Terminal chart attempt needs its original native outcome")
                if terminal != _digest(_terminal_facts(attempt)):
                    raise ValueError("Terminal chart outcome changed")
                if (
                    attempt["status"] != attempt["result_status"]
                    and not (
                        attempt["result_status"] == "completed"
                        and attempt["status"] == "unresolved"
                    )
                ):
                    raise ValueError("Chart classification differs from its native outcome")
                if (
                    attempt["status"] == attempt["result_status"]
                    and attempt["reason"] != attempt["result_reason"]
                ):
                    raise ValueError("Chart reason differs from its retained native outcome")
            if attempt["status"] == "completed":
                if attempt["accepted_context"] is None:
                    raise ValueError("Completed chart attempt needs its retained accepted context")
                if attempt["position_check"] is not None:
                    _check_position(pass_state, attempt["position_check"])
        selected = pass_state["selected_attempts"]
        if not isinstance(selected, list) or len(selected) != count:
            raise ValueError("Chart selection must retain one slot per canonical cell")
        for cell, selected_id in enumerate(selected, 1):
            if selected_id is not None:
                attempt = _attempt(pass_state, selected_id)
                if (
                    attempt["cell"] != cell
                    or attempt["status"] != "completed"
                    or len(attempt["readings"]) != 1
                ):
                    raise ValueError(
                        "Selected chart reading must be a completed attempt for that cell"
                    )
                if attempt["position_check"] is not None and attempt["position_check"]["warning"]:
                    if not any(
                        item["attempt_id"] == selected_id and item["action"] == "keep"
                        for item in pass_state["position_decisions"]
                    ):
                        raise ValueError("Warned chart reading needs its retained Keep decision")
        _validate_position(state, pass_state)
        if not isinstance(pass_state["position_decisions"], list):
            raise ValueError("Chart placement decisions must be retained as a list")
        for decision in pass_state["position_decisions"]:
            _fields(decision, {"token", "attempt_id", "segment_id", "action"})
            decided = _attempt(pass_state, decision["attempt_id"])
            if decision["action"] not in {"keep", "retry"} or decided["status"] != "completed":
                raise ValueError("Placement decision must name its completed retained attempt")
            _uuid(decision["segment_id"])
            _check_position(pass_state, decided["position_check"])
            if decided["position_check"]["warning"] is not True:
                raise ValueError("A placement decision requires a qualified retained warning")
            receipt = dict(
                attempt_id=decided["id"],
                segment_id=decision["segment_id"],
                check=decided["position_check"],
            )
            if decision["token"] != _position_token(state, pass_state, receipt):
                raise ValueError("Retained placement decision does not match its frozen reading")
    _pass(state)
    if len(started) > 1 or any(
        pass_id != state["active_pass"] or segment != state["segment_id"]
        for pass_id, segment in started
    ):
        raise ValueError("Only this segment's active pass may retain one started chart attempt")
    if state["pending_context"] is not None:
        pending = state["pending_context"]
        _fields(pending, {"previous", "proposed", "differences", "reason", "requires_decision"})
        validate_context(pending["previous"])
        validate_context(pending["proposed"])
        if type(pending["requires_decision"]) is not bool:
            raise ValueError("Pending context must retain explicit decision eligibility")
    if not isinstance(state["sources"], list) or len(state["sources"]) > MAX_SOURCES:
        raise ValueError("Chart continuation exceeds 64 source segments")
    source_segments = set()
    for source in state["sources"]:
        _source(source)
        if source["segment_id"] in source_segments or source["segment_id"] == state["segment_id"]:
            raise ValueError("Chart source segments must be unique and precede this segment")
        source_segments.add(source["segment_id"])
    continuation = state["continuation"]
    if continuation is not None:
        _fields(continuation, {"source_segment_id", "source_pass_id", "action"})
        _uuid(continuation["source_segment_id"])
        _pass(state, continuation["source_pass_id"])
        if continuation["action"] not in {"continue", "new-pass"}:
            raise ValueError("Unknown saved chart continuation action")
        if (
            not state["sources"]
            or continuation["source_segment_id"] != state["sources"][-1]["segment_id"]
        ):
            raise ValueError("Chart continuation needs its immediate accepted source segment")
    elif state["sources"]:
        raise ValueError("Inherited chart sources need their continuation relation")
    segments = {state["segment_id"], *source_segments}
    if any(
        attempt["segment_id"] not in segments
        for item in state["passes"]
        for attempt in item["attempts"]
    ):
        raise ValueError("Inherited chart attempt needs its exact accepted source segment")
    if state["finished"] and _pass(state)["pending_position"] is not None:
        raise ValueError("A finished chart cannot retain an unresolved placement decision")
    return state


def _position_token(state, pass_state, pending):
    attempt = _attempt(pass_state, pending["attempt_id"])
    return _digest(
        dict(
            run_id=state["run_id"],
            segment_id=pending["segment_id"],
            pass_id=pass_state["id"],
            attempt_id=pending["attempt_id"],
            cell=attempt["cell"],
            operation_id=attempt["operation_id"],
            readings=attempt["readings"],
            check=pending["check"],
        )
    )


def _validate_position(state, pass_state):
    pending = pass_state["pending_position"]
    if pending is None:
        return
    _fields(pending, {"attempt_id", "segment_id", "check", "token"})
    _uuid(pending["segment_id"])
    attempt = _attempt(pass_state, pending["attempt_id"])
    if attempt["status"] != "completed" or len(attempt["readings"]) != 1:
        raise ValueError("Placement decision needs one retained completed reading")
    if pending["check"] != attempt["position_check"]:
        raise ValueError("Pending placement check differs from its retained attempt")
    _check_position(pass_state, pending["check"])
    if pending["check"]["warning"] is not True:
        raise ValueError("Only a qualified placement warning creates a pending decision")
    if pass_state["id"] == state["active_pass"] and pending["segment_id"] != state["segment_id"]:
        raise ValueError("Active placement decision belongs to another batch segment")
    if pending["token"] != _position_token(state, pass_state, pending):
        raise ValueError("Placement decision token changed")


def _check_position(pass_state, check):
    if (
        not isinstance(check, dict)
        or type(check.get("available")) is not bool
        or type(check.get("warning")) is not bool
        or not isinstance(check.get("reason"), str)
    ):
        raise ValueError("Position checks require shared-science availability, warning and reason")
    if check["warning"]:
        reference = pass_state["reference"]
        if (
            check["available"] is not True
            or not isinstance(reference, dict)
            or reference.get("qualified") is not True
            or check.get("reference_digest") != reference.get("digest")
        ):
            raise ValueError(
                "Position warning requires the exact qualified shared-science reference"
            )


def _editable(state, *, position=True, active=False):
    if state["finished"]:
        raise ValueError("Chart is finished")
    pass_state = _pass(state)
    if position and pass_state["pending_position"] is not None:
        raise ValueError("Keep or Retry the retained placement before changing the active chart")
    if active and any(attempt["status"] == "started" for attempt in pass_state["attempts"]):
        raise ValueError("Chart attempt is still active")
    return pass_state


def _changed(state):
    state["revision"] += 1
    return validate_state(state)


def select_cell(state, cell):
    state = validate_state(state)
    _editable(state, active=True)
    _index(cell, len(state["definition"]["cells"]))
    if state["current_cell"] == cell:
        return state
    state["current_cell"] = cell
    return _changed(state)


def inspect_cell(state, cell):
    state = validate_state(state)
    _index(cell, len(state["definition"]["cells"]))
    state["inspection_cell"] = cell
    return state


def rotate(state, degrees):
    state = validate_state(state)
    _editable(state, position=False)
    if type(degrees) is not int or degrees not in {0, 90, 180, 270}:
        raise ValueError("Use chart orientation 0, 90, 180 or 270")
    if state["orientation"] == degrees:
        return state
    state["orientation"] = degrees
    return _changed(state)


def set_paused(state, paused):
    state = validate_state(state)
    _editable(state, active=True)
    if type(paused) is not bool:
        raise ValueError("Chart pause must be boolean")
    if not paused and state["pending_context"] is not None:
        raise ValueError("Accept the changed context as a new pass before resuming")
    if state["paused"] == paused:
        return state
    state["paused"] = paused
    return _changed(state)


def propose_context(state, context):
    state = validate_state(state)
    pass_state = _editable(state, active=True)
    context = validate_context(context)
    differences = context_difference(pass_state["context"], context)
    if not differences:
        pending = state["pending_context"]
        if pending is not None and not pending["requires_decision"]:
            state["pending_context"] = None
            return _changed(state)
        return state
    if not pass_state["attempts"] and pass_state["reference"] is None:
        pass_state["context"] = context
    else:
        state["pending_context"] = dict(
            previous=pass_state["context"],
            proposed=context,
            differences=differences,
            reason="Acquisition context changed",
            requires_decision=False,
        )
    return _changed(state)


def accept_context(state, *, pass_id):
    state = validate_state(state)
    _editable(state, active=True)
    pending = state["pending_context"]
    if pending is None:
        raise ValueError("No material context change is pending")
    if any(item["id"] == pass_id for item in state["passes"]):
        raise ValueError("New chart pass needs a fresh identity")
    state["passes"].append(
        _new_pass(pass_id, pending["proposed"], len(state["definition"]["cells"]))
    )
    state.update(active_pass=pass_id, current_cell=1, pending_context=None)
    return _changed(state)


def start_pass(state, *, pass_id, context=None):
    """Explicitly start empty coverage, preserving all earlier attempts/references."""
    state = validate_state(state)
    previous = _editable(state, active=True)
    if state["pending_context"] is not None:
        raise ValueError("Resolve the pending material context before starting another pass")
    if any(item["id"] == pass_id for item in state["passes"]):
        raise ValueError("New chart pass needs a fresh pass identity")
    context = previous["context"] if context is None else validate_context(context)
    state["passes"].append(_new_pass(pass_id, context, len(state["definition"]["cells"])))
    state.update(active_pass=pass_id, current_cell=1)
    return _changed(state)


def set_reference(state, reference):
    state = validate_state(state)
    pass_state = _pass(state)
    reference = _plain(reference)
    _reference(reference)
    if _json(pass_state["reference"]) == _json(reference):
        return state
    if pass_state["reference"] is not None:
        raise ValueError("Pass reference is frozen; start a new explicit pass")
    pass_state = _editable(state, active=True)
    if pass_state["attempts"] or state["pending_context"] is not None:
        raise ValueError("Set reference context before attempts in this pass")
    if (
        state["continuation"] is not None
        and state["continuation"]["action"] == "continue"
        and pass_state["id"] == state["continuation"]["source_pass_id"]
    ):
        raise ValueError("Inherited pass retains its frozen reference; start a new pass")
    pass_state["reference"] = reference
    return _changed(state)


def _reference(reference):
    if reference is not None:
        if (
            not isinstance(reference, dict)
            or type(reference.get("qualified")) is not bool
            or not isinstance(reference.get("digest"), str)
            or len(reference["digest"]) != 64
            or any(char not in "0123456789abcdef" for char in reference["digest"])
        ):
            raise ValueError("Freeze the shared-science reference qualification and digest")


def begin_attempt(state, *, attempt_id, command_id, operation_id, request, intended_context):
    state = validate_state(state)
    pass_state = _editable(state, active=True)
    if state["paused"] or state["pending_context"] is not None:
        raise ValueError("Resume the chart and resolve its material context before acquisition")
    intended_context = validate_context(intended_context)
    if context_difference(pass_state["context"], intended_context):
        raise ValueError("Propose and explicitly accept the changed acquisition context first")
    request = _plain(request)
    if not isinstance(request, dict):
        raise ValueError("Chart attempt must freeze the prepared request object")
    pass_state["attempts"].append(
        dict(
            id=_uuid(attempt_id),
            segment_id=state["segment_id"],
            cell=state["current_cell"],
            command_id=_uuid(command_id),
            operation_id=_uuid(operation_id),
            request=request,
            request_sha256=_digest(request),
            intended_context=intended_context,
            accepted_context=None,
            status="started",
            reason="",
            result_status=None,
            result_reason=None,
            readings=[],
            position_check=None,
            terminal_sha256=None,
        )
    )
    return _changed(state)


def _advance(state, pass_state, cell):
    missing = [
        index
        for index, selected in enumerate(pass_state["selected_attempts"], 1)
        if selected is None
    ]
    if not missing:
        return
    after = [index for index in missing if index > cell]
    state["current_cell"] = after[0] if after else missing[0]


def complete_attempt(
    state, attempt_id, *, status, reading_refs, accepted_context, position_check=None, reason=""
):
    state = validate_state(state)
    matches = [
        (item, attempt)
        for item in state["passes"]
        for attempt in item["attempts"]
        if attempt["id"] == attempt_id
    ]
    if len(matches) != 1:
        raise ValueError("Unknown chart attempt")
    pass_state, attempt = matches[0]
    outcome = dict(
        status=status,
        readings=_plain(reading_refs),
        accepted_context=validate_context(accepted_context)
        if accepted_context is not None
        else None,
        position_check=_plain(position_check),
        reason=reason,
    )
    if not isinstance(outcome["readings"], list):
        raise ValueError("Chart reading references must be a list")
    if (
        outcome["accepted_context"] is not None
        and outcome["accepted_context"]["phase"] != "accepted"
    ):
        raise ValueError("A terminal reading cannot substitute intended facts for accepted context")
    if status not in {"completed", "cancelled", "failed", "unresolved"}:
        raise ValueError("Complete a chart attempt with a retained terminal outcome")
    if attempt["status"] != "started":
        original = dict(
            status=attempt["result_status"],
            reason=attempt["result_reason"],
            readings=attempt["readings"],
            accepted_context=attempt["accepted_context"],
            position_check=attempt["position_check"],
        )
        if _json(original) == _json(outcome):
            return state
        raise ValueError("Terminal chart attempt cannot be replaced by a different outcome")
    attempt.update(outcome)
    attempt.update(result_status=status, result_reason=reason)
    differences = (
        context_difference(pass_state["context"], outcome["accepted_context"])
        if accepted_context is not None
        else []
    )
    if status == "completed" and (
        differences or len(outcome["readings"]) != 1 or accepted_context is None
    ):
        attempt["status"] = "unresolved"
        attempt["reason"] = "Returned context changed or spot did not retain exactly one reading"
        state["pending_context"] = dict(
            previous=pass_state["context"],
            proposed=outcome["accepted_context"] or attempt["intended_context"],
            differences=differences,
            reason=attempt["reason"],
            requires_decision=True,
        )
    if attempt["status"] == "completed":
        if position_check is not None:
            _check_position(pass_state, position_check)
        if position_check is not None and position_check["warning"]:
            pending = dict(
                attempt_id=attempt_id,
                segment_id=state["segment_id"],
                check=deepcopy(position_check),
            )
            pending["token"] = _position_token(state, pass_state, pending)
            pass_state["pending_position"] = pending
        else:
            pass_state["selected_attempts"][attempt["cell"] - 1] = attempt_id
            _advance(state, pass_state, attempt["cell"])
        pass_state["context"] = outcome["accepted_context"]
    attempt["terminal_sha256"] = _digest(_terminal_facts(attempt))
    return _changed(state)


def select_attempt(state, *, pass_id, cell, attempt_id):
    state = validate_state(state)
    _editable(state, active=True)
    if pass_id != state["active_pass"] or state["pending_context"] is not None:
        raise ValueError("Historical or changed-context passes are inspection only")
    pass_state = _pass(state, pass_id)
    _index(cell, len(state["definition"]["cells"]))
    attempt = _attempt(pass_state, attempt_id)
    if attempt["cell"] != cell or attempt["status"] != "completed" or len(attempt["readings"]) != 1:
        raise ValueError("Select a successful retained attempt for its original cell")
    if attempt["position_check"] is not None and attempt["position_check"]["warning"]:
        kept = any(
            item["attempt_id"] == attempt_id and item["action"] == "keep"
            for item in pass_state["position_decisions"]
        )
        if not kept:
            raise ValueError(
                "Resolve the qualified placement warning before selecting this attempt"
            )
    pass_state["selected_attempts"][cell - 1] = attempt_id
    return _changed(state)


def resolve_position(state, token, action):
    state = validate_state(state)
    pass_state = _editable(state, position=False, active=True)
    pending = pass_state["pending_position"]
    if pending is None or token != pending["token"]:
        raise ValueError("Stale chart placement decision")
    if action == "later":
        return state  # Closing review changes no chart fact or decision receipt.
    if action not in {"keep", "retry"}:
        raise ValueError("Choose Keep, Retry or Later")
    if state["pending_context"] is not None:
        raise ValueError("Resolve the changed material context before a placement decision")
    attempt = _attempt(pass_state, pending["attempt_id"])
    pass_state["position_decisions"].append(
        dict(
            token=token,
            attempt_id=attempt["id"],
            segment_id=state["segment_id"],
            action=action,
        )
    )
    if action == "keep":
        pass_state["selected_attempts"][attempt["cell"] - 1] = attempt["id"]
        _advance(state, pass_state, attempt["cell"])
    else:
        state["current_cell"] = attempt["cell"]
    pass_state["pending_position"] = None
    return _changed(state)


def finish(state, *, allow_incomplete=False):
    state = validate_state(state)
    if state["finished"]:
        return state
    pass_state = _editable(state, active=True)
    if type(allow_incomplete) is not bool:
        raise ValueError("Incomplete finish requires an explicit boolean choice")
    if any(item is None for item in pass_state["selected_attempts"]) and not allow_incomplete:
        raise ValueError("Active chart pass is incomplete; choose Finish incomplete explicitly")
    state["finished"] = True
    return _changed(state)


def _source(source):
    _fields(source, {"source_id", "segment_id", "sha256"})
    _text(source["source_id"], limit=4096)
    _uuid(source["segment_id"])
    if (
        not isinstance(source["sha256"], str)
        or len(source["sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in source["sha256"])
    ):
        raise ValueError("Continuation source needs its exact accepted SHA-256")


def continue_state(
    source_state,
    context,
    *,
    segment_id,
    source_id,
    source_bytes,
    source_sha256,
    action="continue",
    pass_id=None,
    new_pass_id=None,
):
    """Link caller-authenticated accepted source bytes into a fresh segment only.

    The application's read-only review authenticates operation/result bytes and
    scientific context first. Here the exact accepted manifest must contain this
    chart and source batch identity. This creates no writable old-source owner.
    """
    state = validate_state(source_state)
    _uuid(segment_id)
    if segment_id in {state["segment_id"], *(item["segment_id"] for item in state["sources"])}:
        raise ValueError("Continuation requires a fresh batch segment")
    source = dict(
        source_id=source_id,
        segment_id=state["segment_id"],
        sha256=source_sha256,
    )
    _source(source)
    if (
        not isinstance(source_bytes, bytes)
        or not source_bytes
        or len(source_bytes) > MAX_SOURCE_BYTES
    ):
        raise ValueError("Continuation source bytes exceed 32 MiB or are not exact bytes")
    if hashlib.sha256(source_bytes).hexdigest() != source_sha256:
        raise ValueError("Continuation accepted source bytes changed")
    try:

        def invalid(number):
            raise ValueError(f"Nonfinite continuation source: {number}")

        envelope = json.loads(
            source_bytes,
            object_pairs_hook=unique_object,
            parse_constant=invalid,
        )
    except (ValueError, RecursionError, UnicodeError) as error:
        raise ValueError("Continuation needs accepted JSON manifest bytes") from error
    if (
        not isinstance(envelope, dict)
        or envelope.get("batch_id") != state["segment_id"]
        or _json(envelope.get("chart")) != _json(state)
    ):
        raise ValueError("Accepted continuation bytes do not bind this chart and segment")
    old_pass = _pass(state, pass_id)
    context = validate_context(context)
    if action not in {"continue", "new-pass"}:
        raise ValueError("Choose continue or new-pass")
    if any(item["status"] == "started" for item in old_pass["attempts"]):
        raise ValueError("A live source attempt cannot be continued")
    if action == "continue":
        if state["pending_context"] is not None or context_difference(old_pass["context"], context):
            raise ValueError("Source material continuity is unresolved; choose a new empty pass")
        required = set(old_pass["context"]["required_material"]) | set(context["required_material"])
        candidates = [("source pass", old_pass["context"]), ("current context", context)]
        for selected_id in old_pass["selected_attempts"]:
            if selected_id is not None:
                selected_context = _attempt(old_pass, selected_id)["accepted_context"]
                if selected_context is None or context_difference(selected_context, context):
                    raise ValueError("Inherited selected reading has incompatible material facts")
                candidates.append((f"selected attempt {selected_id}", selected_context))
        for key in sorted(required):
            for label, candidate in candidates:
                fact = candidate["material"].get(key)
                if (
                    fact is None
                    or fact["certainty"] in {"unknown", "requested"}
                    or fact["value"] is None
                    or (
                        isinstance(fact["value"], str)
                        and fact["value"].strip().lower()
                        in {"", "unknown", "unspecified", "not reported"}
                    )
                ):
                    raise ValueError(
                        "Continuation material facts must be established on both sides: "
                        f"{key} is unestablished in {label}"
                    )
        old_pass["context"] = context
        state["active_pass"] = old_pass["id"]
    else:
        if new_pass_id is None or any(item["id"] == new_pass_id for item in state["passes"]):
            raise ValueError("New-pass continuation needs a fresh pass identity")
        state["passes"].append(_new_pass(new_pass_id, context, len(state["definition"]["cells"])))
        state["active_pass"] = new_pass_id
    state["sources"].append(source)
    state["continuation"] = dict(
        source_segment_id=state["segment_id"], source_pass_id=old_pass["id"], action=action
    )
    state.update(segment_id=segment_id, pending_context=None, paused=False, finished=False)
    selected_pass = _pass(state)
    pending = selected_pass["pending_position"]
    if pending is not None:
        pending["segment_id"] = segment_id
        pending["token"] = _position_token(state, selected_pass, pending)
        state["current_cell"] = _attempt(selected_pass, pending["attempt_id"])["cell"]
    else:
        _advance(state, selected_pass, 0)
    return _changed(state)


def reading_cells(state):
    """Map every attempt's operation to its canonical coordinate and known name.

    Table labels use this so chart readings are named by A1/B1 coordinates in every
    view, including attempts in earlier passes and continued segments.
    """
    cells = {cell["index"]: cell for cell in state["definition"]["cells"]}
    classic = state["definition"]["kind"] == "classic"
    return {
        attempt["operation_id"]: dict(
            coordinate=cells[attempt["cell"]]["coordinate"],
            patch_name=_CLASSIC_PATCH_NAMES[attempt["cell"] - 1] if classic else None,
        )
        for pass_state in state["passes"] for attempt in pass_state["attempts"]
    }


def view(state, *, pass_id=None):
    """Return bounded geometry/counts/decisions without raw packets or history."""
    state = validate_state(state)
    pass_state = _pass(state, pass_id)
    count = len(state["definition"]["cells"])
    attempt_counts, latest = [0] * count, [None] * count
    for attempt in pass_state["attempts"]:
        index = attempt["cell"] - 1
        attempt_counts[index] += 1
        latest[index] = {key: attempt[key] for key in ("id", "status", "result_status")}
    cells = []
    for cell in state["definition"]["cells"]:
        index = cell["index"] - 1
        selected = pass_state["selected_attempts"][index]
        cells.append(
            dict(
                cell,
                attempt_count=attempt_counts[index],
                latest_attempt=latest[index],
                selected_attempt=selected,
                measured=selected is not None,
                patch_name=_CLASSIC_PATCH_NAMES[index]
                if state["definition"]["kind"] == "classic" else None,
            )
        )
    coverage = sum(item is not None for item in pass_state["selected_attempts"])
    pending_context = state["pending_context"]
    if pending_context is not None:
        pending_context = dict(
            reason=pending_context["reason"][:1024],
            requires_decision=pending_context["requires_decision"],
            difference_count=len(pending_context["differences"]),
        )
    pending_position = pass_state["pending_position"]
    if pending_position is not None:
        pending_position = dict(
            token=pending_position["token"],
            attempt_id=pending_position["attempt_id"],
            segment_id=pending_position["segment_id"],
            cell=_attempt(pass_state, pending_position["attempt_id"])["cell"],
            reason=pending_position["check"]["reason"][:1024],
        )
    reference = pass_state["reference"]
    return dict(
        {key: state[key] for key in (
            "format", "version", "run_id", "segment_id", "revision", "orientation",
            "current_cell", "inspection_cell", "paused", "finished", "active_pass",
        )},
        definition={key: value for key, value in state["definition"].items()
                    if key not in {"cells", "target_json"}},
        viewed_pass=pass_state["id"],
        pass_count=len(state["passes"]),
        source_count=len(state["sources"]),
        attempt_count=len(pass_state["attempts"]),
        total_attempt_count=sum(len(item["attempts"]) for item in state["passes"]),
        pending_context=pending_context,
        pending_position=pending_position,
        reference={key: reference[key] for key in ("qualified", "digest")}
        if reference is not None else None,
        cells=cells,
        coverage=coverage,
        total=len(cells),
        incomplete=coverage < len(cells),
    )


def attempt_details(state, attempt_id, *, pass_id=None):
    """Return one exact retained attempt, optionally guarded by its pass identity."""
    state = validate_state(state)
    passes = state["passes"] if pass_id is None else [_pass(state, pass_id)]
    for pass_state in passes:
        for attempt in pass_state["attempts"]:
            if attempt["id"] == attempt_id:
                return dict(deepcopy(attempt), pass_id=pass_state["id"])
    raise ValueError("Unknown chart attempt")


def history(state, pass_id, cell, *, offset=0, limit=50):
    """Page chronological attempt identities for one explicit pass and cell."""
    state = validate_state(state)
    if (
        type(offset) is not int or offset < 0 or type(limit) is not int
        or not 1 <= limit <= 50 or type(cell) is not int
        or not 1 <= cell <= len(state["definition"]["cells"])
    ):
        raise ValueError("Use a valid cell, nonnegative offset and page size 1–50")
    if pass_id is None:
        raise ValueError("Unknown chart pass")
    chosen = _pass(state, pass_id)
    attempts = [item for item in chosen["attempts"] if item["cell"] == cell]
    return dict(total=len(attempts), offset=offset, attempts=[
        {key: item[key] for key in ("id", "segment_id", "operation_id", "status")}
        for item in attempts[offset:offset + limit]
    ])


def decision_details(state):
    """Return independent full context, reference and pending placement packets."""
    state = validate_state(state)
    return deepcopy(dict(
        pending_context=state["pending_context"],
        passes=[dict(
            id=item["id"], context=item["context"], reference=item["reference"],
            pending_position=item["pending_position"],
        ) for item in state["passes"]],
    ))
