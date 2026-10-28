"""Explicit persistence of terminal operations without any acquisition or UI.

JSON uses the existing operation schema. MAT level 5 contains that canonical
JSON plus a versioned plain-data view for MATLAB inspection. The JSON is the
reconstruction authority; imports reject a view that disagrees with it. Native
quantities retain exact sample grids and units, with no clipping or resampling.
MAT support requires the optional ``mat`` extra; JSON has no extra dependency.
Inspection text is a MATLAB character row for ASCII/BMP text. Text containing
non-BMP characters uses a plain ``{encoding: 'UTF-16', code_units: uint16 row}``
struct, decoded in MATLAB by ``char(value.code_units)``. This avoids SciPy's
non-BMP character-row framing, which the independent MATLAB reader rejects.
"""

import io
import warnings
from dataclasses import asdict
from pathlib import Path

from .exports import PublicationResult, SaveResult, _publish_new_file
from .models import ScalarObservation, TimingObservation
from .operation_records import OperationResult, result_from_json, result_to_json


class OperationSaveError(OSError):
    """A failed save retains its operation and complete payload for explicit retry."""

    def __init__(
        self, path: Path, result: OperationResult, payload: bytes, format: str, cause: OSError
    ):
        super().__init__(f"Could not save {path}: {cause}")
        self.path = path
        self.result = result
        self.payload = payload
        self.format = format


# Operator wording for an app build without the MAT runtime; the ImportError
# below names the package extra, which is for command-line installations.
MAT_UNAVAILABLE_REASON = "MAT files are unavailable in this app build."


def _mat_dependencies():
    try:
        import numpy as np
        from scipy import io as scipy_io
    except ImportError as error:
        raise ImportError("MAT export/import requires color-measurement[mat]") from error
    return np, scipy_io


def _view(result: OperationResult, np) -> dict:
    """Fixed field names only; caller metadata stays in text key/value cells."""
    def strings(values):
        cell = np.empty((1, len(values)), dtype=object)
        for index, value in enumerate(values):
            cell[0, index] = value
        return cell

    def configuration(values):
        cell = np.empty((len(values), 2), dtype=object)
        for index, pair in enumerate(values):
            cell[index, :] = pair
        return cell

    def row(values):
        if any(float(value) != value for value in values):
            raise ValueError("MAT numerical views require values exactly representable as doubles")
        return np.asarray(values, dtype=np.float64).reshape(1, -1)

    def flag(value):
        return np.asarray([[value]], dtype=np.bool_)

    def integer(value):
        return np.asarray([[value]], dtype=np.int64)

    device = asdict(result.device)
    device["serial_present"] = flag(result.device.serial is not None)
    device["serial"] = result.device.serial if result.device.serial is not None else ""
    device["serial_encoding"] = result.device.serial_encoding or ""
    patches = np.empty((1, len(result.patches)), dtype=object)
    for index, patch in enumerate(result.patches):
        observation = patch.observation
        if isinstance(observation, TimingObservation):
            presentations = np.empty((1, 2), dtype=object)
            for position, stamp in enumerate(observation.timing_context.presentations):
                presentations[0, position] = {
                    **asdict(stamp),
                    "presented_at_seconds": row((stamp.presented_at_seconds,)),
                }
            context = asdict(observation.timing_context)
            context["presentations"] = presentations
            for field in (
                "method_window_started_seconds", "marker_call_started_seconds",
                "marker_call_finished_seconds", "method_window_finished_seconds",
            ):
                context[field] = row((context[field],))
            patches[0, index] = {
                "record_kind": "timing", "index_zero_based": integer(patch.index),
                "patch_id": patch.patch_id, "observation_id": observation.observation_id,
                "acquired_at": observation.acquired_at,
                "configuration": configuration(observation.configuration),
                "calibration": asdict(observation.calibration),
                "backend_reported": {
                    **asdict(observation.backend_reported),
                    "display_update_delay_ms": integer(
                        observation.backend_reported.display_update_delay_ms
                    ),
                    "instrument_reaction_ms": integer(
                        observation.backend_reported.instrument_reaction_ms
                    ),
                },
                "stimulus": asdict(observation.stimulus),
                "timing_context": context,
                "warnings": strings(observation.warnings),
            }
            continue
        if isinstance(observation, ScalarObservation):
            patches[0, index] = {
                "record_kind": "scalar", "index_zero_based": integer(patch.index),
                "patch_id": patch.patch_id, "observation_id": observation.observation_id,
                "acquired_at": observation.acquired_at,
                "configuration": configuration(observation.configuration),
                "calibration": asdict(observation.calibration),
                "backend_reported": {**asdict(observation.backend_reported),
                                     "value": row((observation.backend_reported.value,))},
                "stimulus": asdict(observation.stimulus),
                "warnings": strings(observation.warnings),
            }
            continue
        xyz = observation.backend_reported.xyz
        spectrum = observation.backend_reported.spectrum
        patches[0, index] = {
            "index_zero_based": integer(patch.index),
            "patch_id": patch.patch_id,
            "observation_id": observation.observation_id,
            "acquired_at": observation.acquired_at,
            "configuration": configuration(observation.configuration),
            "calibration": asdict(observation.calibration),
            "xyz_present": flag(xyz is not None),
            "xyz_values": row(xyz.values if xyz else ()),
            "xyz_unit": xyz.unit if xyz else "",
            "xyz_scale": xyz.scale if xyz else "",
            "xyz_observer": xyz.observer if xyz else "",
            "xyz_illuminant": xyz.illuminant if xyz else "",
            "spectrum_present": flag(spectrum is not None),
            "wavelengths_nm": row(spectrum.wavelengths_nm if spectrum else ()),
            "spectral_values": row(spectrum.values if spectrum else ()),
            "spectral_quantity": spectrum.quantity if spectrum else "",
            "spectral_unit": spectrum.unit if spectrum else "",
            "warnings": strings(observation.warnings),
        }
    return {
        "schema_version": integer(result.schema_version),
        "operation_id": result.operation_id,
        "device_id": result.device_id,
        "device": device,
        "provenance": result.provenance,
        "mode": result.mode,
        "workflow": result.workflow,
        "status": result.status,
        "expected_patch_ids": strings(result.expected_patch_ids),
        "patches": patches,
        "configuration": configuration(result.configuration),
        "calibration": asdict(result.calibration),
        "error_present": flag(result.error is not None),
        "error": result.error if result.error is not None else "",
        "software_stopped": flag(result.software_stopped),
        "instrument_state": result.instrument_state,
    }


