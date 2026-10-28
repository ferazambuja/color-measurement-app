"""Explicit current standalone record formats with exact ordinary-file authentication."""

import base64
import hashlib
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from . import exports, operation_exports
from .operation_records import OperationResult, result_from_json

FORMATS = (
    "operation_json", "operation_mat", "observation_json", "observation_mat", "observation_csv",
)
MAX_SOURCE_BYTES = 32 * 1024 * 1024


def _identity(path):
    metadata = path.lstat()
    return metadata.st_dev, metadata.st_ino


class StandaloneRecord:
    """One immutable typed record plus captured input bytes; never a batch or frontend.

    Current canonical readers own all schema and quantity validation. A standalone
    observation keeps its original operation ID without inventing a terminal
    outcome, descriptor, calibration or batch. Imports perform no acquisition.
    """

    def __init__(self, path, *, format):
        if format not in FORMATS:
            raise ValueError("Choose an explicit current standalone record format")
        # Resolve the parent only: the selected file itself must never hide a symlink.
        selected = Path(path).expanduser().absolute()
        self.path = selected.parent.resolve() / selected.name
        self.format = format
        self._identity = _identity(self.path)
        self.payload = exports._read_regular(self.path, limit_bytes=MAX_SOURCE_BYTES)
        if format == "operation_mat":
            self.record, self.canonical_json = operation_exports._decode_mat(self.payload)
        elif format == "observation_mat":
            self.record = exports.from_mat(self.payload)
            self.canonical_json = exports.to_json(self.record)
        else:
            text = self.payload.decode("utf-8")
            reader = {"operation_json": result_from_json, "observation_json": exports.from_json,
                      "observation_csv": exports.from_csv}[format]
            self.record = reader(text)
            self.canonical_json = text if format.endswith("_json") else exports.to_json(self.record)
        self.sha256 = hashlib.sha256(self.payload).hexdigest()
        self.assert_current()

    @property
    def observations(self):
        return tuple(patch.observation for patch in self.record.patches) \
            if isinstance(self.record, OperationResult) else (self.record,)

    def assert_current(self):
        if _identity(self.path) != self._identity or exports._read_regular(
            self.path, limit_bytes=MAX_SOURCE_BYTES,
        ) != self.payload or _identity(self.path) != self._identity:
            raise ValueError("Standalone source was replaced or changed; open the source again")

    def metadata(self):
        operation = isinstance(self.record, OperationResult)
        return dict(
            kind="standalone", record_kind="operation" if operation else "observation",
            format=self.format, path=str(self.path), source_sha256=self.sha256,
            source_bytes=len(self.payload), operation_id=self.record.operation_id,
            operation_status=self.record.status if operation else None,
            operation_reason=self.record.error if operation else None,
            provenance=self.record.provenance,
            software_stopped=self.record.software_stopped if operation else None,
            instrument_state=self.record.instrument_state if operation else None,
            device=asdict(self.record.device),
        )

    def annotations(self):
        return [dict(
            operation_id=record.operation_id, observation_id=record.observation_id,
            name=record.sample_id, notes="", version=0,
        ) for record in self.observations]

    def capture(self):
        self.assert_current()
        return deepcopy(dict(
            kind="standalone", source=self.metadata(), record=asdict(self.record),
            annotations=self.annotations(),
            native_input=dict(
                kind="operation" if isinstance(self.record, OperationResult) else "observation",
                format=self.format, source_sha256=self.sha256, canonical_json=self.canonical_json,
                canonical_json_sha256=hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest(),
            ),
            original_file=dict(
                encoding="base64", data=base64.b64encode(self.payload).decode("ascii"),
                sha256=self.sha256,
            ),
        )), self._identity, {self.path.name: self.payload}
