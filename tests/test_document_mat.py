"""Exact JSON-bearing MAT documents; no arbitrary MATLAB session importer."""

import io

import pytest

from color_measurement import document_mat


@pytest.mark.parametrize("kind", ["analysis", "batch"])
def test_mat_document_roundtrip_preserves_null_signed_data_unicode_and_raw_json(kind):
    document = dict(records=[dict(name="🟦 source", absent=None, signed=-230, measured_zero=0,
                                  original='{"note":"Ω 🟦", "value":null}\n')])
    payload = document_mat.to_mat(document, kind=kind)
    assert document_mat.from_mat(payload, kind=kind) == document
    other = "batch" if kind == "analysis" else "analysis"
    with pytest.raises(ValueError, match="kind"):
        document_mat.from_mat(payload, kind=other)


def test_mat_document_digest_and_schema_refuse_corrupt_or_undigested_content():
    np, scipy_io = document_mat._mat_dependencies()
    original = document_mat.to_mat(dict(native_value=59.94), kind="analysis")
    data = scipy_io.loadmat(io.BytesIO(original))
    data["document_json"] = '{"native_value":0}'
    stream = io.BytesIO()
    scipy_io.savemat(stream, {key: value for key, value in data.items()
                             if not key.startswith("__")})
    with pytest.raises(ValueError, match="digest"):
        document_mat.from_mat(stream.getvalue(), kind="analysis")
    data["document_schema"] = np.asarray([[2]], dtype=np.int64)
    stream = io.BytesIO()
    scipy_io.savemat(stream, {key: value for key, value in data.items()
                             if not key.startswith("__")})
    with pytest.raises(ValueError, match="schema"):
        document_mat.from_mat(stream.getvalue(), kind="analysis")


def test_missing_mat_dependency_is_unavailable_without_fake_payload(monkeypatch):
    def unavailable():
        raise ImportError("Synthetic missing optional MAT dependency")

    monkeypatch.setattr(document_mat, "_mat_dependencies", unavailable)
    with pytest.raises(ImportError, match="dependency"):
        document_mat.to_mat(dict(source="retained"), kind="batch")
