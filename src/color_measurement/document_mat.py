"""Lossless level-5 MAT containers using the existing optional SciPy owner."""

import hashlib
import io
import json
import warnings

from .application import _load
from .operation_exports import _mat_dependencies, _matches_view

KINDS = frozenset({"analysis", "batch", "observation"})


def to_mat(document, *, kind):
    """Keep canonical document JSON and its exact digest in public MAT types.

    This is distinct from operation MAT and from arbitrary MATLAB session files.
    ASCII JSON escapes preserve Unicode, nulls, native values and original
    operation JSON text without an unverifiable numerical convenience copy.
    """
    if kind not in KINDS or not isinstance(document, dict):
        raise ValueError("Choose an analysis, batch or observation document")
    text = json.dumps(document, ensure_ascii=True, allow_nan=False, sort_keys=True)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    np, scipy_io = _mat_dependencies()
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        scipy_io.savemat(stream, {
            "document_schema": np.asarray([[1]], dtype=np.int64),
            "document_kind": kind,
            "document_json": text,
            "document_sha256": digest,
        }, appendmat=False, format="5", long_field_names=False,
            do_compression=False, oned_as="row")
    return stream.getvalue()


def from_mat(payload, *, kind):
    """Decode only the declared current document kind with exact content identity."""
    if kind not in KINDS or not isinstance(payload, bytes) or len(payload) < 128 \
            or not payload.startswith(b"MATLAB 5.0 MAT-file"):
        raise ValueError("Expected a declared level-5 analysis, batch or observation MAT document")
    np, scipy_io = _mat_dependencies()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            data = scipy_io.loadmat(
                io.BytesIO(payload), appendmat=False, struct_as_record=False,
                squeeze_me=False, mat_dtype=True, chars_as_strings=True,
                uint16_codec="utf-8", verify_compressed_data_integrity=True,
            )
    except (ValueError, TypeError, OSError, Warning, scipy_io.matlab.MatReadError) as error:
        raise ValueError(f"Malformed document MAT payload: {error}") from error
    fields = set(data) - {"__header__", "__version__", "__globals__"}
    if fields != {"document_schema", "document_kind", "document_json", "document_sha256"}:
        raise ValueError("Document MAT fields differ from schema 1")
    if not _matches_view(data["document_schema"], np.asarray([[1]], dtype=np.int64), np):
        raise ValueError("Unsupported document MAT schema")

    def text(name):
        value = data[name]
        if not isinstance(value, np.ndarray) or value.dtype.kind != "U" or value.shape != (1,):
            raise ValueError(f"Document MAT {name} must be a character row")
        return str(value[0])

    if text("document_kind") != kind:
        raise ValueError("Document MAT kind differs from requested format")
    canonical = text("document_json")
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != text("document_sha256"):
        raise ValueError("Document MAT content digest differs")
    document = _load(canonical)
    if not isinstance(document, dict):
        raise ValueError("Document MAT must contain an object")
    return document