def _portable_text(value, np):
    """Retain non-BMP Unicode as explicit UTF-16 units, using public MAT types."""
    if isinstance(value, str) and any(ord(character) > 0xFFFF for character in value):
        return {
            "encoding": "UTF-16",
            "code_units": np.frombuffer(value.encode("utf-16-le"), dtype="<u2").reshape(1, -1),
        }
    if isinstance(value, dict):
        return {key: _portable_text(item, np) for key, item in value.items()}
    if isinstance(value, np.ndarray) and value.dtype.kind == "O":
        cell = np.empty(value.shape, dtype=object)
        for index in np.ndindex(value.shape):
            cell[index] = _portable_text(value[index], np)
        return cell
    return value


def to_mat(result: OperationResult) -> bytes:
    """Encode one operation as portable MAT level 5, never a Python object.

    ``record_json`` retains the entire validated record, including raw output,
    request text, computed results and nulls. ``operation`` is an inspection
    view of native quantities and metadata. Its ``patches`` are a 1-by-N cell
    array of structs; XYZ and each wavelength/value array are row vectors.
    Empty quantities have a false presence flag and an empty row, not zeros.
    Configurations are N-by-2 text cells; patch indices remain zero based.
    Text fields/cells are char rows or, for non-BMP Unicode, a struct with
    ``encoding='UTF-16'`` and a uint16 ``code_units`` row. MATLAB callers decode
    that struct with ``char(value.code_units)``; ordinary char rows need no change.
    The projection is not a new record schema or a MAT-only archival format.
    """
    canonical = result_to_json(result)
    np, scipy_io = _mat_dependencies()
    view = _view(result, np)
    _check_view_text(view, np)
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        scipy_io.savemat(
            stream,
            {
                "export_schema": np.asarray([[1]], dtype=np.int64),
                "record_json": canonical,
                "operation": _portable_text(view, np),
            },
            appendmat=False,
            format="5",
            long_field_names=False,
            do_compression=False,
            oned_as="row",
        )
    return stream.getvalue()


