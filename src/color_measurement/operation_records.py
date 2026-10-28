"""Immutable operation envelopes retaining complete, unchanged schema-1 readings.

An incomplete scan retains only the patches actually returned. Patch indices
refer to the requested order; a missing patch is never replaced with zeros.
This is a structural interchange contract, not proof of physical acquisition.
"""

import json
from dataclasses import asdict, dataclass
from typing import Literal

from .json_values import unique_object
from .models import (
    CalibrationContext,
    DeviceIdentity,
    Observation,
    ScalarObservation,
    TimingObservation,
    _decode,
    _text,
    identity_agrees,
)


@dataclass(frozen=True)
class PatchResult:
    """One acquired reading at a zero-based position in the requested patch list."""

    index: int
    patch_id: str
    observation: Observation | ScalarObservation | TimingObservation

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError("Patch index must be a nonnegative integer")
        _text(self.patch_id)
        if not isinstance(self.observation, (Observation, ScalarObservation, TimingObservation)):
            raise ValueError("Result requires a colour, scalar or timing observation record")
        if self.observation.sample_id != self.patch_id:
            raise ValueError("Patch ID must match the observation sample ID")


@dataclass(frozen=True)
class OperationResult:
    """Terminal result with explicit completeness and software/instrument state.

    Mode and workflow names are extensible. The ``calibrate`` workflow carries
    calibration context without measurements; every other workflow declares the
    expected patch IDs. Failed or cancelled operations may retain a validated,
    ordered subset, including no readings. A complete patch set does not imply a
    completed operation: cleanup can fail after acquisition.

    ``software_stopped`` and ``instrument_state`` describe distinct facts;
    successful software termination alone never confirms instrument readiness.

    ``request_json`` retains the original request text, including options and
    layout. This envelope checks its JSON structure and matching identities; the
    request producer validates its full option/capability contract separately.
    ``configuration`` retains applied/readback facts even when calibration has
    produced no observations. Empty facts mean none were reported.
    """

    schema_version: Literal[1]
    operation_id: str
    device_id: str
    device: DeviceIdentity
    provenance: Literal["simulated", "replay", "physical"]
    mode: str
    workflow: str
    status: Literal["completed", "failed", "cancelled"]
    expected_patch_ids: tuple[str, ...]
    patches: tuple[PatchResult, ...]
    request_json: str
    configuration: tuple[tuple[str, str], ...]
    calibration: CalibrationContext
    error: str | None
    software_stopped: bool
    instrument_state: Literal["not_applicable", "unknown", "confirmed_ready"]
    #: The failing stage as the native helper named it, and the ``inst_code`` it
    #: returned. ``error`` stays the human sentence; these two are what a client
    #: branches on. Without them the only machine-readable form of "the driver
    #: refused the trigger" was a substring of English prose, which no client
    #: should have to match. Absent together for anything that did not fail
    #: inside the instrument.
    error_stage: str | None = None
    error_native_code: int | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported operation-result schema version")
        _text(self.operation_id, self.device_id, self.mode, self.workflow)
        if not isinstance(self.device, DeviceIdentity) or self.device.device_id != self.device_id:
            raise ValueError("Operation device identity must match its device ID")
        if self.provenance not in ("simulated", "replay", "physical"):
            raise ValueError("Unknown operation provenance")
        if (self.error_stage is None) != (self.error_native_code is None):
            raise ValueError("A native failure carries both a stage and a code, or neither")
        if self.error_stage is not None:
            _text(self.error_stage)
            if type(self.error_native_code) is not int:
                raise ValueError("A native error code is an integer inst_code")
            if self.status == "completed":
                raise ValueError("A completed operation carries no native failure")
        if self.status not in ("completed", "failed", "cancelled"):
            raise ValueError("Unknown operation-result status")
        if not isinstance(self.expected_patch_ids, tuple):
            raise ValueError("Expected patch IDs must be an immutable tuple")
        for patch_id in self.expected_patch_ids:
            _text(patch_id)
        if len(set(self.expected_patch_ids)) != len(self.expected_patch_ids):
            raise ValueError("Expected patch IDs must be unique")
        if not isinstance(self.patches, tuple) or any(
            not isinstance(patch, PatchResult) for patch in self.patches
        ):
            raise ValueError("Patches must be an immutable tuple of PatchResult records")
        if not isinstance(self.calibration, CalibrationContext):
            raise ValueError("Operation result requires a CalibrationContext record")
        if not isinstance(self.configuration, tuple) or any(
            not isinstance(pair, tuple)
            or len(pair) != 2
            or any(not isinstance(value, str) for value in pair)
            for pair in self.configuration
        ):
            raise ValueError("Configuration must be immutable text key/value pairs")
        if len({key for key, _ in self.configuration}) != len(self.configuration):
            raise ValueError("Duplicate configuration keys")
        if type(self.software_stopped) is not bool:
            raise ValueError("Software termination state must be boolean")
        if self.instrument_state not in ("not_applicable", "unknown", "confirmed_ready"):
            raise ValueError("Unknown instrument state")
        if self.status == "completed":
            if self.error is not None:
                raise ValueError("Completed operation must have no error")
        else:
            _text(self.error)

        if self.workflow in {"calibrate", "mode_snapshot", "stored_refresh", "native_controls"}:
            if self.expected_patch_ids or self.patches:
                raise ValueError("Calibration or configuration query must not contain "
                                 "measurement patches")
        elif not self.expected_patch_ids:
            raise ValueError("Measurement workflow must declare expected patch IDs")

        if not isinstance(self.request_json, str):
            raise ValueError("Retained request JSON must be text")
        request = json.loads(self.request_json, object_pairs_hook=unique_object)
        if not isinstance(request, dict):
            raise ValueError("Retained request must be a JSON object")
        # Reject nonfinite constants and overflowed exponents anywhere in the
        # request, including currently unknown future option/layout structures.
        json.dumps(request, allow_nan=False)
        if "operation_id" in request:
            raise ValueError("Retained request must not assign an operation ID")
        for key, expected in (
            ("device_id", self.device_id),
            ("mode", self.mode),
            ("workflow", self.workflow),
            ("patch_ids", list(self.expected_patch_ids)),
        ):
            if key not in request or request[key] != expected:
                raise ValueError(f"Retained request {key} disagrees with the operation envelope")

        previous_index = -1
        observation_ids = set()
        observed_device = self.device
        for patch in self.patches:
            if not previous_index < patch.index < len(self.expected_patch_ids):
                raise ValueError("Patch indices must be increasing and within the requested range")
            previous_index = patch.index
            if patch.patch_id != self.expected_patch_ids[patch.index]:
                raise ValueError("Patch ID differs from its requested position")
            observation = patch.observation
            if (
                observation.operation_id != self.operation_id
                or observation.device.device_id != self.device_id
                or observation.requested_mode != self.mode
                or observation.actual_mode != self.mode
                or observation.sample_id != patch.patch_id
            ):
                raise ValueError("Observation identity disagrees with the operation envelope")
            if observation.observation_id in observation_ids:
                raise ValueError("Observation IDs must be unique within an operation")
            observation_ids.add(observation.observation_id)
            if (
                not identity_agrees(observation.device, observed_device)
                or observation.provenance != self.provenance
            ):
                raise ValueError("Patch device identity and provenance must match the operation")
            observed_device = observation.device

        if self.workflow == "refresh_frequency":
            if len(self.expected_patch_ids) != 1 or any(
                    not isinstance(p.observation, ScalarObservation) for p in self.patches):
                raise ValueError("Refresh workflow requires one requested scalar record")
            layout = request.get("layout", {})
            for patch in self.patches:
                stimulus = patch.observation.stimulus
                if not isinstance(layout, dict) or (
                    layout.get("stimulus_id") != stimulus.stimulus_id
                    or layout.get("stimulus_description") != stimulus.description
                ):
                    raise ValueError("Scalar stimulus differs from the retained request")
        elif self.workflow == "display_timing":
            if len(self.expected_patch_ids) != 1 or any(
                    not isinstance(p.observation, TimingObservation) for p in self.patches):
                raise ValueError("Display timing requires one requested timing record")
            layout = request.get("layout", {})
            for patch in self.patches:
                stimulus = patch.observation.stimulus
                if not isinstance(layout, dict) or (
                    layout.get("stimulus_id") != stimulus.stimulus_id
                    or layout.get("stimulus_description") != stimulus.description
                    or layout.get("display_id") != stimulus.display_id
                ):
                    raise ValueError("Timing stimulus differs from the retained request")
        elif any(isinstance(p.observation, (ScalarObservation, TimingObservation))
                 for p in self.patches):
            raise ValueError("A non-photometric observation cannot enter a colour workflow")

        if self.status == "completed" and len(self.patches) != len(self.expected_patch_ids):
            raise ValueError("Completed operation requires every expected patch")


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


def result_from_json(text: str) -> OperationResult:
    """Reject malformed, duplicate, unknown or missing fields at every level."""
    if not isinstance(text, str):
        raise ValueError("Operation-result JSON must be text")
    try:
        return _decode(OperationResult, json.loads(text, object_pairs_hook=unique_object))
    except RecursionError as error:
        raise ValueError("Operation-result JSON nesting exceeds the supported depth") from error
