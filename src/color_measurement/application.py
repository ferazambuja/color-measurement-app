"""Durable descriptor-authorized batches over an explicitly bound polled frontend.

This module admits commands and retains outcomes independently of a renderer.
It owns no acquisition, instrument policy or scientific conversion.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import stat
import threading
import unicodedata
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import UUID, uuid4

from . import (
    chart_context,
    chart_science,
    chart_sources,
    chart_state,
    exports,
    operation_exports,
    ti3_exports,
)
from .exports import (
    PublicationResult,
    _folder_lock,
    _OwnedFileReplacedDuringOpen,
    _preserve_publication_failure,
    _publication_cleanup_reason,
    _publish_owned_file,
    _read_regular,
    _require_publication_cleanup,
)
from .frontend import _validate_instruction_payload, _validate_presentation_payload
from .instrument_frontend import (
    InstrumentFrontend,
    _validate_descriptor_request,
    capability_identity,
    descriptor_context,
)
from .json_values import unique_object
from .models import XYZ, DeviceIdentity
from .operation_records import result_from_json, result_to_json
from .operations import OperationBusyError
from .presentation import RESOURCE, resolve_white
from .serial_frontend import DirectSerialFrontend

_MAX_COMMAND_BYTES = 131072
# Retain uncertain acquisition owners until process shutdown; never release their lease.
_UNCERTAIN_OWNERS = []
_COMPANION_FILES = {
    "mat": "measurements.mat", "csv": "measurements.csv",
    "chart_selected_csv": "chart-selected.csv",
}


def _json(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2, sort_keys=True) + "\n"


def _metadata_summary(value):
    """Bound displayed note text without changing the owned full metadata."""
    return dict(
        ((key, item) for key, item in value.items() if key != "notes"),
        notes_preview=value["notes"][:256],
        notes_length=len(value["notes"]),
    )


def _digest(payload):
    return hashlib.sha256(payload).hexdigest()


def _diagnostic_settings(value):
    _fields(value, {"debug_level", "limit_bytes"})
    if (type(value["debug_level"]) is not int or not 0 <= value["debug_level"] <= 8
            or type(value["limit_bytes"]) is not int
            or not 1 <= value["limit_bytes"] <= 1048576):
        raise ValueError("Native diagnostics need integer level 0..8 and byte cap 1..1048576")
    return value


def _diagnostic_metadata(value, command_id):
    if value is None:
        return None
    _fields(value, {
        "schema_version", "kind", "state", "path", "debug_level", "limit_bytes",
        "received_bytes", "retained_bytes", "written_bytes", "sha256", "file_sha256",
        "truncated", "drain_complete", "file_complete", "worker_started", "error",
        "sink_stopped", "retention", "segments",
    })
    _diagnostic_settings({key: value[key] for key in ("debug_level", "limit_bytes")})
    if (type(value["schema_version"]) is not int or value["schema_version"] != 2
            or value["kind"] != "native_stderr" or value["debug_level"] == 0
            or not isinstance(value["state"], str)
            or value["state"] not in {
                "prepared", "capturing", "not_started", "complete", "incomplete",
            }
            or value["path"] != f"diagnostic-{command_id}.log"):
        raise ValueError("Native diagnostic metadata belongs to another reserved capture")
    for key in ("received_bytes", "retained_bytes", "written_bytes"):
        if type(value[key]) is not int or value[key] < 0:
            raise ValueError("Native diagnostic byte counts must be nonnegative integers")
    if not (value["written_bytes"] <= value["retained_bytes"] <= value["limit_bytes"]
            and value["retained_bytes"] <= value["received_bytes"]):
        raise ValueError("Native diagnostic byte counts exceed their retained artifact")
    received, limit = value["received_bytes"], value["limit_bytes"]
    if value["retention"] != "head_tail" or not isinstance(value["segments"], list):
        raise ValueError("Native diagnostics require declared head/tail source segments")
    for segment in value["segments"]:
        _fields(segment, {"offset", "length"})
        if (type(segment["offset"]) is not int or segment["offset"] < 0
                or type(segment["length"]) is not int or segment["length"] <= 0):
            raise ValueError("Native diagnostic source segments need integer offsets and lengths")
    if received <= limit:
        expected = [dict(offset=0, length=received)] if received else []
    else:
        head, tail = limit // 2, limit - limit // 2
        expected = ([dict(offset=0, length=head)] if head else []) + [
            dict(offset=received - tail, length=tail),
        ]
    pending = value["state"] == "capturing" and value["sha256"] is None
    if (value["retained_bytes"] != min(received, limit)
            or _json(value["segments"]) != _json([] if pending else expected)):
        raise ValueError("Native diagnostic source segments differ from the fixed retention policy")
    for key in ("truncated", "drain_complete", "file_complete", "worker_started", "sink_stopped"):
        if type(value[key]) is not bool:
            raise ValueError("Native diagnostic status must contain boolean facts")
    if value["truncated"] != (value["received_bytes"] > value["retained_bytes"]):
        raise ValueError("Native diagnostic truncation differs from its retained original")
    for key in ("sha256", "file_sha256"):
        digest = value[key]
        if digest is not None and (not isinstance(digest, str) or len(digest) != 64
                                  or any(letter not in "0123456789abcdef" for letter in digest)):
            raise ValueError("Native diagnostic digest must be lowercase SHA-256")
    if value["error"] is not None:
        _text(value["error"], 65536)
    if value["state"] in {"prepared", "not_started"} and (
            value["received_bytes"] or value["retained_bytes"] or value["written_bytes"]
            or value["sha256"] is not None or value["file_sha256"] is not None
            or value["drain_complete"] or value["file_complete"] or value["worker_started"]):
        raise ValueError("Unstarted native diagnostics cannot claim captured or executed work")
    if value["state"] in {"complete", "incomplete"} and value["sha256"] is None:
        raise ValueError("Closed native diagnostics need their exact original byte digest")
    if value["state"] == "complete" and (not value["drain_complete"]
            or not value["file_complete"] or value["error"] is not None):
        raise ValueError("Complete native diagnostics require confirmed draining and file closure")
    if value["file_complete"] and (value["written_bytes"] != value["retained_bytes"]
                                    or value["file_sha256"] != value["sha256"]):
        raise ValueError("Complete native diagnostic file differs from its original bytes")
    if not value["sink_stopped"] and (value["file_complete"] or value["file_sha256"] is not None):
        raise ValueError("Unfinished diagnostic sink cannot claim a complete or stable file")
    return value


def _diagnostic_bytes(payload, metadata):
    if (not isinstance(payload, bytes) or len(payload) != metadata["retained_bytes"]
            or _digest(payload) != metadata["sha256"]):
        raise ValueError("Native diagnostic bytes differ from the retained original")
    if (metadata["file_sha256"] is not None
            and _digest(payload[:metadata["written_bytes"]]) != metadata["file_sha256"]):
        raise ValueError("Native diagnostic initial file differs from its retained artifact prefix")
    return payload


def _diagnostic_review(folder, metadata, read=None):
    read = read or (lambda name: _read_regular(folder / name))
    if metadata is None:
        return dict(state="off", reason=None)
    if metadata["state"] == "not_started":
        return dict(state="not_started", reason=metadata["error"])
    if metadata["sha256"] is None:
        return dict(state="unavailable", reason="Native diagnostic capture has no closed original")
    try:
        _diagnostic_bytes(read(metadata["path"]), metadata)
    except (OSError, ValueError) as error:
        return dict(state="unavailable", reason=str(error))
    return dict(state="authenticated", reason=None)


def _load(payload):
    def invalid(value):
        raise ValueError(f"Invalid JSON number: {value}")

    return json.loads(payload, object_pairs_hook=unique_object, parse_constant=invalid)


def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Identity must be a canonical UUID")
    return value


def _text(value, limit):
    if not isinstance(value, str) or len(value) > limit or "\0" in value:
        raise ValueError(f"Expected text of at most {limit} characters without NUL")
    return value


def _sample_label(value):
    _text(value, 1024)
    if any(unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"} for character in value):
        raise ValueError("Sample labels must be single-line text without control or "
                         "format characters")
    return value


def _fields(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"Expected fields: {sorted(fields)}")


def _numbered_name(name):
    match = re.fullmatch(r"(.+?)\s+(#?)([0-9]+)", name)
    if match is None:
        return None
    stem, marker, decimal = match.groups()
    return stem, marker, int(decimal), len(decimal)


def _following_label(stem, marker, ordinal, width):
    """Format the next numbered label; refuse one the label bound cannot hold."""
    following = f"{stem} {marker}{ordinal:0{width}d}"
    if len(following) > 1024:
        raise ValueError(
            "Next sample label exceeds 1,024 characters; shorten its stem or disable numbering"
        )
    return _sample_label(following)


def _draft(value):
    _fields(value, {"id", "version", "name", "notes", "options", "number_samples"})
    _uuid(value["id"])
    _sample_label(value["name"])
    _text(value["notes"], 65536)
    if type(value["version"]) is not int or value["version"] < 0:
        raise ValueError("Invalid draft version")
    if not value["name"].strip() or not isinstance(value["options"], dict):
        raise ValueError("Draft needs a nonempty label and object options")
    if type(value["number_samples"]) is not bool:
        raise ValueError("Draft numbering mode must be a boolean")
    numbered = _numbered_name(value["name"]) if value["number_samples"] else None
    if value["number_samples"] and (numbered is None or numbered[2] < 1):
        raise ValueError("Numbered draft needs a resolved positive ordinal")
    if numbered is not None:
        # The immediate successor must be representable; the live owner also
        # skips consumed ordinals, which a manifest alone cannot name.
        stem, marker, ordinal, width = numbered
        _following_label(stem, marker, ordinal + 1, width)


def _reading_pair(value):
    _fields(value, {"operation_id", "observation_id"})
    _uuid(value["operation_id"])
    _text(value["observation_id"], 1024)
    if not value["observation_id"]:
        raise ValueError("Reading identity must be nonempty")
    return value["operation_id"], value["observation_id"]


def _comparison_descriptor(readings, white):
    if isinstance(white, dict) and white.get("kind") == "retained-reading":
        _fields(white, {"kind", "operation_id", "observation_id"})
        pair = _reading_pair({key: white[key] for key in ("operation_id", "observation_id")})
        white = dict(kind="retained-reading", source_id="/".join(pair))
    try:
        return _load(_json(resolve_white(readings, white)))
    except (KeyError, TypeError) as error:
        raise ValueError("Declared white source lacks valid native XYZ") from error


def _comparison(value, annotation_ids):
    _fields(value, {"version", "reference", "white", "white_descriptor"})
    if type(value["version"]) is not int or value["version"] < 0:
        raise ValueError("Invalid comparison version")
    if value["reference"] is not None and _reading_pair(value["reference"]) not in annotation_ids:
        raise ValueError("Comparison reference has no accepted reading identity")
    white = value["white"]
    if white is None:
        if value["white_descriptor"] is not None:
            raise ValueError("Absent white must not claim resolved assumptions")
    elif not isinstance(white, dict):
        raise ValueError("White declaration must be an object or null")
    elif white.get("kind") == "retained-reading":
        _fields(white, {"kind", "operation_id", "observation_id"})
        pair = _reading_pair({key: white[key] for key in ("operation_id", "observation_id")})
        if pair not in annotation_ids:
            raise ValueError("Declared white has no accepted reading identity")
        descriptor = value["white_descriptor"]
        _fields(
            descriptor,
            {"kind", "source_id", "xyz", "reference_y", "unit", "scale", "observer", "illuminant"},
        )
        if descriptor["kind"] != "retained-reading" or descriptor["source_id"] != "/".join(pair):
            raise ValueError("Declared white descriptor belongs to another source")
        if not isinstance(descriptor["xyz"], list):
            raise ValueError("Declared white descriptor needs an XYZ array")
        native = XYZ(
            tuple(descriptor["xyz"]),
            **{key: descriptor[key] for key in ("unit", "scale", "observer", "illuminant")},
        )
        if (
            any(value <= 0 for value in native.values)
            or type(descriptor["reference_y"]) not in (int, float)
            or descriptor["reference_y"] != native.values[1]
        ):
            raise ValueError("Declared white descriptor needs positive XYZ and its native Y")
    elif value["white_descriptor"] != _comparison_descriptor({}, white):
        raise ValueError("Declared white assumptions differ from their validated declaration")


def _batch_mode(descriptor, mode):
    """Authenticate the frozen instrument descriptor and its advertised workflow offers."""
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("schema_version") != 1
        or type(descriptor.get("schema_version")) is not int
        or descriptor.get("contract") not in {
            "argyll_generic_instrument_v1", "direct_serial_instrument_v1",
        }
        or descriptor.get("provenance") not in {"physical", "simulated", "replay"}
    ):
        raise ValueError("Batch requires a current instrument descriptor")
    try:
        device = DeviceIdentity(**descriptor["device"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Manifest requires a valid frozen DeviceIdentity") from error
    if device.device_id != descriptor.get("device_id"):
        raise ValueError("Frozen device identity must match the descriptor")
    content = {key: item for key, item in descriptor.items() if key != "capability_id"}
    if descriptor.get("capability_id") != capability_identity(content):
        raise ValueError("Frozen descriptor capability identity does not match its content")
    modes = descriptor.get("modes")
    if not isinstance(mode, str) or not isinstance(modes, list):
        raise ValueError("Batch mode must name an advertised mode")
    chosen = [entry for entry in modes if isinstance(entry, dict) and entry.get("mode") == mode]
    if len(chosen) != 1 or chosen[0].get("available") is not True:
        raise ValueError("Batch mode must name one available mode")
    workflows = chosen[0].get("workflows")
    if not isinstance(workflows, list) or any(not isinstance(item, dict) for item in workflows):
        raise ValueError("Batch mode requires advertised workflows")
    offered = {item.get("workflow"): item for item in workflows}
    if len(offered) != len(workflows):
        raise ValueError("Batch mode has duplicate advertised workflows")
    if not any(item.get("available") is True for item in workflows):
        raise ValueError("Batch mode must offer an available workflow")
    return offered


def _batch_request(descriptor, mode, draft, workflow=None, *, patch_ids=None, layout=None):
    offered = _batch_mode(descriptor, mode)
    if workflow is None:
        workflow = "spot" if offered.get("spot", {}).get("available") is True else "mode_snapshot"
    request = dict(
        schema_version=1,
        device_id=descriptor["device_id"],
        capability_id=descriptor["capability_id"],
        mode=mode,
        workflow=workflow,
        patch_ids=([draft["id"]] if workflow == "spot" else [])
        if patch_ids is None else deepcopy(patch_ids),
        options=deepcopy(draft["options"]),
        layout={} if layout is None else deepcopy(layout),
        event_contract="retained_patch_v1",
    )
    _validate_descriptor_request(request, descriptor)
    for identity in request["patch_ids"]:
        _sample_label(identity)
    return request


def _manifest(payload):
    """Validate the current manifest, including reserved paths and contexts."""
    value = _load(payload)
    if isinstance(value, dict) and "schema_version" in value:
        version = value["schema_version"]
        if type(version) is not int or version != 5:
            raise ValueError(f"Unsupported batch manifest schema {version!r}; expected 5")
    _fields(
        value,
        {
            "schema_version",
            "batch_id",
            "batch_name",
            "batch_notes",
            "revision",
            "status",
            "context_id",
            "descriptor",
            "mode",
            "draft",
            "operations",
            "annotations",
            "destinations",
            "destination_issues",
            "shutdown",
            "comparison",
            "chart",
            "native_diagnostics",
        },
    )
    _text(value["batch_name"], 1024)
    _text(value["batch_notes"], 65536)
    if not value["batch_name"].strip():
        raise ValueError("Batch name must contain text")
    _uuid(value["batch_id"])
    _uuid(value["context_id"])
    if type(value["revision"]) is not int or value["revision"] < 0:
        raise ValueError("Invalid manifest revision")
    if not isinstance(value["status"], str) or value["status"] not in {
        "open",
        "finished",
        "abandoned",
    }:
        raise ValueError("Invalid batch state")
    descriptor = value["descriptor"]
    _batch_mode(descriptor, value["mode"])
    _draft(value["draft"])
    _diagnostic_settings(value["native_diagnostics"])
    if not isinstance(value["operations"], list) or not isinstance(value["annotations"], list):
        raise ValueError("Operations and annotations must be arrays")
    identities, operation_ids, earlier = set(), set(), {}
    for entry in value["operations"]:
        _fields(
            entry,
            {
                "command_id",
                "path",
                "context_id",
                "request",
                "draft",
                "operation_id",
                "state",
                "sha256",
                "reason",
                "recovery",
                "repeat_of",
                "native_diagnostics",
            },
        )
        _uuid(entry["command_id"])
        _diagnostic_metadata(entry["native_diagnostics"], entry["command_id"])
        if entry["command_id"] in identities:
            raise ValueError("Duplicate operation intent")
        identities.add(entry["command_id"])
        if entry["path"] != f"operation-{entry['command_id']}.json":
            raise ValueError("Operation path must match its reserved command UUID")
        if entry["context_id"] != value["context_id"]:
            raise ValueError("Intent context belongs to another batch")
        request = entry["request"]
        _fields(
            request,
            {
                "schema_version",
                "device_id",
                "capability_id",
                "mode",
                "workflow",
                "patch_ids",
                "options",
                "layout",
                "event_contract",
            },
        )
        if (
            not isinstance(request, dict)
            or request.get("device_id") != descriptor["device_id"]
            or request.get("mode") != value["mode"]
            or not isinstance(request["workflow"], str)
            or request.get("capability_id") != descriptor["capability_id"]
            or type(request["schema_version"]) is not int
            or request["schema_version"] != 1
            or request["event_contract"] != "retained_patch_v1"
        ):
            raise ValueError("Intent request does not match frozen batch context")
        _validate_descriptor_request(request, descriptor)
        for identity in request["patch_ids"]:
            _sample_label(identity)
        _draft(entry["draft"])
        if (_json(request.get("options")) != _json(entry["draft"]["options"])
                or (request["workflow"] == "spot"
                    and request["patch_ids"] != [entry["draft"]["id"]])):
            raise ValueError("Intent request does not match frozen draft")
        if not isinstance(entry["state"], str) or entry["state"] not in {
            "reserved",
            "started",
            "accepted",
            "terminal_unknown",
        }:
            raise ValueError("Invalid operation intent state")
        if entry["state"] == "terminal_unknown":
            if not isinstance(entry["reason"], str) or not entry["reason"]:
                raise ValueError("Unknown terminal outcome requires its explicit reason")
        elif entry["reason"] is not None:
            raise ValueError("Known intent cannot claim an unknown-terminal reason")
        _uuid(entry["operation_id"])
        recovery = entry["recovery"]
        if recovery is not None:
            if entry["state"] != "terminal_unknown":
                raise ValueError("Recovery evidence requires an unknown terminal outcome")
            _fields(recovery, {"path", "sha256"})
            if recovery["path"] != f"recovery-{entry['command_id']}.json":
                raise ValueError("Recovery path must match its operation command")
            if (
                not isinstance(recovery["sha256"], str)
                or len(recovery["sha256"]) != 64
                or any(value not in "0123456789abcdef" for value in recovery["sha256"])
            ):
                raise ValueError("Recovery requires its exact byte digest")
        if entry["operation_id"] in operation_ids:
            raise ValueError("Duplicate retained operation identity")
        operation_ids.add(entry["operation_id"])
        repeat = entry["repeat_of"]
        if repeat is not None:
            _fields(repeat, {"operation_id", "observation_id"})
            _uuid(repeat["operation_id"])
            _text(repeat["observation_id"], 1024)
            source = earlier.get(repeat["operation_id"])
            if (
                source is None
                or source["state"] != "accepted"
                or source["request"]["workflow"] != "spot"
                or request["workflow"] != "spot"
                or _json(entry["draft"]) != _json(dict(source["draft"], notes=""))
            ):
                raise ValueError("Repeat must preserve an earlier accepted spot's frozen target")
        earlier[entry["operation_id"]] = entry
        if entry["state"] == "accepted":
            if not isinstance(entry["sha256"], str):
                raise ValueError("Accepted operation needs its byte digest")
            if len(entry["sha256"]) != 64 or any(
                c not in "0123456789abcdef" for c in entry["sha256"]
            ):
                raise ValueError("Invalid record digest")
        elif entry["sha256"] is not None:
            raise ValueError("Unaccepted operation cannot bind an accepted digest")
    annotation_ids = set()
    accepted_ids = {
        entry["operation_id"] for entry in value["operations"] if entry["state"] == "accepted"
    }
    for item in value["annotations"]:
        _fields(item, {"operation_id", "observation_id", "name", "notes", "version"})
        _uuid(item["operation_id"])
        if item["operation_id"] not in accepted_ids:
            raise ValueError("Annotation has no operation identity")
        _text(item["observation_id"], 1024)
        _sample_label(item["name"])
        _text(item["notes"], 65536)
        key = item["operation_id"], item["observation_id"]
        if key in annotation_ids or type(item["version"]) is not int or item["version"] < 0:
            raise ValueError("Invalid annotation identity or version")
        annotation_ids.add(key)
    for entry in value["operations"]:
        repeat = entry["repeat_of"]
        if (
            repeat is not None
            and (repeat["operation_id"], repeat["observation_id"]) not in annotation_ids
        ):
            raise ValueError("Repeat source reading identity is not accepted")
    _comparison(value["comparison"], annotation_ids)
    # Destination history retains its originating OS syntax. The live owner
    # independently chooses and authenticates each current writable folder.
    if (
        not isinstance(value["destinations"], list)
        or not value["destinations"]
        or any(
            not isinstance(path, str) or not (
                PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute())
            for path in value["destinations"]
        )
    ):
        raise ValueError("Destinations must contain explicit absolute paths")
    if not isinstance(value["destination_issues"], dict):
        raise ValueError("Destination issues must be an object")
    for path, reason in value["destination_issues"].items():
        if path not in value["destinations"]:
            raise ValueError("Destination issue must identify a retained destination")
        _text(reason, 16384)
    if value["shutdown"] is not None:
        _fields(value["shutdown"], {"reason", "cleanup_confirmed", "instrument_state", "discarded"})
        _text(value["shutdown"]["reason"], 1024)
        if type(value["shutdown"]["cleanup_confirmed"]) is not bool:
            raise ValueError("Shutdown needs an explicit cleanup fact")
        if value["status"] != "abandoned":
            raise ValueError("Shutdown outcome requires abandoned status")
    elif value["status"] == "abandoned":
        raise ValueError("Abandoned status requires its shutdown outcome")
    chart = value["chart"]
    if chart is not None:
        chart_state.validate_state(chart)
        if chart["segment_id"] != value["batch_id"]:
            raise ValueError("Chart segment must identify its owning batch")
        local = {
            attempt["command_id"]: attempt
            for chart_pass in chart["passes"]
            for attempt in chart_pass["attempts"]
            if attempt["segment_id"] == value["batch_id"]
        }
        by_command = {entry["command_id"]: entry for entry in value["operations"]}
        for identity, attempt in local.items():
            entry = by_command.get(identity)
            if (entry is None or entry["operation_id"] != attempt["operation_id"]
                    or _json(entry["request"]) != _json(attempt["request"])
                    or entry["request"]["workflow"] != "spot"):
                raise ValueError("Chart attempt must bind its exact local spot intent")
        if value["status"] == "finished" and not chart["finished"]:
            raise ValueError("Finished batch requires an explicitly finished chart")
    return value


def _check_result(result, entry, descriptor):
    request = _load(result.request_json)
    if (
        result.operation_id != entry["operation_id"]
        or _json(request) != _json(entry["request"])
        or result.device_id != descriptor["device_id"]
        or result.mode != request["mode"]
        or result.workflow != request["workflow"]
        or result.provenance != descriptor["provenance"]
        or asdict(result.device) != descriptor["device"]
    ):
        raise ValueError("Retained operation does not match its reserved identity/context")


def _check_recovery(payload, entry):
    """Validate exact raw failure evidence without promoting it to a reading."""
    failure = _load(payload)
    _fields(failure, {
        "schema_version", "command_id", "operation_id", "request", "result_json",
        "reason", "software_stopped", "instrument_state",
    })
    if (type(failure["schema_version"]) is not int or failure["schema_version"] != 1
            or not isinstance(failure["result_json"], str)
            or failure["software_stopped"] is not None
            and type(failure["software_stopped"]) is not bool
            or not isinstance(failure["instrument_state"], str)
            or failure["instrument_state"] not in {
                "unknown", "not_applicable", "confirmed_ready",
            }):
        raise ValueError("Malformed terminal recovery evidence")
    if (failure["command_id"] != entry["command_id"]
            or failure["operation_id"] != entry["operation_id"]
            or _json(failure["request"]) != _json(entry["request"])
            or failure["reason"] != entry["reason"]):
        raise ValueError("Terminal failure evidence belongs to another intent")
    return failure


class _SourceFiles(dict):
    """Read only explicitly referenced content-addressed source files."""

    def __init__(self, folder, read=None):
        super().__init__()
        self.folder, self.read = folder, read

    def __missing__(self, digest):
        if (not isinstance(digest, str) or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)):
            raise ValueError("Source file needs a canonical SHA-256 identity")
        path = self.folder / f"source-{digest}.json"
        if self.read is None and path.lstat().st_size > chart_sources.MAX_BLOB_BYTES:
            raise ValueError("Chart source bytes exceed 32 MiB")
        payload = self.read(path.name) if self.read is not None else _read_regular(path)
        if len(payload) > chart_sources.MAX_BLOB_BYTES:
            raise ValueError("Chart source bytes exceed 32 MiB")
        self[digest] = payload
        return payload


def _check_chart_attempt(attempt, result, descriptor):
    """Reconstruct chart context from its original typed native operation."""
    expected = chart_context.intended(descriptor, _load(result.request_json))
    if _json(attempt["intended_context"]) != _json(expected):
        raise ValueError("Chart intended context differs from its original request")
    if attempt["accepted_context"] is not None:
        if len(result.patches) != 1:
            raise ValueError("Chart accepted context needs one exact original reading")
        actual = chart_context.accepted(
            descriptor, _load(result.request_json), result,
            result.patches[0].observation.observation_id,
        )
        if _json(actual) != _json(attempt["accepted_context"]):
            raise ValueError("Chart accepted context differs from original native evidence")


def _chart_reading_key(reference):
    return tuple(reference[key] for key in (
        "segment_id", "operation_id", "observation_id", "sample_id",
    ))


def _authenticate_chart(manifest, outcomes, folder, read=None):
    read = read or (lambda name: _read_regular(folder / name))
    chart = manifest["chart"]
    if chart is None:
        return None, {}
    try:
        blobs = _SourceFiles(folder, read)
        for entry in manifest["operations"]:
            reference = entry["recovery"] if entry["state"] == "terminal_unknown" else entry
            if reference is not None and reference.get("sha256") is not None:
                blobs[reference["sha256"]] = read(reference["path"])
            diagnostic = entry["native_diagnostics"]
            if diagnostic is not None and diagnostic["sha256"] is not None:
                blobs[diagnostic["sha256"]] = _diagnostic_bytes(
                    read(diagnostic["path"]), diagnostic,
                )
        originals = chart_sources.authenticate(
            manifest, blobs, validate_manifest=_manifest,
            check_result=_check_result, check_recovery=_check_recovery,
        )
        results = dict(originals["operations"])
        for outcome in outcomes:
            if outcome["review_state"] == "accepted":
                results[manifest["batch_id"], outcome["operation_id"]] = result_from_json(
                    _json(outcome["result"])
                )
        segments = dict(originals["segments"], **{manifest["batch_id"]: manifest})
        wrappers = {}
        native_wrappers = {}
        for chart_pass in chart["passes"]:
            completed = [attempt for attempt in chart_pass["attempts"]
                         if attempt["status"] == "completed"
                         and attempt["accepted_context"] is not None]
            if completed:
                baseline = chart_pass["context"]
                latest = completed[-1]["accepted_context"]
                if (baseline["phase"] == "accepted"
                        and _json(baseline) != _json(latest)):
                    raise ValueError(
                        "Chart pass baseline differs from its original accepted context"
                    )
                for key in latest["required_material"]:
                    original = latest["material"].get(key)
                    claimed = baseline["material"].get(key)
                    if original is not None and original["certainty"] != "unknown":
                        if (claimed is None or claimed["certainty"] == "unknown"
                                or _json(claimed["value"]) != _json(original["value"])):
                            raise ValueError(
                                "Chart pass lost an established original material fact"
                            )
            for attempt in chart_pass["attempts"]:
                key = attempt["segment_id"], attempt["operation_id"]
                result = results.get(key)
                if result is None:
                    if attempt["status"] == "started":
                        raise ValueError("Chart attempt has no authenticated terminal outcome")
                    if key not in originals["recoveries"] and not any(
                        item["operation_id"] == attempt["operation_id"]
                        and item["review_state"] == "terminal_unknown" for item in outcomes
                    ):
                        raise ValueError("Chart attempt's original failure evidence is unavailable")
                    continue
                _check_chart_attempt(attempt, result, segments[attempt["segment_id"]]["descriptor"])
                for ref in attempt["readings"]:
                    patches = [patch for patch in result.patches
                               if patch.observation.observation_id == ref["observation_id"]
                               and patch.observation.sample_id == ref["sample_id"]]
                    if len(patches) != 1:
                        raise ValueError("Chart reference differs from its original observation")
                    wrappers[_chart_reading_key(ref)] = dict(
                        cell_index=attempt["cell"], observation=asdict(patches[0].observation),
                        reading_ref=ref, accepted_context=attempt["accepted_context"],
                    )
                    native_wrappers[_chart_reading_key(ref)] = dict(
                        wrappers[_chart_reading_key(ref)], observation=patches[0].observation,
                    )
            attempts = {item["id"]: item for item in chart_pass["attempts"]}
            for identity in chart_pass["selected_attempts"]:
                if identity is None:
                    continue
                original = attempts[identity]["accepted_context"]
                baseline = chart_pass["context"]
                if original is None or chart_state.context_difference(original, baseline):
                    raise ValueError(
                        "Selected chart reading has incompatible original material facts"
                    )
                # Intended continuation may declare previously unknown facts.
                # It cannot delete any fact already established on an original.
                for key in original["required_material"]:
                    fact = original["material"][key]
                    claimed = baseline["material"].get(key)
                    if fact["certainty"] != "unknown" and (
                            claimed is None or claimed["certainty"] == "unknown"
                            or _json(claimed["value"]) != _json(fact["value"])):
                        raise ValueError("Selected chart reading lost an original material fact")
            reference = chart_pass["reference"]
            if reference is not None:
                chart_science.validate_position_reference(
                    reference, chart["definition"], observer_csv=RESOURCE.read_bytes(),
                )
                for cell in reference["cells"]:
                    source = cell["source"]
                    if source is not None and _json(source) != _json(
                        wrappers.get(_chart_reading_key(source["reading_ref"]))
                    ):
                        raise ValueError(
                            "Measured reference does not bind its original chart reading"
                        )
            for attempt in chart_pass["attempts"]:
                if (attempt["result_status"] != "completed"
                        or attempt["accepted_context"] is None):
                    continue
                check = attempt["position_check"]
                if check is None or len(attempt["readings"]) != 1:
                    raise ValueError("Completed chart reading lost its position-check outcome")
                digest = check.get("reference_digest")
                frozen = reference if digest is not None else None
                if digest is not None and (frozen is None or frozen["digest"] != digest):
                    raise ValueError("Position check lost its frozen reference")
                expected = chart_science.chart_position_check(
                    frozen, chart["definition"],
                    native_wrappers[_chart_reading_key(attempt["readings"][0])],
                    observer_csv=RESOURCE.read_bytes(),
                )
                if _json(check) != _json(expected):
                    raise ValueError(
                        "Position check differs from original native/reference evidence"
                    )
        current_hashes = {reference["sha256"] for entry in manifest["operations"]
                          for reference in [entry["recovery"]
                                            if entry["state"] == "terminal_unknown" else entry]
                          if reference is not None and reference.get("sha256") is not None}
        current_hashes.update(entry["native_diagnostics"]["sha256"]
                              for entry in manifest["operations"]
                              if entry["native_diagnostics"] is not None)
        source_files = [dict(path=f"source-{digest}.json", sha256=digest)
                        for digest in sorted(set(originals["blobs"]) - current_hashes)]
        return dict(
            available=True, reason=None, summary=chart_state.view(chart), source_files=source_files,
        ), originals
    except (OSError, ValueError, UnicodeError) as error:
        return dict(
            available=False, reason=str(error), summary=chart_state.view(chart), source_files=[],
        ), {}


def review_saved(folder: str | Path) -> dict:
    """Inspect only referenced files, without locking, writing or resuming work.

    Concurrent mutable publication can make this view incomplete. Finished
    batches provide a stable canonical view; optional receipts remain unverified
    until the export owner authenticates their files on an explicit retry.
    """
    folder = Path(folder).absolute()
    if not stat.S_ISDIR(folder.lstat().st_mode):
        raise ValueError("Batch directory must be an ordinary directory, not a link")
    folder = folder.resolve(strict=True)
    def read(name):
        path = folder / name
        if name.startswith("source-") and path.lstat().st_size > chart_sources.MAX_BLOB_BYTES:
            raise ValueError("Chart source bytes exceed 32 MiB")
        return _read_regular(path)
    return _review_inputs(folder, read("batch.json"), read)


def review_captured(folder, files, *, unreadable=None):
    """Validate frozen original bytes through the same saved-source review owner."""
    def read(name):
        try:
            return files[name]
        except KeyError as error:
            if name in (unreadable or {}):
                raise OSError(f"Declared source file is unavailable: {name}: "
                              f"{unreadable[name]['reason']}") from error
            raise FileNotFoundError(f"Declared source file is missing: {name}") from error
    return _review_inputs(Path(folder), read("batch.json"), read)


def _review_inputs(folder, payload, read):
    manifest = _manifest(payload)
    outcomes, records = [], []
    observations = set()
    for entry in manifest["operations"]:
        state, reason, result = "unknown", None, None
        try:
            if entry["state"] == "terminal_unknown":
                recovery = entry["recovery"]
                if recovery is None:
                    raise ValueError("Terminal failure evidence is unsaved")
                raw = read(recovery["path"])
                if _digest(raw) != recovery["sha256"]:
                    raise ValueError("Terminal recovery digest does not match")
                failure = _check_recovery(raw, entry)
                outcomes.append(
                    dict(
                        entry,
                        review_state="terminal_unknown",
                        review_reason=entry["reason"],
                        result=None,
                        recovery_result=failure,
                    )
                )
                continue
            raw = read(entry["path"])
            result = result_from_json(raw.decode("utf-8"))
            _check_result(result, entry, manifest["descriptor"])
            if entry["state"] == "accepted":
                if _digest(raw) != entry["sha256"]:
                    raise ValueError("Referenced operation byte digest does not match")
                state = "accepted"
                records.append(result)
                observations.update(
                    (result.operation_id, patch.observation.observation_id)
                    for patch in result.patches
                )
            else:
                state = "found_unaccepted"
        except (OSError, ValueError, UnicodeError) as error:
            reason = str(error)
        outcomes.append(
            dict(
                entry,
                review_state=state,
                review_reason=reason,
                result=_load(result_to_json(result)) if result else None,
            )
        )
    annotation_keys = {
        (item["operation_id"], item["observation_id"]) for item in manifest["annotations"]
    }
    for outcome in outcomes:
        if outcome["review_state"] == "accepted":
            expected_keys = {
                (outcome["operation_id"], patch["observation"]["observation_id"])
                for patch in outcome["result"]["patches"]
            }
            actual_keys = {key for key in annotation_keys if key[0] == outcome["operation_id"]}
            if actual_keys != expected_keys:
                outcome["review_state"] = "unknown"
                outcome["review_reason"] = (
                    "Reading annotations do not match the canonical operation"
                )
    for outcome in outcomes:
        if any(key[0] == outcome["operation_id"] and key not in observations
               for key in annotation_keys):
            # An unavailable record cannot establish an annotation's identity.
            outcome["review_state"] = "unknown"
            outcome["review_reason"] = "; ".join(filter(None, (
                outcome["review_reason"], "Annotation identity cannot be authenticated",
            )))
    by_operation = {item["operation_id"]: item for item in outcomes}
    for outcome in outcomes:
        repeat = outcome["repeat_of"]
        if repeat is None:
            continue
        source = by_operation[repeat["operation_id"]]
        if (
            source["review_state"] != "accepted"
            or source["result"]["status"] != "completed"
            or not any(
                patch["observation"]["observation_id"] == repeat["observation_id"]
                for patch in source["result"]["patches"]
            )
        ):
            outcome["review_state"] = "unknown"
            outcome["review_reason"] = "; ".join(filter(None, (
                outcome["review_reason"], "Repeat source reading cannot be authenticated",
            )))
    comparison = manifest["comparison"]
    comparison_reason = None
    try:
        for pair in (comparison["reference"], comparison["white"]):
            if pair is None or "operation_id" not in pair:
                continue
            source = by_operation[pair["operation_id"]]
            if source["review_state"] != "accepted" or not any(
                patch["observation"]["observation_id"] == pair["observation_id"]
                for patch in source["result"]["patches"]
            ):
                raise ValueError("Comparison source reading cannot be authenticated")
        readings = {
            f"{item['operation_id']}/{patch['observation']['observation_id']}": dict(
                observation=patch["observation"]
            )
            for item in outcomes
            if item["review_state"] == "accepted"
            for patch in item["result"]["patches"]
        }
        if _comparison_descriptor(readings, comparison["white"]) != comparison["white_descriptor"]:
            raise ValueError("Declared white assumptions do not match authenticated source")
    except (ValueError, KeyError, TypeError) as error:
        comparison_reason = str(error)
    try:
        receipt = _load(read("ti3-result.json"))
        if (
            not isinstance(receipt, dict)
            or receipt.get("owner_id") != manifest["batch_id"]
            or receipt.get("source_id") != str(folder / "batch.json")
        ):
            raise ValueError("TI3 receipt belongs to another batch source")
        ti3_exports.validate_result_json(
            _json(receipt),
            owner_id=manifest["batch_id"],
            source_id=str(folder / "batch.json"),
            folder=folder,
        )
        export_reason = (
            "TI3 files require export-owner authentication"
            if receipt.get("source_sha256") == _digest(payload)
            else "TI3 receipt belongs to an earlier manifest revision"
        )
    except (OSError, ValueError, UnicodeError) as error:
        receipt, export_reason = None, str(error)
    chart_review, originals = _authenticate_chart(manifest, outcomes, folder, read)
    for outcome in outcomes:
        outcome["native_diagnostic_review"] = _diagnostic_review(
            folder, outcome["native_diagnostics"], read,
        )
    return dict(
        folder=str(folder),
        manifest=manifest,
        manifest_sha256=_digest(payload),
        operations=outcomes,
        sample_count=sum(
            len(item["result"]["patches"])
            for item in outcomes
            if item["review_state"] == "accepted"
        ),
        ti3=receipt,
        export_state="unverified",
        export_reason=export_reason,
        read_only=True,
        chart=chart_review,
        inherited_operations=[dict(
            segment_id=segment_id, operation_id=operation_id,
            result=_load(result_to_json(result)),
            source_manifest_sha256=next(
                source["sha256"] for source in (manifest["chart"] or {}).get("sources", [])
                if source["segment_id"] == segment_id
            ),
            annotations=originals["segments"][segment_id]["annotations"],
        ) for (segment_id, operation_id), result in originals.get("operations", {}).items()
        if segment_id != manifest["batch_id"]],
        comparison=dict(
            comparison, saved=True, available=comparison_reason is None, reason=comparison_reason
        ),
    )


class _ApplicationWorker:
    """Retain one application callback and revoke only an unclaimed launch.

    This guard owns the drainer/publisher callback, not frontend dispatch or
    instrument cleanup. Thread identity survives termination; a late bootstrap
    after revocation cannot call the callback. A claimed callback stays its sole
    owner even when its thread has already completed before start() raises.
    """

    def __init__(self, target, args, name):
        self._target, self._args, self._name = target, args, name
        self._guard = threading.Lock()
        self._claimed = False
        self._revoked = False
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name=self._name)
        self.thread.start()

    def _run(self):
        with self._guard:
            if self._revoked or self._claimed:
                return
            self._claimed = True
        # The application callback owns failure classification and propagation.
        self._target(*self._args)

    def revoke_if_unclaimed(self):
        with self._guard:
            if self._claimed:
                return False
            self._revoked = True
            return True


class BatchApplication:
    """One writable ordinary-spot batch, with serialized commands and independent draining.

    Construct an exclusively new directory. ``bind`` fences renderer admission;
    ``command`` uses UUID identities and explicit expected draft/context versions.
    Results and command outcomes survive rebind for this application lifetime.
    Disk review is intentionally read only and never resumes acquisition.
    """

    def __init__(
        self, folder: str | Path, *, frontend: InstrumentFrontend | DirectSerialFrontend, mode: str,
        options: dict, expected_descriptor: dict | None = None, name="Untitled batch", notes="",
    ):
        _text(name, 1024)
        _text(notes, 65536)
        if not name.strip():
            raise ValueError("Batch name must contain text")
        if _UNCERTAIN_OWNERS:
            raise ValueError("Unconfirmed acquisition cleanup blocks new owners in this process")
        if not isinstance(frontend, (InstrumentFrontend, DirectSerialFrontend)):
            raise TypeError("Batch requires an already-bound instrument frontend")
        if not isinstance(options, dict):
            raise ValueError("Selected options must be an object")
        descriptor = _load(frontend.describe_json())
        if expected_descriptor is not None:
            if not isinstance(expected_descriptor, dict):
                raise ValueError("Selected frontend descriptor must be an object")
            if (_json(descriptor_context(expected_descriptor))
                    != _json(descriptor_context(descriptor))):
                raise ValueError("Selected frontend descriptor changed; select it again")
        # Preparation validates the selected current configuration without a
        # worker or device I/O. Refuse it before touching the output directory.
        prepared = frontend.prepare(_json(_batch_request(
            descriptor, mode, dict(id=str(uuid4()), options=options),
        )))
        selected_options = _load(prepared.request_json)["options"]
        # The descriptor and mode are frozen for this owner's lifetime, so the
        # offered calibration workflow is a construction fact, not a per-snapshot one.
        self._calibration_available = (
            _batch_mode(descriptor, mode).get("calibrate", {}).get("available") is True
        )
        self._mutex = threading.RLock()
        self._frontend = frontend
        self._generation = 0
        self._revision = 0
        self._commands = {}
        self._retained = {}
        self._payloads = {}
        self._source_blobs = {}
        self._source_results = {}
        self._staged_chart_definition = None
        self._diagnostic_payloads = {}
        self._diagnostic_targets = {}
        self._diagnostic_reasons = {}
        self._diagnostic_publication_reasons = {}
        self._diagnostic_operations = {}
        self._diagnostic_settings_manifest = None
        self._saved_diagnostic_settings = None
        self._record_views = {}
        self._unsaved_ids = set()
        self._saved_ids = set()
        self._saved_annotation_versions = {}
        self._saved_draft = None
        self._saved_comparison_version = None
        self._saved_comparison = None
        self._annotation_drafts = {}
        self._unknown = {}
        self._metadata_saving = False
        self._publishing_manifest = None
        self._process_exit_acknowledged = False
        self._stop_confirmed = True
        self._instrument_state = (
            "not_applicable" if descriptor["provenance"] == "simulated" else "unknown"
        )
        self._rescue_attempts = {}
        self._terminal_facts = {}
        self._operation = None
        self._active = None
        self._starting = False
        self._cancel_requested = False
        self._pending = None
        self._presentation = None
        self._events = []
        self._save_reason = None
        self._cleanup_reason = None
        self._ti3 = None
        self._ti3_reason = "No export attempt"
        self._receipt_digest = None
        self._companions_saving = False
        self._companion_completions = []
        self._companion_queued = None
        self._companion_pending = {}
        self._companions = {}
        self._closed = False
        self._selected = None
        self._folder = Path(folder).absolute()
        self._folder.mkdir()
        self._folder = self._folder.resolve(strict=True)
        folder_metadata = self._folder.lstat()
        self._folder_identity = folder_metadata.st_dev, folder_metadata.st_ino
        self._lock = _folder_lock(self._folder, ".batch.lock")
        self._lock.__enter__()
        try:
            self._manifest_bytes = None
            self._publications = {}
            draft = self._new_draft()
            draft["options"] = selected_options
            self._data = dict(
                schema_version=5,
                batch_id=str(uuid4()),
                batch_name=name,
                batch_notes=notes,
                revision=0,
                status="open",
                context_id=str(uuid4()),
                descriptor=descriptor,
                mode=mode,
                draft=draft,
                operations=[],
                annotations=[],
                destinations=[str(self._folder)],
                destination_issues={},
                shutdown=None,
                comparison=dict(version=0, reference=None, white=None, white_descriptor=None),
                chart=None,
                native_diagnostics=dict(debug_level=0, limit_bytes=65536),
            )
            self._persist()
        except BaseException as error:
            self._release_lock(failure=error)
            raise

    @staticmethod
    def _new_draft():
        identity = str(uuid4())
        return dict(
            id=identity, version=0, name="Sample #01", notes="", options={}, number_samples=True
        )

    def _current_draft(self, identity, version):
        draft = self._data["draft"]
        if identity != draft["id"] or type(version) is not int or version != draft["version"]:
            raise ValueError("Stale draft identity or version")
        return draft

    def _consumed_numbers(self, stem, marker):
        """Retain numbering from acquired intent, independent of edited labels."""
        consumed = {}
        for entry in self._data["operations"]:
            result = self._retained.get(entry["command_id"])
            if (
                result is None
                or result.status != "completed"
                or result.workflow != "spot"
                or not result.patches
                or entry["repeat_of"] is not None
                or not entry["draft"]["number_samples"]
            ):
                continue
            previous = _numbered_name(entry["draft"]["name"])
            if previous[:2] == (stem, marker):
                consumed[previous[2]] = previous[3]
        return consumed

    def _resolve_sample_name(self, name, number_samples):
        _sample_label(name)
        if type(number_samples) is not bool:
            raise ValueError("Sample numbering mode must be a boolean")
        if not number_samples:
            if not name.strip():
                raise ValueError("Exact sample name must contain non-whitespace text")
            return name
        parsed = _numbered_name(name.strip())
        if parsed is None:
            bare_hash = re.fullmatch(r"(.+?)\s+#", name.strip())
            if bare_hash is not None:
                stem, marker, ordinal, width = bare_hash[1], "#", 1, 2
            else:
                stem, marker, ordinal, width = (
                    (name.strip(), "", 1, 2) if name.strip() else ("Sample", "#", 1, 2)
                )
        else:
            stem, marker, ordinal, width = parsed
        consumed = self._consumed_numbers(stem, marker)
        if parsed is None:
            high_water = max(consumed, default=0)
            ordinal = high_water + 1
            if high_water:
                width = consumed[high_water]
        else:
            ordinal = max(1, ordinal)
            while ordinal in consumed:
                ordinal += 1
        resolved = _sample_label(f"{stem} {marker}{ordinal:0{width}d}")
        self._following_name(dict(name=resolved, number_samples=True))
        return resolved

    def _following_name(self, draft):
        if not draft["number_samples"]:
            return draft["name"]
        stem, marker, ordinal, width = _numbered_name(draft["name"])
        consumed = self._consumed_numbers(stem, marker)
        ordinal += 1
        while ordinal in consumed:
            ordinal += 1
        return _following_label(stem, marker, ordinal, width)

    def preview_draft(self, *, batch_id, generation, draft_id, version, name, number_samples):
        """Resolve a label without mutation, fenced to the current renderer and draft."""
        with self._mutex:
            if (
                batch_id != self._data["batch_id"]
                or type(generation) is not int
                or generation != self._generation
            ):
                raise ValueError("Stale batch or renderer binding")
            self._current_draft(draft_id, version)
            return dict(
                batch_id=batch_id,
                generation=generation,
                draft_id=draft_id,
                version=version,
                name=name,
                number_samples=number_samples,
                resolved_name=self._resolve_sample_name(name, number_samples),
            )

    def _release_publication_lock(self, lock, *, failure=None):
        try:
            lock.__exit__(
                type(failure) if failure is not None else None,
                failure,
                failure.__traceback__ if failure is not None else None,
            )
        except BaseException as error:
            self._save_reason = self._publication_reason(error)
            raise

    def _release_lock(self, *, failure=None):
        if self._lock is not None:
            self._release_publication_lock(self._lock, failure=failure)
            self._lock = None

    def _persist(self):
        self._assert_folder()
        payload = _json(self._data).encode("ascii")
        _manifest(payload)
        previous = _digest(self._manifest_bytes) if self._manifest_bytes is not None else None
        try:
            self._note_publication(
                _publish_owned_file(self._folder / "batch.json", payload, previous)
            )
            self._accept_manifest(payload)
        except BaseException as error:
            self._note_publication(getattr(error, "publication", None), complete=False)
            error = self._remember_manifest_publication(payload, error)
            raise error

    def _note_publication(self, publication, *, complete=True):
        if not isinstance(publication, PublicationResult):
            return
        key = str(publication.path)
        self._publications[key] = dict(
            path=key, sha256=_digest(publication.payload), file_sync=publication.file_sync,
            directory_synced=publication.directory_synced,
            limitations=list(publication.limitations),
            # A normal return can report unsupported directory sync. Failed
            # receipts still require retry; neither is stronger durability proof.
            _sync_complete=complete and publication.file_sync != "unconfirmed",
        )

    def _durability_snapshot(self):
        files = [
            {key: value for key, value in item.items() if key != "_sync_complete"}
            for item in self._publications.values()
            if Path(item["path"]).name not in {*_COMPANION_FILES.values(), "ti3-result.json"}
        ]
        current = [item for item in files if Path(item["path"]).parent == self._folder]
        limited = any(
            item["file_sync"] != "fullsync" or not item["directory_synced"] for item in current
        )
        return dict(
            status="limited" if limited else "synchronized" if current else "unconfirmed",
            files=files,
            scope=("Status covers the current destination; files retain observed history. "
                   "No power-loss or storage-device proof"),
        )

    def _remember_manifest_publication(self, payload, error):
        # A failed directory sync may follow a successful atomic replacement.
        # Remember only authenticated current bytes; do not mark sync as passed.
        try:
            self._assert_folder()
            if _read_regular(self._folder / "batch.json") == payload:
                self._manifest_bytes = payload
                path = self._folder / "batch.json"
                known = self._publications.get(str(path))
                if known is None or known["sha256"] != _digest(payload):
                    self._note_publication(
                        PublicationResult(path, payload, "unconfirmed", False), complete=False,
                    )
        except BaseException as failure:
            # Authentication is best effort after the real publication failed.
            # Preserve its first control and receipt while retaining diagnostics.
            return _preserve_publication_failure(
                error, failure, "Manifest authentication failed",
            )
        return error

    @staticmethod
    def _publication_reason(error):
        return "; ".join([str(error), *getattr(error, "__notes__", ())])

    def _assert_folder(self):
        metadata = self._folder.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != self._folder_identity
        ):
            raise OSError("Owned batch directory was replaced")

    def _touch(self):
        self._process_exit_acknowledged = False
        self._revision += 1
        self._data["revision"] += 1

    def _accept_manifest(self, payload):
        saved_comparison = _load(payload)["comparison"]
        self._manifest_bytes = payload
        self._save_reason = None
        self._saved_ids = {
            entry["command_id"]
            for entry in self._data["operations"]
            if entry["state"] == "accepted"
        }
        self._unsaved_ids.difference_update(self._saved_ids)
        self._saved_annotation_versions = {
            (item["operation_id"], item["observation_id"]): item["version"]
            for item in self._data["annotations"]
        }
        self._saved_draft = self._data["draft"]["id"], self._data["draft"]["version"]
        self._saved_comparison = saved_comparison
        self._saved_comparison_version = saved_comparison["version"]
        for draft in self._annotation_drafts.values():
            if not draft["dirty"]:
                draft.update(state="saved", reason=None)
        self._ti3_reason = "Manifest changed; optional export refresh pending"

    def _save(self, *, export=False):
        """Publish only unaccepted records, then the current manifest.

        The caller holds the mutex throughout; the drainer instead stages,
        publishes without the mutex and accepts (``_stage_save``,
        ``_publish_staged``, ``_accept_save``), so snapshots and read-only
        queries answer while its files are synchronized.
        """
        try:
            staged = self._stage_save()
        except BaseException as error:
            if not isinstance(error, (OSError, ValueError)):
                raise
            self._save_reason = self._publication_reason(error)
            return False
        if not self._accept_save(staged, self._publish_staged(staged), companions=not export):
            return False
        if export:
            self._schedule_companions(retry=True)
        return True

    def _stage_save(self):
        """Freeze every pending publication under the mutex; touch no file.

        The manifest bytes are those the publication implies, computed on a
        copy that already records the planned acceptances. While they are in
        flight, mutating commands are refused and manifest readers accept them
        as this owner's own (``_metadata_saving``, ``_publishing_manifest``).
        """
        self._assert_folder()
        data = deepcopy(self._data)
        entries = {entry["command_id"]: entry for entry in data["operations"]}
        files = []
        # Publish original source closure before the manifest may name it.
        for digest, payload in self._source_blobs.items():
            path = self._folder / f"source-{digest}.json"
            files.append(dict(
                kind="source", path=path, payload=payload, previous=None,
                known=deepcopy(self._publications.get(str(path))),
            ))
        for entry in self._data["operations"]:
            identity = entry["command_id"]
            if identity not in self._unsaved_ids:
                continue
            path = self._folder / entry["path"]
            result, expected = self._retained[identity], self._payloads[identity]
            files.append(dict(
                kind="operation", identity=identity, path=path, result=result,
                expected=expected, known=deepcopy(self._publications.get(str(path))),
            ))
            entries[identity].update(
                state="accepted", operation_id=result.operation_id,
                sha256=_digest(expected), reason=None,
            )
        for identity, recovery in self._unknown.items():
            payload = _json(recovery).encode("ascii")
            path = self._folder / f"recovery-{identity}.json"
            files.append(dict(
                kind="recovery", identity=identity, path=path, payload=payload,
                known=deepcopy(self._publications.get(str(path))),
            ))
            entries[identity]["recovery"] = dict(path=path.name, sha256=_digest(payload))
        for entry in self._data["operations"]:
            identity = entry["command_id"]
            if identity not in self._diagnostic_payloads:
                continue
            diagnostic = entry["native_diagnostics"]
            payload = _diagnostic_bytes(self._diagnostic_payloads[identity], diagnostic)
            path = self._folder / diagnostic["path"]
            files.append(dict(
                kind="diagnostic", identity=identity, path=path, payload=payload,
                previous=diagnostic["file_sha256"],
                known=deepcopy(self._publications.get(str(path))),
                blocked=not diagnostic["sink_stopped"]
                and path == self._diagnostic_targets.get(identity),
            ))
        manifest = _json(data).encode("ascii")
        _manifest(manifest)
        previous = _digest(self._manifest_bytes) if self._manifest_bytes is not None else None
        # The in-flight state is the phase ("saving") and the refusals it gates;
        # save_reason stays a failure reason, so a renderer flow that treats any
        # reason as a failed save is not tripped by a publication in progress.
        self._metadata_saving = True
        self._publishing_manifest = manifest
        self._revision += 1
        return dict(files=files, manifest=manifest, previous=previous)

    def _publish_staged(self, staged):
        """Publish the staged files, then the manifest; collect receipts, hold no mutex."""
        receipts = dict(files=[], accepted=[], diagnostics=[], manifest=None,
                        manifest_attempted=False, error=None)
        try:
            for item in staged["files"]:
                path, known = item["path"], item["known"]
                if item["kind"] == "operation":
                    published_now = False
                    try:
                        actual = _read_regular(path)
                    except FileNotFoundError:
                        publication = operation_exports.publish(item["result"], path)
                        receipts["files"].append(publication)
                        published_now = True
                        actual = publication.payload
                        if _read_regular(path) != actual:
                            raise OSError("Published canonical bytes cannot be authenticated")
                    if actual != item["expected"]:
                        raise FileExistsError("Reserved operation file differs from retained bytes")
                    # A file published by this call already carries its observed
                    # sync facts; repeating them cannot improve an unsupported
                    # directory sync. Only an earlier publication without them is
                    # synchronized.
                    if not published_now and (
                        known is None
                        or known["sha256"] != _digest(actual)
                        or not known["_sync_complete"]
                    ):
                        receipts["files"].append(_publish_owned_file(path, actual, _digest(actual)))
                elif item["kind"] == "recovery":
                    payload = item["payload"]
                    try:
                        actual = _read_regular(path)
                    except FileNotFoundError:
                        actual = None
                    if actual is not None and actual != payload:
                        raise FileExistsError(
                            "Reserved recovery evidence differs from retained bytes"
                        )
                    if (
                        actual is None
                        or known is None
                        or known["sha256"] != _digest(payload)
                        or not known["_sync_complete"]
                    ):
                        receipts["files"].append(_publish_owned_file(path, payload, None))
                elif item["kind"] == "diagnostic":
                    identity = item["identity"]
                    try:
                        if item["blocked"]:
                            raise OSError("Native diagnostic sink stop is unconfirmed; "
                                          "rescue its retained bytes to Another folder")
                        payload = item["payload"]
                        if (known is None or known["sha256"] != _digest(payload)
                                or not known["_sync_complete"]):
                            receipts["files"].append(_publish_owned_file(
                                path, payload, item["previous"],
                            ))
                        elif _read_regular(path) != payload:
                            raise ValueError("Native diagnostics differ from published bytes")
                    except (OSError, ValueError) as error:
                        receipts["diagnostics"].append(dict(
                            identity=identity, reason=self._publication_reason(error),
                            publication=getattr(error, "publication", None),
                        ))
                        continue
                    receipts["diagnostics"].append(dict(
                        identity=identity, reason=None, publication=None,
                    ))
                else:
                    payload = item["payload"]
                    if (known is None or known["sha256"] != _digest(payload)
                            or not known["_sync_complete"]):
                        receipts["files"].append(
                            _publish_owned_file(path, payload, item["previous"])
                        )
                    elif _read_regular(path) != payload:
                        raise ValueError(
                            "Retained source or diagnostics differ from published bytes"
                        )
                receipts["accepted"].append(item)
            # The manifest is published by path: re-check the owned directory
            # right before, as _persist does, since the file syncs above take
            # time during which the path could come to name another directory.
            self._assert_folder()
            receipts["manifest_attempted"] = True
            receipts["manifest"] = _publish_owned_file(
                self._folder / "batch.json", staged["manifest"], staged["previous"]
            )
        except BaseException as error:
            receipts["error"] = error
        return receipts

    def _accept_save(self, staged, receipts, *, companions=True):
        """Record the receipts under the mutex and settle the save outcome."""
        saved = False
        try:
            for diagnostic in receipts["diagnostics"]:
                if diagnostic["reason"] is None:
                    self._diagnostic_publication_reasons.pop(diagnostic["identity"], None)
                else:
                    self._note_publication(diagnostic["publication"], complete=False)
                    self._diagnostic_publication_reasons[diagnostic["identity"]] = (
                        diagnostic["reason"]
                    )
            for publication in receipts["files"]:
                self._note_publication(publication)
            for item in receipts["accepted"]:
                if item["kind"] not in {"operation", "recovery"}:
                    continue
                entry = next(
                    entry for entry in self._data["operations"]
                    if entry["command_id"] == item["identity"]
                )
                if item["kind"] == "operation":
                    entry.update(
                        state="accepted", operation_id=item["result"].operation_id,
                        sha256=_digest(item["expected"]), reason=None,
                    )
                else:
                    entry["recovery"] = dict(
                        path=item["path"].name, sha256=_digest(item["payload"])
                    )
            error = receipts["error"]
            if error is None:
                self._note_publication(receipts["manifest"])
                self._accept_manifest(staged["manifest"])
                saved = True
                return True
            self._note_publication(getattr(error, "publication", None), complete=False)
            if receipts["manifest_attempted"]:
                error = self._remember_manifest_publication(staged["manifest"], error)
            if not isinstance(error, (OSError, ValueError)):
                raise error
            self._save_reason = self._publication_reason(error)
            return False
        finally:
            self._metadata_saving = False
            self._publishing_manifest = None
            self._revision += 1
            if saved and companions:
                try:
                    self._schedule_companions()
                except BaseException as error:
                    receipts["companion_start_error"] = error
                    raise

    def _metadata_worker(self, command_id, draft_key=None):
        payload = None
        saved = False
        try:
            with self._mutex:
                outcome = self._commands[command_id]
                outcome["state"] = "saving"
                if draft_key is not None:
                    self._annotation_drafts[draft_key]["state"] = "saving"
                payload = _json(self._data).encode("ascii")
                previous = _digest(self._manifest_bytes)
                # Readers authenticating the manifest accept these bytes while
                # they are in flight: the file may already hold them.
                self._publishing_manifest = payload
                self._revision += 1
            self._assert_folder()
            _manifest(payload)
            publication = _publish_owned_file(self._folder / "batch.json", payload, previous)
            with self._mutex:
                self._note_publication(publication)
                self._accept_manifest(payload)
                saved = True
                # Preserve an earlier interrupted-start diagnostic on success.
                outcome.update(state="completed")
        except BaseException as error:
            with self._mutex:
                outcome = self._commands[command_id]
                self._note_publication(getattr(error, "publication", None), complete=False)
                if payload is not None:
                    error = self._remember_manifest_publication(payload, error)
                reason = self._publication_reason(error)
                if outcome["reason"]:
                    reason = f"{outcome['reason']}; publication failed: {reason}"
                self._save_reason = reason
                outcome.update(state="failed", reason=reason)
                if draft_key is not None:
                    self._annotation_drafts[draft_key].update(state="unsaved", reason=reason)
            if not isinstance(error, Exception):
                raise error
        finally:
            with self._mutex:
                self._metadata_saving = False
                self._publishing_manifest = None
                self._revision += 1
                if saved:
                    self._schedule_companions()

    def _companions_snapshot(self):
        source = _digest(self._manifest_bytes)
        # Intent publication precedes terminal companion scheduling. Expose the
        # actual deferred/queued owner without relabelling a stale file as saved
        # or hiding failed prepared bytes from retry and discard disclosure.
        deferred = self._metadata_saving or (
            self._active is not None and self._operation is not None
        )
        queued = self._companion_queued is not None and (
            self._companion_queued["source"] == source
        )
        refresh_owned = (
            not self._closed and self._data["status"] != "abandoned"
            and not self._cleanup_reason and (deferred or queued)
        )
        formats = {}
        for format, filename in _COMPANION_FILES.items():
            item = dict(self._companions.get(format, dict(
                state="absent", source_manifest_sha256=None, payload_sha256=None,
                reason="No automatic companion preparation yet", publication=None,
            )))
            item["path"] = str(self._folder / filename)
            item["current"] = item["state"] == "current" and (
                item["source_manifest_sha256"] == source
            )
            if item["state"] == "current" and not item["current"]:
                item["state"] = "stale"
            item["refresh_pending"] = (
                item["state"] == "stale" and format not in self._companion_pending
                and refresh_owned
            )
            item["retryable"] = format in self._companion_pending or item["state"] in {
                "failed", "stale",
            }
            formats[format] = item
        return dict(source_manifest_sha256=source, saving=self._companions_saving, formats=formats)

    def _schedule_companions(self, *, retry=False):
        """Queue the latest accepted native bytes; one worker owns optional I/O.

        Accepted original bytes belong to this owner and are immutable. Freeze
        their mapping here, then reuse the saved-review validators off the mutex.
        A newer accepted manifest replaces only an unprepared queued capture,
        never a prepared failed payload retained for an explicit retry.
        """
        if self._closed or self._data["status"] == "abandoned":
            return
        files = {"batch.json": self._manifest_bytes}
        manifest = _load(self._manifest_bytes)
        for entry in manifest["operations"]:
            identity = entry["command_id"]
            if entry["state"] == "accepted":
                files[entry["path"]] = self._payloads[identity]
            if entry["recovery"] is not None:
                files[entry["recovery"]["path"]] = _json(self._unknown[identity]).encode("ascii")
            diagnostic = entry["native_diagnostics"]
            if diagnostic is not None and diagnostic["sha256"] is not None:
                files[diagnostic["path"]] = self._diagnostic_payloads[identity]
        files.update((f"source-{digest}.json", payload)
                     for digest, payload in self._source_blobs.items())
        queued_retry = self._companion_queued is not None and self._companion_queued["retry"]
        self._companion_queued = dict(
            folder=self._folder, directory_identity=self._folder_identity,
            manifest=self._manifest_bytes, source=_digest(self._manifest_bytes),
            files=files, retry=retry or queued_retry, needs_lock=self._lock is None,
        )
        if self._companions_saving:
            return
        self._companions_saving = True
        launch = None
        try:
            launch = _ApplicationWorker(
                self._companion_worker, (), f"companions-{self._data['batch_id']}",
            )
            launch.start()
        except BaseException as error:
            # A claimed callback alone owns settlement, even after start raises.
            if launch is None or launch.revoke_if_unclaimed():
                self._companions_saving = False
                self._companion_queued = None
                for format in _COMPANION_FILES:
                    previous = self._companions.get(format, {})
                    self._companions[format] = dict(
                        previous, state="failed",
                        source_manifest_sha256=previous.get("source_manifest_sha256"),
                        payload_sha256=previous.get("payload_sha256"),
                        publication=previous.get("publication"),
                        reason=f"Companion worker did not start: {type(error).__name__}: {error}",
                    )
            # Ordinary optional failure leaves canonical saving successful.
            # Preserve a control signal after recording optional failure facts;
            # the drainer distinguishes this from canonical publication failure.
            self._revision += 1
            if not isinstance(error, Exception):
                raise

    def _assert_companion_source(self):
        """Refuse external manifest replacement without blocking owner advancement."""
        self._assert_folder()
        while True:
            with self._mutex:
                before = {self._manifest_bytes, self._publishing_manifest}
            try:
                actual = _read_regular(self._folder / "batch.json")
            except _OwnedFileReplacedDuringOpen:
                self._assert_folder()
                with self._mutex:
                    after = {self._manifest_bytes, self._publishing_manifest}
                    publishing = self._publishing_manifest is not None
                if before == after and not publishing:
                    raise
                # Only a classified ordinary-inode race with an owned writer
                # witness is reopened. The next read still validates file type,
                # links, directory identity and exact accepted/in-flight bytes.
                continue
            self._assert_folder()
            with self._mutex:
                after = {self._manifest_bytes, self._publishing_manifest}
            if actual in before or actual in after:
                return
            if before == after:
                raise ValueError(
                    "Canonical manifest changed outside this owner; rescue retained work"
                )
            # Several owned publications can cross one unlocked read. Retry a
            # stable observation rather than misclassifying an intermediate
            # owner manifest as external replacement. Acquisition holds no wait.

    def _publish_companion(self, staged, format, prepared):
        """Publish exact retained bytes; receipt acceptance never runs disk I/O."""
        path = staged["folder"] / _COMPANION_FILES[format]
        payload = prepared["payload"]
        status = dict(
            state="publishing", source_manifest_sha256=prepared["source"],
            payload_sha256=_digest(payload), reason=None, publication=None,
        )
        with self._mutex:
            self._companion_pending[format] = prepared
            self._companions[format] = status
            self._revision += 1
        try:
            self._assert_companion_source()
            publication = _publish_owned_file(path, payload, staged["previous"][format])
        except BaseException as error:
            with self._mutex:
                publication = getattr(error, "publication", None)
                self._note_publication(publication, complete=False)
                if isinstance(publication, PublicationResult):
                    staged["previous"][format] = _digest(publication.payload)
                    status["publication"] = dict(
                        file_sync=publication.file_sync,
                        directory_synced=publication.directory_synced,
                        limitations=list(publication.limitations),
                    )
                status.update(state="failed", reason=self._publication_reason(error))
                self._revision += 1
            if not isinstance(error, Exception):
                raise
            return False
        with self._mutex:
            self._note_publication(publication)
            staged["previous"][format] = _digest(payload)
            status.update(state="current", publication=dict(
                file_sync=publication.file_sync, directory_synced=publication.directory_synced,
                limitations=list(publication.limitations),
            ))
            self._companion_pending.pop(format, None)
            self._revision += 1
        return True

    def _companion_worker(self):
        """Drain coalesced immutable captures without owning acquisition phase."""
        while True:
            with self._mutex:
                staged, self._companion_queued = self._companion_queued, None
                if staged is None:
                    self._companions_saving = False
                    completions, self._companion_completions = self._companion_completions, []
                    try:
                        if self._data["status"] == "finished" and self._lock is not None:
                            self._release_lock()
                    except BaseException as error:
                        self._save_reason = self._publication_reason(error)
                        for completion in completions:
                            completion.update(state="failed", reason=self._save_reason)
                        self._revision += 1
                        raise
                    unresolved = self._unresolved_companions()
                    for completion in completions:
                        completion["state"] = "failed" if unresolved else "completed"
                        if unresolved:
                            completion["reason"] = (
                                "Automatic files still need saving: " + "; ".join(
                                    item["format"].upper() + ": "
                                    + (item["reason"] or item["state"])
                                    for item in unresolved
                                )
                            )
                    self._revision += 1
                    return
                staged.update(
                    pending=dict(self._companion_pending),
                    formats=deepcopy(self._companions),
                    previous={format: self._publications.get(
                        str(staged["folder"] / filename), {},
                    ).get("sha256") for format, filename in _COMPANION_FILES.items()},
                    previous_ti3=deepcopy(self._ti3), receipt_digest=self._receipt_digest,
                )
                for format in _COMPANION_FILES:
                    if format not in self._companion_pending:
                        self._companions[format] = dict(
                            state="preparing", source_manifest_sha256=staged["source"],
                            payload_sha256=None, reason=None, publication=None,
                        )
                self._revision += 1
            try:
                lock = _folder_lock(staged["folder"], ".batch.lock") \
                    if staged["needs_lock"] else nullcontext()
                with lock:
                    self._prepare_companions(staged)
            except BaseException as error:
                with self._mutex:
                    self._ti3_reason = self._publication_reason(error)
                    for format in _COMPANION_FILES:
                        item = self._companions[format]
                        # Successful independent publications keep their facts.
                        if item["state"] in {"preparing", "publishing"}:
                            item.update(state="failed", reason=self._publication_reason(error))
                    if _publication_cleanup_reason(staged["folder"]):
                        self._save_reason = self._publication_reason(error)
                        for completion in self._companion_completions:
                            completion.update(state="failed", reason=self._save_reason)
                        self._companion_completions = []
                    self._revision += 1
                if not isinstance(error, Exception):
                    with self._mutex:
                        self._companion_queued = None
                        self._companions_saving = False
                        for completion in self._companion_completions:
                            completion.update(
                                state="failed", reason=self._publication_reason(error),
                            )
                        self._companion_completions = []
                    raise

    def _prepare_companions(self, staged):
        # Local imports preserve the established application/dataset boundary.
        from .batch_archive import prepare_from_capture
        from .dataset_binding import chart_source_files, referenced_paths

        self._assert_companion_source()
        review = review_captured(staged["folder"], staged["files"])
        if any(item["state"] == "accepted" and item["review_state"] != "accepted"
               for item in review["operations"]):
            raise ValueError("Canonical inputs cannot be authenticated")
        if review["chart"] is not None and not review["chart"]["available"]:
            raise ValueError("Canonical chart sources cannot be authenticated")
        names = referenced_paths(review["manifest"], chart_source_files(review))
        files = {name: payload for name, payload in staged["files"].items() if name in names}
        missing = sorted(names - files.keys())
        needed = []
        for format in _COMPANION_FILES:
            retained = staged["pending"].get(format)
            if retained is not None:
                if not staged["retry"] or not self._publish_companion(staged, format, retained):
                    continue
                if retained["source"] == staged["source"]:
                    continue
            previous = staged["formats"].get(format, {})
            if (previous.get("state") == "current"
                    and previous["source_manifest_sha256"] == staged["source"]):
                try:
                    current = _read_regular(staged["folder"] / _COMPANION_FILES[format])
                except (OSError, ValueError):
                    current = None
                if current is not None and _digest(current) == previous["payload_sha256"]:
                    with self._mutex:
                        self._companions[format] = previous
                    continue
            if format == "chart_selected_csv" and review["manifest"]["chart"] is None:
                with self._mutex:
                    self._companions[format] = dict(
                        state="unavailable", source_manifest_sha256=staged["source"],
                        payload_sha256=None, reason="This batch has no guided chart",
                        publication=None,
                    )
                continue
            needed.append(format)
        prepared = prepare_from_capture(review, files, missing_files=missing) if needed else {}
        for format in needed:
            item = prepared[format]
            if item["payload"] is None:
                with self._mutex:
                    self._companions[format] = dict(
                        state="failed" if item["retryable"] else "unavailable",
                        source_manifest_sha256=staged["source"],
                        payload_sha256=None, reason=item["reason"], publication=None,
                    )
                continue
            self._publish_companion(staged, format, dict(
                payload=item["payload"], source=staged["source"],
            ))
        # TI3 uses this same frozen native review, including chart selections and
        # annotations. Later acquisition cannot splice new metadata into it.
        self._export(staged, review)

    def _schedule_metadata(self, outcome, draft_key=None):
        self._metadata_saving = True
        self._save_reason = "Metadata publication pending"
        outcome.update(admission="admitted", state="pending")
        identity = next(key for key, value in self._commands.items() if value is outcome)
        launch = None
        try:
            launch = _ApplicationWorker(
                self._metadata_worker, (identity, draft_key), f"metadata-{identity}",
            )
            launch.start()
        except BaseException as error:
            reason = f"Metadata worker startup failed: {type(error).__name__}: {error}"
            unclaimed = launch is None or launch.revoke_if_unclaimed()
            outcome["reason"] = reason
            if unclaimed:
                self._metadata_saving = False
                self._save_reason = reason
                outcome["state"] = "failed"
                if draft_key is not None:
                    self._annotation_drafts[draft_key].update(state="unsaved", reason=reason)
            # A claimed writer alone resolves its publication/guard, including
            # if it completed before an interrupted startup return. Do not use
            # is_alive() as evidence of whether the callback ever launched.
            if not isinstance(error, Exception):
                raise
            if unclaimed:
                raise ValueError(reason) from error

    def _export(self, staged, review):
        """Project manifest-accepted inputs through the existing TI3 owner."""
        try:
            self._assert_companion_source()
            data = review["manifest"]
            folder = staged["folder"]
            if any(
                item["review_state"] != "accepted" and item["state"] == "accepted"
                for item in review["operations"]
            ):
                raise ValueError("Canonical inputs cannot be authenticated")
            if review["chart"] is not None and not review["chart"]["available"]:
                raise ValueError("Canonical chart sources cannot be authenticated")
            observations, row_keys, overlays = [], [], {}
            for item in review["operations"]:
                if item["review_state"] == "accepted":
                    result = result_from_json(_json(item["result"]))
                    for patch in result.patches:
                        observations.append(patch.observation)
                        row_keys.append((
                            data["batch_id"], result.operation_id,
                            patch.observation.observation_id, item["repeat_of"],
                        ))
            for annotation in data["annotations"]:
                overlays[data["batch_id"], annotation["operation_id"],
                         annotation["observation_id"]] = annotation
            current_count = len(observations)
            for item in review["inherited_operations"]:
                result = result_from_json(_json(item["result"]))
                for patch in result.patches:
                    observations.append(patch.observation)
                    row_keys.append((item["segment_id"], result.operation_id,
                                     patch.observation.observation_id, None))
                for annotation in item["annotations"]:
                    overlays[item["segment_id"], annotation["operation_id"],
                             annotation["observation_id"]] = annotation
            snapshot = ti3_exports.retained_snapshot(
                observations, data["batch_id"], staged["source"],
                source_id=str(folder / "batch.json"),
            )
            chart = data["chart"]
            record_ids = {}
            for row, (segment_id, operation_id, observation_id, repeat_of) in zip(
                snapshot["records"], row_keys, strict=True,
            ):
                key = segment_id, operation_id, observation_id
                annotation = overlays[key]
                # Record IDs are source-qualified projection keys, not replacement
                # native observation identities. Ordinary batches keep their IDs.
                if chart is not None:
                    row["id"] = "/".join(key)
                record_ids[key] = row["id"]
                row.update(name=annotation["name"], notes=annotation["notes"])
                row["context"]["provenance"].update(
                    source_segment_id=segment_id, operation_id=operation_id,
                    observation_id=observation_id, repeat_of=deepcopy(repeat_of),
                )
            if chart is None:
                if len(record_ids) != len(observations) or len({
                    item.observation_id for item in observations
                }) != len(observations):
                    raise ValueError(
                        "TI3 projection requires unique observation IDs across operations"
                    )
            snapshot["ordinary_record_ids"] = [
                row["id"] for row in snapshot["records"][:current_count]
            ]
            if chart is not None:
                records = {row["id"]: row for row in snapshot["records"]}
                for chart_pass in chart["passes"]:
                    selected, origins = [], set()
                    attempts = {item["id"]: item for item in chart_pass["attempts"]}
                    for cell, selected_id in zip(
                        chart["definition"]["cells"], chart_pass["selected_attempts"], strict=True,
                    ):
                        if selected_id is None:
                            continue
                        reading = attempts[selected_id]["readings"][0]
                        key = tuple(reading[name] for name in (
                            "segment_id", "operation_id", "observation_id",
                        ))
                        record_id = record_ids[key]
                        origins.add(records[record_id]["provenance"])
                        selected.append(dict(
                            record_id=record_id,
                            sample_id=str(cell["source_id"]) if cell["source_id"] is not None
                            else cell["coordinate"], sample_loc=cell["source_location"],
                        ))
                    if len(origins) > 1:
                        raise ValueError("One chart pass cannot mix acquisition provenances")
                    snapshot["charts"].append(dict(
                        id=chart_pass["id"], provenance=next(iter(origins),
                            data["descriptor"]["provenance"]),
                        total_cells=len(chart["definition"]["cells"]), rows=selected,
                        target=chart["definition"].get("target_json"),
                    ))
            previous = staged["previous_ti3"]
            if previous is None:
                try:
                    previous = _load(_read_regular(folder / "ti3-result.json"))
                except FileNotFoundError:
                    previous = None
            result = _load(
                ti3_exports.update_json(_json(snapshot), str(folder), _json(previous))
            )
            # Keep the full result even if its separate receipt publication fails.
            with self._mutex:
                self._ti3 = result
            receipt = _json(result).encode("ascii")
            publication = _publish_owned_file(
                folder / "ti3-result.json", receipt, staged["receipt_digest"],
            )
            with self._mutex:
                self._note_publication(publication)
                self._receipt_digest = _digest(receipt)
                self._ti3_reason = (None if staged["manifest"] == self._manifest_bytes
                                    else "Manifest changed; optional export refresh pending")
        except (OSError, ValueError, KeyError) as error:
            with self._mutex:
                publication = getattr(error, "publication", None)
                self._note_publication(publication, complete=False)
                if (isinstance(publication, PublicationResult)
                        and publication.path == folder / "ti3-result.json"
                        and publication.payload == locals().get("receipt")):
                    self._receipt_digest = _digest(publication.payload)
                self._ti3_reason = str(error)

    def _chart_definition_binding(self, *, batch_id, generation, context_id):
        if (batch_id != self._data["batch_id"] or type(generation) is not int
                or generation != self._generation or context_id != self._data["context_id"]):
            raise ValueError("Stale chart definition binding")
        if self._closed:
            raise ValueError("Application is closed")
        self._mutable_idle()
        if self._save_reason:
            raise ValueError("Save retained work before staging a guided chart")
        if self._data["chart"] is not None or self._data["operations"]:
            raise ValueError("Start a guided chart in a new empty batch")
        current, reason = self._context_status()
        if not current:
            raise ValueError(reason)
        self._request(self._data["draft"], "spot")

    def stage_chart_definition(self, definition, *, batch_id, generation, context_id):
        """Hold one validated definition; expose only fenced geometry and source identity.

        Native importers and headless callers pass the complete definition here,
        rather than through a bounded command envelope. Validation uses the
        existing chart/target owners and runs outside the application mutex.
        """
        _uuid(batch_id)
        _uuid(context_id)
        binding = dict(batch_id=batch_id, generation=generation, context_id=context_id)
        with self._mutex:
            self._chart_definition_binding(**binding)
        definition = chart_state.validate_definition(definition)
        definition_json = _json(definition)
        confirmation, source = None, None
        if definition["kind"] == "ti2":
            target = _load(definition["target_json"])
            confirmation = {key: definition[key] for key in ("strips_as", "rows", "columns")}
            confirmation["definition_id"] = definition["id"]
            source = dict(filename=target["filename"], sha256=target["source_sha256"],
                          byte_count=len(base64.b64decode(target["source_base64"], validate=True)))
        packet = dict(
            binding, definition_token=str(uuid4()), definition_id=definition["id"],
            mapping_confirmation=confirmation, source=source,
            preview={key: definition[key] for key in (
                "kind", "name", "rows", "columns", "traversal", "strips_as", "cells",
            )},
        )
        if len(_json(packet).encode("ascii")) > _MAX_COMMAND_BYTES:
            raise ValueError("Staged chart preview exceeds the bounded command envelope")
        with self._mutex:
            self._chart_definition_binding(**binding)
            self._staged_chart_definition = dict(
                binding=binding, token=packet["definition_token"], definition_json=definition_json,
                mapping_confirmation=_json(confirmation),
            )
        return deepcopy(packet)

    def bind(self) -> dict:
        """Return a full snapshot and revoke admission from earlier bindings."""
        with self._mutex:
            self._generation += 1
            self._staged_chart_definition = None
            return self.snapshot()

    def _context_status(self):
        """Compare cached frontend facts with this owner's immutable input context."""
        try:
            descriptor = _load(self._frontend.describe_json())
            if descriptor["capability_id"] == self._data["descriptor"]["capability_id"]:
                return True, None
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
            return False, f"Current instrument context is unavailable: {error}"
        return False, "Instrument context changed; Finish and create a new batch"

    def _current_chart(self, revision):
        chart = self._data["chart"]
        if (chart is None or type(revision) is not int or revision != chart["revision"]):
            raise ValueError("Guided chart changed; refresh its current revision")
        return chart

    def _chart_wrapper(self, attempt):
        reference = attempt["readings"][0]
        key = reference["segment_id"], reference["operation_id"]
        if key[0] == self._data["batch_id"]:
            result = self._retained.get(attempt["command_id"])
        else:
            result = self._source_results.get(key)
        if result is None:
            raise ValueError("Chart original operation cannot be authenticated")
        matches = [patch.observation for patch in result.patches
                   if patch.observation.observation_id == reference["observation_id"]
                   and patch.observation.sample_id == reference["sample_id"]]
        if len(matches) != 1:
            raise ValueError("Chart reading does not identify its original observation")
        return dict(
            cell_index=attempt["cell"], observation=matches[0],
            reading_ref=deepcopy(reference), accepted_context=deepcopy(attempt["accepted_context"]),
        )

    def chart_readings(self, pass_id=None):
        """Return original selected wrappers, retaining source and cell identities."""
        with self._mutex:
            chart = self._data["chart"]
            if chart is None:
                raise ValueError("This batch has no guided chart")
            if pass_id is not None:
                _uuid(pass_id)
            chosen = chart["active_pass"] if pass_id is None else pass_id
            matches = [item for item in chart["passes"] if item["id"] == chosen]
            if len(matches) != 1:
                raise ValueError("Unknown chart pass")
            chart_pass = matches[0]
            attempts = {item["id"]: item for item in chart_pass["attempts"]}
            return [self._chart_wrapper(attempts[identity])
                    for identity in chart_pass["selected_attempts"] if identity is not None]

    def chart_details(self, pass_id, attempt_id):
        """Inspect one immutable attempt without expanding history in every snapshot."""
        with self._mutex:
            if self._data["chart"] is None:
                raise ValueError("This batch has no guided chart")
            return chart_state.attempt_details(
                self._data["chart"], pass_id=pass_id, attempt_id=attempt_id,
            )

    def chart_history(self, pass_id, cell, *, offset=0, limit=50):
        """Page attempt identities for one canonical cell, without source payloads."""
        with self._mutex:
            chart = self._data["chart"]
            if chart is None:
                raise ValueError("This batch has no guided chart")
            return chart_state.history(chart, pass_id, cell, offset=offset, limit=limit)

    def chart_decision_details(self):
        """Inspect explicit context/reference/placement facts outside polled snapshots."""
        with self._mutex:
            chart = self._data["chart"]
            if chart is None:
                raise ValueError("This batch has no guided chart")
            return chart_state.decision_details(chart)

    def _chart_terminal(self, entry, result=None, reason=""):
        chart = self._data["chart"]
        if chart is None:
            return
        found = [(chart_pass, attempt) for chart_pass in chart["passes"]
                 for attempt in chart_pass["attempts"]
                 if attempt["command_id"] == entry["command_id"]
                 and attempt["segment_id"] == self._data["batch_id"]]
        if not found or found[0][1]["status"] != "started":
            return
        chart_pass, attempt = found[0]
        references, accepted, position = [], None, None
        status = "unresolved" if result is None else result.status
        if result is not None:
            reason = result.error or ""
            references = [dict(
                segment_id=chart["segment_id"], operation_id=result.operation_id,
                observation_id=patch.observation.observation_id,
                sample_id=patch.observation.sample_id,
            ) for patch in result.patches]
            if len(result.patches) == 1:
                try:
                    accepted = chart_context.accepted(
                        self._data["descriptor"], entry["request"], result,
                        result.patches[0].observation.observation_id,
                    )
                    if status == "completed":
                        position = chart_science.chart_position_check(
                            chart_pass["reference"], chart["definition"], dict(
                                cell_index=attempt["cell"],
                                observation=result.patches[0].observation,
                                reading_ref=references[0], accepted_context=accepted,
                            ), observer_csv=RESOURCE.read_bytes(),
                        )
                except (ValueError, OverflowError) as error:
                    # Keep the native result even when chart qualification refuses it.
                    accepted, position = None, None
                    self._events.append(dict(
                        kind="application_chart_qualification_unavailable",
                        operation_id=result.operation_id, payload=dict(reason=str(error)),
                    ))
        self._data["chart"] = chart_state.complete_attempt(
            chart, attempt["id"], status=status, reading_refs=references,
            accepted_context=accepted, position_check=position, reason=reason[:8192],
        )

    def snapshot(self) -> dict:
        """Return current summaries; immutable records/outcomes are explicit queries."""
        with self._mutex:
            context_current, context_reason = self._context_status()
            offered = _batch_mode(self._data["descriptor"], self._data["mode"])
            outcomes = []
            for entry in self._data["operations"]:
                result = self._retained.get(entry["command_id"])
                outcomes.append(
                    dict(
                        command_id=entry["command_id"],
                        operation_id=entry["operation_id"],
                        path=entry["path"],
                        workflow=entry["request"]["workflow"],
                        state=entry["state"],
                        accepted=result is not None,
                        saved=entry["command_id"] in self._saved_ids,
                        status=result.status if result else "unknown",
                        reason=entry["reason"],
                        sample_count=len(result.patches) if result else 0,
                        repeat_of=entry["repeat_of"],
                    )
                )
            annotations = [
                dict(
                    _metadata_summary(item),
                    saved=self._saved_annotation_versions.get(
                        (item["operation_id"], item["observation_id"])
                    )
                    == item["version"],
                )
                for item in self._data["annotations"]
            ]
            publication_reason = _publication_cleanup_reason(self._folder)
            save_reason = self._save_reason
            if publication_reason and publication_reason not in (save_reason or ""):
                save_reason = "; ".join(filter(None, (save_reason, publication_reason)))
            repeat_source = None
            for entry in reversed(self._data["operations"]):
                result = self._retained.get(entry["command_id"])
                if (
                    result is not None
                    and result.status == "completed"
                    and result.workflow == "spot"
                    and result.patches
                ):
                    repeat_source = dict(
                        operation_id=result.operation_id,
                        observation_id=result.patches[0].observation.observation_id,
                        name=entry["draft"]["name"],
                    )
                    break
            return deepcopy(
                dict(
                    batch_id=self._data["batch_id"],
                    batch_name=self._data["batch_name"],
                    batch_notes=self._data["batch_notes"],
                    context_id=self._data["context_id"],
                    context_current=context_current,
                    context_reason=context_reason,
                    generation=self._generation,
                    revision=self._revision,
                    status=self._data["status"]
                    if self._data["status"] == "abandoned"
                    else "closed"
                    if self._closed
                    else self._data["status"],
                    # A publication in flight is "saving" whatever the terminal
                    # classification; cleanup_reason carries that separately.
                    phase="starting"
                    if self._starting
                    else "saving"
                    if self._metadata_saving
                    else "cleanup_uncertain"
                    if self._cleanup_reason
                    else "active"
                    if self._active
                    else "idle",
                    operation_id=self._operation.operation_id if self._operation else None,
                    operation_terminal=bool(self._operation and self._operation.done),
                    software_stopped=self._stop_confirmed,
                    instrument_state=self._instrument_state,
                    process_exit_ready=self._process_exit_ready(),
                    process_exit_acknowledged=self._process_exit_acknowledged,
                    cancel_requested=self._cancel_requested,
                    descriptor=self._data["descriptor"],
                    mode=self._data["mode"],
                    draft=self._data["draft"],
                    native_diagnostics=self._data["native_diagnostics"],
                    native_diagnostic_failures=dict(
                        retention=self._diagnostic_reasons,
                        publication=self._diagnostic_publication_reasons,
                    ),
                    repeat_source=repeat_source,
                    chart=chart_state.view(self._data["chart"])
                    if self._data["chart"] is not None else None,
                    comparison=dict(
                        self._data["comparison"],
                        saved=self._comparison_saved(),
                    ),
                    draft_saved=self._saved_draft
                    == (self._data["draft"]["id"], self._data["draft"]["version"]),
                    annotations=annotations,
                    annotation_drafts=[
                        _metadata_summary(draft) for draft in self._annotation_drafts.values()
                    ],
                    selected=self._selected,
                    instructions=self._pending,
                    presentation=self._presentation,
                    events=self._events[-32:],
                    operations=outcomes,
                    sample_count=sum(len(result.patches) for result in self._retained.values()),
                    accepted_count=len(self._retained),
                    saved_count=len(self._saved_ids),
                    save_reason=save_reason,
                    durability=self._durability_snapshot(),
                    companions=self._companions_snapshot(),
                    cleanup_reason=self._cleanup_reason,
                    folder=str(self._folder),
                    destination_issues=self._data["destination_issues"],
                    ti3=self._ti3,
                    export_state="stale"
                    if self._ti3_reason == "Manifest changed; optional export refresh pending"
                    and self._ti3 is not None
                    else "unverified"
                    if self._ti3 is None or self._ti3_reason
                    else self._ti3["status"],
                    export_reason=self._ti3_reason,
                    shutdown=self._shutdown_snapshot(),
                    can_measure_chart=self._data["chart"] is not None
                    and not self._data["chart"]["paused"]
                    and not self._data["chart"]["finished"]
                    and self._data["chart"]["pending_context"] is None
                    and not any(item["pending_position"] for item in self._data["chart"]["passes"]
                                if item["id"] == self._data["chart"]["active_pass"])
                    and context_current and self._active is None and not self._closed
                    and not self._metadata_saving and not save_reason
                    and self._data["status"] == "open" and not _UNCERTAIN_OWNERS,
                    can_run_workflow=self._data["chart"] is None
                    and context_current and self._active is None
                    and not self._closed
                    and not self._metadata_saving
                    and not save_reason
                    and self._data["status"] == "open"
                    and not _UNCERTAIN_OWNERS,
                    workflows=list(offered.values()),
                    can_measure=self._data["chart"] is None
                    and context_current and self._active is None
                    and not self._closed
                    and not self._metadata_saving
                    and not save_reason
                    and self._data["status"] == "open"
                    and not _UNCERTAIN_OWNERS
                    and offered.get("spot", {}).get("available") is True,
                    can_calibrate=context_current and self._active is None
                    and not self._closed
                    and not self._metadata_saving
                    and not save_reason
                    and self._data["status"] == "open"
                    and not _UNCERTAIN_OWNERS
                    and self._calibration_available,
                    can_finish=self._active is None
                    and not self._closed
                    and not self._metadata_saving
                    and not save_reason
                    and self._data["status"] == "open"
                    and not any(draft["dirty"] for draft in self._annotation_drafts.values()),
                    can_edit_annotations=not self._closed
                    and self._data["status"] == "open"
                    and publication_reason is None,
                    can_apply_annotations=not self._closed
                    and self._data["status"] == "open"
                    and self._active is None
                    and not self._metadata_saving
                    and not save_reason,
                    can_start_new_batch=self._new_batch_ready(),
                )
            )

    def _shutdown_snapshot(self):
        shutdown = self._data["shutdown"]
        if shutdown is None:
            return None
        discarded = shutdown["discarded"]
        return dict(shutdown, discarded=dict(
            discarded,
            annotations=[_metadata_summary(item) for item in discarded["annotations"]],
            annotation_drafts=[_metadata_summary(item) for item in discarded["annotation_drafts"]],
            chart=chart_state.view(discarded["chart"]) if discarded.get("chart") is not None
            else None,
        ))

    def operation_details(self, command_id: str) -> dict:
        with self._mutex:
            entry = next(
                (item for item in self._data["operations"] if item["command_id"] == command_id),
                None,
            )
            if entry is None:
                raise ValueError("Unknown operation command")
            return deepcopy(
                dict(
                    entry,
                    result=self._record_views.get(command_id),
                    recovery_result=self._unknown.get(command_id),
                    native_diagnostic_review=_diagnostic_review(
                        self._folder, entry["native_diagnostics"],
                    ),
                    native_diagnostic_retention_reason=self._diagnostic_reasons.get(command_id),
                )
            )

    def reading_details(self, operation_id: str, observation_id: str) -> dict:
        with self._mutex:
            for result in self._retained.values():
                if result.operation_id == operation_id:
                    for patch in result.patches:
                        if patch.observation.observation_id == observation_id:
                            return deepcopy(asdict(patch.observation))
            raise ValueError("Unknown reading identity")

    def comparison_context(self, version):
        """Return only saved, source-authenticated comparison choices and assumptions."""
        with self._mutex:
            comparison = self._data["comparison"]
            if type(version) is not int or version != comparison["version"]:
                raise ValueError("Stale comparison version")
            if not self._comparison_saved():
                raise ValueError("Save comparison context before quantitative analysis")
            self._assert_folder()
            # An unrelated metadata save may have replaced the file with bytes
            # this owner published but has not yet accepted; both are its own.
            actual = _read_regular(self._folder / "batch.json")
            own_in_flight = (
                self._metadata_saving
                and self._publishing_manifest is not None
                and actual == self._publishing_manifest
            )
            if actual != self._manifest_bytes and not own_in_flight:
                raise ValueError("Comparison manifest cannot be authenticated")
            saved, authenticated = _manifest(self._manifest_bytes), _manifest(actual)
            # The frozen context is authenticated against the accepted manifest
            # even for this owner's in-flight bytes. Only the operations list
            # may differ there: an open retained-result publication already
            # records the entry whose acceptance is pending.
            fields = ("batch_id", "context_id", "descriptor", "mode") + (
                () if own_in_flight else ("operations",)
            )
            if authenticated["comparison"] != self._saved_comparison or any(
                authenticated[field] != saved[field] for field in fields
            ):
                raise ValueError("Comparison frozen context cannot be authenticated")
            for pair in (comparison["reference"], comparison["white"]):
                if pair is None or "operation_id" not in pair:
                    continue
                entry = next(
                    item
                    for item in self._data["operations"]
                    if item["operation_id"] == pair["operation_id"]
                )
                identity = entry["command_id"]
                if (
                    identity not in self._saved_ids
                    or _read_regular(self._folder / entry["path"]) != self._payloads[identity]
                ):
                    raise ValueError("Comparison source reading cannot be authenticated")
            readings = self._comparison_readings()
            reference = comparison["reference"]
            if reference is not None and "/".join(_reading_pair(reference)) not in readings:
                raise ValueError("Comparison reference is unavailable")
            if (
                _comparison_descriptor(readings, comparison["white"])
                != comparison["white_descriptor"]
            ):
                raise ValueError("Declared white assumptions cannot be authenticated")
            return deepcopy(comparison)

    def _comparison_saved(self):
        return (
            self._saved_comparison_version == self._data["comparison"]["version"]
            and self._saved_comparison == self._data["comparison"]
        )

    def _own_retired_publication_locks(self):
        """Use acquired identities and retained objects, never filesystem lookup."""
        return tuple(
            lock for lock in tuple(exports._RETIRED_PUBLICATION_LOCKS.values())
            if lock.path.name in exports._LOCK_NAMES
            and lock.directory_identity == self._folder_identity
            and lock.release_failure is not None
        )

    def _process_exit_ready(self):
        return (
            bool(self._own_retired_publication_locks())
            and not self._starting
            and (self._active is None or self._data["status"] == "abandoned"
                 and self._operation is not None and self._operation.done)
            and (self._operation is None or self._operation.done)
            and not self._metadata_saving
            and not self._companions_saving
            and (
                self._stop_confirmed is True and not _UNCERTAIN_OWNERS
                or self._data["status"] == "abandoned"
                and all(owner is self for owner in _UNCERTAIN_OWNERS)
            )
        )

    def _comparison_readings(self):
        return {
            f"{record['operation_id']}/{patch['observation']['observation_id']}": dict(
                observation=patch["observation"]
            )
            for record in self._record_views.values()
            for patch in record["patches"]
        }

    def annotation_details(self, operation_id: str, observation_id: str) -> dict:
        """Read exact applied metadata and its editing draft without I/O or edits."""
        with self._mutex:
            item = self._annotation(operation_id, observation_id)
            key = operation_id, observation_id
            return deepcopy(dict(
                operation_id=operation_id,
                observation_id=observation_id,
                applied=dict(
                    item, saved=self._saved_annotation_versions.get(key) == item["version"]
                ),
                draft=self._annotation_drafts.get(key),
            ))

    def command_outcome(self, command_id: str) -> dict:
        with self._mutex:
            if command_id not in self._commands:
                raise ValueError("Unknown command identity")
            return deepcopy(self._commands[command_id])

    def command(
        self, command_id: str, generation: int, action: str, payload: dict, *, batch_id: str
    ) -> dict:
        """Admit/deduplicate one validated command and report its actual outcome.

        A refused stale generation is not reserved and can be retried explicitly
        under a new binding. Once reserved, the ID is tied to semantic content for
        the application lifetime, including completed and refused commands.
        """
        _uuid(command_id)
        _uuid(batch_id)
        _text(action, 40)
        if type(generation) is not int or not isinstance(payload, dict):
            raise ValueError("Command needs an integer generation and object payload")
        signature = _json(dict(batch_id=batch_id, action=action, payload=payload))
        if len(signature.encode("ascii")) > _MAX_COMMAND_BYTES:
            raise ValueError("Command is too large")
        content_sha256 = _digest(signature.encode("ascii"))
        # Bind effect to the same owned JSON values used by duplicate detection.
        payload = _load(signature)["payload"]
        with self._mutex:
            if batch_id != self._data["batch_id"]:
                return dict(admission="refused", reason="Command belongs to another batch owner")
            previous = self._commands.get(command_id)
            if previous is not None:
                if previous["content_sha256"] != content_sha256:
                    return dict(
                        admission="refused", reason="Command ID reused with different content"
                    )
                return deepcopy(previous)
            if generation != self._generation:
                return dict(admission="refused", reason="Stale renderer generation")
            outcome = dict(
                admission="refused",
                state="refused",
                reason=None,
                content_sha256=content_sha256,
                operation_id=None,
            )
            if action != "select":
                self._commands[command_id] = outcome
            try:
                if self._closed:
                    raise ValueError("Application is closed")
                if action in {
                    "measure", "measure_chart", "calibrate", "repeat_sample", "run_workflow",
                }:
                    self._admit(command_id, action, payload, outcome)
                else:
                    self._apply(action, payload, outcome)
            except (OSError, ValueError, KeyError, RuntimeError) as error:
                if action in {"annotate", "edit_annotation_draft"}:
                    key = payload.get("operation_id"), payload.get("observation_id")
                    if all(isinstance(part, str) for part in key):
                        draft = self._annotation_drafts.get(key)
                        if (
                            draft is not None
                            and outcome["admission"] != "admitted"
                            and draft["state"] not in {"pending", "saving", "unsaved"}
                        ):
                            draft.update(state="refused", reason=str(error))
                if outcome["admission"] == "admitted":
                    outcome.update(state="failed", reason=str(error))
                else:
                    outcome.update(admission="refused", state="refused", reason=str(error))
            except BaseException as error:
                # The identity stays reserved with its content, so an identical
                # re-send reads what happened instead of repeating an effect.
                if (
                    action in {"close", "finish", "another_folder"}
                    and outcome["admission"] == "admitted"
                    and outcome["state"] == "pending"
                ):
                    outcome.update(state="failed", reason=self._publication_reason(error))
                elif outcome["admission"] != "admitted" and outcome["reason"] is None:
                    # A control signal interrupted the handler; any other
                    # uncaught exception is a failure of the handler itself and
                    # must not read as an interruption on re-send.
                    outcome.update(
                        state="refused",
                        reason=f"Interrupted before admission: {type(error).__name__}"
                        if not isinstance(error, Exception)
                        else f"Failed before admission: {type(error).__name__}: {error}",
                    )
                self._revision += 1
                raise
            self._revision += 1
            return deepcopy(outcome)

    def _mutable_idle(self):
        _require_publication_cleanup(self._folder)
        if self._metadata_saving:
            raise ValueError("Metadata publication is pending")
        if self._active is not None:
            raise ValueError("Operation is still starting or active")
        if self._data["status"] != "open":
            raise ValueError("Batch is finished")

    def _admit(self, command_id, action, payload, outcome):
        fields = {"context_id", "draft_id", "draft_version"}
        if action == "repeat_sample":
            fields |= {"operation_id", "observation_id"}
        elif action == "run_workflow":
            fields |= {"workflow", "patch_ids", "layout"}
        elif action == "measure_chart":
            fields.add("chart_revision")
        _fields(payload, fields)
        if (action == "run_workflow" and isinstance(payload["workflow"], str)
                and payload["workflow"] in {"spot", "calibrate"}):
            raise ValueError("Use the ordinary Measure or Calibrate action for this workflow")
        self._mutable_idle()
        if _UNCERTAIN_OWNERS:
            raise ValueError("Unconfirmed acquisition cleanup blocks acquisition in this process")
        if self._save_reason:
            raise ValueError(f"Save previous work before acquisition: {self._save_reason}")
        context_current, context_reason = self._context_status()
        if not context_current:
            raise ValueError(context_reason)
        draft = self._current_draft(payload["draft_id"], payload["draft_version"])
        if payload["context_id"] != self._data["context_id"]:
            raise ValueError("Stale acquisition context")
        chart = self._data["chart"]
        if chart is not None and action in {"measure", "repeat_sample", "run_workflow"}:
            raise ValueError("Use the guided chart action while this batch owns a chart")
        if action == "measure_chart":
            chart = self._current_chart(payload["chart_revision"])
        repeat_of = None
        if action == "repeat_sample":
            _uuid(payload["operation_id"])
            _text(payload["observation_id"], 1024)
            source = next(
                (
                    entry
                    for entry in self._data["operations"]
                    if entry["operation_id"] == payload["operation_id"]
                ),
                None,
            )
            result = self._retained.get(source["command_id"]) if source is not None else None
            if (
                result is None
                or result.status != "completed"
                or result.workflow != "spot"
                or not any(
                    patch.observation.observation_id == payload["observation_id"]
                    for patch in result.patches
                )
            ):
                raise ValueError("Repeat needs an accepted retained spot and reading pair")
            _check_result(result, source, self._data["descriptor"])
            draft = dict(deepcopy(source["draft"]), notes="")
            repeat_of = {key: payload[key] for key in ("operation_id", "observation_id")}
        elif action in {"measure", "measure_chart"}:
            # Refuse an unrepresentable next label before dispatch, never after
            # a successfully retained reading has consumed the current target.
            self._resolve_sample_name(draft["name"], draft["number_samples"])
        workflow = payload["workflow"] if action == "run_workflow" else (
            "calibrate" if action == "calibrate" else "spot"
        )
        request_json = _json(self._request(
            draft, workflow,
            **({"patch_ids": payload["patch_ids"], "layout": payload["layout"]}
               if action == "run_workflow" else {}),
        ))
        intended = chart_context.intended(self._data["descriptor"], _load(request_json))
        chart_attempt_id = str(uuid4()) if action == "measure_chart" else None
        if chart_attempt_id is not None:
            # Prove all chart guards before preparing an acquisition handle.
            chart_state.begin_attempt(
                chart, attempt_id=chart_attempt_id, command_id=command_id,
                operation_id=str(uuid4()), request=_load(request_json),
                intended_context=intended,
            )
        operation = entry = launch = None
        intent_reserved = False
        previous_facts = self._stop_confirmed, self._instrument_state
        try:
            diagnostics = self._data["native_diagnostics"]
            native_kwargs = {} if diagnostics["debug_level"] == 0 else dict(
                native_diagnostic_target=self._folder / f"diagnostic-{command_id}.log",
                native_debug_level=diagnostics["debug_level"],
                native_diagnostic_limit_bytes=diagnostics["limit_bytes"],
            )
            if native_kwargs:
                self._diagnostic_targets[command_id] = native_kwargs["native_diagnostic_target"]
            operation = self._frontend.prepare(request_json, **native_kwargs)
            # Retain this exact handle and identity before any durable intent
            # write; every later statement is covered by the recovery below,
            # whose own re-reservation is covered by its inner handler.
            self._operation = operation
            self._active = command_id
            outcome["operation_id"] = operation.operation_id
            self._starting = True
            self._cancel_requested = False
            self._stop_confirmed = False
            self._instrument_state = "unknown"
            entry = self._intent_entry(command_id, operation, draft, repeat_of)
            self._data["operations"].append(entry)
            if chart_attempt_id is not None:
                self._data["chart"] = chart_state.begin_attempt(
                    chart, attempt_id=chart_attempt_id, command_id=command_id,
                    operation_id=operation.operation_id, request=_load(operation.request_json),
                    intended_context=intended,
                )
            self._touch()
            self._persist()
            intent_reserved = True
            outcome.update(admission="admitted", state="starting")
            launch = _ApplicationWorker(
                self._run,
                (entry, operation),
                f"batch-{command_id}",
            )
            launch.start()
        except BaseException as error:
            if operation is None:
                # Preparation refused, or a control signal landed before its
                # handle was bound to a name: nothing is held to drain.
                raise
            if not intent_reserved:
                try:
                    if not any(item is entry for item in self._data["operations"]):
                        # The handle is held but its intent was not reserved yet.
                        # Reserve it so the drain retains the cancelled outcome under it.
                        entry = self._intent_entry(command_id, operation, draft, repeat_of)
                        self._data["operations"].append(entry)
                        self._touch()
                    if chart_attempt_id is not None and not any(
                        item["command_id"] == command_id
                        for chart_pass in self._data["chart"]["passes"]
                        for item in chart_pass["attempts"]
                    ):
                        self._data["chart"] = chart_state.begin_attempt(
                            chart, attempt_id=chart_attempt_id, command_id=command_id,
                            operation_id=operation.operation_id,
                            request=_load(operation.request_json), intended_context=intended,
                        )
                except BaseException as second:
                    # A second control signal inside the recovery. No durable
                    # intent names this handle and nothing was dispatched: an
                    # entry this recovery appended is withdrawn, the handle is
                    # cancelled and the name released, rather than keeping an
                    # active command that no drainer will ever drain or a
                    # reserved entry no publication could ever resolve.
                    self._data["operations"] = [
                        item for item in self._data["operations"] if item is not entry
                    ]
                    if chart_attempt_id is not None:
                        self._data["chart"] = chart
                    _, failures = self._recover_startup_failure(
                        entry, operation, error, drain=False,
                    )
                    self._active, self._operation, self._starting = None, None, False
                    self._stop_confirmed, self._instrument_state = previous_facts
                    reason = (
                        f"{type(error).__name__}: {self._publication_reason(error)}; "
                        f"reservation interrupted: {type(second).__name__}"
                    )
                    if failures:
                        reason += "; " + "; ".join(failures)
                    outcome.update(state="refused", reason=reason)
                    raise second
                reason = f"{type(error).__name__}: {self._publication_reason(error)}"
                interruption, failures = self._recover_startup_failure(entry, operation, error)
                if failures:
                    reason += "; " + "; ".join(failures)
                outcome.update(state="refused", reason=reason)
                if interruption is not None:
                    raise interruption
                raise ValueError(f"Cannot reserve operation at {self._folder}: {reason}") from error
            reason = f"Application drainer startup failed: {type(error).__name__}: {error}"
            unclaimed = launch is None or launch.revoke_if_unclaimed()
            interruption, failures = self._recover_startup_failure(
                entry,
                operation,
                error,
                drain=unclaimed,
            )
            if failures:
                reason += "; " + "; ".join(failures)
            if unclaimed:
                # Revocation excludes even a late launched callback. This is
                # the sole drainer for a proved never-dispatched handle.
                outcome["state"] = "failed"
            # A claimed drainer keeps sole ownership regardless of done/alive;
            # only its retained terminal facts can confirm acquisition cleanup.
            outcome["reason"] = reason
            if interruption is not None:
                raise interruption
            if unclaimed:
                raise ValueError(reason) from error

    def _intent_entry(self, command_id, operation, draft, repeat_of):
        return dict(
            command_id=command_id,
            path=f"operation-{command_id}.json",
            context_id=self._data["context_id"],
            request=_load(operation.request_json),
            draft=deepcopy(draft),
            operation_id=operation.operation_id,
            state="reserved",
            sha256=None,
            reason=None,
            recovery=None,
            repeat_of=repeat_of,
            native_diagnostics=_diagnostic_metadata(operation.native_diagnostics(), command_id)
            if self._data["native_diagnostics"]["debug_level"] else None,
        )

    def _capture_diagnostics(self, entry, operation):
        """Retain the shared collector's closed original independently of result parsing."""
        if entry["native_diagnostics"] is None:
            return None
        identity = entry["command_id"]
        try:
            metadata = _diagnostic_metadata(operation.native_diagnostics(), identity)
            if metadata is None:
                raise ValueError("Prepared native diagnostics lost their capture metadata")
            payload = None
            if metadata["sha256"] is not None:
                payload = _diagnostic_bytes(
                    operation.native_diagnostic_bytes(), metadata,
                )
            entry["native_diagnostics"] = deepcopy(metadata)
            if payload is not None:
                self._diagnostic_payloads[identity] = payload
            self._diagnostic_reasons.pop(identity, None)
            self._diagnostic_operations.pop(identity, None)
        except BaseException as error:
            self._diagnostic_reasons[identity] = f"{type(error).__name__}: {error}"
            # Confirmed-stop recovery can clear the active operation. Keep only
            # failed collectors' original handles for an explicit save retry.
            self._diagnostic_operations[identity] = operation
            return error if not isinstance(error, Exception) else None
        return None

    def _retry_diagnostics(self):
        entries = {entry["command_id"]: entry for entry in self._data["operations"]}
        for identity, operation in list(self._diagnostic_operations.items()):
            entry = entries[identity]
            if (operation.operation_id != entry["operation_id"] or not operation.done
                    or identity not in self._retained and identity not in self._unknown):
                raise ValueError("Wait for the original terminal diagnostic operation before retry")
            interruption = self._capture_diagnostics(entry, operation)
            if interruption is not None:
                raise interruption

    def _recover_startup_failure(self, entry, operation, error, *, drain=True):
        """Recover the held handle without replacing the first control signal."""
        interruption = error if not isinstance(error, Exception) else None
        failures = []
        cancellation_error = None
        try:
            operation.cancel(operation.operation_id)
        except BaseException as failure:
            cancellation_error = failure
            failures.append(f"cancellation failed: {type(failure).__name__}: {failure}")
            if interruption is None and not isinstance(failure, Exception):
                interruption = failure
        if drain:
            try:
                if cancellation_error is not None and not operation.done:
                    # An unfinished prepared handle cannot be polled to done
                    # after cancellation failed. Retain it with unknown cleanup.
                    failure = self._drainer_failed(
                        entry, operation, error, cancellation_error=cancellation_error,
                    )
                    if interruption is None:
                        interruption = failure
                else:
                    self._run(entry, operation, dispatch=False)
            except BaseException as failure:
                failures.append(f"terminal recovery failed: {type(failure).__name__}: {failure}")
                if interruption is None and not isinstance(failure, Exception):
                    interruption = failure
        return interruption, failures

    def _request(self, draft, workflow=None, **operands):
        return _batch_request(
            self._data["descriptor"], self._data["mode"], draft, workflow, **operands,
        )

    def _run(self, entry, operation, *, dispatch=True):
        interruption = None
        try:
            interruption = self._drain(entry, operation, dispatch=dispatch)
        except BaseException as error:
            interruption = self._drainer_failed(entry, operation, error)
        # A classified terminal interruption must not enter the failure handler
        # again and overwrite its retained raw evidence or start another drain.
        if interruption is not None:
            raise interruption

    @staticmethod
    def _cancel_after_dispatch_failure(operation):
        """Request cancellation of a handle whose dispatch raised; return any failure."""
        try:
            operation.cancel(operation.operation_id)
        except BaseException as cancellation_error:
            return cancellation_error
        return None

    def _drain(self, entry, operation, *, dispatch=True):
        # The held operation owns dispatch and cleanup; an exception is not
        # evidence that a committed worker has stopped. Always drain its handle.
        dispatch_error = None
        interruption = None
        dispatch_attempted = dispatch and not operation.done
        if dispatch_attempted:
            try:
                operation.dispatch()
            except BaseException as error:
                if isinstance(error, OperationBusyError) and operation.done:
                    # The frontend refused before admitting the attempt because a
                    # cancellation had already resolved the prepared handle.
                    # Nothing was dispatched: neither a started intent nor a
                    # dispatch failure is recorded; retention classifies the cancel.
                    dispatch_attempted = False
                else:
                    dispatch_error = f"Operation dispatch raised {type(error).__name__}: {error}"
                    if not isinstance(error, Exception):
                        interruption = error
                    cancellation_error = self._cancel_after_dispatch_failure(operation)
                    if cancellation_error is not None:
                        dispatch_error += (
                            f"; cancellation failed: {type(cancellation_error).__name__}: "
                            f"{cancellation_error}"
                        )
                        if interruption is None and not isinstance(cancellation_error, Exception):
                            interruption = cancellation_error
                        if not operation.done:
                            return self._drainer_failed(
                                entry,
                                operation,
                                error,
                                cancellation_error=cancellation_error,
                            )
        try:
            with self._mutex:
                # A startup exception can race an already-launched drainer after
                # synchronous no-dispatch retention. Never retain its result twice.
                if entry["command_id"] in self._retained or entry["command_id"] in self._unknown:
                    return interruption
                if dispatch_attempted:
                    self._starting = False
                    entry.update(state="started")
                    self._commands[entry["command_id"]].update(
                        state="active", operation_id=operation.operation_id
                    )
                    if dispatch_error:
                        self._events.append(
                            dict(
                                kind="application_dispatch_failed",
                                operation_id=operation.operation_id,
                                payload=dict(reason=dispatch_error),
                            )
                        )
                    self._touch()
                    try:
                        self._persist()
                    except (OSError, ValueError) as error:
                        self._save_reason = self._publication_reason(error)
            while True:
                event = operation.poll_event_json()
                if event:
                    self._record_event(event, operation)
                elif operation.done:
                    # Completion may have raced the first empty poll. The frontend
                    # promises no new events after done, so drain that final queue.
                    while event := operation.poll_event_json():
                        self._record_event(event, operation)
                    break
                else:
                    # Sleeping does not advance acquisition and is independent of rendering.
                    threading.Event().wait(0.005)
        except BaseException as error:
            if interruption is None or error is interruption:
                raise
            # Keep one unexpected-failure classifier in _run. Preserve the
            # earlier control and the later failure in its saved recovery reason.
            if dispatch_error:
                interruption.add_note(dispatch_error)
            interruption.add_note(
                f"Later application drain failed: {type(error).__name__}: "
                f"{self._publication_reason(error)}"
            )
            raise interruption from error
        staged = None
        with self._mutex:
            retained_ready = False
            diagnostic_control = self._capture_diagnostics(entry, operation)
            if interruption is None:
                interruption = diagnostic_control
            try:
                raw_result = operation.result_json()
                result = result_from_json(raw_result)
                _check_result(result, entry, self._data["descriptor"])
                payload = result_to_json(result).encode("utf-8")
                # Stage fallible view/metadata construction before installing
                # retained ownership or adding a record to the save-retry queue.
                record_view = _load(payload)
                annotations = [
                    dict(
                        operation_id=result.operation_id,
                        observation_id=patch.observation.observation_id,
                        name=entry["draft"]["name"] if result.workflow == "spot"
                        else patch.patch_id,
                        notes=entry["draft"]["notes"],
                        version=0,
                    )
                    for patch in result.patches
                ]
                previous_draft, next_draft = self._data["draft"], None
                if (
                    result.status == "completed"
                    and result.workflow == "spot"
                    and result.patches
                    and entry["repeat_of"] is None
                ):
                    following = self._following_name(entry["draft"])
                    if entry["draft"]["number_samples"]:
                        stem, marker, ordinal, width = _numbered_name(following)
                        try:
                            _following_label(stem, marker, ordinal + 1, width)
                        except ValueError:
                            # Keep a manifest-valid marker when its fresh draft
                            # would have an impossible successor. The acquired
                            # ordinal remains consumed; the resolver refuses the
                            # next acquisition until the operator changes it.
                            following = entry["draft"]["name"]
                    next_draft = dict(
                        self._new_draft(),
                        name=following,
                        options=deepcopy(entry["draft"]["options"]),
                        number_samples=entry["draft"]["number_samples"],
                    )
                self._retained[entry["command_id"]] = result
                self._payloads[entry["command_id"]] = payload
                self._record_views[entry["command_id"]] = record_view
                self._unsaved_ids.add(entry["command_id"])
                self._stop_confirmed = result.software_stopped
                self._instrument_state = result.instrument_state
                self._data["annotations"].extend(annotations)
                self._commands[entry["command_id"]].update(
                    state="completed",
                    result_status=result.status,
                    software_stopped=result.software_stopped,
                    instrument_state=result.instrument_state,
                )
                if result.software_stopped:
                    self._active, self._operation, self._pending = None, None, None
                    self._presentation = None
                else:
                    self._cleanup_reason = (
                        "Software stop is unconfirmed; retain operation ownership"
                    )
                    if self not in _UNCERTAIN_OWNERS:
                        _UNCERTAIN_OWNERS.append(self)
                self._starting = False
                if next_draft is not None:
                    self._data["draft"] = next_draft
                retained_ready = True
                self._chart_terminal(entry, result)
                self._touch()
                staged = self._stage_save()
            except BaseException as error:
                if retained_ready:
                    self._save_reason = (
                        f"Retained-result publication failed: {type(error).__name__}: {error}"
                    )
                    self._revision += 1
                    if interruption is None and not isinstance(error, Exception):
                        interruption = error
                    return interruption
                # A partial installation is not a publication-ready result.
                # Remove only this operation's fragments; preserve prior rows.
                identity = entry["command_id"]
                self._retained.pop(identity, None)
                self._payloads.pop(identity, None)
                self._record_views.pop(identity, None)
                self._unsaved_ids.discard(identity)
                if locals().get("next_draft") is not None and self._data["draft"] is next_draft:
                    self._data["draft"] = previous_draft
                self._data["annotations"] = [
                    item
                    for item in self._data["annotations"]
                    if item["operation_id"] != entry["operation_id"]
                ]
                reason = f"Terminal retention failed: {type(error).__name__}: {error}"
                fact = self._terminal_facts.get(operation.operation_id, {})
                self._starting = False
                self._stop_confirmed = fact.get("software_stopped") is True
                reported_state = fact.get("instrument_state")
                self._instrument_state = (
                    reported_state
                    if reported_state in ("not_applicable", "confirmed_ready", "unknown")
                    else "unknown"
                )
                self._unknown[entry["command_id"]] = dict(
                    schema_version=1,
                    command_id=entry["command_id"],
                    operation_id=operation.operation_id,
                    request=entry["request"],
                    result_json=locals().get("raw_result", ""),
                    reason=reason,
                    software_stopped=fact.get("software_stopped"),
                    instrument_state=fact.get("instrument_state", "unknown"),
                )
                entry.update(state="terminal_unknown", reason=reason)
                self._chart_terminal(entry, reason=reason)
                if self._stop_confirmed:
                    self._active, self._operation = None, None
                    self._cleanup_reason = None
                else:
                    self._cleanup_reason = (
                        "Software stop is unconfirmed; retain operation ownership"
                    )
                    if self not in _UNCERTAIN_OWNERS:
                        _UNCERTAIN_OWNERS.append(self)
                self._pending = None
                self._presentation = None
                self._commands[entry["command_id"]].update(state="unknown", reason=reason)
                self._touch()
                if interruption is None and not isinstance(error, Exception):
                    interruption = error
                # Classification already owns the exact raw terminal evidence.
                # A recovery-write failure must not reclassify it as an empty
                # unexpected-drainer result or replace its original reason.
                try:
                    self._save()
                except BaseException as save_error:
                    self._note_publication(getattr(save_error, "publication", None), complete=False)
                    self._save_reason = (
                        f"Recovery publication failed: {type(save_error).__name__}: {save_error}"
                    )
                    if interruption is None and not isinstance(save_error, Exception):
                        interruption = save_error
            self._revision += 1
        if staged is not None:
            receipts = {}
            # The retained result is installed and the manifest is in flight:
            # publish without the mutex so readers are not held for the syncs.
            try:
                receipts = self._publish_staged(staged)
                with self._mutex:
                    self._accept_save(staged, receipts)
            except BaseException as error:
                with self._mutex:
                    if receipts.get("companion_start_error") is not error:
                        self._save_reason = (
                            f"Retained-result publication failed: {type(error).__name__}: {error}"
                        )
                    self._revision += 1
                if interruption is None and not isinstance(error, Exception):
                    interruption = error
        return interruption

    def _drainer_failed(self, entry, operation, error, *, cancellation_error=None):
        """Retain a callback failure and return its first control interruption."""
        interruption = error if not isinstance(error, Exception) else None
        with self._mutex:
            reason = (
                f"Application draining failed: {type(error).__name__}: "
                f"{self._publication_reason(error)}"
            )
            diagnostic_control = self._capture_diagnostics(entry, operation)
            if interruption is None:
                interruption = diagnostic_control
            if cancellation_error is None:
                try:
                    operation.cancel(operation.operation_id)
                except BaseException as failure:
                    cancellation_error = failure
            if cancellation_error is not None:
                reason += (f"; cancellation failed: {type(cancellation_error).__name__}: "
                           f"{cancellation_error}")
                if interruption is None and not isinstance(cancellation_error, Exception):
                    interruption = cancellation_error
            fact = self._terminal_facts.get(operation.operation_id, {})
            self._stop_confirmed = fact.get("software_stopped") is True
            reported_state = fact.get("instrument_state")
            self._instrument_state = reported_state if reported_state in (
                "not_applicable", "confirmed_ready", "unknown",
            ) else "unknown"
            self._starting = False
            self._pending = None
            self._presentation = None
            self._unknown[entry["command_id"]] = dict(
                schema_version=1, command_id=entry["command_id"],
                operation_id=operation.operation_id, request=entry["request"],
                result_json="", reason=reason, software_stopped=fact.get("software_stopped"),
                instrument_state=fact.get("instrument_state", "unknown"),
            )
            entry.update(state="terminal_unknown", reason=reason)
            self._chart_terminal(entry, reason=reason)
            self._commands[entry["command_id"]].update(state="unknown", reason=reason)
            if self._stop_confirmed:
                self._active, self._operation = None, None
                self._cleanup_reason = None
            else:
                self._cleanup_reason = "Software stop is unconfirmed; retain operation ownership"
                if self not in _UNCERTAIN_OWNERS:
                    _UNCERTAIN_OWNERS.append(self)
            self._touch()
            try:
                self._save()
            except BaseException as save_error:
                self._note_publication(getattr(save_error, "publication", None), complete=False)
                self._save_reason = (
                    f"Recovery publication failed: {type(save_error).__name__}: {save_error}"
                )
                if interruption is None and not isinstance(save_error, Exception):
                    interruption = save_error
            self._revision += 1
        return interruption

    def _record_event(self, event, operation):
        with self._mutex:
            try:
                data = _load(event)
                if (
                    not isinstance(data, dict)
                    or data.get("operation_id") != operation.operation_id
                    or not isinstance(data.get("payload"), dict)
                    or not isinstance(data.get("kind"), str)
                ):
                    raise ValueError("Event does not belong to the bound operation")
                if data["kind"] == "instruction_required":
                    _validate_instruction_payload(data["payload"])
                elif data["kind"] == "presentation_required":
                    _validate_presentation_payload(data["payload"])
            except (ValueError, TypeError) as error:
                self._events.append(
                    dict(
                        kind="application_event_rejected",
                        operation_id=operation.operation_id,
                        payload=dict(reason=str(error)),
                    )
                )
                self._revision += 1
                return
            self._events.append(data)
            if data["kind"] == "terminal" and type(data["payload"].get("software_stopped")) is bool:
                self._terminal_facts[operation.operation_id] = data["payload"]
            if data["kind"] == "instruction_required":
                self._pending = dict(data["payload"], operation_id=operation.operation_id)
            elif data["kind"] == "presentation_required":
                self._presentation = dict(data["payload"], operation_id=operation.operation_id)
            self._revision += 1

    def _annotation(self, operation_id, observation_id):
        _uuid(operation_id)
        _text(observation_id, 1024)
        item = next(
            (
                item
                for item in self._data["annotations"]
                if item["operation_id"] == operation_id and item["observation_id"] == observation_id
            ),
            None,
        )
        if item is None:
            raise ValueError("Unknown reading identity")
        return item

    def _edit_chart(self, action, payload, outcome):
        presentation_only = (action == "chart_edit"
                             and isinstance(payload.get("action"), str)
                             and payload["action"] in {"rotate", "inspect_cell"})
        if not presentation_only:
            self._mutable_idle()
        elif self._data["status"] != "open":
            raise ValueError("Finished charts are inspected through read-only queries")
        if self._save_reason:
            raise ValueError("Save retained work before changing the guided chart")
        chart = self._data["chart"]
        if action in {"configure_chart", "continue_chart"}:
            if chart is not None or self._data["operations"]:
                raise ValueError("Start a guided chart in a new empty batch")
            request = self._request(self._data["draft"], "spot")
            context = chart_context.intended(self._data["descriptor"], request)
            if action == "configure_chart":
                _fields(payload, {"definition_token", "mapping_confirmation"})
                _uuid(payload["definition_token"])
                staged = self._staged_chart_definition
                if staged is None or staged["token"] != payload["definition_token"]:
                    raise ValueError("Unknown or replaced chart definition token")
                self._chart_definition_binding(**staged["binding"])
                if _json(payload["mapping_confirmation"]) != staged["mapping_confirmation"]:
                    raise ValueError("Confirm the staged chart's exact mapping and orientation")
                chart = chart_state.new_state(
                    _load(staged["definition_json"]), context, run_id=str(uuid4()),
                    segment_id=self._data["batch_id"], pass_id=str(uuid4()),
                )
            else:
                _fields(payload, {
                    "folder", "source_sha256", "action", "source_pass_id",
                    "acknowledged_material",
                })
                _text(payload["folder"], 16384)
                _uuid(payload["source_pass_id"])
                folder = Path(payload["folder"]).absolute()
                source_bytes = _read_regular(folder / "batch.json")
                if _digest(source_bytes) != payload["source_sha256"]:
                    raise ValueError("Continuation manifest changed from the selected source")
                review = review_saved(folder)
                if review["manifest_sha256"] != payload["source_sha256"]:
                    raise ValueError("Continuation source changed during read-only review")
                source_chart = review["manifest"]["chart"]
                if source_chart is None or not review["chart"]["available"]:
                    raise ValueError("Continuation requires an authenticated saved chart")
                source_pass = next((item for item in source_chart["passes"]
                                    if item["id"] == payload["source_pass_id"]), None)
                if source_pass is None:
                    raise ValueError("Unknown continuation source pass")
                if payload["action"] == "continue":
                    try:
                        context = chart_context.declared_continuation(
                            self._data["descriptor"], request, source_pass["context"],
                            payload["acknowledged_material"],
                        )
                    except ValueError as error:
                        raise ValueError(
                            f"{error}. Choose New pass for independent measurements."
                        ) from error
                elif payload["acknowledged_material"] != []:
                    raise ValueError("An empty new pass does not accept old material declarations")
                operation_bytes = {}
                for entry in review["manifest"]["operations"]:
                    reference = entry["recovery"] if entry["state"] == "terminal_unknown" else entry
                    if reference is not None:
                        operation_bytes[reference["path"]] = _read_regular(
                            folder / reference["path"]
                        )
                    diagnostic = entry["native_diagnostics"]
                    if diagnostic is not None and diagnostic["sha256"] is not None:
                        operation_bytes[diagnostic["path"]] = _read_regular(
                            folder / diagnostic["path"]
                        )
                captured = chart_sources.collect_source(
                    review, source_bytes, operation_bytes, _SourceFiles(folder),
                    validate_manifest=_manifest, check_result=_check_result,
                    check_recovery=_check_recovery,
                )
                try:
                    chart = chart_state.continue_state(
                        source_chart, context, segment_id=self._data["batch_id"],
                        source_id=captured["source"]["source_id"], source_bytes=source_bytes,
                        source_sha256=payload["source_sha256"], action=payload["action"],
                        pass_id=payload["source_pass_id"], new_pass_id=str(uuid4()),
                    )
                except ValueError as error:
                    if payload["action"] != "continue":
                        raise
                    raise ValueError(
                        f"{error}. Choose New pass for independent measurements."
                    ) from error
                self._source_blobs = captured["blobs"]
                self._source_results = captured["operations"]
            self._data["chart"] = chart
            self._staged_chart_definition = None
            self._touch()
            outcome.update(admission="admitted", state="pending", effect=action)
            if not self._save():
                raise ValueError(self._save_reason)
            self._complete_action(outcome, action)
            return
        if action == "chart_reference":
            _fields(payload, {"chart_revision", "source_pass_id", "qualification", "criteria"})
            chart = self._current_chart(payload["chart_revision"])
            reference = chart_science.prepare_position_reference(
                chart["definition"], self.chart_readings(payload["source_pass_id"]),
                qualification=payload["qualification"], criteria=payload["criteria"],
                observer_csv=RESOURCE.read_bytes(),
            )
            updated = chart_state.set_reference(chart, reference)
        else:
            _fields(payload, {"chart_revision", "action", "operands"})
            chart = self._current_chart(payload["chart_revision"])
            operands = payload["operands"]
            functions = {
                "select_cell": (chart_state.select_cell, {"cell"}),
                "inspect_cell": (chart_state.inspect_cell, {"cell"}),
                "rotate": (chart_state.rotate, {"degrees"}),
                "set_paused": (chart_state.set_paused, {"paused"}),
                "select_attempt": (chart_state.select_attempt, {"pass_id", "cell", "attempt_id"}),
                "resolve_position": (chart_state.resolve_position, {"token", "action"}),
                "accept_context": (chart_state.accept_context, set()),
                "start_pass": (chart_state.start_pass, set()),
            }
            if not isinstance(payload["action"], str) or payload["action"] not in functions:
                raise ValueError("Unknown guided-chart edit")
            function, fields = functions[payload["action"]]
            _fields(operands, fields)
            if payload["action"] in {"accept_context", "start_pass"}:
                operands = dict(pass_id=str(uuid4()))
            updated = function(chart, **operands)
        if _json(chart) == _json(updated):
            outcome.update(admission="no_change", state="completed", effect="chart_unchanged")
            return
        self._data["chart"] = updated
        self._touch()
        if self._active is not None:
            outcome.update(admission="admitted", state="pending")
            self._persist()
            outcome["state"] = "completed"
        else:
            self._schedule_metadata(outcome)

    def _apply(self, action, payload, outcome):
        if action not in {"select", "cancel", "respond", "another_folder", "confirm_process_exit"}:
            _require_publication_cleanup(self._folder)
        if self._metadata_saving and action not in {
            "select",
            "edit_annotation_draft",
            "discard_annotation_draft",
            "cancel",
            "respond",
        }:
            raise ValueError("Metadata publication is pending")
        if self._companions_saving and (
            action in {"another_folder", "close", "abandon", "confirm_process_exit"}
            or self._data["status"] == "finished"
            and action in {"retry_save", "export_ti3", "retry_companions"}
        ):
            raise ValueError("Wait for automatic companion publication before this action")
        if action in {"configure_chart", "continue_chart", "chart_edit", "chart_reference"}:
            return self._edit_chart(action, payload, outcome)
        if action == "configure_native_diagnostics":
            self._mutable_idle()
            settings = _diagnostic_settings(payload)
            offer = self._data["descriptor"].get("limits", {}).get("native_diagnostics", {})
            if settings["debug_level"] and offer.get("available") is not True:
                raise ValueError(offer.get("unavailable_reason") or
                                 "This frontend does not offer authenticated native diagnostics")
            if settings == self._data["native_diagnostics"]:
                outcome.update(admission="no_change", state="completed")
                return
            self._data["native_diagnostics"] = deepcopy(settings)
            self._touch()
            self._schedule_metadata(outcome)
            return
        if action == "confirm_process_exit":
            _fields(payload, {"discard_token"})
            if not self._process_exit_ready():
                raise ValueError(
                    "Process exit requires own retired publication locks and software stop"
                )
            if payload["discard_token"] != self.discard_summary()["token"]:
                raise ValueError("Process exit requires a current discard confirmation")
            self._process_exit_acknowledged = True
            outcome.update(
                admission="admitted", state="completed", effect="process_exit_acknowledged",
            )
            return
        if action == "cancel":
            _fields(payload, {"command_id"})
            if self._active is None or payload["command_id"] != self._active:
                raise ValueError("Cancel does not target the current operation command")
            if self._operation is not None and self._operation.done:
                raise ValueError("Terminal operation cannot accept another cancellation")
            self._cancel_requested = True
            if self._operation is not None:
                self._operation.cancel(self._operation.operation_id)
            outcome.update(admission="admitted", state="completed", effect="cancellation_requested")
            return
        if action == "respond":
            _fields(payload, {"operation_id", "request_id", "response"})
            # No retired owner holds a pending instruction: retirement needs an
            # idle or terminal owner, so publication cleanup is not re-checked.
            if self._operation is None or self._pending is None:
                raise ValueError("No pending instruction")
            self._operation.respond(
                payload["operation_id"], payload["request_id"], payload["response"]
            )
            self._pending = None
            outcome.update(admission="admitted", state="completed", effect="response_consumed")
            return
        if action == "presented":
            _fields(payload, {"operation_id", "request_id", "stamp"})
            _require_publication_cleanup(self._folder)
            if self._operation is None or self._presentation is None:
                raise ValueError("No pending controlled presentation")
            self._operation.presented(
                payload["operation_id"], payload["request_id"], _json(payload["stamp"]),
            )
            self._presentation = None
            outcome.update(admission="admitted", state="completed", effect="presentation_consumed")
            return
        if action in {"retry_save", "export_ti3", "retry_companions"}:
            _fields(payload, set())
            if self._active is not None and (self._operation is None or not self._operation.done):
                raise ValueError("Wait for retained terminal outcome before save retry")
            if action == "retry_save":
                self._retry_diagnostics()
            if self._data["status"] == "finished":
                self._finished_export()
            elif not self._save(export=True):
                raise ValueError(self._save_reason)
        elif action == "another_folder":
            _fields(payload, {"folder"})
            _text(payload["folder"], 16384)
            if self._active is not None and (self._operation is None or not self._operation.done):
                raise ValueError("Wait for terminal retention before changing destination")
            self._retry_diagnostics()
            self._another_folder(payload["folder"], outcome)
        elif action in {"finish", "finish_chart"}:
            _fields(payload, {"chart_revision", "allow_incomplete"}
                    if action == "finish_chart" else set())
            self._mutable_idle()
            if self._save_reason or any(
                draft["dirty"] for draft in self._annotation_drafts.values()
            ):
                raise ValueError("Save or discard unresolved metadata before Finish")
            old_chart = deepcopy(self._data["chart"])
            if action == "finish_chart":
                chart = self._current_chart(payload["chart_revision"])
                if type(payload["allow_incomplete"]) is not bool:
                    raise ValueError("Incomplete chart finish requires an explicit boolean")
                self._data["chart"] = chart_state.finish(
                    chart, allow_incomplete=payload["allow_incomplete"],
                )
            elif old_chart is not None:
                raise ValueError("Use Finish chart with explicit coverage acceptance")
            self._data["status"] = "finished"
            self._touch()
            try:
                saved = self._save(export=True)
            except BaseException as error:
                self._save_reason = self._publication_reason(error)
                if self._manifest_bytes == _json(self._data).encode("ascii"):
                    outcome.update(admission="admitted", state="failed", reason=self._save_reason)
                    self._release_lock(failure=error)
                else:
                    self._data["status"] = "open"
                    self._data["chart"] = old_chart
                raise
            if not saved:
                if self._manifest_bytes == _json(self._data).encode("ascii"):
                    outcome.update(admission="admitted", state="failed")
                    self._release_lock(failure=ValueError(self._save_reason))
                else:
                    self._data["status"] = "open"
                    self._data["chart"] = old_chart
                raise ValueError(self._save_reason)
            outcome.update(admission="admitted", state="pending")
            if not self._companions_saving:
                self._release_lock()
        elif action == "edit_draft":
            _fields(payload, {"draft_id", "version", "name", "notes", "options", "number_samples"})
            self._mutable_idle()
            draft = self._current_draft(payload["draft_id"], payload["version"])
            name = self._resolve_sample_name(payload["name"], payload["number_samples"])
            _text(payload["notes"], 65536)
            if not isinstance(payload["options"], dict):
                raise ValueError("Options must be an object")
            options_unchanged = _json(draft["options"]) == _json(payload["options"])
            context_current, context_reason = self._context_status()
            if not context_current and not options_unchanged:
                raise ValueError(context_reason)
            if context_current:
                validation = self._frontend.prepare(
                    _json(self._request(dict(draft, options=payload["options"])))
                )
                validation.cancel(validation.operation_id)
            if draft["name"] == name and options_unchanged and all(
                draft[key] == payload[key] for key in ("notes", "number_samples")
            ):
                outcome.update(admission="no_change", state="completed", effect="draft_unchanged")
                return
            updated_draft = dict(
                draft, name=name, notes=payload["notes"], options=deepcopy(payload["options"]),
                version=draft["version"] + 1, number_samples=payload["number_samples"],
            )
            if self._data["chart"] is not None and not options_unchanged:
                self._data["chart"] = chart_state.propose_context(
                    self._data["chart"], chart_context.intended(
                        self._data["descriptor"], self._request(updated_draft, "spot"),
                    ),
                )
            draft.update(updated_draft)
            self._touch()
            if self._save_reason:
                outcome.update(
                    admission="admitted",
                    state="failed",
                    effect="draft_retained_unsaved",
                    reason=self._save_reason,
                )
                return
            self._schedule_metadata(outcome)
            return
        elif action == "set_comparison_context":
            _fields(payload, {"version", "reference", "white"})
            self._mutable_idle()
            if self._save_reason:
                raise ValueError("Save preceding work before changing comparison context")
            comparison = self._data["comparison"]
            if type(payload["version"]) is not int or payload["version"] != comparison["version"]:
                raise ValueError("Stale comparison version")
            readings = self._comparison_readings()
            reference = payload["reference"]
            if reference is not None and "/".join(_reading_pair(reference)) not in readings:
                raise ValueError("Comparison reference must identify a retained reading")
            descriptor = _comparison_descriptor(readings, payload["white"])
            if all(payload[key] == comparison[key] for key in ("reference", "white")):
                outcome.update(
                    admission="no_change", state="completed", effect="comparison_unchanged"
                )
                return
            comparison.update(
                reference=deepcopy(reference),
                white=deepcopy(payload["white"]),
                white_descriptor=descriptor,
                version=comparison["version"] + 1,
            )
            self._touch()
            self._schedule_metadata(outcome)
            return
        elif action == "edit_annotation_draft":
            _fields(
                payload,
                {"operation_id", "observation_id", "version", "draft_version", "name", "notes"},
            )
            if self._data["status"] != "open":
                raise ValueError("Batch is finished")
            item = self._annotation(payload["operation_id"], payload["observation_id"])
            key = item["operation_id"], item["observation_id"]
            old = self._annotation_drafts.get(key)
            if type(payload["version"]) is not int or payload["version"] != item["version"]:
                raise ValueError("Stale annotation version")
            if type(payload["draft_version"]) is not int or payload["draft_version"] != (
                old["draft_version"] if old else 0
            ):
                raise ValueError("Stale annotation draft version")
            if old and old["state"] in {"pending", "saving"}:
                raise ValueError("Annotation publication is pending")
            _sample_label(payload["name"])
            _text(payload["notes"], 65536)
            dirty = payload["name"] != item["name"] or payload["notes"] != item["notes"]
            self._annotation_drafts[key] = dict(
                operation_id=key[0],
                observation_id=key[1],
                version=item["version"],
                draft_version=payload["draft_version"] + 1,
                name=payload["name"],
                notes=payload["notes"],
                dirty=dirty,
                state="editing" if dirty else "clean",
                reason=None,
            )
        elif action == "discard_annotation_draft":
            _fields(payload, {"operation_id", "observation_id", "draft_version"})
            item = self._annotation(payload["operation_id"], payload["observation_id"])
            key = item["operation_id"], item["observation_id"]
            draft = self._annotation_drafts.get(key)
            if (
                draft is None
                or type(payload["draft_version"]) is not int
                or (payload["draft_version"] != draft["draft_version"])
            ):
                raise ValueError("Unknown or stale annotation draft")
            if draft["state"] in {"pending", "saving"}:
                raise ValueError("Annotation publication is pending")
            del self._annotation_drafts[key]
        elif action == "annotate":
            _fields(payload, {"operation_id", "observation_id", "version", "draft_version"})
            self._mutable_idle()
            if self._save_reason:
                raise ValueError(
                    "Resolve preceding canonical or metadata saving before applying an annotation"
                )
            item = self._annotation(payload["operation_id"], payload["observation_id"])
            key = item["operation_id"], item["observation_id"]
            draft = self._annotation_drafts.get(key)
            if (
                draft is None
                or type(payload["version"]) is not int
                or payload["version"] != item["version"]
                or type(payload["draft_version"]) is not int
                or payload["draft_version"] != draft["draft_version"]
            ):
                raise ValueError("Unknown or stale annotation draft/version")
            _sample_label(draft["name"])
            if not draft["name"].strip():
                raise ValueError("Annotation name must contain non-whitespace text")
            if not draft["dirty"]:
                draft.update(state="clean", reason=None)
                outcome.update(
                    admission="no_change", state="completed", effect="annotation_unchanged"
                )
                return
            item.update(name=draft["name"], notes=draft["notes"], version=item["version"] + 1)
            draft.update(version=item["version"], state="pending", dirty=False, reason=None)
            self._touch()
            self._schedule_metadata(outcome, key)
            return
        elif action == "select":
            _fields(payload, {"operation_id", "observation_id"})
            self._annotation(payload["operation_id"], payload["observation_id"])
            self._selected = deepcopy(payload)
        elif action == "close":
            _fields(payload, {"discard_token"})
            if self._active is not None:
                raise ValueError("Cancel and confirm software stop before closing")
            if (
                self._save_reason
                or self._unresolved_companions()
                or self.discard_summary()["native_diagnostics"]
                or any(draft["dirty"] for draft in self._annotation_drafts.values())
            ) and (payload["discard_token"] != self.discard_summary()["token"]):
                raise ValueError("Unsaved work needs a current discard confirmation")
            outcome.update(admission="admitted", state="pending")
            self._release_lock()
            self._closed = True
        elif action == "abandon":
            _fields(payload, {"discard_token", "reason"})
            _text(payload["reason"], 1024)
            if not payload["reason"].strip():
                raise ValueError("Abandonment requires an explicit reason")
            if self._active is not None and (self._operation is None or not self._operation.done):
                raise ValueError("Cancel and drain the active worker before abandonment")
            summary = self.discard_summary()
            if payload["discard_token"] != summary["token"]:
                raise ValueError("Abandonment requires a current contents confirmation")
            previous_status, previous_shutdown = self._data["status"], self._data["shutdown"]
            self._data["status"] = "abandoned"
            self._data["shutdown"] = dict(
                reason=payload["reason"],
                cleanup_confirmed=self._stop_confirmed,
                instrument_state=self._instrument_state,
                discarded={key: value for key, value in summary.items() if key != "token"},
            )
            self._touch()
            close_abandoned = True
            abandonment_failure = None
            try:
                saved = self._save()
            except BaseException as error:
                payload = _json(self._data).encode("ascii")
                publication = getattr(error, "publication", None)
                published = self._manifest_bytes == payload or (
                    isinstance(publication, PublicationResult)
                    and publication.path == self._folder / "batch.json"
                    and publication.payload == payload
                )
                if published:
                    try:
                        self._assert_folder()
                    except BaseException as failure:
                        error = _preserve_publication_failure(
                            error,
                            failure,
                            "Abandonment folder authentication failed",
                        )
                        published = False
                self._save_reason = self._publication_reason(error)
                outcome.update(admission="admitted", state="failed", reason=self._save_reason)
                if published:
                    self._manifest_bytes = payload
                else:
                    self._data["status"], self._data["shutdown"] = (
                        previous_status,
                        previous_shutdown,
                    )
                    close_abandoned = False
                abandonment_failure = error
                raise error
            finally:
                if close_abandoned:
                    if not self._stop_confirmed and self not in _UNCERTAIN_OWNERS:
                        _UNCERTAIN_OWNERS.append(self)
                    try:
                        self._release_lock(failure=abandonment_failure)
                    except BaseException as error:
                        self._save_reason = self._publication_reason(error)
                        outcome.update(
                            admission="admitted", state="failed", reason=self._save_reason
                        )
                        raise
                    else:
                        self._closed = True
            outcome.update(
                admission="admitted",
                state="completed",
                effect="owner_abandoned",
                shutdown_saved=saved,
                cleanup_confirmed=self._stop_confirmed,
                process_shutdown_required=not self._stop_confirmed,
                reason=None if saved else self._save_reason,
            )
            return
        else:
            raise ValueError("Unknown application action")
        self._complete_action(outcome, action)

    def _complete_action(self, outcome, action):
        outcome.update(admission="admitted", state="completed", effect=action)
        if action in {
            "retry_save", "export_ti3", "retry_companions", "finish", "finish_chart",
            "another_folder",
        }:
            if self._companions_saving:
                outcome["state"] = "pending"
                self._companion_completions.append(outcome)
            elif self._unresolved_companions():
                outcome.update(state="failed", reason="Automatic files still need saving")

    def _finished_export(self):
        self._assert_folder()
        with _folder_lock(self._folder, ".batch.lock"):
            payload = _read_regular(self._folder / "batch.json")
            if payload != self._manifest_bytes:
                raise ValueError("Finished manifest changed outside this owner")
            review = review_saved(self._folder)
            if any(
                item["review_state"] != "accepted" and item["state"] == "accepted"
                for item in review["operations"]
            ):
                raise ValueError("Finished canonical inputs cannot be authenticated")
            try:
                self._note_publication(_publish_owned_file(
                    self._folder / "batch.json", payload, _digest(payload)
                ))
                self._accept_manifest(payload)
            except BaseException as error:
                self._note_publication(getattr(error, "publication", None), complete=False)
                self._save_reason = self._publication_reason(error)
                raise
            self._schedule_companions(retry=True)

    def _another_folder(self, folder, outcome):
        old_folder, old_lock = self._folder, self._lock
        old_bytes, old_data = self._manifest_bytes, deepcopy(self._data)
        old_receipt, old_digest = self._ti3, self._receipt_digest
        old_reason, old_ti3_reason = self._save_reason, self._ti3_reason
        old_diagnostic_reasons = dict(self._diagnostic_publication_reasons)
        old_identity = self._folder_identity
        old_cache = (
            self._saved_ids.copy(),
            self._unsaved_ids.copy(),
            self._saved_annotation_versions.copy(),
            self._saved_comparison_version,
            deepcopy(self._saved_comparison),
            self._saved_draft,
        )
        target = Path(folder).absolute()
        if target.exists():
            if not stat.S_ISDIR(target.lstat().st_mode):
                raise ValueError("Rescue directory must be an ordinary directory")
            target = target.resolve(strict=True)
            reservation = self._rescue_attempts.get(str(target))
            metadata = target.lstat()
            if reservation is None or reservation["identity"] != (metadata.st_dev, metadata.st_ino):
                raise FileExistsError("Rescue directory was not reserved by this owner")
        else:
            target.mkdir()
            target = target.resolve(strict=True)
            metadata = target.lstat()
            reservation = dict(identity=(metadata.st_dev, metadata.st_ino), manifest_bytes=None)
            self._rescue_attempts[str(target)] = reservation
        new_lock = _folder_lock(target, ".batch.lock")
        new_lock.__enter__()
        try:
            # Authenticate retained originals against remembered acceptance. Old
            # disk conflicts become provenance issues, never overwrite authority.
            saved = _manifest(old_bytes)
            issues = []
            try:
                self._assert_folder()
                actual_manifest = _read_regular(old_folder / "batch.json")
            except OSError as error:
                issues.append(f"Previous manifest cannot be authenticated: {error}")
            else:
                if actual_manifest != old_bytes:
                    issues.append("Previous manifest differs from remembered accepted bytes")
            for entry in saved["operations"]:
                if entry["state"] != "accepted":
                    continue
                retained = self._retained.get(entry["command_id"])
                if retained is None:
                    raise ValueError("Earlier canonical operation is not retained in memory")
                _check_result(retained, entry, saved["descriptor"])
                expected = self._payloads[entry["command_id"]]
                if _digest(expected) != entry["sha256"]:
                    raise ValueError("Retained bytes disagree with earlier accepted digest")
                try:
                    actual = _read_regular(old_folder / entry["path"])
                except OSError as error:
                    issues.append(
                        f"Previous operation {entry['path']} cannot be authenticated: {error}"
                    )
                else:
                    if actual != expected:
                        issues.append(
                            f"Previous operation {entry['path']} differs from accepted bytes"
                        )
            for identity, expected in self._diagnostic_payloads.items():
                path = old_folder / f"diagnostic-{identity}.log"
                try:
                    actual = _read_regular(path)
                except OSError as error:
                    issues.append(f"Previous native diagnostics cannot be authenticated: {error}")
                else:
                    if actual != expected:
                        issues.append(
                            "Previous native diagnostics differ from retained original bytes"
                        )
            if issues:
                self._data["destination_issues"][str(old_folder)] = "; ".join(issues)[:16384]
            self._folder, self._lock = target, new_lock
            self._folder_identity = reservation["identity"]
            self._manifest_bytes = reservation["manifest_bytes"]
            self._receipt_digest, self._ti3 = None, None
            self._saved_ids = set()
            self._unsaved_ids = set(self._retained)
            if str(target) not in self._data["destinations"]:
                self._data["destinations"].append(str(target))
            self._touch()
            if not self._save(export=True):
                raise OSError(self._save_reason)
        except BaseException as error:
            reservation["manifest_bytes"] = (
                self._manifest_bytes if self._folder == target else reservation["manifest_bytes"]
            )
            if self._folder == target:
                try:
                    published = _read_regular(target / "batch.json")
                except BaseException as failure:
                    error = _preserve_publication_failure(
                        error,
                        failure,
                        "Rescue manifest authentication failed",
                    )
                else:
                    if published == _json(self._data).encode("ascii"):
                        reservation["manifest_bytes"] = published
            self._folder, self._lock = old_folder, old_lock
            self._folder_identity = old_identity
            self._manifest_bytes, self._data = old_bytes, old_data
            (
                self._saved_ids,
                self._unsaved_ids,
                self._saved_annotation_versions,
                self._saved_comparison_version,
                self._saved_comparison,
                self._saved_draft,
            ) = old_cache
            self._ti3, self._receipt_digest = old_receipt, old_digest
            self._save_reason, self._ti3_reason = old_reason, old_ti3_reason
            self._diagnostic_publication_reasons = old_diagnostic_reasons
            self._release_publication_lock(new_lock, failure=error)
            raise error
        self._rescue_attempts.pop(str(target), None)
        outcome.update(admission="admitted", state="pending")
        if old_lock is not None and old_lock.release_failure is None:
            # A lock whose close already failed stays retained and is never
            # retried; its destination is retired and this rescue is the remedy.
            self._release_publication_lock(old_lock)
        if self._data["status"] in {"finished", "abandoned"} and not self._companions_saving:
            self._release_lock()

    def _unresolved_companions(self):
        snapshot = self._companions_snapshot()
        unresolved = []
        for format, item in snapshot["formats"].items():
            if not item["retryable"]:
                continue
            retained = self._companion_pending.get(format)
            unresolved.append(dict(
                format=format, path=item["path"], state=item["state"], reason=item["reason"],
                source_manifest_sha256=retained["source"] if retained else
                item["source_manifest_sha256"] or snapshot["source_manifest_sha256"],
                payload_sha256=_digest(retained["payload"]) if retained else None,
                payload_bytes=len(retained["payload"]) if retained else None,
            ))
        if self._ti3_reason not in {
            None, "No export attempt", "Manifest changed; optional export refresh pending",
        } or self._ti3 is None and self._saved_ids:
            pending = self._ti3 is None and self._companions_saving and self._ti3_reason in {
                None, "No export attempt", "Manifest changed; optional export refresh pending",
            }
            unresolved.append(dict(
                format="ti3", path=str(self._folder / "ti3-result.json"),
                state="pending" if pending else "failed",
                reason="Automatic TI3 refresh is pending" if pending else self._ti3_reason,
                source_manifest_sha256=snapshot["source_manifest_sha256"],
                payload_sha256=None, payload_bytes=None,
            ))
        return unresolved

    def _unsaved_native_diagnostics(self):
        # Accepted manifest bytes are immutable. Retain only their diagnostics
        # settings until publication, recovery or relocation replaces those bytes.
        # Eligibility polling must not validate all saved readings or build a
        # complete discard report on every refresh.
        if self._diagnostic_settings_manifest is not self._manifest_bytes:
            self._saved_diagnostic_settings = _load(self._manifest_bytes)["native_diagnostics"]
            self._diagnostic_settings_manifest = self._manifest_bytes
        diagnostics = dict(
            settings=self._data["native_diagnostics"]
            if self._data["native_diagnostics"] != self._saved_diagnostic_settings else None,
            captures=[dict(command_id=identity, sha256=_digest(payload), bytes=len(payload))
                      for identity, payload in self._diagnostic_payloads.items()
                      if not self._publications.get(str(
                          self._folder / f"diagnostic-{identity}.log"
                      ), {}).get("_sync_complete")],
            retention_reasons=deepcopy(self._diagnostic_reasons),
            publication_reasons=deepcopy(self._diagnostic_publication_reasons),
        )
        return diagnostics if any(diagnostics.values()) else None

    def discard_summary(self) -> dict:
        """Return the exact volatile versions a confirmed close would discard."""
        with self._mutex:
            saved = _manifest(self._manifest_bytes)
            saved_ids = {
                item["command_id"] for item in saved["operations"] if item["state"] == "accepted"
            }
            unsaved = [
                dict(
                    command_id=identity,
                    operation_id=result.operation_id,
                    readings=len(result.patches),
                    status=result.status,
                )
                for identity, result in self._retained.items()
                if identity not in saved_ids
            ]
            summary = dict(
                batch_id=self._data["batch_id"],
                operations=unsaved,
                drafts=self._data["draft"] if self._data["draft"] != saved["draft"] else None,
                comparison=self._data["comparison"]
                if self._data["comparison"] != saved["comparison"]
                else None,
                chart=self._data["chart"] if self._data["chart"] != saved["chart"] else None,
                native_diagnostics=self._unsaved_native_diagnostics(),
                companions=self._unresolved_companions(),
                annotations=[
                    item for item in self._data["annotations"] if item not in saved["annotations"]
                ],
                active_command_id=self._active,
                annotation_drafts=list(self._annotation_drafts.values()),
                unknown_outcomes={
                    key: _digest(_json(value).encode("ascii"))
                    for key, value in self._unknown.items()
                },
            )
            retirement = [
                dict(
                    path=str(lock.path), identity=id(lock),
                    directory_identity=lock.directory_identity,
                    failure_identity=id(lock.release_failure),
                    failure=self._publication_reason(lock.release_failure),
                )
                for lock in self._own_retired_publication_locks()
            ]
            facts = dict(
                contents=summary, starting=self._starting, metadata_saving=self._metadata_saving,
                software_stopped=self._stop_confirmed, instrument_state=self._instrument_state,
                operation_terminal=self._operation is None or self._operation.done,
                uncertain_acquisition_owners=[id(owner) for owner in _UNCERTAIN_OWNERS],
                publication_retirement=retirement,
            )
            return deepcopy(dict(summary, token=_digest(_json(facts).encode("ascii"))))

    def _new_batch_ready(self):
        """Durable Finish with no active or unsaved work: the one rule for New batch."""
        with self._mutex:
            return (
                self._data["status"] == "finished"
                and not self._closed
                and self._active is None
                and not self._save_reason
                and not self._metadata_saving
                and not self._companions_saving
                and not self._companion_pending
                and not self._unresolved_companions()
                and not self._unsaved_native_diagnostics()
            )

    def new_batch(
        self, folder: str | Path, *, frontend: InstrumentFrontend | DirectSerialFrontend, mode: str,
        options: dict, expected_descriptor: dict | None = None, name="Untitled batch", notes="",
    ) -> BatchApplication:
        """Select a new frozen binding only after this batch's durable Finish."""
        with self._mutex:
            if not self._new_batch_ready():
                raise ValueError("New batch requires durable Finish with no active or unsaved work")
            return BatchApplication(
                folder, frontend=frontend, mode=mode, options=options,
                expected_descriptor=expected_descriptor, name=name, notes=notes,
            )