def _check_view_text(value, np) -> None:
    # A terminal NUL is lost by SciPy's MATLAB char-row reader. Refuse instead
    # of writing a convenience view which our own reader cannot verify.
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError("MAT inspection text cannot contain NUL characters; use JSON")
        value.encode("utf-8", errors="strict")
    elif isinstance(value, dict):
        for item in value.values():
            _check_view_text(item, np)
    elif isinstance(value, np.ndarray) and value.dtype.kind == "O":
        for item in value.flat:
            _check_view_text(item, np)


def _matches_view(actual, expected, np) -> bool:
    """Compare MATLAB types, shapes and values, including nested cells/structs."""
    if not isinstance(actual, np.ndarray):
        return False
    if isinstance(expected, dict):
        if actual.shape != (1, 1) or actual.dtype.kind != "O":
            return False
        struct = actual[0, 0]
        if set(getattr(struct, "_fieldnames", ()) or ()) != set(expected):
            return False
        return all(
            _matches_view(getattr(struct, key), value, np) for key, value in expected.items()
        )
    if isinstance(expected, str):
        # SciPy represents a MATLAB char row as one string, and '' as no strings.
        return actual.dtype.kind == "U" and (
            (actual.shape == (1,) and actual[0] == expected)
            if expected else actual.shape == (0,)
        )
    if actual.shape != expected.shape or actual.dtype.kind != expected.dtype.kind:
        return False
    if expected.dtype.kind == "O":
        return all(
            _matches_view(a, b, np) for a, b in zip(actual.flat, expected.flat, strict=True)
        )
    return (
        actual.dtype.itemsize == expected.dtype.itemsize
        and bool(np.array_equal(actual, expected))
    )


def _decode_mat(payload: bytes) -> tuple[OperationResult, str]:
    """Load this module's MAT view; reject malformed or contradictory content.

    This is deliberately not an importer for arbitrary MATLAB session files,
    v7.3/HDF5 files or changed numerical projections. To change measurements,
    construct and validate a new canonical record instead of editing its view.
    """
    if (
        not isinstance(payload, bytes) or len(payload) < 128
        or not payload.startswith(b"MATLAB 5.0 MAT-file")
    ):
        raise ValueError("Expected a level-5 operation MAT payload")
    np, scipy_io = _mat_dependencies()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            data = scipy_io.loadmat(
                io.BytesIO(payload),
                appendmat=False,
                struct_as_record=False,
                squeeze_me=False,
                mat_dtype=True,
                chars_as_strings=True,
                uint16_codec="utf-8",
                verify_compressed_data_integrity=True,
            )
    except (ValueError, TypeError, OSError, Warning, scipy_io.matlab.MatReadError) as error:
        raise ValueError(f"Malformed operation MAT payload: {error}") from error
    fields = set(data) - {"__header__", "__version__", "__globals__"}
    if fields != {"export_schema", "record_json", "operation"}:
        raise ValueError("Operation MAT fields differ from export schema 1")
    if not _matches_view(data["export_schema"], np.asarray([[1]], dtype=np.int64), np):
        raise ValueError("Unsupported operation MAT export schema")
    text = data["record_json"]
    if not isinstance(text, np.ndarray) or text.dtype.kind != "U" or text.shape != (1,):
        raise ValueError("Operation MAT record_json must be a character row")
    result = result_from_json(str(text[0]))
    if not _matches_view(data["operation"], _portable_text(_view(result, np), np), np):
        raise ValueError("MAT inspection view disagrees with the canonical operation record")
    return result, str(text[0])


def from_mat(payload: bytes) -> OperationResult:
    """Validate the current operation MAT, preserving its canonical JSON at the decode owner."""
    return _decode_mat(payload)[0]


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


def load(path: str | Path, *, format: str = "json") -> OperationResult:
    """Load explicit JSON/MAT records without inferring format from the filename."""
    if format not in ("json", "mat"):
        raise ValueError("format must be json or mat")
    payload = Path(path).read_bytes()
    return result_from_json(payload.decode("utf-8")) if format == "json" else from_mat(payload)
